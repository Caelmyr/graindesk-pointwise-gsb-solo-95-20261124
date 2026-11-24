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

from . import images

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
    return {"box": [left, top, right, bottom], "calibration": calibration, "holes": holes,
            "threshold": whole(data["threshold"], 0, 255, "灰度阈值"),
            "min_area": whole(data["min_area"], 1, images.MAX_PIXELS, "最小像素面积"),
            "excluded": list(dict.fromkeys(excluded))}


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
                   "calibration": None, "holes": [2, 4, 6, 8, 10], "excluded": []}
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
            row["settings"] = settings(row, changes)
            images.analyze(self.image(plate), row["settings"])
            row["revision"] += 1
            row["updated_at"] = now()
            save_json(self.directory(plate) / "plate.json", row)
        return row

    def reports(self, plate):
        self.plate(plate)
        paths = self.directory(plate).glob("report-*.json")
        records = [read(path) for path in paths]
        return sorted([{"id": row["id"], "title": row["title"], "saved_at": row["saved_at"]}
                       for row in records], key=lambda row: row["saved_at"], reverse=True)

    def report(self, plate, report):
        return read(self.directory(plate) / f"report-{key(report)}.json")

    def save_report(self, plate, revision, options, title):
        with self.lock:
            record = self.current(plate, revision)
            chosen = settings(record, options)
            result = images.analyze(self.image(plate), chosen)
            snapshot = {"id": uuid4().hex[:16], "title": text(title, "图像筛级留样"),
                        "saved_at": now(), "settings": chosen, "results": result.report()}
            save_json(self.directory(plate) / f"report-{snapshot['id']}.json", snapshot)
        return snapshot
