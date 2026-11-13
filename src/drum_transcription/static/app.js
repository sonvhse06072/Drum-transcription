"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  health: null, file: null, job: null, timer: null, generation: 0, submitting: false,
  rhythmBars: [], rhythmEditorJobId: null, barOverrides: new Map(), barEditorDirty: false,
};
const statuses = { queued: "Đang chờ", running: "Đang xử lý", completed: "Hoàn tất", failed: "Có lỗi" };
const instruments = {
  kick: "Kick", snare: "Snare", tom_low: "Low tom", tom_mid: "Mid tom", tom_high: "High tom",
  hihat_closed: "Hi-hat đóng", hihat_open: "Hi-hat mở", hihat_pedal: "Hi-hat chân",
  crash: "Crash / cymbal", ride: "Ride", ride_bell: "Ride bell", china: "China", splash: "Splash",
  side_stick: "Side stick", clap: "Clap", cowbell: "Cowbell", tambourine: "Tambourine",
};
const fileLabels = {
  musicxml: ["MusicXML · mở trong MuseScore", true], pdf: ["PDF", false],
  raw_midi: ["MIDI gốc", false], score_midi: ["MIDI theo sheet", false], events: ["Cú đánh · JSON", false],
  rhythm: ["Bản đồ nhịp & phách · JSON", false],
};

