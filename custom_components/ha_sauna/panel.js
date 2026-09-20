/* Native HA custom panel. Backend objects are authoritative; no control model here. */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const stamp = v => v ? new Date(v).getTime() : null;
const when = v => v ? new Date(v).toLocaleString("de-DE", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "–";
const clock = v => new Date(v).toLocaleTimeString("de-DE", {hour:"2-digit",minute:"2-digit"});
const num = (v,d=1) => v == null ? "–" : Number(v).toLocaleString("de-DE",{maximumFractionDigits:d});
const duration = v => v == null ? "–" : `${Math.floor(Math.max(0,v)/60)}:${String(Math.floor(Math.max(0,v)%60)).padStart(2,"0")} min`;
const phases = {aus:"Aus",aufheizen:"Aufheizen",bereit:"Bereit",saunagang:"Saunagang",nachlauf:"Nachlauf",zwangskühlung:"Zwangskühlung"};
const events = {door_open:"Tür geöffnet",door_close:"Tür geschlossen",person_strong:"Person erkannt",person_weak:"Person erkannt (schwach)",infusion:"Aufguss",ventilation_confirmed:"Durchlüften bestätigt",operation_off:"Betrieb ausgeschaltet",confirmation_expired:"Vorläufigen Gang aufgehoben"};
const signalText = {door_heating:"Temperaturabfall trotz Heizen",door_close:"Türschließung",door_open:"Türöffnung",door:"Tür",infusion:"Aufguss",strong:"Deutliches Personensignal",weak:"Schwaches Personensignal"};
const errorText = error => {
  // hass.callApi legt die Antwort der Integration in body ab; error enthält
  // lediglich den allgemeinen HTTP-Fehler (etwa „Response error: 409“).
  const detail=error?.body?.error||error?.body?.message;
  if(typeof detail==="string"&&detail.trim())return detail;
  if(error?.status_code)return ({401:"Die Anmeldung ist abgelaufen. Bitte Home Assistant neu laden.",403:"Für diese Änderung sind Administratorrechte erforderlich.",409:"Die Aktion ist im aktuellen Zustand nicht möglich. Bitte die Hinweise zur Einrichtung prüfen.",503:"Die Sauna-Integration wird gerade neu geladen. Bitte kurz warten."})[error.status_code]||`Die Anfrage konnte nicht verarbeitet werden (HTTP ${error.status_code}).`;
  if(error?.error==="Request error")return "Home Assistant ist zurzeit nicht erreichbar. Bitte die Verbindung prüfen.";
  return error?.message||error?.error||String(error);
};

// Split before reducing points: a pixel-sized reduction must never hide a
// missing measurement interval.  The cubic controls below are monotone, so
// the display is calm without inventing peaks between measurements.
const historySegments = (values, start, end, ttl) => {
  const segments=[]; let segment=[];
  for(const point of values){
    const time=stamp(point.received_at);
    if(point.value==null || time<start || time>end){if(segment.length)segments.push(segment);segment=[];continue;}
    if(segment.length && ttl && time-stamp(segment.at(-1).received_at)>ttl){segments.push(segment);segment=[];}
    segment.push(point);
  }
  if(segment.length)segments.push(segment);
  return segments;
};
const reduceHistorySegment = (segment, x) => {
  const output=[]; let bucket=[], key=null;
  const flush=()=>{if(!bucket.length)return;const keep=new Set([bucket[0],bucket.at(-1),bucket.reduce((a,b)=>a.value<b.value?a:b),bucket.reduce((a,b)=>a.value>b.value?a:b)]);output.push(...bucket.filter(point=>keep.has(point)));bucket=[];};
  for(const point of segment){const next=Math.floor(x(stamp(point.received_at)));if(key!==null&&next!==key)flush();key=next;bucket.push(point);} flush();
  return output;
};
const monotoneHistoryPath = (points, x, y) => {
  if(!points.length)return "";
  const xy=points.map(point=>[x(stamp(point.received_at)),y(point.value)]);
  if(xy.length===1)return `M${xy[0][0].toFixed(2)},${xy[0][1].toFixed(2)}`;
  const slopes=xy.slice(1).map(([nextX,nextY],i)=>(nextY-xy[i][1])/(nextX-xy[i][0]||1));
  const tangents=xy.map((_,i)=>{
    if(i===0)return slopes[0]; if(i===xy.length-1)return slopes.at(-1);
    const previous=slopes[i-1],next=slopes[i]; if(previous*next<=0)return 0;
    const before=xy[i][0]-xy[i-1][0],after=xy[i+1][0]-xy[i][0],w1=2*after+before,w2=after+2*before;
    return (w1+w2)/(w1/previous+w2/next);
  });
  let path=`M${xy[0][0].toFixed(2)},${xy[0][1].toFixed(2)}`;
  for(let i=0;i<xy.length-1;i++){
    const [x0,y0]=xy[i],[x1,y1]=xy[i+1],dx=(x1-x0)/3;
    path+=` C${(x0+dx).toFixed(2)},${(y0+tangents[i]*dx).toFixed(2)} ${(x1-dx).toFixed(2)},${(y1-tangents[i+1]*dx).toFixed(2)} ${x1.toFixed(2)},${y1.toFixed(2)}`;
  }
  return path;
};

class SaunaPanel extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode:"open"}); this.view="overview"; this.selected="live";
    this.cache=new Map(); this.zoom=1; this.pan=100; this.generation=0; this.positions=new Set(["upper"]); this.historyDetail=false; this.pointers=new Map();
  }
  set hass(value) { this._hass=value; if(this.isConnected && !this.timer) this.start(); }
  get hass() { return this._hass; }
  connectedCallback() { this.shell(); if(this._hass) this.start(); }
  disconnectedCallback() { clearInterval(this.timer); this.timer=null; this.generation++; }
  start() { this.refresh(); this.timer=setInterval(()=>this.refresh(),2000); }
  $(selector) { return this.shadowRoot.querySelector(selector); }
  async api(path, method="GET", body) { return this.hass.callApi(method, `ha_sauna${path}`,body); }
  shell() {
    this.shadowRoot.innerHTML=`<style>
      :host{display:block;height:100%;overflow:auto;color:var(--primary-text-color,#203331);background:var(--primary-background-color,#f4f7f6);font:15px/1.5 system-ui,sans-serif;--accent:#197367;--warm:#cd693a}
      *{box-sizing:border-box}main{max-width:1280px;margin:auto;padding:28px 32px 60px}header{display:flex;align-items:center;gap:18px;margin-bottom:24px}h1{font-size:28px;letter-spacing:-.6px;margin:0}h2{font-size:19px;margin:0 0 12px}h3{font-size:14px;margin:12px 0 8px}p{margin:8px 0}small,.muted{color:var(--secondary-text-color,#657572);font-size:13px}.grow{flex:1}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
      button,select,input{font:inherit;border:1px solid var(--divider-color,#d4dfdc);border-radius:8px;padding:9px 13px;background:var(--card-background-color,#fff);color:inherit}button{cursor:pointer}button:hover{border-color:var(--accent)}button:disabled{opacity:.5;cursor:default}button.primary{background:var(--accent);color:white;border-color:var(--accent)}button.stop{background:#a34029;color:white}button[aria-selected=true]{background:var(--accent);color:white}a{color:var(--accent)}.tabs{display:flex;gap:8px;margin:24px 0 18px}.card{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#dce5e2);border-radius:14px;padding:22px;margin:16px 0}.hero{border-left:4px solid var(--accent)}.phase{font-size:28px;font-weight:650;letter-spacing:-.5px}.badge{display:inline-block;border-radius:20px;padding:3px 11px;background:#e4f2ed;color:#24584c;font-size:13px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px;margin-top:18px}.metric strong{display:block;font-size:24px;font-weight:600;white-space:nowrap}.metric small{display:block}.notice{background:#fff1db;color:#6f481d;border-left:4px solid #d79a42;padding:12px 16px;border-radius:8px;margin:10px 0}.error{background:#fcebe6;color:#862d21}.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}.upper{background:#197367}.lower{background:#bd7133}.chart{width:100%;height:auto;display:block;touch-action:pan-y}.chart text{fill:var(--secondary-text-color,#6d7874);font:12px system-ui}.chart .gridline{stroke:var(--divider-color,#e2e9e6);stroke-width:1}.chart .gang{fill:#5eaa9633;stroke:#5eaa96}.chart .provisional{fill:#dfa94b22;stroke:#bb8b35;stroke-dasharray:5 4}.chart .heat{fill:#d8764433}.chart .event{stroke:#83978e;stroke-dasharray:3 5}.chart .infusion{stroke:#426bbb}.chart path{fill:none;stroke-width:2}.chart path.upper{stroke:#197367}.chart path.lower{stroke:#bd7133}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:10px 12px;border-bottom:1px solid var(--divider-color,#e4ebe8);white-space:nowrap}th{font-weight:600}.toolbar{margin:18px 0 8px}input[type=range]{padding:0;max-width:180px}.forms{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}label.field{display:flex;flex-direction:column;gap:6px;font-size:13px}label.field input{width:100%}fieldset{border:0;padding:0;margin:0}details{margin:18px 0}summary{cursor:pointer;font-weight:600;margin-bottom:14px}[hidden]{display:none!important}#message:empty{display:none}#settings .row{margin:18px 0}.empty{padding:45px;text-align:center;color:var(--secondary-text-color,#657572)}
      @media(max-width:800px){main{padding:18px 16px 40px}.grid{grid-template-columns:repeat(2,1fr)}.forms{grid-template-columns:repeat(2,1fr)}header{gap:10px}h1{font-size:23px}.card{padding:16px}.metric strong{font-size:21px}header select{max-width:180px}}
      @media(max-width:450px){.forms{grid-template-columns:1fr}.phase{font-size:24px}.grid{gap:12px}.legend{gap:12px}}
      :host{--accent:#e58a55}main{max-width:none}header{margin-bottom:12px}.tabs{margin-top:8px}.plot-panel{padding:20px 12px 30px;background:#111;color:#c8c8c8;border-radius:8px}.plot-title{text-align:center;color:#aaa;font-weight:500;font-size:18px}.legend{justify-content:center;margin:18px 0}.legend i{display:inline-block;width:32px;height:5px;margin-right:6px;vertical-align:middle}.top-legend{margin:24px 0 0}.position-select{justify-content:center;font-size:12px;gap:8px}.position-select button{background:transparent;color:#aaa;border:0;padding:6px}.position-select button[aria-pressed=false]{opacity:.35}.plot-wrap{position:relative}.chart{height:520px;width:100%;touch-action:pan-y}.chart text{fill:#aaa}.detector-chart{height:220px}.main-tabs{border-bottom:1px solid var(--divider-color,#333);padding-bottom:10px}.normal-tabs,.detail-tabs{font-size:13px}.chart .gridline{stroke:rgba(255,255,255,.10)}.chart .gang{fill:rgba(165,30,85,.46);stroke:none}.chart .provisional{stroke:#a51e55;stroke-dasharray:5 4}.chart .heat{fill:rgba(255,150,35,.28)}.chart .ready{fill:rgba(38,125,82,.3)}.chart .vent{fill:rgba(58,125,155,.3)}.chart .cool{fill:rgba(103,130,154,.3)}.chart .after{fill:rgba(58,125,155,.18)}.chart .door{fill:rgba(255,205,80,.75)}.chart .infusion{stroke:#f5f5f5;stroke-width:1}.chart path{stroke-width:3.5}.chart path.temperature{stroke:#ff6b4a}.chart path.humidity{stroke:#42a5ff}.chart path.lower{stroke-dasharray:8 5;opacity:.75}.chart .axis-temperature{fill:#ff6b4a}.chart .axis-humidity{fill:#42a5ff}.plot-note{text-align:center;color:#999}#tooltip{position:absolute;pointer-events:none;background:#f6f6f6;color:#333;padding:10px 13px;font:13px/1.5 system-ui;border:1px solid #999;z-index:2;box-shadow:0 3px 10px #0003}.dashboard{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px;max-width:1150px;margin:0 auto}.tiles{display:grid;grid-template-columns:1fr 1fr;gap:12px}.tile{padding:18px;text-align:left;min-height:72px}.tile.full{grid-column:1/-1}.greeting{text-align:center;font-size:24px;margin:25px 0}.dial{max-width:330px;display:block;margin:auto}.dial text{fill:var(--primary-text-color,#ddd)}.dial .reading{font-size:33px}.dial .caption{font-size:13px}.dashboard .card{margin-top:0}.state-line{display:flex;justify-content:space-between;gap:15px}.gauge-card h2{text-align:center}.temperature-choice{display:flex;gap:8px;align-items:center;justify-content:center;margin-top:10px}.temperature-choice input{width:95px}
      @media(max-width:800px){.dashboard{grid-template-columns:1fr}.chart{height:420px}.detector-chart{height:180px}.plot-panel{padding:12px 0}.legend{font-size:12px;gap:12px}.legend i{width:22px}main{padding:14px}}

      .notice button{background:#6f481d;color:#fff;border-color:#6f481d}.card,.dashboard>*,.detail-grid>*,.forms>*{min-width:0}.notice,p,.field{overflow-wrap:anywhere}.dashboard{max-width:1280px;align-items:start;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.greeting{font-size:21px;margin:16px 0}.gauges{display:grid;grid-template-columns:1fr 1fr;gap:12px;text-align:center}.dial{max-width:240px}.gauge-card h2{font-size:16px}.temperature-choice{flex-wrap:wrap}.temperature-choice input{min-width:0;width:80px}.tile{padding:12px;min-height:54px}.tiles{gap:10px}.timer-strip{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:18px 0;padding:14px 0;border-block:1px solid var(--divider-color,#333)}.timer-strip small,.timer-strip strong{display:block}.timer-strip strong{font-size:21px;font-variant-numeric:tabular-nums}.timer-strip small{font-size:12px}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.detail-grid .card{margin:0}.center{text-align:center}dl{margin:0}dt{font-size:13px;color:var(--secondary-text-color,#aaa);margin-top:12px}dd{margin:2px 0 8px}.forms{grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.settings-group{margin:12px 0;border:1px solid var(--divider-color,#333);border-radius:10px;padding:0 16px}.settings-group summary{padding:14px 0;margin:0;font-size:16px}.settings-group[open] summary{border-bottom:1px solid var(--divider-color,#333);margin-bottom:16px}.settings-group .forms{padding-bottom:18px}.expert-group+ .expert-group{border-top:1px solid var(--divider-color,#333);margin-top:20px;padding-top:12px}.field small{font-size:12px}.tabs{flex-wrap:wrap}.state-line .badge{align-self:start;white-space:nowrap}.feedback{font-weight:650}.feedback.on{color:#d86d35}.feedback.off{color:#71817d}.feedback.unknown{color:#a98536}.light-controls{margin-top:14px}.program-buttons{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr;gap:8px}.dial-temperature{position:relative}.dial-temperature foreignObject input{width:190px;accent-color:var(--accent)}.dial-temperature foreignObject{overflow:visible}.manual-controls{border-left:4px solid #9a6b2f}.manual-controls h2{margin-bottom:5px}.manual-section+ .manual-section{border-top:1px solid var(--divider-color,#333);margin-top:16px;padding-top:16px}.manual-status{display:inline-block;border-radius:20px;padding:3px 10px;font-size:13px;font-weight:650;background:#e6ece9;color:#53635d}.manual-status.on{background:#f9dfd4;color:#963f24}.manual-status.off{background:#e1eeea;color:#216551}.manual-light-value{width:92px;text-align:right;font-variant-numeric:tabular-nums}.manual-controls .row{margin-top:10px}.manual-controls .muted{display:block;margin-top:10px}.diagnostic-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.diagnostic-grid .plot-panel{margin:0;padding:14px 10px}.diagnostic-grid h3{margin:0 0 4px}.event-strip{--event-marker-size:17px;--event-marker-gap:3px;position:relative;height:var(--event-strip-height,64px);margin:10px 50px 0;border-bottom:1px solid #667}.event-marker{position:absolute;bottom:var(--event-marker-bottom,-6px);width:var(--event-marker-size);height:var(--event-marker-size);padding:0;border-radius:50%;border:2px solid #f5f5f5;background:#e58a55;color:#111;font-size:10px;font-weight:700;line-height:13px;transform:translateX(-50%)}.event-marker[data-selected=true],.event-row[data-selected=true]{outline:3px solid var(--accent);outline-offset:2px;background:#fff1db}.event-row button{padding:3px 7px;white-space:nowrap}
      .temperature-presets{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px}.temperature-presets .tile{flex:1 1 64px;min-height:40px;padding:8px 10px;text-align:center}.program-buttons button{white-space:nowrap;font-size:13px;padding:8px 10px}
      .diagnostic-grid .detector-chart{height:auto}
      @media(max-width:1000px){.dashboard{grid-template-columns:1fr}.dashboard .gauge-card{max-width:none}.dial{max-width:210px}}
      @media(max-width:600px){.detail-grid,.forms,.diagnostic-grid{grid-template-columns:1fr}.gauges{gap:4px}.gauges h2{font-size:14px}.card{padding:14px}.timer-strip strong{font-size:18px}.temperature-choice{gap:6px}header select{max-width:110px}}
    </style><main>
      <header><button data-action="menu" aria-label="Menü öffnen">☰</button><div><h1>Sauna</h1><small>Steuerung und Verlauf</small></div><span class="grow"></span><select id="instance" aria-label="Sauna auswählen"></select></header>
      <nav class="tabs main-tabs" aria-label="Ansicht"><button data-action="normal" aria-selected="true">Übersicht</button><button data-action="details" aria-selected="false">Details</button></nav>
      <nav class="tabs normal-tabs" aria-label="Übersicht"><button data-action="overview" aria-selected="true">Steuerung</button><button data-action="history" aria-selected="false">Verlauf und Archiv</button></nav>
      <nav class="tabs detail-tabs" aria-label="Detailansicht" hidden><button data-action="detail" aria-selected="true">Betrieb & Fristen</button><button data-action="diagnostics" aria-selected="false">Erkennungskontrolle</button><button data-action="settings" aria-selected="false">Einstellungen & Export</button></nav>
      <div id="message" role="alert"></div><section id="current" aria-live="polite"><p>Lade Saunadaten …</p></section><section id="details" hidden></section>
      <section id="history" hidden><div class="row"><h2 class="grow">Sitzungsverlauf</h2><select id="session" aria-label="Saunasitzung auswählen"><option value="live">Aktuelle oder letzte Saunasitzung</option></select></div>
        <div class="row toolbar"><button data-action="zoom-in" aria-label="Vergrößern">＋</button><button data-action="zoom-out" aria-label="Verkleinern">−</button><button data-action="reset-zoom">Gesamte Saunasitzung</button><label>Zeitausschnitt <input id="pan" type="range" min="0" max="100" value="100" aria-label="Zeitausschnitt verschieben"></label><span id="range" class="muted"></span></div><div id="plots"></div><div id="detection-plots" hidden></div><div id="gangs"></div><div id="event-list"></div>
      </section><section id="settings" hidden></section>
    </main>`;
    this.shadowRoot.addEventListener("click",e=>{const b=e.target.closest("[data-action]"); if(b)this.action(b.dataset.action).catch(err=>this.message(err));});
    this.shadowRoot.addEventListener("change",e=>{
      if(e.target.id==="instance"){this.entry=e.target.value;this.highlightedEventId=null;this.generation++;this.selected="live";this.cache.clear();this.settingsEntry=null;this.draft=null;this.zoom=1;this.refresh();}
      if(e.target.id==="session"){this.selected=e.target.value;this.highlightedEventId=null;this.zoom=1;this.pan=100;this.refresh();}
      if(e.target.id==="target")this.action("target").catch(err=>this.message(err));
    });
    this.shadowRoot.addEventListener("input",e=>{if(e.target.closest("#parameters"))e.target.dataset.edited="true";if(e.target.closest("#current"))this.draft={...this.draft,[e.target.id]:e.target.value};if(e.target.id==="manual-light-value")this.manualLightDraft=e.target.value;if(e.target.id==="pan"){this.pan=Number(e.target.value);this.drawHistory();}});
    this.shadowRoot.addEventListener("pointerdown",e=>{const svg=e.target.closest("svg.session-chart");if(svg&&e.pointerType!=="mouse"){this.pointers.set(e.pointerId,{x:e.clientX,y:e.clientY,svg});this.pinchDistance=null;}});
    this.shadowRoot.addEventListener("pointerup",e=>{this.pointers.delete(e.pointerId);this.pinchDistance=null;});
    this.shadowRoot.addEventListener("pointercancel",e=>{this.pointers.delete(e.pointerId);this.pinchDistance=null;});
    this.shadowRoot.addEventListener("pointermove",e=>{const svg=e.target.closest("svg.session-chart");if(!svg)return;if(this.pointers.has(e.pointerId))this.pointers.set(e.pointerId,{x:e.clientX,y:e.clientY,svg});if(this.pointers.size===2){e.preventDefault();this.pinchZoom();return;}this.hoverChart(e);});
    this.shadowRoot.addEventListener("pointerout",e=>{if(e.target.closest("svg.session-chart")&&!e.relatedTarget?.closest?.("svg.session-chart")){this.$("#tooltip")?.setAttribute("hidden","");this.$("#cursor")?.setAttribute("visibility","hidden");}});
    this.shadowRoot.addEventListener("wheel",e=>{const svg=e.target.closest("svg.session-chart");if(!svg||!(e.ctrlKey||e.metaKey))return;e.preventDefault();this.zoomAt(Math.exp(-e.deltaY*.002),e.clientX,svg);},{passive:false});
    this.shadowRoot.addEventListener("submit",e=>{e.preventDefault();this.saveSettings().catch(err=>this.message(err));});
  }
  message(error,source="action") {
    this.messages??={};this.messages[source]=error;
    const shown=this.messages.action||this.messages.refresh;
    const node=this.$("#message");node.className=shown?"notice error":"";node.textContent=shown?errorText(shown):"";
  }
  async refresh() {
    if(this.busy || !this.isConnected)return;
    this.busy=true; const generation=this.generation;
    try {
      if(!this.entry){
        const instances=await this.api("");
        this.$("#instance").innerHTML=instances.map(i=>`<option value="${esc(i.entry_id)}">${esc(i.title)}</option>`).join("");
        if(!instances.length){this.$("#current").innerHTML='<p>Keine geladene Sauna vorhanden.</p>';return;}
        this.entry=instances[0].entry_id;
      }
      const entry=this.entry;
      const [state,list]=await Promise.all([this.api(`/${entry}/state`),this.api(`/${entry}/archive`)]);
      if(generation!==this.generation || !this.isConnected)return;
      this.state=state; this.sessions=list;
      const optionHtml='<option value="live">Aktuelle oder letzte Saunasitzung</option>'+list.filter(s=>s.session_id!==state.session?.timeline.session_id).map(s=>`<option value="${esc(s.session_id)}">${esc(when(s.started_at))}${s.ended_at?" · beendet":" · unterbrochen"}</option>`).join("");
      if(this.$("#session").innerHTML!==optionHtml){this.$("#session").innerHTML=optionHtml;this.$("#session").value=this.selected;}
      const id=this.selected==="live"?(state.session?.timeline.session_id||list[0]?.session_id):this.selected;
      if(id){
        const cache=this.cache.get(id)||{records:[],after:0};
        if(this.selected==="live"||!cache.loaded){
          let page;
          do {
            page=await this.api(`/${entry}/archive?session_id=${encodeURIComponent(id)}&after=${cache.after}`);
            if(generation!==this.generation || !this.isConnected)return;
            cache.session=page.session;
            cache.records.push(...page.records.filter(r=>["measurement","source_snapshot","diagnostic","phase","detector_trace"].includes(r.kind)));
            cache.after=page.records.at(-1)?.id||cache.after;
          }while(page.next_after);
          cache.loaded=true;this.cache.set(id,cache);
        }
        this.shown={session:this.selected==="live"?(state.session||cache.session):cache.session,records:cache.records};
      }else this.shown=null;
      if(!(this.shadowRoot.activeElement?.tagName==="INPUT"&&this.shadowRoot.activeElement.closest("#current")))this.drawCurrent();
      if(!this.$("#tooltip")||this.$("#tooltip").hidden)this.drawHistory();this.drawSettings();this.message(null,"refresh");
    }catch(error){this.message(error,"refresh");}finally{this.busy=false;}
  }
  drawCurrent() {
    const progressionOpen=this.$("#current details")?.open;
    const s=this.state, session=s.session, p=s.configuration.parameters, now=stamp(s.now), active=session?.timeline.active;
    const remaining=end=>duration(end?(stamp(end)-now)/1000:null);
    const latest=session||s.last_session;
    const doorText=session?({open:"Tür offen",closed:"Tür geschlossen"}[session.timeline.door]||"Türstatus noch nicht ermittelt"):"Türerkennung ruht";
    const count=latest?.timeline.completed.filter(g=>g.infusion_events.length).length??s.gang_count;
    const value=(position,quantity)=>s.measurements.find(m=>m.position===position&&m.quantity===quantity)?.value;
    const quality=(position,quantity)=>s.measurement_status[`${position}_${quantity}`]?.state;
    const qualityText=(position,quantity)=>({current:"Aktueller Messwert",stale:"Letzter Wert · veraltet",unavailable:"Messwert fehlt",validity_unconfigured:"Gültigkeit noch nicht eingestellt"}[quality(position,quantity)]||"Messwert fehlt");
    const formatValue=(position,quantity,unit)=>`${num(value(position,quantity),1)} ${unit}`;
    const timer=s.mechanical_timer;
    const timerPause={operation_off:"Angehalten · Saunabetrieb aus",contactor_off:"Angehalten · Schütz aus",contactor_unavailable:"Angehalten · Schützstellung unbekannt"};
    const timerStatus=timer.state==="paused"?(timerPause[timer.pause_reason]||"Angehalten"):{idle:"Noch nicht gestartet",running:"Geschätzte Restzeit bei eingeschaltetem Schütz",expired:"Drehschalter neu einstellen"}[timer.state];
    const timerText=timer.state==="idle"?"–":duration(timer.remaining_seconds);
    const activity=active?(s.gang_confirmation==="confirmed"?"Durch Aufguss bestätigt":"Vorläufig · Aufguss ausstehend"):session?.after_run||session?.cooling?.started_at?s.manual_controls?.heater?.manual===true?"Manuelle Ofensteuerung aktiv · Ofen heizt.":s.manual_controls?.heater?.manual===false?"Manuelle Ofensteuerung aktiv · Ofen aus.":s.heating_feedback===true?"Ofen heizt.":"Der Ofen bleibt ausgeschaltet.":s.cooling_wait_until?"Türöffnung erkannt · mögliche Personenerkennung abwarten.":session?`${s.operation_enabled?"Sitzung seit":"Sitzung unterbrochen · seit"} ${when(session.timeline.session_started_at)}`:latest?`Letzte Sitzung beendet · ${when(latest.ended_at)}`:"Keine laufende Saunasitzung";
    const energyLabel={measured:"Gemessen",estimated:"Geschätzt",mixed:"Teilweise geschätzt",incomplete:"Unvollständig"};
    const energy=latest?.energy;
    const energyText=`${num(session?s.energy_kwh:energy?energy.measured_kwh+energy.estimated_kwh:0,3)} kWh · ${energyLabel[session?s.energy_source:energy?.unknown_seconds?"incomplete":energy?.measured_seconds?(energy.estimated_seconds?"mixed":"measured"):"estimated"]}`;
    const heatCaption=s.heating_feedback===true?(s.heating_observation?.estimated?"Heizschütz ein":"Ofen heizt"):s.heating_feedback===false?"Ofen aus":"Heizzustand unbekannt";
    const heatSource={power:"Aus gemessener Leistung",independent_feedback:"Unabhängige Heizrückmeldung",contactor:"Nach Schützstellung geschätzt",unknown:"Rückmeldung fehlt"}[s.heating_observation?.source]||"Rückmeldung fehlt";
    const permissions=s.permissions||{};
    const disabled=!permissions.admin;
    const canStart=permissions.control&&(s.operation_enabled||!s.start_errors.length);
    const presets=Array.from({length:p.preset_count},(_,i)=>p.preset_start_c+i*p.preset_step_c).filter(value=>value<=100);
    const end=session?.deadlines.find(d=>d.purpose==="session_gap")?.due_at;
    const availability=s.start_availability;
    const availabilityText=s.operation_enabled&&availability?(availability.gang_elapsed_seconds!=null?`Saunagang seit ${duration(availability.gang_elapsed_seconds)}`:availability.minimum_wait_seconds>0?`Ein weiterer Saunagang ist frühestens in ${duration(availability.minimum_wait_seconds)} möglich. Die Temperaturbereitschaft wird danach erneut geprüft.`:availability.until_ready_seconds>0&&availability.ready_estimated?`Bereit in etwa ${duration(availability.until_ready_seconds)}.`:availability.until_ready_seconds===0?`Start jetzt möglich.${availability.start_window_seconds>0?` Ein weiterer Gang kann noch ${availability.start_window_label||"mindestens"} ${duration(availability.start_window_seconds)} lang begonnen werden.`:""}`:(availability.message||"Startzeit noch nicht abschätzbar.")):"";
    const notices=s.issues.map(i=>`<p>${esc(i.message)}${i.action==="settings"?'<br><button data-action="configure">Einstellungen öffnen</button>':""}</p>`).join("");
    const alert=notices?`<div class="notice" role="alert">${notices}</div>`:"";
    const operation=`<button class="tile full ${s.operation_enabled?"stop":"primary"}" data-action="operation" ${canStart?"":"disabled"}>${s.operation_enabled?"Ausschalten":"Einschalten"}</button>`;
    const stateLine=`<div class="state-line"><strong class="phase" data-phase="${esc(s.phase)}">${phases[s.phase]||"Unbekannt"}</strong><span class="badge">${count} ${count===1?"Saunagang":"Saunagänge"}</span></div><p id="phase-detail" class="muted">${esc(activity)}</p>`;
    const dial=(reading,unit,caption,color,maximum,valid,control="")=>`<svg class="dial ${control?"dial-temperature":""}" viewBox="0 0 300 235" role="${control?"group":"img"}" aria-label="${esc(control?`${caption} und Solltemperatur` : caption)}"><path d="M 54 195 A 120 120 0 1 1 246 195" fill="none" stroke="var(--divider-color,#444)" stroke-width="16" stroke-linecap="round"/><path d="M 54 195 A 120 120 0 1 1 246 195" fill="none" stroke="${valid?color:'#888'}" stroke-width="16" stroke-linecap="round" pathLength="100" stroke-dasharray="${Math.max(0,Math.min(100,(reading??0)/maximum*100))} 100"/><text class="reading" x="150" y="118" text-anchor="middle">${num(reading,1)} ${unit}</text><text class="caption" x="150" y="150" text-anchor="middle">${esc(caption)}</text>${control}</svg>`;
    const timers=`<div class="timer-strip"><div><small>Gezählte Heizzeit</small><strong>${duration(session?.heating.elapsed_seconds||0)}</strong><small>Grenze ${duration(s.heating_limit_seconds)}</small></div><div data-mechanical-timer="${timer.state}"><small>Mechanischer Ofentimer</small><strong>${timerText}</strong><small>${timerStatus}</small></div></div>`;
    const manualPhase=session?.after_run?{purpose:"after_run",token:session.after_run.phase_id,label:"Nachlauf jetzt beenden"}:session?.cooling?.started_at?{purpose:"forced_cooling",token:session.cooling.cycle_id,label:"Zwangskühlung jetzt beenden"}:null;
    const finishPhase=manualPhase?`<div class="row"><button data-action="end-phase:${manualPhase.purpose}:${esc(encodeURIComponent(manualPhase.token))}" ${disabled?"disabled":""}>${manualPhase.label}</button><small class="muted">Setzt den regulären Folgeablauf fort.</small></div>`:"";
    const phaseTimer=s.phase_timer?`<div class="timer-strip" data-phase-timer="${esc(s.phase_timer.kind)}"><div><small>${esc(s.phase_timer.label)}</small><strong>${duration(s.phase_timer.seconds)}</strong></div></div>`:"";
    const temperatureColor={aufheizen:"#e58a55",bereit:"#4f9a6a",saunagang:"#cf7447",nachlauf:"#5b8ca8",zwangskühlung:"#5b8ca8"}[s.phase]||"#888";
    const humidity=value("upper","humidity"), humidityColor=humidity<=20?"#4f9a6a":humidity<=30?"#c59b32":"#bc5546";
    const targetControl=`<foreignObject x="52" y="172" width="196" height="45"><input xmlns="http://www.w3.org/1999/xhtml" id="target" aria-label="Solltemperatur einstellen" type="range" min="1" max="100" step="0.5" value="${s.target_temperature??1}" ${permissions.temperature?"":"disabled"}/></foreignObject>`;
    const profiles=["program_1","program_2"];
    const light=s.manual_controls?.light||{}, automatic=light.automatic==null?"–":`${num(light.automatic,0)} %`;
    const lightControls=`<div class="light-controls"><small>Licht</small><div class="row"><button data-action="light:false" ${permissions.light?"":"disabled"}>Aus</button><button data-action="light:normal" ${permissions.light?"":"disabled"}>Normallicht</button><button data-action="light:true" ${permissions.light?"":"disabled"}>Raumlicht · ${num(p.session_light_brightness_percent,0)} %</button><button data-action="light:auto" ${permissions.light?"":"disabled"}>Auto · ${automatic}</button></div></div>`;
    const heater=s.manual_controls?.heater||{};
    const heaterMode=heater.manual===true?"on":heater.manual===false?"off":"auto";
    const heaterStatus=heater.manual===true?"Manuell EIN":heater.manual===false?"Manuell AUS":heater.automatic===true?"Automatik · Heizfreigabe EIN":heater.automatic===false?"Automatik · Heizfreigabe AUS":"Automatik · Entscheidung ausstehend";
    const lightMode=light.manual==null?"auto":light.manual<=0?"off":"on";
    const lightStatus=light.manual==null?`Automatik · letzter Wert ${automatic}`:`Manuell · ${num(light.manual,0)} %`;
    const manualLightValue=this.manualLightDraft??(light.manual??light.automatic??0);
    const canManualHeater=permissions.admin&&permissions.heater;
    const canManualLight=permissions.admin&&permissions.light;
    const coolingPaused=session?.cooling?.paused_at?`<p class="muted" data-cooling-status="paused">Kühlrest pausiert · ${duration(Math.max(0,(session.cooling.duration_seconds||0)-(session.cooling.credited_seconds||0)-(session.cooling.elapsed_seconds||0)))}</p>`:"";
    const manualControls=permissions.admin?`<div class="card manual-controls"><h2>Manuelle Steuerung</h2><p class="muted">Nur für Home-Assistant-Administratoren. Schutzabschaltungen bleiben wirksam.</p><section class="manual-section"><div class="row"><strong>Ofen</strong><span class="manual-status ${heaterMode}" data-heater-status="${heaterMode}">${heaterStatus}</span></div><div class="row"><button data-action="heater:true" class="primary" ${canManualHeater&&s.operation_enabled?"":"disabled"}>EIN</button><button data-action="heater:false" ${canManualHeater?"":"disabled"}>AUS</button><button data-action="heater:auto" ${canManualHeater?"":"disabled"}>Automatik</button></div>${!s.operation_enabled?'<small class="muted">Zum manuellen Einschalten des Ofens zuerst den Saunabetrieb einschalten.</small>':""}${coolingPaused}</section><section class="manual-section"><div class="row"><strong>Licht</strong><span class="manual-status ${lightMode}" data-light-status="${lightMode}">${lightStatus}</span></div><div class="row"><label for="manual-light-value">Helligkeit</label><input id="manual-light-value" class="manual-light-value" type="number" min="0" max="100" step="1" inputmode="numeric" value="${esc(manualLightValue)}" ${canManualLight?"":"disabled"}><span>%</span><button data-action="manual-light" ${canManualLight?"":"disabled"}>Übernehmen</button></div><div class="row"><button data-action="light:false" ${canManualLight?"":"disabled"}>AUS</button><button data-action="light:normal" ${canManualLight?"":"disabled"}>Normallicht</button><button data-action="light:true" ${canManualLight?"":"disabled"}>Raumlicht · ${num(p.session_light_brightness_percent,0)} %</button><button data-action="light:auto" ${canManualLight?"":"disabled"}>Automatik · ${automatic}</button></div><small class="muted">Die manuelle Helligkeit bleibt bis zum nächsten Phasenwechsel aktiv.</small></section></div>`:"";
    this.$(".main-tabs [data-action=\"details\"]").hidden=!permissions.admin;
    const overviewLightTimer=!session&&s.phase_timer?.kind==="session_light"?`<p class="muted">Lichtnachlauf noch ${duration(s.phase_timer.seconds)}</p>`:"";
    this.$("#current").innerHTML=`<h2 class="greeting">Servus ${esc(this.hass.user?.name||"")}</h2><div class="dashboard"><div class="card">${stateLine}<div class="row muted"><span class="feedback ${s.heating_feedback===true?"on":s.heating_feedback===false?"off":"unknown"}">${heatCaption}</span><span data-door-status>${doorText}</span></div>${availabilityText?`<p class="muted">${esc(availabilityText)}</p>`:""}${overviewLightTimer}<div class="tiles">${operation}<div class="program-buttons">${profiles.map(id=>`<button data-action="profile:${id}" ${permissions.program?"":"disabled"}>${num(p[id+"_gangs"],0)}-Gang-Programm</button>`).join("")}</div><div class="temperature-presets">${presets.map(v=>`<button class="tile" data-action="preset:${v}" ${permissions.temperature?"":"disabled"}>${num(v,1)} °C</button>`).join("")}</div></div>${lightControls}${alert}</div><div class="card gauge-card"><div class="gauges"><div><h2>Temperatur</h2>${dial(value("upper","temperature"),"°C",heatCaption,temperatureColor,120,quality("upper","temperature")==="current",targetControl)}<p class="muted">${qualityText("upper","temperature")}</p></div><div><h2>Luftfeuchte</h2>${dial(humidity,"%","Relative Luftfeuchte",humidityColor,100,quality("upper","humidity")==="current")}<p class="muted">${qualityText("upper","humidity")}</p></div></div><p class="muted center">Solltemperatur: ${num(s.target_temperature)} °C</p><details><summary>Temperatur steigern</summary><p class="muted">Die Verteilungszahl verteilt Start und Ende der Steigerung. Sie begrenzt keine Saunagänge.</p><div class="row"><label class="field">Start (°C)<input id="progression-start" type="number" step="0.5" max="100" value="${p.target_temperature_c??s.target_temperature??''}" ${permissions.program?'':'disabled'}></label><label class="field">Ende (°C)<input id="progression-end" type="number" step="0.5" max="100" value="${p.final_temperature_c??''}" ${permissions.temperature?'':'disabled'}></label><label class="field">Verteilung<input id="progression-gangs" type="number" step="1" value="${p.temperature_gangs??''}" ${permissions.temperature?'':'disabled'}></label><button data-action="progression" ${(permissions.program||permissions.temperature)?'':'disabled'}>Übernehmen</button><button data-action="program-free" ${permissions.program?'':'disabled'}>Neue Steigerung</button></div></details></div></div>`;
    const deadlines={confirmation:"Aufgussbestätigung",person_opportunity:"Wartezeit auf Personenerkennung",after_run:"Nachlauf",forced_cooling:"Zwangskühlung",session_gap:"Ende der Saunasitzung"};
    const temperatureProgram=s.configuration.program_mode==="progressive"&&p.final_temperature_c!=null?`${num(p.target_temperature_c)} → ${num(p.final_temperature_c)} °C`:"Konstant";
    this.$("#details").innerHTML=`<div class="card hero">${stateLine}${phaseTimer}${finishPhase}${operation}${timers}${alert}</div>${manualControls}<div class="detail-grid"><div class="card"><h2>Temperatur und Heizregelung</h2><dl><dt>Aktuelle Solltemperatur</dt><dd>${num(s.target_temperature,1)} °C</dd><dt>Temperaturprogramm</dt><dd>${temperatureProgram}</dd><dt>Bereitschaftstemperatur</dt><dd data-readiness>${num(s.readiness_target,1)} °C</dd><dt>Wieder einschalten unter</dt><dd>${num(s.readiness_target==null?null:s.readiness_target-p.readiness_hysteresis_c,1)} °C</dd><dt>Heizzustand</dt><dd>${heatCaption}</dd><dt>Ermittlung der Heizzeit</dt><dd>${heatSource}</dd></dl><p>${esc(s.decision_text)}</p></div><div class="card"><h2>Messwerte und Verfügbarkeit</h2><p data-door-status>${doorText}</p>${["upper","lower"].map(pos=>`<h3>${pos==="upper"?"Obere":"Untere"} Messposition</h3><p>${formatValue(pos,"temperature","°C")} · ${qualityText(pos,"temperature")}</p><p>${formatValue(pos,"humidity","% relative Luftfeuchte")} · ${qualityText(pos,"humidity")}</p>`).join("")}<p class="muted">${session?`Erkennung mit: ${s.detection_channels.map(p=>p==="upper"?"oberer Messposition":"unterer Messposition").join(" und ")||"noch keiner Messposition"}.`:"Außerhalb einer Saunasitzung werden keine Türbewegungen ausgewertet."}</p></div><div class="card"><h2>Zeiten und Fristen</h2><dl>${(session?.deadlines||[]).map(d=>`<dt>${esc(deadlines[d.purpose]||"Laufende Frist")}</dt><dd>${remaining(d.due_at)} · bis ${when(d.due_at)}</dd>`).join("")}</dl><p>Mechanischer Ofentimer: ${timerText} · ${timerStatus}</p><p class="muted">${timer.ends_at?`Voraussichtlicher Ablauf: ${when(timer.ends_at)}. `:""}Die tatsächliche Stellung des Drehschalters wird nicht gemessen. Die Anzeige löst keine Steuerung aus.${timer.reset_pending?" Beim nächsten Start beginnt die Anzeige neu.":""}</p></div><div class="card"><h2>Energieverbrauch der Saunasitzung</h2><p data-energy>${energyText}</p><p>Ofenleistung für die Schätzung: ${num(p.nominal_power_kw,2)} kW</p><p class="muted">Gültige Leistungsmessungen ersetzen die Schätzung. Messlücken werden als geschätzter Anteil berücksichtigt.</p></div></div>`;
    if(progressionOpen)this.$("#current details").open=true;
    for(const [id,value] of Object.entries(this.draft||{}))if(this.$(`#${id}`))this.$(`#${id}`).value=value;
  }
  async changeTarget(value) {
    if(!Number.isFinite(value))throw Error("Gültige Solltemperatur eingeben");
    await this.api(`/${this.entry}/temperature`,"POST",{target_temperature_c:value});
    this.draft=null; await this.refresh();
  }
  async updateParameters(parameters,start=false,partial=false) {
    const entry=this.entry;
    const saved=await this.api(`/${entry}/${partial?"temperature":"parameters"}`,"POST",parameters);
    parameters=saved.parameters;
    await this.waitForConfiguration(entry,parameters);
    this.settingsEntry=null;this.draft=null;
    if(start)await this.api(`/${entry}/control`,"POST",{enabled:true});
    await this.refresh();
  }
  async waitForConfiguration(entry,parameters,configuration) {
    // A saved parameter is loaded by HA's single options listener. Do not start
    // against the previous runtime while reload is still in progress.
    let loaded=false;
    for(let attempt=0;attempt<100;attempt++){
      try {const state=await this.api(`/${entry}/state`);const current=state.configuration;
        if(Object.keys(current.parameters).length===Object.keys(parameters).length&&Object.entries(parameters).every(([k,v])=>current.parameters[k]===v)&&Object.entries(configuration||{}).every(([key,value])=>key==="parameters"||current[key]===value)){loaded=true;break;}}
      catch(error){if(error.status_code!==503)throw error;}
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    if(!loaded)throw Error("Parameter gespeichert, Neuladen noch nicht abgeschlossen. Bitte Status prüfen.");
  }
  drawHistory() {
    if(!this.shown){this.$("#plots").innerHTML='<div class="card empty">Noch keine Sitzungsdaten. Wähle eine frühere Saunasitzung oder schalte den Betrieb ein.</div>';this.$("#gangs").innerHTML="";this.$("#event-list").innerHTML="";this.$("#detection-plots").innerHTML="";return;}
    const openEvents=[...this.shadowRoot.querySelectorAll("#event-list details")].map(d=>d.open);
    const {session,records}=this.shown, t=session.timeline;
    const start=stamp(t.session_started_at)-15*60000,end=Math.max(start+1000,stamp(session.ended_at||this.state.now))+15*60000;
    const width=(end-start)/this.zoom, right=end-(end-start-width)*(1-this.pan/100);this.window=[right-width,right];
    if(this.view==="diagnostics")this.drawDiagnostics();
    this.$("#range").textContent=`${when(this.window[0])} – ${when(this.window[1])}`;
    const gangs=[...t.completed,...(t.active?[t.active]:[])];
    const e=session.energy; const energySummary=e?`<p class="muted" data-history-energy>Energieverbrauch: ${(e.measured_kwh+e.estimated_kwh).toLocaleString("de-DE",{maximumFractionDigits:3})} kWh · ${e.unknown_seconds?"Unvollständig":e.measured_seconds?(e.estimated_seconds?"Messung mit geschätzten Anteilen":"Aus gemessener Leistung"):"Geschätzt"}</p>`:"";
    const positionControls=this.historyDetail?`<div class="row position-select"><button data-action="position-upper" aria-pressed="${this.positions.has("upper")}">━━ Oben</button><button data-action="position-lower" aria-pressed="${this.positions.has("lower")}">┄┄ Unten</button></div>`:"";
    this.$("#plots").innerHTML=`<div class="plot-panel"><h2 class="plot-title">${this.selected==="live"?"Verlauf der aktuellen oder letzten Saunasitzung":"Verlauf der ausgewählten Saunasitzung"}</h2><div class="legend top-legend"><span><i style="background:#ff6b4a"></i>Temperatur</span><span><i style="background:#42a5ff"></i>Luftfeuchte</span></div><div class="row position-select"><button data-action="history-detail" aria-pressed="${this.historyDetail}">${this.historyDetail?"Messhöhen ausblenden":"Messhöhen vergleichen"}</button></div>${positionControls}<div class="plot-wrap">${this.chart(records,session,gangs)}<div id="tooltip" hidden></div></div><div class="legend"><span><i style="background:rgba(255,205,80,.95)"></i>Saunatür offen</span><span><i style="background:rgba(165,30,85,.95)"></i>Saunagang</span><span><i style="background:#f5f5f5"></i>Aufguss</span></div><div class="legend"><span><i style="background:rgba(255,150,35,.4)"></i>heizen</span><span><i style="background:rgba(38,125,82,.4)"></i>bereit</span><span><i style="background:rgba(58,125,155,.4)"></i>lüften</span><span><i style="background:#67829a"></i>Zwangskühlung</span></div><p class="muted plot-note">${this.historyDetail?"Durchgezogen: oben · gestrichelt: unten · ":""}vorläufiger Gang · schmaler Streifen: gezählte Heizzeit</p></div>`;
    this.$("#gangs").innerHTML=`<div class="card"><h2>Saunagänge</h2>${energySummary}${gangs.length?`<div class="scroll"><table><thead><tr><th>Gang</th><th>Beginn</th><th>Erkannt</th><th>Bestätigung</th><th>Dauer</th><th>Ende</th></tr></thead><tbody>${gangs.map((g,i)=>`<tr data-gang-id="${esc(g.gang_id)}" data-start="${esc(g.started_at)}"><td>${i+1} · ${g.infusion_events.length?"Bestätigt":"Vorläufig"}</td><td>${when(g.started_at)}</td><td>${when(g.detected_at)}</td><td>${g.infusion_events.length?when(g.infusion_events[0].detected_at):"Aufguss ausstehend"}</td><td>${duration((stamp(g.ended_at||session.ended_at||this.state.now)-stamp(g.started_at))/1000)}</td><td>${when(g.ended_at)}</td></tr>`).join("")}</tbody></table></div>`:'<p class="muted">Keine Saunagänge erkannt.</p>'}</div>`;
    const diagnostics=records.filter(r=>r.kind==="diagnostic");
    const eventRows=[...t.processed].map((e,index)=>({...e,event_id:e.event_id||`legacy-${index}`})).reverse();
    this.$("#event-list").innerHTML=`<div class="card"><details><summary>Ereignisse & Zuordnung (${t.processed.length})</summary><div class="scroll"><table><thead><tr><th>Ereignis</th><th>Zugeordnete Zeit</th><th>Erkennungszeit</th><th>Verlauf</th></tr></thead><tbody>${eventRows.map(e=>`<tr class="event-row" data-event-id="${esc(e.event_id)}" data-selected="false"><td>${esc(events[e.kind]||e.kind)}</td><td>${when(e.effective_at)}</td><td>${when(e.detected_at)}</td><td><button data-action="event-row:${esc(e.event_id)}" aria-label="${esc(events[e.kind]||e.kind)} im Erkennungsverlauf zeigen">Zum Marker</button></td></tr>`).join("")}</tbody></table></div></details>${diagnostics.length?`<details><summary>Historische Fehlerhinweise (${diagnostics.length})</summary>${diagnostics.map(r=>`<p><small>${when(r.received_at)}</small> ${esc(r.payload.messages?.join(" ")||"Keine Störung gemeldet.")}</p>`).join("")}</details>`:""}${t.retracted.length?`<p class="muted">${t.retracted.length} vorläufige Erkennung(en) aufgehoben. Diese werden nicht als Gänge gezählt.</p>`:""}</div>`;
    this.shadowRoot.querySelectorAll("#event-list details").forEach((d,i)=>d.open=!!openEvents[i]);
    if(this.highlightedEventId)this.highlightEvent(this.highlightedEventId,false);
  }
  eventNavigation() {
    return (this.shown?.session.timeline.processed||[]).map((event,index)=>({...event,event_id:event.event_id||`legacy-${index}`}));
  }
  highlightEvent(eventId, reveal) {
    this.highlightedEventId=eventId;
    this.shadowRoot.querySelectorAll("[data-event-id]").forEach(node=>node.dataset.selected="false");
    const nodes=[...this.shadowRoot.querySelectorAll("[data-event-id]")].filter(node=>node.dataset.eventId===eventId);
    nodes.forEach(node=>node.dataset.selected="true");
    if(reveal){const row=nodes.find(node=>node.classList.contains("event-row"));const details=row?.closest?.("details");if(details)details.open=true;row?.scrollIntoView?.({block:"nearest",behavior:"smooth"});}
  }
  focusEvent(eventId, revealRow=false) {
    const event=this.eventNavigation().find(item=>item.event_id===eventId), at=stamp(event?.effective_at||event?.detected_at);
    if(!event||!at){this.highlightEvent(eventId,revealRow);return;}
    const domainStart=stamp(this.shown.session.timeline.session_started_at)-15*60000,domainEnd=Math.max(domainStart+1000,stamp(this.shown.session.ended_at||this.state.now))+15*60000;
    const span=Math.min(domainEnd-domainStart,30*60000), zoom=Math.max(1,Math.min(256,(domainEnd-domainStart)/span));
    this.zoom=zoom;
    const left=Math.max(domainStart,Math.min(domainEnd-span,at-span/2));
    this.pan=zoom===1?100:100*(left-domainStart)/((domainEnd-domainStart)-span);
    this.$("#pan").value=String(this.pan); this.drawHistory(); this.highlightEvent(eventId,revealRow);
    if(!revealRow)this.$('.event-marker[data-selected="true"]')?.scrollIntoView({block:"center",behavior:"smooth"});
  }
  chart(records,session,gangs) {
    const [start,end]=this.window,W=1200,H=480,left=65,right=1135,top=18,bottom=435;
    const x=t=>left+(t-start)/(end-start)*(right-left);
    const raw=records.filter(r=>["measurement","source_snapshot"].includes(r.kind)).map(r=>r.payload);
    this.chartMeasurements=raw;
    const visible=raw.filter(m=>this.positions.has(m.position)&&m.quantity==="temperature"&&stamp(m.received_at)>=start&&stamp(m.received_at)<=end&&m.value!=null);
    const bounds=visible.reduce(([lo,hi],m)=>[Math.min(lo,m.value),Math.max(hi,m.value)],[Infinity,-Infinity]);
    const low=visible.length?Math.floor((bounds[0]-2)/10)*10:20,high=visible.length?Math.ceil((bounds[1]+2)/10)*10:100;
    const humidities=raw.filter(m=>this.positions.has(m.position)&&m.quantity==="humidity"&&stamp(m.received_at)>=start&&stamp(m.received_at)<=end&&m.value!=null);
    const humidityHigh=Math.max(60,Math.ceil((humidities.reduce((max,m)=>Math.max(max,m.value),0)+2)/20)*20);
    const yT=v=>bottom-(v-low)/(high-low)*(bottom-top),yH=v=>bottom-v/humidityHigh*(bottom-top);
    const interval=(a,b,klass,title,y=top,height=bottom-top)=>{const aa=Math.max(start,stamp(a)),bb=Math.min(end,stamp(b||session.ended_at||this.state.now));return bb>aa?`<rect class="${klass}" x="${x(aa).toFixed(2)}" y="${y}" width="${(x(bb)-x(aa)).toFixed(2)}" height="${height}"><title>${esc(title)}</title></rect>`:"";};
    let svg=`<svg class="chart session-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Sitzungsverlauf: Temperatur und Luftfeuchte${this.historyDetail?" beider Messhöhen":""}"><defs><clipPath id="plot-clip"><rect x="${left}" y="${top}" width="${right-left}" height="${bottom-top}"/></clipPath></defs><g clip-path="url(#plot-clip)">`;
    const phases=records.filter(r=>r.kind==="phase"),styles={aufheizen:"heat",bereit:"ready",zwangskühlung:"cool",nachlauf:"after"};
    phases.forEach((p,i)=>{if(styles[p.payload.phase])svg+=interval(p.received_at,phases[i+1]?.received_at,styles[p.payload.phase],p.payload.phase);});
    const doorEvents=session.timeline.processed.filter(e=>e.kind==="door_open"||e.kind==="door_close");
    for(const e of session.timeline.processed.filter(e=>e.kind==="ventilation_confirmed")){const close=doorEvents.find(d=>d.kind==="door_close"&&stamp(d.effective_at)>=stamp(e.effective_at));svg+=interval(e.effective_at,close?.effective_at,"vent","lüften");}
    svg+=gangs.map(g=>interval(g.started_at,g.ended_at,g.infusion_events.length?"gang":"gang provisional",`Gang ab ${when(g.started_at)}`)).join("");
    doorEvents.forEach((e,i)=>{if(e.kind==="door_open")svg+=interval(e.effective_at,doorEvents[i+1]?.effective_at,"door","Saunatür offen");});
    svg+=session.heating.intervals.map(i=>interval(i.started_at,i.ended_at,"heat actual-heat",`Gezählte Heizzeit ab ${when(i.started_at)}`,bottom-5,5)).join("");
    for(let n=0;n<=8;n++){const yy=top+(bottom-top)*n/8;svg+=`<line class="gridline" x1="${left}" x2="${right}" y1="${yy}" y2="${yy}"/>`;}
    for(const e of session.timeline.processed.filter(e=>e.kind==="infusion")){const xx=x(stamp(e.effective_at));svg+=`<line class="infusion" x1="${xx}" x2="${xx}" y1="${top}" y2="${bottom}"><title>Aufguss · ${when(e.effective_at)} · erkannt ${when(e.detected_at)}</title></line>`;}
    for(const position of this.positions)for(const quantity of ["temperature","humidity"]){
      const values=raw.filter(m=>m.position===position&&m.quantity===quantity).sort((a,b)=>stamp(a.received_at)-stamp(b.received_at));
      const ttl=(session.configuration?.parameters||this.state.configuration.parameters).sensor_timeout_seconds*1000;
      const path=historySegments(values,start,end,ttl).map(segment=>monotoneHistoryPath(reduceHistorySegment(segment,x),x,quantity==="temperature"?yT:yH)).join(" ");
      svg+=`<path class="${position} ${quantity}" data-series="${position}_${quantity}" d="${path}"/>`;
    }
    svg+='</g>';
    for(let n=0;n<=8;n++){const f=n/8,yy=bottom-(bottom-top)*f,t=start+(end-start)*f;svg+=`<text class="axis-temperature" x="${left-10}" text-anchor="end" y="${yy+4}">${(low+(high-low)*f).toFixed(0)}</text><text class="axis-humidity" x="${right+10}" y="${yy+4}">${(humidityHigh*f).toFixed(0)}</text><text text-anchor="middle" x="${x(t)}" y="${bottom+27}">${clock(t)}</text>`;}
    svg+=`<text class="axis-temperature" x="13" y="${H/2}">°C</text><text class="axis-humidity" x="${W-15}" y="${H/2}">%</text><line id="cursor" x1="0" x2="0" y1="${top}" y2="${bottom}" stroke="#eee" stroke-dasharray="3 3" visibility="hidden"/>`;
    return svg+'</svg>';
  }
  hoverChart(e) {
    const svg=e.target.closest("svg.session-chart"),rect=svg.getBoundingClientRect(),px=(e.clientX-rect.left)/rect.width*1200;
    if(px<65||px>1135)return;
    const time=this.window[0]+(px-65)/1070*(this.window[1]-this.window[0]);
    const ttl=(this.shown.session.configuration?.parameters||this.state.configuration.parameters).sensor_timeout_seconds*1000;
    const rows=[];for(const p of this.positions)for(const q of ["temperature","humidity"]){
      const nearest=this.chartMeasurements.filter(m=>m.position===p&&m.quantity===q&&m.value!=null&&stamp(m.received_at)>=this.window[0]&&stamp(m.received_at)<=this.window[1]).reduce((a,b)=>!a||Math.abs(stamp(b.received_at)-time)<Math.abs(stamp(a.received_at)-time)?b:a,null);
      if(nearest&&(!ttl||Math.abs(stamp(nearest.received_at)-time)<=ttl))rows.push(`<span style="color:${q==="temperature"?'#e25d40':'#2f8bde'}">${q==="temperature"?"Temperatur":"Luftfeuchte"}${this.historyDetail?` ${p==="upper"?"oben":"unten"}`:""}</span><br>${nearest.value.toLocaleString("de-DE")} ${q==="temperature"?"°C":"%"} · ${when(nearest.received_at)}`);
    }
    const tip=this.$("#tooltip");tip.innerHTML=`<strong>${when(time)}</strong><br>${rows.join("<br>")}`;tip.hidden=false;
    tip.style.left=`${Math.max(0,Math.min(rect.width-290,e.clientX-rect.left+12))}px`;tip.style.top=`${Math.max(0,e.clientY-rect.top-90)}px`;
    const cursor=this.$("#cursor");cursor.setAttribute("x1",px);cursor.setAttribute("x2",px);cursor.setAttribute("visibility","visible");
  }
  zoomAt(factor, clientX, svg) {
    if(!this.window)return;
    const rect=svg.getBoundingClientRect(), px=(clientX-rect.left)/rect.width*1200, fraction=Math.max(0,Math.min(1,(px-65)/1070));
    const domainStart=stamp(this.shown.session.timeline.session_started_at)-15*60000,domainEnd=Math.max(domainStart+1000,stamp(this.shown.session.ended_at||this.state.now))+15*60000;
    const nextZoom=Math.max(1,Math.min(256,this.zoom*factor)); if(nextZoom===this.zoom)return;
    const nextWidth=(domainEnd-domainStart)/nextZoom, focus=this.window[0]+fraction*(this.window[1]-this.window[0]);
    const left=Math.max(domainStart,Math.min(domainEnd-nextWidth,focus-fraction*nextWidth));
    this.zoom=nextZoom; this.pan=nextZoom===1?100:100*(left-domainStart)/((domainEnd-domainStart)-nextWidth);
    this.$("#pan").value=String(this.pan); this.drawHistory();
  }
  pinchZoom() {
    const points=[...this.pointers.values()]; if(points.length!==2||points[0].svg!==points[1].svg)return;
    const distance=Math.hypot(points[0].x-points[1].x,points[0].y-points[1].y), center=(points[0].x+points[1].x)/2;
    if(this.pinchDistance){this.zoomAt(distance/this.pinchDistance,center,points[0].svg);} this.pinchDistance=distance;
  }
  eventMarkerLayout(markers, start, end, stripWidth, markerSize=17, markerGap=3) {
    const width=Number.isFinite(stripWidth)&&stripWidth>0?stripWidth:markerSize*8;
    const collisionPercent=100*(markerSize+markerGap)/width;
    const lanes=[];
    const visible=markers.map((event,index)=>({event,index,at:stamp(event.effective_at||event.detected_at)}))
      .filter(marker=>Number.isFinite(marker.at)&&marker.at>=start&&marker.at<=end)
      .sort((a,b)=>a.at-b.at||a.index-b.index);
    for(const marker of visible){
      marker.left=(marker.at-start)/(end-start)*100;
      marker.lane=lanes.findIndex(last=>marker.left-last>=collisionPercent);
      if(marker.lane<0){marker.lane=lanes.length;lanes.push(marker.left);}else lanes[marker.lane]=marker.left;
    }
    return {markers:visible,maxLane:Math.max(0,lanes.length-1),markerSize,markerGap};
  }
  drawDiagnostics() {
    if(!this.shown){this.$("#detection-plots").innerHTML='<p>Keine Saunasitzung ausgewählt.</p>';return;}
    const traces=this.shown.records.filter(r=>r.kind==="detector_trace").map(r=>r.payload);
    const groups=[
      ["Türerkennung","door_temperature_slope","Temperaturänderung · °C/min"],
      ["Türerkennung","door_humidity_delta","Feuchteänderung · Prozentpunkte"],
      ["Starke Personenerkennung","strong_temperature_slope","Temperaturtrend · °C/min"],
      ["Starke Personenerkennung","strong_humidity_slope","Feuchtetrend · Prozentpunkte/min"],
      ["Schwache Personenerkennung","weak_temperature_slope","Temperaturtrend · °C/min"],
      ["Schwache Personenerkennung","weak_humidity_slope","Feuchtetrend · Prozentpunkte/min"],
      ["Aufgusserkennung","infusion_humidity_delta","Feuchteänderung · Prozentpunkte"],
      ["Aufgusserkennung","infusion_temperature_delta","Temperaturänderung · °C"],
      ["Lüftungserkennung","ventilation_temperature_loss","Temperaturverlust · °C"],
      ["Lüftungserkennung","ventilation_absolute_humidity_loss","Absoluter Feuchteverlust · %",v=>v*100]];
    // The session snapshot is the effective configuration for archived data.
    // Older sessions fall back to the current state when no snapshot exists.
    const parameters={...this.state.configuration?.parameters,...this.shown.session.configuration?.parameters};
    const thresholds=(metric,position)=>{
      if(metric==="door_temperature_slope")return [parameters.door_open_slope,parameters.door_heating_slope,parameters.door_close_slope];
      if(metric==="door_humidity_delta")return [-parameters[`door_open_humidity_${position}`]];
      if(metric.startsWith("infusion_"))return [parameters[metric.replace("_delta","")]];
      if(metric==="ventilation_temperature_loss")return [parameters[`vent_drop_${position}`]];
      if(metric==="ventilation_absolute_humidity_loss")return [parameters.vent_absolute_humidity_loss_percent/100];
      return [parameters[metric.replace("_slope",`_${position}`)]];
    };
    const [start,end]=this.window;
    const existingStrip=this.$(".event-strip"), detectionPlots=this.$("#detection-plots");
    // On the first hidden/mobile render the strip has no box yet.  Half the
    // panel width is deliberately conservative; the next refresh uses its box.
    const stripWidth=existingStrip?.getBoundingClientRect?.().width||Math.max(0,(detectionPlots?.getBoundingClientRect?.().width||0)/2);
    const markerLayout=this.eventMarkerLayout(this.eventNavigation(),start,end,stripWidth);
    let html='<details><summary>Hinweise zur Erkennung</summary><p class="muted">Die Kurven zeigen die gespeicherten Erkennungswerte, die gestrichelten Linien die zugehörigen Schwellen. Ein Grenzübertritt löst erst dann ein Ereignis aus, wenn auch die übrigen Bedingungen erfüllt sind. Nach Beginn eines Gangs, im Nachlauf und während laufender Kühlung ruht die Personensuche. Weitere Aufgüsse werden im Gang weiter erkannt.</p></details>';
    const stripHeight=markerLayout.markerSize+(markerLayout.maxLane+1)*(markerLayout.markerSize+markerLayout.markerGap);
    html+=`<div class="plot-panel"><h2>Erkannte Ereignisse</h2><p class="muted">Ein Marker je gespeichertem Ereignis im sichtbaren Zeitausschnitt. Die Zahl bleibt in der gesamten Sitzung stabil.</p><div class="event-strip" style="--event-strip-height:${stripHeight}px" aria-label="Ereignisstreifen auf der Zeitachse">${markerLayout.markers.map(({event,index,left,lane})=>`<button class="event-marker" style="left:${left.toFixed(3)}%;--event-marker-lane:${lane};--event-marker-bottom:${-6+lane*(markerLayout.markerSize+markerLayout.markerGap)}px" data-action="event-marker:${esc(event.event_id)}" data-event-id="${esc(event.event_id)}" data-selected="false" aria-label="${esc(events[event.kind]||event.kind)}, ${when(event.effective_at||event.detected_at)}">${index+1}</button>`).join("")}</div></div>`;
    if(!traces.length){this.$("#detection-plots").innerHTML=html+'<p>Für diese Saunasitzung liegen keine gespeicherten Erkennungsverläufe vor.</p>';return;}
    const availableWidth=detectionPlots?.getBoundingClientRect?.().width||640;
    const columns=availableWidth>600?2:1;
    const chartWidth=Math.max(280,(availableWidth-(columns-1)*14)/columns-20);
    const plotLeft=40,plotRight=chartWidth-25;
    const chartX=t=>plotLeft+(stamp(t)-start)/(end-start)*(plotRight-plotLeft);
    html+='<div class="diagnostic-grid">';
    for(const [group,metric,label,convert=v=>v] of groups){
      const vals=traces.flatMap(t=>Object.values(t.metrics||{}).map(m=>m?.[metric])).filter(Number.isFinite);
      // Pre-ventilation archives do not have these metrics.  Do not show an
      // empty zero line that could be mistaken for a measured loss.
      if(!vals.length)continue;
      const lines=[...thresholds(metric,"upper"),...thresholds(metric,"lower")].filter(Number.isFinite);
      const chartVals=vals.map(convert),chartLines=lines.map(convert);
      const [min,max]=[...chartVals,...chartLines].reduce(([lo,hi],v)=>[Math.min(lo,v),Math.max(hi,v)],[0,0]);
      const lo=min-1,hi=max+1,y=v=>165-(v-lo)/(hi-lo)*140;
      let chart='<svg class="chart detector-chart" viewBox="0 0 '+chartWidth+' 200" role="img" aria-label="'+esc(group+' '+label)+'">';
      for(const pos of ["upper","lower"]){
        let path="",last=null;
        for(const t of traces){const v=t.metrics?.[pos]?.[metric],time=stamp(t.at);if(!Number.isFinite(v)||!Number.isFinite(time)||time<start||time>end)continue;path+=`${last==null||time-last>parameters.person_step_seconds*1000?"M":"L"}${chartX(t.at).toFixed(2)},${y(convert(v)).toFixed(2)} `;last=time;}
        chart+=`<path class="${pos} ${pos==="upper"?"temperature":"humidity"}" data-series="detector_${metric}_${pos}" d="${path}"/>`;
        for(const v of thresholds(metric,pos).filter(Number.isFinite)){const display=convert(v);chart+=`<line x1="${plotLeft}" x2="${plotRight}" y1="${y(display)}" y2="${y(display)}" stroke="${pos==="upper"?'#ff6b4a':'#42a5ff'}" stroke-dasharray="4 5"><title>Schwelle ${pos==="upper"?"oben":"unten"}: ${num(display,2)}</title></line>`;}
      }
      for(let n=0;n<=4;n++){const v=lo+(hi-lo)*n/4,t=start+(end-start)*n/4;chart+=`<text x="4" y="${y(v)+4}">${num(v,1)}</text><text text-anchor="middle" x="${chartX(t)}" y="192">${clock(t)}</text>`;}
      chart+='</svg>';html+=`<div class="plot-panel"><h3>${group} · ${label}</h3><p class="muted">Orange: oben · Blau: unten</p>${chart}</div>`;
    }
    html+='</div><div class="card"><h2>Erkennungsbedingungen und Bestätigung</h2><div class="scroll"><table><thead><tr><th>Zeit</th><th>Aktive Prüfungen</th><th>Bedingungen erfüllt</th><th>Bestätigungszeiten</th><th>Ausgelöste Signale</th></tr></thead><tbody>'+traces.filter(t=>t.signals.length).map(t=>`<tr><td>${when(t.at)}</td><td>${esc(t.checks?Object.entries(t.checks).filter(([,v])=>v).map(([k])=>signalText[k]||"Erkennung").join(", "):"Keine Angabe in älteren Daten")}</td><td>${esc(Object.entries(t.conditions).filter(([,v])=>v).map(([k])=>signalText[k]||"Erkennungsbedingung").join(", "))}</td><td>${esc(Object.entries(t.holds).map(([k,v])=>`${signalText[k]||"Signal"}: ${num(v)} s`).join(" · "))}</td><td>${esc(t.signals.map(k=>events[k]||k).join(", "))}</td></tr>`).join("")+'</tbody></table></div></div>';
    this.$("#detection-plots").innerHTML=html;
  }
  drawSettings() {
    const state=this.state;
    if(this.settingsEntry!==this.entry){
      const field=d=>`<label class="field">${esc(d.label)} (${esc(d.unit)})<input type="number" name="${esc(d.key)}" aria-describedby="help-${esc(d.key)}" step="${d.integer?1:"any"}" min="${d.minimum??0}" max="${d.maximum}" value="${esc(state.configuration.parameters[d.key]??"")}" ${d.optional?"":"required"}><small id="help-${esc(d.key)}">${esc(d.description)}</small></label>`;
      const groups={temperature:"Temperatur und Heizung",temperature_programs:"Temperatursteigerung und Profile",operation:"Betrieb und Kühlung",light:"Licht",monitoring:"Überwachung",timer:"Timer und Energie",display:"Anzeige"};
      const fields=Object.entries(groups).map(([key,title],index)=>`<details class="settings-group" ${index===0?"open":""}><summary>${title}</summary><div class="forms">${state.parameters.filter(d=>d.group===key&&!d.expert).map(field).join("")}</div></details>`).join("");
      const expertGroups={measurement:["Messbasis",d=>["grid_seconds","median_seconds"].includes(d.key)],door:["Tür",d=>d.key.startsWith("door_")],vent:["Lüftung",d=>d.key.startsWith("vent_")],person:["Person",d=>d.key.startsWith("person_")||d.key.startsWith("strong_")||d.key.startsWith("weak_")],infusion:["Aufguss",d=>d.key.startsWith("infusion_")]};
      const experts=Object.values(expertGroups).map(([title,match])=>`<section class="expert-group"><h3>${title}</h3><div class="forms">${state.parameters.filter(d=>d.expert&&match(d)).map(field).join("")}</div></section>`).join("");
      this.$("#settings").innerHTML=`<div class="card"><h2>Einstellungen</h2><p id="configuration-lock" class="muted"></p><form><fieldset id="parameters">${fields}<details class="settings-group"><summary>Experteneinstellungen zur Erkennung</summary><p class="muted">Diese Werte verändern die Erkennung von Türöffnungen, Personen und Aufgüssen. Die Erkennungskontrolle zeigt ihre Wirkung.</p>${experts}</details><button type="submit" class="primary">Einstellungen speichern</button></fieldset></form><div class="row"><a href="/config/integrations/integration/ha_sauna">Sensoren und Geräte zuordnen</a></div></div><div class="card"><h2>Standardwerte</h2><p class="muted">Setzt Parameter, Temperaturprogramm und Protokollierung auf die Standardwerte zurück. Die Zuordnung von Sensoren, Geräten und Tastern bleibt erhalten.</p><button data-action="reset-settings">Standardwerte wiederherstellen</button><p id="settings-reset-status" class="muted" role="status"></p></div><div class="card"><h2>Protokollierung</h2><p class="muted">Im Home-Assistant-Protokoll unter custom_components.ha_sauna. Die Stufe ist auch während einer Saunasitzung änderbar. Das Sitzungsarchiv bleibt unabhängig davon vollständig.</p><div class="row"><label for="log-level">Protokollstufe</label><select id="log-level"><option value="ERROR">ERROR · Fehler</option><option value="INFO">INFO · Betriebsereignisse (Standard)</option><option value="DEBUG">DEBUG · Detaillierte Diagnose</option></select><button data-action="logging">Übernehmen</button></div><p class="muted">INFO enthält Fehler, Warnungen, Zustandswechsel und Schaltbefehle. DEBUG ergänzt Messwerte und Erkennungsprüfungen.</p><a href="/config/logs">Home-Assistant-Protokoll öffnen</a></div><div class="card"><h2>Sitzungsarchiv</h2><p class="muted">Alle empfangenen Messwerte, Sitzungsverläufe und Ereigniszuordnungen herunterladen.</p><button data-action="export">Archiv als ZIP herunterladen</button></div>`;
      this.$("#log-level").value=state.configuration.log_level;
      this.settingsEntry=this.entry;
    }
    this.$("#parameters").disabled=!this.hass.user?.is_admin;
    for(const d of state.parameters){const input=this.$(`#parameters input[name="${d.key}"]`);if(!input)continue;input.disabled=!this.hass.user?.is_admin||(state.configuration_locked&&!d.live_editable);if(!input.dataset.edited&&this.shadowRoot.activeElement!==input)input.value=d.key==="target_temperature_c"?state.target_temperature:(state.configuration.parameters[d.key]??"");}
    this.$("#log-level").disabled=!this.hass.user?.is_admin;
    this.$('[data-action="logging"]').disabled=!this.hass.user?.is_admin;
    this.$('[data-action="reset-settings"]').disabled=!this.hass.user?.is_admin||state.configuration_locked;
    this.$("#configuration-lock").textContent=state.configuration_locked?"Solltemperatur, Endtemperatur und Verteilung der Steigerung sind änderbar. Andere Einstellungen bleiben bis zum Ende der Saunasitzung gesperrt. Laufende Fristen und Heizsperren bleiben immer wirksam.":"Alle erforderlichen Einstellungen haben Standardwerte. Änderungen der Grundeinstellungen gelten für die nächste Saunasitzung.";
  }
  async saveSettings() {
    this.message(null);
    const partial=this.state.configuration_locked;
    const values=partial?{}:{...this.state.configuration.parameters};
    for(const [key,value] of new FormData(this.$("form"))){
      if(partial&&key==="target_temperature_c"&&!this.$(`input[name="${key}"]`).dataset.edited)continue;
      if(value!=="")values[key]=Number(value);else if(partial)values[key]=null;else delete values[key];
    }
    await this.updateParameters(values,false,partial);
  }
  async resetSettings() {
    const entry=this.entry;
    const saved=await this.api(`/${entry}/parameters/reset`,"POST");
    await this.waitForConfiguration(entry,saved.parameters,saved.configuration);
    this.settingsEntry=null;this.draft=null;
    this.shadowRoot.querySelectorAll("#parameters input[data-edited]").forEach(input=>delete input.dataset.edited);
    await this.refresh();
    const status=this.shadowRoot.querySelector("#settings-reset-status");
    if(status)status.textContent="Standardwerte wurden wiederhergestellt.";
  }
  async action(action) {
    this.message(null);
    const permissions=this.state?.permissions||{};
    if((action==="operation"&&!permissions.control)
      ||((action==="target"||action.startsWith("preset:"))&&!permissions.temperature)
      ||((action==="program-free"||action.startsWith("profile:"))&&!permissions.program)
      ||(action.startsWith("light:")&&!permissions.light)
      ||(action==="manual-light"&&(!permissions.admin||!permissions.light))
      ||(action.startsWith("heater:")&&(!permissions.admin||!permissions.heater))
      ||(action.startsWith("end-phase:")&&!permissions.admin)
      ||(action==="reset-settings"&&(!permissions.admin||this.state.configuration_locked))
      ||((action==="details"||action==="detail"||action==="diagnostics"||action==="settings")&&!permissions.admin))return;
    if(action==="configure"){await this.action("details");return this.action("settings");}
    if(action==="reset-settings")return this.resetSettings();
    if(action==="logging"){await this.api(`/${this.entry}/logging`,"POST",{level:this.$("#log-level").value});await this.refresh();return;}
    if(action==="menu"){this.dispatchEvent(new CustomEvent("hass-toggle-menu",{bubbles:true,composed:true}));return;}
    if(action.startsWith("event-marker:")){this.highlightEvent(action.slice(13),true);return;}
    if(action.startsWith("event-row:")){this.focusEvent(action.slice(10));return;}
    if(action==="normal"||action==="details"){
      this.$(".normal-tabs").hidden=action!=="normal";this.$(".detail-tabs").hidden=action!=="details";
      this.shadowRoot.querySelectorAll(".main-tabs button").forEach(b=>b.setAttribute("aria-selected",String(b.dataset.action===action)));
      return this.action(action==="normal"?"overview":"detail");
    }
    if(["overview","history","detail","diagnostics","settings"].includes(action)){
      this.view=action;this.$("#current").hidden=action!=="overview";this.$("#details").hidden=action!=="detail";
      this.$("#history").hidden=!["history","diagnostics"].includes(action);this.$("#settings").hidden=action!=="settings";
      this.$("#plots").hidden=action==="diagnostics";this.$("#detection-plots").hidden=action!=="diagnostics";
      this.$("#gangs").hidden=action==="diagnostics";this.$("#event-list").hidden=action!=="diagnostics";
      this.shadowRoot.querySelectorAll(".normal-tabs button,.detail-tabs button").forEach(b=>b.setAttribute("aria-selected",String(b.dataset.action===action)));
      this.drawHistory();return;
    }
    if(action.startsWith("preset:"))return this.changeTarget(Number(action.slice(7)));
    if(action.startsWith("profile:")){
      await this.api(`/${this.entry}/program`,"POST",{profile:action.slice(8)});
      this.draft=null; await this.refresh(); return;
    }
    if(action==="progression"){
      const start=Number(this.$("#progression-start").value), end=Number(this.$("#progression-end").value), gangs=Number(this.$("#progression-gangs").value);
      if(!Number.isFinite(start)||!Number.isFinite(end)||!Number.isInteger(gangs))throw Error("Start, Ende und Verteilung vollständig eingeben");
      if(this.draft&&Object.hasOwn(this.draft,"progression-start"))return this.action("program-free");
      if(!permissions.temperature)return;
      const changed={};
      if(end!==this.state.configuration.parameters.final_temperature_c)changed.final_temperature_c=end;
      if(gangs!==this.state.configuration.parameters.temperature_gangs)changed.temperature_gangs=gangs;
      if(Object.keys(changed).length){await this.api(`/${this.entry}/temperature`,"POST",changed);this.draft=null;await this.refresh();}
      return;
    }
    if(action==="program-free"){
      const body={target_temperature_c:Number(this.$("#progression-start").value),final_temperature_c:Number(this.$("#progression-end").value),temperature_gangs:Number(this.$("#progression-gangs").value)};
      if(!Number.isFinite(body.target_temperature_c)||!Number.isFinite(body.final_temperature_c)||!Number.isInteger(body.temperature_gangs))throw Error("Start, Ende und Verteilung vollständig eingeben");
      await this.api(`/${this.entry}/program`,"POST",body);this.draft=null;await this.refresh();return;
    }
    if(action==="target")return this.changeTarget(Number(this.$("#target").value));
    if(action.startsWith("light:")){
      const preset=action.slice(6), value=preset==="true"?true:preset==="false"?false:preset==="auto"?null:"normal";
      await this.api(`/${this.entry}/light`,"POST",{value});this.manualLightDraft=null;await this.refresh();return;
    }
    if(action==="manual-light"){
      const value=Number(this.$("#manual-light-value").value);
      if(!Number.isFinite(value)||value<0||value>100)throw Error("Helligkeit zwischen 0 und 100 % eingeben");
      await this.api(`/${this.entry}/light`,"POST",{value});this.manualLightDraft=null;await this.refresh();return;
    }
    if(action.startsWith("heater:")){
      const preset=action.slice(7), value=preset==="true"?true:preset==="false"?false:null;
      if(preset!=="true"&&preset!=="false"&&preset!=="auto")throw Error("Ungültige Ofensteuerung");
      await this.api(`/${this.entry}/heater`,"POST",{value});await this.refresh();return;
    }
    if(action.startsWith("end-phase:")){
      const [,purpose,token]=action.split(":");
      await this.api(`/${this.entry}/finish_phase`,"POST",{purpose,token:decodeURIComponent(token)});
      await this.refresh();return;
    }
    if(action==="operation"){await this.api(`/${this.entry}/control`,"POST",{enabled:!this.state.operation_enabled});await this.refresh();}
    if(action==="zoom-in")this.zoom=Math.min(256,this.zoom*2);
    if(action==="zoom-out")this.zoom=Math.max(1,this.zoom/2);
    if(action==="reset-zoom"){this.zoom=1;this.pan=100;this.$("#pan").value="100";}
    if(action.includes("zoom"))this.drawHistory();
    if(action==="history-detail"){this.historyDetail=!this.historyDetail;if(this.historyDetail)this.positions=new Set(["upper","lower"]);else this.positions=new Set(["upper"]);this.drawHistory();}
    if(action.startsWith("position-")){const p=action.slice(9);this.positions.has(p)?this.positions.delete(p):this.positions.add(p);this.drawHistory();}
    if(action==="export"){
      const signed=await this.hass.callWS({type:"auth/sign_path",path:`/api/ha_sauna/${this.entry}/export`});
      const link=document.createElement("a");link.href=signed.path;link.download="ha-sauna-archive.zip";this.shadowRoot.append(link);link.click();link.remove();
    }
  }
}
if(!customElements.get("ha-sauna-panel"))customElements.define("ha-sauna-panel",SaunaPanel);
