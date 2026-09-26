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
assert.match(source, /data-action="control-mode:automatic"/);
assert.match(source, /data-action="control-mode:manual"/);
assert.match(source, /manual-light-overview/);
assert.match(source, /Gedimmt <small>\$\{num\(light\.normal,0\)\} %<\/small>/);
assert.doesNotMatch(source, /Normallicht/);
assert.doesNotMatch(source, /Raumlicht/);
assert.doesNotMatch(source, /Zwangskühlung pausiert/);
assert.ok(source.includes("Übersteuerungen folgen spätestens nach"));
assert.doesNotMatch(source, /bis zum nächsten Phasenwechsel aktiv/);
assert.match(source, /s\.configuration\.program_mode==="progressive"/);
assert.doesNotMatch(source, /Temperaturprogramm<\/dt><dd>\$\{p\.final_temperature_c!=null/);

const makePanel = (permissions, lightValue="42") => {
  const calls=[];
  const panel=Object.assign(Object.create(Panel.prototype), {
    entry:"entry-1", state:{permissions},
    api:async (...args) => calls.push(args), refresh:async () => {}, message:() => {},
    $:selector => ["#manual-light-value", "#manual-light-value-overview"].includes(selector) ? {value:lightValue} : null,
  });
  return {panel,calls};
};

const renderCurrent = mode => {
  const nodes=new Map();
  const node=selector => {
    if(!nodes.has(selector))nodes.set(selector,{innerHTML:"",hidden:false});
    return nodes.get(selector);
  };
  const panel=Object.assign(Object.create(Panel.prototype), {
    hass:{user:{name:"Admin"}}, $:node, progressionDraft:null, manualLightDraft:null,
    state:{
      now:"2026-09-20T12:00:00Z", session:null, last_session:null,
      configuration:{control_mode:mode,program_mode:"constant",selected_program_id:null,
        temperature_programs:[],parameters:{preset_count:0,preset_start_c:70,preset_step_c:5,
          session_light_brightness_percent:50,manual_override_minutes:10}},
      parameters:[{key:"target_temperature_c",minimum:30,maximum:100,integer:false}],
      measurements:[],measurement_status:{},mechanical_timer:{state:"idle",remaining_seconds:0},
      manual_controls:{light:{normal:25,automatic:12},heater:{}}, permissions:{admin:true,control:true,heater:true,light:true,temperature:true,program:true},
      issues:[],start_errors:[],operation_enabled:false,heating_feedback:false,
      heating_observation:{source:"unknown"},phase:"manuell",gang_count:0,energy_kwh:0,
      energy_source:"estimated",target_temperature:80,
      start_availability:null,phase_timer:null,
    },
  });
  panel.drawCurrent();
  return nodes.get("#current").innerHTML;
};

(async () => {
  const enabled={admin:true,heater:true,light:true};
  let {panel,calls}=makePanel(enabled);
  await panel.action("heater:true");
  await panel.action("heater:false");
  await panel.action("heater:auto");
  await panel.action("manual-light");
  await panel.action("manual-light-overview");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["/entry-1/heater","POST",{value:true}],
    ["/entry-1/heater","POST",{value:false}],
    ["/entry-1/heater","POST",{value:null}],
    ["/entry-1/light","POST",{value:42}],
    ["/entry-1/light","POST",{value:42}],
  ]);

  ({panel,calls}=makePanel(enabled,"101"));
  await assert.rejects(() => panel.action("manual-light"), /Helligkeit zwischen 0 und 100/);
  assert.equal(calls.length,0,"invalid brightness must not reach the actuator API");

  ({panel,calls}=makePanel({admin:false,heater:false,light:false}));
  await panel.action("heater:true");
  await panel.action("manual-light");
  assert.equal(calls.length,0,"non-admin controls must not route API calls");

  ({panel,calls}=makePanel({admin:true,heater:true,light:true,control:true}));
  panel.state.configuration={control_mode:"manual"};
  panel.state.operation_enabled=false;
  await panel.action("heater:true");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),[
    ["/entry-1/control","POST",{enabled:true}],
    ["/entry-1/heater","POST",{value:true}],
  ]);
  const automatic=renderCurrent("automatic"), manual=renderCurrent("manual");
  assert.match(automatic,/Temperaturprogramm/);
  assert.match(automatic,/Temperaturautomatik/);
  assert.doesNotMatch(automatic,/Gedimmt/);
  assert.match(automatic,/data-action="light:auto"[^>]*>Automatik<\/button>/);
  assert.doesNotMatch(manual,/Temperaturprogramm/);
  assert.doesNotMatch(manual,/Temperaturautomatik/);
  assert.match(manual,/data-action="manual-light-overview"/);
  assert.match(manual,/id="manual-light-value-overview"/);
  assert.match(manual,/Gedimmt <small>25 %<\/small>/);
  assert.match(manual,/Hell <small>50 %<\/small>/);

  const detailNodes=new Map();
  const detailNode=selector => {
    if(!detailNodes.has(selector))detailNodes.set(selector,{innerHTML:"",hidden:false});
    return detailNodes.get(selector);
  };
  const detailPanel=Object.assign(Object.create(Panel.prototype), {
    hass:{user:{name:"Admin"}}, $:detailNode, progressionDraft:null, manualLightDraft:null,
    state:{
      now:"2026-09-20T12:00:00Z", phase:"nachlauf", operation_enabled:true,
      session:{timeline:{active:null,door:"closed",completed:[]},heating:{elapsed_seconds:120},
        deadlines:[{purpose:"confirmation",due_at:"2026-09-20T12:03:00Z"}],
        after_run:{paused_at:"2026-09-20T11:59:00Z",duration_seconds:300,elapsed_seconds:120}},
      last_session:null, configuration:{control_mode:"automatic",program_mode:"constant",selected_program_id:null,
        temperature_programs:[],parameters:{preset_count:0,preset_start_c:70,preset_step_c:5,
          session_light_brightness_percent:50,manual_override_minutes:10,readiness_hysteresis_c:3,nominal_power_kw:4.5}},
      parameters:[{key:"target_temperature_c",minimum:30,maximum:100,integer:false}],measurements:[],measurement_status:{},
      mechanical_timer:{state:"paused",remaining_seconds:600,pause_reason:"contactor_off"},
      manual_controls:{light:{normal:25,automatic:40,override_ends_at:"2026-09-20T12:04:00Z"},heater:{}},
      permissions:{admin:true,control:true,heater:true,light:true,temperature:true,program:true},issues:[],start_errors:[],
      heating_feedback:false,heating_observation:{source:"unknown"},gang_count:0,energy_kwh:1.25,energy_source:"estimated",
      target_temperature:80,thermostat_target:85,start_availability:null,
      phase_timer:{kind:"heating",label:"Aufheizen seit",seconds:600},light_after_run:{ends_at:"2026-09-20T12:09:00Z"},
      detection_channels:["upper"],decision_text:"Ofen bleibt aus.",
    },
  });
  detailPanel.drawCurrent();
  const details=detailNodes.get("#details").innerHTML;
  assert.match(details,/Ofenkühlung pausiert[\s\S]*3:00 min/);
  assert.doesNotMatch(details,/Zwangskühlung/);
  assert.match(details,/data-phase-timer="heating"/);
  assert.match(details,/Lichtnachlauf/);
  assert.match(details,/Nennleistung für die Verbrauchsschätzung/);
  assert.match(details,/Erkennung mit: oberer Messposition/);

  const nodes={"#current":{},"#details":{},"#history":{hidden:true}};
  const detailTabs=[{dataset:{action:"detail"},setAttribute(name,value){this[name]=value;}},{dataset:{action:"detail-history"},setAttribute(name,value){this[name]=value;}}];
  const navigation=Object.assign(Object.create(Panel.prototype), {
    state:{permissions:{admin:true}}, message:() => {},
    $:selector => nodes[selector], drawHistory:() => {},
    shadowRoot:{querySelectorAll:selector => selector===".detail-tabs button"?detailTabs:[]},
  });
  await navigation.action("detail-history");
  assert.equal(navigation.view,"history");
  assert.equal(nodes["#history"].hidden,false);
  assert.equal(detailTabs[1]["aria-selected"],"true");
  assert.equal(detailTabs[0]["aria-selected"],"false");
  console.log("panel manual control regressions passed");
})().catch(error => { console.error(error); process.exitCode=1; });
