"""digiposte-cli: command-line interface for your Digiposte vault.

Subcommands:
  sync     authenticate then download the documents
  list     list the documents without downloading (alias: ls)
  login    ensure a valid token (browser login if needed)
  logout   delete the cached token (--reset wipes the browser sessions too)
  status   show configuration and authentication state
  config   show / initialize the configuration file

Configuration: a TOML file (default ~/.config/digiposte-cli/config.toml),
overridable with the global --config option.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from digiposte_cli import paths
from digiposte_cli.api import DigiposteAPI
from digiposte_cli.auth import (
    AuthInfo,
    load_token_cache,
    login_via_own_chrome,
    manual_login,
    refresh_session_token,
    save_token_cache,
)
from digiposte_cli.config import (
    DEFAULT_CONFIG_FILE,
    Config,
    ConfigurationError,
    create_default_config,
    load_config,
)
from digiposte_cli.render import (
    SORT_KEYS,
    DocumentEntry,
    document_date,
    document_size,
    render_document_tree,
    sort_documents,
)
from digiposte_cli.schemas import CliOptions

log = logging.getLogger("digiposte")

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def _setup_logging(level: str) -> None:
    """(Re)configure logging with the requested level (on stderr, so stdout
    stays usable for data output such as `ls --json`)."""
    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    logging.getLogger("digiposte").setLevel(level)
    # Playwright drives its Node driver through an asyncio subprocess whose
    # transport logs raw noise ("execute program …", "… exited with return
    # code 0") — silence it so the CLI output stays clean.
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)


def _resolve_secret(value: str) -> str:
    """If the value starts with 'command:', run the command through the user's
    shell and return its output. Otherwise return the value as-is."""
    if not value.startswith("command:"):
        return value
    cmd = value[len("command:") :].strip()
    shell = os.environ.get("SHELL", "/bin/sh")
    try:
        # Non-interactive shell: avoids loading the user's startup files, which
        # can print noise or hang when spawned from a subprocess.
        result = subprocess.run(
            [shell, "-c", cmd],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.warning("⚠️  Secret command failed (%s): %s", cmd, exc)
        return ""
    return result.stdout.rstrip("\n")


def _report_validation_error(source: str, exc: ValidationError) -> None:
    """Log a pydantic validation error in a human-readable way."""
    log.error("Invalid %s:", source)
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        log.error("  - %s: %s", loc or "<value>", error["msg"])


# ── Argument parsing (subcommands) ─────────────────────────────────────


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Path to the TOML configuration file (default: {DEFAULT_CONFIG_FILE})",
    )
    parser.add_argument(
        "--playwright-channel",
        type=str,
        default=None,
        help="Playwright browser channel (default: chrome). Empty = bundled Chromium.",
    )
    parser.add_argument(
        "--use-running-chrome",
        action="store_true",
        help="Force the dedicated-Chrome (CDP) login — enabled by default; "
        "disable it with `use_running_chrome = false` in the config.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser without a window (only if already authenticated)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose logging (DEBUG)",
    )


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_options(common)

    parser = argparse.ArgumentParser(
        prog="digiposte-cli",
        description="Manage and download documents from your personal Digiposte vault.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_sync = sub.add_parser("sync", parents=[common], help="Authenticate and download documents")
    p_sync.add_argument(
        "--download-dir",
        type=str,
        default=None,
        help=f"Destination directory (default: {paths.download_dir()})",
    )
    p_sync.set_defaults(func=cmd_sync)

    p_list = sub.add_parser(
        "list",
        parents=[common],
        aliases=["ls"],
        help="List documents without downloading",
    )
    p_list.add_argument(
        "--json", action="store_true", help="Output the document list as JSON on stdout"
    )
    p_list.add_argument(
        "--sort",
        choices=list(SORT_KEYS),
        default="date",
        help="Sort documents by this field (default: date). Natural order: "
        "date = newest first, name = A→Z (file name), size = largest first.",
    )
    p_list.add_argument("--reverse", action="store_true", help="Reverse the sort order")
    p_list.set_defaults(func=cmd_list)

    sub.add_parser(
        "login",
        parents=[common],
        help="Ensure a valid token (reuse the cache, or log in through a browser)",
    ).set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", parents=[common], help="Sign out (delete the cached token)")
    p_logout.add_argument(
        "--reset",
        action="store_true",
        help="Also delete the browser sessions (dedicated Chrome profile) — full sign-out",
    )
    p_logout.set_defaults(func=cmd_logout)

    sub.add_parser(
        "status", parents=[common], help="Show the configuration and authentication state"
    ).set_defaults(func=cmd_status)

    p_config = sub.add_parser(
        "config", parents=[common], help="Show or initialize the configuration file"
    )
    p_config.add_argument(
        "--init",
        action="store_true",
        help="Write the default template (if missing) and print its path",
    )
    p_config.set_defaults(func=cmd_config)

    return parser


# ── Shared helpers ─────────────────────────────────────────────────────


def _bootstrap_config(opts: CliOptions) -> tuple[Config, Path]:
    """Resolve + create the config file, load/validate it and set up logging.
    Returns (cfg, config_path)."""
    config_path = opts.config or DEFAULT_CONFIG_FILE

    if not config_path.exists():
        if create_default_config(config_path):
            log.info("📄 No config file found — wrote a default template to %s", config_path)
        else:
            log.warning("⚠️  Config file %s is missing and could not be created.", config_path)

    cfg = load_config(config_path)

    level_name = "DEBUG" if opts.verbose else cfg.log_level
    _setup_logging(level_name)
    log.info("📄 Config: %s", paths.display_path(config_path))
    return cfg, config_path


def _bootstrap_paths(opts: CliOptions, cfg: Config) -> dict[str, Any]:
    """Compute the resolved paths/channel/headless from the config + CLI."""
    profile_dir = cfg.profile_dir or paths.profile_dir()
    cache_path = cfg.token_cache or paths.token_cache_path()
    if opts.download_dir:
        download_dir = Path(opts.download_dir)
    else:
        download_dir = cfg.download_dir or paths.download_dir()
    channel = (
        opts.playwright_channel if opts.playwright_channel is not None else cfg.playwright_channel
    )
    headless = opts.headless or cfg.headless
    use_running_chrome = opts.use_running_chrome or cfg.use_running_chrome
    debug_profile_dir = cfg.debug_profile_dir or paths.debug_profile_dir()

    profile_dir.mkdir(parents=True, exist_ok=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if use_running_chrome:
        debug_profile_dir.mkdir(parents=True, exist_ok=True)
    return {
        "profile_dir": profile_dir,
        "cache_path": cache_path,
        "download_dir": download_dir,
        "channel": channel,
        "headless": headless,
        "use_running_chrome": use_running_chrome,
        "debug_profile_dir": debug_profile_dir,
    }


def _authenticate(opts: CliOptions, cfg: Config, ctx: dict[str, Any]) -> AuthInfo | None:
    """Return a valid AuthInfo: the cached token if it is still valid, otherwise
    a (silent or interactive) browser login. None if authentication failed or
    was cancelled."""
    cache_path = ctx["cache_path"]
    cached = load_token_cache(cache_path)
    if cached is not None:
        log.info("♻️  Cached token still valid — no browser needed.")
        return cached
    log.info("🔑 No valid cached token.")

    if ctx["use_running_chrome"]:
        # The Playwright profile is flagged by La Poste's anti-bot (blank
        # CAPTCHA). Instead, log in through a dedicated Chrome (its own
        # --user-data-dir) that we launch with the DevTools port and attach to
        # over CDP: it is a real, human-operated browser, so the CAPTCHA is
        # solvable there and the session is reused across runs.
        auth = login_via_own_chrome(
            login_url=cfg.login_url,
            user_data_dir=ctx["debug_profile_dir"],
            email=cfg.email,
            password=_resolve_secret(cfg.password),
        )
        if auth is not None and auth.is_valid():
            save_token_cache(auth, cache_path)
        return auth

    # Legacy Playwright profile path: if the account was logged in before and
    # we are in headed mode, try a silent headless refresh first (the SSO
    # session persisted in the profile may still be active). Otherwise fall
    # back to a visible browser below.
    if cache_path.exists() and not ctx["headless"]:
        silent = refresh_session_token(
            login_url=cfg.login_url,
            user_data_dir=ctx["profile_dir"],
            channel=ctx["channel"],
        )
        if silent is not None and silent.is_valid():
            log.info("♻️  Token refreshed silently from the session.")
            save_token_cache(silent, cache_path)
            return silent

    auth = manual_login(
        login_url=cfg.login_url,
        user_data_dir=ctx["profile_dir"],
        email=cfg.email,
        password=_resolve_secret(cfg.password),
        channel=ctx["channel"],
        headless=ctx["headless"],
    )
    if auth is not None and auth.is_valid():
        save_token_cache(auth, cache_path)
    return auth


def _build_api(auth: AuthInfo, cfg: Config) -> DigiposteAPI:
    return DigiposteAPI(
        access_token=auth.access_token,
        base_url=cfg.api_base_url,
        locations=cfg.locations,
        max_results=cfg.max_results,
        rename_rules=cfg.rename_rules,
    )


# ── Subcommands ────────────────────────────────────────────────────────


def cmd_sync(opts: CliOptions) -> None:
    """Authenticate, list and download the documents."""
    cfg, _ = _bootstrap_config(opts)
    ctx = _bootstrap_paths(opts, cfg)

    log.info("=" * 50)
    log.info("🔐 Step 1: Digiposte authentication")
    log.info("=" * 50)
    auth = _authenticate(opts, cfg, ctx)
    if auth is None or not auth.is_valid():
        log.error("❌ Authentication failed — aborting")
        raise SystemExit(1)

    log.info("=" * 50)
    log.info("🌐 Step 2: Fetching documents through the API")
    log.info("=" * 50)
    api = _build_api(auth, cfg)
    try:
        documents = api.list_documents()
        if not documents:
            log.warning("⚠️  No document found.")
            return
        log.info("📄 %s document(s) to download", len(documents))

        log.info("=" * 50)
        log.info("⬇️  Step 3: Download")
        log.info("=" * 50)
        download_dir = ctx["download_dir"]
        download_dir.mkdir(parents=True, exist_ok=True)
        log.info("📁 Base dir: %s", paths.display_path(download_dir.resolve()))
        # Explicitly create one sub-folder per configured location (even when
        # empty), so the tree always mirrors the config.
        for location in api.locations:
            (download_dir / location).mkdir(parents=True, exist_ok=True)

        # Files are written into one sub-folder per location (INBOX, SAFE…),
        # mirroring the configured locations under the base dir. The on-disk
        # file name is the vault title sanitized + extension, with the
        # [download] rename_rules applied to the stem (api.file_name_for). A
        # document already on disk (same file name in its location folder) is
        # skipped: we only compare file names, no local content index. Before
        # downloading, files written by older versions (before the rules
        # existed, under the un-renamed name) are renamed instead of being
        # downloaded again under a second name.
        downloaded = 0
        skipped = 0
        renamed = 0
        for doc in documents:
            location = api.location_of(doc)
            dest = download_dir / location
            file_name = api.file_name_for(doc)
            path = dest / file_name

            if path.exists():
                log.info("⏭️  Already present (by filename): %s/%s", location, file_name)
                skipped += 1
                continue

            if api.rename_legacy(doc, dest) is not None:
                renamed += 1
                continue

            path = api.download_document(doc, destination=dest)
            if path:
                downloaded += 1

        # Post pass — rename the files whose source document is no longer
        # listed by the vault (deleted, moved to trash…) and were therefore
        # never seen above: applying the rules to every on-disk name is
        # idempotent (an already-canonical file is left alone).
        for folder in sorted(d for d in download_dir.iterdir() if d.is_dir()):
            renamed += api.rename_leftover(folder)

        log.info(
            "🎉 Done — %s downloaded, %s renamed, %s already present, in %s",
            downloaded,
            renamed,
            skipped,
            paths.display_path(download_dir.resolve()),
        )
    finally:
        api.close()


def cmd_list(opts: CliOptions) -> None:
    """Authenticate and print the document list (stdout)."""
    cfg, _ = _bootstrap_config(opts)
    ctx = _bootstrap_paths(opts, cfg)

    auth = _authenticate(opts, cfg, ctx)
    if auth is None or not auth.is_valid():
        log.error("❌ Authentication failed — aborting")
        raise SystemExit(1)

    api = _build_api(auth, cfg)
    download_dir = ctx["download_dir"]
    locations = list(api.locations)
    try:
        # Sort the documents and regroup them by location; each document is
        # checked against the download dir so the tree can flag the ones
        # already on disk. The displayed (and `--sort name`) value is the file
        # name `sync` writes — rename rules applied — not the vault title.
        documents = sort_documents(
            api.list_documents(),
            sort_key=opts.sort,
            reverse=opts.reverse,
            name_of=api.file_name_for,
        )
        entries_by_location: dict[str, list[DocumentEntry]] = {}
        for doc in documents:
            entries_by_location.setdefault(api.location_of(doc), []).append(
                DocumentEntry(
                    name=api.file_name_for(doc),
                    date=document_date(doc),
                    size=document_size(doc),
                    downloaded=api.is_downloaded(doc, download_dir),
                )
            )
    finally:
        api.close()

    if opts.as_json:
        json.dump(documents, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    render_document_tree(
        entries_by_location,
        download_dir=download_dir,
        locations=locations,
    )


def cmd_login(opts: CliOptions) -> None:
    """Ensure a valid token. If one is already cached, just report it (no
    browser). Otherwise authenticate — silently via the stored SSO session, or
    through a visible browser window if a real login is needed."""
    cfg, _ = _bootstrap_config(opts)
    ctx = _bootstrap_paths(opts, cfg)

    auth = load_token_cache(ctx["cache_path"])
    if auth is not None:
        log.info("✅ Already logged in — token still valid.")
        return

    auth = _authenticate(opts, cfg, ctx)
    if auth is None or not auth.is_valid():
        log.error("❌ Login failed — aborting")
        raise SystemExit(1)
    log.info("✅ Login successful, token cached.")


def cmd_logout(opts: CliOptions) -> None:
    """Delete the cached token. With --reset, also delete the browser profiles
    (dedicated Chrome session and legacy Playwright profile) — full sign-out."""
    cfg, _ = _bootstrap_config(opts)
    cache_path = cfg.token_cache or paths.token_cache_path()
    removed: list[str] = []
    if cache_path.exists():
        cache_path.unlink()
        removed.append(str(cache_path))

    if opts.reset:
        for profile in (
            cfg.profile_dir or paths.profile_dir(),
            cfg.debug_profile_dir or paths.debug_profile_dir(),
        ):
            if profile.exists():
                shutil.rmtree(profile, ignore_errors=True)
                removed.append(str(profile))

    if removed:
        log.info("🗑️  Removed: %s", ", ".join(paths.display_path(p) for p in removed))
    elif opts.reset:
        log.info("ℹ️  Already signed out — nothing to remove.")
    else:
        log.info("ℹ️  No cached token to remove (%s).", paths.display_path(cache_path))


def cmd_status(opts: CliOptions) -> None:
    """Show the configuration and authentication state (no network)."""
    cfg, config_path = _bootstrap_config(opts)
    cache_path = cfg.token_cache or paths.token_cache_path()

    print(f"Config file:     {paths.display_path(config_path)}")
    print(f"Login URL:       {cfg.login_url}")
    print(f"Channel:         {cfg.playwright_channel}  (headless: {cfg.headless})")
    debug_profile = cfg.debug_profile_dir or paths.debug_profile_dir()
    if cfg.use_running_chrome:
        print(f"Own Chrome (CDP):  yes (dedicated profile: {paths.display_path(debug_profile)})")
    else:
        print("Own Chrome (CDP):  no (Playwright profile)")
    print(f"API base URL:    {cfg.api_base_url}")
    print(
        f"Locations:       {', '.join(cfg.locations or ['SAFE', 'INBOX'])}  (max: {cfg.max_results})"
    )
    print(f"Download dir:    {paths.display_path(cfg.download_dir or paths.download_dir())}")
    print(f"Profile dir:     {paths.display_path(cfg.profile_dir or paths.profile_dir())}")
    print(f"Token cache:     {paths.display_path(cache_path)}")

    auth = load_token_cache(cache_path)
    if auth is None:
        print("Authentication:  ❌ no valid cached token (run `login` or `sync`)")
    else:
        expires = auth.expires_at
        exp = f" (expires at {expires})" if expires else ""
        print(f"Authentication:  ✅ cached token valid{exp}")


def cmd_config(opts: CliOptions) -> None:
    """Show (and optionally initialize) the configuration file."""
    config_path = opts.config or DEFAULT_CONFIG_FILE
    if opts.init:
        if create_default_config(config_path):
            log.info("📄 Wrote a default template to %s", paths.display_path(config_path))
        else:
            log.info("ℹ️  Config file already exists: %s", paths.display_path(config_path))
    print(paths.display_path(config_path))


# ── Entry point ────────────────────────────────────────────────────────


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Running the bare command (no subcommand) shows the help, like `-h`.
    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    # Default logging configured early (in case the config is generated below)
    _setup_logging("INFO")

    # Validate the command-line options with pydantic
    try:
        opts = CliOptions.model_validate(vars(args))
    except ValidationError as exc:
        _report_validation_error("command-line options", exc)
        raise SystemExit(2)

    try:
        args.func(opts)
    except ConfigurationError as exc:
        log.error("%s", exc)
        raise SystemExit(2)
    except KeyboardInterrupt:
        log.info("⏹️  Interrupted by the user")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
