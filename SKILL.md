---
name: pixel-perfect
version: 1.0.0
description: |
  Reproduce a UI reference image in an existing project with framework-agnostic,
  screenshot-driven iteration. Uses bundled Python scripts for deterministic
  browser rendering, Pillow/NumPy image analysis, section crops, optional
  coordinate-grid overlays, text-readable diagnostics, and bounded visual
  verification. Works for agents with or without vision.
---

# Pixel-perfect

Use this skill when a user asks to implement, reproduce, or closely match a UI from a screenshot or other raster reference. The skill is framework-agnostic: preserve the project's existing framework and architecture, whether the target is static HTML, React, Vue, Svelte, Angular, a desktop web shell, or another browser-rendered project.

The skill captures the useful behavior from a high-fidelity implementation session without copying its accidental weaknesses:

```text
inspect -> decompose -> full-page baseline render -> name active section
       -> optional crop/grid -> implement one section -> render -> compare
       -> optional --diagnostic -> repeat incrementally
       -> full-page verify -> interactions -> cleanup
```

The host agent performs the implementation directly in the current checkout. The bundled CLI performs repeatable mechanics; it never edits source code or invents a replacement architecture. The agent is responsible for deciding the next smallest source edit from the evidence. For complex references, the agent works section-first: capture one full-page baseline, compare named sections incrementally, and materialize crops or other visual diagnostics only when the evidence requires them; reserve full-page comparison for final acceptance.

## Non-negotiable behavior

1. **Inspect before editing.** Read project instructions, identify the project root, inspect the reference dimensions, detect the framework and existing commands, and find the real render entrypoint. Do not guess a missing path.
2. **Use the reference viewport.** Derive the primary viewport from the reference image unless the user explicitly supplies another one. Render at device scale factor 1.
3. **Use measurable evidence.** Every visual iteration must produce a machine-readable comparison report. A model without vision must be able to continue from JSON/Markdown output alone.
4. **Edit one cause at a time.** Do not batch unrelated CSS, markup, or asset changes. A focused edit may update the declarations and markup required for one diagnosed cause, but it must have one stated hypothesis.
5. **Render after every focused edit.** Never infer that an edit worked from source inspection. Compare the new screenshot and check for regressions in already-correct regions.
6. **Preserve behavior and architecture.** Reuse the project's framework, dependencies, build command, routing, and assets when they exist. Do not replace a working application with a static mock merely to improve one screenshot.
7. **Keep the loop bounded.** Use a configured iteration cap and stop after a metric plateau. Report remaining mismatch honestly; never claim pixel identity from an unverified visual impression.
8. **Verify behavior separately.** Visual similarity does not prove that navigation, controls, keyboard access, or application behavior still works.

For a complex reference, do not use a full-page score as the sole implementation gate. Use named regions with `compare --section-only` (or `verify --section-only`) for the active section plus previously accepted sections. Run `crop` only when coordinate-level image evidence is needed, and add `--diagnostic` to materialize visual comparison artifacts. A full-page comparison remains required during final verification.

## Reference-image use rule

This is a hard anti-bypass rule: do not bypass the bundled analysis and comparison scripts by displaying the reference/design screenshot in the implementation. Never use the reference/design screenshot itself as runtime UI content. Do not install it as a CSS `background`/`background-image`, `<img>`/`<picture>`, `mask-image`, canvas or texture, CSS `content`, data URI, imported asset, overlay, or any equivalent screenshot-based shortcut. Do not crop or slice the screenshot into implementation assets, and do not make the rendered project depend on the reference file or artifact path. The reference image is an analysis and comparison input only; recreate the interface from measured evidence, using existing project assets or separately authored individual assets when required. Analysis-only crops and coordinate-grid overlays are allowed under the page artifact directory; they must never become runtime UI content or implementation assets.

## Environment and bundled scripts

All operational scripts live inside this skill. Agents must not create one-off PIL, NumPy, OCR, or pixel-probing scripts during an invocation.

The public entrypoint is:

```bash
python <skill-root>/scripts/pixel-perfect.py <subcommand> ...
```

The CLI automatically creates or repairs the workspace-local runtime when a command needs it. `<cwd>` means the directory from which the CLI is invoked; `--project-root` may point to a checkout inside or outside that workspace:

```text
<cwd>/.artifacts/pixel-perfect/.runtime/
```

