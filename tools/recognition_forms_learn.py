"""Lernen der Vergleichsformen aus Aufzeichnungen mit bewerteter Referenz.

Aufruf:
    python tools/recognition_forms_learn.py --data DIR --reference REF.json \
        --train R01,R06,... --parameters PARAMS.json --out FORMS.json \
        [--check DIR REF.json NAME,NAME,...] [--report REPORT.json]

``DIR`` und ``REF.json`` wie bei ``tools/recognition_learn.py``.  ``PARAMS.json``
sind die Modellgrößen derselben Lernmenge (``tools/recognition_learn.py``); aus
ihnen stammen das Evidenzverhältnis, die mittlere Zeit bis zum nächsten
Heizwechsel (Fenstergrenze) und das Zustandsmodell, dessen Öffnungskandidaten
ohne bezeichnetes Ereignis die Störungen der Lernmenge sind.  Das Ergebnis
enthält je Form Raten, Ratenfaktor, Koeffizientenverteilung und Rauschen
(``core/recognition_forms.py``) sowie die gelernten Verteilungen der Lüftung.
``--check`` prüft die bezeichneten Ereignisse weiterer Aufzeichnungen gegen die
gelernte Streuung ihrer Form (mehrfach möglich); ``--report`` schreibt die
Einzelergebnisse.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from recognition_learn import ORIGIN, measurements

from custom_components.ha_sauna.core import recognition_forms as forms
from custom_components.ha_sauna.core.recognition import ClimateRecognizer
from custom_components.ha_sauna.core.recognition_learning import (
    base_parameters,
    compact,
)
from custom_components.ha_sauna.core.recognition_model import (
    CLOSED,
    OUTSIDE_TYPICAL,
    SHIFT,
    is_humidity,
)
from custom_components.ha_sauna.core.timeline import Kind

QUANTILES = (0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0)


def record(case_dir: Path):
    """Messraster wie in der Laufzeit: Zeiten, Kanalwerte, relative Feuchte der Uhrposition."""
    recognizer = ClimateRecognizer(base_parameters(), ORIGIN, evaluate=False)
    last = ORIGIN
    for m in measurements(case_dir):
        recognizer.accept(m)
        last = m.received_at
    recognizer.advance(last)
    r = recognizer.record
    return (
        list(recognizer.channels),
        np.asarray(r.times),
        np.asarray(r.values),
        np.asarray(r.relative),
    )


def candidates(case_dir: Path, parameters: dict) -> list[float]:
    """Öffnungskandidaten des Zustandsmodells (bestätigt oder durch die Form verworfen)."""
    recognizer = ClimateRecognizer(parameters, ORIGIN)
    last = ORIGIN
    for m in measurements(case_dir):
        recognizer.accept(m)
        recognizer.advance(m.received_at)
        last = m.received_at
    recognizer.advance(last)
    out = [
        (d.effective_at - ORIGIN).total_seconds()
        for d in recognizer.detections
        if Kind(d.kind) is Kind.DOOR_OPEN
    ]
    for decision in recognizer.decisions:
        if decision.get("event") == "door_open_rejected":
            at = datetime.fromisoformat(decision["effective_at"])
            out.append((at - ORIGIN).total_seconds())
    return sorted(out)


def labels_of(entry: dict) -> dict:
    return {"doors": entry.get("doors", []), "infusions": entry.get("infusions", [])}


def windows(name, channels, t, y, labels, horizon, extra=()):
    """Ausschnitte ``(Form, Schlüssel, u, Werte, Außenluft)`` einer Aufzeichnung."""
    outside = [
        OUTSIDE_TYPICAL["absolute_humidity"]
        if is_humidity(c)
        else OUTSIDE_TYPICAL["temperature"]
        for c in channels
    ]
    out = []
    for form, anchor, start, end in list(
        forms.event_windows(labels, horizon, float(t[0]), float(t[-1]))
    ) + list(extra):
        piece = forms.cut(
            t, y, anchor, start, end, forms.columns_needed(form, channels)
        )
        if piece is not None:
            out.append((form, f"{name}@{anchor:.1f}", piece[0], piece[1], outside))
    return out


def _two_segment(x, y, kink):
    """Steigungen vor und nach einem festen Knick (stetiges Zwei-Segment-Modell)."""
    d = x - kink
    b = np.column_stack([np.ones_like(d), d, np.maximum(d, 0)])
    coef, *_ = np.linalg.lstsq(b, y, rcond=None)
    return float(coef[1]), float(coef[1] + coef[2])


def _summary(values) -> dict | None:
    v = np.asarray([x for x in values if math.isfinite(x)], float)
    if not len(v):
        return None
    return {
        "n": len(v),
        "mean": float(v.mean()),
        "sd": float(v.std()),
        "quantiles": {f"{q:g}": float(np.quantile(v, q)) for q in QUANTILES},
    }


def ventilation_distributions(recordings) -> dict:
    """Verteilungen anstelle fester Größen der Lüftungsprüfung (Lernmenge).

    Zahl der Messschritte von der Öffnung bis zum Lüften und vom Lüften bis zum
    Durchlüften; Steigungsverhältnis nach/vor dem Knick in ln RH (Lüften) und
    ln AH oben (Durchgelüftet); dasselbe Verhältnis in ln RH am Schluss von
    Öffnungen ohne Lüften (kurze Öffnungen im Gang).
    """
    steps_vent, steps_exch, ratio_vent, ratio_exch, ratio_short = [], [], [], [], []
    for _, channels, t, y, rh, labels in recordings:
        ln_rh = np.log(np.maximum(rh, 0.1))
        ln_ah = np.log(np.maximum(y[:, channels.index("Au")], 0.1))
        for d in labels["doors"]:
            opened, closed = d["open"], d.get("close")
            if closed is None:
                continue
            vent, exch = d.get("ventilated"), d.get("exchanged")
            vent = vent if vent is not None and opened < vent < closed else None
            exch = exch if exch is not None and opened < exch < closed else None
            if vent is not None:
                steps_vent.append(int(((t >= opened) & (t < vent)).sum()))
                end = exch if exch is not None and exch > vent else closed
                m = (t >= opened) & (t <= end)
                s1, s2 = _two_segment(t[m], ln_rh[m], vent)
                ratio_vent.append(s2 / s1 if s1 < 0 else math.nan)
            if exch is not None and vent is not None and exch > vent:
                steps_exch.append(int(((t >= vent) & (t < exch)).sum()))
                m = (t >= vent) & (t <= closed)
                s1, s2 = _two_segment(t[m], ln_ah[m], exch)
                ratio_exch.append(s2 / s1 if s1 < 0 else math.nan)
            if vent is None:
                m = (t >= opened) & (t <= closed + (closed - opened))
                s1, s2 = _two_segment(t[m], ln_rh[m], closed)
                ratio_short.append(s2 / s1 if s1 < 0 else math.nan)
    return {
        "steps_to_ventilation": _summary(steps_vent),
        "steps_to_exchange": _summary(steps_exch),
        "slope_ratio_ventilation": _summary(ratio_vent),
        "slope_ratio_exchange": _summary(ratio_exch),
        "slope_ratio_short_closing": _summary(ratio_short),
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--parameters", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--check", nargs=3, action="append", default=[])
    ap.add_argument("--report")
    args = ap.parse_args()
    reference = json.loads(Path(args.reference).read_text())
    loaded = json.loads(Path(args.parameters).read_text())
    parameters = {k: v for k, v in loaded.items() if k not in ("evidence", "training")}
    ratio = float(parameters["decision"]["evidence_ratio"])
    horizon = 1.0 / float(parameters["rates"][CLOSED][SHIFT])
    names = [n for n in args.train.split(",") if n]
    recordings, pieces = [], []
    for name in names:
        channels, t, y, rh = record(Path(args.data) / name)
        labels = labels_of(reference[name])
        found = candidates(Path(args.data) / name, parameters)
        extra = forms.disturbance_windows(
            found, labels, horizon, float(t[0]), float(t[-1])
        )
        recordings.append((name, channels, t, y, rh, labels))
        pieces += windows(name, channels, t, y, labels, horizon, extra)
        print(
            name,
            len(t),
            "Messschritte,",
            len(found),
            "Kandidaten,",
            len(extra),
            "Störungen",
            flush=True,
        )
    learned, details = {}, {}
    for form in forms.FORMS:
        events = [(u, v, o) for f, _, u, v, o in pieces if f == form]
        keys = [k for f, k, _, _, _ in pieces if f == form]
        if not events:
            continue
        result = forms.learn_form(
            form, events, ratio, keys=keys, channels=recordings[0][1]
        )
        details[form] = {"learning": result.pop("typicality")}
        learned[form] = result
        print(
            form,
            result["events"],
            "Ereignisse; untypisch (Streuung)",
            len(result["atypical"]),
            "; Raten",
            len(result["atypical_rates"]),
            "/ mit Faktor",
            len(result["atypical_rates_factor"]),
            "; Rand",
            [
                f"{e['channel']}:{e['rate']} {e['limit'] or 'Rand'}"
                for e in result["grid_edge"]
            ]
            or "-",
            flush=True,
        )
    out = {
        "version": 1,
        "channels": recordings[0][1],
        "evidence_ratio": ratio,
        "horizon": horizon,
        "rate_grid": [
            float(forms.RATE_GRID[0]),
            float(forms.RATE_GRID[-1]),
            len(forms.RATE_GRID),
        ],
        "outside": OUTSIDE_TYPICAL,
        "training": names,
        "parameters_sha256": sha256(Path(args.parameters)),
        "reference_sha256": sha256(Path(args.reference)),
        "forms": learned,
        "ventilation": ventilation_distributions(recordings),
    }
    Path(args.out).write_text(
        json.dumps(compact(out), sort_keys=True, separators=(",", ":"))
    )
    print("geschrieben", args.out, flush=True)
    checks = []
    for directory, ref_path, listed in args.check:
        ref = json.loads(Path(ref_path).read_text())
        for name in [n for n in listed.split(",") if n]:
            channels, t, y, _ = record(Path(directory) / name)
            for form, key, u, v, o in windows(
                name, channels, t, y, labels_of(ref[name]), horizon
            ):
                result = forms.typicality(learned[form], u, v, o, ratio)
                checks.append({"form": form, "event": key, **result})
            print("geprüft", name, flush=True)
    if args.report:
        report = {"learning": details, "check": checks}
        Path(args.report).write_text(
            json.dumps(compact(report), sort_keys=True, separators=(",", ":"))
        )
        print("geschrieben", args.report, flush=True)


if __name__ == "__main__":
    main()
