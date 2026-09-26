"""Normalisierte Quellenmeldungen; keine Gangzählung und keine Aktorwirkung."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .contracts import PresenceReport
from .timeline import Event, Kind, utc


class ProxyPresenceSource:
    """Kapselt vorhandene Personenereignisse ohne Schwellen oder Zeiten zu ändern."""

    @staticmethod
    def present(event: Event) -> PresenceReport:
        if event.kind not in (Kind.PERSON_STRONG, Kind.PERSON_WEAK):
            raise ValueError("Nur Personenereignisse sind ein Proxy-Präsenzbeleg")
        return PresenceReport(
            f"presence:proxy:{event.event_id}", "present", "provisional_proxy",
            "proxy", event.event_id, event.effective_at, event.detected_at, True,
        )

    @staticmethod
    def retract(event: Event, source_ref: str) -> PresenceReport:
        """Expliziter Belegverlust, niemals eine gemessene Leermeldung."""
        return PresenceReport(
            f"presence:proxy:retract:{event.event_id}:{source_ref}",
            "unknown", "proxy_retraction", "proxy", source_ref,
            event.effective_at, event.detected_at, True, event.kind.value,
        )


def binary_presence(
    entity_id: str, state: str | None, effective_at: datetime,
    received_at: datetime, *, source_ref: str | None = None,
) -> PresenceReport:
    """HA-Zustand ohne Verfallsfrist oder Abzug des Geräte-Abwesenheitsverzugs."""
    if not entity_id.startswith("binary_sensor."):
        raise ValueError("Präsenz benötigt eine binary_sensor-Entität")
    effective_at, received_at = utc(effective_at), utc(received_at)
    raw = state if state is not None else "missing"
    reference = source_ref or f"{entity_id}:{effective_at.isoformat()}:{raw}"
    valid = raw in ("on", "off")
    return PresenceReport(
        f"presence:direct:{reference}",
        {"on": "present", "off": "absent"}.get(raw, "unknown"),
        "direct_presence", entity_id, reference, effective_at, received_at,
        valid, None if valid else raw,
    )


@dataclass(frozen=True)
class PresenceObservation:
    """Beobachteter zusammenhängender Abschnitt, unabhängig von Aufgüssen."""
    source: str
    started_at: datetime
    start_report_id: str
    ended_at: datetime | None = None
    end_report_id: str | None = None
    end_reason: str | None = None


class PresenceProjection:
    """Proxy führt; direkte Belegung bleibt bis zur Gangregelung Beobachtung.

    Meldungen können aus dem Archiv eingelesen werden. IDs bleiben auch beim
    Neuladen gleich; der erneut festgestellte Empfang erzeugt kein neues Ereignis.
    """

    def __init__(self, active_source: str = "proxy"):
        if active_source != "proxy":
            raise ValueError("Direkter Präsenzbetrieb benötigt noch Fachentscheidungen")
        self.active_source = active_source
        self.reports: dict[str, PresenceReport] = {}
        self.current: PresenceReport | None = None
        self.external: dict[str, PresenceReport] = {}

    def accept(self, report: PresenceReport) -> bool:
        previous = self.reports.get(report.report_id)
        if previous is not None:
            if replace(report, received_at=previous.received_at) != previous:
                raise ValueError("Präsenz-ID mit abweichendem Inhalt wiederverwendet")
            return False
        self.reports[report.report_id] = report
        if report.source == "proxy":
            # A retraction removes only the proxy evidence it names.  Detector
            # catch-up can emit an old retraction after a newer person signal
            # in the same runtime cycle; that historical correction must not
            # replace the currently observed newer evidence.
            if (
                report.assertion != "proxy_retraction"
                or (
                    self.current is not None
                    and self.current.source == "proxy"
                    and (
                        self.current.source_ref == report.source_ref
                        # Availability is not a competing person evidence.  It
                        # may be recorded after the candidate and before its
                        # delayed, otherwise valid retraction is delivered.
                        or self.current.occupancy != "present"
                    )
                )
            ):
                self.current = report
        else:
            previous = self.external.get(report.source)
            if previous is None or report.effective_at >= previous.effective_at:
                self.external[report.source] = report
        return True

    @property
    def observations(self) -> tuple[PresenceObservation, ...]:
        result = []
        active: dict[str, PresenceObservation] = {}
        reports = sorted(self.reports.values(), key=lambda r: (r.effective_at, r.received_at))
        for report in reports:
            if report.assertion != "direct_presence":
                continue
            source = report.source
            if report.available and report.occupancy == "present":
                if source not in active:
                    active[source] = PresenceObservation(
                        source, report.effective_at, report.report_id,
                    )
            elif source in active:
                result.append(replace(
                    active.pop(source), ended_at=report.effective_at,
                    end_report_id=report.report_id,
                    end_reason="absent" if report.occupancy == "absent" else report.reason or "unknown",
                ))
        return tuple(sorted(result + list(active.values()), key=lambda o: o.started_at))