The runtime installs Pillow and NumPy. Pillow is required for image I/O; NumPy is used for fast array metrics and is installed automatically. The analysis code has a standard-Python fallback if NumPy installation is unavailable, and the report states which engine ran. No pandas dependency is used. If no system Chrome/Chromium is available, the render path installs and uses Playwright Chromium in the same artifact-root runtime. Installer caches, browser downloads, and process temporary files are redirected below `.artifacts/pixel-perfect/.runtime/`.

Do not install packages globally. Do not add the runtime, generated screenshots, or reports to the implementation commit. **Every file created by the pixel-perfect process must remain below `<cwd>/.artifacts/pixel-perfect/`.** This includes screenshots, reference snapshots, crops, grids, overlays, diffs, JSON/Markdown reports, ledgers, errors, scratch diagnostics, installer caches, browser downloads, and temporary files. Never write process output to `/tmp/`, the project checkout, a home-directory cache, or another workspace. The original reference is a read-only input; implementation source remains project-owned, but the process never writes generated evidence there. If a process copy is needed, the CLI stores it under the artifact root. Pass `--page-name PAGE_NAME` to select the page directory; when omitted, the CLI uses the reference filename stem, or `default` when no reference exists.

### Semantic artifact contract

Do not invent filenames or directories for a visual run. Use this canonical shape:

```text
<cwd>/.artifacts/pixel-perfect/
  .runtime/                         # CLI runtime, browser, cache, and temp files
  <page_name>/
    reference.png                   # immutable snapshot of the input reference
    setup.json                       # optional runtime setup report
    inspection.json
    decomposition.json
    decomposition.md
    sections.json                   # stable section IDs and bounds when sections are used
    iterations.json                  # ordered event ledger
    iterations.md
    iterations/
      000-baseline/
        candidate.png
        candidate.json
      001-<section-id>-<hypothesis-slug>/
        manifest.json                    # current iteration metadata and event trace
        crop.json                        # only when crop is explicitly run
        crop.md                          # only when crop is explicitly run
        crop-candidate.png               # only when crop is explicitly run with a candidate
        crops/<section-id>/...            # only when crop is explicitly run
        candidate.png
        candidate.json
        sections/<section-id>/{reference,candidate,overlay,diff,threshold-mask}.png  # only with --diagnostic
        comparison.json
        comparison.md
        verification.json                     # optional focused verification
        verification.md
        debug/<purpose>/                       # bounded, semantic diagnostics only
        errors/<command>-error-<timestamp>-<pid>.json
    final/
      candidate.png
      candidate.json
      comparison.json
      comparison.md
      verification.json
      verification.md
      overlay.png                              # only with --diagnostic
      diff.png                                 # only with --diagnostic
      threshold-mask.png                       # only with --diagnostic
      responsive-001.png                       # optional smoke captures
      errors/<command>-error-<timestamp>-<pid>.json
    errors/<command>-error-<timestamp>-<pid>.json  # legacy/lifecycle failures
```

Rules:

1. `000-baseline` is the only baseline name. Every focused attempt gets the next zero-padded integer and one stable decomposition section ID: `001-map-panel-boundary`, never `iter-01`, `map26`, `test-foo`, timestamps, or arbitrary root-level names.
2. One iteration represents one hypothesis/source edit. The CLI records `[crop] -> render -> compare` (or focused `verify`) in `iterations.json` and the iteration's `manifest.json`; `crop` is optional and visual diagnostics are opt-in with `--diagnostic`. Do not reuse an iteration after its event is written. A failed hypothesis starts the next number.
3. Keep section IDs stable and sourced from `decomposition.json`/`sections.json`. Never encode iteration numbers, attempts, parameter values, or timestamps into section IDs or crop directory names.
4. Use `--iteration`, `--focus`, `--hypothesis`, and `--iteration-note` for focused commands. The CLI derives the directory and rejects gaps, duplicate events, conflicting metadata, overwrites, and `--output-dir` overrides. Use `--final` for the final full-page verification; do not place final artifacts at the page root.
5. Grids, crops, overlays, diffs, threshold masks, and debug probes are analysis-only. `crop` and `--diagnostic` create them only when explicitly requested. They must stay in the current page/iteration tree and must never be copied into runtime UI assets or source files.
6. Do not create `analysis/`, `test-*`, `iter-*`, `landcomp*`, `map-blend*`, or any other scratch sweep. If a bounded diagnostic is necessary, put it under the current iteration's `debug/<purpose>/` directory with a semantic name and record its purpose in the iteration note.
7. The page root is reserved for the fixed lifecycle files shown above. Do not add arbitrary `.png`, `.json`, `.md`, or timestamp files there. The only process tree outside a page is the shared `.runtime/` under `.artifacts/pixel-perfect/`.
8. Before completion, inspect the artifact tree and confirm every process-created path has the `.artifacts/pixel-perfect/` prefix. A path under `/tmp/` is a contract violation even if it is later copied into the artifact tree.

