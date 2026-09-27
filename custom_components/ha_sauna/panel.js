/* Native HA custom panel. Backend objects are authoritative; no control model here. */
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
const stamp = (v) => (v ? new Date(v).getTime() : null);
const timestampFormatterLimit = 12;
const timestampFormatOptions = Object.freeze({
  when: Object.freeze({
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }),
  tooltip: Object.freeze({
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "shortOffset",
  }),
  clock: Object.freeze({ hour: "2-digit", minute: "2-digit" }),
});
const timestampFormatters = new Map();
// Resolve once for a tooltip/axis batch. Zone names matter: two zones with the
// same current offset can have different rules at the measurement's timestamp.
const localTimeZone = () => new Intl.DateTimeFormat().resolvedOptions().timeZone;
const timestampFormatter = (style, timeZone = localTimeZone()) => {
  const key = `${style}:${timeZone || "default"}`;
  let formatter = timestampFormatters.get(key);
  if (formatter) {
    timestampFormatters.delete(key);
    timestampFormatters.set(key, formatter);
    return formatter;
  }
  formatter = new Intl.DateTimeFormat("de-DE", {
    ...timestampFormatOptions[style],
    ...(timeZone ? { timeZone } : {}),
  });
  timestampFormatters.set(key, formatter);
  if (timestampFormatters.size > timestampFormatterLimit)
    timestampFormatters.delete(timestampFormatters.keys().next().value);
  return formatter;
};
const when = (v, timeZone) => {
  if (!v) return "–";
  const date = new Date(v);
  // Intl.DateTimeFormat#format throws for an invalid date, while the original
  // Date#toLocaleString path returns its established fallback text.
  return Number.isFinite(date.getTime())
    ? timestampFormatter("when", timeZone).format(date)
    : date.toLocaleString("de-DE", timestampFormatOptions.when);
};
const tooltipWhen = (v, timeZone) => {
  if (
    (typeof v !== "string" && (typeof v !== "number" || !Number.isFinite(v))) ||
    (typeof v === "string" && !v.trim())
  )
    return null;
  const date = new Date(v);
  if (!Number.isFinite(date.getTime())) return null;
  const fraction =
    typeof v === "string" ? v.match(/\.(\d+)(?:Z|[+-]\d\d:\d\d)?$/i)?.[1] : null;
  return timestampFormatter("tooltip", timeZone)
    .formatToParts(date)
    .map((part) =>
      part.type === "second" && fraction ? `${part.value}.${fraction}` : part.value,
    )
    .join("");
};
const clock = (v, timeZone) => {
  const date = new Date(v);
  return Number.isFinite(date.getTime())
    ? timestampFormatter("clock", timeZone).format(date)
    : date.toLocaleTimeString("de-DE", timestampFormatOptions.clock);
};
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
  nachlauf: "Ofenkühlung",
  zwangskühlung: "Zwangskühlung (historisch)",
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

const appearanceHex = (value) =>
  typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value.trim())
    ? value.trim().toUpperCase()
    : null;
const appearanceRgb = (hex) => {
  const value = appearanceHex(hex);
  return value
    ? [1, 3, 5].map((index) => parseInt(value.slice(index, index + 2), 16))
    : null;
};
const appearanceThemeHex = (value) => {
  const hex = appearanceHex(value);
  if (hex) return hex;
  const shorthand = String(value || "")
    .trim()
    .match(/^#([0-9a-f]{3})$/i);
  if (shorthand)
    return `#${[...shorthand[1]]
      .map((digit) => digit.repeat(2))
      .join("")
      .toUpperCase()}`;
  const rgb = String(value || "").match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  return rgb && rgb.slice(1).every((channel) => Number(channel) <= 255)
    ? `#${rgb
        .slice(1)
        .map((channel) => Number(channel).toString(16).padStart(2, "0"))
        .join("")
        .toUpperCase()}`
    : null;
};
const appearanceAlpha = (hex, alpha) => {
  const rgb = appearanceRgb(hex);
  return rgb ? `rgba(${rgb.join(", ")}, ${alpha})` : "transparent";
};
const appearanceContrast = (hex) => {
  const rgb = appearanceRgb(hex);
  if (!rgb) return null;
  const luminance = rgb.reduce((sum, channel, index) => {
    const x = channel / 255;
    return (
      sum +
      [0.2126, 0.7152, 0.0722][index] *
        (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)
    );
  }, 0);
  return (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) ? "#000000" : "#FFFFFF";
};
const appearanceContrastRatio = (left, right) => {
  const luminance = (hex) =>
    appearanceRgb(hex).reduce((sum, channel, index) => {
      const x = channel / 255;
      return (
        sum +
        [0.2126, 0.7152, 0.0722][index] *
          (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)
      );
    }, 0);
  const a = luminance(left),
    b = luminance(right);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
};
const appearanceTickValues = (bounds) => {
  if (
    !bounds ||
    !Number.isFinite(bounds.minimum) ||
    !Number.isFinite(bounds.maximum) ||
    bounds.maximum <= bounds.minimum ||
    !Number.isFinite(bounds.maximum - bounds.minimum)
  )
    return [];
  const span = bounds.maximum - bounds.minimum;
  const rough = span / 5;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step =
    [1, 2, 5, 10].map((unit) => unit * power).find((value) => span / value <= 7) ||
    10 * power;
  if (!Number.isFinite(step) || step <= 0) return [bounds.minimum, bounds.maximum];
  const values = [bounds.minimum];
  for (
    let index = Math.ceil(bounds.minimum / step), count = 0;
    count < 7;
    index++, count++
  ) {
    const value = index * step;
    if (value >= bounds.maximum - step * 1e-9) break;
    if (value > bounds.minimum + step * 1e-9)
      values.push(Number(value.toPrecision(12)));
  }
  values.push(bounds.maximum);
  return values;
};

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
      (point.displayGap ||
        (!Object.hasOwn(point, "displayGap") &&
          time - (segment.at(-1).time ?? stamp(segment.at(-1).received_at)) > ttl))
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
  let key = null,
    bucket = [],
    first,
    last,
    minimum,
    maximum;
  const flush = () => {
    if (!bucket.length) return;
    // Scan the original bucket so a repeated reference has the same Set/filter
    // behavior and source ordering as before, without creating either helper.
    for (const point of bucket)
      if (point === first || point === last || point === minimum || point === maximum)
        output.push(point);
    bucket.length = 0;
  };
  for (const point of segment) {
    const next = Math.floor(x(point.time ?? stamp(point.received_at)));
    if (key !== null && next !== key) flush();
    if (!bucket.length) {
      first = last = minimum = maximum = point;
    } else {
      last = point;
      // Negating the comparisons keeps the prior reducer's last-tie choice,
      // including JavaScript's behavior for non-comparable values.
      if (!(minimum.value < point.value)) {
        minimum = point;
      }
      if (!(maximum.value > point.value)) {
        maximum = point;
      }
    }
    key = next;
    bucket.push(point);
  }
  flush();
  return output;
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
const lowerBoundNumber = (values, value) => {
  let low = 0,
    high = values.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (values[middle] < value) low = middle + 1;
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
const roundHistoryCoordinate = (value) => Number(value.toFixed(2));

// Return numeric Canvas commands, not SVG text.  The equations and the
// two-decimal rounding intentionally match monotoneHistoryPath exactly.
const monotoneHistoryCommands = (points, x, y, target) => {
  const commands = [];
  if (!points.length) return [];
  const at = (value) => roundHistoryCoordinate(value);
  const firstPoint = points[0];
  let x0 = x(firstPoint.time ?? stamp(firstPoint.received_at)),
    y0 = y(firstPoint.value);
  if (target) target.moveTo(at(x0), at(y0));
  else commands.push(["M", at(x0), at(y0)]);
  if (points.length === 1) return commands;

  let next = points[1],
    x1 = x(next.time ?? stamp(next.received_at)),
    y1 = y(next.value),
    previousSlope = (y1 - y0) / (x1 - x0 || 1),
    currentTangent = previousSlope;
  for (let index = 0; index < points.length - 1; index++) {
    let nextTangent = previousSlope,
      after,
      x2,
      y2,
      nextSlope;
    if (index + 2 < points.length) {
      after = points[index + 2];
      x2 = x(after.time ?? stamp(after.received_at));
      y2 = y(after.value);
      nextSlope = (y2 - y1) / (x2 - x1 || 1);
      if (previousSlope * nextSlope <= 0) nextTangent = 0;
      else {
        const before = x1 - x0,
          afterDistance = x2 - x1,
          w1 = 2 * afterDistance + before,
          w2 = afterDistance + 2 * before;
        nextTangent = (w1 + w2) / (w1 / previousSlope + w2 / nextSlope);
      }
    }
    const dx = (x1 - x0) / 3,
      controlX0 = at(x0 + dx),
      controlY0 = at(y0 + currentTangent * dx),
      controlX1 = at(x1 - dx),
      controlY1 = at(y1 - nextTangent * dx),
      endX = at(x1),
      endY = at(y1);
    if (target)
      target.bezierCurveTo(controlX0, controlY0, controlX1, controlY1, endX, endY);
    else commands.push(["C", controlX0, controlY0, controlX1, controlY1, endX, endY]);
    if (index + 2 < points.length) {
      x0 = x1;
      y0 = y1;
      next = after;
      x1 = x2;
      y1 = y2;
      previousSlope = nextSlope;
    }
    currentTangent = nextTangent;
  }
  return commands;
};

const historyStyleKey = (style) =>
  [style.stroke, style.lineWidth, style.globalAlpha, style.lineDash.join(",")].join(
    "|",
  );

class HistoryCurves {
  constructor(
    canvas,
    overviewCanvas,
    { Path2DClass = globalThis.Path2D, styles = {} } = {},
  ) {
    if (!Path2DClass) throw Error("HistoryCurves requires Path2D");
    this.canvas = canvas;
    this.overviewCanvas = overviewCanvas;
    this.Path2DClass = Path2DClass;
    this.styles = styles;
    this.mainPaths = new Map();
    this.mainPaintKey = null;
    this.overviewPath = null;
    this.overviewPathKey = null;
    this.overviewPaintKey = null;
    this.canvasGeometry = null;
    this.overviewGeometry = null;
  }

  curveStyle(position, quantity, supplied = {}) {
    const defaultStyle = {
      stroke: this.styles[`${position}:${quantity}`]?.stroke,
      lineWidth: 3.5,
      globalAlpha: position === "lower" ? 0.75 : 1,
      lineDash: position === "lower" ? [8, 5] : [],
    };
    return {
      ...defaultStyle,
      ...(this.styles[`${position}:${quantity}`] || {}),
      ...(supplied[`${position}:${quantity}`] || {}),
      lineDash:
        supplied[`${position}:${quantity}`]?.lineDash ||
        this.styles[`${position}:${quantity}`]?.lineDash ||
        defaultStyle.lineDash,
    };
  }

  resizeCanvas(canvas, geometry, kind) {
    if (!canvas || !geometry) return false;
    const width = Math.max(1, Number(geometry.width)),
      height = Math.max(1, Number(geometry.height)),
      dpr = Math.max(1, Number(geometry.dpr) || 1),
      bitmapWidth = Math.round(width * dpr),
      bitmapHeight = Math.round(height * dpr),
      previous = kind === "main" ? this.canvasGeometry : this.overviewGeometry,
      changed =
        !previous ||
        previous.width !== width ||
        previous.height !== height ||
        previous.dpr !== dpr;
    if (changed) {
      // CSS and bitmap sizes have separate meanings.  This is the only place
      // bitmap dimensions are reset; drawing uses clearRect below.
      if (canvas.style) {
        const cssWidth = `${width}px`,
          cssHeight = `${height}px`;
        if (canvas.style.width !== cssWidth) canvas.style.width = cssWidth;
        if (canvas.style.height !== cssHeight) canvas.style.height = cssHeight;
      }
      if (canvas.width !== bitmapWidth) canvas.width = bitmapWidth;
      if (canvas.height !== bitmapHeight) canvas.height = bitmapHeight;
      const state = { width, height, dpr };
      if (kind === "main") this.canvasGeometry = state;
      else this.overviewGeometry = state;
    }
    return changed;
  }

  buildMainPath(series, snapshot, quantity) {
    const x = snapshot.x,
      y = quantity === "temperature" ? snapshot.yT : snapshot.yH,
      path = new this.Path2DClass();
    for (const segment of historySegments(
      series.values,
      snapshot.start,
      snapshot.end,
      snapshot.ttl,
    ))
      monotoneHistoryCommands(reduceHistorySegment(segment, x), x, y, path);
    return { path };
  }

  mainPathKey(series, snapshot, position, quantity, style) {
    // A caller-provided key/revision owns data invalidation.  Falling back to
    // the values identity makes the fragment useful in small direct callers.
    const source = series.key ?? series.revision ?? series.values;
    return [
      source,
      snapshot.start,
      snapshot.end,
      snapshot.left,
      snapshot.right,
      snapshot.top,
      snapshot.bottom,
      snapshot.width,
      snapshot.height,
      snapshot.ttl,
      position,
      quantity,
      historyStyleKey(style),
      ...(quantity === "temperature"
        ? [snapshot.low, snapshot.high]
        : [snapshot.humidityHigh]),
    ];
  }

  sameKey(left, right) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => value === right[index])
    );
  }

  updateMainPaths(snapshot) {
    const visible = [];
    let pathsChanged = false;
    for (const position of snapshot.positions) {
      for (const quantity of ["temperature", "humidity"]) {
        const name = `${position}:${quantity}`,
          series = snapshot.series.get(name);
        if (!series) continue;
        const style = this.curveStyle(position, quantity, snapshot.styles),
          key = this.mainPathKey(series, snapshot, position, quantity, style),
          cached = this.mainPaths.get(name);
        if (!cached || !this.sameKey(cached.key, key)) {
          const built = this.buildMainPath(series, snapshot, quantity);
          this.mainPaths.set(name, { ...built, key, style });
          pathsChanged = true;
        }
        visible.push(name);
      }
    }
    for (const name of this.mainPaths.keys()) {
      if (!visible.includes(name)) {
        this.mainPaths.delete(name);
        pathsChanged = true;
      }
    }
    return { visible, pathsChanged };
  }

  paintMain(visible, geometry, snapshot) {
    const context = this.canvas?.getContext?.("2d");
    if (!context) return;
    const width = snapshot.width ?? 1200,
      height = snapshot.height ?? 480;
    context.setTransform(
      geometry.dpr * (geometry.width / width),
      0,
      0,
      geometry.dpr * (geometry.height / height),
      0,
      0,
    );
    context.clearRect(0, 0, width, height);
    // historyDisplayValues deliberately contributes neighbours outside the
    // window.  SVG previously clipped them with #plot-clip, so Canvas must
    // clip before replaying their controls as well.
    context.save();
    context.beginPath();
    context.rect(
      snapshot.left,
      snapshot.top,
      snapshot.right - snapshot.left,
      snapshot.bottom - snapshot.top,
    );
    context.clip();
    for (const name of visible) {
      const cached = this.mainPaths.get(name),
        style = cached.style;
      context.strokeStyle = style.stroke;
      context.lineWidth = style.lineWidth;
      context.globalAlpha = style.globalAlpha;
      context.setLineDash(style.lineDash);
      context.stroke(cached.path);
    }
    context.restore();
    context.setLineDash([]);
    context.globalAlpha = 1;
  }

  overviewInput(snapshot) {
    if (snapshot.overview) return snapshot.overview;
    const series = snapshot.series.get("upper:temperature");
    return (
      series && {
        start: snapshot.start,
        end: snapshot.end,
        left: 20,
        right: 1180,
        top: 8,
        bottom: 36,
        low: snapshot.low,
        high: snapshot.high,
        ttl: snapshot.ttl,
        values: series.values,
        key: series.key ?? series.revision,
      }
    );
  }

  updateOverview(snapshot, geometry) {
    const overview = this.overviewInput(snapshot);
    if (!this.overviewCanvas || !overview || !geometry) return false;
    const resized = this.resizeCanvas(this.overviewCanvas, geometry, "overview"),
      style = {
        stroke: this.styles.overview?.stroke,
        lineWidth: 1.5,
        globalAlpha: 1,
        lineDash: [],
      },
      source = overview.key ?? overview.revision ?? overview.values,
      pathKey = [
        source,
        overview.start,
        overview.end,
        overview.low,
        overview.high,
        overview.ttl,
        overview.left,
        overview.right,
        overview.top,
        overview.bottom,
        overview.width,
        overview.height,
        historyStyleKey(style),
      ],
      paintKey = [
        this.overviewPathKey,
        geometry.width,
        geometry.height,
        geometry.dpr,
        this.styles.overview?.track,
      ];
    if (!this.sameKey(this.overviewPathKey, pathKey)) {
      const left = overview.left ?? 20,
        right = overview.right ?? 1180,
        top = overview.top ?? 8,
        bottom = overview.bottom ?? 36,
        x = (time) =>
          left +
          ((time - overview.start) / (overview.end - overview.start)) * (right - left),
        y = (value) =>
          bottom -
          ((value - overview.low) / (overview.high - overview.low || 1)) *
            (bottom - top),
        path = new this.Path2DClass();
      for (const segment of historySegments(
        overview.values,
        overview.start,
        overview.end,
        overview.ttl,
      ))
        monotoneHistoryCommands(reduceHistorySegment(segment, x), x, y, path);
      this.overviewPath = path;
      this.overviewPathKey = pathKey;
    }
    paintKey[0] = this.overviewPathKey;
    if (!resized && this.sameKey(this.overviewPaintKey, paintKey)) return false;
    this.overviewPaintKey = paintKey;
    const context = this.overviewCanvas.getContext("2d");
    context.setTransform(
      geometry.dpr * (geometry.width / (overview.width ?? 1200)),
      0,
      0,
      geometry.dpr * (geometry.height / (overview.height ?? 46)),
      0,
      0,
    );
    context.clearRect(0, 0, overview.width ?? 1200, overview.height ?? 46);
    // The track precedes the curve; the separate SVG handles remain above both.
    context.fillStyle = appearanceAlpha(this.styles.overview?.track, 0.05);
    context.strokeStyle = appearanceAlpha(this.styles.overview?.track, 0.14);
    context.lineWidth = 1;
    context.beginPath();
    context.roundRect(20, 4, 1160, 36, 5);
    context.fill();
    context.stroke();
    context.strokeStyle = style.stroke;
    context.lineWidth = style.lineWidth;
    context.globalAlpha = style.globalAlpha;
    context.setLineDash(style.lineDash);
    context.stroke(this.overviewPath);
    context.setLineDash([]);
    context.globalAlpha = 1;
    return true;
  }

  update(snapshot, geometry) {
    const mainGeometry = geometry;
    const resized = this.resizeCanvas(this.canvas, mainGeometry, "main");
    const { visible, pathsChanged } = this.updateMainPaths(snapshot);
    const mainKey = [
      ...visible.map((name) => this.mainPaths.get(name).key),
      mainGeometry.width,
      mainGeometry.height,
      mainGeometry.dpr,
    ];
    const mainDrawn =
      resized || pathsChanged || !this.sameKey(this.mainPaintKey, mainKey);
    if (mainDrawn) {
      this.paintMain(visible, mainGeometry, snapshot);
      this.mainPaintKey = mainKey;
    }
    const minimapDrawn = this.updateOverview(snapshot, geometry.overview);
    return { mainDrawn, minimapDrawn };
  }
}

/*
 * Persistent history-chart interaction layer.
 *
 * This is a source fragment: panel.js embeds it in its own scope, where
 * `when`, `num`, and `tooltipWhen` retain the established display rules.
 */
