/* Native HA custom panel. Backend objects are authoritative; no control model here. */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const stamp = v => v ? new Date(v).getTime() : null;
const when = v => v ? new Date(v).toLocaleString("de-DE", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "–";
const clock = v => new Date(v).toLocaleTimeString("de-DE", {hour:"2-digit",minute:"2-digit"});
const num = (v,d=1) => v == null ? "–" : Number(v).toLocaleString("de-DE",{maximumFractionDigits:d});
const duration = v => v == null ? "–" : `${Math.floor(Math.max(0,v)/60)}:${String(Math.floor(Math.max(0,v)%60)).padStart(2,"0")} min`;
const phases = {aus:"Aus",aufheizen:"Aufheizen",bereit:"Bereit",saunagang:"Saunagang",nachlauf:"Nachlauf",zwangskühlung:"Zwangskühlung"};
const events = {door_open:"Tür geöffnet",door_close:"Tür geschlossen",person_strong:"Person erkannt",person_weak:"Person erkannt (schwach)",infusion:"Aufguss",ventilation_confirmed:"Durchlüften bestätigt",operation_off:"Betrieb ausgeschaltet",confirmation_expired:"Vorläufigen Gang aufgehoben"};
const signalText = {door_heating:"Temperaturabfall trotz Heizen",door_close:"Türschließung",door_open:"Türöffnung",door_close:"Türschließung",door:"Tür",infusion:"Aufguss",strong:"Deutliches Personensignal",weak:"Schwaches Personensignal"};
const errorText = error => {
  // hass.callApi legt die Antwort der Integration in body ab; error enthält
  // lediglich den allgemeinen HTTP-Fehler (etwa „Response error: 409“).
  const detail=error?.body?.error||error?.body?.message;
  if(typeof detail==="string"&&detail.trim())return detail;
  if(error?.status_code)return ({401:"Die Anmeldung ist abgelaufen. Bitte Home Assistant neu laden.",403:"Für diese Änderung sind Administratorrechte erforderlich.",409:"Die Aktion ist im aktuellen Zustand nicht möglich. Bitte die Hinweise zur Einrichtung prüfen.",503:"Die Sauna-Integration wird gerade neu geladen. Bitte kurz warten."})[error.status_code]||`Die Anfrage konnte nicht verarbeitet werden (HTTP ${error.status_code}).`;
  if(error?.error==="Request error")return "Home Assistant ist zurzeit nicht erreichbar. Bitte die Verbindung prüfen.";
  return error?.message||error?.error||String(error);
};

class SaunaPanel extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode:"open"}); this.view="overview"; this.selected="live";
    this.cache=new Map(); this.zoom=1; this.pan=100; this.generation=0; this.positions=new Set(["upper","lower"]);
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
      :host{--accent:#e58a55}main{max-width:none}header{margin-bottom:12px}.tabs{margin-top:8px}.plot-panel{padding:20px 12px 30px;background:#111;color:#c8c8c8;border-radius:8px}.plot-title{text-align:center;color:#aaa;font-weight:500;font-size:18px}.legend{justify-content:center;margin:18px 0}.legend i{display:inline-block;width:32px;height:5px;margin-right:6px;vertical-align:middle}.top-legend{margin:24px 0 0}.position-select{justify-content:center;font-size:12px;gap:8px}.position-select button{background:transparent;color:#aaa;border:0;padding:6px}.position-select button[aria-pressed=false]{opacity:.35}.plot-wrap{position:relative}.chart{height:520px;width:100%;touch-action:none}.chart text{fill:#aaa}.detector-chart{height:220px}.main-tabs{border-bottom:1px solid var(--divider-color,#333);padding-bottom:10px}.normal-tabs,.detail-tabs{font-size:13px}.chart .gridline{stroke:rgba(255,255,255,.10)}.chart .gang{fill:rgba(165,30,85,.46);stroke:none}.chart .provisional{stroke:#a51e55;stroke-dasharray:5 4}.chart .heat{fill:rgba(255,150,35,.28)}.chart .ready{fill:rgba(38,125,82,.3)}.chart .vent{fill:rgba(58,125,155,.3)}.chart .cool{fill:rgba(103,130,154,.3)}.chart .after{fill:rgba(58,125,155,.18)}.chart .door{fill:rgba(255,205,80,.75)}.chart .infusion{stroke:#f5f5f5;stroke-width:1}.chart path{stroke-width:3.5}.chart path.temperature{stroke:#ff6b4a}.chart path.humidity{stroke:#42a5ff}.chart path.lower{stroke-dasharray:8 5;opacity:.75}.chart .axis-temperature{fill:#ff6b4a}.chart .axis-humidity{fill:#42a5ff}.plot-note{text-align:center;color:#999}#tooltip{position:absolute;pointer-events:none;background:#f6f6f6;color:#333;padding:10px 13px;font:13px/1.5 system-ui;border:1px solid #999;z-index:2;box-shadow:0 3px 10px #0003}.dashboard{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px;max-width:1150px;margin:0 auto}.tiles{display:grid;grid-template-columns:1fr 1fr;gap:12px}.tile{padding:18px;text-align:left;min-height:72px}.tile.full{grid-column:1/-1}.greeting{text-align:center;font-size:24px;margin:25px 0}.dial{max-width:330px;display:block;margin:auto}.dial text{fill:var(--primary-text-color,#ddd)}.dial .reading{font-size:33px}.dial .caption{font-size:13px}.dashboard .card{margin-top:0}.state-line{display:flex;justify-content:space-between;gap:15px}.gauge-card h2{text-align:center}.temperature-choice{display:flex;gap:8px;align-items:center;justify-content:center;margin-top:10px}.temperature-choice input{width:95px}
      @media(max-width:800px){.dashboard{grid-template-columns:1fr}.chart{height:420px}.plot-panel{padding:12px 0}.legend{font-size:12px;gap:12px}.legend i{width:22px}main{padding:14px}}

      .notice button{background:#6f481d;color:#fff;border-color:#6f481d}.card,.dashboard>*,.detail-grid>*,.forms>*{min-width:0}.notice,p,.field{overflow-wrap:anywhere}.dashboard{max-width:1280px;align-items:start;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.greeting{font-size:21px;margin:16px 0}.gauges{display:grid;grid-template-columns:1fr 1fr;gap:12px;text-align:center}.dial{max-width:240px}.gauge-card h2{font-size:16px}.temperature-choice{flex-wrap:wrap}.temperature-choice input{min-width:0;width:80px}.tile{padding:12px;min-height:54px}.tiles{gap:10px}.timer-strip{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:18px 0;padding:14px 0;border-block:1px solid var(--divider-color,#333)}.timer-strip small,.timer-strip strong{display:block}.timer-strip strong{font-size:21px;font-variant-numeric:tabular-nums}.timer-strip small{font-size:12px}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.detail-grid .card{margin:0}.center{text-align:center}dl{margin:0}dt{font-size:13px;color:var(--secondary-text-color,#aaa);margin-top:12px}dd{margin:2px 0 8px}.forms{grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.settings-group{margin-bottom:30px}.field small{font-size:12px}.tabs{flex-wrap:wrap}.state-line .badge{align-self:start;white-space:nowrap}
      @media(max-width:1000px){.dashboard{grid-template-columns:1fr}.dashboard .gauge-card{max-width:none}.dial{max-width:210px}}
      @media(max-width:600px){.detail-grid,.forms{grid-template-columns:1fr}.gauges{gap:4px}.gauges h2{font-size:14px}.card{padding:14px}.timer-strip strong{font-size:18px}.temperature-choice{gap:6px}header select{max-width:110px}}
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
    this.shadowRoot.addEventListener("click",e=>{const b=e.target.closest("button[data-action]"); if(b)this.action(b.dataset.action).catch(err=>this.message(err));});
    this.shadowRoot.addEventListener("change",e=>{
      if(e.target.id==="instance"){this.entry=e.target.value;this.generation++;this.selected="live";this.cache.clear();this.settingsEntry=null;this.draft=null;this.zoom=1;this.refresh();}
      if(e.target.id==="session"){this.selected=e.target.value;this.zoom=1;this.pan=100;this.refresh();}
    });
    this.shadowRoot.addEventListener("input",e=>{if(e.target.closest("#parameters"))e.target.dataset.edited="true";if(e.target.closest("#current"))this.draft={...this.draft,[e.target.id]:e.target.value};if(e.target.id==="pan"){this.pan=Number(e.target.value);this.drawHistory();}});
    this.shadowRoot.addEventListener("pointermove",e=>{if(e.target.closest("svg.session-chart"))this.hoverChart(e);});
    this.shadowRoot.addEventListener("pointerout",e=>{if(e.target.closest("svg.session-chart")&&!e.relatedTarget?.closest?.("svg.session-chart")){this.$("#tooltip")?.setAttribute("hidden","");this.$("#cursor")?.setAttribute("visibility","hidden");}});
    this.shadowRoot.addEventListener("wheel",e=>{if(!e.target.closest("svg.session-chart"))return;e.preventDefault();this.zoom=Math.max(1,Math.min(256,this.zoom*(e.deltaY<0?1.25:.8)));this.drawHistory();},{passive:false});
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
    const count=latest?.timeline.completed.filter(g=>g.infusion_events.length).length??s.gang_count;
    const value=(position,quantity)=>s.measurements.find(m=>m.position===position&&m.quantity===quantity)?.value;
    const quality=(position,quantity)=>s.measurement_status[`${position}_${quantity}`]?.state;
    const qualityText=(position,quantity)=>({current:"Aktueller Messwert",stale:"Letzter Wert · veraltet",unavailable:"Messwert fehlt",validity_unconfigured:"Gültigkeit noch nicht eingestellt"}[quality(position,quantity)]||"Messwert fehlt");
    const formatValue=(position,quantity,unit)=>`${num(value(position,quantity),1)} ${unit}`;
    const timer=s.mechanical_timer;
    const timerStatus={idle:"Noch nicht gestartet",running:"Geschätzte Restzeit",paused:"Angehalten",expired:"Drehschalter neu einstellen"}[timer.state];
    const timerText=timer.state==="idle"?"–":duration(timer.remaining_seconds);
    const activity=active?`Saunagang · ${duration(s.gang_duration_seconds)} · ${s.gang_confirmation==="confirmed"?"durch Aufguss bestätigt":"Aufguss ausstehend"}`:session?.after_run?`Nachlauf · noch ${remaining(session.after_run.ends_at)}`:session?.cooling?.started_at?`Zwangskühlung · noch ${remaining(session.cooling.ends_at)}`:s.cooling_wait_until?`Personenerkennung abwarten · noch ${remaining(s.cooling_wait_until)}`:session?`${s.operation_enabled?"Sitzung seit":"Sitzung unterbrochen · seit"} ${when(session.timeline.session_started_at)}`:latest?`Letzte Sitzung beendet · ${when(latest.ended_at)}`:"Keine laufende Saunasitzung";
    const energyLabel={measured:"Gemessen",estimated:"Geschätzt",mixed:"Teilweise geschätzt",incomplete:"Unvollständig"};
    const energy=latest?.energy;
    const energyText=`${num(session?s.energy_kwh:energy?energy.measured_kwh+energy.estimated_kwh:0,3)} kWh · ${energyLabel[session?s.energy_source:energy?.unknown_seconds?"incomplete":energy?.measured_seconds?(energy.estimated_seconds?"mixed":"measured"):"estimated"]}`;
    const heatCaption=s.heating_feedback===true?(s.heating_observation?.estimated?"Heizschütz ein":"Ofen heizt"):s.heating_feedback===false?"Ofen aus":"Heizzustand unbekannt";
    const heatSource={power:"Aus gemessener Leistung",independent_feedback:"Unabhängige Heizrückmeldung",contactor:"Nach Schützstellung geschätzt",unknown:"Rückmeldung fehlt"}[s.heating_observation?.source]||"Rückmeldung fehlt";
    const disabled=!this.hass.user?.is_admin;
    const presetsDisabled=disabled||s.configuration_locked;
    const canStart=this.hass.user?.is_admin&&(s.operation_enabled||!s.start_errors.length);
    const presets=Array.from({length:p.preset_count},(_,i)=>p.preset_start_c+i*p.preset_step_c);
    const end=session?.deadlines.find(d=>d.purpose==="session_gap")?.due_at;
    const lock=s.configuration_locked?"Solltemperatur und Steigerung sind rechts auch während der Sitzung änderbar. Laufende Heizpausen und Kühlzeiten bleiben wirksam.":"Eine Temperaturtaste stellt die Solltemperatur ein und startet die Sauna.";
    const notices=s.issues.map(i=>`<p>${esc(i.message)}${i.action==="settings"?'<br><button data-action="configure">Einstellungen öffnen</button>':""}</p>`).join("");
    const alert=notices?`<div class="notice" role="alert">${notices}</div>`:"";
    const operation=`<button class="tile full ${s.operation_enabled?"stop":"primary"}" data-action="operation" ${canStart?"":"disabled"}>${s.operation_enabled?"Sauna ausschalten":"Sauna einschalten"}</button>`;
    const stateLine=`<div class="state-line"><strong class="phase" data-phase="${esc(s.phase)}">${phases[s.phase]||"Unbekannt"}</strong><span class="badge">${count} ${count===1?"Saunagang":"Saunagänge"}</span></div><p id="phase-detail" class="muted">${esc(activity)}</p>`;
    const dial=(reading,unit,caption,color,maximum,valid)=>`<svg class="dial" viewBox="0 0 300 235" role="img" aria-label="${esc(caption)}"><path d="M 54 195 A 120 120 0 1 1 246 195" fill="none" stroke="var(--divider-color,#444)" stroke-width="16" stroke-linecap="round"/><path d="M 54 195 A 120 120 0 1 1 246 195" fill="none" stroke="${valid?color:'#888'}" stroke-width="16" stroke-linecap="round" pathLength="100" stroke-dasharray="${Math.max(0,Math.min(100,(reading??0)/maximum*100))} 100"/><text class="reading" x="150" y="128" text-anchor="middle">${num(reading,1)} ${unit}</text><text class="caption" x="150" y="160" text-anchor="middle">${esc(caption)}</text></svg>`;
    const timers=`<div class="timer-strip"><div><small>Gezählte Heizzeit</small><strong>${duration(session?.heating.elapsed_seconds||0)}</strong><small>Grenze ${duration(s.heating_limit_seconds)}</small></div><div data-mechanical-timer="${timer.state}"><small>Mechanischer Ofentimer</small><strong>${timerText}</strong><small>${timerStatus}</small></div></div>`;
    const phaseTimer=s.phase_timer?`<div class="timer-strip" data-phase-timer="${esc(s.phase_timer.kind)}"><div><small>${esc(s.phase_timer.label)}</small><strong>${duration(s.phase_timer.seconds)}</strong></div></div>`:"";
    this.$("#current").innerHTML=`<h2 class="greeting">Servus ${esc(this.hass.user?.name||"")}</h2><div class="dashboard"><div class="card">${stateLine}<div class="row muted"><span>${heatCaption}</span><span>Tür ${{open:"offen",closed:"geschlossen"}[session?.timeline.door]||"unbekannt"}</span><span data-energy>${energyText}</span></div>${phaseTimer}<div class="tiles">${operation}${presets.map(v=>`<button class="tile" data-action="preset:${v}" ${presetsDisabled?"disabled":""}>Sauna ${num(v,1)} °C</button>`).join("")}</div><p class="muted">${lock}</p>${alert}</div><div class="card gauge-card"><div class="gauges"><div><h2>Temperatur oben</h2>${dial(value("upper","temperature"),"°C",heatCaption,"#ff6b4a",120,quality("upper","temperature")==="current")}<p class="muted">${qualityText("upper","temperature")}</p></div><div><h2>Luftfeuchte oben</h2>${dial(value("upper","humidity"),"%","Relative Luftfeuchte","#42a5ff",100,quality("upper","humidity")==="current")}<p class="muted">${qualityText("upper","humidity")}</p></div></div><div class="temperature-choice"><label for="target">Solltemperatur</label><input id="target" aria-label="Solltemperatur" type="number" step="0.5" value="${s.target_temperature??""}" ${disabled?"disabled":""}><span>°C</span><button data-action="target" ${disabled?"disabled":""}>Übernehmen</button></div><p class="muted center">${p.final_temperature_c!=null?`Aktuelle Solltemperatur: ${num(s.target_temperature)} °C · Steigerung bis ${num(p.final_temperature_c)} °C`:'Gewünschte Temperatur während des Saunagangs.'}</p><details><summary>Temperatur von Gang zu Gang steigern</summary><p class="muted">Nach jedem gezählten Saunagang wird die aktuelle Solltemperatur um die gewählte Schrittweite erhöht. Eine neue Solltemperatur gilt sofort als Ausgangspunkt für weitere Steigerungen; alle Ablauf- und Heizsperren bleiben wirksam.</p><div class="row"><label class="field">Endtemperatur (°C)<input id="progression-end" type="number" step="any" value="${p.final_temperature_c??''}" ${disabled?'disabled':''}></label><label class="field">Erhöhung je Gang (°C)<input id="progression-step" type="number" step="any" value="${p.temperature_increase_c}" ${disabled?'disabled':''}></label><button data-action="progression" ${disabled?'disabled':''}>Steigerung übernehmen</button></div><p class="muted">Endtemperatur leer lassen, um wieder konstant zu heizen.</p></details></div></div>`;
    const deadlines={confirmation:"Aufgussbestätigung",person_opportunity:"Wartezeit auf Personenerkennung",after_run:"Nachlauf",forced_cooling:"Zwangskühlung",session_gap:"Ende der Saunasitzung"};
    this.$("#details").innerHTML=`<div class="card hero">${stateLine}${operation}${timers}${alert}</div><div class="detail-grid"><div class="card"><h2>Temperatur und Heizregelung</h2><dl><dt>Aktuelle Solltemperatur</dt><dd>${num(s.target_temperature,1)} °C</dd><dt>Temperaturprogramm</dt><dd>${p.final_temperature_c!=null?`${num(p.target_temperature_c)} → ${num(p.final_temperature_c)} °C · ${num(p.temperature_increase_c)} °C je Gang`:"Konstant"}</dd><dt>Bereitschaftstemperatur</dt><dd data-readiness>${num(s.readiness_target,1)} °C</dd><dt>Wieder einschalten unter</dt><dd>${num(s.readiness_target==null?null:s.readiness_target-p.readiness_hysteresis_c,1)} °C</dd><dt>Heizzustand</dt><dd>${heatCaption}</dd><dt>Ermittlung der Heizzeit</dt><dd>${heatSource}</dd></dl><p>${esc(s.decision_text)}</p></div><div class="card"><h2>Messwerte und Verfügbarkeit</h2>${["upper","lower"].map(pos=>`<h3>${pos==="upper"?"Obere":"Untere"} Messposition</h3><p>${formatValue(pos,"temperature","°C")} · ${qualityText(pos,"temperature")}</p><p>${formatValue(pos,"humidity","% relative Luftfeuchte")} · ${qualityText(pos,"humidity")}</p>`).join("")}<p class="muted">Erkennung mit: ${s.detection_channels.map(p=>p==="upper"?"oberer Messposition":"unterer Messposition").join(" und ")||"noch keiner Messposition"}.</p></div><div class="card"><h2>Zeiten und Fristen</h2><dl>${(session?.deadlines||[]).map(d=>`<dt>${esc(deadlines[d.purpose]||"Laufende Frist")}</dt><dd>${remaining(d.due_at)} · bis ${when(d.due_at)}</dd>`).join("")}</dl><p>Mechanischer Ofentimer: ${timerText} · ${timerStatus}</p><p class="muted">${timer.ends_at?`Voraussichtlicher Ablauf: ${when(timer.ends_at)}. `:""}Die tatsächliche Stellung des Drehschalters wird nicht gemessen. Die Anzeige löst keine Steuerung aus.${timer.reset_pending?" Beim nächsten Start beginnt die Anzeige neu.":""}</p></div><div class="card"><h2>Energieverbrauch der Saunasitzung</h2><p data-energy>${energyText}</p><p>Ofenleistung für die Schätzung: ${num(p.nominal_power_kw,2)} kW</p><p class="muted">Gültige Leistungsmessungen ersetzen die Schätzung. Messlücken werden als geschätzter Anteil berücksichtigt.</p></div></div>`;
    if(progressionOpen)this.$("#current details").open=true;
    for(const [id,value] of Object.entries(this.draft||{}))if(this.$(`#${id}`))this.$(`#${id}`).value=value;
  }
  async changeTarget(value,start=false) {
    if(!Number.isFinite(value))throw Error("Gültige Solltemperatur eingeben");
    return this.updateParameters({target_temperature_c:value},start,true);
  }
  async updateParameters(parameters,start=false,partial=false) {
    const entry=this.entry;
    const saved=await this.api(`/${entry}/${partial?"temperature":"parameters"}`,"POST",parameters);
    parameters=saved.parameters;
    // A saved parameter is loaded by HA's single options listener. Do not start
    // against the previous runtime while reload is still in progress.
    let loaded=false;
    for(let attempt=0;attempt<100;attempt++){
      try {const state=await this.api(`/${entry}/state`);if(Object.keys(state.configuration.parameters).length===Object.keys(parameters).length&&Object.entries(parameters).every(([k,v])=>state.configuration.parameters[k]===v)){loaded=true;break;}}
      catch(error){if(error.status_code!==503)throw error;}
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    if(!loaded)throw Error("Parameter gespeichert, Neuladen noch nicht abgeschlossen. Bitte Status prüfen.");
    this.settingsEntry=null;this.draft=null;
    if(start)await this.api(`/${entry}/control`,"POST",{enabled:true});
    await this.refresh();
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
    this.$("#plots").innerHTML=`<div class="plot-panel"><h2 class="plot-title">${this.selected==="live"?"Verlauf der aktuellen oder letzten Saunasitzung":"Verlauf der ausgewählten Saunasitzung"}</h2><div class="legend top-legend"><span><i style="background:#ff6b4a"></i>Temperatur</span><span><i style="background:#42a5ff"></i>Luftfeuchte</span></div><div class="row position-select"><button data-action="position-upper" aria-pressed="${this.positions.has("upper")}">━━ Oben</button><button data-action="position-lower" aria-pressed="${this.positions.has("lower")}">┄┄ Unten</button></div><div class="plot-wrap">${this.chart(records,session,gangs)}<div id="tooltip" hidden></div></div><div class="legend"><span><i style="background:rgba(255,205,80,.95)"></i>Saunatür offen</span><span><i style="background:rgba(165,30,85,.95)"></i>Saunagang</span><span><i style="background:#f5f5f5"></i>Aufguss</span></div><div class="legend"><span><i style="background:rgba(255,150,35,.4)"></i>heizen</span><span><i style="background:rgba(38,125,82,.4)"></i>bereit</span><span><i style="background:rgba(58,125,155,.4)"></i>lüften</span><span><i style="background:#67829a"></i>Zwangskühlung</span></div><p class="muted plot-note">Durchgezogen: oben · gestrichelt: unten / vorläufiger Gang · schmaler Streifen: gezählte Heizzeit</p></div>`;
    this.$("#gangs").innerHTML=`<div class="card"><h2>Saunagänge</h2>${energySummary}${gangs.length?`<div class="scroll"><table><thead><tr><th>Gang</th><th>Beginn</th><th>Erkannt</th><th>Bestätigung</th><th>Dauer</th><th>Ende</th></tr></thead><tbody>${gangs.map((g,i)=>`<tr data-gang-id="${esc(g.gang_id)}" data-start="${esc(g.started_at)}"><td>${i+1} · ${g.infusion_events.length?"Bestätigt":"Vorläufig"}</td><td>${when(g.started_at)}</td><td>${when(g.detected_at)}</td><td>${g.infusion_events.length?when(g.infusion_events[0].detected_at):"Aufguss ausstehend"}</td><td>${duration((stamp(g.ended_at||session.ended_at||this.state.now)-stamp(g.started_at))/1000)}</td><td>${when(g.ended_at)}</td></tr>`).join("")}</tbody></table></div>`:'<p class="muted">Keine Saunagänge erkannt.</p>'}</div>`;
    const diagnostics=records.filter(r=>r.kind==="diagnostic");
    this.$("#event-list").innerHTML=`<div class="card"><details><summary>Ereignisse & Zuordnung (${t.processed.length})</summary><div class="scroll"><table><thead><tr><th>Ereignis</th><th>Zugeordnete Zeit</th><th>Erkennungszeit</th></tr></thead><tbody>${[...t.processed].reverse().map(e=>`<tr><td>${esc(events[e.kind]||e.kind)}</td><td>${when(e.effective_at)}</td><td>${when(e.detected_at)}</td></tr>`).join("")}</tbody></table></div></details>${diagnostics.length?`<details><summary>Historische Fehlerhinweise (${diagnostics.length})</summary>${diagnostics.map(r=>`<p><small>${when(r.received_at)}</small> ${esc(r.payload.messages?.join(" ")||"Keine Störung gemeldet.")}</p>`).join("")}</details>`:""}${t.retracted.length?`<p class="muted">${t.retracted.length} vorläufige Erkennung(en) aufgehoben. Diese werden nicht als Gänge gezählt.</p>`:""}</div>`;
    this.shadowRoot.querySelectorAll("#event-list details").forEach((d,i)=>d.open=!!openEvents[i]);
  }
  chart(records,session,gangs) {
    const [start,end]=this.window,W=1200,H=480,left=65,right=1135,top=18,bottom=435;
    const x=t=>left+(t-start)/(end-start)*(right-left);
    const raw=records.filter(r=>["measurement","source_snapshot"].includes(r.kind)).map(r=>r.payload);
    this.chartMeasurements=raw;
    const visible=raw.filter(m=>this.positions.has(m.position)&&m.quantity==="temperature"&&stamp(m.received_at)>=start&&stamp(m.received_at)<=end&&m.value!=null);
    const bounds=visible.reduce(([lo,hi],m)=>[Math.min(lo,m.value),Math.max(hi,m.value)],[Infinity,-Infinity]);
    const low=visible.length?Math.floor((bounds[0]-2)/10)*10:20,high=visible.length?Math.ceil((bounds[1]+2)/10)*10:100;
    const yT=v=>bottom-(v-low)/(high-low)*(bottom-top),yH=v=>bottom-v/40*(bottom-top);
    const interval=(a,b,klass,title,y=top,height=bottom-top)=>{const aa=Math.max(start,stamp(a)),bb=Math.min(end,stamp(b||session.ended_at||this.state.now));return bb>aa?`<rect class="${klass}" x="${x(aa).toFixed(2)}" y="${y}" width="${(x(bb)-x(aa)).toFixed(2)}" height="${height}"><title>${esc(title)}</title></rect>`:"";};
    let svg=`<svg class="chart session-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Sitzungsverlauf: Temperatur und Luftfeuchte beider Höhen"><defs><clipPath id="plot-clip"><rect x="${left}" y="${top}" width="${right-left}" height="${bottom-top}"/></clipPath></defs><g clip-path="url(#plot-clip)">`;
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
      const points=[];let bucket=[],key=null;
      const flush=()=>{if(!bucket.length)return;const keep=new Set([bucket[0],bucket.at(-1),bucket.reduce((a,b)=>a.value<b.value?a:b),bucket.reduce((a,b)=>a.value>b.value?a:b)]);points.push(...bucket.filter(p=>keep.has(p)));bucket=[];};
      for(const m of values){const time=stamp(m.received_at);if(time<start||time>end)continue;const k=Math.floor(x(time));if(k!==key||m.value==null){flush();key=k;}if(m.value==null)points.push(m);else bucket.push(m);}flush();
      let path="",last=null;const ttl=(session.configuration?.parameters||this.state.configuration.parameters).sensor_timeout_seconds*1000;
      for(const m of points){const time=stamp(m.received_at);if(m.value==null){last=null;continue;}path+=`${last==null||(ttl&&time-last>ttl)?"M":"L"}${x(time).toFixed(2)},${(quantity==="temperature"?yT(m.value):yH(m.value)).toFixed(2)} `;last=time;}
      svg+=`<path class="${position} ${quantity}" data-series="${position}_${quantity}" d="${path}"/>`;
    }
    svg+='</g>';
    for(let n=0;n<=8;n++){const f=n/8,yy=bottom-(bottom-top)*f,t=start+(end-start)*f;svg+=`<text class="axis-temperature" x="${left-10}" text-anchor="end" y="${yy+4}">${(low+(high-low)*f).toFixed(0)}</text><text class="axis-humidity" x="${right+10}" y="${yy+4}">${(40*f).toFixed(0)}</text><text text-anchor="middle" x="${x(t)}" y="${bottom+27}">${clock(t)}</text>`;}
    svg+=`<text class="axis-temperature" x="13" y="${H/2}">°C</text><text class="axis-humidity" x="${W-15}" y="${H/2}">%</text><line id="cursor" x1="0" x2="0" y1="${top}" y2="${bottom}" stroke="#eee" stroke-dasharray="3 3" visibility="hidden"/>`;
    return svg+'</svg>';
  }
  hoverChart(e) {
    const svg=e.target.closest("svg.session-chart"),rect=svg.getBoundingClientRect(),px=(e.clientX-rect.left)/rect.width*1200;
    if(px<65||px>1135)return;
    const time=this.window[0]+(px-65)/1070*(this.window[1]-this.window[0]);
    const rows=[];for(const p of this.positions)for(const q of ["temperature","humidity"]){
      const nearest=this.chartMeasurements.filter(m=>m.position===p&&m.quantity===q).reduce((a,b)=>!a||Math.abs(stamp(b.received_at)-time)<Math.abs(stamp(a.received_at)-time)?b:a,null);
      if(nearest)rows.push(`<span style="color:${q==="temperature"?'#e25d40':'#2f8bde'}">${q==="temperature"?"Temperatur":"Luftfeuchte"} ${p==="upper"?"oben":"unten"}</span><br>${nearest.value==null?"Nicht verfügbar":`${nearest.value.toLocaleString("de-DE")} ${q==="temperature"?"°C":"%"}`} · ${when(nearest.received_at)}`);
    }
    const tip=this.$("#tooltip");tip.innerHTML=`<strong>${when(time)}</strong><br>${rows.join("<br>")}`;tip.hidden=false;
    tip.style.left=`${Math.max(0,Math.min(rect.width-290,e.clientX-rect.left+12))}px`;tip.style.top=`${Math.max(0,e.clientY-rect.top-90)}px`;
    const cursor=this.$("#cursor");cursor.setAttribute("x1",px);cursor.setAttribute("x2",px);cursor.setAttribute("visibility","visible");
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
      ["Aufgusserkennung","infusion_temperature_delta","Temperaturänderung · °C"]];
    const parameters=this.shown.session.configuration?.parameters||this.state.configuration.parameters;
    const thresholds=(metric,position)=>{
      if(metric==="door_temperature_slope")return [parameters.door_open_slope,parameters.door_heating_slope,parameters.door_close_slope];
      if(metric==="door_humidity_delta")return [-parameters[`door_open_humidity_${position}`]];
      if(metric.startsWith("infusion_"))return [parameters[metric.replace("_delta","")]];
      return [parameters[metric.replace("_slope",`_${position}`)]];
    };
    let html='<div class="notice">Kontrollansicht des tatsächlich laufenden Detektors. Gestrichelte Linien zeigen die für diese Saunasitzung gespeicherten Schwellen. Ein Grenzübertritt allein ist noch kein Ereignis: Kontext, verfügbare Sensoren und Bestätigungszeiten wirken zusätzlich.</div>';
    if(!traces.length){this.$("#detection-plots").innerHTML=html+'<p>Für diese Saunasitzung liegen keine gespeicherten Erkennungsverläufe vor.</p>';return;}
    const [start,end]=this.window,x=t=>50+(stamp(t)-start)/(end-start)*900;
    for(const [group,metric,label] of groups){
      const vals=traces.flatMap(t=>Object.values(t.metrics).map(m=>m[metric])).filter(v=>v!=null),lines=[...thresholds(metric,"upper"),...thresholds(metric,"lower")];
      const [min,max]=[...vals,...lines].reduce(([lo,hi],v)=>[Math.min(lo,v),Math.max(hi,v)],[0,0]);
      const lo=min-1,hi=max+1,y=v=>165-(v-lo)/(hi-lo)*140;
      let chart='<svg class="chart detector-chart" viewBox="0 0 1000 200" role="img" aria-label="'+esc(group+' '+label)+'">';
      for(const pos of ["upper","lower"]){
        let path="",last=null;
        for(const t of traces){const v=t.metrics[pos]?.[metric],time=stamp(t.at);if(v==null||time<start||time>end)continue;path+=`${last==null||time-last>parameters.person_step_seconds*1000?"M":"L"}${x(t.at).toFixed(2)},${y(v).toFixed(2)} `;last=time;}
        chart+=`<path class="${pos} ${pos==="upper"?"temperature":"humidity"}" data-series="detector_${metric}_${pos}" d="${path}"/>`;
        for(const v of thresholds(metric,pos))chart+=`<line x1="50" x2="950" y1="${y(v)}" y2="${y(v)}" stroke="${pos==="upper"?'#ff6b4a':'#42a5ff'}" stroke-dasharray="4 5"><title>Schwelle ${pos==="upper"?"oben":"unten"}: ${num(v,2)}</title></line>`;
      }
      for(let n=0;n<=4;n++){const v=lo+(hi-lo)*n/4,t=start+(end-start)*n/4;chart+=`<text x="4" y="${y(v)+4}">${num(v,1)}</text><text text-anchor="middle" x="${x(t)}" y="192">${clock(t)}</text>`;}
      chart+='</svg>';html+=`<div class="plot-panel"><h3>${group} · ${label}</h3><p class="muted">Orange: oben · Blau: unten</p>${chart}</div>`;
    }
    html+='<div class="card"><h2>Erkennungsbedingungen und Bestätigung</h2><div class="scroll"><table><thead><tr><th>Zeit</th><th>Bedingungen erfüllt</th><th>Bestätigungszeiten</th><th>Ausgelöste Signale</th></tr></thead><tbody>'+traces.filter(t=>t.signals.length).map(t=>`<tr><td>${when(t.at)}</td><td>${esc(Object.entries(t.conditions).filter(([,v])=>v).map(([k])=>signalText[k]||"Erkennungsbedingung").join(", "))}</td><td>${esc(Object.entries(t.holds).map(([k,v])=>`${signalText[k]||"Signal"}: ${num(v)} s`).join(" · "))}</td><td>${esc(t.signals.map(k=>events[k]||k).join(", "))}</td></tr>`).join("")+'</tbody></table></div></div>';
    this.$("#detection-plots").innerHTML=html;
  }
  drawSettings() {
    const state=this.state;
    if(this.settingsEntry!==this.entry){
      const field=d=>`<label class="field">${esc(d.label)} (${esc(d.unit)})<input type="number" name="${esc(d.key)}" aria-describedby="help-${esc(d.key)}" step="${d.integer?1:"any"}" min="${d.minimum??0}" max="${d.maximum}" value="${esc(state.configuration.parameters[d.key]??"")}" ${d.optional?"":"required"}><small id="help-${esc(d.key)}">${esc(d.description)}</small></label>`;
      const groups={temperature:"Temperatur und Heizregelung",operation:"Saunasitzung, Saunagänge und Kühlung",monitoring:"Messwerte und Störungsüberwachung",timer:"Ofentimer und Energieverbrauch",display:"Temperaturtasten in der Übersicht"};
      const fields=Object.entries(groups).map(([key,title])=>`<section class="settings-group"><h2>${title}</h2><div class="forms">${state.parameters.filter(d=>d.group===key&&!d.expert).map(field).join("")}</div></section>`).join("");
      this.$("#settings").innerHTML=`<div class="card"><h2>Einstellungen</h2><p id="configuration-lock" class="muted"></p><form><fieldset id="parameters">${fields}<details><summary>Experteneinstellungen zur Erkennung</summary><p class="muted">Diese Werte verändern die Erkennung von Türöffnungen, Personen und Aufgüssen. Die Erkennungskontrolle zeigt ihre Wirkung.</p><div class="forms">${state.parameters.filter(d=>d.expert).map(field).join("")}</div></details><button type="submit" class="primary">Einstellungen speichern</button></fieldset></form><div class="row"><a href="/config/integrations/integration/ha_sauna">Sensoren und Geräte zuordnen</a></div></div><div class="card"><h2>Protokollierung</h2><p class="muted">Im Home-Assistant-Protokoll unter custom_components.ha_sauna. Die Stufe ist auch während einer Saunasitzung änderbar. Das Sitzungsarchiv bleibt unabhängig davon vollständig.</p><div class="row"><label for="log-level">Protokollstufe</label><select id="log-level"><option value="ERROR">ERROR · Fehler</option><option value="INFO">INFO · Betriebsereignisse (Standard)</option><option value="DEBUG">DEBUG · Detaillierte Diagnose</option></select><button data-action="logging">Übernehmen</button></div><p class="muted">INFO enthält Fehler, Warnungen, Zustandswechsel und Schaltbefehle. DEBUG ergänzt Messwerte und Erkennungsprüfungen.</p><a href="/config/logs">Home-Assistant-Protokoll öffnen</a></div><div class="card"><h2>Sitzungsarchiv</h2><p class="muted">Alle empfangenen Messwerte, Sitzungsverläufe und Ereigniszuordnungen herunterladen.</p><button data-action="export">Archiv als ZIP herunterladen</button></div>`;
      this.$("#log-level").value=state.configuration.log_level;
      this.settingsEntry=this.entry;
    }
    this.$("#parameters").disabled=!this.hass.user?.is_admin;
    for(const d of state.parameters){const input=this.$(`#parameters input[name="${d.key}"]`);input.disabled=!this.hass.user?.is_admin||(state.configuration_locked&&!d.live_editable);if(d.key==="target_temperature_c"&&!input.dataset.edited&&this.shadowRoot.activeElement!==input)input.value=state.target_temperature;}
    this.$("#log-level").disabled=!this.hass.user?.is_admin;
    this.$('[data-action="logging"]').disabled=!this.hass.user?.is_admin;
    this.$("#configuration-lock").textContent=state.configuration_locked?"Solltemperatur, Erhöhung je Gang und Endtemperatur sind änderbar. Andere Einstellungen bleiben bis zum Ende der Saunasitzung gesperrt. Laufende Fristen und Heizsperren bleiben immer wirksam.":"Alle erforderlichen Einstellungen haben Standardwerte. Änderungen der Grundeinstellungen gelten für die nächste Saunasitzung.";
  }
  async saveSettings() {
    this.message(null);
    const values={...this.state.configuration.parameters};for(const [key,value] of new FormData(this.$("form"))){if(value!=="")values[key]=Number(value);else delete values[key];}
    await this.updateParameters(values);
  }
  async action(action) {
    this.message(null);
    if(action==="configure"){await this.action("details");return this.action("settings");}
    if(action==="logging"){await this.api(`/${this.entry}/logging`,"POST",{level:this.$("#log-level").value});await this.refresh();return;}
    if(action==="menu"){this.dispatchEvent(new CustomEvent("hass-toggle-menu",{bubbles:true,composed:true}));return;}
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
    if(action.startsWith("preset:"))return this.changeTarget(Number(action.slice(7)),true);
    if(action==="progression"){
      const parameters={temperature_increase_c:Number(this.$("#progression-step").value),final_temperature_c:this.$("#progression-end").value===""?null:Number(this.$("#progression-end").value)};
      if(this.draft&&Object.hasOwn(this.draft,"target"))parameters.target_temperature_c=Number(this.$("#target").value);
      await this.updateParameters(parameters,false,true);return;
    }
    if(action==="target")return this.changeTarget(Number(this.$("#target").value));
    if(action==="operation"){await this.api(`/${this.entry}/control`,"POST",{enabled:!this.state.operation_enabled});await this.refresh();}
    if(action==="zoom-in")this.zoom=Math.min(256,this.zoom*2);
    if(action==="zoom-out")this.zoom=Math.max(1,this.zoom/2);
    if(action==="reset-zoom"){this.zoom=1;this.pan=100;this.$("#pan").value="100";}
    if(action.includes("zoom"))this.drawHistory();
    if(action.startsWith("position-")){const p=action.slice(9);this.positions.has(p)?this.positions.delete(p):this.positions.add(p);this.drawHistory();}
    if(action==="export"){
      const signed=await this.hass.callWS({type:"auth/sign_path",path:`/api/ha_sauna/${this.entry}/export`});
      const link=document.createElement("a");link.href=signed.path;link.download="ha-sauna-archive.zip";this.shadowRoot.append(link);link.click();link.remove();
    }
  }
}
if(!customElements.get("ha-sauna-panel"))customElements.define("ha-sauna-panel",SaunaPanel);
