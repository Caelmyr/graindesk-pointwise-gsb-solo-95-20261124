"""HTTP entry points for the grading bench and immutable evidence downloads."""

import csv
from io import BytesIO, StringIO
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

from image_lab import create_app as create_preparation
from image_lab.validation import Invalid

from . import images, state


def body():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise Invalid("请发送 JSON 检查参数。")
    return payload


def png_file(data, name, download=False):
    return send_file(data, mimetype="image/png", download_name=name,
                     as_attachment=download, max_age=0)


def create_app(root):
    app = create_preparation(root / "prepare")
    library = state.Library(root)
    desk = Blueprint("grain", __name__, url_prefix="/grain")

    @app.get("/")
    def home():
        return render_template("graindesk.html")

    @desk.get("/health")
    def health():
        return jsonify(status="ready", service="graindesk")

    @desk.get("/plates")
    def plates():
        return jsonify(library.list())

    @desk.post("/demo")
    def sample():
        return jsonify(library.create(images.demo(), "河湾 · 合成铺粒", "estuary-grains.png", synthetic=True)), 201

    @desk.post("/plates")
    def upload():
        file = request.files.get("image")
        if file is None or not file.filename:
            raise Invalid("选择一张铺粒原片。")
        image = images.decode(file.stream.read(images.MAX_UPLOAD + 1))
        return jsonify(library.create(image, request.form.get("title") or Path(file.filename).stem,
                                      Path(file.filename).name)), 201

    @desk.post("/plates/prepared")
    def prepared():
        payload = body()
        project, result = payload.get("project"), payload.get("result")
        if not isinstance(project, str) or not isinstance(result, str):
            raise Invalid("请选择可用的照片准备结果。")
        preparation = app.extensions["results"]
        item = preparation.metadata(project, result)
        if item["stale"]:
            raise Invalid("请先重新计算准备结果，再送入工作台。")
        source = preparation.image_path(project, "image", result)
        with source.open("rb") as stream:
            image = images.decode(stream.read(images.MAX_UPLOAD + 1))
        return jsonify(library.create(image, "照片准备 · 检查原片", "prepared.png",
                                      origin={"project": project, "result": result})), 201

    @desk.get("/plates/<plate>")
    def open_plate(plate):
        return jsonify(library.plate(plate))

    @desk.put("/plates/<plate>/settings")
    def save_settings(plate):
        value = body()
        return jsonify(library.update(plate, value.get("revision"), value.get("settings")))

    @desk.get("/plates/<plate>/source.png")
    def source_png(plate):
        record = library.plate(plate)
        return png_file(library.source(plate), f"{record['id']}-source.png", "download" in request.args)

    @desk.post("/plates/<plate>/inspect")
    def inspect(plate):
        record = library.plate(plate)
        payload = body()
        options = state.settings(record, payload.get("settings", {}))
        result = images.analyze(library.image(plate), options)
        summary = result.report()
        if payload.get("point") is not None:
            point = state.point(payload["point"], record["width"], record["height"])
            summary["selected"] = result.grain_at(point)
        return jsonify(summary)

    @desk.post("/plates/<plate>/trial")
    def trial(plate):
        record = library.plate(plate)
        payload = body()
        options = state.settings(record, payload.get("settings", {}))
        grain_id = payload.get("grain_id")
        if not isinstance(grain_id, str) or not grain_id:
            raise Invalid("请先点选一粒颗粒。")
        result = images.analyze(library.image(plate), options)
        return jsonify(images.trial_grain(result, grain_id, options["holes"]))

    @desk.post("/plates/<plate>/trial/apply")
    def apply_trial(plate):
        payload = body()
        return jsonify(library.adopt_trial(plate, payload.get("revision"),
                                           payload.get("settings"), payload.get("trial")))

    @desk.post("/plates/<plate>/trial/clear")
    def clear_trial(plate):
        payload = body()
        return jsonify(library.clear_trial(plate, payload.get("revision"),
                                           payload.get("settings"), payload.get("grain_id")))

    @desk.post("/plates/<plate>/view/<mode>")
    def view(plate, mode):
        if mode not in images.VIEWS:
            raise Invalid("请选择原片、二值或分档视图。")
        record = library.plate(plate)
        options = state.settings(record, body().get("settings", {}))
        image = library.image(plate)
        rendered = images.render(image, images.analyze(image, options), mode)
        return png_file(BytesIO(images.png(rendered)), f"{mode}.png")

    @desk.get("/plates/<plate>/reports")
    def reports(plate):
        return jsonify(library.reports(plate))

    @desk.post("/plates/<plate>/reports")
    def save_report(plate):
        payload = body()
        return jsonify(library.save_report(plate, payload.get("revision"), payload.get("settings"),
                                           payload.get("title"))), 201

    @desk.get("/plates/<plate>/reports/<report>")
    def report_record(plate, report):
        return jsonify(library.report(plate, report))

    @desk.get("/plates/<plate>/reports/<report>/grade.png")
    def grade_png(plate, report):
        options = library.report(plate, report)["settings"]
        image = library.image(plate)
        rendered = images.render(image, images.analyze(image, options), "grade")
        return png_file(BytesIO(images.png(rendered)), "grain-grade.png", "download" in request.args)

    @desk.get("/plates/<plate>/reports/<report>/grains.csv")
    def grains_csv(plate, report):
        record = library.report(plate, report)
        applied = library.applied_trials(plate, record)
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["grain_id", "x_px", "y_px", "area_px", "equivalent_mm", "class_upper_mm",
                         "status", "trial_angle_deg", "trial_clearance_mm", "trial_touching"])
        for row in record["results"]["grains"]:
            index = row["grade"]
            hole = record["results"]["grades"][index]["hole"] if index is not None else None
            status = "edge" if row["edge"] else "excluded" if row["excluded"] else "unscaled" if index is None else "counted"
            if index is not None and hole is None:
                hole = "oversize"
            size = row["equivalent_mm"]
            trial = applied.get(row["id"])
            writer.writerow([row["id"], *row["center"], row["area_px"],
                             f"{size:.6f}" if size is not None else "",
                             hole if hole is not None else "", status,
                             trial["angle"] if trial else "",
                             f"{trial['clearance_mm']:.4f}" if trial else "",
                             "yes" if trial and trial["touching"] else "no" if trial else ""])
        return send_file(BytesIO(output.getvalue().encode("utf-8-sig")), mimetype="text/csv",
                         download_name="grain-grades.csv", as_attachment=True, max_age=0)

    app.register_blueprint(desk)
    return app
