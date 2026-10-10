/* Native HA custom panel. Backend objects are authoritative; no control model here. */
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
const unitInput = (input, unit) =>
  `<span class="number-input">${input}<span class="input-unit" aria-hidden="true">${esc(unit)}</span></span>`;
const equalPresetColumns = (count, maximum) => {
  for (let columns = Math.min(count, maximum); columns > 1; columns--)
    if (count % columns === 0) return columns;
  return 1;
};
const HISTORY_PLOT = Object.freeze({ width: 1200, left: 65, right: 1135 });
const stamp = (v) => (v ? new Date(v).getTime() : null);
const orderedHistoryEvents = (items = []) =>
  items
    .map((event, index) => ({ event, index, at: stamp(event.effective_at) }))
    .sort((a, b) => {
      const left = Number.isFinite(a.at) ? a.at : Infinity;
      const right = Number.isFinite(b.at) ? b.at : Infinity;
      return left === right ? a.index - b.index : left - right;
    })
    .map(({ event }) => event);
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
const duration = (seconds, mode = "elapsed") => {
  if (!Number.isFinite(seconds)) return "–";
  if (seconds > 0 && seconds < 60) return "unter 1 Minute";
  const minutes = Math[mode === "remaining" ? "ceil" : "floor"](
    Math.max(0, seconds) / 60,
  );
  return `${num(minutes, 0)} ${minutes === 1 ? "Minute" : "Minuten"}`;
};
const phases = {
  aus: "Aus",
  aufheizen: "Aufheizen",
  bereit: "Bereit",
  saunagang: "Saunagang",
  nachlauf: "Ofenkühlung",
  zwangskühlung: "Zwangskühlung (historisch)",
  manuell: "Manuell",
};
const phaseAppearance = {
  aus: { color: "phase_idle", shape: "phase-other" },
  aufheizen: { color: "phase_warmup", shape: "heat" },
  bereit: { color: "phase_ready", shape: "ready" },
  saunagang: { color: "phase_session", shape: "gang" },
  nachlauf: { color: "phase_cooling", shape: "after" },
  zwangskühlung: { color: "phase_forced_cooling", shape: "cool" },
  manuell: { color: "phase_idle", shape: "phase-other" },
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
  presence_confirmed: "Gangbeginn · Präsenz nach Türvorgang",
  presence_ended: "Gangende · Abwesenheit nach Türvorgang",
};
const gangConfirmed = (g) =>
  g.recognition_kind === "presence_confirmed" || !!g.infusion_events?.length;