function textElement(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function announce(message) { $("live-status").textContent = message; }
function showError(message) {
  $("global-alert").textContent = message;
  $("global-alert").hidden = !message;
  if (message) announce(message);
}

function formatError(data, fallback) {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((item) => item.msg || String(item)).join(" · ");
  return data?.error || data?.message || fallback;
}

async function api(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(formatError(data, `Máy chủ trả lỗi ${response.status}.`));
  return data;
}

function chosenEngine() {
  return state.health?.engines?.find((engine) => engine.id === $("engine").value);
}

function updateRunState() {
  const busy = state.submitting || ["running", "queued"].includes(state.job?.status);
  const timingReady = $("rhythm-mode").value === "manual" || state.health?.rhythm?.ready;
  $("run-button").disabled = busy || !state.file || !chosenEngine()?.ready || !state.health?.ffmpeg || !timingReady;
  $("demo-button").disabled = busy || !state.health;
  $("rescore-button").disabled = busy || state.job?.status !== "completed";
  const canEditBars = !busy && state.job?.status === "completed" && state.rhythmBars.length > 0;
  for (const id of ["bar-edit-apply", "bar-edit-reset", "rhythm-rescore-button"]) $(id).disabled = !canEditBars;
}

function populateMeterChoices() {
  const common = ["4/4", "3/4", "6/8", "2/4", "5/4", "7/4", "5/8", "7/8", "9/8", "11/8", "12/8", "13/16", "15/16"];
  for (const id of ["meter", "rescore-meter", "bar-edit-meter"]) {
    const select = $(id);
    const previous = select.value;
    const blank = Array.from(select.options).find((option) => option.value === "");
    select.replaceChildren();
    if (blank) select.append(blank);
    for (const meter of common) {
      const option = textElement("option", meter); option.value = meter; select.append(option);
    }
    const others = document.createElement("optgroup"); others.label = "Số chỉ nhịp khác";
    for (const denominator of [2, 4, 8, 16]) for (let numerator = 1; numerator <= 32; numerator += 1) {
      const meter = `${numerator}/${denominator}`;
      if (!common.includes(meter)) { const option = textElement("option", meter); option.value = meter; others.append(option); }
    }
    select.append(others); select.value = previous;
  }
}

function renderTimingMode() {
  const manual = $("rhythm-mode").value === "manual";
  $("manual-timing-fields").hidden = !manual;
  $("auto-timing-help").hidden = manual;
  for (const id of ["bpm", "downbeat"]) {
    $(id).disabled = !manual;
    $(id).required = manual;
  }
  $("meter").required = manual;
  $("meter-hint").textContent = manual ? "bắt buộc" : "tuỳ chọn";
  $("auto-meter-option").textContent = manual ? "Chọn số chỉ nhịp" : "Tự nhận diện";
  $("meter-help").hidden = manual;
  const missing = !manual && state.health && !state.health.rhythm?.ready;
  $("rhythm-alert").hidden = !missing;
  $("rhythm-alert").textContent = missing
    ? "Chưa có model nhận diện nhịp. Chuẩn bị Beat This! trong mục “Kiểm tra môi trường local”, rồi bấm “Kiểm tra lại”. Tempo và phách 1 sẽ được dò từ audio khi model sẵn sàng."
    : "";
  updateRunState();
}

function renderEngine() {
  const engine = chosenEngine();
  const adtof = $("engine").value === "adtof";
  $("threshold-field").hidden = !adtof;
  $("threshold").disabled = !adtof;
  $("engine-description").textContent = engine?.detail || "Chưa xác định được trạng thái mô hình.";
  let warning = "";
  if (!engine?.ready) warning = "Mô hình chưa sẵn sàng. Cài thư viện và chuẩn bị trọng số local theo hướng dẫn, rồi bấm “Kiểm tra lại”. Bạn vẫn có thể thử mẫu ký âm.";
  else if (adtof) warning = "ADTOF chỉ nhận diện 5 nhóm: kick, snare, tom, hi-hat và cymbal. Kết quả này chưa phân biệt đầy đủ tom, hi-hat mở/đóng hay crash/ride.";
  else warning = "ADT_STR có nhiều nhãn nhạc cụ hơn; độ chính xác vẫn cần kiểm chứng trên nhạc của bạn. Một số kỹ thuật như ride bell, flam và sticking chưa được phân biệt.";
  $("engine-alert").textContent = warning;
  $("engine-alert").hidden = !warning;
  updateRunState();
}

async function loadHealth() {
  try {
    state.health = await api("/api/health");
    $("server-status").textContent = "Máy chủ local đã kết nối";
    $("server-dot").className = "status-dot ready";
    $("version").textContent = state.health.version ? `v${state.health.version}` : "";
    const checks = [["FFmpeg", state.health.ffmpeg, "Sẵn sàng", "Chưa tìm thấy"], ["MuseScore / PDF", state.health.musescore, "Sẵn sàng", "Chưa cấu hình · vẫn xuất XML"]];
    for (const engine of state.health.engines || []) checks.push([engine.label || engine.id, engine.ready, "Sẵn sàng", "Chưa sẵn sàng"]);
    checks.push(["Beat This! / tempo & phách 1", state.health.rhythm?.ready, "Sẵn sàng", "Chưa chuẩn bị"]);
    if (state.health.separation) {
      checks.push(["Demucs / tách trống", state.health.separation.ready, "Sẵn sàng", "Chưa chuẩn bị"]);
      $("separate-drums").disabled = !state.health.separation.ready;
      if (!state.health.separation.ready) $("separate-drums").checked = false;
      $("separate-drums").closest("label").title = state.health.separation.detail || "";
    }
    $("system-checks").replaceChildren(...checks.map(([name, ready, yes, no]) => {
      const row = document.createElement("div"); row.className = "check-row";
      row.append(textElement("span", name), textElement("span", ready ? yes : no, `check-state ${ready ? "ready" : "missing"}`));
      return row;
    }));
    if (!state.health.ffmpeg || !state.health.rhythm?.ready) $("setup-help").open = true;
    renderTimingMode();
    renderEngine();
  } catch (error) {
    state.health = null;
    $("server-status").textContent = "Chưa kết nối được máy chủ local";
    $("server-dot").className = "status-dot error";
    $("engine-description").textContent = "Khởi động ứng dụng theo README rồi kiểm tra lại kết nối.";
    renderTimingMode();
    updateRunState();
    showError(error.message || "Không kết nối được máy chủ.");
  }
}

function selectFile(file) {
  state.file = file || null;
  $("file-name").textContent = file?.name || "Chọn hoặc thả file nhạc vào đây";
  $("file-meta").textContent = file ? `${(file.size / 1024 / 1024).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} MB · file trên máy của bạn` : "WAV, MP3, FLAC, M4A và các định dạng FFmpeg hỗ trợ";
  if (file && ["", "Drum transcription"].includes($("title").value)) $("title").value = file.name.replace(/\.[^.]+$/, "").slice(0, 120);
  updateRunState();
}

function formOptions() {
  const manual = $("rhythm-mode").value === "manual";
  return {
    title: $("title").value.trim() || "Drum transcription", engine: $("engine").value,
    rhythm_mode: manual ? "manual" : "auto",
    bpm: manual ? Number($("bpm").value) : null, meter: $("meter").value || null, subdivisions: Number($("subdivisions").value),
    first_downbeat_seconds: manual ? Number($("downbeat").value) : null, device: $("device").value,
    threshold: Number($("threshold").value), clip_start_seconds: Number($("clip-start").value),
    clip_duration_seconds: Number($("clip-duration").value), separate_drums: $("separate-drums").checked,
  };
}

function automaticTiming(job) {
  return job.kind !== "demo" && job.options?.rhythm_mode === "auto";
}

function resolvedScoreOptions(job, rhythm = job.rhythm) {
  const input = job.options || {};
  const resolved = job.resolved_score_options || {};
  const fallback = automaticTiming(job) ? (rhythm || {}) : input;
  return {
    title: resolved.title ?? input.title,
    bpm: resolved.bpm ?? fallback.bpm,
    meter: resolved.meter ?? fallback.meter,
    subdivisions: resolved.subdivisions ?? input.subdivisions,
    first_downbeat_seconds: resolved.first_downbeat_seconds ?? fallback.first_downbeat_seconds,
  };
}

function formatSeconds(value) {
  return value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toLocaleString("vi-VN", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} s`;
}

function barTempo(bar) {
  const seconds = Number(bar.end_seconds) - Number(bar.start_seconds);
  const quarters = Number(bar.end_quarter) - Number(bar.start_quarter);
  return seconds > 0 && quarters > 0 ? 60 * quarters / seconds : null;
}

function meterSequence(bars) {
  return bars.reduce((sequence, bar) => {
    if (sequence.at(-1) !== bar.meter) sequence.push(bar.meter);
    return sequence;
  }, []);
}

function showBarError(message) {
  $("bar-edit-error").textContent = message;
  $("bar-edit-error").hidden = !message;
}

function renderPendingBars() {
  const pending = [...state.barOverrides.values()].sort((left, right) => left.bar_index - right.bar_index);
  $("bar-edit-pending").hidden = !pending.length;
  $("bar-edit-reset").hidden = !pending.length;
  if (!pending.length) { $("bar-edit-pending").replaceChildren(); return; }
  const groups = [];
  for (const item of pending) {
    const last = groups.at(-1);
    const grouping = item.grouping.join("+");
    if (last && last.end + 1 === item.bar_index && last.meter === item.meter && last.grouping === grouping) last.end = item.bar_index;
    else groups.push({ start: item.bar_index, end: item.bar_index, meter: item.meter, grouping });
  }
  const heading = textElement("strong", `Bản nháp: ${pending.length} ô được chỉnh`);
  const list = document.createElement("ul");
  for (const group of groups) {
    const label = group.start === group.end ? `Ô ${group.start + 1}` : `Ô ${group.start + 1}–${group.end + 1}`;
    list.append(textElement("li", `${label}: ${group.meter} · ${group.grouping || "nhóm mặc định"}`));
  }
  $("bar-edit-pending").replaceChildren(heading, list, textElement("p", "Các thay đổi chỉ áp dụng khi bạn bấm xuất sheet. Bảng phía trên vẫn là kết quả hiện tại.", "field-help"));
}

function renderBarMap(job, rhythm) {
  const bars = automaticTiming(job) && Array.isArray(rhythm?.bars) ? rhythm.bars : [];
  const initialize = state.rhythmEditorJobId !== job.id || (!state.rhythmBars.length && bars.length > 0);
  state.rhythmBars = bars;
  $("bar-map").hidden = !bars.length;
  if (initialize) {
    state.rhythmEditorJobId = job.id;
    state.barOverrides.clear();
    state.barEditorDirty = false;
    $("bar-edit-start").value = 1; $("bar-edit-end").value = 1;
    $("bar-edit-meter").value = bars[0]?.meter || "4/4";
    $("bar-edit-grouping").value = bars[0]?.grouping?.join("+") || "";
    $("rhythm-rescore-grid").value = resolvedScoreOptions(job, rhythm).subdivisions ?? 4;
    showBarError(""); renderPendingBars();
  }
  $("bar-edit-start").max = bars.length; $("bar-edit-end").max = bars.length;
  if (!bars.length) { $("bar-map-body").replaceChildren(); updateRunState(); return; }
  const sequence = meterSequence(bars);
  const changes = Math.max(0, sequence.length - 1);
  $("bar-map-summary").textContent = `${bars.length} ô · ${changes} lần đổi số chỉ nhịp`;
  $("bar-map-body").replaceChildren(...bars.map((bar, index) => {
    const row = document.createElement("tr");
    if (index && bar.meter !== bars[index - 1].meter) row.className = "meter-change";
    const bpm = barTempo(bar);
    const estimated = bar.estimated !== false;
    const tail = bar.source === "extrapolated" || Number(bar.end_seconds) > Number(job.metrics?.duration_seconds) + 0.001;
    const source = bar.source === "user" ? `Đã chỉnh${tail ? " · đuôi dự kiến" : ""}` : tail ? "Đuôi dự kiến" : estimated ? "Ước lượng" : "Đã lưu";
    const values = [String(index + 1), `${formatSeconds(bar.start_seconds)} – ${formatSeconds(bar.end_seconds)}`, bar.meter, bar.grouping?.length ? bar.grouping.join("+") : "—", bpm == null ? "—" : bpm.toLocaleString("vi-VN", { maximumFractionDigits: 1 })];
    row.append(...values.map((value) => textElement("td", value)));
    const status = document.createElement("td"); status.append(textElement("span", source, `bar-source ${bar.source === "user" ? "user" : "estimated"}`)); row.append(status);
    return row;
  }));
  updateRunState();
}

function renderRhythm(job, eventRhythm) {
  const rhythm = job.rhythm || eventRhythm;
  const options = resolvedScoreOptions(job, rhythm);
  const auto = automaticTiming(job);
  const demo = job.kind === "demo";
  const downbeat = options.first_downbeat_seconds ?? job.metrics?.first_downbeat_seconds;
  const original = demo ? null : (job.metrics?.first_downbeat_original_seconds ?? (downbeat == null ? null : Number(downbeat) + Number(job.options?.clip_start_seconds ?? 0)));
  $("rhythm-result").hidden = false;
  $("rhythm-source").textContent = demo ? "Dữ liệu mẫu" : job.kind === "rhythm_rescore" ? "Phách tự động · ký âm đã chỉnh" : auto ? "Tự nhận diện" : job.options?.rhythm_mode === "manual" ? "Nhập thủ công" : "Thông số đã lưu";
  $("tempo-label").textContent = auto ? "Tempo trung vị · ♩ = BPM" : "Tempo cố định · ♩ = BPM";
  $("detected-bpm").textContent = options.bpm == null ? "—" : Number(options.bpm).toLocaleString("vi-VN", { maximumFractionDigits: 2 });
  const sequence = auto ? meterSequence(rhythm?.bars || []) : [];
  $("detected-meter").textContent = sequence.length ? `${sequence.slice(0, 6).join(" → ")}${sequence.length > 6 ? " → …" : ""}` : options.meter || "—";
  $("detected-meter").classList.toggle("meter-sequence", sequence.length > 1);
  $("detected-downbeat").textContent = formatSeconds(downbeat);
  $("original-downbeat").textContent = demo ? "Không có file gốc" : formatSeconds(original);
  const unstable = auto && rhythm?.metadata?.rhythm_review_status === "unstable";
  const reasons = unstable && Array.isArray(rhythm.metadata.rhythm_review_reasons)
    ? rhythm.metadata.rhythm_review_reasons.filter((reason) => typeof reason === "string" && reason.trim())
    : [];
  $("rhythm-review-notice").hidden = !unstable;
  $("rhythm-review-reasons").hidden = !reasons.length;
  $("rhythm-review-reasons").replaceChildren(...reasons.map((reason) => textElement("li", reason)));
  $("rhythm-result-note").textContent = demo
    ? "Các mốc nhịp của mẫu được định nghĩa sẵn để kiểm tra ký âm."
    : auto
      ? "BPM ở đây là tempo trung vị; tempo từng ô có thể thay đổi. Mốc trong file gốc bằng điểm cắt cộng mốc trong clip. Nghe bản phách để kiểm tra: máy có thể nhầm số chỉ nhịp hoặc bỏ sót phách 1. Ô cuối dự kiến có thể kéo dài quá phần audio còn lại."
      : "Sheet dùng tempo và phách đầu đã nhập. Mốc trong file gốc bằng điểm cắt cộng mốc trong clip; timing cú đánh gốc vẫn được giữ.";
  if (auto && !rhythm?.bars?.length) $("rhythm-result-note").textContent += " Kết quả cũ chưa có bản đồ từng ô; chạy lại clip với phiên bản mới để phân tích đoạn đổi nhịp.";
  renderBarMap(job, rhythm);
}

function safeFileUrl(value, jobId) {
  if (typeof value !== "string" || !jobId) return null;
  try {
    const url = new URL(value, location.origin);
    if (url.origin !== location.origin || !url.pathname.startsWith(`/api/jobs/${encodeURIComponent(jobId)}/files/`)) return null;
    return url.pathname + url.search;
  } catch { return null; }
}

function metric(value, label) {
  const item = document.createElement("div"); item.className = "metric";
  item.append(textElement("div", value, "metric-value"), textElement("div", label, "metric-label"));
  return item;
}

function setWarnings(warnings) {
  const unique = [...new Set((warnings || []).filter(Boolean))];
  $("warnings-section").hidden = !unique.length;
  $("warnings-title").textContent = `Lưu ý khi đọc kết quả${unique.length ? ` · ${unique.length}` : ""}`;
  $("warnings-list").replaceChildren(...unique.map((warning) => textElement("li", warning)));
}

function renderOutputs(job) {
  const files = job.files || {};
  const links = [];
  for (const [key, [label, primary]] of Object.entries(fileLabels)) {
    const url = safeFileUrl(files[key], job.id);
    if (!url) continue;
    const link = document.createElement("a"); link.href = url; link.download = "";
    link.className = `download-link${primary ? " primary" : ""}`;
    link.append(textElement("span", "↓"), document.createTextNode(label)); links.push(link);
  }
  $("downloads").replaceChildren(...links);
  const audioLabels = [["source_audio", "Audio gốc"], ["beat_preview", "Audio + phách tự động"], ["drums_audio", "Stem trống"], ["preview_audio", "Cú đánh phát lại"]];
  const audioCards = [];
  for (const [key, label] of audioLabels) {
    const url = safeFileUrl(files[key], job.id);
    if (!url) continue;
    const card = document.createElement("div"); card.className = "audio-card";
    const player = document.createElement("audio"); player.controls = true; player.preload = "none"; player.src = url; player.setAttribute("aria-label", label);
    player.addEventListener("play", () => { for (const other of document.querySelectorAll("audio")) if (other !== player) other.pause(); });
    card.append(textElement("span", label, "audio-label"), player); audioCards.push(card);
  }
  $("audio-players").replaceChildren(...audioCards);
  $("audio-note").hidden = !safeFileUrl(files.preview_audio, job.id);
  $("beat-audio-note").hidden = !safeFileUrl(files.beat_preview, job.id);
  const pdfUrl = safeFileUrl(files.pdf, job.id);
  $("pdf-panel").hidden = !pdfUrl;
  if (pdfUrl) $("pdf-preview").src = `${pdfUrl}#view=FitH`;
  else $("pdf-preview").removeAttribute("src");
  $("pdf-note").textContent = pdfUrl ? "PDF được khắc từ MusicXML. Dùng MusicXML để sửa ký âm trong MuseScore; MIDI gốc giữ thời điểm cú đánh trước khi đưa vào lưới nhịp." : "Mở MusicXML bằng MuseScore để xem và sửa bản nhạc. Xuất PDF tự động cần MuseScore được cài trên máy.";
}

function renderMetrics(job, events) {
  const m = job.metrics || {};
  const hits = events?.hits;
  const hitCount = hits?.length ?? m.hits_count ?? m.hit_count ?? m.total_hits;
  const seconds = events?.duration_seconds ?? m.duration_seconds;
  const elapsed = m.elapsed_seconds ?? m.processing_seconds;
  $("metrics").replaceChildren(
    metric(hitCount == null ? "—" : hitCount.toLocaleString("vi-VN"), "Cú đánh gốc"),
    metric(seconds == null ? "—" : `${Number(seconds).toFixed(1)} s`, "Độ dài đoạn"),
    metric(m.bars == null ? "—" : Number(m.bars).toLocaleString("vi-VN"), "Ô nhịp trên sheet"),
    metric(elapsed == null ? "—" : `${Number(elapsed).toFixed(1)} s`, "Thời gian xử lý"),
  );
}

async function loadEvents(job, generation) {
  try {
    const events = await api(`/api/jobs/${encodeURIComponent(job.id)}/events`);
    if (generation !== state.generation) return;
    const hits = events.hits || [];
    const shown = hits.slice(0, 200);
    $("event-count").textContent = hits.length > 200 ? `Hiển thị 200 / ${hits.length}` : `${hits.length} cú đánh`;
    const counts = {};
    for (const hit of hits) counts[hit.instrument] = (counts[hit.instrument] || 0) + 1;
    $("instrument-counts").replaceChildren(...Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([name, count]) => {
      const chip = textElement("span", instruments[name] || name, "instrument-count"); chip.append(textElement("strong", String(count))); return chip;
    }));
    $("events-body").replaceChildren(...shown.map((hit) => {
      const row = document.createElement("tr");
      const values = [`${Number(hit.time_seconds).toFixed(3)} s`, instruments[hit.instrument] || hit.instrument, hit.velocity == null ? "—" : String(hit.velocity), hit.confidence == null ? "—" : Number(hit.confidence).toFixed(3)];
      row.append(...values.map((value) => textElement("td", value))); return row;
    }));
    if (!hits.length) {
      const row = document.createElement("tr"); const cell = textElement("td", "Không có cú đánh được nhận diện trong đoạn này."); cell.colSpan = 4; row.append(cell); $("events-body").append(row);
    }
    renderMetrics(job, events);
    renderRhythm(job, events.rhythm);
    setWarnings([...(job.warnings || []), ...(events.warnings || []), ...(automaticTiming(job) ? (job.rhythm?.warnings || events.rhythm?.warnings || []) : [])]);
    if (events.engine === "demo" || events.engine === "synthetic") $("demo-notice").hidden = false;
  } catch (error) {
    if (generation !== state.generation) return;
    $("event-count").textContent = "Chưa tải được dữ liệu cú đánh";
    setWarnings([...(job.warnings || []), error.message]);
  }
}

