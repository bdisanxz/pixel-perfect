"""Public command-line interface for the pixel-perfect skill."""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .bootstrap import (
    BootstrapError,
    Runtime,
    ensure_environment,
    is_runtime_python,
)
from .browser import BrowserError, find_system_browser
from .iterations import (
    IterationContext,
    IterationError,
    append_iteration_event,
    ensure_next_event,
    resolve_iteration,
)
from .project import inspect_project
from .reporting import (
    verification_markdown,
    write_comparison_reports,
    write_json,
)


class CliError(RuntimeError):
    """Raised for a user-facing CLI error."""


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project checkout used for source inspection and local entrypoints (default: .)",
    )
    parser.add_argument(
        "--page-name",
        help="Logical page name for default artifacts (defaults to the reference filename stem)",
    )
    parser.add_argument(
        "--no-auto-setup",
        action="store_true",
        help="Do not create/install the workspace-local environment",
    )
    parser.add_argument(
        "--runtime-ready",
        action="store_true",
        help=argparse.SUPPRESS,
    )


def _add_iteration_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--iteration",
        type=int,
        help="Canonical zero-based visual iteration number (000 is the baseline)",
    )
    parser.add_argument(
        "--focus",
        help="Stable decomposition section ID targeted by a focused iteration",
    )
    parser.add_argument(
        "--hypothesis",
        help="Short semantic label for the one source change being tested",
    )
    parser.add_argument(
        "--iteration-note",
        help="Human-readable source-change evidence recorded in iterations.json",
    )


def _add_diagnostic_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="Materialize visual diagnostic artifacts such as crops, overlays, diffs, and masks",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pixel-perfect",
        description="Render, compare, and diagnose UI screenshots with text-readable evidence.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="Create or repair the workspace-local runtime")
    _add_runtime_options(setup)
    setup.add_argument(
        "--install-browser",
        action="store_true",
        help="Also install Playwright Chromium when no system browser is available",
    )

    inspect = commands.add_parser(
        "inspect", help="Inspect the reference image and project without rendering"
    )
    _add_runtime_options(inspect)
    inspect.add_argument("--reference", required=True, help="Reference image path")
    inspect.add_argument(
        "--output",
        help="JSON output path (normalized under the page artifact directory; default: <page artifact dir>/inspection.json)",
    )
    inspect.add_argument(
        "--no-project",
        action="store_true",
        help="Omit project reconnaissance from the JSON report",
    )
    inspect.add_argument(
        "--point",
        action="append",
        default=[],
        help="Pixel probe x,y (repeatable)",
    )

    decompose = commands.add_parser(
        "decompose", help="Create a structured section plan from reference evidence"
    )
    _add_runtime_options(decompose)
    decompose.add_argument("--reference", required=True, help="Reference image path")
    decompose.add_argument(
        "--sections-file",
        help="Optional JSON array/object defining rich section contracts",
    )
    decompose.add_argument(
        "--section",
        action="append",
        default=[],
        help="Named section bounds: id=x,y,width,height (repeatable)",
    )
    decompose.add_argument(
        "--section-first",
        action="store_true",
        help="Require the crop-first section workflow even for a short reference",
    )
    decompose.add_argument(
        "--output-dir",
        default=None,
        help="Artifact/report directory normalized under the page artifact directory (default: .artifacts/pixel-perfect/<page-name>)",
    )

    crop = commands.add_parser(
        "crop", help="Create page-scoped analysis crops and optional coordinate grids"
    )
    _add_runtime_options(crop)
    _add_iteration_options(crop)
    crop.add_argument("--reference", required=True, help="Reference image path")
    crop.add_argument(
        "--candidate", help="Optional full-page candidate image to crop with the reference"
    )
    crop.add_argument(
        "--sections-file",
        help="Optional JSON array/object defining named section bounds",
    )
    crop.add_argument(
        "--section",
        action="append",
        default=[],
        help="Named section bounds: id=x,y,width,height (repeatable)",
    )
    crop.add_argument(
        "--output-dir",
        default=None,
        help="Artifact/report directory normalized under the page artifact directory",
    )
    crop.add_argument(
        "--grid",
        action="store_true",
        help="Also write enlarged coordinate-grid artifacts for each crop",
    )
    crop.add_argument(
        "--grid-spacing",
        type=int,
        default=10,
        help="Grid spacing in source pixels (default: 10)",
    )
    crop.add_argument(
        "--grid-scale",
        type=int,
        default=2,
        help="Nearest-neighbor enlargement factor for grid artifacts (default: 2)",
    )
    crop.add_argument(
        "--grid-axis",
        choices=("both", "vertical", "horizontal"),
        default="both",
        help="Grid orientation (default: both)",
    )

    render = commands.add_parser("render", help="Capture one deterministic viewport screenshot")
    _add_runtime_options(render)
    _add_iteration_options(render)
    render.add_argument("--reference", help="Reference image; derives viewport dimensions")
    render.add_argument("--viewport", help="Viewport as WIDTHxHEIGHT when no reference is given")
    render.add_argument("--url", help="HTTP(S), file, data URL, or local file path")
    render.add_argument("--entry", help="Local entrypoint relative to the project root")
    render.add_argument(
        "--output",
        help="Screenshot output path normalized under the page artifact directory (default: <page artifact dir>/candidate.png)",
    )
    render.add_argument(
        "--browser",
        help="Browser executable path, or 'playwright' to force the bundled adapter",
    )
    render.add_argument("--wait-ms", type=int, default=250)
    render.add_argument("--timeout", type=float, default=60.0)
    render.add_argument("--ready-selector")
    render.add_argument(
        "--report",
        help="JSON render report path normalized under the page artifact directory (default: next to the screenshot)",
    )

    compare = commands.add_parser(
        "compare", help="Compare two same-size images and write machine-readable reports"
    )
    _add_runtime_options(compare)
    _add_iteration_options(compare)
    _add_diagnostic_option(compare)
    compare.add_argument("--reference", required=True, help="Reference image path")
    compare.add_argument("--candidate", required=True, help="Rendered candidate image path")
    compare.add_argument(
        "--sections-file",
        help="Optional JSON array/object defining named section bounds",
    )
    compare.add_argument(
        "--section-only",
        action="store_true",
        help="Compare named sections only and omit full-page metrics/artifacts",
    )
    compare.add_argument(
        "--output-dir",
        default=None,
        help="Artifact/report directory normalized under the page artifact directory (default: .artifacts/pixel-perfect/<page-name>)",
    )
    compare.add_argument("--tolerance", type=int, default=10)
    compare.add_argument("--tile-size", type=int, default=64)
    compare.add_argument(
        "--region",
        action="append",
        default=[],
        help="Named region: name=x,y,width,height (repeatable)",
    )
    compare.add_argument(
        "--point",
        action="append",
        default=[],
        help="Pixel probe x,y (repeatable)",
    )

    verify = commands.add_parser(
        "verify", help="Render if needed, compare, smoke-check, and return an acceptance verdict"
    )
    _add_runtime_options(verify)
    _add_iteration_options(verify)
    _add_diagnostic_option(verify)
    verify.add_argument(
        "--final",
        action="store_true",
        help="Store final full-page verification under the page's final/ directory",
    )
    verify.add_argument("--reference", required=True, help="Reference image path")
    verify.add_argument("--candidate", help="Existing candidate screenshot; otherwise render one")
    verify.add_argument(
        "--sections-file",
        help="Optional JSON array/object defining named section bounds",
    )
    verify.add_argument(
        "--section-only",
        action="store_true",
        help="Compare named sections only and omit full-page metrics/artifacts",
    )
    verify.add_argument("--url", help="HTTP(S), file, data URL, or local file path")
    verify.add_argument("--entry", help="Local entrypoint relative to the project root")
    verify.add_argument("--viewport", help="Override reference viewport as WIDTHxHEIGHT")
    verify.add_argument(
        "--responsive-viewport",
        action="append",
        default=[],
        help="Additional smoke viewport WIDTHxHEIGHT (repeatable)",
    )
    verify.add_argument("--browser")
    verify.add_argument("--wait-ms", type=int, default=250)
    verify.add_argument("--timeout", type=float, default=60.0)
    verify.add_argument("--ready-selector")
    verify.add_argument(
        "--output-dir",
        default=None,
        help="Artifact/report directory normalized under the page artifact directory (default: .artifacts/pixel-perfect/<page-name>)",
    )
    verify.add_argument("--tolerance", type=int, default=10)
    verify.add_argument("--tile-size", type=int, default=64)
    verify.add_argument("--max-mae", type=float, default=10.0)
    verify.add_argument(
        "--max-region-mae",
        type=float,
        default=None,
        help="Maximum mean error for each named region (default: max-mae * 1.5)",
    )
    verify.add_argument("--min-within-tolerance", type=float, default=0.85)
    verify.add_argument(
        "--max-hotspot-mean-error",
        type=float,
        default=64.0,
        help="Maximum mean error allowed in the hottest analysis tile",
    )
    verify.add_argument(
        "--region",
        action="append",
        default=[],
        help="Named region: name=x,y,width,height (repeatable)",
    )
    verify.add_argument(
        "--point",
        action="append",
        default=[],
        help="Pixel probe x,y (repeatable)",
    )
    verify.add_argument(
        "--previous-report",
        help="Previous comparison.json; checks overall and named-region MAE for regression",
    )
    verify.add_argument(
        "--regression-tolerance",
        type=float,
        default=0.5,
        help="Allowed MAE increase over --previous-report",
    )
    return parser


