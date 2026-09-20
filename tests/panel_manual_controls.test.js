// Runs without Home Assistant or a browser: manual-detail routes stay local to
// the panel and only send the narrow API commands selected by the admin.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

let Panel;
const sandbox = {
  HTMLElement: class {},
  customElements: {get: () => undefined, define: (_name, value) => { Panel=value; }},
  Date, Map, Set, Math, Number, String, Object, Array, Infinity,
};
const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
vm.runInNewContext(source, sandbox);

assert.match(source, /<h2>Manuelle Steuerung<\/h2>/);
assert.match(source, /data-action="heater:true"/);
assert.match(source, /data-action="heater:false"/);
assert.match(source, /data-action="heater:auto"/);
assert.match(source, /data-action="manual-light"/);
assert.match(source, /Kühlrest pausiert/);
assert.match(source, /bis zum nächsten Phasenwechsel aktiv/);
assert.match(source, /s\.configuration\.program_mode==="progressive"/);
assert.doesNotMatch(source, /Temperaturprogramm<\/dt><dd>\$\{p\.final_temperature_c!=null/);

const makePanel = (permissions, lightValue="42") => {
  const calls=[];
  const panel=Object.assign(Object.create(Panel.prototype), {
    entry:"entry-1", state:{permissions},
    api:async (...args) => calls.push(args), refresh:async () => {}, message:() => {},
    $:selector => selector==="#manual-light-value" ? {value:lightValue} : null,
  });
  return {panel,calls};
};

(async () => {
  const enabled={admin:true,heater:true,light:true};
  let {panel,calls}=makePanel(enabled);
  await panel.action("heater:true");
  await panel.action("heater:false");
  await panel.action("heater:auto");
  await panel.action("manual-light");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["/entry-1/heater","POST",{value:true}],
    ["/entry-1/heater","POST",{value:false}],
    ["/entry-1/heater","POST",{value:null}],
    ["/entry-1/light","POST",{value:42}],
  ]);

  ({panel,calls}=makePanel(enabled,"101"));
  await assert.rejects(() => panel.action("manual-light"), /Helligkeit zwischen 0 und 100/);
  assert.equal(calls.length,0,"invalid brightness must not reach the actuator API");

  ({panel,calls}=makePanel({admin:false,heater:false,light:false}));
  await panel.action("heater:true");
  await panel.action("manual-light");
  assert.equal(calls.length,0,"non-admin controls must not route API calls");
  console.log("panel manual control regressions passed");
})().catch(error => { console.error(error); process.exitCode=1; });
