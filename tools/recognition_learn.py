"""Erstlernen der Erkennungsgrößen aus Aufzeichnungen mit bewerteter Referenz.

Aufruf:
    python tools/recognition_learn.py --data DIR --reference REF.json \
        --train R01,R06,... [--exclude EVENTS.json] --out PARAMS.json

``DIR`` enthält je Aufzeichnung einen Ordner mit ``case.json`` und
``samples.jsonl.gz``.  ``REF.json`` enthält je Aufzeichnung die bewerteten
Ereignisse (``doors``: open/close/ventilated/exchanged in Sekunden,
``infusions``: Sekunden).  ``EVENTS.json`` (optional) sind Ereignisse einer
früheren Erkennung; sie begrenzen die ruhigen Abschnitte für die Heizwechsel.
Das Ergebnis enthält die Modellgrößen und die Belege als Startwissen für das
spätere Nachlernen.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.ha_sauna.core import recognition_learning as learning  # noqa: E402
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity  # noqa: E402
from custom_components.ha_sauna.core.recognition import ClimateRecognizer  # noqa: E402

ORIGIN = datetime(2000, 1, 1, 12, tzinfo=UTC)
ROLES = {
    "upper_temperature": (Position.UPPER, Quantity.TEMPERATURE),
    "upper_humidity": (Position.UPPER, Quantity.HUMIDITY),
    "lower_temperature": (Position.LOWER, Quantity.TEMPERATURE),
    "lower_humidity": (Position.LOWER, Quantity.HUMIDITY),
}


def measurements(case_dir: Path):
    rows = [json.loads(line) for line in gzip.open(case_dir / "samples.jsonl.gz")]
    rows.sort(key=lambda r: r["t"])
    for r in rows:
        role = ROLES.get(r["role"])
        if role is None or not isinstance(r["value"], (int, float)) or isinstance(r["value"], bool):
            continue
        at = ORIGIN + timedelta(seconds=r["t"])
        yield Measurement(role[0], role[1], float(r["value"]), str(r["value"]), "replay", at)


def series(case_dir: Path, parameters: dict):
    """Messraster genau wie in der Laufzeit (Uhr: Temperatur oben, übrige Kanäle gehalten)."""
    recognizer = ClimateRecognizer(parameters, ORIGIN, evaluate=False)
    last = ORIGIN
    for m in measurements(case_dir):
        recognizer.accept(m)
        last = m.received_at
    recognizer.advance(last)
    record = recognizer.record
    return recognizer.channels, record.times, record.values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--exclude")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    reference = json.loads(Path(args.reference).read_text())
    exclude = json.loads(Path(args.exclude).read_text()) if args.exclude else {}
    names = [n for n in args.train.split(",") if n]
    base = learning.base_parameters()
    collected = []
    for name in names:
        channels, times, values = series(Path(args.data) / name, base)
        labels = {"doors": reference[name]["doors"], "infusions": reference[name]["infusions"]}
        marks = [e[1] for e in exclude.get(name, [])]
        ev = learning.collect_evidence(times, values, channels, labels, exclude=marks, session=name)
        collected.append((name, times, values, ev))
        print(name, len(times), "Messschritte", flush=True)
    # wall and stone resupply β pooled over all long openings, then λ of every opening with that β
    pooled = learning.merge_evidence(*[{k: v for k, v in ev.items() if not k.startswith("_")}
                                       for _, _, _, ev in collected])
    beta = learning.pooled_beta(pooled, base=base)
    for _, _, _, ev in collected:
        learning.fit_exchange_evidence(ev, beta)
    merged = learning.merge_evidence(*[{k: v for k, v in ev.items() if not k.startswith("_")}
                                       for _, _, _, ev in collected])
    first = learning.estimate(merged, base=base)
    # second step with the first estimate: innovations (Student-t) and turbulence profiles
    for name, times, values, ev in collected:
        learning.likelihood_evidence(ev, first, times, values)
    merged = learning.merge_evidence(*[ev for _, _, _, ev in collected])
    params = learning.estimate(merged, base=first)
    learning.pool_forms(merged)                     # shipped start knowledge holds no measured courses
    params["evidence"] = learning.compact(merged)
    params["training"] = names
    Path(args.out).write_text(json.dumps(params, separators=(",", ":")))
    print("geschrieben", args.out)


if __name__ == "__main__":
    main()
