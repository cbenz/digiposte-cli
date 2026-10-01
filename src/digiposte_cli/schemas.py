"""Pydantic schemas used to validate external inputs (config file + CLI).

Central place for every value coming from the outside world (TOML file and
command-line options): types, allowed values and constraints are validated
here so downstream code can trust them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Shared validators ──────────────────────────────────────────────────


def _ensure_http_url(value: str) -> str:
    """Reject values that are not absolute http(s) URLs."""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be an absolute http(s) URL")
    return value


# ── Configuration file (TOML) ──────────────────────────────────────────

# Locations accepted by the Digiposte API.
_LOCATION_LITERALS = Literal["SAFE", "INBOX", "TRASH_SAFE", "TRASH_INBOX"]


class AuthFile(BaseModel):
    """[auth] section of the config file."""

    model_config = ConfigDict(extra="forbid")

    login_url: str = "https://secure.digiposte.fr/home"
    email: str = ""
    password: str = ""
    headless: bool = False
    # Dedicated Chrome (CDP) is the default auth path; set to false to fall
    # back to the Playwright-managed profile.
    use_running_chrome: bool = True

    @field_validator("login_url")
    @classmethod
    def _validate_login_url(cls, value: str) -> str:
        return _ensure_http_url(value)


class PlaywrightFile(BaseModel):
    """[playwright] section of the config file."""

    model_config = ConfigDict(extra="forbid")

    channel: str = "chrome"


class ApiFile(BaseModel):
    """[api] section of the config file."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://api.digiposte.fr/api/v3"
    locations: list[_LOCATION_LITERALS] = ["SAFE", "INBOX"]
    max_results: int = Field(default=1000, ge=1, le=100_000)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        return _ensure_http_url(value)


class RenameRule(BaseModel):
    """One regex substitution applied to a downloaded document file name."""

    model_config = ConfigDict(extra="forbid")

    pattern: str
    replacement: str = ""

    @model_validator(mode="after")
    def _validate_regex(self) -> RenameRule:
        # The replacement is validated against the pattern even with an empty
        # input (re.sub parses the replacement template regardless of matches),
        # so a bad back-reference such as \2 on a single-group pattern is
        # rejected here rather than at sync time.
        try:
            re.compile(self.pattern)
            re.sub(self.pattern, self.replacement, "")
        except re.error as exc:
            raise ValueError(f"invalid rename rule: {exc}") from exc
        return self


class DownloadFile(BaseModel):
    """[download] section of the config file (file-name renaming)."""

    model_config = ConfigDict(extra="forbid")

    rename_rules: list[RenameRule] = Field(default_factory=list)


class LogFile(BaseModel):
    """[log] section of the config file."""

    model_config = ConfigDict(extra="forbid")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("level", mode="before")
    @classmethod
    def _normalize_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value


class PathsFile(BaseModel):
    """[paths] section of the config file."""

    model_config = ConfigDict(extra="forbid")

    download_dir: str = ""
    profile_dir: str = ""
    debug_profile_dir: str = ""
    token_cache: str = ""


class ConfigFile(BaseModel):
    """Root model of the whole config file (all sections optional)."""

    model_config = ConfigDict(extra="forbid")

    auth: AuthFile = Field(default_factory=AuthFile)
    playwright: PlaywrightFile = Field(default_factory=PlaywrightFile)
    api: ApiFile = Field(default_factory=ApiFile)
    download: DownloadFile = Field(default_factory=DownloadFile)
    log: LogFile = Field(default_factory=LogFile)
    paths: PathsFile = Field(default_factory=PathsFile)


# ── CLI options ────────────────────────────────────────────────────────


class CliOptions(BaseModel):
    """Validated command-line options (built from the argparse namespace)."""

    model_config = ConfigDict(extra="ignore")

    config: Path | None = None
    download_dir: str | None = None
    playwright_channel: str | None = None
    headless: bool = False
    use_running_chrome: bool = False
    reset: bool = False
    verbose: bool = False
    # CLI flag `--json` is aliased to avoid clashing with BaseModel.json()
    as_json: bool = Field(default=False, validation_alias="json")
    # `list` only: sort key and direction (see render.SORT_KEYS).
    sort: Literal["date", "name", "size"] = "date"
    reverse: bool = False
    init: bool = False