function HistoryInteraction(panel, surface, wrap, tooltip, cursor, overview) {
  const view = surface?.ownerDocument?.defaultView || globalThis;
  const document = surface?.ownerDocument || view.document;
  const listeners = [];
  const scrollTargets = new Set();
  const state = {
    dirty: true,
    invalidationScheduled: false,
    geometry: null,
    disposed: false,
    tooltip: null,
    tooltipVisible: false,
    cursorVisible: false,
    cursorX: null,
    transform: null,
    media: null,
    mediaListener: null,
    resizeObserver: null,
    rowTextCache: new WeakMap(),
  };

  const number = (value, digits) =>
    typeof num === "function"
      ? num(value, digits)
      : Number(value).toLocaleString("de-DE", { maximumFractionDigits: digits });
  const dateLabel = (value, timeZone) =>
    typeof when === "function"
      ? when(value, timeZone)
      : new Date(value).toLocaleString("de-DE");
  const sourceDateLabel = (value, timeZone) =>
    typeof tooltipWhen === "function"
      ? tooltipWhen(value, timeZone)
      : value == null
        ? null
        : String(value);
  const sourceValue = (value) => `${typeof value}:${String(value)}`;
  const rowText = (nearest, quantity, timeZone) => {
    const source = nearest.source;
    const cacheable = source && typeof source === "object";
    const rawPresent = source?.raw_value != null;
    const key = [
      timeZone,
      quantity,
      sourceValue(nearest.value),
      sourceValue(source?.raw_value),
      sourceValue(source?.received_at),
      sourceValue(source?.measured_at),
    ].join("|");
    const cached = cacheable ? state.rowTextCache.get(source) : null;
    if (cached?.key === key) return cached.value;
    const valueText = rawPresent ? String(source.raw_value) : number(nearest.value, 6);
    const valueLabel = rawPresent ? "Originalwert" : "Wert";
    const receivedAt = sourceDateLabel(source?.received_at, timeZone);
    const measuredAt = sourceDateLabel(source?.measured_at, timeZone);
    const unit = quantity === "temperature" ? "°C" : "%";
    const value = `${valueLabel} ${valueText} ${unit}${rawPresent ? "" : " · kein Originalwert gespeichert"} · Empfangen ${receivedAt || "–"}${measuredAt ? ` · Gemessen ${measuredAt}` : ""}`;
    if (cacheable) state.rowTextCache.set(source, { key, value });
    return value;
  };
  const pixelRatio = () => Math.max(1, Number(view.devicePixelRatio) || 1);
  const rectFor = (node) => {
    const rect = node?.getBoundingClientRect?.();
    return {
      left: Number(rect?.left) || 0,
      top: Number(rect?.top) || 0,
      width: Math.max(0, Number(rect?.width) || 0),
      height: Math.max(0, Number(rect?.height) || 0),
    };
  };
  const addListener = (target, type, callback, options) => {
    if (!target?.addEventListener) return;
    target.addEventListener(type, callback, options);
    listeners.push([target, type, callback, options]);
  };
  const schedule = (reason) => {
    if (state.disposed) return;
    if (typeof panel.scheduleHistoryRender === "function")
      panel.scheduleHistoryRender(reason);
  };
  const invalidateGeometry = (reason = "geometry", scheduleRender = true) => {
    if (state.disposed) return;
    state.dirty = true;
    if (scheduleRender && !state.invalidationScheduled) {
      state.invalidationScheduled = true;
      schedule(reason);
    }
  };
  const armPixelRatioListener = () => {
    if (state.media && state.mediaListener) {
      state.media.removeEventListener?.("change", state.mediaListener);
      state.media.removeListener?.(state.mediaListener);
    }
    state.media = null;
    state.mediaListener = null;
    if (!view.matchMedia) return;
    const media = view.matchMedia(`(resolution: ${pixelRatio()}dppx)`);
    const changed = () => {
      invalidateGeometry("pixelratio");
      armPixelRatioListener();
    };
    state.media = media;
    state.mediaListener = changed;
    if (media.addEventListener) media.addEventListener("change", changed);
    else media.addListener?.(changed);
  };
  const readGeometry = () => {
    if (state.disposed) return state.geometry;
    if (!state.dirty && state.geometry?.dpr !== pixelRatio()) {
      state.dirty = true;
      schedule("pixelratio");
      armPixelRatioListener();
    }
    if (!state.dirty && state.geometry) return state.geometry;

    // This is deliberately the only layout-read phase.  Callers must obtain
    // geometry before changing the tooltip, cursor, SVG, or canvas.
    const surfaceRect = rectFor(surface);
    const wrapRect = rectFor(wrap || surface);
    const overviewRect = rectFor(overview);
    const dpr = pixelRatio();
    state.geometry = {
      dpr,
      rects: { surface: surfaceRect, wrap: wrapRect, overview: overviewRect },
      canvas: {
        width: Math.max(1, Math.round(surfaceRect.width * dpr)),
        height: Math.max(1, Math.round(surfaceRect.height * dpr)),
        dpr,
        cssWidth: surfaceRect.width,
        cssHeight: surfaceRect.height,
        css: { width: surfaceRect.width, height: surfaceRect.height },
      },
      overviewCanvas: {
        width: Math.max(1, Math.round(overviewRect.width * dpr)),
        height: Math.max(1, Math.round(overviewRect.height * dpr)),
        dpr,
        cssWidth: overviewRect.width,
        cssHeight: overviewRect.height,
        css: { width: overviewRect.width, height: overviewRect.height },
      },
    };
    state.dirty = false;
    state.invalidationScheduled = false;
    return state.geometry;
  };
  const coordinatesForGeometry = (geometry, target, clientX, clientY = 0) => {
    const isOverview = target === overview;
    const rect = isOverview ? geometry.rects.overview : geometry.rects.surface;
    const logicalWidth = 1200;
    const logicalHeight = isOverview ? 46 : 480;
    return {
      x: ((clientX - rect.left) / (rect.width || 1)) * logicalWidth,
      y: ((clientY - rect.top) / (rect.height || 1)) * logicalHeight,
    };
  };
  const coordinates = (target, clientX, clientY = 0) =>
    coordinatesForGeometry(readGeometry(), target, clientX, clientY);
  const tooltipNodes = () => {
    if (!tooltip || state.tooltip) return state.tooltip;
    const heading = document.createElement("strong");
    const rows = document.createElement("div");
    const series = new Map();
    for (const position of ["upper", "lower"])
      for (const quantity of ["temperature", "humidity"]) {
        const row = document.createElement("div");
        const label = document.createElement("span");
        const value = document.createElement("span");
        label.style.color = `var(--sauna-color-series-${quantity})`;
        row.append(label, document.createElement("br"), value);
        row.hidden = true;
        rows.append(row);
        series.set(`${position}:${quantity}`, { row, label, value });
      }
    tooltip.replaceChildren(heading, rows);
    state.tooltip = { heading, series };
    return state.tooltip;
  };
  const setText = (node, text) => {
    if (node.textContent !== text) node.textContent = text;
  };
  const hide = () => {
    if (state.disposed) return;
    if (tooltip && state.tooltipVisible) tooltip.hidden = true;
    if (cursor && state.cursorVisible) cursor.setAttribute("visibility", "hidden");
    state.tooltipVisible = false;
    state.cursorVisible = false;
    state.transform = null;
    state.cursorX = null;
  };
  const hover = (event, snapshotTransform) => {
    if (state.disposed || !event) return;
    // Geometry is read before any tooltip or cursor mutation in this turn.
    // The root renders after its own read phase.  Reuse that rectangle even
    // when a scroll/resize invalidation is pending: a hover must never force
    // layout after the root has written canvas or SVG pixels in this frame.
    const geometry = state.geometry || readGeometry();
    const point = coordinatesForGeometry(
      geometry,
      event.svg || surface,
      event.clientX,
      event.clientY,
    );
    const chartWindow = snapshotTransform?.window || panel.window;
    const start = snapshotTransform?.start ?? chartWindow?.[0];
    const end = snapshotTransform?.end ?? chartWindow?.[1];
    const left = snapshotTransform?.left ?? 65;
    const right = snapshotTransform?.right ?? 1135;
    if (!Number.isFinite(start) || !Number.isFinite(end) || right <= left)
      return hide();
    if (point.x < left || point.x > right) return hide();
    const time = start + ((point.x - left) / (right - left)) * (end - start);
    const parameters =
      panel.shown?.session?.configuration?.parameters ||
      panel.state?.configuration?.parameters ||
      {};
    const ttl = Number(parameters.sensor_timeout_seconds) * 1000;
    const nodes = tooltipNodes();
    const timeZone = localTimeZone();
    const visible = new Set();
    for (const position of panel.positions || ["upper"])
      for (const quantity of ["temperature", "humidity"]) {
        const nearest = panel.nearestMeasurement?.(position, quantity, time);
        if (nearest?.value == null || (ttl && Math.abs(nearest.time - time) > ttl))
          continue;
        const key = `${position}:${quantity}`;
        const row = nodes.series.get(key);
        if (!row) continue;
        setText(
          row.label,
          `${quantity === "temperature" ? "Temperatur" : "Luftfeuchte"}${panel.historyDetail ? ` ${position === "upper" ? "oben" : "unten"}` : ""}`,
        );
        setText(row.value, rowText(nearest, quantity, timeZone));
        if (row.row.hidden) row.row.hidden = false;
        visible.add(key);
      }
    for (const [key, row] of nodes.series)
      if (!visible.has(key) && !row.row.hidden) row.row.hidden = true;
    setText(nodes.heading, dateLabel(time, timeZone));
    if (!state.tooltipVisible) tooltip.hidden = false;
    state.tooltipVisible = true;
    const x = Math.max(
      0,
      Math.min(
        geometry.rects.wrap.width - 290,
        event.clientX - geometry.rects.wrap.left + 12,
      ),
    );
    const y = Math.max(0, event.clientY - geometry.rects.wrap.top - 90);
    const transform = `translate3d(${x}px, ${y}px, 0)`;
    if (tooltip.style.transform !== transform) tooltip.style.transform = transform;
    state.transform = transform;
    if (cursor && state.cursorX !== point.x) {
      cursor.setAttribute("x1", String(point.x));
      cursor.setAttribute("x2", String(point.x));
      state.cursorX = point.x;
    }
    if (cursor && !state.cursorVisible) cursor.setAttribute("visibility", "visible");
    state.cursorVisible = true;
    return { point, time, geometry };
  };
  const listenForScroll = (target) => {
    if (!target || scrollTargets.has(target)) return;
    scrollTargets.add(target);
    addListener(target, "scroll", () => invalidateGeometry("scroll"), true);
  };
  const listenComposedAncestors = (node) => {
    const seen = new Set();
    let current = node;
    while (current && !seen.has(current)) {
      seen.add(current);
      listenForScroll(current);
      if (current.assignedSlot) current = current.assignedSlot;
      else if (current.parentNode) current = current.parentNode;
      else if (current.host) current = current.host;
      else {
        const root = current.getRootNode?.();
        current = root && root !== current ? root : null;
      }
    }
  };

  listenComposedAncestors(surface);
  listenComposedAncestors(wrap);
  listenComposedAncestors(overview);
  addListener(view, "scroll", () => invalidateGeometry("scroll"), true);
  addListener(view, "resize", () => invalidateGeometry("resize"));
  addListener(view.visualViewport, "scroll", () =>
    invalidateGeometry("viewport-scroll"),
  );
  addListener(view.visualViewport, "resize", () =>
    invalidateGeometry("viewport-resize"),
  );
  const Resize = view.ResizeObserver || globalThis.ResizeObserver;
  if (Resize) {
    state.resizeObserver = new Resize(() => invalidateGeometry("resize"));
    for (const node of [surface, wrap, overview])
      if (node) state.resizeObserver.observe(node);
  }
  armPixelRatioListener();
  // Establish both persistent tooltip nodes and the initial rectangle before
  // chart-render writes occur.  Later hover turns reuse this cache.
  readGeometry();
  tooltipNodes();

  return {
    readGeometry,
    coordinates,
    hover,
    hide,
    invalidateGeometry,
    get geometry() {
      return readGeometry();
    },
    dispose() {
      if (state.disposed) return;
      state.disposed = true;
      for (const [target, type, callback, options] of listeners)
        target.removeEventListener?.(type, callback, options);
      listeners.length = 0;
      state.resizeObserver?.disconnect();
      if (state.media && state.mediaListener) {
        state.media.removeEventListener?.("change", state.mediaListener);
        state.media.removeListener?.(state.mediaListener);
      }
      state.media = null;
      state.mediaListener = null;
    },
  };
}

// One owner for the lifetime of the selected entry/session. Canvas paths,
// interaction nodes and geometry survive status, archive and phase updates.
class HistoryChart {
  constructor(panel, identity) {
    this.panel = panel;
    this.identity = identity;
    this.prepared = new Map();
    this.preparedOverview = new Map();
    this.domain = panel.historyDomain();
    panel.$("#plots").innerHTML = panel.historyMarkup(panel.shown.session);
    const overview = panel.$("#history-overview");
    overview.innerHTML =
      '<canvas aria-hidden="true"></canvas><svg viewBox="0 0 1200 46" preserveAspectRatio="none" role="slider" tabindex="0" aria-label="Zeitausschnitt der Saunasitzung"><rect class="overview-window" data-history-window x="20" y="5" height="34" rx="4"/><rect class="overview-handle" data-history-handle="start" x="16" y="2" width="8" height="40" rx="3"/><rect class="overview-handle" data-history-handle="end" x="1176" y="2" width="8" height="40" rx="3"/></svg>';
    this.surface = panel.$("svg.session-chart");
    this.curves = new HistoryCurves(
      panel.$("canvas.history-curves"),
      overview.querySelector("canvas"),
      { styles: panel.historyCurveStyles() },
    );
    this.interaction = new HistoryInteraction(
      panel,
      this.surface,
      panel.$(".history-stack"),
      panel.$("#tooltip"),
      panel.$("#cursor"),
      overview.querySelector("svg"),
    );
  }
  render(reasons, session, gangs) {
    // All client geometry is read before any tooltip, layer or canvas writes.
    const geometry = this.interaction.readGeometry();
    if (
      [...reasons].some((reason) => ["initial", "archive", "viewport"].includes(reason))
    )
      this.domain = this.panel.historyDomain();
    const panel = this.panel,
      model = panel.historyModel(this, session);
    this.model = model;
    const chromeKey = `${panel.historyTitle(session)}:${panel.historyDetail}`;
    const chromeChanged = this.chromeKey !== chromeKey;
    this.chromeKey = chromeKey;
    const title = panel.historyTitle(session),
      text = (selector, value) => {
        const node = panel.$(selector);
        if (node.textContent !== value) node.textContent = value;
      };
    text("#history-chart-title", title);
    text(
      '[data-action="history-detail"]',
      panel.historyDetail ? "Messhöhen ausblenden" : "Messhöhen vergleichen",
    );
    panel
      .$('[data-action="history-detail"]')
      .setAttribute("aria-pressed", String(panel.historyDetail));
    panel.$("[data-history-positions]").hidden = !panel.historyDetail;
    for (const position of ["upper", "lower"])
      panel
        .$(`[data-action="position-${position}"]`)
        .setAttribute("aria-pressed", String(panel.positions.has(position)));
    text(
      ".plot-note",
      `${panel.historyDetail ? "Durchgezogen: oben · gestrichelt: unten · " : ""}vorläufiger Gang · schmaler Streifen: gezählte Heizzeit`,
    );
    this.surface.setAttribute(
      "aria-label",
      `Sitzungsverlauf: Temperatur und Luftfeuchte${panel.historyDetail ? " beider Messhöhen" : ""}`,
    );
    const axisKey = `${model.start}:${model.end}:${model.low}:${model.high}:${model.humidityHigh}`;
    if (axisKey !== this.axisKey) {
      panel.$("[data-history-axes]").innerHTML = panel.historyAxes(model);
      this.axisKey = axisKey;
    }
    const phaseKey = `${model.start}:${model.end}:${panel.historyTimelineRevision}:${panel.historyRecords("phase").length}:${session.ended_at || panel.state.now}:${JSON.stringify(panel.shown.phase_projection || null)}`;
    if (phaseKey !== this.phaseKey) {
      panel.$("[data-history-annotations]").innerHTML = panel.historyAnnotations(
        model,
        session,
        gangs,
      );
      this.phaseKey = phaseKey;
    }
    // Revealing the height controls or changing a wrapped heading can move
    // the plot without resizing it. Read the new position in the next frame.
    if (chromeChanged) this.interaction.invalidateGeometry("layout");
    this.curves.update(model, {
      width: geometry.canvas.cssWidth,
      height: geometry.canvas.cssHeight,
      dpr: geometry.dpr,
      overview: {
        width: geometry.overviewCanvas.cssWidth,
        height: geometry.overviewCanvas.cssHeight,
        dpr: geometry.dpr,
      },
    });
    panel.syncHistoryOverview();
    if (
      !chromeChanged &&
      panel.lastHistoryPointer &&
      !panel.chartPointers.size &&
      !panel.webkitHistoryGesture &&
      !panel.historyGesture
    )
      this.interaction.hover({ ...panel.lastHistoryPointer, svg: this.surface }, model);
  }
  destroy() {
    this.interaction.dispose();
    this.curves.dispose?.();
    this.prepared.clear();
    this.preparedOverview.clear();
  }
}

class SaunaPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.view = "overview";
    this.navigation = { main: "overview", detail: "detail" };
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
  }
  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this.timer) this.start();
  }
  get hass() {
    return this._hass;
  }
  connectedCallback() {
    if (!this.$("main")) this.shell();
    if (this._hass) this.start();
  }
  disconnectedCallback() {
    clearInterval(this.timer);
    clearTimeout(this.programSavedTimer);
    this.programSavedTimer = null;
    this.programRequest = null;
    this.appearanceRequest = null;
    this.appearanceStale = true;
    this.applyAppearance();
    this.programSaveState = null;
    this.programSavePending = null;
    this.cancelProgramDrag();
    clearTimeout(this.historyWheelTimer);
    this.timer = null;
    this.generation++;
    this.historyLoad = null;
    this.cancelHistoryFrame();
    this.historyChart?.destroy();
    this.historyChart = null;
    this.chartPointers.clear();
    this.historyGesture =
      this.webkitHistoryGesture =
      this.pendingHover =
      this.lastHistoryPointer =
        null;
    this.historyInputMode = null;
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
  appearanceCatalog() {
    return this.state?.appearance_catalog || { colors: [], scales: {} };
  }
  savedAppearance() {
    return (
      this.state?.appearance ||
      this.state?.configuration?.appearance || { colors: {}, scales: {} }
    );
  }
  appearanceValue() {
    return this.appearanceDraft || this.savedAppearance();
  }
  appearanceColor(role) {
    const definition = this.appearanceCatalog().colors?.find(
      (item) => item.id === role,
    );
    return (
      appearanceHex(this.appearanceValue().colors?.[role]) ||
      appearanceHex(definition?.default)
    );
  }
  appearancePickerColor(role) {
    const configured = this.appearanceColor(role);
    if (configured) return configured;
    const themeRole = {
      page_background: "--primary-background-color",
      card_background: "--card-background-color",
      text: "--primary-text-color",
      muted_text: "--secondary-text-color",
      border: "--divider-color",
      ui_command: "--card-background-color",
      ui_pending: "--secondary-text-color",
      ui_success: "--primary-text-color",
      focus: "--primary-color",
    }[role];
    const theme =
      typeof getComputedStyle === "function" ? getComputedStyle(this) : null;
    return (
      appearanceThemeHex(
        theme?.getPropertyValue(themeRole || "--primary-text-color"),
      ) ||
      this.appearanceColor("ui_accent") ||
      "#FFFFFF"
    );
  }
  appearanceScale(quantity) {
    const defaultScale = this.appearanceCatalog().scales?.[quantity]?.default;
    const selected = this.appearanceValue().scales?.[quantity];
    const minimum = Number(selected?.minimum ?? defaultScale?.minimum);
    const maximum = Number(selected?.maximum ?? defaultScale?.maximum);
    const valid =
      Number.isFinite(minimum) &&
      Number.isFinite(maximum) &&
      minimum < maximum &&
      Number.isFinite(maximum - minimum) &&
      (quantity !== "humidity" || (minimum >= 0 && maximum <= 100));
    return valid ? { minimum, maximum } : defaultScale ? { ...defaultScale } : null;
  }
  historyCurveStyles() {
    const temperature = this.appearanceColor("series_temperature"),
      humidity = this.appearanceColor("series_humidity");
    return {
      "upper:temperature": { stroke: temperature },
      "lower:temperature": { stroke: temperature },
      "upper:humidity": { stroke: humidity },
      "lower:humidity": { stroke: humidity },
      overview: {
        stroke: this.appearanceColor("series_overview"),
        track: this.appearanceColor("chart_minimap_track"),
      },
    };
  }
  applyAppearance() {
    const style = this.style;
    if (!style?.setProperty) return;
    for (const definition of this.appearanceCatalog().colors || []) {
      const value = this.appearanceColor(definition.id);
      const key = `--sauna-color-${definition.id.replaceAll("_", "-")}`;
      if (value) style.setProperty(key, value);
      else style.removeProperty?.(key);
    }
    const theme =
      typeof getComputedStyle === "function" ? getComputedStyle(this) : null;
    const surfaces = {
      page:
        this.appearanceColor("page_background") ||
        appearanceThemeHex(theme?.getPropertyValue("--primary-background-color")),
      card:
        this.appearanceColor("card_background") ||
        appearanceThemeHex(theme?.getPropertyValue("--card-background-color")),
    };
    const readable = (proposed, surface, ratio) => {
      if (!proposed || !surface || appearanceContrastRatio(proposed, surface) >= ratio)
        return proposed;
      return appearanceContrastRatio("#000000", surface) >=
        appearanceContrastRatio("#FFFFFF", surface)
        ? "#000000"
        : "#FFFFFF";
    };
    for (const [role, themeKey] of [
      ["text", "--primary-text-color"],
      ["muted_text", "--secondary-text-color"],
    ]) {
      const proposed =
        this.appearanceColor(role) ||
        appearanceThemeHex(theme?.getPropertyValue(themeKey));
      for (const [surfaceName, surface] of Object.entries(surfaces)) {
        const adjusted = readable(proposed, surface, 4.5);
        if (adjusted)
          style.setProperty(
            `--sauna-${surfaceName}-${role.replaceAll("_", "-")}`,
            adjusted,
          );
      }
    }
    const chartText = readable(
      this.appearanceColor("chart_text"),
      this.appearanceColor("chart_background"),
      4.5,
    );
    if (chartText) style.setProperty("--sauna-chart-ink", chartText);
    const proposedFocus =
      this.appearanceColor("focus") || this.appearancePickerColor("focus");
    for (const [surfaceName, surface] of Object.entries(surfaces)) {
      const focus = readable(proposedFocus, surface, 3);
      if (focus) style.setProperty(`--sauna-${surfaceName}-focus`, focus);
    }
    for (const role of [
      "ui_heater_on",
      "ui_heater_off",
      "ui_heater_unknown",
      "status_info",
      "status_success",
      "status_warning",
      "status_error",
      "status_unknown",
    ]) {
      const ink = readable(this.appearanceColor(role), surfaces.card, 4.5);
      if (ink) style.setProperty(`--sauna-ink-${role.replaceAll("_", "-")}`, ink);
    }
    const accent = this.appearanceColor("ui_accent");
    if (accent) {
      style.setProperty("--sauna-accent-ink", appearanceContrast(accent));
      const readableAccent = readable(accent, surfaces.card, 4.5);
      if (readableAccent) style.setProperty("--sauna-accent-readable", readableAccent);
    }
    const command = this.appearanceColor("ui_command");
    if (command) style.setProperty("--sauna-command-ink", appearanceContrast(command));
    else style.removeProperty?.("--sauna-command-ink");
    const danger = this.appearanceColor("ui_danger");
    if (danger) {
      style.setProperty("--sauna-danger-ink", appearanceContrast(danger));
      const readableDanger = readable(danger, surfaces.card, 4.5);
      if (readableDanger) style.setProperty("--sauna-danger-text", readableDanger);
    }
    const warning = this.appearanceColor("status_warning");
    if (warning)
      style.setProperty("--sauna-status-warning-ink", appearanceContrast(warning));
    const phase =
      this.state?.operation_enabled &&
      this.state?.configuration?.control_mode !== "manual" &&
      !this.appearanceStale
        ? {
            aufheizen: "phase_warmup",
            bereit: "phase_ready",
            saunagang: "phase_session",
            nachlauf: "phase_cooling",
          }[this.state.phase]
        : null;
    const tint = phase && this.appearanceColor(phase);
    if (tint) style.setProperty("--sauna-phase-tint", appearanceAlpha(tint, 0.08));
    else style.removeProperty?.("--sauna-phase-tint");
    if (this.historyChart) this.historyChart.curves.styles = this.historyCurveStyles();
  }
  appearanceSettingsMarkup() {
    const catalog = this.appearanceCatalog();
    const groups = [
      "Bedienung",
      "Grunddarstellung",
      "Zustände und Phasen",
      "Messungen und Ereignisse",
    ];
    const colors = catalog.colors || [];
    const rows = (group) =>
      colors
        .filter((item) => item.group === group)
        .map((item) => {
          const value = this.appearancePickerColor(item.id);
          const shown =
            this.appearanceRaw?.[item.id] ??
            this.appearanceValue().colors?.[item.id] ??
            item.default ??
            "";
          return `<div class="appearance-color-row"><label for="appearance-${esc(item.id)}">${esc(item.label)}</label><input type="color" data-appearance-picker="${esc(item.id)}" aria-label="${esc(item.label)} wählen" value="${esc(value || "#FFFFFF")}"><input id="appearance-${esc(item.id)}" data-appearance-color="${esc(item.id)}" aria-label="${esc(item.label)} als Hexwert" inputmode="text" maxlength="7" placeholder="HA-Standard" value="${esc(shown)}"><button type="button" data-action="appearance-role-default:${esc(item.id)}" aria-label="${esc(item.label)} auf Standard setzen">Standard</button></div>`;
        })
        .join("");
    const scaleFields = ["temperature", "humidity"]
      .map((name) => {
        const label = name === "temperature" ? "Temperatur (°C)" : "Luftfeuchte (%)";
        const scale =
          this.appearanceScale(name) || catalog.scales?.[name]?.default || {};
        return `<fieldset class="appearance-scale"><legend>${label}</legend>${["minimum", "maximum"].map((end) => `<label>${end === "minimum" ? "Minimum" : "Maximum"}<input type="number" step="any" data-appearance-scale="${name}.${end}" value="${esc(this.appearanceRaw?.[`${name}.${end}`] ?? scale[end] ?? "")}"></label>`).join("")}</fieldset>`;
      })
      .join("");
    return `<div class="card appearance-settings" id="appearance-settings"><h2>Darstellung</h2><p class="muted">Farben und Anzeigeskalen dieser Sauna. Änderungen werden sofort als Vorschau angezeigt und gelten nach dem Speichern für alle Benutzer.</p>${groups.map((group) => `<details class="settings-group" ${group === "Bedienung" ? "open" : ""}><summary>${group}</summary><div class="appearance-colors">${rows(group)}${group === "Messungen und Ereignisse" ? `<h3>Anzeigeskalen</h3><div class="appearance-scales">${scaleFields}</div>` : ""}</div></details>`).join("")}<div class="row"><button type="button" class="confirm" data-action="appearance-save">Darstellung speichern</button><button type="button" data-action="appearance-discard">Änderungen verwerfen</button><button type="button" data-action="appearance-default">Standarddarstellung wiederherstellen</button></div><p id="appearance-status" role="status" class="muted"></p></div>`;
  }
  updateAppearanceField(input) {
    if (!this.state?.permissions?.admin || this.appearanceRequest) return;
    if (!this.appearanceDraft) {
      const saved = this.savedAppearance();
      this.appearanceDraft = {
        colors: { ...(saved.colors || {}) },
        scales: Object.fromEntries(
          Object.entries(this.appearanceCatalog().scales || {}).map(([name, spec]) => [
            name,
            { ...spec.default, ...(saved.scales?.[name] || {}) },
          ]),
        ),
      };
    }
    this.appearanceRaw ||= {};
    const role = input.dataset.appearanceColor || input.dataset.appearancePicker;
    if (role) {
      this.appearanceRaw[role] = input.value;
      const value = appearanceHex(input.value);
      if (value) this.appearanceDraft.colors[role] = value;
      else if (!input.value.trim()) delete this.appearanceDraft.colors[role];
      const peer = this.$(
        `[data-appearance-${input.dataset.appearancePicker ? "color" : "picker"}="${role}"]`,
      );
      if (peer && (value || !input.value.trim()))
        peer.value = value || this.appearancePickerColor(role);
    } else if (input.dataset.appearanceScale) {
      const [name, end] = input.dataset.appearanceScale.split(".");
      this.appearanceRaw[input.dataset.appearanceScale] = input.value;
      const value = Number(input.value);
      if (input.value.trim() && Number.isFinite(value))
        this.appearanceDraft.scales[name][end] = value;
    }
    this.appearanceStatus = "Vorschau – noch nicht gespeichert";
    this.appearanceStatusKind = "pending";
    this.applyAppearance();
    if (role && this.historyChart) this.scheduleHistoryRender("appearance");
    this.drawCurrent();
    this.drawAppearanceStatus();
  }
  drawAppearanceStatus() {
    const node = this.$("#appearance-status");
    if (node) {
      node.textContent = this.appearanceStatus || "";
      node.dataset.kind = this.appearanceStatusKind || "info";
    }
    this.$("#appearance-settings")
      ?.querySelectorAll?.("input,button")
      .forEach((element) => {
        element.disabled = !!this.appearanceRequest;
      });
  }
  syncAppearanceEditor() {
    if (this.appearanceDraft) return;
    for (const definition of this.appearanceCatalog().colors || []) {
      const input = this.$(`[data-appearance-color="${definition.id}"]`);
      const picker = this.$(`[data-appearance-picker="${definition.id}"]`);
      if (input && input !== this.shadowRoot.activeElement)
        input.value =
          this.savedAppearance().colors?.[definition.id] || definition.default || "";
      if (picker && picker !== this.shadowRoot.activeElement)
        picker.value = this.appearancePickerColor(definition.id);
    }
    for (const name of ["temperature", "humidity"])
      for (const end of ["minimum", "maximum"]) {
        const input = this.$(`[data-appearance-scale="${name}.${end}"]`);
        if (input && input !== this.shadowRoot.activeElement)
          input.value = this.appearanceScale(name)?.[end] ?? "";
      }
  }
  appearancePayload() {
    if (
      Object.entries(this.appearanceRaw || {}).some(
        ([key, value]) => !key.includes(".") && value.trim() && !appearanceHex(value),
      )
    )
      throw Error("Farben müssen im Format #RRGGBB angegeben werden");
    if (
      Object.entries(this.appearanceRaw || {}).some(
        ([key, value]) =>
          key.includes(".") && (!value.trim() || !Number.isFinite(Number(value))),
      )
    )
      throw Error("Anzeigeskalen benötigen endliche Zahlen");
    const payload = this.appearanceDraft;
    if (!payload) return null;
    for (const [name, spec] of Object.entries(this.appearanceCatalog().scales || {})) {
      const bounds = payload.scales[name];
      if (
        !Number.isFinite(bounds?.minimum) ||
        !Number.isFinite(bounds?.maximum) ||
        bounds.minimum >= bounds.maximum ||
        !Number.isFinite(bounds.maximum - bounds.minimum) ||
        (name === "humidity" &&
          (bounds.minimum < (spec.minimum ?? 0) ||
            bounds.maximum > (spec.maximum ?? 100)))
      )
        throw Error(
          `${name === "temperature" ? "Temperatur" : "Luftfeuchte"}: Minimum muss kleiner als Maximum sein`,
        );
    }
    return payload;
  }
  async saveAppearance() {
    if (!this.state?.permissions?.admin || this.appearanceRequest) return;
    let payload;
    try {
      payload = this.appearancePayload();
    } catch (error) {
      this.appearanceStatus = errorText(error);
      this.appearanceStatusKind = "error";
      this.drawAppearanceStatus();
      return;
    }
    if (!payload) return;
    const entry = this.entry,
      generation = this.generation;
    this.appearanceRevision = (this.appearanceRevision || 0) + 1;
    const request = this.api(`/${entry}/appearance`, "POST", payload);
    this.appearanceRequest = request;
    this.appearanceStatus = "Darstellung wird gespeichert …";
    this.appearanceStatusKind = "pending";
    this.drawAppearanceStatus();
    try {
      const result = await request;
      if (
        this.appearanceRequest !== request ||
        entry !== this.entry ||
        generation !== this.generation
      )
        return;
      this.state.appearance = result.appearance;
      this.state.configuration.appearance = result.appearance;
      this.appearanceRevision++;
      this.appearanceDraft = null;
      this.appearanceRaw = null;
      this.appearanceStatus = "Darstellung gespeichert";
      this.appearanceStatusKind = "success";
      this.applyAppearance();
      this.drawSettings();
      this.syncAppearanceEditor();
      this.scheduleHistoryRender("appearance");
    } catch (error) {
      if (
        this.appearanceRequest !== request ||
        entry !== this.entry ||
        generation !== this.generation
      )
        return;
      this.appearanceStatus = errorText(error);
      this.appearanceStatusKind = "error";
      this.drawAppearanceStatus();
    } finally {
      if (this.appearanceRequest === request) {
        this.appearanceRequest = null;
        this.drawAppearanceStatus();
      }
    }
  }
  resetAppearanceDraft(defaults = false) {
    if (!this.state?.permissions?.admin || this.appearanceRequest) return;
    this.appearanceDraft = defaults
      ? {
          colors: {},
          scales: Object.fromEntries(
            Object.entries(this.appearanceCatalog().scales || {}).map(
              ([name, spec]) => [name, { ...spec.default }],
            ),
          ),
        }
      : null;
    this.appearanceRaw = null;
    this.appearanceStatus = defaults
      ? "Standarddarstellung als Vorschau – noch nicht gespeichert"
      : "Änderungen verworfen";
    this.appearanceStatusKind = defaults ? "pending" : "info";
    this.applyAppearance();
    const node = this.$("#appearance-settings");
    if (node) {
      const restoreAction = this.shadowRoot.activeElement?.dataset?.action;
      const open = [...node.querySelectorAll("details")].map((detail) => detail.open);
      const replacement = this.appearanceSettingsMarkup();
      node.outerHTML = replacement;
      [...this.$("#appearance-settings").querySelectorAll("details")].forEach(
        (detail, index) => {
          detail.open = open[index];
        },
      );
      if (restoreAction) this.$(`[data-action="${restoreAction}"]`)?.focus?.();
    }
    this.drawCurrent();
    this.drawAppearanceStatus();
    this.scheduleHistoryRender("appearance");
  }
  shell() {
    this.shadowRoot.innerHTML = `<style>
      :host {
        display: block;
        height: 100%;
        overflow: auto;
        color: var(--sauna-page-text, var(--sauna-color-text, var(--primary-text-color)));
        background: var(--sauna-color-page-background, var(--primary-background-color));
        font:
          15px/1.5 system-ui,
          sans-serif;
        --accent: var(--sauna-color-ui-accent);
        --accent-ink: var(--sauna-accent-ink);
        --confirm: var(--sauna-color-ui-command, var(--sauna-color-card-background, var(--card-background-color)));
        --danger: var(--sauna-color-ui-danger);
        --danger-text: var(--sauna-danger-text, var(--danger));
        --warm: var(--sauna-color-phase-warmup);
        --sauna-focus-current: var(--sauna-page-focus, var(--accent));
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
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
        align-items: center;
        gap: 18px;
        margin-bottom: 24px;
      }
      .header-brand {
        display: flex;
        align-items: center;
        gap: 14px;
      }
      .header-context {
        justify-self: end;
        min-width: 0;
      }
      .header-context select {
        max-width: 100%;
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
        color: var(--sauna-page-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)));
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
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: 8px;
        padding: 9px 13px;
        background: var(--sauna-color-card-background, var(--card-background-color));
        color: inherit;
      }
      button {
        cursor: pointer;
      }
      button:hover:not(:disabled) {
        filter: brightness(0.94);
      }
      button:focus-visible,
      select:focus-visible,
      input:focus-visible {
        outline: 2px solid var(--sauna-focus-current);
        outline-offset: 3px;
      }
      button:disabled {
        opacity: 0.5;
        cursor: default;
      }
      button.primary,
      button.confirm {
        background: var(--confirm);
        color: var(--sauna-command-ink, inherit);
        border-color: var(--sauna-color-ui-command, var(--sauna-color-border, var(--divider-color)));
      }
      button.stop {
        background: var(--danger);
        color: var(--sauna-danger-ink);
        border-color: var(--danger);
      }
      button[aria-selected="true"] {
        background: var(--accent);
        color: var(--accent-ink);
        border-color: var(--accent);
        font-weight: 700;
      }
      a {
        color: color-mix(in srgb, var(--accent) 60%, var(--sauna-color-text, var(--primary-text-color)));
      }
      .tabs {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin: 8px 0 12px;
      }
      .card {
        background: var(--sauna-color-card-background, var(--card-background-color));
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: 14px;
        padding: 22px;
        margin: 16px 0;
        color: var(--sauna-card-text, inherit);
        --sauna-focus-current: var(--sauna-card-focus, var(--accent));
      }
      .card small,
      .card .muted { color: var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color))); }
      .card strong,
      .card h2,
      .card h3 { color: inherit; }
      .hero {
        border-left: 4px solid var(--accent);
      }
      .control-main {
        background: linear-gradient(var(--sauna-phase-tint, transparent), var(--sauna-phase-tint, transparent)), var(--sauna-color-card-background, var(--card-background-color));
      }
      .oven-feedback { margin: 4px 0 10px; }
      [data-action="program-toggle"] { display: inline-flex; flex-wrap: wrap; align-items: baseline; gap: 2px 10px; max-width: 100%; white-space: normal; text-align: left; }
      .program-active-label { min-width: 0; max-width: 100%; overflow-wrap: anywhere; font-size: 12px; }
      .program-disclosure { margin-top: 12px; }
      .scale-hint { fill: var(--sauna-color-status-warning); color: var(--sauna-ink-status-warning, var(--sauna-card-text, inherit)); font-size: 11px; margin: 2px 0 0; }
      .appearance-colors { display: grid; gap: 9px; }
      .appearance-color-row { display: grid; grid-template-columns: minmax(130px, 1fr) 48px minmax(96px, 118px) auto; gap: 8px; align-items: center; }
      .appearance-color-row input[type="color"] { padding: 2px; width: 48px; height: 40px; }
      .appearance-color-row input:not([type="color"]) { min-width: 0; width: 100%; }
      .appearance-color-row button { padding: 7px 9px; }
      .appearance-scales { display: flex; flex-wrap: wrap; gap: 16px; }
      .appearance-scale { display: flex; gap: 9px; align-items: end; border-top: 1px solid var(--sauna-color-border, var(--divider-color)); padding-top: 12px; }
      .appearance-scale legend { font-weight: 600; }
      .appearance-scale label { display: grid; gap: 4px; font-size: 13px; }
      .appearance-scale input { width: 110px; }
      #appearance-status[data-kind="success"] { color: var(--sauna-ink-status-success, var(--sauna-color-status-success)); }
      #appearance-status[data-kind="error"] { color: var(--sauna-ink-status-error, var(--sauna-color-status-error)); }
      #appearance-status[data-kind="pending"] { border-left: 3px solid var(--sauna-color-status-info); padding-left: 8px; }
      @media (max-width: 540px) {
        .appearance-color-row { grid-template-columns: 48px minmax(0, 1fr) auto; }
        .appearance-color-row label { grid-column: 1 / -1; }
        .appearance-color-row input[type="color"] { grid-column: 1; }
        .appearance-color-row input:not([type="color"]) { grid-column: 2; }
        .appearance-color-row button { grid-column: 3; }
        .appearance-scale { flex-wrap: wrap; }
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
        background: color-mix(in srgb, var(--sauna-color-status-info) 12%, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-card-text, inherit);
        font-size: 13px;
      }
      .notice {
        background: color-mix(in srgb, var(--sauna-color-status-warning) 12%, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-card-text, inherit);
        border-left: 4px solid var(--sauna-color-status-warning);
        padding: 12px 16px;
        border-radius: 8px;
        margin: 10px 0;
      }
      .error {
        background: color-mix(in srgb, var(--sauna-color-status-error) 12%, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-card-text, inherit);
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
        background: var(--sauna-color-series-upper);
      }
      .lower {
        background: var(--sauna-color-series-lower);
      }
      .chart {
        width: 100%;
        height: auto;
        display: block;
        touch-action: pan-y;
      }
      .chart text {
        fill: var(--sauna-chart-ink, var(--sauna-color-chart-text));
        font: 12px system-ui;
      }
      .chart .gridline {
        stroke: var(--sauna-color-border, var(--divider-color));
        stroke-width: 1;
      }
      .chart .gang {
        fill: color-mix(in srgb, var(--sauna-color-phase-session) 20%, transparent);
        stroke: var(--sauna-color-phase-session);
      }
      .chart .provisional {
        fill: color-mix(in srgb, var(--sauna-color-status-warning) 13%, transparent);
        stroke: var(--sauna-color-status-warning);
        stroke-dasharray: 5 4;
      }
      .chart .heat {
        fill: color-mix(in srgb, var(--sauna-color-phase-warmup) 20%, transparent);
      }
      .chart .event {
        stroke: var(--sauna-color-chart-event);
        stroke-dasharray: 3 5;
      }
      .chart .infusion {
        stroke: var(--sauna-color-event-infusion);
      }
      .chart path {
        fill: none;
        stroke-width: 2;
      }
      .chart path.upper {
        stroke: var(--sauna-color-series-upper);
      }
      .chart path.lower {
        stroke: var(--sauna-color-series-lower);
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
        border-bottom: 1px solid var(--sauna-color-border, var(--divider-color));
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
        color: var(--sauna-page-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)));
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
        --accent: var(--sauna-color-ui-accent);
      }
      main {
        max-width: none;
      }
      header {
        margin-bottom: 12px;
      }
      .plot-panel {
        padding: 20px 12px 30px;
        background: var(--sauna-color-chart-background);
        color: var(--sauna-chart-ink, var(--sauna-color-chart-text));
        border-radius: 8px;
      }
      .plot-title {
        text-align: center;
        color: var(--sauna-chart-ink, var(--sauna-color-chart-text));
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
        color: var(--sauna-chart-ink, var(--sauna-color-chart-text));
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
        fill: var(--sauna-chart-ink, var(--sauna-color-chart-text));
      }
      .detector-chart {
        height: 220px;
      }
      .main-tabs {
        justify-content: center;
        margin: 0;
        padding: 4px;
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: 11px;
        max-width: 100%;
        overflow-x: auto;
        flex-wrap: nowrap;
        scrollbar-width: thin;
      }
      .main-tabs button {
        padding: 8px 12px;
        font-size: 13px;
        border-color: transparent;
        flex: 0 0 auto;
      }
      .detail-tabs {
        font-size: 13px;
      }
      .chart .gridline {
        stroke: color-mix(in srgb, var(--sauna-color-chart-grid) 10%, transparent);
      }
      .chart .gang {
        fill: color-mix(in srgb, var(--sauna-color-phase-session) 46%, transparent);
        stroke: none;
      }
      .chart .provisional {
        stroke: var(--sauna-color-phase-session);
        stroke-dasharray: 5 4;
      }
      .chart .heat {
        fill: color-mix(in srgb, var(--sauna-color-phase-warmup) 28%, transparent);
      }
      .chart .ready {
        fill: color-mix(in srgb, var(--sauna-color-phase-ready) 30%, transparent);
      }
      .chart .vent {
        fill: color-mix(in srgb, var(--sauna-color-activity-ventilation) 30%, transparent);
      }
      .chart .cool {
        fill: color-mix(in srgb, var(--sauna-color-phase-forced-cooling) 30%, transparent);
      }
      .chart .after {
        fill: color-mix(in srgb, var(--sauna-color-phase-cooling) 18%, transparent);
      }
      .chart .phase-other {
        fill: color-mix(in srgb, var(--sauna-color-phase-idle) 22%, transparent);
      }
      .chart .door {
        fill: color-mix(in srgb, var(--sauna-color-event-door) 75%, transparent);
      }
      .chart .infusion {
        stroke: var(--sauna-color-event-infusion);
        stroke-width: 1;
      }
      .chart path {
        stroke-width: 3.5;
      }
      .chart path.temperature {
        stroke: var(--sauna-color-series-temperature);
      }
      .chart path.humidity {
        stroke: var(--sauna-color-series-humidity);
      }
      .chart path.lower {
        stroke-dasharray: 8 5;
        opacity: 0.75;
      }
      .chart .axis-temperature {
        fill: var(--sauna-color-series-temperature);
      }
      .chart .axis-humidity {
        fill: var(--sauna-color-series-humidity);
      }
      .plot-panel .plot-note {
        text-align: center;
        color: var(--sauna-chart-ink, var(--sauna-color-chart-text));
      }
      .history-stack { position: relative; }
      .history-stack > .history-background,
      .history-stack > .history-curves { position: absolute; inset: 0; pointer-events: none; }
      .history-stack > .session-chart { position: relative; display: block; }
      .history-overview { position: relative; }
      .history-overview canvas { position: absolute; inset: 0; width: 100%; height: 32px; pointer-events: none; }
      .history-overview svg { position: relative; }
      #tooltip { left: 0; top: 0; width: 290px; box-sizing: border-box;
        position: absolute;
        pointer-events: none;
        background: var(--sauna-color-card-background, var(--card-background-color));
        color: var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)));
        padding: 10px 13px;
        font: 13px/1.5 system-ui;
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        z-index: 2;
        box-shadow: 0 3px 10px color-mix(in srgb, var(--sauna-color-text, var(--primary-text-color)) 20%, transparent);
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
      .operation-control > button + button {
        margin-top: 8px;
      }
      .program-main {
        grid-column: 1/-1;
        min-height: 62px;
        font-weight: 700;
      }
      .program-action-layout .operation-control .tile {
        max-width: 280px;
      }
      .session-status {
        display: block;
        margin-top: 7px;
        text-align: center;
      }
      .dial {
        max-width: 240px;
        display: block;
        margin: auto;
      }
      .dial text {
        fill: var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)));
      }
      .dial .reading {
        font-size: 33px;
      }
      .dial .caption {
        font-size: 13px;
        fill: var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)));
      }
      .dial .target-reading {
        font-size: 16px;
        font-weight: 650;
        fill: var(--sauna-accent-readable, var(--accent));
      }
      .dial .tick {
        font-size: 14px;
        fill: var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)));
      }
      .dashboard .card {
        margin-top: 0;
      }
      .environment {
        display: grid;
        gap: 16px;
      }
      .environment .card {
        margin: 0;
      }
      .environment-status {
        display: grid;
        grid-template-columns: 1fr 1fr;
        padding: 18px 22px;
        gap: 18px;
      }
      .environment-status > div {
        display: flex;
        flex-direction: column;
        gap: 7px;
      }
      .environment-status > div + div {
        padding-left: 18px;
        border-left: 1px solid var(--sauna-color-border, var(--divider-color));
      }
      .environment-status strong {
        font-size: 16px;
      }
      .environment-status .feedback::before {
        content: "";
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: currentColor;
        margin-right: 8px;
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
        background: var(--sauna-color-status-warning);
        color: var(--sauna-status-warning-ink);
        border-color: var(--sauna-card-text, inherit);
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
        border-block: 1px solid var(--sauna-color-border, var(--divider-color));
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
        color: var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)));
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
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: 10px;
        padding: 0 16px;
      }
      .settings-group summary {
        padding: 14px 0;
        margin: 0;
        font-size: 16px;
      }
      .settings-group[open] summary {
        border-bottom: 1px solid var(--sauna-color-border, var(--divider-color));
        margin-bottom: 16px;
      }
      .settings-group .forms {
        padding-bottom: 18px;
      }
      .expert-group + .expert-group {
        border-top: 1px solid var(--sauna-color-border, var(--divider-color));
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
        color: var(--sauna-ink-ui-heater-on, var(--sauna-color-ui-heater-on));
      }
      .feedback.off {
        color: var(--sauna-ink-ui-heater-off, var(--sauna-color-ui-heater-off));
      }
      .feedback.unknown {
        color: var(--sauna-ink-ui-heater-unknown, var(--sauna-color-ui-heater-unknown));
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
        stroke: var(--sauna-color-card-background, var(--card-background-color));
        stroke-width: 4;
        cursor: pointer;
      }
      .target-temperature-handle[data-inert-target] { cursor: default; }
      .target-temperature-track:focus {
        outline: none;
        stroke: var(--sauna-color-focus, var(--accent));
        opacity: 0.8;
      }
      .manual-section + .manual-section {
        border-top: 1px solid var(--sauna-color-border, var(--divider-color));
        margin-top: 16px;
        padding-top: 16px;
      }
      .manual-status {
        display: inline-block;
        border-radius: 20px;
        padding: 3px 10px;
        font-size: 13px;
        font-weight: 650;
        background: color-mix(in srgb, var(--sauna-color-status-unknown) 12%, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-card-text, inherit);
      }
      .manual-status.on {
        background: color-mix(in srgb, var(--sauna-color-ui-heater-on) 16%, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-card-text, inherit);
      }
      .manual-status.off {
        background: color-mix(in srgb, var(--sauna-color-ui-heater-off) 16%, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-card-text, inherit);
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
        border-bottom: 1px solid var(--sauna-color-border, var(--divider-color));
      }
      .event-marker {
        position: absolute;
        bottom: var(--event-marker-bottom, -6px);
        width: var(--event-marker-size);
        height: var(--event-marker-size);
        padding: 0;
        border-radius: 50%;
        border: 2px solid var(--sauna-color-event-infusion);
        background: var(--accent);
        color: var(--accent-ink);
        font-size: 10px;
        font-weight: 700;
        line-height: 13px;
        transform: translateX(-50%);
      }
      .event-marker[data-selected="true"],
      .event-row[data-selected="true"] {
        outline: 3px solid var(--accent);
        outline-offset: 2px;
        background: color-mix(in srgb, var(--sauna-color-status-warning) 12%, var(--sauna-color-card-background, var(--card-background-color)));
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
        fill: color-mix(in srgb, var(--sauna-color-chart-minimap-track) 5%, transparent);
        stroke: color-mix(in srgb, var(--sauna-color-chart-minimap-track) 14%, transparent);
      }
      .history-overview .overview-temperature {
        fill: none;
        stroke: var(--sauna-color-series-overview);
        stroke-width: 1.5;
      }
      .history-overview .overview-window {
        fill: color-mix(in srgb, var(--sauna-color-chart-minimap-track) 20%, transparent);
        stroke: var(--sauna-color-chart-minimap-track);
        stroke-width: 1;
        cursor: grab;
      }
      .history-overview .overview-handle {
        fill: var(--sauna-color-chart-minimap-track);
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
        fill: var(--sauna-color-detector-marker);
        stroke: var(--sauna-color-chart-background);
        stroke-width: 2;
      }
      .diagnostic-marker .event-marker-point {
        fill: var(--sauna-color-detector-marker);
        stroke: var(--sauna-color-chart-background);
        stroke-width: 1;
      }
      .diagnostic-marker .event-marker-link {
        stroke: var(--sauna-color-detector-marker);
        stroke-width: 1.5;
        stroke-dasharray: 2 2;
      }
      .diagnostic-marker .event-marker-label {
        fill: var(--sauna-color-chart-background);
        font: 700 11px system-ui;
        paint-order: stroke;
        stroke: var(--sauna-color-chart-text);
        stroke-width: 3px;
        stroke-linejoin: round;
      }
      .diagnostic-marker[data-selected="true"] .event-marker-dot {
        fill: color-mix(in srgb, var(--sauna-color-status-warning) 12%, var(--sauna-color-chart-background));
        stroke: var(--accent);
        stroke-width: 3;
      }
      .event-row[data-selected="true"] {
        background: color-mix(in srgb, var(--sauna-color-status-warning) 12%, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-color-text, var(--primary-text-color));
        outline: 3px solid var(--accent);
        outline-offset: -3px;
      }
      .event-row[data-selected="true"] button {
        background: color-mix(in srgb, var(--sauna-color-status-warning) 12%, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-color-text, var(--primary-text-color));
        border-color: var(--sauna-color-text, var(--primary-text-color));
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
        color: var(--sauna-card-text, inherit);
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
        color: var(--accent-ink);
        border-color: var(--accent);
        font-weight: 700;
      }
      button[aria-selected="true"]:disabled,
      button[aria-pressed="true"]:disabled {
        opacity: 1;
        cursor: default;
      }
      button[data-action^="light:"][aria-pressed="true"] small {
        color: inherit;
      }
      button[data-action^="heater:"]:disabled:not([aria-pressed="true"]),
      button[data-action^="light:"]:disabled:not([aria-pressed="true"]) {
        opacity: 0.5;
      }
      button[data-action^="program-remove:"] {
        background: transparent;
        color: var(--danger-text);
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
        margin-top: 16px;
      }
      .manual-controls {
        margin-top: 16px;
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
        border-top: 1px solid var(--sauna-color-border, var(--divider-color));
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
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 16px;
        padding-top: 14px;
        border-top: 1px solid var(--sauna-color-border, var(--divider-color));
      }
      .program-saved,
      .program-saving {
        color: var(--sauna-command-ink, inherit);
        border-color: var(--confirm);
        background: var(--confirm);
      }
      .program-saved:disabled,
      .program-saving:disabled {
        opacity: 1;
      }
      .program-draft-label {
        display: block;
        font-size: 12px;
        font-weight: 400;
      }
      .program-pending {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-top: 10px;
        padding: 8px 10px;
        background: color-mix(in srgb, currentColor 6%, transparent);
        border-radius: 8px;
        color: var(--sauna-color-text, var(--primary-text-color));
      }
      .program-kind {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        margin: 10px 0;
      }
      .program-form {
        margin: 10px 0 0 35px;
        padding: 12px;
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: 8px;
      }
      .program-row + .program-row {
        border-top: 1px solid var(--sauna-color-border, var(--divider-color));
      }
      .program-row-summary {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 9px 0;
      }
      .program-row-label {
        display: grid;
        gap: 3px;
        min-width: 0;
        flex: 1;
      }
      .program-row-label strong,
      .program-row-label small {
        overflow-wrap: anywhere;
      }
      .program-drag-handle {
        touch-action: none;
        cursor: grab;
        flex: 0 0 auto;
      }
      .program-edit-button {
        padding: 5px 8px;
        font-size: 12px;
        flex: 0 0 auto;
      }
      .program-row[data-drop-position="before"] { border-top: 3px solid var(--sauna-card-focus, var(--accent)); }
      .program-row[data-drop-position="after"] { border-bottom: 3px solid var(--sauna-card-focus, var(--accent)); }
      .settings-footer {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 8px 12px;
        margin-top: 24px;
      }
      .settings-footer p {
        flex-basis: 100%;
        margin: 0;
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
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: 8px;
        background: var(--sauna-color-card-background, var(--card-background-color));
        box-shadow: 0 4px 14px color-mix(in srgb, var(--sauna-color-text, var(--primary-text-color)) 20%, transparent);
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
        header {
          grid-template-columns: 1fr auto;
          gap: 12px;
          margin-bottom: 16px;
        }
        .main-tabs {
          grid-row: 2;
          grid-column: 1 / -1;
          justify-self: stretch;
          justify-content: flex-start;
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
      <header><div class="header-brand"><button data-action="menu" aria-label="Menü öffnen">☰</button><h1>Sauna</h1></div><nav class="tabs main-tabs" aria-label="Ansicht"><button data-action="overview" aria-selected="true">Steuerung</button><button data-action="history" aria-selected="false">Verlauf</button><button data-action="details" aria-selected="false">Details</button><button data-action="settings" aria-selected="false">Einstellungen</button></nav><div class="header-context"><select id="instance" aria-label="Sauna auswählen"></select></div></header>
      <nav class="tabs detail-tabs" aria-label="Detailansicht" hidden><button data-action="detail" aria-selected="true">Betrieb & Fristen</button><button data-action="detail-history" aria-selected="false">Detailverlauf</button><button data-action="diagnostics" aria-selected="false">Erkennungskontrolle</button></nav>
      <div id="message" role="alert"></div><section id="current" aria-live="polite"><p>Lade Saunadaten …</p></section><section id="details" hidden></section>
      <section id="history" hidden><div class="row"><h2 class="grow">Sitzungsverlauf</h2><select id="session" aria-label="Saunasitzung auswählen"><option value="live">Letzte Sitzung</option></select></div>
        <div class="row toolbar"><div class="history-zoom"><button data-action="zoom-in" aria-label="Vergrößern">＋</button><button data-action="zoom-out" aria-label="Verkleinern">−</button><button data-action="reset-zoom" aria-label="Gesamte Saunasitzung">Gesamt</button></div><div class="history-window"><div id="history-overview" class="history-overview" aria-label="Übersicht der gesamten Saunasitzung"></div><span id="range" class="muted"></span></div></div><p id="history-loading" class="muted" role="status" hidden></p><div id="plots"></div><div id="detection-plots" hidden></div><div id="gangs"></div><div id="event-list"></div>
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
        this.historyLoad = null;
        this.shown = null;
        this.invalidateHistoryIndex();
        this.settingsEntry = null;
        this.programSelectionDraft = null;
        this.programChoiceOpen = false;
        this.controlSessionKey = null;
        clearTimeout(this.programSavedTimer);
        this.programRequest = null;
        this.programSaveState = null;
        this.lastNamedProgramId = null;
        this.progressionDraft = null;
        this.freeProgramKind = null;
        this.freeProgramStepsDraft = null;
        this.programDraft = null;
        this.programEditor = null;
        this.cancelProgramDrag();
        this.programSavePending = null;
        this.appearanceDraft = null;
        this.appearanceRaw = null;
        this.appearanceRequest = null;
        this.appearanceStatus = null;
        this.appearanceStatusKind = null;
        this.appearanceRevision = (this.appearanceRevision || 0) + 1;
        this.programSavedSignature = null;
        this.programSavedCatalogSignature = null;
        this.temperatureChange = null;
        this.zoom = 1;
        this.window = null;
        this.refresh(true);
      }
      if (e.target.id === "session") {
        this.selected = e.target.value;
        this.historySelectionGeneration = (this.historySelectionGeneration || 0) + 1;
        this.historyLoad = null;
        this.shown = null;
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
      if (["button-program", "button-temperature"].includes(e.target.id))
        this.action("button-program").catch((err) => this.message(err));
    });
    this.shadowRoot.addEventListener("input", (e) => {
      if (
        e.target.matches(
          "[data-appearance-color],[data-appearance-picker],[data-appearance-scale]",
        )
      )
        this.updateAppearanceField(e.target);
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
      if (e.target.matches("[data-program-field],[data-program-step]"))
        this.updateProgramEditorField(e.target);
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
      }
      if (e.target.matches("[data-program-step-count]")) {
        if (!this.programEditor) return;
        this.programEditor.values.temperature_steps = this.resizeSteps(
          this.programEditor.values.temperature_steps || [],
          Number(e.target.value),
        );
        this.programLibraryNeedsRender = true;
        this.renderProgramLibrary();
      }
    });
    this.shadowRoot.addEventListener("keydown", (e) => {
      if (e.target.matches("[data-program-drag]")) {
        if (["ArrowUp", "ArrowDown"].includes(e.key)) {
          e.preventDefault();
          this.moveProgram(e.target.dataset.programDrag, e.key === "ArrowUp" ? -1 : 1);
        }
        if (e.key === "Escape") this.cancelProgramDrag();
      }
      if (e.key === "Escape" && this.programEditor) {
        e.preventDefault();
        this.cancelProgramEditor();
      }
      if (e.key === "Escape" && this.programInfoOpen) {
        const open = this.programInfoOpen;
        this.programInfoOpen = null;
        if (open.startsWith("catalog:")) {
          this.programLibraryNeedsRender = true;
          this.renderProgramLibrary();
        } else this.drawCurrent();
      }
      if (e.target.closest("#history-overview svg, svg.session-chart") && this.window) {
        const width = this.window[1] - this.window[0];
        if (["ArrowLeft", "ArrowRight", "Home", "+", "-", "="].includes(e.key)) {
          e.preventDefault();
          if (e.key === "Home") {
            this.zoom = 1;
            this.window = null;
          } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            const offset = width * (e.key === "ArrowLeft" ? -0.1 : 0.1);
            this.setHistoryWindow(this.window[0] + offset, this.window[1] + offset);
          } else {
            const middle = (this.window[0] + this.window[1]) / 2,
              next = width * (e.key === "-" ? 2 : 0.5);
            this.setHistoryWindow(middle - next / 2, middle + next / 2);
          }
          this.scheduleHistoryRender("viewport");
        }
      }
      if (e.target.closest("[data-target-arc]"))
        this.keyTemperatureTarget(e).catch((err) => this.message(err));
    });
    this.shadowRoot.addEventListener("pointerdown", (e) => this.beginProgramDrag(e));
    this.shadowRoot.addEventListener("pointermove", (e) => this.updateProgramDrag(e));
    this.shadowRoot.addEventListener("pointerup", (e) => this.finishProgramDrag(e));
    this.shadowRoot.addEventListener("pointercancel", () => this.cancelProgramDrag());
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
        this.historyChart?.interaction.hide();
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
    const shown =
      this.messages.action || this.messages.refresh || this.messages.history;
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
    const programRevision = this.programRevision || 0;
    const appearanceRevision = this.appearanceRevision || 0;
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
      if (
        generation !== this.generation ||
        !this.isConnected ||
        programRevision !== (this.programRevision || 0) ||
        appearanceRevision !== (this.appearanceRevision || 0)
      )
        return;
      this.state = state;
      const sessionKey = state.session?.timeline?.session_started_at || null;
      if (sessionKey !== this.controlSessionKey) {
        this.programChoiceOpen = false;
        this.controlSessionKey = sessionKey;
      }
      this.appearanceStale = false;
      this.applyAppearance();
      if (
        this.programSavedSignature &&
        JSON.stringify(state.configuration.temperature_programs || []) ===
          this.programSavedCatalogSignature
      ) {
        if (
          JSON.stringify(this.programDraft) === this.programSavedSignature &&
          !this.programEditor
        )
          this.programDraft = null;
        this.programSavedSignature = null;
        this.programSavedCatalogSignature = null;
      }
      this.syncNavigation();
      // The status cards remain live every two seconds.  Archive list/pages
      // are only useful while the history section is actually on screen.
      const historyVisible = !this.$("#history")?.hidden;
      if (!this.temperatureInteraction) {
        const restoreTargetFocus = this.shadowRoot.activeElement?.matches?.(
          '[data-target-arc][role="slider"]',
        );
        this.drawCurrent();
        if (restoreTargetFocus) this.$('[data-target-arc][role="slider"]')?.focus?.();
      }
      if (historyVisible) {
        this.showHistoryCache();
        this.drawHistory("status");
        this.startHistoryLoad();
      }
      this.drawSettings();
      this.syncAppearanceEditor();
      this.message(null, "refresh");
    } catch (error) {
      this.appearanceStale = true;
      this.applyAppearance();
      this.message(error, "refresh");
    } finally {
      this.busy = false;
      if (this.refreshPending) {
        this.refreshPending = false;
        void this.refresh(true);
      }
    }
  }
  historySelectionId() {
    return this.selected === "live"
      ? this.state?.session?.timeline.session_id || this.sessions?.[0]?.session_id
      : this.selected;
  }
  historyCache(id) {
    let cache = this.cache.get(id);
    if (!cache) {
      cache = { records: [], after: 0, pageRunLoaded: false, finalSynced: false };
      this.cache.set(id, cache);
    }
    cache.records ??= [];
    cache.after ??= 0;
    // `loaded` used to mean both "this page run ended" and "this session is
    // final".  Keep it as an alias while callers move to the explicit state.
    cache.pageRunLoaded ??= !!cache.loaded;
    cache.finalSynced ??= !!cache.pageRunLoaded && !!cache.session?.ended_at;
    cache.loaded = cache.pageRunLoaded;
    cache.recordIds ??= new Set(
      cache.records
        .map((record) => record.id)
        .filter((id) => id !== undefined && id !== null),
    );
    return cache;
  }
  updateHistoryCacheMetadata(cache, page) {
    const session = page.session,
      projection = page.phase_projection,
      sessionSignature = JSON.stringify(session || null),
      projectionSignature = JSON.stringify(projection || null);
    if (cache.sessionSignature !== sessionSignature) {
      cache.session = session;
      cache.sessionSignature = sessionSignature;
      cache.sessionRevision = (cache.sessionRevision || 0) + 1;
      cache.metadataRevision = (cache.metadataRevision || 0) + 1;
    }
    if (cache.projectionSignature !== projectionSignature) {
      cache.phase_projection = projection;
      cache.projectionSignature = projectionSignature;
      cache.projectionRevision = (cache.projectionRevision || 0) + 1;
      cache.metadataRevision = (cache.metadataRevision || 0) + 1;
    }
  }
  appendHistoryCacheRecords(cache, records, after) {
    const cursorRecords = records.filter((record) => record.id > after),
      additions = cursorRecords.filter((record) => {
        if (record.id === undefined || record.id === null) return true;
        if (cache.recordIds.has(record.id)) return false;
        cache.recordIds.add(record.id);
        return true;
      });
    if (additions.length) {
      cache.records.push(...additions);
      cache.recordsRevision = (cache.recordsRevision || 0) + 1;
      if (
        additions.some((record) =>
          ["measurement", "source_snapshot"].includes(record.kind),
        )
      )
        cache.measurementRevision = (cache.measurementRevision || 0) + 1;
      // The incremental index remains responsible for identifying affected
      // series.  This only marks that its source records gained entries.
      this.invalidateHistoryIndex();
    }
    return { additions, cursorRecords };
  }
  historyCacheNeedsFetch(cache) {
    return !cache.pageRunLoaded || !cache.finalSynced;
  }
  showHistoryCache() {
    const id = this.historySelectionId();
    if (id !== this.historySessionId) {
      this.historySessionId = id;
      this.window = null;
      this.invalidateHistoryIndex();
      this.historyTimelineSignature = null;
    }
    const cache = id ? this.historyCache(id) : null,
      live = this.selected === "live" && this.state?.session,
      session = live || cache?.session;
    this.shown = session
      ? {
          session,
          records: cache?.records || (this.emptyHistoryRecords ??= []),
          phase_projection: live
            ? this.state.phase_projection
            : cache?.phase_projection,
        }
      : null;
  }
  renderHistoryLoading(load) {
    const node = this.$("#history-loading");
    if (!node) return;
    node.hidden = !load;
    node.textContent = load
      ? `Verlauf wird geladen · ${load.count || 0} Mess- und Ereignispunkte verfügbar`
      : "";
  }
  startHistoryLoad() {
    const entry = this.entry,
      generation = this.generation,
      selected = this.selected,
      liveId = this.state?.session?.timeline.session_id,
      selectionGeneration = this.historySelectionGeneration || 0,
      key = `${generation}:${selectionGeneration}:${entry}:${selected}:${liveId || ""}`;
    if (this.historyLoad?.key === key) return this.historyLoad.promise;
    const load = { key, count: 0 };
    this.historyLoad = load;
    const current = () =>
      this.historyLoad === load &&
      generation === this.generation &&
      selectionGeneration === (this.historySelectionGeneration || 0) &&
      entry === this.entry &&
      selected === this.selected &&
      this.isConnected &&
      !this.$("#history")?.hidden;
    // A live response remains relevant while its session transitions to the
    // archive.  It is stale only when a different live session replaced it.
    const currentFor = (id) => {
      if (!current()) return false;
      if (selected !== "live") return id === this.historySelectionId();
      const nowLiveId = this.state?.session?.timeline.session_id;
      return nowLiveId === liveId || (liveId && !nowLiveId && id === liveId);
    };
    this.renderHistoryLoading(load);
    load.promise = (async () => {
      try {
        const list = await this.api(`/${entry}/archive`);
        if (!current()) return;
        this.sessions = list;
        const liveLabel =
          this.state.operation_enabled && this.state.session
            ? "Laufende Sitzung"
            : "Letzte Sitzung";
        const options =
          `<option value="live">${liveLabel}</option>` +
          list
            .filter((session) => session.session_id !== liveId)
            .map(
              (session) =>
                `<option value="${esc(session.session_id)}">Sitzung vom ${esc(when(session.started_at))}${session.ended_at ? " · beendet" : " · unterbrochen"}</option>`,
            )
            .join("");
        const select = this.$("#session");
        if (select.innerHTML !== options) {
          this.updateMarkup("#session", options);
          select.value = selected;
        }
        const id = this.historySelectionId();
        if (!id) {
          this.showHistoryCache();
          this.drawHistory("archive");
          return;
        }
        const cache = this.historyCache(id);
        if (this.historyCacheNeedsFetch(cache)) {
          let more;
          do {
            const after = cache.after,
              page = await this.api(
                `/${entry}/archive?session_id=${encodeURIComponent(id)}&after=${after}`,
              );
            if (!currentFor(id)) return;
            // Persist each complete page before the next request. A failed
            // later page retries at this cursor and cannot duplicate records.
            const records = Array.isArray(page.records) ? page.records : [],
              { cursorRecords } = this.appendHistoryCacheRecords(cache, records, after),
              next = Math.max(after, ...cursorRecords.map((record) => record.id));
            if (page.next_after && next <= after)
              throw Error("Archivabruf ohne Fortschritt. Erneuter Versuch folgt.");
            this.updateHistoryCacheMetadata(cache, page);
            cache.after = next;
            more = page.next_after;
            cache.pageRunLoaded = !more;
            cache.loaded = cache.pageRunLoaded;
            // A page run can be complete for an open snapshot.  Only a
            // closed final snapshot plus its last page settles the session.
            cache.finalSynced = !!cache.pageRunLoaded && !!cache.session?.ended_at;
            load.count = cache.records.length;
            this.showHistoryCache();
            if (!this.$("#history")?.hidden) this.drawHistory("archive");
            this.renderHistoryLoading(more ? load : null);
            // Let input, paint and the independent status poll run between pages.
            if (more) await new Promise((resolve) => setTimeout(resolve, 0));
          } while (more && current());
        } else {
          this.showHistoryCache();
          this.drawHistory("archive");
        }
        if (current()) this.message(null, "history");
      } catch (error) {
        if (current()) this.message(error, "history");
      } finally {
        if (this.historyLoad === load) {
          this.historyLoad = null;
          this.renderHistoryLoading(null);
        }
      }
    })();
    return load.promise;
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
    const canStart =
      permissions.control && (s.operation_enabled || !s.start_errors.length);
    const sessionGap = !s.operation_enabled
      ? (session?.deadlines || []).find(
          (deadline) => deadline.purpose === "session_gap",
        )
      : null;
    const targetBounds = this.temperatureBounds();
    const temperatureScale = this.appearanceScale("temperature") || targetBounds;
    const humidityScale = this.appearanceScale("humidity") || {
      minimum: 0,
      maximum: 100,
    };
    const arcBounds = this.targetArcBounds();
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
            after_run:
              timerLine.mode === "pending"
                ? timerLine.label || "Ofenkühlung wird vorbereitet"
                : `${timerLine.mode === "paused" ? "pausiert – noch" : "noch"} ${duration(timerLine.seconds, "Minuten")}`,
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
    const operation = sessionGap
      ? `<div class="operation-control"><button class="tile operation stop" data-action="finish-session:${esc(encodeURIComponent(sessionGap.token))}" ${permissions.control ? "" : "disabled"}>Endgültig beenden</button><button class="primary" data-action="operation" ${canStart ? "" : "disabled"}>Fortsetzen</button></div>`
      : `<div class="operation-control"><button class="tile operation ${s.operation_enabled ? "stop" : "primary"}" data-action="operation" ${canStart ? "" : "disabled"}>${s.operation_enabled ? "Ausschalten" : "Einschalten"}</button>${sessionIndicator}</div>`;
    const phaseLabel =
      s.phase === "aufheizen" ? "Heizen" : phases[s.phase] || "Unbekannt";
    const stateLine = `<div class="state-line"><div class="phase-time"><strong class="phase" data-phase="${esc(s.phase)}">${phaseLabel}</strong><span class="availability-line ${availability?.blocker ? "wait" : ""}">${esc(availabilityLine || availabilityText || "")}</span></div><span class="badge">${count} ${count === 1 ? "Saunagang" : "Saunagänge"}</span></div>`;
    const ovenState =
      s.heating_feedback === true
        ? "Ofen an"
        : s.heating_feedback === false
          ? "Ofen aus"
          : "Ofen unbekannt";
    const controlStateLine = `${stateLine}<p class="oven-feedback"><strong class="feedback ${s.heating_feedback === true ? "on" : s.heating_feedback === false ? "off" : "unknown"}">${ovenState}</strong></p>`;
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
      `<svg class="dial ${control ? "dial-temperature" : ""}" viewBox="0 0 300 260" role="${control ? "group" : "img"}" aria-label="${esc(control ? "Temperatur und Solltemperatur" : caption)}"><path d="${temperatureDial.path}" fill="none" stroke="var(--sauna-color-border, var(--divider-color))" stroke-width="16" stroke-linecap="round"/><path d="${temperatureDial.path}" fill="none" stroke="${valid ? color : this.appearanceColor("status_unknown")}" stroke-width="16" stroke-linecap="round" pathLength="100" stroke-dasharray="${Math.max(0, Math.min(100, (((reading ?? minimum) - minimum) / (maximum - minimum || 1)) * 100))} 100"/>${this.temperatureTickMarks({ minimum, maximum })}<text class="reading" x="150" y="119" text-anchor="middle">${num(reading, 1)} ${unit}</text>${control}${caption ? `<text class="caption" x="150" y="240" text-anchor="middle">${esc(caption)}</text>` : ""}</svg>${reading != null && (reading < minimum || reading > maximum) ? `<p class="scale-hint">Messwert außerhalb der Anzeigeskala</p>` : ""}`;
    const deadlineLabels = { confirmation: "Aufgussbestätigung" };
    const phaseRemaining = (phase) =>
      Math.max(
        0,
        (phase.duration_seconds ?? 0) -
          (phase.elapsed_seconds ?? 0) -
          (phase.credited_seconds ?? 0),
      );
    const timerRows = [
      ["Heizsumme (gezählt)", duration(session?.heating.elapsed_seconds || 0)],
      [
        "Mechanischer Ofentimer",
        `<span data-mechanical-timer>${timerText} · ${timerStatus}</span>`,
      ],
    ];
    if (session?.after_run) {
      const phase = session.after_run;
      timerRows.push([
        phase.pending_start || (!phase.ends_at && !phase.paused_at)
          ? "Ofenkühlung wartet auf Schütz-Aus"
          : phase.paused_at
            ? "Ofenkühlung pausiert"
            : "Ofenkühlung",
        phase.pending_start
          ? "Ausschaltung noch nicht bestätigt"
          : !phase.ends_at && !phase.paused_at
            ? `Noch ${duration(phaseRemaining(phase))} bestätigte Auszeit erforderlich`
            : duration(phaseRemaining(phase)),
      ]);
    }
    for (const deadline of session?.deadlines || []) {
      if (!deadlineLabels[deadline.purpose]) continue;
      timerRows.push([deadlineLabels[deadline.purpose], remaining(deadline.due_at)]);
    }
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
      !new Set(["after_run", "session_light", "session_gap"]).has(s.phase_timer.kind)
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
          label: "Ofenkühlung jetzt beenden",
        }
      : null;
    const finishPhase =
      manualPhase && permissions.admin && !manualMode
        ? `<div class="row"><button class="stop" data-action="end-phase:${manualPhase.purpose}:${esc(encodeURIComponent(manualPhase.token))}">${manualPhase.label}</button></div>`
        : "";
    const temperatureColor = this.appearanceColor("series_temperature");
    const humidity = value("upper", "humidity"),
      humidityColor = this.appearanceColor("series_humidity");
    const targetValue = this.temperatureInteraction?.value ?? s.target_temperature,
      targetPoint =
        temperatureScale && this.temperatureArcPoint(targetValue, temperatureScale);
    const targetControl =
      !manualMode && temperatureScale
        ? `${arcBounds ? `<path class="target-temperature-track" data-target-arc="true" d="${temperatureDial.path}" role="slider" tabindex="${permissions.temperature && !this.programRequest ? 0 : -1}" aria-label="Solltemperatur einstellen" aria-valuemin="${arcBounds.minimum}" aria-valuemax="${arcBounds.maximum}" aria-valuenow="${Math.max(arcBounds.minimum, Math.min(arcBounds.maximum, targetValue))}" aria-valuetext="Soll ${num(targetValue, 1)} °C${targetValue < temperatureScale.minimum || targetValue > temperatureScale.maximum ? " · außerhalb der Anzeigeskala" : ""}" aria-disabled="${!permissions.temperature || !!this.programRequest}"/>` : ""}<circle class="target-temperature-handle" ${arcBounds ? 'data-target-arc="true"' : 'data-inert-target="true"'} cx="${targetPoint.x}" cy="${targetPoint.y}" r="10"/><text class="target-reading" x="150" y="151" text-anchor="middle">Soll ${num(targetValue, 1)} °C</text>${targetValue < temperatureScale.minimum || targetValue > temperatureScale.maximum ? '<text class="scale-hint" x="150" y="235" text-anchor="middle">Soll außerhalb der Anzeigeskala</text>' : !arcBounds ? '<text class="scale-hint" x="150" y="235" text-anchor="middle">Sollwertwahl am Bogen nicht möglich</text>' : ""}`
        : "";
    const programs = Array.isArray(s.configuration.temperature_programs)
      ? s.configuration.temperature_programs
      : [];
    const programChoice = this.programChoice(programs),
      programMode = this.programMode(programs),
      activeChoice = this.storedProgramChoice(programs),
      activeMode =
        activeChoice === "constant"
          ? "constant"
          : activeChoice === "individual"
            ? "individual"
            : "program",
      activeProgramLabel =
        activeMode === "program"
          ? programs.find((program) => program.id === activeChoice)?.name ||
            "Individuell"
          : activeMode === "constant"
            ? "Konstant"
            : "Individuell",
      programBusy = !!this.programRequest;
    const draftLabel = (draft) =>
      draft ? '<small class="program-draft-label">Vorgemerkt</small>' : "";
    const programTypes = `<div class="program-types" role="group" aria-label="Temperaturprogramm auswählen"><button type="button" data-action="program-mode:program" aria-pressed="${activeMode === "program"}" class="${programMode === "program" && activeMode !== "program" ? "program-draft" : ""}" ${permissions.program && programs.length && !programBusy ? "" : "disabled"}>Programm${draftLabel(programMode === "program" && activeMode !== "program")}</button><button type="button" data-action="program-mode:individual" aria-pressed="${activeMode === "individual"}" class="${programMode === "individual" && activeMode !== "individual" ? "program-draft" : ""}" ${permissions.program && !programBusy ? "" : "disabled"}>Individuell${draftLabel(programMode === "individual" && activeMode !== "individual")}</button><button type="button" data-action="program-mode:constant" aria-pressed="${activeMode === "constant"}" class="${programMode === "constant" && activeMode !== "constant" ? "program-draft" : ""}" ${permissions.program && !programBusy ? "" : "disabled"}>Konstant${draftLabel(programMode === "constant" && activeMode !== "constant")}</button></div>`;
    const namedPrograms =
      programMode === "program"
        ? `<div class="program-named-list">${programs.map((program) => `<button type="button" class="program-named-choice ${program.id === programChoice && program.id !== activeChoice ? "program-draft" : ""}" data-action="program-select:${esc(program.id)}" aria-pressed="${program.id === activeChoice}" ${permissions.program && !programBusy ? "" : "disabled"}><span>${esc(program.name)}${draftLabel(program.id === programChoice && program.id !== activeChoice)}</span><small>${esc(this.programSteps(program))}</small></button>`).join("")}</div>`
        : "";
    const light = s.manual_controls?.light || {};
    const roundedLight = (value) =>
      Number.isFinite(Number(value)) ? Math.round(Number(value)) : null;
    const manualLight = light.manual == null ? null : roundedLight(light.manual),
      normalLight = roundedLight(light.normal),
      brightLight = roundedLight(p.session_light_brightness_percent),
      sameLightPresets =
        normalLight != null && brightLight != null && normalLight === brightLight;
    const lightPreset =
      manualLight == null
        ? "auto"
        : manualLight <= 0
          ? "off"
          : sameLightPresets && manualLight === brightLight
            ? "bright"
            : manualLight === normalLight
              ? "normal"
              : manualLight === brightLight
                ? "bright"
                : null;
    const lightPresetButtons = (canControl, includeDimmed = manualMode) =>
      `<button data-action="light:false" aria-pressed="${lightPreset === "off"}" ${canControl ? "" : "disabled"}>Aus</button>${manualMode ? "" : `<button data-action="light:auto" aria-pressed="${lightPreset === "auto"}" ${canControl ? "" : "disabled"}>Automatik</button>`}${includeDimmed && !sameLightPresets ? `<button data-action="light:normal" aria-pressed="${lightPreset === "normal"}" ${canControl ? "" : "disabled"}>Gedimmt <small>${num(light.normal, 0)} %</small></button>` : ""}<button data-action="light:true" aria-pressed="${lightPreset === "bright"}" ${canControl ? "" : "disabled"}>${includeDimmed && sameLightPresets ? "Gedimmt / Hell" : "Hell"} <small>${num(p.session_light_brightness_percent, 0)} %</small></button>`;
    const heater = s.manual_controls?.heater || {};
    const overrideRemaining = (endsAt) =>
      endsAt && stamp(endsAt) > now
        ? remaining(endsAt)
        : "Keine laufende Übersteuerung";
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
    const isAdmin = !!permissions.admin;
    const canManualHeater = permissions.heater;
    const canManualLight = isAdmin && permissions.light;
    const modeControls = `<div class="row"><small>Betriebsmodus</small><button data-action="control-mode:automatic" aria-selected="${!manualMode}" ${permissions.control && !modeLocked ? "" : "disabled"}>Automatik</button><button data-action="control-mode:manual" aria-selected="${manualMode}" ${permissions.control && !modeLocked ? "" : "disabled"}>Manuell</button>${modeLocked ? '<small class="muted">Während der Sitzung gesperrt.</small>' : ""}</div>`;
    const heaterControls =
      isAdmin || manualMode
        ? `<section class="manual-section manual-heater"><div class="row"><h3>Ofen</h3><span class="manual-status ${heaterMode}" data-heater-status="${heaterMode}">${heaterStatus}</span></div><div class="row"><button data-action="heater:true" aria-pressed="${heater.manual === true}" ${canManualHeater ? "" : "disabled"}>EIN</button><button data-action="heater:false" aria-pressed="${heater.manual === false}" ${canManualHeater ? "" : "disabled"}>AUS</button>${manualMode ? "" : `<button data-action="heater:auto" aria-pressed="${heater.manual == null}" ${canManualHeater ? "" : "disabled"}>Automatik</button>`}</div>${!s.operation_enabled ? '<small class="muted">EIN startet zuerst den Saunabetrieb.</small>' : ""}</section>`
        : "";
    const lightControls = (includeDimmed) => {
      const fallback = `Vorübergehende Übersteuerungen folgen spätestens nach ${duration(p.manual_override_minutes * 60)} wieder der Automatik.`;
      const brightnessInput = isAdmin
        ? `<div class="row"><label for="manual-light-value-overview">Freie Helligkeit</label><input id="manual-light-value-overview" data-manual-light-value class="manual-light-value" type="number" min="0" max="100" step="1" inputmode="numeric" value="${esc(manualLightValue)}" ${canManualLight ? "" : "disabled"}><span>%</span><button data-action="manual-light-overview" class="confirm" ${canManualLight ? "" : "disabled"}>Übernehmen</button></div><small class="muted">${manualMode ? "Die Lichtwahl bleibt im manuellen Betrieb erhalten." : fallback}</small>`
        : "";
      return `<section class="manual-section manual-light"><div class="row"><h3>Licht</h3><span class="manual-status ${lightMode}" data-light-status="${lightMode}">${lightStatus}</span></div><div class="row">${lightPresetButtons(permissions.light, includeDimmed)}</div>${brightnessInput}</section>`;
    };
    const overviewLightTimer =
      !session && s.phase_timer?.kind === "session_light"
        ? `<p class="muted">Lichtnachlauf noch ${duration(s.phase_timer.seconds, "Minuten")}</p>`
        : "";
    const programBounds = this.programBounds();
    // Temperaturautomatik stays the internal CSS/API term; the control uses the shorter label.
    const temperatureAutomation = !manualMode
      ? `<section class="control-section temperature-automation"><h3>Temperaturwahl</h3>${session ? `<button type="button" data-action="program-toggle" aria-expanded="${!!this.programChoiceOpen || this.programDirty() || !!this.programRequest}" aria-controls="program-choice-body"><span>Programm ändern</span><small class="program-active-label">Aktuell: ${esc(activeProgramLabel)}</small></button><div id="program-choice-body" ${this.programChoiceOpen || this.programDirty() || this.programRequest ? "" : "hidden"}>` : ""}${programTypes}${namedPrograms}${programMode === "individual" ? this.freeProgramForm(programBounds, permissions) : ""}${programMode === "constant" ? `<div class="temperature-presets">${presets.map((v) => `<button type="button" class="tile" data-action="preset:${v}" aria-pressed="${Math.abs(v - s.target_temperature) < 0.01}" ${permissions.temperature && !programBusy ? "" : "disabled"}>${num(v, 1)} °C</button>`).join("")}</div>` : ""}${this.programDirty() && (session || this.programSelectionDraft != null) ? `<div class="program-pending"><span>Noch nicht übernommen: ${esc(programs.find((program) => program.id === programChoice)?.name || (programChoice === "individual" ? "Individuell" : "Konstant"))}</span><button type="button" data-action="program-cancel-draft" ${programBusy ? "disabled" : ""}>Abbrechen</button></div>` : ""}${session ? "</div>" : ""}</section>`
      : "";
    const overviewQuality = (position, quantity) =>
      quality(position, quantity) === "current"
        ? ""
        : `<p class="muted">${qualityText(position, quantity)}</p>`;
    this.updateMarkup(
      "#current",
      this.renderControlView({
        modeControls,
        stateLine: controlStateLine,
        overviewLightTimer,
        operation,
        programAction:
          !manualMode &&
          ((session && (this.programDirty() || this.programSaveState)) ||
            (!session &&
              programMode !== "individual" &&
              (this.programSelectionDraft != null || this.programSaveState)))
            ? this.programApplyButton(permissions)
            : "",
        temperatureAutomation,
        manualMode,
        heaterControls,
        lightControls: lightControls(manualMode || isAdmin),
        heaterOverrideRemaining: overrideRemaining(heater.override_ends_at),
        lightOverrideRemaining: overrideRemaining(light.override_ends_at),
        finishPhase,
        alert,
        temperatureGauge: dial(
          value("upper", "temperature"),
          "°C",
          "",
          temperatureColor,
          temperatureScale?.maximum ?? temperatureDial.scale,
          quality("upper", "temperature") === "current",
          targetControl,
          temperatureScale?.minimum ?? 0,
        ),
        temperatureQuality: overviewQuality("upper", "temperature"),
        humidityGauge: dial(
          humidity,
          "%",
          "Relative Luftfeuchte",
          humidityColor,
          humidityScale.maximum,
          quality("upper", "humidity") === "current",
          "",
          humidityScale.minimum,
        ),
        humidityQuality: overviewQuality("upper", "humidity"),
      }),
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
      this.renderDetailView({
        stateLine,
        timers,
        presence: this.presenceDetails(),
        targetTemperature: num(s.target_temperature, 1),
        thermostatTarget: num(s.thermostat_target, 1),
        restartThreshold: num(restartThreshold, 1),
        temperatureProgram,
        heatCaption,
        heatSource,
        energyText,
        powerText,
        decisionText: esc(s.decision_text),
        lightMode,
        lightStatus,
        lightAutomatic: light.automatic,
        lightManual: light.manual,
        lightOverrideRemaining: overrideRemaining(light.override_ends_at),
        doorText,
        measurements: ["upper", "lower"]
          .map(
            (pos) =>
              `<h3>${pos === "upper" ? "Obere" : "Untere"} Messposition</h3><p>${formatValue(pos, "temperature", "°C")} · ${qualityText(pos, "temperature")}</p><p>${formatValue(pos, "humidity", "% relative Luftfeuchte")} · ${qualityText(pos, "humidity")}</p>`,
          )
          .join(""),
        detectionText: esc(detectionText),
      }),
    );
    if (progressionOpen) this.$("#current details").open = true;
    for (const [id, value] of Object.entries(this.progressionDraft || {}))
      if (this.$(`#${id}`)) this.$(`#${id}`).value = value;
  }
  renderControlView(view) {
    const {
      modeControls,
      stateLine,
      overviewLightTimer,
      operation,
      programAction,
      temperatureAutomation,
      manualMode,
      heaterControls,
      lightControls,
      heaterOverrideRemaining,
      lightOverrideRemaining,
      finishPhase,
      alert,
      temperatureGauge,
      temperatureQuality,
      humidityGauge,
      humidityQuality,
    } = view;
    const controls = manualMode
      ? `<div class="manual-controls">${heaterControls}${lightControls}</div>`
      : heaterControls
        ? `<div class="manual-overrides"><h2>Manuelle Übersteuerung</h2>${heaterControls}<small>Ofen: ${heaterOverrideRemaining}</small>${lightControls}<small>Licht: ${lightOverrideRemaining}</small>${finishPhase}</div>`
        : lightControls;
    return `<div class="card control-mode-card">${modeControls}</div><div class="dashboard"><div class="card control-main">${stateLine}${overviewLightTimer}<div class="tiles ${programAction ? "program-action-layout" : ""}">${programAction}${operation}</div>${temperatureAutomation}${controls}${alert}</div><div class="environment"><div class="card gauge-card"><div class="gauges"><div><h2>Temperatur</h2>${temperatureGauge}${temperatureQuality}</div><div><h2>Luftfeuchte</h2>${humidityGauge}${humidityQuality}</div></div></div></div></div>`;
  }
  renderDetailView(view) {
    const {
      stateLine,
      timers,
      presence,
      targetTemperature,
      thermostatTarget,
      restartThreshold,
      temperatureProgram,
      heatCaption,
      heatSource,
      energyText,
      powerText,
      decisionText,
      lightMode,
      lightStatus,
      lightAutomatic,
      lightManual,
      lightOverrideRemaining,
      doorText,
      measurements,
      detectionText,
    } = view;
    return `<div class="card hero">${stateLine}${timers}</div><div class="detail-grid"><div class="card"><h2>Ofen</h2><dl><dt>Aktuelle Solltemperatur</dt><dd>${targetTemperature} °C</dd><dt>Obere Regeltemperatur</dt><dd data-readiness>${thermostatTarget} °C</dd><dt>Wiedereinschaltschwelle</dt><dd>${restartThreshold} °C</dd><dt>Temperaturprogramm</dt><dd>${temperatureProgram}</dd><dt>Heizzustand</dt><dd>${heatCaption}</dd><dt>Ermittlung der Heizzeit</dt><dd>${heatSource}</dd><dt>Energieverbrauch</dt><dd>${energyText}</dd><dt>Ofenleistung</dt><dd>${powerText}</dd></dl><p>${decisionText}</p></div><div class="card"><h2>Licht</h2><p class="manual-status ${lightMode}" data-light-status="${lightMode}">${lightStatus}</p><dl><dt>Automatischer Wert</dt><dd>${num(lightAutomatic, 0)} %</dd><dt>Manuelle Vorgabe</dt><dd>${lightManual == null ? "Keine" : `${num(lightManual, 0)} %`}</dd><dt>Übersteuerung</dt><dd>${lightOverrideRemaining}</dd></dl></div><div class="card"><h2>Messung</h2><p data-door-status>${doorText}</p>${measurements}<p class="muted">${detectionText}</p></div></div>${presence}`;
  }
  presenceDetails() {
    const presence = this.state?.presence;
    const rules = this.state?.rule_inputs;
    if (!presence && !rules) return "";
    const report = (item) => {
      if (!item) return "Noch keine Meldung";
      const occupancy = item.available
        ? { present: "Anwesend", absent: "Abwesend", unknown: "Unbekannt" }[
            item.occupancy
          ] || "Unbekannt"
        : "Nicht verfügbar";
      const assertion =
        item.assertion === "provisional_proxy"
          ? "Vorläufiger Proxy"
          : item.assertion === "proxy_retraction"
            ? "Proxy zurückgenommen; keine beobachtete Abwesenheit"
            : "Direkte Präsenz";
      return `${occupancy} · ${assertion} · Ereignis ${when(item.effective_at)} · empfangen ${when(item.received_at)}${item.reason ? ` · ${item.reason}` : ""}`;
    };
    return `<div class="card"><h2>Präsenz und Regelursache</h2><dl><dt>Gewünschte Quelle</dt><dd>${presence?.configured_source === "ha_presence" ? "Externe Präsenz (vorbereitet)" : "Proxy"}</dd><dt>Wirksame Quelle</dt><dd>Proxy</dd><dt>Aktuelle Belegung</dt><dd>${esc(report(presence?.current))}</dd>${Object.entries(
      presence?.external || {},
    )
      .map(([source, item]) => `<dt>${esc(source)}</dt><dd>${esc(report(item))}</dd>`)
      .join(
        "",
      )}<dt>Heizanforderung im Saunagang</dt><dd>${rules?.gang_heat_demand ? "Aktiv" : "Inaktiv"}</dd><dt>Temporäres Heizen nach Türschluss</dt><dd>${rules?.temporary_door_heat ? "Aktiv" : "Inaktiv"}</dd><dt>Ofenkühlung</dt><dd>${rules?.cooling ? "Aktiv" : "Inaktiv"}</dd></dl><p class="muted">Externe Präsenz wird beobachtet und ersetzt den Proxy noch nicht. Die Aktivierungsregeln sind offen. Das optionale Audioziel ist vorbereitet; es findet keine Wiedergabe statt.</p></div>`;
  }
  async changeTarget(value) {
    if (!Number.isFinite(value)) throw Error("Gültige Solltemperatur eingeben");
    if (this.programRequest) return;
    this.programSelectionDraft = "constant";
    this.freeProgramKind = null;
    this.freeProgramStepsDraft = null;
    this.programSaveState = null;
    clearTimeout(this.programSavedTimer);
    const entry = this.entry,
      generation = this.generation,
      previous = this.temperatureChange,
      draft = this.progressionDraft;
    const change = (async () => {
      await previous;
      if (this.entry !== entry || this.generation !== generation) return;
      const saved = await this.api(`/${entry}/temperature`, "POST", {
        target_temperature_c: value,
      });
      // A dial request may finish after the user has already filled the
      // progression form. Keep that unsaved form until its own save succeeds.
      if (this.entry !== entry || this.generation !== generation) return;
      this.state.configuration = {
        ...this.state.configuration,
        program_mode: "constant",
        selected_program_id: null,
        temperature_steps: null,
        parameters: {
          ...this.state.configuration.parameters,
          ...(saved?.parameters || { target_temperature_c: value }),
        },
      };
      this.state.target_temperature = value;
      this.programRevision = (this.programRevision || 0) + 1;
      if (this.programSelectionDraft === "constant") this.programSelectionDraft = null;
      if (this.progressionDraft === draft) this.progressionDraft = null;
      await this.refresh(true);
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
  targetArcBounds() {
    const target = this.temperatureBounds(),
      display = this.appearanceScale("temperature");
    if (!target || !display) return null;
    const minimum = Math.max(target.minimum, display.minimum),
      maximum = Math.min(target.maximum, display.maximum);
    return maximum >= minimum ? { minimum, maximum } : null;
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
  }
  selectNamedProgram(id, programs = []) {
    if (!programs.some((program) => program.id === id))
      throw Error("Unbekanntes Temperaturprogramm");
    this.programSelectionDraft = id;
    this.lastNamedProgramId = id;
    this.markProgramEdited();
  }
  cancelProgramDraft() {
    this.programSelectionDraft = null;
    this.freeProgramKind = null;
    this.freeProgramStepsDraft = null;
    this.progressionDraft = null;
    this.programSaveState = null;
    this.programChoiceOpen = false;
    this.drawCurrent();
  }
  async applyProgram() {
    if (
      this.programRequest ||
      !this.state?.permissions?.program ||
      !this.programDirty()
    )
      return;
    const entry = this.entry,
      generation = this.generation,
      configuration = this.state.configuration,
      choice = this.programChoice(configuration.temperature_programs || []),
      previousTarget = this.temperatureChange,
      draft = this.progressionDraft;
    let path = "program",
      body;
    if (choice !== "individual") body = { profile: choice };
    else if (
      this.freeProgramKind === "steps" ||
      (Array.isArray(configuration.temperature_steps) && !this.freeProgramKind)
    )
      body = { temperature_steps: this.freeProgramValues() };
    else {
      const { start, end, gangs } = this.progressionValues();
      const explicitStart = Object.hasOwn(draft || {}, "progression-start");
      const liveEdit =
        configuration.program_mode === "progressive" &&
        configuration.selected_program_id == null &&
        !Array.isArray(configuration.temperature_steps) &&
        !explicitStart;
      if (liveEdit) {
        path = "temperature";
        body = {};
        if (end !== configuration.parameters.final_temperature_c)
          body.final_temperature_c = end;
        if (gangs !== configuration.parameters.temperature_gangs)
          body.temperature_gangs = gangs;
      } else
        body = {
          target_temperature_c: start,
          final_temperature_c: end,
          temperature_gangs: gangs,
        };
      if (!explicitStart && previousTarget && !liveEdit)
        body.target_temperature_c = null; // Filled from the completed target write below.
    }
    if (!Object.keys(body).length) return;
    const request = { entry, generation };
    this.programRequest = request;
    this.programSaveState = "saving";
    clearTimeout(this.programSavedTimer);
    this.drawCurrent();
    try {
      const targetParameters = await previousTarget;
      if (
        this.programRequest !== request ||
        this.entry !== entry ||
        this.generation !== generation
      )
        return;
      if (body.target_temperature_c === null) {
        body.target_temperature_c = Number(targetParameters?.target_temperature_c);
        if (!Number.isFinite(body.target_temperature_c))
          throw Error("Starttemperatur konnte nicht übernommen werden");
      }
      const saved = await this.api(`/${entry}/${path}`, "POST", body);
      if (
        this.programRequest !== request ||
        this.entry !== entry ||
        this.generation !== generation
      )
        return;
      const accepted = {
        parameters: saved?.parameters || { ...configuration.parameters, ...body },
        program_mode:
          saved?.program_mode ||
          (path === "temperature"
            ? "progressive"
            : choice === "constant"
              ? "constant"
              : "progressive"),
        temperature_steps: Object.hasOwn(saved || {}, "temperature_steps")
          ? saved.temperature_steps
          : body.temperature_steps || null,
        selected_program_id: Object.hasOwn(saved || {}, "selected_program_id")
          ? saved.selected_program_id
          : choice !== "constant" && choice !== "individual"
            ? choice
            : null,
      };
      this.programRevision = (this.programRevision || 0) + 1;
      this.state.configuration = {
        ...this.state.configuration,
        ...accepted,
        parameters: { ...this.state.configuration.parameters, ...accepted.parameters },
      };
      this.programSelectionDraft = null;
      this.freeProgramKind = null;
      this.freeProgramStepsDraft = null;
      if (this.progressionDraft === draft) this.progressionDraft = null;
      this.programRequest = null;
      this.programSaveState = "saved";
      this.programChoiceOpen = false;
      this.drawCurrent();
      this.programSavedTimer = setTimeout(() => {
        if (
          this.entry !== entry ||
          this.generation !== generation ||
          this.programSaveState !== "saved"
        )
          return;
        this.programSaveState = null;
        this.drawCurrent();
      }, 2000);
    } catch (error) {
      if (
        this.programRequest !== request ||
        this.entry !== entry ||
        this.generation !== generation
      )
        return;
      if (this.programRequest === request) {
        this.programRequest = null;
        this.programSaveState = null;
        this.drawCurrent();
      }
      throw error;
    }
    await this.refresh(true);
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
    this.drawCurrent();
  }
  programDirty() {
    if (
      this.programSelectionDraft == null &&
      this.progressionDraft == null &&
      this.freeProgramStepsDraft == null &&
      this.freeProgramKind == null
    )
      return false;
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
    return saving
      ? "program-saving"
      : dirty
        ? "confirm"
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
          ? "✓ Übernommen"
          : this.state?.session
            ? "Programm übernehmen"
            : "Übernehmen";
    return `<button type="button" data-action="program-apply" class="${this.programApplyClass(dirty, saving)} ${this.state?.session ? "program-main" : ""}" ${permissions.program && dirty && !saving ? "" : "disabled"}>${label}</button>`;
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
      disabled = permissions.program && !this.programRequest ? "" : "disabled";
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
    return `<div class="program-form">${kindButtons}${fields}${this.state?.session ? "" : `<div class="program-actions">${this.programApplyButton(permissions)}</div>`}</div>`;
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
    bounds = this.appearanceScale("temperature"),
    radius = temperatureDial.radius,
  ) {
    if (!bounds) return { x: temperatureDial.centerX, y: temperatureDial.centerY };
    const bounded = Math.max(bounds.minimum, Math.min(bounds.maximum, Number(value)));
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
  temperatureTickValues(bounds = this.appearanceScale("temperature")) {
    return appearanceTickValues(bounds);
  }
  temperatureTickMarks(bounds = this.appearanceScale("temperature")) {
    const span = bounds?.maximum - bounds?.minimum;
    const formatTick = (value) =>
      span < 0.01
        ? Number(value).toPrecision(3)
        : num(value, span < 1 ? 2 : span < 10 ? 1 : 0);
    return this.temperatureTickValues(bounds)
      .map((value) => {
        const tick = this.temperatureArcPoint(
            value,
            bounds,
            temperatureDial.radius - 12,
          ),
          label = this.temperatureArcPoint(value, bounds, temperatureDial.radius - 29);
        return `<line x1="${tick.x}" y1="${tick.y}" x2="${this.temperatureArcPoint(value, bounds, temperatureDial.radius - 20).x}" y2="${this.temperatureArcPoint(value, bounds, temperatureDial.radius - 20).y}" stroke="var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)))" stroke-width="2"/><text class="tick" x="${label.x}" y="${Number(label.y) + 4}" text-anchor="middle">${formatTick(value)}</text>`;
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
    const bounds = this.appearanceScale("temperature");
    const allowed = this.targetArcBounds();
    if (!bounds || !allowed) return null;
    return this.clampTemperature(
      bounds.minimum +
        ((onArc - temperatureDial.startAngle) /
          (temperatureDial.endAngle - temperatureDial.startAngle)) *
          (bounds.maximum - bounds.minimum),
      allowed,
    );
  }
  renderTemperatureTarget(value) {
    const track = this.$("[data-target-arc]"),
      handle = this.$(".target-temperature-handle");
    if (!track || !handle) return;
    const bounds = this.appearanceScale("temperature"),
      point = this.temperatureArcPoint(value, bounds);
    track.setAttribute("aria-valuenow", value);
    track.setAttribute(
      "aria-valuetext",
      `${num(value, 1)} °C${value < bounds.minimum || value > bounds.maximum ? " · außerhalb der Anzeigeskala" : ""}`,
    );
    handle.setAttribute("cx", point.x);
    handle.setAttribute("cy", point.y);
  }
  beginTemperatureDrag(event, svg, target) {
    if (
      !this.state?.permissions?.temperature ||
      this.programRequest ||
      !this.targetArcBounds() ||
      !svg
    )
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
    if (
      !this.state?.permissions?.temperature ||
      this.temperatureInteraction ||
      this.programRequest
    )
      return;
    const bounds = this.targetArcBounds();
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
  drawHistory(reason = "viewport") {
    this.scheduleHistoryRender(reason);
  }
  renderHistory(reasons = new Set(["viewport"])) {
    if (!this.isConnected || this.$("#history")?.hidden) return;
    if (!this.shown) {
      this.historyChart?.destroy();
      this.historyChart = null;
      this.updateMarkup(
        "#plots",
        '<div class="card empty">Noch keine Sitzungsdaten. Wähle eine frühere Saunasitzung oder schalte den Betrieb ein.</div>',
      );
      for (const selector of ["#gangs", "#event-list", "#detection-plots"])
        this.updateMarkup(selector, "");
      this.historyGangKey = this.historyEventKey = null;
      return;
    }
    if (reasons.size === 1 && reasons.has("cursor") && this.historyChart) {
      this.historyChart.interaction.readGeometry();
      if (this.pendingHover) this.hoverChart(this.pendingHover);
      return;
    }
    const { session, records } = this.shown,
      t = session.timeline;
    if (
      this.chartDataIndex?.records !== records ||
      this.chartDataIndex.indexedCount !== records.length
    )
      this.historyIndex(records);
    if (this.$("#plots")?.hidden) {
      this.ensureHistoryWindow();
      this.updateHistoryTimelineRevision(session);
      const key = `${this.historyDatasetRevision}:${this.historyTimelineRevision}:${this.historyWindowRevision}`;
      if (key !== this.historyDiagnosticsKey) {
        this.historyDiagnosticsKey = key;
        this.drawDiagnostics();
      }
      return;
    }
    const identity = `${this.entry}:${t.session_id}`;
    if (this.historyChart?.identity !== identity) {
      this.historyChart?.destroy();
      this.historyChart = new HistoryChart(this, identity);
      this.scheduleHistoryRender("initial");
      return;
    }
    // Status and archive responses often arrive in different frames. Commit a
    // following viewport with the archive page, so the old cache isn't drawn
    // once at the new time and then drawn again for that same measurement batch.
    if (
      !this.window ||
      [...reasons].some((reason) => ["initial", "archive", "viewport"].includes(reason))
    )
      this.ensureHistoryWindow();
    const gangs = [...t.completed, ...(t.active ? [t.active] : [])];
    this.updateHistoryTimelineRevision(session);
    this.historyChart.render(reasons, session, gangs);
    const gangNow = t.active ? this.state.now : "";
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
    const eventKey = `${this.historyTimelineRevision}:${this.historyEventRevision || 0}`;
    if (this.historyEventKey !== eventKey) {
      this.historyEventKey = eventKey;
      const openEvents = [
        ...this.shadowRoot.querySelectorAll("#event-list details"),
      ].map((d) => d.open);
      const diagnostics = this.historyRecords("diagnostic"),
        traces = this.historyRecords("detector_trace").map((r) => r.payload);
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
      index = {
        records,
        series: new Map(),
        byKind: new Map(),
        display: new Map(),
        seriesState: new Map(),
        indexedCount: 0,
      };
    for (let i = index.indexedCount; i < records.length; i++) {
      const record = records[i],
        grouped = index.byKind.get(record.kind) || [];
      grouped.push(record);
      index.byKind.set(record.kind, grouped);
      if (["diagnostic", "detector_trace"].includes(record.kind))
        this.historyEventRevision = (this.historyEventRevision || 0) + 1;
      if (!["measurement", "source_snapshot"].includes(record.kind)) continue;
      const source = record.payload,
        time = stamp(source.received_at),
        number = source.value == null ? NaN : Number(source.value),
        value = Number.isFinite(number) ? number : null;
      if (!Number.isFinite(time)) continue;
      const key = `${source.position}:${source.quantity}`,
        values = index.series.get(key) || [],
        point = { time, value, source };
      index.seriesState ??= new Map();
      const state = index.seriesState.get(key) || { revision: 0 };
      point.serial = ++state.revision;
      index.seriesState.set(key, state);
      const display = index.display.get(values);
      if (rebuild || !values.length || values.at(-1).time <= time) {
        values.push(point);
        if (display) this.appendHistoryDisplay(display, point, values.length - 1);
      } else {
        values.splice(lowerBoundHistory(values, time), 0, point);
        // Normal archive additions are chronological.  A late item or a
        // duplicate that needs insertion is rare; rebuild only this series'
        // display tree before it is next queried.
        index.display.delete(values);
      }
      index.series.set(key, values);
    }
    if (rebuild)
      for (const values of index.series.values())
        values.sort((a, b) => a.time - b.time);
    index.indexedCount = records.length;
    return (this.chartDataIndex = index);
  }
  historyDisplay(values) {
    const key = values;
    const displays = (this.chartDataIndex.display ??= new Map());
    let display = displays.get(key);
    if (!display) {
      display = { values, levels: new Map() };
      displays.set(key, display);
    }
    return display;
  }
  historyDisplayLevel(display, width) {
    let level = display.levels.get(width);
    if (level) return level;
    level = { width, nodes: new Map(), keys: [] };
    const child = display.levels.get(width / 2);
    if (child) {
      for (const key of child.keys)
        this.appendHistoryDisplayNode(level, child.nodes.get(key));
    } else
      for (let index = 0; index < display.values.length; index++)
        this.appendHistoryDisplayLevel(level, display.values[index], index);
    display.levels.set(width, level);
    return level;
  }
  appendHistoryDisplay(display, point, index) {
    for (const level of display.levels.values())
      this.appendHistoryDisplayLevel(level, point, index);
  }
  appendHistoryDisplayLevel(level, point, index) {
    const key = Math.floor(point.time / level.width);
    let node = level.nodes.get(key);
    if (!node) {
      node = {
        firstIndex: index,
        lastIndex: index,
        first: point,
        last: point,
        minimum: point.value == null ? null : point,
        maximum: point.value == null ? null : point,
        firstValid: point.value == null ? null : point,
        lastValid: point.value == null ? null : point,
        missing: point.value == null,
        maximumGap: 0,
      };
      level.nodes.set(key, node);
      level.keys.push(key);
      return;
    }
    node.lastIndex = index;
    node.last = point;
    if (point.value == null) {
      node.missing = true;
      return;
    }
    if (node.lastValid)
      node.maximumGap = Math.max(node.maximumGap, point.time - node.lastValid.time);
    node.lastValid = point;
    if (!node.firstValid) node.firstValid = point;
    if (!node.minimum || point.value < node.minimum.value) node.minimum = point;
    if (!node.maximum || point.value > node.maximum.value) node.maximum = point;
  }
  appendHistoryDisplayNode(level, child) {
    const key = Math.floor(child.first.time / level.width);
    let node = level.nodes.get(key);
    if (!node) {
      node = { ...child };
      level.nodes.set(key, node);
      level.keys.push(key);
      return;
    }
    node.lastIndex = child.lastIndex;
    node.last = child.last;
    node.missing ||= child.missing;
    node.maximumGap = Math.max(
      node.maximumGap,
      child.maximumGap,
      node.lastValid && child.firstValid
        ? child.firstValid.time - node.lastValid.time
        : 0,
    );
    if (!node.firstValid) node.firstValid = child.firstValid;
    if (child.lastValid) node.lastValid = child.lastValid;
    if (child.minimum && (!node.minimum || child.minimum.value < node.minimum.value))
      node.minimum = child.minimum;
    if (child.maximum && (!node.maximum || child.maximum.value > node.maximum.value))
      node.maximum = child.maximum;
  }
  historyDisplayValues(position, quantity, start, end, ttl, pixels) {
    const values = this.series(position, quantity);
    if (!values.length) return values;
    // A dyadic bucket is at most two display pixels wide.  Its first, last
    // and extrema remain visible; a null or timeout descends to raw points.
    const width = Math.max(1, 2 ** Math.ceil(Math.log2((end - start) / pixels || 1)));
    const display = this.historyDisplay(values),
      level = this.historyDisplayLevel(display, width),
      first = Math.max(0, lowerBoundNumber(level.keys, Math.floor(start / width)) - 1),
      after = Math.min(
        level.keys.length,
        lowerBoundNumber(level.keys, Math.floor(end / width) + 1) + 1,
      ),
      output = [];
    let previous = null;
    for (let offset = first; offset < after; offset++) {
      const node = level.nodes.get(level.keys[offset]);
      if (!node) continue;
      const displayGap = !!previous && !!ttl && node.first.time - previous.time > ttl;
      const edge = node.first.time < start || node.last.time > end;
      if (edge || node.missing || (ttl && node.maximumGap > ttl)) {
        const firstRaw = edge
            ? Math.max(node.firstIndex, lowerBoundHistory(values, start) - 1)
            : node.firstIndex,
          afterRaw = edge
            ? Math.min(node.lastIndex + 1, lowerBoundHistory(values, end + 1) + 1)
            : node.lastIndex + 1;
        for (let index = firstRaw; index < afterRaw; index++) {
          const point = values[index];
          output.push({
            time: point.time,
            value: point.value,
            source: point.source,
            displayGap:
              index === firstRaw
                ? displayGap
                : !!ttl && point.time - values[index - 1].time > ttl,
          });
        }
        previous = node.last;
        continue;
      }
      // A changing viewport still uses the same interior aggregation bins.
      // Retain their selected points instead of sorting and copying every bin
      // on each wheel event or live tick. Appends change lastIndex; a late
      // insertion discards this series' display tree in historyIndex().
      let points = node.displayPoints;
      if (
        !points ||
        node.displayLastIndex !== node.lastIndex ||
        node.displayGap !== displayGap
      ) {
        const selected = [node.first, node.minimum, node.maximum, node.last]
          .filter(Boolean)
          .sort((a, b) => a.time - b.time);
        points = [];
        for (const point of selected)
          if (points.at(-1)?.source !== point.source)
            points.push({
              time: point.time,
              value: point.value,
              source: point.source,
              displayGap: point === selected[0] && displayGap,
            });
        node.displayPoints = points;
        node.displayLastIndex = node.lastIndex;
        node.displayGap = displayGap;
      }
      for (const point of points)
        if (output.at(-1)?.source !== point.source) output.push(point);
      previous = node.last;
    }
    return output;
  }
  series(position, quantity) {
    return this.chartDataIndex?.series.get(`${position}:${quantity}`) || [];
  }
  historyRecords(kind) {
    return this.chartDataIndex?.byKind.get(kind) || [];
  }
  nearestMeasurement(position, quantity, time) {
    if (
      this.shown?.records &&
      (this.chartDataIndex?.records !== this.shown.records ||
        this.chartDataIndex.indexedCount !== this.shown.records.length)
    )
      this.historyIndex(this.shown.records);
    return nearestHistoryPoint(
      this.series(position, quantity),
      time,
      this.window[0],
      this.window[1],
    );
  }
  svgCoordinates(svg, clientX, clientY = 0) {
    if (
      this.historyChart?.interaction &&
      (svg === this.historyChart.surface || svg === this.$("#history-overview svg"))
    )
      return this.historyChart.interaction.coordinates(svg, clientX, clientY);
    const rect = svg.getBoundingClientRect();
    const width = svg.viewBox?.baseVal?.width || 1200,
      height = svg.viewBox?.baseVal?.height || 480;
    return {
      x: ((clientX - rect.left) / rect.width) * width,
      y: ((clientY - rect.top) / rect.height) * height,
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
    this.scheduleHistoryRender("cursor");
  }
  scheduleHistoryRender(reason = "viewport") {
    (this.historyReasons ??= new Set()).add(reason);
    if (!this.isConnected || this.$("#history")?.hidden) return;
    this.scheduleFrame("historyFrame", () => {
      const reasons = this.historyReasons;
      this.historyReasons = new Set();
      this.renderHistory(reasons);
    });
  }
  cancelHistoryFrame() {
    if (this.historyFrame != null) {
      (globalThis.cancelAnimationFrame || clearTimeout)(this.historyFrame);
      this.historyFrame = null;
    }
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
  }
  overviewFraction(svg, clientX) {
    const point = this.svgCoordinates(svg, clientX),
      fraction = (point.x - 20) / 1160;
    return Math.max(0, Math.min(1, fraction));
  }
  beginHistoryGesture(event, svg) {
    if (!this.window) return;
    this.historyChart?.interaction.invalidateGeometry("geometry", false);
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
    const [domainStart, domainEnd] = this.historyChart?.domain || this.historyDomain(),
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
    this.drawHistory();
  }
  beginChartPointer(event, svg) {
    if (this.historyInputMode && this.historyInputMode !== "pointer") return;
    this.historyInputMode = "pointer";
    this.historyChart?.interaction.invalidateGeometry("geometry", false);
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
    if (!this.chartPointers.size) this.scheduleHistoryRender("cursor");
  }
  syncHistoryOverview() {
    const svg = this.$("#history-overview svg");
    if (!svg || !this.window) return;
    const [start, end] = this.historyChart?.domain || this.historyDomain(),
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
  historyMarkup(session) {
    const historyTitle = this.historyTitle(session);
    return `<div class="plot-panel" aria-labelledby="history-chart-title"><h2 id="history-chart-title" class="plot-title">${esc(historyTitle)}</h2><div class="legend top-legend" aria-label="Messkurven für ${esc(historyTitle)}"><span><i style="background:var(--sauna-color-series-temperature)"></i>Temperatur</span><span><i style="background:var(--sauna-color-series-humidity)"></i>Luftfeuchte</span></div><div class="row position-select"><button data-action="history-detail" aria-pressed="${this.historyDetail}">${this.historyDetail ? "Messhöhen ausblenden" : "Messhöhen vergleichen"}</button></div><div class="row position-select" data-history-positions ${this.historyDetail ? "" : "hidden"}><button data-action="position-upper" aria-pressed="${this.positions.has("upper")}">━━ Oben</button><button data-action="position-lower" aria-pressed="${this.positions.has("lower")}">┄┄ Unten</button></div><div class="plot-wrap history-stack"><svg class="chart history-background" viewBox="0 0 1200 480" preserveAspectRatio="none" aria-hidden="true"><defs><clipPath id="history-layer-clip"><rect x="65" y="18" width="1070" height="417"/></clipPath></defs><g data-history-annotations clip-path="url(#history-layer-clip)"></g></svg><canvas class="chart history-curves" role="img" aria-label="Temperatur- und Feuchteverlauf; Ereignisse und Zeiten stehen in den nachfolgenden Tabellen." aria-describedby="gangs event-list">Temperatur und Feuchte der Sitzung. Ereignisse und Saunagänge sind in den Tabellen unter dem Diagramm zugänglich.</canvas><svg class="chart session-chart" viewBox="0 0 1200 480" preserveAspectRatio="none" role="img" tabindex="0" aria-label="Sitzungsverlauf"><g data-history-axes></g><line id="cursor" x1="0" x2="0" y1="18" y2="435" stroke="var(--sauna-color-chart-text)" stroke-dasharray="3 3" visibility="hidden"/></svg><div id="tooltip" hidden></div></div><div class="legend"><span><i style="background:color-mix(in srgb,var(--sauna-color-event-door) 95%,transparent)"></i>Saunatür offen</span><span><i style="background:color-mix(in srgb,var(--sauna-color-phase-session) 95%,transparent)"></i>Saunagang</span><span><i style="background:var(--sauna-color-event-infusion)"></i>Aufguss</span></div><div class="legend"><span><i style="background:color-mix(in srgb,var(--sauna-color-phase-warmup) 40%,transparent)"></i>heizen</span><span><i style="background:color-mix(in srgb,var(--sauna-color-phase-ready) 40%,transparent)"></i>bereit</span><span><i style="background:color-mix(in srgb,var(--sauna-color-activity-ventilation) 40%,transparent)"></i>lüften</span><span><i style="background:var(--sauna-color-phase-cooling)"></i>Ofenkühlung</span></div><p class="muted plot-note">${this.historyDetail ? "Durchgezogen: oben · gestrichelt: unten · " : ""}vorläufiger Gang · schmaler Streifen: gezählte Heizzeit</p></div>`;
  }
  historyPreparedSeries(position, quantity, start, end, ttl, pixels, cache) {
    const values = this.series(position, quantity),
      state = this.chartDataIndex.seriesState?.get(`${position}:${quantity}`),
      first = Math.max(0, lowerBoundHistory(values, start) - 1),
      after = Math.min(values.length, lowerBoundHistory(values, end + 1) + 1),
      // Only inserts in this span change its length. Late inserts before it
      // shift both indices equally; stable neighbour identities still detect
      // an insertion that changes an edge tangent without changing the count.
      key = `${start}:${end}:${ttl}:${pixels}:${after - first}:${values[first]?.serial || 0}:${values[after - 1]?.serial || 0}`;
    const name = `${position}:${quantity}`,
      old = cache.get(name);
    if (old?.source === values && old.key === key) return old;
    const prepared = this.historyDisplayValues(
      position,
      quantity,
      start,
      end,
      ttl,
      pixels,
    );
    let low = Infinity,
      high = -Infinity;
    for (const point of prepared)
      if (point.value != null) {
        low = Math.min(low, point.value);
        high = Math.max(high, point.value);
      }
    const result = {
      source: values,
      values: prepared,
      key,
      revision: state?.revision || 0,
      low,
      high,
    };
    cache.set(name, result);
    return result;
  }
  historyModel(chart, session) {
    const [start, end] = this.window,
      left = 65,
      right = 1135,
      top = 18,
      bottom = 435,
      ttl =
        (session.configuration?.parameters || this.state.configuration.parameters)
          .sensor_timeout_seconds * 1000,
      positions = [...this.positions],
      series = new Map();
    let lo = Infinity,
      hi = -Infinity,
      humidity = 0;
    for (const position of positions)
      for (const quantity of ["temperature", "humidity"]) {
        const entry = this.historyPreparedSeries(
          position,
          quantity,
          start,
          end,
          ttl,
          right - left,
          chart.prepared,
        );
        series.set(`${position}:${quantity}`, entry);
        if (quantity === "temperature") {
          lo = Math.min(lo, entry.low);
          hi = Math.max(hi, entry.high);
        } else humidity = Math.max(humidity, entry.high);
      }
    const low = Number.isFinite(lo) ? Math.floor((lo - 2) / 10) * 10 : 20,
      high = Number.isFinite(hi) ? Math.ceil((hi + 2) / 10) * 10 : 100,
      humidityHigh = Math.max(60, Math.ceil((humidity + 2) / 20) * 20),
      x = (time) => left + ((time - start) / (end - start)) * (right - left),
      yT = (value) => bottom - ((value - low) / (high - low)) * (bottom - top),
      yH = (value) => bottom - (value / humidityHigh) * (bottom - top),
      [domainStart, domainEnd] = chart.domain,
      overviewEntry = this.historyPreparedSeries(
        "upper",
        "temperature",
        domainStart,
        domainEnd,
        ttl,
        1160,
        chart.preparedOverview,
      ),
      overview = {
        start: domainStart,
        end: domainEnd,
        left: 20,
        right: 1180,
        top: 8,
        bottom: 36,
        width: 1200,
        height: 46,
        ttl,
        values: overviewEntry.values,
        key: overviewEntry.key,
        low: Number.isFinite(overviewEntry.low) ? overviewEntry.low : 0,
        high: Number.isFinite(overviewEntry.high) ? overviewEntry.high : 1,
      };
    return {
      start,
      end,
      left,
      right,
      top,
      bottom,
      width: 1200,
      height: 480,
      low,
      high,
      humidityHigh,
      ttl,
      positions,
      series,
      x,
      yT,
      yH,
      overview,
    };
  }
  historyAnnotations(model, session, gangs) {
    const { start, end, left, right, top, bottom, x } = model;
    const records = this.shown.records;
    const interval = (a, b, klass, title, y = top, height = bottom - top) => {
      const aa = Math.max(start, stamp(a)),
        bb = Math.min(end, stamp(b || session.ended_at || this.state.now));
      return bb > aa
        ? `<rect class="${klass}" x="${x(aa).toFixed(2)}" y="${y}" width="${(x(bb) - x(aa)).toFixed(2)}" height="${height}"><title>${esc(title)}</title></rect>`
        : "";
    };
    let svg = "";
    const indexedPhases = this.historyRecords("phase");
    // The real indexed path never rereads all records.  Keep the unindexed
    // fallback for a minimal legacy/test caller that supplies no index.
    const phases =
      indexedPhases.length || this.chartDataIndex
        ? indexedPhases
        : records.filter((item) => item.kind === "phase");
    const styles = {
      aufheizen: "heat",
      bereit: "ready",
      zwangskühlung: "cool",
      nachlauf: "after",
    };
    const projection = this.shown?.phase_projection;
    if (projection) {
      for (const phase of projection.intervals || []) {
        const gang = gangs.find((item) => item.gang_id === phase.source_id);
        const klass =
          phase.phase === "saunagang"
            ? gang && !gang.infusion_events.length
              ? "gang provisional"
              : "gang"
            : styles[phase.phase] || "phase-other";
        svg += interval(
          phase.started_at,
          phase.ended_at,
          klass,
          `${phase.phase}${phase.complete === false ? " · unvollständig" : ""}`,
        );
      }
    } else {
      phases.forEach((p, i) => {
        if (styles[p.payload.phase])
          svg += interval(
            p.received_at,
            phases[i + 1]?.received_at,
            styles[p.payload.phase],
            p.payload.phase,
          );
      });
    }
    const doorEvents = session.timeline.processed.filter(
      (e) => e.kind === "door_open" || e.kind === "door_close",
    );
    // Ventilation is an independent annotation.  It remains visible beside an
    // exclusive main-phase projection and must never depend on its presence.
    for (const e of session.timeline.processed.filter(
      (e) => e.kind === "ventilation_confirmed",
    )) {
      const close = doorEvents.find(
        (d) =>
          d.kind === "door_close" && stamp(d.effective_at) >= stamp(e.effective_at),
      );
      svg += interval(e.effective_at, close?.effective_at, "vent", "lüften");
    }
    if (!projection)
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
    return svg;
  }
  historyAxes(model) {
    const { start, end, left, right, top, bottom, low, high, humidityHigh, x } = model;
    const timeZone = localTimeZone();
    let svg = "";
    for (let n = 0; n <= 8; n++) {
      const f = n / 8,
        yy = bottom - (bottom - top) * f,
        t = start + (end - start) * f;
      svg += `<text class="axis-temperature" x="${left - 10}" text-anchor="end" y="${yy + 4}">${(low + (high - low) * f).toFixed(0)}</text><text class="axis-humidity" x="${right + 10}" y="${yy + 4}">${(humidityHigh * f).toFixed(0)}</text><text text-anchor="middle" x="${x(t)}" y="${bottom + 27}">${clock(t, timeZone)}</text>`;
    }
    return (
      svg +
      '<text class="axis-temperature" x="13" y="240">°C</text><text class="axis-humidity" x="1185" y="240">%</text>'
    );
  }
  hoverChart(event) {
    this.historyChart?.interaction.hover(event, this.historyChart.model);
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
    this.historyChart?.interaction.invalidateGeometry("geometry", false);
    const rect =
      this.historyChart?.interaction.readGeometry().rects.surface ||
      svg.getBoundingClientRect();
    event.preventDefault();
    this.webkitHistoryGesture = {
      svg,
      scale: event.scale || 1,
      clientX: event.clientX || rect.left + rect.width / 2,
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
    this.scheduleHistoryRender();
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
          chart += `<line x1="${plotLeft}" x2="${plotRight}" y1="${y(display)}" y2="${y(display)}" stroke="var(--sauna-color-chart-threshold)" stroke-dasharray="4 5"><title>Schwelle ${pos === "upper" ? "oben" : "unten"}: ${num(display, 2)}</title></line>`;
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
    const state = this.state,
      admin = !!state.permissions?.admin;
    if (this.settingsEntry !== this.entry || this.settingsAdmin !== admin) {
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
      const administration = admin
        ? `<div class="card"><h2>Grundeinstellungen</h2><p id="configuration-lock" class="muted"></p><form><fieldset id="parameters">${temperatureGroups}${otherGroups}${experts ? `<details class="settings-group"><summary>Experteneinstellungen zur Erkennung</summary><p class="muted">Diese Werte verändern die Erkennung von Türöffnungen, Personen und Aufgüssen. Die Erkennungskontrolle zeigt ihre Wirkung.</p>${experts}</details>` : ""}<button type="submit" class="confirm">Einstellungen speichern</button></fieldset></form><div class="row"><a href="/config/integrations/integration/ha_sauna">Sensoren und Geräte zuordnen</a></div></div><div class="card"><h2>Standardwerte</h2><p class="muted">Setzt Parameter, Temperaturprogramm und Protokollierung auf die Standardwerte zurück. Die Zuordnung von Sensoren, Geräten und Tastern bleibt erhalten.</p><button class="stop" data-action="reset-settings">Standardwerte wiederherstellen</button><p id="settings-reset-status" class="muted" role="status"></p></div><div class="card"><h2>Protokollierung</h2><p class="muted">Im Home-Assistant-Protokoll unter custom_components.ha_sauna. Die Stufe ist auch während einer Saunasitzung änderbar. Das Sitzungsarchiv bleibt unabhängig davon vollständig.</p><div class="row"><label for="log-level">Protokollstufe</label><select id="log-level"><option value="ERROR">ERROR · Fehler</option><option value="INFO">INFO · Betriebsereignisse (Standard)</option><option value="DEBUG">DEBUG · Detaillierte Diagnose</option></select><button data-action="logging" class="confirm">Übernehmen</button></div><p class="muted">INFO enthält Fehler, Warnungen, Zustandswechsel und Schaltbefehle. DEBUG ergänzt Messwerte und Ereignisprüfungen.</p><a href="/config/logs">Home-Assistant-Protokoll öffnen</a></div><div class="card"><h2>Sitzungsarchiv</h2><p class="muted">Alle empfangenen Messwerte, Sitzungsverläufe und Ereigniszuordnungen herunterladen.</p><button class="confirm" data-action="export">Archiv als ZIP herunterladen</button></div>`
        : "";
      this.$("#settings").innerHTML =
        `<div class="card"><h2>Saunataster</h2><div id="button-settings"></div></div><div class="card settings-programs"><h2>Programme</h2><p id="program-lock" class="muted" hidden></p><div id="program-library"></div></div>${admin ? this.appearanceSettingsMarkup() : ""}${administration}<footer class="settings-footer"><button data-action="default-page" class="confirm">Als Startseite festlegen</button><small>Für dein Home-Assistant-Profil</small><p id="start-page-status" class="muted" role="status"></p></footer>`;
      if (admin) this.$("#log-level").value = state.configuration.log_level;
      this.settingsEntry = this.entry;
      this.settingsAdmin = admin;
      this.programDraft = null;
      this.programEditor = null;
      this.programLibraryNeedsRender = true;
    }
    for (const d of state.parameters) {
      const input = this.$(`#parameters input[name="${d.key}"]`);
      if (!input) continue;
      input.disabled = !admin || (state.configuration_locked && !d.live_editable);
      if (!input.dataset.edited && this.shadowRoot.activeElement !== input)
        input.value =
          d.key === "target_temperature_c"
            ? state.target_temperature
            : (state.configuration.parameters[d.key] ?? "");
    }
    if (admin) {
      this.drawAppearanceStatus();
      this.$('[data-action="reset-settings"]').disabled = state.configuration_locked;
      this.$("#configuration-lock").textContent = state.configuration_locked
        ? "Grundeinstellungen sind nach Ende der Sitzung wieder änderbar. Solltemperatur, Endtemperatur und Verteilung bleiben verfügbar."
        : "Die Grundeinstellungen gelten für die nächste Sitzung.";
    }
    this.$("#program-lock").hidden = !state.configuration_locked;
    this.$("#program-lock").textContent =
      "Programme und Tasterwahl sind nach Ende der Sitzung wieder änderbar.";
    this.renderProgramLibrary();
    this.drawButtonProgram();
    if (this.hass.userData?.default_panel === "ha-sauna") {
      const button = this.$('[data-action="default-page"]');
      button.disabled = true;
      button.textContent = "Als Startseite festgelegt";
    }
  }
  async setDefaultPage() {
    const button = this.$('[data-action="default-page"]'),
      status = this.$("#start-page-status");
    button.disabled = true;
    status.textContent = "";
    try {
      const current = await this.hass.callWS({
        type: "frontend/get_user_data",
        key: "core",
      });
      await this.hass.callWS({
        type: "frontend/set_user_data",
        key: "core",
        value: { ...current.value, default_panel: "ha-sauna" },
      });
      button.textContent = "Als Startseite festgelegt";
      status.textContent =
        "Diese Übersicht ist als deine Home-Assistant-Startseite gespeichert. Ändern kannst du die Auswahl in deinem Profil.";
    } catch (error) {
      button.disabled = false;
      throw error;
    }
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
    return this.currentProgramDraft();
  }
  programCatalogDirty() {
    const signature = JSON.stringify(this.currentProgramDraft());
    return (
      signature !== this.programSavedSignature &&
      signature !== JSON.stringify(this.state.configuration.temperature_programs || [])
    );
  }
  openProgramEditor(id, isNew = false) {
    if (
      this.programEditor ||
      this.programSavePending ||
      this.state.configuration_locked ||
      !this.state.permissions?.program
    )
      return;
    const program = this.currentProgramDraft().find((item) => item.id === id);
    if (!program) return;
    this.programEditor = {
      id,
      isNew,
      kind: Array.isArray(program.temperature_steps) ? "steps" : "even",
      values: { ...program, temperature_steps: [...(program.temperature_steps || [])] },
    };
    this.programLibraryNeedsRender = true;
    this.renderProgramLibrary();
    this.shadowRoot
      ?.querySelectorAll?.("#program-library [data-program-id]")
      ?.forEach((row) => {
        if (row.dataset.programId === id)
          row.querySelector('[data-program-field="name"]')?.focus?.();
      });
  }
  updateProgramEditorField(input) {
    const editor = this.programEditor;
    if (!editor) return;
    if (input.matches("[data-program-step]"))
      editor.values.temperature_steps[Number(input.dataset.programStep)] = input.value;
    else editor.values[input.dataset.programField] = input.value;
  }
  finishProgramEditor() {
    const editor = this.programEditor;
    if (
      !editor ||
      this.programSavePending ||
      this.state.configuration_locked ||
      !this.state.permissions?.program
    )
      return;
    const { minimum, maximum, gangMinimum, gangMaximum } = this.programBounds(),
      name = String(editor.values.name || "").trim(),
      number = (value, label) => {
        if (String(value).trim() === "" || !Number.isFinite(Number(value)))
          throw Error(`${label} muss eine Zahl sein.`);
        const numeric = Number(value);
        if (numeric < minimum || numeric > maximum)
          throw Error(`${label} muss zwischen ${minimum} und ${maximum} °C liegen.`);
        return numeric;
      };
    if (!name) throw Error("Bitte einen Programmnamen eingeben.");
    let program;
    if (editor.kind === "steps") {
      const steps = editor.values.temperature_steps.map((value, index) =>
        number(value, `Stufe ${index + 1}`),
      );
      if (!steps.length || steps.length > gangMaximum)
        throw Error(`Bitte 1 bis ${gangMaximum} Temperaturstufen eingeben.`);
      program = { id: editor.id, name, temperature_steps: steps };
    } else {
      const distribution = Number(editor.values.distribution_gangs);
      if (
        String(editor.values.distribution_gangs).trim() === "" ||
        !Number.isInteger(distribution) ||
        distribution < gangMinimum ||
        distribution > gangMaximum
      )
        throw Error(
          `Die Verteilung muss zwischen ${gangMinimum} und ${gangMaximum} liegen.`,
        );
      program = {
        id: editor.id,
        name,
        start_c: number(editor.values.start_c, "Starttemperatur"),
        end_c: number(editor.values.end_c, "Endtemperatur"),
        distribution_gangs: distribution,
      };
    }
    this.programDraft = this.currentProgramDraft().map((item) =>
      item.id === editor.id ? program : item,
    );
    this.programEditor = null;
    this.programLibraryNeedsRender = true;
    this.renderProgramLibrary();
  }
  cancelProgramEditor() {
    const editor = this.programEditor;
    if (!editor) return;
    if (editor.isNew)
      this.programDraft = this.currentProgramDraft().filter(
        (item) => item.id !== editor.id,
      );
    this.programEditor = null;
    this.programLibraryNeedsRender = true;
    this.renderProgramLibrary();
  }
  catalogProgramForm(program, bounds, editable) {
    const kind = this.programEditor.kind,
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
    const protectedProgram = [
      this.state.configuration.selected_program_id,
      this.state.configuration.button_program,
    ].includes(program.id);
    return `<div class="program-form" data-program-kind="${kind}"><label class="field">Name<input data-program-field="name" value="${esc(program.name)}" ${disabled}></label>${selector}${values}<div class="row"><button type="button" class="confirm" data-action="program-finish" ${disabled}>Fertig</button><button type="button" data-action="program-cancel">Abbrechen</button><button type="button" data-action="program-remove:${esc(program.id)}" ${protectedProgram || !editable ? "disabled" : ""}>Entfernen</button></div></div>`;
  }
  renderProgramLibrary() {
    const target = this.$("#program-library");
    if (!target) return;
    const state = this.state,
      bounds = this.programBounds(),
      editable = !!state.permissions?.program && !state.configuration_locked,
      programs = this.currentProgramDraft(),
      editor = this.programEditor,
      busy = !!editor || !!this.programSavePending,
      dirty = this.programCatalogDirty();
    const signature = JSON.stringify(programs);
    if (
      target.dataset.editable === String(editable) &&
      (target.dataset.catalogSignature === signature || !!editor) &&
      !this.programLibraryNeedsRender
    )
      return;
    target.innerHTML = `<div class="program-list">${programs
      .map((program, index) => {
        const open = editor?.id === program.id,
          summary = Array.isArray(program.temperature_steps)
            ? `${program.temperature_steps.map((value) => num(value, 1)).join(" → ")} °C · Einzelne Stufen`
            : `${num(program.start_c, 1)} → ${num(program.end_c, 1)} °C · Verteilung: ${program.distribution_gangs} · Gleichmäßig`;
        return `<div class="program-row" data-program-id="${esc(program.id)}"><div class="program-row-summary"><button type="button" class="program-drag-handle" data-program-drag="${esc(program.id)}" aria-label="${esc(program.name)} verschieben, Position ${index + 1} von ${programs.length}; Pfeiltasten verwenden" ${editable && !busy ? "" : "disabled"}>☰</button><div class="program-row-label"><strong>${esc(program.name)}</strong><small>${esc(summary)}</small></div><button type="button" class="program-edit-button" data-action="program-edit:${esc(program.id)}" aria-label="${esc(program.name)} bearbeiten" ${editable && !busy ? "" : "disabled"}>Bearbeiten</button></div>${open ? this.catalogProgramForm(editor.values, bounds, editable) : ""}</div>`;
      })
      .join(
        "",
      )}</div><div class="program-actions"><button type="button" data-action="program-add" ${editable && !busy ? "" : "disabled"}>Programm hinzufügen</button><button type="button" data-action="program-save" class="confirm" ${editable && !busy && dirty ? "" : "disabled"}>Programme speichern</button><button type="button" data-action="program-discard" ${editable && !busy && dirty ? "" : "disabled"}>Änderungen verwerfen</button></div><p id="program-order-status" class="muted" role="status">${esc(this.programOrderStatus || "")}</p>`;
    target.dataset.editable = String(editable);
    target.dataset.catalogSignature = signature;
    this.programLibraryNeedsRender = false;
  }
  drawButtonProgram() {
    const configuration = this.state.configuration,
      editable = !!this.state.permissions?.program && !this.state.configuration_locked,
      button = configuration.button_program,
      bounds = this.temperatureBounds(),
      option = (value, label) =>
        `<option value="${esc(value)}" ${button === value ? "selected" : ""}>${esc(label)}</option>`;
    this.updateMarkup(
      "#button-settings",
      `<div class="row"><label class="field" for="button-program">Beim Einschalten<select id="button-program" ${editable ? "" : "disabled"}>${option("constant", "Konstant")}${configuration.temperature_programs.map((program) => option(program.id, program.name)).join("")}</select></label>${button === "constant" ? `<label class="field" for="button-temperature">Temperatur (°C)<input id="button-temperature" type="number" min="${bounds?.minimum}" max="${bounds?.maximum}" step="${this.temperatureStep()}" value="${configuration.button_temperature_c}" ${editable ? "" : "disabled"}></label>` : ""}</div>`,
    );
  }
  newProgramId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const random = new Uint32Array(2);
    globalThis.crypto?.getRandomValues?.(random);
    return `program-${Date.now().toString(36)}-${random[0].toString(36)}${random[1].toString(36)}`;
  }
  addProgram() {
    if (
      this.programEditor ||
      this.programSavePending ||
      this.state.configuration_locked ||
      !this.state.permissions?.program
    )
      return;
    const bounds = this.programBounds(),
      programs = this.currentProgramDraft(),
      id = this.newProgramId();
    programs.push({
      id,
      name: "Neues Programm",
      start_c: bounds.minimum,
      end_c: bounds.minimum,
      distribution_gangs: Math.max(1, bounds.gangMinimum),
    });
    this.programDraft = programs;
    this.openProgramEditor(id, true);
  }
  removeProgram(id) {
    if (
      this.state.configuration_locked ||
      this.programSavePending ||
      !this.state.permissions?.program ||
      this.programEditor?.id !== id ||
      [
        this.state.configuration.selected_program_id,
        this.state.configuration.button_program,
      ].includes(id)
    )
      return;
    this.programDraft = this.currentProgramDraft().filter(
      (program) => program.id !== id,
    );
    this.programEditor = null;
    this.programLibraryNeedsRender = true;
    this.renderProgramLibrary();
  }
  moveProgram(id, offset, targetIndex = null) {
    if (
      this.programEditor ||
      this.programSavePending ||
      this.state.configuration_locked ||
      !this.state.permissions?.program
    )
      return;
    const programs = [...this.currentProgramDraft()],
      oldIndex = programs.findIndex((program) => program.id === id),
      nextIndex = targetIndex ?? oldIndex + offset;
    if (
      oldIndex < 0 ||
      nextIndex < 0 ||
      nextIndex >= programs.length ||
      oldIndex === nextIndex
    )
      return;
    programs.splice(nextIndex, 0, ...programs.splice(oldIndex, 1));
    this.programDraft = programs;
    this.programOrderStatus = `${programs[nextIndex].name}, Position ${nextIndex + 1} von ${programs.length}`;
    this.programLibraryNeedsRender = true;
    this.renderProgramLibrary();
    [...(this.shadowRoot?.querySelectorAll?.("[data-program-drag]") || [])]
      .find((handle) => handle.dataset.programDrag === id)
      ?.focus?.();
  }
  beginProgramDrag(event) {
    const handle = event.target.closest?.("[data-program-drag]");
    if (
      !handle ||
      handle.disabled ||
      this.programEditor ||
      this.state.configuration_locked
    )
      return;
    const id = handle.dataset.programDrag,
      from = this.currentProgramDraft().findIndex((program) => program.id === id);
    if (from < 0) return;
    this.programDrag = { id, from, to: from, pointerId: event.pointerId, handle };
    handle.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }
  updateProgramDrag(event) {
    const drag = this.programDrag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag.clientX = event.clientX;
    drag.clientY = event.clientY;
    const row = this.shadowRoot
      .elementFromPoint?.(event.clientX, event.clientY)
      ?.closest?.("[data-program-id]");
    this.shadowRoot
      .querySelectorAll(".program-row")
      .forEach((item) => item.removeAttribute("data-drop-position"));
    if (row) {
      const programs = this.currentProgramDraft(),
        index = programs.findIndex((program) => program.id === row.dataset.programId),
        rect = row.getBoundingClientRect(),
        after = event.clientY >= rect.top + rect.height / 2;
      if (index >= 0) {
        drag.to = Math.max(
          0,
          Math.min(
            programs.length - 1,
            index +
              (after && index < drag.from ? 1 : !after && index > drag.from ? -1 : 0),
          ),
        );
        row.dataset.dropPosition = after ? "after" : "before";
      }
    }
    const edge = 48,
      host = this.getBoundingClientRect?.();
    drag.edgeDirection =
      host && event.clientY < host.top + edge
        ? -1
        : host && event.clientY > host.bottom - edge
          ? 1
          : 0;
    if (drag.edgeDirection && !drag.scrollFrame) this.scrollProgramDrag();
    event.preventDefault();
  }
  scrollProgramDrag() {
    const drag = this.programDrag;
    if (!drag || !drag.edgeDirection) return;
    drag.scrollFrame = globalThis.requestAnimationFrame?.(() => {
      drag.scrollFrame = null;
      if (this.programDrag !== drag || !drag.edgeDirection) return;
      this.scrollTop += drag.edgeDirection * 18;
      this.updateProgramDrag({
        pointerId: drag.pointerId,
        clientX: drag.clientX,
        clientY: drag.clientY,
        preventDefault() {},
      });
    });
  }
  finishProgramDrag(event) {
    const drag = this.programDrag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    this.cancelProgramDrag();
    this.moveProgram(drag.id, 0, drag.to);
  }
  cancelProgramDrag() {
    const drag = this.programDrag;
    if (!drag) return;
    if (drag.scrollFrame) globalThis.cancelAnimationFrame?.(drag.scrollFrame);
    drag.handle.releasePointerCapture?.(drag.pointerId);
    this.shadowRoot
      .querySelectorAll(".program-row")
      .forEach((item) => item.removeAttribute("data-drop-position"));
    this.programDrag = null;
  }
  discardPrograms() {
    if (this.programEditor || this.programSavePending) return;
    this.programDraft = null;
    this.programOrderStatus = "";
    this.programLibraryNeedsRender = true;
    this.renderProgramLibrary();
  }
  async savePrograms() {
    if (
      this.programEditor ||
      this.programSavePending ||
      this.state.configuration_locked ||
      !this.state.permissions?.program ||
      !this.programCatalogDirty()
    )
      return;
    const entry = this.entry,
      generation = this.generation,
      programs = this.currentProgramDraft(),
      pending = { entry, generation };
    this.programSavePending = pending;
    this.programLibraryNeedsRender = true;
    this.renderProgramLibrary();
    try {
      const result = await this.api(`/${entry}/programs`, "POST", { programs });
      if (
        this.entry !== entry ||
        this.generation !== generation ||
        this.programSavePending !== pending
      )
        return;
      this.programDraft = programs;
      this.programSavedSignature = JSON.stringify(programs);
      this.programSavedCatalogSignature = JSON.stringify(result?.programs || programs);
      this.programSavePending = null;
      this.programLibraryNeedsRender = true;
      this.renderProgramLibrary();
    } catch (error) {
      if (
        this.programSavePending !== pending ||
        this.entry !== entry ||
        this.generation !== generation
      )
        return;
      if (this.programSavePending === pending) {
        this.programSavePending = null;
        this.programLibraryNeedsRender = true;
        this.renderProgramLibrary();
      }
      throw error;
    }
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
  syncNavigation() {
    this.navigation ??= { main: "overview", detail: "detail" };
    if (this.navigation.main === "details" && !this.state?.permissions?.admin)
      this.navigation.main = "overview";
    const { main, detail } = this.navigation;
    const view =
      main === "details" ? (detail === "detail-history" ? "history" : detail) : main;
    this.view = view;
    const detailsButton = this.$('.main-tabs [data-action="details"]');
    if (detailsButton) detailsButton.hidden = !this.state?.permissions?.admin;
    const detailTabs = this.$(".detail-tabs");
    if (detailTabs) detailTabs.hidden = main !== "details";
    this.shadowRoot
      ?.querySelectorAll(".main-tabs button")
      ?.forEach((button) =>
        button.setAttribute("aria-selected", String(button.dataset.action === main)),
      );
    this.shadowRoot
      ?.querySelectorAll(".detail-tabs button")
      ?.forEach((button) =>
        button.setAttribute("aria-selected", String(button.dataset.action === detail)),
      );
    for (const [selector, hidden] of [
      ["#current", view !== "overview"],
      ["#details", view !== "detail"],
      ["#history", !["history", "diagnostics"].includes(view)],
      ["#settings", view !== "settings"],
      ["#plots", view === "diagnostics"],
      ["#detection-plots", view !== "diagnostics"],
      ["#gangs", view === "diagnostics"],
      ["#event-list", view === "diagnostics"],
    ]) {
      const node = this.$(selector);
      if (node) node.hidden = hidden;
    }
  }
  setPanelView(action) {
    this.navigation ??= { main: "overview", detail: "detail" };
    if (["detail", "detail-history", "diagnostics"].includes(action)) {
      this.navigation.main = "details";
      this.navigation.detail = action;
    } else this.navigation.main = action === "normal" ? "overview" : action;
    this.syncNavigation();
    if (!["history", "diagnostics"].includes(this.view)) {
      this.cancelHistoryFrame();
      this.historyLoad = null;
      this.historyChart?.interaction.hide();
    } else this.historyChart?.interaction.invalidateGeometry("size");
  }
  async action(action) {
    this.message(null);
    const permissions = this.state?.permissions || {};
    if (
      ((action === "operation" || action.startsWith("finish-session:")) &&
        !permissions.control) ||
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
        (!permissions.program || this.state.configuration_locked)) ||
      (action.startsWith("light:") && !permissions.light) ||
      (action === "manual-light-overview" &&
        (!permissions.admin || !permissions.light)) ||
      (action.startsWith("heater:") && !permissions.heater) ||
      (action.startsWith("control-mode:") &&
        (!permissions.control || this.state.configuration_locked)) ||
      (action.startsWith("end-phase:") && !permissions.admin) ||
      (action === "reset-settings" &&
        (!permissions.admin || this.state.configuration_locked)) ||
      (action.startsWith("appearance-") && !permissions.admin) ||
      ((action === "details" ||
        action === "detail" ||
        action === "detail-history" ||
        action === "diagnostics" ||
        action === "logging" ||
        action === "export") &&
        !permissions.admin)
    )
      return;
    if (action === "default-page") return this.setDefaultPage();
    if (action === "configure") return this.action("settings");
    if (action === "reset-settings") return this.resetSettings();
    if (action === "appearance-save") return this.saveAppearance();
    if (action === "appearance-discard") return this.resetAppearanceDraft();
    if (action === "appearance-default") return this.resetAppearanceDraft(true);
    if (action.startsWith("appearance-role-default:")) {
      const role = action.slice("appearance-role-default:".length);
      if (!this.appearanceCatalog().colors?.some((item) => item.id === role)) return;
      if (!this.appearanceDraft)
        this.updateAppearanceField(this.$(`[data-appearance-color="${role}"]`));
      delete this.appearanceDraft.colors[role];
      delete this.appearanceRaw?.[role];
      this.appearanceStatus = "Vorschau – noch nicht gespeichert";
      this.applyAppearance();
      this.drawCurrent();
      this.drawAppearanceStatus();
      this.scheduleHistoryRender("appearance");
      const input = this.$(`[data-appearance-color="${role}"]`);
      if (input)
        input.value =
          this.appearanceCatalog().colors.find((item) => item.id === role)?.default ||
          "";
      const picker = this.$(`[data-appearance-picker="${role}"]`);
      if (picker) picker.value = this.appearancePickerColor(role);
      return;
    }
    if (action === "program-toggle") {
      if (!this.state?.session || this.programDirty() || this.programRequest) return;
      this.programChoiceOpen = !this.programChoiceOpen;
      this.drawCurrent();
      return;
    }
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
    if (action === "details") return this.action(this.navigation?.detail || "detail");
    if (action === "normal") return this.action("overview");
    if (
      [
        "overview",
        "history",
        "detail",
        "detail-history",
        "diagnostics",
        "settings",
      ].includes(action)
    ) {
      if (action === "history") {
        this.historyDetail = false;
        this.positions = new Set(["upper"]);
      }
      if (action === "detail-history") {
        this.historyDetail = true;
        this.positions = new Set(["upper", "lower"]);
      }
      this.setPanelView(action);
      if (action === "settings") return this.drawSettings();
      if (["history", "detail-history", "diagnostics"].includes(action))
        return this.refresh(true);
      if (action === "detail") this.drawHistory();
      return;
    }
    if (action.startsWith("preset:")) return this.changeTarget(Number(action.slice(7)));
    if (action.startsWith("program-mode:")) {
      if (this.programRequest) return;
      this.selectProgramMode(
        action.slice(13),
        this.state.configuration.temperature_programs || [],
      );
      if (
        !this.state.session &&
        this.programChoice(this.state.configuration.temperature_programs || []) !==
          "individual"
      )
        await this.applyProgram();
      return;
    }
    if (action.startsWith("program-select:")) {
      if (this.programRequest) return;
      this.selectNamedProgram(
        action.slice(15),
        this.state.configuration.temperature_programs || [],
      );
      if (!this.state.session) await this.applyProgram();
      return;
    }
    if (action === "program-cancel-draft") {
      if (!this.programRequest) this.cancelProgramDraft();
      return;
    }
    if (action.startsWith("program-kind:")) {
      if (this.programRequest) return;
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
      const editor = this.programEditor;
      if (!editor || editor.id !== id) return;
      const program = editor.values;
      const steps =
        editor.kind === "steps"
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
      }
      editor.kind = kind;
      this.programLibraryNeedsRender = true;
      this.renderProgramLibrary();
      return;
    }
    if (action === "program-apply") return this.applyProgram();
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
    if (action.startsWith("program-edit:"))
      return this.openProgramEditor(action.slice(13));
    if (action === "program-finish") return this.finishProgramEditor();
    if (action === "program-cancel") return this.cancelProgramEditor();
    if (action === "program-discard") return this.discardPrograms();
    if (action.startsWith("program-remove:")) {
      this.removeProgram(action.slice(15));
      return;
    }
    if (action === "program-save") return this.savePrograms();
    if (action === "button-program") {
      const profile = this.$("#button-program").value,
        payload = { profile };
      if (profile === "constant") {
        const input = this.$("#button-temperature"),
          value = input
            ? Number(input.value)
            : this.state.configuration.button_temperature_c,
          bounds = this.temperatureBounds();
        if (
          !bounds ||
          (input && !input.value.trim()) ||
          !Number.isFinite(value) ||
          value < bounds.minimum ||
          value > bounds.maximum
        )
          throw Error("Temperatur innerhalb der zulässigen Grenzen eingeben");
        payload.temperature_c = value;
      }
      await this.api(`/${this.entry}/button-program`, "POST", payload);
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
    if (action === "manual-light-overview") {
      const value = Number(this.$("#manual-light-value-overview").value);
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
    if (action.startsWith("finish-session:")) {
      const token = action.slice("finish-session:".length);
      await this.api(`/${this.entry}/finish-session`, "POST", {
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
      if (svg) {
        const rect = svg.getBoundingClientRect();
        this.zoomAt(factor, rect.left + rect.width / 2, svg);
      } else {
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
