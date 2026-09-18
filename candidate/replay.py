#!/usr/bin/env python3
"""Offline-Kalibrierungsreplay; keine HA-Verbindung, keine Heizungssteuerung.
python3 replay.py ORIGINAL.zip --parameters parameter.json --out ergebnis.json [--tests]
Benoetigt numpy und pandas. Alle Detektorwerte werden aus parameter.json gelesen.
Die alten Tuerzustaende und die Nutzerannotationen werden NUR zur Auswertung benutzt.
"""
from __future__ import annotations
import argparse, hashlib, json, math, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

CHANNELS=(3,6)

def stamp(t): return pd.Timestamp(t,unit='s',tz='UTC').tz_convert('Europe/Berlin')
def local(s): return pd.Timestamp('2026-09-17T'+s,tz='Europe/Berlin')
def iso(t): return pd.Timestamp(t).isoformat()

def load(path):
    names={f'sensor.yellow_j11_sht35_pca_sht35_kanal_{c}_{s}':f'{v}{c}'
           for c in CHANNELS for s,v in [('temperatur','T'),('luftfeuchte','H')]}
    names.update({'input_boolean.saunatur_offen':'old_door','input_boolean.sauna_session_offen':'session'})
    rows={k:[] for k in names.values()}
    with zipfile.ZipFile(path) as z:
        meta=json.loads(z.read('manifest.json'))
        for n,line in enumerate(z.open('states.jsonl'),1):
            r=json.loads(line); key=names.get(r['entity_id'])
            if key: rows[key].append((r['last_updated_ts'],r['state'],r['state_id'],n))
    grid=pd.date_range(meta['start_inclusive'],meta['end_exclusive'],freq='1s',inclusive='left').tz_convert('Europe/Berlin')
    seconds=grid.as_unit('ns').astype('int64').to_numpy()/1e9
    f=pd.DataFrame(index=grid); raw={}
    for key,values in rows.items():
        if not values: raise ValueError('Keine Messwerte fuer '+key)
        d=pd.DataFrame(values,columns=['ts','state','state_id','line']).sort_values(['ts','state_id']).drop_duplicates('ts',keep='last')
        raw[key]=d
        take=np.searchsorted(d.ts.to_numpy(),seconds,side='right')-1
        f[key]=d.state.to_numpy()[np.maximum(0,take)]
        f.loc[take<0,key]=np.nan
        if key not in ('old_door','session'): f[key]=pd.to_numeric(f[key],errors='coerce')
    return f,raw,meta

def slope(s,window,step=1):
    if window%step: raise ValueError('Trendfenster muss durch Pruefraster teilbar sein')
    n=window//step+1
    a=sliding_window_view(s.to_numpy(dtype=float),n)
    i,j=np.triu_indices(n,1)
    out=np.median((a[:,j]-a[:,i])/((j-i)*step/60),axis=1)
    return pd.Series(np.r_[np.full(n-1,np.nan),out],index=s.index)

def sustained(mask,seconds,step=1):
    n=math.ceil(seconds/step)+1
    return pd.Series(mask).fillna(False).rolling(n,min_periods=n).sum().eq(n)

def prepare(f,p):
    f=f.copy(); tp=p['tuer']
    for c in CHANNELS:
        for v in ('T','H'):
            f[f'{v}{c}m']=f[f'{v}{c}'].rolling(p['median_s'],min_periods=p['median_s']).median()
        f[f'trend{c}']=slope(f[f'T{c}m'],tp['trendfenster_s'])
        f[f'dh{c}']=f[f'H{c}m']-f[f'H{c}m'].shift(tp['feuchtefenster_s'])
        f[f'base{c}']=f[f'T{c}m'].rolling(p['lueftung']['basis_rueckblick_s'],min_periods=p['lueftung']['basis_rueckblick_s']).max()
    step=p['person']['pruefraster_s']; ff=f.iloc[::step].copy()
    for c in CHANNELS:
        for v in ('T','H'):
            for path in ('stark','schwach'):
                ff[f'{path}_{v}{c}']=slope(ff[f'{v}{c}m'],p['person'][path]['fenster_s'],step)
    return f,ff

