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
assert.ok(panelSource.includes("`/${this.entry}/temperature`"));
assert.ok(panelSource.includes("`/${this.entry}/program`"));
assert.match(panelSource, /<foreignObject[\s\S]*id="target"[\s\S]*type="range"/);
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

// Humidity uses a useful percentage scale above 40 rather than clipping it.
{
  const p=panel(), svg=p.chart([record(1,80),record(1,55,"humidity")],session,[]);
  assert.match(svg,/>60<\/text>/,"humidity axis should extend beyond 40%");
}

// Hover reads the raw visible source but does not pretend a stale source is
// valid in the middle of a rendered gap.
{
  const p=panel(), tooltip={style:{}}, cursor={setAttribute:()=>{}};
  p.historyDetail=false; p.shown={session}; p.chartMeasurements=[record(0,70).payload];
  p.$=id=>id==="#tooltip"?tooltip:cursor;
  const svg={getBoundingClientRect:()=>({left:0,top:0,width:1200}),closest:()=>svg};
  p.hoverChart({target:svg,clientX:600,clientY:100});
  assert.doesNotMatch(tooltip.innerHTML,/70/,"stale raw sample must stay out of hover");
}

console.log("panel chart regressions passed");
