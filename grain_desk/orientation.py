"""Directional spread of elongated grains inside one inspection box.

Grain long axes are undirected: the two ends of the major axis describe the
same direction, so angles live on a half circle in ``[0, 180)`` degrees.
Near-round grains carry no readable direction and are reported separately so
they never enter the angular statistics.
"""

from math import atan2, ceil, degrees, hypot, sqrt

import cv2

# Minor/major axis ratio at or above which a grain counts as nearly round.
ROUND_AXIS_RATIO = 0.85


def grain_orientation(mask):
    """Return ``(angle_degrees, axis_ratio)`` for one connected component.

    ``angle`` is the undirected major-axis angle folded onto [0, 180);
    ``axis_ratio`` is the minor/major standard-deviation ratio (1.0 = a circle).
    """
    moments = cv2.moments(mask, binaryImage=True)
    mu20, mu02, mu11 = moments["mu20"], moments["mu02"], moments["mu11"]
    common = mu20 + mu02
    spread = hypot((mu20 - mu02) / 2, mu11)
    major = common / 2 + spread
    minor = common / 2 - spread
    ratio = sqrt(minor / major) if major > 0 and minor >= 0 else 1.0
    angle = degrees(atan2(2 * mu11, mu20 - mu02) / 2) % 180.0
    return angle, min(1.0, ratio)


def narrowest_arc(angles, weights, target):
    """Narrowest arc whose weight reaches ``target`` on a 180-degree circle.

    ``angles`` and ``weights`` are parallel lists for oriented grains. The
    sorted sequence is traversed circularly, so an arc crossing the original
    0-degree seam stays contiguous. Returns ``(start, width, indices)`` with
    ``start`` folded to [0, 180) and ``indices`` the grains the arc actually
    contains. Ties prefer the arc covering more weight, then the smallest
    start angle.
    """
    order = sorted(range(len(angles)), key=lambda index: angles[index])
    size = len(order)

    def span(left, right):
        """Angular width of the window order[left..right] with wrap at 180."""
        first, last = order[left], order[right % size]
        if right < size:
            return angles[last] - angles[first]
        return angles[last] + 180.0 - angles[first]

    right = 0
    covered = 0.0
    while covered < target and right < size:
        covered += weights[order[right]]
        right += 1
    best = None
    for left in range(size):
        if right - left > size:
            break
        if covered >= target:
            first = order[left]
            indices = [order[k % size] for k in range(left, right)]
            candidate = (span(left, right - 1), -covered, angles[first], indices)
            if best is None or candidate[:3] < best[:3]:
                best = candidate
        covered -= weights[order[left]]
        while covered < target and right < left + size:
            covered += weights[order[right % size]]
            right += 1
    width, _, start, indices = best
    return start, width, indices


def in_arc(angle, start, width, tolerance=0.01):
    """Whether an undirected angle falls in an arc that may cross 0/180 degrees.

    ``tolerance`` absorbs rounding when the stored angles are shown at two
    decimals while arc geometry was computed at full precision.
    """
    end = start + width
    if end <= 180.0:
        return start - tolerance <= angle <= end + tolerance
    return angle >= start - tolerance or angle <= end - 180.0 + tolerance


def orientation_report(rows, labels, box):
    """Measure every component and build the two narrowest-covering arcs.

    One arc covers at least half of the oriented grains by count, the other at
    least half of their projected pixel area. Nearly round grains are listed
    separately and excluded from both statistics. Grains the operator already
    excluded as misrecognitions are measured but never enter the statistics.
    ``labels`` uses box-local coordinates, so each mask is cropped to the
    component bounds; central moments are translation-invariant.
    """
    left, top = box[0], box[1]
    oriented, round_grains = [], []
    for row in rows:
        x0, y0, x1, y1 = row["bounds"]
        mask = (labels[y0 - top:y1 - top, x0 - left:x1 - left] == row["label"]).astype("uint8")
        angle, ratio = grain_orientation(mask)
        row["angle_deg"] = round(angle, 2)
        row["axis_ratio"] = round(ratio, 3)
        if ratio >= ROUND_AXIS_RATIO:
            round_grains.append(row["id"])
        elif not row["excluded"]:
            oriented.append(row)

    report = {"oriented_total": len(oriented), "round_total": len(round_grains),
              "round_grains": round_grains, "round_axis_ratio": ROUND_AXIS_RATIO,
              "count_arc": None, "area_arc": None}
    if not oriented:
        return report

    angles = [row["angle_deg"] for row in oriented]
    areas = [float(row["area_px"]) for row in oriented]
    total_area = sum(areas)

    def describe(start, width, indices):
        members = [oriented[index]["id"] for index in indices]
        member_area = sum(int(oriented[index]["area_px"]) for index in indices)
        return {"start_deg": round(start, 2), "width_deg": round(width, 2),
                "grain_count": len(members), "area_px": member_area,
                "members": members}

    count_target = float(ceil(len(oriented) / 2))
    start, width, indices = narrowest_arc(angles, [1.0] * len(angles), count_target)
    report["count_arc"] = describe(start, width, indices)

    start, width, indices = narrowest_arc(angles, areas, total_area / 2)
    report["area_arc"] = describe(start, width, indices)
    return report
