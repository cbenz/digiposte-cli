"""Default path resolution following the XDG specifications.

No application state is created inside the source directory. Everything is
stored under the standard XDG directories:
- browser profile   → $XDG_DATA_HOME/digiposte-cli
- session token     → $XDG_STATE_HOME/digiposte-cli
- documents         → ~/Documents/digiposte
"""

import os
from pathlib import Path

_APP = "digiposte-cli"


def _home() -> Path:
    return Path(os.environ.get("HOME", "~")).expanduser()


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (_home() / ".local" / "share"))


def xdg_state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or (_home() / ".local" / "state"))


def xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or (_home() / ".cache"))


def documents_dir() -> Path:
    """The ~/Documents directory (downloaded documents belong to the user)."""
    return _home() / "Documents"


def download_dir() -> Path:
    return documents_dir() / "digiposte"


def profile_dir() -> Path:
    """Persistent browser profile (the SSO session survives there)."""
    return xdg_data_home() / _APP / "chrome-profile"


def token_cache_path() -> Path:
    """API token cache (reusable authentication state, valid ~1 h)."""
    return xdg_state_home() / _APP / "token.json"
