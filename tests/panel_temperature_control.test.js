// Runs without Home Assistant or a browser: the temperature arc keeps its
// geometry and uses only the parameter definition for its limits.
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

assert.match(source, /data-target-arc/);
assert.match(source, /role="slider"/);
assert.doesNotMatch(source, /foreignObject[^`]*id="target"/);
assert.match(source, /Temperaturautomatik/);

const state = {
  permissions: {temperature: true}, target_temperature: 80,
  configuration: {parameters: {sauna_min_temperature_c: 60}},
  parameters: [{key: "target_temperature_c", minimum: 60, maximum: 100}, {key: "temperature_gangs", minimum: 1, maximum: 8}],
};
const calls=[];
const panel=Object.assign(Object.create(Panel.prototype), {
  entry: "entry-1", state, api: async (...args) => calls.push(args), refresh: async () => {},
  drawCurrent: () => {}, $: () => null,
  svgCoordinates: (_svg, x, y) => ({x, y}),
});

assert.deepEqual(JSON.parse(JSON.stringify(panel.temperatureBounds())), {minimum: 60, maximum: 100});
assert.equal(panel.temperatureValueAt({}, 150, 25), 60, "the top of the gauge is the 60 °C scale point");
assert.equal(panel.temperatureValueAt({}, 255, 130), 100, "pointer coordinates follow the dial arc before clamping");
assert.equal(panel.clampTemperature(60.2, {minimum: 60.2, maximum: 100}), 60.2, "rounding cannot escape a fractional minimum");

{
  const inputs={"#progression-start":{value:"60"},"#progression-end":{value:"100"},"#progression-gangs":{value:"8"}};
  const p=Object.assign(Object.create(Panel.prototype), {state, $:selector=>inputs[selector]});
  assert.deepEqual(JSON.parse(JSON.stringify(p.progressionValues())), {start:60,end:100,gangs:8}, "free progression accepts the catalog's dynamic bounds");
  inputs["#progression-start"].value="0";
  assert.throws(()=>p.progressionValues(), /zulässigen Grenzen/, "free progression rejects an out-of-range temperature before the request");
  inputs["#progression-start"].value="60";
  inputs["#progression-gangs"].value="21";
  assert.throws(()=>p.progressionValues(), /zulässigen Grenzen/, "free progression rejects an out-of-range distribution before the request");
}

(async () => {
  const svg={setPointerCapture: () => {}, releasePointerCapture: () => {}};
  panel.beginTemperatureDrag({pointerId: 4, clientX: 255, clientY: 130, preventDefault: () => {}}, svg);
  await panel.endTemperatureDrag({pointerId: 4});
  assert.deepEqual(JSON.parse(JSON.stringify(calls.shift())), ["/entry-1/temperature", "POST", {target_temperature_c: 100}]);

  let prevented=false;
  await panel.keyTemperatureTarget({key: "ArrowUp", preventDefault: () => { prevented=true; }});
  assert.equal(prevented, true, "slider keys suppress page scrolling");
  assert.deepEqual(JSON.parse(JSON.stringify(calls.shift())), ["/entry-1/temperature", "POST", {target_temperature_c: 80.5}]);

  // A new progression clicked while a direct target request is pending starts
  // at that target, unless the user explicitly replaced the Start field.
  for(const explicitStart of [false,true]){
    let releaseTarget, targetSaved=new Promise(resolve => { releaseTarget=resolve; });
    const progressionCalls=[], inputs={
      "#progression-start":{value:explicitStart?"82":"80"},
      "#progression-end":{value:"86"}, "#progression-gangs":{value:"3"},
    };
    const racing=Object.assign(Object.create(Panel.prototype), {
      entry:"entry-1", message:() => {}, $:selector => inputs[selector]||null,
      state:{permissions:{program:true,temperature:true},parameters:[{key:"target_temperature_c",minimum:60,maximum:100},{key:"temperature_gangs",minimum:1,maximum:8}],configuration:{parameters:{target_temperature_c:80,final_temperature_c:95,temperature_gangs:4}}},
      api:async (...args) => {
        progressionCalls.push(args);
        if(args[0]==="/entry-1/temperature"){await targetSaved;return {parameters:{target_temperature_c:75}};}
      },
      refresh:async () => {},
    });
    const target=racing.changeTarget(75);
    racing.progressionDraft={"progression-end":"86","progression-gangs":"3",...(explicitStart?{"progression-start":"82"}:{})};
    const program=racing.action("program-free");
    await Promise.resolve();
    assert.equal(progressionCalls.length,1,"program save waits for the direct target request");
    releaseTarget();
    await Promise.all([target,program]);
    assert.deepEqual(JSON.parse(JSON.stringify(progressionCalls.at(-1))), ["/entry-1/program","POST",{
      target_temperature_c:explicitStart?82:75,final_temperature_c:86,temperature_gangs:3,
    }], explicitStart?"an edited Start is retained":"an unedited Start follows the completed direct target");
  }
  console.log("panel temperature arc regressions passed");
})().catch(error => { console.error(error); process.exitCode=1; });