### Public CLI

| Command | Purpose |
| --- | --- |
| `setup` | Create the local environment and optionally install a browser adapter. |
| `inspect` | Emit reference-image facts and read-only project reconnaissance. |
| `decompose` | Create a structured, text-readable section plan before source edits. |
| `crop` | Create page-scoped analysis crops and optional coordinate-grid overlays; automatically create or append `sections.json`. |
| `render` | Capture one viewport-sized screenshot using system Chrome/Chromium or Playwright. |
| `compare` | Produce full-page or focused-section metrics and mismatch diagnosis; visual artifacts are opt-in with `--diagnostic`. |
| `verify` | Render when necessary, compare, smoke-check, run optional responsive checks, and return an acceptance exit code. |

Common options:

- `--project-root PATH` — target source checkout; defaults to the current directory. It does not control runtime or artifact placement.
- `--page-name PAGE_NAME` — logical page name used by the default artifact directory `.artifacts/pixel-perfect/<page_name>/`; defaults to the reference filename stem, or `default` without a reference.
- `--no-auto-setup` — use an already prepared workspace runtime and fail clearly if it is missing.
- `--reference PATH` — read-only raster input, resolved relative to `<cwd>` unless absolute; the CLI snapshots it as `<page artifact dir>/reference.png` and all reports use that artifact snapshot.
- `--candidate PATH`, `--sections-file PATH`, and `--previous-report PATH` — input/report paths are resolved relative to `<cwd>` unless absolute; an external candidate is copied into the current artifact directory before comparison. `crop` creates or appends the page-scoped `sections.json`, so agents do not need to author it manually.
- `--output`, `--output-dir`, and `--report` — generated paths are normalized under the page artifact directory. With `--iteration`, the CLI derives the current numbered iteration directory and rejects `--output-dir`; with `--final`, it derives `final/`. Do not pass an ad-hoc absolute output path.
- `--iteration N` — select the next zero-based ordered visual iteration; `000` is the baseline and focused numbers must be contiguous.
- `--focus SECTION_ID` — stable section ID from the decomposition targeted by the iteration; never add an attempt/iteration suffix.
- `--hypothesis SLUG` and `--iteration-note TEXT` — semantic source-change label and traceable evidence recorded in `iterations.json` and the current iteration's `manifest.json` for a new focused iteration.
- `--final` — with `verify`, write the final full-page candidate and reports under `final/` rather than the page root.
- `--entry PATH` — local render entrypoint resolved relative to `--project-root` unless absolute.
- `--viewport WIDTHxHEIGHT` — explicit viewport; otherwise `render` and `verify` derive it from the reference.
- `--url URL` — running HTTP(S) URL, `file:` URL, data URL, or existing local file path.
- `--entry PATH` — local entrypoint relative to the project root.
- `--browser PATH|playwright` — select a browser explicitly.
- `--region name=x,y,width,height` — compare a named layout region (repeatable).
- `--section-only` — compare named sections only and omit full-page metrics. Requires `--region`, `--sections-file`, or a generated page-scoped `sections.json`.
- `--diagnostic` — with `compare` or `verify`, materialize visual diagnostics only for the requested section/region; without it, visual diagnostics are not written.
- `--grid` — with `crop`, write optional enlarged coordinate-grid artifacts; use `--grid-axis vertical|horizontal|both` and `--grid-spacing N` as needed.
- `--point x,y` — include an exact pixel probe in the text report (repeatable).

Exit codes are stable: `0` means the command passed, `1` means comparison/verification did not meet its thresholds, and `2` means configuration, environment, browser, or input failure.

### File-first CLI output

The CLI writes detailed JSON/Markdown/image evidence to the page-scoped artifact paths and prints only one compact JSON pointer on stdout. The pointer contains the status, operation, lightweight context, and file descriptors; it does not inline comparison metrics, runtime records, decomposition data, `page_name`, or `output_dir`. Read the returned paths only when deeper evidence is needed. On command failure, stderr contains a short error summary and a descriptor for the persisted error JSON when the artifact directory is writable.

Defaults that make this contract consistent:

