"""Where the CLI keeps its files.

Everything lives under one directory so a machine can be audited or wiped in
one place::

    $SERVICETITAN_HOME                       (override)
    %APPDATA%\\servicetitan-cli                Windows
    ~/Library/Application Support/servicetitan-cli   macOS
    $XDG_CONFIG_HOME/servicetitan-cli        Linux (default ~/.config/servicetitan-cli)

Files:
    profiles.json      companies, tenants, dashboard settings (no secrets)
    credentials.json   secrets, only when the OS keyring is unavailable (mode 0600)
    tokens.json        short-lived access-token cache (mode 0600)
    snapshots/         one <profile>.json data snapshot per profile
"""
import os
import stat
import sys

APP_DIR_NAME = "servicetitan-cli"


def config_dir():
    override = os.environ.get("SERVICETITAN_HOME")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(base, APP_DIR_NAME)


def profiles_path():
    return os.path.join(config_dir(), "profiles.json")


def credentials_path():
    return os.path.join(config_dir(), "credentials.json")


def token_cache_path():
    return os.path.join(config_dir(), "tokens.json")


def snapshots_dir():
    return os.path.join(config_dir(), "snapshots")


def snapshot_path(profile_name):
    return os.path.join(snapshots_dir(), "%s.json" % profile_name)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def write_private(path, text):
    """Write a file readable only by the current user (best effort on Windows,
    where the per-user profile directory is already access-controlled)."""
    ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if not sys.platform.startswith("win"):
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, path)
    return path
