"use strict";

const el = id => document.getElementById(id);
const modes = [...document.querySelectorAll("[data-mode]")];
const editControls = ["threshold", "minimum", "length", "holes", "apply", "settings", "box", "full", "calibrate", "saveReport"];
const state = {plate: null, settings: null, result: null, report: null, mode: "grade", tool: "pick",
               start: null, selected: null, url: null, trial: null, trialGrade: null};
let busy = false;

function status(message, error = false) {
  el("status").textContent = message;
  el("status").classList.toggle("error", error);
}

function handle(fn) {
  return async event => {
    if (busy) return;
    busy = true;
    try {
      await fn(event);
    } catch (error) {
      status(error.message, true);
    } finally {
      busy = false;
    }
  };
}

async function api(path, method = "GET", data) {
  const options = {method};
  if (data instanceof FormData) {
    options.body = data;
  } else if (data !== undefined) {
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(data);
  }
  const response = await fetch("/grain" + path, options);
  const json = response.headers.get("Content-Type")?.startsWith("application/json");
  if (!response.ok) {
    const cause = json ? (await response.json()).error : response.statusText;
    throw Error(cause || "请求未成功，请重试。");
  }
  return json ? response.json() : response.blob();
}

function option(value, label) {
  return new Option(label, value);
}

function platePath(suffix = "") {
  return "/plates/" + state.plate.id + suffix;
}

function reportPath(suffix = "") {
  return platePath("/reports/" + state.report.id + suffix);
}

function setTool(tool) {
  state.tool = tool;
  state.start = null;
  el("cursor").textContent = {pick: "点选检查框内的颗粒", box: "点检查框的一角",
                              calibrate: "点标尺的第一端"}[tool];
  paintOverlay();
}

async function loadPlates() {
  const rows = await api("/plates");
  el("plates").replaceChildren(option("", "选择原片"), ...rows.map(row => option(row.id, row.title)));
  if (state.plate) el("plates").value = state.plate.id;
  return rows;
}

async function loadReports() {
  const reports = await api(platePath("/reports"));
  el("reports").replaceChildren(option("", "当前检查"), ...reports.map(row => option(row.id, row.title)));
  el("reports").value = state.report?.id || "";
}

function loadControls() {
  const options = state.settings;
  el("threshold").value = options.threshold;
  el("minimum").value = options.min_area;
  el("holes").value = options.holes.join(", ");
  el("length").value = options.calibration?.mm ?? "";
  el("resolution").textContent = state.plate.width + " × " + state.plate.height + " px";
  el("raw").href = "/grain" + platePath("/source.png?download");
  el("imageReport").hidden = !state.report;
  el("csvReport").hidden = !state.report;
  if (state.report) {
    el("imageReport").href = "/grain" + reportPath("/grade.png?download");
    el("csvReport").href = "/grain" + reportPath("/grains.csv");
  }
  for (const id of editControls) el(id).disabled = Boolean(state.report);
  el("runTrial").disabled = Boolean(state.report);
}

function trialEligibility(row) {
  if (!row) return "先点选一粒颗粒。";
  if (!state.result || state.result.mm_per_px === null) return "这张原片尚未标定，不能试方筛孔。";
  if (row.edge) return "该粒被检查框截断，轮廓不完整。";
  if (row.excluded) return "该粒已排除，不在筛级汇总内。";
  if (row.grade === null) return "该粒尚未计入筛级汇总。";
  return null;
}

function fieldNumber(id) {
  const field = el(id);
  if (!field.value.trim() || !field.checkValidity()) throw Error("请检查「" + field.labels[0].textContent + "」的值。");
  return field.valueAsNumber;
}

