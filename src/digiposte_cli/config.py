"""TOML configuration loading & generation.

Default file: ~/.config/digiposte-cli/config.toml
(overridable via the --config CLI option).

External values (the TOML content) are validated with pydantic (see
schemas.py). If the file does not exist, the CLI generates it on first run (see
`create_default_config`). Empty/missing values fall back to the XDG defaults
(see paths.py). Precedence: CLI option > config file > built-in default.
"""

import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from digiposte_cli import paths
from digiposte_cli.schemas import ConfigFile

DEFAULT_CONFIG_DIR = Path("~/.config/digiposte-cli").expanduser()
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.toml"


class ConfigurationError(Exception):
    """Raised when the configuration file is invalid."""


def _read_template() -> str:
    """Read the bundled configuration template (assets/config.toml).

    The template is shipped inside the package (src/digiposte_cli/assets/), so
    it is distributed with the wheel/sdist.
    """
    try:
        resource = resources.files(__package__).joinpath("assets", "config.toml")
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, ModuleNotFoundError) as exc:  # pragma: no cover
        # Minimal fallback: should not happen in a normal installation.
        return (
            "# digiposte-cli configuration (defaults apply when unset).\n"
            f"# Template not found in the package ({exc}).\n"
        )


def create_default_config(path: Path) -> bool:
    """Write the configuration template if `path` does not exist yet.
    Returns True if the file was created, False otherwise (exists or error)."""
    if path.exists():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_read_template())
        return True
    except OSError:
        return False


def _format_validation_error(exc: ValidationError, source: str) -> str:
    """Human-readable, one-line-per-error rendering of a pydantic error."""
    lines = [f"Invalid configuration in {source}:"]
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        lines.append(f"  - {loc or '<root>'}: {error['msg']}")
    return "\n".join(lines)


@dataclass
class Config:
    """Effective configuration of the tool (config file + resolved defaults)."""

    # network / credentials
    login_url: str = "https://secure.digiposte.fr/home"
    email: str = ""
    password: str = ""
    playwright_channel: str = "chrome"
    headless: bool = False
    # Dedicated Chrome (own --user-data-dir + CDP) is the DEFAULT auth path:
    # the legacy Playwright profile is flagged by La Poste's anti-bot. Set it
    # to False in the config to fall back to that profile.
    use_running_chrome: bool = True
    # api
    api_base_url: str = "https://api.digiposte.fr/api/v3"
    locations: list[str] | None = None
    max_results: int = 1000
    # logging
    log_level: str = "INFO"
    # paths (resolved: XDG defaults if absent from the config)
    download_dir: Path | None = None
    profile_dir: Path | None = None
    debug_profile_dir: Path | None = None
    token_cache: Path | None = None


def _resolve_path(value: str, xdg_default: Path) -> Path:
    if not value:
        return xdg_default
    return Path(value).expanduser()


def load_config(path: Path | None = None) -> Config:
    """Load and validate the TOML file, then return a complete Config.

    Raises ConfigurationError if the file cannot be parsed or validated.
    """
    file = path or DEFAULT_CONFIG_FILE
    cfg = Config()

    if not file.exists():
        # No file: simply apply the XDG defaults
        cfg.download_dir = paths.download_dir()
        cfg.profile_dir = paths.profile_dir()
        cfg.token_cache = paths.token_cache_path()
        return cfg

    try:
        raw: Any = tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"Invalid TOML in {file}: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"Cannot read {file}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigurationError(f"{file}: the root of the TOML must be a table.")

    try:
        parsed = ConfigFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(_format_validation_error(exc, str(file))) from exc

    cfg.login_url = str(parsed.auth.login_url)
    cfg.email = parsed.auth.email
    cfg.password = parsed.auth.password
    cfg.headless = parsed.auth.headless
    cfg.use_running_chrome = parsed.auth.use_running_chrome
    cfg.playwright_channel = parsed.playwright.channel
    cfg.api_base_url = str(parsed.api.base_url)
    cfg.locations = list(parsed.api.locations)
    cfg.max_results = parsed.api.max_results
    cfg.log_level = parsed.log.level

    cfg.download_dir = _resolve_path(parsed.paths.download_dir, paths.download_dir())
    cfg.profile_dir = _resolve_path(parsed.paths.profile_dir, paths.profile_dir())
    cfg.debug_profile_dir = _resolve_path(parsed.paths.debug_profile_dir, paths.debug_profile_dir())
    cfg.token_cache = _resolve_path(parsed.paths.token_cache, paths.token_cache_path())

    return cfg
