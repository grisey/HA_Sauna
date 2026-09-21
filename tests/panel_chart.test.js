// Runs without Home Assistant or a browser: the chart helpers are pure JS.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

let Panel;
const sandbox = {
  HTMLElement: class {},
  customElements: {get: () => undefined, define: (_name, value) => { Panel=value; }},
  Date, Map, Set, Math, Number, String, Object, Array, Infinity,
};
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), sandbox);
const panelSource = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");

// Overview controls use the narrow live endpoints.  In particular a direct
// target must never start a session or re-submit the complete configuration.
assert.ok(panelSource.includes("`/${entry}/temperature`"));
assert.ok(panelSource.includes("`/${entry}/program`"));
assert.match(panelSource, /data-target-arc[\s\S]*role="slider"/);
assert.match(panelSource, /data-action="light:false"/);
assert.match(panelSource, /permissions\.temperature/);
assert.doesNotMatch(panelSource, /changeTarget\(Number\(action\.slice\(7\)\),true\)/);

const iso = second => new Date(1_700_000_000_000+second*1000).toISOString();
const record = (second, value, quantity="temperature") => ({kind:"measurement",payload:{position:"upper",quantity,value,received_at:iso(second)}});
const session = {
  ended_at: iso(30),
  configuration:{parameters:{sensor_timeout_seconds:5}},
  timeline:{session_started_at:iso(0),processed:[],completed:[]},
  heating:{intervals:[]},
};
const panel = () => Object.assign(Object.create(Panel.prototype), {
  positions:new Set(["upper"]), window:[1_700_000_000_000,1_700_000_030_000], state:{now:iso(30),configuration:{parameters:{sensor_timeout_seconds:5}}},
});

// Three hours at one hertz collapse to pixels, but every raw neighbour is
// still inside the five-second TTL and must remain one continuous path.
{
  const p=panel(), records=[];
  for(let second=0;second<=10_800;second++)records.push(record(second,70+second/10_800));
  p.window=[1_700_000_000_000,1_700_000_000_000+10_800_000];
  const svg=p.chart(records,{...session,ended_at:iso(10_800),timeline:{...session.timeline,session_started_at:iso(0)}},[]);
  const d=/data-series="upper_temperature" d="([^"]*)"/.exec(svg)[1];
  assert.equal((d.match(/M/g)||[]).length,1,"pixel decimation must not create TTL gaps");
}

// A real gap remains a new path even if both remaining samples share pixels.
{
  const p=panel(), svg=p.chart([record(0,70),record(1,71),record(20,72),record(21,73)],session,[]);
  const d=/data-series="upper_temperature" d="([^"]*)"/.exec(svg)[1];
  assert.equal((d.match(/M/g)||[]).length,2,"raw TTL gap must not be bridged");
}

// The interaction index is numeric and sorted once, so the binary lookup
// includes exact visible boundaries and picks the closest adjacent sample.
{
  const p=panel(), records=[record(20,72),record(0,70),record(10,71)];
  p.historyIndex(records);
  assert.equal(p.nearestMeasurement("upper","temperature",1_700_000_000_000).value,70,"first visible sample is reachable");
  assert.equal(p.nearestMeasurement("upper","temperature",1_700_000_020_000).value,72,"last visible sample is reachable");
  assert.equal(p.nearestMeasurement("upper","temperature",1_700_000_016_000).value,72,"binary lookup chooses the closest sample");
}

// New archive pages extend the existing numeric index.  Hover can therefore
// continue to resolve values even while a visible tooltip suppresses no work.
{
  const p=panel(), records=[record(0,70)];
  p.historyIndex(records);const index=p.chartDataIndex;
  records.push(record(2,71));p.invalidateHistoryIndex();p.historyIndex(records);
  assert.equal(p.chartDataIndex,index,"append-only history keeps its lookup index");
  assert.equal(p.series("upper","temperature").length,2,"new archive value joins the lookup index");
}

// A long full-resolution session must not spread every sample into Math.min
// or Math.max; JavaScript engines cap the number of function arguments.
{
  const p=panel(), records=[], values=[];
  for(let index=0;index<200_000;index++)values.push({time:1_700_000_000_000+index/10,value:60+index%20,source:{received_at:iso(0)}});
  p.shown={session,records};p.historyDatasetRevision=1;
  p.chartDataIndex={records,indexedCount:0,series:new Map([["upper:temperature",values]]),byKind:new Map()};
  assert.doesNotThrow(()=>p.minimapBackground(records,session),"large minimaps compute their bounds iteratively");
  assert.doesNotThrow(()=>p.chart(records,session,[]),"large visible series avoid function argument limits");
}

// The curve receives one neighbour across each viewport edge before it is
// reduced, so a continuous raw series does not begin as an artificial dot.
{
  const p=panel();p.window=[1_700_000_002_000,1_700_000_010_000];
  const svg=p.chart([record(0,70),record(4,71)],session,[]);
  const d=/data-series="upper_temperature" d="([^"]*)"/.exec(svg)[1];
  assert.match(d,/ C/,"the boundary neighbour keeps a continuous clipped line");
}

// An explicit missing measurement must remain a gap, never become zero.
{
  const p=panel(), svg=p.chart([record(0,70),record(1,null),record(2,71)],session,[]);
  const d=/data-series="upper_temperature" d="([^"]*)"/.exec(svg)[1];
  assert.equal((d.match(/M/g)||[]).length,2,"explicit missing measurements break the curve");
  assert.equal(p.nearestMeasurement("upper","temperature",1_700_000_001_000).value,null);
}

// Humidity uses a useful percentage scale above 40 rather than clipping it.
{
  const p=panel(), svg=p.chart([record(1,80),record(1,55,"humidity")],session,[]);
  assert.match(svg,/>60<\/text>/,"humidity axis should extend beyond 40%");
}

// Hover reads the raw visible source but does not pretend a stale source is
// valid in the middle of a rendered gap.
{
  const p=panel(), tooltip={style:{}}, cursor={setAttribute:()=>{}};
  p.historyDetail=false; p.shown={session}; p.historyIndex([record(0,70)]);
  p.$=id=>id==="#tooltip"?tooltip:cursor;
  const svg={getBoundingClientRect:()=>({left:0,top:0,width:1200}),closest:()=>svg};
  p.hoverChart({target:svg,clientX:600,clientY:100});
  assert.doesNotMatch(tooltip.innerHTML,/70/,"stale raw sample must stay out of hover");
}

// Updating an existing chart preserves its SVG node and immediately recomputes
// a visible hover against the appended archive index.
{
  const p=panel(), current={setAttribute:()=>{},innerHTML:""};let hover;
  p.chart=()=>'<svg class="session-chart" aria-label="history"><path/></svg>';
  p.$=selector=>selector==="svg.session-chart"?current:null;
  p.lastHistoryPointer={clientX:90,clientY:40};p.scheduleHover=event=>{hover=event;};
  assert.equal(p.updateHistoryChart([],session,[]),true);
  assert.equal(hover.svg,current,"tooltip is recomputed on the retained SVG node");
}

console.log("panel chart regressions passed");
