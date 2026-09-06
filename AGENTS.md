# AGENTS.md

## Development rules

- The README.md file is the main entry point for users and developers. It must always be kept up to date with the features of the application.
- Keep this AGENTS.md file **short**: rules belong here, but rationale, gotchas
  and technical deep-dives go in the dedicated docs (see “Documentation map”).

## Language

Use **English** for everything written in this repository: code (identifiers,
comments, docstrings), documentation and CLI outputs. The project's user-facing
surface is English.

> The language used in the chat does not matter. Even when the conversation is
> held in French, every file created or modified in this repository (source
> code, comments, docs, config, commit messages…) must be written in English.

## Documentation map

- [docs/AUTH.md](docs/AUTH.md) — **browser authentication**: the one technique
  that works (dedicated Chrome launched by the CLI + CDP) and the dead ends to
  avoid (Playwright launch, JS stealth → blank CAPTCHA). Read it before
  touching `auth.py`.
- [docs/API.md](docs/API.md) — the two Digiposte APIs (partner OKAPI vs
  personal vault): URLs, authentication, endpoints. Only the personal vault is
  used.
- [docs/swagger.json](docs/swagger.json) — personal vault (internal API)
  swagger.
- Developer portal (partner OKAPI API):
  <https://developer.laposte.fr/catalog-apis/digiposte@3>