- `inspect` writes `.artifacts/pixel-perfect/<page_name>/inspection.json` and snapshots the reference as `reference.png` when `--output` is omitted.
- The compatibility default for `render` is `.artifacts/pixel-perfect/<page_name>/candidate.png`; for an actual visual run, use `--iteration 000` so the baseline is stored at `iterations/000-baseline/candidate.png`.
- `crop`, `render`, `compare`, and focused `verify` with `--iteration` write only inside the derived numbered iteration directory and append `iterations.json`/`iterations.md` in the page root. Focused iteration order is `[crop] -> render -> compare/verify`; omitting `crop` avoids crop PNGs.
- Final `verify --final` writes all final evidence under `.artifacts/pixel-perfect/<page_name>/final/`.
- `decompose` and lifecycle inspection reports stay under `.artifacts/pixel-perfect/<page_name>/`; optional legacy output directories remain page-scoped, but agents must not use them for canonical iteration evidence.

Each returned file uses an object with `output_path` and `description`. The pointer omits `page_name` and `output_dir`; the page scope is visible in every returned file path. A focused comparison reports `comparison_scope: "regions"`, sets global `metrics` to `null`, and records the omission explicitly:

```json
{
  "status": "ok",
  "operation": "compare",
  "comparison_scope": "regions",
  "reports": {
    "markdown": {
      "output_path": "/workspace/.artifacts/pixel-perfect/dashboard/iterations/001-sidebar-sidebar-boundary/comparison.md",
      "description": "Concise comparison summary; read before comparison.json."
    }
  },
  "artifacts": {
    "section_sidebar_diff": {
      "output_path": "/workspace/.artifacts/pixel-perfect/dashboard/iterations/001-sidebar-sidebar-boundary/sections/sidebar/diff.png",
      "description": "Analysis-only diff for the focused sidebar section."
    }
  },
  "candidate": {
    "output_path": "/workspace/.artifacts/pixel-perfect/dashboard/iterations/001-sidebar-sidebar-boundary/candidate.png",
    "description": "Candidate screenshot used for the comparison."
  }
}
```

A focused comparison omits full-page overlay/diff artifacts. With `--diagnostic`, its artifact pointers and `visual_artifacts` map contain per-section crops, overlays, diffs, and threshold masks; without `--diagnostic`, that map is empty.

### Recommended command sequence

From the project root, after resolving a reference image. The reference may be an existing read-only input; every process-created copy/output remains under `.artifacts/pixel-perfect/`:

```bash
python /path/to/pixel-perfect/scripts/pixel-perfect.py inspect \
  --project-root . \
  --page-name dashboard \
  --reference path/to/reference.png

python /path/to/pixel-perfect/scripts/pixel-perfect.py decompose \
  --project-root . \
  --page-name dashboard \
  --reference path/to/reference.png \
  --section header=0,0,1536,44 \
  --section sidebar=0,44,252,934 \
  --section main=252,44,873,934 \
  --section inspector=1125,44,411,934

# Baseline is always iteration 000; do not name it baseline.png at the page root.
python /path/to/pixel-perfect/scripts/pixel-perfect.py render \
  --project-root . \
  --page-name dashboard \
  --reference path/to/reference.png \
  --url http://localhost:3000 \
  --iteration 0

# The CLI creates/updates the stable page-scoped sections.json automatically.
# Start each focused hypothesis with the next number; run crop only when precise
# coordinate evidence is needed.
python /path/to/pixel-perfect/scripts/pixel-perfect.py crop \
  --project-root . \
  --page-name dashboard \
  --reference path/to/reference.png \
  --iteration 1 \
  --focus sidebar \
  --hypothesis sidebar-boundary \
  --iteration-note "Measure and correct the sidebar boundary." \
  --section sidebar=0,44,252,934
# Add --grid only when coordinate precision is needed; it remains analysis-only.

# After one source edit, use the same iteration metadata for its one render.
python /path/to/pixel-perfect/scripts/pixel-perfect.py render \
  --project-root . \
  --page-name dashboard \
  --reference path/to/reference.png \
  --url http://localhost:3000 \
  --iteration 1 \
  --focus sidebar \
  --hypothesis sidebar-boundary \
  --iteration-note "Measure and correct the sidebar boundary."

python /path/to/pixel-perfect/scripts/pixel-perfect.py compare \
  --project-root . \
  --page-name dashboard \
  --reference path/to/reference.png \
  --candidate .artifacts/pixel-perfect/dashboard/iterations/001-sidebar-sidebar-boundary/candidate.png \
  --sections-file .artifacts/pixel-perfect/dashboard/sections.json \
  --iteration 1 \
  --focus sidebar \
  --hypothesis sidebar-boundary \
  --section-only \
  --diagnostic
```

