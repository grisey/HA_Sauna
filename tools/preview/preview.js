const bootstrap = await (await fetch("/preview-config")).json();
const panel = document.createElement("ha-sauna-panel");
panel.panel = { config: bootstrap };
document.body.append(panel);
panel.entry = "preview";
panel.$("#instance").hidden = true;
panel._hass = { user: { name: "Lokale Vorschau", is_admin: true } };
const playbackButton = document.createElement("button");
playbackButton.id = "preview-playback";
playbackButton.type = "button";
const playbackTiming = document.createElement("small");
const sequence = bootstrap.preview.sequence;
playbackTiming.textContent = `${sequence.advance_seconds} s Simulation je ${sequence.sample_seconds} s`;
document.querySelector("#sim-time").before(playbackButton, playbackTiming);
const previewError = document.querySelector("#preview-error");
let playbackRunning = false;
let playbackTimer = null;
let playbackGeneration = 0;
let previewClosed = false;
let previewGeneration = 0;
let toolbarRevision = 0;
let scenarioRevision = 0;
let pendingSimulations = 0;
let simulationQueue = Promise.resolve();
const requests = new Set();
const clearPlaybackTimer = () => {
  clearTimeout(playbackTimer);
  playbackTimer = null;
};
const syncPlaybackButton = () => {
  playbackButton.textContent = playbackRunning
    ? "Zeitraffer pausieren"
    : "Zeitraffer starten";
  playbackButton.setAttribute("aria-pressed", String(playbackRunning));
  playbackButton.disabled = previewClosed || !panel.state;
};
const pausePlayback = () => {
  playbackRunning = false;
  playbackGeneration++;
  clearPlaybackTimer();
  syncPlaybackButton();
};
const showPreviewError = (error) => {
  if (previewClosed) return;
  pausePlayback();
  previewError.textContent = error.message;
};
syncPlaybackButton();
panel.api = async (path, method = "GET", body) => {
  if (previewClosed) throw new DOMException("Vorschau geschlossen", "AbortError");
  const request = new AbortController();
  const generation = previewGeneration;
  requests.add(request);
  try {
    const response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: request.signal,
    });
    const result = await response.json();
    if (!response.ok)
      throw Object.assign(Error(result.error || response.statusText), {
        status_code: response.status,
        body: result,
      });
    return result;
  } catch (error) {
    if (generation === previewGeneration) showPreviewError(error);
    throw error;
  } finally {
    requests.delete(request);
  }
};
const panelRefresh = panel.refresh.bind(panel);
let latestPanelRefresh = Promise.resolve();
panel.refresh = (requested = false) => {
  const busy = panel.busy;
  const promise = panelRefresh(requested);
  if (!busy) latestPanelRefresh = promise;
  return promise;
};
const refresh = async (
  reset = false,
  revision = toolbarRevision,
  generation = previewGeneration,
) => {
  if (previewClosed || generation !== previewGeneration) return;
  if (reset) {
    panel.cache.clear();
    panel.shown = null;
    panel.historyLoad = null;
    panel.controlHistoryCursor = null;
    panel.chartDataIndex = null;
    panel.historyGangKey = panel.historyEventKey = null;
  }
  await panel.refresh(true);
  // A busy status poll hands the request to a subsequent panel refresh.
  let pendingRefresh;
  do {
    pendingRefresh = latestPanelRefresh;
    await pendingRefresh;
  } while (
    !previewClosed &&
    generation === previewGeneration &&
    pendingRefresh !== latestPanelRefresh
  );
  if (previewClosed || generation !== previewGeneration) return;
  await panel.historyLoad?.promise;
  if (
    previewClosed ||
    generation !== previewGeneration ||
    revision !== toolbarRevision ||
    !panel.state
  )
    return;
  document.querySelector("#role").value = panel.state.permissions.admin
    ? "admin"
    : "user";
  document.querySelector("#scenario").value = panel.state.preview_scenario;
  const feedback = panel.state.preview_feedback;
  document.querySelectorAll("[data-feedback]").forEach((choice) => {
    const value = JSON.parse(choice.dataset.feedback);
    choice.setAttribute(
      "aria-pressed",
      feedback.following ? value === "follow" : value === feedback.on,
    );
  });
  document.querySelector("#feedback-status").textContent = feedback.following
    ? "Folgt der Steuerung"
    : `Fest vorgegeben: ${feedback.on === null ? "Unbekannt" : feedback.on ? "Ein" : "Aus"}`;
  const button = panel.state.preview_button;
  for (const gesture of ["short", "double", "triple", "hold"])
    document.querySelector(`[data-sim="button_${gesture}"]`).disabled = button.pressed;
  document.querySelector('[data-sim="button_release"]').disabled = !button.pressed;
  document.querySelector("#button-status").textContent = button.pressed
    ? button.start_hold
      ? "Gedrückt · Start bestätigt, Lichtbestätigung bis zum Loslassen"
      : "Gedrückt · Loslassen beendet die Geste"
    : "Ereignistaster: fertige Gestenmeldungen";
  panel._hass.user.is_admin = panel.state.permissions.admin;
  document.querySelector("#sim-time").textContent = new Date(
    panel.state.now,
  ).toLocaleTimeString("de-DE");
  syncPlaybackButton();
};
const schedulePlayback = () => {
  if (!playbackRunning || previewClosed || pendingSimulations || playbackTimer !== null)
    return;
  const generation = playbackGeneration;
  playbackTimer = setTimeout(() => {
    playbackTimer = null;
    if (playbackRunning && generation === playbackGeneration)
      simulate("step", undefined, generation);
  }, sequence.sample_seconds * 1000);
};
const simulate = (action, value, playback = null) => {
  const reset = action.startsWith("scenario:");
  if (reset) pausePlayback();
  clearPlaybackTimer();
  const revision = ++toolbarRevision;
  const scenario = reset ? ++scenarioRevision : scenarioRevision;
  const generation = previewGeneration;
  pendingSimulations++;
  simulationQueue = simulationQueue
    .then(async () => {
      if (
        previewClosed ||
        generation !== previewGeneration ||
        (reset && scenario !== scenarioRevision) ||
        (playback !== null && (!playbackRunning || playback !== playbackGeneration))
      )
        return;
      previewError.textContent = "";
      await panel.api("/simulate", "POST", { action, value });
      await refresh(reset, revision, generation);
    })
    .catch((error) => {
      if (generation === previewGeneration) showPreviewError(error);
    })
    .finally(() => {
      pendingSimulations--;
      schedulePlayback();
    });
  return simulationQueue;
};
playbackButton.onclick = () => {
  if (playbackRunning) return pausePlayback();
  playbackRunning = true;
  playbackGeneration++;
  previewError.textContent = "";
  syncPlaybackButton();
  schedulePlayback();
};
document.querySelector("#scenario").onchange = (e) =>
  simulate(`scenario:${e.target.value}`);
