#!/usr/bin/env python3
"""Render precomputed proxy-form analysis as one offline HTML document.

Only the standard library is used. The renderer does not import the product,
replay input, fit curves, or calculate measurement features. All plotted values
come from the supplied analysis JSON files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>Proxyformen · R01–R04</title>
<style>
:root{color-scheme:light;--ink:#192b3b;--muted:#596b79;--line:#d5dfe5;--paper:#fff;--bg:#eef2f4;--upper:#007c86;--lower:#b75611;--selection:#f7dc85;--accent:#205bb0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input,select{font:inherit;color:inherit}button,select,input[type=search],input[type=number]{border:1px solid #b5c4ce;border-radius:5px;background:white;min-height:32px;padding:4px 9px}button{cursor:pointer}button:hover{background:#eaf1fa;border-color:#628ab9}button:disabled{opacity:.45;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #78a6ed;outline-offset:2px}input[type=checkbox]{accent-color:var(--accent)}h1,h2,h3,h4,p{margin:0}h1{font-size:22px;letter-spacing:-.4px}h2{font-size:17px}h3{font-size:15px}h4{font-size:13px;margin:12px 0 5px}header{padding:18px 22px 12px;background:var(--paper);border-bottom:1px solid var(--line)}header p{color:var(--muted);max-width:1160px;margin-top:6px}.topline,.controls,.legend,.navigation,.case-heading{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.topline{justify-content:space-between}.pill{background:#e9eef4;border:1px solid #d8e0e8;border-radius:20px;padding:2px 9px;font-size:12px}.unresolved{background:#fff3ca;border-color:#e7cd79}.controls{margin-top:13px}.controls label{display:flex;align-items:center;gap:5px}.controls .spacer{flex:1}.controls input[type=number]{width:91px}.small{font-size:12px;color:var(--muted)}main{display:grid;grid-template-columns:310px minmax(0,1fr);gap:14px;padding:14px;max-width:1900px;margin:auto}aside,.content{min-width:0}.box,.plot-card{background:var(--paper);border:1px solid var(--line);border-radius:8px}.box{padding:13px;margin-bottom:12px}aside{align-self:start;position:sticky;top:12px;max-height:calc(100vh - 24px);display:flex;flex-direction:column}aside .box{margin-bottom:0;min-height:0;display:flex;flex-direction:column}#search{width:100%;margin:10px 0 7px}#reasonFilter{width:100%;margin-bottom:7px}.case-list{overflow:auto;min-height:120px;max-height:calc(100vh - 300px);margin:8px -5px 0;padding:0 5px}.case-button{display:block;width:100%;text-align:left;padding:9px;margin-bottom:7px;border-color:var(--line);font-size:13px}.case-button[aria-current=true]{background:#edf4ff;border:2px solid var(--accent);padding:8px}.case-button .meta{display:flex;justify-content:space-between;gap:7px;color:var(--muted);font-size:11px;margin-bottom:3px}.case-button .question{display:block}.navigation{justify-content:space-between;margin-top:9px;gap:5px}.navigation button{min-width:72px}.legend{font-size:12px;color:var(--muted);gap:15px;margin-bottom:8px}.legend b{font-weight:600}.upper{color:var(--upper)}.lower{color:var(--lower)}.legend-symbol{font-size:16px;vertical-align:-1px;margin-right:4px}.plots{display:grid;gap:10px}.plot-card{overflow:hidden}.plot-title{display:flex;align-items:center;justify-content:space-between;padding:9px 12px 0;gap:9px}.plot-title h3{font-size:14px}.plot-title small{color:var(--muted);font-size:11px}.canvas-wrap{position:relative}.canvas-wrap canvas{display:block;width:100%;height:198px;touch-action:pan-y;cursor:crosshair}.derivative{border-top:1px solid #e6edf1}.derivative canvas{height:112px}.derivative[hidden]{display:none}.derivative .derivative-title{font-size:11px;color:var(--muted);position:absolute;left:68px;top:4px;pointer-events:none;background:#ffffffce;padding-right:6px}.range-note{display:flex;justify-content:space-between;gap:10px;margin:8px 0 12px;color:var(--muted);font-size:12px}.readout{overflow:auto;margin-top:12px}.readout table{font-size:12px;white-space:nowrap}.readout-head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:7px}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid #e5ebef;padding:5px 7px;vertical-align:top}th{font-size:11px;color:var(--muted);font-weight:600}td:first-child,th:first-child{padding-left:0}td:last-child,th:last-child{padding-right:0}.readout td.number{font-variant-numeric:tabular-nums}.case-detail{margin-top:12px}.case-heading{justify-content:space-between;margin-bottom:10px}.case-question{font-size:17px;line-height:1.4;font-weight:600;margin-bottom:9px}.case-subtitle{font-size:12px;color:var(--muted);margin-bottom:10px}.interpretations{padding-left:20px;margin:5px 0 10px}.interpretations li{margin:5px 0}.reasons{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}.tag{font-size:11px;padding:2px 6px;border:1px solid #d5dfe8;border-radius:4px;background:#f5f8fa}details{margin-top:10px}summary{cursor:pointer;font-weight:600;font-size:13px}pre{background:#f3f6f8;border:1px solid #e1e8ee;border-radius:5px;padding:10px;white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;max-height:310px;overflow:auto;margin:7px 0 0}.evidence{overflow:auto;max-height:290px}.empty{padding:20px 6px;color:var(--muted);font-size:13px}.comparison{padding:10px 0;border-top:1px solid var(--line)}.comparison:first-child{border-top:0}.comparison-title{display:flex;gap:6px;justify-content:space-between;align-items:start}.comparison-metrics{display:flex;gap:16px;font-variant-numeric:tabular-nums;margin:5px 0;color:var(--muted);font-size:12px}.case-link{border:0;background:#eaf0f9;padding:1px 5px;min-height:24px;font-size:12px;margin:3px 4px 0 0;color:#215894}.notice{padding:9px 11px;border-left:3px solid #c09a32;background:#fff9e8;font-size:12px;color:#625025;margin:8px 0 12px}.error{padding:20px;color:#922a20;background:white}.metadata{margin-top:12px}.metadata>details{margin-top:0}.key-values{margin:6px 0}.key-values dt{font-weight:600;font-size:12px;margin-top:6px}.key-values dd{margin:2px 0 0;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}.offline{white-space:nowrap;color:var(--muted);font-size:12px}#sessionSummary{font-size:12px;color:var(--muted);margin-top:5px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@media(max-width:1050px){main{grid-template-columns:250px minmax(0,1fr);gap:10px;padding:10px}.controls .spacer{display:none}.readout table{font-size:11px}header{padding:14px}.canvas-wrap canvas{height:180px}}@media(max-width:760px){main{display:flex;flex-direction:column}aside{position:static;max-height:none}.case-list{max-height:210px;min-height:0}.controls{gap:8px}.controls input[type=number]{width:75px}.legend{gap:9px}.canvas-wrap canvas{height:185px}h1{font-size:20px}.range-note{display:block}.range-note span{display:block}.topline{align-items:start}.box{padding:11px}}
</style>
</head>
<body>
<header>
  <div class="topline"><h1>Proxyformen · Replay R01–R04</h1><span class="offline">Eine Datei · vollständig offline</span></div>
  <p>Temperatur, relative und berechnete absolute Feuchte mit vorhandenen Ableitungen. Die markierten Fälle sind <strong>ungeklärte Formbefunde</strong>: Kurvenformen allein bestätigen kein physisches Ereignis. Die Fallauswahl ist keine vollständige Ereignisliste; die Gesamtansicht enthält sämtliche vorhandenen Kurvenpunkte.</p>
  <div class="controls">
    <label>Sitzung <select id="sessionSelect" aria-label="Sitzung"></select></label>
    <label><input id="showD1" type="checkbox"> D1</label>
    <label><input id="showD2" type="checkbox"> D2</label>
    <label>Skala <select id="scaleSelect" aria-label="Fitfenster in Sekunden"></select></label>
    <label>D1 <select id="modelSelect" aria-label="Vorhandenes D1-Fitmodell"><option value="linear">linear</option><option value="quadratic">quadratisch</option></select></label>
    <span class="spacer"></span>
    <label>Von <input id="rangeStart" type="number" step="0.1" aria-label="Zeitraum von, Minuten"></label>
    <label>bis <input id="rangeEnd" type="number" step="0.1" aria-label="Zeitraum bis, Minuten"> min</label>
    <button id="applyRange" type="button">Zeitraum</button>
    <button id="zoomIn" type="button" title="Zeitraum um die Mitte halbieren">+</button>
    <button id="zoomOut" type="button" title="Zeitraum um die Mitte verdoppeln">−</button>
    <button id="fullRange" type="button">Gesamt</button>
  </div>
  <div id="sessionSummary"></div>
</header>
<main id="application">
  <aside>
    <section class="box" aria-labelledby="caseListTitle">
      <h2 id="caseListTitle">Ungeklärte Fälle <span id="caseCount" class="small"></span></h2>
      <input id="search" type="search" placeholder="Frage, Hinweis oder Quelle suchen" aria-label="Fälle durchsuchen">
      <select id="reasonFilter" aria-label="Nach Auswahlgrund filtern"><option value="">Alle Auswahlgründe</option></select>
      <label class="small"><input id="allSessions" type="checkbox"> Fälle aller Sitzungen</label>
      <nav class="navigation" aria-label="Fallnavigation"><button id="previousCase" type="button">← Zurück</button><span id="casePosition" class="small"></span><button id="nextCase" type="button">Weiter →</button></nav>
      <div id="caseList" class="case-list"></div>
    </section>
  </aside>
  <div class="content">
    <div class="legend" aria-label="Zeichenerklärung"><span class="upper"><b>Oben</b></span><span class="lower"><b>Unten</b></span><span><span class="legend-symbol">●</span>Originalsample T/RH</span><span><span class="legend-symbol">◇</span>Initialstand, Meldezeit rekonstruiert</span><span>┄ Berechnete AH / Ableitung</span><span>× ungültiger Wert</span></div>
    <div class="plots" id="plots"></div>
    <div class="range-note"><span id="rangeDescription"></span><span>Ziehen: Zeitfenster · Doppelklick: Gesamt · Klick: Ablesezeit fixieren</span></div>
    <section class="box readout" aria-labelledby="readoutTitle">
      <div class="readout-head"><h3 id="readoutTitle">Werte am Pointer</h3><span id="cursorTime" class="small">Pointer über eine Kurve bewegen.</span></div>
      <div id="readoutBody" class="small">Gezeigt werden die nächsten vorhandenen Punkte samt ihrer eigenen Zeit, ohne Interpolation.</div>
    </section>
    <section class="box case-detail" id="caseDetail" aria-label="Ausgewählter ungeklärter Fall"></section>
    <section class="box" id="comparisonSection" hidden aria-labelledby="comparisonTitle">
      <div class="case-heading"><h2 id="comparisonTitle">Beitrag der Messgrößen</h2><span id="comparisonCount" class="small"></span></div>
      <p class="small">Vorab berechnete Vergleiche aus feature_comparison.json. Abstände beschreiben die hinterlegte Formmetrik; sie sind keine Ereigniswahrscheinlichkeit.</p>
      <div id="comparisonBody"></div>
      <details id="comparisonLimitations"><summary>Vergleichsmethode und Grenzen</summary><div id="comparisonMetadata"></div></details>
    </section>
    <section class="box metadata"><details><summary>Datengrundlage, Auswahlregeln und Grenzen</summary><div id="metadata"></div></details></section>
  </div>
</main>
<noscript><p class="error">Die eingebettete Kurvenansicht benötigt JavaScript. Alle Analysewerte sind direkt in dieser Datei enthalten; eine Netzwerkverbindung wird nicht verwendet.</p></noscript>
<script id="analysis-data" type="application/json">__ANALYSIS_DATA__</script>
<script>
"use strict";
(() => {
  const data = JSON.parse(document.getElementById("analysis-data").textContent);
  const corpus = data.corpus || {};
  const sessions = Array.isArray(corpus.sessions) ? corpus.sessions : [];
  const cases = Array.isArray(data.cases?.cases) ? data.cases.cases : [];
  const byId = new Map(cases.map(item => [String(item.id), item]));
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const json = value => JSON.stringify(value, null, 2);
  const raw = value => `<pre>${escape(json(value))}</pre>`;
  const text = value => typeof value === "string" ? value : json(value);
  const number = (value, digits = 3) => finite(value) ? value.toLocaleString("de-DE", {maximumFractionDigits:digits}) : "—";
  const time = seconds => {
    if (!finite(seconds)) return "—";
    const sign = seconds < 0 ? "−" : "";
    const absolute = Math.abs(seconds);
    const minutes = Math.floor(absolute / 60);
    return `${sign}${minutes}:${(absolute % 60).toFixed(1).padStart(4,"0")}`;
  };
  const reasonText = reason => {
    if (typeof reason === "string") return reason;
    if (reason && typeof reason === "object") return String(reason.label || reason.code || reason.kind || reason.type || reason.quantity || "Weiteres Auswahlindiz");
    return String(reason ?? "Weiteres Auswahlindiz");
  };
  const reasons = item => Array.isArray(item.selection_reasons) ? item.selection_reasons.map(reasonText) : item.selection_reasons ? [reasonText(item.selection_reasons)] : [];
  const filterReasons = item => {
    const values=Array.isArray(item.selection_reasons) ? item.selection_reasons : [item.selection_reasons];
    return values.filter(Boolean).map(reason => typeof reason === "string" ? reason : String(reason.form || reason.kind || reasonText(reason)));
  };
  const question = item => item.question ? text(item.question) : "Offene Frage in den Fallangaben prüfen.";
  const searchIndex = new Map(cases.map(item => [item,json(Object.fromEntries(Object.entries(item).filter(([key]) => !["curve_slices","curve_evidence"].includes(key)))).toLocaleLowerCase("de-DE")]));
  const channels = [
    {key:"temperature",title:"Temperatur",unit:"°C",upper:"upper_temperature",lower:"lower_temperature",kind:"original"},
    {key:"humidity",title:"Relative Feuchte",unit:"%",upper:"upper_humidity",lower:"lower_humidity",kind:"original"},
    {key:"absolute",title:"Absolute Feuchte",unit:"g/m³",upper:"upper_absolute_humidity",lower:"lower_absolute_humidity",kind:"derived"}
  ];
  const channelNames = {upper_temperature:"T oben",lower_temperature:"T unten",upper_humidity:"RH oben",lower_humidity:"RH unten",upper_absolute_humidity:"AH oben",lower_absolute_humidity:"AH unten"};
  const state = {session:null,active:null,filtered:[],range:[0,1],extent:[0,1],cursor:null,pinned:false,drag:null,frame:null,cache:new Map()};
  const plots = [];
  const colors = ["#007c86", "#b75611"];

  function buildPlots() {
    for (const channel of channels) {
      const card = document.createElement("section");
      card.className = "plot-card";
      card.innerHTML = `<div class="plot-title"><h3>${channel.title} <span class="small">${channel.unit}</span></h3><small>${channel.kind === "derived" ? "AH aus vorhandenen T/RH-Paaren" : "Punkte: Originalsamples · Linien: Leseführung"}</small></div>`;
      for (const order of [0,1,2]) {
        const wrap = document.createElement("div");
        wrap.className = `canvas-wrap${order ? " derivative" : ""}`;
        if (order) {
          wrap.hidden = true;
          wrap.innerHTML = `<span class="derivative-title">D${order} · ${channel.unit}/${order === 1 ? "min" : "min²"}</span>`;
        }
        const canvas = document.createElement("canvas");
        canvas.setAttribute("role", "img");
        canvas.setAttribute("aria-label", `${channel.title}${order ? `, D${order}` : ""}, oben und unten über die Zeit`);
        wrap.appendChild(canvas);
        card.appendChild(wrap);
        const plot = {channel,order,wrap,canvas,bounds:null};
        plots.push(plot);
        canvas.addEventListener("pointermove", event => pointerMove(event, plot));
        canvas.addEventListener("pointerleave", () => {
          if (!state.pinned && !state.drag) {state.cursor = null; requestDraw();}
        });
        canvas.addEventListener("pointerdown", event => pointerDown(event, plot));
        canvas.addEventListener("pointerup", event => pointerUp(event, plot));
        canvas.addEventListener("pointercancel", () => {state.drag = null; requestDraw();});
        canvas.addEventListener("dblclick", () => {state.pinned = false; showFull();});
      }
      $("plots").appendChild(card);
    }
  }

  function sortedPoints(points) {
    return (Array.isArray(points) ? points : []).filter(point => finite(point.t)).slice().sort((a,b) => a.t - b.t);
  }

  function sessionData(session) {
    const id = String(session.id);
    if (state.cache.has(id)) return state.cache.get(id);
    const original = session.original_series || {};
    const ah = session.absolute_humidity_series || {};
    const series = {};
    for (const channel of channels) {
      for (const side of ["upper","lower"]) {
        const key = channel[side];
        series[key] = sortedPoints(channel.kind === "original" ? original[key] : ah[side]);
      }
    }
    const rows = sortedPoints(session.feature_rows);
    const scales = new Set();
    let start = 0;
    let end = finite(session.duration_s) ? session.duration_s : 0;
    for (const points of Object.values(series)) {
      if (points.length) {start = Math.min(start,points[0].t); end = Math.max(end,points.at(-1).t);}
    }
    for (const row of rows) for (const value of Object.values(row.channels || {})) for (const scale of Object.keys(value || {})) scales.add(scale);
    if (rows.length) end = Math.max(end, rows.at(-1).t);
    const result = {series,rows,scales:[...scales].sort((a,b) => Number(a)-Number(b)),extent:[start,Math.max(start+1,end)],derivatives:new Map()};
    state.cache.set(id,result);
    return result;
  }

  function derivativePoints(channel,order) {
    const current = sessionData(state.session);
    const scale = $("scaleSelect").value;
    const model = order === 2 ? "quadratic" : $("modelSelect").value;
    const key = `${channel}:${scale}:${model}:${order}`;
    if (!current.derivatives.has(key)) {
      current.derivatives.set(key,current.rows.map(row => {
        const quality = row.channels?.[channel]?.[scale];
        const fit = quality?.[model];
        return {t:row.t,value:fit?.[`d${order}`],valid:finite(fit?.[`d${order}`]),quality,model,scale};
      }));
    }
    return current.derivatives.get(key);
  }

  function seriesFor(plot,side) {
    return plot.order ? derivativePoints(plot.channel[side],plot.order) : sessionData(state.session).series[plot.channel[side]];
  }

  function setSession(id,reset=true) {
    const session = sessions.find(item => String(item.id) === String(id));
    if (!session) return;
    state.session = session;
    $("sessionSelect").value = String(session.id);
    const current = sessionData(session);
    state.extent = current.extent;
    const oldScale = $("scaleSelect").value;
    $("scaleSelect").replaceChildren();
    for (const scale of current.scales) $("scaleSelect").add(new Option(`${scale} s`,scale));
    if (!current.scales.length) $("scaleSelect").add(new Option("Keine Fitwerte",""));
    if (current.scales.includes(oldScale)) $("scaleSelect").value = oldScale;
    else if (current.scales.includes("60")) $("scaleSelect").value = "60";
    if (reset) {state.range = [...state.extent];state.cursor = null;state.pinned = false;}
    if (state.active && String(state.active.session_id) !== String(session.id)) state.active = null;
    const counts = Object.entries(current.series).map(([key,points]) => `${channelNames[key]}: ${points.length}`).join(" · ");
    $("sessionSummary").textContent = `${session.id} · ${number(session.duration_s / 60,1)} min Sitzungsdauer · Vorhandene Punkte: ${counts}`;
    updateFilters();
    renderCaseDetail();
    renderComparisons();
    syncRangeInputs();
    requestDraw();
  }

  function syncRangeInputs() {
    $("rangeStart").value = (state.range[0]/60).toFixed(2);
    $("rangeEnd").value = (state.range[1]/60).toFixed(2);
    $("rangeDescription").textContent = `${state.session?.id || ""} · ${time(state.range[0])} bis ${time(state.range[1])} · Dauer ${number((state.range[1]-state.range[0])/60,2)} min · Zeit seit Sitzungsbeginn`;
  }

  function setRange(start,end) {
    if (!finite(start) || !finite(end) || end <= start) return;
    const [minimum,maximum] = state.extent;
    start = Math.max(minimum,start);
    end = Math.min(maximum,end);
    if (end-start < .01) return;
    state.range = [start,end];
    syncRangeInputs();
    requestDraw();
  }

  function showFull() {setRange(...state.extent);}
  function zoom(factor) {
    const center = state.cursor !== null && state.cursor >= state.range[0] && state.cursor <= state.range[1] ? state.cursor : (state.range[0]+state.range[1])/2;
    const span = (state.range[1]-state.range[0])*factor;
    let start = center-span/2;
    let end = center+span/2;
    if (start < state.extent[0]) {end += state.extent[0]-start;start = state.extent[0];}
    if (end > state.extent[1]) {start -= end-state.extent[1];end = state.extent[1];}
    setRange(start,end);
  }

  function updateFilters() {
    const query = $("search").value.trim().toLocaleLowerCase("de-DE");
    const reason = $("reasonFilter").value;
    const anySession = $("allSessions").checked;
    state.filtered = cases.filter(item => (anySession || String(item.session_id) === String(state.session?.id)) && (!reason || filterReasons(item).includes(reason)) && (!query || searchIndex.get(item).includes(query)));
    renderCaseList();
  }

  function renderCaseList() {
    $("caseCount").textContent = `(${state.filtered.length})`;
    $("caseList").innerHTML = state.filtered.length ? state.filtered.map(item => `<button class="case-button" type="button" data-case="${escape(item.id)}" aria-current="${state.active?.id === item.id}"><span class="meta"><span>${escape(item.id)} · ${escape(item.session_id)}</span><span>${time(item.window?.start_s)}–${time(item.window?.end_s)}</span></span><span class="question">${escape(question(item))}</span></button>`).join("") : '<p class="empty">Keine Fälle für diese Auswahl.</p>';
    const index = state.filtered.findIndex(item => item.id === state.active?.id);
    $("casePosition").textContent = index >= 0 ? `${index+1} / ${state.filtered.length}` : `${state.filtered.length} Fälle`;
    $("previousCase").disabled = !state.filtered.length || index === 0;
    $("nextCase").disabled = !state.filtered.length || index === state.filtered.length-1;
  }

  function selectCase(id,focus=true) {
    const item = byId.get(String(id));
    if (!item) return;
    if (String(item.session_id) !== String(state.session?.id)) setSession(item.session_id);
    state.active = item;
    renderCaseList();
    renderCaseDetail();
    renderComparisons();
    if (focus && finite(item.window?.start_s) && finite(item.window?.end_s)) setRange(item.window.start_s,item.window.end_s);
    else requestDraw();
    const button = [...$("caseList").querySelectorAll("[data-case]")].find(element => element.dataset.case === String(id));
    if (button) button.scrollIntoView({block:"nearest"});
  }

  function moveCase(direction) {
    const index = state.filtered.findIndex(item => item.id === state.active?.id);
    const next = index < 0 ? (direction > 0 ? 0 : state.filtered.length-1) : index+direction;
    if (state.filtered[next]) selectCase(state.filtered[next].id);
  }

  function renderValue(value) {
    if (value === undefined || value === null) return '<span class="small">Keine Angaben.</span>';
    if (typeof value === "string") return `<p>${escape(value)}</p>`;
    if (Array.isArray(value)) return value.length ? `<ul class="interpretations">${value.map(item => `<li>${renderValue(item)}</li>`).join("")}</ul>` : '<span class="small">Keine Angaben.</span>';
    if (typeof value === "object") return `<dl class="key-values">${Object.entries(value).map(([key,item]) => `<dt>${escape(key)}</dt><dd>${renderValue(item)}</dd>`).join("")}</dl>`;
    return escape(value);
  }

  function bindLazyDetails(id,value) {
    const element=$(id);
    if (!element) return;
    element.addEventListener("toggle",() => {
      if (!element.open || element.dataset.loaded) return;
      const content=document.createElement("pre");
      content.textContent=json(value) ?? "Keine Angaben.";
      element.appendChild(content);
      element.dataset.loaded="true";
    });
  }

  function renderCaseDetail() {
    const item = state.active;
    if (!item) {$("caseDetail").innerHTML = '<h2>Ungeklärte Formbefunde</h2><p class="small" style="margin-top:8px">Ein Fall links öffnet seine konkrete Frage, konkurrierende Deutungen und Quellbezüge. Die Kurven zeigen zunächst die vollständige Sitzung.</p>';return;}
    const evidence = Array.isArray(item.curve_evidence) ? item.curve_evidence : [];
    const known = new Set(["id","session_id","status","window","selection_reasons","question","curve_evidence","competing_interpretations","original_references","curve_slices","curve_slices_reference","analyst_notes","analyst_hints","notes"]);
    const extra = Object.fromEntries(Object.entries(item).filter(([key]) => !known.has(key)));
    const notes = [item.analyst_notes,item.analyst_hints,item.notes].filter(value => value != null);
    $("caseDetail").innerHTML = `<div class="case-heading"><h2>${escape(item.id)} <span class="pill unresolved">ungeklärt</span></h2><button id="focusCase" type="button">Fallfenster</button></div>
      <p class="case-question">${escape(question(item))}</p>
      <p class="case-subtitle">${escape(item.session_id)} · Originalzeitbereich ${time(item.window?.start_s)} bis ${time(item.window?.end_s)} (${number(item.window?.start_s)}–${number(item.window?.end_s)} s)${finite(item.window?.center_s) ? ` · Zentrum ${time(item.window.center_s)}` : ""}</p>
      <div class="reasons">${reasons(item).map(reason => `<span class="tag">${escape(reason)}</span>`).join("")}</div>
      <h3>Konkurrierende Deutungen</h3>${renderValue(item.competing_interpretations)}
      <div class="notice">Die Auswahl bezeichnet eine offene Formfrage. Eine Deutung ist durch die Darstellung nicht bestätigt.</div>
      <h3>Analystenhinweise</h3>${notes.length ? notes.map(renderValue).join("") : '<p class="small">Keine zusätzlichen Hinweise im Fallobjekt; Auswahlgründe und Kurvenbelege stehen unten.</p>'}
      <details ${evidence.length ? "open" : ""}><summary>Vorhandene Kurvenbelege (${evidence.length})</summary><div class="evidence">${evidence.length ? `<table><thead><tr><th>Messgröße</th><th>Skala</th><th>Zeit</th><th>Merkmal</th><th>Wert</th><th>Rang</th></tr></thead><tbody>${evidence.map(row => `<tr><td>${escape(channelNames[row.channel] || row.channel)}</td><td>${number(row.scale_s)} s</td><td>${time(row.t)}</td><td>${escape(row.quantity)}</td><td>${number(row.value,6)}</td><td>${escape(row.rank ?? "—")}</td></tr>`).join("")}</tbody></table>` : '<p class="small">Keine Einzelbelege hinterlegt.</p>'}</div></details>
      <details id="caseSources"><summary>Originalquellen und Punktbezüge</summary></details>
      <details id="caseSlices"><summary>Kurvenausschnitt und Corpusbezug</summary></details>
      <details id="caseReasons"><summary>Vollständige Auswahlindizien und Stützbereiche</summary></details>
      <details id="caseEvidence"><summary>Vollständige Kurvenbelege mit Stützbereichen</summary></details>
      ${Object.keys(extra).length ? `<details><summary>Weitere Fallangaben</summary>${raw(extra)}</details>` : ""}`;
    bindLazyDetails("caseSources",item.original_references);
    bindLazyDetails("caseSlices",item.curve_slices_reference ?? item.curve_slices);
    bindLazyDetails("caseReasons",item.selection_reasons);
    bindLazyDetails("caseEvidence",item.curve_evidence);
    $("focusCase").addEventListener("click", () => setRange(item.window?.start_s,item.window?.end_s));
  }

  function comparisonCaseId(value) {
    if (typeof value === "string") return value;
    return value && typeof value === "object" ? String(value.id || value.case_id || "") : "";
  }

  function renderComparisons() {
    const document = data.feature_comparison;
    if (!document) return;
    $("comparisonSection").hidden = false;
    const all = Array.isArray(document.comparisons) ? document.comparisons : [];
    const activeId = state.active ? String(state.active.id) : null;
    const related = activeId ? all.filter(row => [comparisonCaseId(row.case_a),comparisonCaseId(row.case_b)].includes(activeId)) : all;
    $("comparisonCount").textContent = activeId ? `${related.length} zum gewählten Fall · ${all.length} insgesamt` : `${all.length} Vergleiche`;
    $("comparisonBody").innerHTML = related.length ? related.map(row => {
      const ids = [comparisonCaseId(row.case_a),comparisonCaseId(row.case_b)].filter(Boolean);
      const remaining = Object.fromEntries(Object.entries(row).filter(([key]) => !["id","base_family","added_family","case_a","case_b","base_distance","added_distance","added_contrasts"].includes(key)));
      return `<article class="comparison"><div class="comparison-title"><h3>${escape(text(row.base_family))} → ${escape(text(row.added_family))}</h3><span class="small">${escape(row.id)}</span></div><div>${ids.map(id => byId.has(id) ? `<button class="case-link" data-case="${escape(id)}" type="button">${escape(id)}</button>` : `<span class="small">${escape(id)}</span>`).join(" ")}</div><div class="comparison-metrics"><span>base_distance: <strong>${number(row.base_distance,6)}</strong></span><span>added_distance: <strong>${number(row.added_distance,6)}</strong></span></div>${renderValue(row.added_contrasts)}${Object.keys(remaining).length ? `<details><summary>Vergleichsangaben</summary>${raw(remaining)}</details>` : ""}</article>`;
    }).join("") : '<p class="empty">Für diesen Fall sind keine paarweisen Vergleiche hinterlegt.</p>';
    if (activeId) $("comparisonBody").insertAdjacentHTML("beforeend",'<button type="button" id="showAllComparisons" style="margin-top:10px">Alle Vergleiche anzeigen</button>');
    $("showAllComparisons")?.addEventListener("click", () => {
      const active = state.active;state.active = null;renderComparisons();state.active = active;
    });
    $("comparisonMetadata").innerHTML = raw(Object.fromEntries(Object.entries(document).filter(([key]) => key !== "comparisons")));
  }

  function renderMetadata() {
    const sections = [
      ["Methodik",corpus.methodology], ["Regeln und Grenzen",corpus.policy],
      ["Quellen und Provenienz",corpus.provenance], ["Quellgruppen",corpus.source_groups],
      ["Fallauswahl",data.cases?.selection_summary],
      ["Sitzungsabdeckung",sessions.map(session => ({id:session.id,analysis_window:session.analysis_window,provenance:session.provenance,counts:session.counts,coverage:session.coverage}))],
      ["Eingabedateien",data.input_files], ["HTML-Einbettung",data.html_embedding]
    ];
    $("metadata").innerHTML = `<p class="small" style="margin-top:8px">Alle Punkte und Fitwerte stammen aus den eingebetteten Analyseausgaben. Der Browser berechnet weder absolute Feuchte noch Fits oder Ableitungen. D1 wird pro Minute, D2 pro Minute² dargestellt. Beidseitige Fits verwenden zurückliegende und folgende Punkte und sind retrospektiv. AH ist aus T und RH berechnet; diese Messgrößen sind keine unabhängigen Belege. Initialstände haben rekonstruierte Meldezeiten und sind keine zu diesem Zeitpunkt empfangenen Samples. Fehlende Werte erscheinen nicht als Null. Verbindungslinien dienen der Leseführung; die Ablesung liefert ausschließlich vorhandene Punkte.</p>${sections.filter(([,value]) => value !== undefined).map(([title,value]) => `<h4>${title}</h4>${renderValue(value)}`).join("")}`;
  }

  function lowerBound(points,t) {
    let start = 0, end = points.length;
    while (start < end) {const middle = (start+end) >>> 1;if (points[middle].t < t) start = middle+1;else end = middle;}
    return start;
  }

  function visibleIndices(points) {
    return [Math.max(0,lowerBound(points,state.range[0])-1),Math.min(points.length,lowerBound(points,state.range[1])+1)];
  }

  function nearest(points,t) {
    if (!points.length) return null;
    const index = lowerBound(points,t);
    if (!index) return points[0];
    if (index === points.length) return points.at(-1);
    return t-points[index-1].t <= points[index].t-t ? points[index-1] : points[index];
  }

  function niceStep(span,target) {
    const rawStep = span/target;
    const exponent = Math.pow(10,Math.floor(Math.log10(rawStep)));
    const normalized = rawStep/exponent;
    const fraction = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    return fraction*exponent;
  }

  function axisNumber(value,step) {
    if (value === 0) return "0";
    if (Math.abs(value) < .0001 || Math.abs(value) >= 1e6) return value.toExponential(1).replace("e-","e−");
    return number(value,Math.min(7,Math.max(0,-Math.floor(Math.log10(step)))));
  }

  function drawPlot(plot) {
    if (!state.session || plot.wrap.hidden) return;
    const canvas = plot.canvas;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    const ratio = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width*ratio) || canvas.height !== Math.round(height*ratio)) {canvas.width = Math.round(width*ratio);canvas.height = Math.round(height*ratio);}
    const context = canvas.getContext("2d");
    context.setTransform(ratio,0,0,ratio,0,0);
    context.clearRect(0,0,width,height);
    const bounds = {left:64,right:width-15,top:plot.order ? 24 : 13,bottom:height-27};
    plot.bounds = bounds;
    const [start,end] = state.range;
    const first = seriesFor(plot,"upper");
    const second = seriesFor(plot,"lower");
    let minimum = Infinity, maximum = -Infinity;
    for (const points of [first,second]) {
      const [from,to] = visibleIndices(points);
      for (let index=from;index<to;index++) {
        const point = points[index];
        if (point.t < start || point.t > end || !finite(point.value)) continue;
        minimum = Math.min(minimum,point.value);maximum = Math.max(maximum,point.value);
      }
    }
    const hasValues = finite(minimum);
    if (!hasValues) {minimum = -1;maximum = 1;}
    const padding = (maximum-minimum)*.09 || Math.max(Math.abs(maximum)*.025,.001);
    minimum -= padding;maximum += padding;
    const x = t => bounds.left+(t-start)/(end-start)*(bounds.right-bounds.left);
    const y = value => bounds.bottom-(value-minimum)/(maximum-minimum)*(bounds.bottom-bounds.top);
    context.font = "11px system-ui, sans-serif";
    context.textBaseline = "middle";
    context.fillStyle = "#657683";
    context.lineWidth = 1;
    const yStep = niceStep(maximum-minimum,plot.order ? 3 : 5);
    for (let value=Math.ceil(minimum/yStep)*yStep;value<=maximum+yStep*.001;value+=yStep) {
      const location = y(value);
      context.strokeStyle = Math.abs(value) < yStep*.0001 ? "#c6d1da" : "#eaf0f3";
      context.beginPath();context.moveTo(bounds.left,location);context.lineTo(bounds.right,location);context.stroke();
      context.textAlign = "right";context.fillText(axisNumber(Math.abs(value) < yStep*.0001 ? 0 : value,yStep),bounds.left-8,location);
    }
    const xStep = niceStep((end-start)/60,Math.max(2,Math.floor((bounds.right-bounds.left)/110)))*60;
    for (let value=Math.ceil(start/xStep)*xStep;value<=end+xStep*.001;value+=xStep) {
      const location = x(value);
      context.strokeStyle = "#eaf0f3";
      context.beginPath();context.moveTo(location,bounds.top);context.lineTo(location,bounds.bottom);context.stroke();
      context.fillStyle = "#657683";context.textAlign = "center";
      context.fillText(`${number(value/60,2)} min`,location,height-12);
    }
    context.save();
    context.beginPath();context.rect(bounds.left,bounds.top,bounds.right-bounds.left,bounds.bottom-bounds.top);context.clip();
    const active = state.active?.window;
    if (active && finite(active.start_s) && finite(active.end_s)) {
      context.fillStyle = "#f2ce6b29";
      context.fillRect(x(active.start_s),bounds.top,x(active.end_s)-x(active.start_s),bounds.bottom-bounds.top);
      context.strokeStyle = "#c49e3b88";context.setLineDash([3,3]);
      for (const position of [active.start_s,active.end_s]) {context.beginPath();context.moveTo(x(position),bounds.top);context.lineTo(x(position),bounds.bottom);context.stroke();}
      context.setLineDash([]);
    }
    [first,second].forEach((points,side) => {
      const [from,to] = visibleIndices(points);
      context.strokeStyle = colors[side];context.fillStyle = colors[side];
      context.lineWidth = plot.order ? 1.25 : 1.1;
      context.setLineDash(plot.order || plot.channel.kind === "derived" ? [4,3] : []);
      context.beginPath();
      let connected = false;
      for (let index=from;index<to;index++) {
        const point = points[index];
        const initial = point.origin === "initial_state";
        if (!finite(point.value) || point.valid === false || initial) {connected = false;continue;}
        if (!connected) context.moveTo(x(point.t),y(point.value));
        else context.lineTo(x(point.t),y(point.value));
        connected = true;
      }
      context.stroke();context.setLineDash([]);
      if (!plot.order) {
        context.beginPath();
        for (let index=from;index<to;index++) {
          const point = points[index];
          if (point.t < start || point.t > end || !finite(point.value) || point.valid === false || point.origin === "initial_state") continue;
          const px=x(point.t),py=y(point.value);
          if (plot.channel.kind === "derived") {context.moveTo(px,py-2);context.lineTo(px+2,py);context.lineTo(px,py+2);context.lineTo(px-2,py);context.closePath();}
          else {context.moveTo(px+1.65,py);context.arc(px,py,1.65,0,Math.PI*2);}
        }
        context.fill();
      }
      context.beginPath();
      for (let index=from;index<to;index++) {
        const point=points[index];
        if (point.t < start || point.t > end || !finite(point.value)) continue;
        const px=x(point.t),py=y(point.value);
        if (point.origin === "initial_state") {context.moveTo(px,py-4);context.lineTo(px+4,py);context.lineTo(px,py+4);context.lineTo(px-4,py);context.closePath();}
        else if (point.valid === false) {context.moveTo(px-3,py-3);context.lineTo(px+3,py+3);context.moveTo(px-3,py+3);context.lineTo(px+3,py-3);}
      }
      context.stroke();
    });
    if (state.cursor !== null && state.cursor >= start && state.cursor <= end) {
      context.strokeStyle = "#30475c99";context.setLineDash([2,3]);context.beginPath();context.moveTo(x(state.cursor),bounds.top);context.lineTo(x(state.cursor),bounds.bottom);context.stroke();context.setLineDash([]);
    }
    if (state.drag) {
      context.fillStyle = "#316fcb22";
      context.fillRect(x(Math.min(state.drag.start,state.drag.end)),bounds.top,Math.abs(x(state.drag.end)-x(state.drag.start)),bounds.bottom-bounds.top);
    }
    context.restore();
    if (!hasValues) {
      context.textAlign = "center";context.fillStyle = "#657683";
      context.fillText(plot.order ? "Keine vorhandenen Fitwerte in diesem Zeitraum" : "Keine numerischen Werte in diesem Zeitraum",(bounds.left+bounds.right)/2,(bounds.top+bounds.bottom)/2);
    }
  }

  function pointMetadata(point,derived) {
    const values = [];
    if (point.origin) values.push(point.origin === "initial_state" ? "Initialstand; Meldezeit rekonstruiert" : point.origin);
    if (point.source_index !== undefined) values.push(`Quellindex ${point.source_index}`);
    if (finite(point.reported_age_s)) values.push(`gemeldetes Alter ${number(point.reported_age_s,1)} s`);
    if (point.valid === false) values.push(`ungültig${point.invalid_reason ? `: ${text(point.invalid_reason)}` : ""}`);
    if (point.fit_eligible === false) values.push("nicht für Fits zugelassen");
    if (derived) {
      if (point.temperature_index !== undefined) values.push(`T #${point.temperature_index}`);
      if (point.humidity_index !== undefined) values.push(`RH #${point.humidity_index}`);
      if (finite(point.temperature_t)) values.push(`T bei ${time(point.temperature_t)}`);
      if (finite(point.humidity_t)) values.push(`RH bei ${time(point.humidity_t)}`);
      if (finite(point.pair_skew_s)) values.push(`Paarversatz ${number(point.pair_skew_s,1)} s`);
    }
    return values.join(" · ") || "Keine weiteren Punktangaben";
  }

  function renderReadout() {
    if (!state.session || state.cursor === null) {
      $("cursorTime").textContent = "Pointer über eine Kurve bewegen.";
      $("readoutBody").innerHTML = '<span class="small">Nächste vorhandene Punkte samt eigener Zeit; keine Interpolation.</span>';
      return;
    }
    $("cursorTime").textContent = `${time(state.cursor)} (${number(state.cursor,2)} s)${state.pinned ? " · fixiert" : ""}`;
    const rows = [];
    const current = sessionData(state.session);
    for (const channel of channels) for (const [side,color] of [["upper","upper"],["lower","lower"]]) {
      const key = channel[side];
      const point = nearest(current.series[key],state.cursor);
      if (!point) {rows.push(`<tr><td class="${color}">${channelNames[key]}</td><td colspan="4">Keine Punkte</td></tr>`);continue;}
      rows.push(`<tr><td class="${color}">${channelNames[key]}${channel.kind === "derived" ? " (berechnet)" : ""}</td><td class="number">${number(point.value,5)} ${channel.unit}</td><td class="number">${time(point.t)}</td><td class="number">${number(point.t-state.cursor,2)} s</td><td>${escape(pointMetadata(point,channel.kind === "derived"))}</td></tr>`);
    }
    for (const order of [1,2]) {
      if (!$(order === 1 ? "showD1" : "showD2").checked) continue;
      for (const channel of channels) for (const side of ["upper","lower"]) {
        const key = channel[side];
        const point = nearest(derivativePoints(key,order),state.cursor);
        const quality = point?.quality;
        const fit = quality?.[point?.model];
        const info = quality ? [`${point.model}, ${point.scale} s`, `n=${quality.n ?? "—"}`,`Spanne ${number(quality.span_s,1)} s`,`max. Lücke ${number(quality.max_gap_s,1)} s`,`beidseitig: ${quality.bilateral === true ? "ja" : quality.bilateral === false ? "nein" : "—"}`,`RMSE ${number(fit?.rmse,5)}`].join(" · ") : "Kein vorhandener Fit";
        rows.push(`<tr><td class="${side}">D${order} ${channelNames[key]}</td><td class="number">${number(point?.value,6)} ${channel.unit}/${order === 1 ? "min" : "min²"}</td><td class="number">${time(point?.t)}</td><td class="number">${point ? number(point.t-state.cursor,2) : "—"} s</td><td>${escape(info)}</td></tr>`);
      }
    }
    $("readoutBody").innerHTML = `<table><thead><tr><th>Messgröße</th><th>Vorhandener Wert</th><th>Punktzeit</th><th>Δ Pointer</th><th>Punkt- / Fitangaben</th></tr></thead><tbody>${rows.join("")}</tbody></table><p class="small" style="margin-top:7px">Jede Zeile zeigt ihren eigenen nächsten Punkt; Zeitversätze sind ausdrücklich angegeben. Lücken und fehlende Fits werden nicht interpoliert.</p>`;
  }

  function requestDraw() {
    if (state.frame !== null) return;
    state.frame = window.requestAnimationFrame(() => {
      state.frame = null;
      for (const plot of plots) {
        plot.wrap.hidden = plot.order > 0 && !$(plot.order === 1 ? "showD1" : "showD2").checked;
        drawPlot(plot);
      }
      renderReadout();
    });
  }

  function eventTime(event,plot) {
    if (!plot.bounds) return null;
    const rectangle=plot.canvas.getBoundingClientRect();
    const position=Math.max(plot.bounds.left,Math.min(plot.bounds.right,event.clientX-rectangle.left));
    return state.range[0]+(position-plot.bounds.left)/(plot.bounds.right-plot.bounds.left)*(state.range[1]-state.range[0]);
  }

  function pointerDown(event,plot) {
    if (event.button !== 0 || !plot.bounds) return;
    const rectangle=plot.canvas.getBoundingClientRect();
    const position=event.clientX-rectangle.left;
    if (position < plot.bounds.left || position > plot.bounds.right) return;
    const t=eventTime(event,plot);
    state.drag={start:t,end:t,x:event.clientX,plot};
    plot.canvas.setPointerCapture(event.pointerId);
  }

  function pointerMove(event,plot) {
    const t=eventTime(event,plot);
    if (t === null) return;
    if (state.drag?.plot === plot) state.drag.end=t;
    if (!state.pinned || state.drag) state.cursor=t;
    requestDraw();
  }

  function pointerUp(event,plot) {
    if (!state.drag || state.drag.plot !== plot) return;
    const drag=state.drag;
    state.drag=null;
    if (plot.canvas.hasPointerCapture(event.pointerId)) plot.canvas.releasePointerCapture(event.pointerId);
    if (Math.abs(event.clientX-drag.x) > 7) {
      state.pinned=false;
      setRange(Math.min(drag.start,drag.end),Math.max(drag.start,drag.end));
    } else {
      state.cursor=eventTime(event,plot);
      state.pinned=!state.pinned;
      requestDraw();
    }
  }

  if (!sessions.length) {
    $("application").innerHTML = '<p class="error">corpus.json enthält keine darstellbaren Sitzungen.</p>';
    return;
  }
  for (const session of sessions) $("sessionSelect").add(new Option(String(session.id),String(session.id)));
  const uniqueReasons=[...new Set(cases.flatMap(filterReasons))].sort((a,b) => a.localeCompare(b,"de"));
  for (const reason of uniqueReasons) $("reasonFilter").add(new Option(reason,reason));
  buildPlots();
  renderMetadata();
  $("sessionSelect").addEventListener("change",event => setSession(event.target.value));
  for (const id of ["showD1","showD2","scaleSelect","modelSelect"]) $(id).addEventListener("change",requestDraw);
  for (const id of ["reasonFilter","allSessions"]) $(id).addEventListener("change",updateFilters);
  $("search").addEventListener("input",updateFilters);
  $("applyRange").addEventListener("click",() => setRange(Number($("rangeStart").value)*60,Number($("rangeEnd").value)*60));
  for (const id of ["rangeStart","rangeEnd"]) $(id).addEventListener("keydown",event => {if (event.key === "Enter") $("applyRange").click();});
  $("fullRange").addEventListener("click",showFull);
  $("zoomIn").addEventListener("click",() => zoom(.5));
  $("zoomOut").addEventListener("click",() => zoom(2));
  $("previousCase").addEventListener("click",() => moveCase(-1));
  $("nextCase").addEventListener("click",() => moveCase(1));
  document.addEventListener("click",event => {
    const button=event.target.closest("button[data-case]");
    if (button) selectCase(button.dataset.case);
  });
  document.addEventListener("keydown",event => {
    if (["INPUT","SELECT","TEXTAREA"].includes(event.target.tagName)) return;
    if (event.key === "ArrowLeft") {event.preventDefault();moveCase(-1);}
    if (event.key === "ArrowRight") {event.preventDefault();moveCase(1);}
    if (event.key === "Escape") {state.pinned=false;state.cursor=null;state.drag=null;requestDraw();}
  });
  if (typeof ResizeObserver === "function") new ResizeObserver(requestDraw).observe($("plots"));
  else window.addEventListener("resize",requestDraw);
  setSession(sessions[0].id);
})();
</script>
</body>
</html>
"""


