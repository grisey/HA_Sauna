const panel = document.createElement("ha-sauna-panel");
document.body.append(panel);
panel.entry = "preview";
panel.$("#instance").hidden = true;
panel._hass = { user: { name: "Lokale Vorschau", is_admin: true } };
panel.api = async (path, method = "GET", body) => {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || response.statusText);
  return result;
};
const refresh = async (reset = false) => {
  if (reset) {
    panel.cache.clear();
    panel.shown = null;
    panel.historyLoad = null;
    panel.controlHistoryCursor = null;
    panel.chartDataIndex = null;
    panel.historyGangKey = panel.historyEventKey = null;
  }
  await panel.refresh(true);
  await panel.historyLoad?.promise;
  document.querySelector("#role").value = panel.state.permissions.admin
    ? "admin"
    : "user";
  document.querySelector("#scenario").value = panel.state.preview_scenario;
  panel._hass.user.is_admin = panel.state.permissions.admin;
  document.querySelector("#sim-time").textContent = new Date(
    panel.state.now,
  ).toLocaleTimeString("de-DE");
};
const simulate = async (action, value) => {
  try {
    document.querySelector("#preview-error").textContent = "";
    await panel.api("/simulate", "POST", { action, value });
    await refresh(action.startsWith("scenario:"));
  } catch (error) {
    document.querySelector("#preview-error").textContent = error.message;
  }
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
refresh();

document.querySelector("#role").onchange = async (e) => {
  const role = e.target.value;
  panel._hass.user.is_admin = role === "admin";
  await simulate("role", role);
};