def door(f,p,channels):
    tp=p['tuer']; n=len(f)
    op=np.logical_and.reduce([((f[f'trend{c}']<tp['oeffnung_trend_max_c_min']) &
        (f[f'dh{c}']<=-tp['oeffnung_feuchteabfall_pp'][str(c)])).fillna(False).to_numpy() for c in channels])
    cl=np.logical_and.reduce([(f[f'trend{c}']>tp['schliessung_trend_min_c_min']).fillna(False).to_numpy() for c in channels])
    state=False; count=0; events=[]; state_series=np.zeros(n,dtype=bool)
    for i in range(n):
        ok=cl[i] if state else op[i]
        count=count+1 if ok else 0
        hold=tp['schliessung_bestaetigung_s'] if state else tp['oeffnung_bestaetigung_s']
        if count>=hold+1:
            state=not state; count=0; events.append((i,'open' if state else 'close'))
        state_series[i]=state
    return events,state_series

def ventilation(f,p,events,channels):
    """Ein oeffnendes Ereignis setzt den Kontext zurueck. Bestaetigte Episode bleibt
    nach Schliessung Kontext bis zur naechsten Oeffnung. Keine Kenntnis spaeterer Gange.
    Die Basis wird beim Oeffnungssignal ausschliesslich aus der Vergangenheit fixiert.
    """
    ctx=np.zeros(len(f),dtype=bool); episodes=[]
    lp=p['lueftung']; temperatures={c:f[f'T{c}m'].to_numpy() for c in CHANNELS}
    for j in range(0,len(events),2):
        oi=events[j][0]
        ci=events[j+1][0] if j+1<len(events) else len(f)-1
        closed=j+1<len(events)
        baseline={c:float(f[f'base{c}'].iloc[oi]) for c in CHANNELS}
        vi=None
        # Die Lueftungsentscheidung wird waehrend der offenen Episode getroffen,
        # nicht erst rueckblickend aus ihrem spaeteren Minimum.
        for i in range(oi,ci+1):
            if i-oi>=lp['mindestens_s'] and all(baseline[c]-temperatures[c][i]>=lp['mindestabfall_c'][str(c)] for c in channels):
                vi=i; break
        nxt=events[j+2][0] if j+2<len(events) else len(f)
        if closed and vi is not None: ctx[ci:nxt]=True
        row={'nr':j//2+1,'open_detected_at':iso(f.index[oi]),'close_detected_at':iso(f.index[ci]) if closed else None,
             'vent_detected_at':iso(f.index[vi]) if vi is not None else None,
             'signal_interval_s':ci-oi,'_oi':oi,'_ci':ci}
        for c in CHANNELS:
            segment=f[f'T{c}m'].iloc[oi:ci+1]
            row[f'drop_k{c}_c']=float(baseline[c]-segment.min())
            row[f'trough_k{c}_at']=iso(segment.idxmin())
            row[f'trough_to_close_k{c}_s']=(f.index[ci]-segment.idxmin()).total_seconds() if closed else None
        episodes.append(row)
    return ctx,episodes

def signals(f,ff,p,state,ctx,channels,offset=0):
    step=p['person']['pruefraster_s']
    if offset:
        ff=f.iloc[offset::step].copy()
        for c in CHANNELS:
            for v in ('T','H'):
                for route in ('stark','schwach'):
                    ff[f'{route}_{v}{c}']=slope(ff[f'{v}{c}m'],p['person'][route]['fenster_s'],step)
    eligible=pd.Series(~state & f.session.eq('on').to_numpy(),index=f.index)
    paths={}
    for route in ('stark','schwach'):
        rp=p['person'][route]
        condition=eligible.iloc[offset::step].copy()
        for c in channels:
            condition &= (ff[f'{route}_H{c}']>=rp['feuchtetrend_pp_min'][str(c)]) & (ff[f'{route}_T{c}']>=rp['temperaturtrend_c_min'][str(c)])
        if route=='schwach' and p['person']['schwach_nur_nach_bestaetigter_lueftung']:
            condition &= ctx[offset::step]
        paths[route]=sustained(condition,rp['bestaetigung_s'],step)
    ap=p['aufguss']; infusion=eligible.copy()
    # Aufgussweg verwendet wie der vorige Replay-Kandidat Rohwertdifferenzen;
    # kein zweiter Lowpass und keine vorausschauende Interpolation.
    for c in channels:
        infusion &= (f[f'H{c}']-f[f'H{c}'].shift(ap['fenster_s'])>=ap['feuchteanstieg_pp']) & (f[f'T{c}']-f[f'T{c}'].shift(ap['fenster_s'])>=ap['temperaturaenderung_min_c'])
    infusion=sustained(infusion,ap['bestaetigung_s'])
    return paths,infusion,eligible

