"""Plate storage, settings validation, and immutable inspection snapshots."""

from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
import json
import re
import shutil
from threading import RLock
from uuid import uuid4

from PIL import Image
from werkzeug.exceptions import NotFound

from image_lab.validation import Conflict, Invalid

from . import images, trials

KEY = re.compile(r"[0-9a-f]{16}")
GRAIN = re.compile(r"[0-9]{1,7}:[0-9]{1,7}")


def text(value, fallback):
    return (str(value).strip()[:100] if value is not None else "") or fallback


def whole(value, low, high, title):
    if type(value) is not int or not low <= value <= high:
        raise Invalid(f"{title}须为 {low} 至 {high} 之间的整数。")
    return value


def positive(value, low, high, title):
    if type(value) not in (int, float) or not low <= value <= high or not isfinite(value):
        raise Invalid(f"{title}须在 {low} 至 {high} 之间。")
    return float(value)


def point(raw, width, height):
    if not isinstance(raw, list) or len(raw) != 2:
        raise Invalid("请在原片内选择一个点。")
    return [whole(raw[0], 0, width - 1, "横坐标"), whole(raw[1], 0, height - 1, "纵坐标")]


def adopted_trial(raw, holes, excluded):
    """Validate and normalise one stored square-hole trial.

    Returns None when it no longer belongs to these openings or this grain is
    excluded; such entries are dropped instead of blocking an unrelated save.
    Measurements are re-derived from the outline whenever the plate is analysed,
    so a stored angle or clearance never outranks the current pixels.
    """
    if not isinstance(raw, dict):
        raise Invalid("请选择一个实际摆法。")
    grain = raw.get("grain_id")
    if not isinstance(grain, str) or GRAIN.fullmatch(grain) is None:
        raise Invalid("试摆颗粒编号无效。")
    hole = positive(raw.get("hole"), .001, 10000, "试摆孔径")
    # Keep the adopted opening by value; if the list was renumbered but still
    # holds it, follow the opening to its new class instead of dropping the work.
    grade = next((number for number, opening in enumerate(holes)
                  if abs(opening - hole) <= trials.MM_TOLERANCE), None)
    if grade is None or grain in excluded:
        return None
    angle = whole(raw.get("angle"), -trials.ANGLE_LIMIT, trials.ANGLE_LIMIT, "试摆转角")
    clearance = positive(raw.get("clearance_mm"), 0, 10000, "试摆净余量")
    touching = raw.get("touching")
    if type(touching) is not bool:
        raise Invalid("试摆须标明是否贴边。")
    edges = raw.get("edges_mm")
    if not isinstance(edges, dict) or set(edges) != set(trials.SIDES):
        raise Invalid("试摆的各边净余量不完整。")
    kept = {name: round(max(0.0, positive(edges[name], 0, 10000, "孔边净余量")), 4)
            for name in trials.SIDES}
    entry = {"grain_id": grain, "grade": grade, "hole": hole, "angle": angle,
             "clearance_mm": clearance, "touching": touching, "edges_mm": kept}
    sides = raw.get("touching_edges")
    if sides is not None:
        if not isinstance(sides, list) or any(name not in trials.SIDES for name in sides):
            raise Invalid("试摆贴边位置无效。")
        entry["touching_edges"] = [name for name in trials.SIDES if name in sides]
    return entry


def chosen_trial(raw):
    """Validate a client's choice of grain and opening before recomputing it."""
    if not isinstance(raw, dict):
        raise Invalid("请选择一个实际摆法。")
    grain = raw.get("grain_id")
    if not isinstance(grain, str) or GRAIN.fullmatch(grain) is None:
        raise Invalid("试摆颗粒编号无效。")
    return grain, whole(raw.get("grade"), 0, 7, "试摆孔级")


