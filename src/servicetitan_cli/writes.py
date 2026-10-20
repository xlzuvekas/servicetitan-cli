"""Write policy, request-body handling and the audit log for write operations.

A write runs only when three independent gates pass:

1. the caller asked for it on this invocation (``--write``);
2. the profile's write policy permits the operation. The default policy is
   ``auto``: follow the credential, i.e. allow exactly the operations whose
   ``:w`` scope ServiceTitan granted the app (the tenant admin approved those
   scopes when connecting the app). Explicit patterns (``module``,
   ``module/resource``, ``module/resource/operation``, ``*``) allow named
   operations instead of, or as well as, ``auto``; an empty list (``none``)
   turns writes off. ``SERVICETITAN_ALLOW_WRITES`` replaces the profile's
   policy when set;
3. the token ServiceTitan issued carries the operation's scope (``:w`` for
   nearly every write), checked on every call whatever the policy says.

Every executed write (and every dry run) is appended to ``audit.jsonl`` in
the config directory.
"""
import datetime as dt
import json
import os
import stat
import sys

from .dates import UTC, iso_z
from .errors import UsageError
from .paths import config_dir, ensure_dir

AUTO = "auto"                            # policy word: follow the credential's granted :w scopes
OFF = ("none", "off")                    # policy words meaning "no writes"


# ---------------------------------------------------------------- policy
def parse_patterns(text):
    """Comma/space separated policy words and patterns -> list. ``none`` contributes nothing."""
    out = []
    for raw in (text or "").replace(";", ",").split(","):
        for token in raw.split():
            token = token.strip().strip("/").lower()
            if token in OFF or not token or token in out:
                continue
            out.append(token)
    return out


def follows_credential(patterns):
    return AUTO in (patterns or [])


def matches(patterns, module, resource, operation):
    """The first explicit pattern permitting module/resource/operation, else None (``auto`` is decided by the token)."""
    target = [module.lower(), resource.lower(), operation.lower()]
    for pattern in patterns or []:
        if pattern == AUTO:
            continue
        parts = [p.strip() for p in pattern.lower().strip("/").split("/") if p.strip()]
        if not parts or len(parts) > 3:
            continue
        parts += ["*"] * (3 - len(parts))
        if all(p == "*" or p == t for p, t in zip(parts, target)):
            return pattern
    return None


def describe_policy(patterns):
    """One line a person can read: what the profile lets writes do."""
    patterns = list(patterns or [])
    explicit = [p for p in patterns if p != AUTO]
    if not patterns:
        return "off: no write runs, whatever the credential allows"
    if explicit and AUTO in patterns:
        return "auto (every operation whose :w scope the credential was granted) plus: %s" % ", ".join(explicit)
    if AUTO in patterns:
        return "auto: every operation whose :w scope the credential was granted"
    return "allowlist: %s (each still needs its :w scope on the token)" % ", ".join(explicit)


# ---------------------------------------------------------------- request body
def _parse_value(text):
    """`--set key=value`: JSON when it parses, else a plain string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def build_body(op, body_arg=None, body_stdin=False, sets=None):
    """Assemble and validate the JSON body for a write operation."""
    body = None
    if body_stdin:
        raw = sys.stdin.read()
        if not raw.strip():
            raise UsageError("Expected a JSON body on stdin")
        body = _load_json(raw, "stdin")
    elif body_arg is not None:
        if body_arg.startswith("@"):
            path = body_arg[1:]
            if not os.path.exists(path):
                raise UsageError("Body file %s does not exist" % path)
            with open(path, encoding="utf-8") as f:
                body = _load_json(f.read(), path)
        else:
            body = _load_json(body_arg, "--body")
    if sets:
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise UsageError("--set needs an object body")
        for kv in sets:
            if "=" not in kv:
                raise UsageError("--set expects KEY=VALUE, got %r" % kv)
            k, v = kv.split("=", 1)
            body[k.strip()] = _parse_value(v)
    schema = op.get("body") or {}
    if schema.get("properties") is not None and body is None and (schema.get("required") or schema.get("properties")):
        fields = ", ".join("%s%s" % (k, "*" if k in (schema.get("required") or []) else "") for k in schema["properties"])
        raise UsageError("%s %s needs a JSON body" % (op["method"], op["path"]),
                         hint="Pass --body '{...}', --body @file.json, --body-stdin, or --set key=value (repeatable). "
                              "Fields (* required): %s" % (fields or "(none documented)"))
    if isinstance(body, dict) and schema.get("required"):
        missing = [k for k in schema["required"] if k not in body]
        if missing:
            raise UsageError("Body is missing required field(s): %s" % ", ".join(missing),
                             hint="Required by the OpenAPI document; see --describe for the full body shape.")
    if isinstance(body, dict) and schema.get("properties"):
        unknown = [k for k in body if k not in schema["properties"]]
        if unknown:
            sys.stderr.write("warning: body field(s) not in the documented schema: %s\n" % ", ".join(unknown))
    return body


def _load_json(text, where):
    try:
        return json.loads(text)
    except ValueError as e:
        raise UsageError("%s is not valid JSON: %s" % (where, e))


# ---------------------------------------------------------------- confirmation
def confirm(preview, tenant_code, assume_yes=False, stdin=None, stderr=None):
    """Interactive confirmation when a person is at a terminal. Returns True to proceed.

    Non-interactive sessions (no TTY) proceed on the strength of --write and the
    allowlist; agents therefore need no prompt handling. A person must type the
    tenant code, so a stray Enter cannot confirm.
    """
    stdin = stdin or sys.stdin
    stderr = stderr or sys.stderr
    if assume_yes or not getattr(stdin, "isatty", lambda: False)():
        return True
    stderr.write("\nAbout to send %s %s\n" % (preview["method"], preview["url"]))
    if preview.get("params"):
        stderr.write("  params: %s\n" % json.dumps(preview["params"]))
    if preview.get("body") is not None:
        stderr.write("  body:   %s\n" % json.dumps(preview["body"])[:2000])
    stderr.write("This changes ServiceTitan tenant %s. Type the tenant code to confirm, anything else to abort: " % tenant_code)
    stderr.flush()
    answer = stdin.readline().strip()
    return answer.upper() == str(tenant_code).upper()


# ---------------------------------------------------------------- audit log
def audit_path():
    return os.path.join(config_dir(), "audit.jsonl")


def record(entry):
    """Append one JSON line (file created owner-only)."""
    path = audit_path()
    ensure_dir(os.path.dirname(path))
    entry = dict(entry)
    entry.setdefault("at", iso_z(dt.datetime.now(UTC)))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    return path


def read(limit=50, profile=None):
    path = audit_path()
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if profile and e.get("profile") != profile:
                continue
            rows.append(e)
    return rows[-limit:] if limit else rows