# Bewertungsannotation, niemals Detektoreingabe. Zeitfenster referenzieren bekannte
# Episoden, nicht exakt gemessene mechanische Kontakt- oder Eintrittszeiten.
GANG_WINDOWS=[('20:43:00','20:59:00'),('21:14:00','21:27:00'),('21:55:00','22:11:00'),('22:48:00','23:02:00')]
REFERENCE_ANCHORS=['19:38:39','19:57:44','20:30:48','20:33:10','20:43:51','20:59:38','21:14:43','21:27:38','21:33:55','21:56:06','22:11:59','22:39:40','22:48:41','23:03:29','23:35:54']

def assess(f,events,paths,infusion,eligible):
    opens=[i for i,k in events if k=='open']; closes=[i for i,k in events if k=='close']
    used=set(); extra=[]
    for i in opens:
        choices=[(abs((f.index[i]-local(a)).total_seconds()),j) for j,a in enumerate(REFERENCE_ANCHORS) if j not in used and -120<=(f.index[i]-local(a)).total_seconds()<=60]
        if choices: used.add(min(choices)[1])
        else: extra.append(iso(f.index[i]))
    person=paths['stark']|paths['schwach']; positive=pd.Series(False,index=f.index)
    gangs=[]
    for no,(a,b) in enumerate(GANG_WINDOWS,1):
        a,b=local(a),local(b); positive |= (f.index>=a)&(f.index<b)
        hits=person.loc[a:b]; times=hits[hits].index
        ih=infusion.loc[a:b]; it=ih[ih].index
        t=times[0] if len(times) else None
        gangs.append({'gang':no,'person_detected_at':iso(t) if t is not None else None,
                      'route':('stark' if paths['stark'].loc[t] else 'schwach') if t is not None else None,
                      'infusion_detected_at':iso(it[0]) if len(it) else None,
                      'lead_to_infusion_s':(it[0]-t).total_seconds() if t is not None and len(it) else None})
    background=eligible & ~positive
    return {'reference_opening_episodes':len(REFERENCE_ANCHORS),'detected_openings':len(opens),'detected_closures':len(closes),
            'matched_openings':len(used),'extra_openings':extra,
            'missing_opening_anchors':[a for j,a in enumerate(REFERENCE_ANCHORS) if j not in used],
            'gangs':gangs,'person_background_positive_checks':int((person & background.reindex(person.index)).sum()),
            'infusion_background_positive_seconds':int((infusion & background).sum()),
            'closed_background_seconds':int(background.sum())}

