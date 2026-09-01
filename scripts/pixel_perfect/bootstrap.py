"""Create and maintain the workspace-local runtime used by pixel-perfect.

The runtime is intentionally kept in ``<cwd>/.artifacts/pixel-perfect/.runtime``
so every pixel-perfect process file stays below the artifact root while the
skill never mutates the global Python installation or target project checkout.
Pillow is required for image I/O. NumPy is preferred for fast array operations
but the analysis code retains a standard-Python fallback.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ENV_RELATIVE_PATH = Path(".artifacts") / "pixel-perfect" / ".runtime"
REQUIRED_PACKAGES = (("Pillow>=10", "PIL"),)
OPTIONAL_PACKAGES = (("numpy>=2", "numpy"),)
PLAYWRIGHT_PACKAGE = ("playwright>=1.45,<2", "playwright")


class BootstrapError(RuntimeError):
    """Raised when the workspace-local runtime cannot be prepared."""


@dataclass(frozen=True)
class Runtime:
    project_root: Path
    directory: Path
    python: Path
    numpy_available: bool
    playwright_available: bool
    warnings: tuple[str, ...] = ()
    workspace_root: Path | None = None

    def as_dict(self) -> dict:
        return {
            "project_root": str(self.project_root),
            "workspace_root": str(self.workspace_root or self.project_root),
            "directory": str(self.directory),
            "python": str(self.python),
            "numpy_available": self.numpy_available,
            "playwright_available": self.playwright_available,
            "warnings": list(self.warnings),
        }


def runtime_directory(workspace_root: Path) -> Path:
    return workspace_root / ENV_RELATIVE_PATH


def runtime_python(directory: Path) -> Path:
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def process_environment(storage_root: Path) -> dict[str, str]:
    """Keep installer/browser temporary and cache files under the artifact root."""

    storage_root = Path(storage_root).resolve()
    temp_dir = storage_root / "tmp"
    cache_dir = storage_root / "pip-cache"
    temp_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "TMPDIR": str(temp_dir),
            "TMP": str(temp_dir),
            "TEMP": str(temp_dir),
            "PIP_CACHE_DIR": str(cache_dir),
            "PLAYWRIGHT_BROWSERS_PATH": str(storage_root / "browsers"),
        }
    )
    return environment


def _run(
    command: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command, check=False, text=True, capture_output=True, env=env
        )
    except OSError as exc:
        raise BootstrapError(f"Could not run {command[0]!r}: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise BootstrapError(
            f"Command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def _module_available(python: Path, module: str) -> bool:
    result = _run([str(python), "-c", f"import {module}"], check=False)
    return result.returncode == 0


def _install(python: Path, package: str, storage_root: Path) -> None:
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            package,
        ],
        env=process_environment(storage_root),
    )


def _ensure_venv(directory: Path, base_python: Path) -> Path:
    python = runtime_python(directory)
    if python.exists():
        return python

    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists() and any(directory.iterdir()):
        raise BootstrapError(
            f"Environment path exists but is not a usable virtualenv: {directory}"
        )
    _run([str(base_python), "-m", "venv", str(directory)])
    if not python.exists():
        raise BootstrapError(f"Virtualenv creation did not produce {python}")
    return python


def _ensure_packages(
    python: Path,
    package_specs: Iterable[tuple[str, str]],
    *,
    storage_root: Path,
    optional: bool = False,
) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    available = True
    for package, module in package_specs:
        if _module_available(python, module):
            continue
        try:
            _install(python, package, storage_root)
        except BootstrapError as exc:
            if optional:
                warnings.append(f"Optional package {package} unavailable: {exc}")
                available = False
                continue
            raise
        if not _module_available(python, module):
            message = f"Package {package} installed but import {module!r} still fails"
            if optional:
                warnings.append(message)
                available = False
            else:
                raise BootstrapError(message)
    return available, warnings


def _environment_metadata(runtime: Runtime) -> None:
    metadata = runtime.directory / "environment.json"
    metadata.write_text(json.dumps(runtime.as_dict(), indent=2) + "\n", encoding="utf-8")


def ensure_environment(
    project_root: Path,
    *,
    workspace_root: Path | None = None,
    install: bool = True,
    need_playwright: bool = False,
    base_python: Path | None = None,
) -> Runtime:
    """Ensure the workspace-local runtime exists and return its capabilities.

    ``install=False`` is useful for tests and for callers that want a clear
    diagnostic instead of modifying the workspace. Normal CLI invocations keep
    the default ``install=True`` requested by the skill contract.
    """

    project_root = project_root.resolve()
    workspace_root = (workspace_root or project_root).resolve()
    if not project_root.is_dir():
        raise BootstrapError(f"Project root is not a directory: {project_root}")
    if not workspace_root.is_dir():
        raise BootstrapError(f"Workspace root is not a directory: {workspace_root}")

    directory = runtime_directory(workspace_root)
    base_python = (base_python or Path(sys.executable)).resolve()
    python = runtime_python(directory)
    warnings: list[str] = []

    if not python.exists():
        if not install:
            raise BootstrapError(
                f"Missing pixel-perfect environment at {directory}; rerun without --no-auto-setup"
            )
        python = _ensure_venv(directory, base_python)

    if install:
        _ensure_packages(python, REQUIRED_PACKAGES, storage_root=directory)
        numpy_available, package_warnings = _ensure_packages(
            python, OPTIONAL_PACKAGES, storage_root=directory, optional=True
        )
        warnings.extend(package_warnings)
    else:
        numpy_available = _module_available(python, "numpy")

    playwright_available = _module_available(python, "playwright")
    if need_playwright and not playwright_available:
        if not install:
            raise BootstrapError(
                "Playwright is required for browser fallback; rerun without --no-auto-setup"
            )
        try:
            _install(python, PLAYWRIGHT_PACKAGE[0], directory)
            playwright_available = _module_available(python, "playwright")
        except BootstrapError as exc:
            raise BootstrapError(
                "No system browser was found and Playwright could not be installed. "
                f"{exc}"
            ) from exc

    runtime = Runtime(
        project_root=project_root,
        workspace_root=workspace_root,
        directory=directory,
        python=python,
        numpy_available=numpy_available,
        playwright_available=playwright_available,
        warnings=tuple(warnings),
    )
    if install:
        _environment_metadata(runtime)
    return runtime


def ensure_playwright_browser(runtime: Runtime, *, install: bool = True) -> None:
    """Install Playwright's Chromium browser once, when no system browser exists."""

    marker = runtime.directory / ".chromium-ready"
    if marker.exists():
        return
    if not install:
        raise BootstrapError(
            "Playwright Chromium is not installed; rerun without --no-auto-setup"
        )
    _run(
        [str(runtime.python), "-m", "playwright", "install", "chromium"],
        env=process_environment(runtime.directory),
    )
    marker.write_text("ready\n", encoding="utf-8")


def is_runtime_python(runtime: Runtime) -> bool:
    """Detect the venv by prefix, not resolved executable symlinks."""

    try:
        return Path(sys.prefix).resolve() == runtime.directory.resolve()
    except OSError:
        return False
