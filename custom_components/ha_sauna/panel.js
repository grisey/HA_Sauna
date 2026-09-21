/* Native HA custom panel. Backend objects are authoritative; no control model here. */
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
const stamp = (v) => (v ? new Date(v).getTime() : null);
const when = (v) =>
  v
    ? new Date(v).toLocaleString("de-DE", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "–";
const clock = (v) =>
  new Date(v).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const num = (v, d = 1) =>
  v == null ? "–" : Number(v).toLocaleString("de-DE", { maximumFractionDigits: d });
const duration = (v, unit = "min") =>
  v == null
    ? "–"
    : `${Math.floor(Math.max(0, v) / 60)}:${String(Math.floor(Math.max(0, v) % 60)).padStart(2, "0")} ${unit}`;
const phases = {
  aus: "Aus",
  aufheizen: "Aufheizen",
  bereit: "Bereit",
  saunagang: "Saunagang",
  nachlauf: "Nachlauf",
  zwangskühlung: "Zwangskühlung",
  manuell: "Manuell",
};
const events = {
  door_open: "Tür geöffnet",
  door_close: "Tür geschlossen",
  person_strong: "Person erkannt",
  person_weak: "Person erkannt (schwach)",
  infusion: "Aufguss",
  ventilation_confirmed: "Durchlüften bestätigt",
  operation_off: "Betrieb ausgeschaltet",
  confirmation_expired: "Vorläufigen Gang aufgehoben",
};
const signalText = {
  door_heating: "Temperaturabfall trotz Heizen",
  door_close: "Türschließung",
  door_open: "Türöffnung",
  door: "Tür",
  infusion: "Aufguss",
  strong: "Deutliches Personensignal",
  weak: "Schwaches Personensignal",
};
const errorText = (error) => {
  // hass.callApi legt die Antwort der Integration in body ab; error enthält
  // lediglich den allgemeinen HTTP-Fehler (etwa „Response error: 409“).
  const detail = error?.body?.error || error?.body?.message;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (error?.status_code)
    return (
      {
        401: "Die Anmeldung ist abgelaufen. Bitte Home Assistant neu laden.",
        403: "Für diese Änderung sind Administratorrechte erforderlich.",
        409: "Die Aktion ist im aktuellen Zustand nicht möglich. Bitte die Hinweise zur Einrichtung prüfen.",
        503: "Die Sauna-Integration wird gerade neu geladen. Bitte kurz warten.",
      }[error.status_code] ||
      `Die Anfrage konnte nicht verarbeitet werden (HTTP ${error.status_code}).`
    );
  if (error?.error === "Request error")
    return "Home Assistant ist zurzeit nicht erreichbar. Bitte die Verbindung prüfen.";
  return error?.message || error?.error || String(error);
};
const temperatureDial = Object.freeze({
  centerX: 150,
  centerY: 130,
  radius: 105,
  startAngle: 135,
  endAngle: 405,
  scale: 120,
  path: "M 75.75 204.25 A 105 105 0 1 1 224.25 204.25",
});

// Split before reducing points: a pixel-sized reduction must never hide a
// missing measurement interval.  The cubic controls below are monotone, so
// the display is calm without inventing peaks between measurements.
const historySegments = (values, start, end, ttl) => {
  const segments = [];
  let segment = [];
  for (const point of values) {
    const time = point.time ?? stamp(point.received_at);
    if (point.value == null) {
      if (segment.length) segments.push(segment);
      segment = [];
      continue;
    }
    if (
      segment.length &&
      ttl &&
      time - (segment.at(-1).time ?? stamp(segment.at(-1).received_at)) > ttl
    ) {
      segments.push(segment);
      segment = [];
    }
    segment.push(point);
  }
  if (segment.length) segments.push(segment);
  return segments;
};
const reduceHistorySegment = (segment, x) => {
  const output = [];
  let bucket = [],
    key = null;
  const flush = () => {
    if (!bucket.length) return;
    const keep = new Set([
      bucket[0],
      bucket.at(-1),
      bucket.reduce((a, b) => (a.value < b.value ? a : b)),
      bucket.reduce((a, b) => (a.value > b.value ? a : b)),
    ]);
    output.push(...bucket.filter((point) => keep.has(point)));
    bucket = [];
  };
  for (const point of segment) {
    const next = Math.floor(x(point.time ?? stamp(point.received_at)));
    if (key !== null && next !== key) flush();
    key = next;
    bucket.push(point);
  }
  flush();
  return output;
};
const monotoneHistoryPath = (points, x, y) => {
  if (!points.length) return "";
  const xy = points.map((point) => [
    x(point.time ?? stamp(point.received_at)),
    y(point.value),
  ]);
  if (xy.length === 1) return `M${xy[0][0].toFixed(2)},${xy[0][1].toFixed(2)}`;
  const slopes = xy
    .slice(1)
    .map(([nextX, nextY], i) => (nextY - xy[i][1]) / (nextX - xy[i][0] || 1));
  const tangents = xy.map((_, i) => {
    if (i === 0) return slopes[0];
    if (i === xy.length - 1) return slopes.at(-1);
    const previous = slopes[i - 1],
      next = slopes[i];
    if (previous * next <= 0) return 0;
    const before = xy[i][0] - xy[i - 1][0],
      after = xy[i + 1][0] - xy[i][0],
      w1 = 2 * after + before,
      w2 = after + 2 * before;
    return (w1 + w2) / (w1 / previous + w2 / next);
  });
  let path = `M${xy[0][0].toFixed(2)},${xy[0][1].toFixed(2)}`;
  for (let i = 0; i < xy.length - 1; i++) {
    const [x0, y0] = xy[i],
      [x1, y1] = xy[i + 1],
      dx = (x1 - x0) / 3;
    path += ` C${(x0 + dx).toFixed(2)},${(y0 + tangents[i] * dx).toFixed(2)} ${(x1 - dx).toFixed(2)},${(y1 - tangents[i + 1] * dx).toFixed(2)} ${x1.toFixed(2)},${y1.toFixed(2)}`;
  }
  return path;
};
const lowerBoundHistory = (values, time) => {
  let low = 0,
    high = values.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (values[middle].time < time) low = middle + 1;
    else high = middle;
  }
  return low;
};
const nearestHistoryPoint = (values, time, start, end) => {
  const first = lowerBoundHistory(values, start),
    after = lowerBoundHistory(values, end + 1);
  if (first === after) return null;
  const index = Math.max(first, Math.min(after - 1, lowerBoundHistory(values, time)));
  const before = index > first ? values[index - 1] : null,
    next = index < after ? values[index] : null;
  return !before
    ? next
    : !next
      ? before
      : time - before.time <= next.time - time
        ? before
        : next;
};
const historyWindowValues = (values, start, end) => {
  const first = lowerBoundHistory(values, start),
    after = lowerBoundHistory(values, end + 1);
  // Keep the samples that meet either edge of the clip.  They make a line
  // arrive at the viewport edge without turning a real TTL/missing gap into a
  // connection.
  return values.slice(Math.max(0, first - 1), Math.min(values.length, after + 1));
};

class SaunaPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.view = "overview";
    this.selected = "live";
    this.cache = new Map();
    this.zoom = 1;
    this.window = null;
    this.generation = 0;
    this.positions = new Set(["upper"]);
    this.historyDetail = false;
    this.chartPointers = new Map();
    this.chartDataIndex = null;
    this.historyDatasetRevision = 0;
    this.historyWindowRevision = 0;
    this.historyTimelineRevision = 0;
    this.historyOverviewCache = null;
  }
  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this.timer) this.start();
  }
  get hass() {
    return this._hass;
  }
  connectedCallback() {
    this.shell();
    if (this._hass) this.start();
  }
  disconnectedCallback() {
    clearInterval(this.timer);
    clearTimeout(this.historyWheelTimer);
    this.timer = null;
    this.generation++;
  }
  start() {
    this.refresh();
    this.timer = setInterval(() => this.refresh(), 2000);
  }
  $(selector) {
    return this.shadowRoot.querySelector(selector);
  }
  nodeKey(node) {
    if (node.nodeType !== 1) return null;
    return node.id
      ? `id:${node.id}`
      : node.dataset?.action
        ? `action:${node.dataset.action}`
        : null;
  }
  sameNode(current, next) {
    if (current.nodeType !== next.nodeType) return false;
    if (current.nodeType !== 1) return true;
    const currentKey = this.nodeKey(current),
      nextKey = this.nodeKey(next);
    return (
      current.nodeName === next.nodeName &&
      ((!currentKey && !nextKey) || currentKey === nextKey)
    );
  }
  patchAttributes(current, next) {
    for (const attribute of [...current.attributes])
      if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
    for (const attribute of [...next.attributes])
      if (current.getAttribute(attribute.name) !== attribute.value)
        current.setAttribute(attribute.name, attribute.value);
  }
  optionSignature(select) {
    return [...select.options]
      .map((option) => `${option.value}\u0000${option.text}\u0000${option.disabled}`)
      .join("\u0001");
  }
  patchNode(current, next) {
    if (current.nodeType === 3) {
      if (current.data !== next.data) current.data = next.data;
      return current;
    }
    const focused = this.shadowRoot.activeElement === current;
    if (focused && ["INPUT", "SELECT", "TEXTAREA"].includes(current.nodeName))
      return current;
    this.patchAttributes(current, next);
    if (
      current.nodeName === "SELECT" &&
      this.optionSignature(current) === this.optionSignature(next)
    ) {
      if (current.value !== next.value) current.value = next.value;
      return current;
    }
    if (current.nodeName === "INPUT" || current.nodeName === "TEXTAREA") {
      if (current.value !== next.value) current.value = next.value;
      if ("checked" in current) current.checked = next.checked;
    }
    const oldChildren = [...current.childNodes],
      used = new Set();
    let cursor = current.firstChild;
    for (const nextChild of [...next.childNodes]) {
      const key = this.nodeKey(nextChild);
      let match = key
        ? oldChildren.find((child) => !used.has(child) && this.nodeKey(child) === key)
        : null;
      if (!match && cursor && !used.has(cursor) && this.sameNode(cursor, nextChild))
        match = cursor;
      if (!match)
        match = oldChildren.find(
          (child) => !used.has(child) && this.sameNode(child, nextChild),
        );
      if (match) {
        used.add(match);
        this.patchNode(match, nextChild);
      } else {
        match = nextChild.cloneNode(true);
        used.add(match);
      }
      if (match !== cursor) current.insertBefore(match, cursor);
      cursor = match.nextSibling;
    }
    for (const child of oldChildren) if (!used.has(child)) child.remove();
    return current;
  }
  updateMarkup(selector, markup) {
    const target = this.$(selector);
    if (!target) return;
    if (!target.cloneNode) {
      target.innerHTML = markup;
      return;
    }
    const next = target.cloneNode(false);
    next.innerHTML = markup;
    this.patchNode(target, next);
  }
  async api(path, method = "GET", body) {
    return this.hass.callApi(method, `ha_sauna${path}`, body);
  }
  shell() {
    this.shadowRoot.innerHTML = `<style>
      :host {
        display: block;
        height: 100%;
        overflow: auto;
        color: var(--primary-text-color, #203331);
        background: var(--primary-background-color, #f4f7f6);
        font:
          15px/1.5 system-ui,
          sans-serif;
        --accent: #197367;
        --warm: #cd693a;
      }
      * {
        box-sizing: border-box;
      }
      main {
        max-width: 1280px;
        margin: auto;
        padding: 28px 32px 60px;
      }
      header {
        display: flex;
        align-items: center;
        gap: 18px;
        margin-bottom: 24px;
      }
      h1 {
        font-size: 28px;
        letter-spacing: -0.6px;
        margin: 0;
      }
      h2 {
        font-size: 19px;
        margin: 0 0 12px;
      }
      h3 {
        font-size: 14px;
        margin: 12px 0 8px;
      }
      p {
        margin: 8px 0;
      }
      small,
      .muted {
        color: var(--secondary-text-color, #657572);
        font-size: 13px;
      }
      .grow {
        flex: 1;
      }
      .row {
        display: flex;
        gap: 12px;
        align-items: center;
        flex-wrap: wrap;
      }
      button,
      select,
      input {
        font: inherit;
        border: 1px solid var(--divider-color, #d4dfdc);
        border-radius: 8px;
        padding: 9px 13px;
        background: var(--card-background-color, #fff);
        color: inherit;
      }
      button {
        cursor: pointer;
      }
      button:hover {
        border-color: var(--accent);
      }
      button:disabled {
        opacity: 0.5;
        cursor: default;
      }
      button.primary {
        background: var(--accent);
        color: white;
        border-color: var(--accent);
      }
      button.stop {
        background: #a34029;
        color: white;
      }
      button[aria-selected="true"] {
        background: var(--accent);
        color: white;
      }
      a {
        color: var(--accent);
      }
      .tabs {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin: 8px 0 12px;
      }
      .card {
        background: var(--card-background-color, #fff);
        border: 1px solid var(--divider-color, #dce5e2);
        border-radius: 14px;
        padding: 22px;
        margin: 16px 0;
      }
      .hero {
        border-left: 4px solid var(--accent);
      }
      .phase {
        font-size: 28px;
        font-weight: 650;
        letter-spacing: -0.5px;
      }
      .badge {
        display: inline-block;
        border-radius: 20px;
        padding: 3px 11px;
        background: #e4f2ed;
        color: #24584c;
        font-size: 13px;
      }
      .notice {
        background: #fff1db;
        color: #6f481d;
        border-left: 4px solid #d79a42;
        padding: 12px 16px;
        border-radius: 8px;
        margin: 10px 0;
      }
      .error {
        background: #fcebe6;
        color: #862d21;
      }
      .legend {
        display: flex;
        gap: 18px;
        flex-wrap: wrap;
        font-size: 13px;
      }
      .dot {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        margin-right: 6px;
      }
      .upper {
        background: #197367;
      }
      .lower {
        background: #bd7133;
      }
      .chart {
        width: 100%;
        height: auto;
        display: block;
        touch-action: pan-y;
      }
      .chart text {
        fill: var(--secondary-text-color, #6d7874);
        font: 12px system-ui;
      }
      .chart .gridline {
        stroke: var(--divider-color, #e2e9e6);
        stroke-width: 1;
      }
      .chart .gang {
        fill: #5eaa9633;
        stroke: #5eaa96;
      }
      .chart .provisional {
        fill: #dfa94b22;
        stroke: #bb8b35;
        stroke-dasharray: 5 4;
      }
      .chart .heat {
        fill: #d8764433;
      }
      .chart .event {
        stroke: #83978e;
        stroke-dasharray: 3 5;
      }
      .chart .infusion {
        stroke: #426bbb;
      }
      .chart path {
        fill: none;
        stroke-width: 2;
      }
      .chart path.upper {
        stroke: #197367;
      }
      .chart path.lower {
        stroke: #bd7133;
      }
      .scroll {
        overflow: auto;
      }
      table {
        border-collapse: collapse;
        width: 100%;
        font-size: 13px;
      }
      td,
      th {
        text-align: left;
        padding: 10px 12px;
        border-bottom: 1px solid var(--divider-color, #e4ebe8);
        white-space: nowrap;
      }
      th {
        font-weight: 600;
      }
      .toolbar {
        margin: 18px 0 8px;
      }
      input[type="range"] {
        padding: 0;
        max-width: 180px;
      }
      .forms {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 16px;
      }
      label.field {
        display: flex;
        flex-direction: column;
        gap: 6px;
        font-size: 13px;
      }
      label.field input {
        width: 100%;
      }
      fieldset {
        border: 0;
        padding: 0;
        margin: 0;
      }
      details {
        margin: 18px 0;
      }
      summary {
        cursor: pointer;
        font-weight: 600;
        margin-bottom: 14px;
      }
      [hidden] {
        display: none !important;
      }
      #message:empty {
        display: none;
      }
      #settings .row {
        margin: 18px 0;
      }
      .empty {
        padding: 45px;
        text-align: center;
        color: var(--secondary-text-color, #657572);
      }
      @media (max-width: 800px) {
        main {
          padding: 18px 16px 40px;
        }
        .forms {
          grid-template-columns: repeat(2, 1fr);
        }
        header {
          gap: 10px;
        }
        h1 {
          font-size: 23px;
        }
        .card {
          padding: 16px;
        }
        header select {
          max-width: 180px;
        }
      }
      @media (max-width: 450px) {
        .forms {
          grid-template-columns: 1fr;
        }
        .phase {
          font-size: 24px;
        }
        .legend {
          gap: 12px;
        }
      }
      :host {
        --accent: #e58a55;
      }
      main {
        max-width: none;
      }
      header {
        margin-bottom: 12px;
      }
      .plot-panel {
        padding: 20px 12px 30px;
        background: #111;
        color: #c8c8c8;
        border-radius: 8px;
      }
      .plot-title {
        text-align: center;
        color: #aaa;
        font-weight: 500;
        font-size: 18px;
      }
      .legend {
        justify-content: center;
        margin: 18px 0;
      }
      .legend i {
        display: inline-block;
        width: 32px;
        height: 5px;
        margin-right: 6px;
        vertical-align: middle;
      }
      .top-legend {
        margin: 24px 0 0;
      }
      .position-select {
        justify-content: center;
        font-size: 12px;
        gap: 8px;
      }
      .position-select button {
        background: transparent;
        color: #aaa;
        border: 0;
        padding: 6px;
      }
      .position-select button[aria-pressed="false"] {
        opacity: 0.35;
      }
      .plot-wrap {
        position: relative;
      }
      .chart {
        height: 520px;
        width: 100%;
        touch-action: pan-y;
        user-select: none;
      }
      .chart text {
        fill: #aaa;
      }
      .detector-chart {
        height: 220px;
      }
      .main-tabs {
        border-bottom: 1px solid var(--divider-color, #333);
        padding-bottom: 8px;
      }
      .normal-tabs,
      .detail-tabs {
        font-size: 13px;
      }
      .chart .gridline {
        stroke: rgba(255, 255, 255, 0.1);
      }
      .chart .gang {
        fill: rgba(165, 30, 85, 0.46);
        stroke: none;
      }
      .chart .provisional {
        stroke: #a51e55;
        stroke-dasharray: 5 4;
      }
      .chart .heat {
        fill: rgba(255, 150, 35, 0.28);
      }
      .chart .ready {
        fill: rgba(38, 125, 82, 0.3);
      }
      .chart .vent {
        fill: rgba(58, 125, 155, 0.3);
      }
      .chart .cool {
        fill: rgba(103, 130, 154, 0.3);
      }
      .chart .after {
        fill: rgba(58, 125, 155, 0.18);
      }
      .chart .door {
        fill: rgba(255, 205, 80, 0.75);
      }
      .chart .infusion {
        stroke: #f5f5f5;
        stroke-width: 1;
      }
      .chart path {
        stroke-width: 3.5;
      }
      .chart path.temperature {
        stroke: #ff6b4a;
      }
      .chart path.humidity {
        stroke: #42a5ff;
      }
      .chart path.lower {
        stroke-dasharray: 8 5;
        opacity: 0.75;
      }
      .chart .axis-temperature {
        fill: #ff6b4a;
      }
      .chart .axis-humidity {
        fill: #42a5ff;
      }
      .plot-note {
        text-align: center;
        color: #999;
      }
      #tooltip {
        position: absolute;
        pointer-events: none;
        background: #f6f6f6;
        color: #333;
        padding: 10px 13px;
        font: 13px/1.5 system-ui;
        border: 1px solid #999;
        z-index: 2;
        box-shadow: 0 3px 10px #0003;
      }
      .dashboard {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        gap: 22px;
        max-width: 1280px;
        margin: 0 auto;
        align-items: start;
      }
      .tiles {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
      }
      .tile {
        padding: 12px;
        text-align: left;
        min-height: 54px;
      }
      .tile.full {
        grid-column: 1/-1;
      }
      .operation-control {
        grid-column: 1/-1;
      }
      .operation-control .tile {
        width: 100%;
        text-align: center;
      }
      .session-status {
        display: block;
        margin-top: 7px;
        text-align: center;
      }
      .greeting {
        text-align: center;
        font-size: 21px;
        margin: 16px 0;
      }
      .dial {
        max-width: 240px;
        display: block;
        margin: auto;
      }
      .dial text {
        fill: var(--primary-text-color, #ddd);
      }
      .dial .reading {
        font-size: 33px;
      }
      .dial .caption {
        font-size: 13px;
      }
      .dial .target-reading {
        font-size: 16px;
        font-weight: 650;
        fill: var(--accent);
      }
      .dial .tick {
        font-size: 14px;
        fill: var(--secondary-text-color, #aaa);
      }
      .dashboard .card {
        margin-top: 0;
      }
      .gauge-card h2 {
        text-align: center;
      }
      .temperature-choice {
        display: flex;
        gap: 8px;
        align-items: center;
        justify-content: center;
        margin-top: 10px;
      }
      .temperature-choice input {
        width: 95px;
      }
      @media (max-width: 800px) {
        .dashboard {
          grid-template-columns: 1fr;
        }
        .chart {
          height: 420px;
        }
        .detector-chart {
          height: 180px;
        }
        .plot-panel {
          padding: 12px 0;
        }
        .legend {
          font-size: 12px;
          gap: 12px;
        }
        .legend i {
          width: 22px;
        }
        main {
          padding: 14px;
        }
      }

      .notice button {
        background: #6f481d;
        color: #fff;
        border-color: #6f481d;
      }
      .card,
      .dashboard > *,
      .detail-grid > *,
      .forms > * {
        min-width: 0;
      }
      .notice,
      p,
      .field {
        overflow-wrap: anywhere;
      }
      .gauges {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
        text-align: center;
      }
      .gauge-card h2 {
        font-size: 16px;
      }
      .temperature-choice {
        flex-wrap: wrap;
      }
      .temperature-choice input {
        min-width: 0;
        width: 80px;
      }
      .tiles {
        gap: 10px;
      }
      .timer-strip {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
        margin: 18px 0;
        padding: 14px 0;
        border-block: 1px solid var(--divider-color, #333);
      }
      .timer-strip small,
      .timer-strip strong {
        display: block;
      }
      .timer-strip strong {
        font-size: 21px;
        font-variant-numeric: tabular-nums;
      }
      .timer-strip small {
        font-size: 12px;
      }
      .detail-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 18px;
      }
      .detail-grid .card {
        margin: 0;
      }
      .center {
        text-align: center;
      }
      dl {
        margin: 0;
      }
      dt {
        font-size: 13px;
        color: var(--secondary-text-color, #aaa);
        margin-top: 12px;
      }
      dd {
        margin: 2px 0 8px;
      }
      .forms {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 22px;
      }
      .settings-group {
        margin: 12px 0;
        border: 1px solid var(--divider-color, #333);
        border-radius: 10px;
        padding: 0 16px;
      }
      .settings-group summary {
        padding: 14px 0;
        margin: 0;
        font-size: 16px;
      }
      .settings-group[open] summary {
        border-bottom: 1px solid var(--divider-color, #333);
        margin-bottom: 16px;
      }
      .settings-group .forms {
        padding-bottom: 18px;
      }
      .expert-group + .expert-group {
        border-top: 1px solid var(--divider-color, #333);
        margin-top: 20px;
        padding-top: 12px;
      }
      .field small {
        font-size: 12px;
      }
      .feedback {
        font-weight: 650;
      }
      .feedback.on {
        color: #d86d35;
      }
      .feedback.off {
        color: #71817d;
      }
      .feedback.unknown {
        color: #a98536;
      }
      .light-controls {
        margin-top: 14px;
      }
      .program-buttons {
        grid-column: 1/-1;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
      }
      .program-compact {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 0;
      }
      .program-compact label {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        font-size: 13px;
      }
      .program-compact select {
        min-width: 0;
        max-width: 230px;
      }
      .program-compact small {
        white-space: nowrap;
      }
      .dial-temperature {
        touch-action: none;
      }
      .target-temperature-track {
        fill: none;
        stroke: var(--accent);
        stroke-width: 22;
        stroke-linecap: round;
        opacity: 0.35;
        cursor: pointer;
      }
      .target-temperature-handle {
        fill: var(--accent);
        stroke: var(--card-background-color, #fff);
        stroke-width: 4;
        cursor: pointer;
      }
      .target-temperature-track:focus {
        outline: none;
        stroke: #f1ba72;
        opacity: 0.8;
      }
      .manual-section + .manual-section {
        border-top: 1px solid var(--divider-color, #333);
        margin-top: 16px;
        padding-top: 16px;
      }
      .manual-status {
        display: inline-block;
        border-radius: 20px;
        padding: 3px 10px;
        font-size: 13px;
        font-weight: 650;
        background: #e6ece9;
        color: #53635d;
      }
      .manual-status.on {
        background: #f9dfd4;
        color: #963f24;
      }
      .manual-status.off {
        background: #e1eeea;
        color: #216551;
      }
      .manual-light-value {
        width: 92px;
        text-align: right;
        font-variant-numeric: tabular-nums;
      }
      .diagnostic-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
      }
      .diagnostic-grid .plot-panel {
        margin: 0;
        padding: 14px 10px;
      }
      .event-strip {
        --event-marker-size: 17px;
        --event-marker-gap: 3px;
        position: relative;
        height: var(--event-strip-height, 64px);
        margin: 10px 50px 0;
        border-bottom: 1px solid #667;
      }
      .event-marker {
        position: absolute;
        bottom: var(--event-marker-bottom, -6px);
        width: var(--event-marker-size);
        height: var(--event-marker-size);
        padding: 0;
        border-radius: 50%;
        border: 2px solid #f5f5f5;
        background: #e58a55;
        color: #111;
        font-size: 10px;
        font-weight: 700;
        line-height: 13px;
        transform: translateX(-50%);
      }
      .event-marker[data-selected="true"],
      .event-row[data-selected="true"] {
        outline: 3px solid var(--accent);
        outline-offset: 2px;
        background: #fff1db;
      }
      .event-row button {
        padding: 3px 7px;
        white-space: nowrap;
      }
      .temperature-presets {
        grid-column: 1/-1;
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-top: 10px;
      }
      .temperature-presets .tile {
        flex: 1 1 64px;
        min-height: 40px;
        padding: 8px 10px;
        text-align: center;
      }
      .program-buttons button {
        white-space: nowrap;
        font-size: 13px;
        padding: 8px 10px;
      }
      .compact-times {
        display: grid;
        grid-template-columns: minmax(150px, 1fr) minmax(180px, 2fr);
        column-gap: 16px;
        align-items: baseline;
      }
      .compact-times dt,
      .compact-times dd {
        margin-top: 8px;
        margin-bottom: 0;
      }
      .toolbar {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        gap: 8px;
        align-items: center;
        margin: 10px 0 6px;
      }
      .history-zoom {
        display: flex;
        gap: 4px;
      }
      .history-zoom button {
        min-width: 32px;
        height: 32px;
        padding: 3px 7px;
        line-height: 1;
      }
      .history-window {
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 2px 10px;
        min-width: 0;
      }
      .history-overview {
        grid-column: 1/-1;
        width: 100%;
        min-width: 0;
      }
      .history-overview svg {
        width: 100%;
        height: 32px;
        display: block;
        touch-action: none;
        cursor: grab;
      }
      .history-overview .overview-track {
        fill: #ffffff0d;
        stroke: #ffffff24;
      }
      .history-overview .overview-temperature {
        fill: none;
        stroke: #d98667;
        stroke-width: 1.5;
      }
      .history-overview .overview-window {
        fill: #a5b1ad33;
        stroke: #c1cbc6;
        stroke-width: 1;
        cursor: grab;
      }
      .history-overview .overview-handle {
        fill: #c1cbc6;
        cursor: ew-resize;
      }
      .history-overview svg:active {
        cursor: grabbing;
      }
      #range {
        font-size: 12px;
        line-height: 1.2;
        text-align: right;
      }
      .diagnostic-grid .detector-chart {
        height: auto;
      }
      /* Primärmarker liegen auf dem gespeicherten Merkmalswert, nicht auf einer separaten Zeitachse. */
      .diagnostic-marker {
        position: static;
        bottom: auto;
        width: auto;
        height: auto;
        padding: 0;
        border: 0;
        border-radius: 0;
        background: none;
        color: inherit;
        font-size: inherit;
        font-weight: inherit;
        line-height: normal;
        transform: none;
        cursor: pointer;
        outline: none;
        pointer-events: all;
      }
      .diagnostic-marker[data-selected="true"] {
        outline: none;
        background: none;
      }
      .diagnostic-marker .event-marker-dot {
        fill: #f4b183;
        stroke: #21150e;
        stroke-width: 2;
      }
      .diagnostic-marker .event-marker-point {
        fill: #f4b183;
        stroke: #111;
        stroke-width: 1;
      }
      .diagnostic-marker .event-marker-link {
        stroke: #f4b183;
        stroke-width: 1.5;
        stroke-dasharray: 2 2;
      }
      .diagnostic-marker .event-marker-label {
        fill: #17110c;
        font: 700 11px system-ui;
        paint-order: stroke;
        stroke: #fff4df;
        stroke-width: 3px;
        stroke-linejoin: round;
      }
      .diagnostic-marker[data-selected="true"] .event-marker-dot {
        fill: #fff1db;
        stroke: #e58a55;
        stroke-width: 3;
      }
      .event-row[data-selected="true"] {
        background: #fff1db;
        color: #1d2421;
        outline: 3px solid var(--accent);
        outline-offset: -3px;
      }
      .event-row[data-selected="true"] button {
        background: #fff1db;
        color: #1d2421;
        border-color: #1d2421;
      }
      .state-summary {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 10px 15px;
      }
      .state-line {
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        align-items: baseline;
        gap: 12px;
      }
      .state-line .badge {
        align-self: start;
        flex: none;
        white-space: nowrap;
      }
      .availability-line {
        min-width: 0;
        text-align: left;
        font-weight: 600;
        font-variant-numeric: tabular-nums;
        overflow-wrap: anywhere;
      }
      .availability-line.wait {
        color: #96651d;
      }
      @media (max-width: 1000px) {
        .dashboard {
          grid-template-columns: 1fr;
        }
        .dashboard .gauge-card {
          max-width: none;
        }
        .dial {
          max-width: 210px;
        }
      }
      @media (max-width: 600px) {
        .detail-grid,
        .forms,
        .diagnostic-grid {
          grid-template-columns: 1fr;
        }
        .compact-times {
          grid-template-columns: 1fr;
        }
        .gauges {
          gap: 4px;
        }
        .gauges h2 {
          font-size: 14px;
        }
        .card {
          padding: 14px;
        }
        .timer-strip strong {
          font-size: 18px;
        }
        .temperature-choice {
          gap: 6px;
        }
        header select {
          max-width: 110px;
        }
        .state-line,
        .state-summary {
          align-items: flex-start;
        }
        .toolbar {
          gap: 6px;
        }
        .history-window {
          gap: 2px 6px;
        }
        .history-zoom button {
          padding-inline: 6px;
        }
      }
      button[aria-pressed="true"] {
        background: var(--accent);
        color: white;
        border-color: var(--accent);
      }
      .control-mode-card {
        padding: 12px 16px;
        margin: 10px auto;
        max-width: 1280px;
      }
      .control-mode-card .row {
        justify-content: center;
      }
      .manual-overrides {
        border-left: 4px solid #9a6b2f;
        margin-top: 16px;
        padding-left: 14px;
      }
      .manual-section .row {
        margin-top: 10px;
      }
      .manual-section .muted {
        display: block;
        margin-top: 10px;
      }
      .phase-time {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 6px 10px;
        min-width: 0;
      }
      .control-section {
        margin-top: 16px;
        padding-top: 16px;
        border-top: 1px solid var(--divider-color, #333);
      }
      .control-section h3 {
        margin: 0 0 10px;
      }
      .control-section .row,
      .program-form .row {
        align-items: end;
      }
      .program-types {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        margin: 10px 0;
      }
      .program-named-list {
        display: grid;
        gap: 6px;
        margin: 10px 0;
      }
      .program-named-choice {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 10px;
        width: 100%;
        text-align: left;
      }
      .program-named-choice span {
        font-weight: 650;
      }
      .program-named-choice small {
        white-space: normal;
        text-align: right;
      }
      .program-named-choice[aria-pressed="true"] small {
        color: inherit;
      }
      .program-actions {
        display: flex;
        gap: 8px;
        margin-top: 16px;
        padding-top: 14px;
        border-top: 1px solid var(--divider-color, #333);
      }
      .program-saved {
        color: #216551;
        border-color: #9bcab6;
      }
      .program-saved:disabled {
        opacity: 1;
        background: #e4f2ed;
      }
      .program-kind {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        margin: 10px 0;
      }
      .program-form {
        margin-top: 10px;
      }
      .program-form .field {
        position: relative;
        flex: 1 1 105px;
      }
      .program-step-fields {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(105px, 1fr));
        gap: 8px;
        margin-top: 10px;
      }
      .program-info {
        display: inline-flex;
        align-items: center;
      }
      .program-info button {
        width: 25px;
        height: 25px;
        border-radius: 50%;
        padding: 0;
      }
      .program-info-popup {
        position: absolute;
        z-index: 5;
        top: 100%;
        left: 0;
        min-width: 0;
        width: 100%;
        max-width: 320px;
        padding: 9px 11px;
        border: 1px solid var(--divider-color, #333);
        border-radius: 8px;
        background: var(--card-background-color, #fff);
        box-shadow: 0 4px 14px #0003;
        font-size: 12px;
        line-height: 1.4;
        overflow-wrap: anywhere;
      }
      .manual-section h3 {
        margin: 0;
      }
      .manual-section .row {
        align-items: center;
      }
      @media (max-width: 600px) {
        .main-tabs {
          margin-bottom: 8px;
        }
        .main-tabs button {
          padding: 7px 10px;
        }
        .phase-time {
          gap: 3px 8px;
        }
        .temperature-automation .row {
          align-items: stretch;
        }
        .temperature-automation .field {
          flex: 1 1 110px;
        }
      }
      @media (max-width: 420px) {
        .gauges {
          grid-template-columns: 1fr;
          gap: 16px;
        }
        .dial {
          max-width: 270px;
        }
        .dial .tick {
          font-size: 14px;
        }
      }
    </style><main>
      <header><button data-action="menu" aria-label="Menü öffnen">☰</button><div><h1>Sauna</h1><small>Steuerung und Verlauf</small></div><span class="grow"></span><select id="instance" aria-label="Sauna auswählen"></select></header>
      <nav class="tabs main-tabs" aria-label="Ansicht"><button data-action="normal" aria-selected="true">Übersicht</button><button data-action="details" aria-selected="false">Details</button></nav>
      <nav class="tabs normal-tabs" aria-label="Übersicht"><button data-action="overview" aria-selected="true">Steuerung</button><button data-action="history" aria-selected="false">Verlauf und Archiv</button></nav>
      <nav class="tabs detail-tabs" aria-label="Detailansicht" hidden><button data-action="detail" aria-selected="true">Betrieb & Fristen</button><button data-action="detail-history" aria-selected="false">Detailverlauf</button><button data-action="diagnostics" aria-selected="false">Erkennungskontrolle</button><button data-action="settings" aria-selected="false">Einstellungen & Export</button></nav>
      <div id="message" role="alert"></div><section id="current" aria-live="polite"><p>Lade Saunadaten …</p></section><section id="details" hidden></section>
      <section id="history" hidden><div class="row"><h2 class="grow">Sitzungsverlauf</h2><select id="session" aria-label="Saunasitzung auswählen"><option value="live">Letzte Sitzung</option></select></div>
        <div class="row toolbar"><div class="history-zoom"><button data-action="zoom-in" aria-label="Vergrößern">＋</button><button data-action="zoom-out" aria-label="Verkleinern">−</button><button data-action="reset-zoom" aria-label="Gesamte Saunasitzung">Gesamt</button></div><div class="history-window"><div id="history-overview" class="history-overview" aria-label="Übersicht der gesamten Saunasitzung"></div><span id="range" class="muted"></span></div></div><div id="plots"></div><div id="detection-plots" hidden></div><div id="gangs"></div><div id="event-list"></div>
      </section><section id="settings" hidden></section>
    </main>`;
    this.shadowRoot.addEventListener("click", (e) => {
      const b = e.target.closest("[data-action]");
      if (b) this.action(b.dataset.action).catch((err) => this.message(err));
    });
    this.shadowRoot.addEventListener("change", (e) => {
      if (e.target.id === "instance") {
        this.entry = e.target.value;
        this.highlightedEventId = null;
        this.generation++;
        this.selected = "live";
        this.cache.clear();
        this.invalidateHistoryIndex();
        this.settingsEntry = null;
        this.programSelectionDraft = null;
        this.lastNamedProgramId = null;
        this.progressionDraft = null;
        this.temperatureChange = null;
        this.zoom = 1;
        this.window = null;
        this.refresh(true);
      }
      if (e.target.id === "session") {
        this.selected = e.target.value;
        this.highlightedEventId = null;
        this.invalidateHistoryIndex();
        this.zoom = 1;
        this.window = null;
        this.refresh(true);
      }
      if (e.target.id === "target")
        this.action("target").catch((err) => this.message(err));
      if (e.target.matches("[data-program-kind]")) {
        this.setProgramKind(
          e.target.dataset.programKind,
          e.target.closest("[data-program-id]")?.dataset.programId,
        );
      }
      if (e.target.id === "button-program")
        this.action("button-program").catch((err) => this.message(err));
    });
    this.shadowRoot.addEventListener("input", (e) => {
      if (e.target.closest("#parameters")) e.target.dataset.edited = "true";
      if (e.target.matches("#progression-start,#progression-end,#progression-gangs")) {
        this.progressionDraft = {
          ...this.progressionDraft,
          [e.target.id]: e.target.value,
        };
        this.markProgramEdited();
      }
      if (e.target.matches("[data-free-step]")) {
        this.freeProgramStepsDraft = [
          ...this.shadowRoot.querySelectorAll("[data-free-step]"),
        ].map((input) => Number(input.value));
        this.markProgramEdited();
      }
      if (e.target.matches("[data-manual-light-value]"))
        this.manualLightDraft = e.target.value;
    });
    this.shadowRoot.addEventListener("change", (e) => {
      if (e.target.matches("[data-free-step-count]")) {
        this.freeProgramStepsDraft = this.resizeSteps(
          this.freeSteps(),
          Number(e.target.value),
        );
        this.markProgramEdited();
        this.drawCurrent();
      }
      if (e.target.matches("[data-program-step-count]")) {
        const row = e.target.closest("[data-program-id]"),
          draft = this.readProgramDraft();
        const program = draft.find((item) => item.id === row?.dataset.programId);
        if (program) {
          program.temperature_steps = this.resizeSteps(
            program.temperature_steps || [],
            Number(e.target.value),
          );
          this.programDraft = draft;
          this.programLibraryNeedsRender = true;
          this.renderProgramLibrary();
        }
      }
    });
    this.shadowRoot.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.programInfoOpen) {
        const open = this.programInfoOpen;
        this.programInfoOpen = null;
        if (open.startsWith("catalog:")) {
          this.programLibraryNeedsRender = true;
          this.renderProgramLibrary();
        } else this.drawCurrent();
      }
      if (e.target.closest("[data-target-arc]"))
        this.keyTemperatureTarget(e).catch((err) => this.message(err));
    });
    this.shadowRoot.addEventListener("pointerdown", (e) => {
      const overview = e.target.closest("#history-overview svg");
      if (overview) return this.beginHistoryGesture(e, overview);
      const target = e.target.closest("[data-target-arc]");
      if (target) return this.beginTemperatureDrag(e, target.closest("svg"), target);
      const svg = e.target.closest("svg.session-chart");
      if (svg && e.pointerType !== "mouse") this.beginChartPointer(e, svg);
    });
    this.shadowRoot.addEventListener("pointerup", (e) => {
      if (this.temperatureInteraction?.pointerId === e.pointerId) {
        this.endTemperatureDrag(e).catch((err) => this.message(err));
        return;
      }
      if (this.historyGesture?.pointerId === e.pointerId)
        return this.endHistoryGesture(e);
      this.endChartPointer(e);
    });
    this.shadowRoot.addEventListener("pointercancel", (e) => {
      if (this.temperatureInteraction?.pointerId === e.pointerId)
        return this.cancelTemperatureDrag(e);
      if (this.historyGesture?.pointerId === e.pointerId)
        return this.endHistoryGesture(e);
      this.endChartPointer(e);
    });
    this.shadowRoot.addEventListener("pointermove", (e) => {
      if (this.temperatureInteraction?.pointerId === e.pointerId)
        return this.updateTemperatureDrag(e);
      if (this.historyGesture?.pointerId === e.pointerId)
        return this.updateHistoryGesture(e);
      const svg = e.target.closest("svg.session-chart");
      if (!svg) return;
      if (this.chartPointers.has(e.pointerId))
        this.chartPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.chartPointers.size === 2) {
        e.preventDefault();
        this.pinchZoom(svg);
        return;
      }
      if (this.historyInputMode !== "webkit")
        this.scheduleHover({ svg, clientX: e.clientX, clientY: e.clientY });
    });
    this.shadowRoot.addEventListener("pointerout", (e) => {
      if (
        e.target.closest("svg.session-chart") &&
        !e.relatedTarget?.closest?.("svg.session-chart")
      ) {
        this.pendingHover = null;
        this.lastHistoryPointer = null;
        this.$("#tooltip")?.setAttribute("hidden", "");
        this.$("#cursor")?.setAttribute("visibility", "hidden");
      }
    });
    this.shadowRoot.addEventListener(
      "wheel",
      (e) => {
        const svg = e.target.closest("svg.session-chart");
        if (!svg || !(e.ctrlKey || e.metaKey)) return;
        this.wheelHistoryGesture(e, svg);
      },
      { passive: false },
    );
    this.shadowRoot.addEventListener(
      "gesturestart",
      (e) => this.beginWebkitGesture(e),
      { passive: false },
    );
    this.shadowRoot.addEventListener(
      "gesturechange",
      (e) => this.updateWebkitGesture(e),
      { passive: false },
    );
    this.shadowRoot.addEventListener("gestureend", (e) => this.endWebkitGesture(e), {
      passive: false,
    });
    this.shadowRoot.addEventListener("gesturecancel", (e) => this.endWebkitGesture(e), {
      passive: false,
    });
    this.shadowRoot.addEventListener("submit", (e) => {
      e.preventDefault();
      this.saveSettings().catch((err) => this.message(err));
    });
  }
  message(error, source = "action") {
    this.messages ??= {};
    this.messages[source] = error;
    const shown = this.messages.action || this.messages.refresh;
    const node = this.$("#message");
    node.className = shown ? "notice error" : "";
    node.textContent = shown ? errorText(shown) : "";
  }
  async refresh(requested = false) {
    if (this.busy) {
      if (requested) this.refreshPending = true;
      return;
    }
    if (!this.isConnected) return;
    this.busy = true;
    const generation = this.generation;
    try {
      if (!this.entry) {
        const instances = await this.api("");
        this.$("#instance").innerHTML = instances
          .map((i) => `<option value="${esc(i.entry_id)}">${esc(i.title)}</option>`)
          .join("");
        this.$("#instance").hidden = instances.length < 2;
        if (!instances.length) {
          this.$("#current").innerHTML = "<p>Keine geladene Sauna vorhanden.</p>";
          return;
        }
        this.entry = instances[0].entry_id;
      }
      const entry = this.entry;
      const state = await this.api(`/${entry}/state`);
      if (generation !== this.generation || !this.isConnected) return;
      this.state = state;
      // The status cards remain live every two seconds.  Archive list/pages
      // are only useful while the history section is actually on screen.
      const historyVisible = !this.$("#history")?.hidden;
      if (historyVisible) {
        const list = await this.api(`/${entry}/archive`);
        if (generation !== this.generation || !this.isConnected) return;
        this.sessions = list;
        const liveLabel =
          state.operation_enabled && state.session
            ? "Laufende Sitzung"
            : "Letzte Sitzung";
        const optionHtml =
          `<option value="live">${liveLabel}</option>` +
          list
            .filter((s) => s.session_id !== state.session?.timeline.session_id)
            .map(
              (s) =>
                `<option value="${esc(s.session_id)}">Sitzung vom ${esc(when(s.started_at))}${s.ended_at ? " · beendet" : " · unterbrochen"}</option>`,
            )
            .join("");
        const sessionSelect = this.$("#session");
        if (sessionSelect.innerHTML !== optionHtml) {
          this.updateMarkup("#session", optionHtml);
          sessionSelect.value = this.selected;
        }
        const id =
          this.selected === "live"
            ? state.session?.timeline.session_id || list[0]?.session_id
            : this.selected;
        if (id !== this.historySessionId) {
          this.historySessionId = id;
          this.window = null;
          this.invalidateHistoryIndex();
          this.historyTimelineSignature = null;
        }
        if (id) {
          const cache = this.cache.get(id) || { records: [], after: 0 };
          if (this.selected === "live" || !cache.loaded) {
            let page;
            do {
              page = await this.api(
                `/${entry}/archive?session_id=${encodeURIComponent(id)}&after=${cache.after}`,
              );
              if (generation !== this.generation || !this.isConnected) return;
              cache.session = page.session;
              const additions = page.records.filter((r) =>
                [
                  "measurement",
                  "source_snapshot",
                  "diagnostic",
                  "phase",
                  "detector_trace",
                ].includes(r.kind),
              );
              if (additions.length) {
                cache.records.push(...additions);
                this.invalidateHistoryIndex();
              }
              cache.after = page.records.at(-1)?.id || cache.after;
            } while (page.next_after);
            cache.loaded = true;
            this.cache.set(id, cache);
          }
          this.shown = {
            session:
              this.selected === "live" ? state.session || cache.session : cache.session,
            records: cache.records,
          };
        } else this.shown = null;
      }
      if (!this.temperatureInteraction) {
        const restoreTargetFocus = this.shadowRoot.activeElement?.matches?.(
          '[data-target-arc][role="slider"]',
        );
        this.drawCurrent();
        if (restoreTargetFocus) this.$('[data-target-arc][role="slider"]')?.focus?.();
      }
      if (historyVisible) this.drawHistory();
      this.drawSettings();
      this.message(null, "refresh");
    } catch (error) {
      this.message(error, "refresh");
    } finally {
      this.busy = false;
      if (this.refreshPending) {
        this.refreshPending = false;
        void this.refresh(true);
      }
    }
  }
  drawCurrent() {
    const progressionOpen = this.$("#current details")?.open;
    const s = this.state,
      session = s.session,
      p = s.configuration.parameters,
      now = stamp(s.now),
      active = session?.timeline.active;
    const remaining = (end) => duration(end ? (stamp(end) - now) / 1000 : null);
    const latest = session || s.last_session;
    const doorText = session
      ? { open: "Tür offen", closed: "Tür geschlossen" }[session.timeline.door] ||
        "Türstatus noch nicht ermittelt"
      : "Türerkennung ruht";
    const count =
      latest?.timeline.completed.filter((g) => g.infusion_events.length).length ??
      s.gang_count;
    const value = (position, quantity) =>
      s.measurements.find((m) => m.position === position && m.quantity === quantity)
        ?.value;
    const quality = (position, quantity) =>
      s.measurement_status[`${position}_${quantity}`]?.state;
    const qualityText = (position, quantity) =>
      ({
        current: "Aktueller Messwert",
        stale: "Letzter Wert · veraltet",
        unavailable: "Messwert fehlt",
        validity_unconfigured: "Gültigkeit noch nicht eingestellt",
      })[quality(position, quantity)] || "Messwert fehlt";
    const formatValue = (position, quantity, unit) =>
      `${num(value(position, quantity), 1)} ${unit}`;
    const timer = s.mechanical_timer;
    const timerPause = {
      operation_off: "Angehalten · Saunabetrieb aus",
      contactor_off: "Angehalten · Schütz aus",
      contactor_unavailable: "Angehalten · Schützstellung unbekannt",
    };
    const timerStatus =
      timer.state === "paused"
        ? timerPause[timer.pause_reason] || "Angehalten"
        : {
            idle: "Noch nicht gestartet",
            running: "Geschätzte Restzeit bei eingeschaltetem Schütz",
            expired: "Drehschalter neu einstellen",
          }[timer.state];
    const timerText = timer.state === "idle" ? "–" : duration(timer.remaining_seconds);
    const energyLabel = {
      measured: "Gemessen",
      estimated: "Geschätzt",
      mixed: "Teilweise geschätzt",
      incomplete: "Unvollständig",
    };
    const energy = latest?.energy;
    const energyText = `${num(session ? s.energy_kwh : energy ? energy.measured_kwh + energy.estimated_kwh : 0, 3)} kWh · ${energyLabel[session ? s.energy_source : energy?.unknown_seconds ? "incomplete" : energy?.measured_seconds ? (energy.estimated_seconds ? "mixed" : "measured") : "estimated"]}`;
    const heatCaption =
      s.heating_feedback === true
        ? "Ofen an"
        : s.heating_feedback === false
          ? "Ofen aus"
          : "Ofenzustand unbekannt";
    const heatSource =
      {
        power: "Aus gemessener Leistung",
        independent_feedback: "Unabhängige Heizrückmeldung",
        contactor: "Nach Schützstellung geschätzt",
        unknown: "Rückmeldung fehlt",
      }[s.heating_observation?.source] || "Rückmeldung fehlt";
    const permissions = s.permissions || {};
    const manualMode = s.configuration.control_mode === "manual";
    const modeLocked = !!s.configuration_locked;
    const disabled = !permissions.admin;
    const canStart =
      permissions.control && (s.operation_enabled || !s.start_errors.length);
    const targetBounds = this.temperatureBounds();
    const presets = Array.from(
      { length: p.preset_count },
      (_, i) => p.preset_start_c + i * p.preset_step_c,
    ).filter((value) => value <= (targetBounds?.maximum ?? Infinity));
    const availability = s.start_availability;
    const fiveMinuteEstimate = (seconds) => {
      const minutes = Math.max(0, Number(seconds) || 0) / 60;
      if (minutes < 5) return "unter 5 Minuten";
      return `${Math.max(5, Math.round(minutes / 5) * 5)} Minuten`;
    };
    const startWindow = (seconds) => {
      const minutes = Math.max(0, Number(seconds) || 0) / 60;
      if (minutes < 5) return "unter 5 Minuten";
      return `${Math.floor(minutes / 5) * 5} Minuten`;
    };
    const timerLine = s.phase_timer;
    const phaseTimerText =
      s.operation_enabled && timerLine
        ? {
            gang: `seit ${duration(timerLine.seconds, "Minuten")}`,
            after_run: `${timerLine.mode === "paused" ? "pausiert – noch" : "noch"} ${duration(timerLine.seconds, "Minuten")}`,
            cooling: `noch ${duration(timerLine.seconds, "Minuten")}`,
          }[timerLine.kind] || ""
        : "";
    const blockerText =
      s.operation_enabled && availability?.blocker && !phaseTimerText
        ? availability.minimum_wait_seconds > 0
          ? `Start gesperrt – noch ${duration(availability.minimum_wait_seconds, "Minuten")}`
          : availability.message || "Start derzeit gesperrt."
        : "";
    const readinessText =
      s.operation_enabled &&
      s.phase === "aufheizen" &&
      availability &&
      !availability?.blocker &&
      availability.until_ready_seconds > 0 &&
      availability.ready_estimated
        ? `noch ${fiveMinuteEstimate(availability.until_ready_seconds)} bis bereit`
        : s.operation_enabled &&
            s.phase === "bereit" &&
            availability &&
            !availability?.blocker &&
            availability.until_ready_seconds === 0 &&
            availability.start_window_seconds > 0
          ? `noch ${startWindow(availability.start_window_seconds)}`
          : "";
    const availabilityLine = phaseTimerText || blockerText || readinessText;
    const availabilityText =
      s.operation_enabled &&
      availability &&
      !availabilityLine &&
      availability.until_ready_seconds !== 0
        ? availability.message || "Startzeit noch nicht abschätzbar."
        : "";
    const notices = s.issues
      .map(
        (i) =>
          `<p>${esc(i.message)}${i.action === "settings" ? '<br><button data-action="configure">Einstellungen öffnen</button>' : ""}</p>`,
      )
      .join("");
    const alert = notices ? `<div class="notice" role="alert">${notices}</div>` : "";
    const sessionIndicator =
      s.operation_enabled && session
        ? `<small class="session-status">${active ? (s.gang_confirmation === "confirmed" ? "Saunagang durch Aufguss bestätigt" : "Saunagang vorläufig · Aufguss ausstehend") : `Sitzung seit ${when(session.timeline.session_started_at)}`}</small>`
        : "";
    const operation = `<div class="operation-control"><button class="tile operation ${s.operation_enabled ? "stop" : "primary"}" data-action="operation" ${canStart ? "" : "disabled"}>${s.operation_enabled ? "Ausschalten" : "Einschalten"}</button>${sessionIndicator}</div>`;
    const phaseLabel =
      s.phase === "aufheizen" ? "Heizen" : phases[s.phase] || "Unbekannt";
    const stateLine = `<div class="state-line"><div class="phase-time"><strong class="phase" data-phase="${esc(s.phase)}">${phaseLabel}</strong><span class="availability-line ${availability?.blocker ? "wait" : ""}">${esc(availabilityLine || availabilityText || "")}</span></div><span class="badge">${count} ${count === 1 ? "Saunagang" : "Saunagänge"}</span></div>`;
    const dial = (
      reading,
      unit,
      caption,
      color,
      maximum,
      valid,
      control = "",
      minimum = 0,
    ) =>
      `<svg class="dial ${control ? "dial-temperature" : ""}" viewBox="0 0 300 260" role="${control ? "group" : "img"}" aria-label="${esc(control ? "Temperatur und Solltemperatur" : caption)}"><path d="${temperatureDial.path}" fill="none" stroke="var(--divider-color,#444)" stroke-width="16" stroke-linecap="round"/><path d="${temperatureDial.path}" fill="none" stroke="${valid ? color : "#888"}" stroke-width="16" stroke-linecap="round" pathLength="100" stroke-dasharray="${Math.max(0, Math.min(100, (((reading ?? minimum) - minimum) / (maximum - minimum || 1)) * 100))} 100"/><text class="reading" x="150" y="119" text-anchor="middle">${num(reading, 1)} ${unit}</text>${control}${caption ? `<text class="caption" x="150" y="183" text-anchor="middle">${esc(caption)}</text>` : ""}</svg>`;
    const deadlineLabels = {
      confirmation: "Aufgussbestätigung",
      person_opportunity: "Wartezeit auf Personenerkennung",
    };
    const phaseRemaining = (phase) =>
      Math.max(
        0,
        (phase.duration_seconds ?? 0) -
          (phase.elapsed_seconds ?? 0) -
          (phase.credited_seconds ?? 0),
      );
    const timerRows = [
      [
        "Heizsumme (gezählt)",
        `${duration(session?.heating.elapsed_seconds || 0)} · Grenze ${duration(s.heating_limit_seconds)}`,
      ],
      [
        "Mechanischer Ofentimer",
        `<span data-mechanical-timer>${timerText} · ${timerStatus}</span>`,
      ],
    ];
    if (session?.after_run) {
      const phase = session.after_run;
      timerRows.push([
        phase.paused_at ? "Nachlauf pausiert" : "Nachlauf",
        duration(phaseRemaining(phase)),
      ]);
    }
    if (session?.cooling) {
      const cycle = session.cooling;
      timerRows.push([
        cycle.paused_at
          ? "Zwangskühlung pausiert"
          : cycle.ends_at
            ? "Zwangskühlung"
            : "Zwangskühlung vorgemerkt",
        duration(phaseRemaining(cycle)),
      ]);
    }
    for (const deadline of session?.deadlines || []) {
      if (!deadlineLabels[deadline.purpose]) continue;
      timerRows.push([deadlineLabels[deadline.purpose], remaining(deadline.due_at)]);
    }
    const sessionGap = (session?.deadlines || []).find(
      (deadline) => deadline.purpose === "session_gap",
    );
    const lightAfterRun = s.light_after_run && stamp(s.light_after_run.ends_at) > now;
    if (sessionGap)
      timerRows.push([
        lightAfterRun ? "Wiederaufnahme und Lichtnachlauf" : "Wiederaufnahme",
        remaining(sessionGap.due_at),
      ]);
    else if (lightAfterRun)
      timerRows.push(["Lichtnachlauf", remaining(s.light_after_run.ends_at)]);
    for (const [label, ends] of [
      ["Ofenübersteuerung", s.manual_controls?.heater?.override_ends_at],
      ["Lichtübersteuerung", s.manual_controls?.light?.override_ends_at],
    ]) {
      if (ends && stamp(ends) > now) timerRows.push([label, remaining(ends)]);
    }
    if (
      s.phase_timer &&
      !new Set([
        "after_run",
        "cooling",
        "session_light",
        "session_gap",
        "person_wait",
      ]).has(s.phase_timer.kind)
    )
      timerRows.push([
        s.phase_timer.label,
        `<span data-phase-timer="${esc(s.phase_timer.kind)}">${duration(s.phase_timer.seconds)}</span>`,
        true,
      ]);
    const timers = `<dl class="compact-times">${timerRows.map(([label, value, html]) => `<dt>${esc(label)}</dt><dd>${html || label === "Mechanischer Ofentimer" ? value : esc(value)}</dd>`).join("")}</dl>`;
    const manualPhase = session?.after_run
      ? {
          purpose: "after_run",
          token: session.after_run.phase_id,
          label: "Nachlauf jetzt beenden",
        }
      : session?.cooling?.started_at
        ? {
            purpose: "forced_cooling",
            token: session.cooling.cycle_id,
            label: "Zwangskühlung jetzt beenden",
          }
        : null;
    const finishPhase = manualPhase
      ? `<div class="row"><button data-action="end-phase:${manualPhase.purpose}:${esc(encodeURIComponent(manualPhase.token))}" ${disabled ? "disabled" : ""}>${manualPhase.label}</button><small class="muted">Setzt den regulären Folgeablauf fort.</small></div>`
      : "";
    const temperatureColor =
      {
        aufheizen: "#e58a55",
        bereit: "#4f9a6a",
        saunagang: "#cf7447",
        nachlauf: "#5b8ca8",
        zwangskühlung: "#5b8ca8",
      }[s.phase] || "#888";
    const humidity = value("upper", "humidity"),
      humidityColor =
        humidity <= 20 ? "#4f9a6a" : humidity <= 30 ? "#c59b32" : "#bc5546";
    const targetValue =
        targetBounds &&
        this.clampTemperature(
          this.temperatureInteraction?.value ?? s.target_temperature,
          targetBounds,
        ),
      targetPoint = targetBounds && this.temperatureArcPoint(targetValue, targetBounds);
    const targetControl =
      !manualMode && targetBounds
        ? `${this.temperatureTickMarks(targetBounds)}<path class="target-temperature-track" data-target-arc="true" d="${temperatureDial.path}" role="slider" tabindex="${permissions.temperature ? 0 : -1}" aria-label="Solltemperatur einstellen" aria-valuemin="${targetBounds.minimum}" aria-valuemax="${targetBounds.maximum}" aria-valuenow="${targetValue}" aria-valuetext="${num(targetValue, 1)} °C" aria-disabled="${!permissions.temperature}"/><circle class="target-temperature-handle" data-target-arc="true" cx="${targetPoint.x}" cy="${targetPoint.y}" r="10"/><text class="target-reading" x="150" y="151" text-anchor="middle">Soll ${num(targetValue, 1)} °C</text>`
        : "";
    const programs = Array.isArray(s.configuration.temperature_programs)
      ? s.configuration.temperature_programs
      : [];
    const programChoice = this.programChoice(programs),
      programMode = this.programMode(programs);
    const programTypes = `<div class="program-types" role="group" aria-label="Temperaturprogramm auswählen"><button type="button" data-action="program-mode:program" aria-pressed="${programMode === "program"}" ${permissions.program && programs.length ? "" : "disabled"}>Programm</button><button type="button" data-action="program-mode:individual" aria-pressed="${programMode === "individual"}" ${permissions.program ? "" : "disabled"}>Individuell</button><button type="button" data-action="program-mode:constant" aria-pressed="${programMode === "constant"}" ${permissions.program ? "" : "disabled"}>Konstant</button></div>`;
    const namedPrograms =
      programMode === "program"
        ? `<div class="program-named-list">${programs.map((program) => `<button type="button" class="program-named-choice" data-action="program-select:${esc(program.id)}" aria-pressed="${program.id === programChoice}" ${permissions.program ? "" : "disabled"}><span>${esc(program.name)}</span><small>${esc(this.programSteps(program))}</small></button>`).join("")}</div>`
        : "";
    const light = s.manual_controls?.light || {};
    const roundedLight = (value) =>
      Number.isFinite(Number(value)) ? Math.round(Number(value)) : null;
    const manualLight = light.manual == null ? null : roundedLight(light.manual),
      normalLight = roundedLight(light.normal),
      brightLight = roundedLight(p.session_light_brightness_percent);
    const lightPreset =
      manualLight == null
        ? "auto"
        : manualLight <= 0
          ? "off"
          : manualLight === normalLight
            ? "normal"
            : manualLight === brightLight
              ? "bright"
              : null;
    const lightPresetButtons = (canControl, includeDimmed = manualMode) =>
      `<button data-action="light:false" aria-pressed="${lightPreset === "off"}" ${canControl ? "" : "disabled"}>Aus</button>${manualMode ? "" : `<button data-action="light:auto" aria-pressed="${lightPreset === "auto"}" ${canControl ? "" : "disabled"}>Automatik</button>`}${includeDimmed ? `<button data-action="light:normal" aria-pressed="${lightPreset === "normal"}" ${canControl ? "" : "disabled"}>Gedimmt <small>${num(light.normal, 0)} %</small></button>` : ""}<button data-action="light:true" aria-pressed="${lightPreset === "bright"}" ${canControl ? "" : "disabled"}>Hell <small>${num(p.session_light_brightness_percent, 0)} %</small></button>`;
    const automaticLightControls = `<section class="control-section automatic-light"><h3>Licht</h3><div class="row">${lightPresetButtons(permissions.light, false)}</div></section>`;
    const heater = s.manual_controls?.heater || {};
    const heaterMode =
      heater.manual === true ? "on" : heater.manual === false ? "off" : "auto";
    const heaterStatus =
      heater.manual === true
        ? "Manuell EIN"
        : heater.manual === false
          ? "Manuell AUS"
          : manualMode
            ? "Keine Ofenwahl"
            : heater.automatic === true
              ? "Automatik · Heizfreigabe EIN"
              : heater.automatic === false
                ? "Automatik · Heizfreigabe AUS"
                : "Automatik · Entscheidung ausstehend";
    const lightMode = light.manual == null ? "auto" : light.manual <= 0 ? "off" : "on";
    const lightStatus =
      light.manual == null
        ? manualMode
          ? "Keine Lichtwahl"
          : "Automatik"
        : `Manuell · ${num(light.manual, 0)} %`;
    const manualLightValue =
      this.manualLightDraft ?? light.manual ?? light.automatic ?? 0;
    const isAdmin = !!this.hass.user?.is_admin;
    const canManualHeater = isAdmin && permissions.heater;
    const canManualLight = isAdmin && permissions.light;
    const coolingPaused = session?.cooling?.paused_at
      ? '<p class="muted" data-cooling-status="paused">Zwangskühlung pausiert.</p>'
      : "";
    const modeControls = isAdmin
      ? `<div class="row"><small>Betriebsmodus</small><button data-action="control-mode:automatic" aria-selected="${!manualMode}" ${!modeLocked ? "" : "disabled"}>Automatik</button><button data-action="control-mode:manual" aria-selected="${manualMode}" ${!modeLocked ? "" : "disabled"}>Manuell</button>${modeLocked ? '<small class="muted">Während der Sitzung gesperrt.</small>' : ""}</div>`
      : "";
    const heaterControls = (scope) =>
      isAdmin
        ? `<section class="manual-section manual-heater"><div class="row">${scope === "overview" ? "<h3>Ofen</h3>" : ""}<span class="manual-status ${heaterMode}" data-heater-status="${heaterMode}">${heaterStatus}</span></div><div class="row"><button data-action="heater:true" aria-pressed="${heater.manual === true}" ${canManualHeater ? "" : "disabled"}>EIN</button><button data-action="heater:false" aria-pressed="${heater.manual === false}" ${canManualHeater ? "" : "disabled"}>AUS</button>${manualMode ? "" : `<button data-action="heater:auto" aria-pressed="${heater.manual == null}" ${canManualHeater ? "" : "disabled"}>Automatik</button>`}</div>${!s.operation_enabled ? '<small class="muted">EIN startet zuerst den Saunabetrieb.</small>' : ""}${coolingPaused}</section>`
        : "";
    const lightControls = (scope) => {
      const inputId =
          scope === "details" ? "manual-light-value" : "manual-light-value-overview",
        action = scope === "details" ? "manual-light" : "manual-light-overview";
      const fallback = `Vorübergehende Übersteuerungen folgen spätestens nach ${duration(p.manual_override_minutes * 60)} wieder der Automatik.`;
      return isAdmin
        ? `<section class="manual-section manual-light"><div class="row">${scope === "overview" ? "<h3>Licht</h3>" : ""}<span class="manual-status ${lightMode}" data-light-status="${lightMode}">${lightStatus}</span></div><div class="row">${lightPresetButtons(canManualLight, true)}</div><div class="row"><label for="${inputId}">Freie Helligkeit</label><input id="${inputId}" data-manual-light-value class="manual-light-value" type="number" min="0" max="100" step="1" inputmode="numeric" value="${esc(manualLightValue)}" ${canManualLight ? "" : "disabled"}><span>%</span><button data-action="${action}" ${canManualLight ? "" : "disabled"}>Übernehmen</button></div><small class="muted">${manualMode ? "Die Lichtwahl bleibt im manuellen Betrieb erhalten." : fallback}</small></section>`
        : "";
    };
    this.$('.main-tabs [data-action="details"]').hidden = !permissions.admin;
    const overviewLightTimer =
      !session && s.phase_timer?.kind === "session_light"
        ? `<p class="muted">Lichtnachlauf noch ${duration(s.phase_timer.seconds, "Minuten")}</p>`
        : "";
    const programBounds = this.programBounds();
    // Temperaturautomatik stays the internal CSS/API term; the control uses the shorter label.
    const temperatureAutomation = !manualMode
      ? `<section class="control-section temperature-automation"><h3>Temperaturwahl</h3>${programTypes}${namedPrograms}${programMode === "individual" ? this.freeProgramForm(programBounds, permissions) : ""}${programMode === "constant" ? `<div class="temperature-presets">${presets.map((v) => `<button type="button" class="tile" data-action="preset:${v}" aria-pressed="${Math.abs(v - s.target_temperature) < 0.01}" ${permissions.temperature ? "" : "disabled"}>${num(v, 1)} °C</button>`).join("")}</div>` : ""}${programMode !== "individual" ? `<div class="program-actions">${this.programApplyButton(permissions)}</div>` : ""}</section>`
      : "";
    const overviewQuality = (position, quantity) =>
      quality(position, quantity) === "current"
        ? ""
        : `<p class="muted">${qualityText(position, quantity)}</p>`;
    this.updateMarkup(
      "#current",
      `<h2 class="greeting">Servus ${esc(this.hass.user?.name || "")}</h2>${modeControls ? `<div class="card control-mode-card">${modeControls}</div>` : ""}<div class="dashboard"><div class="card">${stateLine}<div class="row muted"><span class="feedback ${s.heating_feedback === true ? "on" : s.heating_feedback === false ? "off" : "unknown"}">${heatCaption}</span></div>${overviewLightTimer}<div class="tiles">${operation}</div>${temperatureAutomation}${manualMode ? `<div class="manual-overrides">${heaterControls("overview")}${lightControls("overview")}</div>` : automaticLightControls}${alert}</div><div class="card gauge-card"><div class="gauges"><div><h2>Temperatur</h2>${dial(value("upper", "temperature"), "°C", "", temperatureColor, targetBounds?.maximum ?? temperatureDial.scale, quality("upper", "temperature") === "current", targetControl, targetBounds?.minimum ?? 0)}${overviewQuality("upper", "temperature")}</div><div><h2>Luftfeuchte</h2>${dial(humidity, "%", "Relative Luftfeuchte", humidityColor, 100, quality("upper", "humidity") === "current")}${overviewQuality("upper", "humidity")}</div></div><p class="muted" data-door-status>${doorText}</p></div></div>`,
    );
    const temperatureProgram =
      s.configuration.program_mode === "progressive"
        ? Array.isArray(s.configuration.temperature_steps)
          ? s.configuration.temperature_steps
              .map((value) => num(value, 1))
              .join(" → ") + " °C"
          : p.final_temperature_c != null
            ? `${num(p.target_temperature_c)} → ${num(p.final_temperature_c)} °C`
            : "Konstant"
        : "Konstant";
    const restartThreshold =
      s.thermostat_target == null
        ? null
        : s.thermostat_target - (p.readiness_hysteresis_c ?? 0);
    const power = s.heating_observation?.power_w;
    const powerText =
      power == null
        ? `${num(p.nominal_power_kw, 2)} kW · Nennleistung für die Verbrauchsschätzung`
        : `${num(power, 0)} W · gemessen`;
    const detectionText = session
      ? `Erkennung mit: ${(s.detection_channels || []).map((position) => (position === "upper" ? "oberer Messposition" : "unterer Messposition")).join(" und ") || "noch keiner Messposition"}.`
      : "Außerhalb einer Saunasitzung werden keine Türbewegungen ausgewertet.";
    this.updateMarkup(
      "#details",
      `<div class="card hero">${stateLine}${finishPhase}${operation}${timers}${alert}</div><div class="detail-grid"><div class="card"><h2>Ofen</h2>${heaterControls("details")}<dl><dt>Aktuelle Solltemperatur</dt><dd>${num(s.target_temperature, 1)} °C</dd><dt>Obere Regeltemperatur</dt><dd data-readiness>${num(s.thermostat_target, 1)} °C</dd><dt>Wiedereinschaltschwelle</dt><dd>${num(restartThreshold, 1)} °C</dd><dt>Temperaturprogramm</dt><dd>${temperatureProgram}</dd><dt>Heizzustand</dt><dd>${heatCaption}</dd><dt>Ermittlung der Heizzeit</dt><dd>${heatSource}</dd><dt>Energieverbrauch</dt><dd>${energyText}</dd><dt>Ofenleistung</dt><dd>${powerText}</dd></dl><p>${esc(s.decision_text)}</p></div><div class="card"><h2>Licht</h2>${lightControls("details")}</div><div class="card"><h2>Messung</h2><p data-door-status>${doorText}</p>${["upper", "lower"].map((pos) => `<h3>${pos === "upper" ? "Obere" : "Untere"} Messposition</h3><p>${formatValue(pos, "temperature", "°C")} · ${qualityText(pos, "temperature")}</p><p>${formatValue(pos, "humidity", "% relative Luftfeuchte")} · ${qualityText(pos, "humidity")}</p>`).join("")}<p class="muted">${esc(detectionText)}</p></div></div>`,
    );
    if (progressionOpen) this.$("#current details").open = true;
    for (const [id, value] of Object.entries(this.progressionDraft || {}))
      if (this.$(`#${id}`)) this.$(`#${id}`).value = value;
  }
  async changeTarget(value) {
    if (!Number.isFinite(value)) throw Error("Gültige Solltemperatur eingeben");
    this.programSelectionDraft = "constant";
    this.freeProgramKind = null;
    this.freeProgramStepsDraft = null;
    const entry = this.entry,
      previous = this.temperatureChange,
      draft = this.progressionDraft;
    const change = (async () => {
      await previous;
      if (this.entry !== entry) return;
      const saved = await this.api(`/${entry}/temperature`, "POST", {
        target_temperature_c: value,
      });
      // A dial request may finish after the user has already filled the
      // progression form. Keep that unsaved form until its own save succeeds.
      if (this.entry !== entry) return;
      if (this.progressionDraft === draft) this.progressionDraft = null;
      await this.refresh();
      return saved?.parameters;
    })();
    this.temperatureChange = change;
    try {
      await change;
    } finally {
      if (this.temperatureChange === change) this.temperatureChange = null;
    }
  }
  temperatureDefinition() {
    return this.state?.parameters?.find((d) => d.key === "target_temperature_c");
  }
  temperatureBounds() {
    const definition = this.temperatureDefinition(),
      parameters = this.state?.configuration?.parameters || {};
    const minimum = Number(definition?.minimum ?? parameters.sauna_min_temperature_c);
    const maximum = Number(definition?.maximum);
    return Number.isFinite(minimum) && Number.isFinite(maximum) && maximum >= minimum
      ? { minimum, maximum }
      : null;
  }
  temperatureStep() {
    return this.temperatureDefinition()?.integer ? 1 : 0.5;
  }
  programSteps(program) {
    if (Array.isArray(program?.temperature_steps))
      return (
        program.temperature_steps.map((value) => num(value, 1)).join(" → ") + " °C"
      );
    const start = Number(program?.start_c),
      end = Number(program?.end_c),
      gangs = Number(program?.distribution_gangs);
    if (
      !Number.isFinite(start) ||
      !Number.isFinite(end) ||
      !Number.isInteger(gangs) ||
      gangs < 1
    )
      return "";
    return (
      this.distributedSteps(start, end, gangs)
        .map((value) => num(value, 1))
        .join(" → ") + " °C"
    );
  }
  distributedSteps(start, end, gangs) {
    const maximum = Number(this.programBounds?.().gangMaximum),
      count = Number(gangs);
    if (
      !Number.isFinite(maximum) ||
      !Number.isInteger(count) ||
      count < 1 ||
      count > maximum
    )
      return [];
    return count === 1
      ? start === end
        ? [start]
        : [start, end]
      : Array.from(
          { length: count },
          (_, index) => start + ((end - start) * index) / (count - 1),
        );
  }
  programChoice(programs = []) {
    const choice = this.programSelectionDraft ?? this.storedProgramChoice(programs);
    return ["constant", "individual"].includes(choice) ||
      programs.some((program) => program.id === choice)
      ? choice
      : this.storedProgramChoice(programs);
  }
  programMode(programs = []) {
    const choice = this.programChoice(programs);
    return choice === "individual"
      ? "individual"
      : choice === "constant"
        ? "constant"
        : "program";
  }
  selectProgramMode(mode, programs = []) {
    if (mode === "program") {
      if (!programs.length) return;
      const current = this.programChoice(programs);
      this.programSelectionDraft = programs.some((program) => program.id === current)
        ? current
        : programs.find((program) => program.id === this.lastNamedProgramId)?.id ||
          programs.find(
            (program) => program.id === this.state?.configuration?.selected_program_id,
          )?.id ||
          programs[0].id;
    } else if (mode === "individual" || mode === "constant")
      this.programSelectionDraft = mode;
    else throw Error("Ungültige Temperaturwahl");
    if (programs.some((program) => program.id === this.programSelectionDraft))
      this.lastNamedProgramId = this.programSelectionDraft;
    this.markProgramEdited();
    this.drawCurrent();
  }
  selectNamedProgram(id, programs = []) {
    if (!programs.some((program) => program.id === id))
      throw Error("Unbekanntes Temperaturprogramm");
    this.programSelectionDraft = id;
    this.lastNamedProgramId = id;
    this.markProgramEdited();
    this.drawCurrent();
  }
  storedProgramChoice(programs = []) {
    const configuration = this.state?.configuration || {};
    if (programs.some((program) => program.id === configuration.selected_program_id))
      return configuration.selected_program_id;
    return configuration.program_mode === "progressive" ? "individual" : "constant";
  }
  valuesEqual(left, right) {
    return (
      left.length === right.length &&
      left.every((value, index) => Number(value) === Number(right[index]))
    );
  }
  markProgramEdited() {
    this.programSaveState = null;
    this.updateProgramApplyButton();
  }
  updateProgramApplyButton() {
    const button = this.$('[data-action="program-apply"]');
    if (!button) return;
    const saving = this.programSaveState === "saving",
      dirty = this.programDirty();
    button.disabled = !this.state?.permissions?.program || !dirty || saving;
    button.className = this.programApplyClass(dirty, saving);
    button.textContent = saving
      ? "Wird übernommen …"
      : this.programSaveState === "saved"
        ? "Übernommen"
        : "Übernehmen";
  }
  programDirty() {
    const programs = this.state?.configuration?.temperature_programs || [],
      choice = this.programChoice(programs);
    if (choice !== this.storedProgramChoice(programs)) return true;
    if (choice !== "individual") return false;
    const configuration = this.state.configuration,
      manual =
        this.freeProgramKind === "steps" ||
        (Array.isArray(configuration.temperature_steps) && !this.freeProgramKind);
    try {
      if (manual)
        return (
          !Array.isArray(configuration.temperature_steps) ||
          !this.valuesEqual(this.freeProgramValues(), configuration.temperature_steps)
        );
      const values = this.progressionValues(),
        parameters = configuration.parameters || {};
      return (
        Array.isArray(configuration.temperature_steps) ||
        values.start !== Number(parameters.target_temperature_c) ||
        values.end !== Number(parameters.final_temperature_c) ||
        values.gangs !== Number(parameters.temperature_gangs)
      );
    } catch (_error) {
      return true;
    }
  }
  programApplyClass(dirty, saving) {
    return dirty && !saving
      ? "primary"
      : this.programSaveState === "saved"
        ? "program-saved"
        : "";
  }
  programApplyButton(permissions) {
    const saving = this.programSaveState === "saving",
      dirty = this.programDirty(),
      label = saving
        ? "Wird übernommen …"
        : this.programSaveState === "saved"
          ? "Übernommen"
          : "Übernehmen";
    return `<button type="button" data-action="program-apply" class="${this.programApplyClass(dirty, saving)}" ${permissions.program && dirty && !saving ? "" : "disabled"}>${label}</button>`;
  }
  freeSteps() {
    const bounds = this.programBounds(),
      parameters = this.state?.configuration?.parameters || {},
      saved = this.state?.configuration?.temperature_steps;
    if (Array.isArray(this.freeProgramStepsDraft)) return this.freeProgramStepsDraft;
    if (Array.isArray(saved)) return [...saved];
    return this.distributedSteps(
      Number(
        parameters.target_temperature_c ??
          this.state?.target_temperature ??
          bounds.minimum,
      ),
      Number(
        parameters.final_temperature_c ??
          parameters.target_temperature_c ??
          this.state?.target_temperature ??
          bounds.minimum,
      ),
      Number(parameters.temperature_gangs ?? bounds.gangMinimum),
    );
  }
  resizeSteps(values, length) {
    const bounds = this.programBounds(),
      count = Number(length);
    if (!Number.isInteger(count) || count < 1 || !Number.isFinite(bounds.gangMaximum))
      return [];
    const result = [...values],
      limited = Math.min(count, bounds.gangMaximum);
    while (result.length < limited) result.push(result.at(-1) ?? bounds.minimum);
    return result.slice(0, limited);
  }
  freeProgramForm(bounds, permissions) {
    const kind =
        this.freeProgramKind ||
        (Array.isArray(this.state?.configuration?.temperature_steps)
          ? "steps"
          : "even"),
      steps = this.freeSteps(),
      disabled = permissions.program ? "" : "disabled";
    const manual = kind === "steps",
      count = Math.max(1, Math.min(bounds.gangMaximum, steps.length));
    const kindButtons = `<div class="program-kind"><button type="button" data-action="program-kind:even" aria-pressed="${!manual}" ${disabled}>Gleichmäßig</button><button type="button" data-action="program-kind:steps" aria-pressed="${manual}" ${disabled}>Einzelne Stufen</button></div>`;
    const fields = manual
      ? `<div class="row"><label class="field" for="free-step-count"><span>Stufen ${this.distributionInfo("free", steps)}</span><input id="free-step-count" data-free-step-count type="number" min="1" max="${bounds.gangMaximum}" step="1" value="${count}" ${disabled}></label></div><div class="program-step-fields">${this.resizeSteps(
          steps,
          count,
        )
          .map(
            (value, index) =>
              `<label class="field" for="free-step-${index}">Stufe ${index + 1}<input id="free-step-${index}" data-free-step="${index}" type="number" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(value)}" ${disabled}></label>`,
          )
          .join("")}</div>`
      : `<div class="row"><label class="field" for="progression-start">Start<input id="progression-start" type="number" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(steps[0])}" ${disabled}></label><label class="field" for="progression-end">Ende<input id="progression-end" type="number" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(steps.at(-1))}" ${disabled}></label><label class="field" for="progression-gangs"><span>Verteilung ${this.distributionInfo("free", this.distributedSteps(steps[0], steps.at(-1), count))}</span><input id="progression-gangs" type="number" step="1" min="${bounds.gangMinimum}" max="${bounds.gangMaximum}" value="${count}" ${disabled}></label></div>`;
    return `<div class="program-form">${kindButtons}${fields}<div class="program-actions">${this.programApplyButton(permissions)}</div></div>`;
  }
  distributionInfo(id, steps) {
    return `<span class="program-info"><button type="button" data-action="program-info:${esc(id)}" aria-label="Verteilung erklären" aria-expanded="${this.programInfoOpen === id}">i</button>${this.programInfoOpen === id ? `<span class="program-info-popup">${esc(`${steps.length} Stufen von ${num(steps[0], 1)} bis ${num(steps.at(-1), 1)} °C: ${steps.map((value) => num(value, 1)).join(" → ")} °C`)}</span>` : ""}</span>`;
  }
  setFreeProgramKind(kind) {
    const inputs = [...this.shadowRoot.querySelectorAll("[data-free-step]")];
    if (inputs.length)
      this.freeProgramStepsDraft = inputs.map((input) => Number(input.value));
    else if (this.$("#progression-start"))
      this.freeProgramStepsDraft = this.distributedSteps(
        Number(this.$("#progression-start").value),
        Number(this.$("#progression-end").value),
        Number(this.$("#progression-gangs").value),
      );
    this.freeProgramKind = kind;
    this.markProgramEdited();
    this.drawCurrent();
  }
  freeProgramValues() {
    const bounds = this.programBounds(),
      manual =
        this.freeProgramKind === "steps" ||
        (Array.isArray(this.state?.configuration?.temperature_steps) &&
          !this.freeProgramKind);
    const values = manual
      ? [...this.shadowRoot.querySelectorAll("[data-free-step]")].map((input) =>
          Number(input.value),
        )
      : this.distributedSteps(
          Number(this.$("#progression-start").value),
          Number(this.$("#progression-end").value),
          Number(this.$("#progression-gangs").value),
        );
    if (
      !values.length ||
      values.length > bounds.gangMaximum ||
      values.some(
        (value) =>
          !Number.isFinite(value) || value < bounds.minimum || value > bounds.maximum,
      )
    )
      throw Error("Temperaturstufen innerhalb der zulässigen Grenzen eingeben");
    return values;
  }
  progressionValues() {
    const start = Number(this.$("#progression-start").value),
      end = Number(this.$("#progression-end").value),
      gangs = Number(this.$("#progression-gangs").value);
    const { minimum, maximum, gangMinimum, gangMaximum } = this.programBounds();
    if (
      !Number.isFinite(start) ||
      !Number.isFinite(end) ||
      !Number.isInteger(gangs) ||
      !Number.isFinite(minimum) ||
      !Number.isFinite(maximum) ||
      !Number.isFinite(gangMinimum) ||
      !Number.isFinite(gangMaximum) ||
      start < minimum ||
      start > maximum ||
      end < minimum ||
      end > maximum ||
      gangs < gangMinimum ||
      gangs > gangMaximum
    )
      throw Error(
        "Start, Ende und Verteilung innerhalb der zulässigen Grenzen eingeben",
      );
    return { start, end, gangs };
  }
  clampTemperature(value, bounds = this.temperatureBounds()) {
    if (!bounds) return null;
    const number = Number(value),
      clamped = Math.max(
        bounds.minimum,
        Math.min(bounds.maximum, Number.isFinite(number) ? number : bounds.minimum),
      );
    const step = this.temperatureStep(),
      rounded = Math.round(clamped / step) * step;
    return Math.max(bounds.minimum, Math.min(bounds.maximum, rounded));
  }
  temperatureArcPoint(
    value,
    bounds = this.temperatureBounds(),
    radius = temperatureDial.radius,
  ) {
    if (!bounds) return { x: temperatureDial.centerX, y: temperatureDial.centerY };
    const bounded = this.clampTemperature(value, bounds);
    const ratio = (bounded - bounds.minimum) / (bounds.maximum - bounds.minimum || 1);
    const angle =
      ((temperatureDial.startAngle +
        ratio * (temperatureDial.endAngle - temperatureDial.startAngle)) *
        Math.PI) /
      180;
    return {
      x: (temperatureDial.centerX + radius * Math.cos(angle)).toFixed(2),
      y: (temperatureDial.centerY + radius * Math.sin(angle)).toFixed(2),
    };
  }
  temperatureTickValues(bounds = this.temperatureBounds()) {
    if (!bounds) return [];
    const step = bounds.maximum - bounds.minimum <= 40 ? 10 : 20;
    const values = new Set([bounds.minimum, bounds.maximum]);
    for (
      let value = Math.ceil(bounds.minimum / step) * step;
      value < bounds.maximum;
      value += step
    )
      values.add(value);
    return [...values].sort((left, right) => left - right);
  }
  temperatureTickMarks(bounds = this.temperatureBounds()) {
    return this.temperatureTickValues(bounds)
      .map((value) => {
        const tick = this.temperatureArcPoint(
            value,
            bounds,
            temperatureDial.radius - 12,
          ),
          label = this.temperatureArcPoint(value, bounds, temperatureDial.radius - 29);
        return `<line x1="${tick.x}" y1="${tick.y}" x2="${this.temperatureArcPoint(value, bounds, temperatureDial.radius - 20).x}" y2="${this.temperatureArcPoint(value, bounds, temperatureDial.radius - 20).y}" stroke="var(--secondary-text-color,#aaa)" stroke-width="2"/><text class="tick" x="${label.x}" y="${Number(label.y) + 4}" text-anchor="middle">${num(value, 0)}</text>`;
      })
      .join("");
  }
  temperatureValueAt(svg, clientX, clientY) {
    const point = this.svgCoordinates(svg, clientX, clientY),
      angle =
        (Math.atan2(
          point.y - temperatureDial.centerY,
          point.x - temperatureDial.centerX,
        ) *
          180) /
        Math.PI;
    const candidates = [
      angle < 0 ? angle + 360 : angle,
      angle < 0 ? angle + 720 : angle + 360,
    ].map((candidate) => ({
      candidate,
      value: Math.max(
        temperatureDial.startAngle,
        Math.min(temperatureDial.endAngle, candidate),
      ),
    }));
    const onArc = candidates.reduce((nearest, current) =>
      Math.abs(current.value - current.candidate) <
      Math.abs(nearest.value - nearest.candidate)
        ? current
        : nearest,
    ).value;
    const bounds = this.temperatureBounds();
    if (!bounds) return null;
    return this.clampTemperature(
      bounds.minimum +
        ((onArc - temperatureDial.startAngle) /
          (temperatureDial.endAngle - temperatureDial.startAngle)) *
          (bounds.maximum - bounds.minimum),
      bounds,
    );
  }
  renderTemperatureTarget(value) {
    const track = this.$("[data-target-arc]"),
      handle = this.$(".target-temperature-handle");
    if (!track || !handle) return;
    const bounds = this.temperatureBounds(),
      bounded = this.clampTemperature(value, bounds),
      point = this.temperatureArcPoint(bounded, bounds);
    track.setAttribute("aria-valuenow", bounded);
    track.setAttribute("aria-valuetext", `${num(bounded, 1)} °C`);
    handle.setAttribute("cx", point.x);
    handle.setAttribute("cy", point.y);
  }
  beginTemperatureDrag(event, svg, target) {
    if (!this.state?.permissions?.temperature || !this.temperatureBounds() || !svg)
      return;
    event.preventDefault();
    (target?.getAttribute("role") === "slider"
      ? target
      : this.$('[data-target-arc][role="slider"]')
    )?.focus?.();
    this.temperatureInteraction = {
      pointerId: event.pointerId,
      value: this.temperatureValueAt(svg, event.clientX, event.clientY),
      svg,
    };
    svg.setPointerCapture?.(event.pointerId);
    this.renderTemperatureTarget(this.temperatureInteraction.value);
  }
  updateTemperatureDrag(event) {
    const interaction = this.temperatureInteraction;
    if (!interaction) return;
    event.preventDefault();
    interaction.value = this.temperatureValueAt(
      interaction.svg,
      event.clientX,
      event.clientY,
    );
    this.renderTemperatureTarget(interaction.value);
  }
  cancelTemperatureDrag(event) {
    const interaction = this.temperatureInteraction;
    if (!interaction) return;
    interaction.svg.releasePointerCapture?.(event.pointerId);
    this.temperatureInteraction = null;
    this.drawCurrent();
  }
  async endTemperatureDrag(event) {
    const interaction = this.temperatureInteraction;
    if (!interaction) return;
    interaction.svg.releasePointerCapture?.(event.pointerId);
    interaction.pointerId = null;
    interaction.committing = true;
    try {
      await this.changeTarget(interaction.value);
    } finally {
      this.temperatureInteraction = null;
      this.drawCurrent();
      this.$('[data-target-arc][role="slider"]')?.focus?.();
    }
  }
  async keyTemperatureTarget(event) {
    if (!this.state?.permissions?.temperature || this.temperatureInteraction) return;
    const bounds = this.temperatureBounds();
    if (!bounds) return;
    const current = this.clampTemperature(this.state.target_temperature, bounds),
      step = this.temperatureStep();
    const next = {
      ArrowLeft: current - step,
      ArrowDown: current - step,
      ArrowRight: current + step,
      ArrowUp: current + step,
      PageDown: current - step * 10,
      PageUp: current + step * 10,
      Home: bounds.minimum,
      End: bounds.maximum,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const value = this.clampTemperature(next, bounds);
    this.temperatureInteraction = { value, committing: true };
    this.renderTemperatureTarget(value);
    try {
      await this.changeTarget(value);
    } finally {
      this.temperatureInteraction = null;
      this.drawCurrent();
      this.$('[data-target-arc][role="slider"]')?.focus?.();
    }
  }
  async updateParameters(parameters, start = false, partial = false) {
    const entry = this.entry;
    const saved = await this.api(
      `/${entry}/${partial ? "temperature" : "parameters"}`,
      "POST",
      parameters,
    );
    parameters = saved.parameters;
    await this.waitForConfiguration(entry, parameters);
    this.settingsEntry = null;
    this.progressionDraft = null;
    if (start) await this.api(`/${entry}/control`, "POST", { enabled: true });
    await this.refresh();
  }
  async waitForConfiguration(entry, parameters, configuration) {
    // A saved parameter is loaded by HA's single options listener. Do not start
    // against the previous runtime while reload is still in progress.
    let loaded = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      try {
        const state = await this.api(`/${entry}/state`);
        const current = state.configuration;
        if (
          Object.keys(current.parameters).length === Object.keys(parameters).length &&
          Object.entries(parameters).every(([k, v]) => current.parameters[k] === v) &&
          Object.entries(configuration || {}).every(
            ([key, value]) => key === "parameters" || current[key] === value,
          )
        ) {
          loaded = true;
          break;
        }
      } catch (error) {
        if (error.status_code !== 503) throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!loaded)
      throw Error(
        "Parameter gespeichert, Neuladen noch nicht abgeschlossen. Bitte Status prüfen.",
      );
  }
  historyTitle(session) {
    if (this.selected === "live")
      return this.state.operation_enabled && this.state.session
        ? "Laufende Sitzung"
        : "Letzte Sitzung";
    const archive = this.sessions?.find((item) => item.session_id === this.selected);
    const started = session?.timeline?.session_started_at || archive?.started_at;
    return started ? `Sitzung vom ${when(started)}` : "Archivierte Sitzung";
  }
  drawHistory() {
    if (!this.shown) {
      this.updateMarkup(
        "#plots",
        '<div class="card empty">Noch keine Sitzungsdaten. Wähle eine frühere Saunasitzung oder schalte den Betrieb ein.</div>',
      );
      this.updateMarkup("#gangs", "");
      this.updateMarkup("#event-list", "");
      this.updateMarkup("#detection-plots", "");
      this.historyChartKey = null;
      this.historyGangKey = null;
      this.historyEventKey = null;
      return;
    }
    const { session, records } = this.shown,
      t = session.timeline;
    this.ensureHistoryWindow();
    this.renderHistoryOverview(records, session);
    const gangs = [...t.completed, ...(t.active ? [t.active] : [])];
    this.updateHistoryTimelineRevision(session);
    const positions = [...this.positions].join(","),
      chartNow = session.ended_at ? "" : this.state.now,
      gangNow = t.active ? this.state.now : "";
    const chartKey = `${this.historyDatasetRevision}:${this.historyWindowRevision}:${this.historyTimelineRevision}:${chartNow}:${this.historyDetail}:${positions}`;
    if (
      this.historyGesture ||
      this.chartPointers?.size ||
      (this.webkitHistoryGesture && !this.webkitHistoryGesture.suppressed)
    ) {
      this.updateHistoryChart(records, session, gangs);
      this.historyChartKey = chartKey;
      this.historyRenderDeferred = true;
      return;
    }
    const historyTitle = this.historyTitle(session);
    const chromeKey = `${this.selected}:${this.state.operation_enabled}:${session.timeline?.session_id || ""}:${historyTitle}:${this.historyDetail}:${positions}`;
    if (this.historyChromeKey !== chromeKey || !this.$("svg.session-chart")) {
      this.historyChromeKey = chromeKey;
      this.updateMarkup(
        "#plots",
        `<div class="plot-panel" aria-labelledby="history-chart-title"><h2 id="history-chart-title" class="plot-title">${esc(historyTitle)}</h2><div class="legend top-legend" aria-label="Messkurven für ${esc(historyTitle)}"><span><i style="background:#ff6b4a"></i>Temperatur</span><span><i style="background:#42a5ff"></i>Luftfeuchte</span></div><div class="row position-select"><button data-action="history-detail" aria-pressed="${this.historyDetail}">${this.historyDetail ? "Messhöhen ausblenden" : "Messhöhen vergleichen"}</button></div><div class="row position-select" data-history-positions ${this.historyDetail ? "" : "hidden"}><button data-action="position-upper" aria-pressed="${this.positions.has("upper")}">━━ Oben</button><button data-action="position-lower" aria-pressed="${this.positions.has("lower")}">┄┄ Unten</button></div><div class="plot-wrap">${this.chart(records, session, gangs)}<div id="tooltip" hidden></div></div><div class="legend"><span><i style="background:rgba(255,205,80,.95)"></i>Saunatür offen</span><span><i style="background:rgba(165,30,85,.95)"></i>Saunagang</span><span><i style="background:#f5f5f5"></i>Aufguss</span></div><div class="legend"><span><i style="background:rgba(255,150,35,.4)"></i>heizen</span><span><i style="background:rgba(38,125,82,.4)"></i>bereit</span><span><i style="background:rgba(58,125,155,.4)"></i>lüften</span><span><i style="background:#67829a"></i>Zwangskühlung</span></div><p class="muted plot-note">${this.historyDetail ? "Durchgezogen: oben · gestrichelt: unten · " : ""}vorläufiger Gang · schmaler Streifen: gezählte Heizzeit</p></div>`,
      );
      this.historyChartKey = chartKey;
    } else if (this.historyChartKey !== chartKey) {
      this.updateHistoryChart(records, session, gangs);
      this.historyChartKey = chartKey;
    }
    const gangKey = `${this.historyTimelineRevision}:${gangNow}`;
    if (this.historyGangKey !== gangKey) {
      this.historyGangKey = gangKey;
      const e = session.energy,
        energySummary = e
          ? `<p class="muted" data-history-energy>Energieverbrauch: ${num(e.measured_kwh + e.estimated_kwh, 3)} kWh · ${e.unknown_seconds ? "Unvollständig" : e.measured_seconds ? (e.estimated_seconds ? "Messung mit geschätzten Anteilen" : "Aus gemessener Leistung") : "Geschätzt"}</p>`
          : "";
      this.updateMarkup(
        "#gangs",
        `<div class="card"><h2>Saunagänge</h2>${energySummary}${gangs.length ? `<div class="scroll"><table><thead><tr><th>Gang</th><th>Beginn</th><th>Erkannt</th><th>Bestätigung</th><th>Dauer</th><th>Ende</th></tr></thead><tbody>${gangs.map((g, i) => `<tr data-gang-id="${esc(g.gang_id)}" data-start="${esc(g.started_at)}"><td>${i + 1} · ${g.infusion_events.length ? "Bestätigt" : "Vorläufig"}</td><td>${when(g.started_at)}</td><td>${when(g.detected_at)}</td><td>${g.infusion_events.length ? when(g.infusion_events[0].detected_at) : "Aufguss ausstehend"}</td><td>${duration((stamp(g.ended_at || session.ended_at || this.state.now) - stamp(g.started_at)) / 1000)}</td><td>${when(g.ended_at)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">Keine Saunagänge erkannt.</p>'}</div>`,
      );
    }
    const eventKey = `${this.historyTimelineRevision}:${this.historyDatasetRevision}`;
    if (this.historyEventKey !== eventKey) {
      this.historyEventKey = eventKey;
      const openEvents = [
        ...this.shadowRoot.querySelectorAll("#event-list details"),
      ].map((d) => d.open);
      const diagnostics = records.filter((r) => r.kind === "diagnostic"),
        traces = records
          .filter((r) => r.kind === "detector_trace")
          .map((r) => r.payload);
      const eventRows = [...t.processed]
        .map((e, index) => ({ ...e, event_id: e.event_id || `legacy-${index}` }))
        .reverse();
      this.updateMarkup(
        "#event-list",
        `<div class="card"><details><summary>Ereignisse & Zuordnung (${t.processed.length})</summary><div class="scroll"><table><thead><tr><th>Ereignis</th><th>Zugeordnete Zeit</th><th>Erkennungszeit</th><th>Verlauf</th></tr></thead><tbody>${eventRows
          .map((e) => {
            const linked = this.diagnosticTraceForEvent(traces, e);
            return `<tr class="event-row" data-event-id="${esc(e.event_id)}" data-selected="false"><td>${esc(events[e.kind] || e.kind)}</td><td>${when(e.effective_at)}</td><td>${when(e.detected_at)}</td><td>${linked ? `<button data-action="event-row:${esc(e.event_id)}" aria-label="${esc(events[e.kind] || e.kind)} im Erkennungsverlauf zeigen">Zum Marker</button>` : "Kein gespeicherter Kurvenbezug"}</td></tr>`;
          })
          .join(
            "",
          )}</tbody></table></div></details>${diagnostics.length ? `<details><summary>Historische Fehlerhinweise (${diagnostics.length})</summary>${diagnostics.map((r) => `<p><small>${when(r.received_at)}</small> ${esc(r.payload.messages?.join(" ") || "Keine Störung gemeldet.")}</p>`).join("")}</details>` : ""}${t.retracted.length ? `<p class="muted">${t.retracted.length} vorläufige Erkennung(en) aufgehoben. Diese werden nicht als Gänge gezählt.</p>` : ""}</div>`,
      );
      this.shadowRoot
        .querySelectorAll("#event-list details")
        .forEach((d, i) => (d.open = !!openEvents[i]));
      if (this.highlightedEventId) this.highlightEvent(this.highlightedEventId, false);
    }
    const diagnosticsKey = `${this.historyDatasetRevision}:${this.historyTimelineRevision}:${this.historyWindowRevision}`;
    if (this.view === "diagnostics" && this.historyDiagnosticsKey !== diagnosticsKey) {
      this.historyDiagnosticsKey = diagnosticsKey;
      this.drawDiagnostics();
    }
  }
  updateHistoryChart(records, session, gangs) {
    const current = this.$("svg.session-chart");
    if (!current) return false;
    const markup = this.chart(records, session, gangs),
      documentRef = this.ownerDocument || globalThis.document;
    if (documentRef) {
      const template = documentRef.createElement("template");
      template.innerHTML = markup;
      const next = template.content.querySelector("svg.session-chart");
      if (!next) return false;
      // Pointer capture belongs to this SVG. Patch changed paths, axes and
      // intervals in place so a live gesture and its tooltip keep their nodes.
      this.patchNode(current, next);
    } else {
      const end = markup.lastIndexOf("</svg>"),
        open = markup.indexOf(">");
      if (open < 0 || end < 0) return false;
      const label = /aria-label="([^"]*)"/.exec(markup)?.[1];
      if (label) current.setAttribute("aria-label", label);
      current.innerHTML = markup.slice(open + 1, end);
    }
    if (
      this.lastHistoryPointer &&
      !this.chartPointers?.size &&
      !this.webkitHistoryGesture &&
      !this.historyGesture
    )
      this.scheduleHover({ ...this.lastHistoryPointer, svg: current });
    return true;
  }
  eventNavigation() {
    return (this.shown?.session.timeline.processed || []).map((event, index) => ({
      ...event,
      event_id: event.event_id || `legacy-${index}`,
    }));
  }
  highlightEvent(eventId, reveal) {
    this.highlightedEventId = eventId;
    this.shadowRoot
      .querySelectorAll("[data-event-id]")
      .forEach((node) => (node.dataset.selected = "false"));
    const nodes = [...this.shadowRoot.querySelectorAll("[data-event-id]")].filter(
      (node) => node.dataset.eventId === eventId,
    );
    nodes.forEach((node) => (node.dataset.selected = "true"));
    if (reveal) {
      const row = nodes.find((node) => node.classList.contains("event-row"));
      const details = row?.closest?.("details");
      if (details) details.open = true;
      row?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    }
  }
  focusEvent(eventId, revealRow = false) {
    const event = this.eventNavigation().find((item) => item.event_id === eventId),
      at = stamp(event?.effective_at || event?.detected_at);
    if (!event || !at) {
      this.highlightEvent(eventId, revealRow);
      return;
    }
    const [domainStart, domainEnd] = this.historyDomain();
    const span = Math.min(domainEnd - domainStart, 30 * 60000),
      zoom = Math.max(1, Math.min(256, (domainEnd - domainStart) / span));
    const left = Math.max(domainStart, Math.min(domainEnd - span, at - span / 2));
    this.setHistoryWindow(left, left + span);
    this.drawHistory();
    this.highlightEvent(eventId, revealRow);
    if (!revealRow)
      this.$('.event-marker[data-selected="true"]')?.scrollIntoView({
        block: "center",
        behavior: "smooth",
      });
  }
  invalidateHistoryIndex() {
    this.historyDatasetRevision = (this.historyDatasetRevision || 0) + 1;
    this.historyOverviewCache = null;
  }
  updateHistoryTimelineRevision(session) {
    const signature = JSON.stringify({
      id: session.timeline.session_id,
      ended_at: session.ended_at,
      energy: session.energy,
      heating: session.heating,
      active: session.timeline.active,
      completed: session.timeline.completed,
      processed: session.timeline.processed,
      retracted: session.timeline.retracted,
    });
    if (signature !== this.historyTimelineSignature) {
      this.historyTimelineSignature = signature;
      this.historyTimelineRevision = (this.historyTimelineRevision || 0) + 1;
    }
    return this.historyTimelineRevision;
  }
  historyIndex(records) {
    let index = this.chartDataIndex,
      rebuild =
        !index || index.records !== records || index.indexedCount > records.length;
    if (rebuild)
      index = { records, series: new Map(), byKind: new Map(), indexedCount: 0 };
    for (let i = index.indexedCount; i < records.length; i++) {
      const record = records[i],
        grouped = index.byKind.get(record.kind) || [];
      grouped.push(record);
      index.byKind.set(record.kind, grouped);
      if (!["measurement", "source_snapshot"].includes(record.kind)) continue;
      const source = record.payload,
        time = stamp(source.received_at),
        number = source.value == null ? NaN : Number(source.value),
        value = Number.isFinite(number) ? number : null;
      if (!Number.isFinite(time)) continue;
      const key = `${source.position}:${source.quantity}`,
        values = index.series.get(key) || [],
        point = { time, value, source };
      if (rebuild || !values.length || values.at(-1).time <= time) values.push(point);
      else values.splice(lowerBoundHistory(values, time), 0, point);
      index.series.set(key, values);
    }
    if (rebuild)
      for (const values of index.series.values())
        values.sort((a, b) => a.time - b.time);
    index.indexedCount = records.length;
    return (this.chartDataIndex = index);
  }
  series(position, quantity) {
    return this.chartDataIndex?.series.get(`${position}:${quantity}`) || [];
  }
  historyRecords(kind) {
    return this.chartDataIndex?.byKind.get(kind) || [];
  }
  nearestMeasurement(position, quantity, time) {
    if (this.shown?.records) this.historyIndex(this.shown.records);
    return nearestHistoryPoint(
      this.series(position, quantity),
      time,
      this.window[0],
      this.window[1],
    );
  }
  svgCoordinates(svg, clientX, clientY = 0) {
    if (svg.createSVGPoint && svg.getScreenCTM) {
      const point = svg.createSVGPoint(),
        matrix = svg.getScreenCTM();
      if (matrix) {
        point.x = clientX;
        point.y = clientY;
        return point.matrixTransform(matrix.inverse());
      }
    }
    const rect = svg.getBoundingClientRect();
    return {
      x: ((clientX - rect.left) / rect.width) * 1200,
      y: ((clientY - rect.top) / rect.height) * 480,
    };
  }
  scheduleFrame(key, render) {
    if (this[key] != null) return;
    const request =
      globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 0));
    this[key] = request(() => {
      this[key] = null;
      render();
    });
  }
  scheduleHover(event) {
    this.lastHistoryPointer = { clientX: event.clientX, clientY: event.clientY };
    this.pendingHover = event;
    this.scheduleFrame(
      "hoverFrame",
      () => this.pendingHover && this.hoverChart(this.pendingHover),
    );
  }
  scheduleHistoryRender() {
    this.scheduleFrame("historyFrame", () => this.drawHistory());
  }
  historyDomain() {
    const session = this.shown?.session,
      started = stamp(session?.timeline.session_started_at);
    const start =
      (Number.isFinite(started) ? started : stamp(this.state?.now) || Date.now()) -
      15 * 60000;
    return [
      start,
      Math.max(start + 1000, stamp(session?.ended_at || this.state?.now) || start) +
        15 * 60000,
    ];
  }
  ensureHistoryWindow() {
    const [start, end] = this.historyDomain(),
      width = end - start;
    const previous = this.window,
      followDomain = !this.window || this.zoom === 1;
    if (followDomain || this.window[1] <= start || this.window[0] >= end)
      this.window = [start, end];
    const current = Math.max(
      width / 256,
      Math.min(width, this.window[1] - this.window[0]),
    );
    const left = Math.max(start, Math.min(end - current, this.window[0])),
      next = [left, left + current];
    if (!previous || previous[0] !== next[0] || previous[1] !== next[1])
      this.historyWindowRevision = (this.historyWindowRevision || 0) + 1;
    this.window = next;
    this.zoom = width / current;
  }
  setHistoryWindow(left, right) {
    const [start, end] = this.historyDomain(),
      span = end - start,
      minimum = span / 256;
    const width = Math.max(minimum, Math.min(span, right - left));
    const nextLeft = Math.max(start, Math.min(end - width, left));
    const next = [nextLeft, nextLeft + width];
    if (!this.window || this.window[0] !== next[0] || this.window[1] !== next[1])
      this.historyWindowRevision = (this.historyWindowRevision || 0) + 1;
    this.window = next;
    this.zoom = span / width;
    this.syncHistoryOverview();
  }
  overviewFraction(svg, clientX) {
    const point = this.svgCoordinates(svg, clientX),
      fraction = (point.x - 20) / 1160;
    return Math.max(0, Math.min(1, fraction));
  }
  beginHistoryGesture(event, svg) {
    if (!this.window) return;
    const handle = event.target.dataset.historyHandle,
      move = event.target.closest("[data-history-window]");
    if (!handle && !move) return;
    event.preventDefault();
    svg.setPointerCapture?.(event.pointerId);
    this.historyGesture = {
      pointerId: event.pointerId,
      svg,
      kind: handle || "move",
      fraction: this.overviewFraction(svg, event.clientX),
      window: [...this.window],
    };
  }
  updateHistoryGesture(event) {
    const gesture = this.historyGesture;
    if (!gesture) return;
    event.preventDefault();
    const [domainStart, domainEnd] = this.historyDomain(),
      span = domainEnd - domainStart;
    const at = domainStart + this.overviewFraction(gesture.svg, event.clientX) * span,
      [left, right] = gesture.window,
      minimum = span / 256;
    if (gesture.kind === "start")
      this.setHistoryWindow(Math.min(at, right - minimum), right);
    else if (gesture.kind === "end")
      this.setHistoryWindow(left, Math.max(at, left + minimum));
    else {
      const width = right - left,
        next = left + (at - (domainStart + gesture.fraction * span));
      this.setHistoryWindow(next, next + width);
    }
    this.scheduleHistoryRender();
  }
  endHistoryGesture(event) {
    const gesture = this.historyGesture;
    if (!gesture) return;
    gesture.svg.releasePointerCapture?.(event.pointerId);
    this.historyGesture = null;
    if (this.historyRenderDeferred) {
      this.historyRenderDeferred = false;
      this.drawHistory();
    } else this.drawHistory();
  }
  beginChartPointer(event, svg) {
    if (this.historyInputMode && this.historyInputMode !== "pointer") return;
    this.historyInputMode = "pointer";
    svg.setPointerCapture?.(event.pointerId);
    this.chartPointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    this.pinchDistance = null;
  }
  endChartPointer(event) {
    if (!this.chartPointers?.has(event.pointerId)) return;
    event.target
      .closest?.("svg.session-chart")
      ?.releasePointerCapture?.(event.pointerId);
    this.chartPointers.delete(event.pointerId);
    this.pinchDistance = null;
    if (!this.chartPointers.size && this.historyInputMode === "pointer")
      this.historyInputMode = null;
    if (!this.chartPointers.size && this.historyRenderDeferred) {
      this.historyRenderDeferred = false;
      this.drawHistory();
    }
  }
  minimapBackground(records, session) {
    const [start, end] = this.historyDomain(),
      cached = this.historyOverviewCache;
    const ttl =
        (session.configuration?.parameters || this.state.configuration.parameters)
          .sensor_timeout_seconds * 1000,
      domain = `${start}:${end}:${ttl}`;
    if (
      cached?.records === records &&
      cached.revision === this.historyDatasetRevision &&
      cached.domain === domain
    )
      return cached.background;
    this.historyIndex(records);
    const values = this.series("upper", "temperature");
    let low = Infinity,
      high = -Infinity;
    for (const point of values)
      if (point.value != null) {
        low = Math.min(low, point.value);
        high = Math.max(high, point.value);
      }
    if (!Number.isFinite(low)) {
      low = 0;
      high = 1;
    }
    const x = (time) => 20 + ((time - start) / (end - start)) * 1160,
      y = (value) => 36 - ((value - low) / (high - low || 1)) * 28;
    const path = historySegments(values, start, end, ttl)
      .map((segment) => monotoneHistoryPath(reduceHistorySegment(segment, x), x, y))
      .join(" ");
    const background = `<rect class="overview-track" x="20" y="4" width="1160" height="36" rx="5"/><path class="overview-temperature" d="${path}"/>`;
    this.historyOverviewCache = {
      records,
      revision: this.historyDatasetRevision,
      domain,
      path,
      background,
    };
    return background;
  }
  renderHistoryOverview(records, session) {
    const target = this.$("#history-overview");
    if (!target) return;
    let svg = target.querySelector("svg");
    if (!svg) {
      target.innerHTML = `<svg viewBox="0 0 1200 46" preserveAspectRatio="none" role="slider" aria-label="Zeitausschnitt der Saunasitzung">${this.minimapBackground(records, session)}<rect class="overview-window" data-history-window x="20" y="5" height="34" rx="4"/><rect class="overview-handle" data-history-handle="start" x="16" y="2" width="8" height="40" rx="3"/><rect class="overview-handle" data-history-handle="end" x="1176" y="2" width="8" height="40" rx="3"/></svg>`;
      svg = target.querySelector("svg");
    } else if (!this.historyGesture) {
      this.minimapBackground(records, session);
      svg
        .querySelector(".overview-temperature")
        ?.setAttribute("d", this.historyOverviewCache.path);
    }
    this.syncHistoryOverview();
  }
  syncHistoryOverview() {
    const svg = this.$("#history-overview svg");
    if (!svg || !this.window) return;
    const [start, end] = this.historyDomain(),
      width = end - start,
      left = 20 + ((this.window[0] - start) / width) * 1160,
      right = 20 + ((this.window[1] - start) / width) * 1160;
    const selected = svg.querySelector("[data-history-window]"),
      first = svg.querySelector('[data-history-handle="start"]'),
      last = svg.querySelector('[data-history-handle="end"]');
    selected?.setAttribute("x", left);
    selected?.setAttribute("width", Math.max(1, right - left));
    first?.setAttribute("x", left - 4);
    last?.setAttribute("x", right - 4);
    svg.setAttribute(
      "aria-valuetext",
      `${when(this.window[0])} bis ${when(this.window[1])}, Zoom ${num(this.zoom, 1)}×`,
    );
    const range = this.$("#range");
    if (range)
      range.textContent = `${when(this.window[0])} – ${when(this.window[1])} · ${num(this.zoom, 1)}×`;
  }
  chart(records, session, gangs) {
    const [start, end] = this.window,
      W = 1200,
      H = 480,
      left = 65,
      right = 1135,
      top = 18,
      bottom = 435;
    const x = (t) => left + ((t - start) / (end - start)) * (right - left);
    this.historyIndex(records);
    const visible = [];
    for (const position of this.positions)
      for (const value of historyWindowValues(
        this.series(position, "temperature"),
        start,
        end,
      ))
        visible.push(value);
    const validTemperatures = visible.filter((m) => m.value != null),
      bounds = validTemperatures.reduce(
        ([lo, hi], m) => [Math.min(lo, m.value), Math.max(hi, m.value)],
        [Infinity, -Infinity],
      );
    const low = validTemperatures.length ? Math.floor((bounds[0] - 2) / 10) * 10 : 20,
      high = validTemperatures.length ? Math.ceil((bounds[1] + 2) / 10) * 10 : 100;
    const humidities = [];
    for (const position of this.positions) {
      const values = this.series(position, "humidity");
      for (const value of historyWindowValues(values, start, end))
        humidities.push(value);
    }
    const humidityHigh = Math.max(
      60,
      Math.ceil((humidities.reduce((max, m) => Math.max(max, m.value), 0) + 2) / 20) *
        20,
    );
    const yT = (v) => bottom - ((v - low) / (high - low)) * (bottom - top),
      yH = (v) => bottom - (v / humidityHigh) * (bottom - top);
    const interval = (a, b, klass, title, y = top, height = bottom - top) => {
      const aa = Math.max(start, stamp(a)),
        bb = Math.min(end, stamp(b || session.ended_at || this.state.now));
      return bb > aa
        ? `<rect class="${klass}" x="${x(aa).toFixed(2)}" y="${y}" width="${(x(bb) - x(aa)).toFixed(2)}" height="${height}"><title>${esc(title)}</title></rect>`
        : "";
    };
    let svg = `<svg class="chart session-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Sitzungsverlauf: Temperatur und Luftfeuchte${this.historyDetail ? " beider Messhöhen" : ""}"><defs><clipPath id="plot-clip"><rect x="${left}" y="${top}" width="${right - left}" height="${bottom - top}"/></clipPath></defs><g clip-path="url(#plot-clip)">`;
    const phases = this.historyRecords("phase"),
      styles = {
        aufheizen: "heat",
        bereit: "ready",
        zwangskühlung: "cool",
        nachlauf: "after",
      };
    phases.forEach((p, i) => {
      if (styles[p.payload.phase])
        svg += interval(
          p.received_at,
          phases[i + 1]?.received_at,
          styles[p.payload.phase],
          p.payload.phase,
        );
    });
    const doorEvents = session.timeline.processed.filter(
      (e) => e.kind === "door_open" || e.kind === "door_close",
    );
    for (const e of session.timeline.processed.filter(
      (e) => e.kind === "ventilation_confirmed",
    )) {
      const close = doorEvents.find(
        (d) =>
          d.kind === "door_close" && stamp(d.effective_at) >= stamp(e.effective_at),
      );
      svg += interval(e.effective_at, close?.effective_at, "vent", "lüften");
    }
    svg += gangs
      .map((g) =>
        interval(
          g.started_at,
          g.ended_at,
          g.infusion_events.length ? "gang" : "gang provisional",
          `Gang ab ${when(g.started_at)}`,
        ),
      )
      .join("");
    doorEvents.forEach((e, i) => {
      if (e.kind === "door_open")
        svg += interval(
          e.effective_at,
          doorEvents[i + 1]?.effective_at,
          "door",
          "Saunatür offen",
        );
    });
    svg += session.heating.intervals
      .map((i) =>
        interval(
          i.started_at,
          i.ended_at,
          "heat actual-heat",
          `Gezählte Heizzeit ab ${when(i.started_at)}`,
          bottom - 5,
          5,
        ),
      )
      .join("");
    for (let n = 0; n <= 8; n++) {
      const yy = top + ((bottom - top) * n) / 8;
      svg += `<line class="gridline" x1="${left}" x2="${right}" y1="${yy}" y2="${yy}"/>`;
    }
    for (const e of session.timeline.processed.filter((e) => e.kind === "infusion")) {
      const xx = x(stamp(e.effective_at));
      svg += `<line class="infusion" x1="${xx}" x2="${xx}" y1="${top}" y2="${bottom}"><title>Aufguss · ${when(e.effective_at)} · erkannt ${when(e.detected_at)}</title></line>`;
    }
    for (const position of this.positions)
      for (const quantity of ["temperature", "humidity"]) {
        const values = this.series(position, quantity);
        const ttl =
          (session.configuration?.parameters || this.state.configuration.parameters)
            .sensor_timeout_seconds * 1000;
        const path = historySegments(
          historyWindowValues(values, start, end),
          start,
          end,
          ttl,
        )
          .map((segment) =>
            monotoneHistoryPath(
              reduceHistorySegment(segment, x),
              x,
              quantity === "temperature" ? yT : yH,
            ),
          )
          .join(" ");
        svg += `<path class="${position} ${quantity}" data-series="${position}_${quantity}" d="${path}"/>`;
      }
    svg += "</g>";
    for (let n = 0; n <= 8; n++) {
      const f = n / 8,
        yy = bottom - (bottom - top) * f,
        t = start + (end - start) * f;
      svg += `<text class="axis-temperature" x="${left - 10}" text-anchor="end" y="${yy + 4}">${(low + (high - low) * f).toFixed(0)}</text><text class="axis-humidity" x="${right + 10}" y="${yy + 4}">${(humidityHigh * f).toFixed(0)}</text><text text-anchor="middle" x="${x(t)}" y="${bottom + 27}">${clock(t)}</text>`;
    }
    svg += `<text class="axis-temperature" x="13" y="${H / 2}">°C</text><text class="axis-humidity" x="${W - 15}" y="${H / 2}">%</text><line id="cursor" x1="0" x2="0" y1="${top}" y2="${bottom}" stroke="#eee" stroke-dasharray="3 3" visibility="hidden"/>`;
    return svg + "</svg>";
  }
  hoverChart(e) {
    const svg = e.svg || e.target.closest("svg.session-chart"),
      point = this.svgCoordinates(svg, e.clientX, e.clientY),
      px = point.x;
    if (px < 65 || px > 1135) return;
    const time =
      this.window[0] + ((px - 65) / 1070) * (this.window[1] - this.window[0]);
    const ttl =
      (
        this.shown.session.configuration?.parameters ||
        this.state.configuration.parameters
      ).sensor_timeout_seconds * 1000;
    const rows = [];
    for (const p of this.positions)
      for (const q of ["temperature", "humidity"]) {
        const nearest = this.nearestMeasurement(p, q, time);
        if (nearest?.value != null && (!ttl || Math.abs(nearest.time - time) <= ttl))
          rows.push(
            `<span style="color:${q === "temperature" ? "#e25d40" : "#2f8bde"}">${q === "temperature" ? "Temperatur" : "Luftfeuchte"}${this.historyDetail ? ` ${p === "upper" ? "oben" : "unten"}` : ""}</span><br>${num(nearest.value, 3)} ${q === "temperature" ? "°C" : "%"} · ${when(nearest.source.received_at)}`,
          );
      }
    const tip = this.$("#tooltip");
    tip.innerHTML = `<strong>${when(time)}</strong><br>${rows.join("<br>")}`;
    tip.hidden = false;
    const wrap = svg.closest(".plot-wrap"),
      rect = wrap?.getBoundingClientRect() || svg.getBoundingClientRect();
    tip.style.left = `${Math.max(0, Math.min(rect.width - 290, e.clientX - rect.left + 12))}px`;
    tip.style.top = `${Math.max(0, e.clientY - rect.top - 90)}px`;
    const cursor = this.$("#cursor");
    cursor.setAttribute("x1", px);
    cursor.setAttribute("x2", px);
    cursor.setAttribute("visibility", "visible");
  }
  zoomAt(factor, clientX, svg) {
    if (!this.window) return;
    const px = this.svgCoordinates(svg, clientX).x,
      fraction = Math.max(0, Math.min(1, (px - 65) / 1070));
    const [domainStart, domainEnd] = this.historyDomain();
    const nextZoom = Math.max(1, Math.min(256, this.zoom * factor));
    if (nextZoom === this.zoom) return;
    const nextWidth = (domainEnd - domainStart) / nextZoom,
      focus = this.window[0] + fraction * (this.window[1] - this.window[0]);
    const left = Math.max(
      domainStart,
      Math.min(domainEnd - nextWidth, focus - fraction * nextWidth),
    );
    this.setHistoryWindow(left, left + nextWidth);
    this.scheduleHistoryRender();
  }
  pinchZoom(svg) {
    if (this.historyInputMode !== "pointer") return;
    const points = [...this.chartPointers.values()];
    if (points.length !== 2) return;
    const distance = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y),
      center = (points[0].x + points[1].x) / 2;
    if (this.pinchDistance) {
      this.zoomAt(distance / this.pinchDistance, center, svg);
    }
    this.pinchDistance = distance;
  }
  beginWebkitGesture(event) {
    const svg = event.target.closest?.("svg.session-chart");
    if (!svg) return;
    if (this.historyInputMode && this.historyInputMode !== "webkit") {
      event.preventDefault();
      this.webkitHistoryGesture = { suppressed: true };
      return;
    }
    this.historyInputMode = "webkit";
    event.preventDefault();
    this.webkitHistoryGesture = {
      svg,
      scale: event.scale || 1,
      clientX:
        event.clientX ||
        svg.getBoundingClientRect().left + svg.getBoundingClientRect().width / 2,
    };
  }
  updateWebkitGesture(event) {
    const gesture = this.webkitHistoryGesture;
    if (!gesture) return;
    event.preventDefault();
    if (gesture.suppressed) return;
    const scale = event.scale || 1,
      factor = scale / (gesture.scale || 1);
    gesture.scale = scale;
    this.zoomAt(factor, event.clientX || gesture.clientX, gesture.svg);
  }
  endWebkitGesture(event) {
    if (!this.webkitHistoryGesture) return;
    event.preventDefault();
    const suppressed = this.webkitHistoryGesture.suppressed;
    this.webkitHistoryGesture = null;
    if (suppressed) return;
    if (this.historyInputMode === "webkit") this.historyInputMode = null;
    if (this.historyRenderDeferred) {
      this.historyRenderDeferred = false;
      this.drawHistory();
    } else this.scheduleHistoryRender();
  }
  wheelHistoryGesture(event, svg) {
    event.preventDefault();
    if (this.historyInputMode && this.historyInputMode !== "wheel") return;
    this.historyInputMode = "wheel";
    this.zoomAt(Math.exp(-event.deltaY * 0.002), event.clientX, svg);
    clearTimeout(this.historyWheelTimer);
    this.historyWheelTimer = setTimeout(() => {
      if (this.historyInputMode === "wheel") this.historyInputMode = null;
    }, 120);
  }
  eventMarkerLayout(markers, start, end, stripWidth, markerSize = 17, markerGap = 3) {
    const width =
      Number.isFinite(stripWidth) && stripWidth > 0 ? stripWidth : markerSize * 8;
    const collisionPercent = (100 * (markerSize + markerGap)) / width;
    const lanes = [];
    const visible = markers
      .map((marker, index) => ({
        ...marker,
        index: marker.index ?? index,
        at: marker.at ?? stamp(marker.event?.effective_at || marker.event?.detected_at),
      }))
      .filter(
        (marker) =>
          Number.isFinite(marker.at) && marker.at >= start && marker.at <= end,
      )
      .sort((a, b) => a.at - b.at || a.index - b.index);
    for (const marker of visible) {
      marker.left = ((marker.at - start) / (end - start)) * 100;
      marker.lane = lanes.findIndex((last) => marker.left - last >= collisionPercent);
      if (marker.lane < 0) {
        marker.lane = lanes.length;
        lanes.push(marker.left);
      } else lanes[marker.lane] = marker.left;
    }
    return {
      markers: visible,
      maxLane: Math.max(0, lanes.length - 1),
      markerSize,
      markerGap,
    };
  }
  diagnosticMarkers(traces, metric, start, end) {
    return this.eventNavigation().flatMap((event, index) => {
      const route = this.diagnosticRoute(event),
        at = stamp(event.effective_at || event.detected_at);
      if (
        !route ||
        route[0] !== metric ||
        !Number.isFinite(at) ||
        at < start ||
        at > end
      )
        return [];
      const trace = this.diagnosticTraceForEvent(traces, event, route[1], metric);
      if (!trace) return [];
      const position =
        (trace.channels || ["upper", "lower"]).find((pos) =>
          Number.isFinite(trace.metrics?.[pos]?.[metric]),
        ) ||
        ["upper", "lower"].find((pos) =>
          Number.isFinite(trace.metrics?.[pos]?.[metric]),
        );
      return [
        {
          event,
          index,
          at,
          value: trace.metrics[position][metric],
          pointAt: stamp(trace.at),
          position,
        },
      ];
    });
  }
  diagnosticRoute(event) {
    return (
      {
        door_open: ["door_temperature_slope", "door_open"],
        door_close: ["door_temperature_slope", "door_close"],
        person_strong: ["strong_temperature_slope", "person_strong"],
        person_weak: ["weak_temperature_slope", "person_weak"],
        infusion: ["infusion_humidity_delta", "infusion"],
        ventilation_confirmed: [
          "ventilation_temperature_loss",
          "ventilation_confirmed",
        ],
      }[event?.kind] || null
    );
  }
  diagnosticTraceForEvent(traces, event, signal, metric) {
    const route = this.diagnosticRoute(event);
    if (!route) return null;
    signal ??= route[1];
    metric ??= route[0];
    const effective = stamp(event.effective_at);
    if (!Number.isFinite(effective)) return null;
    return (
      traces.find(
        (t) =>
          stamp(t.at) === effective &&
          (t.signals || []).includes(signal) &&
          ["upper", "lower"].some((pos) => Number.isFinite(t.metrics?.[pos]?.[metric])),
      ) || null
    );
  }
  drawDiagnostics() {
    if (!this.shown) {
      this.$("#detection-plots").innerHTML = "<p>Keine Saunasitzung ausgewählt.</p>";
      return;
    }
    const traces = this.shown.records
      .filter((r) => r.kind === "detector_trace")
      .map((r) => r.payload);
    const groups = [
      ["Türerkennung", "door_temperature_slope", "Temperaturänderung · °C/min"],
      ["Türerkennung", "door_humidity_delta", "Feuchteänderung · Prozentpunkte"],
      [
        "Starke Personenerkennung",
        "strong_temperature_slope",
        "Temperaturtrend · °C/min",
      ],
      [
        "Starke Personenerkennung",
        "strong_humidity_slope",
        "Feuchtetrend · Prozentpunkte/min",
      ],
      [
        "Schwache Personenerkennung",
        "weak_temperature_slope",
        "Temperaturtrend · °C/min",
      ],
      [
        "Schwache Personenerkennung",
        "weak_humidity_slope",
        "Feuchtetrend · Prozentpunkte/min",
      ],
      [
        "Aufgusserkennung",
        "infusion_humidity_delta",
        "Feuchteänderung · Prozentpunkte",
      ],
      ["Aufgusserkennung", "infusion_temperature_delta", "Temperaturänderung · °C"],
      ["Lüftungserkennung", "ventilation_temperature_loss", "Temperaturverlust · °C"],
      [
        "Lüftungserkennung",
        "ventilation_absolute_humidity_loss",
        "Absoluter Feuchteverlust · %",
        (v) => v * 100,
      ],
    ];
    // The session snapshot is the effective configuration for archived data.
    // Older sessions fall back to the current state when no snapshot exists.
    const parameters = {
      ...this.state.configuration?.parameters,
      ...this.shown.session.configuration?.parameters,
    };
    const thresholds = (metric, position) => {
      if (metric === "door_temperature_slope")
        return [
          parameters.door_open_slope,
          parameters.door_heating_slope,
          parameters.door_close_slope,
        ];
      if (metric === "door_humidity_delta")
        return [-parameters[`door_open_humidity_${position}`]];
      if (metric.startsWith("infusion_"))
        return [parameters[metric.replace("_delta", "")]];
      if (metric === "ventilation_temperature_loss")
        return [parameters[`vent_drop_${position}`]];
      if (metric === "ventilation_absolute_humidity_loss")
        return [parameters.vent_absolute_humidity_loss_percent / 100];
      return [parameters[metric.replace("_slope", `_${position}`)]];
    };
    const [start, end] = this.window;
    const detectionPlots = this.$("#detection-plots");
    let html =
      '<details><summary>Hinweise zur Erkennung</summary><p class="muted">Die Kurven zeigen die gespeicherten Erkennungswerte, die gestrichelten Linien die zugehörigen Schwellen. Ein Grenzübertritt löst erst dann ein Ereignis aus, wenn auch die übrigen Bedingungen erfüllt sind. Nach Beginn eines Gangs, im Nachlauf und während laufender Kühlung ruht die Personensuche. Weitere Aufgüsse werden im Gang weiter erkannt.</p></details>';
    if (!traces.length) {
      this.$("#detection-plots").innerHTML =
        html +
        "<p>Für diese Saunasitzung liegen keine gespeicherten Erkennungsverläufe vor.</p>";
      return;
    }
    const availableWidth = detectionPlots?.getBoundingClientRect?.().width || 640;
    const columns = availableWidth > 600 ? 2 : 1;
    const chartWidth = Math.max(
      280,
      (availableWidth - (columns - 1) * 14) / columns - 20,
    );
    const plotLeft = 40,
      plotRight = chartWidth - 25;
    const chartX = (t) =>
      plotLeft + ((stamp(t) - start) / (end - start)) * (plotRight - plotLeft);
    html += '<div class="diagnostic-grid">';
    for (const [group, metric, label, convert = (v) => v] of groups) {
      const vals = traces
        .filter((t) => {
          const time = stamp(t.at);
          return Number.isFinite(time) && time >= start && time <= end;
        })
        .flatMap((t) => Object.values(t.metrics || {}).map((m) => m?.[metric]))
        .filter(Number.isFinite);
      // Pre-ventilation archives do not have these metrics.  Do not show an
      // empty zero line that could be mistaken for a measured loss.
      if (!vals.length) continue;
      const lines = [
        ...thresholds(metric, "upper"),
        ...thresholds(metric, "lower"),
      ].filter(Number.isFinite);
      const chartVals = vals.map(convert),
        chartLines = lines.map(convert);
      const [min, max] = [...chartVals, ...chartLines].reduce(
        ([lo, hi], v) => [Math.min(lo, v), Math.max(hi, v)],
        [0, 0],
      );
      const lo = min - 1,
        hi = max + 1,
        y = (v) => 165 - ((v - lo) / (hi - lo)) * 140;
      const markerLayout = this.eventMarkerLayout(
        this.diagnosticMarkers(traces, metric, start, end),
        start,
        end,
        plotRight - plotLeft,
      );
      let chart =
        '<svg class="chart detector-chart" viewBox="0 0 ' +
        chartWidth +
        ' 200" role="img" aria-label="' +
        esc(group + " " + label) +
        '">';
      for (const pos of ["upper", "lower"]) {
        let path = "",
          last = null;
        for (const t of traces) {
          const v = t.metrics?.[pos]?.[metric],
            time = stamp(t.at);
          if (
            !Number.isFinite(v) ||
            !Number.isFinite(time) ||
            time < start ||
            time > end
          )
            continue;
          path += `${last == null || time - last > parameters.person_step_seconds * 1000 ? "M" : "L"}${chartX(t.at).toFixed(2)},${y(convert(v)).toFixed(2)} `;
          last = time;
        }
        chart += `<path class="${pos} ${pos === "upper" ? "temperature" : "humidity"}" data-series="detector_${metric}_${pos}" d="${path}"/>`;
        for (const v of thresholds(metric, pos).filter(Number.isFinite)) {
          const display = convert(v);
          chart += `<line x1="${plotLeft}" x2="${plotRight}" y1="${y(display)}" y2="${y(display)}" stroke="${pos === "upper" ? "#ff6b4a" : "#42a5ff"}" stroke-dasharray="4 5"><title>Schwelle ${pos === "upper" ? "oben" : "unten"}: ${num(display, 2)}</title></line>`;
        }
      }
      for (let n = 0; n <= 4; n++) {
        const v = lo + ((hi - lo) * n) / 4,
          t = start + ((end - start) * n) / 4;
        chart += `<text x="4" y="${y(v) + 4}">${num(v, 1)}</text><text text-anchor="middle" x="${chartX(t)}" y="192">${clock(t)}</text>`;
      }
      chart += markerLayout.markers
        .map((marker) => {
          const eventX = chartX(marker.at),
            pointX = chartX(marker.pointAt),
            pointY = y(convert(marker.value)),
            labelY = Math.max(14, pointY - 10 - marker.lane * 13);
          return `<g class="event-marker diagnostic-marker" tabindex="0" role="button" data-action="event-marker:${esc(marker.event.event_id)}" data-event-id="${esc(marker.event.event_id)}" data-selected="false" aria-label="${esc(events[marker.event.kind] || marker.event.kind)}, ${when(marker.event.effective_at || marker.event.detected_at)} · ${marker.position === "upper" ? "oben" : "unten"}"><line class="event-marker-link" x1="${eventX.toFixed(2)}" y1="${pointY.toFixed(2)}" x2="${pointX.toFixed(2)}" y2="${pointY.toFixed(2)}"/><circle class="event-marker-point" cx="${pointX.toFixed(2)}" cy="${pointY.toFixed(2)}" r="2.5"/><circle class="event-marker-dot" cx="${eventX.toFixed(2)}" cy="${pointY.toFixed(2)}" r="5"/><text class="event-marker-label" text-anchor="middle" x="${eventX.toFixed(2)}" y="${labelY.toFixed(2)}">${marker.index + 1}</text></g>`;
        })
        .join("");
      chart += "</svg>";
      html += `<div class="plot-panel"><h3>${group} · ${label}</h3><p class="muted">Orange: oben · Blau: unten</p>${chart}</div>`;
    }
    html +=
      '</div><div class="card"><h2>Erkennungsbedingungen und Bestätigung</h2><div class="scroll"><table><thead><tr><th>Zeit</th><th>Aktive Prüfungen</th><th>Bedingungen erfüllt</th><th>Bestätigungszeiten</th><th>Ausgelöste Signale</th></tr></thead><tbody>' +
      traces
        .filter((t) => t.signals.length)
        .map(
          (t) =>
            `<tr><td>${when(t.at)}</td><td>${esc(
              t.checks
                ? Object.entries(t.checks)
                    .filter(([, v]) => v)
                    .map(([k]) => signalText[k] || "Erkennung")
                    .join(", ")
                : "Keine Angabe in älteren Daten",
            )}</td><td>${esc(
              Object.entries(t.conditions)
                .filter(([, v]) => v)
                .map(([k]) => signalText[k] || "Erkennungsbedingung")
                .join(", "),
            )}</td><td>${esc(
              Object.entries(t.holds)
                .filter(([, v]) => Number(v) > 0)
                .map(([k, v]) => `${signalText[k] || "Signal"}: ${num(v)} s`)
                .join(" · "),
            )}</td><td>${esc(t.signals.map((k) => events[k] || k).join(", "))}</td></tr>`,
        )
        .join("") +
      "</tbody></table></div></div>";
    this.$("#detection-plots").innerHTML = html;
  }
  drawSettings() {
    const state = this.state;
    if (this.settingsEntry !== this.entry) {
      const field = (d) =>
        `<label class="field">${esc(d.label)} (${esc(d.unit)})<input type="number" name="${esc(d.key)}" aria-describedby="help-${esc(d.key)}" step="${d.integer ? 1 : "any"}" min="${d.minimum ?? 0}" max="${d.maximum}" value="${esc(state.configuration.parameters[d.key] ?? "")}" ${d.optional ? "" : "required"}><small id="help-${esc(d.key)}">${esc(d.description)}</small></label>`;
      const groups = [
        ["temperature", "Temperatur und Ofen"],
        ["temperature_programs", "Temperatursteigerung"],
        ["operation", "Betrieb und Kühlung"],
        ["light", "Licht"],
        ["monitoring", "Überwachung"],
        ["timer", "Timer und Energie"],
        ["display", "Anzeige"],
      ];
      const settingsGroup = ([key, title], open = false) => {
        const entries = state.parameters.filter((d) => d.group === key && !d.expert);
        return entries.length
          ? `<details class="settings-group" ${open ? "open" : ""}><summary>${title}</summary><div class="forms">${entries.map(field).join("")}</div></details>`
          : "";
      };
      const temperatureGroups = groups
        .slice(0, 2)
        .map((group, index) => settingsGroup(group, index === 0))
        .join("");
      const otherGroups = groups
        .slice(2)
        .map((group) => settingsGroup(group))
        .join("");
      const expertGroups = {
        measurement: [
          "Messbasis",
          (d) => ["grid_seconds", "median_seconds"].includes(d.key),
        ],
        door: ["Tür", (d) => d.key.startsWith("door_")],
        vent: ["Lüftung", (d) => d.key.startsWith("vent_")],
        person: [
          "Person",
          (d) =>
            d.key.startsWith("person_") ||
            d.key.startsWith("strong_") ||
            d.key.startsWith("weak_"),
        ],
        infusion: ["Aufguss", (d) => d.key.startsWith("infusion_")],
      };
      const experts = Object.values(expertGroups)
        .map(([title, match]) => {
          const entries = state.parameters.filter((d) => d.expert && match(d));
          return entries.length
            ? `<section class="expert-group"><h3>${title}</h3><div class="forms">${entries.map(field).join("")}</div></section>`
            : "";
        })
        .join("");
      this.$("#settings").innerHTML =
        `<div class="card"><h2>Einstellungen</h2><p id="configuration-lock" class="muted"></p><form><fieldset id="parameters">${temperatureGroups}<section class="settings-programs"><h3>Temperaturprogramme</h3><p class="muted">Programme verteilen Start und Ende über die eingetragene Verteilung. Sie begrenzen keine Saunagänge.</p><div id="program-library"></div></section>${otherGroups}${experts ? `<details class="settings-group"><summary>Experteneinstellungen zur Erkennung</summary><p class="muted">Diese Werte verändern die Erkennung von Türöffnungen, Personen und Aufgüssen. Die Erkennungskontrolle zeigt ihre Wirkung.</p>${experts}</details>` : ""}<button type="submit" class="primary">Einstellungen speichern</button></fieldset></form><div class="row"><a href="/config/integrations/integration/ha_sauna">Sensoren und Geräte zuordnen</a></div></div><div class="card"><h2>Standardwerte</h2><p class="muted">Setzt Parameter, Temperaturprogramm und Protokollierung auf die Standardwerte zurück. Die Zuordnung von Sensoren, Geräten und Tastern bleibt erhalten.</p><button data-action="reset-settings">Standardwerte wiederherstellen</button><p id="settings-reset-status" class="muted" role="status"></p></div><div class="card"><h2>Protokollierung</h2><p class="muted">Im Home-Assistant-Protokoll unter custom_components.ha_sauna. Die Stufe ist auch während einer Saunasitzung änderbar. Das Sitzungsarchiv bleibt unabhängig davon vollständig.</p><div class="row"><label for="log-level">Protokollstufe</label><select id="log-level"><option value="ERROR">ERROR · Fehler</option><option value="INFO">INFO · Betriebsereignisse (Standard)</option><option value="DEBUG">DEBUG · Detaillierte Diagnose</option></select><button data-action="logging">Übernehmen</button></div><p class="muted">INFO enthält Fehler, Warnungen, Zustandswechsel und Schaltbefehle. DEBUG ergänzt Messwerte und Ereignisprüfungen.</p><a href="/config/logs">Home-Assistant-Protokoll öffnen</a></div><div class="card"><h2>Sitzungsarchiv</h2><p class="muted">Alle empfangenen Messwerte, Sitzungsverläufe und Ereigniszuordnungen herunterladen.</p><button data-action="export">Archiv als ZIP herunterladen</button></div>`;
      this.$("#log-level").value = state.configuration.log_level;
      this.settingsEntry = this.entry;
      this.programDraft = null;
      this.programLibraryNeedsRender = true;
    }
    this.$("#parameters").disabled = !this.hass.user?.is_admin;
    for (const d of state.parameters) {
      const input = this.$(`#parameters input[name="${d.key}"]`);
      if (!input) continue;
      input.disabled =
        !this.hass.user?.is_admin || (state.configuration_locked && !d.live_editable);
      if (!input.dataset.edited && this.shadowRoot.activeElement !== input)
        input.value =
          d.key === "target_temperature_c"
            ? state.target_temperature
            : (state.configuration.parameters[d.key] ?? "");
    }
    this.$("#log-level").disabled = !this.hass.user?.is_admin;
    this.$('[data-action="logging"]').disabled = !this.hass.user?.is_admin;
    this.$('[data-action="reset-settings"]').disabled =
      !this.hass.user?.is_admin || state.configuration_locked;
    this.$("#configuration-lock").textContent = state.configuration_locked
      ? "Solltemperatur, Endtemperatur und Verteilung der Steigerung sind änderbar. Andere Einstellungen bleiben bis zum Ende der Saunasitzung gesperrt. Laufende Fristen und Heizsperren bleiben immer wirksam."
      : "Alle erforderlichen Einstellungen haben Standardwerte. Änderungen der Grundeinstellungen gelten für die nächste Saunasitzung.";
    this.renderProgramLibrary();
  }
  programBounds() {
    const definitions = this.state?.parameters || [],
      temperature = definitions.find((d) => d.key === "target_temperature_c"),
      gangs = definitions.find((d) => d.key === "temperature_gangs");
    return {
      minimum: Number(temperature?.minimum),
      maximum: Number(temperature?.maximum),
      gangMinimum: Number(gangs?.minimum),
      gangMaximum: Number(gangs?.maximum),
    };
  }
  currentProgramDraft() {
    return (
      this.programDraft ||
      (this.state?.configuration?.temperature_programs || []).map((program) => ({
        ...program,
        temperature_steps: Array.isArray(program.temperature_steps)
          ? [...program.temperature_steps]
          : undefined,
      }))
    );
  }
  readProgramDraft() {
    const rows = [...this.shadowRoot.querySelectorAll("[data-program-id]")];
    if (!rows.length) return this.currentProgramDraft();
    return rows.map((row) => {
      const id = row.dataset.programId,
        name = row.querySelector('[data-program-field="name"]').value,
        kind = row.dataset.programKind || "even";
      if (kind === "steps")
        return {
          id,
          name,
          temperature_steps: [...row.querySelectorAll("[data-program-step]")].map(
            (input) => Number(input.value),
          ),
        };
      return {
        id,
        name,
        start_c: Number(row.querySelector('[data-program-field="start_c"]').value),
        end_c: Number(row.querySelector('[data-program-field="end_c"]').value),
        distribution_gangs: Number(
          row.querySelector('[data-program-field="distribution_gangs"]').value,
        ),
      };
    });
  }
  catalogProgramForm(program, bounds, editable) {
    const kind =
        this.catalogProgramKinds?.[program.id] ||
        (Array.isArray(program.temperature_steps) ? "steps" : "even"),
      steps = Array.isArray(program.temperature_steps)
        ? program.temperature_steps
        : this.distributedSteps(
            Number(program.start_c),
            Number(program.end_c),
            Number(program.distribution_gangs),
          ),
      disabled = editable ? "" : "disabled",
      count = Math.max(1, Math.min(bounds.gangMaximum, steps.length));
    const countId = `catalog-${program.id}-step-count`,
      distributionId = `catalog-${program.id}-distribution-gangs`;
    const selector = `<div class="program-kind"><button type="button" data-action="catalog-kind:even:${esc(program.id)}" aria-pressed="${kind === "even"}" ${disabled}>Gleichmäßig</button><button type="button" data-action="catalog-kind:steps:${esc(program.id)}" aria-pressed="${kind === "steps"}" ${disabled}>Einzelne Stufen</button></div>`;
    const values =
      kind === "steps"
        ? `<div class="row"><label class="field" for="${esc(countId)}"><span>Stufen ${this.distributionInfo(`catalog:${program.id}`, steps)}</span><input id="${esc(countId)}" data-program-step-count type="number" min="1" max="${bounds.gangMaximum}" step="1" value="${count}" ${disabled}></label></div><div class="program-step-fields">${this.resizeSteps(
            steps,
            count,
          )
            .map(
              (value, index) =>
                `<label class="field" for="catalog-${esc(program.id)}-step-${index}">Stufe ${index + 1}<input id="catalog-${esc(program.id)}-step-${index}" data-program-step="${index}" type="number" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(value)}" ${disabled}></label>`,
            )
            .join("")}</div>`
        : `<div class="row"><label class="field">Start<input data-program-field="start_c" type="number" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(program.start_c)}" ${disabled}></label><label class="field">Ende<input data-program-field="end_c" type="number" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(program.end_c)}" ${disabled}></label><label class="field" for="${esc(distributionId)}"><span>Verteilung ${this.distributionInfo(`catalog:${program.id}`, this.distributedSteps(Number(program.start_c), Number(program.end_c), Number(program.distribution_gangs)))}</span><input id="${esc(distributionId)}" data-program-field="distribution_gangs" type="number" step="1" min="${bounds.gangMinimum}" max="${bounds.gangMaximum}" value="${esc(program.distribution_gangs)}" ${disabled}></label></div>`;
    return `<div class="program-form" data-program-id="${esc(program.id)}" data-program-kind="${kind}"><label class="field">Name<input data-program-field="name" value="${esc(program.name)}" ${disabled}></label>${selector}${values}<button type="button" data-action="program-remove:${esc(program.id)}" ${disabled}>Entfernen</button></div>`;
  }
  renderProgramLibrary() {
    const target = this.$("#program-library");
    if (!target) return;
    const state = this.state,
      bounds = this.programBounds(),
      editable = !!this.hass.user?.is_admin && !state.configuration_locked,
      programs = this.currentProgramDraft(),
      button = state.configuration.button_program || "current";
    if (target.dataset.editable === String(editable) && !this.programLibraryNeedsRender)
      return;
    const option = (value, label) =>
      `<option value="${esc(value)}" ${button === value ? "selected" : ""}>${esc(label)}</option>`;
    target.innerHTML = `<div class="row"><label class="field">Tasterprogramm<select id="button-program" ${editable ? "" : "disabled"}>${option("current", "Aktuelle Temperaturwahl")}${option("constant", "Konstanttemperatur")}${programs.map((program) => option(program.id, program.name)).join("")}</select></label></div>${programs.map((program) => this.catalogProgramForm(program, bounds, editable)).join("")}<div class="row"><button type="button" data-action="program-add" ${editable ? "" : "disabled"}>Programm hinzufügen</button><button type="button" data-action="program-save" class="primary" ${editable ? "" : "disabled"}>Programme speichern</button></div>${editable ? "" : '<p class="muted">Nur Home-Assistant-Administratoren können Programme außerhalb einer Saunasitzung bearbeiten.</p>'}`;
    target.dataset.editable = String(editable);
    this.programLibraryNeedsRender = false;
  }
  newProgramId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const random = new Uint32Array(2);
    globalThis.crypto?.getRandomValues?.(random);
    return `program-${Date.now().toString(36)}-${random[0].toString(36)}${random[1].toString(36)}`;
  }
  addProgram() {
    const bounds = this.programBounds(),
      programs = this.readProgramDraft();
    programs.push({
      id: this.newProgramId(),
      name: "Neues Programm",
      start_c: bounds.minimum,
      end_c: bounds.minimum,
      distribution_gangs: Math.max(1, bounds.gangMinimum),
    });
    this.programDraft = programs;
    this.programLibraryNeedsRender = true;
    this.renderProgramLibrary();
  }
  removeProgram(id) {
    this.programDraft = this.readProgramDraft().filter((program) => program.id !== id);
    this.programLibraryNeedsRender = true;
    this.renderProgramLibrary();
  }
  async savePrograms() {
    this.programDraft = this.readProgramDraft();
    await this.api(`/${this.entry}/programs`, "POST", { programs: this.programDraft });
    this.programDraft = null;
    this.settingsEntry = null;
    await this.refresh();
  }
  async saveSettings() {
    this.message(null);
    const partial = this.state.configuration_locked;
    const values = partial ? {} : { ...this.state.configuration.parameters };
    for (const [key, value] of new FormData(this.$("form"))) {
      if (
        partial &&
        key === "target_temperature_c" &&
        !this.$(`input[name="${key}"]`).dataset.edited
      )
        continue;
      if (value !== "") values[key] = Number(value);
      else if (partial) values[key] = null;
      else delete values[key];
    }
    await this.updateParameters(values, false, partial);
  }
  async resetSettings() {
    const entry = this.entry;
    const saved = await this.api(`/${entry}/parameters/reset`, "POST");
    await this.waitForConfiguration(entry, saved.parameters, saved.configuration);
    this.settingsEntry = null;
    this.progressionDraft = null;
    this.shadowRoot
      .querySelectorAll("#parameters input[data-edited]")
      .forEach((input) => delete input.dataset.edited);
    await this.refresh();
    const status = this.shadowRoot.querySelector("#settings-reset-status");
    if (status) status.textContent = "Standardwerte wurden wiederhergestellt.";
  }
  setPanelView(action) {
    this.view = action;
    this.$("#current").hidden = action !== "overview";
    this.$("#details").hidden = action !== "detail";
    this.$("#history").hidden = !["history", "diagnostics"].includes(action);
    this.$("#settings").hidden = action !== "settings";
    this.$("#plots").hidden = action === "diagnostics";
    this.$("#detection-plots").hidden = action !== "diagnostics";
    this.$("#gangs").hidden = action === "diagnostics";
    this.$("#event-list").hidden = action === "diagnostics";
  }
  async action(action) {
    this.message(null);
    const permissions = this.state?.permissions || {};
    if (
      (action === "operation" && !permissions.control) ||
      ((action === "target" || action.startsWith("preset:")) &&
        !permissions.temperature) ||
      ((action === "program-free" ||
        action === "program-apply" ||
        action.startsWith("profile:")) &&
        !permissions.program) ||
      ((action === "program-add" ||
        action === "program-save" ||
        action.startsWith("program-remove:") ||
        action === "button-program") &&
        (!permissions.admin || this.state.configuration_locked)) ||
      (action.startsWith("light:") && !permissions.light) ||
      ((action === "manual-light" || action === "manual-light-overview") &&
        (!permissions.admin || !permissions.light)) ||
      (action.startsWith("heater:") && (!permissions.admin || !permissions.heater)) ||
      (action.startsWith("control-mode:") &&
        (!permissions.admin || this.state.configuration_locked)) ||
      (action.startsWith("end-phase:") && !permissions.admin) ||
      (action === "reset-settings" &&
        (!permissions.admin || this.state.configuration_locked)) ||
      ((action === "details" ||
        action === "detail" ||
        action === "detail-history" ||
        action === "diagnostics" ||
        action === "settings") &&
        !permissions.admin)
    )
      return;
    if (action === "configure") {
      await this.action("details");
      return this.action("settings");
    }
    if (action === "reset-settings") return this.resetSettings();
    if (action === "logging") {
      await this.api(`/${this.entry}/logging`, "POST", {
        level: this.$("#log-level").value,
      });
      await this.refresh();
      return;
    }
    if (action === "menu") {
      this.dispatchEvent(
        new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }),
      );
      return;
    }
    if (action.startsWith("event-marker:")) {
      this.highlightEvent(action.slice(13), true);
      return;
    }
    if (action.startsWith("event-row:")) {
      this.focusEvent(action.slice(10));
      return;
    }
    if (action === "normal" || action === "details") {
      this.$(".normal-tabs").hidden = action !== "normal";
      this.$(".detail-tabs").hidden = action !== "details";
      this.shadowRoot
        .querySelectorAll(".main-tabs button")
        .forEach((b) =>
          b.setAttribute("aria-selected", String(b.dataset.action === action)),
        );
      return this.action(action === "normal" ? "overview" : "detail");
    }
    if (["overview", "history", "detail", "diagnostics", "settings"].includes(action)) {
      if (action === "history") {
        this.historyDetail = false;
        this.positions = new Set(["upper"]);
      }
      this.setPanelView(action);
      this.shadowRoot
        .querySelectorAll(".normal-tabs button,.detail-tabs button")
        .forEach((b) =>
          b.setAttribute("aria-selected", String(b.dataset.action === action)),
        );
      if (["history", "diagnostics"].includes(action)) return this.refresh(true);
      if (action !== "overview") this.drawHistory();
      return;
    }
    if (action === "detail-history") {
      this.historyDetail = true;
      this.positions = new Set(["upper", "lower"]);
      this.setPanelView("history");
      this.shadowRoot
        .querySelectorAll(".detail-tabs button")
        .forEach((b) =>
          b.setAttribute("aria-selected", String(b.dataset.action === action)),
        );
      return this.refresh(true);
    }
    if (action.startsWith("preset:")) return this.changeTarget(Number(action.slice(7)));
    if (action.startsWith("program-mode:")) {
      this.selectProgramMode(
        action.slice(13),
        this.state.configuration.temperature_programs || [],
      );
      return;
    }
    if (action.startsWith("program-select:")) {
      this.selectNamedProgram(
        action.slice(15),
        this.state.configuration.temperature_programs || [],
      );
      return;
    }
    if (action.startsWith("program-kind:")) {
      this.setFreeProgramKind(action.slice(13));
      return;
    }
    if (action.startsWith("program-info:")) {
      const id = action.slice(13);
      this.programInfoOpen = this.programInfoOpen === id ? null : id;
      if (id.startsWith("catalog:")) {
        this.programLibraryNeedsRender = true;
        this.renderProgramLibrary();
      } else this.drawCurrent();
      return;
    }
    if (action.startsWith("catalog-kind:")) {
      const [, kind, id] = action.split(":");
      const drafts = this.readProgramDraft(),
        program = drafts.find((item) => item.id === id);
      if (!program) return;
      const steps = Array.isArray(program.temperature_steps)
        ? program.temperature_steps
        : this.distributedSteps(
            program.start_c,
            program.end_c,
            program.distribution_gangs,
          );
      if (kind === "steps") program.temperature_steps = steps;
      else {
        program.start_c = steps[0];
        program.end_c = steps.at(-1);
        program.distribution_gangs = steps.length;
        delete program.temperature_steps;
      }
      this.catalogProgramKinds = { ...this.catalogProgramKinds, [id]: kind };
      this.programDraft = drafts;
      this.programLibraryNeedsRender = true;
      this.renderProgramLibrary();
      return;
    }
    if (action === "program-apply") {
      const choice = this.programChoice(
          this.state.configuration.temperature_programs || [],
        ),
        entry = this.entry;
      if (!this.programDirty()) return;
      this.programSaveState = "saving";
      this.drawCurrent();
      try {
        if (
          choice === "individual" &&
          (this.freeProgramKind === "steps" ||
            (Array.isArray(this.state.configuration.temperature_steps) &&
              !this.freeProgramKind))
        ) {
          await this.api(`/${entry}/program`, "POST", {
            temperature_steps: this.freeProgramValues(),
          });
        } else if (choice === "individual") {
          const configuration = this.state.configuration,
            liveEdit =
              configuration.program_mode === "progressive" &&
              configuration.selected_program_id == null &&
              !Array.isArray(configuration.temperature_steps) &&
              !(
                this.progressionDraft &&
                Object.hasOwn(this.progressionDraft, "progression-start")
              );
          await this.action(liveEdit ? "progression" : "program-free");
        } else await this.api(`/${entry}/program`, "POST", { profile: choice });
        if (this.entry !== entry) return;
        this.programSelectionDraft = null;
        this.freeProgramKind = null;
        this.freeProgramStepsDraft = null;
        this.progressionDraft = null;
        this.programSaveState = "saved";
        await this.refresh();
        return;
      } catch (error) {
        this.programSaveState = null;
        this.drawCurrent();
        throw error;
      }
    }
    if (action.startsWith("profile:")) {
      const entry = this.entry,
        draft = this.progressionDraft;
      await this.temperatureChange;
      if (this.entry !== entry) return;
      await this.api(`/${entry}/program`, "POST", { profile: action.slice(8) });
      if (this.entry !== entry) return;
      if (this.progressionDraft === draft) this.progressionDraft = null;
      await this.refresh();
      return;
    }
    if (action === "program-add") {
      this.addProgram();
      return;
    }
    if (action.startsWith("program-remove:")) {
      this.removeProgram(action.slice(15));
      return;
    }
    if (action === "program-save") return this.savePrograms();
    if (action === "button-program") {
      await this.api(`/${this.entry}/button-program`, "POST", {
        profile: this.$("#button-program").value,
      });
      await this.refresh();
      return;
    }
    if (action === "progression") {
      const { start, end, gangs } = this.progressionValues();
      const entry = this.entry,
        draft = this.progressionDraft;
      if (
        this.progressionDraft &&
        Object.hasOwn(this.progressionDraft, "progression-start")
      )
        return this.action("program-free");
      if (!permissions.temperature) return;
      const changed = {};
      if (end !== this.state.configuration.parameters.final_temperature_c)
        changed.final_temperature_c = end;
      if (gangs !== this.state.configuration.parameters.temperature_gangs)
        changed.temperature_gangs = gangs;
      if (Object.keys(changed).length) {
        await this.temperatureChange;
        if (this.entry !== entry) return;
        await this.api(`/${entry}/temperature`, "POST", changed);
        if (this.entry !== entry) return;
        if (this.progressionDraft === draft) this.progressionDraft = null;
        await this.refresh();
      }
      return;
    }
    if (action === "program-free") {
      const entry = this.entry,
        draft = this.progressionDraft;
      const { start, end, gangs } = this.progressionValues();
      const explicitStart = Object.hasOwn(draft || {}, "progression-start");
      const pendingTemperatureChange = this.temperatureChange,
        parameters = await pendingTemperatureChange;
      if (this.entry !== entry) return;
      const body = {
        target_temperature_c: explicitStart
          ? start
          : pendingTemperatureChange
            ? Number(parameters?.target_temperature_c)
            : start,
        final_temperature_c: end,
        temperature_gangs: gangs,
      };
      if (!Number.isFinite(body.target_temperature_c))
        throw Error("Start, Ende und Verteilung vollständig eingeben");
      await this.api(`/${entry}/program`, "POST", body);
      if (this.entry !== entry) return;
      if (this.progressionDraft === draft) this.progressionDraft = null;
      await this.refresh();
      return;
    }
    if (action === "target") return this.changeTarget(Number(this.$("#target").value));
    if (action.startsWith("control-mode:")) {
      const mode = action.slice(13);
      if (mode !== "automatic" && mode !== "manual")
        throw Error("Ungültiger Betriebsmodus");
      await this.api(`/${this.entry}/control-mode`, "POST", { mode });
      await this.refresh();
      return;
    }
    if (action.startsWith("light:")) {
      const preset = action.slice(6),
        value =
          preset === "true"
            ? true
            : preset === "false"
              ? false
              : preset === "auto"
                ? null
                : "normal";
      await this.api(`/${this.entry}/light`, "POST", { value });
      this.manualLightDraft = null;
      await this.refresh();
      return;
    }
    if (action === "manual-light" || action === "manual-light-overview") {
      const input =
        action === "manual-light"
          ? "#manual-light-value"
          : "#manual-light-value-overview";
      const value = Number(this.$(input).value);
      if (!Number.isFinite(value) || value < 0 || value > 100)
        throw Error("Helligkeit zwischen 0 und 100 % eingeben");
      await this.api(`/${this.entry}/light`, "POST", { value });
      this.manualLightDraft = null;
      await this.refresh();
      return;
    }
    if (action.startsWith("heater:")) {
      const preset = action.slice(7),
        value = preset === "true" ? true : preset === "false" ? false : null;
      if (preset !== "true" && preset !== "false" && preset !== "auto")
        throw Error("Ungültige Ofensteuerung");
      if (
        value === true &&
        this.state.configuration?.control_mode === "manual" &&
        !this.state.operation_enabled
      )
        await this.api(`/${this.entry}/control`, "POST", { enabled: true });
      await this.api(`/${this.entry}/heater`, "POST", { value });
      await this.refresh();
      return;
    }
    if (action.startsWith("end-phase:")) {
      const [, purpose, token] = action.split(":");
      await this.api(`/${this.entry}/finish_phase`, "POST", {
        purpose,
        token: decodeURIComponent(token),
      });
      await this.refresh();
      return;
    }
    if (action === "operation") {
      await this.api(`/${this.entry}/control`, "POST", {
        enabled: !this.state.operation_enabled,
      });
      await this.refresh();
    }
    if (action === "zoom-in" || action === "zoom-out") {
      this.ensureHistoryWindow();
      const svg = this.$("svg.session-chart"),
        [left, right] = this.window,
        factor = action === "zoom-in" ? 2 : 0.5;
      if (svg)
        this.zoomAt(
          factor,
          svg.getBoundingClientRect().left + svg.getBoundingClientRect().width / 2,
          svg,
        );
      else {
        const center = (left + right) / 2,
          width = (right - left) / factor;
        this.setHistoryWindow(center - width / 2, center + width / 2);
        this.drawHistory();
      }
    }
    if (action === "reset-zoom") {
      const [start, end] = this.historyDomain();
      this.setHistoryWindow(start, end);
      this.drawHistory();
    }
    if (action === "history-detail") {
      this.historyDetail = !this.historyDetail;
      if (this.historyDetail) this.positions = new Set(["upper", "lower"]);
      else this.positions = new Set(["upper"]);
      this.drawHistory();
    }
    if (action.startsWith("position-")) {
      const p = action.slice(9);
      this.positions.has(p) ? this.positions.delete(p) : this.positions.add(p);
      this.drawHistory();
    }
    if (action === "export") {
      const signed = await this.hass.callWS({
        type: "auth/sign_path",
        path: `/api/ha_sauna/${this.entry}/export`,
      });
      const link = document.createElement("a");
      link.href = signed.path;
      link.download = "ha-sauna-archive.zip";
      this.shadowRoot.append(link);
      link.click();
      link.remove();
    }
  }
}
if (!customElements.get("ha-sauna-panel"))
  customElements.define("ha-sauna-panel", SaunaPanel);
