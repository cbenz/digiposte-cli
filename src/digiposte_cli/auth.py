"""Digiposte authentication: browser login + API token retrieval.

Flow (discovered from the SPA traffic + open-source projects):
1. The user logs in manually in a Chrome window on secure.digiposte.fr
   (La Poste / Keycloak SSO, optional CAPTCHA / 2FA).
2. The script reads the session cookies (including XSRF-TOKEN).
3. It calls GET https://secure.digiposte.fr/rest/security/token with those
   cookies and the X-XSRF-TOKEN header, which returns an access_token.
4. This token is then used as `Authorization: Bearer` on api.digiposte.fr/api/v3
   (see api.py).
"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

log = logging.getLogger("digiposte")

TOKEN_URL = "https://secure.digiposte.fr/rest/security/token"
# Host where the session is actually established (after the SSO is done)
APP_HOST = "secure.digiposte.fr"

# Selectors of the La Poste SSO login form (Keycloak "mon-compte").
# The page is localized, so we target the stable element ids rather than the
# aria-labels, which change with the UI language ("Email address"/"Password"
# in EN vs "Adresse e-mail"/"Mot de passe" in FR).
_LOGIN_SELECTORS = {
    "email": "#username",
    "password": "#password",
    # Submit button ("Se connecter" in FR, "Sign in" in EN)
    "signin": 'button:has-text("Se connecter"), button:has-text("Sign in")',
}

# Script injected at startup to hide automation markers
# (La Poste's liveidentity.com anti-bot detects `navigator.webdriver`).
_ANTI_DETECTION_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""


@dataclass
class AuthInfo:
    """Authentication result: the API access token."""

    access_token: str
    expires_at: float | None = None

    def is_valid(self) -> bool:
        return bool(self.access_token)


# ── On-disk token cache (skips the browser while still valid) ─────────
_SAFETY_MARGIN = 60  # seconds before expiry considered expired
# Validity we assign to a freshly obtained token. Digiposte's own "expires_at"
# is unreliable from this machine (it comes out ~= now, see _fetch_token), so
# we cache for ~1 h from the moment the token was obtained, like the web SPA.
_TOKEN_TTL = 3600


def load_token_cache(path: Path) -> AuthInfo | None:
    """Load a cached token if it has not expired yet."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None

    token = data.get("access_token")
    exp = data.get("expires_at")
    if not isinstance(token, str) or not token:
        return None

    # exp is a unix timestamp (seconds)
    if isinstance(exp, (int, float)) and exp - _SAFETY_MARGIN < time.time():
        return None
    return AuthInfo(access_token=token, expires_at=float(exp) if exp else None)


def save_token_cache(auth: AuthInfo, path: Path) -> None:
    """Write the token (and its expiry) to a JSON file."""
    try:
        path.write_text(
            json.dumps(
                {
                    "access_token": auth.access_token,
                    "expires_at": auth.expires_at,
                }
            )
        )
    except OSError as exc:
        log.warning("⚠️  Could not write the token cache: %s", exc)


def _launch_browser(pw, channel: str, headless: bool) -> tuple[Any, Any]:
    """Launch the browser with the requested channel (e.g. "chrome" = real
    Google Chrome, less detectable by the anti-bot). Falls back to the bundled
    Chromium if the channel is unavailable. Empty `channel` = bundled
    Chromium."""
    args = [
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
    ]
    try:
        if channel:
            browser = pw.chromium.launch(channel=channel, headless=headless, args=args)
        else:
            browser = pw.chromium.launch(headless=headless, args=args)
        log.info("✅ Browser launched (channel=%s)", channel or "bundled")
        return browser, True
    except PlaywrightError as exc:
        log.warning(
            "⚠️  Channel %r unavailable (%s) — falling back to bundled Chromium.",
            channel or "bundled",
            exc,
        )
        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        return browser, False


def _is_logged_in(url: str) -> bool:
    """True if the URL shows we are on the vault (outside login screens)."""
    parsed = urlparse(url)
    if parsed.hostname != APP_HOST:
        return False
    path = parsed.path.lower()
    return not any(marker in path for marker in ("identification", "login", "callback", "timeout"))


def _requests_session_from_cookies(
    cookies_playwright: list[dict[str, Any]],
) -> requests.Session:
    """Build a requests session from the Playwright cookies (with their
    domains/paths)."""
    session = requests.Session()
    for c in cookies_playwright:
        session.cookies.set(
            c["name"],
            c["value"],
            domain=c.get("domain"),
            path=c.get("path", "/"),
        )
    return session


