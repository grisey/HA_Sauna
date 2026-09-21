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

// The history heading follows the operating state even when a live session's
// samples have not changed.  Archive selections always identify their date.
{
  const p=panel();p.selected="live";p.state.operation_enabled=true;p.state.session={timeline:{session_id:"live"}};
  assert.equal(p.historyTitle(session),"Laufende Sitzung");
  p.state.operation_enabled=false;
  assert.equal(p.historyTitle(session),"Letzte Sitzung");
  p.selected="archive-1";p.sessions=[{session_id:"archive-1",started_at:iso(10)}];
  assert.match(p.historyTitle({timeline:{}}),/^Sitzung vom /,"an archive title includes its start date");
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

// Dataset, viewport, and timeline changes have independent monotone signals.
// A gang/event mutation therefore invalidates live content without a new sample.
{
  const p=panel(), live={...session,energy:null,heating:{intervals:[]},timeline:{...session.timeline,session_id:"s",active:null,retracted:[]}};
  const initial=p.updateHistoryTimelineRevision(live);
  live.timeline={...live.timeline,processed:[{event_id:"event-1",kind:"infusion"}]};
  assert.ok(p.updateHistoryTimelineRevision(live)>initial,"timeline-only changes receive a new revision");
  p.historyWindowRevision=0;p.window=null;p.ensureHistoryWindow();
  assert.equal(p.historyWindowRevision,1,"initial viewport receives its own revision");
}

// Safari's gesture events are suppressed when the same physical pinch is
// already owned by Pointer Events, so zoom is applied exactly once.
{
  const p=panel(), svg={setPointerCapture:()=>{},getBoundingClientRect:()=>({left:0,width:1200})};let zooms=0,prevented=0;
  p.chartPointers=new Map();p.zoomAt=()=>{zooms++;};
  p.beginChartPointer({pointerId:1,clientX:10,clientY:10},svg);
  p.beginChartPointer({pointerId:2,clientX:20,clientY:20},svg);
  const event={target:{closest:()=>svg},preventDefault:()=>{prevented++;},scale:2,clientX:15};
  p.beginWebkitGesture(event);p.updateWebkitGesture(event);p.endWebkitGesture(event);
  assert.equal(zooms,0,"suppressed WebKit events do not duplicate pointer zoom");
  assert.equal(p.historyInputMode,"pointer","the original gesture keeps ownership");
  assert.ok(prevented>=3,"suppressed native gesture events remain browser-blocked");
}

assert.doesNotMatch(source, /id="pan"/,"the obsolete numeric pan range is removed");
assert.doesNotMatch(source, /Aktuelle oder letzte Saunasitzung/,"the ambiguous live-session label is removed");
assert.match(source, /aria-label="Gesamte Saunasitzung">Gesamt/,"the compact reset control keeps its full accessible label");
assert.match(source, /\.chart\s*\{\s*height:\s*520px;\s*width:\s*100%;\s*touch-action:\s*pan-y;\s*user-select:\s*none;\s*\}/,"chart text cannot be selected while dragging");
assert.match(source, /if\s*\(\s*!svg\s*\|\|\s*!\(e\.ctrlKey\s*\|\|\s*e\.metaKey\)\s*\)\s*return/,"ordinary wheel scrolling must not zoom");
assert.match(source, /setPointerCapture\?\.\(event\.pointerId\)/,"overview drag owns pointer capture");
assert.match(source, /gesturestart/,"Safari gesture events begin live chart zoom");
assert.match(source, /gesturechange/,"Safari gesture changes update the chart during pinch");
assert.match(source, /updateHistoryChart\(records,\s*session,\s*gangs\)/,"live gestures reuse the chart SVG contents");
assert.match(source, /this\.patchNode\(current,\s*next\)/,"incremental history keeps the existing SVG nodes");
assert.match(source, /if\s*\(\s*action\s*===\s*"history"\s*\)\s*\{\s*this\.historyDetail\s*=\s*false;\s*this\.positions\s*=\s*new Set\(\["upper"\]\);\s*\}/,"normal history returns to one measurement position");

// Explicit navigation that arrives during a periodic refresh queues one fresh
// pass instead of waiting for the next two-second interval.
(async()=>{
  let releaseFirst,stateReads=0;
  const firstState=new Promise(resolve=>{releaseFirst=resolve;});
  const p=Object.assign(Object.create(Panel.prototype),{
    entry:"entry",generation:0,isConnected:true,shadowRoot:{activeElement:null},
    api:async path=>{if(path!=="/entry/state")throw Error(path);stateReads++;return stateReads===1?firstState:{};},
    $:selector=>selector==="#history"?{hidden:true}:null,
    drawCurrent:()=>{},drawSettings:()=>{},message:()=>{},
  });
  const periodic=p.refresh();await Promise.resolve();
  await p.refresh(true);assert.equal(p.refreshPending,true,"navigation marks a refresh already in flight");
  releaseFirst({});await periodic;await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(stateReads,2,"the pending navigation starts a new state read immediately");
  console.log("panel history navigation regressions passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
