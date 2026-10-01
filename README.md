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
digiposte-cli ls --sort name          # sort by file name (A→Z)
digiposte-cli ls --sort size --reverse # smallest first
digiposte-cli list --json     # raw JSON on stdout (includes the document ids)

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

## Listing (`list` / `ls`)

The list is printed as a **tree**: the download directory as the root, one
branch per configured location (`SAFE`, `INBOX`… — empty ones are shown too)
and one line per document:

```
~/Documents/digiposte (13 document(s))
├── SAFE (0)
└── INBOX (13)
    ├── ✓ Bulletin de paie 2026-08.pdf  259.2 KB  2026-09-04
    └── ✗ Bulletin de paie 2026-07.pdf  260.0 KB  2026-07-30
```

- Each line shows the **file name that `sync` writes** — the vault title +
  extension, then the [`[download] rename_rules`](#file-name-renaming-download-rename_rules)
  applied — so the tree reads exactly like the folder on disk (the raw vault
  title is not shown). It is the same value used by the `✓`/`✗` check mark and
  by `--sort name`.
- The check mark tells whether the document is **already on disk** in its
  location folder (`✓` present, `✗` missing), using the same rule as `sync`.
- The **size** and the **metadata date** complete the line. The date is the
  `creation_date` field returned by the listing routes (falling back to
  `publishedOrCreationDate` / `publishedAt` / `createdAt` / … on the detail
  schema), formatted `YYYY-MM-DD`.
- `--sort date|name|size` selects the sort key (default `date`; natural order:
  `date` = newest first, `name` = A→Z, `size` = largest first) and `--reverse`
  flips it. The default `date` sort uses the metadata date above, never the
  file name, so it does not depend on the rename rules. Documents without a
  date/size are listed last.
- Document ids are not printed in the tree; use `list --json` for the raw API
  payload (ids included), sorted the same way.

## Download layout

Files are written under the download base dir, in **one sub-folder per
configured location** (empty locations still get their folder, so the tree
mirrors the config):

```
~/Documents/digiposte/
├── SAFE/
└── INBOX/
    └── Bulletin de paie 2026-02.pdf
```

The on-disk name is the sanitized vault title + extension. Because Digiposte
file names depend on the **vault content** (not dictated by the API), you can
rewrite them with regex substitutions through the
[`[download] rename_rules`](#file-name-renaming-download-rename_rules)
config — e.g. to turn `BULLETIN DE PAIE 02_2026.pdf` (month first, ALL CAPS)
into a normal, readable `Bulletin de paie 2026-02.pdf` that sorts
chronologically.

A file already present in its location folder (same file name after the rules
are applied) is skipped — only file names are compared, there is no local
content index. A file left by an earlier run under the *un-renamed* name is
renamed automatically instead of being downloaded twice — including files
whose source document is no longer in the vault (deleted, moved to trash).

## Configuration

TOML file (generated on first run): `~/.config/digiposte-cli/config.toml`
(use `--config` for another one). Every key is **commented out** by default:
uncomment a line to override its default.

```toml
[auth]        # login_url, email, password, headless, use_running_chrome
[playwright]  # channel = "chrome" (or "msedge", "" = bundled Chromium)
[api]         # base_url, locations = ["SAFE", "INBOX"], max_results = 1000
[download]    # rename_rules (regex substitutions on downloaded file names)
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

### File name renaming (`[download] rename_rules`)

Because Digiposte file names come from the vault content, the CLI does not
impose a name template (unlike `ameli-cli`, where the API dictated the names).
Instead, you express **regex match/replacement rules**, applied in order to
each file **stem** (the extension is kept). Each rule replaces every match of
`pattern` with `replacement` (Python `re` syntax: back-references `\1`,
`\g<name>`, …). Example — a payroll bulletin is sent month-first, ALL CAPS as
`BULLETIN DE PAIE 02_2026.pdf`; the most explicit rule spells out the whole
name and rewrites it to a normal, chronological `Bulletin de paie 2026-02.pdf`
(`(?i)` makes the match case-insensitive):

```toml
[download]
rename_rules = [
  # "BULLETIN DE PAIE 02_2026.pdf" → "Bulletin de paie 2026-02.pdf"
  # (each rule is an inline table: it must stay on a single line)
  { pattern = '(?i)^BULLETIN DE PAIE (0[1-9]|1[0-2])_([0-9]{4})$', replacement = 'Bulletin de paie \2-\1' },
]
```

A shorter rule only reorders a trailing `MM_YYYY` date token, keeping the
vault prefix as-is: `{ pattern = '(0[1-9]|1[0-2])_([0-9]{4})$',
replacement = '\2-\1' }` → `BULLETIN DE PAIE 2026-02.pdf`.

> Prefer **single-quoted** TOML strings (`'…'`): they keep backslashes
> literal, so `\d`, `\1`, `\g<name>` work as in Python. In double-quoted
> strings a backslash must be escaped (`"\\d"`).

The rules are validated when the config is read (a bad pattern or a bad
back-reference aborts the run with a clear message). Files downloaded before
these rules existed — under the un-renamed name — are **renamed automatically**
on the next `sync`, they are not downloaded twice.

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
- [docs/functional-specs.md](docs/functional-specs.md) — functional spec:
  commands, download layout, configuration and constraints.
- [docs/technical-specs.md](docs/technical-specs.md) — technical spec:
  architecture, module responsibilities and key flows.
