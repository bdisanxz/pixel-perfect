"""Create a structured section plan before visual implementation begins."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


IMPLEMENTATION_ORDER = [
    {
        "order": 1,
        "id": "global-frame",
        "goal": "Viewport, page overflow, global background, and persistent chrome.",
    },
    {
        "order": 2,
        "id": "primary-regions",
        "goal": "Major columns, panels, header/footer regions, and their boundaries.",
    },
    {
        "order": 3,
        "id": "internal-sections",
        "goal": "Cards, toolbars, lists, forms, tables, and other section-level structures.",
    },
    {
        "order": 4,
        "id": "repeated-elements",
        "goal": "Rows, icons, badges, controls, and repeated alignment patterns.",
    },
    {
        "order": 5,
        "id": "type-and-assets",
        "goal": "Fonts, text hierarchy, icons, images, colors, borders, and effects.",
    },
    {
        "order": 6,
        "id": "states-and-interactions",
        "goal": "Active, hover, loading, empty, error, focus, and user interaction states.",
    },
    {
        "order": 7,
        "id": "responsive-mapping",
        "goal": "Breakpoint behavior and responsive smoke checks without regressing the reference viewport.",
    },
]

REQUIRED_SECTION_FIELDS = [
    "id",
    "bounds",
    "visual_contract",
    "content_state",
    "layout_owner",
    "dependencies",
    "implementation_order",
    "verification_region",
    "responsive_behavior",
    "acceptance_criteria",
]


def assess_reference_complexity(reference: dict[str, Any]) -> dict[str, Any]:
    """Classify when a conservative section-first workflow is warranted."""

    width = int(reference["width"])
    height = int(reference["height"])
    signals: list[str] = []
    if height >= 1400:
        signals.append("tall_reference")
    if width * height >= 1_500_000:
        signals.append("large_reference_area")
    classification = "complex" if signals else "simple"
    return {
        "classification": classification,
        "section_first_required": classification == "complex",
        "signals": signals,
        "heuristic": {
            "tall_height_px": 1400,
            "large_area_px": 1_500_000,
            "note": "Use --section-first for visually dense short references that need the same treatment.",
        },
    }


def _bounds(values: Iterable[int]) -> dict[str, int]:
    x, y, width, height = (int(value) for value in values)
    return {"x": x, "y": y, "width": width, "height": height}


def _coerce_bounds(value: Any) -> dict[str, int]:
    if isinstance(value, dict):
        try:
            return _bounds((value[key] for key in ("x", "y", "width", "height")))
        except KeyError as exc:
            raise ValueError("Section bounds objects must contain x,y,width,height") from exc
    return _bounds(value)


def _default_section(
    section_id: str,
    bounds: Iterable[int],
    order: int,
) -> dict[str, Any]:
    normalized_bounds = _bounds(bounds)
    return {
        "id": section_id,
        "bounds": normalized_bounds,
        "visual_contract": [],
        "content_state": "default",
        "layout_owner": "unmapped",
        "dependencies": [],
        "implementation_order": order,
        "verification_region": normalized_bounds.copy(),
        "responsive_behavior": "Preserve the existing project behavior; document breakpoint exceptions.",
        "acceptance_criteria": [
            "The section renders at the declared bounds in the reference viewport.",
            "The section comparison improves without regressing an accepted section.",
            "Required visible behavior remains functional.",
        ],
        "status": "draft",
    }


def _merge_section(default: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    result = {**default, **supplied}
    result["bounds"] = _coerce_bounds(result["bounds"])
    verification = result.get("verification_region", result["bounds"])
    result["verification_region"] = _coerce_bounds(verification)
    missing = [
        field
        for field in REQUIRED_SECTION_FIELDS
        if result.get(field) in (None, "")
    ]
    if not result.get("visual_contract"):
        missing.append("visual_contract")
    if result.get("layout_owner") in (None, "", "unmapped"):
        missing.append("layout_owner")
    if not result.get("acceptance_criteria"):
        missing.append("acceptance_criteria")
    missing = list(dict.fromkeys(missing))
    result["missing_fields"] = missing
    result["status"] = "ready" if not missing else "draft"
    return result


def _read_sections_file(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read sections file {path}: {exc}") from exc
    sections = data.get("sections") if isinstance(data, dict) else data
    if not isinstance(sections, list):
        raise ValueError("Sections file must contain a JSON array or an object with a 'sections' array")
    if not all(isinstance(section, dict) for section in sections):
        raise ValueError("Every section in the sections file must be an object")
    return sections


def section_entries_from_file(path: Path) -> list[dict[str, Any]]:
    """Read section entries for crop/compare workflow metadata."""

    return _read_sections_file(path)


def section_regions_from_file(path: Path) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Read named section bounds from a rich section file or decomposition artifact."""

    regions = []
    for section in _read_sections_file(path):
        section_id = str(section.get("id", "")).strip()
        if not section_id:
            raise ValueError("Every section must have a non-empty id")
        if "bounds" not in section:
            raise ValueError(f"Section {section_id!r} is missing bounds")
        bounds = _coerce_bounds(section["bounds"])
        regions.append((section_id, tuple(bounds.values())))
    return regions


