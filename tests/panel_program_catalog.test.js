const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

let Panel;
const sandbox = {
  HTMLElement: class {}, customElements: {get: () => undefined, define: (_name, value) => { Panel=value; }},
  Date, Map, Set, Math, Number, String, Object, Array, Infinity, crypto:{randomUUID:()=>"new-program"},
};
const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
vm.runInNewContext(source, sandbox);

const programs = [
  {id:"quiet",name:"Ruhige Runde",start_c:80,end_c:90,distribution_gangs:3},
  {id:"program_1",name:"Bisheriges Programm 1",start_c:85,end_c:95,distribution_gangs:2},
];

// A program's compact label is calculated from the backend DTO, never from a
// frontend profile list or a supposed maximum number of sauna rounds.
{
  const p=Object.assign(Object.create(Panel.prototype), {state:{parameters:[{key:"target_temperature_c",minimum:60,maximum:100},{key:"temperature_gangs",minimum:1,maximum:8}]}});
  assert.equal(p.programSteps(programs[0]),"80 → 85 → 90 °C");
  assert.equal(p.programSteps({start_c:82,end_c:99,distribution_gangs:1}),"82 → 99 °C", "one distribution step still reaches the configured end target");
  assert.equal(p.programSteps({temperature_steps:[80,86,90]}),"80 → 86 → 90 °C", "manual stages remain exact");
  assert.deepEqual(JSON.parse(JSON.stringify(p.distributedSteps(80,90,9))),[],"stage expansion observes the configured maximum");
}