def main(path,p,tests=False):
    if p['raster_s']!=1: raise ValueError('Dieses Replay unterstuetzt ein Ein-Sekunden-Grundraster')
    source,raw,meta=load(path); f,ff=prepare(source,p)
    result={'candidate':'Tuer-Gang','source':path.name,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'period':{'start':meta['start_inclusive'],'end_exclusive':meta['end_exclusive']},'parameters':p,'modes':{},
            'limitations':['Kalibrierung und Rueckpruefung derselben Session. Keine unabhaengige Validierung.',
              '15 Referenzepisoden: 14 bisherige plus vom Nutzer bestaetigte Gefaessentnahme um 22:39:40.',
              'Keine separat aufgezeichneten mechanischen Tuerzeiten; Detektionszeit ist keine Kontaktmessung.',
              'Statischer Ein-Sensor-Betrieb geprueft; Hardwarefehlerdiagnose und dynamischer Kanalwechsel hier nicht getestet.',
              'Reale kurze Personenabgaenge waehrend eines Gangs sind nicht eigens annotiert; Gefaessentnahme dient nur als kurze Tuerreferenz.',
              'Replay prueft Erkennung und Lueftungskontext, nicht neuen Thermostat oder kompletten produktiven Ablauf.']}
    for name,channels in [('beide',(3,6)),('nur_k3',(3,)),('nur_k6',(6,))]:
        events,state=door(f,p,channels); ctx,episodes=ventilation(f,p,events,channels)
        paths,infusion,eligible=signals(f,ff,p,state,ctx,channels)
        assessment=assess(f,events,paths,infusion,eligible)
        for e in episodes:
            oi=e.pop('_oi'); e.pop('_ci')
            e['proof_open']={}
            for c in CHANNELS:
                for v in ('T','H'):
                    d=raw[f'{v}{c}'];r=d[d.ts<=f.index[oi].timestamp()].iloc[-1]
                    e['proof_open'][f'{v}{c}']={'state':r.state,'state_id':int(r.state_id),'states_jsonl_line':int(r.line)}
        result['modes'][name]={'assessment':assessment,'episodes':episodes}
    if tests:
        checks=[]
        # Einzelfaktorvarianten, keine behauptete unabhaengige Validierung.
        for key,values in [('oeffnung_trend_max_c_min',[-1.6,-1.8,-2.0]),('schliessung_trend_min_c_min',[.1,.15,.2]),('schliessung_bestaetigung_s',[2,3,5])]:
            for value in values:
                pp=json.loads(json.dumps(p));pp['tuer'][key]=value
                for name,channels in [('beide',(3,6)),('nur_k3',(3,)),('nur_k6',(6,))]:
                    events,state=door(f,pp,channels);ctx,_=ventilation(f,pp,events,channels)
                    paths,inf,elig=signals(f,ff,pp,state,ctx,channels)
                    s=assess(f,events,paths,inf,elig)
                    checks.append({'parameter':key,'value':value,'mode':name,'assessment':s})
        for channel,values in [('3',[.4,.45,.5]),('6',[.25,.3,.35])]:
            for value in values:
                pp=json.loads(json.dumps(p));pp['tuer']['oeffnung_feuchteabfall_pp'][channel]=value
                for name,channels in [('beide',(3,6)),('nur_k3',(3,)),('nur_k6',(6,))]:
                    events,state=door(f,pp,channels);ctx,_=ventilation(f,pp,events,channels)
                    paths,inf,elig=signals(f,ff,pp,state,ctx,channels)
                    checks.append({'parameter':'oeffnung_feuchteabfall_pp_k'+channel,'value':value,'mode':name,'assessment':assess(f,events,paths,inf,elig)})
        result['single_parameter_checks']=checks
        result['person_grid_offsets']=[]
        events,state=door(f,p,(3,6));ctx,_=ventilation(f,p,events,(3,6))
        for offset in range(p['person']['pruefraster_s']):
            paths,inf,elig=signals(f,ff,p,state,ctx,(3,6),offset)
            result['person_grid_offsets'].append({'offset_s':offset,'assessment':assess(f,events,paths,inf,elig)})
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('archive',type=Path);ap.add_argument('--parameters',type=Path,default=Path(__file__).with_name('parameter.json'))
    ap.add_argument('--out',type=Path,default=Path('ergebnis.json'));ap.add_argument('--tests',action='store_true');a=ap.parse_args()
    try:
        params=json.loads(a.parameters.read_text(encoding='utf-8'))
        result=main(a.archive,params,a.tests)
        a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    except (OSError,ValueError,KeyError,zipfile.BadZipFile) as exc:
        ap.exit(1,f'Replay abgebrochen: {exc}\n')
    print(str(a.out))