def _fetch_token(cookies_playwright: list[dict[str, Any]]) -> AuthInfo | None:
    """Call /rest/security/token with the session cookies to obtain the API
    access token."""
    session = _requests_session_from_cookies(cookies_playwright)

    xsrf = next(
        (c["value"] for c in cookies_playwright if c["name"] == "XSRF-TOKEN"),
        "",
    )

    log.info("🔑 Fetching the API token from %s…", TOKEN_URL)
    resp = session.get(
        TOKEN_URL,
        headers={
            "X-XSRF-TOKEN": xsrf,
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        log.error("❌ /rest/security/token failed (HTTP %s)", resp.status_code)
        log.debug("Response: %s", resp.text[:500])
        return None

    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        log.error("❌ /rest/security/token response has no access_token")
        return None

    # The server's "expires_at" is not usable as a future deadline here: it
    # comes back ~= the issuance time (seconds later), most likely because of a
    # clock skew between this machine and Digiposte's servers. Trusting it would
    # make every cached token look already expired. We assign our own validity
    # window (~1 h) from the moment the token was obtained.
    expires_at = time.time() + _TOKEN_TTL
    log.info("✅ API token obtained (cached as valid for ~1 h)")
    return AuthInfo(access_token=token, expires_at=expires_at)


def _open_context(pw, user_data_dir: Path | None, channel: str, headless: bool) -> tuple[Any, Any]:
    """Open a browser context. If `user_data_dir` is provided, use a persistent
    profile (the SSO session survives across runs).
    Returns (context, browser_or_None)."""
    args = [
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
    ]
    if user_data_dir is not None:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        log.info("📁 Persistent profile: %s", user_data_dir)
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            channel=channel or None,
            headless=headless,
            args=args,
            locale="fr-FR",
            viewport={"width": 1280, "height": 900},
        )
        return ctx, None
    browser, _ = _launch_browser(pw, channel, headless)
    ctx = browser.new_context(
        locale="fr-FR",
        viewport={"width": 1280, "height": 900},
    )
    return ctx, browser


# Buttons that close the La Poste cookie/privacy consent banner, in preference
# order. "Accepter et fermer" (accept cookies and close) is tried first. Both
# the Digiposte page and the "mon-compte" SSO (Keycloak) page can show a
# banner, with slightly different wording.
_CONSENT_DISMISS_LABELS = (
    "Accepter et fermer",
    # moncompte.laposte.fr (La Poste SSO / Keycloak) refusal variant
    "Ne pas accepter et fermer",
    # secure.digiposte.fr
    "Continuer sans accepter",
    # Accept-and-close variant
    "Tout accepter",
)


def _consent_banner_open(page) -> bool:
    """True if a consent banner dismiss button is currently visible."""
    for label in _CONSENT_DISMISS_LABELS:
        try:
            if page.get_by_role("button", name=label, exact=True).is_visible(timeout=200):
                return True
        except PlaywrightError:
            continue
    return False


def _dismiss_consent_banner(page) -> bool:
    """Close every visible consent banner button (best effort)."""
    for label in _CONSENT_DISMISS_LABELS:
        try:
            button = page.get_by_role("button", name=label, exact=True)
            if button.is_visible(timeout=300):
                button.click(timeout=1500)
                page.wait_for_timeout(400)
        except PlaywrightError:
            continue
    return not _consent_banner_open(page)


def _field_reachable(page, selector: str) -> bool:
    """True if `selector` is visible and not covered by an overlay.

    The consent banner overlays the login form, but the fields behind it are
    still reported "visible" by Playwright — only checking the top-most element
    at the field's center tells us it can really receive input/clicks."""
    try:
        locator = page.locator(selector)
        if not locator.is_visible(timeout=200):
            return False
        return bool(
            page.evaluate(
                "(sel) => {"
                "  const el = document.querySelector(sel);"
                "  if (!el) return false;"
                "  const r = el.getBoundingClientRect();"
                "  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;"
                "  const top = document.elementFromPoint(cx, cy);"
                "  return !!top && (top === el || el.contains(top));"
                "}",
                selector,
            )
        )
    except PlaywrightError:
        return False


def _prepare_login_form(page, timeout: float = 20.0) -> bool:
    """Wait until the SSO login form is actually usable.

    The consent banner can appear a few seconds after load, and again after the
    SSO redirect — so this keeps dismissing it until the email field is truly
    reachable (visible and not covered). Returns False quickly if we end up
    already authenticated (no form to fill)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_logged_in(page.url):
            return False  # session already active — nothing to pre-fill
        if _consent_banner_open(page):
            _dismiss_consent_banner(page)
            page.wait_for_timeout(300)
            continue
        if _field_reachable(page, _LOGIN_SELECTORS["email"]):
            return True
        page.wait_for_timeout(300)
    return False


def _autofill_login(page, email: str, password: str) -> bool:
    """Try to fill in the email + password and submit.
    True if a submit was attempted (otherwise the user types manually)."""
    if not email or not password:
        return False
    if not _prepare_login_form(page):
        return False

    try:
        email_field = page.locator(_LOGIN_SELECTORS["email"])
        email_field.fill(email)
    except PlaywrightError:
        return False

    try:
        # Case 1: email + password on the same page
        password_field = page.locator(_LOGIN_SELECTORS["password"])
        password_field.wait_for(state="visible", timeout=2500)
        password_field.fill(password)
        _dismiss_consent_banner(page)  # a banner may cover the submit button
        submit = page.locator(_LOGIN_SELECTORS["signin"]).first
        try:
            submit.wait_for(state="visible", timeout=1500)
            submit.click(timeout=2000)
        except PlaywrightError:
            password_field.press("Enter")
        return True
    except PlaywrightError:
        # Case 2: two-step form (email → next screen)
        try:
            email_field.press("Enter")
            page.wait_for_timeout(800)
            password_field = page.locator(_LOGIN_SELECTORS["password"])
            password_field.wait_for(state="visible", timeout=5000)
            password_field.fill(password)
            password_field.press("Enter")
            return True
        except PlaywrightError:
            return False


def manual_login(
    login_url: str = "https://secure.digiposte.fr/home",
    user_data_dir: Path | None = None,
    email: str = "",
    password: str = "",
    channel: str = "chrome",
    headless: bool = False,
) -> AuthInfo | None:
    """Open a browser, wait for the user to log in, then return the AuthInfo
    (access token). None if the login is cancelled."""
    log.info("🖥️  Opening a browser for manual login…")
    log.info("   Log in to your Digiposte account in the window that opens.")
    log.info("   ⚠️  A CAPTCHA may appear — solve it manually.")
    log.info("   Once logged in, the script detects it automatically.")
    if email and password:
        log.info("   🤖 Credentials found: automatic email + password pre-fill.")

    pw = sync_playwright().start()
    context, browser = _open_context(pw, user_data_dir, channel, headless)
    page = context.pages[0] if context.pages else context.new_page()
    page.add_init_script(_ANTI_DETECTION_JS)

    try:
        page.goto(login_url, wait_until="domcontentloaded")
        _prepare_login_form(page)
        if _autofill_login(page, email, password):
            log.info("   ✍️  Email + password submitted automatically.")
            log.info("   ⚠️  If a CAPTCHA appears, solve it manually.")
        log.info("⏳ Waiting for login (watching the URL…)")

        while not _is_logged_in(page.url):
            page.wait_for_timeout(1000)
            log.debug("Current URL: %s", page.url)

        log.info("✅ Login detected — fetching the token…")
        auth = _token_from_session(context, page)
        if auth is None:
            return None
        return auth

    except PlaywrightError:
        log.warning("🚫 Browser window closed by the user")
        return None
    except KeyboardInterrupt:
        log.info("⏹️  Interrupted by the user")
        return None
    finally:
        # Tolerant close (an already closed context raises a Playwright error)
        try:
            context.close()
        except PlaywrightError:
            pass
        if browser is not None:
            try:
                browser.close()
            except PlaywrightError:
                pass
        pw.stop()


def _token_from_session(context, page) -> AuthInfo | None:
    """Let the SPA settle, then pull the API token (from the session cookies or
    the SPA's sessionStorage). None if no token can be obtained."""
    page.wait_for_timeout(4000)  # let the SPA load & call the API
    # Token possibly stored by the SPA in sessionStorage
    token_storage = page.evaluate("() => sessionStorage.getItem('access_token') || null")
    raw_cookies = context.cookies()
    log.info("🍪 %s cookie(s) retrieved", len(raw_cookies))
    auth = _fetch_token(raw_cookies)
    if auth is None and token_storage:
        auth = AuthInfo(access_token=token_storage, expires_at=time.time() + _TOKEN_TTL)
        log.info("✅ Token read from the SPA sessionStorage")
    return auth


def refresh_session_token(
    login_url: str,
    user_data_dir: Path,
    channel: str,
    timeout: float = 12.0,
) -> AuthInfo | None:
    """Try to renew the API token from the SSO session persisted in the profile,
    in headless mode (no window is shown).

    Returns None when no active session is found — the caller should then fall
    back to an interactive (visible) login."""
    log.info("🤫 Silent refresh: checking the persistent session…")
    pw = sync_playwright().start()
    context, browser = _open_context(pw, user_data_dir, channel, headless=True)
    page = context.pages[0] if context.pages else context.new_page()
    page.add_init_script(_ANTI_DETECTION_JS)

    try:
        page.goto(login_url, wait_until="domcontentloaded")
        deadline = time.monotonic() + timeout
        while not _is_logged_in(page.url):
            if time.monotonic() > deadline:
                log.info("ℹ️  No active session in the profile — interactive login required.")
                return None
            page.wait_for_timeout(1000)
        log.info("✅ Active session detected — fetching a fresh token…")
        return _token_from_session(context, page)
    except PlaywrightError:
        return None
    except KeyboardInterrupt:
        log.info("⏹️  Interrupted by the user")
        return None
    finally:
        try:
            context.close()
        except PlaywrightError:
            pass
        if browser is not None:
            try:
                browser.close()
            except PlaywrightError:
                pass
        pw.stop()
