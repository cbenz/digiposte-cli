# digiposte-cli — Functional Specification

This document describes **what** `digiposte-cli` does from a user's point of
view: its purpose, the commands it exposes, its observable behavior and its
constraints. The implementation details live in
[docs/technical-specs.md](technical-specs.md); the browser-authentication
internals in [docs/AUTH.md](AUTH.md); the HTTP API in [docs/API.md](API.md).

## 1. Overview

`digiposte-cli` is a command-line tool that backs up your **personal
[Digiposte](https://www.laposte.fr/digiposte/) vault** (La Poste). In a few
commands it:

1. **authenticates** against the personal vault — reusing a cached token while
   it is valid, otherwise logging in through a real Chrome it starts itself;
2. **lists** the documents of the configured locations (safe box `SAFE`,
   inbox `INBOX`, …);
3. **downloads** them into one folder per location on the local disk.

It targets **individual Digiposte account holders** who want a repeatable,
scriptable backup of their own documents on their own machine.

## 2. Goals

- Automate the backup of every document of the configured vault locations.
- Keep a *silent* backup possible: once logged in, later runs reuse the alive
  SSO session headlessly (no window, no CAPTCHA).
- Provide a **no-network** way to inspect configuration and authentication
  state (`status`).
- Support **incremental** runs: a document already on disk is not re-downloaded.
- Make the configuration file-based, editable and fully overridable.

## 3. Non-goals (out of scope)

- **No partner OKAPI API usage**: the tool never sends documents to vaults and
  never accesses documents *shared with a partner* (that is the other,
  unrelated Digiposte API — see [docs/API.md](API.md)).
- **No vault-side organization**: it does not create folders, move or delete
  documents in the vault.
- **No upload / two-way sync**: data only flows from Digiposte to the disk.
- **No content-based change detection**: only file *names* are compared (a
  document is skipped when its sanitized name already exists in its folder).
- **No GUI / multi-account**: a single-user CLI with one configuration file.

## 4. Key user scenarios

| Scenario | Behavior |
| --- | --- |
| First run | No config file exists → the default TOML template is written and logged; commands then apply the built-in defaults. |
| Check state | `status` prints the config paths, locations and whether a valid cached token exists — **no network** call. |
| Fresh login | No valid token and no alive session → a **visible Chrome window** opens with a dedicated profile; credentials are pre-filled and submitted automatically; the user only solves the CAPTCHA (if any); the window closes, the session stays on disk. |
| Silent re-login | Token expired but the SSO session is still alive → the token is renewed **headlessly** (no window, no CAPTCHA). |
| Backup | `sync` authenticates, lists the documents of each location and downloads them under one sub-folder per location. |
| Sign-out | `logout` deletes the cached token; `logout --reset` also deletes the browser sessions (full sign-out). |

## 5. Command reference

> Both `list` and `ls` are the same command. Running the bare `digiposte-cli`
> (no subcommand) prints the help.

| Command | Purpose | Specific options |
| --- | --- | --- |
| `sync` | Authenticate, then list and download the documents of the configured locations. | `--download-dir DIR` |
| `list` (`ls`) | Authenticate and print the document list (label + id per line) without downloading. | `--json` (raw JSON on stdout) |
| `login` | Ensure a valid token. Already valid → just reports it (no browser). Otherwise silent refresh, or a visible browser login. | — |
| `logout` | Delete the cached token. | `--reset` (also delete the browser profiles — full sign-out) |
| `status` | Show configuration and authentication state (no network). | — |
| `config` | Print the path of the configuration file. | `--init` (write the default template if missing, then print the path) |

### Global options (available on any command)

| Option | Effect |
| --- | --- |
| `--config PATH` | Use another configuration file than the default `~/.config/digiposte-cli/config.toml`. |
| `--playwright-channel CH` | Override the browser channel used by the *legacy* Playwright path (default `chrome`). |
| `--use-running-chrome` | Force the dedicated-Chrome (CDP) login path — already the default; only meaningful to force it on when disabled in the config. |
| `--headless` | Run the browser without a window (only useful when already authenticated). |
| `--verbose` | Debug logging. |

## 6. Download layout

Files are written under the download base directory (default
`~/Documents/digiposte`), **one sub-folder per configured location** so the
tree mirrors the `[api] locations` config. Empty locations still get their
folder (created eagerly), keeping the tree stable:

```
~/Documents/digiposte/
├── SAFE/
└── INBOX/
    └── BULLETIN DE PAIE 01_2026.pdf
```

Rules:

- Each document goes into `<base>/<LOCATION>/<file name>`. A document without a
  `location` falls back to a `MISC` folder.
- The on-disk file name is a **sanitized title + extension** (characters like
  `/ \ : * ? " < > |` become `_`).
- **Skip is by file name only**: if `<dest>/<file name>` already exists, the
  document is skipped (`Already present (by filename)`) — there is **no local
  content index** (no SHA-256 / `downloads.json`).
- `sync` reports how many documents were downloaded vs. already present.

## 7. Configuration

Single **TOML** file, generated automatically on first run and overridable per
run with `--config`. Every key is **commented out** in the template: uncomment
a line to override its default. Precedence is **CLI option > config file >
built-in default**.

| Section | Keys | Meaning |
| --- | --- | --- |
| `[auth]` | `login_url`, `email`, `password`, `headless`, `use_running_chrome` | SSO entry point, automatic credentials pre-fill, headless mode, dedicated-Chrome vs legacy Playwright auth. |
| `[playwright]` | `channel` | Browser channel for the legacy path (`chrome`, `msedge`, `""` = bundled Chromium). |
| `[api]` | `base_url`, `locations`, `max_results` | Vault API base URL; locations to browse (uppercase API identifiers, e.g. `["SAFE","INBOX"]`); max documents per call. |
| `[log]` | `level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default `INFO`). |
| `[paths]` | `download_dir`, `profile_dir`, `debug_profile_dir`, `token_cache` | Override of the XDG default locations. |

### Password without a password manager dependency

The `password` may come from **any command** on the machine using the
`command:` prefix — the CLI runs it and uses its stdout:

```toml
[auth]
email = "you@example.com"
password = "command:secret-tool lookup Title 'Compte La Poste'"
```

> Requirement: the `command:` secret must be a **real executable** working in a
> non-interactive shell (shell functions/aliases from `~/.zshrc` are not
> loaded).

## 8. Locations (XDG defaults)

| Data | Default path |
| --- | --- |
| Downloaded documents (one folder per location) | `~/Documents/digiposte` |
| Dedicated Chrome profile (login, anti-CAPTCHA) | `~/.local/state/digiposte-cli/chrome-debug` |
| Token cache (~1 h) | `~/.local/state/digiposte-cli/token.json` |
| Legacy Playwright profile (`use_running_chrome = false`) | `~/.local/share/digiposte-cli/chrome-profile` |
| Configuration | `~/.config/digiposte-cli/config.toml` |

All of them can be overridden under `[paths]` in the config file.

## 9. User-visible behavior of the authentication

- While the cached API token is valid (~1 h), **no browser is involved at all**.
- Otherwise, the CLI launches **its own dedicated Chrome** (a dedicated,
  app-owned profile — never the user's personal browser) and drives it over the
  DevTools protocol:
  - a **silent headless probe** first reuses an already-alive session (no
    window, no CAPTCHA);
  - only when a real login is needed does a **visible window** open. Because
    this is a genuine, human-operated Chrome, La Poste's anti-bot does not flag
    it and any CAPTCHA is solvable there. The configured credentials are
    pre-filled and submitted; the window then closes and the session stays on
    disk for the next silent reuse.
- `logout --reset` is the full sign-out: cached token **and** browser profiles
  are deleted.

## 10. Non-functional requirements

| Area | Requirement |
| --- | --- |
| Security | Credentials are not sent over the network except to La Poste's own SSO. Password may be stored in the config or delegated to a system secret command. The dedicated Chrome profile is separate from the user's personal profile. Tokens are cached on disk under the state dir. |
| Reliability | A document already present by name is skipped (incremental). API / auth failures are reported clearly, with an actionable hint (`Run digiposte-cli login` on HTTP 401). |
| Usability | Human-readable, informative logs on **stderr**; `stdout` stays clean for data output (`ls --json`). French-targeted SSO pages are handled (localized labels, cookie-consent banner). |
| Portability | Linux-oriented XDG paths; requires a Chromium-based browser for interactive login; `SHELL` used for `command:` secrets. CLI language: English. |
| Performance | Only missing documents are downloaded; no per-file hashing, no local index. |
| Exit codes | `0` success · `1` authentication/backup failure · `2` invalid configuration or CLI options · `130` interrupted by the user. |

## 11. Assumptions and constraints

- The user has a personal Digiposte account with documents in at least one
  configured location.
- The machine has network access to `secure.digiposte.fr`,
  `moncompte.laposte.fr` (SSO) and `api.digiposte.fr`.
- A real Chromium-based browser (Google Chrome, Chromium, …) must be installed
  for the interactive login path; a CAPTCHA cannot be solved headless.
- La Poste's anti-bot flags automation: logins must go through the dedicated,
  human-operated Chrome — never through a Playwright-launched browser.
