"""Read prepared plates and measure connected foreground in original pixels."""

from dataclasses import dataclass
from io import BytesIO
from math import hypot, pi

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid

from . import trials

MAX_UPLOAD = 12 * 1024 * 1024
MAX_PIXELS = 3_200_000
MAX_GRAINS = 600
COLORS = [(60, 223, 187), (106, 174, 253), (255, 207, 114), (239, 149, 201),
          (185, 208, 111), (226, 158, 110), (164, 161, 250), (130, 219, 237), (233, 221, 181)]
VIEWS = {"photo", "binary", "grade"}


def decode(data):
    if len(data) > MAX_UPLOAD:
        raise Invalid("单张原片至多 12 MiB。")
    try:
        with Image.open(BytesIO(data)) as source:
            width, height = source.size
            if source.format not in {"PNG", "JPEG", "TIFF"} or getattr(source, "n_frames", 1) != 1:
                raise Invalid("请选择单帧 PNG、JPEG 或 TIFF。")
            if min(width, height) < 20 or width * height > MAX_PIXELS:
                raise Invalid("原片短边至少 20 像素，总量至多 320 万像素。")
            source = ImageOps.exif_transpose(source)
            if "A" in source.getbands() or "transparency" in source.info:
                flattened = Image.new("RGBA", source.size, "white")
                flattened.alpha_composite(source.convert("RGBA"))
                return flattened.convert("RGB")
            if source.mode.startswith("I;16"):
                raster = np.asarray(source, dtype=np.uint16)
                return Image.fromarray((raster // 257).astype(np.uint8)).convert("RGB")
            return source.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise Invalid("读不到这张原片。") from error


def png(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def demo():
    """Generate independent practice silhouettes plus a physical-scale witness."""
    rng = np.random.default_rng(304)
    width, height = 760, 460
    x_axis, y_axis = np.meshgrid(np.arange(width), np.arange(height))
    falloff = 228 + 7 * np.sin(x_axis / 110) + 7 * np.cos(y_axis / 90)
    base = np.clip(falloff + rng.normal(0, .7, (height, width)), 0, 255).astype(np.uint8)
    gray = Image.fromarray(base)
    ink = ImageDraw.Draw(gray)
    particles = [
        (122, 121, 30, 23, -.33), (243, 114, 48, 9, .61), (389, 126, 49, 26, .31),
        (555, 113, 29, 28, .17), (659, 128, 14, 11, -.09), (111, 241, 39, 28, -.41),
        (266, 240, 56, 17, .19), (401, 223, 17, 12, -.25), (448, 250, 12, 9, .54),
        (565, 243, 40, 32, -.36), (658, 272, 23, 10, .71), (121, 343, 19, 15, .24),
        (219, 353, 20, 20, -.04), (356, 355, 40, 28, -.78), (475, 347, 19, 13, .69),
        (573, 352, 44, 10, .55), (678, 354, 21, 19, -.37), (44, 211, 18, 24, .22),
    ]
    for index, (cx, cy, rx, ry, angle) in enumerate(particles):
        points = []
        for step in range(10):
            arg = 2 * pi * step / 10
            radial = rng.uniform(.78, 1.12)
            dx = np.cos(arg) * rx * radial
            dy = np.sin(arg) * ry * radial
            points.append((cx + dx * np.cos(angle) - dy * np.sin(angle),
                           cy + dx * np.sin(angle) + dy * np.cos(angle)))
        ink.polygon(points, fill=31 + (7 * index) % 41)
    for x, y in [(200, 184), (609, 285), (485, 175), (307, 292), (637, 198)]:
        ink.rectangle((x, y, x + 1, y + 1), fill=79)
    image = gray.convert("RGB")
    ruler = ImageDraw.Draw(image)
    ruler.line((80, 420, 180, 420), fill=(66, 101, 145), width=2)
    for x in range(80, 181, 10):
        ruler.line((x, 415 if x in (80, 180) else 418, x, 426), fill=(64, 92, 127), width=2)
    ruler.text((112, 433), "10 mm", fill=(45, 59, 87))
    return image


@dataclass
class Analysis:
    box: list
    labels: np.ndarray
    grains: list
    grades: list
    foreground: int
    mm_per_px: float | None

    def grain_at(self, point):
        x, y = point
        left, top, right, bottom = self.box
        if not (left <= x < right and top <= y < bottom):
            return None
        label = int(self.labels[y - top, x - left])
        match = next((row for row in self.grains if row["label"] == label), None)
        return match["id"] if match else None

    def report(self):
        visible = []
        for row in self.grains:
            item = {key: value for key, value in row.items()
                    if key not in ("label", "equivalent_grade", "applied")}
            if row["applied"] is not None:
                item["trial"] = row["applied"]
            visible.append(item)
        counted = [row for row in visible if row["grade"] is not None]
        return {"box": self.box, "mm_per_px": self.mm_per_px, "foreground_px": self.foreground,
                "total": len(visible), "counted": len(counted), "grains": visible, "grades": self.grades}


def scale(calibration):
    if calibration is None:
        return None
    a, b = calibration["points"]
    return calibration["mm"] / hypot(b[0] - a[0], b[1] - a[1])


def equivalent_size(area, factor):
    return 2 * (area / pi) ** .5 * factor if factor is not None else None


def size_grade(size, holes):
    if size is None:
        return None
    return next((number for number, opening in enumerate(holes) if size <= opening), len(holes))


def component_rows(labels, stats, centroids, box, options, factor):
    left, top, right, bottom = box
    width = right - left
    positions = np.flatnonzero(labels.ravel())
    first = np.full(len(stats), labels.size, dtype=np.int64)
    np.minimum.at(first, labels.ravel()[positions], positions)
    minimum = options["min_area"]
    excluded = set(options["excluded"])
    trials_by_id = {row["grain_id"]: row for row in options.get("trials", [])}
    rows = []
    for label in np.flatnonzero(stats[:, cv2.CC_STAT_AREA] >= minimum):
        if label == 0:
            continue
        x, y, w, h, area = (int(value) for value in stats[label])
        cy, cx = divmod(int(first[label]), width)
        grain_id = f"{left + cx}:{top + cy}"
        size = equivalent_size(area, factor)
        edge = x == 0 or y == 0 or x + w == width or y + h == bottom - top
        omitted = grain_id in excluded
        grade = None if omitted or edge else size_grade(size, options["holes"])
        applied = None
        if factor is not None and not omitted and not edge:
            applied = trials_by_id.get(grain_id)
            if applied is not None and not 0 <= applied["grade"] < len(options["holes"]):
                applied = None
            if applied is not None and abs(options["holes"][applied["grade"]] - applied["hole"]) > trials.MM_TOLERANCE:
                applied = None
        if applied is not None:
            grade = applied["grade"]
        rows.append({"id": grain_id, "label": int(label), "area_px": area,
                     "center": [round(float(centroids[label][0]) + left, 2),
                                round(float(centroids[label][1]) + top, 2)],
                     "bounds": [x + left, y + top, x + w + left, y + h + top],
                     "edge": edge, "excluded": omitted, "equivalent_mm": size, "grade": grade,
                     "equivalent_grade": None if omitted or edge else size_grade(size, options["holes"]),
                     "applied": applied})
    return sorted(rows, key=lambda row: (-row["area_px"], row["center"][1], row["center"][0]))


def group_grades(rows, holes):
    grades = [{"id": n, "hole": opening, "count": 0, "area_px": 0, "doubled": 0,
               "color": "#%02x%02x%02x" % COLORS[n]} for n, opening in enumerate([*holes, None])]
    for row in rows:
        if row["grade"] is not None:
            target = grades[row["grade"]]
            target["count"] += 1
            target["area_px"] += row["area_px"]
            target["doubled"] += row["applied"] is not None
    return grades


def trial_grain(result, grain_id, holes):
    """Square-hole trials for one calibrated, intact, counted grain."""
    row, points = grain_outline(result, grain_id)
    proposals = trials.placements(points, result.mm_per_px, holes)
    fitting = [item for item in proposals if item["fit"]]
    tightest = fitting[0] if fitting else None
    roomy = next((item for item in fitting if item["grade"] > tightest["grade"]), None) if tightest else None
    return {"grain_id": grain_id, "box": result.box, "mm_per_px": result.mm_per_px,
            "equivalent_mm": round(row["equivalent_mm"], 4), "equivalent_grade": row["equivalent_grade"],
            "current_grade": row["grade"], "openings": proposals,
            "fitting": [item["grade"] for item in fitting],
            "tightest": tightest, "roomy": roomy,
            "center": row["center"], "bounds": row["bounds"],
            "applied": row["applied"]}


def grain_outline(result, grain_id):
    row = next((item for item in result.grains if item["id"] == grain_id), None)
    if row is None:
        raise Invalid("找不到这颗颗粒，请重新点选。")
    if result.mm_per_px is None:
        raise Invalid("这张原片尚未标定，不能试方筛孔。")
    reason = trials.eligible(row, result.mm_per_px)
    if reason:
        raise Invalid(reason)
    points = trials.outline(result.labels, result.box, row)
    if points is None or len(points) < 3:
        raise Invalid("读不到这颗颗粒的完整轮廓。")
    return row, points


def retry(image, options, entry, result=None, row=None):
    """Fresh closest placement of an adopted grain in its opening, or None if gone."""
    result = result if result is not None else analyze(image, options)
    row = row or next((item for item in result.grains if item["id"] == entry["grain_id"]), None)
    if row is None or row["edge"] or row["excluded"] or row["equivalent_mm"] is None:
        return None
    points = trials.outline(result.labels, result.box, row)
    if points is None or len(points) < 3:
        return None
    fresh = trials.candidate(points, result.mm_per_px, entry["hole"])
    return fresh if fresh["fit"] else None


def analyze(image, options):
    box = options["box"]
    pixels = np.asarray(image.crop(tuple(box)).convert("L"))
    foreground = np.ascontiguousarray(pixels <= options["threshold"], dtype=np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(foreground, connectivity=8)
    if np.count_nonzero(stats[1:, cv2.CC_STAT_AREA] >= options["min_area"]) > MAX_GRAINS:
        raise Invalid("检查框中的颗粒过多，请缩小框或提高最小面积。")
    factor = scale(options["calibration"])
    rows = component_rows(labels, stats, centroids, box, options, factor)
    return Analysis(box, labels, rows, group_grades(rows, options["holes"]), int(foreground.sum()), factor)


def render(image, result, mode):
    if mode == "photo":
        return image.copy()
    output = np.asarray(image).copy()
    left, top, right, bottom = result.box
    window = output[top:bottom, left:right]
    if mode == "binary":
        window[:] = np.where(result.labels[..., None] != 0, 35, 230)
    else:
        window[:] = (window.astype(np.uint16) * 2 // 5 + 22).astype(np.uint8)
        colors = np.zeros((int(result.labels.max()) + 1, 3), dtype=np.uint8)
        for row in result.grains:
            color = COLORS[row["grade"]] if row["grade"] is not None else (132, 139, 149)
            if row["edge"]:
                color = (247, 112, 89)
            colors[row["label"]] = color
        mapped = colors[result.labels]
        present = mapped.any(axis=2)
        window[present] = mapped[present]
    return Image.fromarray(output)