function renderJob(job) {
  state.job = job;
  $("empty-state").hidden = true;
  $("job-badge").hidden = false;
  $("job-badge").className = `badge ${Object.hasOwn(statuses, job.status) ? job.status : ""}`;
  $("job-badge").textContent = statuses[job.status] || job.status;
  $("result-subtitle").textContent = job.options?.title || job.title || "Kết quả xử lý";
  $("job-progress").hidden = false;
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  $("progress-bar").value = progress; $("progress-value").textContent = `${Math.round(progress)}%`;
  $("stage-text").textContent = job.stage || statuses[job.status] || "Đang xử lý";
  $("job-info").textContent = `Mã lần xử lý: ${job.id}`;
  $("job-error").hidden = job.status !== "failed";
  $("job-error").textContent = job.error || "Không hoàn tất xử lý. Kiểm tra mô hình và thông số rồi thử lại.";
  $("result-content").hidden = job.status !== "completed";
  if (job.status === "completed") {
    $("job-progress").hidden = true;
    const demo = job.kind === "demo" || job.engine === "demo" || job.options?.engine === "demo" || job.is_demo;
    $("demo-notice").hidden = !demo;
    const options = resolvedScoreOptions(job);
    $("rescore-bpm").value = options.bpm ?? "";
    $("rescore-meter").value = options.meter ?? "";
    $("rescore-grid").value = options.subdivisions ?? 4;
    $("rescore-downbeat").value = options.first_downbeat_seconds ?? "";
    renderMetrics(job); renderRhythm(job); renderOutputs(job); setWarnings(job.warnings);
    $("events-body").replaceChildren(); $("instrument-counts").replaceChildren(); $("event-count").textContent = "Đang tải…";
    loadEvents(job, state.generation);
    announce("Đã tạo xong kết quả. Có thể nghe đối chiếu và tải bản nhạc.");
  } else if (job.status === "failed") announce(job.error || "Xử lý gặp lỗi.");
  updateRunState();
}