def _workspace_root() -> Path:
    return Path.cwd().resolve()


def _project_root(args: argparse.Namespace) -> Path:
    root = Path(args.project_root).expanduser()
    if not root.is_absolute():
        root = _workspace_root() / root
    root = root.resolve()
    if not root.is_dir():
        raise CliError(f"Project root is not a directory: {root}")
    return root


def _path(base: Path, value: str | None, default: str | None = None) -> Path | None:
    raw = value if value is not None else default
    if raw is None:
        return None
    result = Path(raw).expanduser()
    return result if result.is_absolute() else base / result


def _needs_playwright(browser: str | None) -> bool:
    if browser and browser.lower() in {"playwright", "chromium-playwright"}:
        return True
    return browser is None and find_system_browser() is None


def _prepare_runtime(
    args: argparse.Namespace,
    *,
    need_browser: bool = False,
) -> tuple[Path, Path, Runtime]:
    workspace = _workspace_root()
    root = _project_root(args)
    install = not args.no_auto_setup
    need_playwright = need_browser and _needs_playwright(getattr(args, "browser", None))
    runtime = ensure_environment(
        root,
        workspace_root=workspace,
        install=install,
        need_playwright=need_playwright,
    )

    if not args.runtime_ready and not is_runtime_python(runtime):
        script = Path(__file__).resolve().parents[1] / "pixel-perfect.py"
        forwarded = list(sys.argv[1:])
        if "--runtime-ready" not in forwarded:
            forwarded.append("--runtime-ready")
        os.execv(str(runtime.python), [str(runtime.python), str(script), *forwarded])
        raise AssertionError("execv returned unexpectedly")
    return root, workspace, runtime


def _json_print(data: dict[str, Any]) -> None:
    print(json.dumps(data, sort_keys=True))


def _pointer(
    status: str,
    operation: str,
    *,
    reports: dict[str, dict[str, str]] | None = None,
    artifacts: dict[str, dict[str, str]] | None = None,
    **context: Any,
) -> None:
    pointer: dict[str, Any] = {"status": status, "operation": operation}
    if reports:
        pointer["reports"] = reports
    if artifacts:
        pointer["artifacts"] = artifacts
    pointer.update(context)
    _json_print(pointer)


def _output_file(path: Path | str, description: str) -> dict[str, str]:
    return {
        "output_path": str(Path(path).expanduser().resolve()),
        "description": description,
    }


def _output_files(
    paths: dict[str, str], descriptions: dict[str, str]
) -> dict[str, dict[str, str]]:
    return {
        name: _output_file(path, descriptions[name])
        for name, path in paths.items()
    }


def _page_name(args: argparse.Namespace) -> str:
    value = getattr(args, "page_name", None)
    if value is None:
        reference = getattr(args, "reference", None)
        value = Path(reference).stem if reference else "default"
    name = str(value).strip()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise CliError("--page-name must be a non-empty single directory name")
    return name


def _default_artifact_dir(workspace: Path, page_name: str) -> Path:
    return workspace / ".artifacts" / "pixel-perfect" / page_name


def _page_artifact_dir(args: argparse.Namespace, workspace: Path) -> Path:
    return _default_artifact_dir(workspace, _page_name(args)).resolve()


def _iteration_context(
    args: argparse.Namespace, workspace: Path
) -> IterationContext | None:
    cached = getattr(args, "_resolved_iteration_context", None)
    if cached is not None:
        return cached
    number = getattr(args, "iteration", None)
    if number is None:
        return None
    try:
        context = resolve_iteration(
            _page_artifact_dir(args, workspace),
            number,
            focus=getattr(args, "focus", None),
            hypothesis=getattr(args, "hypothesis", None),
            note=getattr(args, "iteration_note", None),
        )
    except IterationError as exc:
        raise CliError(str(exc)) from exc
    # A new iteration directory is created while its first command runs, before
    # the append-only ledger event is written. Cache that validated identity so
    # later path/focus checks in the same command do not mistake it for stale data.
    setattr(args, "_resolved_iteration_context", context)
    return context


def _canonical_output_dir(args: argparse.Namespace, workspace: Path) -> Path:
    context = _iteration_context(args, workspace)
    if context is not None:
        if getattr(args, "final", False):
            raise CliError("--final cannot be combined with --iteration")
        return context.directory
    if getattr(args, "final", False):
        return _page_artifact_dir(args, workspace) / "final"
    return _page_artifact_dir(args, workspace)


