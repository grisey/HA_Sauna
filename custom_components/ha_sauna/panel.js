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
const stamp = (v) => (v ? new Date(v).getTime() : null);
const energyIntervalTransports = new WeakMap();
// Date alone drops the owner's microseconds. Compare whole seconds and the
// original fraction separately, retaining the wire strings at every boundary.
const energyIntervalTime = (value) => {
  const match =
    typeof value === "string" &&
    /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|[+-]\d\d:\d\d)$/.exec(value);
  if (!match) return null;
  const seconds = Date.parse(`${match[1]}${match[3]}`);
  return Number.isFinite(seconds) ? [seconds, (match[2] || "").padEnd(6, "0")] : null;
};
const compareEnergyIntervalTimes = (left, right) =>
  left[0] - right[0] || (left[1] < right[1] ? -1 : left[1] > right[1] ? 1 : 0);
const energyIntervalTransport = (energy) =>
  energy && energyIntervalTransports.get(energy);
const energyIntervalSignature = (energy) => {
  if (!energy) return energy;
  const { intervals, interval_delta, intervals_available, ...scalar } = energy,
    transport = energyIntervalTransport(energy);
  return {
    ...scalar,
    interval_horizon: transport?.horizon || null,
    interval_revision: transport?.revision || 0,
    interval_bootstrap_needed: !!transport?.bootstrapNeeded,
  };
};
const mergeEnergyIntervals = (current, incoming, startedAt) => {
  if (!incoming) return incoming;
  const { intervals, interval_delta: delta, intervals_available, ...energy } = incoming;
  // Omitted evidence is legacy. It neither inherits a previous series nor
  // supplies a cursor merely because its scalar accounted_at advanced.
  if (!Object.hasOwn(incoming, "interval_delta")) return energy;
  const previous = energyIntervalTransport(current),
    transport = {
      horizon: previous?.horizon || null,
      revision: previous?.revision || 0,
      intervals: previous?.intervals || [],
      bootstrapNeeded: false,
    },
    finish = () => {
      if (transport.horizon)
        Object.defineProperty(energy, "intervals", { value: transport.intervals });
      energyIntervalTransports.set(energy, transport);
      return energy;
    },
    retry = () => {
      transport.bootstrapNeeded = true;
      return finish();
    };
  if (!delta || !Array.isArray(delta.intervals)) return retry();
  const to = energyIntervalTime(delta.to),
    accounted = energyIntervalTime(incoming.accounted_at),
    bootstrap = delta.from === null,
    from = energyIntervalTime(bootstrap ? startedAt : delta.from),
    horizon = energyIntervalTime(transport.horizon);
  if (
    !to ||
    !accounted ||
    !from ||
    compareEnergyIntervalTimes(to, accounted) !== 0 ||
    compareEnergyIntervalTimes(from, to) > 0 ||
    (!bootstrap && (!horizon || compareEnergyIntervalTimes(from, horizon) > 0))
  )
    return retry();
  // Validate the entire received suffix before changing the cached prefix.
  // This checks transport continuity and original provenance, not accounting.
  let covered = from;
  const additions = [];
  for (const interval of delta.intervals) {
    const start = energyIntervalTime(interval?.started_at),
      end = energyIntervalTime(interval?.ended_at),
      validSource =
        (interval?.source === "unknown" && interval.power_kw === null) ||
        (["measured", "estimated"].includes(interval?.source) &&
          typeof interval.power_kw === "number" &&
          Number.isFinite(interval.power_kw));
    if (
      !start ||
      !end ||
      !validSource ||
      compareEnergyIntervalTimes(start, covered) !== 0 ||
      compareEnergyIntervalTimes(end, start) <= 0 ||
      compareEnergyIntervalTimes(end, to) > 0
    )
      return retry();
    covered = end;
    if (!bootstrap && compareEnergyIntervalTimes(end, horizon) <= 0) continue;
    additions.push({
      started_at:
        !bootstrap && compareEnergyIntervalTimes(start, horizon) < 0
          ? transport.horizon
          : interval.started_at,
      ended_at: interval.ended_at,
      source: interval.source,
      power_kw: interval.power_kw,
    });
  }
  if (
    compareEnergyIntervalTimes(covered, to) !== 0 ||
    (bootstrap &&
      !delta.intervals.length &&
      ["measured_seconds", "estimated_seconds", "unknown_seconds"].some(
        (key) => incoming[key] !== 0,
      ))
  )
    return retry();
  // A delayed, older snapshot cannot roll a confirmed series back. Archive
  // and status streams own separate arrays, including across current/last IDs.
  if (horizon && compareEnergyIntervalTimes(to, horizon) < 0) {
    transport.bootstrapNeeded = !!previous.bootstrapNeeded;
    return finish();
  }
  if (bootstrap) transport.intervals = [];
  for (const interval of additions) {
    const tail = transport.intervals.at(-1);
    if (
      tail &&
      compareEnergyIntervalTimes(
        energyIntervalTime(tail.ended_at),
        energyIntervalTime(interval.started_at),
      ) === 0 &&
      tail.source === interval.source &&
      tail.power_kw === interval.power_kw
    )
      transport.intervals[transport.intervals.length - 1] = {
        ...tail,
        ended_at: interval.ended_at,
      };
    else transport.intervals.push(interval);
  }
  if (bootstrap || !horizon || compareEnergyIntervalTimes(to, horizon) > 0)
    transport.revision++;
  transport.horizon = delta.to;
  // A suffix cannot settle an earlier discontinuity: only a bootstrap can.
  transport.bootstrapNeeded = !bootstrap && !!previous?.bootstrapNeeded;
  return finish();
};
const historyRangeEnergyMemos = new WeakMap();
const energySelectionTime = (value) => {
  if (typeof value === "string") return energyIntervalTime(value);
  if (!Number.isFinite(value)) return null;
  // Preserve chart fractions relative to their whole second, without rounding
  // through Date or scaling the full epoch into microseconds.
  const wholeSecond = Math.floor(value / 1000) * 1000;
  return [wholeSecond, (value - wholeSecond) * 1000];
};
const energyForHistoryRange = (energy, start, end, scope) => {
  const transport = energyIntervalTransport(energy);
  if (!transport || !Array.isArray(transport.intervals)) return null;
  const { intervals, revision, horizon } = transport,
    accountedAt = energy.accounted_at,
    memo = historyRangeEnergyMemos.get(intervals);
  if (
    memo &&
    memo.scope === scope &&
    memo.revision === revision &&
    memo.horizon === horizon &&
    memo.accountedAt === accountedAt &&
    memo.start === start &&
    memo.end === end
  )
    return memo.value;
  const finish = (value) => {
      historyRangeEnergyMemos.set(intervals, {
        scope,
        revision,
        horizon,
        accountedAt,
        start,
        end,
        value,
      });
      return value;
    },
    started = energySelectionTime(start),
    ended = energySelectionTime(end),
    accounted = energyIntervalTime(accountedAt),
    confirmed = energyIntervalTime(horizon);
  if (
    !started ||
    !ended ||
    compareEnergyIntervalTimes(started, ended) >= 0 ||
    !accounted ||
    !confirmed ||
    compareEnergyIntervalTimes(ended, accounted) > 0 ||
    compareEnergyIntervalTimes(ended, confirmed) > 0 ||
    !intervals.length
  )
    return finish(null);
  let first = 0,
    last = intervals.length;
  while (first < last) {
    const middle = Math.floor((first + last) / 2),
      boundary = energyIntervalTime(intervals[middle]?.ended_at);
    if (!boundary) return finish(null);
    if (compareEnergyIntervalTimes(boundary, started) <= 0) first = middle + 1;
    else last = middle;
  }
  const value = {
    measured_kwh: 0,
    estimated_kwh: 0,
    measured_seconds: 0,
    estimated_seconds: 0,
    unknown_seconds: 0,
  };
  let cursor = started,
    measuredPeakW = null;
  // The transport already validated the series. Only inspect contributions
  // intersecting [start, end), retaining their original source and rate.
  for (let index = first; index < intervals.length; index++) {
    const interval = intervals[index],
      originalStart = energyIntervalTime(interval?.started_at),
      originalEnd = energyIntervalTime(interval?.ended_at);
    if (
      !originalStart ||
      !originalEnd ||
      compareEnergyIntervalTimes(originalEnd, originalStart) <= 0
    )
      return finish(null);
    if (compareEnergyIntervalTimes(originalStart, ended) >= 0) break;
    const clippedStart =
        compareEnergyIntervalTimes(originalStart, started) < 0
          ? started
          : originalStart,
      clippedEnd =
        compareEnergyIntervalTimes(originalEnd, ended) > 0 ? ended : originalEnd,
      source = interval.source,
      power = interval.power_kw,
      validSource =
        (source === "unknown" && power === null) ||
        (["measured", "estimated"].includes(source) &&
          typeof power === "number" &&
          Number.isFinite(power));
    if (
      compareEnergyIntervalTimes(clippedStart, cursor) !== 0 ||
      compareEnergyIntervalTimes(clippedEnd, clippedStart) <= 0 ||
      !validSource
    )
      return finish(null);
    const seconds =
      (clippedEnd[0] - clippedStart[0]) / 1000 +
      (Number(clippedEnd[1]) - Number(clippedStart[1])) / 1000000;
    if (source === "unknown") value.unknown_seconds += seconds;
    else {
      value[`${source}_seconds`] += seconds;
      value[`${source}_kwh`] += (power * seconds) / 3600;
      if (source === "measured")
        measuredPeakW =
          measuredPeakW === null ? power * 1000 : Math.max(measuredPeakW, power * 1000);
    }
    cursor = clippedEnd;
    if (compareEnergyIntervalTimes(cursor, ended) === 0) {
      // Only measured overlap supplies W8. The existing energy contribution
      // and duration provide its time mean; estimates never fill a gap.
      value.measured_power =
        value.measured_seconds > 0
          ? {
              mean_w: (value.measured_kwh * 3600000) / value.measured_seconds,
              peak_w: measuredPeakW,
              measured_seconds: value.measured_seconds,
              complete: value.estimated_seconds === 0 && value.unknown_seconds === 0,
            }
          : null;
      return finish(value);
    }
  }
  return finish(null);
};
const orderedHistoryEvents = (items = []) =>
  items
    .map((event, index) => ({ event, index, at: stamp(event.effective_at) }))
    .sort((a, b) => {
      const left = Number.isFinite(a.at) ? a.at : Infinity;
      const right = Number.isFinite(b.at) ? b.at : Infinity;
      return left === right ? a.index - b.index : left - right;
    })
    .map(({ event }) => event);
const timestampFormatOptions = Object.freeze({
  date: Object.freeze({ day: "2-digit", month: "2-digit" }),
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
  clockSeconds: Object.freeze({
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }),
});
const timestampFormatters = new Map();
// Resolve once for a tooltip/axis batch. Zone names matter: two zones with the
// same current offset can have different rules at the measurement's timestamp.
const localTimeZone = () => new Intl.DateTimeFormat().resolvedOptions().timeZone;
const timestampFormatter = (style, timeZone = localTimeZone(), limit) => {
  const key = `${style}:${timeZone || "default"}`;
  if (!Number.isInteger(limit) || limit <= 0)
    return new Intl.DateTimeFormat("de-DE", {
      ...timestampFormatOptions[style],
      ...(timeZone ? { timeZone } : {}),
    });
  let formatter = timestampFormatters.get(key);
  if (formatter) {
    timestampFormatters.delete(key);
    timestampFormatters.set(key, formatter);
    while (timestampFormatters.size > limit)
      timestampFormatters.delete(timestampFormatters.keys().next().value);
    return formatter;
  }
  formatter = new Intl.DateTimeFormat("de-DE", {
    ...timestampFormatOptions[style],
    ...(timeZone ? { timeZone } : {}),
  });
  timestampFormatters.set(key, formatter);
  while (timestampFormatters.size > limit)
    timestampFormatters.delete(timestampFormatters.keys().next().value);
  return formatter;
};
const when = (v, timeZone, limit) => {
  if (!v) return "–";
  const date = new Date(v);
  // Intl.DateTimeFormat#format throws for an invalid date, while the original
  // Date#toLocaleString path returns its established fallback text.
  return Number.isFinite(date.getTime())
    ? timestampFormatter("when", timeZone, limit).format(date)
    : date.toLocaleString("de-DE", timestampFormatOptions.when);
};
const tooltipWhen = (v, timeZone, limit) => {
  if (
    (typeof v !== "string" && (typeof v !== "number" || !Number.isFinite(v))) ||
    (typeof v === "string" && !v.trim())
  )
    return null;
  const date = new Date(v);
  if (!Number.isFinite(date.getTime())) return null;
  const fraction =
    typeof v === "string" ? v.match(/\.(\d+)(?:Z|[+-]\d\d:\d\d)?$/i)?.[1] : null;
  return timestampFormatter("tooltip", timeZone, limit)
    .formatToParts(date)
    .map((part) =>
      part.type === "second" && fraction ? `${part.value}.${fraction}` : part.value,
    )
    .join("");
};
const clock = (v, timeZone, limit) => {
  const date = new Date(v);
  return Number.isFinite(date.getTime())
    ? timestampFormatter("clock", timeZone, limit).format(date)
    : date.toLocaleTimeString("de-DE", timestampFormatOptions.clock);
};
const num = (v, d) =>
  v == null
    ? "–"
    : Number.isInteger(d)
      ? Number(v).toLocaleString("de-DE", { maximumFractionDigits: d })
      : String(v);
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
  ventilation_confirmed: "Lüften bestätigt",
  ventilation_complete: "Vollständiges Lüften bestätigt",
  operation_off: "Betrieb ausgeschaltet",
  confirmation_expired: "Vorläufigen Gang aufgehoben",
  presence_confirmed: "Gangbeginn · Präsenz nach Türvorgang",
  presence_ended: "Gangende · Abwesenheit und Durchlüften",
};
const historyEventMarkerKinds = new Set(["door_open", "door_close", "infusion"]);
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
const appearanceTickValues = (bounds, target, maximum, tolerance, precision) => {
  if (
    !bounds ||
    !Number.isFinite(bounds.minimum) ||
    !Number.isFinite(bounds.maximum) ||
    bounds.maximum <= bounds.minimum ||
    !Number.isFinite(bounds.maximum - bounds.minimum)
  )
    return [];
  const span = bounds.maximum - bounds.minimum;
  const rough = span / target;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step =
    [1, 2, 5, 10]
      .map((unit) => unit * power)
      .find((value) => span / value <= maximum) || 10 * power;
  if (!Number.isFinite(step) || step <= 0) return [bounds.minimum, bounds.maximum];
  const values = [bounds.minimum];
  for (
    let index = Math.ceil(bounds.minimum / step), count = 0;
    count < maximum;
    index++, count++
  ) {
    const value = index * step;
    if (value >= bounds.maximum - step * tolerance) break;
    if (value > bounds.minimum + step * tolerance)
      values.push(Number(value.toPrecision(precision)));
  }
  values.push(bounds.maximum);
  return values;
};

// User-facing history joins available observations across reporting gaps.
// Original missing/stale observations remain available to hover and exports.
const historyMeasurementTtlSeconds = (panel, session) =>
  Number(
    session?.measurement_ttl_seconds ??
      session?.configuration?.parameters?.sensor_timeout_seconds ??
      panel.state?.measurement_ttl_seconds ??
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
const upperBoundHistory = (values, time) => {
  let low = 0,
    high = values.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (values[middle].time <= time) low = middle + 1;
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
const roundHistoryCoordinate = (value, precision) => Number(value.toFixed(precision));

// Return numeric Canvas commands, not SVG text.  The equations and the
// shared coordinate precision keep Canvas and SVG geometry consistent.
const monotoneHistoryCommands = (points, x, y, target, precision) => {
  const commands = [];
  if (!points.length) return [];
  const at = (value) => roundHistoryCoordinate(value, precision);
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
      globalAlpha: 1,
      lineDash: [],
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
      monotoneHistoryCommands(
        reduceHistorySegment(segment, x),
        x,
        y,
        path,
        snapshot.coordinatePrecision,
      );
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
      snapshot.coordinatePrecision,
      position,
      quantity,
      historyStyleKey(style),
      ...(quantity === "temperature"
        ? [snapshot.low, snapshot.high]
        : [snapshot.humidityLow, snapshot.humidityHigh, snapshot.humidityMode]),
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
    const width = snapshot.width,
      height = snapshot.height;
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
function HistoryInteraction(panel, surface, wrap, tooltip, cursor) {
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

  const number = (value) => panel.historyReadoutNumber(value);
  const dateLabel = (value, timeZone) => panel.clock(value, timeZone);
  const sourceValue = (value) => `${typeof value}:${String(value)}`;
  const rowText = (nearest, quantity) => {
    const source = nearest.source;
    const cacheable = source && typeof source === "object";
    const humidity = panel.historyHumidityDisplay?.(),
      unit = quantity === "temperature" ? "°C" : humidity?.unit || "%",
      precision = panel.preference("history_readout_precision");
    const key = [quantity, unit, precision, sourceValue(nearest.value)].join("|");
    const cached = cacheable ? state.rowTextCache.get(source) : null;
    if (cached?.key === key) return cached.value;
    const value = { number: number(nearest.value), unit };
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
    if (["scroll", "viewport-scroll"].includes(reason))
      for (const pointer of panel.chartPointers?.values() || []) pointer.tap = false;
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
    const timeAxisRect = rectFor(panel.$("[data-history-time-axis]"));
    const wrapRect = rectFor(wrap || surface);
    const labelFontFamily = view.getComputedStyle?.(surface)?.fontFamily;
    const nameLineHeight =
        panel.preference("font_size_small_px") * panel.preference("body_line_height"),
      bandNames = {
        phase:
          rectFor(panel.$("[data-history-band-names]")).height ||
          state.geometry?.bandNames?.phase ||
          nameLineHeight,
        event:
          rectFor(panel.$("[data-history-lower-band] > .history-band-names")).height ||
          state.geometry?.bandNames?.event ||
          nameLineHeight,
      };
    const dpr = pixelRatio();
    state.geometry = {
      dpr,
      labelFontFamily,
      bandNames,
      rects: { surface: surfaceRect, timeAxis: timeAxisRect, wrap: wrapRect },
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
  const coordinatesForGeometry = (
    geometry,
    target,
    clientX,
    clientY = 0,
    transform,
  ) => {
    const timeAxis = target?.matches?.("[data-history-time-axis]"),
      rect = timeAxis ? geometry.rects.timeAxis : geometry.rects.surface;
    const plot = panel.historyGeometry();
    const logicalWidth = transform?.width ?? plot.width;
    const logicalHeight = timeAxis
      ? panel.historyTimeAxisHeight()
      : (transform?.height ?? plot.height);
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
    const endRows = document.createElement("div");
    const status = document.createElement("div");
    const eventList = document.createElement("div");
    heading.className = "history-tooltip-time";
    rows.className = "history-tooltip-values";
    endRows.className = "history-tooltip-values";
    endRows.hidden = true;
    status.className = "history-tooltip-status";
    eventList.className = "history-tooltip-events";
    heading.textContent = "–";
    status.textContent = "Saunastatus · –";
    eventList.hidden = true;
    const makeSeries = (container) => {
      const series = new Map();
      for (const position of ["upper", "lower"])
        for (const quantity of ["temperature", "humidity"]) {
          const row = document.createElement("div");
          const label = document.createElement("span");
          const value = document.createElement("span");
          const valueNumber = document.createElement("span");
          const valueUnit = document.createElement("span");
          label.style.color = "var(--sauna-card-text, inherit)";
          label.setAttribute("data-quantity", quantity);
          label.textContent = `${quantity === "temperature" ? "Temperatur" : "Luftfeuchte"}${panel.historyDetail ? ` ${position === "upper" ? "oben" : "unten"}` : ""}: `;
          value.className = "history-tooltip-value";
          valueNumber.className = "history-tooltip-number";
          valueUnit.className = "history-tooltip-unit";
          valueNumber.textContent = "–";
          value.append(valueNumber, valueUnit);
          row.append(label, value);
          row.hidden = !new Set(panel.positions || ["upper"]).has(position);
          container.append(row);
          series.set(`${position}:${quantity}`, {
            row,
            label,
            value,
            number: valueNumber,
            unit: valueUnit,
          });
        }
      return series;
    };
    const series = makeSeries(rows),
      endSeries = makeSeries(endRows);
    for (const row of endSeries.values())
      row.value.setAttribute("data-boundary", "end");
    const snap = document.createElement("div");
    snap.className = "history-tooltip-snap";
    snap.hidden = true;
    const valueHeading = panel.$?.(".history-readout > .history-column-heading"),
      startLabel = document.createElement("span"),
      endLabel = document.createElement("span"),
      boundaries = valueHeading || document.createElement("div"),
      boundarySpace = document.createElement("span");
    startLabel.className = endLabel.className = "history-tooltip-boundary";
    boundaries.classList.add("history-tooltip-boundaries");
    boundarySpace.textContent = valueHeading ? "Messwerte" : "";
    if (!valueHeading) boundarySpace.setAttribute("aria-hidden", "true");
    boundaries.replaceChildren(boundarySpace, startLabel, endLabel);
    if (!valueHeading) rows.prepend(boundaries);
    startLabel.hidden = endLabel.hidden = true;
    heading.hidden = true;
    const headingContainer = panel.$?.(".history-inspection-heading");
    if (headingContainer) {
      headingContainer.querySelector(".history-tooltip-time")?.remove();
      headingContainer.prepend(heading);
      tooltip.replaceChildren(rows, status);
    } else tooltip.replaceChildren(heading, rows, status);
    const eventColumn = panel.$?.(".history-event-readout");
    if (eventColumn) {
      eventColumn.querySelector(".history-tooltip-events")?.remove();
      eventColumn.prepend(eventList);
    } else tooltip.append(eventList);
    tooltip.setAttribute("data-phase", "");
    tooltip.setAttribute("data-selection", "");
    tooltip.hidden = true;
    state.tooltip = {
      heading,
      rows,
      endRows,
      series,
      endSeries,
      startLabel,
      endLabel,
      boundaries,
      valueHeading,
      status,
      eventList,
      snap,
    };
    return state.tooltip;
  };
  const layoutValues = (range) => {
    const nodes = tooltipNodes();
    nodes.startLabel.hidden = !range;
    nodes.endLabel.hidden = !range;
    nodes.boundaries.hidden = !nodes.valueHeading && !range;
    nodes.endRows.hidden = true;
    nodes.endRows.remove?.();
    for (const [key, start] of nodes.series) {
      const end = nodes.endSeries.get(key);
      if (end.value.parentNode !== start.row) start.row.append(end.value);
      end.value.hidden = !range;
    }
  };
  const setText = (node, text) => {
    if (node.textContent !== text) node.textContent = text;
  };
  const setValue = (cell, nearest, quantity) => {
    const value = nearest ? rowText(nearest, quantity) : { number: "–", unit: "" };
    setText(cell.number, value.number);
    setText(cell.unit, value.unit);
  };
  const showValueTimes = (start, end, timeZone, endLabel = "Ende") => {
    const nodes = tooltipNodes(),
      exact = (time) =>
        panel.tooltipWhen?.(time, timeZone) || dateLabel(time, timeZone);
    setText(nodes.startLabel, end == null ? "" : "Anfang");
    if (end != null) nodes.startLabel.setAttribute("title", exact(start));
    else nodes.startLabel.removeAttribute("title");
    setText(nodes.endLabel, end == null ? "" : endLabel);
    if (end != null) {
      nodes.endLabel.setAttribute("title", exact(end));
    } else nodes.endLabel.removeAttribute("title");
  };
  const hide = () => {
    if (state.disposed) return;
    if (panel.historyValueSelection) return refreshSelection();
    if (panel.historyChart?.model && panel.shown?.session)
      return refreshOverview(panel.historyChart.model);
    panel.syncHistoryInformation?.();
    if (tooltip) {
      tooltip.hidden = true;
      if (state.tooltip) {
        state.tooltip.heading.hidden = true;
        state.tooltip.eventList.hidden = true;
      }
      tooltip.setAttribute("data-phase", "");
      tooltip.setAttribute("data-selection", "");
      showSnap([]);
    }
    if (cursor && state.cursorVisible) cursor.setAttribute("visibility", "hidden");
    state.tooltipVisible = false;
    state.cursorVisible = false;
    state.cursorX = null;
    cursor?.setAttribute("data-snap", "false");
  };
  const timeAt = (event, snapshotTransform, snapshotGeometry) => {
    if (state.disposed || !event) return;
    // Geometry is read before any tooltip or cursor mutation in this turn.
    // The root renders after its own read phase.  Reuse that rectangle even
    // when a scroll/resize invalidation is pending: a hover must never force
    // layout after the root has written canvas or SVG pixels in this frame.
    const geometry = snapshotGeometry || state.geometry || readGeometry();
    const point = coordinatesForGeometry(
      geometry,
      event.svg || surface,
      event.clientX,
      event.clientY,
      snapshotTransform,
    );
    const chartWindow = snapshotTransform?.window || panel.window;
    const start = snapshotTransform?.start ?? chartWindow?.[0];
    const end = snapshotTransform?.end ?? chartWindow?.[1];
    const plot = panel.historyGeometry();
    const left = snapshotTransform?.left ?? plot.left;
    const right = snapshotTransform?.right ?? plot.right;
    const timeAxis = event.svg?.matches?.("[data-history-time-axis]"),
      top = timeAxis ? 0 : (snapshotTransform?.top ?? plot.top),
      bottom = timeAxis
        ? panel.historyTimeAxisHeight()
        : (snapshotTransform?.bottom ?? plot.bottom);
    if (!Number.isFinite(start) || !Number.isFinite(end) || right <= left) return null;
    if (event.clampToPlot) {
      point.x = Math.max(left, Math.min(right, point.x));
      point.y = Math.max(top, Math.min(bottom, point.y));
    }
    if (point.x < left || point.x > right || point.y < top || point.y > bottom)
      return null;
    const time = start + ((point.x - left) / (right - left)) * (end - start);
    return { point, time, geometry, start, end, left, right };
  };
  const readValues = (series, content, boundary = "value") => {
    const visible = new Set();
    for (const [key, measurement] of content) {
      const { position, quantity } = measurement,
        value = measurement[boundary],
        row = series.get(key);
      if (!row) continue;
      setText(
        row.label,
        `${quantity === "temperature" ? "Temperatur" : "Luftfeuchte"}${panel.historyDetail ? ` ${position === "upper" ? "oben" : "unten"}` : ""}: `,
      );
      setValue(row, value, quantity);
      if (value) row.value.setAttribute("title", panel.tooltipWhen(value.time));
      else row.value.removeAttribute("title");
      if (row.row.hidden) row.row.hidden = false;
      visible.add(key);
    }
    for (const [key, row] of series)
      if (!visible.has(key) && !row.row.hidden) row.row.hidden = true;
  };
  const showEvents = (nearbyEvents, timeZone) => {
    const nodes = tooltipNodes();
    const inspected = panel.historyInspectedEvent?.();
    if (inspected && !nearbyEvents.some((item) => item.event_id === inspected.event_id))
      nearbyEvents = [...nearbyEvents, inspected];
    if (!panel.historyDetail)
      nearbyEvents = panel.historyMainEvents(nearbyEvents, panel.historyValueSelection);
    const eventKey = JSON.stringify([
      timeZone,
      nearbyEvents.map((item) => [item.event_id, item.effective_at, item.kind]),
    ]);
    if (nodes.eventKey !== eventKey) {
      const eventRows = nearbyEvents.map((item) => {
        const row = document.createElement("div");
        row.className = "history-tooltip-event";
        const at = document.createElement("time");
        at.textContent = panel.clock(item.effective_at, timeZone);
        const label = document.createElement("span");
        label.textContent = panel.historyEventLabel(item);
        row.append(at, label);
        return row;
      });
      nodes.eventList.replaceChildren(...eventRows);
      nodes.eventKey = eventKey;
    }
    nodes.eventList.hidden = nearbyEvents.length === 0;
  };
  const readSessionValues = (timeZone, completed = false, content) => {
    const nodes = tooltipNodes(),
      visible = new Set();
    for (const [key, measurement] of content) {
      const { quantity, value, maximum: maximumValue } = measurement;
      if (completed && quantity !== "temperature") continue;
      const row = nodes.series.get(key),
        maximum = nodes.endSeries.get(key);
      if (!row) continue;
      visible.add(key);
      row.row.hidden = false;
      setText(row.label, quantity === "temperature" ? "Temperatur: " : "Luftfeuchte: ");
      setValue(row, value, quantity);
      if (value)
        row.value.setAttribute("title", panel.tooltipWhen(value.time, timeZone));
      else row.value.removeAttribute("title");
      maximum.value.hidden = completed || quantity !== "temperature";
      setValue(maximum, maximumValue, quantity);
      if (maximumValue)
        maximum.value.setAttribute(
          "title",
          panel.tooltipWhen(maximumValue.time, timeZone),
        );
      else maximum.value.removeAttribute("title");
    }
    for (const [key, row] of nodes.series) if (!visible.has(key)) row.row.hidden = true;
  };
  const showSnap = (targets, timeZone) => {
    const nodes = tooltipNodes(),
      unique = [
        ...new Map(targets.filter(Boolean).map((snap) => [snap.time, snap])).values(),
      ];
    nodes.snap.hidden = true;
    nodes.snap.remove?.();
    setText(
      nodes.snap,
      unique.length
        ? unique
            .map(
              (snap) =>
                `${snap.eventId ? snap.label || events[snap.kind] || snap.kind : snap.boundary === "end" ? "Sitzung beendet" : phases[snap.phase] || "Unbekannt"} · ${panel.tooltipWhen?.(snap.time, timeZone) || dateLabel(snap.time, timeZone)}`,
            )
            .join(" · ")
        : "",
    );
    tooltip.setAttribute("data-snap", String(unique.length > 0));
    if (unique.length) {
      tooltip.setAttribute("title", nodes.snap.textContent);
      tooltip.setAttribute("aria-description", nodes.snap.textContent);
    } else {
      tooltip.removeAttribute("title");
      tooltip.removeAttribute("aria-description");
    }
  };
  const hideCursor = () => {
    if (cursor && state.cursorVisible) cursor.setAttribute("visibility", "hidden");
    state.cursorVisible = false;
    state.cursorX = null;
    cursor?.setAttribute("data-snap", "false");
    panel.syncHistorySnapTargets?.();
  };
  const showCursor = (pointX, snap, model, geometry = state.geometry) => {
    tooltipNodes().heading.hidden = false;
    if (!state.tooltipVisible) tooltip.hidden = false;
    state.tooltipVisible = true;
    if (cursor && state.cursorX !== pointX) {
      cursor.setAttribute("x1", String(pointX));
      cursor.setAttribute("x2", String(pointX));
      state.cursorX = pointX;
    }
    if (cursor && !state.cursorVisible) cursor.setAttribute("visibility", "visible");
    state.cursorVisible = true;
    cursor?.setAttribute("data-snap", String(!!snap));
    panel.syncHistorySnapTargets?.();
  };
  const refreshSelection = (model = panel.historyChart?.model) => {
    const selection = panel.historyValueSelection;
    if (state.disposed || !selection) return false;
    const eventContext = !!selection.eventIds?.length,
      nodes = tooltipNodes(),
      timeZone = localTimeZone(),
      range = selection.kind === "range",
      eventLabel = eventContext
        ? panel.historyEventSpan(panel.historyInspectedEventGroup()).roleLabel
        : null,
      phase = panel.historySelectionPhase?.(selection),
      information = panel.historyInformationContent(selection, phase),
      content = information.measurements,
      label = (time) => dateLabel(time, timeZone),
      exactLabel = (time) => panel.tooltipWhen?.(time, timeZone) || label(time);
    setText(
      nodes.heading,
      eventContext
        ? eventLabel || ""
        : panel.historyInformationHeading(selection, phase, timeZone, information),
    );
    nodes.heading.hidden = eventContext && !eventLabel;
    nodes.heading.setAttribute(
      "title",
      range
        ? `${exactLabel(selection.start)} – ${exactLabel(selection.end)}`
        : exactLabel(selection.start),
    );
    layoutValues(range);
    showValueTimes(selection.start, range ? selection.end : null, timeZone);
    if (range) {
      readValues(nodes.endSeries, content, "end");
    }
    readValues(nodes.series, content);
    setText(nodes.status, `Saunastatus · ${phases[phase?.phase] || "Unbekannt"}`);
    nodes.status.hidden = eventContext || !panel.historyDetail || range;
    panel.syncHistoryInformation(selection, phase, false, information);
    tooltip.setAttribute("data-phase", phase?.phase || "");
    tooltip.setAttribute("data-selection", selection.kind);
    tooltip.setAttribute(
      "aria-label",
      eventContext
        ? range
          ? "Messwerte an den Grenzen des Ereigniszeitraums"
          : "Messwerte am Ereigniszeitpunkt"
        : range
          ? `Werte an den Grenzen des ausgewählten Zeitraums · ${exactLabel(selection.start)} bis ${exactLabel(selection.end)}`
          : `Werte am ausgewählten Zeitpunkt · ${exactLabel(selection.start)}`,
    );
    if (eventContext) nodes.eventList.hidden = true;
    else
      showEvents(
        panel.historySelectionEvents?.() ||
          panel.historyEventsAt?.(selection.start) ||
          [],
        timeZone,
      );
    const preview = eventContext
      ? null
      : panel.historyKeyboardPreview
        ? panel.historyKeyboardTarget || { time: panel.historyKeyboardTime }
        : panel.historyHoverPreview;
    showSnap(
      [selection.startSnap, selection.endSnap, preview?.snap].map(
        (snap) => panel.historyCurrentSnap?.(snap) || null,
      ),
      timeZone,
    );
    if (!state.tooltipVisible) tooltip.hidden = false;
    state.tooltipVisible = true;
    if (preview && model && preview.time >= model.start && preview.time <= model.end)
      showCursor(
        model.x(preview.time),
        panel.historyCurrentSnap?.(preview.snap),
        model,
      );
    else hideCursor();
    return true;
  };
  const refreshOverview = (model = panel.historyChart?.model) => {
    if (state.disposed || !model || !panel.shown?.session) return false;
    const information = panel.historyInformationContent(),
      { completed, start, end } = information,
      nodes = tooltipNodes(),
      timeZone = localTimeZone();
    if (!Number.isFinite(start) || !Number.isFinite(end)) return false;
    setText(
      nodes.heading,
      panel.historyInformationHeading(null, null, timeZone, information),
    );
    nodes.heading.hidden = false;
    nodes.heading.setAttribute(
      "title",
      `${panel.tooltipWhen(start, timeZone)} – ${panel.tooltipWhen(end, timeZone)}`,
    );
    layoutValues(!completed);
    nodes.startLabel.hidden = false;
    setText(nodes.startLabel, completed ? "Maximum" : "Aktuell");
    if (completed) nodes.startLabel.removeAttribute("title");
    else nodes.startLabel.setAttribute("title", panel.tooltipWhen(end, timeZone));
    setText(nodes.endLabel, "Maximum");
    nodes.endLabel.removeAttribute("title");
    readSessionValues(timeZone, completed, information.measurements);
    nodes.status.hidden = true;
    tooltip.setAttribute("data-phase", "");
    tooltip.setAttribute("data-selection", "overview");
    tooltip.setAttribute("data-session-ended", String(completed));
    tooltip.setAttribute(
      "aria-label",
      completed
        ? "Höchsttemperatur der Sitzung"
        : "Aktuelle Messwerte und Höchsttemperatur der Sitzung",
    );
    showEvents([], timeZone);
    showSnap([], timeZone);
    tooltip.hidden = false;
    state.tooltipVisible = true;
    hideCursor();
    panel.syncHistoryInformation(null, null, true, information);
    return true;
  };
  const previewTime = (time, model = panel.historyChart?.model) => {
    if (!model) return;
    if (panel.historyValueSelection) return refreshSelection(model);
    return hover(
      {
        hit: { time, geometry: state.geometry || readGeometry() },
        preview: panel.historyKeyboardTarget,
      },
      model,
    );
  };
  const paintHover = (preview, model, geometry = state.geometry) => {
    if (
      !preview ||
      !model ||
      (!preview.eventId && (preview.time < model.start || preview.time > model.end))
    )
      return hide();
    const time = preview.time,
      snap = panel.historyCurrentSnap?.(preview.snap) || null,
      nodes = tooltipNodes(),
      timeZone = localTimeZone(),
      plot = panel.historyGeometry();
    layoutValues(false);
    nodes.heading.hidden = false;
    const phase = panel.historyPhaseAt?.(time),
      selection = { kind: "point", start: time, end: time },
      information = panel.historyInformationContent(selection, phase),
      label = panel.historyInformationHeading(selection, phase, timeZone, information);
    readValues(nodes.series, information.measurements);
    setText(nodes.heading, label);
    nodes.heading.setAttribute("title", panel.tooltipWhen?.(time, timeZone) || label);
    showValueTimes(time, null, timeZone);
    setText(nodes.status, `Saunastatus · ${phases[phase?.phase] || "Unbekannt"}`);
    nodes.status.hidden = !panel.historyDetail;
    panel.syncHistoryInformation(selection, phase, false, information);
    tooltip.setAttribute("data-phase", phase?.phase || "");
    tooltip.setAttribute("data-selection", "");
    tooltip.setAttribute(
      "aria-label",
      preview.eventId
        ? `Werte am Ereigniszeitpunkt · ${label}`
        : "Werte am markierten Zeitpunkt",
    );
    showSnap([snap], timeZone);
    const eventTolerance = preview.eventId
      ? 0
      : ((model.end - model.start) / (model.right - model.left)) *
        ((panel.preference("history_hover_radius_px") * plot.width) /
          Math.max(1, geometry.rects.surface.width));
    showEvents(panel.historyEventsAt?.(time, eventTolerance) || [], timeZone);
    if (time >= model.start && time <= model.end)
      showCursor(model.x(time), snap, model, geometry);
    else hideCursor();
  };
  const refreshHover = (model = panel.historyChart?.model) => {
    if (state.disposed) return;
    if (panel.historyValueSelection) return refreshSelection(model);
    if (panel.historyHoverPreview?.eventId) panel.historyHoverPreview = null;
    return paintHover(panel.historyHoverPreview, model);
  };
  const hover = (event, snapshotTransform = panel.historyChart?.model) => {
    if (state.disposed) return;
    if (panel.historyValueSelection?.kind === "range")
      return refreshSelection(snapshotTransform);
    const hit = event?.resolved
      ? event.hit
      : event?.hit || timeAt(event, event?.model || snapshotTransform, event?.geometry);
    if (!hit) {
      panel.historyHoverPreview = null;
      if (panel.historyValueSelection) return refreshSelection(snapshotTransform);
      return hide();
    }
    const inputModel = event?.model || snapshotTransform,
      plot = panel.historyGeometry(),
      model = panel.historyChart?.model || {
        ...plot,
        ...inputModel,
        start: inputModel?.start ?? hit.start,
        end: inputModel?.end ?? hit.end,
        x: (time) =>
          (inputModel?.left ?? hit.left) +
          ((time - (inputModel?.start ?? hit.start)) /
            ((inputModel?.end ?? hit.end) - (inputModel?.start ?? hit.start))) *
            ((inputModel?.right ?? hit.right) - (inputModel?.left ?? hit.left)),
      },
      target = event?.preview ||
        panel.historySnapTarget?.(hit.time, inputModel, hit.geometry) || {
          time: hit.time,
          snap: null,
        };
    panel.historyHoverPreview = target;
    if (panel.historyValueSelection) refreshSelection(model);
    else paintHover(target, model, hit.geometry);
    if (panel.shadowRoot) panel.syncHistorySelection?.(model);
    return hit;
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
    for (const node of [surface, wrap]) if (node) state.resizeObserver.observe(node);
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
    refreshHover,
    timeAt,
    refreshSelection,
    previewTime,
    hide,
    invalidateGeometry,
    get geometry() {
      return readGeometry();
    },
    get cachedGeometry() {
      return state.geometry;
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
    );
  }
  render(reasons, session, gangs) {
    // All client geometry is read before any tooltip, layer or canvas writes.
    const geometry =
      this.panel.historySelectionDrag?.geometry || this.interaction.readGeometry();
    if (
      !this.panel.historyInputSnapshot &&
      [...reasons].some((reason) => ["initial", "archive", "viewport"].includes(reason))
    )
      this.domain = this.panel.historyDomain();
    const panel = this.panel,
      model = panel.historyModel(
        this,
        session,
        geometry.canvas.cssWidth,
        geometry.canvas.cssHeight,
      );
    this.model = model;
    panel.syncHistoryGeometry(model, geometry);
    panel.historyDetail = false;
    const chromeKey = panel.historyTitle(session);
    const chromeChanged = this.chromeKey !== chromeKey;
    this.chromeKey = chromeKey;
    this.surface.setAttribute(
      "aria-label",
      `Sitzungsverlauf: Temperatur und ${model.humidityMode === "absolute" ? "absolute" : "relative"} Luftfeuchte`,
    );
    const timeZone = localTimeZone(),
      valueScaleKey = JSON.stringify(model.valueScale);
    const axisKey = `${JSON.stringify(panel.appearanceValue())}:${model.width}:${model.height}:${model.left}:${model.right}:${model.top}:${model.bottom}:${model.start}:${model.end}:${valueScaleKey}:${model.humidityMode}:${model.humidityPrecision}:${timeZone}:${geometry.canvas.cssWidth}:${geometry.canvas.cssHeight}:${JSON.stringify(geometry.bandNames)}:${panel.historyBandDetailState?.detail}`;
    if (axisKey !== this.axisKey) {
      const axes = panel.$("[data-history-axes]"),
        humidityFocused =
          panel.shadowRoot?.activeElement?.dataset?.action === "history-humidity";
      axes.innerHTML = panel.historyAxes(model, {
        width: geometry.canvas.cssWidth,
        height: geometry.canvas.cssHeight,
        geometry,
      });
      panel.$("[data-history-time-ticks]").innerHTML = panel.historyTimeAxis(model, {
        width: geometry.canvas.cssWidth,
      });
      this.axisKey = axisKey;
      if (humidityFocused)
        axes.querySelector('[data-action="history-humidity"]')?.focus?.({
          preventScroll: true,
        });
    }
    const eventVisibilityKey = panel.historyEventVisibilityKey(),
      phaseKey = `${model.width}:${model.height}:${model.left}:${model.right}:${model.top}:${model.bottom}:${geometry.canvas.cssWidth}:${geometry.canvas.cssHeight}:${valueScaleKey}:${model.start}:${model.end}:${panel.historyTimelineRevision}:${panel.historyRecords("phase").length}:${session.ended_at || panel.state.now}:${JSON.stringify(panel.shown.phase_projection || null)}:${timeZone}:${eventVisibilityKey}`;
    if (phaseKey !== this.phaseKey) {
      panel.$("[data-history-annotations]").innerHTML = panel.historyAnnotations(
        model,
        session,
        gangs,
      );
      panel.updateMarkup(
        "[data-history-phase-band]",
        panel.historyPhaseBand(model, session, geometry.canvas.cssWidth, geometry),
      );
      panel.updateMarkup("[data-history-band-names]", panel.historyBandNames(model));
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
    panel.syncHistorySelection(model);
    panel.syncHistoryEventInspection();
    if (panel.historyValueSelection) this.interaction.refreshSelection(model);
    const markersKey = `${model.start}:${model.end}:${model.top}:${model.bottom}:${geometry.canvas.cssWidth}:${geometry.canvas.cssHeight}:${panel.preference("event_marker_size_px")}:${panel.preference("event_marker_gap_px")}:${panel.historyTimelineRevision}:${session.ended_at || panel.state.now}:${JSON.stringify(panel.historyValueSelection)}:${timeZone}:${eventVisibilityKey}`;
    if (this.markersKey !== markersKey) {
      const focused = panel.shadowRoot?.activeElement,
        selectedIds = panel.historyValueSelection?.eventIds || [],
        focusedIds = focused?.dataset?.historyEventMembers
          ? JSON.parse(decodeURIComponent(focused.dataset.historyEventMembers))
          : [],
        selectionFocused =
          selectedIds.length > 0 &&
          focusedIds.length === selectedIds.length &&
          focusedIds.every((id) => selectedIds.includes(id));
      panel.updateMarkup(
        "[data-history-events]",
        panel.historyEventMarkers(model, geometry),
      );
      panel.updateMarkup(
        "[data-history-strip-events]",
        panel.historyStripEvents(model, geometry),
      );
      this.interaction.invalidateGeometry("layout");
      this.markersKey = markersKey;
      panel.syncHistoryEventInspection();
      if (selectionFocused && !focused.isConnected)
        panel.$("[data-history-selection-context]")?.focus?.({ preventScroll: true });
    }
    panel.syncHistoryBandDetail(model, geometry);
    if (
      !panel.webkitHistoryGesture &&
      panel.chartPointers.size < 2 &&
      reasons.has("cursor") &&
      panel.pendingHover
    )
      this.interaction.hover(panel.pendingHover, model);
    else this.interaction.refreshHover(model);
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
    const gap = this.panel.preference("select_menu_gap_px"),
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
        now - (this.searchAt || 0) > this.panel.preference("typeahead_reset_ms")
          ? event.key
          : this.search + event.key;
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
    this.historyHumidityMode = "relative";
    this.historyEventVisibility = new Map();
    this.chartPointers = new Map();
    this.historyValueSelection = null;
    this.historyHoverPreview = null;
    this.historyKeyboardTime = null;
    this.historyInputSnapshot = null;
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
      this.scheduleHistoryRender("size");
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
  set panel(value) {
    this._panel = value;
    if (this.isConnected && this._hass && !this.timer && !this.busy) this.start();
  }
  get panel() {
    return this._panel;
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
    const Resize =
      this.ownerDocument?.defaultView?.ResizeObserver || globalThis.ResizeObserver;
    if (Resize && !this.diagnosticsResizeObserver) {
      this.diagnosticsResizeObserver = new Resize(() => {
        if (this.view === "diagnostics") this.scheduleHistoryRender("size");
      });
      this.diagnosticsResizeObserver.observe(this.$("#detection-plots"));
    }
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
    this.diagnosticsResizeObserver?.disconnect();
    this.diagnosticsResizeObserver = null;
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
    this.cancelHistoryInput();
    this.cancelHistoryFrame();
    this.historyChart?.destroy();
    this.historyChart = null;
    this.chartPointers.clear();
    this.webkitHistoryGesture =
      this.pendingHover =
      this.lastHistoryPointer =
      this.historyHoverPreview =
        null;
    this.historyInputMode = null;
    this.historyInputSnapshot = null;
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
      ? this.preference("status_active_poll_ms")
      : this.preference("status_idle_poll_ms");
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
    const interval = this.statusPollInterval();
    // The HA panel config supplies the factory catalog before the first API
    // response. Never turn a missing catalog into an immediate retry loop.
    if (!Number.isFinite(interval) || interval <= 0) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.refresh();
    }, interval);
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
        : node.dataset?.key
          ? `key:${node.dataset.key}`
          : node.dataset?.detailKey
            ? `detail:${node.dataset.detailKey}`
            : node.dataset?.recordId
              ? `record:${node.dataset.recordId}`
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
      if (
        !next.hasAttribute(attribute.name) &&
        !(current.nodeName === "DETAILS" && attribute.name === "open")
      )
        current.removeAttribute(attribute.name);
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
      if (current.data !== next.data) {
        const selection =
            this.shadowRoot?.getSelection?.() || this.ownerDocument?.getSelection?.(),
          anchor = selection?.anchorNode === current ? selection.anchorOffset : null,
          focus = selection?.focusNode === current ? selection.focusOffset : null,
          anchorNode = selection?.anchorNode,
          focusNode = selection?.focusNode,
          anchorOffset = selection?.anchorOffset,
          focusOffset = selection?.focusOffset;
        current.data = next.data;
        if (anchor !== null || focus !== null)
          selection?.setBaseAndExtent?.(
            anchorNode,
            anchor === null ? anchorOffset : Math.min(anchor, current.length),
            focusNode,
            focus === null ? focusOffset : Math.min(focus, current.length),
          );
      }
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
    // insertBefore can transiently detach a retained keyed subtree. Browsers
    // then clear its focus and adjust native selection even though the same
    // nodes are reinserted. Save identities, not selectors or live Ranges.
    const focused = this.shadowRoot.activeElement,
      ownedFocus = focused && target.contains(focused),
      inputSelection =
        ownedFocus && Number.isInteger(focused.selectionStart)
          ? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection]
          : null,
      selection =
        this.shadowRoot.getSelection?.() || this.ownerDocument?.getSelection?.(),
      endpoint = (node, offset) => ({
        node,
        offset,
        before: node?.childNodes?.[offset],
        after: node?.childNodes?.[offset - 1],
      }),
      selected =
        selection &&
        (target.contains(selection.anchorNode) || target.contains(selection.focusNode))
          ? {
              anchor: endpoint(selection.anchorNode, selection.anchorOffset),
              focus: endpoint(selection.focusNode, selection.focusOffset),
            }
          : null;
    const next = target.cloneNode(false);
    next.innerHTML = markup;
    this.patchNode(target, next);
    this.selectMenu?.sync();
    this.syncInfo();
    if (
      ownedFocus &&
      focused.isConnected &&
      target.contains(focused) &&
      !focused.disabled &&
      !focused.matches?.(":disabled") &&
      !focused.closest?.("[hidden], [inert]") &&
      (!focused.getClientRects || focused.getClientRects().length)
    ) {
      if (this.shadowRoot.activeElement !== focused)
        focused.focus?.({ preventScroll: true });
      if (inputSelection && Number.isInteger(focused.selectionStart))
        focused.setSelectionRange?.(...inputSelection);
    }
    if (
      selected &&
      selected.anchor.node?.isConnected &&
      selected.focus.node?.isConnected &&
      (target.contains(selected.anchor.node) || target.contains(selected.focus.node))
    ) {
      const offset = ({ node, offset: saved, before, after }) => {
        if (node.nodeType === 3) return Math.min(saved, node.length);
        const children = [...node.childNodes];
        if (before?.parentNode === node) return children.indexOf(before);
        if (after?.parentNode === node) return children.indexOf(after) + 1;
        return Math.min(saved, children.length);
      };
      selection.setBaseAndExtent?.(
        selected.anchor.node,
        offset(selected.anchor),
        selected.focus.node,
        offset(selected.focus),
      );
    }
  }
  async api(path, method = "GET", body) {
    return this.hass.callApi(method, `ha_sauna${path}`, body);
  }
  appearanceCatalog() {
    return (
      this.state?.appearance_catalog ||
      this.panel?.config?.appearance_catalog || {
        colors: [],
        scales: {},
        preferences: {},
      }
    );
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
  num(value, digits = this.preference("value_precision")) {
    return num(value, digits);
  }
  historyReadoutNumber(value) {
    const precision = this.preference("history_readout_precision");
    if (this.historyReadoutFormatter?.precision !== precision)
      this.historyReadoutFormatter = {
        precision,
        formatter: new Intl.NumberFormat("de-DE", {
          minimumFractionDigits: precision,
          maximumFractionDigits: precision,
        }),
      };
    return value == null ? "–" : this.historyReadoutFormatter.formatter.format(value);
  }
  when(value, timeZone) {
    return when(value, timeZone, this.preference("timestamp_formatter_cache_entries"));
  }
  clock(value, timeZone) {
    return clock(value, timeZone, this.preference("timestamp_formatter_cache_entries"));
  }
  tooltipWhen(value, timeZone) {
    return tooltipWhen(
      value,
      timeZone,
      this.preference("timestamp_formatter_cache_entries"),
    );
  }
  preferenceDefaults() {
    return Object.fromEntries(
      Object.entries(this.appearanceCatalog().preferences || {})
        .filter(([, definition]) => definition.configuration_surface === "panel")
        .map(([key, definition]) => [key, definition.default]),
    );
  }
  effectivePreferences(overrides = this.appearanceValue().preferences || {}) {
    return Object.fromEntries(
      Object.entries(this.appearanceCatalog().preferences || {}).map(
        ([key, definition]) => [
          key,
          definition.configuration_surface === "panel"
            ? (overrides[key] ?? definition.default)
            : definition.default,
        ],
      ),
    );
  }
  preference(key) {
    const definition = this.appearanceCatalog().preferences?.[key];
    const value =
      definition?.configuration_surface === "panel"
        ? (this.appearanceValue().preferences?.[key] ?? definition.default)
        : definition?.default;
    return Number.isFinite(value) ? value : undefined;
  }
  validPreferenceRelations(values) {
    return (
      values.history_axis_min_ticks <= values.history_axis_max_ticks &&
      values.instrument_tick_target <= values.instrument_tick_maximum &&
      values.history_plot_width_px > values.history_plot_side_padding_px * 2 &&
      values.history_plot_height_px >
        values.history_plot_top_padding_px + values.history_plot_bottom_padding_px &&
      values.detail_plot_width_px >
        values.detail_plot_side_padding_px + values.detail_plot_right_padding_px &&
      values.detail_plot_height_px >
        values.detail_plot_top_padding_px + values.detail_plot_bottom_padding_px &&
      values.detail_plot_height_px >
        Math.max(
          values.detail_axis_top_padding_px,
          values.detail_value_label_top_padding_px,
        ) +
          values.detail_axis_bottom_padding_px &&
      values.detail_plot_height_px > values.detail_time_label_bottom_padding_px &&
      values.diagnostic_min_plot_width_px >
        values.diagnostic_axis_left_padding_px +
          values.diagnostic_axis_right_padding_px &&
      values.diagnostic_plot_height_px >
        values.diagnostic_axis_top_padding_px +
          values.diagnostic_axis_bottom_padding_px &&
      values.diagnostic_plot_height_px >
        values.diagnostic_guide_top_padding_px +
          values.diagnostic_axis_bottom_padding_px &&
      values.diagnostic_plot_height_px > values.diagnostic_time_label_bottom_padding_px
    );
  }
  rangeGeometry(kind) {
    const control = kind === "control",
      thumb = this.preference(control ? "size_16_0_px" : "size_17_0_px"),
      track = this.preference(control ? "size_4_0_px" : "size_5_0_px"),
      border = control ? 0 : this.preference("border_2_0_px");
    return {
      thumb,
      track,
      offset: (track - thumb) / 2,
      inset: thumb / 2,
      content: Math.max(0, thumb - border * 2),
    };
  }
  historyGeometry() {
    const width = this.preference("history_plot_width_px"),
      height = this.preference("history_plot_height_px");
    return {
      width,
      height,
      left: this.preference("history_plot_side_padding_px"),
      right: width - this.preference("history_plot_side_padding_px"),
      top: this.preference("history_plot_top_padding_px"),
      bottom: height - this.preference("history_plot_bottom_padding_px"),
    };
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
      (this.appearanceCatalog().scales?.[quantity]?.minimum == null ||
        minimum >= this.appearanceCatalog().scales[quantity].minimum) &&
      (this.appearanceCatalog().scales?.[quantity]?.maximum == null ||
        maximum <= this.appearanceCatalog().scales[quantity].maximum);
    return valid ? { minimum, maximum } : defaultScale ? { ...defaultScale } : null;
  }
  historyCurveStyles() {
    const temperature = this.appearanceColor("series_temperature"),
      humidity = this.appearanceColor("series_humidity");
    return {
      "upper:temperature": {
        stroke: temperature,
        lineWidth: this.preference("chart_curve_width_px"),
        lineDash: [],
      },
      "lower:temperature": {
        stroke: temperature,
        lineWidth: this.preference("chart_curve_width_px"),
        lineDash: [],
      },
      "upper:humidity": {
        stroke: humidity,
        lineWidth: this.preference("chart_curve_width_px"),
        lineDash: [],
      },
      "lower:humidity": {
        stroke: humidity,
        lineWidth: this.preference("chart_curve_width_px"),
        lineDash: [],
      },
    };
  }
  applyAppearance() {
    const stylesheet = this.shadowRoot?.querySelector?.("#panel-appearance-style"),
      stylesheetKey = JSON.stringify(this.effectivePreferences());
    if (stylesheet && this.appearanceStylesKey !== stylesheetKey) {
      stylesheet.textContent = this.appearanceStyles();
      this.appearanceStylesKey = stylesheetKey;
    }
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
      const value = this.preference(key);
      if (Number.isFinite(value) && value >= 0)
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
      darkSurface
        ? this.preference("raised_surface_lightening_dark")
        : this.preference("raised_surface_lightening_light"),
    );
    const sectionSurface = appearanceBlend(
      surfaces.card,
      "#FFFFFF",
      darkSurface
        ? this.preference("section_surface_lightening_dark")
        : this.preference("section_surface_lightening_light"),
    );
    const recessedSurface = appearanceBlend(
      surfaces.page,
      surfaces.card,
      this.preference("recessed_surface_blend"),
    );
    const controlSurface = appearanceBlend(
      surfaces.card,
      "#FFFFFF",
      darkSurface
        ? this.preference("control_surface_lightening_dark")
        : this.preference("control_surface_lightening_light"),
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
      ["info", "status_info", this.preference("notice_tint")],
      ["warning", "status_warning", this.preference("notice_tint")],
      ["error", "status_error", this.preference("notice_tint")],
      ["unknown", "status_unknown", this.preference("notice_tint")],
      ["heater-on", "ui_heater_on", this.preference("heater_tint")],
      ["heater-off", "ui_heater_off", this.preference("heater_tint")],
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
    const mainSurface =
      tint && appearanceBlend(surfaces.card, tint, this.preference("main_phase_tint"));
    if (mainSurface) style.setProperty("--sauna-main-background", mainSurface);
    else style.removeProperty?.("--sauna-main-background");
    const mainSection =
      tint &&
      appearanceBlend(sectionSurface, tint, this.preference("section_phase_tint"));
    if (mainSection) style.setProperty("--sauna-main-section", mainSection);
    else style.removeProperty?.("--sauna-main-section");
    const pendingSurface = tintedSurface(
      mainSurface || surfaces.card,
      "status_info",
      this.preference("pending_command_tint"),
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
    const preferenceGroups = [
      ...new Set(
        Object.values(catalog.preferences || {})
          .filter((definition) => definition.configuration_surface === "panel")
          .map((definition) => definition.group),
      ),
    ];
    const preferences = preferenceGroups
      .map(
        (group) =>
          `<details class="settings-group appearance-preferences"><summary>${esc(group)}</summary><div class="appearance-preference-fields">${Object.entries(
            catalog.preferences,
          )
            .filter(
              ([, definition]) =>
                definition.configuration_surface === "panel" &&
                definition.group === group,
            )
            .map(
              ([key, definition]) =>
                `<label>${esc(definition.label)}${unitInput(`<input type="number" data-appearance-preference="${esc(key)}" aria-label="${esc(definition.label)}" min="${definition.minimum}" max="${definition.maximum}" step="${definition.step}" value="${esc(this.appearanceRaw?.[`preference.${key}`] ?? this.preference(key))}">`, definition.unit)}<small>${esc(definition.description)}</small></label>`,
            )
            .join("")}</div></details>`,
      )
      .join("");
    return `<div class="card appearance-settings" id="appearance-settings"><h2>Darstellung und Bedienung</h2><div class="appearance-instruments">${instrumentFields}</div><h3>Messgrößen</h3><div class="appearance-colors">${rows(true)}</div><h3>Anzeigeskalen</h3><div class="appearance-scales">${scaleFields}</div><details class="settings-group appearance-advanced"><summary>Erweiterte Farben</summary>${groups.map((group) => `<details class="expert-group"><summary>${esc(group)}</summary><div class="appearance-colors">${rows(false, group)}</div></details>`).join("")}</details>${preferences}<div class="row"><button type="button" class="confirm" data-action="appearance-save">Darstellung speichern</button><button type="button" data-action="appearance-discard">Änderungen verwerfen</button><button type="button" data-action="appearance-default">Standarddarstellung wiederherstellen</button></div><p id="appearance-status" role="status" class="muted"></p></div>`;
  }
  updateAppearanceField(input) {
    if (!this.state?.permissions?.admin || this.appearanceRequest) return;
    if (!this.appearanceDraft) {
      const saved = this.savedAppearance();
      this.appearanceDraft = {
        colors: { ...(saved.colors || {}) },
        preferences: Object.fromEntries(
          Object.keys(this.preferenceDefaults()).map((key) => [
            key,
            saved.preferences?.[key] ?? this.preferenceDefaults()[key],
          ]),
        ),
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
    } else if (input.dataset.appearancePreference) {
      const key = input.dataset.appearancePreference,
        value = Number(input.value),
        definition = this.appearanceCatalog().preferences?.[key];
      this.appearanceRaw[`preference.${key}`] = input.value;
      if (
        definition?.configuration_surface === "panel" &&
        input.value.trim() &&
        Number.isFinite(value) &&
        value >= definition.minimum &&
        value <= definition.maximum &&
        (!definition.integer || Number.isInteger(value)) &&
        this.validPreferenceRelations(
          this.effectivePreferences({
            ...this.appearanceDraft.preferences,
            [key]: value,
          }),
        )
      )
        this.appearanceDraft.preferences[key] = value;
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
    if (
      (role || input.dataset.appearancePreference || input.dataset.appearanceScale) &&
      this.shown
    )
      this.scheduleHistoryRender("appearance");
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
    for (const key of Object.keys(this.appearanceCatalog().preferences || {})) {
      const input = this.$(`[data-appearance-preference="${key}"]`);
      if (input && input !== this.shadowRoot.activeElement)
        input.value = this.preference(key);
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
    if (!this.appearanceDraft) return null;
    const payload = {
      ...this.appearanceDraft,
      preferences: Object.fromEntries(
        Object.keys(this.preferenceDefaults()).map((key) => [
          key,
          this.appearanceDraft.preferences?.[key] ?? this.preferenceDefaults()[key],
        ]),
      ),
    };
    for (const [name, spec] of Object.entries(this.appearanceCatalog().scales || {})) {
      const bounds = payload.scales[name];
      if (
        !Number.isFinite(bounds?.minimum) ||
        !Number.isFinite(bounds?.maximum) ||
        bounds.minimum >= bounds.maximum ||
        !Number.isFinite(bounds.maximum - bounds.minimum) ||
        (spec.minimum != null && bounds.minimum < spec.minimum) ||
        (spec.maximum != null && bounds.maximum > spec.maximum)
      )
        throw Error(
          `${name === "temperature" ? "Temperatur" : "Luftfeuchte"}: Minimum muss kleiner als Maximum sein`,
        );
    }
    for (const [key, definition] of Object.entries(
      this.appearanceCatalog().preferences || {},
    ).filter(([, definition]) => definition.configuration_surface === "panel")) {
      const raw = this.appearanceRaw?.[`preference.${key}`],
        value = raw === undefined ? payload.preferences?.[key] : Number(raw);
      if (
        raw?.trim() === "" ||
        !Number.isFinite(value) ||
        value < definition.minimum ||
        value > definition.maximum ||
        (definition.integer && !Number.isInteger(value))
      )
        throw Error(
          `${definition.label}: Wert innerhalb der zulässigen Grenzen eingeben`,
        );
      payload.preferences[key] = value;
    }
    if (!this.validPreferenceRelations(this.effectivePreferences(payload.preferences)))
      throw Error(
        "Achsenabstände müssen Platz für das Diagramm lassen; Mindestteilungen dürfen Höchstteilungen nicht überschreiten",
      );
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
          preferences: this.preferenceDefaults(),
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
  appearanceStyles() {
    return `
      :host {
        display: block;
        height: calc(100dvh - var(--safe-area-inset-top, 0px) - var(--safe-area-inset-bottom, 0px));
        min-height: 0;
        container-type: inline-size;
        overflow: hidden;
        color: var(--sauna-page-text, var(--sauna-color-text, var(--primary-text-color)));
        background: var(--sauna-color-page-background, var(--primary-background-color));
        font:
          ${this.preference("font_size_body_emphasis_px")}px/${this.preference("body_line_height")} var(--primary-font-family, sans-serif);
        --accent: var(--sauna-color-ui-accent);
        --accent-ink: var(--sauna-accent-ink);
        --confirm: var(--sauna-color-ui-command, var(--sauna-color-card-background, var(--card-background-color)));
        --danger: var(--sauna-color-ui-danger);
        --danger-text: var(--sauna-danger-text, var(--danger));
        --warm: var(--sauna-color-phase-warmup);
        --sauna-focus-current: var(--sauna-page-focus, var(--accent));
        --sauna-shadow-card: 0 ${this.preference("shadow_3_0_px")}px ${this.preference("shadow_5_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_32_ratio")}), 0 ${this.preference("shadow_14_0_px")}px ${this.preference("shadow_30_0_px")}px -${this.preference("shadow_8_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_48_ratio")});
        --sauna-shadow-section: 0 ${this.preference("shadow_2_0_px")}px ${this.preference("shadow_3_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_26_ratio")}), 0 ${this.preference("shadow_7_0_px")}px ${this.preference("shadow_14_0_px")}px -${this.preference("shadow_5_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_36_ratio")});
        --sauna-shadow-control: 0 ${this.preference("shadow_2_0_px")}px ${this.preference("shadow_2_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_35_ratio")}), 0 ${this.preference("shadow_4_0_px")}px ${this.preference("shadow_7_0_px")}px -${this.preference("shadow_2_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_3_ratio")});
      }
      * {
        box-sizing: border-box;
      }
      :host { cursor: default; }
      input:not([type="range"]):not([type="checkbox"]):not([type="radio"]), textarea { cursor: text; }
      main {
        max-width: ${this.preference("size_1280_0_px")}px;
        height: 100%;
        min-height: 0;
        display: flex;
        flex-direction: column;
        margin: auto;
        padding: ${this.preference("layout_space_33_px")}px ${this.preference("layout_space_36_px")}px ${this.preference("layout_space_22_px")}px;
      }
      main > header, main > .detail-tabs, main > #message { flex: 0 0 auto; }
      main > section { flex: 1 1 0; min-height: 0; min-width: 0; overflow: auto; overscroll-behavior: contain; scrollbar-gutter: stable; padding: ${this.preference("layout_space_09_px")}px; }
      header {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
        align-items: center;
        gap: ${this.preference("layout_space_24_px")}px;
        margin-bottom: ${this.preference("surface_gap_px")}px;
        color: var(--sauna-page-text, var(--sauna-color-text, var(--primary-text-color)));
      }
      .header-brand {
        display: flex;
        align-items: center;
        gap: ${this.preference("layout_space_20_px")}px;
      }
      .header-context {
        display: flex;
        align-items: center;
        gap: ${this.preference("layout_space_16_px")}px;
        justify-self: end;
        min-width: 0;
      }
      .header-icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: ${this.preference("layout_space_16_px")}px;
        flex: 0 0 auto;
      }
      .header-icon svg { width: ${this.preference("size_20_0_px")}px; height: ${this.preference("size_20_0_px")}px; fill: none; stroke: currentColor; stroke-width: ${this.preference("stroke_1_7_ratio")}; stroke-linecap: round; stroke-linejoin: round; }
      .header-context :is(select, .sauna-select-trigger) {
        max-width: 100%;
      }
      h1 {
        font-size: ${this.preference("font_size_phase_px")}px;
        letter-spacing: -${this.preference("tracking_0_6_px")}px;
        margin: 0;
      }
      h2 {
        font-size: ${this.preference("font_size_heading_emphasis_px")}px;
        margin: 0 0 ${this.preference("layout_space_18_px")}px;
      }
      h3 {
        font-size: ${this.preference("font_size_body_px")}px;
        margin: ${this.preference("layout_space_18_px")}px 0 ${this.preference("layout_space_13_px")}px;
      }
      p {
        margin: ${this.preference("layout_space_13_px")}px 0;
      }
      small,
      .muted {
        color: var(--sauna-page-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)));
        font-size: ${this.preference("font_size_compact_px")}px;
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
        border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
        border-radius: var(--sauna-control-radius);
        padding: ${this.preference("layout_space_15_px")}px ${this.preference("layout_space_19_px")}px;
        background: var(--sauna-color-card-background, var(--card-background-color));
        color: var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)));
      }
      .number-input { display: flex; align-items: center; min-width: 0; padding: 0; }
      .number-input input { flex: 1 1 0; min-width: 0; width: 100%; border: 0; background: transparent; }
      .input-unit { flex: 0 0 auto; padding-inline-end: ${this.preference("layout_space_19_px")}px; color: var(--sauna-card-muted-text, var(--secondary-text-color)); white-space: nowrap; }
      .number-input:has(input:focus-visible) { outline: ${this.preference("border_2_0_px")}px solid var(--sauna-focus-current); outline-offset: ${this.preference("layout_space_08_px")}px; }
      .number-input input:focus-visible { outline: none; }
      #session, #session + .sauna-select-trigger {
        min-width: 0;
        max-width: 100%;
      }
      select, .sauna-select-trigger {
        appearance: none;
        padding-inline-end: ${this.preference("layout_space_37_px")}px;
        background-image: linear-gradient(45deg, transparent 50%, currentColor 50%), linear-gradient(135deg, currentColor 50%, transparent 50%);
        background-position: right ${this.preference("size_21_0_px")}px center, right ${this.preference("size_16_0_px")}px center;
        background-size: ${this.preference("size_5_0_px")}px ${this.preference("size_5_0_px")}px;
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
        padding: ${this.preference("layout_space_09_px")}px;
        z-index: 100;
        overflow: auto;
        overscroll-behavior: contain;
        box-sizing: border-box;
        border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
        border-radius: var(--sauna-control-radius);
        background: var(--sauna-surface-raised, var(--sauna-color-card-background));
        color: var(--sauna-card-text, var(--primary-text-color));
        box-shadow: var(--sauna-shadow-card);
        font: inherit;
      }
      .sauna-select-menu [role="option"] {
        padding: ${this.preference("layout_space_15_px")}px ${this.preference("layout_space_19_px")}px;
        border-radius: var(--sauna-control-radius);
        min-height: var(--sauna-button-height);
        overflow-wrap: anywhere;
        cursor: pointer;
      }
      .sauna-select-menu [aria-selected="true"] {
        font-weight: ${this.preference("font_weight_600_0_ratio")};
        background: var(--sauna-surface-control, var(--sauna-color-card-background));
      }
      .sauna-select-menu [data-active="true"],
      .sauna-select-menu [role="option"]:hover:not([aria-disabled="true"]) {
        outline: ${this.preference("border_2_0_px")}px solid var(--sauna-card-focus, var(--sauna-color-focus));
        outline-offset: -${this.preference("layout_space_07_px")}px;
      }
      .sauna-select-menu [aria-disabled="true"] { opacity: ${this.preference("opacity_0_5_ratio")}; cursor: default; }
      .sauna-select-group { padding: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_19_px")}px; color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_compact_px")}px; }
      button {
        cursor: pointer;
        min-height: var(--sauna-button-height);
        font-weight: ${this.preference("font_weight_600_0_ratio")};
        line-height: ${this.preference("line_height_1_3_ratio")};
        background: var(--sauna-surface-control, var(--sauna-color-card-background));
        border-color: color-mix(in srgb, var(--sauna-surface-control) ${this.preference("color_mix_72_0_percent")}%, white);
        box-shadow: var(--sauna-shadow-control);
        transition: background-color ${this.preference("transition_140_0_ms")}ms ease, border-color ${this.preference("transition_140_0_ms")}ms ease, box-shadow ${this.preference("transition_140_0_ms")}ms ease;
        --sauna-button-hover: var(--sauna-card-focus, var(--sauna-focus-current));
      }
      button:hover:not(:disabled):not(:focus-visible) {
        text-decoration: none;
        border-color: var(--sauna-button-hover);
      }
      button:focus-visible,
      select:focus-visible,
      input:focus-visible {
        outline: ${this.preference("border_2_0_px")}px solid var(--sauna-focus-current);
        outline-offset: ${this.preference("layout_space_08_px")}px;
      }
      button:disabled {
        opacity: ${this.preference("opacity_0_5_ratio")};
        cursor: default;
        box-shadow: none;
      }
      button:active:not(:disabled) { box-shadow: 0 ${this.preference("shadow_1_0_px")}px ${this.preference("shadow_2_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_25_ratio")}); }
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
        font-weight: ${this.preference("font_weight_700_0_ratio")};
        --sauna-button-hover: var(--sauna-accent-focus, var(--sauna-card-focus));
      }
      a {
        color: var(--sauna-card-text, inherit);
      }
      .tabs {
        display: flex;
        flex-wrap: wrap;
        gap: var(--sauna-control-gap);
        margin: ${this.preference("layout_space_13_px")}px 0 ${this.preference("layout_space_18_px")}px;
      }
      .card {
        background: var(--sauna-surface-raised, var(--sauna-color-card-background, var(--card-background-color)));
        border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
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
        border-left: ${this.preference("border_4_0_px")}px solid var(--accent);
      }
      .control-main {
        background: var(--sauna-main-background, var(--sauna-surface-raised, var(--sauna-color-card-background, var(--card-background-color))));
        color: var(--sauna-main-text, var(--sauna-card-text, inherit));
        --sauna-focus-current: var(--sauna-main-focus, var(--sauna-card-focus, var(--accent)));
        transition: background-color ${this.preference("transition_350_0_ms")}ms ease;
      }
      .control-main small,
      .control-main .muted { color: var(--sauna-main-muted-text, var(--sauna-card-muted-text, inherit)); }
      .control-main button small { color: inherit; }
      .oven-feedback { margin: ${this.preference("layout_space_09_px")}px 0 ${this.preference("layout_space_16_px")}px; }
      .program-current { display: flex; align-items: center; gap: ${this.preference("layout_space_22_px")}px; }
      .program-current-value { flex: 1; min-width: 0; display: grid; gap: ${this.preference("layout_space_09_px")}px; }
      .program-active-label { overflow-wrap: anywhere; font-size: ${this.preference("font_size_subtitle_px")}px; font-weight: ${this.preference("font_weight_600_0_ratio")}; }
      [data-action="program-toggle"] { flex: 0 0 auto; }
      #program-choice-body { margin-top: ${this.preference("layout_space_22_px")}px; }
      .program-disclosure { margin-top: ${this.preference("layout_space_18_px")}px; }
      .scale-hint { fill: var(--sauna-ink-status-warning, var(--sauna-card-text, inherit)); color: var(--sauna-ink-status-warning, var(--sauna-card-text, inherit)); font-size: ${this.preference("font_size_caption_px")}px; margin: ${this.preference("layout_space_07_px")}px 0 0; }
      .control-main .scale-hint { fill: var(--sauna-main-ink-status-warning, var(--sauna-ink-status-warning, inherit)); color: var(--sauna-main-ink-status-warning, var(--sauna-ink-status-warning, inherit)); }
      .appearance-colors { display: grid; gap: ${this.preference("layout_space_15_px")}px; }
      .appearance-color-row { display: grid; grid-template-columns: minmax(${this.preference("size_130_0_px")}px, 1fr) ${this.preference("size_48_0_px")}px minmax(${this.preference("size_96_0_px")}px, ${this.preference("size_118_0_px")}px) auto; gap: ${this.preference("layout_space_13_px")}px; align-items: center; }
      .appearance-color-row input[type="color"] { padding: ${this.preference("layout_space_07_px")}px; width: ${this.preference("size_48_0_px")}px; height: ${this.preference("size_40_0_px")}px; }
      .appearance-color-row input:not([type="color"]) { min-width: 0; width: 100%; }
      .appearance-color-row button { padding: ${this.preference("layout_space_12_px")}px ${this.preference("layout_space_15_px")}px; }
      .appearance-scales { display: flex; flex-wrap: wrap; gap: ${this.preference("layout_space_22_px")}px; }
      .appearance-scale { display: flex; gap: ${this.preference("layout_space_15_px")}px; align-items: end; border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color)); padding-top: ${this.preference("layout_space_18_px")}px; }
      .appearance-scale legend { font-weight: ${this.preference("font_weight_600_0_ratio")}; }
      .appearance-preference-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, ${this.preference("size_18_0_em")}em), 1fr)); gap: var(--sauna-control-gap); }
      .appearance-preference-fields label { display: grid; gap: var(--sauna-control-gap); }
      .appearance-preference-fields small { color: var(--sauna-card-muted-text); }
      .appearance-scale label { display: grid; gap: ${this.preference("layout_space_09_px")}px; font-size: ${this.preference("font_size_compact_px")}px; }
      .appearance-scale .number-input { width: ${this.preference("size_110_0_px")}px; }
      #appearance-status[data-kind="success"] { color: var(--sauna-ink-status-success, var(--sauna-color-status-success)); }
      #appearance-status[data-kind="error"] { color: var(--sauna-ink-status-error, var(--sauna-color-status-error)); }
      #appearance-status[data-kind="pending"] { border-left: ${this.preference("border_3_0_px")}px solid var(--sauna-color-status-info); padding-left: ${this.preference("layout_space_13_px")}px; }
      @media (max-width: ${this.preference("breakpoint_540_0_px")}px) {
        .appearance-color-row { grid-template-columns: ${this.preference("size_48_0_px")}px minmax(0, 1fr) auto; }
        .appearance-color-row label { grid-column: 1 / -1; }
        .appearance-color-row input[type="color"] { grid-column: 1; }
        .appearance-color-row input:not([type="color"]) { grid-column: 2; }
        .appearance-color-row button { grid-column: 3; }
        .appearance-scale { flex-wrap: wrap; }
      }
      .phase {
        font-size: ${this.preference("font_size_phase_px")}px;
        font-weight: ${this.preference("font_weight_650_0_ratio")};
        letter-spacing: -${this.preference("tracking_0_5_px")}px;
      }
      .badge {
        display: inline-block;
        border-radius: ${this.preference("radius_20_0_px")}px;
        padding: ${this.preference("layout_space_08_px")}px ${this.preference("layout_space_17_px")}px;
        background: var(--sauna-tint-info-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-info-text, var(--sauna-card-text, inherit));
        font-size: ${this.preference("font_size_compact_px")}px;
      }
      .notice {
        background: var(--sauna-tint-warning-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-warning-text, var(--sauna-card-text, inherit));
        --sauna-focus-current: var(--sauna-tint-warning-focus, var(--sauna-card-focus, var(--accent)));
        border-left: ${this.preference("border_4_0_px")}px solid var(--sauna-color-status-warning);
        padding: ${this.preference("layout_space_18_px")}px ${this.preference("layout_space_22_px")}px;
        border-radius: ${this.preference("radius_8_0_px")}px;
        margin: ${this.preference("layout_space_16_px")}px 0;
      }
      .error {
        background: var(--sauna-tint-error-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-error-text, var(--sauna-card-text, inherit));
        --sauna-focus-current: var(--sauna-tint-error-focus, var(--sauna-card-focus, var(--accent)));
      }
      .legend {
        display: flex;
        gap: ${this.preference("layout_space_24_px")}px;
        flex-wrap: wrap;
        font-size: ${this.preference("font_size_compact_px")}px;
      }
      .history-legend { display: flex; flex-wrap: wrap; align-items: baseline; gap: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_18_px")}px; }
      .history-legend > strong { font-size: ${this.preference("font_size_small_px")}px; font-weight: ${this.preference("font_weight_500_0_ratio")}; color: var(--sauna-card-muted-text); }
      .history-legend .legend { display: flex; flex-wrap: wrap; justify-content: flex-start; gap: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_18_px")}px; margin: 0; }
      .history-legend .legend > span { display: flex; align-items: center; gap: ${this.preference("control_group_gap_px")}px; min-width: 0; }
      .history-legend-groups { padding: ${this.preference("layout_space_09_px")}px ${this.preference("layout_space_20_px")}px; font-size: ${this.preference("font_size_small_px")}px; }
      .history-legend .legend-swatch { width: ${this.preference("size_22_0_px")}px; height: ${this.preference("size_22_0_px")}px; display: block; flex: 0 0 auto; background: transparent; }
      .legend-swatch .temperature { stroke: var(--sauna-color-series-temperature); stroke-width: ${this.preference("chart_curve_width_px")}; }
      .legend-swatch .humidity { stroke: var(--sauna-color-series-humidity); stroke-width: ${this.preference("chart_curve_width_px")}; }
      .legend-swatch .history-event-marker { cursor: default; }
      .dot {
        display: inline-block;
        width: ${this.preference("size_10_0_px")}px;
        height: ${this.preference("size_10_0_px")}px;
        border-radius: 50%;
        margin-right: ${this.preference("control_group_gap_px")}px;
      }
      .chart {
        width: 100%;
        height: auto;
        display: block;
        touch-action: pan-y;
      }
      .chart text {
        fill: var(--sauna-chart-ink, var(--sauna-color-chart-text));
        font: ${this.preference("font_size_small_px")}px var(--primary-font-family, sans-serif);
      }
      .chart .gridline {
        stroke: var(--sauna-color-border, var(--divider-color));
        stroke-width: ${this.preference("stroke_1_0_ratio")};
      }
      .chart rect { stroke-width: ${this.preference("stroke_1_0_ratio")}; }
      .chart .gang {
        fill: color-mix(in srgb, var(--sauna-color-phase-session) ${this.preference("color_mix_20_0_percent")}%, transparent);
        stroke: var(--sauna-color-phase-session);
      }
      .chart .provisional {
        fill: color-mix(in srgb, var(--sauna-color-status-warning) ${this.preference("color_mix_13_0_percent")}%, transparent);
        stroke: var(--sauna-color-status-warning);
      }
      .chart .heat {
        stroke: var(--sauna-color-phase-warmup);
        fill: color-mix(in srgb, var(--sauna-color-phase-warmup) ${this.preference("color_mix_20_0_percent")}%, transparent);
      }
      .chart .event {
        stroke: var(--sauna-color-chart-event);
        stroke-dasharray: ${this.preference("event_line_dash_length_px")} ${this.preference("event_line_dash_gap_px")};
      }
      .chart .infusion {
        stroke: var(--sauna-color-event-infusion);
      }
      .chart path {
        fill: none;
        stroke-width: ${this.preference("stroke_2_0_ratio")};
      }
      .scroll {
        overflow: auto;
      }
      table {
        border-collapse: collapse;
        width: 100%;
        font-size: ${this.preference("font_size_compact_px")}px;
      }
      td,
      th {
        text-align: left;
        padding: ${this.preference("layout_space_16_px")}px ${this.preference("layout_space_18_px")}px;
        border-bottom: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
        white-space: nowrap;
      }
      th {
        font-weight: ${this.preference("font_weight_600_0_ratio")};
      }
      .toolbar {
        margin: ${this.preference("layout_space_24_px")}px 0 ${this.preference("layout_space_13_px")}px;
      }
      input[type="range"] {
        padding: 0;
        max-width: ${this.preference("size_180_0_px")}px;
      }
      .forms {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: ${this.preference("layout_space_22_px")}px;
      }
      .field {
        display: flex;
        flex-direction: column;
        gap: ${this.preference("control_group_gap_px")}px;
        font-size: ${this.preference("font_size_compact_px")}px;
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
        margin: ${this.preference("layout_space_24_px")}px 0;
      }
      summary {
        cursor: pointer;
        font-weight: ${this.preference("font_weight_600_0_ratio")};
        margin-bottom: ${this.preference("layout_space_20_px")}px;
      }
      [hidden] {
        display: none !important;
      }
      #message:empty {
        display: none;
      }
      #settings .row {
        margin: ${this.preference("layout_space_24_px")}px 0;
      }
      .empty {
        padding: ${this.preference("layout_space_38_px")}px;
        text-align: center;
        color: var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)));
      }
      @media (max-width: ${this.preference("breakpoint_800_0_px")}px) {
        main {
          padding: ${this.preference("layout_space_24_px")}px ${this.preference("layout_space_22_px")}px ${this.preference("layout_space_22_px")}px;
        }
        .forms {
          grid-template-columns: repeat(2, 1fr);
        }
        header {
          gap: ${this.preference("layout_space_16_px")}px;
        }
        h1 {
          font-size: ${this.preference("font_size_display_small_px")}px;
        }
        .card {
          padding: ${this.preference("layout_space_22_px")}px;
        }
        header :is(select, .sauna-select-trigger) {
          max-width: ${this.preference("size_180_0_px")}px;
        }
      }
      @media (max-width: ${this.preference("breakpoint_450_0_px")}px) {
        .forms {
          grid-template-columns: 1fr;
        }
        .phase {
          font-size: ${this.preference("font_size_display_px")}px;
        }
        .legend {
          gap: ${this.preference("layout_space_18_px")}px;
        }
      }
      :host {
        --accent: var(--sauna-color-ui-accent);
      }
      main {
        max-width: none;
      }
      header {
        margin-bottom: ${this.preference("layout_space_18_px")}px;
      }
      .plot-panel {
        padding: var(--sauna-surface-padding);
        background: var(--sauna-surface-raised, var(--sauna-color-card-background));
        color: var(--sauna-card-text);
        --sauna-focus-current: var(--sauna-card-focus);
        border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border);
        border-radius: var(--sauna-surface-radius);
        box-shadow: var(--sauna-shadow-card);
      }
      .plot-title {
        text-align: left;
        color: var(--sauna-card-text);
        font-weight: ${this.preference("font_weight_600_0_ratio")};
        font-size: ${this.preference("font_size_heading_px")}px;
      }
      .legend {
        justify-content: center;
        margin: ${this.preference("layout_space_24_px")}px 0;
      }
      .legend i {
        display: inline-block;
        width: ${this.preference("size_32_0_px")}px;
        height: ${this.preference("size_5_0_px")}px;
        margin-right: ${this.preference("control_group_gap_px")}px;
        vertical-align: middle;
      }
      .top-legend {
        margin: ${this.preference("surface_gap_px")}px 0 0;
      }
      .position-select {
        justify-content: center;
        font-size: ${this.preference("font_size_small_px")}px;
        gap: ${this.preference("layout_space_13_px")}px;
      }
      .position-select button {
        color: var(--sauna-card-text);
        padding: ${this.preference("layout_space_13_px")}px ${this.preference("layout_space_18_px")}px;
      }
      .position-select button[aria-pressed="true"] {
        font-weight: ${this.preference("font_weight_700_0_ratio")};
        text-decoration: underline;
        text-underline-offset: ${this.preference("layout_space_09_px")}px;
      }
      .plot-wrap {
        position: relative;
      }
      .chart {
        height: clamp(${this.preference("size_280_0_px")}px, ${this.preference("history_plot_viewport_height_percent")}dvh, ${this.preference("size_400_0_px")}px);
        width: 100%;
        touch-action: pan-y;
        user-select: none;
        cursor: default;
      }
      .chart text {
        fill: var(--sauna-chart-ink, var(--sauna-color-chart-text));
      }
      .detector-chart {
        height: ${this.preference("size_220_0_px")}px;
      }
      .main-tabs {
        justify-content: center;
        justify-self: center;
        width: max-content;
        margin: 0;
        padding: ${this.preference("layout_space_09_px")}px;
        border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
        border-radius: var(--sauna-control-radius);
        background: var(--sauna-surface-recessed);
        max-width: 100%;
        overflow-x: auto;
        flex-wrap: nowrap;
        scrollbar-width: thin;
      }
      .main-tabs button {
        padding: ${this.preference("layout_space_13_px")}px ${this.preference("layout_space_18_px")}px;
        font-size: ${this.preference("font_size_compact_px")}px;
        border-color: transparent;
        flex: 0 0 auto;
      }
      .detail-tabs {
        font-size: ${this.preference("font_size_compact_px")}px;
      }
      .chart .gridline {
        stroke: color-mix(in srgb, var(--sauna-color-chart-grid) ${this.preference("color_mix_10_0_percent")}%, transparent);
      }
      .chart .gang {
        fill: color-mix(in srgb, var(--sauna-color-phase-session) ${this.preference("color_mix_22_0_percent")}%, transparent);
        stroke: var(--sauna-color-phase-session);
      }
      .chart .provisional {
        stroke: var(--sauna-color-phase-session);
      }
      .chart .heat {
        stroke: var(--sauna-color-phase-warmup);
        fill: color-mix(in srgb, var(--sauna-color-phase-warmup) ${this.preference("color_mix_16_0_percent")}%, transparent);
      }
      .chart .ready {
        stroke: var(--sauna-color-phase-ready);
        fill: color-mix(in srgb, var(--sauna-color-phase-ready) ${this.preference("color_mix_16_0_percent")}%, transparent);
      }
      .chart .vent {
        stroke: var(--sauna-color-activity-ventilation);
        stroke-width: ${this.preference("stroke_2_0_ratio")};
        stroke-dasharray: ${this.preference("ventilation_dash_length_px")} ${this.preference("ventilation_dash_gap_px")};
      }
      .chart .cool {
        stroke: var(--sauna-color-phase-forced-cooling);
        fill: color-mix(in srgb, var(--sauna-color-phase-forced-cooling) ${this.preference("color_mix_16_0_percent")}%, transparent);
      }
      .chart .after {
        stroke: var(--sauna-color-phase-cooling);
        fill: color-mix(in srgb, var(--sauna-color-phase-cooling) ${this.preference("color_mix_16_0_percent")}%, transparent);
      }
      .chart .phase-other {
        stroke: var(--sauna-color-phase-idle);
        fill: color-mix(in srgb, var(--sauna-color-phase-idle) ${this.preference("color_mix_22_0_percent")}%, transparent);
      }
      .chart .door {
        stroke: var(--sauna-color-event-door);
        stroke-width: ${this.preference("stroke_1_0_ratio")};
        fill: color-mix(in srgb, var(--sauna-color-event-door) ${this.preference("color_mix_20_0_percent")}%, transparent);
      }
      .chart .infusion {
        stroke: var(--sauna-color-event-infusion);
        stroke-width: ${this.preference("stroke_2_0_ratio")};
      }
      .chart path {
        stroke-width: ${this.preference("chart_curve_width_px")};
      }
      .chart path.temperature {
        stroke: var(--sauna-color-series-temperature);
      }
      .chart path.humidity {
        stroke: var(--sauna-color-series-humidity);
      }
      .chart path.lower {
        stroke-dasharray: ${this.preference("curve_dash_length_px")} ${this.preference("curve_dash_gap_px")};
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
      .history-stack { position: relative; z-index: 0; }
      .history-stack > .chart { height: clamp(${this.preference("size_220_0_px")}px, ${this.preference("history_plot_viewport_height_percent")}dvh, ${this.preference("size_320_0_px")}px); }
      .history-timeline-strip { position: relative; z-index: 1; display: grid; row-gap: var(--history-band-gap); padding-block-end: var(--history-band-gap); }
      .history-stack > .history-background,
      .history-stack > .history-curves { position: absolute; inset: 0; pointer-events: none; }
      .history-stack > .session-chart { position: relative; display: block; overflow: visible; }
      .session-chart:focus:not(:focus-visible),
      .session-chart .history-selection-handle:focus:not(:focus-visible),
      .session-chart [data-action="history-humidity"]:focus:not(:focus-visible),
      .session-chart .history-event-marker:focus:not(:focus-visible),
      button.history-phase-band-segment:focus:not(:focus-visible),
      button.history-event-band-segment:focus:not(:focus-visible),
      .history-time-axis:focus:not(:focus-visible),
      .history-time-axis [tabindex]:focus:not(:focus-visible) { outline: none; }
      .history-phase-band { position: relative; min-block-size: 0; block-size: var(--history-band-compact-height); font-size: ${this.preference("font_size_small_px")}px; color: var(--sauna-card-text); }
      .history-phase-band-segment { box-sizing: border-box; position: absolute; inset-block: 0; display: grid; place-items: center; min-width: 0; overflow: hidden; white-space: nowrap; border-inline-end: ${this.preference("border_1_0_px")}px solid var(--sauna-color-chart-background); background: var(--history-phase-fill); }
      ${Object.entries(phaseAppearance)
        .map(
          ([phase, style]) => `
        .history-background .${style.shape}, .history-phase-band-segment[data-history-phase="${phase}"] { --history-phase-fill: color-mix(in srgb, var(--sauna-color-${style.color.replaceAll("_", "-")}) ${this.preference(["gang", "phase-other"].includes(style.shape) ? "color_mix_22_0_percent" : "color_mix_16_0_percent")}%, transparent); }
        .history-background .${style.shape} { fill: var(--history-phase-fill); }
      `,
        )
        .join("")}
      button.history-phase-band-segment { min-height: 0; padding: 0; margin: 0; border: 0; border-inline-end: ${this.preference("border_1_0_px")}px solid var(--sauna-color-chart-background); border-radius: 0; box-shadow: none; font: inherit; color: inherit; cursor: pointer; touch-action: manipulation; }
      button.history-phase-band-segment:hover:not(:disabled),
      button.history-phase-band-segment[aria-pressed="true"] { background: var(--history-phase-fill); box-shadow: none; }
      button.history-phase-band-segment:hover:not(:disabled),
      button.history-phase-band-segment[aria-pressed="true"],
      button.history-phase-band-segment:focus-visible { color: var(--sauna-card-text); }
      button.history-phase-band-segment:focus-visible { outline: ${this.preference("stroke_2_0_ratio")}px solid var(--sauna-chart-focus, var(--accent)); outline-offset: -${this.preference("stroke_2_0_ratio")}px; }
      button.history-phase-band-segment[data-snap-target="true"] { box-shadow: inset 0 -${this.preference("stroke_3_0_ratio")}px 0 var(--sauna-chart-focus, var(--accent)); }
      button.history-phase-band-segment[aria-pressed="true"] { box-shadow: inset 0 -${this.preference("stroke_2_0_ratio")}px 0 var(--sauna-chart-focus, var(--accent)); }
      .history-phase-band-segment > span { max-width: 100%; overflow: hidden; }
      .history-lower-band { position: relative; isolation: isolate; display: grid; gap: var(--history-band-gap); padding-block-end: ${this.preference("layout_space_09_px")}px; margin-block-start: var(--history-plot-gap); }
      .history-event-band-row { position: relative; z-index: 1; block-size: var(--history-band-compact-height); pointer-events: none; }
      button.history-event-band-segment { box-sizing: border-box; position: absolute; inset-block: 0; display: grid; place-items: center; min-height: 0; padding: 0; margin: 0; border: 0; border-radius: 0; color: var(--sauna-card-text); font: ${this.preference("font_size_small_px")}px/${this.preference("body_line_height")} var(--primary-font-family, sans-serif); background: color-mix(in srgb, var(--history-event-color) ${this.preference("color_mix_22_0_percent")}%, transparent); box-shadow: none; pointer-events: auto; touch-action: manipulation; }
      button.history-event-band-segment:hover:not(:disabled),
      button.history-event-band-segment[aria-pressed="true"],
      button.history-event-band-segment:active:not(:disabled) { background: color-mix(in srgb, var(--history-event-color) ${this.preference("color_mix_22_0_percent")}%, transparent); border-color: var(--history-event-color); color: var(--sauna-card-text); box-shadow: none; font-weight: inherit; }
      .history-event-band-segment::before { content: ""; position: absolute; inset-block: 0; inset-inline: 0; }
      .history-event-band-segment[data-point="true"] { border-inline-start: ${this.preference("stroke_2_0_ratio")}px solid var(--history-event-color); }
      .history-event-band-segment[data-point="true"]::before { inset-inline: calc(-${this.preference("event_marker_size_px")}px / 2); }
      .history-event-band-segment[data-open="true"] { border-inline-end: ${this.preference("stroke_1_0_ratio")}px dashed var(--history-event-color); }
      .history-event-band-segment > span { position: relative; pointer-events: none; white-space: nowrap; }
      .history-event-band-segment:focus-visible { outline: ${this.preference("stroke_2_0_ratio")}px solid var(--sauna-chart-focus, var(--accent)); outline-offset: -${this.preference("stroke_1_0_ratio")}px; }
      button.history-event-band-segment[data-history-event-kind="door"] { border-block: ${this.preference("stroke_2_0_ratio")}px solid var(--history-event-color); }
      button.history-event-band-segment[data-history-event-kind="infusion"] { z-index: 1; }
      button.history-event-band-segment[data-history-event-kind="heater"] { border-block-start: ${this.preference("stroke_2_0_ratio")}px solid var(--history-event-color); }
      button.history-event-band-segment[data-history-event-kind="door"][data-overlap="true"] { border-block-start: 0; pointer-events: none; }
      button.history-event-band-segment[data-history-event-kind="door"][data-overlap="true"]::before { inset-block: auto -${this.preference("stroke_2_0_ratio")}px; block-size: ${this.preference("stroke_2_0_ratio")}px; pointer-events: auto; }
      .history-event-band-row button.history-event-band-segment[data-history-event-kind="heater"][data-overlap="true"] { z-index: 2; background: transparent; pointer-events: none; }
      .history-event-band-row button.history-event-band-segment[data-snap-target="true"] { box-shadow: inset 0 -${this.preference("stroke_3_0_ratio")}px 0 var(--sauna-chart-focus, var(--accent)); }
      .history-event-band-row button.history-event-band-segment[data-history-event-kind="heater"][data-overlap="true"][data-snap-target="true"] { box-shadow: none; border-block-start-color: var(--sauna-chart-focus, var(--accent)); }
      .history-event-band-row button.history-event-band-segment[data-history-event-kind="door"][data-overlap="true"][data-snap-target="true"] { box-shadow: none; border-block-end-color: var(--sauna-chart-focus, var(--accent)); }
      .history-event-band-row button.history-event-band-segment[aria-pressed="true"] { box-shadow: inset 0 -${this.preference("stroke_2_0_ratio")}px 0 var(--sauna-chart-focus, var(--accent)); }
      .history-event-band-row button.history-event-band-segment[data-history-event-kind="heater"][data-overlap="true"][aria-pressed="true"] { box-shadow: none; border-block-start-color: var(--sauna-chart-focus, var(--accent)); }
      .history-event-band-row button.history-event-band-segment[data-history-event-kind="door"][data-overlap="true"][aria-pressed="true"] { box-shadow: none; border-block-end-color: var(--sauna-chart-focus, var(--accent)); }
      button.history-event-band-segment[data-history-event-kind="heater"][data-overlap="true"]::before { inset-block: -${this.preference("stroke_2_0_ratio")}px auto; block-size: ${this.preference("stroke_2_0_ratio")}px; pointer-events: auto; }
      button.history-event-band-segment[data-history-event-kind="infusion"][data-overlap-heater="true"] { inset-block-start: ${this.preference("stroke_2_0_ratio")}px; }
      button.history-event-band-segment[data-history-event-kind="infusion"][data-overlap-door="true"] { inset-block-end: ${this.preference("stroke_2_0_ratio")}px; }
      .history-band-names { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_13_px")}px; min-block-size: calc(${this.preference("font_size_small_px")}px * ${this.preference("body_line_height")}); font: ${this.preference("font_size_small_px")}px/${this.preference("body_line_height")} var(--primary-font-family, sans-serif); pointer-events: none; }
      .history-band-names button.history-event-category,
      .history-band-names button.history-event-category[aria-pressed="true"],
      .history-band-names button.history-event-category:hover:not(:disabled),
      .history-band-names button.history-event-category:active:not(:disabled) { min-height: 0; margin: 0; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; color: var(--history-event-color); font: inherit; cursor: pointer; pointer-events: auto; }
      .history-band-names button.history-event-category[aria-pressed="false"] { color: var(--sauna-card-muted-text); }
      .history-timeline-strip[data-history-band-detail="true"] { row-gap: 0; }
      .history-timeline-strip[data-history-band-detail="true"] .history-phase-band,
      .history-lower-band[data-history-band-detail="true"] .history-event-band-row { block-size: var(--history-band-height); }
      .history-timeline-strip[data-history-band-detail="true"] > .history-band-names { min-block-size: 0; block-size: 0; overflow: hidden; }
      button.history-phase-band-segment[aria-pressed="true"] { border-inline-end-color: var(--sauna-color-chart-background); }
      button.history-event-band-segment,
      button.history-event-band-segment:hover:not(:disabled),
      button.history-event-band-segment[aria-pressed="true"],
      button.history-event-band-segment:active:not(:disabled) { border-inline-end: ${this.preference("border_1_0_px")}px solid var(--sauna-color-chart-background); }
      .history-lower-band > .history-band-names { position: relative; z-index: 1; }
      .history-time-axis { position: relative; z-index: 1; display: block; width: 100%; margin-block-start: ${this.preference("control_group_gap_px")}px; overflow: visible; cursor: crosshair; touch-action: none; }
      .history-time-axis [data-history-time-ticks] { pointer-events: none; }
      .history-time-axis [data-history-time][data-selected="true"] { fill: var(--sauna-chart-focus, var(--accent)); pointer-events: visiblePainted; cursor: pointer; }
      .history-time-axis [data-history-selection-context] { cursor: pointer; touch-action: manipulation; }
      .history-selection-context-hit { stroke: transparent; stroke-width: ${this.preference("layout_space_09_px")}; pointer-events: visibleStroke; }
      [data-history-selection-context]:focus-visible { outline: none; }
      .history-time-axis:has([data-history-selection-context]:focus-visible) .history-selection-guide { stroke-width: ${this.preference("stroke_3_0_ratio")}; }
      .history-band-names, .history-phase-band-segment > span, .history-event-band-segment > span { transition: opacity ${this.preference("history_view_transition_ms")}ms ease, visibility ${this.preference("history_view_transition_ms")}ms ease; }
      .history-timeline-strip:not([data-history-band-detail="true"]) .history-phase-band-segment > span,
      .history-lower-band:not([data-history-band-detail="true"]) .history-event-band-segment > span,
      .history-phase-band-segment[data-history-label-visible="false"] > span,
      .history-event-band-segment[data-history-label-visible="false"] > span,
      .history-timeline-strip[data-history-band-detail="true"] > .history-band-names { opacity: 0; visibility: hidden; }
      @media (prefers-reduced-motion: reduce) { .history-band-names, .history-phase-band-segment > span, .history-event-band-segment > span { transition: none; } }
      .session-chart [data-action="history-humidity"] { cursor: pointer; touch-action: manipulation; }
      .session-chart [data-action="history-humidity"]:focus-visible { outline: ${this.preference("stroke_2_0_ratio")}px solid var(--sauna-chart-focus, var(--accent)); outline-offset: ${this.preference("layout_space_09_px")}px; }
      #tooltip { box-sizing: border-box;
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        align-items: start;
        gap: ${this.preference("control_group_gap_px")}px;
        background: transparent;
        color: var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)));
        padding: 0;
        font: ${this.preference("font_size_compact_px")}px/${this.preference("body_line_height")} var(--primary-font-family, sans-serif);
      }
      .history-inspection-content { display: grid; grid-template-columns: minmax(0, var(--history-readout-width)) minmax(0, 1fr); align-items: start; gap: ${this.preference("layout_space_13_px")}px; min-width: 0; }
      .history-readout, .history-event-column { display: grid; grid-template-rows: var(--history-heading-height) minmax(0, auto); align-content: start; gap: ${this.preference("control_group_gap_px")}px; min-width: 0; }
      .history-event-column { box-sizing: border-box; padding-inline-start: ${this.preference("layout_space_13_px")}px; border-inline-start: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); }
      .history-event-column:not(:has(.history-tooltip-events:not([hidden]))):has(#history-event-inspection[hidden]) { visibility: hidden; }
      .history-event-readout { display: grid; align-content: start; gap: ${this.preference("control_group_gap_px")}px; min-width: 0; font: ${this.preference("font_size_compact_px")}px/${this.preference("body_line_height")} var(--primary-font-family, sans-serif); }
      .history-column-heading { display: block; margin: 0; min-block-size: calc(${this.preference("font_size_small_px")}px * ${this.preference("body_line_height")}); color: var(--sauna-card-muted-text); font: ${this.preference("font_weight_600_0_ratio")} ${this.preference("font_size_small_px")}px/${this.preference("body_line_height")} var(--primary-font-family, sans-serif); }
      .history-inspection-heading { min-width: 0; block-size: var(--history-heading-height); }
      .history-inspection-heading > .history-tooltip-time { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .history-session-summary { display: grid; grid-template-columns: minmax(0, 1fr); gap: ${this.preference("control_group_gap_px")}px; color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_compact_px")}px; }
      .history-information-values { display: grid; grid-template-columns: minmax(0, var(--history-label-width)) minmax(0, var(--history-value-width)) minmax(0, 1fr); align-items: baseline; gap: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_13_px")}px; }
      .history-information-values > div { display: contents; }
      .history-information-values [data-history-label] { grid-column: 1; min-width: 0; overflow-wrap: anywhere; }
      .history-information-values .history-tooltip-value { grid-column: 2; }
      .history-information-reference { grid-column: 3; min-width: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
      .history-information-status { grid-column: 2 / -1; min-width: 0; overflow-wrap: anywhere; }
      .history-session-summary[hidden] { display: none; }
      #history-information-details { min-width: 0; }
      .history-context-gangs { display: grid; gap: ${this.preference("control_group_gap_px")}px; padding-block-start: ${this.preference("layout_space_13_px")}px; border-block-start: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); font-size: ${this.preference("font_size_compact_px")}px; }
      .history-context-gangs:empty { display: none; }
      .history-context-gang { display: flex; flex-wrap: wrap; align-items: baseline; gap: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_13_px")}px; font-variant-numeric: tabular-nums; }
      .history-inspection { --history-heading-height: calc(${this.preference("font_size_small_px")}px * ${this.preference("body_line_height")}); --history-label-width: calc(${this.preference("font_size_compact_px")}px * ${this.preference("size_8_0_em")}); --history-value-width: calc(${this.preference("font_size_small_px")}px * ${this.preference("size_7_0_em")}); --history-value-columns: minmax(0, var(--history-label-width)) repeat(2, minmax(0, var(--history-value-width))); --history-readout-width: calc(var(--history-label-width) + var(--history-value-width) * 2 + ${this.preference("layout_space_13_px")}px * 2); box-sizing: border-box; display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; inline-size: 100%; max-inline-size: calc(var(--history-readout-width) + ${this.preference("font_size_compact_px")}px * ${this.preference("size_20_0_em")} + ${this.preference("layout_space_13_px")}px * 3); gap: ${this.preference("layout_space_13_px")}px; margin-top: ${this.preference("layout_space_18_px")}px; padding: ${this.preference("layout_space_13_px")}px; border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); border-radius: var(--sauna-control-radius); background: var(--sauna-surface-section); box-shadow: var(--sauna-shadow-section); }
      .history-inspection:has(#tooltip[hidden]):has(#history-event-inspection[hidden]) { display: none; }
      .history-inspection:has(#tooltip[data-selection="overview"][data-session-ended="true"]) { --history-value-columns: minmax(0, var(--history-label-width)) minmax(0, var(--history-value-width)); --history-readout-width: calc(var(--history-label-width) + var(--history-value-width) + ${this.preference("layout_space_13_px")}px); }
      ${Object.entries(phaseAppearance)
        .map(
          ([phase]) => `
        .history-panel:has(#tooltip[data-phase="${phase}"]) [data-history-phase="${phase}"] { font-weight: ${this.preference("font_weight_600_0_ratio")}; }
      `,
        )
        .join("")}
      .history-readout > #tooltip { align-content: start; }
      #tooltip[hidden] { display: none; }
      .history-tooltip-time { grid-column: 1 / -1; color: var(--sauna-card-muted-text); font-weight: ${this.preference("font_weight_500_0_ratio")}; font-size: ${this.preference("font_size_small_px")}px; font-variant-numeric: tabular-nums; }
      .history-tooltip-values { min-width: 0; display: grid; grid-template-columns: var(--history-value-columns); align-items: baseline; align-content: start; gap: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_13_px")}px; }
      .history-tooltip-values > div:not([hidden]) { display: contents; }
      .history-tooltip-values > div[hidden] { display: none; }
      .history-tooltip-values[hidden] { display: none; }
      .history-tooltip-boundaries { display: grid; grid-template-columns: var(--history-value-columns); align-items: baseline; column-gap: ${this.preference("layout_space_13_px")}px; }
      .history-tooltip-boundary { color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_small_px")}px; line-height: var(--history-heading-height); font-weight: ${this.preference("font_weight_500_0_ratio")}; font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
      .history-tooltip-boundaries > :first-child { grid-column: 1; }
      .history-tooltip-boundaries > :nth-child(2) { grid-column: 2; }
      .history-tooltip-boundaries > :nth-child(3) { grid-column: 3; }
      .history-tooltip-values [data-quantity] { grid-column: 1; }
      .history-tooltip-values .history-tooltip-value { grid-column: 2; }
      .history-tooltip-values .history-tooltip-value[data-boundary="end"] { grid-column: 3; }
      #tooltip[data-selection="range"] .history-tooltip-values > div:not([hidden]) { display: contents; }
      #tooltip[data-selection="range"] .history-tooltip-values [data-quantity] { min-width: 0; display: block; overflow-wrap: anywhere; }
      #tooltip[data-selection="range"] .history-tooltip-values [data-quantity]::before { display: inline-block; width: ${this.preference("size_12_0_px")}px; margin-inline-end: ${this.preference("control_group_gap_px")}px; vertical-align: middle; }
      #tooltip[data-selection="range"] .history-tooltip-value { font-size: ${this.preference("font_size_compact_px")}px; text-align: right; white-space: nowrap; }
      #history-event-inspection { min-width: 0; max-inline-size: 100%; margin: 0; overflow-wrap: anywhere; font-size: ${this.preference("font_size_compact_px")}px; color: var(--sauna-card-text); }
      .history-event-inspection-members { display: grid; gap: ${this.preference("control_group_gap_px")}px; }
      .history-event-inspection-members button { max-inline-size: 100%; display: grid; grid-template-columns: max-content minmax(0, max-content); align-items: baseline; gap: ${this.preference("control_group_gap_px")}px; min-height: 0; padding: 0; border: 0; border-radius: 0; font-size: ${this.preference("font_size_small_px")}px; line-height: inherit; text-align: start; }
      .history-event-inspection-members button,
      .history-event-inspection-members button[aria-pressed="true"],
      .history-event-inspection-members button:hover:not(:disabled),
      .history-event-inspection-members button:active:not(:disabled) { background: transparent; color: var(--sauna-card-text); border-color: transparent; box-shadow: none; font-weight: inherit; }
      .history-event-inspection-members time { font-variant-numeric: tabular-nums; }
      .history-selection-band { fill: var(--sauna-chart-focus, var(--accent)); opacity: ${this.preference("opacity_0_12_ratio")}; pointer-events: none; }
      .history-selection-guide { stroke: var(--sauna-chart-focus, var(--accent)); stroke-width: ${this.preference("stroke_1_0_ratio")}; pointer-events: none; }
      .history-time-axis .history-selection-guide { stroke-width: ${this.preference("stroke_2_0_ratio")}; }
      .history-selection-handle { cursor: ew-resize; touch-action: none; }
      .history-selection-hit { fill: transparent; stroke: none; pointer-events: visibleFill; }
      .history-selection-handle:focus-visible { outline: none; }
      .history-selection-handle:focus-visible .history-selection-guide { stroke-width: ${this.preference("stroke_4_0_ratio")}; }
      .session-chart #cursor { pointer-events: none; }
      #cursor[data-snap="true"] { stroke: var(--sauna-chart-focus, var(--accent)); stroke-width: ${this.preference("stroke_2_0_ratio")}; }
      .history-tooltip-values > .history-tooltip-snap { grid-column: 1 / -1; display: block; color: var(--sauna-chart-focus, var(--accent)); font-size: ${this.preference("font_size_small_px")}px; }
      .history-event-marker { cursor: pointer; fill: var(--sauna-color-chart-event); stroke: var(--sauna-color-chart-event); }
      .history-event-guide { stroke: var(--history-event-color, var(--sauna-color-chart-event)); stroke-width: ${this.preference("stroke_1_0_ratio")}; stroke-dasharray: ${this.preference("guide_dash_length_px")} ${this.preference("guide_dash_gap_px")}; pointer-events: none; }
      .history-event-guide[data-snap-target="true"] { stroke: var(--sauna-chart-focus, var(--accent)); stroke-width: ${this.preference("stroke_3_0_ratio")}; }
      .history-event-guide[data-selected="true"] { stroke: var(--sauna-chart-focus, var(--accent)); stroke-width: ${this.preference("stroke_2_0_ratio")}; }
      .history-heater-interval { fill: color-mix(in srgb, var(--history-event-color) ${this.preference("color_mix_10_0_percent")}%, transparent); }
      .history-event-marker .history-event-hit { fill: transparent; stroke: none; pointer-events: all; }
      .history-event-marker .history-event-dot { stroke: var(--sauna-color-chart-background); stroke-width: ${this.preference("stroke_1_0_ratio")}; pointer-events: none; }
      .history-event-marker:focus-visible { fill: var(--sauna-chart-focus, var(--accent)); stroke: var(--sauna-chart-focus, var(--accent)); outline: none; }
      .history-event-marker:focus-visible .history-event-dot { stroke-width: ${this.preference("stroke_2_0_ratio")}; stroke: var(--sauna-chart-focus, var(--accent)); }
      .history-tooltip-values [data-quantity] { min-width: 0; display: block; overflow-wrap: anywhere; }
      .history-tooltip-values [data-quantity]::before { content: ""; display: inline-block; width: ${this.preference("size_12_0_px")}px; height: ${this.preference("size_3_0_px")}px; margin-inline-end: ${this.preference("control_group_gap_px")}px; vertical-align: middle; background: var(--sauna-color-series-temperature); }
      .history-tooltip-values [data-quantity="humidity"]::before { background: var(--sauna-color-series-humidity); }
      .history-tooltip-value { min-width: 0; font-weight: ${this.preference("font_weight_650_0_ratio")}; font-size: ${this.preference("font_size_compact_px")}px; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .history-tooltip-value, .history-tooltip-boundary { display: grid; grid-template-columns: minmax(0, 1fr) calc(${this.preference("font_size_small_px")}px * ${this.preference("size_4_0_em")}); align-items: baseline; column-gap: ${this.preference("control_group_gap_px")}px; }
      .history-tooltip-boundary::after { content: ""; grid-column: 2; }
      .history-tooltip-value[hidden], .history-tooltip-boundary[hidden] { display: none; }
      .history-tooltip-number { text-align: right; }
      .history-tooltip-unit { text-align: start; font-size: ${this.preference("font_size_small_px")}px; }
      .history-tooltip-status { order: 2; flex-basis: 100%; font-weight: ${this.preference("font_weight_600_0_ratio")}; }
      .history-tooltip-events { display: grid; grid-template-columns: max-content minmax(0, max-content); align-items: baseline; align-content: start; gap: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_13_px")}px; max-inline-size: 100%; color: var(--sauna-card-text); }
      .history-tooltip-event { display: contents; }
      .history-tooltip-events[hidden], #history-event-inspection[hidden] { display: none; }
      .history-inspected-event-row { display: grid; grid-template-columns: max-content minmax(0, max-content); align-items: baseline; gap: ${this.preference("layout_space_13_px")}px; }
      .history-inspected-event-row time { color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_small_px")}px; font-variant-numeric: tabular-nums; }
      .history-tooltip-event { display: contents; }
      .history-tooltip-event time { grid-column: 1; color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_small_px")}px; font-variant-numeric: tabular-nums; }
      .history-tooltip-event > span { grid-column: 2 / -1; min-width: 0; overflow-wrap: anywhere; }
      #event-list > .history-event-card { min-height: 0; height: auto; padding: var(--sauna-surface-padding); margin-block: var(--sauna-surface-gap); }
      #event-list > .history-event-card details { margin: 0; }
      #event-list > .history-event-card summary { margin: 0; padding-block: ${this.preference("layout_space_09_px")}px; }
      #event-list > .history-event-card > details:first-child > summary { font-size: ${this.preference("font_size_heading_emphasis_px")}px; line-height: ${this.preference("body_line_height")}; }
      .history-event-groups { list-style: none; margin: ${this.preference("control_group_gap_px")}px 0 0; padding: 0; }
      .history-event-group { display: grid; grid-template-columns: ${this.preference("size_64_0_px")}px minmax(0, 1fr); align-items: baseline; column-gap: ${this.preference("layout_space_13_px")}px; margin: 0; }
      .history-event-group + .history-event-group { border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); }
      .history-event-time { padding-block: ${this.preference("layout_space_09_px")}px; color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_body_px")}px; font-variant-numeric: tabular-nums; }
      .history-event-items { list-style: none; margin: 0; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
      .history-event-items .event-row { display: flex; align-items: baseline; justify-content: space-between; gap: ${this.preference("layout_space_13_px")}px; padding: ${this.preference("layout_space_09_px")}px 0; font-size: ${this.preference("font_size_body_px")}px; }
      .history-event-items .event-row + .event-row { border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); }
      .history-event-items .event-row button { flex: 0 0 auto; min-height: ${this.preference("size_32_0_px")}px; padding: ${this.preference("layout_space_09_px")}px ${this.preference("layout_space_13_px")}px; font-size: ${this.preference("font_size_small_px")}px; }
      .control-overview { max-width: ${this.preference("size_1280_0_px")}px; margin-inline: auto; }
      .dashboard {
        display: grid;
        grid-template-columns: minmax(${this.preference("size_300_0_px")}px, 2fr) minmax(0, 3fr);
        gap: var(--sauna-surface-gap);
        max-width: ${this.preference("size_1280_0_px")}px;
        margin: 0 auto;
        align-items: start;
      }
      @container (max-width: ${this.preference("breakpoint_800_0_px")}px) {
        .dashboard { grid-template-columns: 1fr; }
      }
      .tiles {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: ${this.preference("layout_space_18_px")}px;
      }
      .tile {
        padding: ${this.preference("layout_space_18_px")}px;
        text-align: left;
        min-height: ${this.preference("size_54_0_px")}px;
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
        margin-top: ${this.preference("layout_space_13_px")}px;
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
        margin-top: ${this.preference("layout_space_12_px")}px;
        text-align: center;
      }
      .dial {
        max-width: ${this.preference("size_240_0_px")}px;
        display: block;
        margin: auto;
      }
      .dial text {
        fill: var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)));
      }
      .dial .reading {
        font-size: ${this.preference("font_size_phase_px")}px;
        fill: var(--measurement-color);
      }
      .dial .caption {
        font-size: ${this.preference("font_size_compact_px")}px;
        fill: var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)));
      }
      .dial .target-reading {
        font-size: ${this.preference("font_size_large_px")}px;
        font-weight: ${this.preference("font_weight_650_0_ratio")};
        fill: var(--sauna-accent-readable, var(--accent));
      }
      .dial .tick {
        font-size: ${this.preference("font_size_body_px")}px;
        fill: var(--measurement-color);
      }
      .dashboard .card {
        margin-top: 0;
      }
      .gauge-card h2 {
        text-align: center;
      }
      @media (max-width: ${this.preference("breakpoint_800_0_px")}px) {
        .chart {
          height: clamp(${this.preference("size_280_0_px")}px, ${this.preference("history_compact_viewport_height_percent")}dvh, ${this.preference("size_340_0_px")}px);
        }
        .history-stack > .chart { height: clamp(${this.preference("size_220_0_px")}px, ${this.preference("history_compact_viewport_height_percent")}dvh, ${this.preference("size_280_0_px")}px); }
        .detector-chart {
          height: ${this.preference("size_180_0_px")}px;
        }
        .plot-panel {
          padding: ${this.preference("layout_space_22_px")}px;
        }
        .legend {
          font-size: ${this.preference("font_size_small_px")}px;
          gap: ${this.preference("layout_space_18_px")}px;
        }
        .legend i {
          width: ${this.preference("size_22_0_px")}px;
        }
        main {
          padding: ${this.preference("layout_space_20_px")}px;
        }
      }
      @container (max-width: ${this.preference("breakpoint_520_0_px")}px) {
        .history-inspection-content { grid-template-columns: minmax(0, 1fr); }
        .history-readout { max-inline-size: var(--history-readout-width); }
        .history-event-column { padding-inline-start: 0; border-inline-start: 0; padding-block-start: ${this.preference("layout_space_13_px")}px; border-block-start: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); }
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
        gap: ${this.preference("layout_space_18_px")}px;
        text-align: center;
      }
      .gauge-card h2 {
        font-size: ${this.preference("font_size_large_px")}px;
      }
      .tiles {
        gap: ${this.preference("layout_space_16_px")}px;
      }
      .detail-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: ${this.preference("layout_space_24_px")}px;
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
        font-size: ${this.preference("font_size_compact_px")}px;
        color: var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)));
        margin-top: ${this.preference("layout_space_18_px")}px;
      }
      dd {
        margin: ${this.preference("layout_space_07_px")}px 0 ${this.preference("layout_space_13_px")}px;
      }
      .forms {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: ${this.preference("layout_space_28_px")}px;
      }
      .settings-group {
        margin: ${this.preference("layout_space_18_px")}px 0;
        border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
        border-radius: ${this.preference("control_radius_px")}px;
        padding: 0 ${this.preference("layout_space_22_px")}px;
        background: var(--sauna-surface-section);
        box-shadow: var(--sauna-shadow-section);
      }
      .settings-group summary {
        padding: ${this.preference("layout_space_20_px")}px 0;
        margin: 0;
        font-size: ${this.preference("font_size_large_px")}px;
      }
      .settings-group[open] summary {
        border-bottom: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
        margin-bottom: ${this.preference("layout_space_22_px")}px;
      }
      .settings-group .forms {
        padding-bottom: ${this.preference("layout_space_24_px")}px;
      }
      .parameter-section + .parameter-section,
      .parameter-section + .expert-group {
        margin-top: var(--sauna-surface-gap);
        padding-top: var(--sauna-surface-gap);
        border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
      }
      .parameter-section h3 { margin: 0 0 ${this.preference("layout_space_24_px")}px; }
      .settings-parameters-card .field > .number-input { margin-top: auto; }
      .settings-parameters-card > button { margin-top: var(--sauna-surface-gap); }
      .settings-layout {
        display: grid;
        --settings-navigation-width: ${this.preference("size_210_0_px")}px;
        grid-template-columns: var(--settings-navigation-width) minmax(0, 1fr);
        gap: ${this.preference("surface_gap_px")}px;
        align-items: start;
        height: 100%;
        min-height: 0;
        position: relative;
      }
      #settings { overflow: hidden; }
      .settings-menu-toggle, .settings-menu-backdrop { display: none; }
      .settings-navigation {
        display: grid;
        gap: ${this.preference("layout_space_13_px")}px;
        padding-top: ${this.preference("layout_space_09_px")}px;
      }
      .settings-navigation button { text-align: left; }
      .settings-configuration-link { display: flex; justify-content: space-between; align-items: center; gap: ${this.preference("layout_space_13_px")}px; padding: ${this.preference("layout_space_18_px")}px; margin-top: ${this.preference("layout_space_13_px")}px; color: inherit; border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); text-decoration: none; font-size: ${this.preference("font_size_compact_px")}px; }
      .settings-configuration-link:hover, .settings-configuration-link:focus-visible { text-decoration: underline; }
      .settings-content { min-width: 0; min-height: 0; height: 100%; overflow: auto; scrollbar-gutter: stable; overscroll-behavior: contain; padding: ${this.preference("layout_space_09_px")}px; }
      .settings-content > section > h2 { margin-top: 0; }
      .settings-content .card:first-of-type { margin-top: 0; }
      .settings-content .card { padding: ${this.preference("surface_padding_px")}px; }
      .light-feedback { font-size: ${this.preference("font_size_compact_px")}px; }
      .oven-feedback { display: flex; gap: ${this.preference("layout_space_22_px")}px; flex-wrap: wrap; align-items: center; }
      @container (max-width: ${this.preference("breakpoint_700_0_px")}px) {
        .settings-layout { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); gap: ${this.preference("layout_space_13_px")}px; }
        .settings-menu-toggle { display: inline-flex; justify-self: start; align-items: center; gap: ${this.preference("layout_space_13_px")}px; margin: 0 ${this.preference("layout_space_09_px")}px; }
        .settings-menu-toggle svg { width: ${this.preference("size_18_0_px")}px; height: ${this.preference("size_18_0_px")}px; fill: none; stroke: currentColor; stroke-width: ${this.preference("stroke_1_7_ratio")}; stroke-linecap: round; }
        .settings-navigation { color: var(--sauna-card-text, inherit); display: none; position: absolute; grid-area: 2 / 1; top: 0; bottom: ${this.preference("layout_space_09_px")}px; left: ${this.preference("layout_space_09_px")}px; width: min(var(--settings-navigation-width), calc(100% - ${this.preference("size_8_0_px")}px)); align-content: start; overflow: auto; z-index: 6; padding: ${this.preference("layout_space_13_px")}px; border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); border-radius: var(--sauna-control-radius); background: var(--sauna-surface-raised); box-shadow: var(--sauna-shadow-card); }
        .settings-layout[data-menu-open] .settings-navigation { display: grid; }
        .settings-layout[data-menu-open] .settings-menu-backdrop { display: block; position: absolute; grid-area: 2 / 1; inset: 0; z-index: 5; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
        .settings-content { grid-column: 1; grid-row: 2; }
        .settings-content .card { padding: ${this.preference("layout_space_22_px")}px; }
      }
      .expert-group + .expert-group {
        border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
        margin-top: ${this.preference("surface_padding_px")}px;
        padding-top: ${this.preference("layout_space_18_px")}px;
      }
      .field small {
        font-size: ${this.preference("font_size_small_px")}px;
      }
      .feedback {
        font-weight: ${this.preference("font_weight_650_0_ratio")};
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
        margin-top: ${this.preference("layout_space_20_px")}px;
      }
      .program-buttons {
        grid-column: 1/-1;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: ${this.preference("layout_space_13_px")}px;
      }
      .dial-temperature, .dial-light {
        touch-action: none;
      }
      .target-temperature-track, .instrument-arc-track {
        fill: none;
        stroke: var(--accent);
        stroke-width: ${this.preference("stroke_22_0_ratio")};
        stroke-linecap: round;
        opacity: 0;
        cursor: pointer;
      }
      .target-temperature-handle, .instrument-arc-handle {
        fill: var(--accent);
        stroke: var(--sauna-color-card-background, var(--card-background-color));
        stroke-width: ${this.preference("stroke_4_0_ratio")};
        cursor: pointer;
      }
      .target-temperature-handle[data-inert-target] { cursor: default; }
      .target-temperature-track:focus, .instrument-arc-track:focus {
        outline: none;
        stroke: var(--accent);
        opacity: ${this.preference("opacity_0_35_ratio")};
      }
      .target-temperature-track:focus + .target-temperature-handle, .instrument-arc-track:focus + .instrument-arc-handle {
        stroke: var(--sauna-card-focus, var(--sauna-focus-current));
        stroke-width: ${this.preference("stroke_6_0_ratio")};
      }
      .manual-status {
        display: inline-block;
        border-radius: ${this.preference("radius_20_0_px")}px;
        padding: ${this.preference("layout_space_08_px")}px ${this.preference("layout_space_16_px")}px;
        font-size: ${this.preference("font_size_compact_px")}px;
        font-weight: ${this.preference("font_weight_650_0_ratio")};
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
        grid-template-columns: repeat(${this.preference("diagnostic_wide_columns")}, minmax(0, 1fr));
        gap: ${this.preference("diagnostic_plot_gap_px")}px;
      }
      #detection-plots { container-type: inline-size; }
      @container (max-width: ${this.preference("diagnostic_columns_breakpoint_px")}px) { .diagnostic-grid { grid-template-columns: 1fr; } }
      [data-diagnostic-metric][data-selected="true"] { outline: ${this.preference("border_2_0_px")}px solid var(--sauna-chart-focus, var(--accent)); outline-offset: -${this.preference("layout_space_07_px")}px; }
      .diagnostic-selection-band { fill: var(--sauna-color-detector-marker); opacity: ${this.preference("opacity_0_12_ratio")}; pointer-events: none; }
      .diagnostic-selection-guide { stroke: var(--sauna-chart-focus, var(--accent)); stroke-width: ${this.preference("stroke_2_0_ratio")}; stroke-dasharray: ${this.preference("guide_dash_length_px")} ${this.preference("guide_dash_gap_px")}; pointer-events: none; }
      .diagnostic-selection-context { margin: ${this.preference("layout_space_18_px")}px 0; padding: ${this.preference("layout_space_18_px")}px; border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); border-radius: var(--sauna-control-radius); }
      .diagnostic-selection-context dl { display: grid; grid-template-columns: minmax(${this.preference("size_6_0_rem")}rem, 1fr) minmax(0, 2fr); gap: ${this.preference("layout_space_09_px")}px ${this.preference("layout_space_18_px")}px; overflow-wrap: anywhere; }
      .diagnostic-selection-context dd { margin: 0; }
      .diagnostic-trace-navigation { display: flex; align-items: center; flex-wrap: wrap; gap: ${this.preference("layout_space_13_px")}px; }
      .diagnostic-trace-navigation input { flex: 1 1 ${this.preference("size_120_0_px")}px; min-width: 0; }
      .diagnostic-chart-context { font-size: ${this.preference("font_size_compact_px")}px; margin: ${this.preference("layout_space_13_px")}px 0; }
      .diagnostic-grid .plot-panel {
        margin: 0;
        padding: ${this.preference("layout_space_20_px")}px ${this.preference("diagnostic_plot_padding_px") / 2}px;
      }
      .event-marker {
        position: absolute;
        bottom: var(--event-marker-bottom, -${this.preference("control_group_gap_px")}px);
        width: var(--event-marker-size);
        height: var(--event-marker-size);
        padding: 0;
        border-radius: 50%;
        border: ${this.preference("border_2_0_px")}px solid var(--sauna-color-event-infusion);
        background: var(--accent);
        color: var(--accent-ink);
        font-size: ${this.preference("font_size_fine_px")}px;
        font-weight: ${this.preference("font_weight_700_0_ratio")};
        line-height: ${this.preference("size_13_0_px")}px;
        transform: translateX(-50%);
      }
      .event-marker[data-selected="true"],
      .event-row[data-selected="true"] {
        outline: ${this.preference("border_3_0_px")}px solid var(--accent);
        outline-offset: ${this.preference("layout_space_07_px")}px;
        background: var(--sauna-tint-warning-background, var(--sauna-color-card-background, var(--card-background-color)));
      }
      .event-marker[data-selected="true"] {
        color: var(--sauna-tint-warning-text, var(--sauna-card-text, inherit));
      }
      .event-row button {
        padding: ${this.preference("layout_space_08_px")}px ${this.preference("layout_space_12_px")}px;
        white-space: nowrap;
      }
      .temperature-presets {
        grid-column: 1/-1;
        display: grid;
        grid-template-columns: repeat(var(--preset-columns-wide), minmax(0, 1fr));
        gap: ${this.preference("control_group_gap_px")}px;
        margin-top: ${this.preference("layout_space_16_px")}px;
      }
      .temperature-presets .tile {
        min-width: 0;
        min-height: ${this.preference("size_40_0_px")}px;
        padding: ${this.preference("layout_space_13_px")}px ${this.preference("layout_space_16_px")}px;
        text-align: center;
        white-space: nowrap;
      }
      #program-choice-body { container: program-choice / inline-size; }
      @container program-choice (width < ${this.preference("breakpoint_420_0_px")}px) {
        .temperature-presets { grid-template-columns: repeat(var(--preset-columns-medium), minmax(0, 1fr)); }
      }
      @container program-choice (width < ${this.preference("breakpoint_240_0_px")}px) {
        .temperature-presets { grid-template-columns: repeat(var(--preset-columns-narrow), minmax(0, 1fr)); }
      }
      .program-buttons button {
        white-space: nowrap;
        font-size: ${this.preference("font_size_compact_px")}px;
        padding: ${this.preference("layout_space_13_px")}px ${this.preference("layout_space_16_px")}px;
      }
      .compact-times {
        display: grid;
        grid-template-columns: minmax(${this.preference("size_150_0_px")}px, 1fr) minmax(${this.preference("size_180_0_px")}px, 2fr);
        column-gap: ${this.preference("layout_space_22_px")}px;
        align-items: baseline;
      }
      .compact-times dt,
      .compact-times dd {
        margin-top: ${this.preference("layout_space_13_px")}px;
        margin-bottom: 0;
      }
      .history-plot-frame { background: var(--sauna-color-chart-background); border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); border-radius: var(--sauna-control-radius); box-shadow: 0 ${this.preference("shadow_2_0_px")}px ${this.preference("shadow_3_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_3_ratio")}), 0 ${this.preference("shadow_7_0_px")}px ${this.preference("shadow_16_0_px")}px -${this.preference("shadow_5_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_45_ratio")}); overflow: hidden; }
      .history-plot-frame .history-stack { border: 0; border-radius: 0; box-shadow: none; }
      .history-background [data-history-annotations] rect { stroke: none; }
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
        stroke-width: ${this.preference("stroke_2_0_ratio")};
      }
      .diagnostic-marker .event-marker-point {
        fill: var(--sauna-color-detector-marker);
        stroke: var(--sauna-color-chart-background);
        stroke-width: ${this.preference("stroke_1_0_ratio")};
      }
      .diagnostic-marker .event-marker-link {
        stroke: var(--sauna-color-detector-marker);
        stroke-width: ${this.preference("stroke_1_5_ratio")};
        stroke-dasharray: ${this.preference("marker_link_dash_length_px")} ${this.preference("marker_link_dash_gap_px")};
      }
      .diagnostic-marker .event-marker-label {
        fill: var(--sauna-color-chart-background);
        font: ${this.preference("font_weight_700_0_ratio")} ${this.preference("font_size_caption_px")}px var(--primary-font-family, sans-serif);
        paint-order: stroke;
        stroke: var(--sauna-chart-ink, var(--sauna-color-chart-text));
        stroke-width: ${this.preference("stroke_3_0_px")}px;
        stroke-linejoin: round;
      }
      .diagnostic-marker[data-selected="true"] .event-marker-dot {
        fill: color-mix(in srgb, var(--sauna-color-status-warning) ${this.preference("color_mix_12_0_percent")}%, var(--sauna-color-chart-background));
        stroke: var(--sauna-chart-focus, var(--sauna-chart-ink));
        stroke-width: ${this.preference("stroke_3_0_ratio")};
      }
      .diagnostic-marker:focus-visible .event-marker-dot {
        stroke: var(--sauna-chart-focus, var(--sauna-chart-ink));
        stroke-width: ${this.preference("stroke_4_0_ratio")};
      }
      .event-row[data-selected="true"] {
        background: var(--sauna-tint-warning-background, var(--sauna-color-card-background, var(--card-background-color)));
        color: var(--sauna-tint-warning-text, var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color))));
        outline: ${this.preference("border_3_0_px")}px solid var(--sauna-tint-warning-focus, var(--sauna-card-focus, var(--accent)));
        outline-offset: -${this.preference("layout_space_08_px")}px;
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
        gap: ${this.preference("layout_space_16_px")}px ${this.preference("layout_space_21_px")}px;
      }
      .state-line {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: baseline;
        gap: ${this.preference("layout_space_18_px")}px;
      }
      .state-line .badge {
        align-self: start;
        flex: none;
        white-space: nowrap;
      }
      .availability-line {
        min-width: 0;
        text-align: left;
        font-weight: ${this.preference("font_weight_600_0_ratio")};
        font-variant-numeric: tabular-nums;
        overflow-wrap: anywhere;
      }
      .availability-line.wait {
        color: inherit;
      }
      @media (max-width: ${this.preference("breakpoint_1000_0_px")}px) {
        .dashboard .gauge-card {
          max-width: none;
        }
        .dial {
          max-width: ${this.preference("size_210_0_px")}px;
        }
      }
      @media (max-width: ${this.preference("diagnostic_columns_breakpoint_px")}px) {
        .detail-grid,
        .forms,
        .diagnostic-grid {
          grid-template-columns: 1fr;
        }
        .compact-times {
          grid-template-columns: 1fr;
        }
        .gauges {
          gap: ${this.preference("layout_space_09_px")}px;
        }
        .gauges h2 {
          font-size: ${this.preference("font_size_body_px")}px;
        }
        .card {
          padding: ${this.preference("layout_space_20_px")}px;
        }
        header :is(select, .sauna-select-trigger) {
          max-width: ${this.preference("size_110_0_px")}px;
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
        font-weight: ${this.preference("font_weight_700_0_ratio")};
        --sauna-button-hover: var(--sauna-accent-focus, var(--sauna-card-focus));
      }
      button[aria-current="page"]:disabled,
      button[aria-pressed="true"]:disabled {
        opacity: 1;
        cursor: default;
      }
      button[data-action^="heater:"]:disabled:not([aria-pressed="true"]),
      button[data-action^="light:"]:disabled:not([aria-pressed="true"]) {
        opacity: ${this.preference("opacity_0_5_ratio")};
      }
      button[data-action^="program-remove:"] {
        background: transparent;
        color: var(--danger-text);
      }
      .control-status-row { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: center; margin-bottom: var(--sauna-control-gap); }
      .control-status-row .state-line { grid-column: 1; min-width: 0; }
      .control-status-row .control-mode { grid-column: 2; justify-self: center; max-width: 100%; }
      .manual-controls {
        margin-top: ${this.preference("layout_space_22_px")}px;
      }
      .phase-time {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_16_px")}px;
        min-width: 0;
      }
      .control-section {
        margin-top: ${this.preference("layout_space_22_px")}px;
        padding-top: ${this.preference("layout_space_22_px")}px;
        border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
      }
      .control-section h3 {
        margin: 0 0 ${this.preference("layout_space_16_px")}px;
      }
      .control-section .row,
      .program-form .row {
        align-items: end;
      }
      .program-types {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: var(--sauna-control-gap);
        padding: ${this.preference("layout_space_09_px")}px;
        border-radius: var(--sauna-control-radius);
        background: var(--sauna-surface-recessed);
        margin: ${this.preference("layout_space_22_px")}px 0 ${this.preference("surface_padding_px")}px;
      }
      .program-named-list {
        display: grid;
        gap: ${this.preference("control_group_gap_px")}px;
        margin: ${this.preference("layout_space_16_px")}px 0;
      }
      .program-named-choice {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: ${this.preference("layout_space_16_px")}px;
        width: 100%;
        text-align: left;
      }
      .program-choice-summary,
      .program-choice-details { grid-area: choice; align-self: center; }
      .program-choice-summary { display: flex; align-items: baseline; justify-content: space-between; gap: ${this.preference("layout_space_16_px")}px; }
      .program-choice-summary > span { font-weight: ${this.preference("font_weight_650_0_ratio")}; }
      .program-choice-summary small { white-space: nowrap; font-weight: ${this.preference("font_weight_400_0_ratio")}; }
      .program-choice-details { opacity: 0; font-weight: ${this.preference("font_weight_400_0_ratio")}; white-space: normal; line-height: ${this.preference("line_height_1_4_ratio")}; }
      .program-named-choice:not(.program-feedback):is(:hover, :focus-visible) .program-choice-summary { opacity: 0; }
      .program-named-choice:not(.program-feedback):is(:hover, :focus-visible) .program-choice-details { opacity: 1; }
      .program-types button,
      .program-named-choice {
        position: relative;
        font-weight: ${this.preference("font_weight_700_0_ratio")};
      }
      .program-feedback > .program-choice-content {
        visibility: hidden;
      }
      .program-save-label {
        position: absolute;
        inset: ${this.preference("layout_space_07_px")}px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: inherit;
        font-size: ${this.preference("font_size_small_px")}px;
        line-height: ${this.preference("line_height_1_2_ratio")};
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
        gap: ${this.preference("layout_space_13_px")}px;
        margin-top: ${this.preference("layout_space_22_px")}px;
        padding-top: ${this.preference("layout_space_20_px")}px;
        border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
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
        gap: ${this.preference("layout_space_16_px")}px;
        margin-top: ${this.preference("layout_space_16_px")}px;
        padding: ${this.preference("layout_space_13_px")}px ${this.preference("layout_space_16_px")}px;
        background: var(--sauna-pending-background, transparent);
        border-radius: ${this.preference("radius_8_0_px")}px;
        color: var(--sauna-pending-text, var(--sauna-main-text, var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)))));
        --sauna-focus-current: var(--sauna-pending-focus, var(--sauna-main-focus, var(--sauna-card-focus, var(--accent))));
      }
      .program-pending[aria-hidden="true"] { visibility: hidden; }
      .program-kind.segmented-mode {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        margin: 0 0 ${this.preference("layout_space_22_px")}px;
      }
      .program-kind button {
        min-width: 0;
      }
      .program-form {
        margin: 0;
        padding: ${this.preference("layout_space_18_px")}px;
        border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
        border-radius: ${this.preference("radius_8_0_px")}px;
      }
      .temperature-automation .program-form {
        padding: 0;
        border: 0;
      }
      .program-row .program-form {
        max-width: ${this.preference("size_36_0_rem")}rem;
      }
      .program-form > .field:first-child {
        margin-bottom: ${this.preference("layout_space_20_px")}px;
      }
      .program-temperature-fields {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: ${this.preference("layout_space_18_px")}px;
        margin-bottom: ${this.preference("layout_space_20_px")}px;
      }
      .program-form .program-count-field {
        display: grid;
        grid-template-columns: minmax(0, 1fr) ${this.preference("size_5_5_rem")}rem;
        align-items: center;
        gap: ${this.preference("layout_space_18_px")}px;
      }
      .program-count-field > span {
        display: flex;
        align-items: center;
        gap: ${this.preference("control_group_gap_px")}px;
      }
      .program-count-field > input {
        width: ${this.preference("size_5_5_rem")}rem;
        text-align: center;
      }
      .program-count-field .program-info {
        flex: 0 0 auto;
      }
      .program-row + .program-row {
        border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
      }
      .program-row-summary {
        display: flex;
        align-items: center;
        gap: ${this.preference("layout_space_13_px")}px;
        padding: ${this.preference("layout_space_15_px")}px 0;
      }
      .program-row-label {
        display: grid;
        gap: ${this.preference("layout_space_08_px")}px;
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
        padding: ${this.preference("layout_space_10_px")}px ${this.preference("layout_space_13_px")}px;
        font-size: ${this.preference("font_size_small_px")}px;
        flex: 0 0 auto;
      }
      .program-row[data-drop-position="before"] { border-top: ${this.preference("border_3_0_px")}px solid var(--sauna-card-focus, var(--accent)); }
      .program-row[data-drop-position="after"] { border-bottom: ${this.preference("border_3_0_px")}px solid var(--sauna-card-focus, var(--accent)); }
      .settings-footer {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: ${this.preference("layout_space_13_px")}px ${this.preference("layout_space_18_px")}px;
        margin-top: ${this.preference("surface_gap_px")}px;
      }
      .settings-footer p {
        flex-basis: 100%;
        margin: 0;
      }
      .program-form .field {
        position: relative;
        flex: 1 1 ${this.preference("size_105_0_px")}px;
      }
      .program-step-fields {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(${this.preference("size_105_0_px")}px, 1fr));
        gap: ${this.preference("layout_space_13_px")}px;
        margin-top: ${this.preference("layout_space_16_px")}px;
      }
      .program-info {
        display: inline;
      }
      .program-info button {
        width: ${this.preference("size_25_0_px")}px;
        height: ${this.preference("size_25_0_px")}px;
        min-height: ${this.preference("size_25_0_px")}px;
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
        width: ${this.preference("size_320_0_px")}px;
        max-width: ${this.preference("size_320_0_px")}px;
        overflow: auto;
        overscroll-behavior: contain;
        padding: ${this.preference("layout_space_15_px")}px ${this.preference("layout_space_17_px")}px;
        border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color));
        border-radius: ${this.preference("radius_8_0_px")}px;
        background: var(--sauna-color-card-background, var(--card-background-color));
        color: var(--sauna-card-text, var(--sauna-color-text, var(--primary-text-color)));
        --sauna-focus-current: var(--sauna-card-focus, var(--accent));
        box-shadow: 0 ${this.preference("shadow_4_0_px")}px ${this.preference("shadow_14_0_px")}px color-mix(in srgb, var(--sauna-color-text, var(--primary-text-color)) ${this.preference("color_mix_20_0_percent")}%, transparent);
        font-size: ${this.preference("font_size_small_px")}px;
        line-height: ${this.preference("line_height_1_4_ratio")};
        overflow-wrap: anywhere;
      }
      .program-info-popup[hidden] { display: none; }
      .manual-section h3 {
        margin: 0;
      }
      @container (max-width: ${this.preference("diagnostic_columns_breakpoint_px")}px) {
        header {
          grid-template-columns: 1fr auto;
          gap: ${this.preference("layout_space_18_px")}px;
          margin-bottom: ${this.preference("layout_space_22_px")}px;
        }
        .main-tabs {
          grid-row: 2;
          grid-column: 1 / -1;
        }
        .phase-time {
          gap: ${this.preference("layout_space_08_px")}px ${this.preference("layout_space_13_px")}px;
        }
        .temperature-automation .row {
          align-items: stretch;
        }
        .temperature-automation .field {
          flex: 1 1 ${this.preference("size_110_0_px")}px;
        }
      }
      @media (max-width: ${this.preference("breakpoint_420_0_px")}px) {
        .gauges {
          grid-template-columns: 1fr;
          gap: ${this.preference("layout_space_22_px")}px;
        }
        .dial {
          max-width: ${this.preference("size_270_0_px")}px;
        }
        .dial .tick {
          font-size: ${this.preference("font_size_body_px")}px;
        }
      }

      .control-main .phase { font-weight: ${this.preference("font_weight_600_0_ratio")}; letter-spacing: -${this.preference("tracking_0_025_em")}em; }
      .control-main .control-section, .control-main .manual-controls {
        margin-top: var(--sauna-surface-gap);
        padding: var(--sauna-surface-padding);
        border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border);
        border-radius: var(--sauna-control-radius);
        background: var(--sauna-main-section, var(--sauna-surface-section));
        box-shadow: var(--sauna-shadow-section);
        transition: background-color ${this.preference("transition_350_0_ms")}ms ease;
        color: var(--sauna-page-text);
      }
      .control-main button:focus-visible {
        outline: ${this.preference("border_2_0_px")}px solid var(--sauna-focus-current);
        outline-offset: ${this.preference("layout_space_09_px")}px;
      }
      .control-main .operation-control > button, .control-main [data-action="program-apply"] {
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: var(--sauna-action-height);
        padding: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_18_px")}px;
        font-size: ${this.preference("font_size_large_px")}px;
        line-height: ${this.preference("size_20_0_px")}px;
        font-weight: ${this.preference("font_weight_600_0_ratio")};
        text-align: center;
      }
      .program-current:has([aria-expanded="true"]) {
        padding: 0 0 ${this.preference("layout_space_22_px")}px;
        border-bottom: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border);
      }
      .main-tabs button, .program-types button, .segmented-mode button, .control-auto button {
        position: relative;
        min-height: var(--sauna-button-height);
        background: transparent;
        box-shadow: none;
        border-color: transparent;
        border-radius: calc(var(--sauna-control-radius) - ${this.preference("radius_3_0_px")}px);
        color: var(--sauna-card-muted-text);
        font-size: ${this.preference("font_size_body_px")}px;
        font-weight: ${this.preference("font_weight_600_0_ratio")};
        padding: ${this.preference("layout_space_13_px")}px ${this.preference("layout_space_18_px")}px;
      }
      .main-tabs button[aria-current="page"], .program-types button[aria-pressed="true"], .segmented-mode button[aria-pressed="true"], .control-auto button[aria-pressed="true"] {
        color: var(--sauna-card-text);
        background: var(--sauna-surface-control);
        box-shadow: var(--sauna-shadow-control);
      }
      .main-tabs button[aria-current="page"]::after, .program-types button[aria-pressed="true"]::after, .segmented-mode button[aria-pressed="true"]::after, .control-auto button[aria-pressed="true"]::after {
        content: "";
        position: absolute;
        bottom: ${this.preference("layout_space_08_px")}px;
        left: 30%;
        right: 30%;
        height: ${this.preference("size_2_0_px")}px;
        border-radius: ${this.preference("radius_2_0_px")}px;
        background: var(--accent);
      }
      .segmented-mode { display: flex; gap: var(--sauna-control-gap); padding: ${this.preference("layout_space_09_px")}px; background: var(--sauna-surface-recessed); border-radius: var(--sauna-control-radius); }
      .temperature-presets { gap: var(--sauna-control-gap); }
      .program-named-list { gap: var(--sauna-control-gap); }
      .program-named-choice { padding: ${this.preference("layout_space_18_px")}px ${this.preference("layout_space_20_px")}px; }
      .program-pending { background: transparent; padding: ${this.preference("layout_space_18_px")}px 0 0; }
      .program-actions { margin-top: ${this.preference("layout_space_18_px")}px; border-top: 0; padding-top: 0; }
      .history-controls { display: grid; grid-template-columns: minmax(0, 1fr) max-content; align-items: center; gap: ${this.preference("control_group_gap_px")}px ${this.preference("layout_space_18_px")}px; min-width: 0; margin: 0 0 ${this.preference("layout_space_22_px")}px; }
      .history-session-choice { display: grid; grid-template-columns: max-content minmax(0, 1fr); justify-self: start; align-items: center; gap: ${this.preference("control_group_gap_px")}px; min-width: 0; max-inline-size: 100%; }
      .history-session-choice > label { font-size: ${this.preference("font_size_small_px")}px; color: var(--sauna-card-muted-text); }
      .history-selection-actions { display: grid; justify-items: end; align-items: center; min-block-size: ${this.preference("size_32_0_px")}px; min-inline-size: calc(${this.preference("font_size_small_px")}px * ${this.preference("size_8_0_em")} + ${this.preference("layout_space_13_px")}px * 2); }
      .history-controls [data-action="clear-history-selection"] { min-height: ${this.preference("size_32_0_px")}px; padding: ${this.preference("layout_space_09_px")}px ${this.preference("layout_space_13_px")}px; font-size: ${this.preference("font_size_small_px")}px; white-space: nowrap; }
      .history-controls [data-action="clear-history-selection"][hidden] { display: block !important; visibility: hidden; pointer-events: none; }
      #history-loading { grid-column: 1 / -1; justify-self: end; font-size: ${this.preference("font_size_small_px")}px; color: var(--sauna-card-muted-text); white-space: nowrap; }
      .history-controls :is(select, .sauna-select-trigger) { max-width: 100%; font-weight: ${this.preference("font_weight_600_0_ratio")}; }
      @container (max-width: ${this.preference("breakpoint_520_0_px")}px) {
        .history-controls { grid-template-columns: minmax(0, 1fr); }
        .history-selection-actions { justify-self: end; }
      }
      .history-stack, .detector-chart { background: var(--sauna-color-chart-background); border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); border-radius: var(--sauna-control-radius); box-shadow: 0 ${this.preference("shadow_2_0_px")}px ${this.preference("shadow_3_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_3_ratio")}), 0 ${this.preference("shadow_7_0_px")}px ${this.preference("shadow_16_0_px")}px -${this.preference("shadow_5_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_45_ratio")}); }
      .control-history-inspection > div { padding: ${this.preference("layout_space_22_px")}px; border-radius: var(--sauna-control-radius); border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); background: var(--sauna-surface-section); box-shadow: var(--sauna-shadow-section); }
      #control-history { margin-top: var(--sauna-surface-gap); }
      #plots { margin: 0; }
      .card, .plot-panel { isolation: isolate; }
      @container (max-width: ${this.preference("breakpoint_450_0_px")}px) {
        .main-tabs { min-width: 0; gap: ${this.preference("layout_space_07_px")}px; }
        .program-types button, .main-tabs button, .segmented-mode button { padding-inline: ${this.preference("layout_space_12_px")}px; font-size: ${this.preference("font_size_compact_px")}px; }
        .main-tabs button { flex: 0 1 auto; white-space: nowrap; }
        .control-main .control-section, .control-main .manual-controls { padding: ${this.preference("layout_space_18_px")}px; }
      }
      .manual-controls:not(.manual-entry) { container: output-controls / inline-size; display: grid; grid-template-columns: minmax(0, 1fr); gap: ${this.preference("layout_space_20_px")}px; }
      .manual-controls .manual-section { display: grid; grid-template-columns: ${this.preference("size_4_0_em")}em minmax(0, 1fr); align-items: center; min-width: 0; }
      .manual-controls .manual-section + .manual-section { border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border); padding-top: ${this.preference("layout_space_20_px")}px; }
      .manual-heading h3 { font-size: ${this.preference("font_size_body_emphasis_px")}px; font-weight: ${this.preference("font_weight_600_0_ratio")}; }
      .manual-selection { display: grid; grid-template-columns: ${this.preference("size_7_5_em")}em minmax(0, 1fr); align-items: center; gap: ${this.preference("layout_space_18_px")}px; min-width: 0; width: min(100%, ${this.preference("size_20_0_em")}em); justify-self: center; font-size: ${this.preference("font_size_body_px")}px; }
      .manual-selection:not(:has(.control-auto)) { grid-template-columns: minmax(0, 1fr); max-width: ${this.preference("size_12_0_em")}em; }
      .control-auto, .output-toggle { padding: ${this.preference("layout_space_09_px")}px; background: var(--sauna-surface-recessed); border-radius: var(--sauna-control-radius); }
      .control-auto button, .output-toggle button { width: 100%; min-width: 0; padding-inline: ${this.preference("layout_space_16_px")}px; font-size: ${this.preference("font_size_body_px")}px; font-weight: ${this.preference("font_weight_600_0_ratio")}; white-space: nowrap; }
      .output-toggle { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: ${this.preference("layout_space_09_px")}px; }
      .output-toggle button { background: transparent; border-color: transparent; border-radius: calc(var(--sauna-control-radius) - ${this.preference("radius_3_0_px")}px); color: var(--sauna-card-muted-text); box-shadow: none; }
      .output-toggle button[aria-pressed="true"] { box-shadow: var(--sauna-shadow-control); }
      .output-toggle button[data-regulation-selected="true"] { outline: ${this.preference("border_2_0_px")}px solid var(--sauna-card-muted-text); outline-offset: -${this.preference("layout_space_08_px")}px; }
      @container output-controls (max-width: ${this.preference("breakpoint_280_0_px")}px) {
        .manual-controls .manual-section { grid-template-columns: minmax(0, 1fr); gap: ${this.preference("layout_space_13_px")}px; }
        .manual-selection { gap: ${this.preference("layout_space_13_px")}px; }
        .manual-selection:not(:has(.control-auto)) { max-width: none; }
      }
      .output-toggle button[data-action$=":true"] { --output-surface: var(--sauna-feedback-on-surface); --output-ink: var(--sauna-feedback-on-ink); --output-focus: var(--sauna-feedback-on-focus); }
      .output-toggle button[data-action$=":false"] { --output-surface: var(--sauna-feedback-off-surface); --output-ink: var(--sauna-feedback-off-ink); --output-focus: var(--sauna-feedback-off-focus); }
      .output-toggle button[aria-pressed="true"] { background: var(--output-surface); border-color: var(--output-surface); color: var(--output-ink); --sauna-button-hover: var(--output-focus); --sauna-focus-current: var(--output-focus); }
      .instrument-arc-track[aria-disabled="true"], .instrument-arc-track[aria-disabled="true"] + .instrument-arc-handle { cursor: default; }
      .sr-only { position: absolute; width: ${this.preference("size_1_0_px")}px; height: ${this.preference("size_1_0_px")}px; padding: 0; margin: -${this.preference("layout_space_05_px")}px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
      button[data-command-pending] { position: relative; }
      button[data-command-pending]::after { content: ""; position: absolute; inset: auto ${this.preference("layout_space_13_px")}px ${this.preference("layout_space_10_px")}px; height: ${this.preference("size_2_0_px")}px; background: currentColor; opacity: ${this.preference("opacity_0_65_ratio")}; animation: command-wait ${this.preference("transition_1_2_s")}s ease-in-out infinite alternate; }
      @keyframes command-wait { from { opacity: ${this.preference("opacity_0_25_ratio")}; transform: scaleX(${this.preference("command_wait_minimum_scale")}); } to { opacity: ${this.preference("opacity_0_8_ratio")}; transform: scaleX(1); } }
      @media (prefers-reduced-motion: reduce) { button[data-command-pending]::after { animation: none; } }
      .manual-entry { display: flex; align-items: center; justify-content: space-between; gap: ${this.preference("layout_space_18_px")}px; }
      #control-history { container-type: inline-size; }
      .control-history-lanes { min-width: 0; }
      .control-track { display: grid; grid-template-columns: ${this.preference("size_130_0_px")}px minmax(0, 1fr); align-items: center; gap: ${this.preference("layout_space_18_px")}px; margin: ${this.preference("layout_space_15_px")}px 0; font-size: ${this.preference("font_size_small_px")}px; }
      .control-track > span { color: var(--sauna-card-muted-text); }
      .control-track-bar { height: ${this.preference("size_17_0_px")}px; position: relative; border-radius: ${this.preference("radius_3_0_px")}px; overflow: hidden; background: repeating-linear-gradient(135deg,transparent,transparent ${this.preference("size_4_0_px")}px,var(--sauna-color-border, var(--divider-color)) ${this.preference("size_4_0_px")}px,var(--sauna-color-border, var(--divider-color)) ${this.preference("size_5_0_px")}px); }
      .control-track-bar button { position: absolute; top: 0; bottom: 0; padding: 0; border: 0; border-radius: 0; min-width: ${this.preference("size_1_0_px")}px; box-shadow: none; }
      .control-track-bar button:hover { outline: ${this.preference("border_2_0_px")}px solid var(--sauna-card-text); z-index: 1; }
      .control-track-bar i { position: absolute; top: 0; bottom: 0; border-left: ${this.preference("border_2_0_px")}px solid var(--sauna-card-text); pointer-events: none; }
      .control-time-axis { display: flex; justify-content: space-between; margin-left: ${this.preference("size_130_0_px") + this.preference("layout_space_18_px")}px; font-size: ${this.preference("font_size_small_px")}px; color: var(--sauna-card-muted-text); }
      .control-history-inspection { display: grid; grid-template-columns: 1fr 1fr; gap: ${this.preference("surface_gap_px")}px; }
      .control-history-inspection dl { display: grid; grid-template-columns: 1fr 1fr; gap: ${this.preference("layout_space_13_px")}px; margin: 0; font-size: ${this.preference("font_size_compact_px")}px; }
      .control-history-inspection dd { margin: 0; text-align: right; }
      .control-cursor-row { margin: ${this.preference("layout_space_13_px")}px 0 ${this.preference("layout_space_22_px")}px ${this.preference("size_130_0_px") + this.preference("layout_space_18_px")}px; }
      input.control-cursor { appearance: none; display: block; width: calc(100% + ${this.rangeGeometry("control").thumb}px); max-width: none; height: ${this.preference("size_24_0_px")}px; border: 0; border-radius: 0; padding: 0; margin: 0 0 0 -${this.rangeGeometry("control").inset}px; background: transparent; cursor: pointer; }
      input.control-cursor::-webkit-slider-runnable-track { height: ${this.rangeGeometry("control").track}px; background: var(--sauna-color-border, var(--divider-color)); border-radius: ${this.preference("radius_2_0_px")}px; }
      input.control-cursor::-webkit-slider-thumb { appearance: none; width: ${this.rangeGeometry("control").thumb}px; height: ${this.rangeGeometry("control").thumb}px; border: 0; border-radius: 50%; margin-top: ${this.rangeGeometry("control").offset}px; background: var(--accent); }
      input.control-cursor::-moz-range-track { height: ${this.rangeGeometry("control").track}px; background: var(--sauna-color-border, var(--divider-color)); border-radius: ${this.preference("radius_2_0_px")}px; }
      input.control-cursor::-moz-range-thumb { width: ${this.rangeGeometry("control").thumb}px; height: ${this.rangeGeometry("control").thumb}px; border: 0; border-radius: 50%; background: var(--accent); }
      .control-history-events td { vertical-align: top; font-size: ${this.preference("font_size_compact_px")}px; }
      .control-history-events td:first-child { white-space: nowrap; }
      .control-history-events button { padding: ${this.preference("layout_space_08_px")}px ${this.preference("layout_space_12_px")}px; font-variant-numeric: tabular-nums; }
      .control-history-events tr[data-selected="true"] { background: color-mix(in srgb, var(--accent) ${this.preference("color_mix_10_0_percent")}%, transparent); }
      .control-history-events tr[data-error="true"] { box-shadow: inset ${this.preference("shadow_3_0_px")}px 0 var(--sauna-color-error, var(--error-color)); }
      .detail-data { margin: ${this.preference("layout_space_13_px")}px 0; font-size: ${this.preference("font_size_compact_px")}px; }
      .detail-data > div { display: grid; grid-template-columns: minmax(${this.preference("size_8_0_em")}em, 1fr) minmax(0, 2fr); gap: ${this.preference("layout_space_18_px")}px; padding: ${this.preference("layout_space_10_px")}px 0; border-bottom: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color)); }
      .detail-data dt { color: var(--sauna-card-muted-text); overflow-wrap: anywhere; }
      .detail-data dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
      .detail-data details { margin: 0; }
      .detail-data details > summary { font-weight: ${this.preference("font_weight_500_0_ratio")}; }
      .detail-record-list > details { padding: ${this.preference("layout_space_13_px")}px 0; border-bottom: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color)); }
      .detail-value-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: ${this.preference("layout_space_18_px")}px; }
      .detail-value-plot { min-width: 0; }
      .detail-value-plot h3 { font-size: ${this.preference("font_size_compact_px")}px; margin: ${this.preference("layout_space_13_px")}px 0 ${this.preference("layout_space_08_px")}px; }
      .detail-value-plot svg { width: 100%; display: block; }
      .detail-value-plot text { font-size: ${this.preference("font_size_fine_px")}px; fill: var(--sauna-card-muted-text); }
      .detail-event-list { padding-left: ${this.preference("layout_space_24_px")}px; font-size: ${this.preference("font_size_compact_px")}px; }
      .detail-event-list li { padding: ${this.preference("layout_space_10_px")}px 0; }
      #control-session-evidence > details, .detail-evidence > details { border-top: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color)); padding: ${this.preference("layout_space_16_px")}px 0; }
      @container (max-width: ${this.preference("breakpoint_520_0_px")}px) {
        .control-track { grid-template-columns: ${this.preference("size_94_0_px")}px minmax(0, 1fr); gap: ${this.preference("layout_space_13_px")}px; }
        .control-time-axis, .control-cursor-row { margin-left: ${this.preference("size_94_0_px") + this.preference("layout_space_13_px")}px; }
        .control-time-axis { font-size: ${this.preference("font_size_fine_px")}px; }
        .control-history-inspection { grid-template-columns: 1fr; gap: ${this.preference("layout_space_18_px")}px; }
        .detail-value-grid { grid-template-columns: 1fr; }
        .detail-data > div { grid-template-columns: minmax(${this.preference("size_7_0_em")}em, 1fr) minmax(0, 1.3fr); gap: ${this.preference("layout_space_13_px")}px; }
      }
      @media (max-width: ${this.preference("breakpoint_700_0_px")}px) { .control-history-inspection { grid-template-columns: 1fr; gap: ${this.preference("layout_space_18_px")}px; } }
      .gauge-card { padding: ${this.preference("layout_space_32_px")}px ${this.preference("surface_padding_px")}px ${this.preference("surface_padding_px")}px; }
      .gauge-card h2 { font-size: ${this.preference("font_size_compact_px")}px; font-weight: ${this.preference("font_weight_500_0_ratio")}; letter-spacing: ${this.preference("tracking_0_035_em")}em; color: var(--sauna-card-muted-text); margin-bottom: ${this.preference("layout_space_16_px")}px; }
      .gauges { gap: ${this.preference("layout_space_16_px")}px; grid-template-columns: repeat(var(--round-count, 3), minmax(0, 1fr)); align-items: start; }
      .gauges > * { min-width: 0; }
      .gauges > [data-instrument-style="linear"] { grid-column: 1 / -1; }
      .dial { width: 100%; max-width: ${this.preference("size_320_0_px")}px; }
      .instrument-face { stroke: var(--sauna-color-border, var(--divider-color)); stroke-width: ${this.preference("stroke_0_7_ratio")}; filter: drop-shadow(0 ${this.preference("shadow_3_0_px")}px ${this.preference("shadow_3_0_px")}px rgb(0 0 0 / ${this.preference("opacity_0_25_ratio")})); }
      .instrument-rim { fill: none; stroke-width: ${this.preference("stroke_1_0_ratio")}; pointer-events: none; }
      .instrument-readout { fill: var(--sauna-color-card-background, var(--card-background-color)); stroke: color-mix(in srgb, var(--measurement-color) ${this.preference("color_mix_24_0_percent")}%, transparent); stroke-width: ${this.preference("stroke_1_0_ratio")}; }
      .dial .reading { font-size: ${this.preference("font_size_instrument_px")}px; font-weight: ${this.preference("font_weight_550_0_ratio")}; letter-spacing: -${this.preference("tracking_0_8_px")}px; font-variant-numeric: tabular-nums; }
      .dial .reading-unit { font-size: ${this.preference("font_size_body_emphasis_px")}px; font-weight: ${this.preference("font_weight_400_0_ratio")}; letter-spacing: 0; }
      .dial .target-caption { font-size: ${this.preference("font_size_micro_px")}px; font-weight: ${this.preference("font_weight_500_0_ratio")}; letter-spacing: ${this.preference("tracking_1_5_px")}px; fill: var(--sauna-card-muted-text); }
      .dial .target-caption-next { letter-spacing: ${this.preference("tracking_0_45_px")}px; }
      .dial .target-reading { font-size: ${this.preference("font_size_heading_px")}px; font-weight: ${this.preference("font_weight_450_0_ratio")}; fill: var(--sauna-card-text); font-variant-numeric: tabular-nums; }
      .dial .target-reading .reading-unit { font-size: ${this.preference("font_size_caption_px")}px; }
      .dial .tick { font-size: ${this.preference("font_size_caption_px")}px; font-weight: ${this.preference("font_weight_450_0_ratio")}; opacity: ${this.preference("opacity_0_85_ratio")}; }
      .humidity-symbol { fill: color-mix(in srgb, var(--measurement-color) ${this.preference("color_mix_12_0_percent")}%, transparent); stroke: var(--measurement-color); stroke-width: ${this.preference("stroke_1_2_ratio")}; opacity: ${this.preference("opacity_0_75_ratio")}; }
      .light-symbol { fill: none; stroke: var(--measurement-color); stroke-width: ${this.preference("stroke_1_5_ratio")}; stroke-linecap: round; stroke-linejoin: round; opacity: ${this.preference("opacity_0_75_ratio")}; }
      .light-instrument { --accent: var(--sauna-color-series-light); }
      .measurement-instrument { min-width: 0; text-align: center; }
      .measurement-instrument[data-instrument-style="linear"] { padding: ${this.preference("layout_space_18_px")}px ${this.preference("layout_space_22_px")}px; border: ${this.preference("border_1_0_px")}px solid var(--sauna-color-border, var(--divider-color)); border-radius: var(--sauna-control-radius); background: linear-gradient(135deg, var(--sauna-color-card-background, var(--card-background-color)), var(--sauna-color-page-background, var(--primary-background-color))); box-shadow: var(--sauna-shadow-section); }
      .measurement-instrument[data-instrument-style="linear"] > h2 { margin: 0 0 ${this.preference("layout_space_07_px")}px; text-align: left; }
      .linear-instrument { display: grid; grid-template-columns: ${this.preference("size_100_0_px")}px minmax(0, 1fr) ${this.preference("size_22_0_px")}px; align-items: center; gap: ${this.preference("layout_space_22_px")}px; }
      .linear-reading { margin: 0; color: var(--measurement-color); text-align: left; white-space: nowrap; font-size: ${this.preference("font_size_instrument_px")}px; font-weight: ${this.preference("font_weight_550_0_ratio")}; letter-spacing: -${this.preference("tracking_0_8_px")}px; font-variant-numeric: tabular-nums; line-height: ${this.preference("line_height_1_25_ratio")}; }
      .linear-symbol { width: ${this.preference("size_22_0_px")}px; height: ${this.preference("size_22_0_px")}px; fill: none; stroke: var(--measurement-color); stroke-width: ${this.preference("stroke_1_5_ratio")}; stroke-linecap: round; stroke-linejoin: round; opacity: ${this.preference("opacity_0_75_ratio")}; }
      .linear-reading > span { font-size: ${this.preference("font_size_body_emphasis_px")}px; font-weight: ${this.preference("font_weight_400_0_ratio")}; letter-spacing: 0; }
      .linear-scale { container: linear-scale / inline-size; display: grid; grid-template-areas: "caption" "track" "ticks"; grid-template-columns: minmax(0, 1fr); grid-template-rows: ${this.preference("size_17_0_px")}px ${this.preference("size_28_0_px")}px ${this.preference("size_18_0_px")}px; align-items: center; min-width: 0; }
      .linear-rail { grid-area: track; height: ${this.preference("size_8_0_px")}px; border-radius: ${this.preference("control_radius_px")}px; background: var(--sauna-color-border); overflow: hidden; }
      .linear-rail i { display: block; height: 100%; border-radius: inherit; background: var(--measurement-color); }
      .linear-ticks { grid-area: ticks; position: relative; height: ${this.preference("size_18_0_px")}px; margin-top: 0; color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_fine_px")}px; font-variant-numeric: tabular-nums; }
      .linear-ticks span { position: absolute; transform: translateX(-50%); }
      .linear-ticks [data-tick-density="middle"] { display: none; }
      @container linear-scale (max-width: ${this.preference("breakpoint_220_0_px")}px) {
        .linear-ticks [data-tick-density="detail"] { display: none; }
        .linear-ticks [data-tick-density="middle"] { display: inline; }
      }
      @container linear-scale (max-width: ${this.preference("breakpoint_110_0_px")}px) {
        .linear-ticks [data-tick-density="middle"] { display: none; }
      }
      .instrument-slider { margin: ${this.preference("layout_space_09_px")}px auto 0; max-width: ${this.preference("size_240_0_px")}px; width: 100%; }
      .linear-scale .instrument-slider { display: contents; }
      .linear-scale .instrument-slider label { grid-area: caption; justify-content: flex-end; gap: ${this.preference("control_group_gap_px")}px; margin: 0; font-size: ${this.preference("font_size_caption_px")}px; }
      .linear-scale .instrument-slider output { font-size: ${this.preference("font_size_small_px")}px; }
      .instrument-slider label { display: flex; justify-content: space-between; align-items: baseline; margin: 0 0 ${this.preference("control_group_gap_px")}px; color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_small_px")}px; }
      .instrument-slider output { color: var(--sauna-card-text); font-size: ${this.preference("font_size_body_px")}px; font-variant-numeric: tabular-nums; }
      input.instrument-range { display: block; appearance: none; box-sizing: border-box; width: 100%; max-width: none; min-width: 0; padding: 0; margin: 0; height: ${this.preference("size_28_0_px")}px; border: 0; background: transparent; cursor: pointer; }
      input.instrument-range::-webkit-slider-runnable-track { height: ${this.rangeGeometry("instrument").track}px; border-radius: ${this.preference("radius_5_0_px")}px; background: var(--sauna-color-border); }
      input.instrument-range::-webkit-slider-thumb { appearance: none; width: ${this.rangeGeometry("instrument").thumb}px; height: ${this.rangeGeometry("instrument").thumb}px; margin-top: ${this.rangeGeometry("instrument").offset}px; border-radius: 50%; background: var(--accent); border: ${this.preference("border_2_0_px")}px solid var(--sauna-color-card-background); box-shadow: var(--sauna-shadow-control); }
      input.instrument-range::-moz-range-track { height: ${this.rangeGeometry("instrument").track}px; border-radius: ${this.preference("radius_5_0_px")}px; background: var(--sauna-color-border); }
      input.instrument-range::-moz-range-thumb { width: ${this.rangeGeometry("instrument").content}px; height: ${this.rangeGeometry("instrument").content}px; border-radius: 50%; background: var(--accent); border: ${this.preference("border_2_0_px")}px solid var(--sauna-color-card-background); }
      .linear-scale input.instrument-range { grid-area: track; z-index: 1; width: calc(100% + ${this.rangeGeometry("instrument").thumb}px); margin-inline: -${this.rangeGeometry("instrument").inset}px; }
      .linear-scale input.instrument-range::-webkit-slider-runnable-track { background: transparent; }
      .linear-scale input.instrument-range::-moz-range-track { background: transparent; }
      input.instrument-range:disabled { opacity: ${this.preference("opacity_0_45_ratio")}; cursor: default; }
      .instrument-notice { display: block; margin-top: ${this.preference("layout_space_10_px")}px; color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_small_px")}px; }
      .weather-instrument { display: grid; grid-template-columns: auto minmax(0, 1fr); grid-template-areas: "heading heading" "condition values"; gap: ${this.preference("layout_space_16_px")}px ${this.preference("surface_padding_px")}px; align-items: center; margin-top: ${this.preference("layout_space_22_px")}px; padding: ${this.preference("layout_space_18_px")}px ${this.preference("layout_space_20_px")}px; border-radius: var(--sauna-control-radius); background: var(--sauna-surface-section); box-shadow: var(--sauna-shadow-section); }
      .weather-heading { grid-area: heading; display: flex; align-items: baseline; flex-wrap: wrap; gap: ${this.preference("layout_space_09_px")}px ${this.preference("layout_space_13_px")}px; }
      .weather-heading > span { color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_fine_px")}px; letter-spacing: ${this.preference("tracking_0_08_em")}em; }
      .weather-heading h2 { margin: 0; font-size: ${this.preference("font_size_body_px")}px; color: var(--sauna-card-text); letter-spacing: 0; }
      .weather-condition { grid-area: condition; display: flex; flex-direction: column; align-items: center; gap: 0; margin: 0; color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_caption_px")}px; }
      .weather-picture { width: ${this.preference("size_60_0_px")}px; flex: 0 0 auto; overflow: visible; fill: none; stroke: var(--sauna-card-muted-text); stroke-width: ${this.preference("stroke_3_0_ratio")}; stroke-linecap: round; stroke-linejoin: round; }
      .weather-sun, .weather-moon, .weather-lightning { color: var(--accent); stroke: currentColor; }
      .weather-sun circle, .weather-moon { fill: color-mix(in srgb, var(--accent) ${this.preference("color_mix_18_0_percent")}%, transparent); }
      .weather-cloud { fill: var(--sauna-surface-control); }
      .weather-rain, .weather-snow { stroke: var(--sauna-color-series-humidity); }
      .weather-values { grid-area: values; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: ${this.preference("layout_space_13_px")}px ${this.preference("layout_space_22_px")}px; margin: 0; }
      .weather-values dt { margin: 0; font-size: ${this.preference("font_size_caption_px")}px; color: var(--sauna-card-muted-text); overflow-wrap: anywhere; }
      .weather-values dd { margin: ${this.preference("layout_space_07_px")}px 0 0; font-size: ${this.preference("font_size_subtitle_px")}px; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .weather-values dd span { font-size: ${this.preference("font_size_caption_px")}px; color: var(--sauna-card-muted-text); }
      .weather-instrument time { margin-inline-start: auto; color: var(--sauna-card-muted-text); font-size: ${this.preference("font_size_caption_px")}px; white-space: nowrap; }
      .appearance-instruments { display: grid; grid-template-columns: repeat(auto-fit, minmax(${this.preference("size_180_0_px")}px, 1fr)); gap: ${this.preference("layout_space_20_px")}px; margin-bottom: ${this.preference("surface_gap_px")}px; }
      .appearance-instruments label { display: grid; gap: ${this.preference("layout_space_12_px")}px; font-size: ${this.preference("font_size_compact_px")}px; }
      .appearance-instruments :is(select, .sauna-select-trigger) { width: 100%; min-width: 0; }
      @container (max-width: ${this.preference("breakpoint_450_0_px")}px) {
        .gauges { grid-template-columns: minmax(0, 1fr); }
        .weather-instrument { grid-template-columns: ${this.preference("size_70_0_px")}px minmax(0, 1fr); gap: ${this.preference("layout_space_13_px")}px ${this.preference("layout_space_18_px")}px; padding: ${this.preference("layout_space_18_px")}px; }
        .measurement-instrument[data-instrument-style="linear"] { padding: ${this.preference("layout_space_16_px")}px ${this.preference("layout_space_18_px")}px; }
        .linear-instrument { grid-template-columns: ${this.preference("size_88_0_px")}px minmax(0, 1fr) ${this.preference("size_18_0_px")}px; gap: ${this.preference("layout_space_18_px")}px; }
        .linear-reading { font-size: ${this.preference("font_size_display_emphasis_px")}px; }
        .linear-reading > span { font-size: ${this.preference("font_size_compact_px")}px; }
        .linear-symbol { width: ${this.preference("size_18_0_px")}px; height: ${this.preference("size_18_0_px")}px; }
        .weather-values { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: ${this.preference("layout_space_13_px")}px; margin: 0; }
        .weather-values dd { font-size: ${this.preference("font_size_large_px")}px; }
      }
    `;
  }
  shell() {
    this.shadowRoot.innerHTML = `<style id="panel-appearance-style">${this.appearanceStyles()}</style><main>
      <header><div class="header-brand"><button class="header-icon" data-action="menu" aria-label="Home-Assistant-Seitenleiste umschalten" title="Home-Assistant-Seitenleiste umschalten" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button><h1 id="instance-title">Sauna</h1></div><nav class="tabs main-tabs" id="main-navigation" aria-label="Ansicht"><button data-action="overview" aria-current="page">Steuerung</button><button data-action="history">Verlauf</button><button data-action="details">Details</button><button data-action="settings">Einstellungen</button></nav><div class="header-context"><select id="instance" aria-label="Sauna auswählen"></select><button class="header-icon" data-action="fullscreen" aria-label="Vollbild" title="Vollbild" hidden><svg data-fullscreen-icon="enter" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></svg><svg data-fullscreen-icon="exit" viewBox="0 0 24 24" aria-hidden="true" hidden><path d="M3 8h5V3m8 0v5h5M8 21v-5H3m18 0h-5v5"/></svg></button></div></header>
      <nav class="tabs detail-tabs" aria-label="Detailansicht" hidden><button data-action="detail" aria-current="page">Betrieb & Fristen</button><button data-action="detail-history">Detailverlauf</button><button data-action="diagnostics">Erkennungskontrolle</button></nav>
      <div id="message" role="alert"></div><section id="current" aria-live="polite"><p>Lade Saunadaten …</p></section><section id="details" hidden></section>
      <section id="history" hidden><div class="plot-panel history-panel"><div class="history-controls"><div class="history-session-choice"><label for="session">Sitzung vom</label><select id="session" aria-label="Saunasitzung auswählen"><option value="live">Letzte Sitzung</option></select></div><div class="history-selection-actions"><button data-action="clear-history-selection" hidden disabled>Auswahl aufheben</button></div><span id="history-loading" role="status" hidden></span></div><div class="history-plot-frame"><div id="plots"></div><div id="history-legends">${this.historyLegendMarkup()}</div></div>
        <div class="history-inspection" id="history-inspection"><div class="history-inspection-heading"></div><div class="history-inspection-content"><div class="history-readout"><strong class="history-column-heading">Messwerte</strong><div id="tooltip" role="group" aria-label="Werte am markierten Zeitpunkt" hidden></div></div><div class="history-event-column"><strong class="history-column-heading">Ereignisse</strong><div class="history-event-readout"><div id="history-event-inspection" role="group" aria-label="Ereignisinspektion" aria-live="polite" hidden></div></div></div></div><div class="history-session-summary" data-history-session-summary hidden></div><div class="history-context-gangs" data-history-context-gangs></div></div></div><div id="control-history" hidden></div><div id="detection-plots" hidden></div><div id="gangs"></div><div id="event-list"></div>
      </section><section id="settings" hidden></section>
    </main>`;
    this.shadowRoot.addEventListener("click", (e) => {
      const moment = e.target.closest("[data-control-time]");
      if (moment) {
        this.controlHistoryCursor = Number(moment.dataset.controlTime);
        this.controlHistoryEnd = Math.max(
          this.controlHistoryEnd || 0,
          this.controlHistoryCursor,
        );
        this.renderControlHistory();
      }
      const b = e.target.closest("[data-action]");
      if (b) this.runPanelAction(() => this.action(b.dataset.action));
    });
    this.shadowRoot.addEventListener("change", (e) => {
      if (e.target.id === "diagnostic-trace-index") {
        const trace = this.diagnosticData().traces[Number(e.target.value)];
        if (trace) {
          this.selectedDiagnosticTrace = trace;
          this.selectedDiagnosticTraceAt = trace.at;
          this.highlightedEventId = null;
          this.pendingEventFocus = null;
          this.drawDiagnostics();
        }
        return;
      }
      if (e.target.id === "control-history-cursor") {
        this.controlHistoryCursor =
          stamp(this.shown.session.timeline.session_started_at) +
          Number(e.target.value);
        this.renderControlHistory();
        this.$("#control-history-cursor")?.focus();
      }
      if (e.target.id !== "instance" && !this.state) return;
      if (e.target.id === "instance") {
        this.clearHistorySelection();
        this.cancelHistoryInput();
        this.entry = e.target.value;
        this.syncInstanceTitle();
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
        this.clearHistorySelection();
        this.cancelHistoryInput();
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
        this.startHistoryLoad(true);
      }
      if (e.target.id === "button-program")
        this.runPanelAction(() => this.action("button-program"));
      if (e.target.id === "button-session-gesture")
        this.runPanelAction(() => this.action("button-gesture"));
    });
    this.shadowRoot.addEventListener("input", (e) => {
      if (!this.state) return;
      if (
        e.target.matches(
          "[data-appearance-color],[data-appearance-picker],[data-appearance-scale],[data-appearance-preference]",
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
        if (output) output.textContent = `${this.num(this.linearTargetDraft)} °C`;
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
        e.target.matches(
          '.diagnostic-marker[data-action], .history-event-marker[data-action], .history-phase-band-segment[data-action], .session-chart [data-action="history-humidity"], [data-history-selection-context]',
        ) &&
        ["Enter", " "].includes(e.key)
      ) {
        e.preventDefault();
        this.runPanelAction(() => this.action(e.target.dataset.action));
        return;
      }
      if (
        e.key === "Escape" &&
        this.historySelectionDrag &&
        e.target.closest("#history")
      ) {
        e.preventDefault();
        this.cancelHistorySelectionDrag();
        return;
      }
      if (
        e.key === "Escape" &&
        e.target.closest("#history") &&
        (this.historyValueSelection || this.historyHoverPreview)
      ) {
        e.preventDefault();
        this.clearHistorySelection();
        return;
      }
      if (e.target.closest('.session-chart [data-action="history-humidity"]')) return;
      if (e.target.closest("svg.session-chart, svg.history-time-axis")) {
        this.keyHistorySelection(e);
        if (e.defaultPrevented) return;
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
      if (e.target.closest("svg.session-chart, svg.history-time-axis") && this.window) {
        const width = this.window[1] - this.window[0];
        if (["ArrowLeft", "ArrowRight", "Home", "+", "-", "="].includes(e.key)) {
          e.preventDefault();
          if (e.key === "Home") {
            this.cancelHistoryViewTransition();
            this.zoom = 1;
            this.window = null;
          } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            const offset =
              width *
              (e.key === "ArrowLeft" ? -1 : 1) *
              this.preference("history_keyboard_pan_fraction");
            this.setHistoryWindow(this.window[0] + offset, this.window[1] + offset);
          } else {
            const middle = (this.window[0] + this.window[1]) / 2,
              next =
                width *
                (e.key === "-"
                  ? this.preference("history_zoom_factor")
                  : 1 / this.preference("history_zoom_factor"));
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
      const handle = this.eventElement(
        e,
        "[data-history-selection-handle], [data-history-selection-boundary]",
      );
      if (handle) return this.beginHistorySelectionDrag(e, handle);
      const lightTarget = this.eventElement(e, "[data-light-arc]");
      if (lightTarget) return this.beginLightDrag(e, lightTarget.closest("svg"));
      const target = this.eventElement(e, "[data-target-arc]");
      if (target) return this.beginTemperatureDrag(e, target.closest("svg"), target);
      const svg = this.eventElement(e, "svg.session-chart, svg.history-time-axis");
      if (svg) {
        if (
          this.eventElement(
            e,
            '.history-event-marker, .session-chart [data-action="history-humidity"], [data-action="history-selection-window"]',
          )
        )
          return;
        this.beginChartPointer(e, svg);
        this.scheduleHover({
          svg,
          pointerId: e.pointerId,
          clientX: e.clientX,
          clientY: e.clientY,
        });
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
      if (this.historySelectionDrag?.pointerId === e.pointerId)
        return this.endHistorySelectionDrag(e);
      this.endChartPointer(e);
    });
    this.shadowRoot.addEventListener("pointercancel", (e) => {
      if (this.lightInteraction?.pointerId === e.pointerId)
        return this.cancelLightDrag();
      if (this.temperatureInteraction?.pointerId === e.pointerId)
        return this.cancelTemperatureDrag(e);
      if (this.historySelectionDrag?.pointerId === e.pointerId)
        return this.endHistorySelectionDrag(e, true);
      this.endChartPointer(e, true);
    });
    this.shadowRoot.addEventListener("lostpointercapture", (e) => {
      if (this.lightInteraction?.pointerId === e.pointerId) this.cancelLightDrag();
      if (this.temperatureInteraction?.pointerId === e.pointerId)
        this.cancelTemperatureDrag(e);
      if (this.historySelectionDrag?.pointerId === e.pointerId)
        return this.endHistorySelectionDrag(e, true);
      this.endChartPointer(e, true);
    });
    this.shadowRoot.addEventListener("pointermove", (e) => {
      if (this.lightInteraction?.pointerId === e.pointerId)
        return this.updateLightDrag(e);
      if (this.temperatureInteraction?.pointerId === e.pointerId)
        return this.updateTemperatureDrag(e);
      if (this.historySelectionDrag?.pointerId === e.pointerId)
        return this.updateHistorySelectionDrag(e);
      const svg = this.eventElement(e, "svg.session-chart, svg.history-time-axis");
      if (!svg) return;
      if (
        this.eventElement(
          e,
          '.session-chart [data-action="history-humidity"], [data-action="history-selection-window"]',
        )
      )
        return;
      this.updateChartPointer(e);
      if (this.chartPointers.size === 2 && this.chartPointers.has(e.pointerId)) {
        e.preventDefault();
        this.pinchZoom(svg);
        return;
      }
      if (this.historyInputMode !== "webkit")
        this.scheduleHover({
          svg,
          historyEventId: this.eventElement(e, ".history-event-marker")?.dataset
            .historyEventId,
          pointerId: e.pointerId,
          clientX: e.clientX,
          clientY: e.clientY,
        });
    });
    this.shadowRoot.addEventListener("pointerout", (e) => {
      if (
        this.eventElement(e, "svg.session-chart, svg.history-time-axis") &&
        !e.relatedTarget?.closest?.("svg.session-chart, svg.history-time-axis")
      ) {
        this.pendingHover = null;
        this.lastHistoryPointer = null;
        this.historyHoverPreview = null;
        this.historyChart?.interaction.hide();
        this.syncHistorySelection();
      }
    });
    this.shadowRoot.addEventListener(
      "wheel",
      (e) => {
        const svg = this.eventElement(e, "svg.session-chart, svg.history-time-axis");
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
    for (const key of ["session", "last_session"]) {
      const session = state[key],
        id = session?.timeline?.session_id,
        previous =
          !archiveChanged && id
            ? [this.state?.session, this.state?.last_session].find(
                (candidate) => candidate?.timeline?.session_id === id,
              )
            : null;
      if (session)
        session.energy = mergeEnergyIntervals(
          previous?.energy,
          session.energy,
          session.timeline?.session_started_at,
        );
    }
    this.state = state;
    if (state.title !== undefined || state.instances)
      this.syncInstanceTitle(state.title, state.instances);
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
  syncInstanceTitle(title, instances = []) {
    const select = this.$("#instance"),
      options = Array.from(select?.options || []),
      names = new Map(instances.map((item) => [item.entry_id, item.title]));
    if (title !== undefined) names.set(this.entry, title);
    let changed = false;
    for (const option of options) {
      const name = names.get(option.value);
      if (name !== undefined && option.textContent !== name) {
        option.textContent = name;
        changed = true;
      }
    }
    const option = options.find((item) => item.value === this.entry),
      name = names.get(this.entry) ?? option?.textContent ?? "Sauna";
    const heading = this.$("#instance-title");
    if (heading && heading.textContent !== name) heading.textContent = name;
    if (changed) this.selectMenu?.sync();
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
  stateRequestPath(entry, includeEnergyIntervals = true) {
    const query = [];
    if (this.panel?.config?.appearance_catalog) query.push("include_catalog=false");
    if (!includeEnergyIntervals) query.push("include_energy_intervals=false");
    else if (entry === this.entry) {
      for (const [key, idKey, afterKey] of [
        ["session", "energy_session_id", "energy_after"],
        ["last_session", "last_energy_session_id", "last_energy_after"],
      ]) {
        const session = this.state?.[key],
          id = session?.timeline?.session_id,
          transport = energyIntervalTransport(session?.energy);
        if (id && transport?.horizon && !transport.bootstrapNeeded)
          query.push(
            `${idKey}=${encodeURIComponent(id)}`,
            `${afterKey}=${encodeURIComponent(transport.horizon)}`,
          );
      }
    }
    return `/${entry}/state${query.length ? `?${query.join("&")}` : ""}`;
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
        this.syncInstanceTitle();
      }
      const entry = this.entry;
      const state = await this.api(this.stateRequestPath(entry));
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
        this.startHistoryLoad(requested);
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
      cache = {
        sessionId: id,
        records: [],
        after: 0,
        pageRunLoaded: false,
        finalSynced: false,
      };
      this.cache.set(id, cache);
    }
    // Map order is the most recent access order, including warm selections.
    this.cache.delete(id);
    this.cache.set(id, cache);
    cache.sessionId ??= id;
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
    const configured = this.preference("history_cache_records"),
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
    const incomingSession = page.session;
    if (
      incomingSession &&
      cache.sessionId &&
      incomingSession.timeline?.session_id !== cache.sessionId
    )
      throw Error("Archivantwort für eine andere Sitzung.");
    cache.sessionId ??= incomingSession?.timeline?.session_id;
    const measurementWindowSignature = JSON.stringify(page.measurement_window || null);
    if (cache.measurementWindowSignature !== measurementWindowSignature) {
      cache.measurement_window = page.measurement_window;
      cache.measurementWindowSignature = measurementWindowSignature;
      cache.metadataRevision = (cache.metadataRevision || 0) + 1;
    }
    const session = incomingSession
        ? {
            ...incomingSession,
            energy: mergeEnergyIntervals(
              cache.energy,
              incomingSession.energy,
              incomingSession.timeline?.session_started_at,
            ),
          }
        : incomingSession,
      projection = page.phase_projection,
      sessionSignature = JSON.stringify(
        session
          ? { ...session, energy: energyIntervalSignature(session.energy) }
          : null,
      ),
      projectionSignature = JSON.stringify(projection || null);
    cache.energy = session?.energy;
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
      cache.measurement_window?.complete === false ||
      !!energyIntervalTransport(cache.energy)?.bootstrapNeeded
    );
  }
  showHistoryCache() {
    const id = this.historySelectionId();
    if (id !== this.historySessionId) {
      this.clearHistorySelection();
      this.cancelHistoryInput();
      this.historySessionId = id;
      this.window = null;
      this.selectedDiagnosticTraceAt = null;
      this.selectedDiagnosticTrace = null;
      this.highlightedEventId = null;
      this.pendingEventFocus = null;
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
      latest =
        this.state.session ||
        this.sessions.find(
          (session) => session.session_id === this.historySelectionId(),
        ),
      liveLabel = latest ? this.sessionLabel(latest, timeZone) : "Letzte Sitzung",
      options =
        `<option value="live">${liveLabel}</option>` +
        this.sessions
          .filter((session) => session.session_id !== liveId)
          .map(
            (session) =>
              `<option value="${esc(session.session_id)}">${esc(this.sessionLabel(session, timeZone))}</option>`,
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
    const initial = !!this.historyLoad?.initial;
    node.hidden = !initial;
    node.textContent = initial ? "Wird geladen …" : "";
    this.$("#plots")?.setAttribute?.("aria-busy", String(initial));
  }
  startHistoryLoad(force = false) {
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
    const initialCache = initialId ? this.historyCache(initialId) : null,
      completedSinceLastRead =
        initialCache?.lastReadLive &&
        (liveId !== initialId || !!this.state?.session?.ended_at),
      now = Date.now();
    if (
      !force &&
      !listNeeded &&
      initialCache?.pageRunLoaded &&
      !initialCache.session?.ended_at &&
      !completedSinceLastRead &&
      now - (initialCache.lastFetchedAt || 0) < this.preference("history_live_poll_ms")
    )
      return;
    const load = {
      key,
      count: 0,
      sessionId: initialId,
      initial: !initialCache?.pageRunLoaded && !initialCache?.records.length,
    };
    this.historyLoad = load;
    if (load.initial) this.message(null, "history");
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
              transport = energyIntervalTransport(cache.energy),
              energyAfter =
                transport?.horizon && !transport.bootstrapNeeded
                  ? `&energy_after=${encodeURIComponent(transport.horizon)}`
                  : "",
              page = await this.api(
                `/${entry}/archive?session_id=${encodeURIComponent(id)}&projection=history&after=${after}${energyAfter}`,
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
            if (!more) {
              cache.lastFetchedAt = Date.now();
              cache.lastReadLive = id === liveId && !cache.session?.ended_at;
            }
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
      s.regulation_temperature_position ||
      s.configuration_model?.measurement_positions?.[0] ||
      "upper";
    const measurementHeight = measurementPosition === "lower" ? "unten" : "oben";
    const formatValue = (position, quantity, unit) =>
      `${this.num(value(position, quantity), this.preference("value_precision"))} ${unit}`;
    const energy = latest?.energy;
    const energyText = `${this.num(session ? s.energy_kwh : energy ? energy.measured_kwh + energy.estimated_kwh : 0, this.preference("energy_precision"))} kWh`;
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
        contactor: "Schützrückmeldung",
        unknown: "Rückmeldung fehlt",
      }[s.heating_observation?.source] || "Rückmeldung fehlt";
    const permissions = s.permissions || {};
    const manualMode = s.configuration.control_mode === "manual";
    const modeLocked = !s.control_mode_change_allowed;
    const lightAfterRun = s.light_after_run && stamp(s.light_after_run.ends_at) > now;
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
      if (minutes < step) return `unter ${this.num(step)} Minuten`;
      return `${this.num(Math.max(step, Math.round(minutes / step) * step))} Minuten`;
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
        ? availability.message || ""
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
        ? `<div class="operation-control${lightAfterRun ? " split" : ""}">${lightAfterRun ? `<button class="tile operation stop" data-action="finish-session:${esc(encodeURIComponent(sessionGap.token))}" ${permissions.control ? "" : "disabled"}>Lichtnachlauf beenden</button>` : ""}<button class="tile operation primary" data-action="operation"${this.controlCommandAttributes("operation", !s.operation_enabled)} ${canStart ? "" : "disabled"}>${this.controlCommandLabel("operation", true, "Fortsetzen")}</button></div>`
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
        : `<svg class="dial ${control ? `dial-${key}` : ""}" style="--measurement-color:${color}" viewBox="25 5 250 250" role="${control ? "group" : "img"}" aria-label="${esc(key === "temperature" && control ? "Temperatur und Solltemperatur" : `${caption}: ${this.num(reading, key === "light" ? this.preference("percentage_precision") : this.preference("value_precision"))} ${unit}`)}"><defs><linearGradient id="instrument-face-${key}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--sauna-color-card-background, var(--card-background-color))"/><stop offset="1" stop-color="var(--sauna-color-page-background, var(--primary-background-color))"/></linearGradient><linearGradient id="instrument-rim-${key}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="white" stop-opacity="${this.preference("instrument_rim_highlight_opacity")}"/><stop offset=".5" stop-color="white" stop-opacity="${this.preference("instrument_rim_mid_opacity")}"/><stop offset="1" stop-color="black" stop-opacity="${this.preference("instrument_rim_shade_opacity")}"/></linearGradient></defs><circle class="instrument-face" cx="150" cy="130" r="119" fill="url(#instrument-face-${key})"/><circle class="instrument-rim" cx="150" cy="130" r="119" stroke="url(#instrument-rim-${key})"/><path d="${temperatureDial.path}" fill="none" stroke="var(--sauna-color-border, var(--divider-color))" stroke-width="${this.preference("instrument_value_arc_width_px")}" stroke-linecap="round"/>${Number.isFinite(reading) && reading > minimum ? `<path class="instrument-value-arc" d="${temperatureDial.path}" fill="none" stroke="${valid ? color : this.appearanceColor("status_unknown")}" stroke-width="${this.preference("instrument_value_arc_width_px")}" stroke-linecap="round" pathLength="100" stroke-dasharray="${Math.max(0, Math.min(100, ((reading - minimum) / (maximum - minimum || 1)) * 100))} 100"/>` : ""}${this.temperatureTickMarks({ minimum, maximum })}<rect class="instrument-readout" width="0" height="0" rx="6"/><text class="reading" ${key === "light" ? "data-light-observation" : ""} x="150" y="130" text-anchor="middle" dominant-baseline="central"><tspan>${this.num(reading, key === "light" ? this.preference("percentage_precision") : this.preference("value_precision"))}</tspan><tspan class="reading-unit" dx="5"> ${unit}</tspan></text>${control || (unit === "%" ? '<path class="humidity-symbol" transform="translate(150 210) scale(.58) translate(-150 -126)" d="M150 104 C146 113 135 121 135 132 A15 15 0 0 0 165 132 C165 121 154 113 150 104 Z"/>' : "")}</svg>`;
    const deadlineLabels = { confirmation: "Aufgussbestätigung" };
    const phaseRemaining = (phase) =>
      phase.remaining_seconds ??
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
        this.temperatureControlValue(),
      targetPoint =
        temperatureScale && this.temperatureArcPoint(targetValue, temperatureScale);
    const targetControl = temperatureScale
      ? `${arcBounds ? `<path class="target-temperature-track" data-target-arc="true" d="${temperatureDial.path}" role="slider" tabindex="${permissions.temperature && !this.programRequest ? 0 : -1}" aria-label="${this.temperatureControlsNextGang() ? "Temperatur für den nächsten Gang einstellen" : "Solltemperatur einstellen"}" aria-valuemin="${arcBounds.minimum}" aria-valuemax="${arcBounds.maximum}" aria-valuenow="${Math.max(arcBounds.minimum, Math.min(arcBounds.maximum, targetValue))}" aria-valuetext="${this.temperatureControlsNextGang() ? "Nächster Gang" : "Soll"} ${this.num(targetValue, this.preference("value_precision"))} °C" aria-disabled="${!permissions.temperature || !!this.programRequest}"/>` : ""}<circle class="target-temperature-handle" ${arcBounds ? 'data-target-arc="true"' : 'data-inert-target="true"'} cx="${targetPoint.x}" cy="${targetPoint.y}" r="${this.preference("instrument_handle_radius_px")}"/><text class="target-caption${this.temperatureControlsNextGang() ? " target-caption-next" : ""}" x="150" y="193" text-anchor="middle">${this.temperatureControlsNextGang() ? "NÄCHSTER GANG" : "SOLL"}</text><text class="target-reading" x="150" y="211" text-anchor="middle" dominant-baseline="central">${this.num(targetValue, this.preference("value_precision"))}<tspan class="reading-unit" dx="4">°C</tspan></text>`
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
              return `<button type="button" class="program-named-choice ${feedback ? `program-feedback program-${this.programSaveState}` : ""}" data-action="program-select:${esc(program.id)}" aria-pressed="${program.id === programChoice.id}" aria-label="${esc(program.name)}${feedback ? `: ${feedback}` : ""}" ${programChoiceAvailable ? "" : "disabled"}>${choiceContent(`<span class="program-choice-summary"><span>${esc(program.name)}</span><small>${this.num(program.start_c, 0)} → ${this.num(program.end_c, 0)} °C</small></span><small class="program-choice-details">${esc(this.programSteps(program))}</small>`, feedback)}</button>`;
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
        : `Manuell · ${this.num(light.manual, this.preference("percentage_precision"))} %`;
    const manualControls = this.manualControlAvailability(),
      canManualHeater = manualControls.heater,
      canManualLight = manualControls.brightness;
    const modeControls = modeLocked
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
        ? `${s.configuration.temperature_steps.map((value) => this.num(value, this.preference("value_precision"))).join(" → ")} °C`
        : `${this.num(p.target_temperature_c, this.preference("value_precision"))} → ${this.num(p.final_temperature_c, this.preference("value_precision"))} °C`;
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
      ? `<section class="control-section temperature-automation"><h3>Temperaturwahl</h3>${session ? `<div class="program-current"><div class="program-current-value"><strong class="program-active-label">${esc(activeProgramLabel)}</strong><small>${activeMode === "constant" ? `${this.num(s.target_temperature, this.preference("value_precision"))} °C` : esc(activeProgramSteps)}</small></div><button type="button" data-action="program-toggle" aria-expanded="${programEditorOpen}" aria-controls="program-choice-body" ${programEditorLocked ? "disabled" : ""}>${programEditorOpen ? "Schließen" : "Ändern"}</button></div>` : ""}<div id="program-choice-body" ${programEditorOpen ? "" : "hidden"}>${programTypes}${namedPrograms}${programMode === "individual" ? this.freeProgramForm(programBounds) : ""}${programMode === "constant" ? `<div class="temperature-presets" style="--preset-columns-wide:${equalPresetColumns(presets.length, this.preference("preset_columns_wide"))};--preset-columns-medium:${equalPresetColumns(presets.length, this.preference("preset_columns_medium"))};--preset-columns-narrow:${equalPresetColumns(presets.length, this.preference("preset_columns_narrow"))}">${presets.map((v) => `<button type="button" class="tile" data-action="preset:${v}" aria-pressed="${v === (programChoice.temperature ?? (this.programSelectionDraft?.mode === "constant" ? p.standard_temperature_c : s.target_temperature))}" ${permissions.temperature && (!session || permissions.program) && !programBusy ? "" : "disabled"}>${this.num(v, this.preference("value_precision"))} °C</button>`).join("")}</div>` : ""}${programActionsVisible ? `<div class="program-pending"${this.programDirty() ? "" : ' aria-hidden="true" inert'}><span>Noch nicht übernommen: ${esc(this.programChoiceLabel(programChoice, programs))}</span><button type="button" data-action="program-cancel-draft" ${programBusy ? "disabled" : ""}>Abbrechen</button></div>` : ""}${programActionsVisible ? `<div class="program-actions">${this.programApplyButton(permissions)}</div>` : ""}</div></section>`
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
        overrideLimitMinutes: this.num(p.manual_override_minutes, 0),
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
              .map((value) => this.num(value, this.preference("value_precision")))
              .join(" → ") + " °C"
          : p.final_temperature_c != null
            ? `${this.num(p.target_temperature_c)} → ${this.num(p.final_temperature_c)} °C`
            : "Konstant"
        : "Konstant";
    const power = s.heating_observation?.power_w;
    const powerText =
      power == null
        ? `${this.num(p.nominal_power_kw, this.preference("detail_value_precision"))} kW · Nennleistung`
        : `${this.num(power, 0)} W · gemessen`;
    const detectionText = session
      ? `Erkennung mit: ${(s.detection_channels || []).map((position) => (position === "upper" ? "oberer Messposition" : "unterer Messposition")).join(" und ") || "noch keiner Messposition"}.`
      : "Außerhalb einer Saunasitzung werden keine Türbewegungen ausgewertet.";
    this.updateMarkup(
      "#details",
      this.renderDetailView({
        stateLine,
        timers,
        presence: this.presenceDetails(),
        targetTemperature: this.num(
          s.temperature_control?.current_target,
          this.preference("value_precision"),
        ),
        nextGangTemperature: this.temperatureControlsNextGang()
          ? this.num(
              s.temperature_control?.next_target,
              this.preference("value_precision"),
            )
          : null,
        thermostatTarget: this.num(
          s.thermostat_target,
          this.preference("value_precision"),
        ),
        restartThreshold: this.num(
          s.thermostat_restart_temperature,
          this.preference("value_precision"),
        ),
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
        measurements: (s.configuration_model?.measurement_positions || [])
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
    return `<div class="linear-instrument" style="--measurement-color:${this.appearanceColor(valid ? color : "status_unknown")}"><div class="linear-reading" ${key === "light" ? "data-light-observation" : ""}>${this.num(reading, key === "light" ? this.preference("percentage_precision") : this.preference("value_precision"))} <span>${unit}</span></div><div class="linear-scale"><div class="linear-rail" aria-hidden="true"><i style="width:${fraction}%"></i></div><div class="linear-ticks" aria-hidden="true">${this.temperatureTickValues(
      bounds,
    )
      .map(
        (value, index, ticks) =>
          `<span data-tick-density="${index === 0 || index === ticks.length - 1 ? "edge" : "detail"}" style="left:${((value - bounds.minimum) / (bounds.maximum - bounds.minimum)) * 100}%">${this.num(value)}</span>`,
      )
      .join(
        "",
      )}<span data-tick-density="middle" style="left:50%">${this.num((bounds.minimum + bounds.maximum) / 2)}</span></div>${control}</div><svg class="linear-symbol" viewBox="0 0 24 24" aria-hidden="true">${symbol}</svg></div>`;
  }
  linearTargetControl(value, bounds) {
    if (!bounds) return "";
    const allowed = this.state.permissions?.temperature && !this.programRequest,
      scale = this.appearanceScale("temperature") || bounds;
    return `<div class="instrument-slider"><label for="linear-target-temperature">${this.temperatureControlsNextGang() ? "Nächster Gang" : "Soll"} <output data-linear-target>${this.num(value)} °C</output></label><input id="linear-target-temperature" class="instrument-range" type="range" min="${scale.minimum}" max="${scale.maximum}" step="${this.temperatureStep("temperature_dial_step_c")}" value="${Math.max(bounds.minimum, Math.min(bounds.maximum, value))}" aria-label="${this.temperatureControlsNextGang() ? "Temperatur für den nächsten Gang einstellen" : "Solltemperatur einstellen"}" ${allowed ? "" : "disabled"}></div>`;
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
      arc = `<path class="instrument-arc-track" data-light-arc="true" d="${temperatureDial.path}" role="slider" tabindex="${allowed ? 0 : -1}" aria-label="Lichthelligkeit einstellen" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${sliderValue}" aria-valuetext="${this.num(target, this.preference("percentage_precision"))} %" aria-disabled="${!allowed}"/><circle class="instrument-arc-handle" data-light-arc="true" cx="${point.x}" cy="${point.y}" r="${this.preference("instrument_handle_radius_px")}"/><g class="light-symbol" transform="translate(138 198)" aria-hidden="true">${symbol}</g>`,
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
            ? this.num(metric.value, this.preference("value_precision"))
            : esc(metric.state)
          : "–";
    return `<section class="weather-instrument" aria-label="Umgebungswetter"><div class="weather-heading"><span>Umgebungswetter</span><h2>${esc(environment.station?.name || "Umgebungswetter")}</h2>${environment.measurement_time ? `<time datetime="${esc(environment.measurement_time)}" title="Messzeit">${esc(new Date(environment.measurement_time).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }))}</time>` : ""}</div><div class="weather-condition">${graphic}<span>${esc(label)}</span></div><dl class="weather-values">${metrics.map((metric) => `<div data-weather-quantity="${esc(metric.key)}"><dt>${esc(metric.label)}</dt><dd>${shown(metric)} <span>${esc(metric.unit || "")}</span></dd></div>`).join("")}</dl></section>`;
  }
  fitInstrumentReadouts() {
    for (const dial of this.shadowRoot?.querySelectorAll?.("#current .dial") || []) {
      const bounds = dial.querySelector(".reading")?.getBBox?.();
      const readout = dial.querySelector(".instrument-readout");
      if (!readout || !bounds?.width || !bounds?.height) continue;
      for (const [name, value] of Object.entries({
        x: bounds.x - this.preference("layout_space_13_px"),
        y: bounds.y - this.preference("layout_space_09_px"),
        width: bounds.width + this.preference("layout_space_13_px") * 2,
        height: bounds.height + this.preference("layout_space_09_px") * 2,
      }))
        readout.setAttribute(name, value);
    }
  }
  renderEnvironmentDetails() {
    const environment = this.state?.environment;
    if (!environment?.configured) return "";
    const times = [
        ["Messzeit", environment.measurement_time],
        ["Aktualisiert", environment.updated_at],
      ].filter(([, at]) => at),
      metrics = (environment.values || [])
        .map(
          (metric) =>
            `<div data-weather-quantity="${esc(metric.key)}"><dt>${esc(metric.label)}</dt><dd>${metric.available ? `${Number.isFinite(metric.value) ? this.num(metric.value, this.preference("value_precision")) : esc(metric.value)} ${esc(metric.unit || "")}` : "Nicht verfügbar"}</dd></div>`,
        )
        .join("");
    return `<div class="card" id="detail-environment"><h2>Aktuelle Umgebung</h2>${environment.station?.name ? `<p>${esc(environment.station.name)}</p>` : ""}<p class="muted">${times.length ? times.map(([label, at]) => `${label}: ${esc(this.when(at))}`).join(" · ") : "Mess- und Aktualisierungszeit nicht verfügbar"}</p><dl>${metrics}</dl></div>`;
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
    if (this.state?.configuration_model?.regulation_causes?.length === 0) {
      view = {
        ...view,
        nextGangTemperature: null,
        temperatureProgram: "Konstante Temperaturregelung (manuell)",
      };
    }
    const {
      stateLine,
      timers,
      presence,
      targetTemperature,
      nextGangTemperature,
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
    return `<div class="card hero">${stateLine}${timers}</div><div class="detail-grid"><div class="card"><h2>Ofen</h2><dl><dt>Aktuelle Solltemperatur</dt><dd>${targetTemperature} °C</dd>${nextGangTemperature == null ? "" : `<dt>Solltemperatur des nächsten Gangs</dt><dd>${nextGangTemperature} °C</dd>`}<dt>Obere Regeltemperatur</dt><dd data-readiness>${thermostatTarget} °C</dd><dt>Wiedereinschaltschwelle</dt><dd>${restartThreshold} °C</dd><dt>Temperaturprogramm</dt><dd>${temperatureProgram}</dd><dt>Heizzustand</dt><dd>${heatCaption}</dd><dt>Ermittlung der Heizzeit</dt><dd>${heatSource}</dd><dt>Energieverbrauch</dt><dd>${energyText}</dd><dt>Ofenleistung</dt><dd>${powerText}</dd></dl><p>${decisionText}</p></div><div class="card"><h2>Licht</h2><p class="manual-status ${lightMode}" data-light-status="${lightMode}">${lightStatus}</p><dl><dt>Automatischer Wert</dt><dd>${this.num(lightAutomatic, this.preference("percentage_precision"))} %</dd><dt>Manuelle Vorgabe</dt><dd>${lightManual == null ? "Keine" : `${this.num(lightManual, this.preference("percentage_precision"))} %`}</dd><dt>Übersteuerung</dt><dd>${lightOverrideRemaining}</dd></dl></div><div class="card"><h2>Messung</h2><p data-door-status>${doorText}</p>${measurements}<p class="muted">${detectionText}</p></div>${this.renderEnvironmentDetails()}</div>${presence}${this.renderLiveEvidence()}`;
  }
  renderLiveEvidence() {
    const state = this.state;
    if (!state?.permissions?.admin) return "";
    const model = state.configuration_model;
    const ruleKeys = {
      gang_heat: "gang_heat_demand",
      provisional_door_heat: "temporary_door_heat",
      cooling: "cooling",
    };
    const rules = model
      ? Object.fromEntries(
          (model.regulation_causes || []).flatMap((cause) =>
            ruleKeys[cause]
              ? [[ruleKeys[cause], state.rule_inputs?.[ruleKeys[cause]]]]
              : [],
          ),
        )
      : state.rule_inputs;
    const heatingObservation =
      state.heating_observation &&
      Object.fromEntries(
        Object.entries(state.heating_observation).filter(
          ([key]) => key !== "power_w" || !model || model.power_sensor_configured,
        ),
      );
    const measurements = (state.measurements || []).filter(
      (measurement) =>
        !model || (model.measurement_positions || []).includes(measurement.position),
    );
    const groups = [
      [
        "regulation",
        "Regelung und Schutz",
        {
          decision: state.decision,
          ...(Object.keys(rules || {}).length ? { rule_inputs: rules } : {}),
          protection: state.protection,
          inhibits: state.inhibits,
          faults: state.faults,
          temperature_control: state.temperature_control,
          thermostat: state.session?.thermostat,
        },
      ],
      [
        "inputs",
        "Messquellen und Eingaben",
        {
          measurements,
          measurement_status: state.measurement_status,
          regulation_temperature_position: state.regulation_temperature_position,
          presence: state.presence,
          environment: state.environment,
        },
      ],
      [
        "outputs",
        "Geräteausgabe und Rückmeldungen",
        {
          heater: state.manual_controls?.heater,
          light: state.manual_controls?.light,
          heating_observation: heatingObservation,
          ...(state.light_after_run ? { light_after_run: state.light_after_run } : {}),
        },
      ],
      [
        "session",
        "Sitzung, Energie und Fristen",
        {
          heating: state.session?.heating,
          energy: state.session?.energy,
          after_run: state.session?.after_run,
          after_run_history: state.session?.after_run_history,
          deadlines: state.session?.deadlines,
          start_availability: state.start_availability,
        },
      ],
    ];
    return `<div class="card detail-evidence"><h2>Aktuelle Daten im Detail</h2>${groups.map(([key, label, fields]) => `<details id="detail-${key}"><summary>${label}</summary>${this.detailFields(fields, `live-${key}`)}</details>`).join("")}</div>`;
  }
  presenceDetails() {
    const presence = this.state?.presence;
    const rules = this.state?.rule_inputs;
    const model = this.state?.configuration_model;
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
      return `${occupancy} · ${assertion} · Ereignis ${this.when(item.effective_at)} · empfangen ${this.when(item.received_at)}${reason ? ` · ${reason}` : ""}`;
    };
    const causeLabels = {
      gang_heat: ["Heizanforderung im Saunagang", rules?.gang_heat_demand],
      provisional_door_heat: [
        "Vorläufige Heizanforderung nach Türschluss",
        rules?.temporary_door_heat,
      ],
      cooling: ["Ofenkühlung", rules?.cooling],
    };
    const causes = (model?.regulation_causes || [])
      .filter((cause) => causeLabels[cause])
      .map((cause) => {
        const [label, active] = causeLabels[cause];
        return `<dt>${label}</dt><dd>${active ? "Aktiv" : "Inaktiv"}</dd>`;
      })
      .join("");
    return `<div class="card"><h2>${causes ? "Präsenz und Regelursache" : "Präsenz"}</h2><dl><dt>Personenerkennung</dt><dd>${model?.presence?.direct ? "Präsenzsensor mit Türereignis" : "Temperatur und Feuchte"}</dd><dt>Aktuelle Belegung</dt><dd>${esc(report(presence?.current))}</dd>${causes}</dl></div>`;
  }
  async changeTarget(value) {
    if (!this.state?.permissions?.temperature) return;
    value = this.roundTargetTemperature(value);
    if (this.programRequest) return;
    const nextGang = this.temperatureControlsNextGang();
    if (!nextGang) {
      this.programSelectionDraft = { mode: "constant" };
      this.freeProgramKind = null;
      this.freeProgramStepsDraft = null;
      this.programSaveState = null;
      clearTimeout(this.programSavedTimer);
    }
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
        program_mode: saved?.program_mode || this.state.configuration.program_mode,
        selected_program_id: Object.hasOwn(saved || {}, "selected_program_id")
          ? saved.selected_program_id
          : this.state.configuration.selected_program_id,
        temperature_steps: Object.hasOwn(saved || {}, "temperature_steps")
          ? saved.temperature_steps
          : this.state.configuration.temperature_steps,
        parameters: {
          ...this.state.configuration.parameters,
          ...(saved?.parameters || {}),
        },
      };
      if (saved?.temperature_control) {
        this.state.temperature_control = saved.temperature_control;
        this.state.target_temperature = saved.temperature_control.current_target;
        this.state.next_gang_temperature = saved.temperature_control.next_target;
      }
      this.programRevision = (this.programRevision || 0) + 1;
      if (!nextGang) {
        if (this.programSelectionDraft?.mode === "constant")
          this.programSelectionDraft = null;
        if (this.progressionDraft === draft) this.progressionDraft = null;
        this.programChoiceOpen = false;
      }
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
  temperatureControlsNextGang() {
    return this.state?.temperature_control?.scope === "next_gang";
  }
  temperatureControlValue() {
    return this.state?.temperature_control?.value;
  }
  temperatureDefinition() {
    return this.state?.parameters?.find((d) => d.key === "target_temperature_c");
  }
  frontendStep(key) {
    const value = this.preference(key);
    return Number.isFinite(value) && value > 0 ? value : null;
  }
  lightFeedback() {
    const light = this.state?.manual_controls?.light || {};
    const observation = light.observation;
    if (observation?.available && Number.isFinite(observation.brightness_percent))
      return `Licht ${this.num(observation.brightness_percent, this.preference("percentage_precision"))} %`;
    const target = light.manual ?? light.automatic;
    return Number.isFinite(target)
      ? `Lichtvorgabe ${this.num(target, this.preference("percentage_precision"))} % · Rückmeldung nicht verfügbar`
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
        program.temperature_steps
          .map((value) => this.num(value, this.preference("value_precision")))
          .join(" → ") + " °C"
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
        .map((value) => this.num(value, this.preference("value_precision")))
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
      if (choice.mode === "constant")
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
        }, this.preference("program_saved_feedback_ms"));
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
    const kindButtons = `<div class="program-kind segmented-mode" role="group" aria-label="Temperaturverteilung"><button type="button" data-action="program-kind:even" aria-pressed="${!manual}" ${disabled}>Gleichmäßig</button><button type="button" data-action="program-kind:steps" aria-pressed="${manual}" ${disabled}>Einzelne Stufen</button></div>`;
    const fields = manual
      ? `<label class="field program-count-field" for="free-step-count"><span>Anzahl der Temperaturstufen ${this.distributionInfo("free", steps)}</span><input id="free-step-count" data-free-step-count type="number" min="1" max="${bounds.gangMaximum}" step="1" value="${esc(this.progressionDraft?.["free-step-count"] ?? count)}" ${disabled}></label><div class="program-step-fields">${this.resizeSteps(
          steps,
          count,
        )
          .map(
            (value, index) =>
              `<label class="field" for="free-step-${index}">Stufe ${index + 1}${unitInput(`<input id="free-step-${index}" data-free-step="${index}" type="number" aria-label="Stufe ${index + 1} (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(value)}" ${disabled}>`, "°C")}</label>`,
          )
          .join("")}</div>`
      : `<div class="program-temperature-fields"><label class="field" for="progression-start">Starttemperatur${unitInput(`<input id="progression-start" type="number" aria-label="Starttemperatur (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(values.start)}" ${disabled}>`, "°C")}</label><label class="field" for="progression-end">Endtemperatur${unitInput(`<input id="progression-end" type="number" aria-label="Endtemperatur (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(values.end)}" ${disabled}>`, "°C")}</label></div><label class="field program-count-field" for="progression-gangs"><span>Verteilung auf Saunagänge ${this.distributionInfo("free", distribution)}</span><input id="progression-gangs" type="number" step="1" min="${bounds.gangMinimum}" max="${bounds.gangMaximum}" value="${esc(values.gangs)}" ${disabled}></label>`;
    return `<div class="program-form">${kindButtons}${fields}</div>`;
  }
  distributionInfo(id, steps) {
    const text = steps?.length
      ? `${steps.length} Temperaturstufen: ${steps.map((value) => this.num(value, this.preference("value_precision"))).join(" → ")} °C`
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
      gap = this.preference("info_popup_gap_px"),
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
      x: (temperatureDial.centerX + radius * Math.cos(angle)).toFixed(
        this.preference("chart_coordinate_precision"),
      ),
      y: (temperatureDial.centerY + radius * Math.sin(angle)).toFixed(
        this.preference("chart_coordinate_precision"),
      ),
    };
  }
  temperatureTickValues(bounds = this.appearanceScale("temperature")) {
    return appearanceTickValues(
      bounds,
      this.preference("instrument_tick_target"),
      this.preference("instrument_tick_maximum"),
      this.preference("tick_epsilon"),
      this.preference("tick_precision"),
    );
  }
  temperatureTickMarks(bounds = this.appearanceScale("temperature")) {
    const span = bounds?.maximum - bounds?.minimum;
    const formatTick = (value) =>
      span < 10 ** -this.preference("detail_value_precision")
        ? Number(value).toPrecision(
            this.preference("instrument_tick_significant_digits"),
          )
        : this.num(
            value,
            Math.max(
              0,
              Math.min(
                this.preference("detail_value_precision"),
                this.preference("value_precision") - Math.floor(Math.log10(span)),
              ),
            ),
          );
    const values = this.temperatureTickValues(bounds);
    const minor = values
      .slice(1)
      .flatMap((value, i) =>
        Array.from(
          { length: this.preference("instrument_minor_ticks") },
          (_, index) => index + 1,
        ).map((part) => {
          const position =
            values[i] +
            ((value - values[i]) * part) /
              (this.preference("instrument_minor_ticks") + 1);
          const a = this.temperatureArcPoint(
            position,
            bounds,
            temperatureDial.radius - this.preference("instrument_tick_inset_px"),
          );
          const b = this.temperatureArcPoint(
            position,
            bounds,
            temperatureDial.radius -
              this.preference("instrument_tick_inset_px") -
              this.preference("instrument_minor_tick_length_px"),
          );
          return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="var(--measurement-color)" opacity="${this.preference("instrument_minor_tick_opacity")}" stroke-width="${this.preference("stroke_1_0_ratio")}"/>`;
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
              temperatureDial.radius - this.preference("instrument_tick_inset_px"),
            ),
            label = this.temperatureArcPoint(
              value,
              bounds,
              temperatureDial.radius -
                this.preference("instrument_tick_label_inset_px"),
            );
          return `<line x1="${tick.x}" y1="${tick.y}" x2="${this.temperatureArcPoint(value, bounds, temperatureDial.radius - this.preference("instrument_tick_inset_px") - this.preference("instrument_major_tick_length_px")).x}" y2="${this.temperatureArcPoint(value, bounds, temperatureDial.radius - this.preference("instrument_tick_inset_px") - this.preference("instrument_major_tick_length_px")).y}" stroke="var(--sauna-card-muted-text, var(--sauna-color-muted-text, var(--secondary-text-color)))" stroke-width="${this.preference("stroke_2_0_ratio")}"/><text class="tick" x="${label.x}" y="${Number(label.y) + this.preference("font_size_caption_px") / 3}" text-anchor="middle">${formatTick(value)}</text>`;
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
    track.setAttribute(
      "aria-valuetext",
      `${this.num(value, this.preference("value_precision"))} °C`,
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
    const current = this.clampArcTemperature(this.temperatureControlValue(), bounds),
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
    for (
      let attempt = 0;
      attempt < this.preference("settings_reload_attempts");
      attempt++
    ) {
      try {
        const state = await this.api(this.stateRequestPath(entry, false));
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
      await new Promise((resolve) =>
        setTimeout(resolve, this.preference("settings_reload_interval_ms")),
      );
    }
    if (!loaded)
      throw Error(
        "Parameter gespeichert, Neuladen noch nicht abgeschlossen. Bitte Status prüfen.",
      );
  }
  sessionLabel(session, timeZone = localTimeZone()) {
    const start = session.started_at || session.timeline?.session_started_at;
    if (!start) return "Ohne Startzeit";
    const date = new Intl.DateTimeFormat("de-DE", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      timeZone,
    }).format(new Date(start));
    const end = session.ended_at;
    const running =
      !end &&
      this.state?.operation_enabled &&
      session.timeline?.session_id === this.state?.session?.timeline?.session_id;
    const sameDay =
      end &&
      new Intl.DateTimeFormat("de-DE", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        timeZone,
      }).format(new Date(end)) === date;
    return `${date} · ${this.clock(start, timeZone)}${end ? ` – ${sameDay ? this.clock(end, timeZone) : this.when(end, timeZone)}` : running ? " · laufend" : " · unterbrochen"}`;
  }
  historyTitle(session) {
    if (this.selected === "live")
      return this.state.operation_enabled && this.state.session
        ? "Laufende Sitzung"
        : "Letzte Sitzung";
    const archive = this.sessions?.find((item) => item.session_id === this.selected);
    const started = session?.timeline?.session_started_at || archive?.started_at;
    return started
      ? this.sessionLabel({ ...archive, ...session, started_at: started })
      : "Archivierte Sitzung";
  }
  drawHistory(reason = "viewport") {
    this.scheduleHistoryRender(reason);
  }
  clearHistoryDisplay() {
    this.cancelHistoryInput();
    this.historyChart?.destroy();
    this.historyChart = null;
    this.$(".history-tooltip-events")?.remove();
    const readout = this.$("#tooltip");
    if (readout) {
      readout.replaceChildren();
      readout.hidden = true;
      readout.setAttribute("data-phase", "");
    }
    this.$(".history-inspection-heading .history-tooltip-time")?.remove();
    this.updateMarkup("[data-history-session-summary]", "");
    this.updateMarkup("[data-history-context-gangs]", "");
    const sessionSummary = this.$("[data-history-session-summary]");
    if (sessionSummary) sessionSummary.hidden = true;
    const eventInspection = this.$("#history-event-inspection");
    if (eventInspection) {
      eventInspection.hidden = true;
      eventInspection.textContent = "";
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
  historyFaultEvents(records = this.shown?.records || []) {
    let previous = {},
      count = 0;
    const events = [];
    for (const [index, record] of records.entries()) {
      if (record.kind !== "diagnostic") continue;
      const payload = record.payload || {},
        faults = payload.faults || {};
      const messages = Object.keys(faults).map((key, i) => [
        key,
        payload.messages?.[i] || key,
      ]);
      const labels = Object.fromEntries(messages);
      for (const [key, value] of Object.entries(faults)) {
        if (Object.hasOwn(previous, key) && previous[key].value === value) continue;
        const changed = Object.hasOwn(previous, key);
        if (!changed) count++;
        events.push({
          id: `fault-${record.id ?? index}-${key}`,
          at: stamp(record.received_at),
          label: changed ? "Fehlerstatus geändert" : "Fehler aufgetreten",
          detail: `${labels[key]} · Pfad: ${this.detailFieldLabel(key)}`,
          source: "Fehler",
          error: true,
          status: value,
          key,
        });
      }
      for (const [key, value] of Object.entries(previous)) {
        if (Object.hasOwn(faults, key)) continue;
        events.push({
          id: `resolved-${record.id ?? index}-${key}`,
          at: stamp(record.received_at),
          label: "Entwarnung",
          detail: `${value.label} · Pfad: ${this.detailFieldLabel(key)}`,
          source: "Fehler",
          resolved: true,
          key,
        });
      }
      previous = Object.fromEntries(
        Object.entries(faults).map(([key, value]) => [
          key,
          { value, label: labels[key] },
        ]),
      );
    }
    return { count, events };
  }
  historyFaultMarkup(records, prefix = "history", showEmpty = true) {
    if (this.state?.permissions?.admin === false) return "";
    const faults = this.historyFaultEvents(records);
    if (!faults.events.length)
      return showEmpty
        ? '<p class="muted" data-fault-count="0">Keine Fehler aufgezeichnet.</p>'
        : "";
    return `<details id="${prefix}-faults"><summary>Fehler im Sitzungsverlauf · <span data-fault-count="${faults.count}">${faults.count}</span></summary><ol class="detail-event-list">${faults.events.map((event) => `<li data-record-id="${esc(event.id)}"><time>${this.clock(event.at)}</time> <strong>${esc(event.label)}</strong> · ${esc(event.detail)}${event.status ? ` <span class="muted">(${esc(this.detailValue(event.status))})</span>` : ""}</li>`).join("")}</ol></details>`;
  }
  historyLightValue(record) {
    if (record.payload.service === "turn_off") return "Aus";
    return record.payload.brightness_pct == null
      ? "Helligkeit nicht aufgezeichnet"
      : `${this.num(record.payload.brightness_pct, this.preference("percentage_precision"))} %`;
  }
  historyLightActivity(records) {
    const result = [];
    let group = null;
    const intervals = this.shown?.phase_projection?.intervals || [];
    const value = (record) => this.historyLightValue(record);
    const boundary = (reason, at) => {
      if (group) {
        group.boundary = reason;
        group.boundaryAt = at;
      }
      group = null;
    };
    const ordered = records
      .map((record, index) => ({
        record,
        index,
        at: stamp(
          record.kind === "light_command"
            ? record.payload.sent_at || record.received_at
            : record.payload?.change?.at || record.received_at,
        ),
      }))
      .sort((left, right) => left.at - right.at || left.index - right.index);
    for (const { index, record, at } of ordered) {
      if (
        [
          "phase",
          "manual_light",
          "manual_heater",
          "manual_phase_end",
          "manual_session_light_end",
          "interruption",
        ].includes(record.kind)
      ) {
        if (record.kind === "manual_light" && record.payload?.change?.reason === "keep")
          continue;
        boundary(
          record.kind === "phase" ? "Phasenwechsel" : "Bedienung oder Unterbrechung",
          at,
        );
        continue;
      }
      if (record.kind !== "light_command") continue;
      const payload = record.payload || {};
      const phaseIndex = intervals.findIndex(
          (phase) => stamp(phase.started_at) <= at && at < stamp(phase.ended_at),
        ),
        phase = intervals[phaseIndex],
        key = JSON.stringify([
          phase ? [phase.started_at, phase.phase, phase.source_id] : null,
          payload.phase,
          payload.purpose,
        ]);
      const isolated =
        payload.service_error ||
        payload.service === "turn_off" ||
        payload.phase === "light_reassignment";
      if (isolated || group?.key !== key)
        boundary(payload.service_error ? "Dienstfehler" : "Neuer Lichtabschnitt", at);
      if (!group) {
        group = {
          id: `light-${record.id ?? index}`,
          at,
          effective: payload.sent_at || record.received_at,
          source: "Licht",
          key,
          records: [],
          first: record,
          last: record,
          anchorIndex: index,
          error: !!payload.service_error,
          phaseInterval: phase,
          phaseEndedAt:
            phase &&
            phase.complete !== false &&
            (phaseIndex < intervals.length - 1 || this.shown?.session?.ended_at)
              ? stamp(phase.ended_at)
              : null,
          label: payload.service_error
            ? "Lichtbefehl fehlgeschlagen"
            : payload.service === "turn_off"
              ? "Lichtbefehl Aus"
              : `Licht · ${phases[payload.phase] || { session_light: "Lichtnachlauf", manual: "Manuelle Wahl", button_press: "Tasterrückmeldung", button_hold: "Tasterrückmeldung", light_reassignment: "Gerätewechsel" }[payload.phase] || payload.phase || "Ausgabe"}`,
        };
        result.push(group);
      }
      // A late service completion may precede the first known command in
      // send time. Keep the identity of the earliest received group member.
      if (index < group.anchorIndex) {
        group.id = `light-${record.id ?? index}`;
        group.anchorIndex = index;
      }
      group.records.push(record);
      group.last = record;
      if (isolated)
        boundary(
          payload.service_error
            ? "Dienstfehler"
            : payload.service === "turn_off"
              ? "AUS-Befehl"
              : "Einzelbefehl",
          at,
        );
    }
    return result.map((item) => {
      const phase = item.phaseInterval,
        changes = item.records.filter(
          (record, index) =>
            index &&
            (record.payload.brightness_pct !==
              item.records[index - 1].payload.brightness_pct ||
              record.payload.ends_at !== item.records[index - 1].payload.ends_at),
        ),
        context = phase
          ? `Heizphase ${phases[phase.phase] || phase.phase} ab ${this.clock(phase.started_at)}${phase.complete === false ? " · unvollständiger Phasenbeleg" : ""}`
          : "Heizphase nicht aufgezeichnet",
        ending = Number.isFinite(item.phaseEndedAt)
          ? `Phasenende ${this.clock(item.phaseEndedAt)}`
          : "kein Abschluss belegt (Heizphase)",
        boundary = Number.isFinite(item.boundaryAt)
          ? ` · Befehlsabschnitt bis ${this.clock(item.boundaryAt)}: ${item.boundary}`
          : "";
      return {
        ...item,
        adjustments: changes,
        detail: `${context} · ${ending} · Beginn der Ausgabe ${this.clock(item.at)}: ${value(item.first)} · letzter belegter Wert (Vorgabe) ${this.clock(item.last.payload.sent_at || item.last.received_at)}: ${value(item.last)} · ${changes.length} Anpassungen · ${item.records.length} Befehle${item.error ? ` · Dienstfehler: ${item.first.payload.service_error}` : " · Dienst abgeschlossen; Rückmeldung separat"}${boundary}`,
      };
    });
  }
  detailValue(value) {
    if (value == null) return "Nicht aufgezeichnet";
    if (value === true) return "Ja";
    if (value === false) return "Nein";
    if (typeof value === "number")
      return Number.isFinite(value)
        ? this.num(value, this.preference("detail_value_precision"))
        : "Nicht verfügbar";
    if (Array.isArray(value))
      return value.length
        ? value.map((item) => this.detailValue(item)).join(" · ")
        : "Keine";
    if (typeof value === "object")
      return Object.entries(value)
        .map(
          ([key, item]) => `${this.detailFieldLabel(key)}: ${this.detailValue(item)}`,
        )
        .join(" · ");
    if (/^\d{4}-\d\d-\d\dT/.test(value)) return this.when(value);
    return (
      {
        on: "Ein",
        off: "Aus",
        unknown: "Unbekannt",
        unavailable: "Nicht verfügbar",
        pending: "Wird geprüft",
        confirmed: "Bestätigt",
        present: "Anwesend",
        absent: "Abwesend",
        automatic: "Automatik",
        manual: "Manuell",
        measured: "Gemessen",
        estimated: "Geschätzt",
        mixed: "Messung und Berechnung",
        incomplete: "Unvollständig",
        direct_presence: "Direkte Präsenz",
        provisional_proxy: "Vorläufiger indirekter Hinweis",
        proxy_retraction: "Indirekter Hinweis aufgehoben",
        power: "Leistungsmessung",
        contactor: "Schützrückmeldung",
        turn_on: "Einschalten",
        turn_off: "Ausschalten",
        feedback_off: "Bestätigt Aus",
        feedback_unknown: "Heizrückmeldung unbekannt",
      }[value] || String(value)
    );
  }
  detailFieldLabel(key) {
    return (
      this.state?.parameters?.find((definition) => definition.key === key)?.label ||
      {
        value: "Wert",
        state: "Zustand",
        source: "Quelle",
        role: "Rolle",
        attributes: "Quellenattribute",
        raw_value: "Originalwert",
        received_at: "Empfangen",
        measured_at: "Gemessen",
        sent_at: "Gesendet",
        completed_at: "Dienst abgeschlossen",
        planned_at: "Geplant",
        effective_at: "Wirksam seit",
        created_at: "Entschieden",
        at: "Zeit",
        started_at: "Beginn",
        ended_at: "Ende",
        ends_at: "Geplantes Ende",
        due_at: "Fällig",
        purpose: "Zweck",
        heat: "Heizanforderung",
        heating: "Heizung",
        reason: "Grund",
        decision: "Entscheidung",
        power_w: "Leistung (W)",
        mean_w: "Gemessene mittlere Leistung (W)",
        peak_w: "Gemessene höchste Leistung (W)",
        available: "Verfügbar",
        phase: "Phase",
        service: "Dienst",
        service_error: "Dienstfehler",
        brightness_pct: "Helligkeit (%)",
        transition_seconds: "Übergang (s)",
        upper: "Oben",
        lower: "Unten",
        temperature: "Temperatur",
        humidity: "Luftfeuchte",
        position: "Messposition",
        quantity: "Messgröße",
        occupancy: "Belegung",
        assertion: "Art der Präsenzmeldung",
        confidence: "Sicherheit",
        event_id: "Ereigniskennung",
        report_id: "Meldungskennung",
        session_id: "Sitzungskennung",
        gang_id: "Gangkennung",
        source_ref: "Quellenbezug",
        trace_at: "Prüfzeit",
        kind: "Art",
        status: "Status",
        channels: "Messpositionen",
        payload: "Daten",
        event: "Ereignis",
        measured_kwh: "Gemessene Energie (kWh)",
        estimated_kwh: "Geschätzte Energie (kWh)",
        measured_seconds: "Messdauer (s)",
        estimated_seconds: "Geschätzte Dauer (s)",
        unknown_seconds: "Dauer ohne Beleg (s)",
        elapsed_seconds: "Vergangene Dauer (s)",
        accounted_at: "Abgerechnet bis",
        intervals: "Intervalle",
        reported_heating: "Rückgemeldete Heizung",
        off_since: "Aus seit",
        last_reset_at: "Letztes Zurücksetzen",
        end_reason: "Endgrund",
        upper_temperature: "Temperatur oben",
        lower_temperature: "Temperatur unten",
        upper_humidity: "Luftfeuchte oben",
        lower_humidity: "Luftfeuchte unten",
        heater_power: "Leistungsmessung",
        heater_feedback: "Unabhängige Heizrückmeldung",
        regulation_temperature_unavailable: "Regeltemperatur",
        heater_feedback_unavailable: "Schützrückmeldung",
        heater_feedback_mismatch: "Schaltbestätigung",
        heater_service_unavailable: "Ansteuerung des Heizschützes",
        heater_still_heating: "Heizabschaltung",
        heater_no_power: "Heizleistung",
        archive: "Sitzungsarchiv",
        operation_light: "Saunalicht im Betrieb",
        after_run_light: "Saunalicht bei Ofenkühlung",
        session_light: "Lichtnachlauf",
        start_rejected: "Startvoraussetzungen",
        demand: "Thermostatanforderung",
        cooldown_until: "Heizpause bis",
        protection: "Schutzabschaltungen",
        inhibits: "Startsperren",
        faults: "Fehlerzustände",
        messages: "Fehlermeldungen",
        gang_heat_demand: "Gang fordert Heizung",
        temporary_door_heat: "Türhilfe",
        cooling: "Ofenkühlung",
        checks: "Aktive Prüfungen",
        conditions: "Bedingungen",
        holds: "Prüfpunkte",
        signals: "Signale",
        metrics: "Erkennungswerte",
        automatic: "Automatischer Wert",
        manual: "Manuelle Vorgabe",
        observation: "Rückmeldung",
        override_ends_at: "Übersteuerung bis",
        command: "Befehl",
        command_pending: "Befehl ausstehend",
        target: "Sollwert",
        mode: "Betriebsart",
        current_target: "Aktueller Sollwert",
        next_target: "Nächster Sollwert",
        confirmation: "Bestätigung",
        confirmation_at: "Bestätigt am",
        duration_seconds: "Dauer (s)",
        credited_seconds: "Angerechnete Dauer (s)",
        pending_start: "Beginn ausstehend",
        paused_at: "Pausiert seit",
        phase_id: "Phasenkennung",
        token: "Identität",
        name: "Name",
        unit_of_measurement: "Einheit",
        brightness: "Helligkeit (0–255)",
        illuminance: "Beleuchtungsstärke",
        last_changed: "Geändert",
        last_updated: "Aktualisiert",
        last_reported: "Gemeldet",
        session_start_snapshot: "Anfangswert",
        operation_enabled: "Betrieb eingeschaltet",
        operation_off_at: "Betrieb ausgeschaltet",
        ready_at: "Bereit seit",
        temperature_base_c: "Temperaturbasis (°C)",
        target_history: "Solltemperaturverlauf",
        target_c: "Wirksame Solltemperatur (°C)",
        temperature_program_mode: "Temperaturprogramm",
        temperature_program_steps: "Programmstufen (°C)",
        active_gang_temperature_c: "Sollwert aktiver Gang (°C)",
        next_gang_temperature_c: "Nächster Sollwert (°C)",
        next_gang_temperature_blocked_by: "Nächste Stufe gesperrt durch",
        current: "Aktuell",
        external: "Externe Meldungen",
        configured_source: "Gewählte Präsenzquelle",
        effective_source: "Wirksame Präsenzquelle",
        external_snapshot: "Präsenzmeldungen beim Start",
      }[key] ||
      key.replaceAll("_", " ")
    );
  }
  detailFields(value, key = "data") {
    if (!value || typeof value !== "object")
      return `<p>${esc(this.detailValue(value))}</p>`;
    if (!Object.keys(value).length) return '<p class="muted">Keine Einträge.</p>';
    return `<dl class="detail-data">${Object.entries(value)
      .map(([field, item]) => {
        const identity = `${key}:${field}`;
        const label = Array.isArray(value)
          ? `Eintrag ${Number(field) + 1}`
          : this.detailFieldLabel(field);
        const nested = item && typeof item === "object" && Object.keys(item).length;
        return `<div data-detail-key="${esc(identity)}"><dt>${esc(label)}</dt><dd>${nested ? `<details id="detail-${esc(identity)}"><summary>${Array.isArray(item) ? item.length : Object.keys(item).length} ${Array.isArray(item) ? "Einträge" : "Werte"}</summary>${this.detailFields(item, identity)}</details>` : esc(this.detailValue(item))}</dd></div>`;
      })
      .join("")}</dl>`;
  }
  historyManualActivity(records) {
    const projected = new Map(),
      active = { manual_light: null, manual_heater: null },
      reasons = {
        selection: "Neue Auswahl",
        explicit: "Explizite Rückgabe",
        timeout: "Fristablauf",
        phase_change: "Phasenwechsel",
      },
      chosen = (value) =>
        value === null
          ? "Automatik"
          : value === true
            ? "Ein"
            : value === false
              ? "Aus"
              : typeof value === "number"
                ? `${this.num(value, this.preference("percentage_precision"))} %`
                : String(value),
      close = (kind, at, reason) => {
        const item = active[kind];
        if (item && at >= item.at) {
          item.endedAt = at;
          item.endReason = reason;
        }
        active[kind] = null;
      },
      ordered = records
        .map((record, index) => ({
          record,
          index,
          at: stamp(
            record.kind === "decision"
              ? record.payload?.at
              : record.payload?.change?.at || record.received_at,
          ),
        }))
        .filter(
          ({ record, at }) =>
            Number.isFinite(at) &&
            ["manual_light", "manual_heater", "decision"].includes(record.kind),
        )
        .sort((left, right) => left.at - right.at || left.index - right.index);
    for (const { record, at } of ordered) {
      const payload = record.payload || {},
        kind = record.kind;
      if (kind === "decision") {
        // Only an original manual_override decision identifies the automatic
        // heater override. Thermostat changes in manual mode do not end a choice.
        if (active.manual_heater?.override && payload.reason !== "manual_override")
          close("manual_heater", at, `Regelentscheidung: ${payload.reason}`);
        continue;
      }
      const change = kind === "manual_light" ? payload.change : null,
        reason = change?.reason,
        value = change ? change.value : payload.value,
        item = {
          at,
          value,
          rawValue: payload.value,
          change,
          endsPreviousOnly: payload.ends_previous_only === true,
          adjustments: [],
        };
      projected.set(record, item);
      if (reason === "keep") {
        if (active.manual_light)
          active.manual_light.adjustments.push({ at, text: "Planfrist aufgehoben" });
        item.label = "Lichtwahl · Planfrist aufgehoben";
        item.note = `Weiterhin ${chosen(value)} · kein Ende der manuellen Wahl`;
        continue;
      }
      if (kind === "manual_light" && change) {
        if (change.previous_value !== null) close(kind, at, reasons[reason] || reason);
        else active[kind] = null;
        if (payload.ends_previous_only === true) {
          item.label = "Lichtwahl beendet";
          item.note = `${chosen(change.previous_value)} · Ende ${this.clock(at)} · Ersetzt in anderem Sitzungskontext`;
          continue;
        }
        if (reason !== "selection") {
          item.label = "Lichtwahl beendet";
          item.note = `${chosen(change.previous_value)} · Ende ${this.clock(at)} · ${reasons[reason] || reason}${change.previous_ends_at ? ` · vorherige Planfrist ${this.clock(change.previous_ends_at)}` : ""}`;
          continue;
        }
        item.plannedEndsAt = change.ends_at;
      } else if (kind === "manual_heater") {
        close(kind, at, value === null ? "Explizite Rückgabe" : "Neue Auswahl");
      } else {
        // Legacy light records contain the input, not the owner's end. A
        // later input cannot repair a missing implicit handover retroactively.
        active[kind] = null;
      }
      if (value === null) {
        item.note = "Automatik gewählt";
        continue;
      }
      item.override =
        kind === "manual_heater" && payload.decision?.reason === "manual_override";
      active[kind] = item;
    }
    for (const item of projected.values()) {
      item.detail =
        item.note ||
        `${chosen(item.value)} · Beginn ${this.clock(item.at)} · ${Number.isFinite(item.endedAt) ? `Ende ${this.clock(item.endedAt)}: ${item.endReason}` : "kein Gültigkeitsende belegt"}${item.plannedEndsAt ? ` · Planfrist ${this.clock(item.plannedEndsAt)}` : ""}${item.adjustments.map((change) => ` · ${this.clock(change.at)}: ${change.text}`).join("")}`;
      if (
        !item.endsPreviousOnly &&
        item.change?.reason === "selection" &&
        item.rawValue !== item.value
      )
        item.detail += ` · Bedienwert ${chosen(item.rawValue)}`;
    }
    return projected;
  }
  historyAdditionalActivity(records) {
    const definitions = {
      manual_light: ["Bedienung", "Lichtwahl"],
      manual_heater: ["Bedienung", "Ofenwahl"],
      heater_override_rejected: ["Bedienung", "Ofenwahl abgelehnt"],
      manual_phase_end: ["Bedienung", "Ofenkühlung beendet"],
      manual_session_light_end: ["Bedienung", "Lichtnachlauf beendet"],
      interruption: ["Betrieb", "Sitzung unterbrochen"],
      notice: ["Hinweis", "Betriebshinweis"],
      consumer_event: ["Regelung", "Zustandsereignis"],
      presence_source: ["Präsenz", "Präsenzquelle"],
      heating_observation: ["Heizrückmeldung", "Heizbeobachtung"],
    };
    const manual = this.historyManualActivity(records);
    let programSignature = null;
    return records.flatMap((record, index) => {
      if (record.kind === "session") {
        const payload = record.payload || {};
        const program = Object.fromEntries(
          Object.entries(payload).filter(([key]) => key.includes("temperature")),
        );
        const signature = JSON.stringify(program);
        if (signature === programSignature) return [];
        programSignature = signature;
        return [
          {
            id: `program-${record.id ?? index}`,
            at: stamp(record.received_at),
            source: "Temperaturprogramm",
            label: "Temperaturvorgaben",
            detail: this.detailValue(program),
            record,
          },
        ];
      }
      const definition = definitions[record.kind];
      if (!definition) return [];
      const payload = record.payload || {};
      if (
        record.kind === "consumer_event" &&
        payload.kind === "occupancy" &&
        payload.presence
      ) {
        const source = this.historyPresenceProjection().sourceForRecord(record),
          direct = payload.presence.assertion === "direct_presence";
        if (source !== (direct ? "ha_presence" : "proxy")) return [];
      }
      const detailPayload =
        record.kind === "presence_source"
          ? Object.fromEntries(
              Object.entries(payload).filter(([key]) => key !== "external_snapshot"),
            )
          : payload;
      const intervention = manual.get(record);
      const selection = Object.hasOwn(payload, "value")
        ? payload.value === null
          ? "Automatik"
          : payload.value === false
            ? "Aus"
            : payload.value === true
              ? "Ein"
              : typeof payload.value === "number"
                ? `${this.num(payload.value, this.preference("percentage_precision"))} %`
                : String(payload.value)
        : null;
      return [
        {
          id: `record-${record.id ?? index}`,
          at: intervention?.at ?? stamp(record.received_at),
          source: definition[0],
          label: intervention?.label || definition[1],
          detail: intervention?.detail || selection || this.detailValue(detailPayload),
          intervention,
          error: record.kind === "heater_override_rejected",
          record,
        },
      ];
    });
  }
  historyValueSeries(records) {
    let cache = this.detailValueCache;
    if (cache?.records !== records || cache.count > records.length) cache = null;
    if (cache?.count === records.length) return [...cache.series.values()];
    const series = cache?.series || new Map();
    const number = (value) =>
      value == null ||
      value === "" ||
      typeof value === "boolean" ||
      !Number.isFinite(Number(value))
        ? null
        : Number(value);
    const add = (key, label, unit, role, record, value, at = record.received_at) => {
      const time = stamp(at);
      if (!Number.isFinite(time)) return;
      if (!series.has(key)) series.set(key, { key, label, unit, role, values: [] });
      series.get(key).values.push({ time, value, source: record });
    };
    for (const record of records.slice(cache?.count || 0)) {
      const p = record.payload || {};
      if (["measurement", "source_snapshot"].includes(record.kind)) {
        const temperature = p.quantity === "temperature";
        add(
          `${p.position}:${p.quantity}`,
          `${temperature ? "Temperatur" : "Luftfeuchte"} ${p.position === "upper" ? "oben" : "unten"}`,
          temperature ? "°C" : "%",
          temperature ? "temperature" : "humidity",
          record,
          number(p.value),
          p.received_at || record.received_at,
        );
      } else if (record.kind === "source_state" && p.role === "heater_power") {
        add(
          "power",
          "Gemessene Leistung",
          "W",
          "temperature",
          record,
          number(record.display?.power_w),
        );
      } else if (record.kind === "source_state" && p.role === "light") {
        add(
          "light-observed",
          "Lichtrückmeldung",
          "%",
          "humidity",
          record,
          p.state === "off"
            ? 0
            : p.state === "on" && number(p.attributes?.brightness) != null
              ? (number(p.attributes.brightness) * 100) / 255
              : null,
        );
      } else if (record.kind === "light_command") {
        add(
          "light-command",
          "Lichtvorgabe · abgeschlossene Dienste",
          "%",
          "humidity",
          record,
          p.service_error
            ? null
            : p.service === "turn_off"
              ? 0
              : number(p.brightness_pct),
          p.completed_at || record.received_at,
        );
      } else if (record.kind === "illuminance") {
        const unit = p.attributes?.unit_of_measurement || "Einheit unbekannt";
        add(
          `illuminance:${unit}`,
          "Beleuchtungsstärke",
          unit,
          "humidity",
          record,
          number(p.state),
        );
      } else if (record.kind === "session") {
        for (const [key, label] of [
          ["temperature_base_c", "Temperaturbasis"],
          ["active_gang_temperature_c", "Sollwert des aktiven Gangs"],
          ["next_gang_temperature_c", "Sollwert des nächsten Gangs"],
        ]) {
          if (Object.hasOwn(p, key))
            add(key, label, "°C", "temperature", record, number(p[key]));
        }
      }
    }
    for (const item of series.values()) item.values.sort((a, b) => a.time - b.time);
    this.detailValueCache = { records, count: records.length, series };
    return [...series.values()];
  }
  renderHistoryValues(records, start, end, cursor) {
    const series = this.historyValueSeries(records);
    const rows = series
      .map((item) => {
        const point = item.values.filter((value) => value.time <= cursor).at(-1);
        return `<tr data-detail-key="value-${esc(item.key)}"><th>${esc(item.label)}</th><td>${point?.value == null ? "Nicht verfügbar" : `${this.num(point.value, this.preference("detail_value_precision"))} ${esc(item.unit)}`}</td><td>${point ? this.clock(point.time) : "Nicht aufgezeichnet"}</td><td>${esc(point?.source.payload.source || (point?.source.kind === "light_command" ? "Geräteausgabe" : point?.source.kind === "session" ? "Sitzungszustand" : "Quelle nicht aufgezeichnet"))}</td></tr>`;
      })
      .join("");
    const plots = series
      .map((item) => {
        const points = item.values.filter(
          (point) => point.time >= start && point.time <= end,
        );
        const values = points.map((point) => point.value).filter(Number.isFinite);
        if (!values.length)
          return `<section class="detail-value-plot" data-detail-key="plot-${esc(item.key)}"><h3>${esc(item.label)}</h3><p class="muted">Keine gültigen Werte im Ausschnitt.</p></section>`;
        const [minimum, maximum] = values.reduce(
          ([low, high], value) => [Math.min(low, value), Math.max(high, value)],
          [Infinity, -Infinity],
        );
        const range = maximum - minimum || 1;
        const width = this.preference("detail_plot_width_px"),
          height = this.preference("detail_plot_height_px"),
          left = this.preference("detail_plot_side_padding_px"),
          right = width - this.preference("detail_plot_right_padding_px"),
          top = this.preference("detail_plot_top_padding_px"),
          bottom = height - this.preference("detail_plot_bottom_padding_px"),
          axisTop = this.preference("detail_axis_top_padding_px"),
          axisBottom = height - this.preference("detail_axis_bottom_padding_px"),
          valueLabelTop = this.preference("detail_value_label_top_padding_px"),
          timeLabel = height - this.preference("detail_time_label_bottom_padding_px"),
          x = (time) => left + ((right - left) * (time - start)) / (end - start),
          y = (value) => bottom - ((bottom - top) * (value - minimum)) / range;
        let segments = [],
          segment = [];
        for (const point of points) {
          if (point.value == null) {
            if (segment.length) segments.push(segment);
            segment = [];
          } else segment.push(point);
        }
        if (segment.length) segments.push(segment);
        const path = segments
          .map((values) =>
            reduceHistorySegment(values, x)
              .map(
                (point, index) =>
                  `${index ? "L" : "M"}${x(point.time).toFixed(this.preference("chart_coordinate_precision"))},${y(point.value).toFixed(this.preference("chart_coordinate_precision"))}`,
              )
              .join(" "),
          )
          .join(" ");
        const single = segments
          .filter((values) => values.length === 1)
          .map(
            ([point]) =>
              `<circle cx="${x(point.time)}" cy="${y(point.value)}" r="${this.preference("detail_single_point_radius_px")}"/>`,
          )
          .join("");
        return `<section class="detail-value-plot" data-detail-key="plot-${esc(item.key)}"><h3>${esc(item.label)} <small>${esc(item.unit)}</small></h3><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(item.label)} · ${this.clock(start)} bis ${this.clock(end)}"><g fill="currentColor" stroke="currentColor" style="color:var(--sauna-color-series-${item.role})"><path d="${path}" fill="none" stroke-width="${this.preference("detail_curve_width_px")}"/>${single}</g><line x1="${x(cursor)}" x2="${x(cursor)}" y1="${axisTop}" y2="${axisBottom}" stroke="currentColor" stroke-dasharray="${this.preference("guide_dash_length_px")} ${this.preference("guide_dash_gap_px")}"/><text x="0" y="${valueLabelTop}">${this.num(maximum, this.preference("value_precision"))}</text><text x="0" y="${axisBottom}">${this.num(minimum, this.preference("value_precision"))}</text><text x="${left}" y="${timeLabel}">${this.clock(start)}</text><text x="${right}" y="${timeLabel}" text-anchor="end">${this.clock(end)}</text></svg></section>`;
      })
      .join("");
    return `<div class="card" id="control-values"><h2>Messwerte und Lichtverlauf</h2><div class="detail-value-grid">${plots || '<p class="muted">Keine Messwerte aufgezeichnet.</p>'}</div><div class="scroll"><table><thead><tr><th>Wert am gewählten Zeitpunkt</th><th>Letzter Messwert</th><th>Messmeldung</th><th>Quelle</th></tr></thead><tbody>${rows}</tbody></table></div><p class="muted">Gezeigt werden aufgezeichnete Werte. Fehlende Rückmeldungen bleiben unbekannt; die Heizbeobachtung ist keine lückenlose Leistungsmessung. Lichtvorgabe und Rückmeldung sind getrennt.</p></div>`;
  }
  historyRecordLabel(kind) {
    return (
      {
        session: "Sitzungszustand",
        measurement: "Messung",
        source_snapshot: "Anfangsmessung",
        source_state: "Quellenzustand",
        illuminance: "Beleuchtungsstärke",
        heating_observation: "Heizbeobachtung",
        detector_trace: "Erkennungsprüfung",
        detection: "Erkennung",
        diagnostic: "Fehlerzustand",
        decision: "Heizentscheidung",
        command: "Ofenausgabe",
        light_command: "Lichtausgabe",
        consumer_event: "Verbraucherereignis",
        presence: "Präsenzmeldung",
        presence_source: "Präsenzquelle",
        phase: "Betriebsphase",
        notice: "Hinweis",
        manual_light: "Lichtwahl",
        manual_heater: "Ofenwahl",
        heater_override_rejected: "Ofenwahl abgelehnt",
        manual_phase_end: "Ofenkühlung beendet",
        manual_session_light_end: "Lichtnachlauf beendet",
        interruption: "Unterbrechung",
      }[kind] || this.detailFieldLabel(kind)
    );
  }
  renderHistoryRecords(records) {
    const size = this.preference("detail_records_per_page"),
      pages = Math.max(1, Math.ceil(records.length / size));
    const page = Math.max(0, Math.min(pages - 1, this.detailRecordPage || 0));
    const slice = records.slice(page * size, (page + 1) * size);
    return `<div class="card"><details id="detail-records"><summary>Einzelbelege · ${records.length} Datensätze</summary><p class="muted">Archivfelder nach Empfangsreihenfolge. Sitzungszustände sind kompakte Auszüge; der Archivexport enthält die vollständigen Originale. Messungen, Regelentscheidungen, Erkennungsprüfungen, Bedienung und Ausgaben bleiben einzeln prüfbar.</p><div class="row"><button data-action="detail-record-page:${page - 1}" ${page ? "" : "disabled"}>Zurück</button><span>Seite ${page + 1} von ${pages}</span><button data-action="detail-record-page:${page + 1}" ${page + 1 < pages ? "" : "disabled"}>Weiter</button></div><div class="detail-record-list">${slice.map((record, index) => `<details data-record-id="${record.id ?? page * size + index}"><summary>${this.clock(record.received_at)} · ${esc(this.historyRecordLabel(record.kind))}${record.projection ? " · Auszug" : ""}</summary>${this.detailFields(record.payload, `record-${record.id ?? index}`)}</details>`).join("")}</div></details></div>`;
  }
  renderSessionEvidence(session) {
    return `<div class="card" id="control-session-evidence">${this.renderSessionEvidenceContents(session)}</div>`;
  }
  renderHeatingCycles(session) {
    if (!Array.isArray(session.heating?.intervals))
      return '<p class="muted">Heizzyklen: Originale Heizintervalle nicht aufgezeichnet.</p>';
    const selection =
      this.historyValueSelection?.identity === this.historyValueIdentity()
        ? this.historyValueSelection
        : null;
    if (selection?.kind === "point")
      return '<p class="muted">Heizzyklen: Für eine mittlere Länge ist ein Zeitraum erforderlich.</p>';
    const start = selection?.start ?? stamp(session.timeline.session_started_at),
      end = selection?.end ?? stamp(session.ended_at || this.state.now),
      intervals = (session.heating?.intervals || []).filter((interval) => {
        const left = stamp(interval.started_at),
          right = stamp(interval.ended_at);
        return (
          Number.isFinite(left) &&
          left < end &&
          (!Number.isFinite(right) || right > start || left >= start)
        );
      }),
      complete = intervals.filter((interval) => {
        const left = stamp(interval.started_at),
          right = stamp(interval.ended_at);
        return (
          left >= start &&
          Number.isFinite(right) &&
          right >= left &&
          right <= end &&
          interval.end_reason === "feedback_off"
        );
      }),
      excluded = intervals.filter((interval) => !complete.includes(interval)),
      open = excluded.filter((interval) => !interval.ended_at).length,
      unknown = excluded.filter(
        (interval) => interval.ended_at && interval.end_reason !== "feedback_off",
      ).length,
      clipped = excluded.length - open - unknown,
      mean = complete.length
        ? complete.reduce(
            (total, interval) =>
              total + stamp(interval.ended_at) - stamp(interval.started_at),
            0,
          ) /
          complete.length /
          60000
        : null;
    return `<section id="detail-heating-cycles"><h3>Heizzyklen · ${selection ? "Ausgewählter Zeitraum" : "Sitzung"}</h3><p>${complete.length} vollständig belegte Ein-/Aus-Intervalle · Mittlere Länge: ${mean === null ? "Kein vollständig belegtes Intervall" : `${this.num(mean, this.preference("detail_value_precision"))} min`}</p><p class="muted">${this.clock(start)} – ${this.clock(end)} · Nicht im Mittel: ${open} offen, ${clipped} angeschnitten, ${unknown} ohne bestätigtes Aus beendet.</p></section>`;
  }
  renderSessionEvidenceContents(session) {
    const measuredPower = this.historyInformationContent()?.measuredPower;
    const groups = [
      [
        "energy",
        "Heizzeit und Energie",
        {
          Heizzeit: session.heating,
          Energie: session.energy,
          Gemessene_Leistung: {
            started_at: session.timeline.session_started_at,
            ended_at: session.energy?.accounted_at,
            mean_w: measuredPower?.mean_w ?? null,
            peak_w: measuredPower?.peak_w ?? null,
            measured_seconds: measuredPower?.measured_seconds ?? null,
            Messbezug: measuredPower
              ? measuredPower.complete
                ? "Gesamter Zeitraum"
                : "Nur gemessene Abschnitte"
              : "Keine auswertbaren Messintervalle",
          },
          Schützverlauf: session.contactor_history,
        },
      ],
      [
        "cooling",
        "Kühlung und Fristen",
        {
          Aktuelle_Ofenkühlung: session.after_run,
          Kühlphasen: session.after_run_history,
          Fristen: session.deadlines,
        },
      ],
      [
        "temperature",
        "Temperaturprogramm",
        Object.fromEntries(
          Object.entries(session).filter(
            ([key]) =>
              key.includes("temperature") ||
              key === "thermostat" ||
              key === "target_history",
          ),
        ),
      ],
      [
        "recognition",
        "Gänge und Präsenz",
        {
          Bestätigte_Gänge: session.timeline.completed,
          Aktiver_Gang: session.timeline.active,
          Aufgehobene_Erkennungen: session.timeline.retracted,
        },
      ],
      ["configuration", "Einstellungen dieser Sitzung", session.configuration || null],
    ];
    return `<h2>Sitzungsdaten</h2><p class="muted">Gespeicherter Sitzungsstand. Nicht aufgezeichnete frühere Einstellungen und Zwischenzustände lassen sich daraus nicht rekonstruieren.</p>${groups.map(([key, label, value]) => `<details id="session-evidence-${key}"><summary>${label}</summary>${key === "energy" ? this.renderHeatingCycles(session) : ""}${this.detailFields(value, `session-${key}`)}</details>`).join("")}`;
  }
  renderControlEvents(activity, selected) {
    const size = this.preference("detail_events_per_page"),
      pages = Math.max(1, Math.ceil(activity.length / size));
    const page = Math.max(0, Math.min(pages - 1, this.detailEventPage || 0));
    const shown = activity.slice(page * size, (page + 1) * size);
    const hiddenErrors = activity.filter(
      (event) => event.error && !shown.includes(event),
    );
    const faults = `<div class="card">${this.historyFaultMarkup(this.shown?.records || [], "control")}${hiddenErrors.map((event) => `<p data-record-id="error-${esc(event.id)}"><strong>${this.clock(event.at)} · ${esc(event.label)}</strong> · ${esc(event.detail)}</p>`).join("")}</div><div class="row"><button data-action="detail-event-page:${page - 1}" ${page ? "" : "disabled"}>Frühere Ereignisse</button><span>Ereignisse · Seite ${page + 1} von ${pages}</span><button data-action="detail-event-page:${page + 1}" ${page + 1 < pages ? "" : "disabled"}>Spätere Ereignisse</button></div>`;
    activity = shown;
    return `${faults}<div class="card control-history-events"><h2>Bedienung, Regelung und Ausgaben</h2><div class="scroll"><table><thead><tr><th>Uhrzeit</th><th>Quelle</th><th>Ereignis</th><th>Status / Grund</th></tr></thead><tbody>${activity.map((event, index) => `<tr data-record-id="${esc(event.id || event.ref || `activity-${index}`)}" data-selected="${event === selected}" ${event.error ? 'data-error="true"' : ""} ${event.records ? 'data-light-section="true"' : ""}><td><button data-control-time="${event.at}">${this.clock(event.at)}</button></td><td>${esc(event.source)}</td><td>${esc(event.label)}</td><td>${esc(event.detail)}${event.records?.length ? `<details id="commands-${esc(event.id)}"><summary>${event.records.length} Einzelbefehle und Anpassungen</summary>${event.records.map((record, i) => `<p data-record-id="light-command-${record.id ?? i}">Gesendet ${this.clock(record.payload.sent_at || record.received_at)} · Vorgabe ${esc(this.historyLightValue(record))}${record.payload.ends_at ? ` · Planfrist ${this.clock(record.payload.ends_at)}` : ""} · ${record.payload.service_error ? `Dienstfehler: ${esc(record.payload.service_error)}` : `Dienst abgeschlossen ${this.clock(record.payload.completed_at || record.received_at)}; Rückmeldung separat`}</p>`).join("")}</details>` : ""}</td></tr>`).join("")}</tbody></table></div></div>`;
  }
  renderControlHistory() {
    const { session, records, phase_projection: projection } = this.shown;
    const timeline = session.timeline;
    const start = stamp(timeline.session_started_at);
    const latest = Math.max(
      start + this.preference("history_minimum_span_ms"),
      stamp(session.ended_at || this.state.now),
    );
    if (this.controlHistorySession !== timeline.session_id) {
      this.controlHistorySession = timeline.session_id;
      this.controlHistoryCursor = null;
      this.controlHistoryEnd = latest;
      this.detailRecordPage = 0;
      this.detailEventPage = 0;
    }
    const end = this.controlHistoryEnd ?? latest;
    const cursor = Math.max(start, Math.min(end, this.controlHistoryCursor ?? end));
    const renderKey = JSON.stringify([
      timeline,
      session.ended_at,
      session.contactor_history,
      (projection?.intervals || []).map((item) => [
        item.phase,
        item.started_at,
        Math.min(end, stamp(item.ended_at) || end),
      ]),
      this.state.phase,
      this.historyConfigurationModel(),
      end,
      cursor,
      latest > end,
      this.detailRecordPage || 0,
      this.detailEventPage || 0,
      this.historyValueSelection,
      this.appearanceValue(),
    ]);
    const retained = this.controlHistoryRenderCache;
    const evidenceTick = Math.floor(
      (stamp(this.state.now) || 0) / this.preference("history_live_poll_ms"),
    );
    if (
      retained?.records === records &&
      retained.count === records.length &&
      retained.key === renderKey &&
      (!this.shadowRoot || this.$("#control-state-history"))
    ) {
      if (this.shadowRoot && this.controlEvidenceTick !== evidenceTick) {
        // Counters may change without an archive record. Refresh just their
        // retained detail card at the archive cadence, leaving plots alone.
        this.updateMarkup(
          "#control-session-evidence",
          this.renderSessionEvidenceContents(session),
        );
        this.controlEvidenceTick = evidenceTick;
      }
      return;
    }
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
    const presenceProjection = this.historyPresenceProjection(),
      presenceReports = this.historyPresenceReports(start, end);
    const occupancy = (r) =>
      !r
        ? "Unbekannt"
        : r.assertion === "proxy_retraction"
          ? "Hinweis aufgehoben"
          : !r.available
            ? "Nicht verfügbar"
            : { present: "Anwesend", absent: "Abwesend" }[r.occupancy] || "Unbekannt";
    const decisionRecords = records.filter((r) => r.kind === "decision");
    const decisions = decisionRecords.map((r) => r.payload);
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
      coolingChanges.push(coolingState(this.state.phase, latest));
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
        "Präsenz",
        changes(
          presenceReports.map(({ at, report }) => ({
            at,
            label: occupancy(report),
            color: report?.available
              ? report.occupancy === "present"
                ? color("phase-ready")
                : report.occupancy === "absent"
                  ? neutral
                  : null
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
            at: stamp(d.at),
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
    const observations = records.filter(
      (record) => record.kind === "heating_observation",
    );
    if (observations.length)
      lanes.splice(5, 0, [
        "Heizrückmeldung",
        changes(
          observations.map((record) => ({
            at: stamp(record.received_at),
            label:
              record.payload.heating === true
                ? "Heizt"
                : record.payload.heating === false
                  ? "Heizt nicht"
                  : "Unbekannt",
            color:
              record.payload.heating == null
                ? null
                : record.payload.heating
                  ? color("ui-heater-on")
                  : neutral,
          })),
        ),
      ]);
    const activity = [
      ...records
        .filter((r) => r.kind === "command")
        .map((r) => ({
          id: `command-${r.id ?? r.received_at}`,
          at: stamp(r.received_at),
          effective: r.payload.sent_at || r.received_at,
          label: `Ofenbefehl ${r.payload.heat ? "Ein" : "Aus"}`,
          source: "Geräteausgabe",
          error: !!r.payload.service_error,
          detail: r.payload.service_error
            ? `Dienstfehler: ${r.payload.service_error}`
            : "Dienst abgeschlossen; Rückmeldung separat",
        })),
      ...this.historyLightActivity(records),
      ...this.historyAdditionalActivity(records),
      ...this.historyFaultEvents(records).events,
      ...(timeline.processed || [])
        .filter(
          (event) =>
            !["person_strong", "person_weak"].includes(event.kind) ||
            presenceProjection.sourceForEvent(event) === "proxy" ||
            !presenceProjection.hasSourceInformation,
        )
        .map((e) => ({
          id: e.event_id,
          at: stamp(e.detected_at),
          effective: e.effective_at,
          label: events[e.kind] || e.kind,
          source: "Erkennung",
          detail:
            {
              door_open: "Türöffnung erkannt",
              door_close: "Türschluss erkannt",
              presence_confirmed: "Türvorgang abgeschlossen · Präsenz belegt",
              presence_ended: "Austrittsöffnung · Abwesenheit und Durchlüften belegt",
              infusion: "Feuchteanstieg erkannt",
            }[e.kind] ||
            events[e.kind] ||
            e.kind,
          ref: e.event_id,
        })),
      ...presenceReports
        .filter((item) => item.report)
        .map(({ at, report }) => ({
          id: `${report.report_id || "presence"}:${at}`,
          at,
          effective: report.effective_at,
          label: occupancy(report),
          source:
            report.assertion === "direct_presence"
              ? "Präsenzsensor"
              : "Indirekte Präsenz",
          detail: report.source,
        })),
      ...decisions.map((d, index) => ({
        id: `decision-${decisionRecords[index].id ?? index}`,
        at: stamp(d.at),
        effective: d.at,
        label: `Heizanforderung ${d.heat ? "Ein" : "Aus"}`,
        source: "Steuerung",
        detail: `${cause(d.reason)}${d.created_at && d.created_at !== d.at ? ` · Entschieden ${this.clock(d.created_at)}` : ""}`,
      })),
      ...(session.contactor_history || []).map((m) => ({
        id: `contactor-${m.at}`,
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
    const signature = (spans) => {
      const values = [];
      for (const span of spans) {
        const status = span.label.startsWith("Ein")
          ? "Ein"
          : span.label.startsWith("Aus")
            ? "Aus"
            : "Unbekannt";
        if (values.at(-1)?.status !== status) values.push({ at: span.at, status });
      }
      return JSON.stringify(values);
    };
    const sameHeaterEvidence = signature(lanes[3][1]) === signature(lanes[4][1]);
    const combinedLanes = sameHeaterEvidence
      ? lanes
          .filter((_, index) => index !== 4)
          .map((lane, index) =>
            index === 3 ? ["Anforderung / Schütz", lane[1]] : lane,
          )
      : lanes;
    const laneMarkup = combinedLanes
      .map(
        ([name, spans]) =>
          `<div class="control-track" data-detail-key="lane-${esc(name)}"><span>${esc(name)}</span><div class="control-track-bar">${spans.map((v) => `<button type="button" data-control-time="${v.at}" style="left:${pct(v.at)}%;width:${pct(v.end) - pct(v.at)}%;${v.color ? `background:${v.color}` : "background:transparent"}" title="${esc(`${name}: ${v.label} · ${this.clock(v.at)} – ${this.clock(v.end)}`)}" aria-label="${esc(`${name}: ${v.label} ab ${this.clock(v.at)}`)}"></button>`).join("")}<i style="left:${pct(cursor)}%"></i></div></div>`,
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
      `<div class="card" id="control-state-history"><div class="row"><h2>Betriebsverlauf</h2><span class="muted">${this.clock(start)} – ${this.clock(end)}</span><button data-action="detail-follow" ${latest > end ? "" : "disabled"}>Bis zum neuesten Stand</button></div><div class="control-history-lanes">${laneMarkup}<div class="control-time-axis">${[0, 0.25, 0.5, 0.75, 1].map((f) => `<span>${this.clock(start + (end - start) * f)}</span>`).join("")}</div><div class="control-cursor-row"><input id="control-history-cursor" class="control-cursor" type="range" min="0" max="${end - start}" step="1" value="${cursor - start}" aria-label="Zeitpunkt im Betriebsverlauf" aria-valuetext="${this.clock(cursor)}"></div></div><p class="muted">Schraffiert: kein belegter Zustand. Regelgründe zeigen die gespeicherte priorisierte Entscheidung.${sameHeaterEvidence ? " Anforderung und Schütz haben hier denselben Verlauf." : " Anforderung und Schütz sind getrennte Belege."}</p><div class="control-history-inspection"><div><h3>Status · ${this.clock(cursor)}</h3><dl>${stateMarkup}</dl></div><div><h3>Letztes Ereignis${selected ? ` · ${this.clock(selected.at)}` : ""}</h3>${selected ? `<p>${esc(selected.label)}</p><p class="muted">${esc(selected.detail)}</p>` : '<p class="muted">Noch kein Ereignis.</p>'}</div></div></div>${this.renderHistoryValues(records, start, end, cursor)}${this.renderControlEvents(activity, selected)}${this.renderSessionEvidence(session)}${this.renderHistoryRecords(records)}`,
    );
    this.controlHistoryRenderCache = { records, count: records.length, key: renderKey };
    this.controlEvidenceTick = evidenceTick;
  }
  historyGangRows(selection = null) {
    const session = this.shown?.session;
    if (!session) return [];
    const timeline = session.timeline,
      gangs = [...timeline.completed, ...(timeline.active ? [timeline.active] : [])];
    return gangs.flatMap((gang, index) => {
      const originalStarted = stamp(gang.started_at),
        originalEnded = stamp(gang.ended_at),
        horizon = stamp(gang.ended_at || session.ended_at || this.state.now),
        open = !gang.ended_at && !session.ended_at,
        ongoing =
          open &&
          gang === timeline.active &&
          this.state?.operation_enabled &&
          timeline.session_id === this.state?.session?.timeline?.session_id,
        range = selection?.kind === "range",
        started = range ? Math.max(originalStarted, selection.start) : originalStarted,
        ended = range ? Math.min(horizon, selection.end) : horizon;
      if (
        selection?.kind === "point" &&
        !(
          started <= selection.start &&
          (selection.start < ended || (open && selection.start === ended))
        )
      )
        return [];
      if (range && !(ended > started)) return [];
      const complete =
          Number.isFinite(originalEnded) &&
          (!range ||
            (selection.start <= originalStarted && selection.end >= originalEnded)),
        coversObserved =
          !range || (selection.start <= originalStarted && selection.end >= horizon),
        scope = complete
          ? "Vollständig"
          : ongoing && coversObserved
            ? "Laufend"
            : "Ausschnitt",
        selectedDuration = duration((ended - started) / 1000);
      return [
        {
          gang,
          number: index + 1,
          originalStarted,
          originalEnded,
          started,
          ended,
          label: `Saunagang ${index + 1} · ${scope}`,
          confirmationText: gangConfirmed(gang) ? "Bestätigt" : "Vorläufig",
          durationText:
            range && ongoing ? `${selectedDuration} ausgewählt` : selectedDuration,
        },
      ];
    });
  }
  historyInformationContent(selection = null, phase = null) {
    const session = this.shown?.session;
    if (!session) return null;
    const rows = this.historyGangRows(selection),
      range = selection?.kind === "range",
      singleGang = range && phase?.phase === "saunagang" && rows.length === 1,
      start = selection ? selection.start : stamp(session.timeline.session_started_at),
      end = selection ? selection.end : stamp(session.ended_at || this.state.now),
      label = !selection
        ? "Sitzung"
        : singleGang
          ? rows[0].label
          : range
            ? phases[phase?.phase] || "Ausgewählter Zeitraum"
            : phases[phase?.phase] || "Punkt",
      energy = !selection
        ? session.energy
        : range && selection.identity === this.historyValueIdentity()
          ? energyForHistoryRange(session.energy, start, end, selection.identity)
          : null,
      measuredEnergy = !selection
        ? energyForHistoryRange(
            session.energy,
            session.timeline.session_started_at,
            session.energy?.accounted_at,
            this.historyValueIdentity(),
          )
        : energy;
    return {
      session,
      selection,
      rows,
      singleGang,
      start,
      end,
      label,
      completed: !!session.ended_at,
      durationSeconds: (end - start) / 1000,
      gangCount: session.timeline.completed.filter(gangConfirmed).length,
      measurements: this.historyMeasurementContent(selection, true),
      energy,
      measuredPower: measuredEnergy?.measured_power || null,
    };
  }
  historyInformationHeading(
    selection,
    phase,
    timeZone,
    content = this.historyInformationContent(selection, phase),
  ) {
    if (!content) return "";
    const times =
        selection?.kind === "point"
          ? this.clock(content.start, timeZone)
          : `${this.clock(content.start, timeZone)} – ${this.clock(content.end, timeZone)}`,
      durationText = !selection
        ? duration(content.durationSeconds)
        : content.singleGang
          ? content.rows[0].durationText
          : "";
    return `${content.label} · ${times}${durationText ? ` · ${durationText}` : ""}`;
  }
  syncHistoryInformation(
    selection = null,
    phase = null,
    overview = false,
    content = this.historyInformationContent(selection, phase),
  ) {
    if (!content) return;
    const { rows, singleGang, gangCount, energy, measuredPower, measurements } =
        content,
      range = selection?.kind === "range",
      summary = this.$("[data-history-session-summary]");
    if (summary) summary.hidden = !(overview || range);
    if (overview || range) {
      const valueRow = (
          key,
          label,
          value,
          unit = "",
          reference = "",
          status = "Nicht verfügbar",
        ) =>
          `<div data-key="history-information-${esc(key)}"${key === "energy" ? " data-history-energy" : ""}><span data-history-label>${esc(label)}</span>${Number.isFinite(value) ? `<span class="history-tooltip-value"><span class="history-tooltip-number">${this.historyReadoutNumber(value)}</span><span class="history-tooltip-unit">${esc(unit)}</span></span>${reference ? `<span class="history-information-reference">${reference}</span>` : ""}` : `<span class="history-information-status">${esc(status)}</span>`}</div>`,
        energyValue =
          energy &&
          Number.isFinite(energy.measured_kwh) &&
          Number.isFinite(energy.estimated_kwh) &&
          !(
            energy.unknown_seconds > 0 &&
            !(energy.measured_seconds > 0 || energy.estimated_seconds > 0)
          )
            ? energy.measured_kwh + energy.estimated_kwh
            : null,
        energyReference = overview
          ? energy?.accounted_at
            ? `bis ${this.clock(energy.accounted_at)}`
            : "Zeitstand nicht aufgezeichnet"
          : `${this.clock(content.start)} – ${this.clock(content.end)}`,
        maximumRows = !range
          ? ""
          : [...measurements]
              .filter(([, measurement]) => measurement.quantity === "temperature")
              .map(([key, measurement]) =>
                valueRow(
                  `maximum-${key}`,
                  `Temperaturmaximum${this.historyDetail ? ` ${measurement.position === "upper" ? "oben" : "unten"}` : ""}`,
                  measurement.maximum?.value,
                  "°C",
                  measurement.maximum
                    ? `<time title="${esc(this.tooltipWhen(measurement.maximum.time))}">um ${this.clock(measurement.maximum.time)}</time>`
                    : "",
                  "Keine gültige Temperatur im Zeitraum",
                ),
              )
              .join(""),
        powerEnd = overview ? content.session.energy?.accounted_at : content.end,
        powerDetails = measuredPower
          ? `<p>Gemessene Leistung · ${this.clock(content.start)} – ${powerEnd ? this.clock(powerEnd) : "Ende nicht aufgezeichnet"}${measuredPower.complete ? "" : " · Nur gemessene Abschnitte"}</p><div class="history-information-values">${valueRow("power-mean", "Mittlere gemessene Leistung", measuredPower.mean_w, "W")}${valueRow("power-peak", "Höchste gemessene Leistung", measuredPower.peak_w, "W")}</div>`
          : '<p class="muted">Gemessene Leistung: Nicht verfügbar</p>';
      this.updateMarkup(
        "[data-history-session-summary]",
        `<div class="history-information-values">${overview ? `<div data-key="history-information-gang-count"><span data-history-label>Saunagänge</span><span class="history-tooltip-value"><span class="history-tooltip-number">${gangCount}</span><span class="history-tooltip-unit"></span></span></div>` : ""}${maximumRows}${valueRow("energy", "Energieverbrauch", energyValue, "kWh", energyReference, energy ? "Nicht verfügbar" : this.historyLoad ? "Nachladend" : "Für diesen Zeitraum nicht belegt")}</div><details id="history-information-details"><summary>Weitere Daten · ${overview ? "Sitzung" : "Ausgewählter Zeitraum"}</summary><p class="muted">${this.clock(content.start)} – ${this.clock(content.end)}${overview && energy?.accounted_at ? ` · Energie bis ${this.clock(energy.accounted_at)}` : ""}</p>${powerDetails}</details>`,
      );
    }
    this.updateMarkup(
      "[data-history-context-gangs]",
      overview || !selection || singleGang
        ? ""
        : rows
            .map(
              ({ gang, label, started, ended, durationText, confirmationText }) =>
                `<div class="history-context-gang" data-key="history-context-gang:${esc(gang.gang_id)}" data-gang-id="${esc(gang.gang_id)}"><strong>${esc(label)}</strong><span>${this.clock(started)} – ${this.clock(ended)}</span><span>${esc(durationText)}</span><span>${esc(confirmationText)}</span></div>`,
            )
            .join(""),
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
    this.syncHistoryValueIdentity();
    if (this.historyChart?.identity !== identity) {
      if (this.historyChart) this.clearHistorySelection();
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
    const selection =
        this.navigation?.main === "history" ? this.historyValueSelection : null,
      selectionKey = selection
        ? `${selection.identity}:${selection.kind}:${selection.start}:${selection.end}`
        : "",
      selectionLabel = selection
        ? selection.kind === "range"
          ? `${this.clock(selection.start)} – ${this.clock(selection.end)}`
          : this.clock(selection.start)
        : "",
      gangNow = t.active ? this.state.now : "";
    const gangKey = `${this.navigation?.main}:${identity}:${this.historyTimelineRevision}:${gangNow}:${timeZone}:${selectionKey}`;
    if (this.historyGangKey !== gangKey) {
      this.historyGangKey = gangKey;
      if (this.navigation?.main === "history") this.updateMarkup("#gangs", "");
      else {
        const information = this.historyInformationContent(selection),
          e = information.energy,
          energySummary =
            e && !selection
              ? `<p class="muted" data-history-energy>Energieverbrauch: ${this.num(e.measured_kwh + e.estimated_kwh, this.preference("energy_precision"))} kWh</p>`
              : "",
          point = selection?.kind === "point",
          gangRows = information.rows.map((row) =>
            point ? { ...row, started: selection.start, ended: selection.start } : row,
          );
        this.updateMarkup(
          "#gangs",
          `<div class="card"><h2>Saunagänge${selection ? ` · ${esc(selectionLabel)}` : ""}</h2>${energySummary}${gangRows.length ? `<div class="scroll"><table><thead><tr><th>Gang</th><th>Status</th>${point ? "<th>Zeitpunkt</th>" : `<th>${selection ? "Von" : "Beginn"}</th><th>Dauer</th><th>${selection ? "Bis" : "Ende"}</th>`}</tr></thead><tbody>${gangRows.map(({ gang: g, number, started, ended, confirmationText }) => `<tr data-gang-id="${esc(g.gang_id)}" data-start="${esc(g.started_at)}"><td>${number}</td><td>${confirmationText}</td><td>${this.clock(started)}</td>${point ? "" : `<td>${duration((ended - started) / 1000)}</td><td>${selection || g.ended_at ? this.clock(ended) : "–"}</td>`}</tr>`).join("")}</tbody></table></div>` : '<p class="muted">Keine Saunagänge erkannt.</p>'}</div>`,
        );
      }
    }
    const eventKey = `${identity}:${this.historyProjection()}:${this.historyTimelineRevision}:${this.historyEventRevision || 0}:${timeZone}:${selectionKey}:${this.historyEventVisibilityKey()}`;
    if (this.historyEventKey !== eventKey) {
      this.historyEventKey = eventKey;
      const openEvents = [
        ...this.shadowRoot.querySelectorAll("#event-list details"),
      ].map((d) => d.open);
      const traces = this.diagnosticData().traces;
      const eventRows =
        this.navigation?.main === "history"
          ? this.historyMainEvents(undefined, selection)
          : orderedHistoryEvents(this.normalHistoryEvents());
      const eventGroups = [];
      for (const event of eventRows) {
        const at = stamp(event.effective_at);
        const minute = Number.isFinite(at)
          ? Math.floor(at / this.preference("history_event_group_interval_ms"))
          : null;
        let group = eventGroups.at(-1);
        if (!group || group.minute !== minute) {
          group = {
            minute,
            label: minute == null ? "Ohne Zeit" : this.clock(at, timeZone),
            events: [],
          };
          eventGroups.push(group);
        }
        group.events.push(event);
      }
      this.updateMarkup(
        "#event-list",
        `<div class="card history-event-card"><details><summary>Ereignisse (${eventRows.length})${selection ? ` · ${esc(selectionLabel)}` : ""}</summary><ol class="history-event-groups">${eventGroups
          .map(
            (group) =>
              `<li class="history-event-group"><div class="history-event-time">${esc(group.label)}</div><ol class="history-event-items">${group.events
                .map((e) => {
                  const linked = e.display_only
                      ? null
                      : this.diagnosticTraceForEvent(traces, e),
                    label = this.historyEventLabel(e);
                  return `<li class="event-row" data-event-id="${esc(e.event_id)}" data-selected="false"><span>${esc(label)}</span>${linked && this.state?.permissions?.admin ? `<button data-action="event-row:${esc(e.event_id)}" aria-label="${esc(label)} im Erkennungsverlauf zeigen">Zum Marker</button>` : ""}</li>`;
                })
                .join("")}</ol></li>`,
          )
          .join(
            "",
          )}</ol>${!selection && t.retracted.length ? `<p class="muted">${t.retracted.length} vorläufige Erkennung(en) aufgehoben. Diese werden nicht als Gänge gezählt.</p>` : ""}</details>${selection ? "" : this.historyFaultMarkup(records, "history", this.navigation?.main !== "history")}</div>`,
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
  historyPresenceProjection() {
    const session = this.shown?.session,
      records = this.shown?.records || (this.emptyHistoryRecords ??= []),
      live =
        session?.timeline?.session_id &&
        session.timeline.session_id === this.state?.session?.timeline.session_id &&
        !session.ended_at,
      model =
        session?.configuration_model || (live ? this.state?.configuration_model : null),
      configuration =
        session?.configuration || (live ? this.state?.configuration : null),
      source = (value) => (["proxy", "ha_presence"].includes(value) ? value : null),
      fallback =
        source(model?.presence?.source) ||
        (typeof model?.presence?.direct === "boolean"
          ? model.presence.direct
            ? "ha_presence"
            : "proxy"
          : null) ||
        source(configuration?.presence_source),
      cached = this.historyPresenceCache;
    if (
      cached?.session === session &&
      cached.records === records &&
      cached.count === records.length &&
      cached.fallback === fallback
    )
      return cached.projection;
    const markers = records
      .map((record, order) => ({ record, order }))
      .filter(({ record }) => record.kind === "presence_source")
      .map(({ record, order }) => ({
        order,
        at: stamp(record.received_at),
        source: source(record.payload?.effective_source),
        initial: !record.payload?.previous_source,
      }))
      .filter((marker) => Number.isFinite(marker.at) && marker.source)
      .sort((a, b) => a.at - b.at);
    // The initial archive anchor describes the owner already in effect when
    // the session began; later markers apply at their recorded receipt time.
    const initial = markers[0]?.initial ? markers[0].source : fallback,
      changes = [];
    let previous = initial;
    for (const marker of markers) {
      if (marker.source === previous) continue;
      changes.push(marker);
      previous = marker.source;
    }
    // A report can precede a source boundary with the same receipt time.
    // Preserve archive order and bind person events by their original IDs.
    const changesByRecord = new Map(
        changes.map((change) => [records[change.order], change]),
      ),
      recordOwners = new Map(),
      eventOwners = new Map();
    let owner = { source: initial, order: -1 };
    for (const record of records) {
      const change = changesByRecord.get(record);
      if (change) owner = { source: change.source, order: change.order };
      recordOwners.set(record, owner);
      const payload = record.payload || {},
        report = record.kind === "presence" ? payload : payload.presence,
        eventId = record.kind === "detection" ? payload.event?.event_id : null;
      if (eventId) eventOwners.set(eventId, owner.source);
      else if (
        report?.assertion === "provisional_proxy" &&
        report.source_ref &&
        !eventOwners.has(report.source_ref)
      )
        eventOwners.set(report.source_ref, owner.source);
    }
    const boundaryAt = (at) => {
      const time = stamp(at);
      for (let index = changes.length - 1; index >= 0; index--)
        if (changes[index].at <= time) return changes[index];
      return null;
    };
    const sourceAt = (at) => {
      return boundaryAt(at)?.source ?? initial;
    };
    const intervalAt = (at) => {
      return boundaryAt(at)?.at ?? -Infinity;
    };
    const intervals = (start, end) => {
      const spans = [];
      let at = start,
        active = sourceAt(start),
        order = boundaryAt(start)?.order ?? -1;
      for (const change of changes) {
        if (change.at <= start || change.at > end) continue;
        spans.push({ at, end: change.at, source: active, order });
        at = change.at;
        active = change.source;
        order = change.order;
      }
      spans.push({ at, end, source: active, order });
      return spans;
    };
    const projection = {
      changes,
      hasSourceInformation: initial !== null || markers.length > 0,
      sources: [...new Set([initial, ...changes.map((change) => change.source)])],
      sourceAt,
      intervalAt,
      intervals,
      sourceForRecord: (record) => recordOwners.get(record)?.source ?? null,
      intervalForRecord: (record) => recordOwners.get(record)?.order ?? -1,
      sourceForEvent: (event) =>
        eventOwners.get(event.event_id) ?? (markers.length ? null : initial),
    };
    this.historyPresenceCache = {
      session,
      records,
      count: records.length,
      fallback,
      projection,
    };
    return projection;
  }
  historyPresenceReports(start, end) {
    const session = this.shown?.session,
      entity = session?.configuration?.bindings?.presence,
      projection = this.historyPresenceProjection(),
      seen = new Set(),
      reports = (this.shown?.records || [])
        .flatMap((record) =>
          record.kind === "presence"
            ? [{ record, report: record.payload, received: record.received_at }]
            : record.kind === "presence_source"
              ? Object.values(record.payload?.external_snapshot || {}).map(
                  (report) => ({ record, report, received: record.received_at }),
                )
              : [],
        )
        .map(({ record, report, received }) => ({
          record,
          report,
          received: stamp(report.received_at || received),
        }))
        .filter(({ report, received }) => {
          if (
            !Number.isFinite(received) ||
            (report.report_id && seen.has(report.report_id))
          )
            return false;
          if (report.report_id) seen.add(report.report_id);
          return true;
        })
        .sort((a, b) => a.received - b.received);
    return projection.intervals(start, end).flatMap((interval) => {
      const matching = reports.filter(({ report }) =>
        interval.source === "ha_presence"
          ? report.assertion === "direct_presence" &&
            (!entity || report.source === entity)
          : interval.source === "proxy" &&
            ["provisional_proxy", "proxy_retraction"].includes(report.assertion),
      );
      // A direct sensor retains its report through fallback. Proxy evidence
      // belongs to its owner interval and cannot revive after a later return.
      const initial = matching
          .filter(
            (item) =>
              item.received <= interval.at &&
              (interval.source !== "proxy" ||
                projection.intervalForRecord(item.record) === interval.order),
          )
          .at(-1),
        changes = matching.filter(
          (item) =>
            item.received > interval.at &&
            item.received <= interval.end &&
            projection.intervalForRecord(item.record) === interval.order,
        );
      return [
        { at: interval.at, report: initial?.report || null },
        ...changes
          .map(({ report }) => ({
            // A retained report starts with the new owner interval, even when
            // its original evidence predates that source change.
            at: Math.max(interval.at, stamp(report.effective_at)),
            report,
          }))
          .filter((item) => Number.isFinite(item.at) && item.at <= interval.end),
      ];
    });
  }
  historyConfigurationModel(at = null) {
    const session = this.shown?.session,
      live =
        session?.timeline?.session_id &&
        session.timeline.session_id === this.state?.session?.timeline.session_id &&
        !session.ended_at,
      model =
        session?.configuration_model || (live ? this.state?.configuration_model : null),
      source = this.historyPresenceProjection().sourceAt(
        at ?? session?.ended_at ?? this.state?.now,
      );
    if (!source) return model || null;
    if (
      model?.presence?.source === source &&
      model.presence.direct === (source === "ha_presence")
    )
      return model;
    return {
      ...model,
      presence: { ...model?.presence, source, direct: source === "ha_presence" },
    };
  }
  diagnosticPersonDataActive(at = null) {
    const projection = this.historyPresenceProjection();
    return at == null
      ? projection.sources.some((source) => source !== "ha_presence")
      : projection.sourceAt(at) !== "ha_presence";
  }
  diagnosticsParameters(at = null) {
    const session = this.shown?.session,
      live =
        session?.timeline.session_id === this.state?.session?.timeline.session_id &&
        !session?.ended_at,
      parameters =
        session?.configuration?.parameters ||
        (live ? this.state?.configuration?.parameters : {}) ||
        {};
    const personDataActive = this.diagnosticPersonDataActive(at);
    return Object.fromEntries(
      Object.entries(parameters).filter(
        ([key]) =>
          /^(door_|strong_|weak_|infusion_|vent_|person_step_seconds$|grid_seconds$)/.test(
            key,
          ) &&
          (personDataActive || !/^(strong_|weak_|person_step_seconds$)/.test(key)),
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
      this.historyConfigurationModel(),
      this.historyPresenceProjection().changes,
      this.appearanceValue(),
      this.window,
      this.highlightedEventId,
      this.selectedDiagnosticTraceAt || data.traces[0]?.at || null,
      this.$("#detection-plots")?.getBoundingClientRect?.().width ||
        this.preference("diagnostic_fallback_width_px"),
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
    this.shadowRoot
      .querySelectorAll(".diagnostic-marker")
      .forEach((node) =>
        node.setAttribute?.("aria-pressed", String(node.dataset.eventId === eventId)),
      );
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
    if (
      (pending.sessionId &&
        pending.sessionId !== this.shown?.session.timeline.session_id) ||
      (kind === "marker" && this.view !== "diagnostics") ||
      (kind === "row" && this.navigation?.main !== "history")
    ) {
      this.pendingEventFocus = null;
      return;
    }
    const target = [
      ...this.shadowRoot.querySelectorAll(
        kind === "row" ? "#event-list [data-action]" : ".diagnostic-marker",
      ),
    ].find(
      (node) =>
        node.dataset.eventId === pending.eventId ||
        node.dataset.action === `event-row:${pending.eventId}`,
    );
    // A reveal is a single navigation operation, never a latent request that
    // can steal focus after unrelated data arrives.
    this.pendingEventFocus = null;
    if (!target) return;
    this.highlightEvent(pending.eventId, kind === "row");
    target.scrollIntoView?.({ block: "center", behavior: "smooth" });
    target.focus?.({ preventScroll: true });
  }
  focusEvent(eventId, revealRow = false) {
    const event = this.eventNavigation().find((item) => item.event_id === eventId),
      trace =
        event && this.diagnosticTraceForEvent(this.diagnosticData().traces, event),
      at = stamp(trace?.at || event?.effective_at || event?.detected_at);
    this.selectedDiagnosticTraceAt = trace?.at || null;
    this.selectedDiagnosticTrace = trace || null;
    if (!event || !Number.isFinite(at) || (!trace && !revealRow)) {
      this.pendingEventFocus = null;
      this.highlightEvent(eventId, revealRow);
      this.drawHistory("selection");
      return;
    }
    const [domainStart, domainEnd] = this.historyDomain();
    if (!this.window || at < this.window[0] || at > this.window[1]) {
      const span = Math.min(
          domainEnd - domainStart,
          this.preference("history_marker_focus_span_ms"),
        ),
        left = Math.max(domainStart, Math.min(domainEnd - span, at - span / 2));
      this.setHistoryWindow(left, left + span);
    }
    this.highlightEvent(eventId, revealRow);
    this.drawHistory("selection");
  }
  invalidateHistoryIndex() {
    this.historyDatasetRevision = (this.historyDatasetRevision || 0) + 1;
  }
  updateHistoryTimelineRevision(session) {
    const signature = JSON.stringify({
      id: session.timeline.session_id,
      ended_at: session.ended_at,
      energy: energyIntervalSignature(session.energy),
      heating: session.heating,
      door_cycles: session.door_cycles,
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
      const observations = [{ quantity: source.quantity, time, value, source }],
        absolute = record.display?.absolute_humidity,
        absoluteTime = stamp(absolute?.received_at);
      if (absolute && Number.isFinite(absoluteTime))
        observations.push({
          quantity: "absolute_humidity",
          time: absoluteTime,
          value:
            typeof absolute.value === "number" && Number.isFinite(absolute.value)
              ? absolute.value
              : null,
          source: absolute,
          validUntil: stamp(absolute.valid_until),
        });
      for (const observation of observations) {
        const { quantity, time, value, source: pointSource } = observation,
          key = `${source.position}:${quantity}`,
          values = index.series.get(key) || [],
          point = { time, value, source: pointSource },
          derived = quantity === "absolute_humidity";
        if (derived) point.validUntil = observation.validUntil;
        index.seriesState ??= new Map();
        const state = index.seriesState.get(key) || { revision: 0 };
        point.serial = ++state.revision;
        if (value != null) state.hasValue = true;
        index.seriesState.set(key, state);
        // Raw observations retain nulls for truthful hover readouts. The
        // curve index shares only valid point objects, making a valid neighbour
        // across even a long missing interval available by binary search.
        const curveValues = index.curveSeries.get(key) || [];
        if (derived) {
          // A pair can become complete on the second record at the same time.
          // Keep its final projection in both the raw and curve indexes.
          for (const target of [values, curveValues]) {
            const at = lowerBoundHistory(target, time);
            if (target[at]?.time === time) {
              target.splice(at, 1);
              index.display.delete(target);
            }
          }
        }
        const targets = [values];
        if (value != null) {
          index.curveSeries.set(key, curveValues);
          targets.push(curveValues);
        }
        for (const target of targets) {
          const display = index.display.get(target);
          if ((!derived && rebuild) || !target.length || target.at(-1).time <= time) {
            target.push(point);
            if (display) this.appendHistoryDisplay(display, point, target.length - 1);
          } else {
            target.splice(lowerBoundHistory(target, time), 0, point);
            index.display.delete(target);
          }
        }
        index.series.set(key, values);
      }
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
  historyIntervalMeasurements(position, quantity, start, end, scope = "range") {
    if (
      this.shown?.records &&
      (this.chartDataIndex?.records !== this.shown.records ||
        this.chartDataIndex.indexedCount !== this.shown.records.length)
    )
      this.historyIndex(this.shown.records);
    const session = this.shown?.session,
      index = this.chartDataIndex,
      values = this.series(position, quantity),
      started = stamp(session?.timeline?.session_started_at),
      ended = stamp(session?.ended_at),
      left =
        scope === "session"
          ? Number.isFinite(started)
            ? Math.max(start, started)
            : NaN
          : start,
      right =
        scope === "session" && Number.isFinite(ended) ? Math.min(end, ended) : end,
      valid = Number.isFinite(left) && Number.isFinite(right) && left <= right,
      first = valid ? lowerBoundHistory(values, left) : values.length,
      after = valid
        ? scope === "range"
          ? lowerBoundHistory(values, right)
          : upperBoundHistory(values, right)
        : values.length,
      revision = index?.seriesState?.get(`${position}:${quantity}`)?.revision || 0,
      key = `${this.historyValueIdentity()}:${started}:${ended}:${revision}:${first}:${after}`,
      summaries = index ? (index.measurementSummaries ??= new Map()) : new Map(),
      name = `${position}:${quantity}:${scope}`,
      cached = summaries.get(name);
    if (cached?.source === values && cached.key === key) return cached;
    let maximum = null;
    if (quantity === "temperature")
      for (let offset = first; offset < after; offset++) {
        const point = values[offset];
        if (
          point.value != null &&
          (!maximum ||
            point.value > maximum.value ||
            (point.value === maximum.value &&
              point.time === maximum.time &&
              point.serial < maximum.serial))
        )
          maximum = point;
      }
    const summary = {
      source: values,
      key,
      maximum,
    };
    summaries.set(name, summary);
    return summary;
  }
  historySessionMeasurements(position, quantity) {
    const session = this.shown?.session,
      start = stamp(session?.timeline?.session_started_at),
      end = stamp(session?.ended_at || this.state?.now),
      summary = this.historyIntervalMeasurements(
        position,
        quantity,
        start,
        end,
        "session",
      );
    return {
      ...summary,
      // Elapsed time can invalidate a current value without changing records.
      last: this.historyMeasurementAt(position, quantity, end, true, true),
    };
  }
  historyMeasurementAt(position, quantity, time, pinned = false, current = false) {
    if (
      this.shown?.records &&
      (this.chartDataIndex?.records !== this.shown.records ||
        this.chartDataIndex.indexedCount !== this.shown.records.length)
    )
      this.historyIndex(this.shown.records);
    const values = this.series(position, quantity);
    let point, after;
    if (current) {
      after = upperBoundHistory(values, time);
      point = values[after - 1];
    } else {
      point = this.nearestMeasurement(position, quantity, time, pinned);
      after = point ? upperBoundHistory(values, point.time) : 0;
    }
    if (!point) return null;
    // Keep the existing nearest timestamp, then use its latest original input.
    // Late inserts must not let an older valid value hide a newer null.
    for (
      let offset = after - 1;
      offset >= 0 && values[offset].time === point.time;
      offset--
    )
      if (values[offset].serial > point.serial) point = values[offset];
    if (point?.value == null || !Number.isFinite(time)) return null;
    if (quantity === "absolute_humidity")
      return point.time <= time &&
        Number.isFinite(point.validUntil) &&
        time <= point.validUntil
        ? point
        : null;
    const ttl = historyMeasurementTtlSeconds(this, this.shown?.session) * 1000;
    return Number.isFinite(ttl) && ttl >= 0 && Math.abs(point.time - time) <= ttl
      ? point
      : null;
  }
  historyMeasurementContent(selection = null, pinned = false) {
    const content = new Map(),
      session = this.shown?.session,
      completed = !!session?.ended_at,
      humidity = this.historyHumidityDisplay(),
      range = selection?.kind === "range" && selection.end > selection.start;
    for (const position of this.positions || ["upper"])
      for (const quantity of ["temperature", "humidity"]) {
        const sourceQuantity = quantity === "humidity" ? humidity.quantity : quantity,
          summary = !selection
            ? this.historySessionMeasurements(position, sourceQuantity)
            : range
              ? this.historyIntervalMeasurements(
                  position,
                  sourceQuantity,
                  selection.start,
                  selection.end,
                )
              : null;
        content.set(`${position}:${quantity}`, {
          position,
          quantity,
          value: selection
            ? this.historyMeasurementAt(
                position,
                sourceQuantity,
                selection.start,
                pinned,
              )
            : completed
              ? summary.maximum
              : summary.last,
          end: range
            ? this.historyMeasurementAt(position, sourceQuantity, selection.end, pinned)
            : null,
          maximum: summary?.maximum || null,
        });
      }
    return content;
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
  historyHumidityDisplay() {
    const absolute = this.historyHumidityMode === "absolute";
    return {
      mode: absolute ? "absolute" : "relative",
      quantity: absolute ? "absolute_humidity" : "humidity",
      unit: absolute ? "g/m³" : "%",
      label: absolute ? "Absolute Feuchte (g/m³)" : "Relative Feuchte (%)",
      precision: this.preference(
        absolute ? "absolute_humidity_precision" : "value_precision",
      ),
    };
  }
  nearestMeasurement(position, quantity, time, pinned = false) {
    if (
      this.shown?.records &&
      (this.chartDataIndex?.records !== this.shown.records ||
        this.chartDataIndex.indexedCount !== this.shown.records.length)
    )
      this.historyIndex(this.shown.records);
    const values = this.series(position, quantity);
    if (quantity === "absolute_humidity") {
      const after = lowerBoundHistory(values, time),
        index = values[after]?.time === time ? after : after - 1;
      return values[index] || null;
    }
    return nearestHistoryPoint(
      values,
      time,
      pinned ? -Infinity : this.window[0],
      pinned ? Infinity : this.window[1],
    );
  }
  historyValueIdentity() {
    return `${this.entry}:${this.shown?.session?.timeline?.session_id}`;
  }
  syncHistoryValueIdentity() {
    if (
      this.historyValueSelection &&
      this.historyValueSelection.identity !== this.historyValueIdentity()
    ) {
      this.clearHistorySelection();
      this.cancelHistoryInput();
    }
  }
  historyPhaseBoundaries() {
    const intervals = this.shown?.phase_projection?.intervals || [],
      boundaries = intervals
        .filter(
          (phase, index) => index === 0 || phase.phase !== intervals[index - 1].phase,
        )
        .map((phase) => ({
          time: stamp(phase.started_at),
          phase: phase.phase,
          phaseKey: this.historyPhaseKey(phase),
          boundary: "start",
        }))
        .filter((item) => Number.isFinite(item.time)),
      last = intervals.at(-1),
      end = stamp(last?.ended_at),
      sessionEnd = stamp(this.shown?.session?.ended_at);
    // The projection's final end in a live session is its read horizon.
    // Only an archived session end proves a terminal phase boundary.
    if (Number.isFinite(end) && end === sessionEnd)
      boundaries.push({
        time: end,
        phase: last.phase,
        phaseKey: this.historyPhaseKey(last),
        boundary: "end",
      });
    return boundaries;
  }
  historyCurrentSnap(snap) {
    if (!snap) return null;
    if (snap.eventId) {
      return (
        this.historyEventProjection().snapTargets.find(
          (target) => target.eventId === snap.eventId && target.time === snap.time,
        ) || null
      );
    }
    return (
      this.historyPhaseBoundaries().find((item) => item.time === snap.time) || null
    );
  }
  historySnapTarget(
    time,
    model = this.historyChart?.model,
    geometry,
    includeEvents = false,
  ) {
    const radius = this.preference("history_phase_snap_radius_px");
    const target = { time, snap: null };
    if (!radius || !model) return target;
    const rect =
      geometry?.rects?.surface ||
      this.historyChart?.interaction?.geometry?.rects?.surface;
    if (!(rect?.width > 0) || !(model.end > model.start) || !(model.right > model.left))
      return target;
    const tolerance =
      ((model.end - model.start) / (model.right - model.left)) *
      (model.width / rect.width) *
      radius;
    let distance = Infinity;
    const targets = this.historyPhaseBoundaries();
    if (includeEvents) targets.push(...this.historyEventProjection().snapTargets);
    for (const boundary of targets) {
      const at = boundary.time,
        delta = Math.abs(at - time);
      if (
        at >= model.start &&
        at <= model.end &&
        delta <= tolerance &&
        delta < distance
      ) {
        target.time = at;
        target.snap = boundary;
        distance = delta;
      }
    }
    return target;
  }
  historySnapTime(time, model = this.historyChart?.model, geometry) {
    return this.historySnapTarget(time, model, geometry).time;
  }
  historyPhaseKey(phase) {
    return JSON.stringify([
      stamp(phase.started_at),
      phase.phase,
      phase.source_id ?? null,
    ]);
  }
  selectHistoryPhase(key) {
    const phase = (this.shown?.phase_projection?.intervals || []).find(
      (item) => this.historyPhaseKey(item) === key,
    );
    if (!phase || !this.shown?.session) return;
    const current = this.historyValueSelection,
      same =
        current?.identity === this.historyValueIdentity() && current.phaseKey === key;
    const start = stamp(phase.started_at),
      end = stamp(phase.ended_at || this.shown.session.ended_at || this.state.now);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return;
    this.selectHistoryValueContext(
      {
        identity: this.historyValueIdentity(),
        kind: "range",
        start,
        end,
        phaseKey: key,
        startSnap: this.historyCurrentSnap({ time: start }),
        endSnap: this.historyCurrentSnap({ time: end }),
      },
      same,
    );
  }
  selectHistoryValueContext(selection, same = false) {
    const domain = this.historyDomain(),
      view = this.historyViewTransition?.target || this.window || domain,
      fullView = this.historyViewTransition
        ? this.historyViewTransition.fullView
        : this.zoom === 1 || (view[0] <= domain[0] && view[1] >= domain[1]);
    this.cancelHistoryInput();
    if (!same) this.historyValueSelection = selection;
    this.pendingHover = this.lastHistoryPointer = this.historyHoverPreview = null;
    this.historyKeyboardPreview = false;
    this.historyKeyboardTarget = null;
    this.historyKeyboardTime = this.historyValueSelection.start;
    if (same && !fullView) this.transitionHistoryWindow(...domain, true);
    else if (same || !fullView)
      this.transitionHistoryWindow(
        ...this.historySelectionWindow(this.historyValueSelection),
      );
    this.syncHistorySelection();
    this.historyChart?.interaction.refreshSelection();
    this.scheduleHistoryRender("selection");
  }
  historySelectionWindow(selection) {
    const span =
        selection.kind === "point"
          ? this.preference("history_marker_focus_span_ms")
          : (selection.end - selection.start) *
            (1 + 2 * this.preference("history_selection_context_fraction")),
      middle = selection.start + (selection.end - selection.start) / 2;
    return this.historyWindowBounds(middle - span / 2, middle + span / 2);
  }
  toggleHistorySelectionWindow() {
    const selection = this.historyValueSelection;
    if (!selection || selection.identity !== this.historyValueIdentity()) return;
    this.selectHistoryValueContext(selection, true);
  }
  historyTimeSelectionKind(time) {
    const current = this.historyValueSelection;
    if (current?.identity !== this.historyValueIdentity()) return "point";
    if (current.kind === "range") return null;
    return current.start === time ? "point" : "range";
  }
  historyTimeSnapTarget(time, model = this.historyChart?.model, geometry) {
    const current = this.historyValueSelection;
    if (
      current?.identity === this.historyValueIdentity() &&
      current.kind === "point" &&
      current.start === time
    )
      return { time, snap: this.historyCurrentSnap(current.startSnap) };
    return this.historySnapTarget(
      time,
      model,
      geometry,
      this.historyTimeSelectionKind(time) === "point",
    );
  }
  selectHistoryTime(time, model = this.historyChart?.model, geometry, resolvedTarget) {
    if (!Number.isFinite(time) || !this.shown?.session) return;
    this.cancelHistoryViewTransition();
    const identity = this.historyValueIdentity(),
      current = this.historyValueSelection;
    if (current?.identity === identity && current.kind === "range") {
      this.clearHistorySelection();
      return;
    }
    const target = resolvedTarget || this.historyTimeSnapTarget(time, model, geometry);
    time = target.time;
    const beginsBefore = current?.start < time;
    this.historyValueSelection =
      current?.identity === identity &&
      current.kind === "point" &&
      current.start !== time
        ? {
            identity,
            kind: "range",
            start: Math.min(current.start, time),
            end: Math.max(current.start, time),
            startSnap: beginsBefore
              ? current.startSnap?.eventId
                ? null
                : current.startSnap
              : target.snap,
            endSnap: beginsBefore
              ? target.snap
              : current.startSnap?.eventId
                ? null
                : current.startSnap,
          }
        : {
            identity,
            kind: "point",
            start: time,
            end: time,
            startSnap: target.snap,
            endSnap: target.snap,
          };
    this.historyKeyboardTime = time;
    this.historyKeyboardPreview = false;
    this.historyKeyboardTarget = this.historyHoverPreview = null;
    this.pendingHover = null;
    this.syncHistorySelection();
    this.historyChart?.interaction.refreshSelection();
    this.scheduleHistoryRender("selection");
  }
  clearHistorySelection() {
    this.cancelHistorySelectionDrag(false);
    this.cancelHistoryViewTransition();
    this.historyValueSelection = null;
    this.historyKeyboardTime = null;
    this.historyKeyboardPreview = false;
    this.historyKeyboardTarget = this.historyHoverPreview = null;
    this.pendingHover = this.lastHistoryPointer = null;
    for (const pointer of this.chartPointers?.values() || []) pointer.tap = false;
    this.historyChart?.interaction.hide();
    this.syncHistorySelection();
    this.syncHistoryEventInspection();
    this.scheduleHistoryRender("selection");
    if (
      this.shadowRoot?.activeElement?.matches?.(
        ".history-event-marker, .history-event-band-segment, [data-history-selection-context], .history-selection-handle",
      )
    )
      this.historyChart?.surface.focus?.({ preventScroll: true });
  }
  historySelectionPhase(selection = this.historyValueSelection) {
    if (!selection) return null;
    if (selection.kind === "point") return this.historyPhaseAt(selection.start);
    return (
      (this.shown?.phase_projection?.intervals || []).find((phase) => {
        const start = stamp(phase.started_at),
          end = stamp(phase.ended_at);
        return (
          Number.isFinite(start) &&
          Number.isFinite(end) &&
          start <= selection.start &&
          end >= selection.end
        );
      }) || null
    );
  }
  syncHistorySelection(model = this.historyChart?.model) {
    const selection = this.historyValueSelection,
      layer = this.$("[data-history-selection]"),
      band = this.$("[data-history-selection-band]"),
      first = this.$("[data-history-selection-start]"),
      last = this.$("[data-history-selection-end]"),
      context = this.$("[data-history-selection-context]"),
      handles = ["start", "end"].map((boundary) => ({
        boundary,
        node: this.$(`[data-history-selection-handle="${boundary}"]`),
        tick: this.$(`[data-history-selection-${boundary}-tick]`),
        hit: this.$(`[data-history-selection-${boundary}-hit]`),
      })),
      startSnap =
        selection?.kind === "range" && selection.startSnap?.eventId
          ? null
          : this.historyCurrentSnap(selection?.startSnap),
      endSnap =
        selection?.kind === "range" && selection.endSnap?.eventId
          ? null
          : this.historyCurrentSnap(selection?.endSnap),
      inView = (time) => !!model && time >= model.start && time <= model.end,
      visible =
        !!selection &&
        !!model &&
        selection.start <= model.end &&
        selection.end >= model.start;
    layer?.setAttribute?.("visibility", visible ? "visible" : "hidden");
    context?.setAttribute("visibility", visible ? "visible" : "hidden");
    context?.setAttribute("aria-hidden", String(!visible));
    context?.setAttribute("tabindex", visible ? "0" : "-1");
    const tickGeometry =
        this.historySelectionDrag?.geometry ||
        this.historyChart?.interaction.cachedGeometry,
      tickScaleX =
        (model?.width || 1) /
        Math.max(1, tickGeometry?.rects?.timeAxis?.width || model?.width || 1),
      tickGap = this.preference("control_group_gap_px") * tickScaleX,
      boundaries = selection
        ? [selection.start, ...(selection.kind === "range" ? [selection.end] : [])]
            .filter(inView)
            .map((time) => model.x(time))
        : [];
    let previousTickRight = -Infinity;
    for (const tick of this.shadowRoot?.querySelectorAll?.("[data-history-time]") ||
      []) {
      const time = Number(tick.dataset.historyTime),
        center = model?.x(time),
        halfWidth =
          (this.historyLabelWidth(tick.textContent, tickGeometry) * tickScaleX) / 2,
        left = center - halfWidth,
        right = center + halfWidth,
        shown =
          !!model &&
          left >= model.left + tickGap &&
          right <= model.right - tickGap &&
          left >= previousTickRight + tickGap &&
          boundaries.every((at) => right + tickGap <= at || left - tickGap >= at),
        selected = !!selection && time >= selection.start && time <= selection.end;
      tick.setAttribute("visibility", shown ? "visible" : "hidden");
      tick.setAttribute("aria-hidden", String(!shown));
      tick.setAttribute("data-selected", String(selected));
      if (shown) previousTickRight = right;
      if (shown && selected)
        tick.setAttribute("data-action", "history-selection-window");
      else tick.removeAttribute("data-action");
    }
    if (visible) {
      const phase = selection.phaseKey ? this.historySelectionPhase(selection) : null,
        label = selection.eventIds?.length
          ? selection.eventLabel ||
            this.historyEventGroupLabel(this.historyInspectedEventGroup())
          : phase
            ? phases[phase.phase] || phase.phase
            : selection.kind === "range"
              ? "Ausgewählter Zeitraum"
              : "Ausgewählter Zeitpunkt",
        when =
          selection.kind === "range"
            ? `${this.tooltipWhen(selection.start)} – ${this.tooltipWhen(selection.end)}`
            : this.tooltipWhen(selection.start);
      context?.setAttribute("aria-label", `${label} · ${when} · Ansicht wechseln`);
    }
    band?.setAttribute(
      "visibility",
      visible && selection.kind === "range" ? "visible" : "hidden",
    );
    first?.setAttribute(
      "visibility",
      selection && inView(selection.start) ? "visible" : "hidden",
    );
    last?.setAttribute(
      "visibility",
      selection?.kind === "range" && inView(selection.end) ? "visible" : "hidden",
    );
    first?.setAttribute("data-snap", String(!!startSnap));
    last?.setAttribute("data-snap", String(!!endSnap));
    for (const { boundary, node } of handles) {
      const shown =
        !!selection &&
        inView(selection[boundary]) &&
        (boundary === "start" || selection.kind === "range");
      node?.setAttribute("visibility", shown ? "visible" : "hidden");
      node?.setAttribute("aria-hidden", String(!shown));
      node?.setAttribute("tabindex", shown ? "0" : "-1");
      node?.setAttribute(
        "data-snap",
        String(!!(boundary === "start" ? startSnap : endSnap)),
      );
      if (selection && model) {
        node?.setAttribute(
          "aria-label",
          selection.kind === "point"
            ? "Zeitpunkt"
            : boundary === "start"
              ? "Beginn des ausgewählten Zeitraums"
              : "Ende des ausgewählten Zeitraums",
        );
        node?.setAttribute("aria-valuemin", String(model.start));
        node?.setAttribute("aria-valuemax", String(model.end));
        node?.setAttribute("aria-valuenow", String(selection[boundary]));
        node?.setAttribute("aria-valuetext", this.tooltipWhen(selection[boundary]));
      }
    }
    if (selection && model) {
      const left = model.x(selection.start),
        right = model.x(selection.end);
      band?.setAttribute("x", String(Math.max(model.left, left)));
      band?.setAttribute(
        "width",
        String(Math.max(0, Math.min(model.right, right) - Math.max(model.left, left))),
      );
      band?.setAttribute("y", String(model.top));
      band?.setAttribute("height", String(model.bottom - model.top));
      for (const [node, x] of [
        [first, left],
        [last, right],
      ])
        for (const [name, value] of Object.entries({
          x1: x,
          x2: x,
          y1: model.top,
          y2: model.bottom,
        }))
          node?.setAttribute(name, String(value));
      const geometry =
          this.historySelectionDrag?.geometry ||
          this.historyChart?.interaction.cachedGeometry,
        hitRadius =
          Math.max(
            this.preference("event_marker_size_px"),
            this.preference("size_22_0_px"),
          ) / 2;
      const axisHeight = this.historyTimeAxisHeight(),
        axisWidth = geometry?.rects?.timeAxis?.width || model.width,
        tickTop = this.preference("layout_space_09_px") / 2,
        tickBottom = axisHeight,
        contextLeft =
          selection.kind === "point"
            ? left - (hitRadius * model.width) / axisWidth
            : Math.max(model.left, left),
        contextRight =
          selection.kind === "point"
            ? left + (hitRadius * model.width) / axisWidth
            : Math.min(model.right, right);
      for (const line of context?.querySelectorAll?.("line") || [])
        for (const [name, value] of Object.entries({
          x1: Math.max(model.left, contextLeft),
          x2: Math.min(model.right, contextRight),
          y1: 0,
          y2: 0,
        }))
          line.setAttribute(name, String(value));
      for (const { boundary, hit, tick } of handles) {
        const at = model.x(selection[boundary]);
        for (const [name, value] of Object.entries({
          x1: at,
          x2: at,
          y1: tickTop,
          y2: tickBottom,
        }))
          tick?.setAttribute(name, String(value));
        for (const [name, value] of Object.entries({
          cx: at,
          cy: (tickTop + tickBottom) / 2,
          rx: (hitRadius * model.width) / axisWidth,
          ry: (tickBottom - tickTop) / 2,
        }))
          hit?.setAttribute(name, String(value));
      }
    }
    for (const phase of this.shadowRoot?.querySelectorAll?.(
      "[data-history-phase-key]",
    ) || [])
      phase.setAttribute(
        "aria-pressed",
        String(
          !!selection?.phaseKey && selection.phaseKey === phase.dataset.historyPhaseKey,
        ),
      );
    const clear = this.$('[data-action="clear-history-selection"]');
    if (clear) {
      clear.hidden = !selection;
      clear.disabled = !selection;
    }
    this.syncHistoryEventInspection();
  }
  historyInspectedEvent() {
    const group = this.historyInspectedEventGroup();
    return group.length === 1 ? group[0] : null;
  }
  inspectHistoryEvent(eventId) {
    this.inspectHistoryEvents([eventId]);
  }
  historyInspectedEventGroup() {
    const selection = this.historyValueSelection;
    if (!selection?.eventIds || selection.identity !== this.historyValueIdentity())
      return [];
    const ids = new Set(selection.eventIds),
      source = this.historyDetail
        ? orderedHistoryEvents(this.historyVisibleEvents())
        : this.historyMainEvents();
    return source.filter((event) => ids.has(event.event_id));
  }
  historyEventSpan(members) {
    const ids = new Set(members.map((event) => event.event_id)),
      displayGroup = this.historyEventGroups().find((group) =>
        [...ids].every((id) =>
          group.members.some(({ event }) => event.event_id === id),
        ),
      ),
      ordered = orderedHistoryEvents(members),
      complete = displayGroup?.members.length === ids.size;
    return {
      start: complete ? displayGroup.start : stamp(ordered[0]?.effective_at),
      end: complete ? displayGroup.end : stamp(ordered.at(-1)?.effective_at),
      sourceGroupKey: displayGroup?.key,
      roleLabel: complete ? displayGroup.selectionLabel : undefined,
    };
  }
  inspectHistoryEventGroup(eventIds) {
    this.inspectHistoryEvents(eventIds);
  }
  inspectHistoryEvents(eventIds) {
    if (!this.shown?.session || !Array.isArray(eventIds) || !eventIds.length) return;
    const identity = this.historyValueIdentity(),
      ids = [...new Set(eventIds)],
      source = this.historyDetail
        ? this.historyVisibleEvents()
        : this.historyMainEvents(),
      available = new Map(source.map((event) => [event.event_id, event])),
      members = ids.map((id) => available.get(id));
    if (members.some((event) => !event || !Number.isFinite(stamp(event.effective_at))))
      return;
    const spanContext = this.historyEventSpan(members),
      current = this.historyValueSelection,
      selected =
        current?.identity === identity &&
        current?.eventIds?.length === ids.length &&
        ids.every((id) => current.eventIds.includes(id));
    const snapAt = (time) => {
      const event = members.find((member) => stamp(member.effective_at) === time);
      return event
        ? this.historyCurrentSnap({ time, eventId: event.event_id, kind: event.kind })
        : null;
    };
    this.selectHistoryValueContext(
      {
        identity,
        kind: spanContext.start === spanContext.end ? "point" : "range",
        start: spanContext.start,
        end: spanContext.end,
        eventIds: ids,
        sourceGroupKey: spanContext.sourceGroupKey,
        eventLabel: spanContext.roleLabel,
        startSnap: snapAt(spanContext.start),
        endSnap: snapAt(spanContext.end),
      },
      selected,
    );
  }
  syncHistoryEventInspection() {
    const event = this.historyInspectedEvent(),
      group = this.historyInspectedEventGroup(),
      node = this.$("#history-event-inspection");
    if (node) {
      node.hidden = !event && !group.length;
      if (group.length > 1) {
        this.updateMarkup(
          "#history-event-inspection",
          `<div class="history-event-inspection-members">${group
            .map(
              (member) =>
                `<button type="button" data-key="history-member:${esc(member.event_id)}" data-action="history-event:${esc(member.event_id)}" data-history-event-id="${esc(member.event_id)}" aria-pressed="true"><time datetime="${esc(member.effective_at)}" title="${esc(this.tooltipWhen(member.effective_at))}">${esc(this.clock(member.effective_at))}</time><span>${esc(this.historyEventLabel(member))}</span></button>`,
            )
            .join("")}</div>`,
        );
      } else {
        this.updateMarkup(
          "#history-event-inspection",
          event
            ? `<div class="history-inspected-event-row"><time datetime="${esc(event.effective_at)}" title="${esc(this.tooltipWhen(event.effective_at))}">${esc(this.clock(event.effective_at))}</time><span>${esc(this.historyEventLabel(event))}</span></div>`
            : "",
        );
      }
    }
    const selectedIds = new Set(group.map((member) => member.event_id)),
      markers =
        this.shadowRoot?.querySelectorAll?.(
          "[data-history-event-id], [data-history-event-members]",
        ) || [];
    if (event) selectedIds.add(event.event_id);
    for (const marker of markers) {
      const memberIds = marker.dataset.historyEventMembers
          ? JSON.parse(decodeURIComponent(marker.dataset.historyEventMembers))
          : [marker.dataset.historyEventId],
        selected = memberIds.length > 0 && memberIds.every((id) => selectedIds.has(id));
      marker.setAttribute("data-selected", String(selected));
      marker.setAttribute("aria-pressed", String(selected));
    }
    this.syncHistorySnapTargets();
  }
  syncHistorySnapTargets() {
    const selection = this.historyValueSelection,
      preview = this.historyKeyboardPreview
        ? this.historyKeyboardTarget
        : this.historyHoverPreview,
      boundary =
        this.historySelectionDrag?.activeBoundary ||
        this.historyKeyboardTarget?.boundary,
      snaps = [boundary ? selection?.[`${boundary}Snap`] : null, preview?.snap]
        .map((snap) => this.historyCurrentSnap(snap))
        .filter((snap) => snap && !(selection?.kind === "range" && snap.eventId)),
      phaseKeys = new Set(snaps.map((snap) => snap.phaseKey).filter(Boolean)),
      eventIds = new Set(snaps.map((snap) => snap.eventId).filter(Boolean));
    for (const phase of this.shadowRoot?.querySelectorAll?.(
      "[data-history-phase-key]",
    ) || [])
      phase.setAttribute(
        "data-snap-target",
        String(phaseKeys.has(phase.dataset.historyPhaseKey)),
      );
    for (const marker of this.shadowRoot?.querySelectorAll?.(
      "[data-history-event-id], [data-history-event-members]",
    ) || []) {
      const ids = marker.dataset.historyEventMembers
        ? JSON.parse(decodeURIComponent(marker.dataset.historyEventMembers))
        : [marker.dataset.historyEventId];
      marker.setAttribute(
        "data-snap-target",
        String(ids.some((id) => eventIds.has(id))),
      );
    }
  }
  keyHistorySelection(event) {
    if (!this.window) return;
    const handle = event.target.closest?.("[data-history-selection-handle]");
    if (event.key === "Escape") {
      event.preventDefault();
      if (this.historySelectionDrag) {
        this.cancelHistorySelectionDrag();
        return;
      }
      this.clearHistorySelection();
      return;
    }
    if (this.historySelectionDrag) {
      if (["ArrowLeft", "ArrowRight", "Home", "End", "Enter", " "].includes(event.key))
        event.preventDefault();
      return;
    }
    if (handle && this.historyValueSelection) {
      if (!["ArrowLeft", "ArrowRight", "Home", "End", "Enter", " "].includes(event.key))
        return;
      event.preventDefault();
      this.cancelHistoryViewTransition();
      if (["Enter", " "].includes(event.key)) return;
      const selection = this.historyValueSelection,
        boundary = handle.dataset.historySelectionHandle,
        at =
          this.historyKeyboardTarget?.boundary === boundary
            ? this.historyKeyboardTarget.inputTime
            : selection[boundary],
        time =
          event.key === "Home"
            ? this.window[0]
            : event.key === "End"
              ? this.window[1]
              : at +
                (this.window[1] - this.window[0]) *
                  this.preference("history_keyboard_selection_fraction") *
                  (event.key === "ArrowLeft" ? -1 : 1),
        inputTime = Math.max(this.window[0], Math.min(this.window[1], time)),
        target = this.historySnapTarget(
          inputTime,
          this.historyChart?.model,
          this.historyChart?.interaction.readGeometry?.(),
          selection.kind === "point",
        ),
        moved = this.moveHistorySelectionBoundary(boundary, target, selection);
      this.historyKeyboardTime = inputTime;
      this.historyKeyboardTarget = { ...target, boundary: moved, inputTime };
      this.$(`[data-history-selection-handle="${moved}"]`)?.focus?.({
        preventScroll: true,
      });
      return;
    }
    if (!["Enter", " ", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
    if (["ArrowLeft", "ArrowRight"].includes(event.key) && !event.shiftKey) return;
    event.preventDefault();
    this.cancelHistoryViewTransition();
    const [start, end] = this.window;
    this.historyKeyboardTime = Math.max(
      start,
      Math.min(end, this.historyKeyboardTime ?? (start + end) / 2),
    );
    if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
      this.historyKeyboardTime = Math.max(
        start,
        Math.min(
          end,
          this.historyKeyboardTime +
            (end - start) *
              this.preference("history_keyboard_selection_fraction") *
              (event.key === "ArrowLeft" ? -1 : 1),
        ),
      );
      this.historyKeyboardTarget = this.historyTimeSnapTarget(
        this.historyKeyboardTime,
        this.historyChart?.model,
        this.historyChart?.interaction.readGeometry?.(),
      );
      const svg = event.target.closest?.("svg") || this.historyChart?.surface;
      if (svg)
        svg.setAttribute(
          "aria-description",
          `Zeitpunkt · ${this.tooltipWhen(this.historyKeyboardTarget.time)}`,
        );
      this.historyKeyboardPreview = true;
      this.historyHoverPreview = null;
      this.historyChart?.interaction.previewTime(this.historyKeyboardTime);
    } else
      this.selectHistoryTime(
        this.historyKeyboardTime,
        this.historyChart?.model,
        this.historyChart?.interaction.cachedGeometry,
        this.historyKeyboardTarget,
      );
  }
  moveHistorySelectionBoundary(boundary, target, selection) {
    if (!selection || !Number.isFinite(target?.time)) return boundary;
    let moved = "start";
    if (selection.kind === "point") {
      this.historyValueSelection = {
        identity: selection.identity,
        kind: "point",
        start: target.time,
        end: target.time,
        startSnap: target.snap,
        endSnap: target.snap,
      };
    } else {
      const other = boundary === "start" ? "end" : "start",
        fixed = selection[other],
        before = target.time <= fixed,
        fixedSnap = selection[`${other}Snap`]?.eventId
          ? null
          : selection[`${other}Snap`];
      moved = before ? "start" : "end";
      this.historyValueSelection = {
        identity: selection.identity,
        kind: target.time === fixed ? "point" : "range",
        start: Math.min(target.time, fixed),
        end: Math.max(target.time, fixed),
        startSnap: before ? target.snap : fixedSnap,
        endSnap: target.time === fixed ? target.snap : before ? fixedSnap : target.snap,
      };
    }
    this.historyKeyboardTime = target.time;
    this.historyKeyboardPreview = false;
    this.historyKeyboardTarget = null;
    this.pendingHover = this.lastHistoryPointer = this.historyHoverPreview = null;
    this.syncHistorySelection();
    this.historyChart?.interaction.refreshSelection();
    this.scheduleHistoryRender("selection");
    return moved;
  }
  beginHistorySelectionDrag(event, handle) {
    if (event.button != null && event.button !== 0) return;
    if (!this.historyValueSelection || !this.historyChart?.model) return;
    if (this.historyInputMode === "wheel") this.endHistoryWheel();
    if (this.historyInputMode) return;
    event.preventDefault();
    this.beginHistoryInput();
    this.historyInputMode = "selection";
    this.historyChart.interaction.invalidateGeometry("geometry", false);
    const svg = handle.closest?.("svg") || this.historyChart.surface,
      geometry = this.historyChart.interaction.readGeometry(),
      model = { ...this.historyChart.model },
      boundary =
        handle.dataset.historySelectionHandle ||
        handle.dataset.historySelectionBoundary,
      rect = svg.matches?.("[data-history-time-axis]")
        ? geometry.rects.timeAxis
        : geometry.rects.surface;
    this.historySelectionDrag = {
      pointerId: event.pointerId,
      svg,
      model,
      geometry,
      boundary,
      activeBoundary: boundary,
      startX: event.clientX,
      startY: event.clientY,
      movement: 0,
      tap: true,
      offsetX:
        event.clientX -
        rect.left -
        (model.x(this.historyValueSelection[boundary]) / model.width) * rect.width,
      selection: { ...this.historyValueSelection },
    };
    this.pendingHover = this.lastHistoryPointer = this.historyHoverPreview = null;
    this.historyKeyboardPreview = false;
    this.historyKeyboardTarget = null;
    this.historyValueSelection = { ...this.historyValueSelection };
    delete this.historyValueSelection.phaseKey;
    delete this.historyValueSelection.eventIds;
    delete this.historyValueSelection.sourceGroupKey;
    delete this.historyValueSelection.eventLabel;
    handle.focus?.({ preventScroll: true });
    svg.setPointerCapture?.(event.pointerId);
    this.syncHistorySelection();
  }
  updateHistorySelectionDrag(event) {
    const drag = this.historySelectionDrag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    drag.movement = Math.max(
      drag.movement,
      Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY),
    );
    if (drag.movement > this.preference("history_tap_movement_px")) drag.tap = false;
    if (drag.tap) return;
    const { model, geometry, svg } = drag,
      rect = svg.matches?.("[data-history-time-axis]")
        ? geometry.rects.timeAxis
        : geometry.rects.surface,
      hit = this.historyChart?.interaction.timeAt(
        {
          svg,
          clientX: event.clientX - drag.offsetX,
          clientY: rect.top + rect.height / 2,
          clampToPlot: true,
        },
        model,
        geometry,
      );
    if (!hit) return;
    const target = this.historySnapTarget(
      Math.max(model.start, Math.min(model.end, hit.time)),
      model,
      geometry,
      drag.selection.kind === "point",
    );
    drag.activeBoundary = this.moveHistorySelectionBoundary(
      drag.boundary,
      target,
      drag.selection,
    );
  }
  endHistorySelectionDrag(event, cancelled = false) {
    const drag = this.historySelectionDrag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (!cancelled) this.updateHistorySelectionDrag(event);
    const tapped = !cancelled && event.type === "pointerup" && drag.tap,
      boundary = tapped ? "start" : drag.activeBoundary;
    if (tapped) {
      const selection = drag.selection,
        other = drag.boundary === "start" ? "end" : "start",
        time = selection[other],
        snap = selection[`${other}Snap`];
      this.historyValueSelection =
        selection.kind === "point"
          ? null
          : {
              identity: selection.identity,
              kind: "point",
              start: time,
              end: time,
              startSnap: snap,
              endSnap: snap,
            };
      this.historyKeyboardTime = this.historyValueSelection?.start ?? null;
    }
    this.cancelHistorySelectionDrag(cancelled);
    if (!cancelled) {
      if (tapped && !this.historyValueSelection) this.historyChart?.interaction.hide();
      const focus = this.historyValueSelection
        ? this.$(`[data-history-selection-handle="${boundary}"]`)
        : this.historyChart?.surface;
      focus?.focus?.({ preventScroll: true });
    }
  }
  cancelHistorySelectionDrag(restore = true, finish = true) {
    const drag = this.historySelectionDrag;
    if (!drag) return;
    this.historySelectionDrag = null;
    if (restore) this.historyValueSelection = drag.selection;
    if (this.historyInputMode === "selection") this.historyInputMode = null;
    if (drag.svg.hasPointerCapture?.(drag.pointerId))
      drag.svg.releasePointerCapture(drag.pointerId);
    this.syncHistorySelection();
    this.historyChart?.interaction.refreshSelection();
    if (finish) {
      this.finishHistoryInput();
      this.scheduleHistoryRender("selection");
    }
  }
  beginHistoryInput() {
    this.cancelHistoryViewTransition();
    if (this.historyInputSnapshot) return;
    this.pendingHover = null;
    const chart = this.historyChart;
    this.historyInputSnapshot = {
      domain: [...(chart?.domain || this.historyDomain())],
      model: chart?.model ? { ...chart.model } : null,
    };
  }
  finishHistoryInput() {
    if (
      this.chartPointers?.size ||
      this.historySelectionDrag ||
      this.webkitHistoryGesture ||
      this.historyInputMode
    )
      return;
    this.historyInputSnapshot = null;
    if (this.historyChart) this.historyChart.domain = this.historyDomain();
    this.scheduleHistoryRender("viewport");
  }
  cancelHistoryInput() {
    this.cancelHistoryViewTransition();
    this.cancelHistorySelectionDrag(true, false);
    const captures = [...(this.chartPointers || [])].map(([pointerId, pointer]) => [
      pointer.svg,
      pointerId,
    ]);
    this.chartPointers?.clear();
    this.webkitHistoryGesture =
      this.historyInputMode =
      this.historyInputSnapshot =
      this.pinchDistance =
        null;
    if (this.historyWheelTimer != null) clearTimeout(this.historyWheelTimer);
    this.historyWheelTimer = null;
    for (const [svg, pointerId] of captures)
      if (svg?.hasPointerCapture?.(pointerId)) svg.releasePointerCapture(pointerId);
  }
  svgCoordinates(svg, clientX, clientY = 0) {
    if (
      this.historyChart?.interaction &&
      (svg === this.historyChart.surface || svg?.matches?.("[data-history-time-axis]"))
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
      width = viewBox?.width || this.historyGeometry().width,
      height = viewBox?.height || this.historyGeometry().height;
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
    if (this.historyValueSelection?.kind === "range") return;
    if (
      this.historySelectionDrag ||
      this.webkitHistoryGesture ||
      this.chartPointers?.size > 1 ||
      (this.historyInputMode && this.historyInputMode !== "pointer")
    )
      return;
    const pointer = this.chartPointers?.get(event.pointerId),
      interaction = this.historyChart?.interaction;
    if (this.chartPointers?.size && !pointer) return;
    if (event.historyEventId) {
      this.pendingHover = this.lastHistoryPointer = this.historyHoverPreview = null;
      interaction?.refreshHover();
      this.syncHistorySelection();
      return;
    }
    if (pointer && !pointer.tap) {
      this.pendingHover = this.lastHistoryPointer = this.historyHoverPreview = null;
      interaction?.hide();
      return;
    }
    const geometry = pointer?.geometry || interaction?.readGeometry(),
      model = pointer?.model || this.historyChart?.model,
      hit = interaction?.timeAt(event, model, geometry),
      preview = hit ? this.historyTimeSnapTarget(hit.time, model, geometry) : null;
    this.historyKeyboardPreview = false;
    this.historyKeyboardTarget = null;
    this.lastHistoryPointer = preview ? { time: preview.time } : null;
    this.pendingHover = {
      ...event,
      resolved: true,
      hit,
      model,
      preview,
    };
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
    return [
      start,
      Math.max(
        start + this.preference("history_minimum_span_ms"),
        complete ? end : Math.min(end, now),
      ),
    ];
  }
  ensureHistoryWindow() {
    // Incoming data never moves a user's viewport or changes its zoom. Only a
    // session change or the explicit full-session action clears this window.
    if (this.window) return;
    this.window = this.historyDomain();
    this.zoom = 1;
    this.historyWindowRevision = (this.historyWindowRevision || 0) + 1;
  }
  historyWindowBounds(
    left,
    right,
    domain = this.historyInputSnapshot?.domain || this.historyDomain(),
  ) {
    const [start, end] = domain,
      span = end - start,
      minimum = Math.min(
        span / this.preference("history_max_zoom"),
        this.window ? this.window[1] - this.window[0] : Infinity,
      );
    const width = Math.max(minimum, Math.min(span, right - left));
    const nextLeft = Math.max(start, Math.min(end - width, left));
    return [nextLeft, nextLeft + width];
  }
  setHistoryWindow(left, right, transitioning = false) {
    if (!transitioning) this.cancelHistoryViewTransition();
    const domain = this.historyInputSnapshot?.domain || this.historyDomain(),
      next = this.historyWindowBounds(left, right, domain);
    if (!this.window || this.window[0] !== next[0] || this.window[1] !== next[1])
      this.historyWindowRevision = (this.historyWindowRevision || 0) + 1;
    this.window = next;
    this.zoom = (domain[1] - domain[0]) / (next[1] - next[0]);
  }
  cancelHistoryViewTransition() {
    const transition = this.historyViewTransition;
    this.historyViewTransition = null;
    if (transition?.frame != null)
      (globalThis.cancelAnimationFrame || clearTimeout)(transition.frame);
  }
  transitionHistoryWindow(left, right, fullView = false) {
    this.cancelHistoryViewTransition();
    const domain = this.historyDomain(),
      target = this.historyWindowBounds(left, right, domain),
      from = [...(this.window || domain)],
      duration = this.preference("history_view_transition_ms"),
      reduced = this.ownerDocument?.defaultView?.matchMedia?.(
        "(prefers-reduced-motion: reduce)",
      )?.matches;
    fullView ||= target[0] === domain[0] && target[1] === domain[1];
    if (!duration || reduced || (from[0] === target[0] && from[1] === target[1])) {
      this.setHistoryWindow(...target);
      if (fullView) this.zoom = 1;
      this.scheduleHistoryRender("viewport");
      return;
    }
    const transition = { from, target, fullView, started: null, frame: null },
      request =
        globalThis.requestAnimationFrame ||
        ((callback) =>
          setTimeout(() => callback(globalThis.performance?.now?.() ?? Date.now()), 0)),
      frame = (at) => {
        if (this.historyViewTransition !== transition || !this.isConnected) return;
        transition.started ??= at;
        const fraction = Math.min(1, Math.max(0, (at - transition.started) / duration)),
          progress = fraction * fraction * (3 - 2 * fraction);
        this.setHistoryWindow(
          from[0] + (target[0] - from[0]) * progress,
          from[1] + (target[1] - from[1]) * progress,
          true,
        );
        this.scheduleHistoryRender("viewport");
        if (fraction < 1) transition.frame = request(frame);
        else {
          this.historyViewTransition = null;
          if (fullView) this.zoom = 1;
        }
      };
    this.historyViewTransition = transition;
    transition.frame = request(frame);
  }
  beginChartPointer(event, svg) {
    if (event.button != null && event.button !== 0) return;
    if (this.historyInputMode === "wheel") this.endHistoryWheel();
    if (this.historyInputMode && this.historyInputMode !== "pointer") return;
    this.beginHistoryInput();
    this.historyInputMode = "pointer";
    this.historyKeyboardPreview = false;
    this.historyKeyboardTarget = null;
    this.historyChart?.interaction.invalidateGeometry("geometry", false);
    svg.setPointerCapture?.(event.pointerId);
    const model = this.historyChart?.model;
    const geometry = this.historyChart?.interaction.readGeometry?.();
    this.chartPointers.set(event.pointerId, {
      x: event.clientX,
      y: event.clientY,
      startX: event.clientX,
      startY: event.clientY,
      movement: 0,
      tap: !this.chartPointers.size,
      svg,
      model: model ? { ...model } : null,
      geometry,
    });
    if (this.chartPointers.size > 1)
      for (const point of this.chartPointers.values()) point.tap = false;
    if (this.chartPointers.size > 1) {
      this.pendingHover = this.lastHistoryPointer = this.historyHoverPreview = null;
      this.historyChart?.interaction.hide?.();
    }
    this.pinchDistance = null;
    if (this.chartPointers.size === 2) this.pinchZoom(svg);
  }
  updateChartPointer(event) {
    const pointer = this.chartPointers.get(event.pointerId);
    if (!pointer) return;
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    pointer.movement = Math.max(
      pointer.movement,
      Math.hypot(event.clientX - pointer.startX, event.clientY - pointer.startY),
    );
    if (pointer.movement > this.preference("history_tap_movement_px"))
      pointer.tap = false;
  }
  endChartPointer(event, cancelled = false) {
    const pointer = this.chartPointers?.get(event.pointerId);
    if (!pointer) return;
    this.updateChartPointer(event);
    this.chartPointers.delete(event.pointerId);
    if (
      !cancelled &&
      event.type === "pointerup" &&
      pointer.tap &&
      !this.chartPointers.size
    ) {
      this.historyChart?.interaction.readGeometry();
      const hit = this.historyChart?.interaction.timeAt(
        { ...event, svg: pointer.svg, clientX: event.clientX, clientY: event.clientY },
        pointer.model,
        pointer.geometry,
      );
      if (hit) {
        this.selectHistoryTime(hit.time, pointer.model, hit.geometry);
        pointer.svg.focus?.({ preventScroll: true });
      }
    }
    if (cancelled || !pointer.tap) {
      this.pendingHover = this.lastHistoryPointer = this.historyHoverPreview = null;
      this.historyChart?.interaction.hide?.();
    }
    pointer.svg.releasePointerCapture?.(event.pointerId);
    this.pinchDistance = null;
    if (!this.chartPointers.size && this.historyInputMode === "pointer")
      this.historyInputMode = null;
    if (!this.chartPointers.size) this.finishHistoryInput();
  }
  historyLegend(label, items) {
    if (!items.length) return "";
    const dotRadius = this.historyEventDotSize() / 2,
      stroke = this.preference("stroke_1_0_ratio"),
      swatchSize = Math.max(this.preference("size_22_0_px"), (dotRadius + stroke) * 2),
      center = swatchSize / 2,
      top = Math.max(
        dotRadius + stroke,
        Math.min(
          swatchSize - dotRadius - stroke,
          this.preference("event_marker_size_px") / 2,
        ),
      );
    return `<div class="history-legend" aria-label="${esc(label)}"><strong>${esc(label)}</strong><div class="legend">${items
      .map(([kind, text]) => {
        const marker = `<g class="history-event-marker"><ellipse class="history-event-dot" cx="${center}" cy="${top}" rx="${dotRadius}" ry="${dotRadius}"/></g>`;
        const shape =
          kind === "event"
            ? `<line class="history-event-guide" x1="${center}" x2="${center}" y1="0" y2="${swatchSize}"/>${marker}`
            : kind === "infusion"
              ? `<line class="infusion" x1="${center}" x2="${center}" y1="0" y2="${swatchSize}"/>${marker}`
              : `<rect class="${kind}" x="0" y="0" width="${swatchSize}" height="${swatchSize}"/>`;
        return `<span><svg class="chart legend-swatch" viewBox="0 0 ${swatchSize} ${swatchSize}" style="width: ${swatchSize}px; height: ${swatchSize}px" aria-hidden="true">${shape}</svg>${esc(text)}</span>`;
      })
      .join("")}</div></div>`;
  }
  historyMarkup() {
    const handles = ["start", "end"]
      .map(
        (boundary) =>
          `<g class="history-selection-handle" data-history-selection-handle="${boundary}" role="slider" tabindex="-1" visibility="hidden" aria-hidden="true"><line class="history-selection-guide" vector-effect="non-scaling-stroke" data-history-selection-${boundary}-tick/><ellipse class="history-selection-hit" data-history-selection-${boundary}-hit/></g>`,
      )
      .join("");
    return `<div class="history-timeline-strip" data-history-timeline-strip role="group" aria-label="Phasen der Sitzung"><div class="history-band-names" data-history-band-names></div><div class="history-phase-band" data-history-phase-band></div></div><div class="plot-wrap history-stack"><svg class="chart history-background" preserveAspectRatio="none" aria-hidden="true"><defs><clipPath id="history-layer-clip"><rect data-history-clip/></clipPath></defs><g data-history-annotations clip-path="url(#history-layer-clip)"></g></svg><canvas class="chart history-curves" role="img" aria-label="Temperatur- und Feuchteverlauf; Ereignisse und Zeiten stehen in den nachfolgenden Tabellen." aria-describedby="gangs event-list">Temperatur und Feuchte der Sitzung. Ereignisse und Saunagänge sind in den Tabellen unter dem Diagramm zugänglich.</canvas><svg class="chart session-chart" preserveAspectRatio="none" role="group" tabindex="0" aria-label="Sitzungsverlauf"><g data-history-axes></g><g data-history-selection clip-path="url(#history-layer-clip)" visibility="hidden" aria-hidden="true"><rect class="history-selection-band" data-history-selection-band/></g><g data-history-events clip-path="url(#history-layer-clip)"></g><line class="history-selection-guide" vector-effect="non-scaling-stroke" data-history-selection-start visibility="hidden"/><line class="history-selection-guide" vector-effect="non-scaling-stroke" data-history-selection-end visibility="hidden"/><line id="cursor" stroke="var(--sauna-color-chart-text)" visibility="hidden"/></svg></div><div class="history-lower-band" data-history-lower-band data-history-strip-events role="group" aria-label="Türvorgänge und Aufgüsse"></div><svg class="chart history-time-axis" data-history-time-axis preserveAspectRatio="none" role="group" tabindex="0" aria-label="Zeitachse der Sitzung"><g data-history-time-ticks></g><g data-history-selection-context data-action="history-selection-window" role="button" tabindex="-1" visibility="hidden" aria-hidden="true"><line class="history-selection-context-hit" vector-effect="non-scaling-stroke"/></g>${handles}</svg>`;
  }
  syncHistoryGeometry(plot = this.historyGeometry(), geometry) {
    const attributes = (node, values) => {
      for (const [key, value] of Object.entries(values))
        if (node?.getAttribute?.(key) !== String(value)) node?.setAttribute(key, value);
    };
    for (const selector of [".history-background", "svg.session-chart"])
      attributes(this.$(selector), { viewBox: `0 0 ${plot.width} ${plot.height}` });
    attributes(this.$("[data-history-clip]"), {
      x: plot.left,
      y: plot.top,
      width: plot.right - plot.left,
      height: plot.bottom - plot.top,
    });
    attributes(this.$("#cursor"), { y1: plot.top, y2: plot.bottom });
    const cssHeight = geometry?.canvas?.cssHeight || plot.height,
      plotGap = this.historyBandGap(),
      timeAxisHeight = this.historyTimeAxisHeight();
    attributes(this.$(".history-stack"), {
      style: `margin-block-start: -${(plot.top / plot.height) * cssHeight}px; margin-block-end: -${((plot.height - plot.bottom) / plot.height) * cssHeight}px`,
    });
    attributes(this.$("[data-history-time-axis]"), {
      viewBox: `0 0 ${plot.width} ${timeAxisHeight}`,
      style: `height: ${timeAxisHeight}px`,
    });
    for (const [kind, selector] of [
      ["phase", "[data-history-timeline-strip]"],
      ["event", "[data-history-lower-band]"],
    ]) {
      const dimensions = this.historyBandDimensions(kind, geometry);
      attributes(this.$(selector), {
        style: `margin-inline: ${(plot.left / plot.width) * 100}% ${((plot.width - plot.right) / plot.width) * 100}%; --history-band-gap: ${this.historyBandGap()}px; --history-plot-gap: ${plotGap}px; --history-band-height: ${dimensions.height}px; --history-band-compact-height: ${dimensions.compactHeight}px`,
      });
    }
  }
  historyTimeAxisHeight() {
    return (
      this.preference("font_size_small_px") * this.preference("body_line_height") +
      this.preference("layout_space_09_px")
    );
  }
  historyBandDimensions(kind, geometry) {
    const lineHeight =
        this.preference("font_size_small_px") * this.preference("body_line_height"),
      height =
        lineHeight +
        this.preference(
          kind === "phase" ? "layout_space_09_px" : "layout_space_07_px",
        ) *
          2;
    return {
      height,
      compactHeight: this.preference("history_compact_band_height_px"),
    };
  }
  historyPhaseBand(model, session, cssWidth = model.width, geometry) {
    const { start, end, left, right, width, x } = model,
      plotWidth = right - left;
    return (this.shown?.phase_projection?.intervals || [])
      .flatMap((phase) => {
        const first = Math.max(start, stamp(phase.started_at)),
          last = Math.min(
            end,
            stamp(phase.ended_at || session.ended_at || this.state.now),
          );
        if (!(last > first)) return [];
        const phaseLeft = x(first) - left,
          phaseWidth = x(last) - x(first),
          label = phases[phase.phase] || phase.phase,
          color = phaseAppearance[phase.phase]?.color || "phase_idle",
          key = this.historyPhaseKey(phase),
          selected = this.historyValueSelection?.phaseKey === key;
        return [
          `<button type="button" class="history-phase-band-segment" data-key="history-phase:${esc(key)}" data-history-phase="${esc(phase.phase)}" data-history-phase-key="${esc(key)}" data-action="history-phase:${encodeURIComponent(key)}" aria-label="${esc(label)}" aria-pressed="${selected}" style="left: ${(phaseLeft / plotWidth) * 100}%; width: ${(phaseWidth / plotWidth) * 100}%; --history-phase-color: var(--sauna-color-${color.replaceAll("_", "-")})"><span>${esc(label)}</span></button>`,
        ];
      })
      .join("");
  }
  historyLegendMarkup() {
    return "";
  }
  historyBandNames(model) {
    const [start, end] = this.historyViewTransition?.target || [model.start, model.end];
    return [
      ...new Set(
        (this.shown?.phase_projection?.intervals || [])
          .filter(
            (phase) =>
              stamp(phase.started_at) < end &&
              stamp(phase.ended_at || this.shown.session.ended_at || this.state.now) >
                start,
          )
          .map((phase) => phase.phase),
      ),
    ]
      .map((phase) => {
        const color = phaseAppearance[phase]?.color || "phase_idle";
        return `<span style="color: var(--sauna-color-${color.replaceAll("_", "-")})">${esc(phases[phase] || phase)}</span>`;
      })
      .join("");
  }
  syncHistoryBandDetail(model, geometry) {
    const identity = this.historyValueIdentity();
    if (this.historyBandDetailState?.identity !== identity)
      this.historyBandDetailState = { identity, detail: false, transition: null };
    const state = this.historyBandDetailState,
      previousDetail = state.detail,
      transition = this.historyViewTransition,
      detailFor = ([start, end]) => {
        const domain = this.historyDomain();
        return !(start <= domain[0] && end >= domain[1]);
      };
    if (transition) {
      if (state.transition !== transition) {
        state.transition = transition;
        state.targetDetail = detailFor(transition.target);
      }
    } else {
      state.detail =
        state.transition &&
        model.start === state.transition.target[0] &&
        model.end === state.transition.target[1]
          ? state.targetDetail
          : detailFor([model.start, model.end]);
      state.transition = null;
    }
    for (const selector of [
      "[data-history-timeline-strip]",
      "[data-history-lower-band]",
    ])
      this.$(selector)?.setAttribute("data-history-band-detail", String(state.detail));
    const plotWidth =
        ((model.right - model.left) * geometry.canvas.cssWidth) / model.width,
      scale = plotWidth / (model.end - model.start),
      padding = this.preference("control_group_gap_px") * 2,
      intervals = new Map(
        (this.shown?.phase_projection?.intervals || []).map((phase) => [
          this.historyPhaseKey(phase),
          phase,
        ]),
      );
    for (const node of this.shadowRoot?.querySelectorAll?.(
      "[data-history-phase-key]",
    ) || []) {
      const phase = intervals.get(node.dataset.historyPhaseKey),
        first = Math.max(model.start, stamp(phase?.started_at)),
        last = Math.min(
          model.end,
          stamp(phase?.ended_at || this.shown.session.ended_at || this.state.now),
        ),
        available = Math.max(0, last - first) * scale,
        label = phases[phase?.phase] || phase?.phase || "";
      node.setAttribute(
        "data-history-label-visible",
        String(available >= this.historyLabelWidth(label, geometry) + padding),
      );
    }
    const lane = this.historyStripEventGroups(model, geometry).sort(
        (a, b) => a.at - b.at,
      ),
      labels = new Map(
        lane.map((group, index) => {
          const first = Math.max(model.start, group.start),
            last = Math.min(model.end, group.end),
            center = (first + (last - first) / 2 - model.start) * scale,
            previous = lane[index - 1],
            next = lane[index + 1],
            left = previous
              ? Math.max(0, ((previous.at - model.start) * scale + center) / 2)
              : 0,
            right = next
              ? Math.min(plotWidth, ((next.at - model.start) * scale + center) / 2)
              : plotWidth,
            cellWidth = Math.max(0, Math.min(center - left, right - center) * 2),
            available =
              group.start === group.end
                ? cellWidth
                : Math.min((last - first) * scale, cellWidth),
            key = encodeURIComponent(
              JSON.stringify(group.members.map(({ event }) => event.event_id)),
            );
          return [
            key,
            available >= this.historyLabelWidth(group.label, geometry) + padding,
          ];
        }),
      );
    for (const node of this.shadowRoot?.querySelectorAll?.(
      ".history-event-band-segment",
    ) || [])
      node.setAttribute(
        "data-history-label-visible",
        String(!!labels.get(node.dataset.historyEventMembers)),
      );
    if (previousDetail !== state.detail)
      this.historyChart?.interaction.invalidateGeometry("layout");
  }
  historyPreparedSeries(position, quantity, start, end, ttl, pixels, cache) {
    const values = this.historyCurveSeries(position, quantity),
      state = this.chartDataIndex.seriesState?.get(`${position}:${quantity}`),
      first = Math.max(0, lowerBoundHistory(values, start) - 1),
      after = Math.min(values.length, lowerBoundHistory(values, end + 1) + 1),
      // Only inserts in this span change its length. Late inserts before it
      // shift both indices equally; stable neighbour identities still detect
      // an insertion that changes an edge tangent without changing the count.
      key = `${this.historyProjection()}:${this.historyProjectionGeneration || 0}:${quantity}:${quantity === "absolute_humidity" ? state?.revision || 0 : ""}:${start}:${end}:${ttl}:${pixels}:${after - first}:${values[first]?.serial || 0}:${values[after - 1]?.serial || 0}`;
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
    const include = (value) => {
      low = Math.min(low, value);
      high = Math.max(high, value);
    };
    for (const point of prepared)
      if (point.value != null && point.time >= start && point.time <= end)
        include(point.value);
    // Outside neighbours shape the curve, but only its visible intersections
    // with the window contribute to the scale. Reuse the renderer's controls.
    let previousTime, previousValue;
    monotoneHistoryCommands(
      prepared,
      (time) => time - start,
      (value) => value,
      {
        moveTo(time, value) {
          previousTime = time;
          previousValue = value;
        },
        bezierCurveTo(_left, leftValue, _right, rightValue, time, value) {
          for (const boundary of [0, end - start])
            if (previousTime < boundary && boundary < time) {
              const fraction = (boundary - previousTime) / (time - previousTime),
                rest = 1 - fraction;
              include(
                rest ** 3 * previousValue +
                  3 * rest ** 2 * fraction * leftValue +
                  3 * rest * fraction ** 2 * rightValue +
                  fraction ** 3 * value,
              );
            }
          previousTime = time;
          previousValue = value;
        },
      },
      this.preference("tick_precision"),
    );
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
  historyValueScale(
    low,
    high,
    humidityLow,
    humidityHigh,
    cssHeight,
    humidityPrecision,
  ) {
    const geometry = this.historyGeometry(),
      epsilon = this.preference("tick_epsilon"),
      precision = this.preference("tick_precision"),
      round = (value) => Number(value.toPrecision(precision)),
      minimumTicks = Math.max(this.preference("history_axis_min_ticks"), 1),
      intervals = Math.max(
        minimumTicks,
        Math.min(
          this.preference("history_axis_max_ticks"),
          Math.floor(
            ((geometry.bottom - geometry.top) * cssHeight) /
              geometry.height /
              this.preference("history_value_tick_spacing_px"),
          ),
        ),
      ),
      axis = (minimum, maximum, intervals, resolution, alignTop = false) => {
        const rough = Math.max((maximum - minimum) / intervals, resolution);
        let unit = rough >= 1 ? Math.max(1, resolution) : resolution;
        for (;;) {
          const lower = round(Math.floor(minimum / unit) * unit),
            upper = round(Math.ceil(maximum / unit) * unit),
            step = round(
              Math.ceil((upper - lower) / intervals / unit - epsilon) * unit,
            );
          if (step >= 1 && unit < 1) {
            unit = 1;
            continue;
          }
          const first = alignTop ? round(upper - intervals * step) : lower,
            last = alignTop ? upper : round(first + intervals * step);
          return {
            first,
            last,
            values: Array.from({ length: intervals + 1 }, (_, index) =>
              round(first + index * step),
            ),
          };
        }
      },
      temperature = axis(
        low,
        high,
        intervals,
        10 ** -this.preference("value_precision"),
        true,
      ),
      humidity = axis(humidityLow, humidityHigh, intervals, 10 ** -humidityPrecision);
    return {
      intervals,
      low: temperature.first,
      high: temperature.last,
      humidityLow: humidity.first,
      humidityHigh: humidity.last,
      temperature: temperature.values,
      humidity: humidity.values,
    };
  }
  historyModel(
    chart,
    session,
    cssWidth = this.historyGeometry().width,
    cssHeight = this.historyGeometry().height,
  ) {
    const [start, end] = this.window,
      geometry = this.historyGeometry(),
      top = geometry.top,
      bottom = geometry.bottom,
      ttl = historyMeasurementTtlSeconds(this, session) * 1000,
      positions = [...this.positions],
      domain = chart.domain || this.historyDomain(),
      zoomed = start > domain[0] || end < domain[1],
      humidityDisplay = this.historyHumidityDisplay(),
      series = new Map();
    let lo = Infinity,
      hi = -Infinity,
      humidityMinimum = Infinity,
      humidityMaximum = -Infinity;
    for (const position of positions)
      for (const quantity of ["temperature", "humidity"]) {
        const entry = this.historyPreparedSeries(
          position,
          quantity === "humidity" ? humidityDisplay.quantity : quantity,
          start,
          end,
          ttl,
          Math.max(1, ((geometry.right - geometry.left) * cssWidth) / geometry.width),
          chart.prepared,
        );
        series.set(`${position}:${quantity}`, entry);
        if (quantity === "temperature") {
          lo = Math.min(lo, entry.low);
          hi = Math.max(hi, entry.high);
        } else {
          humidityMinimum = Math.min(humidityMinimum, entry.low);
          humidityMaximum = Math.max(humidityMaximum, entry.high);
        }
      }
    const temperaturePadding = this.preference("history_temperature_padding_c"),
      temperatureStep = zoomed
        ? 10 ** -this.preference("value_precision")
        : this.preference("history_temperature_scale_step_c"),
      humidityPadding = this.preference("history_humidity_padding_percent"),
      humidityStep = zoomed
        ? 10 ** -humidityDisplay.precision
        : this.preference("history_humidity_scale_step_percent"),
      absoluteStep = 10 ** -humidityDisplay.precision,
      fallback = this.appearanceScale("temperature"),
      frozen = this.historyInputSnapshot?.model,
      dataLow = Number.isFinite(lo)
        ? Math.floor((lo - temperaturePadding) / temperatureStep) * temperatureStep
        : fallback.minimum,
      dataHigh = Number.isFinite(hi)
        ? Math.max(
            dataLow + temperatureStep,
            Math.ceil((hi + temperaturePadding) / temperatureStep) * temperatureStep,
          )
        : fallback.maximum,
      humidity = Number.isFinite(humidityMaximum) ? humidityMaximum : 0,
      humidityMargin =
        humidityDisplay.mode === "absolute" ? absoluteStep : humidityPadding,
      dataHumidityLow =
        zoomed && Number.isFinite(humidityMinimum)
          ? Math.max(
              0,
              Math.floor((humidityMinimum - humidityMargin) / humidityStep) *
                humidityStep,
            )
          : 0,
      dataHumidityHigh =
        humidityDisplay.mode === "absolute"
          ? Math.max(
              dataHumidityLow + absoluteStep,
              Math.ceil((humidity + (zoomed ? absoluteStep : 0)) / absoluteStep) *
                absoluteStep,
            )
          : Math.max(
              zoomed
                ? dataHumidityLow + humidityStep
                : this.preference("history_humidity_minimum_maximum_percent"),
              Math.ceil((humidity + humidityPadding) / humidityStep) * humidityStep,
            ),
      valueScale =
        (frozen?.humidityMode === humidityDisplay.mode ? frozen.valueScale : null) ||
        this.historyValueScale(
          dataLow,
          dataHigh,
          dataHumidityLow,
          dataHumidityHigh,
          cssHeight,
          humidityDisplay.precision,
        ),
      { low, high, humidityLow, humidityHigh } = valueScale,
      scaleX = geometry.width / Math.max(1, cssWidth),
      labelGeometry = chart.interaction.cachedGeometry,
      labelInset = this.preference("layout_space_13_px"),
      labelGap = this.preference("control_group_gap_px"),
      temperatureLabelWidth = Math.max(
        this.historyLabelWidth("°C", labelGeometry),
        ...valueScale.temperature.map((value) =>
          this.historyLabelWidth(
            this.num(value, this.preference("value_precision")),
            labelGeometry,
          ),
        ),
      ),
      humidityLabelWidth = Math.max(
        this.historyLabelWidth(humidityDisplay.unit, labelGeometry),
        ...valueScale.humidity.map((value) =>
          this.historyLabelWidth(
            this.num(value, humidityDisplay.precision),
            labelGeometry,
          ),
        ),
      ),
      maximumGutter = Math.max(
        0,
        (geometry.width - this.preference("size_32_0_px") * scaleX) / 2,
      ),
      left =
        frozen?.left ??
        Math.min(
          maximumGutter,
          Math.max(
            geometry.left,
            (labelInset + temperatureLabelWidth + labelGap) * scaleX,
          ),
        ),
      right =
        frozen?.right ??
        geometry.width -
          Math.min(
            maximumGutter,
            Math.max(
              geometry.width - geometry.right,
              (labelInset + humidityLabelWidth + labelGap) * scaleX,
            ),
          ),
      x = (time) => left + ((time - start) / (end - start)) * (right - left),
      yT = (value) => bottom - ((value - low) / (high - low)) * (bottom - top),
      yH = (value) =>
        bottom -
        ((value - humidityLow) / (humidityHigh - humidityLow)) * (bottom - top);

    return {
      start,
      end,
      left,
      right,
      top,
      bottom,
      width: this.historyGeometry().width,
      height: this.historyGeometry().height,
      coordinatePrecision: this.preference("chart_coordinate_precision"),
      low,
      high,
      humidityLow,
      humidityHigh,
      valueScale,
      humidityMode: humidityDisplay.mode,
      humidityUnit: humidityDisplay.unit,
      humidityPrecision: humidityDisplay.precision,
      ttl,
      positions,
      series,
      x,
      yT,
      yH,
    };
  }
  historyPhaseAt(time) {
    const session = this.shown?.session,
      started = stamp(session?.timeline?.session_started_at),
      ended = stamp(session?.ended_at),
      intervals = this.shown?.phase_projection?.intervals || [];
    if (
      (Number.isFinite(started) && time < started) ||
      (Number.isFinite(ended) && time >= ended)
    )
      return { phase: "aus" };
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
  normalHistoryEvents(references = false) {
    return (this.shown?.session?.timeline?.processed || [])
      .map((event, index) => ({
        ...event,
        event_id: event.event_id || `legacy-${index}`,
        ...(references
          ? { source_ref: event, display_source: "session.timeline.processed" }
          : {}),
      }))
      .filter(
        (event) =>
          !["ventilation_confirmed", "ventilation_complete"].includes(event.kind),
      );
  }
  historyHeaterEvents() {
    const source = "session.heating.intervals",
      identity = this.historyValueIdentity(),
      starts = new Map();
    return (this.shown?.session?.heating?.intervals || []).flatMap((interval) => {
      const start = stamp(interval.started_at),
        end = stamp(interval.ended_at);
      if (
        !Number.isFinite(start) ||
        (interval.ended_at && (!Number.isFinite(end) || end < start))
      )
        return [];
      const occurrence = starts.get(start) || 0;
      starts.set(start, occurrence + 1);
      const key = `history-heater:${encodeURIComponent(JSON.stringify([identity, source, start, occurrence]))}`,
        boundary = (kind, at, label) => ({
          event_id: `${key}:${kind}`,
          kind: `history_heater_${kind}`,
          effective_at: at,
          history_category: "heater",
          history_interval_key: key,
          display_only: true,
          display_source: source,
          display_label: label,
          source_ref: interval,
          source_boundary: kind,
        });
      return [
        boundary("on", interval.started_at, "Ofen ein"),
        ...(interval.ended_at && interval.end_reason === "feedback_off"
          ? [boundary("off", interval.ended_at, "Ofen aus")]
          : []),
      ];
    });
  }
  historyEventSources() {
    return [...this.normalHistoryEvents(true), ...this.historyHeaterEvents()];
  }
  historyEventCategory(event) {
    if (event.history_category) return event.history_category;
    if (event.kind === "infusion") return "infusion";
    if (event.kind === "door_open" || event.kind === "door_close") return "door";
    return null;
  }
  historyEventCategoryVisible(kind) {
    const definition = this.appearanceCatalog().history_events?.[kind];
    if (!definition) return false;
    if (!this.historyEventVisibility.has(kind))
      this.historyEventVisibility.set(kind, definition.default_visible);
    return this.historyEventVisibility.get(kind);
  }
  historyEventVisible(event) {
    const category = this.historyEventCategory(event);
    return !category || this.historyEventCategoryVisible(category);
  }
  historyVisibleEvents(source) {
    const events = this.historyEventProjection().events;
    if (!source) return events;
    const ids = new Set(source.map((event) => event.event_id));
    return events.filter((event) => ids.has(event.event_id));
  }
  historyEventVisibilityKey() {
    return JSON.stringify(
      Object.entries(this.appearanceCatalog().history_events || {}).map(
        ([kind, definition]) => [
          kind,
          this.historyEventCategoryVisible(kind),
          definition,
        ],
      ),
    );
  }
  historyEventLabel(event) {
    return event.display_label || events[event.kind] || event.kind;
  }
  toggleHistoryEventCategory(kind) {
    if (!this.appearanceCatalog().history_events?.[kind]) return;
    this.historyEventVisibility.set(kind, !this.historyEventCategoryVisible(kind));
    const source = new Map(
        this.historyEventProjection().allEvents.map((event) => [event.event_id, event]),
      ),
      hiddenId = (id) => {
        const event = source.get(id);
        return event && !event.history_visible;
      },
      selectionWithoutHiddenEvents = (selection) => {
        if (!selection) return selection;
        const next = { ...selection };
        if (next.eventIds?.some(hiddenId)) {
          delete next.eventIds;
          delete next.sourceGroupKey;
          delete next.eventLabel;
        }
        for (const boundary of ["startSnap", "endSnap"])
          if (next[boundary]?.eventId && hiddenId(next[boundary].eventId))
            next[boundary] = null;
        return next;
      },
      previewWithoutHiddenEvents = (preview) => {
        if (!preview) return preview;
        const next = { ...preview };
        if (next.eventId && hiddenId(next.eventId)) {
          delete next.eventId;
          delete next.kind;
          delete next.label;
          next.snap = null;
        }
        if (next.historyEventId && hiddenId(next.historyEventId))
          delete next.historyEventId;
        if (next.snap?.eventId && hiddenId(next.snap.eventId)) next.snap = null;
        return next;
      };
    this.historyValueSelection = selectionWithoutHiddenEvents(
      this.historyValueSelection,
    );
    if (this.historySelectionDrag)
      this.historySelectionDrag.selection = selectionWithoutHiddenEvents(
        this.historySelectionDrag.selection,
      );
    this.historyHoverPreview = previewWithoutHiddenEvents(this.historyHoverPreview);
    this.historyKeyboardTarget = previewWithoutHiddenEvents(this.historyKeyboardTarget);
    this.lastHistoryPointer = previewWithoutHiddenEvents(this.lastHistoryPointer);
    if (this.pendingHover) {
      this.pendingHover = previewWithoutHiddenEvents(this.pendingHover);
      this.pendingHover.preview = previewWithoutHiddenEvents(this.pendingHover.preview);
    }
    this.syncHistorySelection();
    this.historyChart?.interaction.refreshHover();
    this.historyChart?.interaction.invalidateGeometry("layout");
    this.scheduleHistoryRender("events");
  }
  historyEventsAt(time, tolerance = 0) {
    return orderedHistoryEvents(this.historyVisibleEvents()).filter((event) => {
      const at = stamp(event.effective_at);
      return Number.isFinite(at) && Math.abs(at - time) <= Math.max(0, tolerance);
    });
  }
  historyMainEvents(source, selection = null) {
    const events = this.historyEventProjection().mainEvents,
      ids = source ? new Set(source.map((event) => event.event_id)) : null;
    return events.filter((event) => {
      if (ids && !ids.has(event.event_id)) return false;
      if (!selection) return true;
      const at = stamp(event.effective_at);
      return (
        Number.isFinite(at) &&
        (selection.kind === "range"
          ? at >= selection.start && at < selection.end
          : at === selection.start)
      );
    });
  }
  historySelectionEvents() {
    const selection = this.historyValueSelection;
    if (!selection) return [];
    if (!this.historyDetail) return this.historyMainEvents(undefined, selection);
    return orderedHistoryEvents(this.historyVisibleEvents()).filter((event) => {
      const at = stamp(event.effective_at);
      return (
        Number.isFinite(at) &&
        (selection.kind === "range"
          ? event.history_category && at >= selection.start && at < selection.end
          : at === selection.start)
      );
    });
  }
  historyEventDotSize() {
    return Math.min(
      this.preference("history_event_dot_size_px"),
      this.preference("event_marker_size_px"),
    );
  }
  historyBandGap() {
    return Math.min(
      Math.max(
        this.preference("event_marker_gap_px"),
        this.preference("stroke_1_0_ratio"),
      ),
      this.historyEventDotSize() / 2,
    );
  }
  historyEventColor(event) {
    const color =
      this.appearanceCatalog().history_events?.[this.historyEventCategory(event)]
        ?.color;
    return color
      ? `var(--sauna-color-${color.replaceAll("_", "-")})`
      : "var(--sauna-color-chart-event)";
  }
  historyEventProjection() {
    // A read-only view of the Timeline/Gang and heating interval owners.
    // Display references never become persisted events or a second lifecycle.
    const session = this.shown?.session;
    if (!session)
      return { allEvents: [], events: [], mainEvents: [], groups: [], snapTargets: [] };
    const allEvents = this.historyEventSources().map((event) => {
        const category = this.historyEventCategory(event),
          label = this.historyEventLabel(event),
          at = stamp(event.effective_at);
        return {
          ...event,
          history_category: category,
          history_visible: this.historyEventVisible(event),
          display_label: label,
          snap:
            category && Number.isFinite(at)
              ? { time: at, eventId: event.event_id, kind: event.kind, label }
              : null,
        };
      }),
      source = allEvents.filter(
        (event) =>
          historyEventMarkerKinds.has(event.kind) &&
          Number.isFinite(stamp(event.effective_at)),
      ),
      groups = [],
      horizon = stamp(session.ended_at || this.state?.now),
      append = (kind, key, eventsInGroup, open = false, gang = null) => {
        const members = orderedHistoryEvents(eventsInGroup).map((event) => ({
          event,
          at: stamp(event.effective_at),
        }));
        if (!members.length) return;
        const first = members[0],
          last = members.at(-1);
        groups.push({
          kind,
          key,
          members,
          first,
          last,
          start: first.at,
          end: open && Number.isFinite(horizon) ? Math.max(first.at, horizon) : last.at,
          open,
          source_refs: members.map(({ event }) => event.source_ref),
          gang_ref: gang,
        });
        return groups.at(-1);
      };
    const timeline = session.timeline,
      gangs = [
        ...(timeline.completed || []),
        ...(timeline.active ? [timeline.active] : []),
      ],
      doors = new Map(
        source
          .filter((event) => event.history_category === "door")
          .map((event) => [event.event_id, event]),
      );
    for (const cycle of session.door_cycles || []) {
      const members = (cycle.event_ids || []).map((id) => doors.get(id)),
        start = stamp(cycle.started_at),
        end = stamp(cycle.ended_at);
      if (
        !members.length ||
        members.some((event) => !event) ||
        !Number.isFinite(start) ||
        (cycle.ended_at && (!Number.isFinite(end) || end < start))
      )
        continue;
      const group = append("door", `door:${cycle.id}`, members, cycle.open);
      group.start = start;
      group.end = Number.isFinite(end)
        ? end
        : cycle.open && Number.isFinite(horizon)
          ? Math.max(start, horizon)
          : start;
      group.door_cycle_ref = cycle;
      group.gang_links = cycle.gang_links;
      group.roleLabel =
        cycle.role === "entry"
          ? "Eintritt"
          : cycle.role === "exit"
            ? "Austritt"
            : undefined;
    }
    const infusions = new Map(
        source
          .filter((event) => event.kind === "infusion")
          .map((event) => [event.event_id, event]),
      ),
      claimed = new Set();
    for (const gang of gangs) {
      const members = [
        ...new Set((gang.infusion_events || []).map((event) => event.event_id)),
      ]
        .filter((id) => infusions.has(id) && !claimed.has(id))
        .map((id) => {
          claimed.add(id);
          return infusions.get(id);
        });
      append("infusion", `infusion:${gang.gang_id}`, members, false, gang);
    }
    for (const [id, event] of infusions)
      if (!claimed.has(id)) append("infusion", `event:${id}`, [event]);
    const heating = new Map();
    for (const event of allEvents.filter(
      (event) => event.history_category === "heater",
    )) {
      if (!heating.has(event.history_interval_key))
        heating.set(event.history_interval_key, []);
      heating.get(event.history_interval_key).push(event);
    }
    for (const [key, members] of heating) {
      const interval = members[0].source_ref,
        group = append("heater", key, members, !interval.ended_at),
        end = stamp(interval.ended_at);
      // An unknown end closes the confirmed heating span without claiming
      // an OFF event or supplying an OFF snap target.
      if (Number.isFinite(end)) group.end = end;
    }
    for (const group of groups) {
      group.label = this.historyEventBandLabel(group);
      group.selectionLabel =
        group.roleLabel || (group.kind === "heater" ? group.label : undefined);
      group.visible = group.first.event.history_visible;
      group.annotation =
        group.kind === "heater"
          ? "heater"
          : group.kind === "door" && group.first.event.kind === "door_open"
            ? "door"
            : null;
      group.snapTargets = group.members.map(({ event }) => event.snap).filter(Boolean);
      group.children =
        group.kind === "infusion"
          ? group.members.map((member) => ({
              kind: group.kind,
              key: `event:${member.event.event_id}`,
              members: [member],
              first: member,
              last: member,
              start: member.at,
              end: member.at,
              open: false,
              label: member.event.display_label,
              source_refs: [member.event.source_ref],
            }))
          : [];
    }
    const events = orderedHistoryEvents(
        allEvents.filter((event) => event.history_visible),
      ),
      mainEvents = events.filter((event) => event.history_category);
    return {
      allEvents,
      events,
      mainEvents,
      groups: groups.filter((group) => group.visible).sort((a, b) => a.start - b.start),
      snapTargets: mainEvents.map((event) => event.snap).filter(Boolean),
    };
  }
  historyEventGroups() {
    return this.historyEventProjection().groups;
  }
  historyStripEventGroups(model, geometry) {
    const plotWidth =
        ((model.right - model.left) *
          (geometry?.rects?.surface?.width ?? model.width)) /
        model.width,
      view = this.historyViewTransition?.target || [model.start, model.end],
      domain = this.historyDomain(),
      fullView = view[0] <= domain[0] && view[1] >= domain[1],
      focus = this.historyValueSelection,
      separation =
        this.preference("event_marker_size_px") +
        this.preference("event_marker_gap_px");
    if (!(plotWidth > 0)) return [];
    return this.historyEventGroups().flatMap((group) => {
      const focused =
          !fullView &&
          focus?.identity === this.historyValueIdentity() &&
          focus.sourceGroupKey === group.key,
        children = group.children.length ? group.children : [group],
        center = (item) => item.start + (item.end - item.start) / 2,
        resolved =
          !fullView &&
          children.length > 1 &&
          (focused ||
            children
              .slice(1)
              .every(
                (item, index) =>
                  ((center(item) - center(children[index])) * plotWidth) /
                    (view[1] - view[0]) >=
                  separation,
              )),
        displayed = resolved ? children : [group];
      return displayed
        .filter((item) => item.start <= model.end && item.end >= model.start)
        .map((item) => ({ ...item, at: item.start + (item.end - item.start) / 2 }));
    });
  }
  historyEventGroupLabel(members) {
    const doorCount = members.filter((event) => event.kind !== "infusion").length,
      infusionCount = members.length - doorCount;
    if (!infusionCount) return `Tür (${doorCount})`;
    if (!doorCount) return `Aufgüsse (${infusionCount})`;
    return `${doorCount} ${doorCount === 1 ? "Türereignis" : "Türereignisse"} · ${infusionCount} ${infusionCount === 1 ? "Aufguss" : "Aufgüsse"}`;
  }
  historyLabelWidth(label, geometry) {
    if (!this.historyLabelContext)
      this.historyLabelContext = this.ownerDocument
        ?.createElement?.("canvas")
        ?.getContext?.("2d");
    const fontSize = this.preference("font_size_small_px");
    if (!this.historyLabelContext) return label.length * fontSize;
    this.historyLabelContext.font = `${this.preference("font_weight_600_0_ratio")} ${fontSize}px ${geometry?.labelFontFamily || "sans-serif"}`;
    return this.historyLabelContext.measureText(label).width;
  }
  historyEventBandLabel(group) {
    if (group.kind === "heater")
      return this.appearanceCatalog().history_events?.heater?.label;
    if (group.roleLabel) return group.roleLabel;
    if (group.open) return "Tür offen";
    if (group.members.length > 1)
      return group.kind === "door" ? "Türvorgang" : "Aufgüsse";
    const event = group.first.event;
    return this.historyEventLabel(event);
  }
  historyStripEvents(model, geometry) {
    const groups = this.historyStripEventGroups(model, geometry),
      plotWidth = model.right - model.left,
      position = (time) => ((model.x(time) - model.left) / plotWidth) * 100,
      bars = groups
        .map((group) => {
          const { members, first, start, end, open, kind } = group,
            ids = members.map(({ event }) => event.event_id),
            key = encodeURIComponent(JSON.stringify(ids)),
            grouped = ids.length > 1,
            label = group.label,
            when =
              start === end
                ? this.tooltipWhen(start)
                : `${this.tooltipWhen(start)} – ${this.tooltipWhen(end)}`,
            left = position(Math.max(model.start, start)),
            right = position(Math.min(model.end, end)),
            overlapping = groups.filter(
              (other) =>
                other !== group &&
                other.kind !== kind &&
                other.start <= end &&
                other.end >= start,
            ),
            overlap = overlapping.length > 0,
            action = grouped
              ? `history-event-group:${key}`
              : `history-event:${first.event.event_id}`;
          return `<button type="button" class="history-event-band-segment" data-key="history-band:${esc(group.key)}" data-action="${esc(action)}" data-history-event-kind="${kind}" data-history-event-members="${esc(key)}"${grouped ? ` data-history-event-group="${esc(key)}"` : ` data-history-event-id="${esc(first.event.event_id)}"`} data-point="${start === end}" data-open="${open}" data-overlap="${overlap}" data-overlap-heater="${overlapping.some((other) => other.kind === "heater")}" data-overlap-door="${overlapping.some((other) => other.kind === "door")}" aria-pressed="false" aria-label="${esc(label)} · ${esc(when)}" title="${esc(label)} · ${esc(when)}" style="left: ${left}%; width: ${right - left}%; --history-event-color: ${this.historyEventColor(first.event)}"><span>${esc(label)}</span></button>`;
        })
        .join(""),
      names = Object.entries(this.appearanceCatalog().history_events || {})
        .map(
          ([kind, definition]) =>
            `<button type="button" class="history-event-category" data-key="history-category:${esc(kind)}" data-action="history-event-category:${esc(kind)}" aria-pressed="${this.historyEventCategoryVisible(kind)}" style="--history-event-color: var(--sauna-color-${esc(definition.color.replaceAll("_", "-"))})">${esc(definition.label)}</button>`,
        )
        .join(""),
      row = `<div class="history-event-band-row" role="group" aria-label="Ereignisse">${bars}</div><div class="history-band-names">${names}</div>`;
    return row;
  }
  historyEventMarkers(model, geometry) {
    return this.historyStripEventGroups(model, geometry)
      .map(({ members, at }) => {
        const x = model.x(at),
          first = members[0].event,
          sameColor = members.every(
            ({ event }) => (event.kind === "infusion") === (first.kind === "infusion"),
          ),
          color = sameColor
            ? this.historyEventColor(first)
            : "var(--sauna-color-chart-event)",
          key = encodeURIComponent(
            JSON.stringify(members.map(({ event }) => event.event_id)),
          );
        return `<line class="history-event-guide" data-key="history-event-guide:${esc(key)}" data-history-event-members="${esc(key)}" vector-effect="non-scaling-stroke" style="--history-event-color: ${color}" x1="${x}" x2="${x}" y1="${model.top}" y2="${model.bottom}"/>`;
      })
      .join("");
  }
  historyAnnotations(model, session, gangs) {
    const { start, end, left, right, top, bottom, x } = model;
    const interval = (a, b, klass, title, y = top, height = bottom - top) => {
      const aa = Math.max(start, stamp(a)),
        bb = Math.min(end, stamp(b || session.ended_at || this.state.now));
      return bb > aa
        ? `<rect class="${klass}" x="${x(aa).toFixed(this.preference("chart_coordinate_precision"))}" y="${y}" width="${(x(bb) - x(aa)).toFixed(this.preference("chart_coordinate_precision"))}" height="${height}"><title>${esc(title)}</title></rect>`
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
    for (const group of this.historyEventGroups()) {
      if (group.annotation === "door")
        svg += interval(group.start, group.end, "door", "Saunatür offen");
      else if (group.annotation === "heater")
        svg += `<g style="--history-event-color: ${this.historyEventColor(group.first.event)}">${interval(group.start, group.end, "history-heater-interval", group.label)}</g>`;
    }
    const gridTicks = model.valueScale.intervals;
    for (let n = 0; n <= gridTicks; n++) {
      const yy = top + ((bottom - top) * n) / gridTicks;
      svg += `<line class="gridline" x1="${left}" x2="${right}" y1="${yy}" y2="${yy}"/>`;
    }
    for (const e of this.historyMainEvents().filter((e) => e.kind === "infusion")) {
      const xx = x(stamp(e.effective_at));
      svg += `<line class="infusion" x1="${xx}" x2="${xx}" y1="${top}" y2="${bottom}"><title>Aufguss · ${this.clock(e.effective_at)}</title></line>`;
    }
    return svg;
  }
  historyAxes(model, { width = model.width, height = model.height, geometry } = {}) {
    const { start, end, left, right, top, bottom, valueScale, x } = model;
    const timeZone = localTimeZone();
    const scaleX = model.width / Math.max(1, width),
      scaleY = model.height / Math.max(1, height);
    // Cancel the SVG's unequal viewport scaling only for labels. Curves and
    // annotation positions continue to share their existing plot coordinates.
    const text = (label, px, py, klass = "", anchor = "start", attributes = "") =>
      `<text class="${klass}" font-size="${this.preference("font_size_small_px")}" text-anchor="${anchor}" transform="translate(${px} ${py}) scale(${scaleX} ${scaleY})"${attributes}>${esc(label)}</text>`;
    let svg = "";
    for (let n = 0; n <= valueScale.intervals; n++) {
      const fraction = n / valueScale.intervals,
        y =
          bottom -
          (bottom - top) * fraction +
          (this.preference("font_size_small_px") / 3) * scaleY;
      svg += text(
        this.num(valueScale.temperature[n], this.preference("value_precision")),
        left - this.preference("control_group_gap_px") * scaleX,
        y,
        "axis-temperature",
        "end",
      );
      svg += text(
        this.num(
          valueScale.humidity[n],
          model.humidityPrecision ?? this.preference("value_precision"),
        ),
        right + this.preference("control_group_gap_px") * scaleX,
        y,
        "axis-humidity",
        "start",
      );
    }
    const temperatureHeadingLeft =
        left - this.preference("control_group_gap_px") * scaleX,
      humidityHeadingRight = right + this.preference("control_group_gap_px") * scaleX,
      humidityUnit = model.humidityMode === "absolute" ? "g/m³" : "%",
      phaseDimensions = this.historyBandDimensions("phase", geometry),
      detail = !!this.historyBandDetailState?.detail,
      phaseBandHeight = detail ? phaseDimensions.height : phaseDimensions.compactHeight,
      labelHeight =
        this.preference("font_size_small_px") * this.preference("body_line_height"),
      headingOffset = detail
        ? phaseBandHeight / 2
        : phaseBandHeight +
          this.historyBandGap() +
          (geometry?.bandNames?.phase || labelHeight) / 2,
      headingY =
        top - (this.historyBandGap() + Math.max(headingOffset, labelHeight)) * scaleY;
    const humidityLabel =
        model.humidityMode === "absolute"
          ? "Absolute Feuchte (g/m³)"
          : "Relative Feuchte (%)",
      titleHeight = Math.max(phaseBandHeight, labelHeight) * scaleY,
      titleTop = headingY - titleHeight / 2,
      humidityTitleLeft = right,
      humidityTitleRight = model.width - this.preference("layout_space_13_px") * scaleX;
    return (
      svg +
      text(
        "°C",
        temperatureHeadingLeft,
        headingY,
        "axis-temperature",
        "end",
        ' dominant-baseline="central"',
      ) +
      `<g role="button" tabindex="0" data-action="history-humidity" aria-pressed="${model.humidityMode === "absolute"}" aria-label="${humidityLabel}, ${model.humidityMode === "absolute" ? "zu relativer Feuchte wechseln" : "zu absoluter Feuchte wechseln"}"><rect x="${humidityTitleLeft}" y="${titleTop}" width="${Math.max(0, humidityTitleRight - humidityTitleLeft)}" height="${titleHeight}" fill="transparent" stroke="none" pointer-events="all"/>` +
      text(
        humidityUnit,
        humidityHeadingRight,
        headingY,
        "axis-humidity",
        "start",
        ' dominant-baseline="central"',
      ) +
      "</g>"
    );
  }
  historyTimeAxis(model, { width = model.width } = {}) {
    const { start, end, left, right, x } = model,
      timeZone = localTimeZone(),
      scaleX = model.width / Math.max(1, width),
      secondsFormatter = timestampFormatter(
        "clockSeconds",
        timeZone,
        this.preference("timestamp_formatter_cache_entries"),
      ),
      labelSpacing = Math.max(
        this.preference("history_time_tick_spacing_px"),
        this.historyLabelWidth(
          secondsFormatter.format(new Date(start)),
          this.historyChart?.interaction.cachedGeometry,
        ) +
          this.preference("control_group_gap_px") * 2,
      ),
      timeTicks = Math.max(
        1,
        Math.min(
          this.preference("history_axis_max_ticks"),
          Math.floor((right - left) / scaleX / labelSpacing),
        ),
      ),
      rough = (end - start) / timeTicks,
      second = 1000,
      minute = 60 * second,
      hour = 60 * minute,
      day = 24 * hour,
      // Conventional clock divisions; spacing and count remain catalogue-led.
      steps = [second, minute]
        .flatMap((unit) => [1, 2, 5, 10, 15, 20, 30].map((count) => count * unit))
        .concat([1, 2, 3, 6, 12].map((count) => count * hour)),
      step = steps.find((value) => value >= rough) || Math.ceil(rough / day) * day,
      dateFormatter =
        step >= day
          ? timestampFormatter(
              "date",
              timeZone,
              this.preference("timestamp_formatter_cache_entries"),
            )
          : null,
      align = (reference) => {
        let candidate = reference;
        // Re-evaluate the historical offset if rounding crosses a DST change.
        for (let pass = 0; pass < 2; pass++) {
          const offset = new Date(candidate).getTimezoneOffset() * minute;
          candidate = Math.ceil((reference - offset) / step) * step + offset;
        }
        return candidate;
      };
    let svg = "",
      time = align(start);
    for (
      let index = 0;
      index <= timeTicks && time <= end;
      index++, time = align(time + 1)
    ) {
      const offset = new Date(time).getTimezoneOffset() * minute;
      if ((time - offset) % step !== 0) continue;
      const label =
        step < minute
          ? secondsFormatter.format(new Date(time))
          : dateFormatter
            ? dateFormatter.format(new Date(time))
            : this.clock(time, timeZone);
      svg += `<text data-history-time="${time}" font-size="${this.preference("font_size_small_px")}" text-anchor="middle" transform="translate(${x(time)} ${this.preference("font_size_small_px") * this.preference("body_line_height")}) scale(${scaleX} 1)">${esc(label)}</text>`;
    }
    return svg;
  }
  hoverChart(event) {
    if (
      this.historySelectionDrag ||
      this.webkitHistoryGesture ||
      this.chartPointers?.size > 1
    )
      return this.historyChart?.interaction.refreshHover(this.historyChart.model);
    this.historyChart?.interaction.hover(event, this.historyChart.model);
  }
  zoomAt(factor, clientX, svg) {
    if (!this.window) return;
    const plot =
      this.historyInputSnapshot?.model ||
      this.historyChart?.model ||
      this.historyGeometry();
    const px = this.svgCoordinates(svg, clientX).x,
      fraction = Math.max(0, Math.min(1, (px - plot.left) / (plot.right - plot.left)));
    const [domainStart, domainEnd] =
      this.historyInputSnapshot?.domain || this.historyDomain();
    const currentWidth = this.window[1] - this.window[0],
      minimum = Math.min(
        currentWidth,
        (domainEnd - domainStart) / this.preference("history_max_zoom"),
      ),
      nextWidth = Math.max(
        minimum,
        Math.min(domainEnd - domainStart, currentWidth / factor),
      ),
      focus = this.window[0] + fraction * (this.window[1] - this.window[0]);
    if (nextWidth === currentWidth) return;
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
    const svg = this.eventElement(event, "svg.session-chart, svg.history-time-axis");
    if (!svg) return;
    if (this.historyInputMode === "wheel") this.endHistoryWheel();
    if (this.historyInputMode && this.historyInputMode !== "webkit") {
      event.preventDefault();
      this.webkitHistoryGesture = { suppressed: true };
      return;
    }
    this.historyInputMode = "webkit";
    this.beginHistoryInput();
    this.historyChart?.interaction.invalidateGeometry("geometry", false);
    const rect =
      this.historyChart?.interaction.readGeometry()?.rects?.[
        svg.matches?.("[data-history-time-axis]") ? "timeAxis" : "surface"
      ] || svg.getBoundingClientRect();
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
    if (suppressed) {
      this.finishHistoryInput();
      return;
    }
    if (this.historyInputMode === "webkit") this.historyInputMode = null;
    this.finishHistoryInput();
    this.scheduleHistoryRender();
  }
  wheelHistoryGesture(event, svg) {
    event.preventDefault();
    if (this.historyInputMode && this.historyInputMode !== "wheel") return;
    this.beginHistoryInput();
    this.historyInputMode = "wheel";
    this.zoomAt(
      Math.exp(-event.deltaY * this.preference("history_wheel_zoom_coefficient")),
      event.clientX,
      svg,
    );
    if (this.historyWheelTimer != null) clearTimeout(this.historyWheelTimer);
    this.historyWheelTimer = setTimeout(
      () => this.endHistoryWheel(),
      this.preference("history_wheel_idle_ms"),
    );
  }
  endHistoryWheel() {
    if (this.historyWheelTimer != null) clearTimeout(this.historyWheelTimer);
    this.historyWheelTimer = null;
    if (this.historyInputMode !== "wheel") return;
    this.historyInputMode = null;
    this.finishHistoryInput();
  }
  eventMarkerLayout(
    markers,
    start,
    end,
    stripWidth,
    markerSize = this.preference("event_marker_size_px"),
    markerGap = this.preference("event_marker_gap_px"),
  ) {
    const width =
      Number.isFinite(stripWidth) && stripWidth > 0
        ? stripWidth
        : markerSize * this.preference("event_marker_fallback_count");
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
      const route = this.diagnosticRoute(event, traces),
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
      if (/^(strong_|weak_)/.test(metric) && !this.diagnosticPersonDataActive(trace.at))
        return [];
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
  diagnosticRoute(event, traces = null) {
    if (event?.kind === "ventilation_confirmed") {
      const data = this.diagnosticData(),
        at = stamp(
          event.trace_at || data.traceTimes.get(event.event_id) || event.effective_at,
        ),
        candidates =
          !traces || traces === data.traces
            ? data.byTime.get(at) || []
            : traces.filter((trace) => stamp(trace.at) === at),
        metric =
          ["ventilation_relative_humidity_slope", "ventilation_water_slope"].find(
            (metric) =>
              Number.isFinite(at) &&
              candidates.some(
                (trace) =>
                  (trace.signals || []).includes("ventilation_confirmed") &&
                  ["upper", "lower"].some((position) =>
                    Number.isFinite(trace.metrics?.[position]?.[metric]),
                  ),
              ),
          ) || "ventilation_temperature_loss";
      return [metric, "ventilation_confirmed"];
    }
    return (
      {
        door_open: ["door_temperature_slope", "door_open"],
        door_close: ["door_temperature_slope", "door_close"],
        person_strong: ["strong_humidity_slope", "person_strong"],
        person_weak: ["weak_humidity_slope", "person_weak"],
        infusion: ["infusion_humidity_delta", "infusion"],
        ventilation_complete: ["ventilation_water_slope", "ventilation_complete"],
      }[event?.kind] || null
    );
  }
  diagnosticTraceForEvent(traces, event, signal, metric) {
    const route = this.diagnosticRoute(event, traces);
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
    if (/^(strong_|weak_)/.test(metric) && !this.diagnosticPersonDataActive(effective))
      return null;
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
  diagnosticMetricDefinitions(at = null) {
    return [
      ["Türerkennung", "door_temperature_slope", "Temperaturänderung · °C/min"],
      ["Türerkennung", "door_humidity_delta", "Feuchteänderung · Prozentpunkte"],
      [
        "Starke Personenerkennung",
        "strong_temperature_slope",
        "Temperaturtrend (Kontext) · °C/min",
      ],
      [
        "Starke Personenerkennung",
        "strong_humidity_slope",
        "Feuchtetrend · Prozentpunkte/min",
      ],
      [
        "Schwache Personenerkennung",
        "weak_temperature_slope",
        "Temperaturtrend (Kontext) · °C/min",
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
      [
        "Aufgusserkennung",
        "infusion_temperature_delta",
        "Temperaturänderung (Kontext) · °C",
      ],
      [
        "Starke Personenerkennung",
        "strong_absolute_humidity_delta",
        "Absoluter Feuchteanstieg · g/m³",
      ],
      [
        "Schwache Personenerkennung",
        "weak_absolute_humidity_delta",
        "Absoluter Feuchteanstieg · g/m³",
      ],
      [
        "Aufgusserkennung",
        "infusion_absolute_humidity_delta",
        "Absoluter Feuchteanstieg · g/m³",
      ],
      [
        "Lüftungserkennung",
        "ventilation_relative_humidity_slope",
        "Relativer Feuchtetrend · Prozentpunkte/min",
        (v) => v * 60,
      ],
      [
        "Lüftungserkennung",
        "ventilation_water_slope",
        "Absoluter Feuchtetrend · g/m³/min",
        (v) => v * 60,
      ],
      ["Lüftungserkennung", "ventilation_temperature_loss", "Temperaturverlust · °C"],
      [
        "Lüftungserkennung",
        "ventilation_absolute_humidity_loss",
        "Absoluter Feuchteverlust · %",
        (v) => v * 100,
      ],
    ].filter(
      ([, metric]) =>
        this.diagnosticPersonDataActive(at) || !/^(strong_|weak_)/.test(metric),
    );
  }
  diagnosticSelectionMarkup(traces) {
    const event = this.eventNavigation().find(
        (item) =>
          item.event_id === this.highlightedEventId &&
          (!["person_strong", "person_weak"].includes(item.kind) ||
            this.diagnosticPersonDataActive(
              item.trace_at || item.detected_at || item.effective_at,
            )),
      ),
      linked = event && this.diagnosticTraceForEvent(traces, event),
      trace =
        linked ||
        (traces.includes(this.selectedDiagnosticTrace)
          ? this.selectedDiagnosticTrace
          : null) ||
        traces.find((item) => item.at === this.selectedDiagnosticTraceAt) ||
        (!event ? traces[0] : null);
    if (!trace)
      return `<section id="diagnostic-selection" class="diagnostic-selection-context" aria-live="polite"><h3>Erkennungsnachweis</h3><p>${event ? "Für dieses Ereignis fehlt der gespeicherte Erkennungsnachweis." : "Noch keine gespeicherten Erkennungswerte."}</p></section>`;
    this.selectedDiagnosticTraceAt = trace.at;
    this.selectedDiagnosticTrace = trace;
    const index = traces.indexOf(trace),
      configurationModel = this.historyConfigurationModel(trace.at),
      personDataActive = this.diagnosticPersonDataActive(trace.at),
      activeKey = (key) => personDataActive || !/^(strong|weak|person)(_|$)/.test(key),
      definitions = new Map(
        this.diagnosticMetricDefinitions(trace.at).map((definition) => [
          definition[1],
          definition,
        ]),
      ),
      labels = {
        ...signalText,
        ventilation: "Lüftung",
        ventilation_complete: "Vollständiges Lüften",
        ventilation_context: "Lüften bestätigt",
        ventilation_recovery: "Temperaturerholung",
        ventilation_temperature_drop: "Temperaturverlust ausreichend",
        ventilation_water_loss: "Wasserverlust ausreichend",
        ventilation_time_held: "Mindestdauer erreicht",
        ventilation_sources_coherent: "Messquellen durchgängig",
        ventilation_proven: "Lüften belegt",
        ventilation_complete_proven: "Vollständiges Lüften belegt",
        ventilation_close_conflict: "Türschließung im selben Messstand erkannt",
      },
      value = (item, key, kind) => {
        if (item == null)
          return kind === "conditions" ? "Nicht geprüft" : "Nicht gespeichert";
        if (kind === "checks" && typeof item === "boolean")
          return item ? "Prüfung aktiv" : "Nicht geprüft";
        if (kind === "conditions" && typeof item === "boolean")
          return item ? "Erfüllt" : "Nicht erfüllt";
        if (kind === "holds" && typeof item === "number")
          return `${this.num(item, 0)} Prüfpunkte`;
        if (typeof item === "boolean") return item ? "Ja" : "Nein";
        const definition = definitions.get(key);
        if (definition && typeof item === "number") {
          const converted = definition[3] ? definition[3](item) : item;
          return `${this.num(converted, this.preference(key.includes("absolute_humidity") && !key.endsWith("loss") ? "absolute_humidity_precision" : "detail_value_precision"))} ${definition[2].split(" · ").at(-1)}`;
        }
        if (typeof item === "number")
          return this.num(item, this.preference("detail_value_precision"));
        return String(item);
      },
      label = (key) => {
        const definition = definitions.get(key);
        return definition
          ? `${definition[0]} · ${definition[2].split(" · ")[0]}`
          : labels[key]
            ? labels[key]
            : this.detailFieldLabel?.(key) || key.replaceAll("_", " ");
      },
      entries = (items, kind) =>
        Object.entries(items || {})
          .filter(([key]) => activeKey(key))
          .map(
            ([key, item]) =>
              `<dt data-key="label:${esc(key)}">${esc(label(key))}</dt><dd data-key="value:${esc(key)}">${item && typeof item === "object" ? `<dl>${entries(item, kind)}</dl>` : esc(value(item, key, kind))}</dd>`,
          )
          .join("") || "<dt>Angaben</dt><dd>Nicht gespeichert</dd>",
      groups = [
        ["Prüfungen", trace.checks, "checks"],
        ["Bedingungen erfüllt", trace.conditions, "conditions"],
        ["Prüfpunkte", trace.holds, "holds"],
        ...(Object.hasOwn(trace, "readiness")
          ? [["Bereitschaft", trace.readiness]]
          : []),
        ...["upper", "lower"]
          .filter(
            (position) =>
              configurationModel?.measurement_positions?.includes(position) ||
              Object.hasOwn(trace.metrics || {}, position),
          )
          .map((position) => [
            position === "upper" ? "Messwerte oben" : "Messwerte unten",
            trace.metrics?.[position],
          ]),
        [
          "Weitere Daten",
          Object.fromEntries(
            Object.entries(trace).filter(
              ([key]) =>
                ![
                  "at",
                  "checks",
                  "conditions",
                  "holds",
                  "readiness",
                  "metrics",
                  "signals",
                  "channels",
                ].includes(key),
            ),
          ),
        ],
      ];
    return `<section id="diagnostic-selection" class="diagnostic-selection-context" tabindex="-1" data-trace-at="${esc(trace.at)}"><h3>Erkennungsnachweis · ${this.when(trace.at)}</h3><p id="diagnostic-selection-label" aria-live="polite">${event ? esc(events[event.kind] || event.kind) : "Gespeicherter Prüfzeitpunkt"} · ${esc((trace.channels || []).map((position) => (position === "upper" ? "oben" : position === "lower" ? "unten" : position)).join(", ") || "Keine Messhöhe gespeichert")}${event ? ` <button data-action="event-history:${esc(event.event_id)}">Ereignis im Verlauf</button>` : ""}</p><div class="diagnostic-trace-navigation"><button data-action="diagnostic-trace:previous" ${index <= 0 ? "disabled" : ""} aria-label="Vorheriger Erkennungsnachweis">←</button><input id="diagnostic-trace-index" type="range" min="0" max="${traces.length - 1}" step="1" value="${index}" aria-label="Gespeicherten Erkennungszeitpunkt auswählen" aria-valuetext="${esc(this.when(trace.at))}"><button data-action="diagnostic-trace:next" ${index >= traces.length - 1 ? "disabled" : ""} aria-label="Nächster Erkennungsnachweis">→</button><span>${index + 1} / ${traces.length}</span></div><p>Signale: ${esc(
      (trace.signals || [])
        .filter(activeKey)
        .map((key) => events[key] || key)
        .join(", ") || "Keine",
    )}</p>${groups.map(([label, items, kind]) => `<details id="diagnostic-values-${esc(label.toLowerCase().replaceAll(" ", "-"))}"><summary>${label}</summary><dl>${entries(items, kind)}</dl></details>`).join("")}</section>`;
  }
  drawDiagnostics() {
    if (!this.state?.permissions?.admin) {
      this.updateMarkup("#detection-plots", "");
      return;
    }
    if (!this.shown) {
      this.updateMarkup("#detection-plots", "<p>Keine Saunasitzung ausgewählt.</p>");
      return;
    }
    const traces = this.diagnosticData().traces;
    const groups = this.diagnosticMetricDefinitions();
    // Archived thresholds are only those actually saved with the session.
    const parameters = this.diagnosticsParameters();
    const thresholds = (metric, position) => {
      if (
        [
          "strong_temperature_slope",
          "weak_temperature_slope",
          "infusion_temperature_delta",
        ].includes(metric)
      )
        return [];
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
    const [start, end] = this.window,
      presenceProjection = this.historyPresenceProjection(),
      sourceIntervals = presenceProjection.intervals(start, end),
      sources = [...new Set(sourceIntervals.map((interval) => interval.source))];
    const detectionPlots = this.$("#detection-plots");
    let html =
      '<details id="diagnostic-help"><summary>Hinweise zur Erkennung</summary><p class="muted">Die Kurven zeigen die gespeicherten Erkennungswerte, die gestrichelten Linien die zugehörigen gespeicherten Schwellen. Ein Grenzübertritt löst erst dann ein Ereignis aus, wenn auch die übrigen Bedingungen erfüllt sind. Ein Marker hebt seinen Erkennungsnachweis hervor. Die Gangerkennung läuft auch während der Ofenkühlung weiter. Weitere Aufgüsse werden im Gang weiter erkannt.</p></details>';
    const configurationModel = this.historyConfigurationModel(),
      configuredPositions = configurationModel?.measurement_positions || [],
      presenceCaption = !configurationModel
        ? "Die damalige Anlagenkonfiguration ist nicht gespeichert. Es werden die aufgezeichneten Merkmale gezeigt."
        : sources.length > 1
          ? "Die gespeicherten Prüfungen zeigen die zum jeweiligen Zeitpunkt wirksame Personenquelle."
          : sources[0] === "ha_presence"
            ? "Personenquelle: Präsenzsensor mit Türereignis."
            : "Personenquelle: Temperatur und Feuchte. Gespeicherte Prüfungen belegen, welche Erkennungswege zum jeweiligen Zeitpunkt aktiv waren.";
    html += `<p class="muted" id="diagnostic-configuration">${presenceCaption}${configuredPositions.length ? ` Konfigurierte Messhöhen: ${configuredPositions.map((position) => (position === "upper" ? "oben" : "unten")).join(", ")}.` : ""}</p>`;
    if (!Object.keys(parameters).length)
      html +=
        '<p class="muted" id="diagnostic-threshold-status">Für diese Sitzung sind keine historischen Schwellen gespeichert. Aktuelle Einstellungen werden nicht als damalige Schwellen angezeigt.</p>';
    if (!traces.length) {
      this.updateMarkup(
        "#detection-plots",
        html +
          "<p>Für diese Saunasitzung liegen keine gespeicherten Erkennungsverläufe vor.</p>",
      );
      this.pendingEventFocus = null;
      return;
    }
    const availableWidth =
      detectionPlots?.getBoundingClientRect?.().width ||
      this.preference("diagnostic_fallback_width_px");
    const columns =
      availableWidth > this.preference("diagnostic_columns_breakpoint_px")
        ? this.preference("diagnostic_wide_columns")
        : 1;
    const chartWidth = Math.max(
      this.preference("diagnostic_min_plot_width_px"),
      (availableWidth - (columns - 1) * this.preference("diagnostic_plot_gap_px")) /
        columns -
        this.preference("diagnostic_plot_padding_px"),
    );
    const plotLeft = this.preference("diagnostic_axis_left_padding_px"),
      plotRight = chartWidth - this.preference("diagnostic_axis_right_padding_px"),
      chartHeight = this.preference("diagnostic_plot_height_px"),
      plotTop = this.preference("diagnostic_axis_top_padding_px"),
      plotBottom = chartHeight - this.preference("diagnostic_axis_bottom_padding_px"),
      guideTop = this.preference("diagnostic_guide_top_padding_px"),
      timeLabel =
        chartHeight - this.preference("diagnostic_time_label_bottom_padding_px");
    const chartX = (t) =>
      plotLeft + ((stamp(t) - start) / (end - start)) * (plotRight - plotLeft);
    const selectedEvent = this.eventNavigation().find(
        (event) => event.event_id === this.highlightedEventId,
      ),
      selectedRoute = this.diagnosticRoute(selectedEvent),
      selectedTrace =
        selectedEvent && this.diagnosticTraceForEvent(traces, selectedEvent);
    html += '<div class="diagnostic-grid" id="diagnostic-charts">';
    const visibleTraces = traces.filter((trace) => {
      const time = stamp(trace.at);
      return Number.isFinite(time) && time >= start && time <= end;
    });
    for (const [group, metric, label, convert = (v) => v] of groups) {
      const quantity = metric.includes("temperature") ? "temperature" : "humidity",
        personMetric = /^(strong_|weak_)/.test(metric),
        metricTraces = personMetric
          ? visibleTraces.filter(
              (trace) => presenceProjection.sourceAt(trace.at) !== "ha_presence",
            )
          : visibleTraces;
      const vals = metricTraces
        .flatMap((t) => Object.values(t.metrics || {}).map((m) => m?.[metric]))
        .filter(Number.isFinite);
      // Pre-ventilation archives do not have these metrics.  Do not show an
      // empty zero line that could be mistaken for a measured loss.
      if (!vals.length) continue;
      const positions = ["upper", "lower"].filter((position) =>
          metricTraces.some((trace) =>
            Number.isFinite(trace.metrics?.[position]?.[metric]),
          ),
        ),
        lines = positions
          .flatMap((position) => thresholds(metric, position))
          .filter(Number.isFinite);
      const chartVals = vals.map(convert),
        chartLines = lines.map(convert);
      const [min, max] = [...chartVals, ...chartLines].reduce(
        ([lo, hi], v) => [Math.min(lo, v), Math.max(hi, v)],
        [0, 0],
      );
      const lo = min - this.preference("diagnostic_scale_padding"),
        hi = max + this.preference("diagnostic_scale_padding"),
        y = (v) => plotBottom - ((v - lo) / (hi - lo)) * (plotBottom - plotTop);
      const markerLayout = this.eventMarkerLayout(
        this.diagnosticMarkers(traces, metric, start, end),
        start,
        end,
        plotRight - plotLeft,
      );
      let chart =
        '<svg class="chart detector-chart" viewBox="0 0 ' +
        chartWidth +
        ` ${chartHeight}" role="group" aria-label="` +
        esc(group + " " + label) +
        '">';
      for (const pos of positions) {
        let path = "",
          last = null,
          lastInterval = null;
        for (const t of metricTraces) {
          const v = t.metrics?.[pos]?.[metric],
            time = stamp(t.at),
            interval = personMetric ? presenceProjection.intervalAt(time) : null;
          if (
            !Number.isFinite(v) ||
            !Number.isFinite(time) ||
            time < start ||
            time > end
          )
            continue;
          const point = `${chartX(t.at).toFixed(this.preference("chart_coordinate_precision"))},${y(convert(v)).toFixed(this.preference("chart_coordinate_precision"))}`;
          path +=
            !Number.isFinite(
              parameters[
                metric.startsWith("strong_") || metric.startsWith("weak_")
                  ? "person_step_seconds"
                  : "grid_seconds"
              ],
            ) ||
            last == null ||
            interval !== lastInterval ||
            time - last >
              parameters[
                metric.startsWith("strong_") || metric.startsWith("weak_")
                  ? "person_step_seconds"
                  : "grid_seconds"
              ] *
                1000
              ? `M${point}l0,0 `
              : `L${point} `;
          last = time;
          lastInterval = interval;
        }
        chart += `<path class="${pos} ${quantity}" data-series="detector_${metric}_${pos}" d="${path}" stroke-linecap="round"/>`;
        for (const v of thresholds(metric, pos).filter(Number.isFinite)) {
          const display = convert(v);
          for (const interval of personMetric
            ? sourceIntervals.filter((item) => item.source !== "ha_presence")
            : [{ at: start, end }])
            chart += `<line x1="${chartX(interval.at)}" x2="${chartX(interval.end)}" y1="${y(display)}" y2="${y(display)}" stroke="var(--sauna-color-chart-threshold)" stroke-dasharray="${this.preference("threshold_dash_length_px")} ${this.preference("threshold_dash_gap_px")}"><title>Schwelle ${pos === "upper" ? "oben" : "unten"}: ${this.num(display, this.preference("detail_value_precision"))}</title></line>`;
        }
      }
      const axisTicks = this.preference("diagnostic_axis_ticks");
      for (let n = 0; n <= axisTicks; n++) {
        const v = lo + ((hi - lo) * n) / axisTicks,
          t = start + ((end - start) * n) / axisTicks;
        chart += `<text class="axis-${quantity}" x="4" y="${y(v) + 4}">${this.num(v, this.preference("value_precision"))}</text><text text-anchor="middle" x="${chartX(t)}" y="${timeLabel}">${this.clock(t)}</text>`;
      }
      if (selectedRoute?.[0] === metric && selectedTrace) {
        const time = stamp(selectedTrace.at),
          x = chartX(time);
        if (time >= start && time <= end)
          chart += `<rect class="diagnostic-selection-band" x="${Math.max(plotLeft, x - this.preference("event_marker_size_px") / 2)}" y="${guideTop}" width="${this.preference("event_marker_size_px")}" height="${plotBottom - guideTop}"/><line class="diagnostic-selection-guide" x1="${x}" x2="${x}" y1="${guideTop}" y2="${plotBottom}"/>`;
      }
      chart += markerLayout.markers
        .map((marker) => {
          const eventX = chartX(marker.at),
            pointX = chartX(marker.pointAt),
            pointY = y(convert(marker.value)),
            labelY = Math.max(
              plotTop,
              pointY -
                this.preference("event_marker_size_px") / 2 -
                marker.lane *
                  (this.preference("event_marker_size_px") +
                    this.preference("event_marker_gap_px")),
            );
          return `<g class="event-marker diagnostic-marker" tabindex="0" role="button" data-action="event-marker:${esc(marker.event.event_id)}" data-event-id="${esc(marker.event.event_id)}" data-selected="${marker.event.event_id === this.highlightedEventId}" aria-pressed="${marker.event.event_id === this.highlightedEventId}" aria-label="${esc(events[marker.event.kind] || marker.event.kind)}, ${this.when(marker.event.effective_at || marker.event.detected_at)} · ${marker.position === "upper" ? "oben" : "unten"}"><line class="event-marker-link" x1="${eventX.toFixed(this.preference("chart_coordinate_precision"))}" y1="${pointY.toFixed(this.preference("chart_coordinate_precision"))}" x2="${pointX.toFixed(this.preference("chart_coordinate_precision"))}" y2="${pointY.toFixed(this.preference("chart_coordinate_precision"))}"/><circle class="event-marker-point" cx="${pointX.toFixed(this.preference("chart_coordinate_precision"))}" cy="${pointY.toFixed(this.preference("chart_coordinate_precision"))}" r="${this.preference("diagnostic_marker_point_radius_px")}"/><circle class="event-marker-dot" cx="${eventX.toFixed(this.preference("chart_coordinate_precision"))}" cy="${pointY.toFixed(this.preference("chart_coordinate_precision"))}" r="${this.preference("diagnostic_marker_event_radius_px")}"/><text class="event-marker-label" text-anchor="middle" x="${eventX.toFixed(this.preference("chart_coordinate_precision"))}" y="${labelY.toFixed(this.preference("chart_coordinate_precision"))}">${marker.index + 1}</text></g>`;
        })
        .join("");
      chart += "</svg>";
      const selectedMarker = markerLayout.markers.find(
          (marker) => marker.event.event_id === this.highlightedEventId,
        ),
        context = selectedMarker
          ? `${esc(events[selectedMarker.event.kind] || selectedMarker.event.kind)} · Prüfung ${this.when(selectedMarker.pointAt)} · ${selectedMarker.position === "upper" ? "oben" : "unten"}: ${this.num(convert(selectedMarker.value), this.preference("detail_value_precision"))} ${esc(label.split(" · ").at(-1))} <button data-action="diagnostic-inspect:${metric}">Prüfbedingungen</button>`
          : "";
      html += `<div class="plot-panel" data-key="diagnostic:${metric}" data-diagnostic-metric="${metric}" data-selected="${selectedRoute?.[0] === metric && !!selectedTrace}"><h3>${group} · <span style="color:var(--sauna-color-series-${quantity})">${label}</span></h3><p class="diagnostic-chart-context" data-key="context:${metric}" ${context ? "" : "hidden"}>${context}</p><div class="legend diagnostic-legend" style="color:var(--sauna-color-series-${quantity})">${positions.map((position) => `<span>${position === "upper" ? "━━ Oben" : "┄┄ Unten"}</span>`).join("")}</div>${chart}</div>`;
    }
    html += "</div>" + this.diagnosticSelectionMarkup(traces);
    this.updateMarkup("#detection-plots", html);
    if (this.highlightedEventId && this.shadowRoot?.querySelectorAll)
      this.highlightEvent(this.highlightedEventId, false);
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
    const source = state.configuration_model?.presence?.source;
    const subgroups = state.frontend_defaults.settings_subgroups || [];
    const entries = state.parameters
      .filter((d) => d.settings_group === id && d.applicable)
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
      `${this.state.session ? '<p class="muted">Löschen erst nach Ende der laufenden Sitzung.</p>' : ""}<div class="row"><button data-action="archive-list" ${this.archiveAdminBusy ? "disabled" : ""}>Sitzungen verwalten</button><button class="stop" data-action="archive-reset" ${disabled}>Datenbank zurücksetzen</button></div>${sessions ? (sessions.length ? sessions.map((session) => `<div class="row"><span>Sitzung vom ${esc(this.when(session.started_at))}</span><button class="stop" data-action="archive-delete:${esc(encodeURIComponent(session.session_id))}" ${locked || !session.ended_at ? "disabled" : ""}>Sitzung löschen</button></div>`).join("") : "<p>Keine gespeicherten Sitzungen.</p>") : ""}${pending ? `<div class="notice" role="alert"><p>${pending.reset ? "Alle Sitzungen und Messdaten dieser Sauna endgültig löschen? Einstellungen und Gerätezuordnungen bleiben erhalten." : `Sitzung vom ${esc(this.when(pending.started_at))} und alle zugehörigen Messdaten endgültig löschen?`}</p><div class="row"><button class="stop" data-action="archive-confirm" ${disabled}>Endgültig löschen</button><button data-action="archive-cancel" ${this.archiveAdminBusy ? "disabled" : ""}>Abbrechen</button></div></div>` : ""}<p role="status">${esc(this.archiveAdminMessage || "")}</p>`,
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
    const selector = `<div class="program-kind segmented-mode" role="group" aria-label="Temperaturverteilung"><button type="button" data-action="catalog-kind:even:${esc(program.id)}" aria-pressed="${kind === "even"}" ${disabled}>Gleichmäßig</button><button type="button" data-action="catalog-kind:steps:${esc(program.id)}" aria-pressed="${kind === "steps"}" ${disabled}>Einzelne Stufen</button></div>`;
    const values =
      kind === "steps"
        ? `<label class="field program-count-field" for="${esc(countId)}"><span>Anzahl der Temperaturstufen ${this.distributionInfo(`catalog:${program.id}`, steps)}</span><input id="${esc(countId)}" data-program-step-count type="number" min="1" max="${bounds.gangMaximum}" step="1" value="${count}" ${disabled}></label><div class="program-step-fields">${this.resizeSteps(
            steps,
            count,
          )
            .map(
              (value, index) =>
                `<label class="field" for="catalog-${esc(program.id)}-step-${index}">Stufe ${index + 1}${unitInput(`<input id="catalog-${esc(program.id)}-step-${index}" data-program-step="${index}" type="number" aria-label="Stufe ${index + 1} (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(value)}" ${disabled}>`, "°C")}</label>`,
            )
            .join("")}</div>`
        : `<div class="program-temperature-fields"><label class="field">Starttemperatur${unitInput(`<input data-program-field="start_c" type="number" aria-label="Starttemperatur (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(program.start_c)}" ${disabled}>`, "°C")}</label><label class="field">Endtemperatur${unitInput(`<input data-program-field="end_c" type="number" aria-label="Endtemperatur (°C)" step="${this.temperatureStep()}" min="${bounds.minimum}" max="${bounds.maximum}" value="${esc(program.end_c)}" ${disabled}>`, "°C")}</label></div><label class="field program-count-field" for="${esc(distributionId)}"><span>Verteilung auf Saunagänge ${this.distributionInfo(`catalog:${program.id}`, this.distributedSteps(Number(program.start_c), Number(program.end_c), Number(program.distribution_gangs)))}</span><input id="${esc(distributionId)}" data-program-field="distribution_gangs" type="number" step="1" min="${bounds.gangMinimum}" max="${bounds.gangMaximum}" value="${esc(program.distribution_gangs)}" ${disabled}></label>`;
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
      gestureOptions = (this.state.configuration_model?.button?.session_gestures || [])
        .map(
          (value) =>
            `<option value="${value}" ${configuration.button_session_gesture === value ? "selected" : ""}>${{ long: "Gedrückt halten", double: "Zweimal drücken", triple: "Dreimal drücken" }[value]}</option>`,
        )
        .join(""),
      button = configuration.button_program,
      option = (value, label) =>
        `<option value="${esc(value)}" ${button === value ? "selected" : ""}>${esc(label)}</option>`;
    this.updateMarkup(
      "#button-settings",
      `${gestureOptions ? `<div class="row"><label class="field" for="button-session-gesture">Sitzung starten und beenden<select id="button-session-gesture" ${gestureEditable ? "" : "disabled"}>${gestureOptions}</select></label></div>` : ""}<div class="row"><div class="field"><span><label for="button-program">Temperaturwahl</label> ${this.infoButton("button-start", "Temperaturwahl beim externen Start", "Wird sofort als Startvorgabe für Taster und Betriebsschalter gespeichert. Beim Einschalten in der Steuerungsansicht gilt deren Temperaturwahl.")}</span><select id="button-program" ${editable ? "" : "disabled"}>${option("constant", "Konstante Temperatur")}<optgroup label="Gespeicherte Programme">${configuration.temperature_programs.map((program) => option(program.id, program.name)).join("")}</optgroup></select></div></div>${button !== "constant" ? `<p class="muted">Temperaturfolge: ${esc(this.programSteps(configuration.temperature_programs.find((program) => program.id === button)))}</p>` : ""}`,
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
    const edge = this.preference("program_drag_edge_px"),
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
      if (container)
        container.scrollTop +=
          drag.edgeDirection * this.preference("program_drag_scroll_px");
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
      ["#history-inspection", main === "details"],
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
      // Manual recordings begin/end with heater choices, not actuator feedback.
      const ended =
        (key === "heater" || key === "light") &&
        (command.mode !== state.configuration?.control_mode ||
          (command.mode !== "manual" &&
            command.session !== (state.session?.timeline?.session_id ?? null)));
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
    track?.setAttribute(
      "aria-valuetext",
      `${this.num(value, this.preference("percentage_precision"))} %`,
    );
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
    if (action.startsWith("history-event-category:")) {
      this.toggleHistoryEventCategory(action.slice("history-event-category:".length));
      return;
    }
    if (action === "history-selection-window") {
      this.toggleHistorySelectionWindow();
      return;
    }
    if (action.startsWith("history-phase:")) {
      this.selectHistoryPhase(
        decodeURIComponent(action.slice("history-phase:".length)),
      );
      return;
    }
    if (action.startsWith("history-event:")) {
      this.inspectHistoryEvent(action.slice("history-event:".length));
      return;
    }
    if (action.startsWith("history-event-group:")) {
      this.inspectHistoryEventGroup(
        JSON.parse(decodeURIComponent(action.slice("history-event-group:".length))),
      );
      return;
    }
    if (action.startsWith("history-") || action === "clear-history-selection")
      this.cancelHistoryViewTransition();
    if (action === "clear-history-selection") return this.clearHistorySelection();
    if (action === "history-humidity") {
      this.historyHumidityMode =
        this.historyHumidityMode === "absolute" ? "relative" : "absolute";
      this.scheduleHistoryRender("humidity");
      return;
    }
    if (
      action === "detail-follow" ||
      action.startsWith("detail-record-page:") ||
      action.startsWith("detail-event-page:")
    ) {
      if (!this.state?.permissions?.admin || !this.shown) return;
      if (action === "detail-follow") {
        this.controlHistoryEnd = Math.max(
          stamp(this.shown.session.timeline.session_started_at) +
            this.preference("history_minimum_span_ms"),
          stamp(this.shown.session.ended_at || this.state.now),
        );
        this.controlHistoryCursor = this.controlHistoryEnd;
      } else {
        const page = Number(action.slice(action.indexOf(":") + 1));
        if (!Number.isInteger(page)) return;
        if (action.startsWith("detail-event-page:"))
          this.detailEventPage = Math.max(0, page);
        else this.detailRecordPage = Math.max(0, page);
      }
      this.renderControlHistory();
      return;
    }
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
        (!permissions.control || !this.state.control_mode_change_allowed)) ||
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
      this.pendingEventFocus = {
        kind: "marker",
        eventId,
        sessionId: this.shown?.session.timeline.session_id,
      };
      this.setPanelView("diagnostics", true);
      this.focusEvent(eventId);
      return;
    }
    if (action.startsWith("event-row:")) {
      if (!permissions.admin) return;
      const eventId = action.slice(10);
      this.pendingEventFocus = {
        kind: "marker",
        eventId,
        sessionId: this.shown?.session.timeline.session_id,
      };
      this.setPanelView("diagnostics", true);
      this.focusEvent(eventId);
      return;
    }
    if (action.startsWith("event-history:")) {
      if (!permissions.admin) return;
      const eventId = action.slice("event-history:".length);
      this.historyDetail = false;
      this.positions = new Set([this.historyPrimaryPosition()]);
      this.pendingEventFocus = {
        kind: "row",
        eventId,
        sessionId: this.shown?.session.timeline.session_id,
      };
      this.setPanelView("history", true);
      this.focusEvent(eventId, true);
      return;
    }
    if (action.startsWith("diagnostic-inspect:")) {
      if (!permissions.admin) return;
      const target = this.$("#diagnostic-selection");
      target?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
      target?.focus?.({ preventScroll: true });
      return;
    }
    if (action.startsWith("diagnostic-trace:")) {
      if (!permissions.admin) return;
      const traces = this.diagnosticData().traces,
        index = traces.includes(this.selectedDiagnosticTrace)
          ? traces.indexOf(this.selectedDiagnosticTrace)
          : traces.findIndex((trace) => trace.at === this.selectedDiagnosticTraceAt),
        next = Math.max(
          0,
          Math.min(traces.length - 1, index + (action.endsWith(":previous") ? -1 : 1)),
        );
      if (traces[next]) {
        this.selectedDiagnosticTrace = traces[next];
        this.selectedDiagnosticTraceAt = traces[next].at;
        this.highlightedEventId = null;
        this.pendingEventFocus = null;
        this.drawDiagnostics();
      }
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
      const profile = this.$("#button-program").value;
      await this.api(`/${this.entry}/button-program`, "POST", { profile });
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
        value = preset === "true" ? true : preset === "false" ? false : null;
      if (preset !== "true" && preset !== "false" && preset !== "auto")
        throw Error("Ungültige Lichtwahl");
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