function selectJob(job) {
  clearTimeout(state.timer); state.generation += 1;
  renderJob(job);
  if (["queued", "running"].includes(job.status)) schedulePoll(job.id, state.generation);
}

function schedulePoll(id, generation, failures = 0) {
  state.timer = setTimeout(async () => {
    if (generation !== state.generation) return;
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (generation !== state.generation) return;
      renderJob(job);
      if (["queued", "running"].includes(job.status)) schedulePoll(id, generation);
      else loadHistory();
    } catch (error) {
      if (generation !== state.generation) return;
      if (failures < 4) schedulePoll(id, generation, failures + 1);
      else { showError(`Mất kết nối khi đọc tiến độ. ${error.message} Bấm “Làm mới” ở danh sách xử lý để kiểm tra lại.`); }
    }
  }, failures ? 3000 : 1200);
}

async function loadHistory() {
  try {
    const data = await api("/api/jobs");
    const jobs = Array.isArray(data) ? data : data.jobs || [];
    $("history-panel").hidden = !jobs.length;
    $("job-history").replaceChildren(...jobs.slice(0, 12).map((job) => {
      const button = document.createElement("button"); button.type = "button"; button.className = "history-job";
      const main = document.createElement("span"); main.className = "history-main";
      const date = job.created_at ? new Date(job.created_at) : null;
      const dateText = date && !Number.isNaN(date.getTime()) ? date.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" }) : job.id;
      main.append(textElement("span", job.options?.title || job.title || job.id, "history-title"), textElement("span", dateText, "history-meta"));
      button.append(main, textElement("span", statuses[job.status] || job.status, `badge ${Object.hasOwn(statuses, job.status) ? job.status : ""}`));
      button.addEventListener("click", async () => {
        showError("");
        try { selectJob(await api(`/api/jobs/${encodeURIComponent(job.id)}`)); $("result-title").scrollIntoView({ behavior: "smooth", block: "start" }); }
        catch (error) { showError(error.message); }
      });
      return button;
    }));
  } catch { /* Health shows connection failures; history is optional. */ }
}

