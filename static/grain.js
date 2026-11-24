"use strict";

const el = id => document.getElementById(id);
const modes = [...document.querySelectorAll("[data-mode]")];
const editControls = ["threshold", "minimum", "length", "holes", "apply", "settings", "box", "full", "calibrate", "saveReport"];
const state = {plate: null, settings: null, result: null, report: null, mode: "grade", tool: "pick",
               start: null, selected: null, url: null, review: null};
let busy = false;

function fmtAngle(value) {
  return value.toFixed(1) + "°";
}

function arcSpan(arc) {
  const end = arc.start_deg + arc.width_deg;
  return end <= 180 ? fmtAngle(arc.start_deg) + " – " + fmtAngle(end)
    : fmtAngle(arc.start_deg) + " – " + fmtAngle(end - 180) + "（跨过 0°）";
}

function reviewMembers() {
  if (!state.review) return null;
  const excluded = new Set(state.settings.excluded);
  return state.review.members.filter(id => !excluded.has(id));
}

function inReview(id) {
  return Boolean(state.review) && reviewMembers().includes(id);
}

function exitReview() {
  state.review = null;
  if (state.result) renderData();
}

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
  return {...options, threshold, min_area, holes,
          excluded: threshold !== options.threshold || min_area !== options.min_area ? [] : options.excluded};
}

function currentRow() {
  return state.result?.grains.find(row => row.id === state.selected);
}

function gradeLabel(row) {
  if (row.edge) return "框缘截断";
  if (row.excluded) return "已排除";
  if (row.grade === null) return "待标定";
  const opening = state.result.grades[row.grade].hole;
  return opening === null ? "超最大档" : "≤ " + opening + " mm";
}

function sizeLabel(value) {
  return value === null ? "—" : value.toFixed(2) + " mm";
}

function renderData() {
  const result = state.result;
  const row = currentRow();
  const members = reviewMembers();
  const scopedRows = members ? result.grains.filter(grain => members.includes(grain.id)) : result.grains;
  const scopedCounted = scopedRows.filter(grain => grain.grade !== null).length;
  el("counts").textContent = members ?
    "复核 " + scopedCounted + " / " + scopedRows.length + " 粒（全框 " + result.counted + " / " + result.total + "）"
    : "计入 " + result.counted + " / " + result.total + " 粒";
  el("calibration").textContent = result.mm_per_px === null ?
    "未标定。仍可检查像素面积与轮廓。" : result.mm_per_px.toPrecision(4) + " mm / px";
  el("probe").textContent = row ? "粒 " + row.id + " · " + row.area_px + " px² · " + sizeLabel(row.equivalent_mm) +
    " · " + gradeLabel(row) : "点图或列表可定位颗粒。";
  el("exclude").textContent = row?.excluded ? "恢复计入" : "排除该粒";
  el("exclude").disabled = !row || row.edge || Boolean(state.report) ||
    (Boolean(state.review) && !inReview(row.id));
  const summary = result.grades.map(grade => {
    const chip = document.createElement("div");
    chip.className = "grade-chip";
    const swatch = document.createElement("i");
    swatch.className = "swatch";
    swatch.style.background = grade.color;
    const count = document.createElement("b");
    count.textContent = grade.count + " 粒";
    chip.append(swatch, grade.hole === null ? "超最大档" : "≤ " + grade.hole + " mm", count);
    return chip;
  });
  el("summary").replaceChildren(...summary);
  const visibleGrains = members ? result.grains.filter(grain => members.includes(grain.id)) : result.grains;
  const body = visibleGrains.map(grain => {
    const tr = document.createElement("tr");
    const label = document.createElement("button");
    label.textContent = grain.id;
    label.onclick = () => selectGrain(grain.id);
    tr.insertCell().append(label);
    for (const value of [grain.area_px, sizeLabel(grain.equivalent_mm), gradeLabel(grain)]) {
      tr.insertCell().textContent = value;
    }
    tr.classList.toggle("active-row", grain.id === state.selected);
    return tr;
  });
  el("rows").replaceChildren(...body);
  renderOrientation();
  paintOverlay();
}

function drawBox(ctx, box, color, thickness) {
  const [x0, y0, x1, y1] = box;
  ctx.strokeStyle = color;
  ctx.lineWidth = thickness;
  ctx.strokeRect(x0 + .5, y0 + .5, x1 - x0 - 1, y1 - y0 - 1);
}

