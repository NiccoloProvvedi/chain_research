"""Separate browser for reading pages (Playwright's Chromium, profile inside the project, without your accounts)
that cannot reach the computer or the home network.

Adapted from jev_ultrafast/browser.py (MIT, see LICENSE_JEV_ULTRAFAST.txt): instead of Browser Harness, which
connects to the user's Chrome, a dedicated Chromium is started.
"""
import os
import shutil
import socket
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .proxy import SafeProxy
from .network import forbidden_host, safe_address  # noqa: F401 - also used by other modules

WIDTH, HEIGHT = 1120, 780


class StalePage(RuntimeError):
    """The page changed while the script was being evaluated."""


def _remove_abandoned_lockfile(profile_folder: Path) -> None:
    """Chromium refuses a profile "in use" if a previous browser was closed abruptly (container shutdown, crash). If
    the lock's owner no longer exists, the lock is removed."""
    lockfile = profile_folder / "SingletonLock"
    try:
        owner = os.readlink(lockfile)
    except OSError:
        return
    host, _, pid = owner.rpartition("-")
    alive = False
    if host == socket.gethostname() and pid.isdigit():
        try:
            os.kill(int(pid), 0)
            alive = True
        except OSError:
            alive = False
    if not alive:
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (profile_folder / name).unlink()
            except OSError:
                pass


def _empty_profile(profile_folder: Path) -> None:
    """Every question starts from an empty profile (no cookies or service workers from earlier pages, no growing
    disk usage). A profile with a lock that is still alive is in use: it is left alone."""
    if not os.path.lexists(profile_folder / "SingletonLock"):
        shutil.rmtree(profile_folder, ignore_errors=True)


class Browser:
    def __init__(self, url: str, engines_folder: Path, profile_folder: Path, headless: bool = True,
                 local_allow: bool = False) -> None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(engines_folder)
        profile_folder.mkdir(parents=True, exist_ok=True)
        _remove_abandoned_lockfile(profile_folder)
        _empty_profile(profile_folder)
        profile_folder.mkdir(parents=True, exist_ok=True)
        self.local_allow = local_allow
        self._profile = profile_folder
        self._proxy = None if local_allow else SafeProxy()
        self._pw = sync_playwright().start()
        try:
            # channel="chromium": full Chromium in modern headless mode (many sites, like DuckDuckGo, block the
            # "headless shell" build). All traffic goes through the proxy: `route` does not see redirects and
            # WebSockets; service workers are off because they escape `route`.
            self._context = self._pw.chromium.launch_persistent_context(
                str(profile_folder), headless=headless, channel="chromium",
                viewport={"width": WIDTH, "height": HEIGHT}, locale="en-US", service_workers="block",
                **({"proxy": {"server": self._proxy.address}} if self._proxy else {}),
            )
            if not local_allow:
                # The navigator's browser cannot reach the computer or the home network (first, fast filter).
                self._context.route("**/*", self._filter_request)
            self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self.session = self._context.new_cdp_session(self.page)
            self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            identity = self.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
            self.call("Emulation.setUserAgentOverride", userAgent=identity, acceptLanguage="en-US,en;q=0.9")
            if not local_allow and url != "about:blank" and not safe_address(url):
                raise ValueError("Address not allowed: the navigator cannot open the computer or the home network.")
            self.page.goto(url, wait_until="load", timeout=30_000)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _filter_request(route: Any) -> None:
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:")) or safe_address(url):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    def call(self, method: str, **params: Any) -> dict:
        try:
            return self.session.send(method, params)
        except PlaywrightError as error:
            raise RuntimeError(str(error).splitlines()[0]) from None

    def evaluate(self, expression: str) -> Any:
        try:
            response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        except RuntimeError:
            raise StalePage("Document changed during evaluation") from None
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def close(self) -> None:
        context, pw = getattr(self, "_context", None), getattr(self, "_pw", None)
        self._context = self._pw = None
        for teardown in (context.close if context else None, pw.stop if pw else None):
            try:
                if teardown:
                    teardown()
            except Exception:  # noqa: BLE001 - best-effort close
                pass
        proxy, self._proxy = getattr(self, "_proxy", None), None
        if proxy is not None:
            proxy.teardown()
        if getattr(self, "_profile", None) is not None:
            _empty_profile(self._profile)
