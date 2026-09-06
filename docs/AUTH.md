# Browser authentication (the anti-CAPTCHA login)

Digiposte login goes through La Poste's SSO (Keycloak "mon-compte"), which is
protected by a **server-side anti-bot** (`captcha.liveidentity.com`). Read this
before touching `src/digiposte_cli/auth.py`: it records the technique that
actually works and the dead ends we already fell into, so we don't regress.

## TL;DR

- The CLI keeps an API `access_token` (~1 h) in a cache. While it is valid, no
  browser is involved.
- Otherwise it logs in through a **dedicated Chrome** that the CLI **launches
  itself** (`--remote-debugging-port=9222 --user-data-dir=<profile>`) and
  drives **over CDP** (`connect_over_cdp`). Because that Chrome is a real,
  human-operated browser, La Poste's anti-bot does not flag it: the CAPTCHA is
  solvable (or absent because the session is reused).
- **Do NOT** launch the browser through Playwright, and **do NOT** apply JS
  "stealth" patches. Both get detected and produce a **blank, unsolvable
  CAPTCHA**.

## Flow

1. `login_via_own_chrome()` (auth.py):
   - if a Chrome already answers on the port → attach to it;
   - else launch a dedicated Chrome and **headless probe** it first: if the SSO
     session stored in the profile is still alive, the token is renewed with
     **no window and no CAPTCHA**. The probe must fast-fail as soon as the SSO
     login form (`#username`) appears (session provably dead);
   - if there is no live session, open a **visible** Chrome window: configured
     credentials (`email`/`password` from the config, possibly a `command:`
     secret) are pre-filled and submitted with a **human-like typing cadence**;
     the user solves a CAPTCHA if one appears; the window is then closed (the
     session stays on disk for the next silent reuse).
2. The API token is fetched with the session cookies from
   `secure.digiposte.fr/rest/security/token` (header `X-XSRF-TOKEN`), then
   cached.

This is the default path (`use_running_chrome` in `[auth]`, true by default).
The legacy Playwright-profile path only remains reachable with
`use_running_chrome = false`.

## Why a dedicated profile (not the user's personal Chrome)

- Relaunching the user's personal Chrome with a debug port is invasive, and it
  exposes the whole browsing profile on `127.0.0.1:9222`.
- The dedicated profile is app-owned, lives under the XDG **state** dir
  (`[paths] debug_profile_dir`, default
  `~/.local/state/digiposte-cli/chrome-debug`), persists the session across
  runs, and is removed by `logout --reset` (full sign-out).

## Dead ends — do not reintroduce

1. **Playwright-launched Chrome** — whether it is the bundled Chromium, a
   `channel="chrome"` real Google Chrome, or a persistent profile: liveidentity
   flags it (blank CAPTCHA, forever). Launching Chrome through Playwright is
   detectable regardless of extra flags.
2. **JS stealth in the page** (`navigator.webdriver` overrides, faking
   `navigator.plugins`/`navigator.languages`, …) — counter-productive: these
   patches are fingerprints themselves, e.g. returning `undefined` for
   `webdriver` (real Chrome: boolean `false`) or a plain `Array` for `plugins`
   (real Chrome: a `PluginArray`).
3. **Passing the login URL on the Chrome command line** while also navigating
   via CDP → two tabs open on the SSO. Navigate only through
   `ctx.new_page().goto()`.
4. **Headless auto-login** — a CAPTCHA cannot be solved headless; never submit
   credentials in the headless probe, only check for an existing session.
5. **Waiting a fixed probe timeout** when there is no session — bail as soon as
   the login form is visible instead of burning the probe timeout.

## Operational notes

- The dedicated Chrome is launched as a **plain subprocess** (never through
  Playwright), so it keeps a normal, human fingerprint.
- `_setup_logging` sets the `asyncio` logger to `CRITICAL`: Playwright's Node
  driver otherwise prints raw subprocess transport noise
  (`execute program …`, `… exited with return code 0`).
- Chrome is single-instance per `--user-data-dir`: after the headless probe,
  wait for the port to free before opening the visible window.
