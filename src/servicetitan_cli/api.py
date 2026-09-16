"""ServiceTitan API client. GET only.

Facts this client is built on (from ServiceTitan's developer documentation):

* Authentication is OAuth 2.0 client credentials against
  ``https://auth.servicetitan.io/connect/token`` (integration environment:
  ``auth-integration``). Access tokens live 15 minutes and ServiceTitan
  throttles token requests, so tokens are cached on disk per client id.
* Every request carries ``Authorization: Bearer <token>`` and ``ST-App-Key``.
* Rate limit: 600 calls per 10 seconds per app per tenant (reporting APIs:
  one run of the same report per minute). 429s are retried with backoff.
* List endpoints page with ``page``/``pageSize`` and answer
  ``{"page", "pageSize", "hasMore", "totalCount", "data"}``. ``pageSize``
  defaults to 50 and the documented maximum is 500.
* Export endpoints (``.../export/<collection>``) stream every record with a
  ``from`` continuation token and ``continueFrom``/``hasMore`` in the answer.

The only non-GET request this module ever makes is the token exchange with
the auth server. There is deliberately no method to POST, PUT, PATCH or
DELETE against the API host.
"""
import json
import os
import re
import threading
import time

import requests

from . import __version__
from .errors import ApiError, AuthError, UsageError
from .paths import token_cache_path, write_private

API_HOSTS = {"production": "https://api.servicetitan.io",
             "integration": "https://api-integration.servicetitan.io"}
AUTH_URLS = {"production": "https://auth.servicetitan.io/connect/token",
             "integration": "https://auth-integration.servicetitan.io/connect/token"}
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500
TOKEN_LIFETIME_S = 15 * 60
TOKEN_REFRESH_MARGIN_S = 60
RATE_LIMIT_CALLS, RATE_LIMIT_WINDOW_S = 600, 10.0
_VERSION_SEG = re.compile(r"^v\d+$")


class TokenCache(object):
    """Short-lived bearer tokens, keyed by environment and client id, in an
    owner-only file. Tokens expire in 15 minutes, so the exposure window is
    small, and caching avoids ServiceTitan's throttling of token requests when
    an agent runs many CLI commands in a row."""

    def __init__(self, path=None, enabled=True):
        self.path = path or token_cache_path()
        self.enabled = enabled
        self._lock = threading.Lock()

    def _load(self):
        if not self.enabled or not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f) or {}
        except (OSError, ValueError):
            return {}

    def get(self, key):
        entry = self._load().get(key)
        if entry and entry.get("expires_at", 0) - TOKEN_REFRESH_MARGIN_S > time.time():
            return entry.get("access_token")
        return None

    def put(self, key, token, expires_in):
        if not self.enabled:
            return
        with self._lock:
            data = self._load()
            now = time.time()
            data = {k: v for k, v in data.items() if v.get("expires_at", 0) > now}
            data[key] = {"access_token": token, "expires_at": now + float(expires_in or TOKEN_LIFETIME_S)}
            try:
                write_private(self.path, json.dumps(data))
            except OSError:
                pass

    def drop(self, key):
        with self._lock:
            data = self._load()
            if key in data:
                del data[key]
                try:
                    write_private(self.path, json.dumps(data))
                except OSError:
                    pass


