"""Framework-agnostic, read-only project reconnaissance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FRAMEWORK_MARKERS = {
    "next": "Next.js",
    "react": "React",
    "@vitejs/plugin-react": "React + Vite",
    "vite": "Vite",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "svelte": "Svelte",
    "@angular/core": "Angular",
    "solid-js": "Solid",
    "astro": "Astro",
    "remix": "Remix",
    "@tauri-apps/api": "Tauri",
    "electron": "Electron",
}


def _relative_files(root: Path, limit: int = 200) -> list[str]:
    ignored = {".git", ".xzy-env", ".artifacts", "node_modules", "__pycache__"}
    files: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in ignored for part in path.relative_to(root).parts):
            continue
        files.append(path.relative_to(root).as_posix())
        if len(files) >= limit:
            break
    return files


def _package_info(root: Path) -> dict[str, Any] | None:
    package_path = root / "package.json"
    if not package_path.is_file():
        return None
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"path": str(package_path), "error": str(exc)}

    dependencies = {}
    dependencies.update(package.get("dependencies", {}))
    dependencies.update(package.get("devDependencies", {}))
    frameworks = []
    for marker, label in FRAMEWORK_MARKERS.items():
        if marker in dependencies:
            frameworks.append(label)
    scripts = package.get("scripts", {})
    return {
        "path": str(package_path),
        "name": package.get("name"),
        "package_manager": _package_manager(root),
        "frameworks": sorted(set(frameworks)),
        "scripts": scripts if isinstance(scripts, dict) else {},
        "dependencies": sorted(dependencies),
    }


def _package_manager(root: Path) -> str | None:
    lockfiles = (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
        ("package-lock.json", "npm"),
    )
    for filename, manager in lockfiles:
        if (root / filename).exists():
            return manager
    return "npm" if (root / "package.json").exists() else None


def _candidate_entrypoints(root: Path) -> list[str]:
    names = {
        "index.html",
        "src/main.ts",
        "src/main.tsx",
        "src/main.js",
        "src/main.jsx",
        "src/App.tsx",
        "src/App.jsx",
        "app/page.tsx",
        "app/page.jsx",
        "pages/index.tsx",
        "pages/index.jsx",
        "manage.py",
        "pyproject.toml",
        "Cargo.toml",
        "go.mod",
    }
    return [file for file in _relative_files(root) if file in names]


def inspect_project(root: Path) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Project root is not a directory: {root}")

    package = _package_info(root)
    manifest_patterns = (
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "*.csproj",
    )
    manifests: list[str] = []
    for pattern in manifest_patterns:
        matches = list(root.glob(pattern))
        manifests.extend(
            match.relative_to(root).as_posix() for match in matches if match.is_file()
        )
    frameworks = package["frameworks"] if package and "frameworks" in package else []
    if (root / "index.html").exists() and not frameworks:
        frameworks = ["static HTML"]
    if (root / "pyproject.toml").exists():
        frameworks = [*frameworks, "Python project"]
    if (root / "Cargo.toml").exists():
        frameworks = [*frameworks, "Rust project"]

    return {
        "project_root": str(root),
        "frameworks": sorted(set(frameworks)),
        "manifests": manifests,
        "package": package,
        "candidate_entrypoints": _candidate_entrypoints(root),
        "files": _relative_files(root),
        "notes": [
            "Project inspection is read-only.",
            "Use the existing project command to start a dev server when a file URL is not sufficient.",
        ],
    }
