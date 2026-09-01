"""Image inspection and comparison primitives used by the public CLI.

The functions in this module return JSON-serializable data so a model without
vision can make the same decisions from a report that a model with vision
would make from a side-by-side image.
"""

from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import re
from typing import Any, Iterable

from PIL import Image, ImageChops, ImageDraw, ImageEnhance

try:  # NumPy is installed by the bootstrap command when possible.
    import numpy as np
except ImportError:  # pragma: no cover - exercised in minimal environments.
    np = None


class ImageAnalysisError(RuntimeError):
    """Raised when an input image cannot be analyzed."""


def load_rgb(path: Path) -> Image.Image:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ImageAnalysisError(f"Image file not found: {path}")
    try:
        with Image.open(path) as image:
            return image.convert("RGB")
    except Exception as exc:
        raise ImageAnalysisError(f"Could not read image {path}: {exc}") from exc


def parse_viewport(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().replace(" ", "").split("x", 1)
        width, height = int(width_text), int(height_text)
    except (ValueError, AttributeError) as exc:
        raise ImageAnalysisError(
            f"Invalid viewport {value!r}; expected WIDTHxHEIGHT"
        ) from exc
    if width <= 0 or height <= 0:
        raise ImageAnalysisError(f"Viewport dimensions must be positive: {value!r}")
    return width, height


def parse_point(value: str) -> tuple[int, int]:
    try:
        x_text, y_text = (part.strip() for part in value.split(","))
        x, y = int(x_text), int(y_text)
    except ValueError as exc:
        raise ImageAnalysisError(f"Invalid point {value!r}; expected x,y") from exc
    if x < 0 or y < 0:
        raise ImageAnalysisError(f"Point coordinates must be non-negative: {value!r}")
    return x, y


def validate_points(points: Iterable[tuple[int, int]], size: tuple[int, int]) -> list[tuple[int, int]]:
    width, height = size
    result = []
    for x, y in points:
        if x >= width or y >= height:
            raise ImageAnalysisError(
                f"Point ({x},{y}) falls outside image {width}x{height}"
            )
        result.append((x, y))
    return result


def _sample_points(image: Image.Image, points: Iterable[tuple[int, int]]) -> list[dict[str, Any]]:
    return [
        {
            "x": x,
            "y": y,
            "rgb": list(image.getpixel((x, y))),
            "hex": _serial_color(image.getpixel((x, y))),
        }
        for x, y in points
    ]


def parse_region(value: str) -> tuple[str, tuple[int, int, int, int]]:
    """Parse ``name=x,y,width,height`` or ``name:x,y,width,height``."""

    if "=" in value:
        name, coordinates = value.split("=", 1)
    elif ":" in value:
        name, coordinates = value.split(":", 1)
    else:
        raise ImageAnalysisError(
            f"Invalid region {value!r}; expected name=x,y,width,height"
        )
    try:
        x, y, width, height = (int(part.strip()) for part in coordinates.split(","))
    except ValueError as exc:
        raise ImageAnalysisError(
            f"Invalid region {value!r}; expected name=x,y,width,height"
        ) from exc
    if not name.strip() or width <= 0 or height <= 0:
        raise ImageAnalysisError(f"Region must have a name and positive size: {value!r}")
    return name.strip(), (x, y, width, height)


def validate_regions(
    regions: Iterable[tuple[str, tuple[int, int, int, int]]], size: tuple[int, int]
) -> list[tuple[str, tuple[int, int, int, int]]]:
    width, height = size
    result = []
    names: set[str] = set()
    for name, (x, y, region_width, region_height) in regions:
        if name in names:
            raise ImageAnalysisError(f"Duplicate region name: {name}")
        if (
            x < 0
            or y < 0
            or region_width <= 0
            or region_height <= 0
            or x + region_width > width
            or y + region_height > height
        ):
            raise ImageAnalysisError(
                f"Region {name!r} ({x},{y},{region_width},{region_height}) "
                f"falls outside image {width}x{height}"
            )
        names.add(name)
        result.append((name, (x, y, region_width, region_height)))
    return result


def _serial_color(color: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % color


def _pixel_data(image: Image.Image):
    flattened = getattr(image, "get_flattened_data", None)
    return flattened() if flattened is not None else image.getdata()


def _dominant_colors(image: Image.Image, limit: int = 12) -> list[dict[str, Any]]:
    sample_width = min(160, image.width)
    sample_height = max(1, round(image.height * sample_width / image.width))
    sample = image.resize((sample_width, sample_height))
    quantized = sample.quantize(colors=limit).convert("RGB")
    counts = Counter(_pixel_data(quantized))
    total = sample_width * sample_height
    return [
        {
            "color": _serial_color(tuple(color)),
            "rgb": list(color),
            "pixels": count,
            "fraction": round(count / total, 6),
        }
        for color, count in counts.most_common(limit)
    ]


def _probe_positions(length: int) -> list[int]:
    values = {
        0,
        max(0, length - 1),
        max(0, length - 2),
        round(length * 0.05),
        round(length * 0.10),
        round(length * 0.25),
        round(length * 0.50),
        round(length * 0.75),
        round(length * 0.90),
        round(length * 0.95),
    }
    return sorted(value for value in values if 0 <= value < length)


def _bucket_color(color: tuple[int, int, int], step: int = 8) -> tuple[int, int, int]:
    return tuple((channel // step) * step for channel in color)


def _scanline_runs(image: Image.Image, *, axis: str) -> list[dict[str, Any]]:
    """Summarize long quantized color runs at deterministic probe lines."""

    if axis not in {"x", "y"}:
        raise ImageAnalysisError(f"Unknown scanline axis: {axis}")
    length = image.width if axis == "x" else image.height
    positions = _probe_positions(image.height if axis == "x" else image.width)
    minimum_length = max(8, length // 80)
    result = []
    for position in positions:
        colors = (
            [_bucket_color(image.getpixel((index, position))) for index in range(length)]
            if axis == "x"
            else [_bucket_color(image.getpixel((position, index))) for index in range(length)]
        )
        runs = []
        start = 0
        previous = colors[0]
        for index, color in enumerate(colors[1:], start=1):
            if color == previous:
                continue
            if index - start >= minimum_length:
                runs.append(
                    {
                        "start": start,
                        "end": index - 1,
                        "length": index - start,
                        "color": _serial_color(previous),
                    }
                )
            start = index
            previous = color
        if length - start >= minimum_length:
            runs.append(
                {
                    "start": start,
                    "end": length - 1,
                    "length": length - start,
                    "color": _serial_color(previous),
                }
            )
        runs.sort(key=lambda run: run["length"], reverse=True)
        result.append({"position": position, "runs": runs[:24]})
    return result


def _top_peaks(values: list[float], limit: int = 12, minimum: float = 0.0) -> list[dict[str, Any]]:
    candidates = [
        (index, value) for index, value in enumerate(values) if value > minimum
    ]
    candidates.sort(key=lambda item: item[1], reverse=True)
    return [
        {"position": index, "score": round(float(value), 4)}
        for index, value in candidates[:limit]
    ]


def _edge_peaks(image: Image.Image) -> dict[str, Any]:
    if np is None:
        return {"engine": "unavailable", "x": [], "y": []}
    array = np.asarray(image, dtype=np.float32)
    gray = array @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    x_gradient = np.abs(np.diff(gray, axis=1)).mean(axis=0).tolist()
    y_gradient = np.abs(np.diff(gray, axis=0)).mean(axis=1).tolist()
    return {
        "engine": "numpy",
        "x": _top_peaks(x_gradient, minimum=0.35),
        "y": _top_peaks(y_gradient, minimum=0.35),
    }


def inspect_image(
    path: Path, *, points: Iterable[tuple[int, int]] = ()
) -> dict[str, Any]:
    path = Path(path).expanduser().resolve()
    image = load_rgb(path)
    corners = {
        "top_left": list(image.getpixel((0, 0))),
        "top_right": list(image.getpixel((image.width - 1, 0))),
        "bottom_left": list(image.getpixel((0, image.height - 1))),
        "bottom_right": list(image.getpixel((image.width - 1, image.height - 1))),
    }
    checked_points = validate_points(points, image.size)
    with Image.open(path) as source:
        image_format = source.format
    return {
        "path": str(path),
        "format": image_format,
        "mode": "RGB",
        "width": image.width,
        "height": image.height,
        "viewport": f"{image.width}x{image.height}",
        "corners": corners,
        "samples": _sample_points(image, checked_points),
        "dominant_colors": _dominant_colors(image),
        "edge_peaks": _edge_peaks(image),
        "scanlines": {
            "horizontal": _scanline_runs(image, axis="x"),
            "vertical": _scanline_runs(image, axis="y"),
        },
        "analysis_engine": "numpy" if np is not None else "python",
    }


def _bbox_numpy(mask: Any) -> dict[str, int] | None:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return {
        "x": int(xs.min()),
        "y": int(ys.min()),
        "width": int(xs.max() - xs.min() + 1),
        "height": int(ys.max() - ys.min() + 1),
        "pixels": int(mask.sum()),
    }


def _bbox_python(mask: list[list[bool]]) -> dict[str, int] | None:
    min_x = min_y = None
    max_x = max_y = -1
    pixels = 0
    for y, row in enumerate(mask):
        for x, value in enumerate(row):
            if not value:
                continue
            pixels += 1
            min_x = x if min_x is None else min(min_x, x)
            min_y = y if min_y is None else min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
    if min_x is None or min_y is None:
        return None
    return {
        "x": min_x,
        "y": min_y,
        "width": max_x - min_x + 1,
        "height": max_y - min_y + 1,
        "pixels": pixels,
    }


def _ranked_density(values: Any, limit: int = 12) -> list[dict[str, Any]]:
    if hasattr(values, "tolist"):
        values = values.tolist()
    pairs = [(index, float(value)) for index, value in enumerate(values) if value > 0]
    pairs.sort(key=lambda item: item[1], reverse=True)
    return [
        {"position": index, "fraction": round(value, 6)}
        for index, value in pairs[:limit]
    ]


def _tile_hotspots_numpy(pixel_error: Any, tile_size: int, tolerance: int) -> list[dict[str, Any]]:
    height, width = pixel_error.shape
    tiles = []
    for y in range(0, height, tile_size):
        for x in range(0, width, tile_size):
            tile = pixel_error[y : y + tile_size, x : x + tile_size]
            tiles.append(
                {
                    "x": x,
                    "y": y,
                    "width": int(tile.shape[1]),
                    "height": int(tile.shape[0]),
                    "mean_error": round(float(tile.mean()), 4),
                    "max_error": int(tile.max()),
                    "mismatch_fraction": round(float((tile > tolerance).mean()), 6),
                }
            )
    tiles.sort(
        key=lambda tile: (tile["mean_error"], tile["mismatch_fraction"]), reverse=True
    )
    return tiles[:20]


def _tile_hotspots_python(
    ref: Image.Image, candidate: Image.Image, tile_size: int, tolerance: int
) -> list[dict[str, Any]]:
    tiles = []
    for y in range(0, ref.height, tile_size):
        for x in range(0, ref.width, tile_size):
            errors = []
            mismatch = 0
            for yy in range(y, min(y + tile_size, ref.height)):
                for xx in range(x, min(x + tile_size, ref.width)):
                    a = ref.getpixel((xx, yy))
                    b = candidate.getpixel((xx, yy))
                    error = max(abs(a[i] - b[i]) for i in range(3))
                    errors.append(error)
                    mismatch += error > tolerance
            if errors:
                tiles.append(
                    {
                        "x": x,
                        "y": y,
                        "width": min(tile_size, ref.width - x),
                        "height": min(tile_size, ref.height - y),
                        "mean_error": round(sum(errors) / len(errors), 4),
                        "max_error": max(errors),
                        "mismatch_fraction": round(mismatch / len(errors), 6),
                    }
                )
    tiles.sort(
        key=lambda tile: (tile["mean_error"], tile["mismatch_fraction"]), reverse=True
    )
    return tiles[:20]


def _metrics_numpy(reference: Image.Image, candidate: Image.Image, tolerance: int) -> tuple[dict[str, Any], Any]:
    reference_array = np.asarray(reference, dtype=np.int16)
    candidate_array = np.asarray(candidate, dtype=np.int16)
    difference = np.abs(reference_array - candidate_array)
    pixel_error = difference.max(axis=2)
    mismatch = pixel_error > tolerance
    total = mismatch.size
    metrics = {
        "mean_abs_error": round(float(difference.mean()), 6),
        "mean_abs_error_by_channel": [
            round(float(value), 6) for value in difference.mean(axis=(0, 1))
        ],
        "max_error": int(pixel_error.max()),
        "exact_fraction": round(float((pixel_error == 0).mean()), 6),
        "within_tolerance_fraction": round(float((~mismatch).mean()), 6),
        "mismatch_fraction": round(float(mismatch.mean()), 6),
        "mismatch_pixels": int(mismatch.sum()),
        "tolerance": tolerance,
        "analysis_engine": "numpy",
        "mismatch_bbox": _bbox_numpy(mismatch),
        "top_mismatch_rows": _ranked_density(mismatch.mean(axis=1)),
        "top_mismatch_columns": _ranked_density(mismatch.mean(axis=0)),
    }
    return metrics, (difference, pixel_error, mismatch)


def _metrics_python(reference: Image.Image, candidate: Image.Image, tolerance: int) -> tuple[dict[str, Any], Any]:
    mismatch: list[list[bool]] = []
    total_channels = 0
    total_error = 0
    channel_error = [0, 0, 0]
    max_error = 0
    exact = 0
    mismatch_pixels = 0
    row_counts = [0] * reference.height
    column_counts = [0] * reference.width
    for y in range(reference.height):
        row: list[bool] = []
        for x in range(reference.width):
            a = reference.getpixel((x, y))
            b = candidate.getpixel((x, y))
            errors = [abs(a[i] - b[i]) for i in range(3)]
            pixel_error = max(errors)
            row_mismatch = pixel_error > tolerance
            row.append(row_mismatch)
            total_error += sum(errors)
            total_channels += 3
            for index, error in enumerate(errors):
                channel_error[index] += error
            max_error = max(max_error, pixel_error)
            if pixel_error == 0:
                exact += 1
            if row_mismatch:
                mismatch_pixels += 1
                row_counts[y] += 1
                column_counts[x] += 1
        mismatch.append(row)
    total = reference.width * reference.height
    metrics = {
        "mean_abs_error": round(total_error / total_channels, 6),
        "mean_abs_error_by_channel": [
            round(value / total, 6) for value in channel_error
        ],
        "max_error": max_error,
        "exact_fraction": round(exact / total, 6),
        "within_tolerance_fraction": round(1 - mismatch_pixels / total, 6),
        "mismatch_fraction": round(mismatch_pixels / total, 6),
        "mismatch_pixels": mismatch_pixels,
        "tolerance": tolerance,
        "analysis_engine": "python",
        "mismatch_bbox": _bbox_python(mismatch),
        "top_mismatch_rows": _ranked_density(
            [count / reference.width for count in row_counts]
        ),
        "top_mismatch_columns": _ranked_density(
            [count / reference.height for count in column_counts]
        ),
    }
    return metrics, mismatch


def crop_image(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """Return an exact analysis crop without changing the source image."""

    x, y, width, height = box
    return image.crop((x, y, x + width, y + height))


def _region_image(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    return crop_image(image, box)


def artifact_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    if slug != value:
        suffix = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug or 'section'}-{suffix}"
    return slug or "section"


def save_grid_overlay(
    image: Image.Image,
    output_path: Path,
    *,
    origin: tuple[int, int] = (0, 0),
    spacing: int = 10,
    scale: int = 2,
    axis: str = "both",
) -> str:
    """Save an enlarged coordinate grid for analysis, never for runtime UI."""

    if spacing <= 0:
        raise ImageAnalysisError("Grid spacing must be positive")
    if scale <= 0:
        raise ImageAnalysisError("Grid scale must be positive")
    if axis not in {"both", "vertical", "horizontal"}:
        raise ImageAnalysisError(
            f"Invalid grid axis {axis!r}; expected both, vertical, or horizontal"
        )

    resampling = getattr(Image, "Resampling", Image).NEAREST
    enlarged = image.resize((image.width * scale, image.height * scale), resampling)
    draw = ImageDraw.Draw(enlarged)
    line_color = "#39414a"
    label_color = "#ffff00"
    origin_x, origin_y = origin
    vertical_positions = list(range(0, image.width, spacing))
    horizontal_positions = list(range(0, image.height, spacing))
    if image.width - 1 not in vertical_positions:
        vertical_positions.append(image.width - 1)
    if image.height - 1 not in horizontal_positions:
        horizontal_positions.append(image.height - 1)

    if axis in {"both", "vertical"}:
        for position in vertical_positions:
            coordinate = position * scale
            draw.line((coordinate, 0, coordinate, enlarged.height - 1), fill=line_color)
            draw.text(
                (min(coordinate + 2, max(0, enlarged.width - 1)), 2),
                str(origin_x + position),
                fill=label_color,
            )
    if axis in {"both", "horizontal"}:
        for position in horizontal_positions:
            coordinate = position * scale
            draw.line((0, coordinate, enlarged.width - 1, coordinate), fill=line_color)
            draw.text(
                (2, min(coordinate + 2, max(0, enlarged.height - 1))),
                str(origin_y + position),
                fill=label_color,
            )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    enlarged.save(output_path)
    return str(output_path)


def save_region_visual_artifacts(
    reference: Image.Image,
    candidate: Image.Image,
    regions: Iterable[tuple[str, tuple[int, int, int, int]]],
    output_dir: Path,
    *,
    tolerance: int = 10,
) -> dict[str, str]:
    """Save per-section crops and diagnostics without producing a full-page diff."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, str] = {}
    used_slugs: set[str] = set()
    for name, box in regions:
        slug = artifact_slug(name)
        suffix = 2
        while slug in used_slugs:
            slug = f"{artifact_slug(name)}-{suffix}"
            suffix += 1
        used_slugs.add(slug)
        section_dir = output_dir / "sections" / slug
        section_dir.mkdir(parents=True, exist_ok=True)
        reference_crop = crop_image(reference, box)
        candidate_crop = crop_image(candidate, box)
        reference_path = section_dir / "reference.png"
        candidate_path = section_dir / "candidate.png"
        reference_crop.save(reference_path)
        candidate_crop.save(candidate_path)
        visual_paths = save_visual_artifacts(
            reference_crop, candidate_crop, section_dir, tolerance=tolerance
        )
        files = {
            "reference": str(reference_path),
            "candidate": str(candidate_path),
            **visual_paths,
        }
        for kind, path in files.items():
            artifacts[f"section_{slug}_{kind}"] = path
    return artifacts


def compare_images(
    reference_path: Path,
    candidate_path: Path,
    *,
    regions: Iterable[tuple[str, tuple[int, int, int, int]]] = (),
    tolerance: int = 10,
    tile_size: int = 64,
    points: Iterable[tuple[int, int]] = (),
    regions_only: bool = False,
) -> tuple[dict[str, Any], Image.Image, Image.Image]:
    if tolerance < 0 or tolerance > 255:
        raise ImageAnalysisError("Pixel tolerance must be between 0 and 255")
    if tile_size <= 0:
        raise ImageAnalysisError("Tile size must be positive")

    reference = load_rgb(reference_path)
    candidate = load_rgb(candidate_path)
    if reference.size != candidate.size:
        raise ImageAnalysisError(
            f"Image dimensions differ: reference={reference.width}x{reference.height}, "
            f"candidate={candidate.width}x{candidate.height}"
        )
    parsed_regions = validate_regions(regions, reference.size)
    checked_points = validate_points(points, reference.size)
    if regions_only and not parsed_regions:
        raise ImageAnalysisError(
            "Focused section comparison requires at least one named region"
        )

    metrics: dict[str, Any] | None = None
    if not regions_only:
        if np is not None:
            metrics, arrays = _metrics_numpy(reference, candidate, tolerance)
            _, pixel_error, mismatch = arrays
            metrics["tile_hotspots"] = _tile_hotspots_numpy(
                pixel_error, tile_size, tolerance
            )
        else:
            metrics, mismatch = _metrics_python(reference, candidate, tolerance)
            metrics["tile_hotspots"] = _tile_hotspots_python(
                reference, candidate, tile_size, tolerance
            )

    region_reports = []
    for name, box in parsed_regions:
        region_reference = crop_image(reference, box)
        region_candidate = crop_image(candidate, box)
        if np is not None:
            region_metrics, _ = _metrics_numpy(
                region_reference, region_candidate, tolerance
            )
        else:
            region_metrics, _ = _metrics_python(
                region_reference, region_candidate, tolerance
            )
        region_metrics.pop("tile_hotspots", None)
        local_bbox = region_metrics.get("mismatch_bbox")
        absolute_bbox = None
        if local_bbox:
            absolute_bbox = {
                **local_bbox,
                "x": local_bbox["x"] + box[0],
                "y": local_bbox["y"] + box[1],
            }
        region_reports.append(
            {
                "name": name,
                "box": list(box),
                "mismatch_bbox_absolute": absolute_bbox,
                **region_metrics,
            }
        )

    report: dict[str, Any] = {
        "comparison_scope": "regions" if regions_only else "full-page",
        "reference": str(Path(reference_path).expanduser().resolve()),
        "candidate": str(Path(candidate_path).expanduser().resolve()),
        "width": reference.width,
        "height": reference.height,
        "viewport": f"{reference.width}x{reference.height}",
        "metrics": metrics,
        "regions": region_reports,
        "samples": {
            "reference": _sample_points(reference, checked_points),
            "candidate": _sample_points(candidate, checked_points),
            "delta": [
                {
                    "x": x,
                    "y": y,
                    "delta": [
                        candidate.getpixel((x, y))[channel]
                        - reference.getpixel((x, y))[channel]
                        for channel in range(3)
                    ],
                }
                for x, y in checked_points
            ],
        },
    }
    if regions_only:
        report["global_metrics"] = {
            "status": "omitted",
            "reason": "Focused section comparison intentionally omits full-page metrics.",
        }
    else:
        report.update(
            {
                "reference_edge_peaks": _edge_peaks(reference),
                "candidate_edge_peaks": _edge_peaks(candidate),
                "reference_scanlines": {
                    "horizontal": _scanline_runs(reference, axis="x"),
                    "vertical": _scanline_runs(reference, axis="y"),
                },
                "candidate_scanlines": {
                    "horizontal": _scanline_runs(candidate, axis="x"),
                    "vertical": _scanline_runs(candidate, axis="y"),
                },
                "reference_dominant_colors": _dominant_colors(reference),
                "candidate_dominant_colors": _dominant_colors(candidate),
            }
        )
    return report, reference, candidate


def save_visual_artifacts(
    reference: Image.Image,
    candidate: Image.Image,
    output_dir: Path,
    *,
    tolerance: int = 10,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = output_dir / "overlay.png"
    diff_path = output_dir / "diff.png"
    mask_path = output_dir / "threshold-mask.png"

    Image.blend(reference, candidate, 0.5).save(overlay_path)
    diff = ImageChops.difference(reference, candidate)
    ImageEnhance.Brightness(diff).enhance(4).save(diff_path)

    if np is not None:
        reference_array = np.asarray(reference, dtype=np.int16)
        candidate_array = np.asarray(candidate, dtype=np.int16)
        mask = (np.abs(reference_array - candidate_array).max(axis=2) > tolerance).astype(
            "uint8"
        ) * 255
        Image.fromarray(mask, mode="L").save(mask_path)
    else:
        mask_image = Image.new("L", reference.size, 0)
        pixels = mask_image.load()
        for y in range(reference.height):
            for x in range(reference.width):
                a = reference.getpixel((x, y))
                b = candidate.getpixel((x, y))
                pixels[x, y] = 255 if max(abs(a[i] - b[i]) for i in range(3)) > tolerance else 0
        mask_image.save(mask_path)

    return {
        "overlay": str(overlay_path),
        "diff": str(diff_path),
        "threshold_mask": str(mask_path),
    }
