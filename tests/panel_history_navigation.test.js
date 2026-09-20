// The overview window is deliberately data-coordinate based so rapid wheel
// events and pointer drags always build on the current, bounded viewport.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

let Panel;
const sandbox = {
  HTMLElement: class {}, customElements: {get: () => undefined, define: (_name, value) => { Panel=value; }},
  Date, Map, Set, Math, Number, String, Object, Array, Infinity, setTimeout,
};
const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
vm.runInNewContext(source, sandbox);

const iso = second => new Date(1_700_000_000_000 + second * 1000).toISOString();
const session = {ended_at:iso(3600), timeline:{session_started_at:iso(0),processed:[],completed:[]}};
const panel = () => Object.assign(Object.create(Panel.prototype), {
  shown:{session,records:[]}, state:{now:iso(3600),configuration:{parameters:{sensor_timeout_seconds:5}}},
  $: () => null, scheduleHistoryRender: () => {},
});

// Dragging either edge stays inside the session and never creates a viewport
// narrower than the supported 256× maximum zoom.
{
  const p=panel(), [start,end]=p.historyDomain(), span=end-start;
  p.setHistoryWindow(start-10_000,end+10_000);
  assert.equal(p.window[0],start,"window begins at the session start");
  assert.equal(p.window[1],end,"window ends at the session end");
  p.setHistoryWindow(end-1,end);
  assert.equal(p.window[1],end,"right handle remains at the session end");
  assert.ok(p.window[1]-p.window[0]>=span/256,"viewport respects the maximum zoom limit");
}

// Zooming at a chart coordinate preserves that data-time anchor immediately,
// so a second wheel event cannot use the previous rendered window.
{
  const p=panel(), [start,end]=p.historyDomain();
  p.setHistoryWindow(start,end);
  p.svgCoordinates=() => ({x:600}); // centre of the chart plot area
  const before=(p.window[0]+p.window[1])/2;
  p.zoomAt(2,0,{});
  assert.equal((p.window[0]+p.window[1])/2,before,"centre anchor remains fixed after zoom");
  const firstWidth=p.window[1]-p.window[0];
  p.zoomAt(2,0,{});
  assert.equal(p.window[1]-p.window[0],firstWidth/2,"second zoom uses the synchronously updated window");
}

assert.doesNotMatch(source, /id="pan"/,"the obsolete numeric pan range is removed");
assert.match(source, /if\(!svg\|\|!\(e\.ctrlKey\|\|e\.metaKey\)\)return/,"ordinary wheel scrolling must not zoom");
assert.match(source, /setPointerCapture\?\.\(event\.pointerId\)/,"overview drag owns pointer capture");

console.log("panel history navigation regressions passed");
