// The detector view works without HA or a browser; the DOM here records real
// click actions and the stable event-id mapping used by both directions.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

let Panel;
const sandbox = {
  HTMLElement: class {}, customElements: {get: () => undefined, define: (_n, value) => { Panel=value; }},
  Date, Map, Set, Math, Number, String, Object, Array, Infinity,
};
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), sandbox);

const iso = second => new Date(1_700_000_000_000 + second * 1000).toISOString();
const processed = [
  {event_id:"door-7",kind:"door_open",effective_at:iso(10),detected_at:iso(11)},
  {event_id:"infusion-8",kind:"infusion",effective_at:iso(10),detected_at:iso(12)},
];
const session = {ended_at:iso(60),timeline:{session_started_at:iso(0),processed,completed:[]},configuration:{parameters:{person_step_seconds:5}}};
const parameters = {door_open_slope:-1,door_heating_slope:-1,door_close_slope:1,door_open_humidity_upper:1,door_open_humidity_lower:1,vent_drop_upper:3,vent_drop_lower:3,vent_absolute_humidity_loss_percent:30};

// Equal timestamps do not collapse: IDs from timeline.processed remain the
// navigation key and each gets exactly one strip marker.
{
  const plots = {};
  const p = Object.assign(Object.create(Panel.prototype), {
    shown:{session,records:[]}, state:{configuration:{parameters}}, window:[Date.parse(iso(0)),Date.parse(iso(60))],
    $: selector => selector === "#detection-plots" ? plots : null,
  });
  p.drawDiagnostics();
  assert.deepEqual(p.eventNavigation().map(e => e.event_id), ["door-7","infusion-8"]);
  assert.equal((plots.innerHTML.match(/class="event-marker"/g) || []).length, 2);
  assert.match(plots.innerHTML, /event-marker:door-7/);
  assert.match(plots.innerHTML, /event-marker:infusion-8/);
  assert.match(plots.innerHTML, /--event-marker-bottom:-6px/);
  assert.match(plots.innerHTML, /--event-marker-bottom:14px/);
}

// Ventilation losses are archived as fractions, but the diagnostics display
// them as percentages and retain the archived 30 % threshold (25 % is below,
// 35 % above).  Earlier traces without these metrics render no false zero line.
{
  const plots = {};
  const traces = [
    {at:iso(20),metrics:{upper:{ventilation_temperature_loss:2.5,ventilation_absolute_humidity_loss:.25},lower:{ventilation_temperature_loss:3.1,ventilation_absolute_humidity_loss:.25}},conditions:{},holds:{},signals:[]},
    {at:iso(25),metrics:{upper:{ventilation_temperature_loss:3.5,ventilation_absolute_humidity_loss:.35},lower:{ventilation_temperature_loss:3.4,ventilation_absolute_humidity_loss:.35}},conditions:{},holds:{},signals:[]},
  ];
  const archived = {...session,configuration:{parameters:{...parameters,vent_absolute_humidity_loss_percent:30}}};
  const p = Object.assign(Object.create(Panel.prototype), {
    shown:{session:archived,records:traces.map(payload => ({kind:"detector_trace",payload}))},
    state:{configuration:{parameters:{...parameters,vent_absolute_humidity_loss_percent:99}}}, window:[Date.parse(iso(0)),Date.parse(iso(60))],
    $: selector => selector === "#detection-plots" ? plots : null,
  });
  p.drawDiagnostics();
  assert.match(plots.innerHTML, /Lüftungserkennung · Temperaturverlust · °C/);
  assert.match(plots.innerHTML, /Lüftungserkennung · Absoluter Feuchteverlust · %/);
  assert.match(plots.innerHTML, /data-series="detector_ventilation_absolute_humidity_loss_upper" d="M[\d.]+,66\.62 L[\d.]+,28\.78 "/);
  assert.match(plots.innerHTML, /Schwelle oben: 30/);
  assert.doesNotMatch(plots.innerHTML, /Schwelle oben: 99/);
}

// Old detector traces still show their available signals, without inventing a
// ventilation measurement or emitting NaN coordinates.
{
  const plots = {};
  const p = Object.assign(Object.create(Panel.prototype), {
    shown:{session,records:[{kind:"detector_trace",payload:{at:iso(20),metrics:{upper:{door_temperature_slope:-2},lower:{door_temperature_slope:-1}},conditions:{},holds:{},signals:[]}}]},
    state:{configuration:{parameters}}, window:[Date.parse(iso(0)),Date.parse(iso(60))],
    $: selector => selector === "#detection-plots" ? plots : null,
  });
  p.drawDiagnostics();
  assert.doesNotMatch(plots.innerHTML, /Lüftungserkennung/);
  assert.doesNotMatch(plots.innerHTML, /NaN/);
}

// The strip contains only events in the current viewport.  Marker labels keep
// their index from the complete timeline, while nearby rendered positions use
// separate lanes even when their timestamps differ.
{
  const plots = {};
  const allEvents = [
    {event_id:"door-7",kind:"door_open",effective_at:iso(10)},
    {event_id:"infusion-8",kind:"infusion",effective_at:iso(11)},
    {event_id:"door-9",kind:"door_close",effective_at:iso(40)},
    {event_id:"future-10",kind:"infusion",effective_at:iso(120)},
  ];
  const p = Object.assign(Object.create(Panel.prototype), {
    shown:{session:{...session,timeline:{...session.timeline,processed:allEvents}},records:[]},
    state:{configuration:{parameters}}, window:[Date.parse(iso(0)),Date.parse(iso(60))],
    $: selector => selector === "#detection-plots" ? plots : null,
  });
  p.drawDiagnostics();
  assert.equal((plots.innerHTML.match(/class="event-marker"/g) || []).length, 3);
  assert.match(plots.innerHTML, /event-marker:door-9[^>]*>3<\//);
  assert.doesNotMatch(plots.innerHTML, /future-10/);
  assert.match(plots.innerHTML, /--event-marker-lane:1/);

  p.window=[Date.parse(iso(20)),Date.parse(iso(60))];
  p.drawDiagnostics();
  assert.equal((plots.innerHTML.match(/class="event-marker"/g) || []).length, 1);
  assert.match(plots.innerHTML, /event-marker:door-9[^>]*>3<\//);
}

// Marker -> row highlights the exact same event id, never a nearest timestamp.
{
  const rows = [
    {dataset:{eventId:"door-7",selected:"false"},classList:{contains:() => true},scrollIntoView:() => { rows[0].scrolled=true; }},
    {dataset:{eventId:"infusion-8",selected:"false"},classList:{contains:() => true},scrollIntoView:() => { rows[1].scrolled=true; }},
  ];
  const p = Object.assign(Object.create(Panel.prototype), {shadowRoot:{querySelectorAll:() => rows}});
  p.highlightEvent("infusion-8", true);
  assert.equal(rows[0].dataset.selected, "false");
  assert.equal(rows[1].dataset.selected, "true");
  assert.equal(rows[1].scrolled, true);
}

// Both click actions invoke their reciprocal navigation method.
{
  const calls=[];
  const p = Object.assign(Object.create(Panel.prototype), {
    state:{permissions:{}}, message:() => {}, highlightEvent:(id,reveal) => calls.push(["marker",id,reveal]), focusEvent:id => calls.push(["row",id]),
  });
  p.action("event-marker:infusion-8");
  p.action("event-row:door-7");
  assert.deepEqual(calls, [["marker","infusion-8",true],["row","door-7"]]);
}

console.log("panel detection navigation regressions passed");