def read_json(path: Path) -> dict:
    """Read one analysis object without changing its numerical values."""
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected a JSON object")
    return value


def render(input_dir: Path, output_path: Path) -> None:
    """Embed analysis files in deterministic, script-safe JSON."""
    corpus_path = input_dir / "corpus.json"
    cases_path = input_dir / "cases.json"
    comparison_path = input_dir / "feature_comparison.json"
    corpus = read_json(corpus_path)
    cases = read_json(cases_path)
    if not isinstance(corpus.get("sessions"), list):
        raise ValueError("corpus.json: expected a sessions array")
    if not isinstance(cases.get("cases"), list):
        raise ValueError("cases.json: expected a cases array")
    if any(
        not isinstance(item, dict) or "id" not in item for item in corpus["sessions"]
    ):
        raise ValueError("corpus.json: every session must be an object with an id")
    if any(not isinstance(item, dict) or "id" not in item for item in cases["cases"]):
        raise ValueError("cases.json: every case must be an object with an id")
    embedded_cases = dict(cases)
    embedded_cases["cases"] = []
    for item in cases["cases"]:
        embedded = {key: value for key, value in item.items() if key != "curve_slices"}
        if "curve_slices" in item:
            embedded["curve_slices_reference"] = {
                "session_id": item.get("session_id"),
                "window": item.get("window"),
                "source": "corpus.sessions by session_id and window",
                "series": [
                    "original_series",
                    "absolute_humidity_series",
                    "feature_rows",
                ],
            }
        embedded_cases["cases"].append(embedded)
    cases.clear()
    payload = {
        "corpus": corpus,
        "cases": embedded_cases,
        "input_files": ["corpus.json", "cases.json"],
        "html_embedding": {
            "curve_slices": "Referenced from the complete corpus; not duplicated.",
            "source_json": "Unchanged. All case descriptors remain embedded.",
        },
    }
    if comparison_path.is_file():
        payload["feature_comparison"] = read_json(comparison_path)
        payload["input_files"].append("feature_comparison.json")
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    for literal, escaped in (
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        serialized = serialized.replace(literal, escaped)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        HTML.replace("__ANALYSIS_DATA__", serialized), encoding="utf-8", newline="\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, type=Path, help="Directory containing analysis JSONs"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="Destination standalone HTML file"
    )
    arguments = parser.parse_args()
    try:
        render(arguments.input, arguments.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"render_proxy_forms: {error}\n")
    print(f"Wrote {arguments.output}")


if __name__ == "__main__":
    main()