$("transcribe-form").addEventListener("submit", async (event) => {
  event.preventDefault(); showError("");
  if (!state.file) { showError("Hãy chọn một file nhạc."); return; }
  const options = formOptions();
  if (options.rhythm_mode === "manual" && options.first_downbeat_seconds >= options.clip_duration_seconds) { showError("Phách 1 phải nằm bên trong đoạn nhạc đã chọn."); $("downbeat").focus(); return; }
  state.submitting = true; updateRunState();
  try {
    const form = new FormData(); form.append("file", state.file); form.append("options", JSON.stringify(options));
    selectJob(await api("/api/jobs", { method: "POST", body: form })); loadHistory();
  } catch (error) { showError(error.message); }
  finally { state.submitting = false; updateRunState(); }
});

$("demo-button").addEventListener("click", async () => {
  showError(""); state.submitting = true; updateRunState();
  try { selectJob(await api("/api/demo", { method: "POST" })); loadHistory(); }
  catch (error) { showError(error.message); }
  finally { state.submitting = false; updateRunState(); }
});

$("rescore-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (!state.job) return; showError("");
  const options = { title: state.job.options?.title || "Drum transcription", bpm: Number($("rescore-bpm").value), meter: $("rescore-meter").value, subdivisions: Number($("rescore-grid").value), first_downbeat_seconds: Number($("rescore-downbeat").value) };
  state.submitting = true; updateRunState();
  try { selectJob(await api(`/api/jobs/${encodeURIComponent(state.job.id)}/rescore`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options) })); }
  catch (error) { showError(error.message); }
  finally { state.submitting = false; updateRunState(); }
});