The example requests visual diagnostics explicitly. Omit `--diagnostic` for the lean path; metrics and metadata are still written.

For a static local entrypoint, omit `--url` and pass `--entry index.html` (or let the CLI discover `index.html` under `--project-root`). For a framework application, use the project's normal dev server and pass its URL; do not make the CLI guess a port. A local file supplied through `--url` is resolved relative to `<cwd>` unless absolute.

For final acceptance, use `--final` so the full-page gate is isolated from focused iterations:

```bash
python /path/to/pixel-perfect/scripts/pixel-perfect.py verify \
  --project-root . \
  --page-name dashboard \
  --reference path/to/reference.png \
  --url http://localhost:3000 \
  --final \
  --responsive-viewport 390x844 \
  --max-mae 10 \
  --min-within-tolerance 0.85
```

The command writes its candidate, comparison, verification, and responsive smoke screenshots below `final/`. Add `--diagnostic` when full-page overlay/diff/mask evidence is needed. A focused iteration writes `crop.json`, `crop.md`, exact reference/candidate crops, optional grid overlays, and section diagnostics only when `crop` or `--diagnostic` is explicitly requested; its comparison/verification reports and iteration metadata are always written. For comparison output, read `comparison.md` first because it is the concise diagnostic summary; open the usually much larger `comparison.json` only when exact metrics, probes, regions, scanlines, or tile details are needed. For verification output, read `verification.md` first and open `verification.json` only for deeper evidence. Do not rely on the generated image being visible to the model.

### Rich section definitions

Use `decompose --sections-file sections.json` when a section needs more than a bounding box. For ordinary crop work, pass repeated `--section id=x,y,width,height` values instead; `crop` automatically creates or appends the page-scoped `sections.json`. The file may be a JSON array or an object with a `sections` array:

```json
{
  "sections": [
    {
      "id": "sidebar",
      "bounds": [0, 44, 252, 934],
      "visual_contract": ["Dark navigation panel with compact project and session groups"],
      "content_state": "default",
      "layout_owner": "src/layout/Sidebar.tsx",
      "dependencies": ["global-frame"],
      "implementation_order": 2,
      "verification_region": [0, 44, 252, 934],
      "responsive_behavior": "Collapse below 768px.",
      "acceptance_criteria": ["Navigation remains keyboard reachable."]
    }
  ]
}
```

A section supplied only through `--section id=x,y,width,height` is deliberately marked `draft` because its semantic contract is still missing. `crop` still records that bound in the generated `sections.json` so later focused commands can reuse it; complete the richer decomposition contract before treating the section as implementation-ready.

## Workflow

### 1. Resolve scope and project state

- Treat the user-provided reference as the visual contract and preserve its established terminology.
- Read the repository's instruction files and any project-local README or architecture guidance.
- Resolve the target checkout from the project's documented root mechanism when one exists. If the root, target file, route, or reference is ambiguous in a way that changes implementation, ask one focused question before editing.
- If the target directory is absent, do not create it silently. Ask for permission; after permission, create the smallest valid project foundation and record that decision.
- Check existing working-tree changes. Never overwrite unrelated user work; distinguish baseline changes from this task's changes.

### 2. Inspect the environment and reference

Run `inspect` before implementation. It reports:

- exact image width, height, mode, and derived primary viewport;
- corner colors and dominant quantized colors;
- strongest horizontal and vertical edge positions;
- the available analysis engine;
- project manifests, likely framework, package manager, scripts, candidate entrypoints, and a bounded file list.

Also inspect available renderers. The CLI prefers an installed Chrome/Chromium executable because it is close to the browser screenshot workflow used by the source session. It uses Playwright Chromium only when required or explicitly selected. If an optional tool is missing, use the CLI's setup path rather than writing an ad-hoc replacement.

### 3. Decompose the visual contract

Before writing the first implementation, run `decompose` and create a section plan. If no section definitions are available yet, accept its `draft` output as a scaffold and complete it before editing source. Do not treat automatically suggested edge positions as semantic labels.

Every named section must have this contract:

```text
id
bounds: x, y, width, height
visual_contract
content_state
layout_owner
dependencies
implementation_order
verification_region
responsive_behavior
acceptance_criteria
```