function readControls() {
  const options = state.settings;
  const threshold = fieldNumber("threshold");
  const min_area = fieldNumber("minimum");
  const holes = el("holes").value.split(/[,，]/).map(value => value.trim() ? Number(value) : NaN);
  if (holes.some(value => !Number.isFinite(value) || value <= 0)) throw Error("请用逗号分隔有效孔径。");
  const stale = threshold !== options.threshold || min_area !== options.min_area ||
    holes.join() !== options.holes.join();
  return {...options, threshold, min_area, holes,
          excluded: stale ? [] : options.excluded,
          trials: stale ? [] : (options.trials || [])};
}

function currentRow() {
  return state.result?.grains.find(row => row.id === state.selected);
}

function gradeLabel(row) {
  if (row.edge) return "框缘截断";
  if (row.excluded) return "已排除";
  if (row.grade === null) return "待标定";
  const opening = state.result.grades[row.grade].hole;
  const base = opening === null ? "超最大档" : "≤ " + opening + " mm";
  return row.trial ? base + " · 方筛" : base;
}

function sizeLabel(value) {
  return value === null ? "—" : value.toFixed(2) + " mm";
}

const SIDE_NAMES = {left: "左", right: "右", top: "上", bottom: "下"};

function sidesLabel(names) {
  return names.map(name => SIDE_NAMES[name] || name).join(" / ");
}

function touchingSides(item) {
  if (item.touching_edges?.length) return item.touching_edges;
  const edges = item.edges_mm || {};
  return Object.keys(SIDE_NAMES).filter(name => (edges[name] ?? 1) <= 0.005);
}

function appliedSummary(row) {
  const trial = row?.trial;
  if (trial) {
    const sides = touchingSides(trial);
    return "已采用：孔径 " + trial.hole + " mm · 转角 " + trial.angle + "° · 最近孔边余量 " +
      trial.clearance_mm.toFixed(2) + " mm" + (sides.length ? " · 贴边（" + sidesLabel(sides) + "）" : "") +
      (state.report ? "（留样核对）" : "");
  }
  if (!state.report) return "";
  const adopted = state.result.grains.filter(grain => grain.trial);
  const head = row ? "该粒在这份留样中按等面积圆径分档。" : "";
  if (!adopted.length) return head + "这份留样没有采用方筛孔级。";
  return head + "留样采用 " + adopted.length + " 粒方筛孔级：" + adopted.map(grain => {
    const sides = touchingSides(grain.trial);
    return grain.id + " · " + grain.trial.hole + " mm / " + grain.trial.angle + "° / 余 " +
      grain.trial.clearance_mm.toFixed(2) + " mm" + (sides.length ? " 贴" + sidesLabel(sides) + "边" : "");
  }).join("；");
}

function trialRowText(item) {
  if (!item.fit) {
    return "容不下 · 最小需 " + item.required_mm.toFixed(2) + " mm（" + item.required_angle + "°）";
  }
  return item.angle + "° · 贴边 " + (item.touching ? "是（" + sidesLabel(touchingSides(item)) + "）" : "否");
}

function renderData() {
  const result = state.result;
  const row = currentRow();
  el("counts").textContent = "计入 " + result.counted + " / " + result.total + " 粒";
  el("calibration").textContent = result.mm_per_px === null ?
    "未标定。仍可检查像素面积与轮廓。" : result.mm_per_px.toPrecision(4) + " mm / px";
  el("probe").textContent = row ? "粒 " + row.id + " · " + row.area_px + " px² · " + sizeLabel(row.equivalent_mm) +
    " · " + gradeLabel(row) : "点图或列表可定位颗粒。";
  el("exclude").textContent = row?.excluded ? "恢复计入" : "排除该粒";
  el("exclude").disabled = !row || row.edge || Boolean(state.report);
  el("runTrial").disabled = Boolean(state.report) || Boolean(trialEligibility(row));
  el("trialHint").textContent = state.report ? "留样只读；下方列出已采用的方筛摆法。" :
    (trialEligibility(row) || "点「按当前孔径试摆」查看各孔级的实际摆法。");
  renderTrial(row);
  const summary = result.grades.map(grade => {
    const chip = document.createElement("div");
    chip.className = "grade-chip";
    const swatch = document.createElement("i");
    swatch.className = "swatch";
    swatch.style.background = grade.color;
    const count = document.createElement("b");
    count.textContent = grade.count + " 粒" + (grade.doubled ? " · 方筛 " + grade.doubled : "");
    chip.append(swatch, grade.hole === null ? "超最大档" : "≤ " + grade.hole + " mm", count);
    return chip;
  });
  el("summary").replaceChildren(...summary);
  const body = result.grains.map(grain => {
    const tr = document.createElement("tr");
    const label = document.createElement("button");
    label.textContent = grain.id;
    label.onclick = () => selectGrain(grain.id);
    tr.insertCell().append(label);
    for (const value of [grain.area_px, sizeLabel(grain.equivalent_mm), gradeLabel(grain)]) {
      tr.insertCell().textContent = value;
    }
    const trial = grain.trial;
    tr.insertCell().textContent = trial ?
      trial.hole + " mm · " + trial.angle + "° · 余 " + trial.clearance_mm.toFixed(2) : "—";
    tr.classList.toggle("active-row", grain.id === state.selected);
    return tr;
  });
  el("rows").replaceChildren(...body);
  paintOverlay();
}