def settings(record, updates):
    if not isinstance(updates, dict):
        raise Invalid("请发送有效的检查参数。")
    data = {**record["settings"], **updates}
    width, height = record["width"], record["height"]
    box = data["box"]
    if not isinstance(box, list) or len(box) != 4:
        raise Invalid("检查框需有四个边界坐标。")
    left = whole(box[0], 0, width - 1, "左边")
    top = whole(box[1], 0, height - 1, "上边")
    right = whole(box[2], left + 1, width, "右边")
    bottom = whole(box[3], top + 1, height, "下边")
    calibration = data["calibration"]
    if calibration is not None:
        if not isinstance(calibration, dict) or not isinstance(calibration.get("points"), list) or len(calibration["points"]) != 2:
            raise Invalid("请用两个原片端点和已知的毫米值标定。")
        ends = [point(p, width, height) for p in calibration["points"]]
        if ends[0] == ends[1]:
            raise Invalid("标尺两端须在不同位置。")
        calibration = {"points": ends, "mm": positive(calibration.get("mm"), .001, 10000, "标尺长度")}
    holes = data["holes"]
    if not isinstance(holes, list) or not 1 <= len(holes) <= 8:
        raise Invalid("请填写 1 至 8 个孔径。")
    holes = [positive(value, .001, 10000, "孔径") for value in holes]
    if any(a >= b for a, b in zip(holes, holes[1:])):
        raise Invalid("孔径须严格递增。")
    excluded = data["excluded"]
    if not isinstance(excluded, list) or len(excluded) > images.MAX_GRAINS:
        raise Invalid("排除列表超过检查上限。")
    if any(not isinstance(value, str) or GRAIN.fullmatch(value) is None for value in excluded):
        raise Invalid("排除列表的颗粒编号无效。")
    entries = data.get("trials") or []
    if not isinstance(entries, list) or len(entries) > images.MAX_GRAINS:
        raise Invalid("试摆记录超过检查上限。")
    unique = set(excluded)
    kept = {}
    for raw in entries:
        entry = adopted_trial(raw, holes, unique)
        if entry is None:
            continue
        if entry["grain_id"] in kept:
            raise Invalid("同一颗粒只能采用一个方筛孔级。")
        kept[entry["grain_id"]] = entry
    return {"box": [left, top, right, bottom], "calibration": calibration, "holes": holes,
            "threshold": whole(data["threshold"], 0, 255, "灰度阈值"),
            "min_area": whole(data["min_area"], 1, images.MAX_PIXELS, "最小像素面积"),
            "excluded": list(dict.fromkeys(excluded)),
            "trials": sorted(kept.values(), key=lambda entry: entry["grain_id"])}


def reconcile(entries, rows, holes):
    """Drop adopted trials whose grain is gone, ineligible, or no longer on that opening."""
    present = {row["id"]: row for row in rows}
    kept = []
    for entry in entries:
        row = present.get(entry["grain_id"])
        if row is None or row["edge"] or row["excluded"]:
            continue
        if entry["grade"] >= len(holes) or abs(holes[entry["grade"]] - entry["hole"]) > trials.MM_TOLERANCE:
            continue
        kept.append(entry)
    return kept


def applied_entries(record, entries):
    """Trials that still match a report snapshot's rows and openings."""
    rows = {row["id"]: row for row in record["results"]["grains"]}
    holes = record["settings"]["holes"]
    kept = {}
    for entry in entries:
        row = rows.get(entry["grain_id"])
        if row is None or row["edge"] or row["excluded"] or row["grade"] != entry["grade"]:
            continue
        if entry["grade"] >= len(holes):
            continue
        kept[row["id"]] = entry
    return kept


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def key(value):
    if not isinstance(value, str) or KEY.fullmatch(value) is None:
        raise NotFound("找不到这张原片或留样。")
    return value


def read(path):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        raise NotFound("找不到这张原片或留样。") from None


def write(path, data):
    scratch = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        scratch.write_bytes(data)
        scratch.replace(path)
    finally:
        scratch.unlink(missing_ok=True)


def save_json(path, payload):
    write(path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())