def _page_scoped_path(
    args: argparse.Namespace,
    workspace: Path,
    value: str,
    *,
    option: str,
) -> Path:
    page_name = _page_name(args)
    page_dir = _page_artifact_dir(args, workspace)
    base_dir = _canonical_output_dir(args, workspace)
    raw = Path(value).expanduser()
    if raw.is_absolute():
        path = raw.resolve()
    elif raw.parts[:2] == (".artifacts", "pixel-perfect"):
        suffix = raw.parts[2:]
        if suffix and suffix[0] == page_name:
            suffix = suffix[1:]
        path = base_dir.joinpath(*suffix).resolve()
    else:
        path = (base_dir / raw).resolve()
    try:
        path.relative_to(base_dir)
    except ValueError:
        filename = path.name
        if not filename or filename in {".", ".."}:
            raise CliError(f"{option} must name a file or directory under {base_dir}")
        path = (base_dir / filename).resolve()
    # Keep the page directory variable in this helper so the containment contract
    # remains explicit even when the canonical base is a nested iteration/final dir.
    try:
        path.relative_to(page_dir)
    except ValueError as exc:
        raise CliError(f"{option} must remain under {page_dir}") from exc
    return path


def _artifact_dir(args: argparse.Namespace, workspace: Path) -> Path:
    if getattr(args, "output_dir", None) and (
        getattr(args, "iteration", None) is not None or getattr(args, "final", False)
    ):
        raise CliError("--output-dir cannot be combined with --iteration or --final")
    if getattr(args, "output_dir", None):
        return _page_scoped_path(args, workspace, args.output_dir, option="--output-dir")
    return _canonical_output_dir(args, workspace)


def _stage_reference(
    args: argparse.Namespace, workspace: Path, value: str
) -> Path:
    source = _path(workspace, value)
    assert source is not None
    source = source.resolve()
    if not source.is_file():
        raise CliError(f"Image file not found: {source}")
    target = _page_artifact_dir(args, workspace) / "reference.png"
    if target != source:
        if target.exists() and not filecmp.cmp(source, target, shallow=False):
            raise CliError(
                f"Reference snapshot already exists at {target} with different contents; "
                "choose a new --page-name"
            )
        if not target.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            except OSError as exc:
                raise CliError(f"Could not stage reference image at {target}: {exc}") from exc
    return target