function renderTrial(row) {
  const trial = state.trial;
  const box = el("trialResult");
  el("appliedTrial").textContent = appliedSummary(row);
  el("appliedActions").hidden = !row?.trial || Boolean(state.report);
  if (!trial || trial.grain_id !== state.selected) {
    box.hidden = true;
    state.trialGrade = null;
    return;
  }
  box.hidden = false;
  const chosen = trial.openings.find(item => item.grade === state.trialGrade);
  const pair = trial.tightest && trial.roomy ?
    "；紧档 " + trial.tightest.hole + " mm 需转 " + trial.tightest.angle + "°，大档 " +
    trial.roomy.hole + " mm 为 " + trial.roomy.angle + "°" : "";
  el("trialReadout").textContent = "粒 " + trial.grain_id + " · 等效直径 " + trial.equivalent_mm.toFixed(2) +
    " mm · 原向 0°" + pair + "；采用后只重分这一粒，其余照旧。";
  el("trialRows").replaceChildren(...trial.openings.map(item => {
    const tr = document.createElement("tr");
    const button = document.createElement("button");
    const tag = trial.roomy && trial.tightest?.grade === item.grade ? " · 紧" :
      trial.roomy?.grade === item.grade ? " · 宽" : "";
    button.textContent = item.grade + 1 + " 档" + tag;
    button.onclick = () => { state.trialGrade = item.grade; renderTrial(row); };
    tr.insertCell().append(button);
    tr.insertCell().textContent = item.hole + " mm";
    tr.insertCell().textContent = trialRowText(item);
    tr.insertCell().textContent = item.fit ? item.clearance_mm.toFixed(3) + " mm" : "—";
    tr.classList.toggle("active-row", item.grade === state.trialGrade);
    return tr;
  }));
  el("adoptTrial").disabled = !chosen?.fit || Boolean(state.report);
  el("adoptTrial").textContent = chosen?.fit ? "采用 " + chosen.hole + " mm · " + chosen.angle + "°" : "采用该孔级";
  paintStages(trial);
}

function paintStages(trial) {
  const host = el("trialStages");
  const grades = [trial.tightest?.grade ?? null, trial.roomy?.grade ?? null];
  const unique = [...new Set(grades.filter(grade => grade !== null))];
  if (!unique.length) unique.push(state.trialGrade);
  host.replaceChildren(...unique.map(grade => {
    const wrap = document.createElement("div");
    wrap.className = "trial-stage-wrap" + (grade === state.trialGrade ? " active" : "");
    const canvas = document.createElement("canvas");
    canvas.width = 240; canvas.height = 240;
    canvas.style.width = canvas.style.height = "240px";
    canvas.setAttribute("aria-label", "方筛孔摆法");
    canvas.onclick = () => { state.trialGrade = grade; renderTrial(currentRow()); };
    wrap.append(canvas);
    const item = trial.openings.find(entry => entry.grade === grade);
    const caption = document.createElement("small");
    caption.textContent = item.fit ?
      (grade === trial.tightest?.grade ? "紧档 " : "大档 ") + item.hole + " mm · " + item.angle + "° · 余 " +
        item.clearance_mm.toFixed(2) + " mm" + (item.touching ? " · 贴边" : "") :
      item.hole + " mm · 容不下";
    wrap.append(caption);
    paintStage(canvas, trial, item);
    return wrap;
  }));
}

