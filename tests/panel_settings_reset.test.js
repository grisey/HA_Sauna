// Runs without Home Assistant or a browser: reset stays restricted to an idle
// administrator and waits for HA's reloaded configuration before clearing edits.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

let Panel;
const sandbox = {
  HTMLElement: class {},
  customElements: {get: () => undefined, define: (_name, value) => { Panel=value; }},
  Date, Map, Set, Math, Number, String, Object, Array, Infinity,
  setTimeout: callback => callback(),
};
const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
vm.runInNewContext(source, sandbox);

assert.match(source, /Standardwerte wiederherstellen/);
assert.match(source, /Die Zuordnung von Sensoren, Geräten und Tastern bleibt erhalten/);

const defaults = {target_temperature_c: 80};
const configuration = {log_level:"INFO", program_mode:"constant", button_program:"current"};
const makePanel = ({admin=true, locked=false}={}) => {
  const calls=[], edited={dataset:{edited:"true"}};
  let stateReads=0, refreshed=false;
  const panel=Object.assign(Object.create(Panel.prototype), {
    entry:"entry-1", draft:{target:"95"}, settingsEntry:"entry-1",
    state:{configuration_locked:locked, permissions:{admin}},
    message:() => {},
    shadowRoot:{
      querySelectorAll:selector => selector==="#parameters input[data-edited]"?[edited]:[],
      querySelector:() => null,
    },
    refresh:async () => { refreshed=true; },
    api:async (...args) => {
      calls.push(args);
      if(args[0]==="/entry-1/parameters/reset")return {parameters:defaults,configuration};
      if(args[0]==="/entry-1/state") {
        stateReads++;
        return {configuration:stateReads===1?{parameters:defaults,log_level:"DEBUG",program_mode:"progressive",button_program:"program_1"}:{parameters:defaults,...configuration}};
      }
      throw Error(`unexpected API request ${args[0]}`);
    },
  });
  return {panel,calls,edited,get refreshed(){return refreshed;}};
};

(async () => {
  let test=makePanel();
  await test.panel.action("reset-settings");
  assert.deepEqual(JSON.parse(JSON.stringify(test.calls.filter(([path])=>path.includes("reset")))), [
    ["/entry-1/parameters/reset","POST"],
  ], "one reset POST is sent");
  assert.equal(test.calls.filter(([path])=>path==="/entry-1/state").length,2, "waits through stale log and program state until defaults are loaded");
  assert.equal(test.panel.draft,null, "draft values are cleared only after loaded state");
  assert.equal(test.panel.settingsEntry,null, "settings form is rebuilt from the loaded state");
  assert.equal(test.edited.dataset.edited,undefined, "edited input markers are cleared");
  assert.equal(test.refreshed,true, "loaded settings, program and logging state are refreshed");

  test=makePanel({admin:false});
  await test.panel.action("reset-settings");
  assert.equal(test.calls.length,0, "non-admin reset must not reach the API");

  test=makePanel({locked:true});
  await test.panel.action("reset-settings");
  assert.equal(test.calls.length,0, "reset during an active session must not reach the API");
  console.log("panel settings reset regressions passed");
})().catch(error => { console.error(error); process.exitCode=1; });
