#!/usr/bin/env python3
"""Standalone checks for directional review (run: python3 -I tests/test_orientation.py)."""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), "/opt/pointwise-env/site-packages"]

from grain_desk import images, orientation  # noqa: E402

PASS, FAIL = 0, 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} {detail}")


def arc_ids(angles, weights, target):
    start, width, indices = orientation.narrowest_arc(angles, weights, target)
    return start, width, indices


print("narrowest arc: count target with seam at 0 degrees")
small = [2, 5, 176, 178, 4, 174]
big = [87, 93, 90]
angles = small + big
start, width, indices = arc_ids(angles, [1.0] * len(angles), math.ceil(len(angles) / 2))
check("wraps across the 0/180 seam", start == 176.0 and round(width, 6) == 9.0,
      f"start={start} width={width}")
check("seam arc contains the five small grains", len(indices) == 5, str(indices))
check("seam arc excludes the 90-degree cluster",
      all(angles[i] < 30 or angles[i] > 150 for i in indices))

print("narrowest arc: area target picks the large-grain cluster")
areas = [100.0] * 6 + [1000.0] * 3
start, width, indices = arc_ids(angles, areas, sum(areas) / 2)
check("area arc sits on the big cluster", start == 87.0 and round(width, 6) == 3.0,
      f"start={start} width={width}")
check("two big grains already reach half the projected area", len(indices) == 2, str(indices))
check("area arc only takes large grains", all(i >= 6 for i in indices))

print("narrowest arc: edge cases")
s, w, idx = arc_ids([42.0], [1.0], 1.0)
check("single grain is a zero-width arc", s == 42.0 and w == 0.0 and idx == [0])
s, w, idx = arc_ids([10.0, 10.0, 10.0, 10.0], [1.0] * 4, 2.0)
check("identical angles collapse to zero width", s == 10.0 and w == 0.0 and len(idx) == 2)
s, w, idx = arc_ids([0.0, 30.0, 60.0, 90.0, 120.0, 150.0], [1.0] * 6, 3.0)
check("uniform spread takes the smallest start", s == 0.0 and round(w, 6) == 60.0,
      f"start={s} width={w}")
check("arc never includes more grains than exist", len(idx) <= 6)

print("in_arc membership, including wrap")
check("inside normal arc", orientation.in_arc(20.0, 10.0, 30.0))
check("outside normal arc", not orientation.in_arc(45.0, 10.0, 30.0))
check("inside wrapping arc low side", orientation.in_arc(3.0, 174.0, 12.0))
check("inside wrapping arc high side", orientation.in_arc(178.0, 174.0, 12.0))
check("outside wrapping arc", not orientation.in_arc(90.0, 174.0, 12.0))
check("boundary angles count as inside", orientation.in_arc(174.0, 174.0, 12.0)
      and orientation.in_arc(6.0, 174.0, 12.0))

print("orientation measurement")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

mask = np.zeros((60, 60), dtype=np.uint8)
cv2.ellipse(mask, (30, 30), (24, 8), 30, 0, 360, 1, -1)
angle, ratio = orientation.grain_orientation(mask)
check("elongated grain is not treated as round", ratio < orientation.ROUND_AXIS_RATIO,
      f"ratio={ratio}")
check("major-axis angle recovered within two degrees", abs(angle - 30.0) <= 2.0,
      f"angle={angle}")
check("axis direction is undirected (folded under 180)", 0.0 <= angle < 180.0)

disc = np.zeros((60, 60), dtype=np.uint8)
cv2.circle(disc, (30, 30), 18, 1, -1)
_, round_ratio = orientation.grain_orientation(disc)
check("disc classified as nearly round", round_ratio >= orientation.ROUND_AXIS_RATIO,
      f"ratio={round_ratio}")

print("full pipeline on the synthetic plate")
image = images.demo()
options = {"box": [28, 65, 730, 393], "threshold": 110, "min_area": 36,
           "calibration": {"points": [[80, 420], [180, 420]], "mm": 10},
           "holes": [2, 4, 6, 8, 10], "excluded": []}
