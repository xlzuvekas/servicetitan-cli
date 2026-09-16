"""Error types with stable codes and exit statuses.

Agents branch on exit codes and on the ``error.code`` field of the JSON error
envelope, so these are part of the CLI's contract. See ``servicetitan schema``.
"""


class CliError(Exception):
    """Base class. ``code`` is a short machine-readable tag, ``hint`` says what
    to do about it, ``details`` is any extra structured context."""

    exit_code = 1
    code = "ERROR"

    def __init__(self, message, hint=None, details=None, code=None, exit_code=None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details
        if code:
            self.code = code
        if exit_code is not None:
            self.exit_code = exit_code

    def to_dict(self):
        err = {"code": self.code, "exit": self.exit_code, "message": self.message}
        if self.hint:
            err["hint"] = self.hint
        if self.details:
            err["details"] = self.details
        return {"error": err}


class UsageError(CliError):
    exit_code = 2
    code = "USAGE"


class ConfigError(CliError):
    exit_code = 3
    code = "CONFIG"


class AuthError(CliError):
    exit_code = 4
    code = "AUTH"


class ApiError(CliError):
    exit_code = 5
    code = "API"

    def __init__(self, message, status=None, body=None, url=None, **kw):
        details = dict(kw.pop("details", None) or {})
        if status is not None:
            details["status"] = status
        if url:
            details["url"] = url
        if body:
            details["body"] = body if len(body) <= 600 else body[:600] + "..."
        super().__init__(message, details=details or None, **kw)
        self.status = status


class ValidationError(CliError):
    exit_code = 6
    code = "VALIDATION"


class DataError(CliError):
    exit_code = 7
    code = "DATA"


EXIT_CODES = {
    0: "success",
    1: "unexpected error",
    2: "usage error (bad arguments)",
    3: "configuration problem (no profile, missing credentials, bad config, writes not allowed by the profile)",
    4: "authentication failed (client id/secret, app key or tenant id rejected; or the token lacks the scope a write needs)",
    5: "ServiceTitan API or network error",
    6: "validation failed (snapshot failed its integrity checks; nothing written)",
    7: "snapshot missing or stale (run `servicetitan fetch`)",
}