function renderOrientation() {
  const host = el("orientationBody");
  if (!state.result) {
    host.replaceChildren();
    return;
  }
  const info = state.result.orientation;
  host.replaceChildren();
  if (state.review) {
    const banner = document.createElement("div");
    banner.className = "review-banner";
    const text = document.createElement("span");
    const liveArea = state.result.grains
      .filter(grain => reviewMembers().includes(grain.id))
      .reduce((total, grain) => total + grain.area_px, 0);
    text.textContent = "复核范围：" + state.review.title + " " + arcSpan(state.review) +
      " · " + reviewMembers().length + " 粒 · " + liveArea + " px²";
    const leave = document.createElement("button");
    leave.textContent = "结束复核";
    leave.onclick = handle(async () => {
      exitReview();
      status("已回到全框，筛级未改变。");
    });
    banner.append(text, leave);
    host.append(banner);
    if (reviewMembers().length === 0) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "该角段的成员均已被排除；恢复计入或结束复核即可。";
      host.append(empty);
    }
  }
  const head = document.createElement("p");
  head.className = "hint";
  head.textContent = "有方向 " + info.oriented_total + " 粒；长轴两端同向，角度按 0–180° 统计。";
  host.append(head);
  const cards = [["count_arc", "按粒数最窄角段"], ["area_arc", "按投影面积最窄角段"]]
    .map(([key, title]) => buildArcCard(key, title, info));
  if (cards.length) host.append(...cards);
  const round = document.createElement("p");
  round.className = "round";
  if (info.round_total) {
    round.innerHTML = "方向不清（近圆，长短轴比 ≥ " + info.round_axis_ratio + "）：<b>" +
      info.round_total + " 粒</b>，不进入角段统计。";
  } else {
    round.textContent = "没有近圆颗粒，全部长颗粒均参与方向统计。";
  }
  host.append(round);
}

function buildArcCard(key, title, info) {
  const arc = info[key];
  const card = document.createElement("div");
  card.className = "arc";
  if (!arc) {
    card.append(Object.assign(document.createElement("p"), {className: "arc-meta",
      textContent: "有方向的颗粒不足，无法计算角段。"}));
    return card;
  }
  const adopted = state.review?.kind === key;
  card.classList.toggle("adopted", adopted);
  const name = document.createElement("div");
  name.className = "arc-name";
  name.append(document.createTextNode(title), document.createTextNode(arcSpan(arc)));
  const meta = document.createElement("div");
  meta.className = "arc-meta";
  meta.textContent = "角段宽 " + fmtAngle(arc.width_deg) + " · 实际罩住 " + arc.grain_count +
    " 粒 · 投影面积 " + arc.area_px + " px²";
  card.append(name, meta);
  if (!state.report) {
    const button = document.createElement("button");
    button.textContent = adopted ? "已采用为本次复核范围" : "采用此角段复核";
    button.disabled = Boolean(state.review);
    button.onclick = handle(async () => {
      state.review = {kind: key, title, start_deg: arc.start_deg, width_deg: arc.width_deg,
                      grain_count: arc.grain_count, area_px: arc.area_px, members: [...arc.members]};
      if (!inReview(state.selected)) state.selected = null;
      renderData();
      status("已按「" + title + "」限定复核范围，原片仍显示整框。");
    });
    card.append(button);
  }
  return card;
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
  if (state.review) {
    const present = new Set(result.grains.map(grain => grain.id));
    state.review.members = state.review.members.filter(id => present.has(id));
    if (!inReview(state.selected)) state.selected = null;
  } else if (!currentRow()) {
    state.selected = null;
  }
  await redraw();
  renderData();
}

async function openPlate(id) {
  if (!id) return;
  state.plate = await api("/plates/" + id);
  state.report = null;
  state.selected = null;
  state.review = null;
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
  state.review = null;
  state.mode = "grade";
  setTool("pick");
  loadControls();
  await redraw();
  renderData();
  status("已打开保存的留样。");
}

function selectGrain(id) {
  if (state.review && !inReview(id)) {
    status("该粒不在当前复核角段内，先结束复核再选它。");
    return;
  }
  state.selected = id;
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
    if (result.selected && state.review && !inReview(result.selected)) {
      status("该粒不在当前复核角段内；点粒与列表仅复核该组粒号。");
      return;
    }
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
    exitReview();
    const box = [Math.min(first[0], point[0]), Math.min(first[1], point[1]),
                 Math.max(first[0], point[0]) + 1, Math.max(first[1], point[1]) + 1];
    await recalc({...readControls(), box, excluded: []});
    status("已重算检查框。");
  } else {
    exitReview();
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
  exitReview();
  await recalc({...readControls(), box: [0, 0, state.plate.width, state.plate.height], excluded: []});
  setTool("pick");
  status("已显示整张原片。");
});
el("apply").onclick = handle(async () => {
  exitReview();
  await recalc(readControls());
  status("检查结果已重算。");
});
el("settings").onclick = handle(async () => {
  exitReview();
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
  status(row.excluded ? "已恢复计入该粒。" : "该粒已从复核组与筛级汇总中排除，角段保持不变。");
});
for (const button of modes) button.onclick = handle(async () => {
  state.mode = button.dataset.mode;
  await redraw();
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