const signalText = {
  door_heating: "Temperaturabfall trotz Heizen",
  door_close: "Türschließung",
  door_open: "Türöffnung",
  door_open_loss: "Mindesttemperaturverlust der Türöffnung",
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
const appearanceBlend = (background, foreground, alpha) => {
  const base = appearanceRgb(background),
    overlay = appearanceRgb(foreground);
  return base && overlay
    ? `#${base
        .map((channel, index) =>
          Math.round(channel * (1 - alpha) + overlay[index] * alpha)
            .toString(16)
            .padStart(2, "0"),
        )
        .join("")
        .toUpperCase()}`
    : null;
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

// User-facing history joins available observations across reporting gaps.
// Original missing/stale observations remain available to hover and exports.
const historyMeasurementTtlSeconds = (panel, session) =>
  Number(
    session?.measurement_ttl_seconds ??
      panel.state?.measurement_ttl_seconds ??
      session?.configuration?.parameters?.sensor_timeout_seconds ??
      panel.state?.configuration?.parameters?.sensor_timeout_seconds,
  );
const historySegments = (values) => {
  const available = values.filter((point) => point.value != null);
  return available.length ? [available] : [];
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
  constructor(canvas, { Path2DClass = globalThis.Path2D, styles = {} } = {}) {
    if (!Path2DClass) throw Error("HistoryCurves requires Path2D");
    this.canvas = canvas;
    this.Path2DClass = Path2DClass;
    this.styles = styles;
    this.mainPaths = new Map();
    this.mainPaintKey = null;
    this.canvasGeometry = null;
  }

  curveStyle(position, quantity, supplied = {}) {
    const defaultStyle = {
      stroke: this.styles[`${position}:${quantity}`]?.stroke,
      lineWidth: 3.5,
      globalAlpha: 1,
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

  resizeCanvas(canvas, geometry) {
    if (!canvas || !geometry) return false;
    const width = Math.max(1, Number(geometry.width)),
      height = Math.max(1, Number(geometry.height)),
      dpr = Math.max(1, Number(geometry.dpr) || 1),
      bitmapWidth = Math.round(width * dpr),
      bitmapHeight = Math.round(height * dpr),
      previous = this.canvasGeometry,
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
      this.canvasGeometry = state;
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

  update(snapshot, geometry) {
    const mainGeometry = geometry;
    const resized = this.resizeCanvas(this.canvas, mainGeometry);
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
    return { mainDrawn };
  }
}

/*
 * Persistent history-chart interaction layer.
 *
 * This is a source fragment: panel.js embeds it in its own scope, where
 * `clock` and `num` provide the shared compact display rules.
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
    typeof clock === "function"
      ? clock(value, timeZone)
      : new Date(value).toLocaleTimeString("de-DE", {
          hour: "2-digit",
          minute: "2-digit",
        });
  const sourceValue = (value) => `${typeof value}:${String(value)}`;
  const rowText = (nearest, quantity) => {
    const source = nearest.source;
    const cacheable = source && typeof source === "object";
    const key = [quantity, sourceValue(nearest.value)].join("|");
    const cached = cacheable ? state.rowTextCache.get(source) : null;
    if (cached?.key === key) return cached.value;
    const unit = quantity === "temperature" ? "°C" : "%";
    const value = `${number(nearest.value, 1)} ${unit}`;
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
    const status = document.createElement("div");
    const eventList = document.createElement("div");
    heading.className = "history-tooltip-time";
    rows.className = "history-tooltip-values";
    status.className = "history-tooltip-status";
    eventList.className = "history-tooltip-events";
    heading.textContent = "–";
    status.textContent = "Saunastatus · –";
    eventList.hidden = true;
    const series = new Map();
    for (const position of ["upper", "lower"])
      for (const quantity of ["temperature", "humidity"]) {
        const row = document.createElement("div");
        const label = document.createElement("span");
        const value = document.createElement("span");
        label.style.color = "var(--sauna-card-text, inherit)";
        label.setAttribute("data-quantity", quantity);
        label.textContent = `${quantity === "temperature" ? "Temperatur" : "Luftfeuchte"}${panel.historyDetail ? ` ${position === "upper" ? "oben" : "unten"}` : ""}: `;
        value.textContent = "–";
        row.append(label, value);
        row.hidden = !new Set(panel.positions || ["upper"]).has(position);
        rows.append(row);
        series.set(`${position}:${quantity}`, { row, label, value });
      }
    tooltip.replaceChildren(heading, rows, status, eventList);
    tooltip.setAttribute("data-phase", "");
    tooltip.hidden = true;
    state.tooltip = { heading, series, status, eventList };
    return state.tooltip;
  };
  const setText = (node, text) => {
    if (node.textContent !== text) node.textContent = text;
  };
  const hide = () => {
    if (state.disposed) return;
    if (tooltip) {
      tooltip.hidden = true;
      tooltip.setAttribute("data-phase", "");
    }
    if (cursor && state.cursorVisible) cursor.setAttribute("visibility", "hidden");
    state.tooltipVisible = false;
    state.cursorVisible = false;
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
    if (point.x < left || point.x > right || point.y < 18 || point.y > 435)
      return hide();
    const time = start + ((point.x - left) / (right - left)) * (end - start);
    const ttl = historyMeasurementTtlSeconds(panel, panel.shown?.session) * 1000;
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
          `${quantity === "temperature" ? "Temperatur" : "Luftfeuchte"}${panel.historyDetail ? ` ${position === "upper" ? "oben" : "unten"}` : ""}: `,
        );
        setText(row.value, rowText(nearest, quantity));
        if (row.row.hidden) row.row.hidden = false;
        visible.add(key);
      }
    for (const [key, row] of nodes.series)
      if (!visible.has(key) && !row.row.hidden) row.row.hidden = true;
    setText(nodes.heading, dateLabel(time, timeZone));
    const phase = panel.historyPhaseAt?.(time);
    setText(nodes.status, `Saunastatus · ${phases[phase?.phase] || "Unbekannt"}`);
    tooltip.setAttribute("data-phase", phase?.phase || "");
    // Match the cursor to event markers within six physical screen pixels.
    const eventTolerance =
      ((end - start) / (right - left)) *
      ((6 * 1200) / Math.max(1, geometry.rects.surface.width));
    const nearbyEvents = panel.historyEventsAt?.(time, eventTolerance) || [];
    const eventKey = JSON.stringify([
      timeZone,
      nearbyEvents.map((item) => [item.event_id, item.effective_at, item.kind]),
    ]);
    if (nodes.eventKey !== eventKey) {
      const eventRows = nearbyEvents.map((item) => {
        const row = document.createElement("div");
        row.className = "history-tooltip-event";
        const at = document.createElement("time");
        at.textContent = clock(item.effective_at, timeZone);
        const label = document.createElement("span");
        label.textContent = events[item.kind] || item.kind;
        row.append(at, label);
        return row;
      });
      nodes.eventList.replaceChildren(...eventRows);
      nodes.eventKey = eventKey;
    }
    nodes.eventList.hidden = nearbyEvents.length === 0;
    if (!state.tooltipVisible) tooltip.hidden = false;
    state.tooltipVisible = true;
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
    this.domain = panel.historyDomain();
    panel.$("#plots").innerHTML = panel.historyMarkup(panel.shown.session);
    const overview = panel.$("#history-overview");
    overview.innerHTML = `<svg viewBox="0 0 ${HISTORY_PLOT.width} 46" preserveAspectRatio="none" role="slider" tabindex="0" aria-label="Zeitausschnitt der Saunasitzung" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><rect class="overview-window" data-history-window x="${HISTORY_PLOT.left}" y="5" height="34" rx="4"/><rect class="overview-handle" data-history-handle="start" x="${HISTORY_PLOT.left - 4}" y="2" width="8" height="40" rx="3"/><rect class="overview-handle" data-history-handle="end" x="${HISTORY_PLOT.right - 4}" y="2" width="8" height="40" rx="3"/></svg>`;
    this.surface = panel.$("svg.session-chart");
    this.curves = new HistoryCurves(panel.$("canvas.history-curves"), {
      styles: panel.historyCurveStyles(),
    });
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
      model = panel.historyModel(this, session, geometry.canvas.cssWidth);
    this.model = model;
    panel.historyDetail = false;
    const chromeKey = panel.historyTitle(session);
    const chromeChanged = this.chromeKey !== chromeKey;
    this.chromeKey = chromeKey;
    this.surface.setAttribute(
      "aria-label",
      `Sitzungsverlauf: Temperatur und Luftfeuchte${panel.historyDetail ? " beider Messhöhen" : ""}`,
    );
    const timeZone = localTimeZone();
    const axisKey = `${model.start}:${model.end}:${model.low}:${model.high}:${model.humidityHigh}:${timeZone}:${geometry.canvas.cssWidth}:${geometry.canvas.cssHeight}`;
    if (axisKey !== this.axisKey) {
      panel.$("[data-history-axes]").innerHTML = panel.historyAxes(model, {
        width: geometry.canvas.cssWidth,
        height: geometry.canvas.cssHeight,
      });
      this.axisKey = axisKey;
    }
    const phaseKey = `${model.start}:${model.end}:${panel.historyTimelineRevision}:${panel.historyRecords("phase").length}:${session.ended_at || panel.state.now}:${JSON.stringify(panel.shown.phase_projection || null)}:${timeZone}`;
    if (phaseKey !== this.phaseKey) {
      panel.$("[data-history-annotations]").innerHTML = panel.historyAnnotations(
        model,
        session,
        gangs,
      );
      this.phaseKey = phaseKey;
    }
    const legend = panel.$("#history-legends");
    const legendMarkup = panel.historyLegendMarkup();
    if (legend && legendMarkup !== this.legendMarkup) {
      legend.innerHTML = legendMarkup;
      this.legendMarkup = legendMarkup;
    }
    // Revealing the height controls or changing a wrapped heading can move
    // the plot without resizing it. Read the new position in the next frame.
    if (chromeChanged) this.interaction.invalidateGeometry("layout");
    this.curves.update(model, {
      width: geometry.canvas.cssWidth,
      height: geometry.canvas.cssHeight,
      dpr: geometry.dpr,
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
  }
}

class SaunaSelectMenu {
  constructor(panel) {
    this.panel = panel;
    this.root = panel.shadowRoot;
    this.document = panel.ownerDocument;
    this.window = this.document.defaultView;
    this.listeners = [];
    this.models = new Map();
    this.triggers = new WeakMap();
    this.serial = 0;
    this.search = "";
  }
  connect() {
    if (this.listeners.length) return;
    const listen = (target, type, handler, options = true) => {
      target.addEventListener(type, handler, options);
      this.listeners.push(() => target.removeEventListener(type, handler, options));
    };
    const selectFrom = (event) =>
      event
        .composedPath()
        .map((node) => this.triggers.get(node))
        .find(Boolean);
    listen(this.root, "pointerdown", (event) => {
      if (this.menu && event.composedPath().includes(this.menu)) event.preventDefault();
    });
    listen(this.root, "click", (event) => {
      let select = selectFrom(event);
      if (
        !select &&
        !event
          .composedPath()
          .some((node) => ["BUTTON", "A", "INPUT", "TEXTAREA"].includes(node.tagName))
      ) {
        const label = event.composedPath().find((node) => node.tagName === "LABEL");
        if (this.models.has(label?.control)) select = label.control;
      }
      if (select) {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!this.supports(select)) return;
        this.models.get(select).trigger.focus({ preventScroll: true });
        this.select === select ? this.close() : this.open(select);
      } else {
        const option = event
          .composedPath()
          .find((node) => node.dataset?.selectIndex != null);
        if (option && this.menu?.contains(option))
          this.choose(Number(option.dataset.selectIndex));
      }
    });
    listen(this.root, "keydown", (event) => {
      const select = selectFrom(event);
      if (this.supports(select)) this.key(event, select);
    });
    for (const type of ["input", "change"])
      listen(this.root, type, (event) => {
        if (!this.models.has(event.target)) return;
        this.syncAttributes(event.target);
        if (event.target === this.select) this.close();
      });
    const outside = (event) => {
      if (
        this.select &&
        !event.composedPath().includes(this.models.get(this.select)?.trigger) &&
        !event.composedPath().includes(this.menu)
      )
        this.close();
    };
    listen(this.document, "pointerdown", outside);
    listen(this.document, "focusin", outside);
    for (const target of [this.document, this.root])
      listen(target, "scroll", (event) => {
        if (this.select && event.target !== this.menu) this.place();
      });
    listen(this.window, "resize", () => this.place());
    if (this.window.visualViewport) {
      listen(this.window.visualViewport, "resize", () => this.place());
      listen(this.window.visualViewport, "scroll", () => this.place());
    }
    this.observer = new MutationObserver((records) => {
      this.sync();
      if (this.select && records.some((record) => this.select.contains(record.target)))
        this.render();
    });
    this.observer.observe(this.root, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: [
        "disabled",
        "hidden",
        "label",
        "value",
        "selected",
        "class",
        "style",
        "aria-label",
        "aria-labelledby",
        "aria-describedby",
      ],
    });
    this.sync();
  }
  disconnect() {
    this.close();
    this.observer?.disconnect();
    this.listeners.splice(0).forEach((remove) => remove());
    for (const select of this.models.keys()) this.restore(select);
  }
  supports(select) {
    return !!select && !select.disabled && !select.multiple && select.size <= 1;
  }
  isTrigger(node) {
    return this.triggers.has(node);
  }
  hasFocus(select) {
    const trigger = this.models.get(select)?.trigger;
    return !!trigger && this.root.activeElement === trigger;
  }
  restore(select) {
    const record = this.models.get(select);
    if (!record) return;
    if (this.select === select) this.close();
    for (const [name, value] of record.attributes)
      value == null ? select.removeAttribute(name) : select.setAttribute(name, value);
    select.removeAttribute("data-sauna-select-model");
    record.trigger.remove();
    this.triggers.delete(record.trigger);
    this.models.delete(select);
  }
  sync() {
    for (const select of this.models.keys())
      if (!this.root.contains(select) || select.multiple || select.size > 1)
        this.restore(select);
    for (const select of this.root.querySelectorAll("select")) {
      if (select.multiple || select.size > 1) continue;
      if (!this.models.has(select)) {
        const trigger = this.document.createElement("button");
        trigger.type = "button";
        trigger.id = `sauna-select-trigger-${++this.serial}`;
        trigger.setAttribute("role", "combobox");
        trigger.setAttribute("aria-haspopup", "listbox");
        this.models.set(select, {
          trigger,
          attributes: new Map(
            ["tabindex", "aria-hidden"].map((name) => [
              name,
              select.getAttribute(name),
            ]),
          ),
        });
        this.triggers.set(trigger, select);
      }
      const trigger = this.models.get(select).trigger;
      if (select.nextSibling !== trigger) {
        const focused = this.hasFocus(select);
        select.after(trigger);
        if (focused) trigger.focus({ preventScroll: true });
      }
      this.syncAttributes(select);
    }
    if (
      this.select &&
      (!this.supports(this.select) ||
        !this.models.get(this.select)?.trigger.getClientRects().length)
    )
      this.close();
  }
  setAttribute(node, name, value) {
    if (value == null) {
      if (node.hasAttribute(name)) node.removeAttribute(name);
    } else if (node.getAttribute(name) !== String(value))
      node.setAttribute(name, String(value));
  }
  label(select) {
    const labels = [...(select.labels || [])].map((label) => {
      const clone = label.cloneNode(true);
      clone
        .querySelectorAll("select, input, textarea, button")
        .forEach((control) => control.remove());
      return clone.textContent.trim();
    });
    return (
      select.getAttribute("aria-label") || labels.filter(Boolean).join(" ") || "Auswahl"
    );
  }
  available(option) {
    return (
      !option.disabled &&
      !option.hidden &&
      !option.parentElement?.disabled &&
      !option.parentElement?.hidden
    );
  }
  open(select) {
    if (!this.supports(select) || !this.models.has(select)) return;
    this.close();
    this.select = select;
    this.active = select.selectedIndex;
    this.menu = this.document.createElement("div");
    this.menu.className = "sauna-select-menu";
    this.menu.id = `sauna-select-menu-${++this.serial}`;
    this.menu.setAttribute("role", "listbox");
    this.menu.setAttribute("aria-label", this.label(select));
    this.menu.setAttribute("popover", "manual");
    this.root.append(this.menu);
    this.render();
    this.menu.showPopover?.();
    this.place();
    this.menu
      .querySelector('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }
  close(focus = false) {
    const select = this.select;
    this.select = null;
    this.menu?.remove();
    this.menu = null;
    if (select) {
      this.syncAttributes(select);
      const trigger = this.models.get(select)?.trigger;
      if (focus && trigger?.isConnected) trigger.focus({ preventScroll: true });
    }
    this.search = "";
  }
  syncAttributes(select) {
    const record = this.models.get(select);
    if (!record) return;
    const trigger = record.trigger;
    // The renderer may replace model attributes from the next markup.
    if (!select.hasAttribute("data-sauna-select-model"))
      for (const name of record.attributes.keys())
        record.attributes.set(name, select.getAttribute(name));
    this.setAttribute(select, "data-sauna-select-model", "");
    this.setAttribute(select, "tabindex", "-1");
    this.setAttribute(select, "aria-hidden", "true");
    this.setAttribute(
      trigger,
      "class",
      `${select.className} sauna-select-trigger`.trim(),
    );
    this.setAttribute(trigger, "style", select.getAttribute("style"));
    this.setAttribute(trigger, "aria-label", this.label(select));
    for (const name of ["aria-labelledby", "aria-describedby", "title"])
      this.setAttribute(trigger, name, select.getAttribute(name));
    this.setAttribute(trigger, "disabled", select.disabled ? "" : null);
    this.setAttribute(trigger, "hidden", select.hidden ? "" : null);
    const text = select.selectedOptions[0]?.label || "";
    if (trigger.textContent !== text) trigger.textContent = text;
    const expanded = this.select === select && !!this.menu;
    this.setAttribute(trigger, "aria-expanded", String(expanded));
    this.setAttribute(trigger, "aria-controls", expanded ? this.menu.id : null);
    this.setAttribute(
      trigger,
      "aria-activedescendant",
      expanded && this.active >= 0 ? `${this.menu.id}-${this.active}` : null,
    );
  }
  render() {
    if (!this.select) return;
    const options = [...this.select.options];
    if (!options[this.active] || !this.available(options[this.active]))
      this.active = options.findIndex((option) => this.available(option));
    let group = null;
    this.menu.replaceChildren();
    options.forEach((option, index) => {
      if (option.hidden || option.parentElement?.hidden) return;
      if (
        option.parentElement?.tagName === "OPTGROUP" &&
        option.parentElement !== group
      ) {
        group = option.parentElement;
        const heading = this.document.createElement("div");
        heading.className = "sauna-select-group";
        heading.textContent = group.label;
        this.menu.append(heading);
      }
      const row = this.document.createElement("div");
      row.id = `${this.menu.id}-${index}`;
      row.dataset.selectIndex = String(index);
      row.dataset.active = String(index === this.active);
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(index === this.select.selectedIndex));
      row.setAttribute("aria-disabled", String(!this.available(option)));
      row.textContent = option.label;
      this.menu.append(row);
    });
    this.syncAttributes(this.select);
    this.place();
    this.menu
      .querySelector('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }
  place() {
    if (!this.select || !this.menu) return;
    const rect = this.models.get(this.select).trigger.getBoundingClientRect();
    if (!rect.width || !rect.height) return this.close();
    const viewport = this.window.visualViewport;
    const width = viewport?.width || this.window.innerWidth,
      height = viewport?.height || this.window.innerHeight,
      left = viewport?.offsetLeft || 0,
      top = viewport?.offsetTop || 0;
    const gap = 4,
      below = top + height - rect.bottom - gap,
      above = rect.top - top - gap;
    const menuWidth = Math.min(
      Math.max(rect.width, this.menu.scrollWidth),
      width - gap * 2,
    );
    this.menu.style.width = `${menuWidth}px`;
    this.menu.style.maxHeight = `${Math.max(0, Math.max(above, below) - gap)}px`;
    const menuHeight = Math.max(
      0,
      Math.min(this.menu.scrollHeight, Math.max(above, below) - gap),
    );
    this.menu.style.left = `${Math.max(left + gap, Math.min(rect.left, left + width - menuWidth - gap))}px`;
    this.menu.style.top = `${below >= menuHeight ? rect.bottom + gap : Math.max(top + gap, rect.top - menuHeight - gap)}px`;
  }
  choose(index) {
    const select = this.select;
    if (!select || !select.options[index] || !this.available(select.options[index]))
      return;
    const changed = select.selectedIndex !== index;
    select.selectedIndex = index;
    this.close(true);
    if (changed) {
      select.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
      select.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    }
  }
  key(event, select) {
    if (event.key === "Tab") {
      if (this.select === select && this.active >= 0) this.choose(this.active);
      else this.close();
      return;
    }
    if (event.key === "Escape") {
      if (this.select) {
        event.preventDefault();
        this.close(true);
      }
      return;
    }
    if (event.key === "F4") {
      event.preventDefault();
      event.stopImmediatePropagation();
      this.select === select ? this.close(true) : this.open(select);
      return;
    }
    const navigation = ["ArrowDown", "ArrowUp", "Home", "End", "Enter", " "].includes(
      event.key,
    );
    const typing =
      event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
    if (!navigation && !typing) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const wasOpen = this.select === select;
    if (!wasOpen) this.open(select);
    if (event.key === "Enter" || event.key === " ") {
      if (wasOpen) this.choose(this.active);
      return;
    }
    const indexes = [...select.options].flatMap((option, index) =>
      this.available(option) ? [index] : [],
    );
    const current = indexes.indexOf(this.active);
    if (event.key === "Home") this.active = indexes[0];
    else if (event.key === "End") this.active = indexes.at(-1);
    else if (event.key === "ArrowDown")
      this.active = indexes[Math.min(indexes.length - 1, current + (wasOpen ? 1 : 0))];
    else if (event.key === "ArrowUp")
      this.active = indexes[Math.max(0, current - (wasOpen ? 1 : 0))];
    else if (typing) {
      const now = Date.now();
      this.search =
        now - (this.searchAt || 0) > 700 ? event.key : this.search + event.key;
      this.searchAt = now;
      const query = this.search.toLocaleLowerCase();
      const match = indexes.find((index) =>
        select.options[index].label.toLocaleLowerCase().startsWith(query),
      );
      if (match != null) this.active = match;
    }
    this.render();
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
    this.historyListStale = true;
    this.historyPendingFinalId = null;
    this.historyOptionsSignature = null;
    this.manualOverridesOpen = false;
    this.fullscreenKioskHandler = () => {
      if (!this.fullscreenKioskDispatching) this.fullscreenKioskOwned = false;
    };
    this.fullscreenChangeHandler = () => {
      this.syncFullscreenNavigation();
      this.historyChart?.interaction.invalidateGeometry("size");
      this.fitInstrumentReadouts();
    };
    this.visibilityHandler = () => {
      if (typeof document !== "undefined" && document.hidden) {
        clearTimeout(this.timer);
        this.timer = null;
        this.historyListStale = true;
        this.historyLoad = null;
        return;
      }
      if (this.isConnected && this._hass) this.start();
    };
  }
  set hass(value) {
    this._hass = value;
    if (this.isConnected && this._hass && !this.timer && !this.busy) this.start();
  }
  get hass() {
    return this._hass;
  }
  connectedCallback() {
    if (!this.$("main")) this.shell();
    this.selectMenu ??= new SaunaSelectMenu(this);
    this.selectMenu.connect();
    this.infoOutsideHandler ??= (event) => {
      if (
        !event.composedPath().some((node) => node.classList?.contains("program-info"))
      )
        this.closeInfo();
    };
    this.ownerDocument?.addEventListener("click", this.infoOutsideHandler, true);
    this.infoGeometryHandler ??= (event) => {
      if (!event.target?.classList?.contains("program-info-popup")) this.closeInfo();
    };
    this.shadowRoot.addEventListener?.("scroll", this.infoGeometryHandler, true);
    this.ownerDocument?.addEventListener("scroll", this.infoGeometryHandler, true);
    this.ownerDocument?.defaultView?.addEventListener(
      "resize",
      this.infoGeometryHandler,
    );
    this.ownerDocument?.defaultView?.visualViewport?.addEventListener(
      "resize",
      this.infoGeometryHandler,
    );
    this.ownerDocument?.defaultView?.visualViewport?.addEventListener(
      "scroll",
      this.infoGeometryHandler,
    );
    if (typeof MutationObserver !== "undefined") {
      this.infoObserver ??= new MutationObserver(() => this.syncInfo());
      this.infoObserver.observe(this.shadowRoot, { childList: true, subtree: true });
    }
    this.fullscreenDocument = this.ownerDocument;
    this.fullscreenDocument?.addEventListener(
      "fullscreenchange",
      this.fullscreenChangeHandler,
    );
    this.fullscreenDocument?.defaultView?.addEventListener(
      "hass-kiosk-mode",
      this.fullscreenKioskHandler,
    );
    this.syncFullscreenNavigation();
    if (typeof document !== "undefined")
      document.addEventListener("visibilitychange", this.visibilityHandler);
    if (this._hass) this.start();
  }
  disconnectedCallback() {
    this.selectMenu?.disconnect();
    this.ownerDocument?.removeEventListener("click", this.infoOutsideHandler, true);
    this.shadowRoot?.removeEventListener?.("scroll", this.infoGeometryHandler, true);
    this.ownerDocument?.removeEventListener("scroll", this.infoGeometryHandler, true);
    this.ownerDocument?.defaultView?.removeEventListener(
      "resize",
      this.infoGeometryHandler,
    );
    this.ownerDocument?.defaultView?.visualViewport?.removeEventListener(
      "resize",
      this.infoGeometryHandler,
    );
    this.ownerDocument?.defaultView?.visualViewport?.removeEventListener(
      "scroll",
      this.infoGeometryHandler,
    );
    this.infoObserver?.disconnect();
    this.closeInfo();
    clearTimeout(this.timer);
    this.fullscreenDocument?.removeEventListener(
      "fullscreenchange",
      this.fullscreenChangeHandler,
    );
    this.syncFullscreenKiosk(false);
    this.fullscreenDocument?.defaultView?.removeEventListener(
      "hass-kiosk-mode",
      this.fullscreenKioskHandler,
    );
    this.fullscreenDocument = null;
    if (typeof document !== "undefined")
      document.removeEventListener("visibilitychange", this.visibilityHandler);
    clearTimeout(this.programSavedTimer);
    this.programSavedTimer = null;
    this.programRequest = null;
    this.programPendingApply = null;
    this.appearanceRequest = null;
    this.appearanceStale = true;
    this.applyAppearance();
    this.programSaveState = null;
    this.programSavePending = null;
    this.cancelProgramDrag();
    this.cancelTemperatureDrag(undefined, false);
    clearTimeout(this.historyWheelTimer);
    this.timer = null;
    this.generation++;
    this.controlCommands = {};
    this.historyLoad = null;
    this.historyListStale = true;
    this.pendingEventFocus = null;
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
    clearTimeout(this.timer);
    this.timer = null;
    if (typeof document !== "undefined" && document.hidden) return;
    void this.refresh(true);
  }
  statusPollInterval() {
    const state = this.state,
      now = stamp(state?.now),
      lightAfterRun = stamp(state?.light_after_run?.ends_at);
    return state?.operation_enabled ||
      state?.session ||
      lightAfterRun > now ||
      state?.configuration?.control_mode === "manual" ||
      Object.keys(this.controlCommands || {}).length
      ? 2000
      : 10000;
  }
  scheduleRefresh() {
    clearTimeout(this.timer);
    this.timer = null;
    if (
      !this.isConnected ||
      !this._hass ||
      (typeof document !== "undefined" && document.hidden)
    )
      return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.refresh();
    }, this.statusPollInterval());
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
    this.selectMenu?.syncAttributes(current);
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
    const focused =
      this.shadowRoot.activeElement === current || this.selectMenu?.hasFocus(current);
    if (focused && current.nodeName === "SELECT") {
      this.patchAttributes(current, next);
      return current;
    }
    if (focused && ["INPUT", "TEXTAREA"].includes(current.nodeName)) {
      const value = current.value,
        checked = current.checked;
      this.patchAttributes(current, next);
      if (current.value !== value) current.value = value;
      if ("checked" in current && current.checked !== checked)
        current.checked = checked;
      return current;
    }
    this.patchAttributes(current, next);
    if (
      current.nodeName === "SELECT" &&
      this.optionSignature(current) === this.optionSignature(next)
    ) {
      if (current.value !== next.value) current.value = next.value;
      this.selectMenu?.syncAttributes(current);
      return current;
    }
    if (current.nodeName === "INPUT" || current.nodeName === "TEXTAREA") {
      if (current.value !== next.value) current.value = next.value;
      if ("checked" in current) current.checked = next.checked;
    }
    const oldChildren = [...current.childNodes].filter(
        (child) => !this.selectMenu?.isTrigger(child),
      ),
      used = new Set();
    let cursor = oldChildren[0] || null;
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
      while (cursor && this.selectMenu?.isTrigger(cursor)) cursor = cursor.nextSibling;
    }
    for (const child of oldChildren) if (!used.has(child)) child.remove();
    this.selectMenu?.syncAttributes(current);
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
    this.selectMenu?.sync();
    this.syncInfo();
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
  instrumentSelection(name) {
    return (
      this.appearanceValue().instruments?.[name] ??
      this.appearanceCatalog().instruments?.[name]?.default
    );
  }
  instrumentStyle(name) {
    const selected = this.instrumentSelection(name);
    return selected === "inherit" ? this.instrumentSelection("default") : selected;
  }
  instrumentDefaults() {
    return Object.fromEntries(
      Object.entries(this.appearanceCatalog().instruments || {}).map(
        ([name, definition]) => [name, definition.default],
      ),
    );
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
      focus: "--primary-color",
    }[role];
    const theme =
      typeof getComputedStyle === "function" ? getComputedStyle(this) : null;
    return (
      appearanceThemeHex(
        theme?.getPropertyValue(themeRole || "--primary-text-color"),
      ) ||
      this.appearanceColor("ui_accent") ||
      ""
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
      "upper:temperature": { stroke: temperature, lineDash: [] },
      "lower:temperature": { stroke: temperature, lineDash: [] },
      "upper:humidity": { stroke: humidity, lineDash: [] },
      "lower:humidity": { stroke: humidity, lineDash: [] },
    };
  }
  applyAppearance() {
    const style = this.style;
    if (!style?.setProperty) return;
    for (const [key, property] of Object.entries({
      control_button_height_px: "--sauna-button-height",
      primary_action_height_px: "--sauna-action-height",
      control_radius_px: "--sauna-control-radius",
      surface_radius_px: "--sauna-surface-radius",
      surface_gap_px: "--sauna-surface-gap",
      surface_padding_px: "--sauna-surface-padding",
      control_group_gap_px: "--sauna-control-gap",
    })) {
      const value = this.state?.frontend_defaults?.[key];
      if (Number.isFinite(value) && value > 0)
        style.setProperty(property, `${value}px`);
    }
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
    const darkSurface =
      surfaces.card && appearanceContrast(surfaces.card) === "#FFFFFF";
    const raisedSurface = appearanceBlend(
      surfaces.card,
      "#FFFFFF",
      darkSurface ? 0.02 : 0,
    );
    const sectionSurface = appearanceBlend(
      surfaces.card,
      "#FFFFFF",
      darkSurface ? 0.075 : 0.55,
    );
    const recessedSurface = appearanceBlend(surfaces.page, surfaces.card, 0.4);
    const controlSurface = appearanceBlend(
      surfaces.card,
      "#FFFFFF",
      darkSurface ? 0.2 : 0.85,
    );
    for (const [name, value] of Object.entries({
      raised: raisedSurface,
      section: sectionSurface,
      recessed: recessedSurface,
      control: controlSurface,
    })) {
      if (value) style.setProperty(`--sauna-surface-${name}`, value);
    }
    if (raisedSurface) surfaces.card = raisedSurface;
    const readable = (proposed, surface, ratio) => {
      if (!proposed || !surface || appearanceContrastRatio(proposed, surface) >= ratio)
        return proposed;
      return appearanceContrastRatio("#000000", surface) >=
        appearanceContrastRatio("#FFFFFF", surface)
        ? "#000000"
        : "#FFFFFF";
    };
    const proposedText =
      this.appearanceColor("text") ||
      appearanceThemeHex(theme?.getPropertyValue("--primary-text-color"));
    const proposedFocus =
      this.appearanceColor("focus") || this.appearancePickerColor("focus");
    const tintedSurface = (base, role, amount) =>
      appearanceBlend(base, this.appearanceColor(role), amount);
    for (const [name, role, amount] of [
      ["info", "status_info", 0.12],
      ["warning", "status_warning", 0.12],
      ["error", "status_error", 0.12],
      ["unknown", "status_unknown", 0.12],
      ["heater-on", "ui_heater_on", 0.16],
      ["heater-off", "ui_heater_off", 0.16],
    ]) {
      const surface = tintedSurface(surfaces.card, role, amount);
      if (surface) style.setProperty(`--sauna-tint-${name}-background`, surface);
      else style.removeProperty?.(`--sauna-tint-${name}-background`);
      const ink = surface && readable(proposedText, surface, 4.5);
      const focus = surface && readable(proposedFocus, surface, 3);
      if (ink) style.setProperty(`--sauna-tint-${name}-text`, ink);
      else style.removeProperty?.(`--sauna-tint-${name}-text`);
      if (focus) style.setProperty(`--sauna-tint-${name}-focus`, focus);
      else style.removeProperty?.(`--sauna-tint-${name}-focus`);
    }
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
    for (const [surfaceName, surface] of Object.entries(surfaces)) {
      const focus = readable(proposedFocus, surface, 3);
      if (focus) style.setProperty(`--sauna-${surfaceName}-focus`, focus);
    }
    const chartFocus = readable(
      proposedFocus,
      this.appearanceColor("chart_background"),
      3,
    );
    if (chartFocus) style.setProperty("--sauna-chart-focus", chartFocus);
    const statusRoles = [
      "ui_heater_on",
      "ui_heater_off",
      "ui_heater_unknown",
      "status_info",
      "status_success",
      "status_warning",
      "status_error",
      "status_unknown",
    ];
    for (const role of statusRoles) {
      const ink = readable(this.appearanceColor(role), surfaces.card, 4.5);
      if (ink) style.setProperty(`--sauna-ink-${role.replaceAll("_", "-")}`, ink);
    }
    const accent = this.appearanceColor("ui_accent");
    for (const state of ["on", "off"]) {
      const color = this.appearanceColor(`ui_feedback_${state}`);
      if (color) {
        const surface = color;
        style.setProperty(`--sauna-feedback-${state}-surface`, surface);
        style.setProperty(
          `--sauna-feedback-${state}-ink`,
          readable(proposedText, surface, 4.5),
        );
        style.setProperty(
          `--sauna-feedback-${state}-focus`,
          readable(proposedFocus, recessedSurface, 3),
        );
      }
    }
    if (accent) {
      style.setProperty("--sauna-accent-ink", appearanceContrast(accent));
      const readableAccent = readable(accent, surfaces.card, 4.5);
      if (readableAccent) style.setProperty("--sauna-accent-readable", readableAccent);
    }
    const command = this.appearanceColor("ui_command");
    if (command) {
      style.setProperty("--sauna-command-ink", appearanceContrast(command));
      style.setProperty("--sauna-command-focus", readable(proposedFocus, command, 3));
    } else {
      style.removeProperty?.("--sauna-command-ink");
      style.removeProperty?.("--sauna-command-focus");
    }
    const danger = this.appearanceColor("ui_danger");
    if (danger) {
      style.setProperty("--sauna-danger-ink", appearanceContrast(danger));
      const readableDanger = readable(danger, surfaces.card, 4.5);
      if (readableDanger) style.setProperty("--sauna-danger-text", readableDanger);
      style.setProperty("--sauna-danger-focus", readable(proposedFocus, danger, 3));
    }
    if (accent)
      style.setProperty("--sauna-accent-focus", readable(proposedFocus, accent, 3));
    const warning = this.appearanceColor("status_warning");
    if (warning)
      style.setProperty("--sauna-status-warning-ink", appearanceContrast(warning));
    const phase =
      this.state?.operation_enabled &&
      this.state?.configuration?.control_mode !== "manual" &&
      !this.appearanceStale
        ? phaseAppearance[this.state.phase]?.color
        : null;
    const tint = phase && this.appearanceColor(phase);
    const mainSurface = tint && appearanceBlend(surfaces.card, tint, 0.24);
    if (mainSurface) style.setProperty("--sauna-main-background", mainSurface);
    else style.removeProperty?.("--sauna-main-background");
    const mainSection = tint && appearanceBlend(sectionSurface, tint, 0.18);
    if (mainSection) style.setProperty("--sauna-main-section", mainSection);
    else style.removeProperty?.("--sauna-main-section");
    const pendingSurface = tintedSurface(
      mainSurface || surfaces.card,
      "status_info",
      0.06,
    );
    if (pendingSurface) style.setProperty("--sauna-pending-background", pendingSurface);
    else style.removeProperty?.("--sauna-pending-background");
    for (const [name, proposed, ratio] of [
      ["pending-text", proposedText, 4.5],
      ["pending-focus", proposedFocus, 3],
    ]) {
      const value = pendingSurface && readable(proposed, pendingSurface, ratio);
      if (value) style.setProperty(`--sauna-${name}`, value);
      else style.removeProperty?.(`--sauna-${name}`);
    }
    for (const role of statusRoles) {
      const ink = mainSurface && readable(this.appearanceColor(role), mainSurface, 4.5);
      const key = `--sauna-main-ink-${role.replaceAll("_", "-")}`;
      if (ink) style.setProperty(key, ink);
      else style.removeProperty?.(key);
    }
    for (const [role, themeKey] of [
      ["text", "--primary-text-color"],
      ["muted_text", "--secondary-text-color"],
      ["focus", "--primary-color"],
    ]) {
      const proposed =
        this.appearanceColor(role) ||
        appearanceThemeHex(theme?.getPropertyValue(themeKey));
      const value =
        mainSurface && readable(proposed, mainSurface, role === "focus" ? 3 : 4.5);
      if (value) style.setProperty(`--sauna-main-${role.replaceAll("_", "-")}`, value);
      else style.removeProperty?.(`--sauna-main-${role.replaceAll("_", "-")}`);
    }
    if (this.historyChart) this.historyChart.curves.styles = this.historyCurveStyles();
  }
  appearanceSettingsMarkup() {
    const catalog = this.appearanceCatalog();
    const colors = (catalog.colors || []).filter((item) => !item.hidden);
    const groups = [
      ...new Set(colors.filter((item) => !item.featured).map((item) => item.group)),
    ];
    const rows = (featured, group) =>
      colors
        .filter(
          (item) => !!item.featured === featured && (!group || item.group === group),
        )
        .map((item) => {
          const value = this.appearancePickerColor(item.id);
          const shown =
            this.appearanceRaw?.[item.id] ??
            this.appearanceValue().colors?.[item.id] ??
            item.default ??
            "";
          return `<div class="appearance-color-row"><label for="appearance-${esc(item.id)}">${esc(item.label)}</label><input type="color" data-appearance-picker="${esc(item.id)}" aria-label="${esc(item.label)} wählen" value="${esc(value)}"><input id="appearance-${esc(item.id)}" data-appearance-color="${esc(item.id)}" aria-label="${esc(item.label)} als Hexwert" inputmode="text" maxlength="7" placeholder="HA-Standard" value="${esc(shown)}"><button type="button" data-action="appearance-role-default:${esc(item.id)}" aria-label="${esc(item.label)} auf Standard setzen">Standard</button></div>`;
        })
        .join("");
    const scaleFields = ["temperature", "humidity"]
      .map((name) => {
        const label = name === "temperature" ? "Temperatur" : "Luftfeuchte",
          unit = name === "temperature" ? "°C" : "%";
        const scale =
          this.appearanceScale(name) || catalog.scales?.[name]?.default || {};
        return `<fieldset class="appearance-scale"><legend>${label}</legend>${["minimum", "maximum"].map((end) => `<label>${end === "minimum" ? "Minimum" : "Maximum"}${unitInput(`<input type="number" aria-label="${label} ${end === "minimum" ? "Minimum" : "Maximum"} (${unit})" step="any" data-appearance-scale="${name}.${end}" value="${esc(this.appearanceRaw?.[`${name}.${end}`] ?? scale[end] ?? "")}">`, unit)}</label>`).join("")}</fieldset>`;
      })
      .join("");
    const instrumentFields = Object.entries(catalog.instruments || {})
      .map(
        ([name, definition]) =>
          `<label>${esc(definition.label)}<select data-appearance-instrument="${esc(name)}">${definition.options.map((option) => `<option value="${esc(option)}" ${this.instrumentSelection(name) === option ? "selected" : ""}>${{ round: "Rund", linear: "Gerade", inherit: "Gemeinsame Vorgabe" }[option]}</option>`).join("")}</select></label>`,
      )
      .join("");
    return `<div class="card appearance-settings" id="appearance-settings"><h2>Instrumente, Farben und Skalen</h2><div class="appearance-instruments">${instrumentFields}</div><h3>Messgrößen</h3><div class="appearance-colors">${rows(true)}</div><h3>Anzeigeskalen</h3><div class="appearance-scales">${scaleFields}</div><details class="settings-group appearance-advanced"><summary>Erweiterte Farben</summary>${groups.map((group) => `<details class="expert-group"><summary>${esc(group)}</summary><div class="appearance-colors">${rows(false, group)}</div></details>`).join("")}</details><div class="row"><button type="button" class="confirm" data-action="appearance-save">Darstellung speichern</button><button type="button" data-action="appearance-discard">Änderungen verwerfen</button><button type="button" data-action="appearance-default">Standarddarstellung wiederherstellen</button></div><p id="appearance-status" role="status" class="muted"></p></div>`;
  }
  updateAppearanceField(input) {
    if (!this.state?.permissions?.admin || this.appearanceRequest) return;
    if (!this.appearanceDraft) {
      const saved = this.savedAppearance();
      this.appearanceDraft = {
        colors: { ...(saved.colors || {}) },
        instruments: { ...this.instrumentDefaults(), ...(saved.instruments || {}) },
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
    } else if (input.dataset.appearanceInstrument) {
      const name = input.dataset.appearanceInstrument;
      if (this.appearanceCatalog().instruments?.[name]?.options.includes(input.value))
        this.appearanceDraft.instruments[name] = input.value;
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
      ?.querySelectorAll?.("input,select,button")
      .forEach((element) => {
        element.disabled = !!this.appearanceRequest;
      });
  }
  syncAppearanceEditor() {
    if (this.appearanceDraft) return;
    for (const name of Object.keys(this.appearanceCatalog().instruments || {})) {
      const input = this.$(`[data-appearance-instrument="${name}"]`);
      if (
        input &&
        input !== this.shadowRoot.activeElement &&
        !this.selectMenu?.hasFocus(input)
      )
        input.value = this.instrumentSelection(name);
      this.selectMenu?.syncAttributes(input);
    }
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
          instruments: this.instrumentDefaults(),
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
        height: calc(100dvh - var(--safe-area-inset-top, 0px) - var(--safe-area-inset-bottom, 0px));
        min-height: 0;
        container-type: inline-size;
        overflow: hidden;
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
        --sauna-shadow-card: 0 3px 5px rgb(0 0 0 / .32), 0 14px 30px -8px rgb(0 0 0 / .48);
        --sauna-shadow-section: 0 2px 3px rgb(0 0 0 / .26), 0 7px 14px -5px rgb(0 0 0 / .36);
        --sauna-shadow-control: 0 2px 2px rgb(0 0 0 / .35), 0 4px 7px -2px rgb(0 0 0 / .30);
      }
      * {
        box-sizing: border-box;
      }
      main {
        max-width: 1280px;
        height: 100%;
        min-height: 0;
        display: flex;
        flex-direction: column;
        margin: auto;
        padding: 28px 32px 16px;
      }
      main > header, main > .detail-tabs, main > #message { flex: 0 0 auto; }
      main > section { flex: 1 1 0; min-height: 0; min-width: 0; overflow: auto; overscroll-behavior: contain; scrollbar-gutter: stable; padding: 4px; }
      header {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
        align-items: center;
        gap: 18px;
        margin-bottom: 24px;
        color: var(--sauna-page-text, var(--sauna-color-text, var(--primary-text-color)));
      }
      .header-brand {
        display: flex;
        align-items: center;
        gap: 14px;
      }
      .header-context {
        display: flex;
        align-items: center;
        gap: 10px;
        justify-self: end;
        min-width: 0;
      }
      .header-icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 10px;
        flex: 0 0 auto;
      }
      .header-icon svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
      .header-context :is(select, .sauna-select-trigger) {
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
        gap: var(--sauna-control-gap);
        align-items: center;
        flex-wrap: wrap;
      }
      button,
      select,
      input,
      .number-input {
        font: inherit;
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: var(--sauna-control-radius);
        padding: 9px 13px;
        background: var(--sauna-color-card-background, var(--card-background-color));
        color: var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)));
      }
      .number-input { display: flex; align-items: center; min-width: 0; padding: 0; }
      .number-input input { flex: 1 1 0; min-width: 0; width: 100%; border: 0; background: transparent; }
      .input-unit { flex: 0 0 auto; padding-inline-end: 13px; color: var(--sauna-card-muted-text, var(--secondary-text-color)); white-space: nowrap; }
      .number-input:has(input:focus-visible) { outline: 2px solid var(--sauna-focus-current); outline-offset: 3px; }
      .number-input input:focus-visible { outline: none; }
      #session, #session + .sauna-select-trigger {
        min-width: 0;
        max-width: 100%;
      }
      select, .sauna-select-trigger {
        appearance: none;
        padding-inline-end: 40px;
        background-image: linear-gradient(45deg, transparent 50%, currentColor 50%), linear-gradient(135deg, currentColor 50%, transparent 50%);
        background-position: right 21px center, right 16px center;
        background-size: 5px 5px;
        background-repeat: no-repeat;
      }
      select[data-sauna-select-model] { display: none !important; }
      .sauna-select-trigger {
        text-align: start;
        font: inherit;
        min-height: 0;
        background-color: var(--sauna-color-card-background, var(--card-background-color));
        border-color: var(--sauna-color-border, var(--divider-color));
        box-shadow: none;
      }
      .sauna-select-menu {
        position: fixed;
        inset: auto;
        margin: 0;
        padding: 4px;
        z-index: 100;
        overflow: auto;
        overscroll-behavior: contain;
        box-sizing: border-box;
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: var(--sauna-control-radius);
        background: var(--sauna-surface-raised, var(--sauna-color-card-background));
        color: var(--sauna-card-text, var(--primary-text-color));
        box-shadow: var(--sauna-shadow-card);
        font: inherit;
      }
      .sauna-select-menu [role="option"] {
        padding: 9px 13px;
        border-radius: var(--sauna-control-radius);
        min-height: var(--sauna-button-height);
        overflow-wrap: anywhere;
        cursor: pointer;
      }
      .sauna-select-menu [aria-selected="true"] {
        font-weight: 600;
        background: var(--sauna-surface-control, var(--sauna-color-card-background));
      }
      .sauna-select-menu [data-active="true"],
      .sauna-select-menu [role="option"]:hover:not([aria-disabled="true"]) {
        outline: 2px solid var(--sauna-card-focus, var(--sauna-color-focus));
        outline-offset: -2px;
      }
      .sauna-select-menu [aria-disabled="true"] { opacity: 0.5; cursor: default; }
      .sauna-select-group { padding: 6px 13px; color: var(--sauna-card-muted-text); font-size: 13px; }
      button {
        cursor: pointer;
        min-height: var(--sauna-button-height);
        font-weight: 600;
        line-height: 1.3;
        background: var(--sauna-surface-control, var(--sauna-color-card-background));
        border-color: color-mix(in srgb, var(--sauna-surface-control) 72%, white);
        box-shadow: var(--sauna-shadow-control);
        transition: background-color 140ms ease, border-color 140ms ease, box-shadow 140ms ease;
        --sauna-button-hover: var(--sauna-card-focus, var(--sauna-focus-current));
      }
      button:hover:not(:disabled):not(:focus-visible) {
        text-decoration: none;
        border-color: var(--sauna-button-hover);
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
        box-shadow: none;
      }
      button:active:not(:disabled) { box-shadow: 0 1px 2px rgb(0 0 0 / .25); }
      button.primary,
      button.confirm {
        background: var(--confirm);
        color: var(--sauna-command-ink, var(--sauna-card-text, inherit));
        border-color: var(--sauna-color-ui-command, var(--sauna-color-border, var(--divider-color)));
        --sauna-button-hover: var(--sauna-command-focus, var(--sauna-card-focus));
      }
      button.stop {
        background: var(--danger);
        color: var(--sauna-danger-ink);
        border-color: var(--danger);
        --sauna-button-hover: var(--sauna-danger-focus, var(--sauna-card-focus));
      }
      button[aria-current="page"],
      button[data-action^="control-mode:"][aria-pressed="true"] {
        background: var(--accent);
        color: var(--accent-ink);
        border-color: var(--accent);
        font-weight: 700;
        --sauna-button-hover: var(--sauna-accent-focus, var(--sauna-card-focus));
      }
      a {
        color: var(--sauna-card-text, inherit);
      }
      .tabs {
        display: flex;
        flex-wrap: wrap;
        gap: var(--sauna-control-gap);
        margin: 8px 0 12px;
      }
      .card {
        background: var(--sauna-surface-raised, var(--sauna-color-card-background, var(--card-background-color)));
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: var(--sauna-surface-radius);
        padding: var(--sauna-surface-padding);
        margin: var(--sauna-surface-gap) 0;
        box-shadow: var(--sauna-shadow-card);
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
        background: var(--sauna-main-background, var(--sauna-surface-raised, var(--sauna-color-card-background, var(--card-background-color))));
        color: var(--sauna-main-text, var(--sauna-card-text, inherit));
        --sauna-focus-current: var(--sauna-main-focus, var(--sauna-card-focus, var(--accent)));
        transition: background-color 350ms ease;
      }
      .control-main small,
      .control-main .muted { color: var(--sauna-main-muted-text, var(--sauna-card-muted-text, inherit)); }
      .control-main button small { color: inherit; }
      .oven-feedback { margin: 4px 0 10px; }
      .program-current { display: flex; align-items: center; gap: 16px; }
      .program-current-value { flex: 1; min-width: 0; display: grid; gap: 4px; }
      .program-active-label { overflow-wrap: anywhere; font-size: 17px; font-weight: 600; }
      [data-action="program-toggle"] { flex: 0 0 auto; }
      #program-choice-body { margin-top: 16px; }
      .program-disclosure { margin-top: 12px; }
      .scale-hint { fill: var(--sauna-ink-status-warning, var(--sauna-card-text, inherit)); color: var(--sauna-ink-status-warning, var(--sauna-card-text, inherit)); font-size: 11px; margin: 2px 0 0; }
      .control-main .scale-hint { fill: var(--sauna-main-ink-status-warning, var(--sauna-ink-status-warning, inherit)); color: var(--sauna-main-ink-status-warning, var(--sauna-ink-status-warning, inherit)); }
      .appearance-colors { display: grid; gap: 9px; }
      .appearance-color-row { display: grid; grid-template-columns: minmax(130px, 1fr) 48px minmax(96px, 118px) auto; gap: 8px; align-items: center; }
      .appearance-color-row input[type="color"] { padding: 2px; width: 48px; height: 40px; }
      .appearance-color-row input:not([type="color"]) { min-width: 0; width: 100%; }
      .appearance-color-row button { padding: 7px 9px; }
      .appearance-scales { display: flex; flex-wrap: wrap; gap: 16px; }
      .appearance-scale { display: flex; gap: 9px; align-items: end; border-top: 1px solid var(--sauna-color-border, var(--divider-color)); padding-top: 12px; }
      .appearance-scale legend { font-weight: 600; }
      .appearance-scale label { display: grid; gap: 4px; font-size: 13px; }
      .appearance-scale .number-input { width: 110px; }
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
        background: var(--sauna-tint-info-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-info-text, var(--sauna-card-text, inherit));
        font-size: 13px;
      }
      .notice {
        background: var(--sauna-tint-warning-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-warning-text, var(--sauna-card-text, inherit));
        --sauna-focus-current: var(--sauna-tint-warning-focus, var(--sauna-card-focus, var(--accent)));
        border-left: 4px solid var(--sauna-color-status-warning);
        padding: 12px 16px;
        border-radius: 8px;
        margin: 10px 0;
      }
      .error {
        background: var(--sauna-tint-error-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-error-text, var(--sauna-card-text, inherit));
        --sauna-focus-current: var(--sauna-tint-error-focus, var(--sauna-card-focus, var(--accent)));
      }
      .legend {
        display: flex;
        gap: 18px;
        flex-wrap: wrap;
        font-size: 13px;
      }
      .history-legend { display: grid; grid-template-columns: 76px minmax(0, 1fr); align-items: baseline; gap: 12px; }
      .history-legend > strong { font-size: 12px; font-weight: 500; color: var(--sauna-card-muted-text); }
      .history-legend .legend { display: grid; grid-template-columns: repeat(auto-fit, minmax(108px, 1fr)); gap: 6px; margin: 0; }
      .history-legend .legend > span { display: flex; align-items: center; gap: 6px; min-width: 0; padding: 4px 6px; border: 1px solid transparent; border-radius: calc(var(--sauna-control-radius) / 2); }
      .history-legend-groups { display: grid; gap: 8px; padding-top: 12px; margin-top: 10px; border-top: 1px solid var(--sauna-color-border); font-size: 12px; }
      @container (max-width: 500px) {
        .history-legend { grid-template-columns: 1fr; gap: 4px; }
        .history-legend .legend { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      }
      .history-legend .legend-swatch { width: 22px; height: 12px; display: block; flex: 0 0 auto; background: transparent; }
      .legend-swatch .temperature { stroke: var(--sauna-color-series-temperature); stroke-width: 3.5; }
      .legend-swatch .humidity { stroke: var(--sauna-color-series-humidity); stroke-width: 3.5; }
      .dot {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        margin-right: 6px;
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
      .chart rect { stroke-width: 1; }
      .chart .gang {
        fill: color-mix(in srgb, var(--sauna-color-phase-session) 20%, transparent);
        stroke: var(--sauna-color-phase-session);
      }
      .chart .provisional {
        fill: color-mix(in srgb, var(--sauna-color-status-warning) 13%, transparent);
        stroke: var(--sauna-color-status-warning);
      }
      .chart .heat {
        stroke: var(--sauna-color-phase-warmup);
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
      .field {
        display: flex;
        flex-direction: column;
        gap: 6px;
        font-size: 13px;
      }
      .field input {
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
        color: var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)));
      }
      @media (max-width: 800px) {
        main {
          padding: 18px 16px 16px;
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
        header :is(select, .sauna-select-trigger) {
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
        padding: var(--sauna-surface-padding);
        background: var(--sauna-surface-raised, var(--sauna-color-card-background));
        color: var(--sauna-card-text);
        --sauna-focus-current: var(--sauna-card-focus);
        border: 1px solid var(--sauna-color-border);
        border-radius: var(--sauna-surface-radius);
        box-shadow: var(--sauna-shadow-card);
      }
      .plot-title {
        text-align: left;
        color: var(--sauna-card-text);
        font-weight: 600;
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
        color: var(--sauna-card-text);
        padding: 8px 12px;
      }
      .position-select button[aria-pressed="true"] {
        font-weight: 700;
        text-decoration: underline;
        text-underline-offset: 4px;
      }
      .plot-wrap {
        position: relative;
      }
      .chart {
        height: clamp(280px, 44dvh, 400px);
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
        justify-self: center;
        width: max-content;
        margin: 0;
        padding: 4px;
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: var(--sauna-control-radius);
        background: var(--sauna-surface-recessed);
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
        fill: color-mix(in srgb, var(--sauna-color-phase-session) 22%, transparent);
        stroke: var(--sauna-color-phase-session);
      }
      .chart .provisional {
        stroke: var(--sauna-color-phase-session);
      }
      .chart .heat {
        stroke: var(--sauna-color-phase-warmup);
        fill: color-mix(in srgb, var(--sauna-color-phase-warmup) 16%, transparent);
      }
      .chart .ready {
        stroke: var(--sauna-color-phase-ready);
        fill: color-mix(in srgb, var(--sauna-color-phase-ready) 16%, transparent);
      }
      .chart .vent {
        stroke: var(--sauna-color-activity-ventilation);
        stroke-width: 2;
        stroke-dasharray: 8 4;
      }
      .chart .cool {
        stroke: var(--sauna-color-phase-forced-cooling);
        fill: color-mix(in srgb, var(--sauna-color-phase-forced-cooling) 16%, transparent);
      }
      .chart .after {
        stroke: var(--sauna-color-phase-cooling);
        fill: color-mix(in srgb, var(--sauna-color-phase-cooling) 16%, transparent);
      }
      .chart .phase-other {
        stroke: var(--sauna-color-phase-idle);
        fill: color-mix(in srgb, var(--sauna-color-phase-idle) 22%, transparent);
      }
      .chart .door {
        stroke: var(--sauna-color-event-door);
        stroke-width: 1;
        fill: color-mix(in srgb, var(--sauna-color-event-door) 20%, transparent);
      }
      .chart .infusion {
        stroke: var(--sauna-color-event-infusion);
        stroke-width: 2;
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
      .history-overview svg { position: relative; }
      #tooltip { box-sizing: border-box;
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        gap: 8px 20px;
        background: transparent;
        color: var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)));
        padding: 0;
        font: 13px/1.5 system-ui;
      }
      .history-readout { block-size: 5rem; overflow: auto; scrollbar-gutter: stable; }
      @container (max-width: 500px) { .history-readout { block-size: 6rem; } }
      .history-inspection { margin-top: 12px; padding: 14px; border: 1px solid var(--sauna-color-border); border-radius: var(--sauna-control-radius); background: color-mix(in srgb, var(--history-phase-color, var(--sauna-surface-section)) 20%, var(--sauna-surface-section)); box-shadow: var(--sauna-shadow-section); }
      ${Object.entries(phaseAppearance)
        .map(
          ([phase, style]) => `
        .history-inspection:has(#tooltip[data-phase="${phase}"]) { --history-phase-color: var(--sauna-color-${style.color.replaceAll("_", "-")}); }
        .history-inspection:has(#tooltip[data-phase="${phase}"]) [data-legend-phase="${phase}"] { border-color: var(--history-phase-color); background: color-mix(in srgb, var(--history-phase-color) 16%, transparent); font-weight: 600; }
      `,
        )
        .join("")}
      .history-readout > #tooltip { min-block-size: 100%; align-content: start; }
      #tooltip[hidden] { display: none; }
      .history-tooltip-time { color: var(--sauna-card-muted-text); font-weight: 500; font-size: 12px; font-variant-numeric: tabular-nums; }
      .history-tooltip-values { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 14rem), 1fr)); gap: 6px 24px; }
      .history-tooltip-values > div { display: grid; grid-template-columns: minmax(0, 1fr) max-content; min-width: 0; align-items: baseline; gap: 12px; }
      .history-tooltip-values > div[hidden] { display: none; }
      .history-tooltip-values [data-quantity] { display: inline-flex; align-items: center; gap: 7px; }
      .history-tooltip-values [data-quantity]::before { content: ""; width: 22px; height: 3px; flex: 0 0 auto; background: var(--sauna-color-series-temperature); }
      .history-tooltip-values [data-quantity="humidity"]::before { background: var(--sauna-color-series-humidity); }
      .history-tooltip-values > div > span:last-child { font-weight: 650; font-size: 16px; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .history-tooltip-status { grid-column: 2; font-weight: 600; }
      .history-tooltip-events { grid-column: 2; color: var(--sauna-card-text); }
      .history-tooltip-event { display: grid; grid-template-columns: auto 1fr; gap: 10px; padding: 3px 0; align-items: baseline; }
      .history-tooltip-event time { color: var(--sauna-card-muted-text); font-size: 12px; font-variant-numeric: tabular-nums; }
      .history-event-groups { list-style: none; margin: 16px 0 0; padding: 0; }
      .history-event-group { display: grid; grid-template-columns: 64px minmax(0, 1fr); column-gap: 16px; margin-top: 16px; }
      .history-event-time { padding-top: 14px; color: var(--sauna-card-muted-text); font-variant-numeric: tabular-nums; }
      .history-event-items { list-style: none; margin: 0; padding: 4px 14px; border: 1px solid var(--sauna-color-border); border-radius: var(--sauna-control-radius); background: var(--sauna-surface-section); box-shadow: var(--sauna-shadow-section); }
      .history-event-items .event-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; }
      .history-event-items .event-row + .event-row { border-top: 1px solid var(--sauna-color-border); }
      .history-event-items .event-row button { flex: 0 0 auto; }
      .control-overview { max-width: 1280px; margin-inline: auto; }
      .dashboard {
        display: grid;
        grid-template-columns: minmax(300px, 2fr) minmax(0, 3fr);
        gap: var(--sauna-surface-gap);
        max-width: 1280px;
        margin: 0 auto;
        align-items: start;
      }
      @container (max-width: 800px) {
        .dashboard { grid-template-columns: 1fr; }
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
      .operation-control.split { display: flex; }
      .operation-control.split > button { flex: 1 1 50%; width: 50%; min-width: 0; margin: 0; border-radius: 0; }
      .operation-control.split > button:first-child { border-radius: var(--sauna-control-radius) 0 0 var(--sauna-control-radius); }
      .operation-control.split > button:last-child { border-radius: 0 var(--sauna-control-radius) var(--sauna-control-radius) 0; border-left: 0; }
      .program-main {
        grid-column: 1/-1;
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
        font-size: 28px;
        fill: var(--measurement-color);
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
        fill: var(--measurement-color);
      }
      .dashboard .card {
        margin-top: 0;
      }
      .gauge-card h2 {
        text-align: center;
      }
      @media (max-width: 800px) {
        .chart {
          height: clamp(280px, 42dvh, 340px);
        }
        .detector-chart {
          height: 180px;
        }
        .plot-panel {
          padding: 16px;
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
      .tiles {
        gap: 10px;
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
        background: var(--sauna-surface-section);
        box-shadow: var(--sauna-shadow-section);
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
      .parameter-section + .parameter-section,
      .parameter-section + .expert-group {
        margin-top: var(--sauna-surface-gap);
        padding-top: var(--sauna-surface-gap);
        border-top: 1px solid var(--sauna-color-border, var(--divider-color));
      }
      .parameter-section h3 { margin: 0 0 18px; }
      .settings-parameters-card .field > .number-input { margin-top: auto; }
      .settings-parameters-card > button { margin-top: var(--sauna-surface-gap); }
      .settings-layout {
        display: grid;
        --settings-navigation-width: 210px;
        grid-template-columns: var(--settings-navigation-width) minmax(0, 1fr);
        gap: 24px;
        align-items: start;
        height: 100%;
        min-height: 0;
        position: relative;
      }
      #settings { overflow: hidden; }
      .settings-menu-toggle, .settings-menu-backdrop { display: none; }
      .settings-navigation {
        display: grid;
        gap: 8px;
        padding-top: 4px;
      }
      .settings-navigation button { text-align: left; }
      .settings-configuration-link { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 12px; margin-top: 8px; color: inherit; border-top: 1px solid var(--sauna-color-border); text-decoration: none; font-size: 13px; }
      .settings-configuration-link:hover, .settings-configuration-link:focus-visible { text-decoration: underline; }
      .settings-content { min-width: 0; min-height: 0; height: 100%; overflow: auto; scrollbar-gutter: stable; overscroll-behavior: contain; padding: 4px; }
      .settings-content > section > h2 { margin-top: 0; }
      .settings-content .card:first-of-type { margin-top: 0; }
      .settings-content .card { padding: 20px; }
      .light-feedback { font-size: 13px; }
      .oven-feedback { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
      @container (max-width: 700px) {
        .settings-layout { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); gap: 8px; }
        .settings-menu-toggle { display: inline-flex; justify-self: start; align-items: center; gap: 8px; margin: 0 4px; }
        .settings-menu-toggle svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; }
        .settings-navigation { color: var(--sauna-card-text, inherit); display: none; position: absolute; grid-area: 2 / 1; top: 0; bottom: 4px; left: 4px; width: min(var(--settings-navigation-width), calc(100% - 8px)); align-content: start; overflow: auto; z-index: 6; padding: 8px; border: 1px solid var(--sauna-color-border); border-radius: var(--sauna-control-radius); background: var(--sauna-surface-raised); box-shadow: var(--sauna-shadow-card); }
        .settings-layout[data-menu-open] .settings-navigation { display: grid; }
        .settings-layout[data-menu-open] .settings-menu-backdrop { display: block; position: absolute; grid-area: 2 / 1; inset: 0; z-index: 5; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
        .settings-content { grid-column: 1; grid-row: 2; }
        .settings-content .card { padding: 16px; }
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
      .control-main .feedback.on { color: var(--sauna-main-ink-ui-heater-on, var(--sauna-ink-ui-heater-on)); }
      .control-main .feedback.off { color: var(--sauna-main-ink-ui-heater-off, var(--sauna-ink-ui-heater-off)); }
      .control-main .feedback.unknown { color: var(--sauna-main-ink-ui-heater-unknown, var(--sauna-ink-ui-heater-unknown)); }
      .light-controls {
        margin-top: 14px;
      }
      .program-buttons {
        grid-column: 1/-1;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
      }
      .dial-temperature, .dial-light {
        touch-action: none;
      }
      .target-temperature-track, .instrument-arc-track {
        fill: none;
        stroke: var(--accent);
        stroke-width: 22;
        stroke-linecap: round;
        opacity: 0;
        cursor: pointer;
      }
      .target-temperature-handle, .instrument-arc-handle {
        fill: var(--accent);
        stroke: var(--sauna-color-card-background, var(--card-background-color));
        stroke-width: 4;
        cursor: pointer;
      }
      .target-temperature-handle[data-inert-target] { cursor: default; }
      .target-temperature-track:focus, .instrument-arc-track:focus {
        outline: none;
        stroke: var(--accent);
        opacity: 0.35;
      }
      .target-temperature-track:focus + .target-temperature-handle, .instrument-arc-track:focus + .instrument-arc-handle {
        stroke: var(--sauna-card-focus, var(--sauna-focus-current));
        stroke-width: 6;
      }
      .manual-status {
        display: inline-block;
        border-radius: 20px;
        padding: 3px 10px;
        font-size: 13px;
        font-weight: 650;
        background: var(--sauna-tint-unknown-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-unknown-text, var(--sauna-card-text, inherit));
      }
      .manual-status.on {
        background: var(--sauna-tint-heater-on-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-heater-on-text, var(--sauna-card-text, inherit));
      }
      .manual-status.off {
        background: var(--sauna-tint-heater-off-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-heater-off-text, var(--sauna-card-text, inherit));
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
        background: var(--sauna-tint-warning-background, var(--sauna-color-card-background, var(--card-background-color)));
      }
      .event-marker[data-selected="true"] {
        color: var(--sauna-tint-warning-text, var(--sauna-card-text, inherit));
      }
      .event-row button {
        padding: 3px 7px;
        white-space: nowrap;
      }
      .temperature-presets {
        grid-column: 1/-1;
        display: grid;
        grid-template-columns: repeat(var(--preset-columns-wide), minmax(0, 1fr));
        gap: 6px;
        margin-top: 10px;
      }
      .temperature-presets .tile {
        min-width: 0;
        min-height: 40px;
        padding: 8px 10px;
        text-align: center;
        white-space: nowrap;
      }
      #program-choice-body { container: program-choice / inline-size; }
      @container program-choice (width < 420px) {
        .temperature-presets { grid-template-columns: repeat(var(--preset-columns-medium), minmax(0, 1fr)); }
      }
      @container program-choice (width < 240px) {
        .temperature-presets { grid-template-columns: repeat(var(--preset-columns-narrow), minmax(0, 1fr)); }
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
      .history-plot-frame { background: var(--sauna-color-chart-background); border: 1px solid var(--sauna-color-border); border-radius: var(--sauna-control-radius); box-shadow: 0 2px 3px rgb(0 0 0 / .3), 0 7px 16px -5px rgb(0 0 0 / .45); overflow: hidden; }
      .history-plot-frame .history-stack { border: 0; border-radius: 0; box-shadow: none; }
      .history-background [data-history-annotations] rect { stroke: none; }
      #history-navigation {
        --sauna-focus-current: var(--sauna-chart-focus, var(--sauna-chart-ink));
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto auto auto minmax(0, 1fr);
        gap: 2px 8px;
        align-items: center;
        margin: 0;
        padding: 0 0 8px;
        color: var(--sauna-chart-ink, var(--sauna-color-chart-text));
      }
      #history-navigation > button {
        grid-row: 2;
        min-width: 32px;
        min-height: 32px;
        padding: 4px;
        border: 0;
        background: transparent;
        box-shadow: none;
        font-size: 18px;
        line-height: 1;
        color: inherit;
      }
      #history-navigation [data-action="zoom-out"] { grid-column: 2; }
      #history-navigation [data-action="zoom-in"] { grid-column: 4; }
      #history-navigation .history-overview { grid-column: 1 / -1; grid-row: 1; }
      .history-window-caption {
        grid-column: 3;
        grid-row: 2;
        display: flex;
        align-items: center;
        justify-content: flex-end;
        color: inherit;
        font-size: 12px;
        font-variant-numeric: tabular-nums;
      }
      .history-window-caption button {
        min-height: 24px;
        min-width: 32px;
        padding: 0 4px;
        border: 0;
        background: transparent;
        box-shadow: none;
        color: inherit;
        font: inherit;
      }
      #history-navigation button:hover:not(:disabled):not(:focus-visible) {
        outline: 1px solid var(--sauna-focus-current);
        outline-offset: -1px;
      }
      .history-overview {
        grid-column: 1/-1;
        width: 100%;
        min-width: 0;
      }
      .history-overview svg {
        width: 100%;
        height: 24px;
        display: block;
        touch-action: none;
        cursor: grab;
      }
      .history-overview .overview-window {
        fill: transparent;
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
        stroke: var(--sauna-chart-ink, var(--sauna-color-chart-text));
        stroke-width: 3px;
        stroke-linejoin: round;
      }
      .diagnostic-marker[data-selected="true"] .event-marker-dot {
        fill: color-mix(in srgb, var(--sauna-color-status-warning) 12%, var(--sauna-color-chart-background));
        stroke: var(--sauna-chart-focus, var(--sauna-chart-ink));
        stroke-width: 3;
      }
      .diagnostic-marker:focus-visible .event-marker-dot {
        stroke: var(--sauna-chart-focus, var(--sauna-chart-ink));
        stroke-width: 4;
      }
      .event-row[data-selected="true"] {
        background: var(--sauna-tint-warning-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-warning-text, var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color))));
        outline: 3px solid var(--sauna-tint-warning-focus, var(--sauna-card-focus, var(--accent)));
        outline-offset: -3px;
      }
      .event-row[data-selected="true"] button {
        --sauna-focus-current: var(--sauna-tint-warning-focus, var(--sauna-card-focus, var(--accent)));
        background: var(--sauna-tint-warning-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-warning-text, var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color))));
        border-color: currentColor;
      }
      .state-summary {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 10px 15px;
      }
      .state-line {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
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
        color: inherit;
      }
      @media (max-width: 1000px) {
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
        header :is(select, .sauna-select-trigger) {
          max-width: 110px;
        }
        .state-line,
        .state-summary {
          align-items: flex-start;
        }
      }
      button[aria-pressed="true"] {
        background: var(--accent);
        color: var(--accent-ink);
        border-color: var(--accent);
        font-weight: 700;
        --sauna-button-hover: var(--sauna-accent-focus, var(--sauna-card-focus));
      }
      button[aria-current="page"]:disabled,
      button[aria-pressed="true"]:disabled {
        opacity: 1;
        cursor: default;
      }
      button[data-action^="heater:"]:disabled:not([aria-pressed="true"]),
      button[data-action^="light:"]:disabled:not([aria-pressed="true"]) {
        opacity: 0.5;
      }
      button[data-action^="program-remove:"] {
        background: transparent;
        color: var(--danger-text);
      }
      .control-status-row { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: center; margin-bottom: var(--sauna-control-gap); }
      .control-status-row .state-line { grid-column: 1; min-width: 0; }
      .control-status-row .control-mode { grid-column: 2; justify-self: center; max-width: 100%; }
      .manual-controls {
        margin-top: 16px;
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
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: var(--sauna-control-gap);
        padding: 4px;
        border-radius: var(--sauna-control-radius);
        background: var(--sauna-surface-recessed);
        margin: 16px 0 20px;
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
      .program-choice-summary,
      .program-choice-details { grid-area: choice; align-self: center; }
      .program-choice-summary { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
      .program-choice-summary > span { font-weight: 650; }
      .program-choice-summary small { white-space: nowrap; font-weight: 400; }
      .program-choice-details { opacity: 0; font-weight: 400; white-space: normal; line-height: 1.4; }
      .program-named-choice:not(.program-feedback):is(:hover, :focus-visible) .program-choice-summary { opacity: 0; }
      .program-named-choice:not(.program-feedback):is(:hover, :focus-visible) .program-choice-details { opacity: 1; }
      .program-types button,
      .program-named-choice {
        position: relative;
        font-weight: 700;
      }
      .program-feedback > .program-choice-content {
        visibility: hidden;
      }
      .program-save-label {
        position: absolute;
        inset: 2px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: inherit;
        font-size: 12px;
        line-height: 1.2;
        white-space: normal;
        text-align: center;
        pointer-events: none;
      }
      .program-named-choice > .program-choice-content {
        display: grid;
        grid-template-areas: "choice";
        width: 100%;
      }
      .program-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 16px;
        padding-top: 14px;
        border-top: 1px solid var(--sauna-color-border, var(--divider-color));
      }
      .program-actions > button { flex: 1 1 100%; min-width: 0; }
      .program-saved,
      .program-saving {
        color: var(--sauna-command-ink, var(--sauna-card-text, inherit));
        border-color: var(--confirm);
        background: var(--confirm);
      }
      .program-saved:disabled,
      .program-saving:disabled {
        opacity: 1;
      }
      .program-pending {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-top: 10px;
        padding: 8px 10px;
        background: var(--sauna-pending-background, transparent);
        border-radius: 8px;
        color: var(--sauna-pending-text, var(--sauna-main-text, var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)))));
        --sauna-focus-current: var(--sauna-pending-focus, var(--sauna-main-focus, var(--sauna-card-focus, var(--accent))));
      }
      .program-pending[aria-hidden="true"] { visibility: hidden; }
      .program-kind {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        margin: 10px 0;
      }
      .program-form {
        margin: 0;
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
        display: inline;
      }
      .program-info button {
        width: 25px;
        height: 25px;
        min-height: 25px;
        border-radius: 50%;
        padding: 0;
      }
      .program-info-popup {
        position: fixed;
        inset: auto;
        z-index: 100;
        box-sizing: border-box;
        margin: 0;
        min-width: 0;
        width: 320px;
        max-width: 320px;
        overflow: auto;
        overscroll-behavior: contain;
        padding: 9px 11px;
        border: 1px solid var(--sauna-color-border, var(--divider-color));
        border-radius: 8px;
        background: var(--sauna-color-card-background, var(--card-background-color));
        color: var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)));
        --sauna-focus-current: var(--sauna-card-focus, var(--accent));
        box-shadow: 0 4px 14px color-mix(in srgb, var(--sauna-color-text, var(--primary-text-color)) 20%, transparent);
        font-size: 12px;
        line-height: 1.4;
        overflow-wrap: anywhere;
      }
      .program-info-popup[hidden] { display: none; }
      .manual-section h3 {
        margin: 0;
      }
      @container (max-width: 600px) {
        header {
          grid-template-columns: 1fr auto;
          gap: 12px;
          margin-bottom: 16px;
        }
        .main-tabs {
          grid-row: 2;
          grid-column: 1 / -1;
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

      .control-main .phase { font-weight: 600; letter-spacing: -.025em; }
      .control-main .control-section, .control-main .manual-controls {
        margin-top: var(--sauna-surface-gap);
        padding: var(--sauna-surface-padding);
        border: 1px solid var(--sauna-color-border);
        border-radius: var(--sauna-control-radius);
        background: var(--sauna-main-section, var(--sauna-surface-section));
        box-shadow: var(--sauna-shadow-section);
        transition: background-color 350ms ease;
        color: var(--sauna-page-text);
      }
      .control-main button:focus-visible {
        outline: 2px solid var(--sauna-focus-current);
        outline-offset: 4px;
      }
      .control-main .operation-control > button, .control-main [data-action="program-apply"] {
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: var(--sauna-action-height);
        padding: 6px 12px;
        font-size: 16px;
        line-height: 20px;
        font-weight: 600;
        text-align: center;
      }
      .program-current:has([aria-expanded="true"]) {
        padding: 0 0 16px;
        border-bottom: 1px solid var(--sauna-color-border);
      }
      .main-tabs button, .program-types button, .segmented-mode button, .control-auto button {
        position: relative;
        min-height: var(--sauna-button-height);
        background: transparent;
        box-shadow: none;
        border-color: transparent;
        border-radius: calc(var(--sauna-control-radius) - 3px);
        color: var(--sauna-card-muted-text);
        font-size: 14px;
        font-weight: 600;
        padding: 8px 12px;
      }
      .main-tabs button[aria-current="page"], .program-types button[aria-pressed="true"], .segmented-mode button[aria-pressed="true"], .control-auto button[aria-pressed="true"] {
        color: var(--sauna-card-text);
        background: var(--sauna-surface-control);
        box-shadow: var(--sauna-shadow-control);
      }
      .main-tabs button[aria-current="page"]::after, .program-types button[aria-pressed="true"]::after, .segmented-mode button[aria-pressed="true"]::after, .control-auto button[aria-pressed="true"]::after {
        content: "";
        position: absolute;
        bottom: 3px;
        left: 30%;
        right: 30%;
        height: 2px;
        border-radius: 2px;
        background: var(--accent);
      }
      .segmented-mode { display: flex; gap: var(--sauna-control-gap); padding: 4px; background: var(--sauna-surface-recessed); border-radius: var(--sauna-control-radius); }
      .temperature-presets { gap: var(--sauna-control-gap); }
      .program-named-list { gap: var(--sauna-control-gap); }
      .program-named-choice { padding: 12px 14px; }
      .program-pending { background: transparent; padding: 12px 0 0; }
      .program-actions { margin-top: 12px; border-top: 0; padding-top: 0; }
      .history-controls { display: flex; align-items: center; gap: 12px; min-width: 0; margin: 0 0 16px; }
      #history-loading { margin-left: auto; font-size: 12px; color: var(--sauna-card-muted-text); white-space: nowrap; }
      .history-controls :is(select, .sauna-select-trigger) { max-width: 100%; font-weight: 600; }
      .history-stack, .detector-chart { background: var(--sauna-color-chart-background); border: 1px solid var(--sauna-color-border); border-radius: var(--sauna-control-radius); box-shadow: 0 2px 3px rgb(0 0 0 / .3), 0 7px 16px -5px rgb(0 0 0 / .45); }
      .history-overview { background: transparent; border-radius: var(--sauna-control-radius); }
      .control-history-inspection > div { padding: 16px; border-radius: var(--sauna-control-radius); border: 1px solid var(--sauna-color-border); background: var(--sauna-surface-section); box-shadow: var(--sauna-shadow-section); }
      #control-history { margin-top: var(--sauna-surface-gap); }
      #plots { margin: 0; }
      .card, .plot-panel { isolation: isolate; }
      @container (max-width: 450px) {
        .main-tabs { min-width: 0; gap: 2px; }
        .program-types button, .main-tabs button, .segmented-mode button { padding-inline: 7px; font-size: 13px; }
        .main-tabs button { flex: 0 1 auto; white-space: nowrap; }
        .control-main .control-section, .control-main .manual-controls { padding: 12px; }
      }
      .manual-controls:not(.manual-entry) { container: output-controls / inline-size; display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; }
      .manual-controls .manual-section { display: grid; grid-template-columns: 4em minmax(0, 1fr); align-items: center; min-width: 0; }
      .manual-controls .manual-section + .manual-section { border-top: 1px solid var(--sauna-color-border); padding-top: 14px; }
      .manual-heading h3 { font-size: 15px; font-weight: 600; }
      .manual-selection { display: grid; grid-template-columns: 7.5em minmax(0, 1fr); align-items: center; gap: 12px; min-width: 0; width: min(100%, 20em); justify-self: center; font-size: 14px; }
      .manual-selection:not(:has(.control-auto)) { grid-template-columns: minmax(0, 1fr); max-width: 12em; }
      .control-auto, .output-toggle { padding: 4px; background: var(--sauna-surface-recessed); border-radius: var(--sauna-control-radius); }
      .control-auto button, .output-toggle button { width: 100%; min-width: 0; padding-inline: 10px; font-size: 14px; font-weight: 600; white-space: nowrap; }
      .output-toggle { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; }
      .output-toggle button { background: transparent; border-color: transparent; border-radius: calc(var(--sauna-control-radius) - 3px); color: var(--sauna-card-muted-text); box-shadow: none; }
      .output-toggle button[aria-pressed="true"] { box-shadow: var(--sauna-shadow-control); }
      .output-toggle button[data-regulation-selected="true"] { outline: 2px solid var(--sauna-card-muted-text); outline-offset: -3px; }
      @container output-controls (max-width: 280px) {
        .manual-controls .manual-section { grid-template-columns: minmax(0, 1fr); gap: 8px; }
        .manual-selection { gap: 8px; }
        .manual-selection:not(:has(.control-auto)) { max-width: none; }
      }
      .output-toggle button[data-action$=":true"] { --output-surface: var(--sauna-feedback-on-surface); --output-ink: var(--sauna-feedback-on-ink); --output-focus: var(--sauna-feedback-on-focus); }
      .output-toggle button[data-action$=":false"] { --output-surface: var(--sauna-feedback-off-surface); --output-ink: var(--sauna-feedback-off-ink); --output-focus: var(--sauna-feedback-off-focus); }
      .output-toggle button[aria-pressed="true"] { background: var(--output-surface); border-color: var(--output-surface); color: var(--output-ink); --sauna-button-hover: var(--output-focus); --sauna-focus-current: var(--output-focus); }
      .instrument-arc-track[aria-disabled="true"], .instrument-arc-track[aria-disabled="true"] + .instrument-arc-handle { cursor: default; }
      .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
      button[data-command-pending] { position: relative; }
      button[data-command-pending]::after { content: ""; position: absolute; inset: auto 8px 5px; height: 2px; background: currentColor; opacity: .65; animation: command-wait 1.2s ease-in-out infinite alternate; }
      @keyframes command-wait { from { opacity: .25; transform: scaleX(.45); } to { opacity: .8; transform: scaleX(1); } }
      @media (prefers-reduced-motion: reduce) { button[data-command-pending]::after { animation: none; } }
      .manual-entry { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
      .control-history-lanes { min-width: 660px; }
      .control-track { display: grid; grid-template-columns: 130px 1fr; align-items: center; gap: 18px; margin: 16px 0; font-size: 13px; }
      .control-track > span { color: var(--sauna-card-muted-text); }
      .control-track-bar { height: 25px; position: relative; border-radius: 5px; overflow: hidden; background: repeating-linear-gradient(135deg,transparent,transparent 4px,var(--sauna-color-border, var(--divider-color)) 4px,var(--sauna-color-border, var(--divider-color)) 5px); }
      .control-track-bar button { position: absolute; top: 0; bottom: 0; padding: 0; border: 0; border-radius: 0; min-width: 1px; box-shadow: none; }
      .control-track-bar button:hover { outline: 2px solid var(--sauna-card-text); z-index: 1; }
      .control-track-bar i { position: absolute; top: 0; bottom: 0; border-left: 2px solid var(--sauna-card-text); pointer-events: none; }
      .control-time-axis { display: flex; justify-content: space-between; margin-left: 148px; font-size: 12px; color: var(--sauna-card-muted-text); }
      .control-history-inspection { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
      .control-history-inspection dl { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 0; font-size: 13px; }
      .control-history-inspection dd { margin: 0; text-align: right; }
      .control-cursor-row { margin: 8px 0 16px 148px; }
      input.control-cursor { appearance: none; display: block; width: calc(100% + 16px); max-width: none; height: 24px; border: 0; border-radius: 0; padding: 0; margin: 0 0 0 -8px; background: transparent; cursor: pointer; }
      input.control-cursor::-webkit-slider-runnable-track { height: 4px; background: var(--sauna-color-border, var(--divider-color)); border-radius: 2px; }
      input.control-cursor::-webkit-slider-thumb { appearance: none; width: 16px; height: 16px; border: 0; border-radius: 50%; margin-top: -6px; background: var(--accent); }
      input.control-cursor::-moz-range-track { height: 4px; background: var(--sauna-color-border, var(--divider-color)); border-radius: 2px; }
      input.control-cursor::-moz-range-thumb { width: 16px; height: 16px; border: 0; border-radius: 50%; background: var(--accent); }
      .control-history-events td { vertical-align: top; font-size: 13px; }
      .control-history-events td:first-child { white-space: nowrap; }
      .control-history-events button { padding: 3px 7px; font-variant-numeric: tabular-nums; }
      .control-history-events tr[data-selected="true"] { background: color-mix(in srgb, var(--accent) 10%, transparent); }
      @media (max-width: 700px) { .control-history-inspection { grid-template-columns: 1fr; gap: 12px; } }
      .gauge-card { padding: 26px 20px 20px; }
      .gauge-card h2 { font-size: 13px; font-weight: 500; letter-spacing: .035em; color: var(--sauna-card-muted-text); margin-bottom: 10px; }
      .gauges { gap: 10px; grid-template-columns: repeat(var(--round-count, 3), minmax(0, 1fr)); align-items: start; }
      .gauges > * { min-width: 0; }
      .gauges > [data-instrument-style="linear"] { grid-column: 1 / -1; }
      .dial { width: 100%; max-width: 320px; }
      .instrument-face { stroke: var(--sauna-color-border, var(--divider-color)); stroke-width: .7; filter: drop-shadow(0 3px 3px rgb(0 0 0 / .25)); }
      .instrument-rim { fill: none; stroke-width: 1; pointer-events: none; }
      .instrument-readout { fill: var(--sauna-color-card-background, var(--card-background-color)); stroke: color-mix(in srgb, var(--measurement-color) 24%, transparent); stroke-width: 1; }
      .dial .reading { font-size: 31px; font-weight: 550; letter-spacing: -.8px; font-variant-numeric: tabular-nums; }
      .dial .reading-unit { font-size: 15px; font-weight: 400; letter-spacing: 0; }
      .dial .target-caption { font-size: 8px; font-weight: 500; letter-spacing: 1.5px; fill: var(--sauna-card-muted-text); }
      .dial .target-reading { font-size: 18px; font-weight: 450; fill: var(--sauna-card-text); font-variant-numeric: tabular-nums; }
      .dial .target-reading .reading-unit { font-size: 11px; }
      .dial .tick { font-size: 11px; font-weight: 450; opacity: .85; }
      .humidity-symbol { fill: color-mix(in srgb, var(--measurement-color) 12%, transparent); stroke: var(--measurement-color); stroke-width: 1.2; opacity: .75; }
      .light-symbol { fill: none; stroke: var(--measurement-color); stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; opacity: .75; }
      .light-instrument { --accent: var(--sauna-color-series-light); }
      .measurement-instrument { min-width: 0; text-align: center; }
      .measurement-instrument[data-instrument-style="linear"] { padding: 12px 16px; border: 1px solid var(--sauna-color-border, var(--divider-color)); border-radius: var(--sauna-control-radius); background: linear-gradient(135deg, var(--sauna-color-card-background, var(--card-background-color)), var(--sauna-color-page-background, var(--primary-background-color))); box-shadow: var(--sauna-shadow-section); }
      .measurement-instrument[data-instrument-style="linear"] > h2 { margin: 0 0 2px; text-align: left; }
      .linear-instrument { display: grid; grid-template-columns: 100px minmax(0, 1fr) 22px; align-items: center; gap: 16px; }
      .linear-reading { margin: 0; color: var(--measurement-color); text-align: left; white-space: nowrap; font-size: 31px; font-weight: 550; letter-spacing: -.8px; font-variant-numeric: tabular-nums; line-height: 1.25; }
      .linear-symbol { width: 22px; height: 22px; fill: none; stroke: var(--measurement-color); stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; opacity: .75; }
      .linear-reading > span { font-size: 15px; font-weight: 400; letter-spacing: 0; }
      .linear-scale { container: linear-scale / inline-size; display: grid; grid-template-areas: "caption" "track" "ticks"; grid-template-columns: minmax(0, 1fr); grid-template-rows: 17px 28px 18px; align-items: center; min-width: 0; }
      .linear-rail { grid-area: track; height: 8px; border-radius: 10px; background: var(--sauna-color-border); overflow: hidden; }
      .linear-rail i { display: block; height: 100%; border-radius: inherit; background: var(--measurement-color); }
      .linear-ticks { grid-area: ticks; position: relative; height: 18px; margin-top: 0; color: var(--sauna-card-muted-text); font-size: 10px; font-variant-numeric: tabular-nums; }
      .linear-ticks span { position: absolute; transform: translateX(-50%); }
      .linear-ticks [data-tick-density="middle"] { display: none; }
      @container linear-scale (max-width: 220px) {
        .linear-ticks [data-tick-density="detail"] { display: none; }
        .linear-ticks [data-tick-density="middle"] { display: inline; }
      }
      @container linear-scale (max-width: 110px) {
        .linear-ticks [data-tick-density="middle"] { display: none; }
      }
      .instrument-slider { margin: 4px auto 0; max-width: 240px; width: 100%; }
      .linear-scale .instrument-slider { display: contents; }
      .linear-scale .instrument-slider label { grid-area: caption; justify-content: flex-end; gap: 6px; margin: 0; font-size: 11px; }
      .linear-scale .instrument-slider output { font-size: 12px; }
      .instrument-slider label { display: flex; justify-content: space-between; align-items: baseline; margin: 0 0 6px; color: var(--sauna-card-muted-text); font-size: 12px; }
      .instrument-slider output { color: var(--sauna-card-text); font-size: 14px; font-variant-numeric: tabular-nums; }
      input.instrument-range { display: block; appearance: none; box-sizing: border-box; width: 100%; max-width: none; min-width: 0; padding: 0; margin: 0; height: 28px; border: 0; background: transparent; cursor: pointer; }
      input.instrument-range::-webkit-slider-runnable-track { height: 5px; border-radius: 5px; background: var(--sauna-color-border); }
      input.instrument-range::-webkit-slider-thumb { appearance: none; width: 17px; height: 17px; margin-top: -6px; border-radius: 50%; background: var(--accent); border: 2px solid var(--sauna-color-card-background); box-shadow: var(--sauna-shadow-control); }
      input.instrument-range::-moz-range-track { height: 5px; border-radius: 5px; background: var(--sauna-color-border); }
      input.instrument-range::-moz-range-thumb { width: 13px; height: 13px; border-radius: 50%; background: var(--accent); border: 2px solid var(--sauna-color-card-background); }
      .linear-scale input.instrument-range { grid-area: track; z-index: 1; width: calc(100% + 17px); margin-inline: -8.5px; }
      .linear-scale input.instrument-range::-webkit-slider-runnable-track { background: transparent; }
      .linear-scale input.instrument-range::-moz-range-track { background: transparent; }
      input.instrument-range:disabled { opacity: .45; cursor: default; }
      .instrument-notice { display: block; margin-top: 5px; color: var(--sauna-card-muted-text); font-size: 12px; }
      .weather-instrument { display: grid; grid-template-columns: auto minmax(0, 1fr); grid-template-areas: "heading heading" "condition values"; gap: 10px 20px; align-items: center; margin-top: 16px; padding: 12px 14px; border-radius: var(--sauna-control-radius); background: var(--sauna-surface-section); box-shadow: var(--sauna-shadow-section); }
      .weather-heading { grid-area: heading; display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 8px; }
      .weather-heading > span { color: var(--sauna-card-muted-text); font-size: 10px; letter-spacing: .08em; }
      .weather-heading h2 { margin: 0; font-size: 14px; color: var(--sauna-card-text); letter-spacing: 0; }
      .weather-condition { grid-area: condition; display: flex; flex-direction: column; align-items: center; gap: 0; margin: 0; color: var(--sauna-card-muted-text); font-size: 11px; }
      .weather-picture { width: 60px; flex: 0 0 auto; overflow: visible; fill: none; stroke: var(--sauna-card-muted-text); stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
      .weather-sun, .weather-moon, .weather-lightning { color: var(--accent); stroke: currentColor; }
      .weather-sun circle, .weather-moon { fill: color-mix(in srgb, var(--accent) 18%, transparent); }
      .weather-cloud { fill: var(--sauna-surface-control); }
      .weather-rain, .weather-snow { stroke: var(--sauna-color-series-humidity); }
      .weather-values { grid-area: values; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; margin: 0; }
      .weather-values dt { margin: 0; font-size: 11px; color: var(--sauna-card-muted-text); overflow-wrap: anywhere; }
      .weather-values dd { margin: 2px 0 0; font-size: 17px; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .weather-values dd span { font-size: 11px; color: var(--sauna-card-muted-text); }
      .weather-instrument time { margin-inline-start: auto; color: var(--sauna-card-muted-text); font-size: 11px; white-space: nowrap; }
      .appearance-instruments { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 24px; }
      .appearance-instruments label { display: grid; gap: 7px; font-size: 13px; }
      .appearance-instruments :is(select, .sauna-select-trigger) { width: 100%; min-width: 0; }
      @container (max-width: 450px) {
        .gauges { grid-template-columns: minmax(0, 1fr); }
        .weather-instrument { grid-template-columns: 70px minmax(0, 1fr); gap: 8px 12px; padding: 12px; }
        .measurement-instrument[data-instrument-style="linear"] { padding: 10px 12px; }
        .linear-instrument { grid-template-columns: 88px minmax(0, 1fr) 18px; gap: 12px; }
        .linear-reading { font-size: 25px; }
        .linear-reading > span { font-size: 13px; }
        .linear-symbol { width: 18px; height: 18px; }
        .weather-values { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 0; }
        .weather-values dd { font-size: 16px; }
      }
    </style><main>
      <header><div class="header-brand"><button class="header-icon" data-action="menu" aria-label="Home-Assistant-Seitenleiste umschalten" title="Home-Assistant-Seitenleiste umschalten" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button><h1>Sauna</h1></div><nav class="tabs main-tabs" id="main-navigation" aria-label="Ansicht"><button data-action="overview" aria-current="page">Steuerung</button><button data-action="history">Verlauf</button><button data-action="details">Details</button><button data-action="settings">Einstellungen</button></nav><div class="header-context"><select id="instance" aria-label="Sauna auswählen"></select><button class="header-icon" data-action="fullscreen" aria-label="Vollbild" title="Vollbild" hidden><svg data-fullscreen-icon="enter" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></svg><svg data-fullscreen-icon="exit" viewBox="0 0 24 24" aria-hidden="true" hidden><path d="M3 8h5V3m8 0v5h5M8 21v-5H3m18 0h-5v5"/></svg></button></div></header>
      <nav class="tabs detail-tabs" aria-label="Detailansicht" hidden><button data-action="detail" aria-current="page">Betrieb & Fristen</button><button data-action="detail-history">Detailverlauf</button><button data-action="diagnostics">Erkennungskontrolle</button></nav>
      <div id="message" role="alert"></div><section id="current" aria-live="polite"><p>Lade Saunadaten …</p></section><section id="details" hidden></section>
      <section id="history" hidden><div class="plot-panel history-panel"><div class="history-controls"><select id="session" aria-label="Saunasitzung auswählen"><option value="live">Letzte Sitzung</option></select><span id="history-loading" role="status" hidden></span></div><div class="history-plot-frame"><div id="plots"></div><div id="history-navigation"><button data-action="zoom-out" aria-label="Verkleinern">−</button><div id="history-overview" class="history-overview" aria-label="Übersicht der gesamten Saunasitzung"></div><button data-action="zoom-in" aria-label="Vergrößern">＋</button><div class="history-window-caption"><button data-action="reset-zoom" aria-label="Gesamte Saunasitzung" title="Gesamte Sitzung anzeigen">1×</button></div></div></div>
        <div class="history-inspection" id="history-inspection"><div class="history-readout"><div id="tooltip" role="group" aria-label="Werte am markierten Zeitpunkt" hidden></div></div><div id="history-legends">${this.historyLegendMarkup()}</div></div></div><div id="control-history" hidden></div><div id="detection-plots" hidden></div><div id="gangs"></div><div id="event-list"></div>
      </section><section id="settings" hidden></section>
    </main>`;
    this.shadowRoot.addEventListener("click", (e) => {
      const moment = e.target.closest("[data-control-time]");
      if (moment) {
        this.controlHistoryCursor = Number(moment.dataset.controlTime);
        this.renderControlHistory();
      }
      const b = e.target.closest("[data-action]");
      if (b) this.runPanelAction(() => this.action(b.dataset.action));
    });
    this.shadowRoot.addEventListener("change", (e) => {
      if (e.target.id === "control-history-cursor") {
        this.controlHistoryCursor =
          stamp(this.shown.session.timeline.session_started_at) +
          Number(e.target.value);
        this.renderControlHistory();
        this.$("#control-history-cursor")?.focus();
      }
      if (e.target.id !== "instance" && !this.state) return;
      if (e.target.id === "instance") {
        this.entry = e.target.value;
        this.controlCommands = {};
        this.state = null;
        this.messages = {};
        this.message(null);
        this.highlightedEventId = null;
        this.pendingEventFocus = null;
        this.generation++;
        this.selected = "live";
        this.sessions = null;
        this.historyListStale = true;
        this.historyPendingFinalId = null;
        this.historyOptionsSignature = null;
        this.cache.clear();
        this.archiveRevision = undefined;
        this.archiveAdminSessions = null;
        this.archiveDeletePending = null;
        this.historyLoad = null;
        this.shown = null;
        this.chartDataIndex = null;
        this.historyNavigationCache = null;
        this.clearHistoryDisplay();
        this.invalidateHistoryIndex();
        this.settingsEntry = null;
        this.programSelectionDraft = null;
        this.programChoiceOpen = false;
        this.manualOverridesOpen = false;
        this.linearTargetDraft = null;
        this.settingsSection = null;
        this.controlSessionKey = null;
        clearTimeout(this.programSavedTimer);
        this.programRequest = null;
        this.programPendingApply = null;
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
        this.temperatureChange = null;
        this.temperatureInteraction = null;
        this.cancelLightDrag(false);
        this.manualLightDraft = null;
        this.manualLightRevision = (this.manualLightRevision || 0) + 1;
        this.zoom = 1;
        this.window = null;
        this.renderStateLoading();
        this.syncNavigation();
        this.refresh(true);
      }
      if (e.target.id === "session") {
        this.selected = e.target.value;
        this.historySelectionGeneration = (this.historySelectionGeneration || 0) + 1;
        this.historyLoad = null;
        this.syncHistoryLoading();
        this.message(null, "history");
        this.shown = null;
        this.clearHistoryDisplay();
        this.highlightedEventId = null;
        this.pendingEventFocus = null;
        this.invalidateHistoryIndex();
        this.zoom = 1;
        this.window = null;
        this.showHistoryCache();
        this.drawHistory("archive");
        this.startHistoryLoad();
      }
      if (["button-program", "button-temperature"].includes(e.target.id))
        this.runPanelAction(() => this.action("button-program"));
      if (e.target.id === "button-session-gesture")
        this.runPanelAction(() => this.action("button-gesture"));
    });
    this.shadowRoot.addEventListener("input", (e) => {
      if (!this.state) return;
      if (
        e.target.matches(
          "[data-appearance-color],[data-appearance-picker],[data-appearance-scale]",
        )
      )
        this.updateAppearanceField(e.target);
      if (e.target.matches("#parameters input[name]")) e.target.dataset.edited = "true";
      if (e.target.matches("#parameters input[name]"))
        this.settingsEditRevision = (this.settingsEditRevision || 0) + 1;
      if (
        e.target.matches(
          "#progression-start,#progression-end,#progression-gangs,[data-free-step-count]",
        )
      ) {
        const savedFeedback = this.programSaveState === "saved";
        this.progressionDraft = {
          ...this.progressionDraft,
          [e.target.id]: e.target.value,
        };
        this.programSaveState = this.programRequest ? "saving" : null;
        if (savedFeedback) this.drawCurrent();
      }
      if (e.target.matches("[data-free-step]")) {
        const savedFeedback = this.programSaveState === "saved";
        this.freeProgramStepsDraft = [
          ...this.shadowRoot.querySelectorAll("[data-free-step]"),
        ].map((input) => input.value);
        this.programSaveState = this.programRequest ? "saving" : null;
        if (savedFeedback) this.drawCurrent();
      }
      if (e.target.matches("[data-program-field],[data-program-step]"))
        this.updateProgramEditorField(e.target);
      if (e.target.matches("[data-manual-light-value]")) {
        this.manualLightDraft = e.target.value;
        this.manualLightRevision = (this.manualLightRevision || 0) + 1;
      }
      if (e.target.id === "linear-target-temperature") {
        this.linearTargetDraft = this.clampArcTemperature(Number(e.target.value));
        e.target.value = this.linearTargetDraft;
        this.linearTargetRevision = (this.linearTargetRevision || 0) + 1;
        const output = this.$("[data-linear-target]");
        if (output) output.textContent = `${num(this.linearTargetDraft)} °C`;
      }
    });
    this.shadowRoot.addEventListener("change", (e) => {
      if (!this.state) return;
      if (e.target.matches("[data-appearance-instrument]"))
        this.updateAppearanceField(e.target);
      if (e.target.matches("[data-manual-light-value]"))
        this.runPanelAction(() => this.action("manual-light-overview"));
      if (e.target.id === "linear-target-temperature") {
        const value = this.clampArcTemperature(Number(e.target.value));
        this.runPanelAction(() => this.commitLinearTarget(value));
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
      if (
        e.target.matches(
          "#progression-start,#progression-end,#progression-gangs,[data-free-step],[data-free-step-count]",
        )
      )
        this.runPanelAction(() => this.finishFreeProgramInput(e.target));
    });
    this.shadowRoot.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.programInfoOpen) {
        e.preventDefault();
        e.stopPropagation();
        this.closeInfo(true);
        return;
      }
      if (!this.state) return;
      if (
        e.key === "Escape" &&
        this.$(".settings-layout")?.hasAttribute("data-menu-open")
      ) {
        e.preventDefault();
        this.setSettingsMenu(false, true);
        return;
      }
      if (
        e.target.matches(".diagnostic-marker[data-action]") &&
        ["Enter", " "].includes(e.key)
      ) {
        e.preventDefault();
        this.runPanelAction(() => this.action(e.target.dataset.action));
        return;
      }
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
      if (e.target.closest("[data-light-arc]"))
        this.runPanelAction(() => this.keyLightTarget(e));
      if (e.target.closest("[data-target-arc]"))
        this.runPanelAction(() => this.keyTemperatureTarget(e));
      if (
        e.key === "Enter" &&
        e.target.matches(
          "#progression-start,#progression-end,#progression-gangs,[data-free-step],[data-free-step-count]",
        )
      ) {
        e.preventDefault();
        this.runPanelAction(() => this.finishFreeProgramInput(e.target));
      }
    });
    this.shadowRoot.addEventListener("pointerdown", (e) => this.beginProgramDrag(e));
    this.shadowRoot.addEventListener("pointermove", (e) => this.updateProgramDrag(e));
    this.shadowRoot.addEventListener("pointerup", (e) => this.finishProgramDrag(e));
    this.shadowRoot.addEventListener("pointercancel", () => this.cancelProgramDrag());
    this.shadowRoot.addEventListener("pointerdown", (e) => {
      const overview = this.eventElement(e, "#history-overview svg");
      if (overview) return this.beginHistoryGesture(e, overview);
      const lightTarget = this.eventElement(e, "[data-light-arc]");
      if (lightTarget) return this.beginLightDrag(e, lightTarget.closest("svg"));
      const target = this.eventElement(e, "[data-target-arc]");
      if (target) return this.beginTemperatureDrag(e, target.closest("svg"), target);
      const svg = this.eventElement(e, "svg.session-chart");
      if (svg) {
        if (e.pointerType !== "mouse") this.beginChartPointer(e, svg);
        this.scheduleHover({ svg, clientX: e.clientX, clientY: e.clientY });
      }
    });
    this.shadowRoot.addEventListener("pointerup", (e) => {
      if (this.lightInteraction?.pointerId === e.pointerId) {
        this.runPanelAction(() => this.endLightDrag(e));
        return;
      }
      if (this.temperatureInteraction?.pointerId === e.pointerId) {
        this.runPanelAction(() => this.endTemperatureDrag(e));
        return;
      }
      if (this.historyGesture?.pointerId === e.pointerId)
        return this.endHistoryGesture(e);
      this.endChartPointer(e);
    });
    this.shadowRoot.addEventListener("pointercancel", (e) => {
      if (this.lightInteraction?.pointerId === e.pointerId)
        return this.cancelLightDrag();
      if (this.temperatureInteraction?.pointerId === e.pointerId)
        return this.cancelTemperatureDrag(e);
      if (this.historyGesture?.pointerId === e.pointerId)
        return this.endHistoryGesture(e);
      this.endChartPointer(e);
    });
    this.shadowRoot.addEventListener("lostpointercapture", (e) => {
      if (this.lightInteraction?.pointerId === e.pointerId) this.cancelLightDrag();
      if (this.temperatureInteraction?.pointerId === e.pointerId)
        this.cancelTemperatureDrag(e);
    });
    this.shadowRoot.addEventListener("pointermove", (e) => {
      if (this.lightInteraction?.pointerId === e.pointerId)
        return this.updateLightDrag(e);
      if (this.temperatureInteraction?.pointerId === e.pointerId)
        return this.updateTemperatureDrag(e);
      if (this.historyGesture?.pointerId === e.pointerId)
        return this.updateHistoryGesture(e);
      const svg = this.eventElement(e, "svg.session-chart");
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
        this.eventElement(e, "svg.session-chart") &&
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
        const svg = this.eventElement(e, "svg.session-chart");
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
      this.runPanelAction(() => this.saveSettings());
    });
    this.shadowRoot.addEventListener(
      "invalid",
      (e) => {
        const section = e.target.closest("[data-settings-section]");
        if (!section) return;
        this.selectSettingsSection(section.dataset.settingsSection);
        for (
          let node = e.target.parentElement;
          node && node !== section;
          node = node.parentElement
        )
          if (node.tagName === "DETAILS") node.open = true;
      },
      true,
    );
  }
  runPanelAction(operation) {
    const entry = this.entry,
      generation = this.generation;
    const report = (error) => {
      if (this.entry === entry && this.generation === generation) this.message(error);
    };
    try {
      Promise.resolve(operation()).catch(report);
    } catch (error) {
      report(error);
    }
  }
  eventElement(event, selector) {
    return (
      event.composedPath?.().find((node) => node?.matches?.(selector)) ||
      event.target?.closest?.(selector) ||
      null
    );
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
  renderStateLoading() {
    const loading = '<p role="status">Lade Saunadaten …</p>';
    for (const selector of ["#current", "#details", "#settings"])
      this.updateMarkup(selector, loading);
    const session = this.$("#session");
    if (session) {
      session.innerHTML = '<option value="live">Letzte Sitzung</option>';
      session.disabled = true;
    }
  }
  acceptState(state) {
    const revision = state.archive_revision ?? 0,
      archiveChanged =
        this.archiveRevision !== undefined && revision !== this.archiveRevision;
    if (archiveChanged) this.clearArchiveCaches();
    this.archiveRevision = revision;
    const previousSessionId = this.state?.session?.timeline?.session_id || null,
      previousLastSessionId = this.state?.last_session?.timeline?.session_id || null;
    this.state = state;
    this.reconcileControlCommands();
    const currentSessionId = state.session?.timeline?.session_id || null,
      lastSessionId = state.last_session?.timeline?.session_id || null;
    if (
      previousSessionId === currentSessionId &&
      previousLastSessionId === lastSessionId
    )
      return;
    this.historyListStale = true;
    if (previousSessionId && !currentSessionId)
      this.historyPendingFinalId = previousSessionId;
    else if (currentSessionId) this.historyPendingFinalId = null;
    if (archiveChanged && previousSessionId !== lastSessionId)
      this.historyPendingFinalId = null;
    if (
      previousLastSessionId !== lastSessionId &&
      lastSessionId &&
      lastSessionId !== this.historyPendingFinalId
    )
      this.historyPendingFinalId = null;
  }
  clearArchiveCaches() {
    this.historySelectionGeneration = (this.historySelectionGeneration || 0) + 1;
    this.historyLoad = null;
    this.cache?.clear();
    this.sessions = null;
    this.archiveAdminSessions = null;
    this.archiveDeletePending = null;
    this.selected = "live";
    this.historyPendingFinalId = null;
    this.historyOptionsSignature = null;
    this.historyListStale = true;
    this.shown = null;
    this.window = null;
    this.chartDataIndex = null;
    this.historyNavigationCache = null;
    this.diagnosticTraceIndexes = new WeakMap();
    this.invalidateHistoryIndex();
    this.clearHistoryDisplay();
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
    const controlRevision = this.controlRevision || 0;
    const appearanceRevision = this.appearanceRevision || 0;
    try {
      if (!this.entry) {
        const instances = await this.api("");
        if (generation !== this.generation || !this.isConnected) return;
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
        entry !== this.entry ||
        !this.isConnected ||
        programRevision !== (this.programRevision || 0) ||
        controlRevision !== (this.controlRevision || 0) ||
        appearanceRevision !== (this.appearanceRevision || 0) ||
        (this.archiveRevision !== undefined &&
          (state.archive_revision ?? 0) < this.archiveRevision)
      )
        return;
      this.acceptState(state);
      this.syncHistoryProjection();
      const sessionSelect = this.$("#session");
      if (sessionSelect) sessionSelect.disabled = false;
      const sessionKey = state.session?.timeline?.session_started_at || null;
      if (sessionKey !== this.controlSessionKey) {
        this.programChoiceOpen = false;
        this.controlSessionKey = sessionKey;
      }
      this.appearanceStale = false;
      this.applyAppearance();
      this.syncNavigation();
      // Status polling adapts to the visible operating state. Archive
      // list/pages are only useful while history is actually on screen.
      const historyVisible = !this.$("#history")?.hidden;
      if (!this.temperatureInteraction && !this.lightInteraction) {
        const restoreTargetFocus = this.shadowRoot.activeElement?.matches?.(
          '[data-target-arc][role="slider"]',
        );
        this.drawCurrent();
        if (restoreTargetFocus) this.$('[data-target-arc][role="slider"]')?.focus?.();
      }
      if (historyVisible) {
        this.syncHistorySessions();
        this.showHistoryCache();
        this.drawHistory("status");
        this.startHistoryLoad();
      }
      this.drawSettings();
      this.syncAppearanceEditor();
      this.message(null, "refresh");
    } catch (error) {
      if (generation !== this.generation) return;
      this.appearanceStale = true;
      this.applyAppearance();
      this.message(error, "refresh");
    } finally {
      this.busy = false;
      if (this.refreshPending) {
        this.refreshPending = false;
        void this.refresh(true);
      } else this.scheduleRefresh();
    }
  }
  historySelectionId() {
    return this.selected === "live"
      ? this.state?.session?.timeline.session_id ||
          this.historyPendingFinalId ||
          this.sessions?.[0]?.session_id
      : this.selected;
  }
  historyProjection() {
    return this.state?.permissions?.admin ? "admin" : "public";
  }
  syncHistoryProjection() {
    const projection = this.historyProjection();
    if (this.historyAccessProjection === projection) return;
    const previous = this.historyAccessProjection;
    this.historyAccessProjection = projection;
    if (previous === undefined) return;
    // Permission-specific cursors and data cannot be reused by another
    // projection. The generation also rejects responses from an earlier stream.
    this.historyProjectionGeneration = (this.historyProjectionGeneration || 0) + 1;
    this.cache.clear();
    this.sessions = null;
    this.historyListStale = true;
    this.historyOptionsSignature = null;
    this.historyLoad = null;
    this.shown = null;
    this.chartDataIndex = null;
    this.historyNavigationCache = null;
    this.invalidateHistoryIndex();
    this.historyTimelineSignature = null;
    this.historyGangKey = this.historyEventKey = this.historyDiagnosticsKey = null;
    this.highlightedEventId = this.pendingEventFocus = this.pendingHover = null;
    this.lastHistoryPointer = null;
    this.historyChart?.interaction.hide();
    if (this.historyChart) {
      this.historyChart.model = null;
      this.historyChart.prepared.clear();
      this.historyChart.phaseKey = null;
    }
    const focused = this.shadowRoot.activeElement;
    if (focused?.closest?.("#event-list, #detection-plots")) focused.blur?.();
    for (const selector of [
      "#gangs",
      "#event-list",
      "#detection-plots",
      "#control-history",
    ])
      this.updateMarkup(selector, "");
  }
  historyCache(id) {
    const accessProjection = this.historyProjection();
    let cache = this.cache.get(id);
    if (cache?.accessProjection && cache.accessProjection !== accessProjection)
      cache = null;
    if (!cache) {
      cache = { records: [], after: 0, pageRunLoaded: false, finalSynced: false };
      this.cache.set(id, cache);
    }
    // Map order is the most recent access order, including warm selections.
    this.cache.delete(id);
    this.cache.set(id, cache);
    cache.accessProjection = accessProjection;
    cache.records ??= [];
    cache.after ??= 0;
    // `loaded` used to mean both "this page run ended" and "this session is
    // final".  Keep it as an alias while callers move to the explicit state.
    cache.pageRunLoaded ??= !!cache.loaded;
    cache.finalSynced ??=
      !!cache.pageRunLoaded &&
      !!cache.session?.ended_at &&
      (cache.measurement_window?.complete ?? true);
    cache.loaded = cache.pageRunLoaded;
    cache.recordIds ??= new Set(
      cache.records
        .map((record) => record.id)
        .filter((id) => id !== undefined && id !== null),
    );
    return cache;
  }
  trimHistoryCaches() {
    const configured = this.state?.frontend_defaults?.history_cache_records,
      budget = Number.isSafeInteger(configured) && configured >= 0 ? configured : 0,
      protectedIds = new Set([
        this.historySelectionId(),
        this.state?.session?.timeline?.session_id,
        this.historyLoad?.sessionId,
      ]);
    let count = 0;
    for (const cache of this.cache.values()) count += cache.records.length;
    for (const [id, cache] of this.cache) {
      if (count <= budget) break;
      if (protectedIds.has(id)) continue;
      this.cache.delete(id);
      count -= cache.records.length;
      if (this.chartDataIndex?.records === cache.records) {
        this.chartDataIndex = null;
        this.historyNavigationCache = null;
      }
    }
  }
  updateHistoryCacheMetadata(cache, page) {
    const measurementWindowSignature = JSON.stringify(page.measurement_window || null);
    if (cache.measurementWindowSignature !== measurementWindowSignature) {
      cache.measurement_window = page.measurement_window;
      cache.measurementWindowSignature = measurementWindowSignature;
      cache.metadataRevision = (cache.metadataRevision || 0) + 1;
    }
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
    return (
      !cache.pageRunLoaded ||
      !cache.finalSynced ||
      cache.measurement_window?.complete === false
    );
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
          measurement_window: cache?.measurement_window,
          phase_projection: live
            ? this.state.phase_projection
            : cache?.phase_projection,
        }
      : null;
    this.trimHistoryCaches();
  }
  syncHistorySessions() {
    const select = this.$("#session");
    if (!select || !this.sessions || !this.state) return;
    const liveId = this.state.session?.timeline.session_id,
      timeZone = localTimeZone(),
      liveLabel =
        this.state.operation_enabled && this.state.session
          ? "Laufende Sitzung"
          : "Letzte Sitzung",
      options =
        `<option value="live">${liveLabel}</option>` +
        this.sessions
          .filter((session) => session.session_id !== liveId)
          .map(
            (session) =>
              `<option value="${esc(session.session_id)}">Sitzung vom ${esc(when(session.started_at, timeZone))}${session.ended_at ? " · beendet" : " · unterbrochen"}</option>`,
          )
          .join("");
    if (this.historyOptionsSignature !== options) {
      select.innerHTML = options;
      this.historyOptionsSignature = options;
    }
    if (select.value !== this.selected) select.value = this.selected;
    this.selectMenu?.syncAttributes(select);
  }
  syncHistoryLoading() {
    const node = this.$("#history-loading");
    if (!node) return;
    node.hidden = !this.historyLoad;
    node.textContent = this.historyLoad ? "Wird geladen …" : "";
    this.$("#plots")?.setAttribute?.("aria-busy", String(!!this.historyLoad));
  }
  startHistoryLoad() {
    this.syncHistoryLoading();
    if (
      !this.state ||
      !this.isConnected ||
      (typeof document !== "undefined" && document.hidden) ||
      this.$("#history")?.hidden
    )
      return;
    this.syncHistoryProjection();
    const entry = this.entry,
      generation = this.generation,
      projection = this.historyProjection(),
      projectionGeneration = this.historyProjectionGeneration || 0,
      selected = this.selected,
      liveId = this.state?.session?.timeline.session_id,
      lastSessionId = this.state?.last_session?.timeline?.session_id,
      selectionGeneration = this.historySelectionGeneration || 0,
      key = `${generation}:${projectionGeneration}:${projection}:${selectionGeneration}:${entry}:${selected}:${liveId || ""}:${lastSessionId || ""}`;
    const listNeeded = this.sessions == null || this.historyListStale,
      initialId = this.historySelectionId(),
      pageNeeded = initialId
        ? this.historyCacheNeedsFetch(this.historyCache(initialId))
        : false;
    if (!listNeeded && !pageNeeded) return;
    if (this.historyLoad?.key === key) return this.historyLoad.promise;
    const load = { key, count: 0, sessionId: initialId };
    this.historyLoad = load;
    this.message(null, "history");
    this.syncHistoryLoading();
    const current = () =>
      this.historyLoad === load &&
      generation === this.generation &&
      projection === this.historyProjection() &&
      projectionGeneration === (this.historyProjectionGeneration || 0) &&
      selectionGeneration === (this.historySelectionGeneration || 0) &&
      entry === this.entry &&
      selected === this.selected &&
      this.isConnected &&
      (typeof document === "undefined" || !document.hidden) &&
      !this.$("#history")?.hidden;
    // A live response remains relevant while its session transitions to the
    // archive.  It is stale only when a different live session replaced it.
    const currentFor = (id) => {
      if (!current()) return false;
      if (selected !== "live") return id === this.historySelectionId();
      const nowLiveId = this.state?.session?.timeline.session_id;
      return nowLiveId === liveId || (liveId && !nowLiveId && id === liveId);
    };
    load.promise = (async () => {
      try {
        if (listNeeded) {
          const list = await this.api(`/${entry}/archive`);
          if (
            !current() ||
            liveId !== this.state?.session?.timeline.session_id ||
            lastSessionId !== this.state?.last_session?.timeline?.session_id
          )
            return;
          this.sessions = list;
          this.historyListStale = false;
          this.syncHistorySessions();
        }
        const id = this.historySelectionId();
        if (!id) {
          this.showHistoryCache();
          this.drawHistory("archive");
          return;
        }
        load.sessionId = id;
        const cache = this.historyCache(id);
        if (this.historyCacheNeedsFetch(cache)) {
          let more;
          do {
            const after = cache.after,
              page = await this.api(
                `/${entry}/archive?session_id=${encodeURIComponent(id)}&projection=history&after=${after}`,
              );
            if (!currentFor(id)) return;
            // Persist each complete page before the next request. A failed
            // later page retries at this cursor and cannot duplicate records.
            const records = Array.isArray(page.records) ? page.records : [],
              continuation = page.next_after;
            if (
              continuation != null &&
              (!Number.isSafeInteger(continuation) || continuation <= after)
            )
              throw Error("Archivabruf ohne Fortschritt. Erneuter Versuch folgt.");
            const { cursorRecords } = this.appendHistoryCacheRecords(
                cache,
                records,
                after,
              ),
              next =
                continuation ??
                Math.max(after, ...cursorRecords.map((record) => record.id));
            this.updateHistoryCacheMetadata(cache, page);
            cache.after = next;
            more = continuation != null;
            cache.pageRunLoaded = !more;
            cache.loaded = cache.pageRunLoaded;
            // A page run can be complete for an open snapshot.  Only a
            // closed final snapshot plus its last page settles the session.
            cache.finalSynced =
              !!cache.pageRunLoaded &&
              !!cache.session?.ended_at &&
              (cache.measurement_window?.complete ?? true);
            load.count = cache.records.length;
            this.showHistoryCache();
            if (!this.$("#history")?.hidden) this.drawHistory("archive");
            // Let input, paint and the independent status poll run between pages.
            if (more) await new Promise((resolve) => setTimeout(resolve, 0));
          } while (more && current());
        } else {
          this.showHistoryCache();
          this.drawHistory("archive");
        }
        if (current()) this.message(null, "history");
      } catch (error) {
        if (current()) {
          this.message(error, "history");
          if (!this.shown) this.clearHistoryDisplay();
        }
      } finally {
        if (this.historyLoad === load) {
          this.historyLoad = null;
          this.syncHistoryLoading();
        }
        this.trimHistoryCaches();
      }
    })();
    return load.promise;
  }
  drawCurrent() {
    if (!this.state) {
      this.renderStateLoading();
      return;
    }
    const s = this.state,
      session = s.session,
      p = s.configuration.parameters,
      now = stamp(s.now),
      active = session?.timeline.active;
    const remaining = (end) =>
      duration(end ? (stamp(end) - now) / 1000 : null, "remaining");
    const latest = session || s.last_session;
    const doorText = session
      ? { open: "Tür offen", closed: "Tür geschlossen" }[session.timeline.door] ||
        "Türstatus noch nicht ermittelt"
      : "Türerkennung ruht";
    const count =
      session?.timeline.completed.filter(gangConfirmed).length ?? s.gang_count;
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
    const measurementPosition =
      s.regulation_temperature_position || s.measurement_positions?.[0] || "upper";
    const measurementHeight = measurementPosition === "lower" ? "unten" : "oben";
    const formatValue = (position, quantity, unit) =>
      `${num(value(position, quantity), 1)} ${unit}`;
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
        ? "Ofen · Ein"
        : s.heating_feedback === false
          ? "Ofen · Aus"
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
    const temperatureScale = this.appearanceScale("temperature");
    const humidityScale = this.appearanceScale("humidity");
    const arcBounds = this.targetArcBounds();
    const presets = [
      ...new Set(
        Array.from(
          { length: p.preset_count },
          (_, i) => p.preset_start_c + i * p.preset_step_c,
        ).flatMap((value) => {
          try {
            return [this.roundTargetTemperature(value, targetBounds)];
          } catch (_error) {
            return [];
          }
        }),
      ),
    ];
    const availability = s.start_availability;
    const minuteEstimate = (seconds) => {
      const minutes = Math.max(0, Number(seconds) || 0) / 60;
      const step = this.frontendStep("warmup_minutes_step");
      if (!step) return "unbekannte Zeit";
      if (minutes < step) return `unter ${num(step)} Minuten`;
      return `${num(Math.max(step, Math.round(minutes / step) * step))} Minuten`;
    };
    const timerLine = s.phase_timer;
    const phaseTimerText =
      s.operation_enabled && timerLine
        ? {
            gang: `seit ${duration(timerLine.seconds)}`,
            after_run:
              timerLine.mode === "pending"
                ? timerLine.label || "Ofenkühlung wird vorbereitet"
                : `${timerLine.mode === "paused" ? "pausiert – noch" : "noch"} ${duration(timerLine.seconds, "remaining")}`,
            cooling: `noch ${duration(timerLine.seconds, "remaining")}`,
          }[timerLine.kind] || ""
        : "";
    const blockerText =
      s.operation_enabled && availability?.blocker && !phaseTimerText
        ? availability.minimum_wait_seconds > 0
          ? `Start gesperrt – noch ${duration(availability.minimum_wait_seconds, "remaining")}`
          : availability.message || "Start derzeit gesperrt."
        : "";
    const readinessText =
      s.operation_enabled &&
      s.phase === "aufheizen" &&
      availability &&
      !availability?.blocker &&
      availability.until_ready_seconds > 0 &&
      availability.ready_estimated
        ? `noch ${minuteEstimate(availability.until_ready_seconds)} bis bereit`
        : "";
    const availabilityLine = phaseTimerText || blockerText || readinessText;
    const notices = s.issues
      .map(
        (i) =>
          `<p>${esc(i.message)}${i.action === "settings" ? '<br><button data-action="configure">Einstellungen öffnen</button>' : ""}</p>`,
      )
      .join("");
    const alert = notices ? `<div class="notice" role="alert">${notices}</div>` : "";
    const finishPhase =
      session?.after_run && s.operation_enabled && !manualMode
        ? `<button class="tile operation" data-action="end-phase:after_run:${esc(encodeURIComponent(session.after_run.phase_id))}" ${permissions.control ? "" : "disabled"}>Kühlung beenden</button>`
        : "";
    const operation = manualMode
      ? ""
      : sessionGap
        ? `<div class="operation-control split"><button class="tile operation stop" data-action="finish-session:${esc(encodeURIComponent(sessionGap.token))}" ${permissions.control ? "" : "disabled"}>Endgültig beenden</button><button class="tile operation primary" data-action="operation"${this.controlCommandAttributes("operation", !s.operation_enabled)} ${canStart ? "" : "disabled"}>${this.controlCommandLabel("operation", true, "Fortsetzen")}</button></div>`
        : `<div class="operation-control${finishPhase ? " split" : ""}"><button class="tile operation ${s.operation_enabled ? "stop" : "primary"}" data-action="operation"${this.controlCommandAttributes("operation", !s.operation_enabled)} ${canStart ? "" : "disabled"}>${this.controlCommandLabel("operation", !s.operation_enabled, s.operation_enabled ? "Ausschalten" : "Einschalten")}</button>${finishPhase}</div>`;
    const phaseLabel =
      s.phase === "aufheizen" ? "Heizen" : phases[s.phase] || "Unbekannt";
    const availabilityHint = availabilityLine;
    const stateLine = `<div class="state-line"><div class="phase-time"><strong class="phase" data-phase="${esc(s.phase)}">${phaseLabel}</strong>${availabilityHint ? `<span class="availability-line ${availability?.blocker ? "wait" : ""}">${esc(availabilityHint)}</span>` : ""}</div>${session ? `<span class="badge">${count} ${count === 1 ? "Saunagang" : "Saunagänge"}</span>` : ""}</div>`;
    const dial = (
      reading,
      unit,
      caption,
      color,
      maximum,
      valid,
      control = "",
      minimum,
      key = unit === "°C" ? "temperature" : "humidity",
    ) =>
      !Number.isFinite(minimum) || !Number.isFinite(maximum)
        ? '<p class="muted">Anzeigeskala nicht verfügbar</p>'
        : `<svg class="dial ${control ? `dial-${key}` : ""}" style="--measurement-color:${color}" viewBox="25 5 250 250" role="${control ? "group" : "img"}" aria-label="${esc(key === "temperature" && control ? "Temperatur und Solltemperatur" : `${caption}: ${num(reading, key === "light" ? 0 : 1)} ${unit}`)}"><defs><linearGradient id="instrument-face-${key}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--sauna-color-card-background, var(--card-background-color))"/><stop offset="1" stop-color="var(--sauna-color-page-background, var(--primary-background-color))"/></linearGradient><linearGradient id="instrument-rim-${key}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="white" stop-opacity=".16"/><stop offset=".5" stop-color="white" stop-opacity=".025"/><stop offset="1" stop-color="black" stop-opacity=".28"/></linearGradient></defs><circle class="instrument-face" cx="150" cy="130" r="119" fill="url(#instrument-face-${key})"/><circle class="instrument-rim" cx="150" cy="130" r="119" stroke="url(#instrument-rim-${key})"/><path d="${temperatureDial.path}" fill="none" stroke="var(--sauna-color-border, var(--divider-color))" stroke-width="10" stroke-linecap="round"/>${Number.isFinite(reading) && reading > minimum ? `<path class="instrument-value-arc" d="${temperatureDial.path}" fill="none" stroke="${valid ? color : this.appearanceColor("status_unknown")}" stroke-width="10" stroke-linecap="round" pathLength="100" stroke-dasharray="${Math.max(0, Math.min(100, ((reading - minimum) / (maximum - minimum || 1)) * 100))} 100"/>` : ""}${this.temperatureTickMarks({ minimum, maximum })}<rect class="instrument-readout" width="0" height="0" rx="6"/><text class="reading" ${key === "light" ? "data-light-observation" : ""} x="150" y="130" text-anchor="middle" dominant-baseline="central"><tspan>${num(reading, key === "light" ? 0 : 1)}</tspan><tspan class="reading-unit" dx="5"> ${unit}</tspan></text>${control || (unit === "%" ? '<path class="humidity-symbol" transform="translate(150 210) scale(.58) translate(-150 -126)" d="M150 104 C146 113 135 121 135 132 A15 15 0 0 0 165 132 C165 121 154 113 150 104 Z"/>' : "")}</svg>`;
    const deadlineLabels = { confirmation: "Aufgussbestätigung" };
    const phaseRemaining = (phase) =>
      Math.max(
        0,
        (phase.duration_seconds ?? 0) -
          (phase.elapsed_seconds ?? 0) -
          (phase.credited_seconds ?? 0),
      );
    const timerRows = [
      ["Heizsumme (gezählt)", duration(session ? session.heating?.elapsed_seconds : 0)],
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
            ? `Noch ${duration(phaseRemaining(phase), "remaining")} bestätigte Auszeit erforderlich`
            : duration(phaseRemaining(phase), "remaining"),
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
        `<span data-phase-timer="${esc(s.phase_timer.kind)}">${duration(s.phase_timer.seconds, s.phase_timer.kind === "gang" ? "elapsed" : "remaining")}</span>`,
        true,
      ]);
    const timers = `<dl class="compact-times">${timerRows.map(([label, value, html]) => `<dt>${esc(label)}</dt><dd>${html ? value : esc(value)}</dd>`).join("")}</dl>`;
    const temperatureColor = this.appearanceColor("series_temperature");
    const humidity = value(measurementPosition, "humidity"),
      humidityColor = this.appearanceColor("series_humidity");
    const targetValue =
        this.temperatureInteraction?.value ??
        this.linearTargetDraft ??
        s.target_temperature,
      targetPoint =
        temperatureScale && this.temperatureArcPoint(targetValue, temperatureScale);
    const targetControl = temperatureScale
      ? `${arcBounds ? `<path class="target-temperature-track" data-target-arc="true" d="${temperatureDial.path}" role="slider" tabindex="${permissions.temperature && !this.programRequest ? 0 : -1}" aria-label="Solltemperatur einstellen" aria-valuemin="${arcBounds.minimum}" aria-valuemax="${arcBounds.maximum}" aria-valuenow="${Math.max(arcBounds.minimum, Math.min(arcBounds.maximum, targetValue))}" aria-valuetext="Soll ${num(targetValue, 1)} °C" aria-disabled="${!permissions.temperature || !!this.programRequest}"/>` : ""}<circle class="target-temperature-handle" ${arcBounds ? 'data-target-arc="true"' : 'data-inert-target="true"'} cx="${targetPoint.x}" cy="${targetPoint.y}" r="9"/><text class="target-caption" x="150" y="193" text-anchor="middle">SOLL</text><text class="target-reading" x="150" y="211" text-anchor="middle" dominant-baseline="central">${num(targetValue, 1)}<tspan class="reading-unit" dx="4">°C</tspan></text>`
      : "";
    const programs = Array.isArray(s.configuration.temperature_programs)
      ? s.configuration.temperature_programs
      : [];
    const programChoice = this.programChoice(programs),
      programMode = this.programMode(programs),
      activeChoice = this.storedProgramChoice(programs),
      activeMode = activeChoice.mode,
      activeProgramLabel = this.programChoiceLabel(activeChoice, programs),
      programBusy = !!this.programRequest,
      programChoiceAvailable = this.programChoiceAvailable();
    const programFeedback = (choice) =>
      !session &&
      this.programChoiceMatches(choice, programChoice) &&
      this.programSaveState
        ? this.programSaveState === "saving"
          ? "Wird übernommen …"
          : "✓ Übernommen"
        : "";
    const choiceContent = (content, feedback) =>
      `<span class="program-choice-content" ${feedback ? 'aria-hidden="true"' : ""}>${content}</span>${feedback ? `<span class="program-save-label" role="status">${feedback}</span>` : ""}`;
    const programTypes = `<div class="program-types" role="group" aria-label="Temperaturprogramm auswählen">${[
      ["program", "Programm"],
      ["individual", "Individuell"],
      ["constant", "Konstant"],
    ]
      .map(([mode, label]) => {
        const feedback = programFeedback({ mode });
        return `<button type="button" data-action="program-mode:${mode}" aria-pressed="${programMode === mode}" aria-label="${label}${feedback ? `: ${feedback}` : ""}" class="${feedback ? `program-feedback program-${this.programSaveState}` : ""}" ${programChoiceAvailable && (mode !== "program" || programs.length) ? "" : "disabled"}>${choiceContent(label, feedback)}</button>`;
      })
      .join("")}</div>`;
    const namedPrograms =
      programMode === "program"
        ? `<div class="program-named-list">${programs
            .map((program) => {
              const feedback = programFeedback({ mode: "program", id: program.id });
              return `<button type="button" class="program-named-choice ${feedback ? `program-feedback program-${this.programSaveState}` : ""}" data-action="program-select:${esc(program.id)}" aria-pressed="${program.id === programChoice.id}" aria-label="${esc(program.name)}${feedback ? `: ${feedback}` : ""}" ${programChoiceAvailable ? "" : "disabled"}>${choiceContent(`<span class="program-choice-summary"><span>${esc(program.name)}</span><small>${num(program.start_c, 0)} → ${num(program.end_c, 0)} °C</small></span><small class="program-choice-details">${esc(this.programSteps(program))}</small>`, feedback)}</button>`;
            })
            .join("")}</div>`
        : "";
    const light = s.manual_controls?.light || {};
    const heater = s.manual_controls?.heater || {};
    const overrideRemaining = (endsAt) =>
      endsAt && stamp(endsAt) > now
        ? remaining(endsAt)
        : "Keine laufende Übersteuerung";
    const lightMode = light.manual == null ? "auto" : light.manual <= 0 ? "off" : "on";
    const lightStatus =
      light.manual == null
        ? manualMode
          ? "Keine Lichtwahl"
          : "Automatik"
        : `Manuell · ${num(light.manual, 0)} %`;
    const manualControls = this.manualControlAvailability(),
      canManualHeater = manualControls.heater,
      canManualLight = manualControls.brightness;
    const modeControls = session
      ? ""
      : `<div class="segmented-mode control-mode" role="group" aria-label="Betriebsmodus"><button data-action="control-mode:automatic" aria-pressed="${!manualMode}"${this.controlCommandAttributes("control-mode", "automatic")} ${permissions.control && !modeLocked ? "" : "disabled"}>${this.controlCommandLabel("control-mode", "automatic", "Automatik")}</button><button data-action="control-mode:manual" aria-pressed="${manualMode}"${this.controlCommandAttributes("control-mode", "manual")} ${permissions.control && !modeLocked ? "" : "disabled"}>${this.controlCommandLabel("control-mode", "manual", "Manuell")}</button></div>`;
    const outputControls = (
      key,
      title,
      selected,
      observed,
      canControl,
      blockedOnReason = "",
    ) =>
      `<section class="manual-section manual-${key}"><div class="manual-heading"><h3>${title}</h3></div><div class="manual-selection">${manualMode ? "" : `<div class="control-auto"><button data-action="${key}:auto" aria-pressed="${selected == null}"${this.controlCommandAttributes(key, null)} ${canControl ? "" : "disabled"}>${this.controlCommandLabel(key, null, "Automatik")}</button></div>`}<div class="output-toggle" role="group" aria-label="${title}">${[
        [false, "Aus"],
        [true, "Ein"],
      ]
        .map(
          ([on, label]) =>
            `<button data-action="${key}:${on}" aria-pressed="${observed === on}"${this.controlCommandAttributes(key, on)}${manualMode && key === "heater" ? ` data-regulation-selected="${(selected === true) === on}" aria-label="Temperaturregelung ${label}${(selected === true) === on ? ", gewählt" : ""}; Ofen ${observed == null ? "unbekannt" : observed ? "Ein" : "Aus"}"` : ""}${on && blockedOnReason ? ` title="${esc(blockedOnReason)}"` : manualMode && key === "heater" ? ` title="Temperaturregelung ${label}${(selected === true) === on ? ", gewählt" : ""}"` : ""} ${canControl && !(on && blockedOnReason) ? "" : "disabled"}>${this.controlCommandLabel(key, on, label)}</button>`,
        )
        .join("")}</div></div></section>`;
    const heaterControls = outputControls(
      "heater",
      "Ofen",
      heater.manual,
      this.outputState("heater"),
      canManualHeater,
      heater.blocked_on_reason,
    );
    const lightControls = outputControls(
      "light",
      "Licht",
      light.manual,
      this.outputState("light"),
      manualControls.light,
    );
    const manualEntry =
      !session && !manualMode
        ? `<section class="manual-section manual-controls manual-entry"><h3>Ofen und Licht</h3><button data-action="manual-entry"${this.controlCommandAttributes("control-mode", "manual")} ${permissions.control && !modeLocked ? "" : "disabled"}>${this.controlCommandLabel("control-mode", "manual", "Manuell steuern")}</button></section>`
        : "";
    const overviewLightTimer =
      !session && s.phase_timer?.kind === "session_light"
        ? `<p class="muted">Lichtnachlauf noch ${duration(s.phase_timer.seconds, "remaining")}</p>`
        : "";
    const programBounds = this.programBounds();
    const activeProgram = programs.find((program) => program.id === activeChoice.id);
    const activeProgramSteps = activeProgram
      ? this.programSteps(activeProgram)
      : Array.isArray(s.configuration.temperature_steps)
        ? `${s.configuration.temperature_steps.map((value) => num(value, 1)).join(" → ")} °C`
        : `${num(p.target_temperature_c, 1)} → ${num(p.final_temperature_c, 1)} °C`;
    // Temperaturautomatik stays the internal CSS/API term; the control uses the shorter label.
    const programEditorOpen =
      !session ||
      !!this.programChoiceOpen ||
      this.programDirty() ||
      !!this.programRequest;
    const programEditorLocked = this.programDirty() || !!this.programRequest;
    const programActionsVisible = session
      ? this.programDirty() || this.programSaveState
      : this.programDirty() && !this.programRequest;
    const temperatureAutomation = !manualMode
      ? `<section class="control-section temperature-automation"><h3>Temperaturwahl</h3>${session ? `<div class="program-current"><div class="program-current-value"><strong class="program-active-label">${esc(activeProgramLabel)}</strong><small>${activeMode === "constant" ? `${num(s.target_temperature, 1)} °C` : esc(activeProgramSteps)}</small></div><button type="button" data-action="program-toggle" aria-expanded="${programEditorOpen}" aria-controls="program-choice-body" ${programEditorLocked ? "disabled" : ""}>${programEditorOpen ? "Schließen" : "Ändern"}</button></div>` : ""}<div id="program-choice-body" ${programEditorOpen ? "" : "hidden"}>${programTypes}${namedPrograms}${programMode === "individual" ? this.freeProgramForm(programBounds) : ""}${programMode === "constant" ? `<div class="temperature-presets" style="--preset-columns-wide:${equalPresetColumns(presets.length, 6)};--preset-columns-medium:${equalPresetColumns(presets.length, 3)};--preset-columns-narrow:${equalPresetColumns(presets.length, 2)}">${presets.map((v) => `<button type="button" class="tile" data-action="preset:${v}" aria-pressed="${Math.abs(v - (programChoice.temperature ?? s.target_temperature)) < 0.01}" ${permissions.temperature && (!session || permissions.program) && !programBusy ? "" : "disabled"}>${num(v, 1)} °C</button>`).join("")}</div>` : ""}${programActionsVisible ? `<div class="program-pending"${this.programDirty() ? "" : ' aria-hidden="true" inert'}><span>Noch nicht übernommen: ${esc(this.programChoiceLabel(programChoice, programs))}</span><button type="button" data-action="program-cancel-draft" ${programBusy ? "disabled" : ""}>Abbrechen</button></div>` : ""}${programActionsVisible ? `<div class="program-actions">${this.programApplyButton(permissions)}</div>` : ""}</div></section>`
      : "";
    this.updateMarkup(
      "#current",
      this.renderControlView({
        modeControls,
        stateLine: manualMode
          ? `<div class="state-line"><div class="phase-time"><strong class="phase" aria-label="Temperaturregelung ${heater.manual === true ? "Ein" : "Aus"}">${heater.manual === true ? "Ein" : "Aus"}</strong></div></div>`
          : stateLine,
        overviewLightTimer,
        operation,
        temperatureAutomation,
        manualMode,
        heaterControls,
        lightControls,
        manualEntry,
        overrideLimitMinutes: num(p.manual_override_minutes, 0),
        heaterOverrideRemaining: overrideRemaining(heater.override_ends_at),
        lightOverrideRemaining: overrideRemaining(light.override_ends_at),
        alert,
        temperatureGauge:
          this.instrumentStyle("temperature") === "linear"
            ? this.linearInstrument({
                key: "temperature",
                reading: value(measurementPosition, "temperature"),
                unit: "°C",
                bounds: temperatureScale,
                valid: quality(measurementPosition, "temperature") === "current",
                control: this.linearTargetControl(targetValue, arcBounds),
              })
            : dial(
                value(measurementPosition, "temperature"),
                "°C",
                `Temperatur ${measurementHeight}`,
                temperatureColor,
                temperatureScale?.maximum,
                quality(measurementPosition, "temperature") === "current",
                targetControl,
                temperatureScale?.minimum,
              ),
        humidityGauge:
          this.instrumentStyle("humidity") === "linear"
            ? this.linearInstrument({
                key: "humidity",
                reading: humidity,
                unit: "%",
                bounds: humidityScale,
                valid: quality(measurementPosition, "humidity") === "current",
              })
            : dial(
                humidity,
                "%",
                `Relative Luftfeuchte ${measurementHeight}`,
                humidityColor,
                humidityScale?.maximum,
                quality(measurementPosition, "humidity") === "current",
                "",
                humidityScale?.minimum,
              ),
        lightGauge: this.lightInstrument(dial, canManualLight),
        weather: this.weatherMarkup(),
      }),
    );
    this.fitInstrumentReadouts();
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
        restartThreshold: num(s.thermostat_restart_temperature, 1),
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
              `<h3>${pos === "upper" ? "Obere" : "Untere"} Messposition</h3><p style="color:var(--sauna-color-series-temperature)">${formatValue(pos, "temperature", "°C")} · ${qualityText(pos, "temperature")}</p><p style="color:var(--sauna-color-series-humidity)">${formatValue(pos, "humidity", "% relative Luftfeuchte")} · ${qualityText(pos, "humidity")}</p>`,
          )
          .join(""),
        detectionText: esc(detectionText),
      }),
    );
  }
  linearInstrument({ key, reading, unit, bounds, valid, control = "" }) {
    if (!bounds) return '<p class="muted">Anzeigeskala nicht verfügbar</p>';
    const fraction = Math.max(
      0,
      Math.min(
        100,
        (((reading ?? bounds.minimum) - bounds.minimum) /
          (bounds.maximum - bounds.minimum)) *
          100,
      ),
    );
    const color = `series_${key}`,
      symbol = {
        temperature:
          '<path d="M9 14.5V5a3 3 0 0 1 6 0v9.5a5 5 0 1 1-6 0Z"/><path d="M12 8v10"/>',
        humidity: '<path d="M12 3C10 7 5 11 5 15a7 7 0 0 0 14 0c0-4-5-8-7-12Z"/>',
        light: '<path d="M9 18h6m-5 3h4M8 15a6 6 0 1 1 8 0v3H8Z"/>',
      }[key];
    return `<div class="linear-instrument" style="--measurement-color:${this.appearanceColor(valid ? color : "status_unknown")}"><div class="linear-reading" ${key === "light" ? "data-light-observation" : ""}>${num(reading, key === "light" ? 0 : 1)} <span>${unit}</span></div><div class="linear-scale"><div class="linear-rail" aria-hidden="true"><i style="width:${fraction}%"></i></div><div class="linear-ticks" aria-hidden="true">${appearanceTickValues(
      bounds,
    )
      .map(
        (value, index, ticks) =>
          `<span data-tick-density="${index === 0 || index === ticks.length - 1 ? "edge" : "detail"}" style="left:${((value - bounds.minimum) / (bounds.maximum - bounds.minimum)) * 100}%">${num(value)}</span>`,
      )
      .join(
        "",
      )}<span data-tick-density="middle" style="left:50%">${num((bounds.minimum + bounds.maximum) / 2)}</span></div>${control}</div><svg class="linear-symbol" viewBox="0 0 24 24" aria-hidden="true">${symbol}</svg></div>`;
  }
  linearTargetControl(value, bounds) {
    if (!bounds) return "";
    const allowed = this.state.permissions?.temperature && !this.programRequest,
      scale = this.appearanceScale("temperature") || bounds;
    return `<div class="instrument-slider"><label for="linear-target-temperature">Soll <output data-linear-target>${num(value)} °C</output></label><input id="linear-target-temperature" class="instrument-range" type="range" min="${scale.minimum}" max="${scale.maximum}" step="${this.temperatureStep("temperature_dial_step_c")}" value="${Math.max(bounds.minimum, Math.min(bounds.maximum, value))}" aria-label="Solltemperatur einstellen" ${allowed ? "" : "disabled"}></div>`;
  }
  async commitLinearTarget(value) {
    const entry = this.entry,
      generation = this.generation,
      revision = this.linearTargetRevision || 0;
    try {
      await this.changeTarget(value);
    } finally {
      if (
        this.entry === entry &&
        this.generation === generation &&
        (this.linearTargetRevision || 0) === revision
      ) {
        this.linearTargetDraft = null;
        this.drawCurrent();
      }
    }
  }
  lightInstrument(dial, allowed) {
    const light = this.state.manual_controls?.light || {},
      observation = light.observation,
      available =
        !!observation?.available && Number.isFinite(observation.brightness_percent),
      reading = available ? observation.brightness_percent : null,
      target = this.lightTargetValue(),
      sliderValue = Number.isFinite(target) ? Math.max(0, Math.min(100, target)) : 0,
      style = this.instrumentStyle("light"),
      point = this.temperatureArcPoint(sliderValue, { minimum: 0, maximum: 100 }),
      symbol = '<path d="M9 18h6m-5 3h4M8 15a6 6 0 1 1 8 0v3H8Z"/>',
      arc = `<path class="instrument-arc-track" data-light-arc="true" d="${temperatureDial.path}" role="slider" tabindex="${allowed ? 0 : -1}" aria-label="Lichthelligkeit einstellen" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${sliderValue}" aria-valuetext="${num(target, 0)} %" aria-disabled="${!allowed}"/><circle class="instrument-arc-handle" data-light-arc="true" cx="${point.x}" cy="${point.y}" r="9"/><g class="light-symbol" transform="translate(138 198)" aria-hidden="true">${symbol}</g>`,
      graphic =
        style === "linear"
          ? this.linearInstrument({
              key: "light",
              reading,
              unit: "%",
              bounds: { minimum: 0, maximum: 100 },
              valid: available,
              control: `<div class="instrument-slider"><input id="manual-light-value-overview" data-manual-light-value class="instrument-range" type="range" min="0" max="100" step="${this.frontendStep("brightness_step_percent")}" value="${sliderValue}" aria-label="Lichthelligkeit einstellen" ${allowed ? "" : "disabled"}></div>`,
            })
          : dial(
              reading,
              "%",
              "Licht",
              this.appearanceColor("series_light"),
              100,
              available,
              arc,
              0,
              "light",
            );
    return `<section class="light-instrument measurement-instrument"${this.pendingControlCommand("light") ? ' aria-busy="true"' : ""} data-instrument="light" data-instrument-style="${style}"><h2>Licht${this.pendingControlCommand("light") ? '<span aria-hidden="true"> …</span><span class="sr-only" role="status"> – Rückmeldung ausstehend</span>' : ""}</h2>${graphic}${available ? "" : '<small class="instrument-notice">Rückmeldung fehlt</small>'}</section>`;
  }
  weatherMarkup() {
    const environment = this.state.environment;
    if (!environment?.configured) return "";
    const labels = {
        "clear-night": "Klare Nacht",
        cloudy: "Bewölkt",
        exceptional: "Außergewöhnliches Wetter",
        fog: "Nebel",
        hail: "Hagel",
        lightning: "Gewitter",
        "lightning-rainy": "Gewitter mit Regen",
        partlycloudy: "Leicht bewölkt",
        pouring: "Starker Regen",
        rainy: "Regen",
        snowy: "Schnee",
        "snowy-rainy": "Schneeregen",
        sunny: "Sonnig",
        windy: "Windig",
        "windy-variant": "Windig und bewölkt",
      },
      condition = environment.condition,
      label = labels[condition] || "Wetterlage nicht verfügbar",
      rainy = ["rainy", "pouring", "lightning-rainy", "snowy-rainy"].includes(
        condition,
      ),
      snowy = ["snowy", "snowy-rainy", "hail"].includes(condition),
      cloud =
        !["sunny", "clear-night", "fog", "windy"].includes(condition) &&
        !!labels[condition],
      sun = ["sunny", "partlycloudy"].includes(condition),
      graphic = `<svg class="weather-picture" viewBox="0 0 120 88" role="img" aria-label="${esc(label)}">${sun ? '<g class="weather-sun"><circle cx="48" cy="33" r="17"/><path d="M48 6v-5m0 59v5M21 33h-6m60 0h6M29 14l-4-4m42 42 4 4M29 52l-4 4m42-42 4-4"/></g>' : ""}${condition === "clear-night" ? '<path class="weather-moon" d="M78 54A30 30 0 0 1 51 12a31 31 0 1 0 27 42Z"/>' : ""}${cloud ? '<path class="weather-cloud" d="M27 61a15 15 0 0 1-1-30 23 23 0 0 1 43-7 18 18 0 0 1 22 18 10 10 0 0 1-1 19Z"/>' : ""}${rainy ? '<path class="weather-rain" d="m38 69-3 8m21-8-3 8m21-8-3 8"/>' : ""}${snowy ? '<path class="weather-snow" d="M42 70v12m-6-6h12m20-6v12m-6-6h12"/>' : ""}${condition?.startsWith("lightning") ? '<path class="weather-lightning" d="m59 52-9 15h10l-8 15"/>' : ""}${condition === "fog" || condition?.startsWith("windy") ? '<path class="weather-wind" d="M18 38h68m-58 12h69M20 62h58"/>' : ""}${!labels[condition] ? '<path class="weather-unknown" d="M30 45h60"/>' : ""}</svg>`,
      metrics = (environment.values || []).filter((metric) =>
        ["temperature", "humidity", "wind_speed", "precipitation"].includes(metric.key),
      ),
      shown = (metric) =>
        metric.available
          ? Number.isFinite(metric.value)
            ? num(metric.value, 1)
            : esc(metric.state)
          : "–";
    return `<section class="weather-instrument" aria-label="Umgebungswetter"><div class="weather-heading"><span>DWD</span><h2>${esc(environment.station?.name || "Umgebungswetter")}</h2>${environment.measurement_time ? `<time datetime="${esc(environment.measurement_time)}" title="Messzeit">${esc(new Date(environment.measurement_time).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }))}</time>` : ""}</div><div class="weather-condition">${graphic}<span>${esc(label)}</span></div><dl class="weather-values">${metrics.map((metric) => `<div data-weather-quantity="${esc(metric.key)}"><dt>${esc(metric.label)}</dt><dd>${shown(metric)} <span>${esc(metric.unit || "")}</span></dd></div>`).join("")}</dl></section>`;
  }
  fitInstrumentReadouts() {
    for (const dial of this.shadowRoot?.querySelectorAll?.("#current .dial") || []) {
      const bounds = dial.querySelector(".reading")?.getBBox?.();
      const readout = dial.querySelector(".instrument-readout");
      if (!readout || !bounds?.width || !bounds?.height) continue;
      for (const [name, value] of Object.entries({
        x: bounds.x - 8,
        y: bounds.y - 4,
        width: bounds.width + 16,
        height: bounds.height + 8,
      }))
        readout.setAttribute(name, value);
    }
  }
  renderControlView(view) {
    const {
      modeControls,
      stateLine,
      overviewLightTimer,
      operation,
      temperatureAutomation,
      heaterControls,
      lightControls,
      manualEntry,
      alert,
      temperatureGauge,
      humidityGauge,
      lightGauge,
      weather,
    } = view;
    const controls =
      manualEntry ||
      `<div class="manual-controls">${heaterControls}${lightControls}</div>`;
    const roundCount = ["temperature", "humidity", "light"].filter(
      (key) => this.instrumentStyle(key) === "round",
    ).length;
    const status = modeControls
      ? `<div class="control-status-row">${stateLine}${modeControls}</div>`
      : stateLine;
    const actions = operation ? `<div class="tiles">${operation}</div>` : "";
    return `<div class="control-overview"><div class="dashboard"><div class="card control-main">${status}${overviewLightTimer}${actions}${temperatureAutomation}${controls}${alert}</div><div class="card gauge-card"><div class="gauges" style="--round-count:${Math.max(1, roundCount)}"><div class="measurement-instrument" data-instrument="temperature" data-instrument-style="${this.instrumentStyle("temperature")}"><h2>Temperatur</h2>${temperatureGauge}</div><div class="measurement-instrument" data-instrument="humidity" data-instrument-style="${this.instrumentStyle("humidity")}"><h2>Luftfeuchte</h2>${humidityGauge}</div>${lightGauge}</div>${weather}</div></div></div>`;
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
          ? "Vorläufiger Hinweis aus Temperatur und Feuchte"
          : item.assertion === "proxy_retraction"
            ? "Indirekter Hinweis zurückgenommen; keine beobachtete Abwesenheit"
            : "Direkte Präsenz";
      const reason = {
        no_proxy_evidence: "Noch kein Personenhinweis",
        source_unavailable: "Messquelle nicht verfügbar",
        unavailable: "Sensor nicht verfügbar",
        unknown: "Sensorzustand unbekannt",
        missing: "Sensor fehlt",
        gang_retracted: "Vorläufiger Gang aufgehoben",
        gang_ended: "Gang beendet",
        ...events,
      }[item.reason];
      return `${occupancy} · ${assertion} · Ereignis ${when(item.effective_at)} · empfangen ${when(item.received_at)}${reason ? ` · ${reason}` : ""}`;
    };
    return `<div class="card"><h2>Präsenz und Regelursache</h2><dl><dt>Eingestellte Personenerkennung</dt><dd>${(this.state.configuration?.presence_source || presence?.configured_source) === "ha_presence" ? "Präsenzsensor" : "Temperatur und Feuchte"}</dd><dt>Wirksame Personenerkennung</dt><dd>${presence?.effective_source === "ha_presence" ? "Präsenzsensor mit Türereignis" : "Temperatur und Feuchte"}</dd><dt>Aktuelle Belegung</dt><dd>${esc(report(presence?.current))}</dd>${Object.entries(
      presence?.external || {},
    )
      .map(([source, item]) => {
        const leading =
          presence?.effective_source === "ha_presence" &&
          source === this.state.configuration?.bindings?.presence;
        return `<dt>${leading ? "Führender Präsenzsensor" : "Beobachtete Quelle"} · ${esc(source)}</dt><dd>${esc(report(item))}</dd>`;
      })
      .join(
        "",
      )}<dt>Heizanforderung im Saunagang</dt><dd>${rules?.gang_heat_demand ? "Aktiv" : "Inaktiv"}</dd><dt>Temporäres Heizen nach Türschluss</dt><dd>${rules?.temporary_door_heat ? "Aktiv" : "Inaktiv"}</dd><dt>Ofenkühlung</dt><dd>${rules?.cooling ? "Aktiv" : "Inaktiv"}</dd></dl></div>`;
  }
  async changeTarget(value) {
    if (!this.state?.permissions?.temperature) return;
    value = this.roundTargetTemperature(value);
    if (this.programRequest) return;
    this.programSelectionDraft = { mode: "constant" };
    this.freeProgramKind = null;
    this.freeProgramStepsDraft = null;
    this.programSaveState = null;
    clearTimeout(this.programSavedTimer);
    const entry = this.entry,
      generation = this.generation,
      previous = this.temperatureChange,
      draft = this.progressionDraft;
    const change = (async () => {
      // Queue ordering survives a rejected predecessor; this choice still
      // reports its own failure to the action that submitted it.
      await previous?.catch(() => null);
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
      if (this.programSelectionDraft?.mode === "constant")
        this.programSelectionDraft = null;
      if (this.progressionDraft === draft) this.progressionDraft = null;
      this.programChoiceOpen = false;
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
  frontendStep(key) {
    const value = this.state?.frontend_defaults?.[key];
    return Number.isFinite(value) && value > 0 ? value : null;
  }
  lightFeedback() {
    const light = this.state?.manual_controls?.light || {};
    const observation = light.observation;
    if (observation?.available && Number.isFinite(observation.brightness_percent))
      return `Licht ${num(observation.brightness_percent, 0)} %`;
    const target = light.manual ?? light.automatic;
    return Number.isFinite(target)
      ? `Lichtvorgabe ${num(target, 0)} % · Rückmeldung nicht verfügbar`
      : "Licht unbekannt · Rückmeldung nicht verfügbar";
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
    const step = this.temperatureStep("temperature_dial_step_c");
    if (!step) return null;
    const minimum = Math.ceil(Math.max(target.minimum, display.minimum) / step) * step,
      maximum = Math.floor(Math.min(target.maximum, display.maximum) / step) * step;
    return maximum >= minimum ? { minimum, maximum } : null;
  }
  clampArcTemperature(value, bounds = this.targetArcBounds()) {
    const step = this.temperatureStep("temperature_dial_step_c");
    if (!bounds || !step) return null;
    const minimum = Math.ceil(bounds.minimum),
      maximum = Math.floor(bounds.maximum);
    if (maximum < minimum) return null;
    const number = Number(value),
      rounded =
        Math.round((Number.isFinite(number) ? number : bounds.minimum) / step) * step;
    return Math.max(minimum, Math.min(maximum, rounded));
  }
  temperatureStep(key = "temperature_step_c") {
    const step = this.frontendStep(key);
    return step ? Math.max(1, Math.round(step)) : null;
  }
  roundTargetTemperature(value, bounds = this.temperatureBounds()) {
    const rounded = Math.round(value);
    if (
      !Number.isFinite(value) ||
      (bounds &&
        [value, rounded].some(
          (temperature) => temperature < bounds.minimum || temperature > bounds.maximum,
        ))
    )
      throw Error("Solltemperatur innerhalb der zulässigen Grenzen eingeben");
    return rounded;
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
      count = Number(gangs),
      rawFirst = Number(start),
      rawLast = Number(end);
    if (
      !Number.isFinite(maximum) ||
      (typeof start === "string" && !start.trim()) ||
      (typeof end === "string" && !end.trim()) ||
      !Number.isFinite(rawFirst) ||
      !Number.isFinite(rawLast) ||
      !Number.isInteger(count) ||
      count < 1 ||
      count > maximum
    )
      return [];
    let first, last;
    try {
      first = this.roundTargetTemperature(rawFirst);
      last = this.roundTargetTemperature(rawLast);
    } catch (_error) {
      return [];
    }
    return count === 1
      ? first === last
        ? [first]
        : [first, last]
      : Array.from({ length: count }, (_, index) =>
          this.roundTargetTemperature(first + ((last - first) * index) / (count - 1)),
        );
  }
  programChoice(programs = []) {
    const choice = this.programSelectionDraft ?? this.storedProgramChoice(programs);
    return ["constant", "individual"].includes(choice.mode) ||
      (choice.mode === "program" &&
        programs.some((program) => program.id === choice.id))
      ? choice
      : this.storedProgramChoice(programs);
  }
  programMode(programs = []) {
    return this.programChoice(programs).mode;
  }
  programChoiceLabel(choice, programs = []) {
    return choice.mode === "program"
      ? programs.find((program) => program.id === choice.id)?.name || "Individuell"
      : choice.mode === "constant"
        ? "Konstant"
        : "Individuell";
  }
  programChoiceMatches(left, right) {
    return left?.mode === right?.mode && left?.id === right?.id;
  }
  programChoiceAvailable() {
    return (
      !!this.state?.permissions?.program &&
      (!this.programRequest || !!this.programRequest.submission?.automatic)
    );
  }
  selectProgramMode(mode, programs = []) {
    const current = this.programChoice(programs);
    if (mode === "program") {
      if (!programs.length) return;
      if (current.mode !== "program")
        this.programSelectionDraft = {
          mode: "program",
          id:
            programs.find((program) => program.id === this.lastNamedProgramId)?.id ||
            programs.find(
              (program) =>
                program.id === this.state?.configuration?.selected_program_id,
            )?.id ||
            programs[0].id,
        };
    } else if (mode === "individual" || mode === "constant") {
      if (
        mode === "individual" &&
        current.mode !== "individual" &&
        this.programRequest?.submission?.automatic
      ) {
        // Keep the form shown at this choice stable if the earlier response
        // changes the stored program's values or input kind.
        const steps = this.freeSteps(),
          kind =
            this.freeProgramKind ||
            (Array.isArray(this.state.configuration.temperature_steps)
              ? "steps"
              : "even");
        if (kind === "steps") this.freeProgramStepsDraft = [...steps];
        else
          this.progressionDraft = {
            "progression-start": String(steps[0]),
            "progression-end": String(steps.at(-1)),
            "progression-gangs": String(steps.length),
            ...this.progressionDraft,
          };
        this.freeProgramKind = kind;
      }
      if (current.mode !== mode) this.programSelectionDraft = { mode };
    } else throw Error("Ungültige Temperaturwahl");
    if (this.programChoice(programs).mode === "program")
      this.lastNamedProgramId = this.programChoice(programs).id;
    this.markProgramEdited();
  }
  selectNamedProgram(id, programs = []) {
    if (!programs.some((program) => program.id === id))
      throw Error("Unbekanntes Temperaturprogramm");
    if (
      !this.programChoiceMatches(this.programChoice(programs), {
        mode: "program",
        id,
      })
    )
      this.programSelectionDraft = { mode: "program", id };
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
  finishFreeProgramInput(input) {
    if (!this.programChoiceAvailable()) return;
    if (input.matches("[data-free-step-count]")) {
      if (this.progressionDraft?.[input.id] !== input.value)
        this.progressionDraft = { ...this.progressionDraft, [input.id]: input.value };
      const count = this.freeProgramStepCount(input.value),
        steps = this.freeSteps();
      if (count !== steps.length)
        this.freeProgramStepsDraft = this.resizeSteps(steps, count);
      this.drawCurrent();
    }
    if (this.state.session) this.markProgramEdited();
    else return this.applyProgram();
  }
  programSubmission() {
    const choice = this.programChoice(
        this.state.configuration.temperature_programs || [],
      ),
      kind =
        this.freeProgramKind ||
        (Array.isArray(this.state.configuration.temperature_steps) ? "steps" : "even"),
      values =
        choice.mode !== "individual"
          ? null
          : kind === "steps"
            ? this.freeProgramValues()
            : this.progressionValues(),
      draft = {
        selection: this.programSelectionDraft,
        progression: this.progressionDraft,
        steps: this.freeProgramStepsDraft,
        kind: this.freeProgramKind,
      };
    return {
      choice: { ...choice },
      kind,
      values,
      draft,
      automatic: !this.state.session,
      key: JSON.stringify(
        choice.mode === "individual" ? [choice, kind, values] : [choice],
      ),
    };
  }
  async applyProgram(submission = null) {
    if (!this.state?.permissions?.program || this.isConnected === false) return;
    if (!submission) {
      if (!this.programRequest && !this.programDirty()) return;
      submission = this.programSubmission();
    }
    if (this.programRequest) {
      if (submission.automatic) {
        this.programPendingApply =
          submission.key === this.programRequest.submission.key ? null : submission;
        if (!this.programPendingApply)
          this.programRequest.submission.draft = submission.draft;
      }
      return;
    }
    if (submission.automatic && this.state.session) {
      this.programPendingApply = null;
      this.programSaveState = null;
      this.programChoiceOpen = true;
      this.drawCurrent();
      return;
    }
    const entry = this.entry,
      generation = this.generation,
      configuration = this.state.configuration,
      { choice, draft } = submission,
      previousTarget = this.temperatureChange,
      draftUnchanged = () => {
        const latest = submission.draft;
        return (
          this.programSelectionDraft === latest.selection &&
          this.progressionDraft === latest.progression &&
          this.freeProgramStepsDraft === latest.steps &&
          this.freeProgramKind === latest.kind
        );
      };
    let path = "program",
      body,
      nextSubmission;
    if (choice.mode === "constant" && Number.isFinite(choice.temperature)) {
      path = "temperature";
      body = { target_temperature_c: this.roundTargetTemperature(choice.temperature) };
    } else if (choice.mode !== "individual")
      body = { profile: choice.mode === "program" ? choice.id : "constant" };
    else if (submission.kind === "steps")
      body = { temperature_steps: submission.values };
    else {
      const { start, end, gangs } = submission.values;
      const explicitStart = Object.hasOwn(draft.progression || {}, "progression-start");
      const liveEdit =
        configuration.program_mode === "progressive" &&
        configuration.selected_program_id == null &&
        !Array.isArray(configuration.temperature_steps) &&
        draft.kind == null &&
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
    const request = { entry, generation, submission };
    this.programRequest = request;
    this.programSaveState = "saving";
    clearTimeout(this.programSavedTimer);
    this.drawCurrent();
    try {
      const targetParameters = await previousTarget?.catch(() => null);
      if (
        this.programRequest !== request ||
        this.entry !== entry ||
        this.generation !== generation ||
        this.isConnected === false
      )
        return;
      if (submission.automatic && this.state.session) {
        this.programRequest = null;
        this.programPendingApply = null;
        this.programSaveState = null;
        this.programChoiceOpen = true;
        this.drawCurrent();
        return;
      }
      if (body.target_temperature_c === null) {
        body.target_temperature_c = Number(
          targetParameters?.target_temperature_c ??
            this.state.configuration.parameters.target_temperature_c,
        );
        if (!Number.isFinite(body.target_temperature_c))
          throw Error("Starttemperatur konnte nicht übernommen werden");
      }
      const saved = await this.api(`/${entry}/${path}`, "POST", body);
      if (
        this.programRequest !== request ||
        this.entry !== entry ||
        this.generation !== generation ||
        this.isConnected === false
      )
        return;
      const accepted = {
        parameters: saved?.parameters || { ...configuration.parameters, ...body },
        program_mode:
          saved?.program_mode ||
          (path === "temperature" && choice.mode !== "constant"
            ? "progressive"
            : choice.mode === "constant"
              ? "constant"
              : "progressive"),
        temperature_steps: Object.hasOwn(saved || {}, "temperature_steps")
          ? saved.temperature_steps
          : body.temperature_steps || null,
        selected_program_id: Object.hasOwn(saved || {}, "selected_program_id")
          ? saved.selected_program_id
          : choice.mode === "program"
            ? choice.id
            : null,
      };
      this.programRevision = (this.programRevision || 0) + 1;
      this.state.configuration = {
        ...this.state.configuration,
        ...accepted,
        parameters: { ...this.state.configuration.parameters, ...accepted.parameters },
      };
      if (choice.mode === "constant" && Number.isFinite(choice.temperature))
        this.state.target_temperature = accepted.parameters.target_temperature_c;
      const unchanged = draftUnchanged(),
        pending = this.programPendingApply;
      if (unchanged) {
        this.programSelectionDraft = null;
        this.freeProgramKind = null;
        this.freeProgramStepsDraft = null;
        this.progressionDraft = null;
        this.message(null);
      }
      this.programRequest = null;
      this.programPendingApply = null;
      this.programSaveState = unchanged ? "saved" : null;
      nextSubmission = pending;
      this.drawCurrent();
      if (unchanged && !pending)
        this.programSavedTimer = setTimeout(() => {
          if (
            this.entry !== entry ||
            this.generation !== generation ||
            this.programRequest
          )
            return;
          this.programSaveState = null;
          if (this.state?.session && !this.programDirty())
            this.programChoiceOpen = false;
          this.drawCurrent();
        }, 2000);
    } catch (error) {
      if (
        this.programRequest !== request ||
        this.entry !== entry ||
        this.generation !== generation ||
        this.isConnected === false
      )
        return;
      if (this.programRequest === request) {
        this.programRequest = null;
        this.programPendingApply = null;
        this.programSaveState = null;
        this.drawCurrent();
      }
      throw error;
    }
    if (nextSubmission) return this.applyProgram(nextSubmission);
    await this.refresh(true);
  }
  storedProgramChoice(programs = []) {
    const configuration = this.state?.configuration || {};
    if (programs.some((program) => program.id === configuration.selected_program_id))
      return { mode: "program", id: configuration.selected_program_id };
    return {
      mode: configuration.program_mode === "progressive" ? "individual" : "constant",
    };
  }
  valuesEqual(left, right) {
    return (
      left.length === right.length &&
      left.every((value, index) => Number(value) === Number(right[index]))
    );
  }
  markProgramEdited() {
    this.programSaveState = this.programRequest ? "saving" : null;
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
      choice = this.programChoice(programs),
      stored = this.storedProgramChoice(programs);
    if (choice.mode !== stored.mode || choice.id !== stored.id) return true;
    if (choice.mode === "constant")
      return (
        Number.isFinite(choice.temperature) &&
        choice.temperature !== this.state.target_temperature
      );
    if (choice.mode !== "individual") return false;
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
  freeProgramForm(bounds) {
    const kind =
        this.freeProgramKind ||
        (Array.isArray(this.state?.configuration?.temperature_steps)
          ? "steps"
          : "even"),
      steps = this.freeSteps(),
      disabled = this.programChoiceAvailable() ? "" : "disabled";
    const manual = kind === "steps",
      count = Math.max(1, Math.min(bounds.gangMaximum, steps.length));
    const values = {
      start: this.progressionDraft?.["progression-start"] ?? steps[0],
      end: this.progressionDraft?.["progression-end"] ?? steps.at(-1),
      gangs: this.progressionDraft?.["progression-gangs"] ?? count,
    };
    let distribution = null;
    if (!manual) {
      try {
        const { start, end, gangs } = this.progressionValues(values);
        distribution = this.distributedSteps(start, end, gangs);
      } catch (_error) {
        // Keep incomplete fields visible without explaining an older draft.
      }
    }
    const kindButtons = `<div class="program-kind"><button type="button" data-action="program-kind:even" aria-pressed="${!manual}" ${disabled}>Gleichmäßig</button><button type="button" data-action="program-kind:steps" aria-pressed="${manual}" ${disabled}>Einzelne Stufen</button></div>`;
    const fields = manual
      ? `<div class="row"><label class="field" for="free-step-count"><span>Anzahl der Temperaturstufen ${this.distributionInfo("free", steps)}</span><input id="free-step-count" data-free-step-count type="number" min="1" max="${bounds.gangMaximum}" step="1" value="${esc(this.progressionDraft?.["free-step-count"] ?? count)}" ${disabled}></label></div><div class="program-step-fields">${this.resizeSteps(
          steps,
          count,
        )
          .map(
            (value, index) =>
              `<label class="field" for="free-step-${index}">Stufe ${index + 1}${unitInput(`<input id="free-step-${index}" data-free-step="${index}" type="number" aria-label="Stufe ${index + 1} (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(value)}" ${disabled}>`, "°C")}</label>`,
          )
          .join("")}</div>`
      : `<div class="row"><label class="field" for="progression-start">Starttemperatur${unitInput(`<input id="progression-start" type="number" aria-label="Starttemperatur (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(values.start)}" ${disabled}>`, "°C")}</label><label class="field" for="progression-end">Endtemperatur${unitInput(`<input id="progression-end" type="number" aria-label="Endtemperatur (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(values.end)}" ${disabled}>`, "°C")}</label><label class="field" for="progression-gangs"><span>Verteilung auf Saunagänge ${this.distributionInfo("free", distribution)}</span><input id="progression-gangs" type="number" step="1" min="${bounds.gangMinimum}" max="${bounds.gangMaximum}" value="${esc(values.gangs)}" ${disabled}></label></div>`;
    return `<div class="program-form">${kindButtons}${fields}</div>`;
  }
  distributionInfo(id, steps) {
    const text = steps?.length
      ? `${steps.length} Temperaturstufen: ${steps.map((value) => num(value, 1)).join(" → ")} °C`
      : "Start, Ende und Verteilung innerhalb der zulässigen Grenzen eingeben";
    return this.infoButton(id, "Verteilung erklären", text);
  }
  infoButton(id, label, text, descriptionId = `info-${id}`) {
    const open = this.programInfoOpen === id;
    return `<span class="program-info"><button type="button" data-action="program-info:${esc(id)}" aria-label="${esc(label)}" aria-controls="${esc(descriptionId)}" aria-expanded="${open}">i</button><span class="program-info-popup" popover="manual" id="${esc(descriptionId)}" ${open ? "" : "hidden"}>${esc(text)}</span></span>`;
  }
  syncInfo() {
    if (!this.programInfoOpen) return;
    const button = [...this.shadowRoot.querySelectorAll(".program-info button")].find(
      (node) => node.dataset.action === `program-info:${this.programInfoOpen}`,
    );
    if (!button?.getClientRects().length) return this.closeInfo();
    const popup = button.nextElementSibling;
    button.setAttribute("aria-expanded", "true");
    popup.hidden = false;
    if (popup.showPopover && !popup.matches(":popover-open")) popup.showPopover();
    const win = this.ownerDocument.defaultView,
      viewport = win.visualViewport,
      width = viewport?.width || win.innerWidth,
      height = viewport?.height || win.innerHeight,
      left = viewport?.offsetLeft || 0,
      top = viewport?.offsetTop || 0,
      rect = button.getBoundingClientRect(),
      gap = 6,
      below = top + height - rect.bottom - gap,
      above = rect.top - top - gap;
    popup.style.maxWidth = `${Math.max(0, width - gap * 2)}px`;
    popup.style.maxHeight = `${Math.max(0, Math.max(above, below) - gap)}px`;
    const box = popup.getBoundingClientRect();
    popup.style.left = `${Math.max(left + gap, Math.min(rect.left, left + width - box.width - gap))}px`;
    popup.style.top = `${below >= box.height ? rect.bottom + gap : Math.max(top + gap, rect.top - box.height - gap)}px`;
  }
  closeInfo(focus = false) {
    const id = this.programInfoOpen;
    this.programInfoOpen = null;
    for (const button of this.shadowRoot?.querySelectorAll?.(".program-info button") ||
      []) {
      button.setAttribute("aria-expanded", "false");
      if (
        button.nextElementSibling.showPopover &&
        button.nextElementSibling.matches(":popover-open")
      )
        button.nextElementSibling.hidePopover();
      button.nextElementSibling.hidden = true;
      if (focus && button.dataset.action === `program-info:${id}`)
        button.focus({ preventScroll: true });
    }
  }
  setFreeProgramKind(kind) {
    if (!["even", "steps"].includes(kind)) throw Error("Ungültige Eingabeart");
    const current =
      this.freeProgramKind ||
      (Array.isArray(this.state?.configuration?.temperature_steps) ? "steps" : "even");
    if (kind === current) return;
    const inputs = [...this.shadowRoot.querySelectorAll("[data-free-step]")];
    if (inputs.length) this.freeProgramStepsDraft = inputs.map((input) => input.value);
    else if (this.$("#progression-start")) {
      const { start, end, gangs } = this.progressionValues();
      this.freeProgramStepsDraft = this.distributedSteps(start, end, gangs);
    }
    this.progressionDraft = null;
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
      (manual && values.length !== this.freeProgramStepCount()) ||
      values.length > bounds.gangMaximum ||
      values.some(
        (value) =>
          !Number.isFinite(value) || value < bounds.minimum || value > bounds.maximum,
      )
    )
      throw Error("Temperaturstufen innerhalb der zulässigen Grenzen eingeben");
    return values.map((value) => this.roundTargetTemperature(value, bounds));
  }
  freeProgramStepCount(
    value = this.progressionDraft?.["free-step-count"] ??
      this.$("#free-step-count")?.value ??
      this.freeSteps().length,
  ) {
    const count = Number(value),
      maximum = this.programBounds().gangMaximum;
    if (
      !String(value ?? "").trim() ||
      !Number.isInteger(count) ||
      !Number.isFinite(maximum) ||
      count < 1 ||
      count > maximum
    )
      throw Error("Gültige Stufenzahl innerhalb der zulässigen Grenzen eingeben");
    return count;
  }
  progressionValues(
    values = {
      start: this.$("#progression-start").value,
      end: this.$("#progression-end").value,
      gangs: this.$("#progression-gangs").value,
    },
  ) {
    const start = Number(values.start),
      end = Number(values.end),
      gangs = Number(values.gangs);
    const { minimum, maximum, gangMinimum, gangMaximum } = this.programBounds();
    if (
      Object.values(values).some((value) => !String(value ?? "").trim()) ||
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
    return {
      start: this.roundTargetTemperature(start, { minimum, maximum }),
      end: this.roundTargetTemperature(end, { minimum, maximum }),
      gangs,
    };
  }
  clampTemperature(value, bounds = this.temperatureBounds()) {
    if (!bounds) return null;
    const minimum = Math.ceil(bounds.minimum),
      maximum = Math.floor(bounds.maximum);
    if (maximum < minimum) return null;
    const number = Number(value),
      clamped = Math.max(
        minimum,
        Math.min(maximum, Number.isFinite(number) ? number : minimum),
      );
    const step = this.temperatureStep();
    if (!step) return null;
    const rounded = Math.round(clamped / step) * step;
    return this.roundTargetTemperature(Math.max(minimum, Math.min(maximum, rounded)), {
      minimum,
      maximum,
    });
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
    const values = this.temperatureTickValues(bounds);
    const minor = values
      .slice(1)
      .flatMap((value, i) =>
        [1, 2, 3, 4].map((part) => {
          const position = values[i] + ((value - values[i]) * part) / 5;
          const a = this.temperatureArcPoint(
            position,
            bounds,
            temperatureDial.radius - 12,
          );
          const b = this.temperatureArcPoint(
            position,
            bounds,
            temperatureDial.radius - 16,
          );
          return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="var(--measurement-color)" opacity=".45" stroke-width="1"/>`;
        }),
      )
      .join("");
    return (
      minor +
      values
        .map((value) => {
          const tick = this.temperatureArcPoint(
              value,
              bounds,
              temperatureDial.radius - 12,
            ),
            label = this.temperatureArcPoint(
              value,
              bounds,
              temperatureDial.radius - 29,
            );
          return `<line x1="${tick.x}" y1="${tick.y}" x2="${this.temperatureArcPoint(value, bounds, temperatureDial.radius - 20).x}" y2="${this.temperatureArcPoint(value, bounds, temperatureDial.radius - 20).y}" stroke="var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)))" stroke-width="2"/><text class="tick" x="${label.x}" y="${Number(label.y) + 4}" text-anchor="middle">${formatTick(value)}</text>`;
        })
        .join("")
    );
  }
  instrumentArcFractionAt(svg, clientX, clientY) {
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
    return (
      (onArc - temperatureDial.startAngle) /
      (temperatureDial.endAngle - temperatureDial.startAngle)
    );
  }
  temperatureValueAt(svg, clientX, clientY) {
    const bounds = this.appearanceScale("temperature");
    const allowed = this.targetArcBounds();
    if (!bounds || !allowed) return null;
    return this.clampArcTemperature(
      bounds.minimum +
        this.instrumentArcFractionAt(svg, clientX, clientY) *
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
    track.setAttribute("aria-valuetext", `${num(value, 1)} °C`);
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
  cancelTemperatureDrag(event, redraw = true) {
    const interaction = this.temperatureInteraction;
    if (!interaction) return;
    // Clear first: releasing capture can synchronously report capture loss.
    this.temperatureInteraction = null;
    if (interaction.svg?.hasPointerCapture?.(interaction.pointerId))
      interaction.svg.releasePointerCapture?.(interaction.pointerId);
    if (redraw) this.drawCurrent();
  }
  async endTemperatureDrag(event) {
    const interaction = this.temperatureInteraction;
    if (!interaction) return;
    interaction.pointerId = null;
    interaction.committing = true;
    interaction.svg.releasePointerCapture?.(event.pointerId);
    try {
      await this.changeTarget(interaction.value);
    } finally {
      if (this.temperatureInteraction === interaction) {
        this.temperatureInteraction = null;
        this.drawCurrent();
      }
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
    const current = this.clampArcTemperature(this.state.target_temperature, bounds),
      step = this.temperatureStep("temperature_dial_step_c");
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
    const value = this.clampArcTemperature(next, bounds);
    const interaction = { value, committing: true };
    this.temperatureInteraction = interaction;
    this.renderTemperatureTarget(value);
    try {
      await this.changeTarget(value);
    } finally {
      if (this.temperatureInteraction === interaction) {
        this.temperatureInteraction = null;
        this.drawCurrent();
        this.$('[data-target-arc][role="slider"]')?.focus?.();
      }
    }
  }
  async updateParameters(parameters, start = false, partial = false, method = "POST") {
    if (!this.state) return;
    parameters = { ...parameters };
    for (const key of [
      "target_temperature_c",
      "final_temperature_c",
      "preset_start_c",
    ]) {
      if (!Object.hasOwn(parameters, key) || parameters[key] == null) continue;
      const definition = this.state.parameters?.find((item) => item.key === key);
      parameters[key] = this.roundTargetTemperature(
        parameters[key],
        definition || this.temperatureBounds(),
      );
    }
    const entry = this.entry,
      generation = this.generation,
      editRevision = this.settingsEditRevision || 0,
      progressionDraft = this.progressionDraft,
      request = (this.settingsRequestSerial = (this.settingsRequestSerial || 0) + 1);
    const saved = await this.api(
      `/${entry}/${partial ? "temperature" : "parameters"}`,
      method,
      parameters,
    );
    parameters = saved.parameters;
    await this.waitForConfiguration(entry, parameters);
    if (
      this.entry !== entry ||
      this.generation !== generation ||
      this.settingsRequestSerial !== request
    )
      return;
    if ((this.settingsEditRevision || 0) === editRevision) {
      this.settingsEntry = null;
      this.shadowRoot
        .querySelectorAll("#parameters input[data-edited]")
        .forEach((input) => delete input.dataset.edited);
    }
    if (this.progressionDraft === progressionDraft) this.progressionDraft = null;
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
  clearHistoryDisplay() {
    this.historyChart?.destroy();
    this.historyChart = null;
    this.$("#history-overview").replaceChildren();
    const readout = this.$("#tooltip");
    if (readout) {
      readout.replaceChildren();
      readout.hidden = true;
      readout.setAttribute("data-phase", "");
    }
    this.updateMarkup(
      "#plots",
      this.messages?.history
        ? '<div class="card empty">Sitzungsverlauf konnte nicht geladen werden.</div>'
        : this.historySelectionId() || this.historyLoad || this.sessions == null
          ? '<div class="card empty" role="status">Lade Sitzungsverlauf …</div>'
          : '<div class="card empty">Noch keine Sitzungsdaten.</div>',
    );
    for (const selector of [
      "#gangs",
      "#event-list",
      "#detection-plots",
      "#control-history",
    ])
      this.updateMarkup(selector, "");
    this.historyGangKey = this.historyEventKey = this.historyDiagnosticsKey = null;
  }
  renderControlHistory() {
    const { session, records, phase_projection: projection } = this.shown;
    const timeline = session.timeline;
    const start = stamp(timeline.session_started_at);
    const end = Math.max(start + 1000, stamp(session.ended_at || this.state.now));
    if (this.controlHistorySession !== timeline.session_id) {
      this.controlHistorySession = timeline.session_id;
      this.controlHistoryCursor = null;
    }
    const cursor = Math.max(start, Math.min(end, this.controlHistoryCursor ?? end));
    const pct = (at) => (100 * (at - start)) / (end - start);
    const neutral = "var(--sauna-color-card-background, var(--card-background-color))";
    const color = (role) => `var(--sauna-color-${role})`;
    const changes = (items, initial = "Unbekannt", initialColor = null) => {
      const sorted = items
        .filter((v) => Number.isFinite(v.at))
        .sort((a, b) => a.at - b.at);
      const spans = [];
      let previous = { at: start, label: initial, color: initialColor };
      for (const item of sorted) {
        if (item.at > end) break;
        if (item.at > start) spans.push({ ...previous, end: item.at });
        previous = { ...item, at: Math.max(start, item.at) };
      }
      spans.push({ ...previous, end });
      return spans.filter((v) => v.end >= v.at);
    };
    const intervals = (items, label, role) =>
      changes(
        items.flatMap((item) => [
          { at: stamp(item.started_at), label, color: color(role) },
          ...(item.ended_at
            ? [{ at: stamp(item.ended_at), label: "Inaktiv", color: neutral }]
            : []),
        ]),
        "Inaktiv",
        neutral,
      );
    const configured = session.configuration || this.state.configuration;
    const entity = configured?.bindings?.presence;
    const reports = records.flatMap((r) =>
      r.kind === "presence"
        ? [r.payload]
        : r.kind === "presence_source"
          ? Object.values(r.payload.external_snapshot || {})
          : [],
    );
    const direct = reports.filter(
      (r) => r.assertion === "direct_presence" && (!entity || r.source === entity),
    );
    const occupancy = (r) =>
      !r.available
        ? "Nicht verfügbar"
        : { present: "Anwesend", absent: "Abwesend" }[r.occupancy] || "Unbekannt";
    const decisions = records
      .filter((r) => r.kind === "decision")
      .map((r) => r.payload);
    const cause = (reason) => {
      if (reason?.startsWith("protection:")) return "Bestätigte technische Störung";
      if (reason?.startsWith("inhibit:")) return "Einrichtung unvollständig";
      return (
        {
          gang_heat_demand: "Aktiver Gang",
          gang_active: "Aktiver Gang",
          person_present: "Aktiver Gang",
          operation_off: "Betrieb Aus",
          after_run: "Ofenkühlung",
          manual_override: "Manuelle Vorgabe",
          manual_mode: "Manueller Betrieb · Ofen Aus",
          temperature_configuration_required: "Temperatureinstellungen unvollständig",
          upper_temperature_unavailable: "Gültiger Regeltemperaturwert fehlt",
          below_target: "Unter Einschaltgrenze",
          temperature_reached: "Obere Regeltemperatur erreicht",
          hysteresis_band: "Temperatur im Regelband",
          minimum_heating: "Mindestheizzeit",
          thermostat_cooldown: "Heizpause",
          temporary_door_heat: "Türhilfe",
        }[reason] ||
        reason ||
        "Ohne Begründung"
      );
    };
    const phaseIntervals = projection?.intervals || [];
    const coolingState = (phase, at) => ({
      at,
      label:
        phase === "unknown"
          ? "Unbekannt"
          : ["nachlauf", "zwangskühlung"].includes(phase)
            ? "Aktiv"
            : "Inaktiv",
      color:
        phase === "unknown"
          ? null
          : ["nachlauf", "zwangskühlung"].includes(phase)
            ? color("phase-cooling")
            : neutral,
    });
    const coolingChanges = phaseIntervals.map((p) =>
      coolingState(p.phase, stamp(p.started_at)),
    );
    if (
      !session.ended_at &&
      this.state.session?.timeline?.session_id === timeline.session_id
    ) {
      coolingChanges.push(coolingState(this.state.phase, end));
    }
    const lanes = [
      [
        "Tür",
        changes(
          (timeline.processed || [])
            .filter((e) => ["door_open", "door_close"].includes(e.kind))
            .map((e) => ({
              at: stamp(e.effective_at),
              label: e.kind === "door_open" ? "Offen" : "Geschlossen",
              color: e.kind === "door_open" ? color("event-door") : neutral,
            })),
        ),
      ],
      [
        "Präsenzsensor",
        changes(
          direct.map((r) => ({
            at: stamp(r.effective_at),
            label: occupancy(r),
            color: r.available
              ? r.occupancy === "present"
                ? color("phase-ready")
                : neutral
              : null,
          })),
        ),
      ],
      [
        "Saunagang",
        intervals(
          [...timeline.completed, ...(timeline.active ? [timeline.active] : [])],
          "Aktiv",
          "phase-session",
        ),
      ],
      [
        "Heizanforderung",
        changes(
          decisions.map((d) => ({
            at: stamp(d.created_at || d.at),
            label: `${d.heat ? "Ein" : "Aus"} · ${cause(d.reason)}`,
            color: d.heat ? color("ui-heater-on") : neutral,
          })),
        ),
      ],
      [
        "Ofenschalter",
        changes(
          (session.contactor_history || []).map((m) => ({
            at: stamp(m.at),
            label: m.state === true ? "Ein" : m.state === false ? "Aus" : "Unbekannt",
            color: m.state == null ? null : m.state ? color("ui-heater-on") : neutral,
          })),
        ),
      ],
      ["Ofenkühlung", changes(coolingChanges)],
    ];
    const activity = [
      ...records
        .filter((r) => r.kind === "command" || r.kind === "light_command")
        .map((r) => ({
          at: stamp(r.received_at),
          effective: r.payload.sent_at || r.received_at,
          label:
            r.kind === "command"
              ? `Ofenbefehl ${r.payload.heat ? "Ein" : "Aus"}`
              : `Lichtbefehl ${r.payload.brightness_pct == null ? "Aus" : `${num(r.payload.brightness_pct, 0)} %`}`,
          source: "Geräteausgabe",
          detail: r.payload.service_error
            ? `Dienstfehler: ${r.payload.service_error}`
            : "Dienst abgeschlossen; Rückmeldung separat",
        })),
      ...(timeline.processed || []).map((e) => ({
        at: stamp(e.detected_at),
        effective: e.effective_at,
        label: events[e.kind] || e.kind,
        source: "Erkennung",
        detail:
          {
            door_open: "Türöffnung erkannt",
            door_close: "Türschluss erkannt",
            presence_confirmed: "Türvorgang abgeschlossen · Präsenz belegt",
            presence_ended: "Türvorgang abgeschlossen · Abwesenheit belegt",
            infusion: "Feuchteanstieg erkannt",
          }[e.kind] ||
          events[e.kind] ||
          e.kind,
        ref: e.event_id,
      })),
      ...direct.map((r) => ({
        at: stamp(r.received_at),
        effective: r.effective_at,
        label: occupancy(r),
        source: "Präsenzsensor",
        detail: r.source,
      })),
      ...decisions.map((d) => ({
        at: stamp(d.created_at || d.at),
        effective: d.at,
        label: `Heizanforderung ${d.heat ? "Ein" : "Aus"}`,
        source: "Steuerung",
        detail: cause(d.reason),
      })),
      ...(session.contactor_history || []).map((m) => ({
        at: stamp(m.at),
        effective: m.at,
        label: `Ofenschalter ${m.state === true ? "Ein" : m.state === false ? "Aus" : "unbekannt"}`,
        source: "Schütz",
        detail: "Bestätigte Schalterstellung",
      })),
    ]
      .filter((e) => Number.isFinite(e.at))
      .sort((a, b) => a.at - b.at);
    const selected = activity.filter((e) => e.at <= cursor).at(-1);
    const laneMarkup = lanes
      .map(
        ([name, spans]) =>
          `<div class="control-track"><span>${esc(name)}</span><div class="control-track-bar">${spans.map((v) => `<button type="button" data-control-time="${v.at}" style="left:${pct(v.at)}%;width:${pct(v.end) - pct(v.at)}%;${v.color ? `background:${v.color}` : "background:transparent"}" title="${esc(`${name}: ${v.label} · ${clock(v.at)} – ${clock(v.end)}`)}" aria-label="${esc(`${name}: ${v.label} ab ${clock(v.at)}`)}"></button>`).join("")}<i style="left:${pct(cursor)}%"></i></div></div>`,
      )
      .join("");
    const stateMarkup = lanes
      .map(
        ([name, spans]) =>
          `<dt>${esc(name)}</dt><dd>${esc(
            spans
              .slice()
              .reverse()
              .find(
                (v) =>
                  v.at <= cursor &&
                  (v.end > cursor || (cursor === end && v.end === end)),
              )?.label || "Unbekannt",
          )}</dd>`,
      )
      .join("");
    this.updateMarkup(
      "#control-history",
      `<div class="card"><div class="row"><span class="muted">${clock(start)} – ${clock(end)}</span></div><div class="scroll"><div class="control-history-lanes">${laneMarkup}<div class="control-time-axis">${[0, 0.25, 0.5, 0.75, 1].map((f) => `<span>${clock(start + (end - start) * f)}</span>`).join("")}</div><div class="control-cursor-row"><input id="control-history-cursor" class="control-cursor" type="range" min="0" max="${end - start}" step="1" value="${cursor - start}" aria-label="Zeitpunkt im Betriebsverlauf" aria-valuetext="${clock(cursor)}"></div></div></div><p class="muted">Schraffiert: kein belegter Zustand.</p><div class="control-history-inspection"><div><h3>Status · ${clock(cursor)}</h3><dl>${stateMarkup}</dl></div><div><h3>Letztes Ereignis${selected ? ` · ${clock(selected.at)}` : ""}</h3>${selected ? `<p>${esc(selected.label)}</p><p class="muted">${esc(selected.detail)}</p>` : '<p class="muted">Noch kein Ereignis.</p>'}</div></div></div><div class="card control-history-events"><h2>Ereignisse</h2><div class="scroll"><table><thead><tr><th>Uhrzeit</th><th>Quelle</th><th>Ereignis</th><th>Status / Grund</th></tr></thead><tbody>${activity.map((e) => `<tr data-selected="${e === selected}"><td><button data-control-time="${e.at}">${clock(e.at)}</button></td><td>${esc(e.source)}</td><td>${esc(e.label)}</td><td title="${esc(e.ref || e.detail)}">${esc(e.detail)}</td></tr>`).join("")}</tbody></table></div></div>`,
    );
  }
  renderHistory(reasons = new Set(["viewport"])) {
    if (!this.isConnected || this.$("#history")?.hidden) return;
    if (!this.shown) {
      this.clearHistoryDisplay();
      return;
    }
    if (reasons.size === 1 && reasons.has("cursor") && this.historyChart) {
      this.historyChart.interaction.readGeometry();
      if (this.pendingHover) this.hoverChart(this.pendingHover);
      return;
    }
    const timeZone = localTimeZone();
    const { session, records } = this.shown,
      t = session.timeline;
    if (
      this.chartDataIndex?.records !== records ||
      this.chartDataIndex.indexedCount !== records.length
    )
      this.historyIndex(records);
    if (this.navigation?.main === "history") this.historyDetail = false;
    if (!this.historyDetail)
      this.positions = new Set([this.historyPrimaryPosition(session)]);
    if (
      this.navigation?.main === "details" &&
      this.navigation.detail === "detail-history"
    ) {
      this.renderControlHistory();
      return;
    }
    if (this.$("#plots")?.hidden) {
      this.ensureHistoryWindow();
      this.updateHistoryTimelineRevision(session);
      const key = this.diagnosticsRenderKey(timeZone);
      if (key !== this.historyDiagnosticsKey) {
        this.historyDiagnosticsKey = key;
        this.drawDiagnostics();
      }
      this.revealEventTarget("marker");
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
    const gangKey = `${this.historyTimelineRevision}:${gangNow}:${timeZone}`;
    if (this.historyGangKey !== gangKey) {
      this.historyGangKey = gangKey;
      const e = session.energy,
        energySummary = e
          ? `<p class="muted" data-history-energy>Energieverbrauch: ${num(e.measured_kwh + e.estimated_kwh, 3)} kWh · ${e.unknown_seconds ? "Unvollständig" : e.measured_seconds ? (e.estimated_seconds ? "Messung mit geschätzten Anteilen" : "Aus gemessener Leistung") : "Geschätzt"}</p>`
          : "";
      this.updateMarkup(
        "#gangs",
        `<div class="card"><h2>Saunagänge</h2>${energySummary}${gangs.length ? `<div class="scroll"><table><thead><tr><th>Gang</th><th>Status</th><th>Beginn</th><th>Dauer</th><th>Ende</th></tr></thead><tbody>${gangs.map((g, i) => `<tr data-gang-id="${esc(g.gang_id)}" data-start="${esc(g.started_at)}"><td>${i + 1}</td><td>${gangConfirmed(g) ? "Bestätigt" : "Vorläufig"}</td><td>${clock(g.started_at)}</td><td>${duration((stamp(g.ended_at || session.ended_at || this.state.now) - stamp(g.started_at)) / 1000)}</td><td>${g.ended_at ? clock(g.ended_at) : "–"}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">Keine Saunagänge erkannt.</p>'}</div>`,
      );
    }
    const eventKey = `${this.historyProjection()}:${this.historyTimelineRevision}:${this.historyEventRevision || 0}:${timeZone}`;
    if (this.historyEventKey !== eventKey) {
      this.historyEventKey = eventKey;
      const openEvents = [
        ...this.shadowRoot.querySelectorAll("#event-list details"),
      ].map((d) => d.open);
      const diagnostics = this.historyRecords("diagnostic"),
        traces = this.diagnosticData().traces;
      const eventRows = orderedHistoryEvents(this.normalHistoryEvents());
      const eventGroups = [];
      for (const event of eventRows) {
        const at = stamp(event.effective_at);
        const minute = Number.isFinite(at) ? Math.floor(at / 60000) : null;
        let group = eventGroups.at(-1);
        if (!group || group.minute !== minute) {
          group = {
            minute,
            label: minute == null ? "Ohne Zeit" : clock(at, timeZone),
            events: [],
          };
          eventGroups.push(group);
        }
        group.events.push(event);
      }
      this.updateMarkup(
        "#event-list",
        `<div class="card"><details><summary>Ereignisse (${eventRows.length})</summary><ol class="history-event-groups">${eventGroups
          .map(
            (group) =>
              `<li class="history-event-group"><div class="history-event-time">${esc(group.label)}</div><ol class="history-event-items">${group.events
                .map((e) => {
                  const linked = this.diagnosticTraceForEvent(traces, e);
                  return `<li class="event-row" data-event-id="${esc(e.event_id)}" data-selected="false"><span>${esc(events[e.kind] || e.kind)}</span>${linked && this.state?.permissions?.admin ? `<button data-action="event-row:${esc(e.event_id)}" aria-label="${esc(events[e.kind] || e.kind)} im Erkennungsverlauf zeigen">Zum Marker</button>` : ""}</li>`;
                })
                .join("")}</ol></li>`,
          )
          .join(
            "",
          )}</ol></details>${diagnostics.length ? `<details><summary>Historische Fehlerhinweise (${diagnostics.length})</summary>${diagnostics.map((r) => `<p><small>${clock(r.received_at)}</small> ${esc(r.payload.messages?.join(" ") || "Keine Störung gemeldet.")}</p>`).join("")}</details>` : ""}${t.retracted.length ? `<p class="muted">${t.retracted.length} vorläufige Erkennung(en) aufgehoben. Diese werden nicht als Gänge gezählt.</p>` : ""}</div>`,
      );
      this.shadowRoot
        .querySelectorAll("#event-list details")
        .forEach((d, i) => (d.open = !!openEvents[i]));
      if (this.highlightedEventId) this.highlightEvent(this.highlightedEventId, false);
    }
    const diagnosticsKey = this.diagnosticsRenderKey(timeZone);
    if (this.view === "diagnostics" && this.historyDiagnosticsKey !== diagnosticsKey) {
      this.historyDiagnosticsKey = diagnosticsKey;
      this.drawDiagnostics();
    }
    this.revealEventTarget("row");
  }
  diagnosticData() {
    const records = this.shown?.records || (this.emptyHistoryRecords ??= []);
    if (
      this.chartDataIndex?.records !== records ||
      this.chartDataIndex.indexedCount !== records.length
    )
      this.historyIndex(records);
    return this.chartDataIndex.diagnostics;
  }
  diagnosticsParameters() {
    const parameters = {
      ...this.state.configuration?.parameters,
      ...this.shown?.session.configuration?.parameters,
    };
    return Object.fromEntries(
      Object.entries(parameters).filter(([key]) =>
        /^(door_|strong_|weak_|infusion_|vent_|person_step_seconds$)/.test(key),
      ),
    );
  }
  diagnosticsRenderKey(timeZone) {
    const data = this.diagnosticData();
    return JSON.stringify([
      this.historyProjection(),
      this.shown?.session.timeline.session_id,
      this.historyDiagnosticsDatasetRevision,
      data.revision,
      this.eventNavigation(),
      this.diagnosticsParameters(),
      this.window,
      this.$("#detection-plots")?.getBoundingClientRect?.().width || 640,
      timeZone,
    ]);
  }
  eventNavigation() {
    const data = this.diagnosticData(),
      processed = this.shown?.session.timeline.processed || [],
      cached = this.historyNavigationCache;
    if (
      cached?.data === data &&
      cached.processed === processed &&
      cached.revision === data.revision
    )
      return cached.events;
    const signature = JSON.stringify(processed);
    if (
      cached?.data === data &&
      cached.signature === signature &&
      cached.revision === data.revision
    ) {
      cached.processed = processed;
      return cached.events;
    }
    const events = processed.map((event, index) => ({
      ...event,
      event_id: event.event_id || `legacy-${index}`,
      trace_at: data.traceTimes.get(event.event_id) || null,
    }));
    this.historyNavigationCache = {
      data,
      processed,
      signature,
      revision: data.revision,
      events,
      byId: new Map(events.map((event) => [event.event_id, event])),
    };
    return events;
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
  revealEventTarget(kind) {
    const pending = this.pendingEventFocus;
    if (!pending || pending.kind !== kind) return;
    const target = [
      ...this.shadowRoot.querySelectorAll(
        kind === "row" ? "#event-list [data-action]" : ".diagnostic-marker",
      ),
    ].find(
      (node) =>
        node.dataset.eventId === pending.eventId ||
        node.dataset.action === `event-row:${pending.eventId}`,
    );
    if (!target) return;
    this.highlightEvent(pending.eventId, kind === "row");
    target.scrollIntoView?.({ block: "center", behavior: "smooth" });
    target.focus?.();
    this.pendingEventFocus = null;
  }
  focusEvent(eventId, revealRow = false) {
    const event = this.eventNavigation().find((item) => item.event_id === eventId),
      at = stamp(event?.trace_at || event?.effective_at || event?.detected_at);
    if (!event || !at) {
      this.highlightEvent(eventId, revealRow);
      return;
    }
    const [domainStart, domainEnd] = this.historyDomain();
    const span = Math.min(domainEnd - domainStart, 30 * 60000);
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
    if (rebuild) {
      this.historyDiagnosticsDatasetRevision =
        (this.historyDiagnosticsDatasetRevision || 0) + 1;
      index = {
        records,
        series: new Map(),
        curveSeries: new Map(),
        byKind: new Map(),
        display: new Map(),
        seriesState: new Map(),
        indexedCount: 0,
        diagnostics: {
          traces: [],
          byTime: new Map(),
          traceTimes: new Map(),
          revision: 0,
        },
      };
    }
    for (let i = index.indexedCount; i < records.length; i++) {
      const record = records[i],
        grouped = index.byKind.get(record.kind) || [];
      grouped.push(record);
      index.byKind.set(record.kind, grouped);
      if (["diagnostic", "detector_trace", "detection"].includes(record.kind))
        this.historyEventRevision = (this.historyEventRevision || 0) + 1;
      if (record.kind === "detector_trace") {
        const trace = record.payload,
          time = stamp(trace.at),
          data = index.diagnostics;
        data.traces.push(trace);
        const bucket = data.byTime.get(time) || [];
        bucket.push(trace);
        data.byTime.set(time, bucket);
        data.revision++;
      } else if (record.kind === "detection") {
        const payload = record.payload;
        if (payload?.event?.event_id && payload.trace_at)
          index.diagnostics.traceTimes.set(payload.event.event_id, payload.trace_at);
        index.diagnostics.revision++;
      }
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
      if (value != null) state.hasValue = true;
      index.seriesState.set(key, state);
      // Raw observations retain nulls for truthful hover readouts. The
      // curve index shares only valid point objects, making a valid neighbour
      // across even a long missing interval available by binary search.
      const targets = [values];
      if (value != null) {
        const curveValues = index.curveSeries.get(key) || [];
        index.curveSeries.set(key, curveValues);
        targets.push(curveValues);
      }
      for (const target of targets) {
        const display = index.display.get(target);
        if (rebuild || !target.length || target.at(-1).time <= time) {
          target.push(point);
          if (display) this.appendHistoryDisplay(display, point, target.length - 1);
        } else {
          target.splice(lowerBoundHistory(target, time), 0, point);
          index.display.delete(target);
        }
      }
      index.series.set(key, values);
    }
    if (rebuild)
      for (const values of [...index.series.values(), ...index.curveSeries.values()])
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
        minimum: point,
        maximum: point,
      };
      level.nodes.set(key, node);
      level.keys.push(key);
      return;
    }
    node.lastIndex = index;
    node.last = point;
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
    if (child.minimum && (!node.minimum || child.minimum.value < node.minimum.value))
      node.minimum = child.minimum;
    if (child.maximum && (!node.maximum || child.maximum.value > node.maximum.value))
      node.maximum = child.maximum;
  }
  historyDisplayValues(position, quantity, start, end, _ttl, pixels) {
    const values = this.historyCurveSeries(position, quantity);
    if (!values.length) return values;
    // Keep first/last/extrema per one-to-two-pixel bin. Missing observations
    // and elapsed time do not disable reduction or split the user's curve.
    const width = Math.max(1, 2 ** Math.ceil(Math.log2((end - start) / pixels || 1)));
    const display = this.historyDisplay(values),
      level = this.historyDisplayLevel(display, width),
      firstIndex = Math.max(0, lowerBoundHistory(values, start) - 1),
      afterIndex = Math.min(values.length, lowerBoundHistory(values, end + 1) + 1),
      first = lowerBoundNumber(level.keys, Math.floor(values[firstIndex].time / width)),
      after = lowerBoundNumber(
        level.keys,
        Math.floor(values[afterIndex - 1].time / width) + 1,
      ),
      output = [];
    for (let offset = first; offset < after; offset++) {
      let node = level.nodes.get(level.keys[offset]);
      if (!node) continue;
      if (node.firstIndex < firstIndex || node.lastIndex >= afterIndex) {
        // Reduce the clipped edge bin too. Only exact adjacent valid samples
        // outside the viewport may contribute; older extrema must not leak in.
        const left = Math.max(firstIndex, node.firstIndex),
          right = Math.min(afterIndex, node.lastIndex + 1);
        let minimum = values[left],
          maximum = values[left];
        for (let index = left + 1; index < right; index++) {
          if (values[index].value < minimum.value) minimum = values[index];
          if (values[index].value > maximum.value) maximum = values[index];
        }
        node = {
          first: values[left],
          last: values[right - 1],
          minimum,
          maximum,
          lastIndex: right - 1,
        };
      }
      // Retain unchanged interior selections across appends and navigation.
      let points = node.displayPoints;
      if (!points || node.displayLastIndex !== node.lastIndex) {
        const selected = [node.first, node.minimum, node.maximum, node.last].sort(
          (a, b) => a.time - b.time,
        );
        points = [];
        for (const point of selected)
          if (points.at(-1)?.source !== point.source) points.push(point);
        node.displayPoints = points;
        node.displayLastIndex = node.lastIndex;
      }
      for (const point of points)
        if (output.at(-1)?.source !== point.source) output.push(point);
    }
    return output;
  }
  historyCurveSeries(position, quantity) {
    return this.chartDataIndex?.curveSeries.get(`${position}:${quantity}`) || [];
  }
  series(position, quantity) {
    return this.chartDataIndex?.series.get(`${position}:${quantity}`) || [];
  }
  historyPrimaryPosition(session = this.shown?.session) {
    const hasValues = (position) =>
      this.chartDataIndex?.seriesState?.get(`${position}:temperature`)?.hasValue;
    const currentPosition =
      this.state?.session &&
      session?.timeline?.session_id === this.state.session.timeline?.session_id
        ? this.state.regulation_temperature_position
        : null;
    if (currentPosition && hasValues(currentPosition)) return currentPosition;
    if (hasValues("upper")) return "upper";
    if (hasValues("lower")) return "lower";
    return this.state?.measurement_positions?.[0] || "upper";
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
    const matrix = svg.getScreenCTM?.();
    if (matrix && svg.createSVGPoint) {
      const point = svg.createSVGPoint();
      point.x = clientX;
      point.y = clientY;
      const local = point.matrixTransform(matrix.inverse());
      if (Number.isFinite(local.x) && Number.isFinite(local.y))
        return { x: local.x, y: local.y };
    }
    const rect = svg.getBoundingClientRect();
    const viewBox = svg.viewBox?.baseVal,
      width = viewBox?.width || 1200,
      height = viewBox?.height || 480;
    return {
      x: (viewBox?.x || 0) + ((clientX - rect.left) / rect.width) * width,
      y: (viewBox?.y || 0) + ((clientY - rect.top) / rect.height) * height,
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
      // The archive list supplies session bounds before the first record page.
      archive =
        !session &&
        this.sessions?.find((item) => item.session_id === this.historySelectionId()),
      measurementWindow = this.shown?.measurement_window || archive?.measurement_window,
      started = stamp(
        measurementWindow?.started_at ||
          session?.timeline.session_started_at ||
          archive?.started_at,
      ),
      now = stamp(this.state?.now) || Date.now(),
      start = Number.isFinite(started) ? started : now,
      ended = stamp(
        measurementWindow?.ended_at || session?.ended_at || archive?.ended_at,
      ),
      end = Number.isFinite(ended) ? ended : now,
      complete =
        measurementWindow?.complete ?? !!(session?.ended_at || archive?.ended_at);
    return [start, Math.max(start + 1000, complete ? end : Math.min(end, now))];
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
      fraction =
        (point.x - HISTORY_PLOT.left) / (HISTORY_PLOT.right - HISTORY_PLOT.left);
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
      left =
        HISTORY_PLOT.left +
        ((this.window[0] - start) / width) * (HISTORY_PLOT.right - HISTORY_PLOT.left),
      right =
        HISTORY_PLOT.left +
        ((this.window[1] - start) / width) * (HISTORY_PLOT.right - HISTORY_PLOT.left);
    const selected = svg.querySelector("[data-history-window]"),
      first = svg.querySelector('[data-history-handle="start"]'),
      last = svg.querySelector('[data-history-handle="end"]');
    selected?.setAttribute("x", left);
    selected?.setAttribute("width", Math.max(1, right - left));
    first?.setAttribute("x", left - 4);
    last?.setAttribute("x", right - 4);
    svg.setAttribute(
      "aria-valuetext",
      `${clock(this.window[0])} bis ${clock(this.window[1])}, Zoom ${num(this.zoom, 1)}×`,
    );
    svg.setAttribute(
      "aria-valuenow",
      String(Math.round(((this.window[0] - start) / width) * 100)),
    );
    const reset = this.$('[data-action="reset-zoom"]');
    if (reset) reset.textContent = `${num(this.zoom, 1)}×`;
  }
  historyLegend(label, items) {
    if (!items.length) return "";
    return `<div class="history-legend" aria-label="${esc(label)}"><strong>${esc(label)}</strong><div class="legend">${items
      .map(([kind, text, phase]) => {
        const shape = ["temperature", "humidity", "infusion"].includes(kind)
          ? `<line class="${kind}" x1="2" x2="26" y1="7" y2="7"/>`
          : `<rect class="${kind}" x="2" y="2" width="24" height="10" rx="2"/>`;
        return `<span${phase ? ` data-legend-phase="${esc(phase)}"` : ""}><svg class="chart legend-swatch" viewBox="0 0 28 14" aria-hidden="true">${shape}</svg>${esc(text)}</span>`;
      })
      .join("")}</div></div>`;
  }
  historyMarkup() {
    return `<div class="plot-wrap history-stack"><svg class="chart history-background" viewBox="0 0 ${HISTORY_PLOT.width} 480" preserveAspectRatio="none" aria-hidden="true"><defs><clipPath id="history-layer-clip"><rect x="${HISTORY_PLOT.left}" y="18" width="${HISTORY_PLOT.right - HISTORY_PLOT.left}" height="417"/></clipPath></defs><g data-history-annotations clip-path="url(#history-layer-clip)"></g></svg><canvas class="chart history-curves" role="img" aria-label="Temperatur- und Feuchteverlauf; Ereignisse und Zeiten stehen in den nachfolgenden Tabellen." aria-describedby="gangs event-list">Temperatur und Feuchte der Sitzung. Ereignisse und Saunagänge sind in den Tabellen unter dem Diagramm zugänglich.</canvas><svg class="chart session-chart" viewBox="0 0 ${HISTORY_PLOT.width} 480" preserveAspectRatio="none" role="img" tabindex="0" aria-label="Sitzungsverlauf"><g data-history-axes></g><line id="cursor" x1="0" x2="0" y1="18" y2="435" stroke="var(--sauna-color-chart-text)" visibility="hidden"/></svg></div>`;
  }
  historyLegendMarkup() {
    const presentPhases = [
      ...new Set(
        (this.shown?.phase_projection?.intervals || []).map((item) => item.phase),
      ),
    ];
    const phaseItems = presentPhases
      .filter((phase) => phaseAppearance[phase])
      .map((phase) => [phaseAppearance[phase].shape, phases[phase], phase]);
    return `<div class="history-legend-groups">${this.historyLegend("Phasen", phaseItems)}${this.historyLegend(
      "Ereignisse",
      [
        ["door", "Saunatür offen"],
        ["infusion", "Aufguss"],
      ],
    )}</div>`;
  }
  historyPreparedSeries(position, quantity, start, end, ttl, pixels, cache) {
    const values = this.historyCurveSeries(position, quantity),
      state = this.chartDataIndex.seriesState?.get(`${position}:${quantity}`),
      first = Math.max(0, lowerBoundHistory(values, start) - 1),
      after = Math.min(values.length, lowerBoundHistory(values, end + 1) + 1),
      // Only inserts in this span change its length. Late inserts before it
      // shift both indices equally; stable neighbour identities still detect
      // an insertion that changes an edge tangent without changing the count.
      key = `${this.historyProjection()}:${this.historyProjectionGeneration || 0}:${start}:${end}:${ttl}:${pixels}:${after - first}:${values[first]?.serial || 0}:${values[after - 1]?.serial || 0}`;
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
  historyModel(chart, session, cssWidth = HISTORY_PLOT.width) {
    const [start, end] = this.window,
      left = HISTORY_PLOT.left,
      right = HISTORY_PLOT.right,
      top = 18,
      bottom = 435,
      ttl = historyMeasurementTtlSeconds(this, session) * 1000,
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
          Math.max(1, ((right - left) * cssWidth) / HISTORY_PLOT.width),
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
      yH = (value) => bottom - (value / humidityHigh) * (bottom - top);

    return {
      start,
      end,
      left,
      right,
      top,
      bottom,
      width: HISTORY_PLOT.width,
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
    };
  }
  historyPhaseAt(time) {
    const intervals = this.shown?.phase_projection?.intervals || [];
    return intervals.find((item, index) => {
      const start = stamp(item.started_at),
        end = stamp(item.ended_at);
      return (
        Number.isFinite(start) &&
        Number.isFinite(end) &&
        start <= time &&
        (time < end || (time === end && index === intervals.length - 1))
      );
    });
  }
  normalHistoryEvents() {
    return (this.shown?.session?.timeline?.processed || [])
      .map((event, index) => ({
        ...event,
        event_id: event.event_id || `legacy-${index}`,
      }))
      .filter((event) => event.kind !== "ventilation_confirmed");
  }
  historyEventsAt(time, tolerance = 0) {
    return orderedHistoryEvents(this.normalHistoryEvents()).filter((event) => {
      const at = stamp(event.effective_at);
      return Number.isFinite(at) && Math.abs(at - time) <= Math.max(0, tolerance);
    });
  }
  historyAnnotations(model, session, gangs) {
    const { start, end, left, right, top, bottom, x } = model;
    const interval = (a, b, klass, title, y = top, height = bottom - top) => {
      const aa = Math.max(start, stamp(a)),
        bb = Math.min(end, stamp(b || session.ended_at || this.state.now));
      return bb > aa
        ? `<rect class="${klass}" x="${x(aa).toFixed(2)}" y="${y}" width="${(x(bb) - x(aa)).toFixed(2)}" height="${height}"><title>${esc(title)}</title></rect>`
        : "";
    };
    let svg = "";
    const projection = this.shown?.phase_projection;
    if (projection) {
      for (const phase of projection.intervals || []) {
        const gang = gangs.find((item) => item.gang_id === phase.source_id);
        const klass =
          phase.phase === "saunagang"
            ? gang && !gangConfirmed(gang)
              ? "gang provisional"
              : "gang"
            : phaseAppearance[phase.phase]?.shape || "phase-other";
        svg += interval(
          phase.started_at,
          phase.ended_at,
          klass,
          `${phase.phase}${phase.complete === false ? " · unvollständig" : ""}`,
        );
      }
    }
    const doorEvents = session.timeline.processed.filter(
      (e) => e.kind === "door_open" || e.kind === "door_close",
    );
    doorEvents.forEach((e, i) => {
      if (e.kind === "door_open")
        svg += interval(
          e.effective_at,
          doorEvents[i + 1]?.effective_at,
          "door",
          "Saunatür offen",
        );
    });
    for (let n = 0; n <= 8; n++) {
      const yy = top + ((bottom - top) * n) / 8;
      svg += `<line class="gridline" x1="${left}" x2="${right}" y1="${yy}" y2="${yy}"/>`;
    }
    for (const e of session.timeline.processed.filter((e) => e.kind === "infusion")) {
      const xx = x(stamp(e.effective_at));
      svg += `<line class="infusion" x1="${xx}" x2="${xx}" y1="${top}" y2="${bottom}"><title>Aufguss · ${clock(e.effective_at)}</title></line>`;
    }
    return svg;
  }
  historyAxes(model, { width = 1200, height = 480 } = {}) {
    const { start, end, left, right, top, bottom, low, high, humidityHigh, x } = model;
    const timeZone = localTimeZone();
    const scaleX = 1200 / Math.max(1, width),
      scaleY = 480 / Math.max(1, height);
    // Cancel the SVG's unequal viewport scaling only for labels. Curves and
    // annotation positions continue to share their existing plot coordinates.
    const text = (label, px, py, klass = "", anchor = "start") =>
      `<text class="${klass}" text-anchor="${anchor}" transform="translate(${px} ${py}) scale(${scaleX} ${scaleY})">${esc(label)}</text>`;
    const valueTicks = Math.max(
        2,
        Math.min(8, Math.floor((bottom - top) / scaleY / 44)),
      ),
      timeTicks = Math.max(2, Math.min(8, Math.floor((right - left) / scaleX / 80)));
    let svg = "";
    for (let n = 0; n <= valueTicks; n++) {
      const fraction = n / valueTicks,
        y = bottom - (bottom - top) * fraction + 4 * scaleY;
      svg += text(
        (low + (high - low) * fraction).toFixed(0),
        Math.max(0, left - 40 * scaleX),
        y,
        "axis-temperature",
      );
      svg += text(
        (humidityHigh * fraction).toFixed(0),
        Math.min(1200, right + 40 * scaleX),
        y,
        "axis-humidity",
        "end",
      );
    }
    for (let n = 0; n <= timeTicks; n++) {
      const time = start + ((end - start) * n) / timeTicks;
      svg += text(
        clock(time, timeZone),
        x(time),
        bottom + 24 * scaleY,
        "",
        n === 0 ? "start" : n === timeTicks ? "end" : "middle",
      );
    }
    return (
      svg +
      text("Temperatur (°C)", left, 12 * scaleY, "axis-temperature") +
      text("Luftfeuchte (%)", right, 12 * scaleY, "axis-humidity", "end")
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
    const svg = this.eventElement(event, "svg.session-chart");
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
        at = stamp(event.trace_at || event.effective_at || event.detected_at);
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
    const data = this.diagnosticData();
    this.eventNavigation();
    const linked = event.trace_at
      ? event
      : this.historyNavigationCache.byId.get(event.event_id) || event;
    const effective = stamp(linked.trace_at || linked.effective_at);
    if (!Number.isFinite(effective)) return null;
    let byTime = data.byTime;
    if (traces !== data.traces) {
      this.diagnosticTraceIndexes ??= new WeakMap();
      let indexed = this.diagnosticTraceIndexes.get(traces);
      if (!indexed || indexed.count > traces.length)
        indexed = { count: 0, byTime: new Map() };
      for (let i = indexed.count; i < traces.length; i++) {
        const trace = traces[i],
          time = stamp(trace.at),
          bucket = indexed.byTime.get(time) || [];
        bucket.push(trace);
        indexed.byTime.set(time, bucket);
      }
      indexed.count = traces.length;
      this.diagnosticTraceIndexes.set(traces, indexed);
      byTime = indexed.byTime;
    }
    return (
      (byTime.get(effective) || []).find(
        (t) =>
          (t.signals || []).includes(signal) &&
          ["upper", "lower"].some((pos) => Number.isFinite(t.metrics?.[pos]?.[metric])),
      ) || null
    );
  }
  drawDiagnostics() {
    if (!this.state?.permissions?.admin) {
      this.updateMarkup("#detection-plots", "");
      return;
    }
    if (!this.shown) {
      this.$("#detection-plots").innerHTML = "<p>Keine Saunasitzung ausgewählt.</p>";
      return;
    }
    const traces = this.diagnosticData().traces;
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
    const parameters = this.diagnosticsParameters();
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
    const visibleTraces = traces.filter((trace) => {
      const time = stamp(trace.at);
      return Number.isFinite(time) && time >= start && time <= end;
    });
    for (const [group, metric, label, convert = (v) => v] of groups) {
      const quantity = metric.includes("temperature") ? "temperature" : "humidity";
      const vals = visibleTraces
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
        for (const t of visibleTraces) {
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
        chart += `<path class="${pos} ${quantity}" data-series="detector_${metric}_${pos}" d="${path}"/>`;
        for (const v of thresholds(metric, pos).filter(Number.isFinite)) {
          const display = convert(v);
          chart += `<line x1="${plotLeft}" x2="${plotRight}" y1="${y(display)}" y2="${y(display)}" stroke="var(--sauna-color-chart-threshold)" stroke-dasharray="4 5"><title>Schwelle ${pos === "upper" ? "oben" : "unten"}: ${num(display, 2)}</title></line>`;
        }
      }
      for (let n = 0; n <= 4; n++) {
        const v = lo + ((hi - lo) * n) / 4,
          t = start + ((end - start) * n) / 4;
        chart += `<text class="axis-${quantity}" x="4" y="${y(v) + 4}">${num(v, 1)}</text><text text-anchor="middle" x="${chartX(t)}" y="192">${clock(t)}</text>`;
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
      html += `<div class="plot-panel"><h3>${group} · <span style="color:var(--sauna-color-series-${quantity})">${label}</span></h3><div class="legend diagnostic-legend" style="color:var(--sauna-color-series-${quantity})"><span>━━ Oben</span><span>┄┄ Unten</span></div>${chart}</div>`;
    }
    html +=
      '</div><div class="card"><h2>Erkennungsbedingungen und Bestätigung</h2><div class="scroll"><table><thead><tr><th>Zeit</th><th>Aktive Prüfungen</th><th>Bedingungen erfüllt</th><th>Erfüllte Prüfpunkte</th><th>Ausgelöste Signale</th></tr></thead><tbody>' +
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
                .map(([k, v]) => `${signalText[k] || "Signal"}: ${num(v)} Prüfpunkte`)
                .join(" · "),
            )}</td><td>${esc(t.signals.map((k) => events[k] || k).join(", "))}</td></tr>`,
        )
        .join("") +
      "</tbody></table></div></div>";
    this.$("#detection-plots").innerHTML = html;
    this.revealEventTarget("marker");
  }
  settingsParameterGroup({ id }) {
    const state = this.state;
    // Program values are edited through the catalog or the temperature choice.
    const group = state.frontend_defaults.settings_groups.find(
      (group) => group.id === id,
    );
    if (
      !state.permissions?.admin ||
      id === "programs" ||
      group?.surface === "integration"
    )
      return "";
    const source = state.configuration.presence_source || "proxy";
    const subgroups = state.frontend_defaults.settings_subgroups || [];
    const entries = state.parameters
      .filter((d) => {
        const group = subgroups.find((group) => group.id === d.settings_subgroup);
        return (
          d.settings_group === id &&
          (!group?.presence_sources || group.presence_sources.includes(source))
        );
      })
      .sort((a, b) => a.order - b.order);
    if (!entries.length) return "";
    const field = (d) =>
      `<div class="field"><span><label for="parameter-${esc(d.key)}">${esc(d.label)}</label> ${this.infoButton(`parameter:${d.key}`, `${d.label} erklären`, d.description, `help-${d.key}`)}</span>${unitInput(`<input id="parameter-${esc(d.key)}" form="settings-parameters" type="number" aria-label="${esc(d.label)} (${esc(d.unit)})" name="${esc(d.key)}" aria-describedby="help-${esc(d.key)}" step="${d.step}" min="${d.minimum ?? ""}" max="${d.maximum ?? ""}" value="${esc(state.configuration.parameters[d.key] ?? "")}" ${d.optional ? "" : "required"}>`, d.unit)}</div>`;
    const groupFields = (members) => {
      const grouped = subgroups
        .map((group) => {
          const fields = members.filter((d) => d.settings_subgroup === group.id);
          if (!fields.length) return "";
          const description = group.descriptions?.[source] || group.description;
          const heading = `${esc(group.label)}${description ? ` ${this.infoButton(`parameter-group:${group.id}`, `${group.label} erklären`, description)}` : ""}`;
          return `<section class="parameter-section" aria-labelledby="parameter-group-${esc(group.id)}"><h3 id="parameter-group-${esc(group.id)}">${heading}</h3><div class="forms">${fields.map(field).join("")}</div></section>`;
        })
        .join("");
      const ungrouped = members.filter(
        (d) => !subgroups.some((group) => group.id === d.settings_subgroup),
      );
      return (
        grouped +
        (ungrouped.length
          ? `<div class="forms parameter-section">${ungrouped.map(field).join("")}</div>`
          : "")
      );
    };
    return `<div class="card settings-parameters-card">${groupFields(entries)}<button type="submit" form="settings-parameters" class="confirm">Einstellungen speichern</button></div>`;
  }
  drawSettings() {
    if (!this.state) {
      this.renderStateLoading();
      return;
    }
    const state = this.state,
      admin = !!state.permissions?.admin;
    if (this.settingsEntry !== this.entry || this.settingsAdmin !== admin) {
      const groups = state.frontend_defaults.settings_groups.filter(
        ({ id, surface }) =>
          surface !== "integration" && (admin || ["programs", "personal"].includes(id)),
      );
      const parameterGroup = (group) => this.settingsParameterGroup(group);
      const contents = {
        programs: `<div class="card settings-programs"><h2>Programme</h2><div id="program-library"></div></div><div class="card"><h2>Start über Taster oder Betriebsschalter</h2><div id="button-settings"></div></div>`,
        appearance: admin ? this.appearanceSettingsMarkup() : "",
        maintenance: `<div class="card"><h2>Sitzungsarchiv ${this.infoButton("archive-retention", "Gespeicherte Sitzungen", "Nur Sitzungen mit bestätigtem Saunagang bleiben im Archiv.")}</h2><button class="confirm" data-action="export">Archiv als ZIP herunterladen</button><div id="archive-management"></div></div><div class="card"><h2>Protokollierung ${this.infoButton("logging", "Protokollumfang", "Home-Assistant-Protokoll, unabhängig vom Sitzungsarchiv. Betriebsereignisse enthalten auch Fehler und Warnungen; Diagnose ergänzt Messwerte und Erkennungsprüfungen.")}</h2><div class="row"><label for="log-level">Umfang</label><select id="log-level"><option value="ERROR">Fehler</option><option value="INFO">Betriebsereignisse</option><option value="DEBUG">Detaillierte Diagnose</option></select><button data-action="logging" class="confirm">Übernehmen</button></div><a href="/config/logs">Home-Assistant-Protokoll öffnen</a></div><div class="card"><h2>Werkseinstellungen ${this.infoButton("reset-scope", "Umfang des Zurücksetzens", "Setzt Anlagenwerte, Programme, Startvorgaben und Protokollierung zurück. Gerätezuordnung, Erkennungsverfahren, Farben, Instrumente und Archiv bleiben erhalten.")}</h2><button class="stop" data-action="reset-settings">Werkseinstellungen wiederherstellen</button><p id="settings-reset-status" class="muted" role="status"></p></div>`,
        personal: `<div class="card"><h2>Persönliche Startseite ${this.infoButton("start-page", "Gültigkeit der Startseite", "Gilt nur für das aktuelle Home-Assistant-Profil.")}</h2><button data-action="default-page" class="confirm">Als Startseite festlegen</button><p id="start-page-status" class="muted" role="status"></p></div>`,
      };
      this.$("#settings").innerHTML =
        `<div class="settings-layout"><button type="button" class="settings-menu-toggle" data-action="settings-menu" aria-label="Einstellungsbereiche öffnen" aria-expanded="false" aria-controls="settings-navigation"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg><span data-settings-current></span></button><button type="button" class="settings-menu-backdrop" data-action="settings-menu-close" tabindex="-1" aria-label="Einstellungsbereiche schließen"></button><nav id="settings-navigation" class="settings-navigation" aria-label="Einstellungsbereiche">${groups.map(({ id, label }) => `<button type="button" data-action="settings-section:${esc(id)}" aria-controls="settings-${esc(id)}">${esc(label)}</button>`).join("")}${admin ? '<a class="settings-configuration-link" href="/config/integrations/integration/ha_sauna">Sauna konfigurieren <span aria-hidden="true">↗</span></a>' : ""}</nav><div class="settings-content" ${admin ? 'id="parameters"' : ""}>${admin ? '<form id="settings-parameters"></form>' : ""}${groups.map((group) => `<section id="settings-${esc(group.id)}" data-settings-section="${esc(group.id)}" aria-label="${esc(group.label)}">${group.id === "appearance" ? parameterGroup(group) + (contents[group.id] || "") : (contents[group.id] || "") + parameterGroup(group)}</section>`).join("")}</div></div>`;
      if (admin) this.$("#log-level").value = state.configuration.log_level;
      this.settingsEntry = this.entry;
      this.settingsAdmin = admin;
      this.programDraft = null;
      this.programEditor = null;
      this.programLibraryNeedsRender = true;
    }
    this.selectSettingsSection(this.settingsSection);
    for (const d of state.parameters) {
      const input = this.$(`#parameters input[name="${d.key}"]`);
      if (!input) continue;
      input.disabled = !admin || state.configuration_locked;
      input.min = d.minimum == null ? "" : String(d.minimum);
      input.max = d.maximum == null ? "" : String(d.maximum);
      input.step = String(d.step);
      input.required = !d.optional;
      if (!input.dataset.edited && this.shadowRoot.activeElement !== input)
        input.value = state.configuration.parameters[d.key] ?? "";
    }
    for (const button of this.shadowRoot.querySelectorAll(
      'button[form="settings-parameters"]',
    ))
      button.disabled = !admin || state.configuration_locked;
    if (admin) {
      this.drawAppearanceStatus();
      this.drawArchiveManagement();
      this.$('[data-action="reset-settings"]').disabled = state.configuration_locked;
    }
    this.renderProgramLibrary();
    this.drawButtonProgram();
    if (this.hass.userData?.default_panel === "ha-sauna") {
      const button = this.$('[data-action="default-page"]');
      button.disabled = true;
      button.textContent = "Als Startseite festgelegt";
    }
  }
  selectSettingsSection(selected) {
    const sections = [
      ...(this.shadowRoot?.querySelectorAll?.("[data-settings-section]") || []),
    ];
    if (!sections.length) return;
    this.settingsSection = sections.some(
      (section) => section.dataset.settingsSection === selected,
    )
      ? selected
      : sections[0].dataset.settingsSection;
    for (const section of sections)
      section.hidden = section.dataset.settingsSection !== this.settingsSection;
    this.shadowRoot
      .querySelectorAll('[data-action^="settings-section:"]')
      .forEach((button) => {
        if (button.dataset.action === `settings-section:${this.settingsSection}`)
          button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
      });
    const currentLabel = this.$("[data-settings-current]");
    if (currentLabel)
      currentLabel.textContent =
        this.$('.settings-navigation [aria-current="page"]')?.textContent ||
        "Einstellungen";
  }
  setSettingsMenu(open, restoreFocus = false) {
    this.$(".settings-layout")?.toggleAttribute("data-menu-open", open);
    const toggle = this.$('[data-action="settings-menu"]');
    toggle?.setAttribute("aria-expanded", String(open));
    toggle?.setAttribute(
      "aria-label",
      open ? "Einstellungsbereiche schließen" : "Einstellungsbereiche öffnen",
    );
    if (open) this.$('.settings-navigation [aria-current="page"]')?.focus?.();
    else if (restoreFocus && toggle?.getClientRects?.()?.length) toggle.focus?.();
  }
  drawArchiveManagement() {
    if (!this.state?.permissions?.admin || !this.$("#archive-management")) return;
    const locked = !!this.state.session || this.archiveAdminBusy,
      disabled = locked ? "disabled" : "",
      pending = this.archiveDeletePending,
      sessions = this.archiveAdminSessions;
    this.updateMarkup(
      "#archive-management",
      `${this.state.session ? '<p class="muted">Löschen erst nach Ende der laufenden Sitzung.</p>' : ""}<div class="row"><button data-action="archive-list" ${this.archiveAdminBusy ? "disabled" : ""}>Sitzungen verwalten</button><button class="stop" data-action="archive-reset" ${disabled}>Datenbank zurücksetzen</button></div>${sessions ? (sessions.length ? sessions.map((session) => `<div class="row"><span>Sitzung vom ${esc(when(session.started_at))}</span><button class="stop" data-action="archive-delete:${esc(encodeURIComponent(session.session_id))}" ${locked || !session.ended_at ? "disabled" : ""}>Sitzung löschen</button></div>`).join("") : "<p>Keine gespeicherten Sitzungen.</p>") : ""}${pending ? `<div class="notice" role="alert"><p>${pending.reset ? "Alle Sitzungen und Messdaten dieser Sauna endgültig löschen? Einstellungen und Gerätezuordnungen bleiben erhalten." : `Sitzung vom ${esc(when(pending.started_at))} und alle zugehörigen Messdaten endgültig löschen?`}</p><div class="row"><button class="stop" data-action="archive-confirm" ${disabled}>Endgültig löschen</button><button data-action="archive-cancel" ${this.archiveAdminBusy ? "disabled" : ""}>Abbrechen</button></div></div>` : ""}<p role="status">${esc(this.archiveAdminMessage || "")}</p>`,
    );
  }
  async loadArchiveManagement() {
    if (!this.state?.permissions?.admin || this.archiveAdminBusy) return;
    const entry = this.entry,
      generation = this.generation,
      revision = this.archiveRevision;
    this.archiveAdminBusy = true;
    this.drawArchiveManagement();
    try {
      const sessions = await this.api(`/${entry}/archive`);
      if (
        entry === this.entry &&
        generation === this.generation &&
        revision === this.archiveRevision
      )
        this.archiveAdminSessions = sessions;
    } finally {
      this.archiveAdminBusy = false;
      this.drawArchiveManagement();
    }
  }
  async eraseArchive() {
    const pending = this.archiveDeletePending;
    if (
      !pending ||
      !this.state?.permissions?.admin ||
      this.state.session ||
      this.archiveAdminBusy
    )
      return;
    const entry = this.entry,
      generation = this.generation;
    this.archiveAdminBusy = true;
    this.drawArchiveManagement();
    try {
      const result = await this.api(
        `/${entry}/archive/erase`,
        "POST",
        pending.reset ? { reset: true } : { session_id: pending.session_id },
      );
      if (entry !== this.entry || generation !== this.generation) return;
      this.clearArchiveCaches();
      this.archiveRevision = result.archive_revision;
      if (
        this.state &&
        (pending.reset ||
          this.state.last_session?.timeline?.session_id === pending.session_id)
      )
        this.state = { ...this.state, last_session: null };
      this.archiveAdminMessage = pending.reset
        ? "Archivdatenbank wurde geleert."
        : "Sitzung wurde gelöscht.";
      await this.refresh(true);
    } finally {
      this.archiveAdminBusy = false;
      this.drawArchiveManagement();
    }
    if (entry === this.entry && generation === this.generation)
      await this.loadArchiveManagement();
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
        "Als Home-Assistant-Startseite gespeichert. Im Profil änderbar.";
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
  programCatalogDirty() {
    return (
      JSON.stringify(this.currentProgramDraft()) !==
      JSON.stringify(this.state.configuration.temperature_programs || [])
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
        return this.roundTargetTemperature(numeric, { minimum, maximum });
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
        ? `<div class="row"><label class="field" for="${esc(countId)}"><span>Anzahl der Temperaturstufen ${this.distributionInfo(`catalog:${program.id}`, steps)}</span><input id="${esc(countId)}" data-program-step-count type="number" min="1" max="${bounds.gangMaximum}" step="1" value="${count}" ${disabled}></label></div><div class="program-step-fields">${this.resizeSteps(
            steps,
            count,
          )
            .map(
              (value, index) =>
                `<label class="field" for="catalog-${esc(program.id)}-step-${index}">Stufe ${index + 1}${unitInput(`<input id="catalog-${esc(program.id)}-step-${index}" data-program-step="${index}" type="number" aria-label="Stufe ${index + 1} (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(value)}" ${disabled}>`, "°C")}</label>`,
            )
            .join("")}</div>`
        : `<div class="row"><label class="field">Starttemperatur${unitInput(`<input data-program-field="start_c" type="number" aria-label="Starttemperatur (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(program.start_c)}" ${disabled}>`, "°C")}</label><label class="field">Endtemperatur${unitInput(`<input data-program-field="end_c" type="number" aria-label="Endtemperatur (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(program.end_c)}" ${disabled}>`, "°C")}</label><label class="field" for="${esc(distributionId)}"><span>Verteilung auf Saunagänge ${this.distributionInfo(`catalog:${program.id}`, this.distributedSteps(Number(program.start_c), Number(program.end_c), Number(program.distribution_gangs)))}</span><input id="${esc(distributionId)}" data-program-field="distribution_gangs" type="number" step="1" min="${bounds.gangMinimum}" max="${bounds.gangMaximum}" value="${esc(program.distribution_gangs)}" ${disabled}></label></div>`;
    const protectedProgram = [
      this.state.configuration.selected_program_id,
      this.state.configuration.button_program,
    ].includes(program.id);
    return `<div class="program-form"><label class="field">Name<input data-program-field="name" value="${esc(program.name)}" ${disabled}></label>${selector}${values}<div class="row"><button type="button" class="confirm" data-action="program-finish" ${disabled}>Fertig</button><button type="button" data-action="program-cancel">Abbrechen</button><button type="button" data-action="program-remove:${esc(program.id)}" ${protectedProgram || !editable ? "disabled" : ""}>Entfernen</button></div></div>`;
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
          summary = this.programSteps(program);
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
      gestureEditable =
        !!this.state.permissions?.control && !this.state.configuration_locked,
      gestureOptions = (this.state.button_session_gestures || [])
        .map(
          (value) =>
            `<option value="${value}" ${configuration.button_session_gesture === value ? "selected" : ""}>${{ long: "Gedrückt halten", double: "Zweimal drücken", triple: "Dreimal drücken" }[value]}</option>`,
        )
        .join(""),
      button = configuration.button_program,
      bounds = this.temperatureBounds(),
      option = (value, label) =>
        `<option value="${esc(value)}" ${button === value ? "selected" : ""}>${esc(label)}</option>`;
    this.updateMarkup(
      "#button-settings",
      `${gestureOptions ? `<div class="row"><label class="field" for="button-session-gesture">Sitzung starten und beenden<select id="button-session-gesture" ${gestureEditable ? "" : "disabled"}>${gestureOptions}</select></label></div>` : ""}<div class="row"><div class="field"><span><label for="button-program">Temperaturwahl</label> ${this.infoButton("button-start", "Temperaturwahl beim externen Start", "Wird sofort als Startvorgabe für Taster und Betriebsschalter gespeichert. Beim Einschalten in der Steuerungsansicht gilt deren Temperaturwahl.")}</span><select id="button-program" ${editable ? "" : "disabled"}>${option("constant", "Konstante Temperatur")}<optgroup label="Gespeicherte Programme">${configuration.temperature_programs.map((program) => option(program.id, program.name)).join("")}</optgroup></select></div>${button === "constant" ? `<label class="field" for="button-temperature">Solltemperatur${unitInput(`<input id="button-temperature" type="number" aria-label="Solltemperatur (°C)" min="${bounds?.minimum}" max="${bounds?.maximum}" step="${this.temperatureStep()}" value="${configuration.button_temperature_c}" ${editable ? "" : "disabled"}>`, "°C")}</label>` : ""}</div>${button !== "constant" ? `<p class="muted">Temperaturfolge: ${esc(this.programSteps(configuration.temperature_programs.find((program) => program.id === button)))}</p>` : ""}`,
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
      !this.state ||
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
      !this.state ||
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
      host = this.$(".settings-content")?.getBoundingClientRect?.();
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
      const container = this.$(".settings-content");
      if (container) container.scrollTop += drag.edgeDirection * 18;
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
    if (!this.state) return;
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
      this.state.configuration.temperature_programs = result?.programs || programs;
      this.programDraft = null;
      this.programRevision = (this.programRevision || 0) + 1;
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
    await this.refresh(true);
  }
  async saveSettings() {
    if (!this.state?.permissions?.admin || this.state.configuration_locked) return;
    this.message(null);
    const values = {};
    for (const [key, value] of new FormData(this.$("#settings-parameters"))) {
      if (value !== "") values[key] = Number(value);
      else delete values[key];
    }
    await this.updateParameters(values, false, false, "PATCH");
  }
  async resetSettings() {
    if (!this.state) return;
    const entry = this.entry,
      generation = this.generation,
      editRevision = this.settingsEditRevision || 0,
      progressionDraft = this.progressionDraft,
      request = (this.settingsRequestSerial = (this.settingsRequestSerial || 0) + 1);
    const saved = await this.api(`/${entry}/parameters/reset`, "POST");
    await this.waitForConfiguration(entry, saved.parameters, saved.configuration);
    if (
      this.entry !== entry ||
      this.generation !== generation ||
      this.settingsRequestSerial !== request
    )
      return;
    if ((this.settingsEditRevision || 0) === editRevision) {
      this.settingsEntry = null;
      this.shadowRoot
        .querySelectorAll("#parameters input[data-edited]")
        .forEach((input) => delete input.dataset.edited);
    }
    if (this.progressionDraft === progressionDraft) this.progressionDraft = null;
    await this.refresh();
    if (
      this.entry !== entry ||
      this.generation !== generation ||
      this.settingsRequestSerial !== request
    )
      return;
    const status = this.shadowRoot.querySelector("#settings-reset-status");
    if (status) status.textContent = "Werkseinstellungen wurden wiederhergestellt.";
  }
  isPanelFullscreen() {
    const document = this.ownerDocument;
    return (
      !!document?.documentElement &&
      document.fullscreenElement === document.documentElement
    );
  }
  hasHomeAssistantNavigation() {
    for (let node = this; node; node = node.parentNode || node.host) {
      if (node.localName === "home-assistant-main") return true;
    }
    return false;
  }
  fullscreenAvailable() {
    return (
      !!this.ownerDocument?.fullscreenEnabled &&
      typeof this.ownerDocument.documentElement?.requestFullscreen === "function" &&
      typeof this.ownerDocument.exitFullscreen === "function"
    );
  }
  syncFullscreenKiosk(fullscreen) {
    if (!!this.fullscreenKioskActive === fullscreen) return;
    this.fullscreenKioskActive = fullscreen;
    const window = this.fullscreenDocument?.defaultView;
    if (!window) return;
    if (fullscreen) {
      if (!this.hasHomeAssistantNavigation() || !this._hass || this._hass.kioskMode)
        return;
      this.fullscreenKioskOwned = true;
    } else {
      if (!this.fullscreenKioskOwned) return;
      this.fullscreenKioskOwned = false;
    }
    this.fullscreenKioskDispatching = true;
    try {
      window.dispatchEvent(
        new CustomEvent("hass-kiosk-mode", { detail: { enable: fullscreen } }),
      );
    } finally {
      this.fullscreenKioskDispatching = false;
    }
  }
  syncFullscreenNavigation() {
    const fullscreen = this.isPanelFullscreen();
    this.syncFullscreenKiosk(fullscreen);
    const menu = this.$('[data-action="menu"]');
    if (menu) menu.hidden = !fullscreen || !this.hasHomeAssistantNavigation();
    const navigation = this.$(".main-tabs");
    if (navigation) navigation.hidden = false;
    const button = this.$('[data-action="fullscreen"]');
    if (button) {
      const label = fullscreen ? "Vollbild verlassen" : "Vollbild";
      button.hidden = !fullscreen && !this.fullscreenAvailable();
      button.disabled = !!this.fullscreenRequest;
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
    }
    this.$('[data-fullscreen-icon="enter"]')?.toggleAttribute("hidden", fullscreen);
    this.$('[data-fullscreen-icon="exit"]')?.toggleAttribute("hidden", !fullscreen);
  }
  async toggleFullscreen() {
    if (this.fullscreenRequest) return;
    const fullscreen = this.isPanelFullscreen();
    if (!fullscreen && !this.fullscreenAvailable()) return;
    this.fullscreenRequest = true;
    this.syncFullscreenNavigation();
    try {
      if (fullscreen) await this.ownerDocument.exitFullscreen();
      else await this.ownerDocument.documentElement.requestFullscreen();
    } catch {
      throw Error("Die Vollbildansicht konnte nicht umgeschaltet werden.");
    } finally {
      this.fullscreenRequest = false;
      this.syncFullscreenNavigation();
    }
  }
  syncNavigation() {
    this.navigation ??= { main: "overview", detail: "detail" };
    if (
      this.state &&
      this.navigation.main === "details" &&
      !this.state.permissions?.admin
    )
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
        button.setAttribute(
          "aria-current",
          button.dataset.action === main ? "page" : "false",
        ),
      );
    this.shadowRoot
      ?.querySelectorAll(".detail-tabs button")
      ?.forEach((button) =>
        button.setAttribute(
          "aria-current",
          button.dataset.action === detail ? "page" : "false",
        ),
      );
    for (const [selector, hidden] of [
      ["#current", view !== "overview"],
      ["#details", view !== "detail"],
      ["#history", !["history", "diagnostics"].includes(view)],
      ["#settings", view !== "settings"],
      ["#control-history", !(main === "details" && detail === "detail-history")],
      ["#history-inspection", main === "details" && detail === "detail-history"],
      [".history-plot-frame", main === "details" && detail === "detail-history"],
      [".history-readout", main === "details"],
      ["#history-legends", main === "details"],
      [
        "#plots",
        view === "diagnostics" || (main === "details" && detail === "detail-history"),
      ],
      ["#detection-plots", view !== "diagnostics"],
      ["#gangs", main === "details"],
      ["#event-list", main === "details"],
    ]) {
      const node = this.$(selector);
      if (node) node.hidden = hidden;
    }
    this.syncFullscreenNavigation();
    if (view === "overview") this.fitInstrumentReadouts();
  }
  setPanelView(action, preserveEventFocus = false) {
    if (!preserveEventFocus) this.pendingEventFocus = null;
    this.setSettingsMenu(false);
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
  manualControlAvailability() {
    const permissions = this.state?.permissions || {},
      manualMode = this.state?.configuration?.control_mode === "manual",
      running = !!this.state?.session && !!this.state?.operation_enabled,
      available = manualMode || running;
    return {
      heater: !!permissions.heater && available,
      light: !!permissions.light && available,
      brightness: !!permissions.light && available,
    };
  }
  outputState(key) {
    const observation = this.state?.manual_controls?.[key]?.observation;
    if (!observation?.available) return null;
    if (key === "heater")
      return typeof observation.on === "boolean" ? observation.on : null;
    return Number.isFinite(observation.brightness_percent)
      ? observation.brightness_percent > 0
      : null;
  }
  pendingControlCommand(key) {
    const command = this.controlCommands?.[key];
    return command &&
      command.entry === this.entry &&
      command.generation === this.generation
      ? command
      : null;
  }
  controlCommandAttributes(key, value) {
    const command = this.pendingControlCommand(key);
    if (!command || command.value !== value) return "";
    const text =
      command.stage === "sending" ? "Auftrag wird gesendet" : "Rückmeldung ausstehend";
    return ` data-command-pending="${command.stage}" aria-busy="true" aria-description="${text}"`;
  }
  controlCommandLabel(key, value, label) {
    return this.pendingControlCommand(key)?.value === value
      ? `${label}<span aria-hidden="true"> …</span><span class="sr-only" role="status"> – ${this.pendingControlCommand(key).stage === "sending" ? "Auftrag wird gesendet" : "Rückmeldung ausstehend"}</span>`
      : label;
  }
  reconcileControlCommands() {
    const state = this.state;
    for (const [key, command] of Object.entries(this.controlCommands || {})) {
      if (command !== this.pendingControlCommand(key)) {
        delete this.controlCommands[key];
        continue;
      }
      if (command.stage === "sending") continue;
      const control = state?.manual_controls?.[key];
      let confirmed = false;
      if (key === "operation") confirmed = state.operation_enabled === command.value;
      else if (key === "control-mode")
        confirmed = state.configuration.control_mode === command.value;
      else if (key === "heater") {
        confirmed =
          control?.manual === command.value &&
          typeof control?.commanded === "boolean" &&
          this.outputState(key) === control.commanded;
      } else if (key === "light") {
        confirmed =
          command.value === null
            ? control?.manual === null
            : control?.manual === command.manualTarget &&
              Number.isFinite(control.manual_feedback_target) &&
              control?.observation?.available &&
              Math.round(control.observation.brightness_percent) ===
                Math.round(control.manual_feedback_target);
      }
      const ended =
        (key === "heater" || key === "light") &&
        (command.mode !== state.configuration?.control_mode ||
          command.session !== (state.session?.timeline?.session_id ?? null));
      const failed = (state.issues || []).some(({ key: issue }) =>
        key === "heater"
          ? [
              "heater_service_unavailable",
              "heater_feedback_mismatch",
              "heater_feedback_unavailable",
              "heater_still_heating",
            ].includes(issue)
          : key === "light" &&
            ["operation_light", "after_run_light", "session_light"].includes(issue),
      );
      if (confirmed || ended || failed) delete this.controlCommands[key];
    }
  }
  async sendControlCommand(key, value, path, body) {
    if (this.pendingControlCommand(key)?.value === value) return;
    const command = {
      value,
      entry: this.entry,
      generation: this.generation,
      stage: "sending",
      mode: this.state.configuration?.control_mode,
      session: this.state.session?.timeline?.session_id ?? null,
      manualTarget:
        key !== "light"
          ? undefined
          : value === true
            ? this.state.configuration.parameters?.session_light_brightness_percent
            : value === false
              ? 0
              : value,
    };
    this.controlCommands ??= {};
    this.controlCommands[key] = command;
    this.controlRevision = (this.controlRevision || 0) + 1;
    this.drawCurrent();
    const current = () => this.pendingControlCommand(key) === command;
    try {
      await this.api(`/${command.entry}/${path}`, "POST", body);
      if (!current()) return;
      command.stage = "waiting";
      this.controlRevision++;
      this.drawCurrent();
      // A poll that started before the command cannot acknowledge its result.
      // Queue a fresh read even if another read is still in flight.
      await this.refresh(true);
    } catch (error) {
      if (current()) {
        delete this.controlCommands[key];
        this.controlRevision++;
        this.drawCurrent();
        // A failed response does not prove the physical command was not applied.
        void this.refresh(true);
        throw error;
      }
    }
  }
  async submitLight(value) {
    const light = this.state?.manual_controls?.light,
      observation = light?.observation,
      same =
        typeof value === "boolean"
          ? this.outputState("light") === value
          : typeof value === "number" &&
            observation?.available &&
            Math.round(observation.brightness_percent) === value,
      entry = this.entry,
      generation = this.generation,
      revision = this.manualLightRevision || 0,
      request = (this.manualLightRequest = (this.manualLightRequest || 0) + 1);
    try {
      if (!this.manualControlAvailability().light) return;
      if (!same || this.pendingControlCommand("light"))
        await this.sendControlCommand("light", value, "light", { value });
    } finally {
      if (
        this.entry === entry &&
        this.generation === generation &&
        (this.manualLightRevision || 0) === revision &&
        this.manualLightRequest === request
      ) {
        this.manualLightDraft = null;
        if (!this.lightInteraction) this.drawCurrent();
      }
    }
    if (this.entry !== entry || this.generation !== generation) return;
    await this.refresh(true);
  }
  lightTargetValue() {
    const observation = this.state?.manual_controls?.light?.observation;
    return Number(
      this.manualLightDraft ??
        (observation?.available && Number.isFinite(observation.brightness_percent)
          ? observation.brightness_percent
          : 0),
    );
  }
  renderLightTarget(value) {
    const point = this.temperatureArcPoint(value, { minimum: 0, maximum: 100 }),
      track = this.$('[data-light-arc][role="slider"]'),
      handle = this.$(".light-instrument .instrument-arc-handle");
    track?.setAttribute("aria-valuenow", value);
    track?.setAttribute("aria-valuetext", `${num(value, 0)} %`);
    handle?.setAttribute("cx", point.x);
    handle?.setAttribute("cy", point.y);
  }
  lightValueAt(svg, x, y) {
    const step = this.frontendStep("brightness_step_percent");
    return Math.max(
      0,
      Math.min(
        100,
        Math.round((this.instrumentArcFractionAt(svg, x, y) * 100) / step) * step,
      ),
    );
  }
  beginLightDrag(event, svg) {
    if (!svg || !this.manualControlAvailability().brightness || this.lightInteraction)
      return;
    event.preventDefault();
    this.lightInteraction = {
      svg,
      pointerId: event.pointerId,
      value: this.lightValueAt(svg, event.clientX, event.clientY),
    };
    svg.setPointerCapture?.(event.pointerId);
    this.updateLightDrag(event);
  }
  updateLightDrag(event) {
    const interaction = this.lightInteraction;
    if (!interaction) return;
    event.preventDefault();
    interaction.value = this.lightValueAt(
      interaction.svg,
      event.clientX,
      event.clientY,
    );
    this.manualLightDraft = String(interaction.value);
    this.manualLightRevision = (this.manualLightRevision || 0) + 1;
    this.renderLightTarget(interaction.value);
  }
  cancelLightDrag(redraw = true) {
    const interaction = this.lightInteraction;
    if (!interaction) return;
    this.lightInteraction = null;
    this.manualLightDraft = null;
    if (interaction.svg?.hasPointerCapture?.(interaction.pointerId))
      interaction.svg.releasePointerCapture?.(interaction.pointerId);
    if (redraw) this.drawCurrent();
  }
  async endLightDrag(event) {
    const interaction = this.lightInteraction;
    if (!interaction) return;
    interaction.pointerId = null;
    interaction.svg.releasePointerCapture?.(event.pointerId);
    try {
      await this.submitLight(interaction.value);
    } finally {
      if (this.lightInteraction === interaction) {
        this.lightInteraction = null;
        this.drawCurrent();
      }
    }
  }
  async keyLightTarget(event) {
    if (!this.manualControlAvailability().brightness || this.lightInteraction) return;
    const value = this.lightTargetValue(),
      step = this.frontendStep("brightness_step_percent"),
      next = {
        ArrowLeft: value - step,
        ArrowDown: value - step,
        ArrowRight: value + step,
        ArrowUp: value + step,
        PageDown: value - step * 10,
        PageUp: value + step * 10,
        Home: 0,
        End: 100,
      }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const interaction = { value: Math.max(0, Math.min(100, Math.round(next))) };
    this.lightInteraction = interaction;
    this.manualLightDraft = String(interaction.value);
    this.manualLightRevision = (this.manualLightRevision || 0) + 1;
    this.renderLightTarget(interaction.value);
    try {
      await this.submitLight(interaction.value);
    } finally {
      if (this.lightInteraction === interaction) {
        this.lightInteraction = null;
        this.drawCurrent();
        this.$('[data-light-arc][role="slider"]')?.focus?.();
      }
    }
  }
  async action(action) {
    if (action === "fullscreen") return this.toggleFullscreen();
    if (action === "menu") {
      if (!this.isPanelFullscreen() || !this.hasHomeAssistantNavigation()) return;
      this.dispatchEvent(
        new CustomEvent("hass-toggle-menu", {
          bubbles: true,
          composed: true,
          detail: {},
        }),
      );
      return;
    }
    if (action.startsWith("archive-")) {
      if (!this.state?.permissions?.admin) return;
      if (action === "archive-list") return this.loadArchiveManagement();
      if (action === "archive-confirm") return this.eraseArchive();
      if (this.archiveAdminBusy) return;
      if (action === "archive-cancel") this.archiveDeletePending = null;
      else if (!this.state.session && action === "archive-reset")
        this.archiveDeletePending = { reset: true };
      else if (!this.state.session && action.startsWith("archive-delete:")) {
        const id = decodeURIComponent(action.slice(15));
        this.archiveDeletePending =
          this.archiveAdminSessions?.find(
            (session) => session.session_id === id && session.ended_at,
          ) || null;
      }
      this.drawArchiveManagement();
      return;
    }
    if (!this.state) return;
    this.message(null);
    const permissions = this.state?.permissions || {},
      manualControls = this.manualControlAvailability();
    if (
      ((action === "operation" || action.startsWith("finish-session:")) &&
        !permissions.control) ||
      (action.startsWith("preset:") && !permissions.temperature) ||
      (action === "program-apply" && !permissions.program) ||
      (action === "button-gesture" &&
        (!permissions.control || this.state.configuration_locked)) ||
      ((action === "program-add" ||
        action === "program-save" ||
        action.startsWith("program-remove:") ||
        action === "button-program") &&
        (!permissions.program || this.state.configuration_locked)) ||
      (action.startsWith("light:") && !manualControls.light) ||
      (action === "manual-light-overview" && !manualControls.brightness) ||
      (action.startsWith("heater:") && !manualControls.heater) ||
      (action.startsWith("control-mode:") &&
        (!permissions.control || this.state.configuration_locked)) ||
      (action.startsWith("end-phase:") &&
        (!permissions.control || !action.startsWith("end-phase:after_run:"))) ||
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
    if (action === "manual-entry") return this.action("control-mode:manual");
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
      if (this.programDirty() || this.programRequest) return;
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
    if (action.startsWith("event-marker:")) {
      if (!permissions.admin) return;
      const eventId = action.slice(13);
      this.historyDetail = false;
      this.positions = new Set([this.historyPrimaryPosition()]);
      this.pendingEventFocus = { kind: "row", eventId };
      this.setPanelView("history", true);
      this.focusEvent(eventId, true);
      return;
    }
    if (action.startsWith("event-row:")) {
      if (!permissions.admin) return;
      const eventId = action.slice(10);
      this.pendingEventFocus = { kind: "marker", eventId };
      this.setPanelView("diagnostics", true);
      this.focusEvent(eventId);
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
        this.positions = new Set([this.historyPrimaryPosition()]);
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
    if (action.startsWith("preset:")) {
      const temperature = this.roundTargetTemperature(Number(action.slice(7)));
      if (!this.state.session) return this.changeTarget(temperature);
      if (!permissions.program || this.programRequest) return;
      this.programSelectionDraft = { mode: "constant", temperature };
      this.freeProgramKind = null;
      this.freeProgramStepsDraft = null;
      this.progressionDraft = null;
      this.markProgramEdited();
      return;
    }
    if (action.startsWith("program-mode:")) {
      if (!this.programChoiceAvailable()) return;
      this.selectProgramMode(
        action.slice(13),
        this.state.configuration.temperature_programs || [],
      );
      if (!this.state.session) await this.applyProgram();
      return;
    }
    if (action.startsWith("program-select:")) {
      if (!this.programChoiceAvailable()) return;
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
      if (!this.programChoiceAvailable()) return;
      this.setFreeProgramKind(action.slice(13));
      if (!this.state.session) await this.applyProgram();
      return;
    }
    if (action.startsWith("program-info:")) {
      const id = action.slice(13);
      const open = this.programInfoOpen !== id;
      this.closeInfo();
      if (open) {
        this.programInfoOpen = id;
        this.syncInfo();
      }
      return;
    }
    if (action.startsWith("catalog-kind:")) {
      const choice = action.slice("catalog-kind:".length),
        separator = choice.indexOf(":"),
        kind = choice.slice(0, separator),
        id = choice.slice(separator + 1);
      if (separator < 0 || !["even", "steps"].includes(kind)) return;
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
      if (!steps?.length) throw Error("Programmwerte zuerst prüfen");
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
    if (action === "button-gesture") {
      const gesture = this.$("#button-session-gesture").value;
      await this.api(`/${this.entry}/button-gesture`, "POST", { gesture });
      await this.refresh();
      return;
    }
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
        payload.temperature_c = this.roundTargetTemperature(value, bounds);
      }
      await this.api(`/${this.entry}/button-program`, "POST", payload);
      await this.refresh();
      return;
    }
    if (action.startsWith("control-mode:")) {
      const mode = action.slice(13);
      if (mode !== "automatic" && mode !== "manual")
        throw Error("Ungültiger Betriebsmodus");
      await this.sendControlCommand("control-mode", mode, "control-mode", { mode });
      return;
    }
    if (action.startsWith("settings-section:")) {
      this.selectSettingsSection(action.slice(17));
      this.setSettingsMenu(false, true);
      return;
    }
    if (action === "settings-menu" || action === "settings-menu-close") {
      const open =
        action === "settings-menu" &&
        !this.$(".settings-layout")?.hasAttribute("data-menu-open");
      this.setSettingsMenu(open, !open);
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
      return this.submitLight(value);
    }
    if (action === "manual-light-overview") {
      const input = this.$("#manual-light-value-overview"),
        value = Number(input?.value);
      if (!input?.value?.trim() || !Number.isInteger(value) || value < 0 || value > 100)
        throw Error("Helligkeit zwischen 0 und 100 % in ganzen Prozent eingeben");
      return this.submitLight(value);
    }
    if (action.startsWith("heater:")) {
      const preset = action.slice(7),
        value = preset === "true" ? true : preset === "false" ? false : null;
      if (preset !== "true" && preset !== "false" && preset !== "auto")
        throw Error("Ungültige Ofensteuerung");
      if (
        this.state.configuration.control_mode !== "manual" &&
        value != null &&
        this.outputState("heater") === value &&
        !this.pendingControlCommand("heater")
      )
        return;
      await this.sendControlCommand("heater", value, "heater", { value });
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
      const enabled = !this.state.operation_enabled;
      await this.sendControlCommand("operation", enabled, "control", { enabled });
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