def _validate_bounds(bounds: Iterable[int], width: int, height: int) -> tuple[int, int, int, int]:
    values = tuple(int(value) for value in bounds)
    if len(values) != 4:
        raise ValueError("Section bounds must contain x,y,width,height")
    x, y, section_width, section_height = values
    if x < 0 or y < 0 or section_width <= 0 or section_height <= 0:
        raise ValueError("Section bounds must use non-negative coordinates and positive size")
    if x + section_width > width or y + section_height > height:
        raise ValueError(f"Section bounds {values} fall outside reference {width}x{height}")
    return values


def build_decomposition(
    reference: dict[str, Any],
    project: dict[str, Any],
    *,
    sections: Iterable[dict[str, Any]] = (),
    reference_path: Path | None = None,
    section_first: bool | None = None,
) -> dict[str, Any]:
    width = int(reference["width"])
    height = int(reference["height"])
    supplied_sections = list(sections)
    normalized_sections = []
    seen: set[str] = set()
    for index, supplied in enumerate(supplied_sections, start=1):
        section_id = str(supplied.get("id", "")).strip()
        if not section_id:
            raise ValueError("Every section must have a non-empty id")
        if section_id in seen:
            raise ValueError(f"Duplicate section id: {section_id}")
        seen.add(section_id)
        if "bounds" not in supplied:
            raise ValueError(f"Section {section_id!r} is missing bounds")
        normalized_input_bounds = _coerce_bounds(supplied["bounds"])
        validated_bounds = _validate_bounds(normalized_input_bounds.values(), width, height)
        default = _default_section(section_id, validated_bounds, index)
        normalized_sections.append(_merge_section(default, supplied))

    edge_peaks = reference.get("edge_peaks", {})
    complexity = assess_reference_complexity(reference)
    if section_first is not None:
        complexity["section_first_required"] = section_first
        complexity["override"] = "section-first" if section_first else "whole-page"
    else:
        complexity["override"] = "auto"
    status = "ready" if normalized_sections and all(
        section["status"] == "ready" for section in normalized_sections
    ) else "draft"
    section_first_required = bool(complexity["section_first_required"])
    return {
        "schema_version": 1,
        "status": status,
        "complexity": complexity,
        "workflow_policy": {
            "mode": "section-first" if section_first_required else "whole-page-allowed",
            "requires_named_sections": section_first_required,
            "requires_analysis_crops": False,
            "full_page_compare": "final-only" if section_first_required else "allowed",
            "grid": "optional analysis artifact when coordinate precision is needed",
            "visual_diagnostics": "opt-in via --diagnostic; crop remains an explicit analysis command",
        },
        "reference": {
            "path": str(reference_path.resolve()) if reference_path else reference.get("path"),
            "viewport": reference["viewport"],
            "width": width,
            "height": height,
        },
        "project": {
            "root": project.get("project_root"),
            "frameworks": project.get("frameworks", []),
            "candidate_entrypoints": project.get("candidate_entrypoints", []),
        },
        "implementation_order": IMPLEMENTATION_ORDER,
        "required_section_fields": REQUIRED_SECTION_FIELDS,
        "sections": normalized_sections,
        "suggested_boundaries": {
            "vertical": edge_peaks.get("x", [])[:16],
            "horizontal": edge_peaks.get("y", [])[:16],
            "note": "Use these as evidence for section bounds; do not treat edge peaks as semantic section names.",
        },
        "workflow": [
            "Define or review section bounds and visual contracts before editing source.",
            "Render a complete full-page baseline before focused section work.",
            "For section-first plans, name the active section and materialize a crop only when precise measurement is needed.",
            "Implement one section in dependency order, starting with the global frame.",
            "Render and compare the active section plus previously accepted sections without using a full-page score as the implementation gate.",
            "Repeat the focused edit, render, and section comparison cycle for each section; use crop or --diagnostic only when needed.",
            "Use full-page comparison only for final verification and regression review when section-first mode is required.",
            "Do not mark the decomposition ready until every section has an owner, state, acceptance criteria, and responsive behavior.",
        ],
    }


