"""Browser discovery and deterministic screenshot capture helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bootstrap import (
    BootstrapError,
    Runtime,
    ensure_playwright_browser,
    process_environment,
)


class BrowserError(RuntimeError):
    """Raised when a browser cannot render the requested page."""


@dataclass(frozen=True)
class Browser:
    engine: str
    executable: Path | None = None


def _candidate_paths() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    explicit = os.environ.get("PIXEL_PERFECT_BROWSER")
    if explicit:
        candidates.append(Path(explicit).expanduser())

    for command in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))

    candidates.extend(
        [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            home / "Applications/Chromium.app/Contents/MacOS/Chromium",
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", ""))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google/Chrome/Application/chrome.exe",
        ]
    )
    return candidates


def find_system_browser(explicit: str | None = None) -> Browser | None:
    """Return a supported installed browser, preferring an explicit path."""

    if explicit and explicit.lower() in {"playwright", "chromium-playwright"}:
        return None

    candidates = [Path(explicit).expanduser()] if explicit else _candidate_paths()
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return Browser(engine="chrome-cli", executable=candidate)
    return None


def browser_plan(
    runtime: Runtime,
    *,
    explicit: str | None = None,
    install: bool = True,
) -> Browser:
    """Select a system browser or prepare the Playwright Chromium adapter."""

    system = find_system_browser(explicit)
    if system is not None:
        return system
    if explicit and explicit.lower() not in {"playwright", "chromium-playwright"}:
        raise BrowserError(f"Browser executable not found or not executable: {explicit}")
    if not runtime.playwright_available:
        raise BrowserError(
            "No system browser was found and Playwright is unavailable. "
            "Run the CLI without --no-auto-setup to install it."
        )
    try:
        ensure_playwright_browser(runtime, install=install)
    except BootstrapError as exc:
        raise BrowserError(str(exc)) from exc
    return Browser(engine="playwright")


def _chrome_command(browser: Browser, url: str, output: Path, width: int, height: int, wait_ms: int) -> list[str]:
    command = [
        str(browser.executable),
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size={width},{height}",
        f"--screenshot={output}",
    ]
    if wait_ms > 0:
        command.append(f"--virtual-time-budget={wait_ms}")
    command.append(url)
    return command


def _capture_chrome(
    browser: Browser,
    url: str,
    output: Path,
    width: int,
    height: int,
    wait_ms: int,
    timeout: float,
    environment: dict[str, str],
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = _chrome_command(browser, url, output, width, height, wait_ms)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrowserError(f"Browser timed out after {timeout:.1f}s: {url}") from exc
    except OSError as exc:
        raise BrowserError(f"Could not start browser: {exc}") from exc

    if not output.exists():
        detail = (result.stderr or result.stdout or "").strip()
        raise BrowserError(
            "Browser completed without producing a screenshot"
            + (f": {detail[-1000:]}" if detail else "")
        )

    warnings = []
    if result.returncode != 0:
        warnings.append(f"Browser exited with status {result.returncode}")
    if result.stderr.strip():
        warnings.append(result.stderr.strip()[-1000:])
    return {
        "engine": browser.engine,
        "url": url,
        "output": str(output),
        "warnings": warnings,
        "console_capture": False,
    }


def _capture_playwright(
    runtime: Runtime,
    url: str,
    output: Path,
    width: int,
    height: int,
    wait_ms: int,
    timeout: float,
    ready_selector: str | None,
    environment: dict[str, str],
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserError(
            "Playwright is not importable in the active environment; rerun setup."
        ) from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    environment_keys = ("PLAYWRIGHT_BROWSERS_PATH", "TMPDIR", "TMP", "TEMP")
    previous_environment = {
        key: os.environ.get(key) for key in environment_keys
    }
    os.environ.update({key: environment[key] for key in environment_keys})
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
            )
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(url, wait_until="load", timeout=int(timeout * 1000))
            if ready_selector:
                page.wait_for_selector(ready_selector, timeout=int(timeout * 1000))
            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            page.screenshot(path=str(output), full_page=False)
            browser.close()
    except Exception as exc:
        raise BrowserError(f"Playwright could not render {url}: {exc}") from exc
    finally:
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    return {
        "engine": "playwright",
        "url": url,
        "output": str(output),
        "warnings": [],
        "console_errors": console_errors,
        "page_errors": page_errors,
        "console_capture": True,
    }


def capture(
    runtime: Runtime,
    *,
    url: str,
    output: Path,
    width: int,
    height: int,
    browser: str | None = None,
    wait_ms: int = 250,
    timeout: float = 60.0,
    ready_selector: str | None = None,
    install: bool = True,
) -> dict[str, Any]:
    """Capture one viewport-sized screenshot with an available browser."""

    selected = browser_plan(runtime, explicit=browser, install=install)
    environment = process_environment(runtime.directory)
    if selected.engine == "chrome-cli":
        if ready_selector:
            raise BrowserError(
                "--ready-selector requires the Playwright engine; use --browser playwright"
            )
        result = _capture_chrome(
            selected, url, output, width, height, wait_ms, timeout, environment
        )
    else:
        result = _capture_playwright(
            runtime,
            url,
            output,
            width,
            height,
            wait_ms,
            timeout,
            ready_selector,
            environment,
        )

    try:
        from PIL import Image

        with Image.open(output) as image:
            actual = image.size
    except Exception as exc:
        raise BrowserError(f"Screenshot is unreadable: {output}: {exc}") from exc
    result["size"] = {"width": actual[0], "height": actual[1]}
    if actual != (width, height):
        raise BrowserError(
            f"Screenshot size {actual[0]}x{actual[1]} does not match requested "
            f"viewport {width}x{height}"
        )
    return result