function paintStage(canvas, trial, item) {
  const size = 240;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = canvas.height = size * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, size, size);
  const holes = trial.openings.map(entry => entry.hole);
  const span = Math.max(...holes.map(hole => hole * 1.1));
  const centre = size / 2, scaleTo = (size - 34) / span;
  const drawHole = (hole, stroke, dash) => {
    ctx.save();
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 1.5;
    ctx.setLineDash(dash || []);
    const side = hole * scaleTo;
    ctx.strokeRect(centre - side / 2, centre - side / 2, side, side);
    ctx.restore();
  };
  if (!item) return;
  drawHole(item.hole, "#99e5ce");
  // Faint reference across the opening at the photographed 0° orientation.
  ctx.save();
  ctx.strokeStyle = "#4f6b66";
  ctx.setLineDash([2, 4]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(centre - item.hole * scaleTo / 2, centre);
  ctx.lineTo(centre + item.hole * scaleTo / 2, centre);
  ctx.stroke();
  ctx.restore();
  const outline = item.outline.map(([x, y]) => [centre + x * scaleTo, centre + y * scaleTo]);
  ctx.beginPath();
  ctx.moveTo(...outline[0]);
  for (const point of outline.slice(1)) ctx.lineTo(...point);
  ctx.closePath();
  ctx.fillStyle = item.fit ? "rgba(106,174,253,.28)" : "rgba(247,112,89,.25)";
  ctx.strokeStyle = item.fit ? "#6aaefd" : "#f77059";
  ctx.lineWidth = 2;
  ctx.fill();
  ctx.stroke();
  if (item.fit && item.touching) {
    const half = item.hole * scaleTo / 2;
    const spots = {left: [centre - half, centre], right: [centre + half, centre],
                   top: [centre, centre - half], bottom: [centre, centre + half]};
    ctx.fillStyle = "#ffcf72";
    for (const name of item.touching_edges) {
      ctx.beginPath();
      ctx.arc(...spots[name], 4, 0, 2 * Math.PI);
      ctx.fill();
    }
  }
  ctx.fillStyle = "#f0f4ef";
  ctx.font = "11px system-ui";
  ctx.fillText(item.hole + " mm · " + (item.fit ? item.angle + "°" : "容不下"), 8, 16);
}

function drawBox(ctx, box, color, thickness) {
  const [x0, y0, x1, y1] = box;
  ctx.strokeStyle = color;
  ctx.lineWidth = thickness;
  ctx.strokeRect(x0 + .5, y0 + .5, x1 - x0 - 1, y1 - y0 - 1);
}

function paintOverlay() {
  if (!state.plate) return;
  const canvas = el("overlay");
  const ctx = canvas.getContext("2d");
  canvas.width = state.plate.width;
  canvas.height = state.plate.height;
  drawBox(ctx, state.settings.box, "#b8f1df", 2);
  const row = currentRow();
  if (row) drawBox(ctx, row.bounds, "#ffffff", 3);
  if (state.settings.calibration) {
    const [a, b] = state.settings.calibration.points;
    ctx.strokeStyle = "#9bd4ff";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
  }
  if (state.start) {
    ctx.fillStyle = "#ffcf72";
    ctx.beginPath();
    ctx.arc(state.start[0], state.start[1], 5, 0, 2 * Math.PI);
    ctx.fill();
  }
}

