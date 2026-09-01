"""Canonical, ordered visual-iteration artifact paths and ledger events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class IterationError(ValueError):
    """Raised when an iteration cannot satisfy the ordered artifact contract."""


@dataclass(frozen=True)
class IterationContext:
    number: int
    focus: str | None
    hypothesis: str | None
    note: str | None
    directory: Path
    ledger_path: Path


def semantic_slug(value: str, *, field: str) -> str:
    """Normalize a human label for a stable, readable artifact directory name."""

    raw = str(value).strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if not slug:
        raise IterationError(f"{field} must contain letters or numbers")
    return slug[:64].rstrip("-")


def _ledger_path(page_dir: Path) -> Path:
    return Path(page_dir).resolve() / "iterations.json"


def _load_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "iterations": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IterationError(f"Could not read iteration ledger {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("iterations"), list):
        raise IterationError(
            f"Iteration ledger must be an object with an 'iterations' array: {path}"
        )
    if data.get("schema_version") != SCHEMA_VERSION:
        raise IterationError(
            f"Unsupported iteration ledger schema in {path}: {data.get('schema_version')}"
        )
    return data


def _entries_by_number(ledger: dict[str, Any]) -> dict[int, dict[str, Any]]:
    entries: dict[int, dict[str, Any]] = {}
    for entry in ledger["iterations"]:
        if not isinstance(entry, dict):
            raise IterationError("Every iteration ledger entry must be an object")
        try:
            number = int(entry["number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise IterationError("Every iteration ledger entry needs an integer number") from exc
        if number in entries:
            raise IterationError(f"Duplicate iteration number in ledger: {number}")
        entries[number] = entry
    if sorted(entries) != list(range(len(entries))):
        raise IterationError("Iteration ledger numbers must start at 000 and have no gaps")
    return entries


def resolve_iteration(
    page_dir: Path,
    number: int,
    *,
    focus: str | None = None,
    hypothesis: str | None = None,
    note: str | None = None,
) -> IterationContext:
    """Resolve a new or existing canonical iteration without creating files."""

    page_dir = Path(page_dir).resolve()
    try:
        number = int(number)
    except (TypeError, ValueError) as exc:
        raise IterationError("Iteration number must be an integer") from exc
    if number < 0:
        raise IterationError("Iteration number must be non-negative")

    ledger_path = _ledger_path(page_dir)
    ledger = _load_ledger(ledger_path)
    entries = _entries_by_number(ledger)
    existing = entries.get(number)
    if existing is not None:
        if existing.get("number") != number:
            raise IterationError(f"Iteration {number:03d} has invalid number metadata")
        expected_focus = existing.get("focus")
        expected_hypothesis = existing.get("hypothesis")
        expected_note = existing.get("note")
        events = existing.get("events")
        if not isinstance(events, list):
            raise IterationError(f"Iteration {number:03d} has no valid events list")
        if number == 0:
            if expected_focus is not None or expected_hypothesis is not None or expected_note is not None:
                raise IterationError("Iteration 000 has unexpected focused metadata")
            expected_directory = "iterations/000-baseline"
        else:
            if not isinstance(expected_focus, str) or not expected_focus:
                raise IterationError(f"Iteration {number:03d} is missing focus metadata")
            if not isinstance(expected_hypothesis, str) or not expected_hypothesis:
                raise IterationError(f"Iteration {number:03d} is missing hypothesis metadata")
            if not isinstance(expected_note, str) or not expected_note.strip():
                raise IterationError(f"Iteration {number:03d} is missing iteration-note metadata")
            if semantic_slug(expected_hypothesis, field="hypothesis") != expected_hypothesis:
                raise IterationError(f"Iteration {number:03d} has an invalid hypothesis slug")
            if re.search(r"\d+$", expected_focus):
                raise IterationError(
                    f"Iteration {number:03d} uses a numeric-suffixed focus ID {expected_focus!r}; "
                    "repair sections.json and start with a stable ID"
                )
            expected_directory = (
                f"iterations/{number:03d}-"
                f"{semantic_slug(expected_focus, field='focus')}-{expected_hypothesis}"
            )
        if existing.get("directory") != expected_directory:
            raise IterationError(
                f"Iteration {number:03d} has a non-canonical directory metadata value"
            )
        if focus is not None and focus != expected_focus:
            raise IterationError(
                f"Iteration {number:03d} already belongs to focus {expected_focus!r}"
            )
        if hypothesis is not None:
            normalized = semantic_slug(hypothesis, field="hypothesis")
            if normalized != expected_hypothesis:
                raise IterationError(
                    f"Iteration {number:03d} already uses hypothesis {expected_hypothesis!r}"
                )
        if note is not None and note.strip() != expected_note:
            raise IterationError(f"Iteration {number:03d} already has a different note")
        directory_value = existing.get("directory")
        if not isinstance(directory_value, str) or not directory_value:
            raise IterationError(f"Iteration {number:03d} has no valid directory in the ledger")
        return IterationContext(
            number=number,
            focus=expected_focus,
            hypothesis=expected_hypothesis,
            note=expected_note,
            directory=(page_dir / directory_value).resolve(),
            ledger_path=ledger_path,
        )

    expected_number = max(entries, default=-1) + 1
    if number != expected_number:
        raise IterationError(
            f"Iterations must be sequential; expected {expected_number:03d}, received {number:03d}"
        )

    if number == 0:
        if focus or hypothesis or note:
            raise IterationError("Iteration 000 is reserved for the full-page baseline render")
        directory_name = "000-baseline"
        resolved_focus = resolved_hypothesis = resolved_note = None
    else:
        if not focus:
            raise IterationError("Focused iterations require --focus SECTION_ID")
        if not hypothesis:
            raise IterationError("Focused iterations require --hypothesis SLUG")
        if not note or not note.strip():
            raise IterationError("New focused iterations require --iteration-note")
        resolved_focus = str(focus).strip()
        if re.search(r"\d+$", resolved_focus):
            raise IterationError(
                "--focus must be a stable decomposition section ID, not an iteration-suffixed ID"
            )
        resolved_hypothesis = semantic_slug(hypothesis, field="hypothesis")
        resolved_note = note.strip()
        focus_slug = semantic_slug(resolved_focus, field="focus")
        directory_name = f"{number:03d}-{focus_slug}-{resolved_hypothesis}"

    directory = page_dir / "iterations" / directory_name
    if directory.exists():
        raise IterationError(
            f"Iteration directory already exists without a matching ledger entry: {directory}"
        )
    return IterationContext(
        number=number,
        focus=resolved_focus,
        hypothesis=resolved_hypothesis,
        note=resolved_note,
        directory=directory,
        ledger_path=ledger_path,
    )


def ensure_next_event(page_dir: Path, context: IterationContext, operation: str) -> None:
    """Reject duplicate or out-of-order operation events before writing artifacts."""

    del page_dir
    ledger = _load_ledger(context.ledger_path)
    entries = _entries_by_number(ledger)
    entry = entries.get(context.number)
    events = entry.get("events", []) if entry else []
    if not isinstance(events, list):
        raise IterationError(f"Iteration {context.number:03d} has an invalid events list")
    operations = [event.get("operation") for event in events if isinstance(event, dict)]
    if operation in operations:
        raise IterationError(
            f"Iteration {context.number:03d} already contains a {operation} event; start a new iteration"
        )
    if context.number == 0:
        if operation != "render" or operations:
            raise IterationError("Iteration 000 accepts exactly one render event")
        return
    expected_next = {
        (): ("crop", "render"),
        ("crop",): ("render",),
        ("render",): ("compare", "verify"),
        ("crop", "render"): ("compare", "verify"),
    }
    allowed = expected_next.get(tuple(operations))
    if allowed and operation in allowed:
        return
    if allowed:
        expected_text = " or ".join(allowed)
        raise IterationError(
            f"Iteration {context.number:03d} expects {expected_text} next, received {operation}"
        )
    raise IterationError(
        f"Iteration {context.number:03d} is complete; start a new iteration for another edit"
    )


def _relative(value: Any, page_dir: Path) -> Any:
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        path = Path(value)
        if path.is_absolute():
            try:
                return str(path.resolve().relative_to(page_dir.resolve()))
            except ValueError as exc:
                raise IterationError(
                    f"Iteration artifacts must stay under the page artifact directory: {path}"
                ) from exc
        return value
    if isinstance(value, dict):
        return {key: _relative(item, page_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [_relative(item, page_dir) for item in value]
    return value


def _markdown(ledger: dict[str, Any]) -> str:
    lines = [
        "# Pixel-perfect iteration ledger",
        "",
        "Each numbered entry is one focused hypothesis/edit. Events are recorded in execution order; start a new iteration instead of overwriting an event artifact.",
        "",
        "| # | Focus | Hypothesis | Status | Events | Directory |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for entry in ledger.get("iterations", []):
        events = entry.get("events", [])
        operations = ", ".join(
            str(event.get("operation"))
            for event in events
            if isinstance(event, dict)
        )
        lines.append(
            f"| {int(entry.get('number', 0)):03d} | `{entry.get('focus') or 'baseline'}` | "
            f"`{entry.get('hypothesis') or 'baseline'}` | **{entry.get('status', 'unknown')}** | "
            f"`{operations or 'none'}` | `{entry.get('directory')}` |"
        )
        if entry.get("note"):
            lines.append(f"  - Note: {entry['note']}")
        for event in events:
            if not isinstance(event, dict):
                continue
            lines.append(
                f"  - `{event.get('operation')}`: {event.get('status', 'ok')}"
            )
            artifacts = event.get("artifacts", {})
            if isinstance(artifacts, dict):
                for name, path in artifacts.items():
                    lines.append(f"    - `{name}`: `{path}`")
    return "\n".join(lines) + "\n"


def append_iteration_event(
    page_dir: Path,
    context: IterationContext,
    operation: str,
    event: dict[str, Any],
) -> dict[str, str]:
    """Append one event and persist the JSON/Markdown ledger."""

    page_dir = Path(page_dir).resolve()
    ensure_next_event(page_dir, context, operation)
    ledger = _load_ledger(context.ledger_path)
    entries = _entries_by_number(ledger)
    entry = entries.get(context.number)
    if entry is None:
        entry = {
            "number": context.number,
            "focus": context.focus,
            "hypothesis": context.hypothesis,
            "note": context.note,
            "directory": str(context.directory.relative_to(page_dir)),
            "events": [],
        }
        ledger["iterations"].append(entry)
    recorded_event = dict(event)
    recorded_event["operation"] = operation
    recorded_event = _relative(recorded_event, page_dir)
    entry.setdefault("events", []).append(recorded_event)
    entry["status"] = event.get("status", operation)
    entry["last_operation"] = operation
    ledger["iterations"].sort(key=lambda item: int(item["number"]))
    context.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    context.ledger_path.write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    markdown_path = context.ledger_path.with_suffix(".md")
    markdown_path.write_text(_markdown(ledger), encoding="utf-8")
    context.directory.mkdir(parents=True, exist_ok=True)
    manifest_path = context.directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "iteration": entry,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "json": str(context.ledger_path),
        "markdown": str(markdown_path),
        "manifest": str(manifest_path),
    }