Use sections that match implementation seams and meaningful visual regions. A practical dependency order is:

1. global frame and persistent chrome;
2. primary columns and panel boundaries;
3. internal cards, toolbars, lists, and forms;
4. repeated rows, icons, badges, and controls;
5. typography, assets, colors, borders, and effects;
6. active/loading/empty/error/focus states and interactions;
7. responsive mapping and smoke checks.

Before the baseline, make the plan `ready`: each section has an owner, visible state, verification region, responsive behavior, and acceptance criteria. Dependencies may be empty for a root section but must be explicit. Keep sections small enough that one focused edit can target one section or one dependency edge; do not split the page into arbitrary score-optimizing slices.

### Sectioning cases

Use these rules when deciding whether something is a new section, a state, or a child element:

- **Persistent frame:** header, footer, sidebar shell, global background, and overflow belong to `global-frame` or `primary-regions`.
- **Spatial region:** a visually bounded column, panel, card group, or large empty area gets its own section when it has an independent layout owner or verification region.
- **Repeated structure:** rows, cards, nav items, icons, and badges are children of their owning section; describe the repeated pattern once and list important variants in `content_state`.
- **Visible state:** active, selected, loading, empty, error, focused, expanded, and disabled states are state contracts, not arbitrary extra rectangles. Add a separate section only when the state changes a separate implementation seam or overlay.
- **Overlay/portal:** dialogs, menus, tooltips, drawers, and floating controls get an explicit section because they can affect z-index, viewport bounds, and interaction verification.
- **Dynamic content:** freeze the data/state needed for the reference and record the fixture or route in `content_state`; do not tune layout against a moving response.
- **Scrollable content:** record the scroll position and viewport clipping in the section contract. Do not compare an unscrolled page with a scrolled reference.
- **Responsive behavior:** keep the semantic section identity across breakpoints and describe reflow, collapse, hide/show, or overflow in `responsive_behavior`.
- **Typography/assets:** keep them under the owning visual section unless a shared font/icon asset affects the entire frame; then make the dependency explicit.

The section plan becomes the text-only implementation map. For each section, record:

- its bounds, alignment anchors, and relationship to the global viewport;
- its hierarchy: panels, cards, rows, controls, and repeated structures;
- typography hierarchy, line-height, text density, and likely font sources;
- colors, gradients, borders, radii, shadows, icons, and assets;
- visible content and state: default, active, loading, empty, error, or focused;
- interaction rules, source owner, dependencies, and responsive exceptions;
- the exact verification region and acceptance evidence that will mark it accepted.

Prefer the section plan's `verification_region` values for comparison. Regions should correspond to meaningful layout sections, not arbitrary slices chosen only to make the score look better. Keep a short hypothesis log in the task progress tracker, for example: `main panel begins 4px too low because the toolbar line box is taller than the reference`.

### Section-first execution for complex references

Use the section-first path when `decomposition.json` marks the reference `complex`, when `workflow_policy.requires_named_sections` is `true`, or when the page is visually dense enough that a whole-page comparison cannot isolate the cause. A short but dense reference can opt in with `decompose --section-first`. Simple references may use the regular full-page path, but meaningful independent regions should still be named when they improve diagnosis.

Follow this order exactly; the crop step is optional:

1. Decompose the reference into meaningful implementation sections.
2. Create one complete full-page baseline render at the target viewport as iteration `000`. This baseline is orientation evidence, not the focused acceptance gate.
3. Start the next sequential iteration with `--iteration N --focus SECTION_ID --hypothesis SLUG --iteration-note TEXT`. Run `crop` only when coordinate-level evidence is needed; when used, it creates or appends the page-scoped `sections.json` automatically. Otherwise render first. Do not hand-author that file for ordinary crop work.
4. Add a grid overlay only when coordinate precision is needed. Use `--grid`, `--grid-spacing`, and `--grid-axis vertical|horizontal|both`; the overlay labels absolute source-image coordinates and is analysis-only.
5. Implement one section or one diagnosed cause.
6. Run `render` with the same iteration metadata; the CLI writes `candidate.png` inside that numbered directory.
7. Run `compare --section-only` (or focused `verify`) with the active section and previously accepted sections. Add `--diagnostic` only when visual artifacts are needed. The CLI appends the event and all paths to `iterations.json`.
8. Never rerun or overwrite a completed event. If the hypothesis fails or another edit is needed, increment `N` and create a new semantic directory. Include previously accepted sections in the focused comparison to catch regressions.
9. After all sections pass, run `verify --final` for the full-page gate, exercise interactions, inspect the artifact tree for path violations, remove temporary analysis artifacts from the source change, and commit the implementation.

