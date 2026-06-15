"""Playwright browser/session management for Ivan.

Session handling uses Playwright's storage_state: a single JSON file in
session/session.json holding cookies + local storage. It is created once by a
manual login (visible browser) and reloaded headless for every scan.

The public functions follow FILE_CONTRACTS.md. create_session() and
is_session_valid() are synchronous wrappers around async work (they own their
own event loop via asyncio.run). load_session() and close_browser() are async
because the scanner awaits them inside its own scan coroutine.
"""

import asyncio
import os
from datetime import datetime

from playwright.async_api import async_playwright

from bot.logger import (
    BASE_DIR,
    SESSION_DIR,
    SETTINGS_PATH,
    load_settings,
    log_error,
    log_info,
    log_success,
    log_warning,
    save_json,
)

SESSION_PATH = os.path.join(SESSION_DIR, 'session.json')
FACEBOOK_URL = "https://www.facebook.com/"


def _record_login_time() -> None:
    """Persist the current time as the session creation timestamp."""
    try:
        settings = load_settings()
        settings['session_created_at'] = datetime.now().isoformat()
        save_json(SETTINGS_PATH, settings)
    except Exception as e:
        log_error(f"Could not record login time: {e}")


async def _apply_stealth(page) -> None:
    """Apply playwright-stealth to a page, tolerating API differences across versions."""
    try:
        from playwright_stealth import stealth_async  # type: ignore
        await stealth_async(page)
        return
    except Exception:
        pass
    try:
        # playwright-stealth 2.x exposes a Stealth class instead.
        from playwright_stealth import Stealth  # type: ignore
        await Stealth().apply_stealth_async(page)
        return
    except Exception as e:
        log_warning(f"Stealth not applied (continuing without it): {e}")


async def _create_session_async() -> bool:
    """Open a visible browser, wait for manual login, then persist storage state."""
    playwright = None
    browser = None
    try:
        os.makedirs(SESSION_DIR, exist_ok=True)
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await _apply_stealth(page)
        await page.goto(FACEBOOK_URL, wait_until="domcontentloaded")

        log_info("A browser window has opened. Log in to Facebook, then return here.")
        print("\n" + "=" * 60)
        print("  Ivan — Facebook Login")
        print("  1. Log in to Facebook in the browser window that opened.")
        print("  2. Once you can see your news feed, come back here.")
        print("  3. Press ENTER to save your session.")
        print("=" * 60 + "\n")
        try:
            input("Press ENTER once you are logged in... ")
        except EOFError:
            # No interactive stdin (unexpected); give the user time to log in.
            await asyncio.sleep(60)

        await context.storage_state(path=SESSION_PATH)
        _record_login_time()
        log_success("Facebook session saved successfully.")
        return True
    except Exception as e:
        log_error(f"Failed to create session: {e}")
        return False
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass


def create_session() -> bool:
    """Open a visible browser for manual Facebook login and save cookies.

    Intended to be launched in a subprocess from the dashboard's
    /settings/login route so the blocking input() prompt has its own console.
    Returns True if the session file was written.
    """
    return asyncio.run(_create_session_async())


async def load_session():
    """Load saved cookies into a fresh headless context with stealth applied.

    Returns (playwright_instance, browser, page) or (None, None, None) if the
    session file is missing or fails to load. The caller must close the browser.
    """
    if not os.path.exists(SESSION_PATH):
        log_warning("No saved session found — run Facebook login from Settings.")
        return None, None, None

    playwright = None
    browser = None
    try:
        settings = load_settings()
        headless = bool(settings.get('headless_mode', True))
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(storage_state=SESSION_PATH)
        page = await context.new_page()
        await _apply_stealth(page)
        return playwright, browser, page
    except Exception as e:
        log_error(f"Failed to load session: {e}")
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass
        return None, None, None


async def close_browser(playwright_instance, browser) -> None:
    """Gracefully close the browser and stop the Playwright instance."""
    if browser:
        try:
            await browser.close()
        except Exception as e:
            log_error(f"Error closing browser: {e}")
    if playwright_instance:
        try:
            await playwright_instance.stop()
        except Exception as e:
            log_error(f"Error stopping Playwright: {e}")


async def _is_session_valid_async() -> bool:
    """Open the saved session, visit Facebook, and check for a logged-in UI."""
    playwright, browser, page = await load_session()
    if page is None:
        return False
    try:
        await page.goto(FACEBOOK_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        # A logged-out session shows a login form (email/password inputs).
        login_form = await page.query_selector("input[name='email'], input[name='pass']")
        if login_form is not None:
            log_warning("Session appears logged out (login form present).")
            return False
        # A logged-in session exposes navigation/search affordances.
        logged_in = await page.query_selector(
            "[aria-label='Facebook'], [role='navigation'], "
            "[aria-label='Search Facebook'], a[href*='/me/'], "
            "div[role='banner']"
        )
        if logged_in is not None:
            return True
        # Fall back to a heuristic on the URL.
        return "login" not in page.url
    except Exception as e:
        log_error(f"Error validating session: {e}")
        return False
    finally:
        await close_browser(playwright, browser)


def is_session_valid() -> bool:
    """Return True if the saved session is still logged in to Facebook."""
    try:
        return asyncio.run(_is_session_valid_async())
    except Exception as e:
        log_error(f"Session validation failed: {e}")
        return False


if __name__ == "__main__":
    # Invoked as a subprocess by the dashboard to run the manual login flow.
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "login":
        ok = create_session()
        sys.exit(0 if ok else 1)
    elif len(sys.argv) > 1 and sys.argv[1] == "validate":
        sys.exit(0 if is_session_valid() else 1)
    else:
        print("Usage: python -m bot.browser [login|validate]")
        sys.exit(2)