class Library:
    def __init__(self, root):
        self.root = Path(root) / "plates"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def directory(self, plate):
        return self.root / key(plate)

    def list(self):
        records = [read(path) for path in self.root.glob("*/plate.json")]
        return sorted(records, key=lambda row: (row["updated_at"], row["id"]), reverse=True)

    def create(self, image, title, filename, synthetic=False, origin=None):
        plate = uuid4().hex[:16]
        width, height = image.size
        options = {"box": [0, 0, width, height], "threshold": 110, "min_area": 36,
                   "calibration": None, "holes": [2, 4, 6, 8, 10], "excluded": [], "trials": []}
        if synthetic:
            options["box"] = [28, 65, 730, 393]
            options["calibration"] = {"points": [[80, 420], [180, 420]], "mm": 10}
        row = {"id": plate, "width": width, "height": height, "synthetic": synthetic, "origin": origin,
               "title": text(title, "未命名原片"), "filename": text(filename, "source.png"),
               "settings": options, "revision": 0, "updated_at": now()}
        directory = self.directory(plate)
        directory.mkdir()
        try:
            write(directory / "source.png", images.png(image))
            save_json(directory / "plate.json", row)
        except OSError:
            shutil.rmtree(directory)
            raise
        return row

    def plate(self, plate):
        return read(self.directory(plate) / "plate.json")

    def source(self, plate):
        path = self.directory(plate) / "source.png"
        if not path.is_file():
            raise NotFound("找不到原片。")
        return path

    def image(self, plate):
        with Image.open(self.source(plate)) as image:
            return image.copy()

    def current(self, plate, revision):
        row = self.plate(plate)
        if whole(revision, 0, 10 ** 9, "原片版本") != row["revision"]:
            raise Conflict("这张原片的设置已有变化，请重开后再保存。")
        return row

    def update(self, plate, revision, changes):
        with self.lock:
            row = self.current(plate, revision)
            chosen = settings(row, changes)
            row["settings"], _ = self._settled(plate, chosen)
            row["revision"] += 1
            row["updated_at"] = now()
            save_json(self.directory(plate) / "plate.json", row)
        return row

    def _settled(self, plate, chosen):
        """Recalculate, dropping adopted trials the current settings no longer uphold."""
        image = self.image(plate)
        result = images.analyze(image, chosen)
        kept = []
        for entry in reconcile(chosen["trials"], result.grains, chosen["holes"]):
            row = next(item for item in result.grains if item["id"] == entry["grain_id"])
            fresh = images.retry(image, chosen, entry, result, row)
            if fresh is not None and fresh["angle"] == entry["angle"] and \
                    abs(fresh["clearance_mm"] - entry["clearance_mm"]) <= trials.MM_TOLERANCE:
                kept.append(entry)
        if len(kept) != len(chosen["trials"]):
            chosen = {**chosen, "trials": kept}
            result = images.analyze(image, chosen)
        return chosen, result

    def adopt_trial(self, plate, revision, options, raw):
        grain_id, grade = chosen_trial(raw)
        with self.lock:
            record = self.current(plate, revision)
            chosen = settings(record, options)
            if grade >= len(chosen["holes"]):
                raise Invalid("试摆孔级不在当前孔径档位内，请重新试摆。")
            if grain_id in chosen["excluded"]:
                raise Invalid("已排除的颗粒不能采用方筛孔级。")
            image = self.image(plate)
            proposal = images.trial_grain(images.analyze(image, chosen), grain_id, chosen["holes"])
            target = proposal["openings"][grade]
            if not target["fit"]:
                raise Invalid("这个孔径容不下该粒，请另选孔级。")
            entry = {"grain_id": grain_id, "grade": grade, "hole": chosen["holes"][grade],
                     "angle": target["angle"], "clearance_mm": target["clearance_mm"],
                     "touching": target["touching"], "edges_mm": target["edges_mm"]}
            if target["touching"]:
                # The touching sides are part of the evidence the operator saw.
                entry["touching_edges"] = target["touching_edges"]
            rest = [item for item in chosen["trials"] if item["grain_id"] != grain_id]
            settled, result = self._settled(plate, {**chosen, "trials": [*rest, entry]})
            if not any(item["grain_id"] == grain_id for item in settled["trials"]):
                raise Invalid("采用后试摆不再成立，请重新试摆。")
            record["settings"] = settled
            record["revision"] += 1
            record["updated_at"] = now()
            save_json(self.directory(plate) / "plate.json", record)
        summary = result.report()
        summary["selected"] = grain_id
        return {"plate": record, "trial": entry, "result": summary}

    def clear_trial(self, plate, revision, options, grain_id):
        """Return one grain to its equal-area class without touching the rest."""
        if not isinstance(grain_id, str) or GRAIN.fullmatch(grain_id) is None:
            raise Invalid("试摆颗粒编号无效。")
        with self.lock:
            record = self.current(plate, revision)
            chosen = settings(record, options)
            rest = [item for item in chosen["trials"] if item["grain_id"] != grain_id]
            settled, result = self._settled(plate, {**chosen, "trials": rest})
            record["settings"] = settled
            record["revision"] += 1
            record["updated_at"] = now()
            save_json(self.directory(plate) / "plate.json", record)
        summary = result.report()
        summary["selected"] = grain_id
        return {"plate": record, "result": summary}

    def reports(self, plate):
        self.plate(plate)
        paths = self.directory(plate).glob("report-*.json")
        records = [read(path) for path in paths]
        return sorted([{"id": row["id"], "title": row["title"], "saved_at": row["saved_at"]}
                       for row in records], key=lambda row: row["saved_at"], reverse=True)

    def report(self, plate, report):
        return read(self.directory(plate) / f"report-{key(report)}.json")

    def applied_trials(self, plate, record):
        """Adopted trials that still match the saved snapshot's grains and openings."""
        return applied_entries(record, record["settings"].get("trials", []))

    def save_report(self, plate, revision, options, title):
        with self.lock:
            record = self.current(plate, revision)
            chosen, result = self._settled(plate, settings(record, options))
            snapshot = {"id": uuid4().hex[:16], "title": text(title, "图像筛级留样"),
                        "saved_at": now(), "settings": chosen, "results": result.report()}
            save_json(self.directory(plate) / f"report-{snapshot['id']}.json", snapshot)
        return snapshot
