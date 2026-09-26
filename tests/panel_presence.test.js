const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
let Panel;
vm.runInNewContext(fs.readFileSync('custom_components/ha_sauna/panel.js','utf8'),{
  HTMLElement:class {},customElements:{get:()=>undefined,define:(_n,c)=>Panel=c},
  Date,Map,Set,Math,Number,String,Object,Array,Infinity,
});
const t='2026-01-01T12:00:00Z', end='2026-01-01T12:10:00Z';
const panel=Object.assign(Object.create(Panel.prototype),{
  state:{now:end,configuration:{parameters:{sensor_timeout_seconds:60}},presence:{configured_source:'ha_presence',current:{available:true,occupancy:'unknown',assertion:'proxy_retraction',effective_at:t},external:{'<sensor>':{available:false,assertion:'direct_presence',effective_at:t}}},rule_inputs:{gang_veto:true}},
  window:[Date.parse(t),Date.parse(end)],positions:new Set(),historyIndex:()=>{},series:()=>[],
});
const detail=panel.presenceDetails();
assert.match(detail,/Proxy zurückgenommen; keine beobachtete Abwesenheit/);
assert.match(detail,/Externe Präsenz \(vorbereitet\)/);
assert.match(detail,/Nicht verfügbar/);
assert.match(detail,/&lt;sensor&gt;/);
assert.match(detail,/keine Wiedergabe/);
const session={ended_at:end,timeline:{processed:[]},heating:{intervals:[{started_at:t,ended_at:end}]}};
const gangs=[{gang_id:'g',started_at:t,ended_at:end,infusion_events:[{}]}];
const records=[{kind:'phase',received_at:t,payload:{phase:'aufheizen'}}];
panel.shown={phase_projection:{complete:true,intervals:[{started_at:t,ended_at:end,phase:'saunagang',source_id:'g'}]}};
let chart=panel.chart(records,session,gangs);
assert.equal((chart.match(/class="gang"/g)||[]).length,1,'one projected main phase, no duplicate gang overlay');
assert.equal((chart.match(/class="heat"/g)||[]).length,0,'raw phase not rendered over projection');
assert.equal((chart.match(/class="heat actual-heat"/g)||[]).length,1,'actual heating remains separate');
panel.shown={};
chart=panel.chart(records,session,gangs);
assert.match(chart,/class="heat"/,'legacy archive keeps its fallback');
console.log('presence and phase projection panel tests passed');