document.querySelector("#reset").onclick = () =>
  simulate(`scenario:${document.querySelector("#scenario").value}`);
document.querySelector("#temperature").onclick = () =>
  simulate("temperature", Number(document.querySelector("#sim-temperature").value));
document
  .querySelectorAll("[data-sim]")
  .forEach((b) => (b.onclick = () => simulate(b.dataset.sim)));
document
  .querySelectorAll("[data-feedback]")
  .forEach(
    (b) => (b.onclick = () => simulate("feedback", JSON.parse(b.dataset.feedback))),
  );
pendingSimulations++;
const initialGeneration = previewGeneration;
simulationQueue = refresh(false, toolbarRevision, initialGeneration)
  .catch((error) => {
    if (initialGeneration === previewGeneration) showPreviewError(error);
  })
  .finally(() => {
    pendingSimulations--;
    schedulePlayback();
  });

document.querySelector("#role").onchange = async (e) => {
  const role = e.target.value;
  panel._hass.user.is_admin = role === "admin";
  await simulate("role", role);
};

window.addEventListener("pagehide", () => {
  previewClosed = true;
  previewGeneration++;
  pausePlayback();
  for (const request of requests) request.abort();
});
window.addEventListener("pageshow", () => {
  previewClosed = false;
  syncPlaybackButton();
});
