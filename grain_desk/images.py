"""Read prepared plates and measure connected foreground in original pixels."""

from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO
from math import hypot, pi

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid

MAX_UPLOAD = 12 * 1024 * 1024
MAX_PIXELS = 3_200_000
MAX_GRAINS = 600
COLORS = [(60, 223, 187), (106, 174, 253), (255, 207, 114), (239, 149, 201),
          (185, 208, 111), (226, 158, 110), (164, 161, 250), (130, 219, 237), (233, 221, 181)]
VIEWS = {"photo", "binary", "grade"}
# legacy keeps the old box-cropped reading; centroid and coverage measure whole
# grains against the original plate and only change how the box is credited.
CROSSING = ("legacy", "centroid", "coverage")
DEFAULT_CROSSING = "centroid"


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
    crossing: str = "legacy"

    def grain_at(self, point):
        x, y = point
        left, top, right, bottom = self.box
        if not (left <= x < right and top <= y < bottom):
            return None
        label = int(self.labels[y - top, x - left])
        if label == 0:
            return None
        match = next((row for row in self.grains if row["label"] == label), None)
        return match["id"] if match else None

    def report(self):
        visible = [{k: value for k, value in row.items() if k != "label"} for row in self.grains]
        credited = [row for row in visible if row["grade"] is not None and row.get("share", 1) > 0]
        if self.crossing == "coverage":
            counted = float(sum(Fraction(row["in_box_px"], row["area_px"]) for row in credited))
        else:
            counted = len(credited)
        truncated = sum(1 for row in visible if row.get("truncated"))
        return {"box": self.box, "crossing": self.crossing, "mm_per_px": self.mm_per_px,
                "foreground_px": self.foreground, "total": len(visible), "truncated": truncated,
                "counted": counted, "counted_grains": len(credited),
                "grains": visible, "grades": self.grades}

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
    """Legacy reading: components are found inside the box crop, so box edges truncate."""
    left, top, right, bottom = box
    width = right - left
    positions = np.flatnonzero(labels.ravel())
    first = np.full(len(stats), labels.size, dtype=np.int64)
    np.minimum.at(first, labels.ravel()[positions], positions)
    minimum = options["min_area"]
    excluded = set(options["excluded"])
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
        rows.append({"id": grain_id, "label": int(label), "area_px": area,
                     "center": [round(float(centroids[label][0]) + left, 2),
                                round(float(centroids[label][1]) + top, 2)],
                     "bounds": [x + left, y + top, x + w + left, y + h + top],
                     "edge": edge, "excluded": omitted, "equivalent_mm": size, "grade": grade,
                     "share": 1.0})
    return sorted(rows, key=lambda row: (-row["area_px"], row["center"][1], row["center"][0]))


def plate_components(image, options):
    """Label foreground once on the whole plate; only the plate border truncates a grain."""
    pixels = np.asarray(image.convert("L"))
    foreground = np.ascontiguousarray(pixels <= options["threshold"], dtype=np.uint8)
    _, labels, stats, centroids = cv2.connectedComponentsWithStats(foreground, connectivity=8)
    border = {int(label) for label in np.concatenate(
        (labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1])) if label}
    return labels, stats, centroids, border


def first_pixel(labels, stats, label):
    """Topmost, then leftmost, pixel of a component: a stable original-plate grain number."""
    x, y, width = (int(stats[label, cv2.CC_STAT_LEFT]), int(stats[label, cv2.CC_STAT_TOP]),
                   int(stats[label, cv2.CC_STAT_WIDTH]))
    row = labels[y, x:x + width] == label
    return x + int(np.flatnonzero(row)[0]), y


def crossing_rows(labels, stats, centroids, border, box, options, factor, mode):
    """Measure whole original grains and decide how much this box credits each one."""
    left, top, right, bottom = box
    minimum = options["min_area"]
    excluded = set(options["excluded"])
    rows = []
    for label, item in component_catalog(labels, stats, centroids, border, box, minimum).items():
        x0, y0, x1, y1 = item["bounds"]
        ix0, iy0 = max(left, x0), max(top, y0)
        ix1, iy1 = min(right, x1), min(bottom, y1)
        inside = int(np.count_nonzero(labels[iy0:iy1, ix0:ix1] == label)) if ix0 < ix1 and iy0 < iy1 else 0
        if inside == 0:
            continue
        identifier = item["id"]
        truncated = item["truncated"]
        omitted = identifier in excluded
        centre_x, centre_y = item["center"]
        if truncated:
            size, grade, share = None, None, 0.0
        else:
            size = equivalent_size(item["area_px"], factor)
            grade = None if omitted else size_grade(size, options["holes"])
            if omitted:
                share = 0.0
            elif mode == "centroid":
                share = 1.0 if left <= centre_x < right and top <= centre_y < bottom else 0.0
            else:
                share = inside / item["area_px"]
        rows.append({"id": identifier, "label": label, "area_px": item["area_px"],
                     "in_box_px": inside, "share": share,
                     "center": [round(centre_x, 2), round(centre_y, 2)],
                     "bounds": item["bounds"], "truncated": truncated,
                     "edge": truncated, "excluded": omitted,
                     "equivalent_mm": size, "grade": grade})
    return sorted(rows, key=lambda row: (-row["area_px"], row["id"]))


