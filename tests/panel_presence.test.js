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

(async()=>{
  const archivedProjection={complete:false,intervals:[{started_at:t,ended_at:end,phase:'bereit'}]};
  const liveProjection={complete:true,intervals:[{started_at:t,ended_at:end,phase:'saunagang',source_id:'g'}]};
  const archivedSession={...session,timeline:{...session.timeline,session_id:'s'}};
  let apiState={...panel.state,session:archivedSession,phase_projection:liveProjection};
  let archiveCalls=0, rendered;
  const nodes={'#session':{innerHTML:'',value:''}};
  const refreshing=Object.assign(Object.create(Panel.prototype),panel,{
    isConnected:true,entry:'entry',generation:0,selected:'live',cache:new Map(),shadowRoot:{activeElement:null},
    $:selector=>nodes[selector]||null,invalidateHistoryIndex:()=>{},drawCurrent:()=>{},drawSettings:()=>{},
    message:error=>{if(error)throw error;},
    drawHistory(){rendered=this.chart(this.shown.records,this.shown.session,gangs);},
    api:async path=>{
      if(path==='/entry/state')return apiState;
      if(path==='/entry/archive')return [{session_id:'s',started_at:t,ended_at:end}];
      archiveCalls++;
      return {session:archivedSession,records:archiveCalls===1?records.map((r,i)=>({...r,id:i+1})):[],phase_projection:archivedProjection,next_after:null};
    },
  });
  await refreshing.refresh();
  assert.equal(refreshing.shown.phase_projection,liveProjection,'active session uses current runtime projection');
  assert.equal((rendered.match(/class="gang"/g)||[]).length,1);
  assert.doesNotMatch(rendered,/class="heat"/,'refresh preserves projection into chart');
  refreshing.selected='s';
  await refreshing.refresh();
  assert.equal(archiveCalls,1,'archive selection reuses loaded cache');
  assert.equal(refreshing.shown.phase_projection,archivedProjection,'cached archive keeps its own projection');
  assert.match(rendered,/class="ready"/);
  assert.doesNotMatch(rendered,/class="gang"/,'archive cannot inherit current gang projection');
  refreshing.selected='live';
  apiState={...apiState,session:null};
  await refreshing.refresh();
  assert.equal(refreshing.shown.phase_projection,archivedProjection,'last-session view uses archive projection when live session is absent');
  console.log('refresh projection transport tests passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