function applyBarEdits() {
  showBarError("");
  const first = Number($("bar-edit-start").value);
  const last = Number($("bar-edit-end").value);
  if (!Number.isInteger(first) || !Number.isInteger(last) || first < 1 || last < first || last > state.rhythmBars.length) {
    showBarError(`Chọn khoảng ô hợp lệ từ 1 đến ${state.rhythmBars.length}.`); return false;
  }
  const meter = $("bar-edit-meter").value;
  if (!/^(?:[1-9]|[12]\d|3[0-2])\/(?:2|4|8|16)$/.test(meter)) { showBarError("Chọn số chỉ nhịp hợp lệ."); return false; }
  const raw = $("bar-edit-grouping").value.trim();
  if (raw && !/^\d+(?:\s*\+\s*\d+)*$/.test(raw)) { showBarError("Nhóm phách dùng số nguyên dương nối bằng dấu +, ví dụ 2+2+3."); return false; }
  const grouping = raw ? raw.split("+").map(Number) : [];
  if (grouping.some((value) => !Number.isInteger(value) || value <= 0) || (grouping.length && grouping.reduce((sum, value) => sum + value, 0) !== Number(meter.split("/")[0]))) {
    showBarError("Tổng các nhóm phách phải bằng tử số của số chỉ nhịp."); return false;
  }
  for (let number = first; number <= last; number += 1) state.barOverrides.set(number - 1, { bar_index: number - 1, meter, grouping: [...grouping] });
  state.barEditorDirty = false;
  renderPendingBars();
  announce(`Đã ghi chỉnh sửa ô ${first}${last !== first ? ` đến ${last}` : ""} vào bản nháp.`);
  return true;
}

