import json
import time

import pytest
import requests

from servicetitan_cli.api import ServiceTitanClient, TokenCache, WriteAuthorization, MAX_PAGE_SIZE
from servicetitan_cli.config import Credentials
from servicetitan_cli.errors import ApiError, AuthError, UsageError


class FakeResponse(object):
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body) if body is not None else ""
        self.content = self.text.encode()
        self.ok = 200 <= status < 300

    def json(self):
        return self._body


class FakeSession(object):
    """Records every request; answers from a script keyed by (method, url-substring)."""

    def __init__(self, script):
        self.script = script
        self.calls = []
        self.headers = {}

    def post(self, url, timeout=None, data=None):
        self.calls.append(("POST", url, data))
        return FakeResponse(200, {"access_token": "tok-%d" % len(self.calls), "expires_in": 900, "token_type": "Bearer",
                                  "scope": "tn.stt.businessunits:r tn.jpm.jobs:r"})

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, params, headers))
        return self._answer(url)

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        """The single write path; records the body as the fifth element."""
        self.calls.append((method, url, params, headers, json))
        return self._answer(url)

    def _answer(self, url):
        for key, responses in self.script.items():
            if key in url:
                if isinstance(responses, list):
                    return responses.pop(0) if len(responses) > 1 else responses[0]
                return responses
        return FakeResponse(404, {"title": "Not Found"})


def creds(env="production"):
    return Credentials("NORTH", "North Branch", "555", "cid", "secret", "appkey", env)


def client(script, env="production", **kw):
    sess = FakeSession(script)
    c = ServiceTitanClient(creds(env), session=sess, token_cache=TokenCache(enabled=False), max_retries=2, **kw)
    return c, sess


def test_resolve_url_forms():
    c, _ = client({})
    base = "https://api.servicetitan.io"
    assert c.resolve_url("jpm/v2/jobs") == base + "/jpm/v2/tenant/555/jobs"
    assert c.resolve_url("/jpm/v2/tenant/{tenant}/jobs") == base + "/jpm/v2/tenant/555/jobs"
    assert c.resolve_url("/jpm/v2/tenant/555/jobs/9") == base + "/jpm/v2/tenant/555/jobs/9"
    assert c.resolve_url("telecom/v3/calls") == base + "/telecom/v3/tenant/555/calls"
    assert c.resolve_url(base + "/crm/v2/tenant/{tenant}/customers") == base + "/crm/v2/tenant/555/customers"
    with pytest.raises(UsageError):
        c.resolve_url("https://evil.example.com/jpm/v2/tenant/555/jobs")
    with pytest.raises(UsageError):
        c.resolve_url("jobs")
    ci, _ = client({}, env="integration")
    assert ci.resolve_url("jpm/v2/jobs").startswith("https://api-integration.servicetitan.io/")
    assert ci.auth_url == "https://auth-integration.servicetitan.io/connect/token"


def test_reads_never_send_anything_but_get_to_the_api_host():
    c, sess = client({"business-units": FakeResponse(200, {"data": [{"id": 1, "name": "Service"}], "hasMore": False})})
    rep = c.check()
    assert rep["grantedScopes"] == ["tn.jpm.jobs:r", "tn.stt.businessunits:r"]
    assert [x for x in sess.calls if x[0] == "GET"][0][2]["active"] == "Any"
    non_get = [x for x in sess.calls if x[0] != "GET"]
    assert non_get and all(x[0] == "POST" and x[1] == c.auth_url for x in non_get)   # only the token request
    # the client has exactly one way to send a write (`write`), and it demands a WriteAuthorization
    assert not [n for n in dir(ServiceTitanClient) if n in ("post", "put", "patch", "delete")]
    get = [x for x in sess.calls if x[0] == "GET"][0]
    assert get[3]["ST-App-Key"] == "appkey" and get[3]["Authorization"].startswith("Bearer ")