def component_catalog(labels, stats, centroids, border, box, minimum):
    """Index the plate components that can matter to this box: stable id, bounds, area, centre."""
    left, top, right, bottom = box
    catalog = {}
    relevant = np.unique(labels[top:bottom, left:right])
    for raw in relevant:
        label = int(raw)
        if label == 0 or int(stats[label, cv2.CC_STAT_AREA]) < minimum:
            continue
        x, y, w, h = (int(stats[label, cv2.CC_STAT_LEFT]), int(stats[label, cv2.CC_STAT_TOP]),
                      int(stats[label, cv2.CC_STAT_WIDTH]), int(stats[label, cv2.CC_STAT_HEIGHT]))
        gx, gy = first_pixel(labels, stats, label)
        catalog[label] = {"id": f"{gx}:{gy}", "bounds": [x, y, x + w, y + h],
                          "area_px": int(stats[label, cv2.CC_STAT_AREA]),
                          "center": [float(centroids[label][0]), float(centroids[label][1])],
                          "truncated": label in border}
    return catalog


def group_grades(rows, holes, mode="legacy"):
    """Credit each grade under the active convention; coverage keeps exact fractional counts."""
    fractional = mode == "coverage"
    grades = [{"id": n, "hole": opening, "count": Fraction(0), "area_px": 0,
               "color": "#%02x%02x%02x" % COLORS[n]} for n, opening in enumerate([*holes, None])]
    for row in rows:
        if row["grade"] is None:
            continue
        target = grades[row["grade"]]
        if mode == "legacy":
            credit, area = 1, row["area_px"]
        elif mode == "coverage":
            credit, area = Fraction(row["in_box_px"], row["area_px"]), row["in_box_px"]
        else:
            credit, area = (1, row["area_px"]) if row["share"] > 0 else (0, 0)
        target["count"] += credit
        target["area_px"] += area
    for grade in grades:
        grade["count"] = float(grade["count"]) if fractional else int(grade["count"])
        grade["area_px"] = float(grade["area_px"]) if fractional else int(grade["area_px"])
    return grades


def analyze(image, options):
    box = options["box"]
    crossing = options.get("crossing", "legacy")
    if crossing not in CROSSING:
        raise Invalid("请选择有效的跨框颗粒口径。")
    factor = scale(options["calibration"])
    if crossing == "legacy":
        pixels = np.asarray(image.crop(tuple(box)).convert("L"))
        foreground = np.ascontiguousarray(pixels <= options["threshold"], dtype=np.uint8)
        _, labels, stats, centroids = cv2.connectedComponentsWithStats(foreground, connectivity=8)
        if np.count_nonzero(stats[1:, cv2.CC_STAT_AREA] >= options["min_area"]) > MAX_GRAINS:
            raise Invalid("检查框中的颗粒过多，请缩小框或提高最小面积。")
        rows = component_rows(labels, stats, centroids, box, options, factor)
        return Analysis(box, labels, rows, group_grades(rows, options["holes"]),
                        int(foreground.sum()), factor, "legacy")
    labels, stats, centroids, border = plate_components(image, options)
    left, top, right, bottom = box
    rows = crossing_rows(labels, stats, centroids, border, box, options, factor, crossing)
    if len(rows) > MAX_GRAINS:
        raise Invalid("检查框中的颗粒过多，请缩小框或提高最小面积。")
    window = labels[top:bottom, left:right]
    return Analysis(box, window, rows, group_grades(rows, options["holes"], crossing),
                    int(np.count_nonzero(window)), factor, crossing)


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
        labels = result.labels
        palette = np.zeros((int(labels.max()) + 1, 3), dtype=np.uint8)
        tinted = np.zeros(int(labels.max()) + 1, dtype=bool)
        for row in result.grains:
            if row["edge"]:
                color = (247, 112, 89)
            elif row["grade"] is not None and row.get("share", 1) > 0:
                color = COLORS[row["grade"]]
            else:
                color = (132, 139, 149)
            palette[row["label"]] = color
            tinted[row["label"]] = True
        present = tinted[labels]
        window[present] = palette[labels[present]]
    return Image.fromarray(output)
