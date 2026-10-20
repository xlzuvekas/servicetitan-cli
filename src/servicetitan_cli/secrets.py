"""Secret storage for client secrets and app keys.

Backends, in order of preference:

``keyring``  The operating system credential store (Windows Credential
             Manager, macOS Keychain, Linux Secret Service / KWallet) through
             the ``keyring`` package. Secrets never touch disk in plain text.
``file``     ``credentials.json`` inside the CLI's config directory, created
             with owner-only permissions. Used when no keyring is available
             (headless Linux boxes, containers) and reported loudly.
``env``      Read-only: ``SERVICETITAN_CLIENT_SECRET_<CODE>`` style variables
             injected by an orchestrator or CI. Always consulted first.

Secrets are keyed ``<profile>/<tenant code>/<field>``; the profile store
records which fields are stored and in which backend, because keyrings
cannot be enumerated.
"""
import json
import os

from .errors import ConfigError
from .paths import credentials_path, write_private

SERVICE_NAME = "servicetitan-cli"
SECRET_FIELDS = ("clientSecret", "appKey")
ENV_NAMES = {"clientSecret": "SERVICETITAN_CLIENT_SECRET", "appKey": "SERVICETITAN_APP_KEY",
             "clientId": "SERVICETITAN_CLIENT_ID", "tenantId": "SERVICETITAN_TENANT_ID"}


def secret_key(profile, tenant_code, field):
    return "%s/%s/%s" % (profile, tenant_code, field)


def env_lookup(field, suffix=None, environ=None):
    """``SERVICETITAN_<FIELD>_<SUFFIX>`` first, then the unsuffixed name."""
    environ = os.environ if environ is None else environ
    base = ENV_NAMES[field]
    if suffix:
        v = environ.get("%s_%s" % (base, suffix.upper())) or environ.get("%s_%s" % (base, suffix))
        if v:
            return v.strip()
    v = environ.get(base)
    return v.strip() if v else None


class _KeyringBackend(object):
    name = "keyring"

    def __init__(self):
        import keyring  # noqa: F401  (import error handled by caller)
        from keyring import errors as keyring_errors
        self._keyring = keyring
        self._errors = keyring_errors
        backend = keyring.get_keyring()
        cls = type(backend).__module__ + "." + type(backend).__name__
        if "fail" in cls or "null" in cls:
            raise RuntimeError("no usable keyring backend (%s)" % cls)
        # Probe: chainer backends can look fine and still have nothing behind them.
        try:
            keyring.get_password(SERVICE_NAME, "__probe__")
        except Exception as e:  # NoKeyringError, InitError, dbus failures ...
            raise RuntimeError("keyring backend %s is not usable: %s" % (cls, e))
        self.detail = cls

    def get(self, key):
        try:
            return self._keyring.get_password(SERVICE_NAME, key)
        except Exception as e:
            raise ConfigError("Could not read secret %r from the OS keyring: %s" % (key, e),
                              hint="If the keyring is locked or unavailable, provide the value through an "
                                   "environment variable instead (see `servicetitan tenant --help`).")

    def set(self, key, value):
        self._keyring.set_password(SERVICE_NAME, key, value)

    def delete(self, key):
        try:
            self._keyring.delete_password(SERVICE_NAME, key)
        except self._errors.PasswordDeleteError:
            pass


class _FileBackend(object):
    name = "file"
    detail = None

    def __init__(self, path=None):
        self.path = path or credentials_path()
        self.detail = self.path

    def _load(self):
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding="utf-8") as f:
            try:
                return json.load(f) or {}
            except json.JSONDecodeError as e:
                raise ConfigError("%s is not valid JSON: %s" % (self.path, e),
                                  hint="Fix or delete the file, then re-add the affected tenants' secrets.")

    def _save(self, data):
        write_private(self.path, json.dumps(data, indent=2, sort_keys=True))

    def get(self, key):
        return self._load().get(key)

    def set(self, key, value):
        data = self._load()
        data[key] = value
        self._save(data)

    def delete(self, key):
        data = self._load()
        if key in data:
            del data[key]
            self._save(data)


class SecretStore(object):
    """Facade over the backends. ``preference`` is ``auto``, ``keyring`` or ``file``."""

    def __init__(self, preference="auto", file_path=None):
        self.preference = preference or "auto"
        self._file_path = file_path
        self._backend = None
        self.fallback_reason = None

    @property
    def backend(self):
        if self._backend is None:
            self._backend = self._select()
        return self._backend

    def _select(self):
        pref = self.preference
        if pref == "file":
            return _FileBackend(self._file_path)
        try:
            return _KeyringBackend()
        except ImportError:
            reason = "the `keyring` package is not installed (pip install keyring)"
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:  # keyring backends can fail in non-Exception ways (native panics)
            reason = "%s: %s" % (type(e).__name__, str(e)[:200] or "no usable backend")
        if pref == "keyring":
            raise ConfigError("The OS keyring is not usable: %s" % reason,
                              hint="Install/unlock a keyring, or run with `--secrets file` to keep secrets in "
                                   "an owner-only file under the config directory.")
        self.fallback_reason = reason
        return _FileBackend(self._file_path)

    @property
    def name(self):
        return self.backend.name

    @property
    def detail(self):
        return getattr(self.backend, "detail", None)

    def get(self, key):
        return self.backend.get(key)

    def set(self, key, value):
        if value is None or value == "":
            raise ConfigError("Refusing to store an empty secret for %s" % key)
        self.backend.set(key, value)

    def delete(self, key):
        self.backend.delete(key)

    def describe(self):
        d = {"backend": self.name}
        if self.detail:
            d["detail"] = self.detail
        if self.fallback_reason:
            d["fallbackReason"] = self.fallback_reason
        return d