def _authorization():
    return WriteAuthorization("acme", "NORTH", "jpm jobs create", "tn.jpm.jobs:w",
                              {"flag": "--write", "allowlist": "jpm/jobs", "scope": "granted"})


def test_write_requires_authorization_and_is_not_retried_on_5xx(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    auth = _authorization()
    c, sess = client({"jobs": FakeResponse(201, {"id": 9})})
    with pytest.raises(ApiError) as ei:
        c.write("POST", "jpm/v2/jobs", body={"a": 1})
    assert ei.value.code == "WRITE"
    with pytest.raises(ApiError):
        c.write("POST", "jpm/v2/jobs", body={"a": 1}, authorization={"flag": "--write"})   # a dict is not an authorization
    with pytest.raises(UsageError):
        c.write("GET", "jpm/v2/jobs", authorization=auth)
    assert not [x for x in sess.calls if x[1] != c.auth_url]   # nothing reached the API host
    res = c.write("POST", "jpm/v2/jobs", params={"x": None, "y": 1}, body={"a": 1}, authorization=auth)
    assert res == {"status": 201, "body": {"id": 9}}
    sent = [x for x in sess.calls if x[1] != c.auth_url]
    assert len(sent) == 1 and sent[0][0] == "POST" and sent[0][1].endswith("/jpm/v2/tenant/555/jobs")
    assert sent[0][2] == {"y": 1} and sent[0][4] == {"a": 1} and sent[0][3]["ST-App-Key"] == "appkey"
    assert auth.describe()["gates"]["allowlist"] == "jpm/jobs"
    # 5xx: surfaced at once, never repeated (the write may already have been applied)
    c2, sess2 = client({"jobs": [FakeResponse(503, {}), FakeResponse(201, {"id": 10})]})
    with pytest.raises(ApiError) as ei:
        c2.write("POST", "jpm/v2/jobs", body={}, authorization=auth)
    assert ei.value.status == 503 and len([x for x in sess2.calls if x[1] != c2.auth_url]) == 1
    # a dropped connection: same rule, and the hint says the outcome is unknown

    class Dropping(FakeSession):
        def request(self, method, url, **kw):
            self.calls.append((method, url))
            raise requests.ConnectionError("reset")
    sess3 = Dropping({})
    c3 = ServiceTitanClient(creds(), session=sess3, token_cache=TokenCache(enabled=False), max_retries=2)
    with pytest.raises(ApiError) as ei:
        c3.write("POST", "jpm/v2/jobs", body={}, authorization=auth)
    assert "may or may not have been applied" in ei.value.hint and len([x for x in sess3.calls if x[1] != c3.auth_url]) == 1
    # 429 (rejected, nothing applied) and a stale token (401) are each retried once
    c4, sess4 = client({"jobs": [FakeResponse(429, {}, {"Retry-After": "2"}), FakeResponse(401, {}),
                                 FakeResponse(200, {"ok": True})]})
    assert c4.write("PATCH", "jpm/v2/jobs/1", body={}, authorization=auth)["body"] == {"ok": True}
    assert sleeps == [2.0] and len([x for x in sess4.calls if x[1] == c4.auth_url]) == 2
    assert len([x for x in sess4.calls if x[1] != c4.auth_url]) == 3


def test_paging_follows_has_more_and_caps():
    pages = [FakeResponse(200, {"data": [1, 2], "hasMore": True, "page": 1}),
             FakeResponse(200, {"data": [3], "hasMore": True, "page": 2}),
             FakeResponse(200, {"data": [4], "hasMore": False, "page": 3})]
    c, sess = client({"jobs": pages})
    assert c.get_all("jpm/v2/jobs") == [1, 2, 3, 4]
    params = [x[2] for x in sess.calls if x[0] == "GET"]
    assert [p["page"] for p in params] == [1, 2, 3] and params[0]["pageSize"] == 200
    pages2 = [FakeResponse(200, {"data": [1], "hasMore": True}), FakeResponse(200, {"data": [2], "hasMore": True})]
    c2, _ = client({"jobs": pages2})
    assert c2.get_all("jpm/v2/jobs", max_pages=1) == [1] and c2.last_truncated
    c3, _ = client({}, page_size=99999)
    assert c3.page_size == MAX_PAGE_SIZE


def test_401_refreshes_token_once_then_fails():
    c, sess = client({"jobs": [FakeResponse(401, {"title": "Unauthorized"}), FakeResponse(200, {"data": [], "hasMore": False})]})
    c.get("jpm/v2/jobs")
    assert len([x for x in sess.calls if x[0] == "POST"]) == 2
    c2, _ = client({"jobs": FakeResponse(401, {"title": "Unauthorized"})})
    with pytest.raises(AuthError):
        c2.get("jpm/v2/jobs")


def test_429_and_5xx_are_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    c, _ = client({"jobs": [FakeResponse(429, {}, {"Retry-After": "3"}), FakeResponse(503, {}),
                            FakeResponse(200, {"data": [7], "hasMore": False})]})
    assert c.get_all("jpm/v2/jobs") == [7]
    assert sleeps[0] == 3.0
    c2, _ = client({"jobs": FakeResponse(403, {"title": "Forbidden"})})
    with pytest.raises(ApiError) as ei:
        c2.get("jpm/v2/jobs")
    assert ei.value.status == 403 and "scope" in ei.value.hint


def test_export_follows_continue_from():
    batches = [FakeResponse(200, {"data": [1], "continueFrom": "t1", "hasMore": True}),
               FakeResponse(200, {"data": [2], "continueFrom": "t2", "hasMore": False})]
    c, sess = client({"export/jobs": batches})
    got = list(c.export("jpm/v2/export/jobs", from_token="2026-01-01", include_recent_changes=True))
    assert [b[0] for b in got] == [[1], [2]] and got[-1][1] == "t2" and got[-1][2] is False
    first = [x for x in sess.calls if x[0] == "GET"][0][2]
    assert first["from"] == "2026-01-01" and first["includeRecentChanges"] == "true"


def test_token_cache_reuses_and_expires(tmp_path):
    cache = TokenCache(str(tmp_path / "tokens.json"))
    cache.put("production:cid", "abc", 900)
    assert cache.get("production:cid") == "abc"
    cache.put("production:old", "zzz", 30)  # inside the refresh margin -> treated as expired
    assert cache.get("production:old") is None
    cache.drop("production:cid")
    assert cache.get("production:cid") is None
    sess = FakeSession({})
    c = ServiceTitanClient(creds(), session=sess, token_cache=cache)
    c.token()
    c2 = ServiceTitanClient(creds(), session=FakeSession({}), token_cache=cache)
    assert c2.token() == c.token() and len([x for x in sess.calls if x[0] == "POST"]) == 1


def test_bad_credentials_is_auth_error():
    class RejectingSession(FakeSession):
        def post(self, url, timeout=None, data=None):
            return FakeResponse(400, {"error": "invalid_client"})
    sess = RejectingSession({})
    c = ServiceTitanClient(creds(), session=sess, token_cache=TokenCache(enabled=False))
    with pytest.raises(AuthError) as ei:
        c.token()
    assert ei.value.exit_code == 4


def test_problem_details_are_surfaced():
    body = {"type": "https://tools.ietf.org/html/rfc9110#section-15.5.1", "title": "One or more validation errors occurred.",
            "status": 400, "traceId": "00-abc", "errors": {"pageSize": ["The value 'x' is not valid."]}}
    c, _ = client({"jobs": FakeResponse(400, body)})
    with pytest.raises(ApiError) as ei:
        c.get("jpm/v2/jobs")
    assert ei.value.details["problem"]["errors"]["pageSize"] and "validation" in ei.value.message
    assert ServiceTitanClient.problem_details("not json") is None