Focused comparison must not be replaced by a whole-page score for a complex reference. The full-page baseline remains available for orientation, and full-page metrics are restored for the final verification gate.

### 4. Establish a coarse but complete baseline

Implement sections in the plan's dependency order. Finish a coarse vertical slice of the global frame and primary regions before polishing internal components. Mark a section accepted only after its focused comparison passes and the previously accepted sections show no unacceptable regression.

Implement the smallest complete vertical slice that can render the whole target state. Use existing project conventions and assets. Do not spend the first pass on one icon while the page frame is absent.

- Preserve semantic structure and existing behavior where possible.
- Use real project fonts/assets when available; do not silently substitute a dependency that changes the project architecture.
- Use CSS layout primitives appropriate to the project. Absolute positioning is acceptable for a fixed mockup only when the contract is explicitly fixed; it is not a default for framework applications.
- Keep the first pass observable: it must load at the target route and produce a screenshot.

Render the baseline immediately and preserve its screenshot for orientation. For a complex reference, do not run a full-page comparison as the implementation gate at this stage; later iterations use active and accepted named regions, with crops materialized only when needed.

### 5. Iterate with a diagnosis-first loop

For each focused iteration, follow this exact order:

1. Choose the next contiguous number and record `--focus`, `--hypothesis`, and `--iteration-note`; the CLI creates the semantic directory and ledger entry on the first event.
2. If coordinate-level evidence is needed, run `crop` (and optional `--grid`) for the active section before the source edit.
3. Make one focused source edit.
4. Render with the same iteration metadata at the primary viewport.
5. Run `compare --section-only` with the active section's `verification_region`, all previously accepted section regions, and the configured pixel tolerance. This calculates per-section metrics without writing visual artifacts unless `--diagnostic` is supplied. A focused `verify` may replace `compare` when its acceptance checks are needed.
6. Read the text report and identify the highest-impact *single* mismatch class:
   - dimension/viewport mismatch;
   - global frame or panel boundary;
   - component position or size;
   - spacing or line-box alignment;
   - typography/font weight/line height;
   - color, border, gradient, or shadow;
   - content or state;
   - interaction/runtime error.
7. Use the focused report's mismatch bounding box, row/column density, tile hotspots, edge peaks, per-channel error, and region metrics to localize the cause. Run `crop`, add `--diagnostic`, or both when image-level coordinate evidence is needed. Do not make a CSS change without a stated cause.
8. Keep the edit only if the active section improves without an unacceptable regression in previously accepted sections; otherwise create the next iteration for the corrected hypothesis. Never rerender in-place after an event has been written.
9. Update the section status and append the result to the progress log: change, evidence before/after, and next hypothesis.

The comparison tools expose global and local evidence, but their scope is deliberate: a complex-page iteration must use named-region evidence, not a global score. A lower global error can hide a broken header or text block, so inspect the active and accepted-region results before accepting an iteration; request image artifacts only when the text evidence is insufficient.

Useful diagnosis patterns:

- A continuous vertical edge mismatch usually indicates a column boundary, width, or scrollbar issue.
- A continuous horizontal edge mismatch usually indicates a row height, margin, or line-box issue.
- A narrow mismatch around glyphs with otherwise correct boxes usually indicates font, weight, anti-aliasing, or text color.
- A broad low-amplitude mismatch across a panel usually indicates background, gradient, opacity, or color-scheme error.
- A compact high-error tile usually indicates one component, icon, or content-state mismatch.

If a comparison script fails, fix the cause instead of hiding the error. The CLI already handles missing NumPy, missing browser adapters, unreadable images, and dimension mismatches with explicit diagnostics.

### 6. Use bounded stopping rules

Set a task-appropriate `MAX_ITERATIONS`; use 20 when the user has not supplied a limit. Stop refinement when all of the following are true:

- reference and candidate dimensions match exactly;
- the final full-page comparison meets configured `max-mae`, `min-within-tolerance`, and hottest-tile thresholds;
- during section-first work, the active and previously accepted named regions meet their configured region thresholds;
- named important regions meet their own visual expectations;
- no large unresolved mismatch cluster remains in a high-priority region;
- the last three accepted iterations do not show a meaningful improvement, or the acceptance thresholds have passed;
- any responsive smoke viewport renders a non-flat page without captured browser/page errors;
- required project verification and interaction checks pass.

