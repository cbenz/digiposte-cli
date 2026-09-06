# digiposte-cli

Command-line tool to back up your **personal [Digiposte](https://www.laposte.fr/digiposte/) vault** (La Poste):
authenticate (cached token or a real Chrome), then list and download the documents
through the vault's internal API.

## How it works

1. **Authentication** — an `access_token` (valid ~1 h) is reused while still
   valid; otherwise the CLI authenticates through a **dedicated Chrome** that
   it starts itself with its own profile (`--user-data-dir`) and drives over
   the DevTools protocol (CDP). Because it is a real, human-operated Chrome,
   La Poste's anti-bot does not flag it: the CAPTCHA (if any) is solvable
   there, unlike with a Playwright-managed profile.
2. The token is obtained from `secure.digiposte.fr/rest/security/token` and
   cached on disk for the following runs.
3. **Listing** uses the per-location routes (`GET /documents/safe`,
   `GET /documents/inbox`, …); **downloading** uses
   `GET /document/{id}/content` on `api.digiposte.fr/api/v3`
   (`requests`, `Authorization: Bearer` header).

Details of the Chrome flow: the SSO session lives in a dedicated profile
(`~/.local/state/digiposte-cli/chrome-debug`), so a silent headless probe
reuses an alive session (no window, no CAPTCHA); only when a real login is
needed does a visible window open. Credentials (`email`/`password` from the
config) are pre-filled and submitted automatically there — you only solve the
CAPTCHA, if any — and the window is then closed (the session stays on disk).

## Installation

> ⚠️ `digiposte-cli` is **not published on PyPI**: you cannot install it with
> `pip install digiposte-cli` or `uvx digiposte-cli`. Use one of the methods
> below (all resolve the package from this Git repository).

### Run it directly with `uvx` (no install)

```bash
uvx --from git+https://github.com/cbenz/digiposte-cli.git digiposte-cli --help
```

`uvx` fetches the package from the repository into an **ephemeral** environment
and runs the command. Configuration, token cache and Chrome profile live in the
standard XDG directories (`~/.config`, `~/.local/state`, `~/.local/share`) and
are shared by every method below — so an ephemeral `uvx` run reuses an existing
session, but resolves the dependency again on every start (slower first command).

### Install it as a standalone command

```bash
uv tool install --from git+https://github.com/cbenz/digiposte-cli.git digiposte-cli
digiposte-cli --help
```

To update later: `uv tool upgrade digiposte-cli`.

### Development / local

```bash
uv sync                        # creates .venv and installs the dependencies
uv run digiposte-cli --help    # list the available commands

# or an editable install, always in sync with the working tree:
uv tool install --editable .
```

## Usage

```bash
# Show the configuration / authentication state (no network)
digiposte-cli status

# List the documents (both names work)
digiposte-cli list
digiposte-cli ls
digiposte-cli list --json     # raw JSON on stdout

# Authenticate then download everything (into <base>/<LOCATION>/…)
digiposte-cli sync
digiposte-cli sync --download-dir /some/dir

# Manage the session
digiposte-cli login           # if already logged in, just says so; else authenticates
digiposte-cli logout          # delete the cached token
digiposte-cli logout --reset  # full sign-out: token + Chrome profiles

# Configuration file
digiposte-cli config              # print its path
digiposte-cli config --init       # write the default template if missing
```

Global options (usable on any command): `--config PATH`,
`--playwright-channel CHANNEL`, `--use-running-chrome` (force the Chrome/CDP
login, which is the default), `--headless`, `--verbose`.

## Download layout

Files are written under the download base dir, in **one sub-folder per
configured location** (empty locations still get their folder, so the tree
mirrors the config):

```
~/Documents/digiposte/
├── SAFE/
└── INBOX/
    └── BULLETIN DE PAIE 01_2026.pdf
```

A file already present in its location folder (same sanitized file name) is
skipped — only file names are compared, there is no local content index.

## Configuration

TOML file (generated on first run): `~/.config/digiposte-cli/config.toml`
(use `--config` for another one). Every key is **commented out** by default:
uncomment a line to override its default.

```toml
[auth]        # login_url, email, password, headless, use_running_chrome
[playwright]  # channel = "chrome" (or "msedge", "" = bundled Chromium)
[api]         # base_url, locations = ["SAFE", "INBOX"], max_results = 1000
[log]         # level = "INFO" (DEBUG, INFO, WARNING, ERROR)
[paths]       # download_dir, profile_dir, debug_profile_dir, token_cache
```

The automatic pre-fill password can be produced by **any command** on your
machine through the `command:` prefix (no dependency on a specific manager):

```toml
[auth]
email = "you@example.com"
password = "command:pass show digiposte"
```

## Locations (XDG defaults)

| Data | Default path |
| --- | --- |
| Downloaded documents (one folder per location) | `~/Documents/digiposte` |
| Dedicated Chrome profile (login, anti-CAPTCHA) | `~/.local/state/digiposte-cli/chrome-debug` |
| Token cache (~1 h) | `~/.local/state/digiposte-cli/token.json` |
| Legacy Playwright profile (when `use_running_chrome = false`) | `~/.local/share/digiposte-cli/chrome-profile` |
| Configuration | `~/.config/digiposte-cli/config.toml` |

Everything can be overridden under `[paths]` in the config file.

## Development

```bash
uv run ruff check src/digiposte_cli/
uv run digiposte-cli --help
```

## Technical documentation

- [docs/AUTH.md](docs/AUTH.md) — internals of the browser authentication
  (dedicated Chrome + CDP, and the anti-CAPTCHA pitfalls).
- [docs/API.md](docs/API.md) (and the local swagger
  [docs/swagger.json](docs/swagger.json)) — the underlying HTTP API details.