def decomposition_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Pixel-perfect section decomposition",
        "",
        f"- Status: **{plan['status'].upper()}**",
        f"- Reference viewport: `{plan['reference']['viewport']}`",
        f"- Frameworks: `{', '.join(plan['project'].get('frameworks', [])) or 'unclassified'}`",
        f"- Complexity: **{plan['complexity']['classification']}**",
        f"- Workflow: **{plan['workflow_policy']['mode']}**",
        "",
        "## Workflow policy",
        "",
        f"- Section-first required: `{plan['workflow_policy']['requires_named_sections']}`",
        f"- Analysis crops required: `{plan['workflow_policy']['requires_analysis_crops']}`",
        f"- Full-page compare: `{plan['workflow_policy']['full_page_compare']}`",
        f"- Visual diagnostics: `{plan['workflow_policy']['visual_diagnostics']}`",
        "- Grid overlay: optional analysis aid when precise coordinate measurement is needed.",
        "- Crops and grids are analysis artifacts only; never use them as runtime UI assets.",
        "",
        "## Implementation order",
        "",
        "| Order | Section | Goal |",
        "| ---: | --- | --- |",
    ]
    for phase in plan["implementation_order"]:
        lines.append(f"| {phase['order']} | `{phase['id']}` | {phase['goal']} |")

    lines.extend(["", "## Sections", ""])
    if not plan["sections"]:
        lines.append("No named sections supplied. Add sections before implementation, using the suggested edge evidence only as a starting point.")
    else:
        lines.extend(
            [
                "| Section | Bounds | Order | Owner | State | Status |",
                "| --- | --- | ---: | --- | --- | --- |",
            ]
        )
        for section in plan["sections"]:
            bounds = section["bounds"]
            box = f"{bounds['x']},{bounds['y']},{bounds['width']},{bounds['height']}"
            lines.append(
                f"| `{section['id']}` | `{box}` | {section['implementation_order']} | "
                f"`{section['layout_owner']}` | `{section['content_state']}` | **{section['status']}** |"
            )
            if section["missing_fields"]:
                lines.append(f"  - Missing fields: `{', '.join(section['missing_fields'])}`")

    lines.extend(["", "## Required section fields", ""])
    lines.extend(f"- `{field}`" for field in plan["required_section_fields"])
    lines.extend(
        [
            "",
            "## Evidence boundaries",
            "",
            f"- Vertical edge candidates: `{plan['suggested_boundaries']['vertical']}`",
            f"- Horizontal edge candidates: `{plan['suggested_boundaries']['horizontal']}`",
            "- Edge candidates are measurement evidence, not semantic labels.",
        ]
    )
    return "\n".join(lines) + "\n"


def load_and_build(
    reference: dict[str, Any],
    project: dict[str, Any],
    *,
    sections_file: Path | None = None,
    section_specs: Iterable[tuple[str, tuple[int, int, int, int]]] = (),
    reference_path: Path | None = None,
    section_first: bool | None = None,
) -> dict[str, Any]:
    supplied = _read_sections_file(sections_file) if sections_file else []
    supplied.extend(
        {"id": name, "bounds": list(bounds)} for name, bounds in section_specs
    )
    return build_decomposition(
        reference,
        project,
        sections=supplied,
        reference_path=reference_path,
        section_first=section_first,
    )