assert.match(source,/configuration\.temperature_programs/);
assert.match(source,/start_c:\s*Number\(/);
assert.match(source,/distribution_gangs:\s*Number\(/);
assert.doesNotMatch(source,/const profiles=\["program_1","program_2"\]/);
assert.match(source,/`\/\$\{this\.entry\}\/programs`/);
assert.match(source,/`\/\$\{this\.entry\}\/button-program`/);
assert.match(source,/crypto\?\.randomUUID/);
assert.match(source,/crypto\?\.getRandomValues/);
assert.match(source,/temperature_steps/);
assert.doesNotMatch(source,/<select id="program-select"/);
assert.match(source,/data-action="program-mode:program"/);
assert.match(source,/data-action="program-select:\$\{esc\(program\.id\)\}"/);

// Catalog saves send the stable IDs and all exact backend fields in one body.
{
  const calls=[];
  const p=Object.assign(Object.create(Panel.prototype), {
    entry:"entry-1", programDraft:programs.map(program=>({...program})), readProgramDraft(){return this.programDraft;},
    api:async(...args)=>calls.push(args), refresh:async()=>{}, settingsEntry:"entry-1",
  });
  p.savePrograms().then(()=>{
    assert.deepEqual(JSON.parse(JSON.stringify(calls)),[['/entry-1/programs','POST',{programs}]]);
    assert.equal(p.programDraft,null);
    console.log("panel program catalog regressions passed");
  }).catch(error=>{throw error;});
}

// Mode and named-program selections only alter the local draft.  They use the
// saved named program again when returning to "Programm" and never call the
// backend before the shared apply action.
{
  const calls=[];
  const p=Object.assign(Object.create(Panel.prototype), {
    state:{permissions:{program:true},configuration:{program_mode:"progressive",selected_program_id:"quiet",temperature_programs:programs}},
    $:()=>null, drawCurrent:()=>{}, api:async(...args)=>calls.push(args), message:()=>{},
  });
  assert.equal(p.programMode(programs),"program","a saved named ID selects the Program type");
  p.selectProgramMode("individual",programs);
  assert.equal(p.programSelectionDraft,"individual");
  assert.equal(p.programMode(programs),"individual");
  p.selectProgramMode("program",programs);
  assert.equal(p.programSelectionDraft,"quiet","Program restores the saved named selection");
  p.action("program-select:program_1");
  assert.equal(p.programSelectionDraft,"program_1","named row selection remains a draft");
  assert.deepEqual(calls,[]);
  p.selectProgramMode("constant",programs);
  assert.equal(p.programMode(programs),"constant");
  assert.deepEqual(calls,[]);
  console.log("panel temperature mode selection regressions passed");
}

// The uniform form keeps the existing live end/distribution path.  Only an
// explicitly edited start creates a new progression; it never becomes a stage
// array merely because the user chose "Individuell".
{
  const calls=[], inputs={"#progression-start":{value:"80"},"#progression-end":{value:"95"},"#progression-gangs":{value:"4"}};
  let release;const requested=new Promise(resolve=>{release=resolve;});
  const p=Object.assign(Object.create(Panel.prototype), {
    entry:"entry-1", message:()=>{}, drawCurrent:()=>{}, refresh:async()=>{}, $:selector=>inputs[selector],
    state:{permissions:{program:true,temperature:true},parameters:[{key:"target_temperature_c",minimum:60,maximum:100},{key:"temperature_gangs",minimum:1,maximum:8}],configuration:{program_mode:"progressive",selected_program_id:null,temperature_programs:[],parameters:{target_temperature_c:80,final_temperature_c:90,temperature_gangs:4}}},
    api:async(...args)=>{calls.push(args);await requested;},
  });
  const saved=p.action("program-apply");
  assert.equal(p.programSaveState,"saving");
  release();
  saved.then(()=>{
    assert.deepEqual(JSON.parse(JSON.stringify(calls)),[["/entry-1/temperature","POST",{final_temperature_c:95}]]);
    console.log("panel uniform individual regressions passed");
  }).catch(error=>{throw error;});
}

// Selecting "Individuell" remains local until the explicit apply button sends
// the exact stage array accepted by the backend.
{
  const calls=[];
  const p=Object.assign(Object.create(Panel.prototype), {
    entry:"entry-1", message:()=>{}, drawCurrent:()=>{}, refresh:async()=>{}, freeProgramKind:"steps", freeProgramValues:()=>[80,86,90],
    state:{permissions:{program:true},configuration:{program_mode:"progressive",selected_program_id:null,temperature_programs:[]}},
    api:async(...args)=>calls.push(args),
  });
  assert.equal(p.programChoice([]),"individual");
  p.action("program-apply").then(()=>{
    assert.deepEqual(JSON.parse(JSON.stringify(calls)),[["/entry-1/program","POST",{temperature_steps:[80,86,90]}]]);
    console.log("panel individual program regressions passed");
  }).catch(error=>{throw error;});
}

// Switching from constant or a named program to an individual progression is a new
// program, even when the visible start has not been edited.
for (const selected of [null, "quiet"]) {
  const calls=[], inputs={"#progression-start":{value:"80"},"#progression-end":{value:"95"},"#progression-gangs":{value:"4"}};
  const p=Object.assign(Object.create(Panel.prototype), {
    entry:"entry-1", message:()=>{}, drawCurrent:()=>{}, refresh:async()=>{}, $:selector=>inputs[selector], programSelectionDraft:"individual",
    state:{permissions:{program:true,temperature:true},parameters:[{key:"target_temperature_c",minimum:60,maximum:100},{key:"temperature_gangs",minimum:1,maximum:8}],configuration:{program_mode:selected?"progressive":"constant",selected_program_id:selected,temperature_programs:programs,parameters:{target_temperature_c:80,final_temperature_c:90,temperature_gangs:4}}},
    api:async(...args)=>calls.push(args),
  });
  assert.equal(p.programDirty(),true,"a changed selection enables apply");
  p.action("program-apply").then(()=>{
    assert.deepEqual(JSON.parse(JSON.stringify(calls)),[["/entry-1/program","POST",{target_temperature_c:80,final_temperature_c:95,temperature_gangs:4}]]);
    assert.equal(p.programSaveState,"saved");
    assert.equal(p.programSelectionDraft,null);
    assert.equal(p.programDirty(),false,"the saved selection disables apply");
    console.log("panel constant individual regressions passed");
  }).catch(error=>{throw error;});
}

// A refresh must not rebuild an already visible catalog from its older draft.
// This covers the interval refresh after adding a row and after a rejected save.
{
  const library={dataset:{editable:"true"},innerHTML:"Name geändert"};
  const p=Object.assign(Object.create(Panel.prototype), {
    hass:{user:{is_admin:true}}, programDraft:programs.map(program=>({...program})), programLibraryNeedsRender:false,
    state:{configuration_locked:false,configuration:{temperature_programs:programs,button_program:"current"},parameters:[{key:"target_temperature_c",minimum:60,maximum:110},{key:"temperature_gangs",minimum:1,maximum:8}]},
    $:selector=>selector==="#program-library"?library:null,
  });
  p.renderProgramLibrary();
  assert.equal(library.innerHTML,"Name geändert","ordinary refresh retains edited catalog inputs");
  p.readProgramDraft=()=>[{...programs[0],name:"Nach Fehler"}];
  p.api=async()=>{throw Error("server rejected catalog");};
  p.savePrograms().catch(()=>{}).then(()=>{
    assert.equal(p.programDraft[0].name,"Nach Fehler","rejected save retains the submitted draft");
    p.renderProgramLibrary();
    assert.equal(library.innerHTML,"Name geändert","rejected save does not replace visible inputs");
    console.log("panel program draft regressions passed");
  }).catch(error=>{throw error;});
}