If the iteration cap or plateau is reached first, stop and report the remaining region mismatch bbox, hottest regions, metrics, and likely next hypothesis. Never loop indefinitely and never describe a threshold pass as exact pixel identity unless `exact_fraction` is actually 1.0 under the chosen renderer. For focused comparisons, state that global metrics were intentionally omitted rather than treating the missing value as a pass.

### 7. Verify behavior and finalize

After visual acceptance:

1. Run `verify --final` without `--section-only` and preserve its JSON/Markdown reports. Add `--diagnostic` only when final overlay/diff/mask evidence is needed. A section-first plan is not complete until this final whole-page gate passes.
2. Run the project's normal build, lint, typecheck, and test commands when available.
3. Exercise visible interactions that the reference implies: tabs, navigation, search/filtering, toggles, buttons, and keyboard focus as applicable. Use the highest practical seam; do not replace behavior tests with screenshot equality.
4. Check responsive smoke sizes when the project is expected to be responsive.
5. Inspect the final diff. Remove debugging probes, temporary source markup, accidental dependencies, and generated files from the implementation change.
6. Keep `<cwd>/.artifacts/pixel-perfect/` out of the source commit unless the user explicitly requests it; this is the sole storage root for the pixel-perfect runtime and generated process artifacts.
7. Report exactly what passed, what remains, the renderer and analysis engine used, the final thresholds, and any environment limitations.

## Framework guidance

The CLI does not assume a framework. The agent should:

- identify the framework and existing package scripts from `inspect`;
- use the project's normal start/build command and an explicit URL for browser rendering;
- preserve routing and state rather than replacing the app with a screenshot-shaped shell;
- keep visual changes localized to the owning module/style layer;
- use project tests and browser interaction checks in addition to image metrics.

For a static HTML target, a self-contained file may be the simplest correct implementation. For a framework target, reuse installed dependencies and existing component/style conventions before adding anything. The reference image is evidence about appearance, not permission to remove application behavior.

## No-vision contract

A model without image vision must still be able to complete the workflow. It must read:

1. `inspection.json` for reference dimensions, colors, and edge peaks;
2. `decomposition.json` or `decomposition.md` for the section map, order, owners, states, dependencies, complexity policy, and acceptance criteria;
3. generated `sections.json` for active/accepted bounds; use `crop.md`/`crop.json` and grid artifacts only when `crop` was explicitly run;
4. `comparison.md` first for the concise full-page or focused regional summary; visual artifacts are present only when `--diagnostic` was used;
5. mismatch bbox, absolute region bbox, top rows/columns, scanlines, point probes, and tile hotspots from the relevant report for localization;
6. the current source and browser/project inspection output for mapping coordinates to selectors or modules;
7. the progress log for the previous hypothesis and regression result;
8. `iterations.md`/`iterations.json` for the ordered section, hypothesis, event sequence, status, and artifact paths of every visual iteration.

The model must not claim to have visually inspected an image it cannot see. If text-only evidence cannot distinguish two plausible causes, state the uncertainty and use an additional targeted probe or ask the user for the missing visual decision.

## Failure handling

- Missing or malformed project root: stop and ask; do not guess.
- Missing reference or candidate image: stop with the CLI error and correct the path.
- Missing or conflicting generated `sections.json` entry: correct the section ID/bounds through the `crop --section` command; do not silently overwrite an existing section with different bounds.
- Missing, malformed, gapped, or conflicting `iterations.json`: stop and repair the ledger under the page artifact directory before producing another visual artifact; never bypass it with a new root filename.
- Artifact path outside `.artifacts/pixel-perfect/`, including `/tmp/`, project source, or a home cache: treat it as a process failure and move the output through the CLI's page-scoped path contract before continuing.
- Dimension mismatch: treat it as a structural failure, not as a reason to resize silently.
- Browser failure: inspect the command and environment; use the supported adapter or fix the project server. Do not add arbitrary sleeps or retries.
- NumPy installation failure: retain the explicit Python fallback only when the report records it; do not pretend the fast analysis ran.
- Verification failure: identify the smallest root cause, make up to three focused self-fixes, rerun the relevant check, and ask the user how to proceed if it still fails.
- User feedback such as “refine incrementally” changes the iteration contract immediately: stop batching and continue with one hypothesis/edit/render cycle.