class ServiceTitanClient(object):
    def __init__(self, creds, page_size=DEFAULT_PAGE_SIZE, timeout=30, token_cache=None, session=None,
                 max_retries=4, progress=None):
        self.creds = creds
        self.environment = creds.environment if creds.environment in API_HOSTS else "production"
        self.host = API_HOSTS[self.environment]
        self.auth_url = AUTH_URLS[self.environment]
        self.page_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
        self.timeout = timeout
        self.token_cache = token_cache if token_cache is not None else TokenCache()
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = "servicetitan-cli/%s (+read-only)" % __version__
        self.max_retries = max_retries
        self.progress = progress or (lambda msg: None)
        self._token = None
        self._recent = []
        self.requests_made = 0
        self.last_truncated = False

    # ------------------------------------------------------------ auth
    @property
    def _cache_key(self):
        return "%s:%s" % (self.environment, self.creds.client_id)

    def token(self, force=False):
        if self._token and not force:
            return self._token
        if not force:
            cached = self.token_cache.get(self._cache_key)
            if cached:
                self._token = cached
                return cached
        try:
            r = self.session.post(self.auth_url, timeout=self.timeout, data={
                "grant_type": "client_credentials",
                "client_id": self.creds.client_id,
                "client_secret": self.creds.client_secret,
            })
        except requests.RequestException as e:
            raise ApiError("Could not reach %s: %s" % (self.auth_url, e),
                           hint="Check network access or proxy settings.")
        if r.status_code in (400, 401):
            raise AuthError("Token request for tenant %s was rejected (%s)" % (self.creds.code, r.status_code),
                            hint="The client id or client secret is wrong, or was generated for the other environment "
                                 "(integration vs production). The secret is shown once when created; generate a new "
                                 "one under Settings > Integrations > API Application Access if unsure.",
                            details={"status": r.status_code, "body": r.text[:300]})
        if not r.ok:
            raise ApiError("Token request failed with HTTP %s" % r.status_code, status=r.status_code, body=r.text,
                           url=self.auth_url)
        body = r.json()
        tok = body.get("access_token")
        if not tok:
            raise AuthError("Token response had no access_token", details={"body": r.text[:300]})
        self._token = tok
        self.token_cache.put(self._cache_key, tok, body.get("expires_in"))
        return tok

    def _headers(self):
        return {"Authorization": "Bearer %s" % self.token(), "ST-App-Key": self.creds.app_key,
                "Accept": "application/json"}

    # ------------------------------------------------------------ paths
    def resolve_url(self, path):
        """Turn any of these into a full URL for this tenant:

        ``/jpm/v2/tenant/{tenant}/jobs``   placeholder filled in
        ``/jpm/v2/tenant/123/jobs``        used as is
        ``jpm/v2/jobs``                    ``tenant/<id>`` inserted after the version
        ``https://api.servicetitan.io/...`` must be this environment's API host
        """
        p = (path or "").strip()
        if not p:
            raise UsageError("Empty API path")
        if p.startswith("http://") or p.startswith("https://"):
            if not p.startswith(self.host + "/"):
                raise UsageError("Refusing to send credentials to %s" % p.split("/")[2],
                                 hint="Only %s is allowed for the %s environment." % (self.host, self.environment))
            p = p[len(self.host):]
        p = "/" + p.lstrip("/")
        p = p.replace("{tenant}", self.creds.tenant_id).replace("{tenantId}", self.creds.tenant_id)
        segs = p.split("/")
        if "tenant" not in segs:
            idx = next((i for i, s in enumerate(segs) if _VERSION_SEG.match(s)), None)
            if idx is None:
                raise UsageError("Cannot place the tenant id in path %r" % path,
                                 hint="Use the documented form, e.g. /jpm/v2/tenant/{tenant}/jobs, or the shorthand "
                                      "jpm/v2/jobs. Run `servicetitan api endpoints --search jobs`.")
            segs[idx + 1:idx + 1] = ["tenant", self.creds.tenant_id]
            p = "/".join(segs)
        return self.host + p

    # ------------------------------------------------------------ requests
    def _throttle(self):
        now = time.time()
        self._recent = [t for t in self._recent if now - t < RATE_LIMIT_WINDOW_S]
        if len(self._recent) >= RATE_LIMIT_CALLS - 20:
            wait = RATE_LIMIT_WINDOW_S - (now - self._recent[0]) + 0.05
            if wait > 0:
                time.sleep(wait)
        self._recent.append(time.time())

    def get(self, path, params=None):
        """GET one URL and return the decoded JSON body."""
        url = self.resolve_url(path)
        params = {k: v for k, v in (params or {}).items() if v is not None}
        refreshed = False
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            try:
                r = self.session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
            except requests.RequestException as e:
                if attempt <= self.max_retries:
                    time.sleep(min(2 ** attempt, 20))
                    continue
                raise ApiError("Request failed: %s" % e, url=url,
                               hint="Network or proxy problem; the request was retried %d times." % self.max_retries)
            self.requests_made += 1
            if r.status_code == 401 and not refreshed:
                refreshed = True
                self.token_cache.drop(self._cache_key)
                self.token(force=True)
                continue
            if r.status_code == 429 and attempt <= self.max_retries:
                retry_after = r.headers.get("Retry-After")
                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = min(2 ** attempt, 30)
                self.progress("   rate limited on %s, waiting %.0fs" % (url.split("/tenant/")[-1], wait))
                time.sleep(wait)
                continue
            if r.status_code >= 500 and attempt <= self.max_retries:
                time.sleep(min(2 ** attempt, 20))
                continue
            if r.ok:
                if not r.content:
                    return None
                try:
                    return r.json()
                except ValueError:
                    return r.text
            self._raise_for(r, url)

    def _raise_for(self, r, url):
        code = r.status_code
        body = r.text
        if code == 401:
            raise AuthError("ServiceTitan rejected the request for tenant %s (401)" % self.creds.code,
                            hint="The app key is wrong, or this app is not connected to tenant %s in ServiceTitan "
                                 "(Settings > Integrations > API Application Access)." % self.creds.tenant_id,
                            details={"status": code, "url": url, "body": body[:300]})
        if code == 403:
            raise ApiError("Forbidden (403) for %s" % url, status=code, body=body, url=url,
                           hint="The app lacks the API scope for this endpoint. Add the read scope in the developer "
                                "portal, then re-approve the app inside ServiceTitan.")
        if code == 404:
            raise ApiError("Not found (404) for %s" % url, status=code, body=body, url=url,
                           hint="Check the path (`servicetitan api endpoints --search ...`) and that the tenant id "
                                "belongs to these credentials.")
        if code == 429:
            raise ApiError("Rate limited (429) and retries exhausted", status=code, body=body, url=url,
                           hint="ServiceTitan allows 600 calls per 10 seconds per app per tenant. Slow down or retry later.")
        raise ApiError("HTTP %s for %s" % (code, url), status=code, body=body, url=url)

    def get_pages(self, path, params=None, max_pages=None, page_size=None):
        """Yield (page_number, items, body) for a paged list endpoint until hasMore is false."""
        size = min(int(page_size or self.page_size), MAX_PAGE_SIZE)
        page = 1
        self.last_truncated = False
        while True:
            body = self.get(path, dict(params or {}, page=page, pageSize=size))
            if isinstance(body, dict):
                items = body.get("data") or []
                has_more = bool(body.get("hasMore"))
            elif isinstance(body, list):
                items, has_more = body, False
            else:
                items, has_more = [], False
            yield page, items, body
            if not has_more:
                return
            if max_pages and page >= max_pages:
                self.last_truncated = True
                return
            page += 1

    def get_all(self, path, params=None, max_pages=None, page_size=None, label=None):
        items = []
        for page, batch, _ in self.get_pages(path, params, max_pages=max_pages, page_size=page_size):
            items.extend(batch)
        if self.last_truncated:
            self.progress("   [%s] hit the %d-page cap; results are truncated" % (label or path, max_pages))
        return items

    def export(self, path, from_token=None, include_recent_changes=False, max_batches=None):
        """Yield (items, continue_from, has_more) batches from an export endpoint."""
        token = from_token
        n = 0
        while True:
            params = {"from": token} if token else {}
            if include_recent_changes:
                params["includeRecentChanges"] = "true"
            body = self.get(path, params) or {}
            items = body.get("data") or []
            token = body.get("continueFrom")
            has_more = bool(body.get("hasMore"))
            n += 1
            yield items, token, has_more
            if not has_more or (max_batches and n >= max_batches):
                return

    # ------------------------------------------------------------ convenience
    def check(self):
        """Authenticate and read business units. Returns a small report dict."""
        self.token(force=True)
        body = self.get("settings/v2/business-units", {"pageSize": 200, "page": 1})
        units = (body or {}).get("data") or []
        return {"authenticated": True, "businessUnits": [{"id": u.get("id"), "name": u.get("name"),
                                                            "active": u.get("active")} for u in units]}