result = images.analyze(image, options)
report = result.report()
info = report["orientation"]
check("round grains are kept out of the oriented roster",
      info["oriented_total"] + info["round_total"] == report["total"])
check("each grain carries angle and ratio data",
      all("angle_deg" in row and "axis_ratio" in row for row in report["grains"]))
for kind in ("count_arc", "area_arc"):
    arc = info[kind]
    check(f"{kind} width stays on the half circle", 0.0 <= arc["width_deg"] <= 180.0)
    check(f"{kind} member list matches the reported count",
          arc["grain_count"] == len(arc["members"]))
    check(f"{kind} reported area equals its members' projected area",
          arc["area_px"] == sum(row["area_px"] for row in report["grains"]
                                if row["id"] in arc["members"]))
    check(f"{kind} members are all oriented (no round grain)",
          not (set(arc["members"]) & set(info["round_grains"])))
    for row in report["grains"]:
        if row["id"] in arc["members"]:
            check(f"{kind} membership agrees with in_arc for {row['id']}",
                  orientation.in_arc(row["angle_deg"], arc["start_deg"], arc["width_deg"]))
area_arc = info["area_arc"]
oriented_area = sum(row["area_px"] for row in report["grains"]
                    if row["id"] not in info["round_grains"])
check("area arc covers at least half the oriented projected area",
      2 * area_arc["area_px"] >= oriented_area)
count_arc = info["count_arc"]
check("count arc covers at least half the oriented grains by count",
      2 * count_arc["grain_count"] >= info["oriented_total"])

print("round grains disappear from statistics when everything is round")
labels = np.zeros((120, 120), dtype=np.int32)
cv2.circle(labels, (30, 30), 22, 1, -1)
cv2.circle(labels, (85, 85), 22, 2, -1)
rows = [{"id": "a", "label": 1, "area_px": 10, "bounds": [8, 8, 53, 53]},
        {"id": "b", "label": 2, "area_px": 10, "bounds": [63, 63, 108, 108]}]
empty = orientation.orientation_report(rows, labels, [0, 0, 120, 120])
check("no oriented grains yields null arcs", empty["count_arc"] is None
      and empty["area_arc"] is None and empty["oriented_total"] == 0)
check("round roster still lists every grain", sorted(empty["round_grains"]) == ["a", "b"])

print("adversarial arc inputs")
# weights summing exactly onto the boundary
s, w, idx = arc_ids([10.0, 50.0, 90.0], [1.0, 1.0, 1.0], 2.0)
check("two-of-three count picks the closest pair", w == 40.0 and idx == [0, 1], str(idx))
# tie in width: prefer the arc covering more weight, then smallest start
s, w, idx = arc_ids([0.0, 30.0, 60.0], [1.0, 5.0, 1.0], 1.0)
check("single-grain target takes the heaviest grain on a width tie", idx == [1], str(idx))
# every grain at the same angle, area target above any single grain
s, w, idx = arc_ids([25.0, 25.0, 25.0], [3.0, 3.0, 3.0], 5.0)
check("degenerate identical angles still reach the area target", w == 0.0 and len(idx) == 2)
# full 180-degree span needed
s, w, idx = arc_ids([0.0, 90.0, 179.9], [1.0, 1.0, 1.0], 3.0)
check("window never wraps a grain onto itself", len(idx) == 3 and len(set(idx)) == 3, str(idx))

print("orientation is invariant to 180-degree flips of the mask")
flipped = np.flipud(np.fliplr(mask))
angle2, ratio2 = orientation.grain_orientation(flipped.astype(np.uint8))
check("flipped elongated grain keeps the same undirected angle",
      min(abs(angle2 - angle), abs(angle2 - angle - 180.0)) <= 2.0, f"{angle} vs {angle2}")
check("flipped grain keeps the same ratio", abs(ratio2 - ratio) < 0.02)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