def _stage_candidate(
    args: argparse.Namespace,
    workspace: Path,
    value: str,
    *,
    target_dir: Path | None = None,
    filename: str = "candidate-input.png",
) -> Path:
    source = _path(workspace, value)
    assert source is not None
    if target_dir is not None:
        target = target_dir / filename
    elif getattr(args, "final", False):
        target = _artifact_dir(args, workspace) / "candidate.png"
    else:
        target = _page_scoped_path(args, workspace, value, option="--candidate")
    source = source.resolve()
    if source == target:
        return target
    if not source.is_file():
        raise CliError(f"Candidate image not found: {source}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        raise CliError(f"Could not stage candidate image at {target}: {exc}") from exc
    return target


def _render_report_path(output: Path) -> Path:
    report = output.with_suffix(".json")
    return report if report != output else output.with_name(f"{output.name}.report.json")


def _iteration_guard(
    args: argparse.Namespace, workspace: Path, operation: str
) -> IterationContext | None:
    context = _iteration_context(args, workspace)
    if context is None:
        return None
    try:
        ensure_next_event(_page_artifact_dir(args, workspace), context, operation)
    except IterationError as exc:
        raise CliError(str(exc)) from exc
    return context


def _ensure_iteration_artifact_is_new(
    context: IterationContext | None, path: Path
) -> None:
    if context is not None and path.exists():
        raise CliError(
            f"Iteration {context.number:03d} already has {path.name}; "
            "start a new iteration instead of overwriting evidence"
        )


def _validate_iteration_focus(
    args: argparse.Namespace,
    workspace: Path,
    names: list[str],
) -> None:
    context = _iteration_context(args, workspace)
    if context is None:
        return
    numeric_suffixes = [name for name in names if re.search(r"\d+$", name)]
    if numeric_suffixes:
        raise CliError(
            "Iteration section IDs must be stable decomposition IDs; remove numeric "
            f"attempt suffixes from {numeric_suffixes}"
        )
    if context.focus not in names:
        raise CliError(
            f"Iteration focus {context.focus!r} is not present in this command's sections"
        )


def _record_iteration_event(
    args: argparse.Namespace,
    workspace: Path,
    context: IterationContext | None,
    operation: str,
    event: dict[str, Any],
) -> dict[str, str] | None:
    if context is None:
        return None
    try:
        return append_iteration_event(
            _page_artifact_dir(args, workspace), context, operation, event
        )
    except IterationError as exc:
        raise CliError(str(exc)) from exc


def _iteration_reports(
    ledger_paths: dict[str, str] | None,
) -> dict[str, dict[str, str]]:
    if ledger_paths is None:
        return {}
    reports = {
        "iteration_ledger_json": _output_file(
            ledger_paths["json"], "Ordered semantic visual-iteration ledger."
        ),
        "iteration_ledger_markdown": _output_file(
            ledger_paths["markdown"], "Human-readable ordered visual-iteration ledger."
        ),
    }
    if "manifest" in ledger_paths:
        reports["iteration_manifest"] = _output_file(
            ledger_paths["manifest"], "Current iteration manifest and event trace."
        )
    return reports


def _iteration_context_pointer(
    context: IterationContext | None,
) -> dict[str, Any]:
    if context is None:
        return {}
    return {
        "iteration": context.number,
        "focus": context.focus,
        "hypothesis": context.hypothesis,
    }


def _error_directory(args: argparse.Namespace, workspace: Path) -> Path:
    try:
        context = _iteration_context(args, workspace)
        if context is not None:
            return context.directory / "errors"
        if getattr(args, "final", False):
            return _page_artifact_dir(args, workspace) / "final" / "errors"
        return _page_artifact_dir(args, workspace) / "errors"
    except CliError:
        try:
            return _default_artifact_dir(workspace, _page_name(args)) / "errors"
        except CliError:
            return _default_artifact_dir(workspace, "default") / "errors"


def _persist_error(args: argparse.Namespace, workspace: Path, exc: Exception) -> Path | None:
    directory = _error_directory(args, workspace)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{args.command}-error-{timestamp}-{os.getpid()}.json"
    try:
        write_json(
            path,
            {
                "status": "error",
                "operation": args.command,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
    except Exception:
        return None
    return path


def _error_summary(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return type(exc).__name__
    return message.splitlines()[0][:500]


def _validate_named_regions(
    regions: list[tuple[str, tuple[int, int, int, int]]], size: tuple[int, int]
) -> list[tuple[str, tuple[int, int, int, int]]]:
    width, height = size
    names: set[str] = set()
    for name, (x, y, region_width, region_height) in regions:
        if name in names:
            raise CliError(f"Duplicate region name: {name}")
        if (
            x < 0
            or y < 0
            or region_width <= 0
            or region_height <= 0
            or x + region_width > width
            or y + region_height > height
        ):
            raise CliError(
                f"Region {name!r} falls outside image {width}x{height}: "
                f"{x},{y},{region_width},{region_height}"
            )
        names.add(name)
    return regions


def _parse_regions(values: list[str], size: tuple[int, int]) -> list[tuple[str, tuple[int, int, int, int]]]:
    from .images import parse_region

    return _validate_named_regions([parse_region(value) for value in values], size)


def _regions_from_args(
    args: argparse.Namespace,
    workspace: Path,
    size: tuple[int, int],
    *,
    section_values: list[str] | None = None,
) -> list[tuple[str, tuple[int, int, int, int]]]:
    from .decomposition import section_regions_from_file
    from .images import parse_region

    values = list(getattr(args, "region", []) or [])
    values.extend(section_values or [])
    regions = [parse_region(value) for value in values]
    sections_file_value = getattr(args, "sections_file", None)
    if sections_file_value:
        sections_file = _path(workspace, sections_file_value)
        assert sections_file is not None
        if not sections_file.is_file():
            raise CliError(f"Sections file not found: {sections_file}")
        try:
            regions.extend(section_regions_from_file(sections_file))
        except ValueError as exc:
            raise CliError(str(exc)) from exc
    elif not regions and getattr(args, "section_only", False):
        generated_sections = _default_artifact_dir(
            workspace, _page_name(args)
        ) / "sections.json"
        if generated_sections.is_file():
            try:
                regions.extend(section_regions_from_file(generated_sections))
            except ValueError as exc:
                raise CliError(str(exc)) from exc
    return _validate_named_regions(regions, size)


def _parse_points(values: list[str], size: tuple[int, int]) -> list[tuple[int, int]]:
    from .images import parse_point, validate_points

    return validate_points([parse_point(value) for value in values], size)


def _artifact_descriptions(paths: dict[str, str], prefix: str) -> dict[str, str]:
    return {name: f"{prefix}: {name}." for name in paths}


def _cmd_decompose(args: argparse.Namespace) -> int:
    root, workspace, runtime = _prepare_runtime(args)
    from .decomposition import decomposition_markdown, load_and_build
    from .images import inspect_image

    reference = _stage_reference(args, workspace, args.reference)
    output_dir = _artifact_dir(args, workspace)
    sections_file = _path(workspace, args.sections_file)
    assert reference is not None and output_dir is not None
    if sections_file is None and not args.section:
        generated_sections = output_dir / "sections.json"
        if generated_sections.is_file():
            sections_file = generated_sections
    if sections_file is not None and not sections_file.is_file():
        raise CliError(f"Sections file not found: {sections_file}")
    reference_info = inspect_image(reference)
    project_info = inspect_project(root)
    section_specs = [_parse_section(value) for value in args.section]
    plan = load_and_build(
        reference_info,
        project_info,
        sections_file=sections_file,
        section_specs=section_specs,
        reference_path=reference,
        section_first=True if args.section_first else None,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "decomposition.json"
    markdown_path = output_dir / "decomposition.md"
    plan["runtime"] = runtime.as_dict()
    write_json(json_path, plan)
    markdown_path.write_text(decomposition_markdown(plan), encoding="utf-8")
    _pointer(
        plan["status"],
        "decompose",
        reports={
            "json": _output_file(
                json_path, "Structured section plan and visual implementation contracts."
            ),
            "markdown": _output_file(
                markdown_path,
                "Concise human-readable section plan for implementation sequencing.",
            ),
        },
    )
    return 0


def _section_entry_box(entry: dict[str, Any]) -> tuple[int, int, int, int]:
    bounds = entry.get("bounds")
    if isinstance(bounds, dict):
        try:
            values = tuple(int(bounds[key]) for key in ("x", "y", "width", "height"))
        except (KeyError, TypeError, ValueError) as exc:
            raise CliError("Section bounds objects must contain integer x,y,width,height") from exc
    else:
        try:
            values = tuple(int(value) for value in bounds)
        except (TypeError, ValueError) as exc:
            raise CliError("Section bounds must contain x,y,width,height") from exc
    if len(values) != 4:
        raise CliError("Section bounds must contain x,y,width,height")
    return (values[0], values[1], values[2], values[3])


def _load_section_entries(path: Path) -> list[dict[str, Any]]:
    from .decomposition import section_entries_from_file

    try:
        return section_entries_from_file(path)
    except ValueError as exc:
        raise CliError(str(exc)) from exc


def _update_generated_sections_file(
    path: Path,
    *,
    reference: Path,
    viewport: str,
    regions: list[tuple[str, tuple[int, int, int, int]]],
    source_entries: list[dict[str, Any]],
    persist: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    existing = _load_section_entries(path) if path.is_file() else []
    existing_by_id: dict[str, dict[str, Any]] = {}
    for entry in existing:
        section_id = str(entry.get("id", "")).strip()
        if not section_id:
            raise CliError(f"Generated sections file contains an entry without an id: {path}")
        if section_id in existing_by_id:
            raise CliError(f"Generated sections file contains duplicate id: {section_id}")
        existing_by_id[section_id] = dict(entry)

    incoming_by_id: dict[str, dict[str, Any]] = {}
    for entry in source_entries:
        section_id = str(entry.get("id", "")).strip()
        if not section_id:
            raise CliError("Every section must have a non-empty id")
        if section_id in incoming_by_id:
            raise CliError(f"Duplicate section id: {section_id}")
        incoming_by_id[section_id] = dict(entry)

    for name, box in regions:
        incoming = incoming_by_id.setdefault(name, {"id": name, "bounds": list(box)})
        if _section_entry_box(incoming) != box:
            raise CliError(
                f"Section {name!r} has conflicting bounds between its sources"
            )

    merged = [dict(entry) for entry in existing]
    merged_ids = set(existing_by_id)
    for name, box in regions:
        incoming = incoming_by_id[name]
        if name in existing_by_id:
            if _section_entry_box(existing_by_id[name]) != box:
                raise CliError(
                    f"Section {name!r} already exists with different bounds; "
                    "choose a new id or correct the section bounds"
                )
            continue
        merged.append(dict(incoming))
        merged_ids.add(name)

    # Include rich entries supplied by --sections-file even if their order was not
    # represented by a direct --section option.
    for entry in source_entries:
        name = str(entry.get("id", "")).strip()
        if name in merged_ids:
            continue
        merged.append(dict(entry))
        merged_ids.add(name)

    status = "updated" if path.is_file() else "created"
    payload = {
        "schema_version": 1,
        "reference": str(reference.resolve()),
        "viewport": viewport,
        "coordinate_system": "absolute source-image pixels; bounds use x,y,width,height",
        "generated_by": "pixel-perfect crop",
        "sections": merged,
    }
    if persist:
        write_json(path, payload)
    return merged, status


def _cmd_crop(args: argparse.Namespace) -> int:
    root, workspace, runtime = _prepare_runtime(args)
    from .images import artifact_slug, crop_image, load_rgb, save_grid_overlay
    from .reporting import crop_markdown

    del root
    context = _iteration_guard(args, workspace, "crop")
    reference = _stage_reference(args, workspace, args.reference)
    output_dir = _artifact_dir(args, workspace)
    candidate = _path(workspace, args.candidate)
    assert reference is not None
    reference_image = load_rgb(reference)
    candidate_image = load_rgb(candidate) if candidate is not None else None
    if candidate_image is not None and candidate_image.size != reference_image.size:
        raise CliError(
            f"Image dimensions differ: reference={reference_image.width}x{reference_image.height}, "
            f"candidate={candidate_image.width}x{candidate_image.height}"
        )
    source_entries: list[dict[str, Any]] = []
    if args.sections_file:
        sections_file = _path(workspace, args.sections_file)
        assert sections_file is not None
        if not sections_file.is_file():
            raise CliError(f"Sections file not found: {sections_file}")
        source_entries = _load_section_entries(sections_file)
    generated_sections_path = (
        _page_artifact_dir(args, workspace) / "sections.json"
        if context is not None
        else output_dir / "sections.json"
    )
    if not args.section and not args.sections_file and generated_sections_path.is_file():
        source_entries = _load_section_entries(generated_sections_path)

    regions = _regions_from_args(
        args,
        workspace,
        reference_image.size,
        section_values=args.section,
    )
    if not regions and source_entries:
        regions = _validate_named_regions(
            [
                (str(entry.get("id", "")).strip(), _section_entry_box(entry))
                for entry in source_entries
            ],
            reference_image.size,
        )
    if not regions:
        raise CliError("Provide at least one --section or --sections-file entry")
    _validate_iteration_focus(args, workspace, [name for name, _ in regions])
    _ensure_iteration_artifact_is_new(
        context, output_dir / "crop.json"
    )
    if args.grid and args.grid_spacing <= 0:
        raise CliError("Grid spacing must be positive")
    if args.grid and args.grid_scale <= 0:
        raise CliError("Grid scale must be positive")

    if candidate is not None and context is not None:
        candidate = _stage_candidate(
            args,
            workspace,
            args.candidate,
            target_dir=output_dir,
            filename="crop-candidate.png",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    needs_sections_update = (
        not source_entries
        or bool(args.section)
        or bool(args.sections_file)
        or not generated_sections_path.is_file()
    )
    sections_file_status = "unchanged"
    if needs_sections_update:
        _, sections_file_status = _update_generated_sections_file(
            generated_sections_path,
            reference=reference,
            viewport=f"{reference_image.width}x{reference_image.height}",
            regions=regions,
            source_entries=source_entries,
            persist=False,
        )
    crop_root = output_dir / "crops"
    sections = []
    artifact_paths: dict[str, str] = {}
    used_slugs: set[str] = set()
    for name, box in regions:
        slug = artifact_slug(name)
        suffix = 2
        while slug in used_slugs:
            slug = f"{artifact_slug(name)}-{suffix}"
            suffix += 1
        used_slugs.add(slug)
        section_dir = crop_root / slug
        reference_crop = crop_image(reference_image, box)
        reference_path = section_dir / "reference.png"
        reference_path.parent.mkdir(parents=True, exist_ok=True)
        reference_crop.save(reference_path)
        files = {"reference": str(reference_path)}
        if args.grid:
            files["reference_grid"] = save_grid_overlay(
                reference_crop,
                section_dir / "reference-grid.png",
                origin=(box[0], box[1]),
                spacing=args.grid_spacing,
                scale=args.grid_scale,
                axis=args.grid_axis,
            )
        if candidate_image is not None:
            candidate_crop = crop_image(candidate_image, box)
            candidate_path = section_dir / "candidate.png"
            candidate_crop.save(candidate_path)
            files["candidate"] = str(candidate_path)
            if args.grid:
                files["candidate_grid"] = save_grid_overlay(
                    candidate_crop,
                    section_dir / "candidate-grid.png",
                    origin=(box[0], box[1]),
                    spacing=args.grid_spacing,
                    scale=args.grid_scale,
                    axis=args.grid_axis,
                )
        sections.append(
            {
                "id": name,
                "bounds": list(box),
                "size": {"width": box[2], "height": box[3]},
                "files": files,
            }
        )
        artifact_paths.update(
            {f"section_{slug}_{kind}": path for kind, path in files.items()}
        )

    if needs_sections_update:
        _update_generated_sections_file(
            generated_sections_path,
            reference=reference,
            viewport=f"{reference_image.width}x{reference_image.height}",
            regions=regions,
            source_entries=source_entries,
        )
    json_path = output_dir / "crop.json"
    markdown_path = output_dir / "crop.md"
    manifest = {
        "status": "ok",
        "operation": "crop",
        "reference": str(reference.resolve()),
        "candidate": str(candidate.resolve()) if candidate is not None else None,
        "viewport": f"{reference_image.width}x{reference_image.height}",
        "coordinate_system": "absolute source-image pixels; crop bounds use x,y,width,height",
        "sections_file": str(generated_sections_path),
        "sections_file_status": sections_file_status,
        "grid": {
            "enabled": args.grid,
            "spacing": args.grid_spacing,
            "scale": args.grid_scale,
            "axis": args.grid_axis,
            "purpose": "Analysis-only coordinate aid; never a runtime UI asset.",
        },
        "sections": sections,
        "artifacts": artifact_paths,
        "runtime": runtime.as_dict(),
        "report": str(json_path),
    }
    write_json(json_path, manifest)
    markdown_path.write_text(crop_markdown(manifest), encoding="utf-8")
    ledger_paths = _record_iteration_event(
        args,
        workspace,
        context,
        "crop",
        {
            "status": "cropped",
            "artifacts": {
                "manifest": json_path,
                "markdown": markdown_path,
                "sections": generated_sections_path,
                **({"candidate": candidate} if candidate is not None else {}),
            },
            "section_ids": [name for name, _ in regions],
            "grid": args.grid,
        },
    )
    _pointer(
        "ok",
        "crop",
        reports={
            "json": _output_file(
                json_path, "Crop bounds, coordinate-grid settings, and generated analysis files."
            ),
            "markdown": _output_file(
                markdown_path, "Concise section crop manifest for focused implementation."
            ),
            "sections": _output_file(
                generated_sections_path,
                "Generated/updated named section bounds reused by later crop and focused compare commands.",
            ),
            **_iteration_reports(ledger_paths),
        },
        artifacts=_output_files(
            artifact_paths,
            _artifact_descriptions(artifact_paths, "Analysis-only section crop artifact"),
        ),
        section_count=len(sections),
        grid=args.grid,
        **_iteration_context_pointer(context),
    )
    return 0


def _parse_section(value: str) -> tuple[str, tuple[int, int, int, int]]:
    from .images import parse_region

    return parse_region(value)


def _cmd_setup(args: argparse.Namespace) -> int:
    workspace = _workspace_root()
    root = _project_root(args)
    runtime = ensure_environment(
        root,
        workspace_root=workspace,
        install=not args.no_auto_setup,
        need_playwright=args.install_browser,
    )
    if args.install_browser and not args.no_auto_setup:
        from .browser import browser_plan

        browser_plan(runtime, explicit="playwright", install=True)
    report_path = _artifact_dir(args, workspace) / "setup.json"
    write_json(
        report_path,
        {
            "status": "ready",
            "operation": "setup",
            "runtime": runtime.as_dict(),
        },
    )
    _pointer(
        "ready",
        "setup",
        reports={
            "json": _output_file(
                report_path, "Runtime, dependency, and browser setup details."
            )
        },
        runtime_dir=str(runtime.directory),
        runtime_python=str(runtime.python),
    )
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    root, workspace, runtime = _prepare_runtime(args)
    from .images import inspect_image, parse_point
    reference = _stage_reference(args, workspace, args.reference)
    image_report = inspect_image(
        reference, points=[parse_point(value) for value in args.point]
    )
    report_path = (
        _page_scoped_path(args, workspace, args.output, option="--output")
        if args.output
        else _artifact_dir(args, workspace) / "inspection.json"
    )
    result: dict[str, Any] = {
        "status": "ok",
        "reference": image_report,
        "runtime": runtime.as_dict(),
        "report": str(report_path),
    }
    if not args.no_project:
        result["project"] = inspect_project(root)
    write_json(report_path, result)
    _pointer(
        "ok",
        "inspect",
        reports={
            "json": _output_file(
                report_path, "Reference image facts and read-only project reconnaissance."
            )
        },
        reference=str(reference),
        viewport=image_report["viewport"],
    )
    return 0


def _reference_viewport(
    workspace: Path,
    reference: str | None,
    viewport: str | None,
    *,
    reference_path: Path | None = None,
) -> str:
    from .images import inspect_image, parse_viewport

    if viewport:
        parse_viewport(viewport)
        return viewport
    if not reference:
        raise CliError("Provide --reference or --viewport")
    reference_path = reference_path or _path(workspace, reference)
    assert reference_path is not None
    info = inspect_image(reference_path)
    return info["viewport"]


def _cmd_render(args: argparse.Namespace) -> int:
    root, workspace, runtime = _prepare_runtime(args, need_browser=True)
    from .rendering import render_target, resolve_target_url
    context = _iteration_guard(args, workspace, "render")
    reference = (
        _stage_reference(args, workspace, args.reference)
        if args.reference
        else None
    )
    viewport = _reference_viewport(
        workspace, args.reference, args.viewport, reference_path=reference
    )
    url = resolve_target_url(root, workspace_root=workspace, url=args.url, entry=args.entry)
    if context is not None and args.output and Path(args.output).name != "candidate.png":
        raise CliError("Focused iteration renders must use the canonical candidate.png filename")
    if context is not None and args.report and Path(args.report).name != "candidate.json":
        raise CliError("Focused iteration render reports must use the canonical candidate.json filename")
    output = (
        _page_scoped_path(args, workspace, args.output, option="--output")
        if args.output
        else _artifact_dir(args, workspace) / "candidate.png"
    )
    _ensure_iteration_artifact_is_new(context, output)
    report_path = (
        _page_scoped_path(args, workspace, args.report, option="--report")
        if args.report
        else _render_report_path(output)
    )
    _ensure_iteration_artifact_is_new(context, report_path)
    result = render_target(
        runtime,
        url=url,
        output=output,
        viewport=viewport,
        browser=args.browser,
        wait_ms=args.wait_ms,
        timeout=args.timeout,
        ready_selector=args.ready_selector,
        install=not args.no_auto_setup,
    )
    result["status"] = "ok"
    result["viewport"] = viewport
    result["runtime"] = runtime.as_dict()
    if reference:
        result["reference"] = str(reference)
    result["report"] = str(report_path)
    write_json(report_path, result)
    ledger_paths = _record_iteration_event(
        args,
        workspace,
        context,
        "render",
        {
            "status": "baseline-rendered" if context and context.number == 0 else "rendered",
            "artifacts": {"candidate": output, "render_report": report_path},
            "viewport": viewport,
        },
    )
    _pointer(
        "ok",
        "render",
        reports={
            "json": _output_file(
                report_path, "Browser capture metadata and runtime diagnostics."
            ),
            **_iteration_reports(ledger_paths),
        },
        artifacts={
            "candidate": _output_file(
                output, "Rendered candidate screenshot used for comparison."
            )
        },
        viewport=viewport,
        **_iteration_context_pointer(context),
    )
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    root, workspace, runtime = _prepare_runtime(args)
    from .images import (
        compare_images,
        inspect_image,
        save_region_visual_artifacts,
        save_visual_artifacts,
    )

    del root, runtime
    context = _iteration_guard(args, workspace, "compare")
    reference = _stage_reference(args, workspace, args.reference)
    output_dir = _artifact_dir(args, workspace)
    _ensure_iteration_artifact_is_new(context, output_dir / "comparison.json")
    _ensure_iteration_artifact_is_new(context, output_dir / "comparison.md")
    assert reference is not None
    reference_info = inspect_image(reference)
    image_size = (reference_info["width"], reference_info["height"])
    regions = _regions_from_args(args, workspace, image_size)
    if context is not None and not args.section_only:
        raise CliError(
            "Focused iterations require --section-only; reserve full-page compare for final verification"
        )
    _validate_iteration_focus(args, workspace, [name for name, _ in regions])
    if args.section_only and not regions:
        raise CliError(
            "--section-only requires --region or --sections-file with at least one section"
        )
    points = _parse_points(args.point, image_size)
    if context is not None:
        candidate_source = _path(workspace, args.candidate)
        assert candidate_source is not None
        if candidate_source.resolve() != (output_dir / "candidate-input.png").resolve():
            _ensure_iteration_artifact_is_new(
                context, output_dir / "candidate-input.png"
            )
    candidate = _stage_candidate(
        args,
        workspace,
        args.candidate,
        target_dir=output_dir if context is not None else None,
    )
    report, reference_image, candidate_image = compare_images(
        reference,
        candidate,
        regions=regions,
        tolerance=args.tolerance,
        tile_size=args.tile_size,
        points=points,
        regions_only=args.section_only,
    )
    if args.diagnostic and args.section_only:
        artifacts = save_region_visual_artifacts(
            reference_image,
            candidate_image,
            regions,
            output_dir,
            tolerance=args.tolerance,
        )
    elif args.diagnostic:
        artifacts = save_visual_artifacts(
            reference_image, candidate_image, output_dir, tolerance=args.tolerance
        )
    else:
        artifacts = {}
    report["visual_artifacts"] = artifacts
    reports = write_comparison_reports(report, output_dir)
    ledger_paths = _record_iteration_event(
        args,
        workspace,
        context,
        "compare",
        {
            "status": "compared",
            "artifacts": {
                "candidate": candidate,
                "comparison_json": reports["json"],
                "comparison_markdown": reports["markdown"],
                **artifacts,
            },
            "comparison_scope": report["comparison_scope"],
            "section_ids": [name for name, _ in regions],
            "diagnostic": args.diagnostic,
        },
    )
    _pointer(
        "ok",
        "compare",
        reports=_output_files(
            reports,
            {
                "json": "Complete machine-readable pixel comparison report.",
                "markdown": "Concise comparison summary; read before comparison.json.",
            },
        )
        | _iteration_reports(ledger_paths),
        artifacts=_output_files(
            artifacts,
            _artifact_descriptions(
                artifacts, "Analysis-only focused section artifact"
            )
            if args.section_only
            else {
                "overlay": "Blended reference and candidate screenshots for alignment diagnosis.",
                "diff": "Enhanced visualization of pixel-level differences.",
                "threshold_mask": "Mask showing pixels outside the configured tolerance.",
            },
        ),
        reference=str(reference.resolve()),
        candidate=_output_file(
            candidate, "Candidate screenshot used for the comparison."
        ),
        viewport=report["viewport"],
        comparison_scope=report["comparison_scope"],
        diagnostic=args.diagnostic,
        **_iteration_context_pointer(context),
    )
    return 0


def _comparison_checks(
    report: dict[str, Any],
    *,
    max_mae: float,
    max_region_mae: float | None,
    min_within: float,
    max_hotspot: float,
) -> list[dict[str, Any]]:
    checks = [
        {
            "name": "dimensions",
            "status": "pass",
            "detail": f"reference and candidate are both {report['viewport']}",
        }
    ]
    region_limit = max_region_mae if max_region_mae is not None else max_mae * 1.5
    if report.get("comparison_scope") == "regions":
        checks.append(
            {
                "name": "focused_scope",
                "status": "pass" if report.get("regions") else "fail",
                "detail": "named sections only; full-page metrics intentionally omitted",
            }
        )
        for region in report.get("regions", []):
            checks.extend(
                [
                    {
                        "name": f"region_{region['name']}_mean_abs_error",
                        "status": "pass"
                        if region["mean_abs_error"] <= region_limit
                        else "fail",
                        "detail": f"{region['mean_abs_error']:.4f} <= {region_limit:.4f}",
                    },
                    {
                        "name": f"region_{region['name']}_within_tolerance",
                        "status": "pass"
                        if region["within_tolerance_fraction"] >= min_within
                        else "fail",
                        "detail": f"{region['within_tolerance_fraction']:.4f} >= {min_within:.4f}",
                    },
                ]
            )
        return checks

    metrics = report["metrics"]
    checks.extend(
        [
            {
                "name": "mean_abs_error",
                "status": "pass" if metrics["mean_abs_error"] <= max_mae else "fail",
                "detail": f"{metrics['mean_abs_error']:.4f} <= {max_mae:.4f}",
            },
            {
                "name": "within_tolerance_fraction",
                "status": "pass"
                if metrics["within_tolerance_fraction"] >= min_within
                else "fail",
                "detail": f"{metrics['within_tolerance_fraction']:.4f} >= {min_within:.4f}",
            },
        ]
    )
    hotspots = metrics.get("tile_hotspots", [])
    hottest = hotspots[0]["mean_error"] if hotspots else 0.0
    checks.append(
        {
            "name": "hottest_tile",
            "status": "pass" if hottest <= max_hotspot else "fail",
            "detail": f"{hottest:.4f} <= {max_hotspot:.4f}",
        }
    )
    for region in report.get("regions", []):
        checks.append(
            {
                "name": f"region_{region['name']}_mean_abs_error",
                "status": "pass"
                if region["mean_abs_error"] <= region_limit
                else "fail",
                "detail": f"{region['mean_abs_error']:.4f} <= {region_limit:.4f}",
            }
        )
    return checks


def _regression_checks(
    current: dict[str, Any], previous_path: Path | None, allowed_increase: float
) -> list[dict[str, Any]]:
    if previous_path is None:
        return []
    if not previous_path.is_file():
        raise CliError(f"Previous report not found: {previous_path}")
    try:
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"Invalid previous comparison report: {previous_path}: {exc}") from exc

    current_scope = current.get("comparison_scope", "full-page")
    previous_scope = previous.get("comparison_scope", "full-page")
    if current_scope != previous_scope:
        raise CliError(
            "Previous comparison scope does not match the current scope; "
            "use a same-scope report for regression checks"
        )

    previous_regions = {
        region.get("name"): region
        for region in previous.get("regions", [])
        if region.get("name")
    }
    checks = []
    if current_scope == "regions":
        for region in current.get("regions", []):
            previous_region = previous_regions.get(region.get("name"))
            if previous_region is None:
                checks.append(
                    {
                        "name": f"has_region_regression_baseline_{region['name']}",
                        "status": "fail",
                        "detail": "previous report has no matching named section",
                    }
                )
                continue
            current_region_mae = float(region["mean_abs_error"])
            previous_region_mae = float(previous_region["mean_abs_error"])
            checks.append(
                {
                    "name": f"no_region_regression_{region['name']}",
                    "status": "pass"
                    if current_region_mae <= previous_region_mae + allowed_increase
                    else "fail",
                    "detail": f"current {current_region_mae:.4f} <= previous {previous_region_mae:.4f} + {allowed_increase:.4f}",
                }
            )
        return checks

    try:
        previous_mae = float(previous["metrics"]["mean_abs_error"])
        current_mae = float(current["metrics"]["mean_abs_error"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CliError(f"Invalid previous comparison report: {previous_path}: {exc}") from exc
    checks.append(
        {
            "name": "no_mae_regression",
            "status": "pass" if current_mae <= previous_mae + allowed_increase else "fail",
            "detail": f"current {current_mae:.4f} <= previous {previous_mae:.4f} + {allowed_increase:.4f}",
        }
    )
    for region in current.get("regions", []):
        previous_region = previous_regions.get(region.get("name"))
        if previous_region is None:
            continue
        current_region_mae = float(region["mean_abs_error"])
        previous_region_mae = float(previous_region["mean_abs_error"])
        checks.append(
            {
                "name": f"no_region_regression_{region['name']}",
                "status": "pass"
                if current_region_mae <= previous_region_mae + allowed_increase
                else "fail",
                "detail": f"current {current_region_mae:.4f} <= previous {previous_region_mae:.4f} + {allowed_increase:.4f}",
            }
        )
    return checks


def _cmd_verify(args: argparse.Namespace) -> int:
    root, workspace, runtime = _prepare_runtime(args, need_browser=args.candidate is None or bool(args.responsive_viewport))
    context = _iteration_guard(args, workspace, "verify")
    if args.final and args.section_only:
        raise CliError("--final verification must be full-page; remove --section-only")
    if context is not None and not args.section_only:
        raise CliError("Focused verification requires --section-only; use --final for the full-page gate")
    if context is not None and not args.candidate:
        raise CliError("Focused verification requires --candidate from the completed render event")
    from .images import (
        compare_images,
        inspect_image,
        parse_viewport,
        save_region_visual_artifacts,
        save_visual_artifacts,
    )
    from .rendering import render_target, resolve_target_url, smoke_check
    reference = _stage_reference(args, workspace, args.reference)
    output_dir = _artifact_dir(args, workspace)
    _ensure_iteration_artifact_is_new(context, output_dir / "comparison.json")
    _ensure_iteration_artifact_is_new(context, output_dir / "comparison.md")
    _ensure_iteration_artifact_is_new(context, output_dir / "verification.json")
    _ensure_iteration_artifact_is_new(context, output_dir / "verification.md")
    assert reference is not None
    output_dir.mkdir(parents=True, exist_ok=True)
    reference_info = inspect_image(reference)
    viewport = args.viewport or reference_info["viewport"]
    parse_viewport(viewport)
    image_size = (reference_info["width"], reference_info["height"])
    regions = _regions_from_args(args, workspace, image_size)
    _validate_iteration_focus(args, workspace, [name for name, _ in regions])
    if args.section_only and not regions:
        raise CliError(
            "--section-only requires --region or --sections-file with at least one section"
        )
    points = _parse_points(args.point, image_size)

    render_result: dict[str, Any] | None = None
    if args.candidate:
        if context is not None:
            _ensure_iteration_artifact_is_new(context, output_dir / "candidate-input.png")
        candidate = _stage_candidate(
            args,
            workspace,
            args.candidate,
            target_dir=output_dir if context is not None else None,
        )
    else:
        url = resolve_target_url(root, workspace_root=workspace, url=args.url, entry=args.entry)
        candidate = output_dir / "candidate.png"
        render_result = render_target(
            runtime,
            url=url,
            output=candidate,
            viewport=viewport,
            browser=args.browser,
            wait_ms=args.wait_ms,
            timeout=args.timeout,
            ready_selector=args.ready_selector,
            install=not args.no_auto_setup,
        )

    report, reference_image, candidate_image = compare_images(
        reference,
        candidate,
        regions=regions,
        tolerance=args.tolerance,
        tile_size=args.tile_size,
        points=points,
        regions_only=args.section_only,
    )
    if args.diagnostic and args.section_only:
        artifacts = save_region_visual_artifacts(
            reference_image,
            candidate_image,
            regions,
            output_dir,
            tolerance=args.tolerance,
        )
    elif args.diagnostic:
        artifacts = save_visual_artifacts(
            reference_image, candidate_image, output_dir, tolerance=args.tolerance
        )
    else:
        artifacts = {}
    report["visual_artifacts"] = artifacts
    comparison_reports = write_comparison_reports(report, output_dir)
    candidate_report: Path | None = None
    if args.final:
        candidate_report = output_dir / "candidate.json"
        write_json(
            candidate_report,
            {
                "status": "ok",
                "operation": "render",
                "output": str(candidate.resolve()),
                "viewport": viewport,
                "provided_candidate": args.candidate is not None,
                "render": render_result,
                "runtime": runtime.as_dict(),
            },
        )
    checks = _comparison_checks(
        report,
        max_mae=args.max_mae,
        max_region_mae=args.max_region_mae,
        min_within=args.min_within_tolerance,
        max_hotspot=args.max_hotspot_mean_error,
    )

    smoke = smoke_check(candidate)
    checks.append(
        {
            "name": "candidate_smoke",
            "status": smoke["status"],
            "detail": f"{smoke['sample_unique_colors']} sampled colors; non_flat={smoke['non_flat']}",
        }
    )
    if render_result is not None:
        browser_errors = render_result.get("console_errors", []) + render_result.get("page_errors", [])
        checks.append(
            {
                "name": "browser_runtime_errors",
                "status": "pass" if not browser_errors else "fail",
                "detail": "none" if not browser_errors else "; ".join(browser_errors[:5]),
            }
        )

    responsive_results = []
    for index, responsive_viewport in enumerate(args.responsive_viewport, start=1):
        parse_viewport(responsive_viewport)
        url = resolve_target_url(root, workspace_root=workspace, url=args.url, entry=args.entry)
        responsive_output = output_dir / f"responsive-{index}.png"
        responsive_render = render_target(
            runtime,
            url=url,
            output=responsive_output,
            viewport=responsive_viewport,
            browser=args.browser,
            wait_ms=args.wait_ms,
            timeout=args.timeout,
            ready_selector=args.ready_selector,
            install=not args.no_auto_setup,
        )
        responsive_smoke = smoke_check(responsive_output)
        responsive_results.append(
            {
                "viewport": responsive_viewport,
                "render": responsive_render,
                "smoke": responsive_smoke,
            }
        )
        responsive_errors = responsive_render.get("console_errors", []) + responsive_render.get("page_errors", [])
        checks.append(
            {
                "name": f"responsive_smoke_{responsive_viewport}",
                "status": "pass"
                if responsive_smoke["status"] == "pass" and not responsive_errors
                else "fail",
                "detail": "rendered and non-flat"
                if responsive_smoke["status"] == "pass" and not responsive_errors
                else "responsive render or runtime errors detected",
            }
        )

    checks.extend(
        _regression_checks(
            report,
            _path(workspace, args.previous_report),
            args.regression_tolerance,
        )
    )

    passed = all(check["status"] == "pass" for check in checks)
    result = {
        "status": "pass" if passed else "fail",
        "viewport": viewport,
        "candidate": str(candidate.resolve()),
        "candidate_report": str(candidate_report) if candidate_report else None,
        "reference": str(reference.resolve()),
        "checks": checks,
        "comparison": report,
        "diagnostic": args.diagnostic,
        "artifacts": artifacts,
        "reports": comparison_reports,
        "smoke": smoke,
        "responsive": responsive_results,
        "render": render_result,
        "warnings": list(runtime.warnings),
        "runtime": runtime.as_dict(),
    }
    verification_json = output_dir / "verification.json"
    verification_md = output_dir / "verification.md"
    result["verification_reports"] = {
        "json": str(verification_json),
        "markdown": str(verification_md),
    }
    write_json(verification_json, result)
    verification_md.write_text(verification_markdown(result), encoding="utf-8")
    ledger_paths = _record_iteration_event(
        args,
        workspace,
        context,
        "verify",
        {
            "status": "verified-pass" if passed else "verified-fail",
            "artifacts": {
                "candidate": candidate,
                "comparison_json": comparison_reports["json"],
                "comparison_markdown": comparison_reports["markdown"],
                "verification_json": verification_json,
                "verification_markdown": verification_md,
                **artifacts,
            },
            "comparison_scope": report["comparison_scope"],
            "section_ids": [name for name, _ in regions],
            "diagnostic": args.diagnostic,
            "failed_checks": [check["name"] for check in checks if check["status"] != "pass"],
        },
    )
    _pointer(
        result["status"],
        "verify",
        reports={
            "comparison_json": _output_file(
                comparison_reports["json"],
                "Complete machine-readable pixel comparison report.",
            ),
            "comparison_markdown": _output_file(
                comparison_reports["markdown"],
                "Concise comparison summary; read before comparison.json.",
            ),
            **(
                {
                    "candidate_json": _output_file(
                        candidate_report,
                        "Final candidate capture/staging metadata.",
                    )
                }
                if candidate_report is not None
                else {}
            ),
            "verification_json": _output_file(
                verification_json,
                "Complete machine-readable verification verdict and diagnostics.",
            ),
            "verification_markdown": _output_file(
                verification_md,
                "Concise verification summary and acceptance checks.",
            ),
            **_iteration_reports(ledger_paths),
        },
        artifacts=_output_files(
            artifacts,
            _artifact_descriptions(
                artifacts, "Analysis-only focused section artifact"
            )
            if args.section_only
            else {
                "overlay": "Blended reference and candidate screenshots for alignment diagnosis.",
                "diff": "Enhanced visualization of pixel-level differences.",
                "threshold_mask": "Mask showing pixels outside the configured tolerance.",
            },
        ),
        reference=str(reference.resolve()),
        candidate=_output_file(
            candidate, "Candidate screenshot used for verification."
        ),
        viewport=viewport,
        comparison_scope=report["comparison_scope"],
        diagnostic=args.diagnostic,
        failed_checks=[check["name"] for check in checks if check["status"] != "pass"],
        **_iteration_context_pointer(context),
        **({"final": True} if args.final else {}),
    )
    return 0 if passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            return _cmd_setup(args)
        if args.command == "inspect":
            return _cmd_inspect(args)
        if args.command == "decompose":
            return _cmd_decompose(args)
        if args.command == "crop":
            return _cmd_crop(args)
        if args.command == "render":
            return _cmd_render(args)
        if args.command == "compare":
            return _cmd_compare(args)
        if args.command == "verify":
            return _cmd_verify(args)
        raise CliError(f"Unknown command: {args.command}")
    except (BootstrapError, BrowserError, CliError, RuntimeError, ValueError) as exc:
        error_report = _persist_error(args, _workspace_root(), exc)
        payload: dict[str, Any] = {
            "status": "error",
            "operation": args.command,
            "error": _error_summary(exc),
        }
        if error_report is not None:
            payload["error_report"] = _output_file(
                error_report, "Full persisted details for this failed command."
            )
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 2
