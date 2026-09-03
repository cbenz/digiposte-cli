# digiposte-cli

Command-line tool to back up your **personal Digiposte vault** (La Poste): log
in (cached token or browser), then list and download the documents through the
vault's internal API.

## How it works

1. **Authentication**: an `access_token` (valid ~1 h) is reused if still valid;
   otherwise a Chrome window opens for a manual login (La Poste SSO, a CAPTCHA
   may need to be solved by hand).
2. The token is obtained from `secure.digiposte.fr/rest/security/token` and
   cached on disk for the following runs.
3. **Listing & downloading** of the documents through `api.digiposte.fr/api/v3`
   (`requests`, `Authorization: Bearer` header).

A **persistent Chrome profile** keeps the SSO session, so the browser opens only
rarely; credential re-entry can also be automated if you provide them.

## Installation

```bash
uv sync                        # creates .venv and installs the dependencies
uv run digiposte-cli --help    # list the available commands
```

To use Playwright's bundled Chromium (instead of the system Google Chrome),
install it once: `uv run playwright install chromium`.

## Usage

```bash
# Show the configuration / authentication state (no network)
uv run digiposte-cli status

# List the documents
uv run digiposte-cli ls
uv run digiposte-cli ls --folder-id <id>
uv run digiposte-cli ls --json

# Authenticate then download everything
uv run digiposte-cli sync
uv run digiposte-cli sync --folder-id <id>
uv run digiposte-cli sync --download-dir /some/dir

# Manage the session
uv run digiposte-cli login      # force a browser login and refresh the token
uv run digiposte-cli logout     # delete the cached token

# Configuration file
uv run digiposte-cli config            # print its path
uv run digiposte-cli config --init     # write the default template if missing
```

Global options (usable on any command): `--config PATH`,
`--playwright-channel CHANNEL`, `--headless`, `--verbose`.

## Configuration

TOML file (generated on first run): `~/.config/digiposte-cli/config.toml`
(use `--config` for another one). Every key is **commented out** by default:
uncomment a line to override its default.

```toml
[auth]        # login_url, email, password, headless
[playwright]  # channel = "chrome" (or "msedge", "" = bundled Chromium)
[api]         # base_url, locations = ["SAFE","INBOX"], max_results = 1000
[log]         # level = "INFO" (DEBUG, INFO, WARNING, ERROR)
[paths]       # download_dir, profile_dir, token_cache
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
|---|---|
| Downloaded documents | `~/Documents/digiposte` |
| Persistent browser profile | `~/.local/share/digiposte-cli/chrome-profile` |
| Token cache (~1 h) | `~/.local/state/digiposte-cli/token.json` |
| Configuration | `~/.config/digiposte-cli/config.toml` |

Everything can be overridden under `[paths]` in the config file.

## Development

```bash
uv run ruff check src/digiposte_cli/
uv run digiposte-cli --help
```

## Technical documentation

See [docs/API.md](docs/API.md) (and the local swagger
[docs/swagger.json](docs/swagger.json)) for the underlying HTTP API details.
