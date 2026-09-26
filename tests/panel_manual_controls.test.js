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

assert.match(source, /manual-section manual-heater/);
assert.match(source, /data-action="heater:true"/);
assert.match(source, /data-action="heater:false"/);
assert.match(source, /data-action="heater:auto"/);
assert.match(source, /data-action="control-mode:automatic"/);
assert.match(source, /data-action="control-mode:manual"/);
assert.match(source, /manual-light-overview/);
assert.match(source, /Gedimmt <small>\$\{num\(light\.normal,\s*0\)\} %<\/small>/);
assert.doesNotMatch(source, /Normallicht/);
assert.doesNotMatch(source, /Raumlicht/);
assert.doesNotMatch(source, /Zwangskühlung pausiert/);
assert.ok(source.includes("Übersteuerungen folgen spätestens nach"));
assert.doesNotMatch(source, /bis zum nächsten Phasenwechsel aktiv/);
assert.match(source, /s\.configuration\.program_mode\s*===\s*"progressive"/);
assert.doesNotMatch(source, /Temperaturprogramm<\/dt><dd>\$\{p\.final_temperature_c!=null/);
assert.doesNotMatch(source, /Servus|greeting/);

const makePanel = (permissions, lightValue="42") => {
  const calls=[];
  const panel=Object.assign(Object.create(Panel.prototype), {
    entry:"entry-1", state:{permissions},
    api:async (...args) => calls.push(args), refresh:async () => {}, message:() => {},
    $:selector => ["#manual-light-value", "#manual-light-value-overview"].includes(selector) ? {value:lightValue} : null,
  });
  return {panel,calls};
};

const renderCurrent = (mode, controls={}, heatingFeedback=false, target="#current", grants={}, locked=false, session=null, startErrors=[]) => {
  const permissions={admin:true,control:true,heater:true,light:true,temperature:true,program:true,...grants};
  const nodes=new Map();
  const node=selector => {
    if(!nodes.has(selector))nodes.set(selector,{innerHTML:"",hidden:false});
    return nodes.get(selector);
  };
  const panel=Object.assign(Object.create(Panel.prototype), {
    hass:{user:{name:"Testperson",is_admin:permissions.admin}}, $:node, progressionDraft:null, manualLightDraft:null,
    state:{
      now:"2026-09-20T12:00:00Z", session, last_session:null,
      configuration:{control_mode:mode,program_mode:"constant",selected_program_id:null,
        temperature_programs:[],parameters:{preset_count:0,preset_start_c:70,preset_step_c:5,
          session_light_brightness_percent:50,manual_override_minutes:10}},
      parameters:[{key:"target_temperature_c",minimum:30,maximum:100,integer:false}],
      measurements:[],measurement_status:{},mechanical_timer:{state:"idle",remaining_seconds:0},
      manual_controls:{light:{normal:25,automatic:12,...controls.light},heater:controls.heater||{}}, permissions, configuration_locked:locked,
      issues:[],start_errors:startErrors,operation_enabled:false,heating_feedback:heatingFeedback,
      heating_observation:{source:"unknown"},phase:"manuell",gang_count:0,energy_kwh:0,
      energy_source:"estimated",target_temperature:80,
      start_availability:null,phase_timer:null,
    },
  });
  panel.drawCurrent();
  return nodes.get(target).innerHTML;
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
  const preferenceCalls=[], startButton={disabled:false,textContent:"Als Startseite festlegen"}, startStatus={textContent:""};
  const startPage=Object.assign(Object.create(Panel.prototype), {
    state:{permissions:{admin:false}}, message:()=>{}, api:async()=>{throw Error("Sauna API must not be called");},
    hass:{user:{is_admin:false},callWS:async request=>{
      preferenceCalls.push(request);
      return request.type==="frontend/get_user_data" ? {value:{default_panel:"lovelace",theme:"night",sidebarHidden:true}} : undefined;
    }},
    $:selector=>selector==='[data-action="default-page"]'?startButton:selector==="#start-page-status"?startStatus:null,
  });
  await startPage.action("default-page");
  assert.deepEqual(JSON.parse(JSON.stringify(preferenceCalls)),[
    {type:"frontend/get_user_data",key:"core"},
    {type:"frontend/set_user_data",key:"core",value:{default_panel:"ha-sauna",theme:"night",sidebarHidden:true}},
  ]);
  assert.equal(startButton.textContent,"Als Startseite festgelegt");

  const failedButton={disabled:false}, failedStatus={textContent:"vorher"};
  const failedStartPage=Object.assign(Object.create(Panel.prototype), {
    state:{permissions:{admin:false}}, message:()=>{}, hass:{callWS:async()=>{throw Error("profile unavailable");}},
    $:selector=>selector==='[data-action="default-page"]'?failedButton:selector==="#start-page-status"?failedStatus:null,
  });
  await assert.rejects(()=>failedStartPage.action("default-page"),/profile unavailable/);
  assert.equal(failedButton.disabled,false,"a failed profile update leaves the button usable");
  const automatic=renderCurrent("automatic"), manual=renderCurrent("manual");
  assert.match(automatic,/Temperaturwahl/);
  assert.match(automatic,/data-action="program-mode:program" aria-pressed="false"/);
  assert.match(automatic,/data-action="program-mode:individual" aria-pressed="false"/);
  assert.match(automatic,/data-action="program-mode:constant" aria-pressed="true"/);
  assert.match(automatic,/temperature-presets/);
  assert.doesNotMatch(automatic,/program-named-list|program-form/);
  assert.doesNotMatch(automatic,/Gedimmt/);
  assert.match(automatic,/data-action="light:auto"[^>]*>Automatik<\/button>/);
  assert.match(automatic,/class="card environment-status"[\s\S]*data-door-status[\s\S]*Heizung/);
  assert.doesNotMatch(manual,/Temperaturwahl|program-types|temperature-presets/);
  assert.match(manual,/data-action="manual-light-overview"/);
  assert.match(manual,/id="manual-light-value-overview"/);
  assert.match(manual,/Gedimmt <small>25 %<\/small>/);
  assert.match(manual,/Hell <small>50 %<\/small>/);
  assert.doesNotMatch(manual,/environment-status|data-door-status/,"manual overview has no environment status tile");

  const userAutomatic=renderCurrent("automatic",{},false,"#current",{admin:false,heater:false});
  assert.match(userAutomatic,/Temperaturwahl/);
  assert.match(userAutomatic,/data-target-arc="true"/);
  assert.match(userAutomatic,/data-action="control-mode:manual" aria-selected="false" >Manuell/);
  assert.match(userAutomatic,/data-action="light:auto"/);
  assert.doesNotMatch(userAutomatic,/data-action="heater:|manual-light-value/);
  const userManual=renderCurrent("manual",{},false,"#current",{admin:false});
  assert.match(userManual,/data-action="control-mode:automatic" aria-selected="false" >Automatik/);
  assert.match(userManual,/data-action="heater:true" aria-pressed="false" >EIN/);
  assert.match(userManual,/data-action="heater:false" aria-pressed="false" >AUS/);
  for(const preset of ["false","normal","true"])assert.ok(userManual.includes(`data-action="light:${preset}"`));
  assert.doesNotMatch(userManual,/Temperaturwahl|data-target-arc|heater:auto|manual-light-value|Freie Helligkeit/);
  const userLocked=renderCurrent("manual",{},false,"#current",{admin:false},true);
  assert.match(userLocked,/data-action="control-mode:automatic"[^>]*disabled/);
  assert.match(userLocked,/data-action="control-mode:manual"[^>]*disabled/);
  const readOnly=renderCurrent("manual",{},false,"#current",{admin:false,control:false,heater:false,light:false});
  for(const action of ["control-mode:automatic","control-mode:manual","heater:true","heater:false","light:true"])
    assert.match(readOnly,new RegExp(`data-action="${action}"[^>]*disabled`));

  const pausedSession={
    timeline:{active:null,completed:[],session_started_at:"2026-09-20T11:00:00Z"},
    heating:{elapsed_seconds:0},
    deadlines:[{purpose:"session_gap",token:"gap-current",due_at:"2026-09-20T12:10:00Z"}],
  };
  const pausedOverview=renderCurrent("automatic",{},false,"#current",{admin:false,control:true},false,pausedSession);
  const pausedDetails=renderCurrent("automatic",{},false,"#details",{admin:false,control:true},false,pausedSession);
  for(const markup of [pausedOverview,pausedDetails]) {
    assert.match(markup,/class="tile operation stop" data-action="finish-session:gap-current"[^>]*>Endgültig beenden<\/button>/);
    assert.match(markup,/data-action="operation"[^>]*>Fortsetzen<\/button>/);
    assert.doesNotMatch(markup,/Einschalten/);
  }
  const pausedWithStartError=renderCurrent("automatic",{},false,"#current",{admin:false,control:true},false,pausedSession,["missing_sensor"]);
  assert.match(pausedWithStartError,/data-action="finish-session:gap-current"(?![^>]*disabled)/);
  assert.match(pausedWithStartError,/data-action="operation"[^>]*disabled>Fortsetzen<\/button>/);
  ({panel,calls}=makePanel({admin:false,control:true}));
  panel.state.operation_enabled=false;
  await panel.action("finish-session:gap-current");
  await panel.action("operation");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),[
    ["/entry-1/finish-session","POST",{token:"gap-current"}],
    ["/entry-1/control","POST",{enabled:true}],
  ],"finishing uses the displayed gap token while resuming stays a regular control request");
  calls.length=0;
  panel.state.permissions.control=false;
  await panel.action("finish-session:gap-current");
  assert.equal(calls.length,0,"finishing also requires control permission in the action handler");

  ({panel,calls}=makePanel({admin:false,control:true,heater:true,light:true}));
  panel.state.configuration={control_mode:"manual"};
  panel.state.operation_enabled=false;
  await panel.action("heater:true");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),[
    ["/entry-1/control","POST",{enabled:true}],
    ["/entry-1/heater","POST",{value:true}],
  ],"a regular user's manual ON starts the operation before the heater");
  calls.length=0;
  await panel.action("control-mode:automatic");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),[["/entry-1/control-mode","POST",{mode:"automatic"}]]);
  calls.length=0;
  panel.state.configuration_locked=true;
  await panel.action("control-mode:manual");
  await panel.action("manual-light-overview");
  await panel.action("details");
  assert.equal(calls.length,0,"session locking and admin actions remain enforced");
  panel.state.permissions.heater=false;
  await panel.action("heater:true");
  assert.equal(calls.length,0,"automatic heater overrides still require the server's permission");
  panel.state.permissions.control=false;
  panel.state.configuration_locked=false;
  await panel.action("control-mode:automatic");
  assert.equal(calls.length,0,"a user without control permission cannot change modes");

  const automaticHeater=renderCurrent("automatic",{heater:{manual:null},light:{manual:null}},true,"#details");
  assert.match(automaticHeater,/data-action="heater:auto" aria-pressed="true"/);
  assert.match(automaticHeater,/data-action="heater:true" aria-pressed="false"/);
  assert.match(automaticHeater,/Ofen an/,"physical feedback remains visible when the manual choice is automatic");
  assert.doesNotMatch(automaticHeater,/data-action="heater:true" class="primary"/);
  const automaticLight=renderCurrent("automatic",{light:{manual:null}});
  assert.match(automaticLight,/data-action="light:auto" aria-pressed="true"/);

  const lightOff=renderCurrent("manual",{heater:{manual:true},light:{manual:0}});
  assert.match(lightOff,/data-action="heater:true" aria-pressed="true"/);
  assert.match(lightOff,/data-action="light:false" aria-pressed="true"/);
  const lightDimmed=renderCurrent("manual",{light:{manual:25.4}});
  assert.match(lightDimmed,/data-action="light:normal" aria-pressed="true"/);
  const lightBright=renderCurrent("manual",{light:{manual:50.4}});
  assert.match(lightBright,/data-action="light:true" aria-pressed="true"/);
  const freeLight=renderCurrent("manual",{light:{manual:42}});
  assert.doesNotMatch(freeLight,/data-action="light:(?:false|normal|true)" aria-pressed="true"/,"a free brightness does not select a named preset");

  const detailNodes=new Map();
  const detailNode=selector => {
    if(!detailNodes.has(selector))detailNodes.set(selector,{innerHTML:"",hidden:false});
    return detailNodes.get(selector);
  };
  const detailPanel=Object.assign(Object.create(Panel.prototype), {
    hass:{user:{name:"Admin",is_admin:true}}, $:detailNode, progressionDraft:null, manualLightDraft:null,
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

  const nodes={"#current":{},"#details":{},"#history":{hidden:true},"#settings":{},"#plots":{},"#detection-plots":{},"#gangs":{},"#event-list":{}};
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
