"""Target URL resolution and render-level smoke checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .browser import capture
from .bootstrap import Runtime
from .images import ImageAnalysisError, load_rgb, parse_viewport


def resolve_target_url(
    project_root: Path,
    *,
    workspace_root: Path | None = None,
    url: str | None = None,
    entry: str | None = None,
) -> str:
    """Resolve a URL or local entrypoint without inventing a dev server."""

    if url:
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https", "file", "data"}:
            return url
        candidate = Path(url).expanduser()
        if not candidate.is_absolute():
            candidate = (workspace_root or project_root) / candidate
        if candidate.is_file():
            return candidate.resolve().as_uri()
        raise ImageAnalysisError(
            f"Target URL must include http(s)/file scheme or name an existing file: {url}"
        )

    if entry:
        candidate = Path(entry).expanduser()
        if not candidate.is_absolute():
            candidate = project_root / candidate
    else:
        candidate = project_root / "index.html"
    if not candidate.is_file():
        raise ImageAnalysisError(
            f"No render target found at {candidate}. Provide --url for a running dev server "
            "or --entry for a local HTML entrypoint."
        )
    return candidate.resolve().as_uri()


def render_target(
    runtime: Runtime,
    *,
    url: str,
    output: Path,
    viewport: str,
    browser: str | None = None,
    wait_ms: int = 250,
    timeout: float = 60.0,
    ready_selector: str | None = None,
    install: bool = True,
) -> dict[str, Any]:
    width, height = parse_viewport(viewport)
    return capture(
        runtime,
        url=url,
        output=output,
        width=width,
        height=height,
        browser=browser,
        wait_ms=wait_ms,
        timeout=timeout,
        ready_selector=ready_selector,
        install=install,
    )


def smoke_check(path: Path) -> dict[str, Any]:
    """Return conservative non-vision evidence that a screenshot rendered."""

    image = load_rgb(path)
    sample = image.resize((min(96, image.width), max(1, round(image.height * 96 / image.width))))
    flattened = getattr(sample, "get_flattened_data", None)
    colors = len(set(flattened() if flattened is not None else sample.getdata()))
    extrema = image.getextrema()
    channel_spans = [high - low for low, high in extrema]
    non_flat = sum(span > 2 for span in channel_spans) > 0
    result = {
        "path": str(Path(path).expanduser().resolve()),
        "width": image.width,
        "height": image.height,
        "sample_unique_colors": colors,
        "channel_spans": channel_spans,
        "non_flat": non_flat,
        "status": "pass" if non_flat and colors > 1 else "fail",
    }
    if not non_flat or colors <= 1:
        result["reason"] = "Rendered image is flat; page may be blank or failed to load"
    return result
