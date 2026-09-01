from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import venv
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = SKILL_ROOT / "scripts"
FIXTURE = SKILL_ROOT / "tests" / "fixtures" / "screen.png"
COMPLEX_FIXTURE = SKILL_ROOT / "tests" / "fixtures" / "complex-screen.png"
sys.path.insert(0, str(SCRIPT_ROOT))

from pixel_perfect.bootstrap import process_environment, runtime_directory  # noqa: E402
from pixel_perfect.cli import (  # noqa: E402
    CliError,
    _page_name,
    _page_scoped_path,
    main as cli_main,
)
from pixel_perfect.decomposition import build_decomposition  # noqa: E402
from pixel_perfect.images import (  # noqa: E402
    ImageAnalysisError,
    compare_images,
    crop_image,
    inspect_image,
    parse_point,
    parse_region,
    parse_viewport,
    save_grid_overlay,
    save_visual_artifacts,
)
from pixel_perfect.iterations import (  # noqa: E402
    IterationError,
    append_iteration_event,
    resolve_iteration,
)
from pixel_perfect.project import inspect_project  # noqa: E402
from pixel_perfect.rendering import resolve_target_url, smoke_check  # noqa: E402


class IterationArtifactTests(unittest.TestCase):
    def test_ordered_semantic_iteration_ledger_rejects_gaps_and_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            page_dir = Path(directory) / "dashboard"
            baseline = resolve_iteration(page_dir, 0)
            baseline_candidate = baseline.directory / "candidate.png"
            baseline_candidate.parent.mkdir(parents=True)
            baseline_candidate.write_bytes(b"baseline")
            append_iteration_event(
                page_dir,
                baseline,
                "render",
                {"status": "baseline-rendered", "artifacts": {"candidate": baseline_candidate}},
            )

            with self.assertRaises(IterationError):
                resolve_iteration(
                    page_dir,
                    2,
                    focus="map",
                    hypothesis="panel-boundary",
                    note="Move the map panel boundary.",
                )

            with self.assertRaises(IterationError):
                resolve_iteration(
                    page_dir,
                    1,
                    focus="map26",
                    hypothesis="panel-boundary",
                    note="Move the map panel boundary.",
                )
            iteration = resolve_iteration(
                page_dir,
                1,
                focus="map",
                hypothesis="Map panel boundary",
                note="Move the map panel boundary.",
            )
            self.assertEqual(
                iteration.directory,
                (page_dir / "iterations/001-map-map-panel-boundary").resolve(),
            )
            append_iteration_event(
                page_dir,
                iteration,
                "render",
                {"status": "rendered", "artifacts": {"candidate": iteration.directory / "candidate.png"}},
            )
            with self.assertRaises(IterationError):
                append_iteration_event(page_dir, iteration, "crop", {})
            ledger_paths = append_iteration_event(
                page_dir,
                iteration,
                "compare",
                {
                    "status": "compared",
                    "artifacts": {"report": iteration.directory / "comparison.json"},
                },
            )
            self.assertTrue(Path(ledger_paths["json"]).is_file())
            self.assertTrue(Path(ledger_paths["markdown"]).is_file())
            self.assertTrue(Path(ledger_paths["manifest"]).is_file())
            manifest = json.loads(Path(ledger_paths["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["iteration"]["number"], 1)
            ledger = json.loads(Path(ledger_paths["json"]).read_text(encoding="utf-8"))
            self.assertEqual(
                [event["operation"] for event in ledger["iterations"][1]["events"]],
                ["render", "compare"],
            )
            self.assertEqual(ledger["iterations"][1]["focus"], "map")
            self.assertEqual(ledger["iterations"][1]["hypothesis"], "map-panel-boundary")
            with self.assertRaises(IterationError):
                append_iteration_event(page_dir, iteration, "compare", {})

    def test_iteration_ledger_rejects_malformed_or_missing_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            page_dir = Path(directory) / "dashboard"
            ledger_path = page_dir / "iterations.json"
            ledger_path.parent.mkdir(parents=True)
            ledger_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(IterationError):
                resolve_iteration(page_dir, 0)

            ledger_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "iterations": [{"number": 0, "events": []}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(IterationError):
                resolve_iteration(page_dir, 0)

    def test_iteration_ledger_rejects_artifacts_outside_page_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            page_dir = Path(directory) / "dashboard"
            baseline = resolve_iteration(page_dir, 0)
            append_iteration_event(page_dir, baseline, "render", {"artifacts": {}})
            iteration = resolve_iteration(
                page_dir,
                1,
                focus="sidebar",
                hypothesis="panel-size",
                note="Adjust the sidebar panel size.",
            )
            with self.assertRaises(IterationError):
                append_iteration_event(
                    page_dir,
                    iteration,
                    "crop",
                    {"artifacts": {"scratch": Path(directory) / "outside.png"}},
                )


class ImageAnalysisTests(unittest.TestCase):
    def test_parse_viewport_and_region(self):
        self.assertEqual(parse_viewport("1536x1024"), (1536, 1024))
        self.assertEqual(parse_point("7,3"), (7, 3))
        self.assertEqual(parse_region("sidebar=0,44,252,934"), ("sidebar", (0, 44, 252, 934)))
        with self.assertRaises(ImageAnalysisError):
            parse_viewport("1536")

    def test_inspect_reports_dimensions_and_colors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.png"
            image = Image.new("RGB", (8, 6), "#101820")
            image.putpixel((0, 0), (255, 0, 0))
            image.save(path)
            result = inspect_image(path)
            self.assertEqual(result["viewport"], "8x6")
            self.assertEqual(result["mode"], "RGB")
            self.assertTrue(result["dominant_colors"])
            self.assertEqual(result["corners"]["top_left"], [255, 0, 0])

    def test_compare_identical_images_pass_structural_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.png"
            candidate = root / "candidate.png"
            Image.new("RGB", (12, 10), "#101820").save(reference)
            Image.new("RGB", (12, 10), "#101820").save(candidate)
            report, reference_image, candidate_image = compare_images(
                reference,
                candidate,
                regions=[("center", (2, 2, 6, 5))],
                points=[(2, 2)],
            )
            self.assertEqual(report["metrics"]["mean_abs_error"], 0)
            self.assertEqual(report["samples"]["delta"][0]["delta"], [0, 0, 0])
            self.assertEqual(report["metrics"]["exact_fraction"], 1)
            self.assertEqual(report["regions"][0]["name"], "center")
            artifacts = save_visual_artifacts(
                reference_image, candidate_image, root / "artifacts"
            )
            self.assertTrue(Path(artifacts["diff"]).is_file())
            self.assertTrue(Path(artifacts["threshold_mask"]).is_file())

    def test_compare_reports_localized_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.png"
            candidate = root / "candidate.png"
            Image.new("RGB", (10, 10), "#101820").save(reference)
            changed = Image.new("RGB", (10, 10), "#101820")
            changed.putpixel((7, 3), (255, 255, 255))
            changed.save(candidate)
            report, _, _ = compare_images(
                reference, candidate, tolerance=10, points=[(7, 3)]
            )
            metrics = report["metrics"]
            self.assertGreater(metrics["mismatch_pixels"], 0)
            self.assertEqual(report["samples"]["delta"][0]["delta"], [239, 231, 223])
            self.assertEqual(metrics["mismatch_bbox"]["x"], 7)
            self.assertEqual(metrics["mismatch_bbox"]["y"], 3)
            self.assertGreater(metrics["mismatch_fraction"], 0)

    def test_compare_rejects_different_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.png"
            candidate = root / "candidate.png"
            Image.new("RGB", (4, 4), "black").save(reference)
            Image.new("RGB", (5, 4), "black").save(candidate)
            with self.assertRaises(ImageAnalysisError):
                compare_images(reference, candidate)

    def test_tolerance_controls_mismatch_and_hotspot_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.png"
            candidate = root / "candidate.png"
            Image.new("RGB", (4, 4), (0, 0, 0)).save(reference)
            Image.new("RGB", (4, 4), (5, 5, 5)).save(candidate)
            tolerant, _, _ = compare_images(reference, candidate, tolerance=10)
            strict, _, _ = compare_images(reference, candidate, tolerance=1)
            self.assertEqual(tolerant["metrics"]["mismatch_pixels"], 0)
            self.assertEqual(tolerant["metrics"]["tile_hotspots"][0]["mismatch_fraction"], 0)
            self.assertEqual(strict["metrics"]["mismatch_pixels"], 16)
            self.assertEqual(strict["metrics"]["tile_hotspots"][0]["mismatch_fraction"], 1)


class WorkspaceStorageTests(unittest.TestCase):
    def test_page_name_defaults_to_reference_stem_and_rejects_paths(self):
        self.assertEqual(
            _page_name(SimpleNamespace(page_name=None, reference="screens/login.png")),
            "login",
        )
        with self.assertRaises(CliError):
            _page_name(SimpleNamespace(page_name="../escape", reference=None))

    def test_generated_paths_are_normalized_under_the_page_directory(self):
        args = SimpleNamespace(page_name="dashboard", reference=None)
        workspace = Path("/workspace")
        page_dir = workspace / ".artifacts/pixel-perfect/dashboard"
        self.assertEqual(
            _page_scoped_path(
                args,
                workspace,
                ".artifacts/pixel-perfect/iteration-06/diff.png",
                option="--output",
            ),
            page_dir / "iteration-06/diff.png",
        )
        self.assertEqual(
            _page_scoped_path(args, workspace, "/tmp/diff.png", option="--output"),
            page_dir / "diff.png",
        )

    def test_process_environment_keeps_cache_and_temp_paths_under_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Path(directory) / ".artifacts/pixel-perfect/.runtime"
            environment = process_environment(storage)
            for key in ("TMPDIR", "TMP", "TEMP", "PIP_CACHE_DIR", "PLAYWRIGHT_BROWSERS_PATH"):
                self.assertTrue(Path(environment[key]).resolve().is_relative_to(storage.resolve()))
            self.assertTrue((storage / "tmp").is_dir())
            self.assertTrue((storage / "pip-cache").is_dir())

    def test_iteration_output_paths_are_nested_and_page_scoped(self):
        args = SimpleNamespace(
            page_name="dashboard",
            reference=None,
            iteration=0,
            focus=None,
            hypothesis=None,
            iteration_note=None,
            final=False,
        )
        workspace = Path("/workspace")
        self.assertEqual(
            _page_scoped_path(args, workspace, "/tmp/candidate.png", option="--output"),
            workspace / ".artifacts/pixel-perfect/dashboard/iterations/000-baseline/candidate.png",
        )

    def test_iteration_crop_writes_semantic_tree_and_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            project.mkdir()
            runtime_path = runtime_directory(workspace)
            venv.EnvBuilder(with_pip=False).create(runtime_path)
            page_dir = workspace / ".artifacts/pixel-perfect/complex-dashboard"
            baseline = resolve_iteration(page_dir, 0)
            append_iteration_event(page_dir, baseline, "render", {"artifacts": {}})
            original_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                stdout = StringIO()
                with redirect_stdout(stdout):
                    status = cli_main(
                        [
                            "crop",
                            "--page-name",
                            "complex-dashboard",
                            "--iteration",
                            "1",
                            "--focus",
                            "sidebar",
                            "--hypothesis",
                            "sidebar-boundary",
                            "--iteration-note",
                            "Measure the sidebar boundary.",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            str(COMPLEX_FIXTURE),
                            "--candidate",
                            str(COMPLEX_FIXTURE),
                            "--section",
                            "sidebar=0,64,184,896",
                        ]
                    )
            finally:
                os.chdir(original_cwd)
            self.assertEqual(status, 0)
            pointer = json.loads(stdout.getvalue())
            iteration_dir = page_dir / "iterations/001-sidebar-sidebar-boundary"
            self.assertEqual(Path(pointer["reports"]["json"]["output_path"]).parent, iteration_dir.resolve())
            self.assertTrue((iteration_dir / "crop.json").is_file())
            self.assertTrue((iteration_dir / "crop-candidate.png").is_file())
            self.assertTrue((page_dir / "sections.json").is_file())
            ledger = json.loads((page_dir / "iterations.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [event["operation"] for event in ledger["iterations"][1]["events"]],
                ["crop"],
            )
            self.assertEqual(ledger["iterations"][1]["focus"], "sidebar")
            self.assertEqual(
                ledger["iterations"][1]["directory"],
                "iterations/001-sidebar-sidebar-boundary",
            )

    def test_iteration_compare_stays_in_the_numbered_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            project.mkdir()
            runtime_path = runtime_directory(workspace)
            venv.EnvBuilder(with_pip=False).create(runtime_path)
            page_dir = workspace / ".artifacts/pixel-perfect/complex-dashboard"
            page_dir.mkdir(parents=True)
            (page_dir / "sections.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sections": [
                            {"id": "sidebar", "bounds": [0, 64, 184, 896]}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            baseline = resolve_iteration(page_dir, 0)
            append_iteration_event(page_dir, baseline, "render", {"artifacts": {}})
            iteration = resolve_iteration(
                page_dir,
                1,
                focus="sidebar",
                hypothesis="sidebar-boundary",
                note="Measure the sidebar boundary.",
            )
            iteration.directory.mkdir(parents=True)
            candidate = iteration.directory / "candidate.png"
            with Image.open(COMPLEX_FIXTURE) as image:
                image.save(candidate)
            append_iteration_event(
                page_dir,
                iteration,
                "render",
                {"artifacts": {"candidate": candidate}},
            )
            original_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                stdout = StringIO()
                with redirect_stdout(stdout):
                    status = cli_main(
                        [
                            "compare",
                            "--page-name",
                            "complex-dashboard",
                            "--iteration",
                            "1",
                            "--focus",
                            "sidebar",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            str(COMPLEX_FIXTURE),
                            "--candidate",
                            str(candidate),
                            "--sections-file",
                            str(page_dir / "sections.json"),
                            "--section-only",
                        ]
                    )
            finally:
                os.chdir(original_cwd)
            self.assertEqual(status, 0)
            pointer = json.loads(stdout.getvalue())
            comparison_path = Path(pointer["reports"]["json"]["output_path"])
            self.assertEqual(comparison_path.parent, iteration.directory.resolve())
            self.assertEqual(
                pointer["candidate"]["output_path"],
                str((iteration.directory / "candidate-input.png").resolve()),
            )
            ledger = json.loads((page_dir / "iterations.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [event["operation"] for event in ledger["iterations"][1]["events"]],
                ["render", "compare"],
            )

    def test_inspect_cli_returns_only_a_report_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            project.mkdir()
            reference = workspace / "reference.png"
            Image.new("RGB", (8, 6), "#101820").save(reference)
            runtime_path = runtime_directory(workspace)
            venv.EnvBuilder(with_pip=False).create(runtime_path)
            original_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                stdout = StringIO()
                with redirect_stdout(stdout):
                    status = cli_main(
                        [
                            "inspect",
                            "--page-name",
                            "dashboard",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            "reference.png",
                        ]
                    )
            finally:
                os.chdir(original_cwd)
            self.assertEqual(status, 0)
            pointer = json.loads(stdout.getvalue())
            self.assertEqual(pointer["status"], "ok")
            self.assertEqual(pointer["operation"], "inspect")
            self.assertNotIn("page_name", pointer)
            self.assertNotIn("output_dir", pointer)
            self.assertNotIn("dominant_colors", pointer)
            report = json.loads(
                Path(pointer["reports"]["json"]["output_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(report["reference"]["viewport"], "8x6")
            self.assertEqual(
                report["report"], pointer["reports"]["json"]["output_path"]
            )

    def test_compare_cli_returns_only_artifact_pointers(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            project.mkdir()
            reference = workspace / "reference.png"
            candidate = workspace / "candidate.png"
            Image.new("RGB", (8, 6), "#101820").save(reference)
            Image.new("RGB", (8, 6), "#101820").save(candidate)
            runtime_path = runtime_directory(workspace)
            venv.EnvBuilder(with_pip=False).create(runtime_path)
            original_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                stdout = StringIO()
                with redirect_stdout(stdout):
                    status = cli_main(
                        [
                            "compare",
                            "--page-name",
                            "dashboard",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            "reference.png",
                            "--candidate",
                            "candidate.png",
                            "--output-dir",
                            ".artifacts/pixel-perfect/iteration-06",
                        ]
                    )
            finally:
                os.chdir(original_cwd)
            self.assertEqual(status, 0)
            pointer = json.loads(stdout.getvalue())
            self.assertEqual(pointer["status"], "ok")
            self.assertEqual(pointer["operation"], "compare")
            self.assertNotIn("page_name", pointer)
            self.assertNotIn("output_dir", pointer)
            self.assertNotIn("metrics", pointer)
            comparison = json.loads(
                Path(pointer["reports"]["json"]["output_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(comparison["metrics"]["mean_abs_error"], 0)
            self.assertEqual(comparison["visual_artifacts"], {})
            self.assertNotIn("artifacts", pointer)
            self.assertFalse(
                (workspace / ".artifacts/pixel-perfect/dashboard/iteration-06/diff.png").exists()
            )
            candidate_path = Path(pointer["candidate"]["output_path"])
            self.assertEqual(
                candidate_path.parent,
                (workspace / ".artifacts/pixel-perfect/dashboard").resolve(),
            )
            self.assertTrue(candidate_path.is_file())
            self.assertIn("description", pointer["candidate"])

    def test_compare_cli_materializes_visual_artifacts_with_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            project.mkdir()
            reference = workspace / "reference.png"
            candidate = workspace / "candidate.png"
            Image.new("RGB", (8, 6), "#101820").save(reference)
            Image.new("RGB", (8, 6), "#101820").save(candidate)
            runtime_path = runtime_directory(workspace)
            venv.EnvBuilder(with_pip=False).create(runtime_path)
            original_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                stdout = StringIO()
                with redirect_stdout(stdout):
                    status = cli_main(
                        [
                            "compare",
                            "--page-name",
                            "dashboard",
                            "--diagnostic",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            "reference.png",
                            "--candidate",
                            "candidate.png",
                            "--output-dir",
                            ".artifacts/pixel-perfect/iteration-07",
                        ]
                    )
            finally:
                os.chdir(original_cwd)
            self.assertEqual(status, 0)
            pointer = json.loads(stdout.getvalue())
            self.assertEqual(pointer["diagnostic"], True)
            diff_path = Path(pointer["artifacts"]["diff"]["output_path"])
            self.assertTrue(diff_path.is_file())
            self.assertEqual(
                diff_path.parent,
                (workspace / ".artifacts/pixel-perfect/dashboard/iteration-07").resolve(),
            )
            comparison = json.loads(
                Path(pointer["reports"]["json"]["output_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(comparison["visual_artifacts"]["diff"], str(diff_path))

    def test_final_verify_writes_only_to_final_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            project.mkdir()
            reference = workspace / "reference.png"
            candidate = workspace / "candidate.png"
            with Image.open(COMPLEX_FIXTURE) as image:
                image.save(reference)
                image.save(candidate)
            runtime_path = runtime_directory(workspace)
            venv.EnvBuilder(with_pip=False).create(runtime_path)
            original_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                stdout = StringIO()
                with redirect_stdout(stdout):
                    status = cli_main(
                        [
                            "verify",
                            "--page-name",
                            "dashboard",
                            "--final",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            "reference.png",
                            "--candidate",
                            "candidate.png",
                        ]
                    )
            finally:
                os.chdir(original_cwd)
            self.assertEqual(status, 0)
            pointer = json.loads(stdout.getvalue())
            final_dir = workspace / ".artifacts/pixel-perfect/dashboard/final"
            self.assertTrue(pointer["final"])
            self.assertNotIn("artifacts", pointer)
            self.assertEqual(Path(pointer["candidate"]["output_path"]).parent, final_dir.resolve())
            self.assertEqual(Path(pointer["reports"]["candidate_json"]["output_path"]).parent, final_dir.resolve())
            self.assertTrue((final_dir / "verification.json").is_file())
            verification = json.loads((final_dir / "verification.json").read_text(encoding="utf-8"))
            self.assertEqual(verification["artifacts"], {})
            self.assertFalse((final_dir / "overlay.png").exists())
            self.assertFalse((workspace / ".artifacts/pixel-perfect/dashboard/verification.json").exists())

    def test_cli_error_returns_a_persisted_error_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            project.mkdir()
            runtime_path = runtime_directory(workspace)
            venv.EnvBuilder(with_pip=False).create(runtime_path)
            original_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    status = cli_main(
                        [
                            "inspect",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            "missing.png",
                        ]
                    )
            finally:
                os.chdir(original_cwd)
            self.assertEqual(status, 2)
            self.assertEqual(stdout.getvalue(), "")
            pointer = json.loads(stderr.getvalue())
            self.assertEqual(pointer["status"], "error")
            self.assertEqual(pointer["operation"], "inspect")
            self.assertIn("description", pointer["error_report"])
            error = json.loads(
                Path(pointer["error_report"]["output_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(error["status"], "error")
            self.assertIn("Image file not found", error["error"])

    def test_cli_keeps_runtime_and_artifacts_in_invocation_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            project.mkdir()
            reference = workspace / "reference.png"
            Image.new("RGB", (8, 6), "#101820").save(reference)
            runtime_path = runtime_directory(workspace)
            venv.EnvBuilder(with_pip=False).create(runtime_path)
            original_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                stdout = StringIO()
                with redirect_stdout(stdout):
                    status = cli_main(
                        [
                            "decompose",
                            "--page-name",
                            "dashboard",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            str(project),
                            "--reference",
                            "reference.png",
                        ]
                    )
            finally:
                os.chdir(original_cwd)
            self.assertEqual(status, 0)
            pointer = json.loads(stdout.getvalue())
            self.assertEqual(pointer["status"], "draft")
            self.assertEqual(pointer["operation"], "decompose")
            self.assertNotIn("page_name", pointer)
            self.assertNotIn("output_dir", pointer)
            self.assertNotIn("plan", pointer)
            report = json.loads(
                Path(pointer["reports"]["json"]["output_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(report["runtime"]["directory"], str(runtime_path.resolve()))
            self.assertTrue(
                runtime_path.resolve().is_relative_to(
                    (workspace / ".artifacts/pixel-perfect").resolve()
                )
            )
            self.assertTrue(
                (workspace / ".artifacts/pixel-perfect/dashboard/decomposition.json").is_file()
            )
            self.assertTrue(
                (workspace / ".artifacts/pixel-perfect/dashboard/decomposition.md").is_file()
            )
            self.assertFalse((project / ".artifacts").exists())
            self.assertFalse((project / ".xzy-env").exists())

    def test_crop_cli_creates_and_appends_sections_for_complex_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            project.mkdir()
            runtime_path = runtime_directory(workspace)
            venv.EnvBuilder(with_pip=False).create(runtime_path)
            original_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                first_stdout = StringIO()
                with redirect_stdout(first_stdout):
                    first_status = cli_main(
                        [
                            "crop",
                            "--page-name",
                            "complex-dashboard",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            str(COMPLEX_FIXTURE),
                            "--candidate",
                            str(COMPLEX_FIXTURE),
                            "--section",
                            "header=0,0,1536,64",
                            "--grid",
                            "--grid-axis",
                            "vertical",
                            "--grid-spacing",
                            "32",
                        ]
                    )
                second_stdout = StringIO()
                with redirect_stdout(second_stdout):
                    second_status = cli_main(
                        [
                            "crop",
                            "--page-name",
                            "complex-dashboard",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            str(COMPLEX_FIXTURE),
                            "--section",
                            "sidebar=0,64,184,896",
                        ]
                    )
                conflict_stderr = StringIO()
                with redirect_stderr(conflict_stderr):
                    conflict_status = cli_main(
                        [
                            "crop",
                            "--page-name",
                            "complex-dashboard",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            str(COMPLEX_FIXTURE),
                            "--section",
                            "header=1,0,1535,64",
                        ]
                    )
                compare_stdout = StringIO()
                with redirect_stdout(compare_stdout):
                    compare_status = cli_main(
                        [
                            "compare",
                            "--page-name",
                            "complex-dashboard",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            str(COMPLEX_FIXTURE),
                            "--candidate",
                            str(COMPLEX_FIXTURE),
                            "--section-only",
                        ]
                    )
                verify_stdout = StringIO()
                with redirect_stdout(verify_stdout):
                    verify_status = cli_main(
                        [
                            "verify",
                            "--page-name",
                            "complex-dashboard",
                            "--runtime-ready",
                            "--no-auto-setup",
                            "--project-root",
                            "project",
                            "--reference",
                            str(COMPLEX_FIXTURE),
                            "--candidate",
                            str(COMPLEX_FIXTURE),
                            "--section-only",
                        ]
                    )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(first_status, 0)
            self.assertEqual(second_status, 0)
            self.assertEqual(conflict_status, 2)
            self.assertIn("different bounds", conflict_stderr.getvalue())
            self.assertEqual(compare_status, 0)
            self.assertEqual(verify_status, 0)
            first_pointer = json.loads(first_stdout.getvalue())
            second_pointer = json.loads(second_stdout.getvalue())
            compare_pointer = json.loads(compare_stdout.getvalue())
            verify_pointer = json.loads(verify_stdout.getvalue())
            self.assertEqual(first_pointer["operation"], "crop")
            self.assertEqual(second_pointer["operation"], "crop")
            sections_path = workspace / ".artifacts/pixel-perfect/complex-dashboard/sections.json"
            sections = json.loads(sections_path.read_text(encoding="utf-8"))
            self.assertEqual([entry["id"] for entry in sections["sections"]], ["header", "sidebar"])
            self.assertEqual(sections["sections"][0]["bounds"], [0, 0, 1536, 64])
            self.assertTrue(
                (workspace / ".artifacts/pixel-perfect/complex-dashboard/crops/header/reference-grid.png").is_file()
            )
            self.assertEqual(compare_pointer["comparison_scope"], "regions")
            self.assertNotIn("artifacts", compare_pointer)
            self.assertEqual(verify_pointer["comparison_scope"], "regions")
            self.assertNotIn("artifacts", verify_pointer)
            comparison = json.loads(
                Path(compare_pointer["reports"]["json"]["output_path"]).read_text(encoding="utf-8")
            )
            self.assertIsNone(comparison["metrics"])
            self.assertEqual(
                {region["name"] for region in comparison["regions"]},
                {"header", "sidebar"},
            )
            verification = json.loads(
                Path(verify_pointer["reports"]["verification_json"]["output_path"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(verification["status"], "pass")
            self.assertIsNone(verification["comparison"]["metrics"])


class FixtureImageTests(unittest.TestCase):
    def test_real_fixture_inspection_preserves_reference_dimensions(self):
        result = inspect_image(FIXTURE, points=[(390, 1400)])
        self.assertEqual(result["width"], 780)
        self.assertEqual(result["height"], 2798)
        self.assertEqual(result["mode"], "RGB")
        self.assertEqual(result["corners"]["top_left"], [248, 249, 255])
        self.assertEqual(result["samples"][0]["rgb"], [239, 244, 255])
        self.assertTrue(result["scanlines"]["horizontal"])

    def test_real_fixture_known_mutation_is_localized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Image.open(FIXTURE) as source:
                reference = source.convert("RGB").crop((350, 1350, 430, 1430))
            candidate = reference.copy()
            candidate.putpixel((40, 50), (0, 0, 0))
            reference_path = root / "fixture-region.png"
            candidate_path = root / "mutated-region.png"
            reference.save(reference_path)
            candidate.save(candidate_path)
            report, _, _ = compare_images(
                reference_path,
                candidate_path,
                points=[(40, 50)],
                tolerance=10,
            )
            self.assertEqual(report["metrics"]["mismatch_bbox"]["x"], 40)
            self.assertEqual(report["metrics"]["mismatch_bbox"]["y"], 50)
            self.assertEqual(report["metrics"]["mismatch_pixels"], 1)
            self.assertEqual(report["samples"]["delta"][0]["delta"], [-239, -244, -255])


class ComplexFixtureWorkflowTests(unittest.TestCase):
    def test_complex_fixture_requires_section_first_workflow(self):
        reference = inspect_image(COMPLEX_FIXTURE)
        plan = build_decomposition(
            reference,
            {"project_root": "/tmp/project", "frameworks": []},
        )
        self.assertEqual(reference["viewport"], "1536x1024")
        self.assertEqual(plan["complexity"]["classification"], "complex")
        self.assertFalse(plan["workflow_policy"]["requires_analysis_crops"])
        self.assertIn("--diagnostic", plan["workflow_policy"]["visual_diagnostics"])
        self.assertEqual(plan["workflow_policy"]["full_page_compare"], "final-only")

    def test_complex_fixture_crop_and_grid_keep_source_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Image.open(COMPLEX_FIXTURE) as source:
                reference = source.convert("RGB")
            box = (0, 64, 184, 896)
            crop = crop_image(reference, box)
            crop_path = root / "sidebar" / "reference.png"
            crop_path.parent.mkdir(parents=True)
            crop.save(crop_path)
            grid_path = root / "sidebar" / "reference-grid.png"
            save_grid_overlay(
                crop,
                grid_path,
                origin=(box[0], box[1]),
                spacing=20,
                scale=2,
                axis="both",
            )
            self.assertEqual(crop.size, (184, 896))
            self.assertEqual(crop.getpixel((0, 0)), reference.getpixel((0, 64)))
            with Image.open(grid_path) as grid:
                self.assertEqual(grid.size, (368, 1792))
                self.assertEqual(grid.getpixel((40, 100)), (57, 65, 74))

    def test_complex_fixture_focused_compare_omits_full_page_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "reference.png"
            candidate_path = root / "candidate.png"
            with Image.open(COMPLEX_FIXTURE) as source:
                reference = source.convert("RGB")
            candidate = reference.copy()
            original = candidate.getpixel((20, 80))
            replacement = (0, 0, 0) if original != (0, 0, 0) else (255, 255, 255)
            self.assertNotEqual(original, replacement)
            candidate.putpixel((20, 80), replacement)
            reference.save(reference_path)
            candidate.save(candidate_path)
            report, _, _ = compare_images(
                reference_path,
                candidate_path,
                regions=[("sidebar", (0, 64, 184, 896))],
                regions_only=True,
                tolerance=10,
            )
            self.assertEqual(report["comparison_scope"], "regions")
            self.assertIsNone(report["metrics"])
            self.assertEqual(report["global_metrics"]["status"], "omitted")
            section = report["regions"][0]
            self.assertEqual(section["mismatch_bbox"]["x"], 20)
            self.assertEqual(section["mismatch_bbox"]["y"], 16)
            self.assertEqual(section["mismatch_bbox_absolute"]["x"], 20)
            self.assertEqual(section["mismatch_bbox_absolute"]["y"], 80)


class SectionDecompositionTests(unittest.TestCase):
    def setUp(self):
        self.reference = {
            "path": "/tmp/reference.png",
            "viewport": "120x80",
            "width": 120,
            "height": 80,
            "edge_peaks": {
                "x": [{"position": 40, "score": 12.5}],
                "y": [{"position": 24, "score": 10.0}],
            },
        }
        self.project = {
            "project_root": "/tmp/project",
            "frameworks": ["static HTML"],
            "candidate_entrypoints": ["index.html"],
        }

    def test_empty_plan_is_a_draft_with_ordered_evidence(self):
        plan = build_decomposition(self.reference, self.project)
        self.assertEqual(plan["status"], "draft")
        self.assertEqual(plan["sections"], [])
        self.assertEqual(plan["implementation_order"][0]["id"], "global-frame")
        self.assertEqual(plan["suggested_boundaries"]["vertical"][0]["position"], 40)

    def test_complete_section_contract_becomes_ready(self):
        plan = build_decomposition(
            self.reference,
            self.project,
            sections=[
                {
                    "id": "main",
                    "bounds": [0, 0, 120, 80],
                    "visual_contract": ["Dark surface with one centered card"],
                    "content_state": "default",
                    "layout_owner": "index.html",
                    "dependencies": [],
                    "implementation_order": 1,
                    "verification_region": [0, 0, 120, 80],
                    "responsive_behavior": "Stack card below 640px.",
                    "acceptance_criteria": ["Card remains inside the viewport."],
                }
            ],
        )
        section = plan["sections"][0]
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(section["status"], "ready")
        self.assertEqual(section["missing_fields"], [])
        self.assertEqual(section["verification_region"]["width"], 120)

    def test_section_outside_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            build_decomposition(
                self.reference,
                self.project,
                sections=[{"id": "bad", "bounds": [100, 70, 30, 20]}],
            )


class ProjectAndRenderTests(unittest.TestCase):
    def test_project_inspection_detects_framework_and_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "scripts": {"dev": "vite"},
                        "dependencies": {"react": "^19.0.0", "vite": "^7.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "index.html").write_text("<main>demo</main>", encoding="utf-8")
            result = inspect_project(root)
            self.assertIn("React", result["frameworks"])
            self.assertIn("Vite", result["frameworks"])
            self.assertEqual(result["package"]["scripts"]["dev"], "vite")
            self.assertIn("index.html", result["candidate_entrypoints"])

    def test_local_entry_resolves_to_file_url_and_smoke_detects_flat_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "index.html"
            entry.write_text("<h1>demo</h1>", encoding="utf-8")
            self.assertTrue(resolve_target_url(root).startswith("file://"))
            flat = root / "flat.png"
            Image.new("RGB", (4, 4), "black").save(flat)
            self.assertEqual(smoke_check(flat)["status"], "fail")

    def test_local_url_resolves_relative_to_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            project.mkdir()
            target = workspace / "target.html"
            target.write_text("<main>workspace target</main>", encoding="utf-8")
            self.assertEqual(
                resolve_target_url(project, workspace_root=workspace, url="target.html"),
                target.resolve().as_uri(),
            )


if __name__ == "__main__":
    unittest.main()
