"""Square-aperture trials against a grain's original outline.

The sieve opening is a square.  A trial rotates the grain outline and centres
its outer-edge bounding box in the opening, so both opposite sides always share
the same angle and gap.  Angles are whole degrees, counted counter-clockwise as
seen on screen, with the photographed orientation at 0°.

Contour points lie on boundary pixel centres.  A boundary pixel is itself a
square, so the physical outline is half a pixel outside each point; after a
rotation by θ that support is (|cos θ| + |sin θ|) / 2 pixels per side.  The
convex hull is used because only the outline's extreme envelope can decide
whether an opening admits the grain.
"""

from math import cos, radians, sin

import cv2
import numpy as np

ANGLE_LIMIT = 45
MM_TOLERANCE = 1e-6
EPS = 1e-9
SIDES = ("left", "right", "top", "bottom")
# Gaps are printed to 0.01 mm; anything at that precision is an edge contact for
# the operator to see, not hidden room.
TOUCH_MM = 0.005


def outline(labels, box, row):
    """Convex outline of a labelled grain, through boundary pixel centres."""
    left, top, _, _ = box
    x0, y0, x1, y1 = row["bounds"]
    window = labels[y0 - top:y1 - top, x0 - left:x1 - left]
    mask = np.ascontiguousarray(window == row["label"], dtype=np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
    contour += np.float32((x0, y0))
    hull = cv2.convexHull(contour)
    return hull.reshape(-1, 2).astype(np.float64)


def angles():
    """Whole-degree rotations, nearest the photographed orientation first."""
    yield 0
    for value in range(1, ANGLE_LIMIT + 1):
        yield value
        yield -value


def _rotate(points, angle):
    radians_value = radians(angle)
    cosine, sine = cos(radians_value), sin(radians_value)
    return (cosine * points[:, 0] + sine * points[:, 1],
            -sine * points[:, 0] + cosine * points[:, 1])


def _extents(points, angle):
    """Outer-edge bounds of the rotated outline, in pixel coordinates."""
    x, y = _rotate(points, angle)
    support = (abs(cos(radians(angle))) + abs(sin(radians(angle)))) / 2
    return x.min() - support, x.max() + support, y.min() - support, y.max() + support, x, y


def _gaps(left, right, top, bottom, hole, factor):
    """Distance from the centred outline's outer edge to each opening side, in mm.

    Rotation is about the origin, so after centring the outline's bounding box
    in the opening both gaps of an opposing pair are equal.  Both sides are
    therefore always decided at this one angle, never as separate fits.
    """
    centre_x, centre_y = (left + right) / 2, (top + bottom) / 2
    half = hole / 2
    return {"left": half - (centre_x - left) * factor,
            "right": half - (right - centre_x) * factor,
            "top": half - (centre_y - top) * factor,
            "bottom": half - (bottom - centre_y) * factor}


def place(points, factor, hole, angle):
    """A centred placement at one angle, or None when the opening is too small."""
    left, right, top, bottom, x, y = _extents(points, angle)
    width = round(float(right - left) * factor, 4)
    height = round(float(bottom - top) * factor, 4)
    if max(width, height) > hole + EPS:
        return None
    gaps = _gaps(left, right, top, bottom, hole, factor)
    gaps = {name: round(max(0.0, float(value)), 4) for name, value in gaps.items()}
    touching_edges = [name for name in SIDES if gaps[name] <= TOUCH_MM]
    centre_x, centre_y = (left + right) / 2, (top + bottom) / 2
    return {"hole": hole, "angle": angle, "fit": True,
            "width_mm": width, "height_mm": height,
            "clearance_mm": min(gaps.values()), "touching": bool(touching_edges),
            "edges_mm": gaps, "touching_edges": touching_edges,
            "outline": [[round(float((px - centre_x) * factor), 4),
                         round(float((py - centre_y) * factor), 4)] for px, py in zip(x, y)]}


def best_fit(points, factor, hole):
    """Smallest clearance at the closest-to-original fitting angle, or None."""
    for angle in angles():
        metrics = place(points, factor, hole, angle)
        if metrics is not None:
            return metrics
    return None


def envelope(points, factor):
    """Smallest square opening, over all angles, that could hold the outline."""
    closest = None
    for angle in angles():
        left, right, top, bottom, _, _ = _extents(points, angle)
        largest = float(max(right - left, bottom - top)) * factor
        if closest is None or largest < closest[0]:
            closest = (largest, angle, left, right, top, bottom)
    return closest


def candidate(points, factor, hole):
    """The placement nearest the photographed orientation in one opening."""
    metrics = best_fit(points, factor, hole)
    if metrics is not None:
        return metrics
    required, angle, left, right, top, bottom = envelope(points, factor)
    x, y = _rotate(points, angle)
    centre_x, centre_y = (left + right) / 2, (top + bottom) / 2
    return {"hole": hole, "fit": False, "angle": angle,
            "required_mm": round(float(required), 4), "required_angle": angle,
            "width_mm": round(float(right - left) * factor, 4),
            "height_mm": round(float(bottom - top) * factor, 4),
            "outline": [[round(float((px - centre_x) * factor), 4),
                         round(float((py - centre_y) * factor), 4)] for px, py in zip(x, y)]}


def placements(points, factor, holes):
    return [{**candidate(points, factor, hole), "grade": grade}
            for grade, hole in enumerate(holes)]


def eligible(row, factor):
    """Return a reason when a grain cannot be trialled, otherwise None."""
    if factor is None:
        return "这张原片尚未标定，不能试方筛孔。"
    if row["edge"]:
        return "该粒被检查框截断，轮廓不完整。"
    if row["excluded"]:
        return "该粒已排除，不在筛级汇总内。"
    if row["grade"] is None:
        return "该粒尚未计入筛级汇总。"
    return None