async function redraw() {
  const blob = state.report && state.mode === "grade" ?
    await api(reportPath("/grade.png")) : await api(platePath("/view/" + state.mode), "POST", {settings: state.settings});
  const url = URL.createObjectURL(blob);
  const image = el("preview");
  image.src = url;
  if (state.url) URL.revokeObjectURL(state.url);
  state.url = url;
  image.alt = {photo: "骨料原片", binary: "二值铺粒图", grade: "颗粒分档图"}[state.mode];
  for (const button of modes) button.setAttribute("aria-pressed", String(state.mode === button.dataset.mode));
}

async function recalc(options) {
  const result = await api(platePath("/inspect"), "POST", {settings: options});
  state.settings = options;
  state.result = result;
  if (!currentRow()) state.selected = null;
  state.trial = null;
  state.trialGrade = null;
  await redraw();
  renderData();
}

async function openPlate(id) {
  if (!id) return;
  state.plate = await api("/plates/" + id);
  state.report = null;
  state.selected = null;
  state.trial = null;
  state.trialGrade = null;
  state.mode = "grade";
  state.settings = state.plate.settings;
  setTool("pick");
  el("plates").value = id;
  el("workbench").hidden = false;
  el("reports").disabled = false;
  loadControls();
  await Promise.all([loadReports(), recalc(state.settings)]);
  status(state.plate.synthetic ? "已打开合成练习。" : "已打开原片。");
}

async function openReport(id) {
  if (!id) {
    await openPlate(state.plate.id);
    return;
  }
  state.report = await api(platePath("/reports/" + id));
  state.settings = state.report.settings;
  state.result = state.report.results;
  state.selected = null;
  state.trial = null;
  state.trialGrade = null;
  state.mode = "grade";
  setTool("pick");
  loadControls();
  await redraw();
  renderData();
  status("已打开保存的留样。");
}

function selectGrain(id) {
  state.selected = id;
  state.trial = null;
  state.trialGrade = null;
  renderData();
}

function canvasPoint(event) {
  const rect = el("overlay").getBoundingClientRect();
  const size = [state.plate.width, state.plate.height];
  return [[event.clientX - rect.left, rect.width], [event.clientY - rect.top, rect.height]].map(
    ([offset, screen], index) => Math.max(0, Math.min(size[index] - 1, Math.floor(offset / screen * size[index]))));
}

el("overlay").onclick = handle(async event => {
  const point = canvasPoint(event);
  if (state.tool === "pick") {
    const result = await api(platePath("/inspect"), "POST", {settings: state.settings, point});
    selectGrain(result.selected);
    status(result.selected ? "已定位颗粒。" : "该位置无可复核颗粒。");
    return;
  }
  const first = state.start;
  if (!first) {
    state.start = point;
    paintOverlay();
    el("cursor").textContent = state.tool === "box" ? "点检查框的对角" : "点标尺的第二端";
    return;
  }
  if (state.tool === "box") {
    const box = [Math.min(first[0], point[0]), Math.min(first[1], point[1]),
                 Math.max(first[0], point[0]) + 1, Math.max(first[1], point[1]) + 1];
    await recalc({...readControls(), box, excluded: []});
    status("已重算检查框。");
  } else {
    await recalc({...readControls(), calibration: {points: [first, point], mm: fieldNumber("length")}});
    status("标尺已就位。");
  }
  setTool("pick");
});