$("bar-edit-apply").addEventListener("click", applyBarEdits);

$("bar-edit-reset").addEventListener("click", () => {
  state.barOverrides.clear(); state.barEditorDirty = false;
  const bar = state.rhythmBars[Number($("bar-edit-start").value) - 1];
  if (bar) { $("bar-edit-meter").value = bar.meter; $("bar-edit-grouping").value = bar.grouping?.join("+") || ""; }
  showBarError(""); renderPendingBars(); announce("Đã bỏ các chỉnh sửa số chỉ nhịp trong bản nháp.");
});

$("bar-edit-start").addEventListener("change", () => {
  const number = Number($("bar-edit-start").value);
  if (Number($("bar-edit-end").value) < number) $("bar-edit-end").value = number;
  const bar = state.rhythmBars[number - 1];
  if (bar) {
    const current = state.barOverrides.get(number - 1) || bar;
    $("bar-edit-meter").value = current.meter;
    $("bar-edit-grouping").value = current.grouping?.join("+") || "";
    state.barEditorDirty = false;
  }
});

$("bar-edit-meter").addEventListener("change", () => { $("bar-edit-grouping").value = ""; state.barEditorDirty = true; });
$("bar-edit-grouping").addEventListener("input", () => { state.barEditorDirty = true; });
$("bar-edit-end").addEventListener("input", () => { state.barEditorDirty = true; });

