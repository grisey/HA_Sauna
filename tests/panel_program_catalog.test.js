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
  const p=Object.create(Panel.prototype);
  assert.equal(p.programSteps(programs[0]),"80 → 85 → 90 °C");
  assert.equal(p.programSteps({start_c:82,end_c:99,distribution_gangs:1}),"82 °C");
}

assert.match(source,/configuration\.temperature_programs/);
assert.match(source,/start_c:Number\(/);
assert.match(source,/distribution_gangs:Number\(/);
assert.doesNotMatch(source,/const profiles=\["program_1","program_2"\]/);
assert.match(source,/`\/\$\{this\.entry\}\/programs`/);
assert.match(source,/`\/\$\{this\.entry\}\/button-program`/);
assert.match(source,/crypto\?\.randomUUID/);
assert.match(source,/crypto\?\.getRandomValues/);

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