el("demo").onclick = handle(async () => {
  const plate = await api("/demo", "POST");
  await loadPlates();
  await openPlate(plate.id);
});
el("import").onclick = () => el("file").click();
el("file").onchange = handle(async event => {
  const file = event.target.files[0];
  if (!file) return;
  const data = new FormData();
  data.set("image", file);
  event.target.value = "";
  const plate = await api("/plates", "POST", data);
  await loadPlates();
  await openPlate(plate.id);
});
el("plates").onchange = handle(async event => {
  if (event.target.value) await openPlate(event.target.value);
  else if (state.plate) event.target.value = state.plate.id;
});
el("reports").onchange = handle(event => openReport(event.target.value));
el("pick").onclick = () => setTool("pick");
el("box").onclick = () => setTool("box");
el("calibrate").onclick = handle(async () => {
  fieldNumber("length");
  setTool("calibrate");
});
el("full").onclick = handle(async () => {
  await recalc({...readControls(), box: [0, 0, state.plate.width, state.plate.height], excluded: []});
  setTool("pick");
  status("已显示整张原片。");
});
el("apply").onclick = handle(async () => {
  await recalc(readControls());
  status("检查结果已重算。");
});
el("settings").onclick = handle(async () => {
  const plate = await api(platePath("/settings"), "PUT", {revision: state.plate.revision, settings: readControls()});
  state.plate = plate;
  await recalc(plate.settings);
  status("检查设置已保存。");
});
el("exclude").onclick = handle(async () => {
  const row = currentRow();
  const excluded = state.settings.excluded.filter(id => id !== row.id);
  if (!row.excluded) excluded.push(row.id);
  await recalc({...state.settings, excluded});
  status(row.excluded ? "已恢复计入该粒。" : "该粒已从筛级汇总排除。");
});
el("runTrial").onclick = handle(async () => {
  const row = currentRow();
  const reason = trialEligibility(row);
  if (reason) throw Error(reason);
  state.trial = await api(platePath("/trial"), "POST",
                          {settings: state.settings, grain_id: row.id});
  state.trialGrade = state.trial.tightest?.grade ?? null;
  renderData();
  status(state.trial.fitting.length ? "已试摆当前孔径，选择要采用的孔级。" : "现有孔径都容不下该粒。");
});
el("adoptTrial").onclick = handle(async () => {
  const row = currentRow();
  const item = state.trial?.openings.find(entry => entry.grade === state.trialGrade && entry.fit);
  if (!row || !item) throw Error("请先选择一个可容下的摆法。");
  const answer = await api(platePath("/trial/apply"), "POST", {
    revision: state.plate.revision, settings: state.settings,
    trial: {grain_id: row.id, grade: item.grade}});
  state.plate = answer.plate;
  state.settings = answer.plate.settings;
  state.result = answer.result;
  state.selected = row.id;
  state.trial = null;
  state.trialGrade = null;
  await redraw();
  renderData();
  const applied = answer.trial;
  status("已按 " + applied.hole + " mm 孔级（" + applied.angle + "°）重分这一粒，其余颗粒照旧。");
});
for (const button of modes) button.onclick = handle(async () => {
  state.mode = button.dataset.mode;
  await redraw();
});
el("clearTrial").onclick = handle(async () => {
  const row = currentRow();
  if (!row?.trial) throw Error("该粒没有采用方筛孔级。");
  const answer = await api(platePath("/trial/clear"), "POST", {
    revision: state.plate.revision, settings: state.settings, grain_id: row.id});
  state.plate = answer.plate;
  state.settings = answer.plate.settings;
  state.result = answer.result;
  state.selected = row.id;
  state.trial = null;
  state.trialGrade = null;
  await redraw();
  renderData();
  status("该粒已恢复按等面积圆径分档，其余颗粒照旧。");
});

el("saveReport").onclick = () => {
  el("reportTitle").value = state.plate.title + " · 留样";
  el("reportDialog").showModal();
};
el("cancelReport").onclick = () => el("reportDialog").close();
el("reportForm").onsubmit = event => {
  event.preventDefault();
  return handle(async () => {
    const report = await api(platePath("/reports"), "POST", {revision: state.plate.revision,
                              settings: readControls(), title: el("reportTitle").value});
    el("reportDialog").close();
    await openReport(report.id);
    await loadReports();
  })(event);
};

handle(async () => {
  const plates = await loadPlates();
  const requested = new URLSearchParams(location.search).get("plate");
  if (requested && plates.some(row => row.id === requested)) await openPlate(requested);
  else if (plates.length) await openPlate(plates[0].id);
})();
