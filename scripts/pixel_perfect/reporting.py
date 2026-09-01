"""Stable JSON and Markdown reports for model-readable visual verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _format_metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def comparison_markdown(report: dict[str, Any]) -> str:
    metrics = report.get("metrics") or {}
    scope = report.get("comparison_scope", "full-page")
    lines = [
        "# Pixel-perfect comparison",
        "",
        f"- Reference: `{report.get('reference')}`",
        f"- Candidate: `{report.get('candidate')}`",
        f"- Viewport: `{report.get('viewport')}`",
        f"- Comparison scope: **{scope}**",
        f"- Analysis engine: `{metrics.get('analysis_engine', 'region metrics')}`",
        "",
    ]
    if scope == "regions":
        lines.extend(
            [
                "## Focused section metrics",
                "",
                "Full-page metrics were intentionally omitted; only the named section crops below are evidence for this iteration.",
            ]
        )
    else:
        lines.extend(
            [
                "## Overall metrics",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
            ]
        )
        for key in (
            "mean_abs_error",
            "exact_fraction",
            "within_tolerance_fraction",
            "mismatch_fraction",
            "mismatch_pixels",
            "max_error",
            "tolerance",
        ):
            if key in metrics:
                lines.append(f"| `{key}` | `{_format_metric(metrics[key])}` |")

    regions = report.get("regions", [])
    if regions:
        lines.extend(
            [
                "",
                "## Regions",
                "",
                "| Region | Box | Mean error | Within tolerance | Mismatch fraction |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
        )
        for region in regions:
            lines.append(
                "| `{name}` | `{box}` | `{error}` | `{within}` | `{mismatch}` |".format(
                    name=region.get("name"),
                    box=",".join(str(value) for value in region.get("box", [])),
                    error=_format_metric(region.get("mean_abs_error", "n/a")),
                    within=_format_metric(region.get("within_tolerance_fraction", "n/a")),
                    mismatch=_format_metric(region.get("mismatch_fraction", "n/a")),
                )
            )

    bbox = metrics.get("mismatch_bbox")
    lines.extend(["", "## Diagnosis", ""])
    if scope == "regions":
        lines.append("- Full-page mismatch bounding box: `omitted (focused section scope)`")
    else:
        lines.append(f"- Mismatch bounding box: `{bbox or 'none'}`")
    reference_edges = report.get("reference_edge_peaks", {})
    candidate_edges = report.get("candidate_edge_peaks", {})
    lines.append(
        "- Strong reference edges: "
        + ", ".join(
            f"x={item['position']} ({item['score']:.2f})"
            for item in reference_edges.get("x", [])[:6]
        )
        + "; "
        + ", ".join(
            f"y={item['position']} ({item['score']:.2f})"
            for item in reference_edges.get("y", [])[:6]
        )
        if reference_edges.get("x") or reference_edges.get("y")
        else "- Strong reference edges: unavailable"
    )
    lines.append(
        "- Strong candidate edges: "
        + ", ".join(
            f"x={item['position']} ({item['score']:.2f})"
            for item in candidate_edges.get("x", [])[:6]
        )
        + "; "
        + ", ".join(
            f"y={item['position']} ({item['score']:.2f})"
            for item in candidate_edges.get("y", [])[:6]
        )
        if candidate_edges.get("x") or candidate_edges.get("y")
        else "- Strong candidate edges: unavailable"
    )
    if scope != "regions":
        lines.append(
            "- Top mismatch rows: "
            + ", ".join(
                f"{item['position']} ({item['fraction']:.3f})"
                for item in metrics.get("top_mismatch_rows", [])[:8]
            )
            if metrics.get("top_mismatch_rows")
            else "- Top mismatch rows: none"
        )
        lines.append(
            "- Top mismatch columns: "
            + ", ".join(
                f"{item['position']} ({item['fraction']:.3f})"
                for item in metrics.get("top_mismatch_columns", [])[:8]
            )
            if metrics.get("top_mismatch_columns")
            else "- Top mismatch columns: none"
        )
    hotspots = metrics.get("tile_hotspots", [])[:10]
    if hotspots:
        lines.append("- Hottest tiles:")
        for tile in hotspots:
            lines.append(
                "  - `{x},{y} {width}x{height}`: mean `{mean_error}`, mismatch `{mismatch_fraction}`".format(
                    **tile
                )
            )
    if scope == "regions":
        lines.append("- Region-local mismatch boxes are recorded in `comparison.json` as `mismatch_bbox` and `mismatch_bbox_absolute`.")
    return "\n".join(lines) + "\n"


def crop_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Pixel-perfect analysis crops",
        "",
        f"- Reference: `{manifest.get('reference')}`",
        f"- Candidate: `{manifest.get('candidate') or 'not supplied'}`",
        f"- Viewport: `{manifest.get('viewport')}`",
        "- Coordinate system: absolute source-image pixels",
        f"- Grid: `{'enabled' if manifest.get('grid', {}).get('enabled') else 'disabled'}`",
        "",
        "## Sections",
        "",
        "| Section | Bounds | Reference crop | Candidate crop | Grid artifacts |",
        "| --- | --- | --- | --- | --- |",
    ]
    for section in manifest.get("sections", []):
        bounds = ",".join(str(value) for value in section.get("bounds", []))
        files = section.get("files", {})
        grid_files = ", ".join(
            f"`{path}`" for key, path in files.items() if "grid" in key
        ) or "none"
        lines.append(
            f"| `{section.get('id')}` | `{bounds}` | `{files.get('reference')}` | "
            f"`{files.get('candidate', 'not supplied')}` | {grid_files} |"
        )
    lines.extend(
        [
            "",
            "Grid files are analysis-only overlays. They must never be copied into the runtime UI or committed as implementation assets.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_comparison_reports(
    report: dict[str, Any], output_dir: Path
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "comparison.json"
    markdown_path = output_dir / "comparison.md"
    write_json(json_path, report)
    markdown_path.write_text(comparison_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def verification_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Pixel-perfect verification",
        "",
        f"- Status: **{result.get('status', 'unknown').upper()}**",
        f"- Viewport: `{result.get('viewport', 'unknown')}`",
        "",
        "## Checks",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for check in result.get("checks", []):
        lines.append(
            "| `{name}` | **{status}** | {detail} |".format(
                name=check.get("name"),
                status=check.get("status"),
                detail=str(check.get("detail", "")).replace("|", "\\|"),
            )
        )
    if result.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result["warnings"])
    return "\n".join(lines) + "\n"
