"""Präsenzzeiten aus Aufzeichnungen mit Präsenzsensor und bewerteter Referenz.

Aufruf:
    python tools/presence_learn.py --data DIR --reference REF.json \
        --train NAME,NAME,... --params PARAMS.json

``DIR`` enthält je Aufzeichnung einen Ordner mit ``samples.jsonl.gz`` (Rolle
``presence`` mit Wahrheitswerten).  ``REF.json`` enthält je Aufzeichnung die
bewerteten Ereignisse (``doors``: open/close/ventilated in Sekunden,
``infusions``: Sekunden).  Gänge sind die Aufgussfolgen zwischen gelüfteten
Türöffnungen; Eintritt ist der letzte Türschluss vor dem ersten Aufguss,
Austritt die erste gelüftete Öffnung nach dem letzten.

Gelernt werden die Zeiten aus ``core/presence_evidence``.  Der Block
``presence`` in ``PARAMS.json`` wird ersetzt; alle übrigen Erkennungsgrößen
bleiben unverändert.  Der Block enthält nur Verteilungsgrößen und Anzahlen.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.ha_sauna.core import presence_evidence as evidence


def presence_states(case_dir: Path):
    with gzip.open(case_dir / "samples.jsonl.gz") as lines:
        rows = [json.loads(line) for line in lines]
    states = sorted((r["t"], r["value"]) for r in rows
                    if r["role"] == "presence" and isinstance(r["value"], bool))
    return states, max(r["t"] for r in rows)


def state_at(states, t):
    value = None
    for when, state in states:
        if when > t:
            break
        value = state
    return value


def gangs(reference):
    """(Eintrittsschluss, Austrittsöffnung) je Gang der Referenz."""
    doors = reference["doors"]
    groups = []
    for t in sorted(reference["infusions"]):
        if groups and not any(groups[-1][-1] < d["open"] < t and d["ventilated"] for d in doors):
            groups[-1].append(t)
        else:
            groups.append([t])
    result = []
    for group in groups:
        entry = [d for d in doors if d["close"] is not None and d["close"] <= group[0]]
        leave = [d for d in doors if d["open"] > group[-1] and d["ventilated"]]
        result.append((entry[-1]["close"] if entry else None, leave[0]["open"] if leave else None))
    return result


def collect(states, end, reference):
    """Dauern je Größe: (beobachtet, zensiert)."""
    found = {key: ([], []) for key in ("hold", "stay", "dropout", "vacancy")}
    doors = sorted(reference["doors"], key=lambda d: d["open"])
    stays = gangs(reference)
    for door in doors:
        close = door["close"]
        if close is None:
            continue
        following = [d["open"] for d in doors if d["open"] > close]
        limit = following[0] if following else end
        entry = any(start is not None and abs(close - start) < 1 for start, _ in stays)
        inside = any(start is not None and start < door["open"] < (leave or math.inf)
                     for start, leave in stays)
        if inside and not entry:
            continue
        changes = [(t, v) for t, v in states if close < t < limit]
        off = [t for t, v in changes if v is False]
        if state_at(states, close) is True:
            observed, censored = found["stay" if entry else "hold"]
            (observed.append(off[0] - close) if off else censored.append(limit - close))
        if entry:
            continue
        since = close if state_at(states, close) is False else None
        for t, v in changes:
            if v is False and since is None:
                since = t
            elif v is True and since is not None:
                found["vacancy"][0].append(t - since)
                since = None
        if since is not None:
            found["vacancy"][1].append(limit - since)
    for start, leave in stays:
        if start is None:
            continue
        stop = leave if leave is not None else end
        state, since = state_at(states, start), None
        for t, v in states:
            if start < t < stop:
                if v is False and state:
                    since = t
                if v is True and state is False and since is not None:
                    found["dropout"][0].append(t - since)
                    since = None
                state = v
        if since is not None:
            found["dropout"][1].append(stop - since)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--params", required=True)
    args = ap.parse_args()
    reference = json.loads(Path(args.reference).read_text())
    found = {key: ([], []) for key in ("hold", "stay", "dropout", "vacancy")}
    names = [n for n in args.train.split(",") if n]
    for name in names:
        states, end = presence_states(Path(args.data) / name)
        for key, (observed, censored) in collect(states, end, reference[name]).items():
            found[key][0].extend(observed)
            found[key][1].extend(censored)
    stay = evidence.fit_lognormal(*found["stay"])
    shape, scale, outside = evidence.fit_hold(*found["hold"], stay)
    block = {
        "hold": [shape, scale],
        "stay": list(stay),
        "dropout": list(evidence.fit_lognormal(*found["dropout"])),
        "vacancy": list(evidence.fit_lognormal(*found["vacancy"])),
        "seen_after_exit": outside,
        "counts": {key: len(a) + len(b) for key, (a, b) in found.items()},
        "training": names,
    }
    path = Path(args.params)
    params = json.loads(path.read_text())
    params["presence"] = {key: (evidence_round(value)) for key, value in block.items()}
    path.write_text(json.dumps(params, separators=(",", ":")))
    print(json.dumps(params["presence"], indent=1))


def evidence_round(value):
    if isinstance(value, float):
        return float(f"{value:.6g}")
    if isinstance(value, list):
        return [evidence_round(v) for v in value]
    return value


if __name__ == "__main__":
    main()