$("rhythm-rescore-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (!state.job || !state.rhythmBars.length) return;
  showError(""); showBarError("");
  if (state.barEditorDirty && !applyBarEdits()) return;
  const options = {
    title: state.job.options?.title || "Drum transcription",
    subdivisions: Number($("rhythm-rescore-grid").value),
    bar_overrides: [...state.barOverrides.values()].sort((left, right) => left.bar_index - right.bar_index),
  };
  state.submitting = true; updateRunState();
  try {
    selectJob(await api(`/api/jobs/${encodeURIComponent(state.job.id)}/rhythm-rescore`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options) }));
    loadHistory();
  } catch (error) { showBarError(error.message); }
  finally { state.submitting = false; updateRunState(); }
});

$("audio-file").addEventListener("change", (event) => selectFile(event.target.files[0]));
$("engine").addEventListener("change", renderEngine);
$("rhythm-mode").addEventListener("change", renderTimingMode);
$("refresh-health").addEventListener("click", () => { showError(""); loadHealth(); });
$("refresh-jobs").addEventListener("click", loadHistory);
for (const type of ["dragenter", "dragover"]) $("drop-zone").addEventListener(type, (event) => { event.preventDefault(); $("drop-zone").classList.add("dragover"); });
for (const type of ["dragleave", "drop"]) $("drop-zone").addEventListener(type, (event) => { event.preventDefault(); $("drop-zone").classList.remove("dragover"); });
$("drop-zone").addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files[0];
  if (file) { $("audio-file").files = event.dataTransfer.files; selectFile(file); }
});

populateMeterChoices();
renderTimingMode();
Promise.allSettled([loadHealth(), loadHistory()]);
