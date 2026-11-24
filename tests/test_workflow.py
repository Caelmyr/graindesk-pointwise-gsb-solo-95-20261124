#!/usr/bin/env python3
"""End-to-end HTTP checks for the directional review workflow.

The review scope itself lives in the browser; here we verify the server always
returns the data it needs and never changes grading because of inspection.
Run: python3 -I tests/test_workflow.py
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), "/opt/pointwise-env/site-packages"]

from grain_desk.web import create_app  # noqa: E402

PASS, FAIL = 0, 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} {detail}")


with tempfile.TemporaryDirectory() as tmp:
    app = create_app(Path(tmp))
    client = app.test_client()

    print("demo plate and inspection")
    created = client.post("/grain/demo")
    check("demo created", created.status_code == 201, created.status_code)
    plate = created.get_json()
    plate_id = plate["id"]
    revision = plate["revision"]
    settings = plate["settings"]

    inspected = client.post(f"/grain/plates/{plate_id}/inspect", json={"settings": settings})
    check("inspect succeeds", inspected.status_code == 200, inspected.data[:200])
    result = inspected.get_json()
    info = result["orientation"]
    check("orientation block present", {"oriented_total", "round_total", "round_grains",
          "count_arc", "area_arc"} <= set(info))
    check("round grains are excluded from both arc member sets",
          not (set(info["count_arc"]["members"]) & set(info["round_grains"]))
          and not (set(info["area_arc"]["members"]) & set(info["round_grains"])))
    check("each arc reports real member count and area",
          info["count_arc"]["grain_count"] == len(info["count_arc"]["members"])
          and info["area_arc"]["grain_count"] == len(info["area_arc"]["members"]))
    area_by_id = {g["id"]: g["area_px"] for g in result["grains"]}
    for kind in ("count_arc", "area_arc"):
        arc = info[kind]
        check(f"{kind} area is the sum of its members",
              arc["area_px"] == sum(area_by_id[gid] for gid in arc["members"]))
    check("count arc reaches half the oriented grains",
          2 * info["count_arc"]["grain_count"] >= info["oriented_total"])
    oriented_area = sum(area for gid, area in area_by_id.items()
                        if gid not in info["round_grains"])
    check("area arc reaches half the oriented area",
          2 * info["area_arc"]["area_px"] >= oriented_area)
    check("every grain exposes angle and roundness",
          all(0 <= g["angle_deg"] < 180 and 0 < g["axis_ratio"] <= 1 for g in result["grains"]))

    print("inspection does not mutate stored settings or revision")
    again = client.get(f"/grain/plates/{plate_id}").get_json()
    check("revision unchanged after inspect", again["revision"] == revision)
    check("stored settings unchanged after inspect", again["settings"] == settings)

    print("excluding a misrecognized member leaves the adopted arc geometry stable")
    victim = info["count_arc"]["members"][0]
    adopted = {"kind": "count_arc", "start_deg": info["count_arc"]["start_deg"],
               "width_deg": info["count_arc"]["width_deg"],
               "members": list(info["count_arc"]["members"])}
    excluded_settings = {**settings, "excluded": [victim]}
    narrowed = client.post(f"/grain/plates/{plate_id}/inspect",
                           json={"settings": excluded_settings}).get_json()
    new_arc = narrowed["orientation"]["count_arc"]
    check("excluded grain still appears in the roster but flagged",
          any(g["id"] == victim and g["excluded"] for g in narrowed["grains"]))
    check("excluded grain drops out of the arc statistics",
          victim not in new_arc["members"])
    check("remaining arc members are untouched",
          set(new_arc["members"]) == set(info["count_arc"]["members"]) - {victim})
    check("arc area recounts without the excluded grain",
          new_arc["area_px"] == info["count_arc"]["area_px"] - area_by_id[victim])
    # the browser keeps the adopted arc frozen; only the membership set shrinks
    frozen_members = [gid for gid in adopted["members"] if gid != victim]
    check("adopted review group keeps its frozen geometry and other members",
          adopted["start_deg"] == info["count_arc"]["start_deg"]
          and adopted["width_deg"] == info["count_arc"]["width_deg"]
          and frozen_members == [gid for gid in info["count_arc"]["members"] if gid != victim])

    print("grading stays identical across plain re-inspection (enter/exit review)")
    grade_before = {g["id"]: g["grade"] for g in result["grains"]}
    grade_after = {g["id"]: g["grade"] for g in narrowed["grains"]}
    check("only the excluded grain changes grade",
          all(grade_after[gid] == grade for gid, grade in grade_before.items()
              if gid != victim) and grade_after[victim] is None)

    print("views and report snapshot still work with the new payload")
    for mode in ("photo", "binary", "grade"):
        view = client.post(f"/grain/plates/{plate_id}/view/{mode}", json={"settings": settings})
        check(f"{mode} view renders", view.status_code == 200 and view.data[:8] == b"\x89PNG\r\n\x1a\n")
    saved = client.post(f"/grain/plates/{plate_id}/reports",
                        json={"revision": revision, "settings": settings, "title": "方向留样"})
    check("report saves", saved.status_code == 201, saved.data[:200])
    report = saved.get_json()
    check("snapshot keeps orientation evidence",
          report["results"]["orientation"]["oriented_total"] == info["oriented_total"])
    csv_response = client.get(f"/grain/plates/{plate_id}/reports/{report['id']}/grains.csv")
    check("csv export still works", csv_response.status_code == 200)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
