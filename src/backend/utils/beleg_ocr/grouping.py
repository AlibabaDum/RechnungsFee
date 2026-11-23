"""Seiten zu Belegen gruppieren und Doppelgaenger erkennen."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ocr import PageText
from .fields import fold

_SEITE_RE = re.compile(r"\bseite\s*(\d{1,3})\s*(?:von|/)\s*(\d{1,3})", re.I)
_KENNWORT_NEU = [
    "rechnung", "rechnungsbeleg", "rechnung ueber", "gutschrift", "honorarnote",
    "vertrag", "monatsabrechnung", "prepaid", "auftrag", "bestaetigung",
]


@dataclass
class SeitenGruppe:
    seiten: list[int] = field(default_factory=list)
    profil: str | None = None
    grund: str = ""
    kennung: str = ""

    def seiten_text(self, pages: list[PageText]) -> str:
        return "\n".join(p.text for p in pages if p.page_no in self.seiten)


def _fortsetzung(page: PageText) -> tuple[int, int] | None:
    """(aktuelle Seite, Gesamtseiten) wenn die Seite eindeutig eine Fortsetzung ist."""
    m = _SEITE_RE.search(page.text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def seiten_gruppieren(pages: list[PageText],
                      profil_fuer_seite: dict[int, tuple[str | None, float]]) -> list[SeitenGruppe]:
    """Erste Gruppierung nach Seitenzahlern und Lieferantenwechsel.

    rueckgabe: Liste von SeitenGruppe, Reihenfolge wie im Dokument.
    """
    gruppen: list[SeitenGruppe] = []
    for page in pages:
        profil, _ = profil_fuer_seite.get(page.page_no, (None, 0.0))
        nr = _fortsetzung(page)
        aktuell, gesamt = nr if nr else (1, 1)
        if gruppen and aktuell > 1 and (
            gruppen[-1].profil in (None, profil) or profil is None
        ):
            gruppen[-1].seiten.append(page.page_no)
            gruppen[-1].grund += f" fortsetzung {aktuell}/{gesamt}"
            continue
        if gruppen and profil is None and gruppen[-1].profil is not None:
            # Seite ohne erkennbaren Absender -> Anlage zum vorherigen Beleg
            gruppen[-1].seiten.append(page.page_no)
            gruppen[-1].grund += " anlage"
            continue
        gruppen.append(SeitenGruppe(seiten=[page.page_no], profil=profil,
                                    grund="start" + (f"/{profil}" if profil else "/unbekannt")))
    return gruppen


def zusammenfuehren(gruppen: list[SeitenGruppe], kennung_fuer_gruppe: dict[int, str]) -> list[SeitenGruppe]:
    """Gruppen mit gleicher Lieferanten-Kennung + Rechnungsnummer zusammenfuehren.

    kennung_fuer_gruppe: index der Gruppe -> 'RECHNUNGSNUMMER' (leer wenn unbekannt).
    """
    zusammen: list[SeitenGruppe] = []
    for idx, g in enumerate(gruppen):
        kennung = kennung_fuer_gruppe.get(idx, "")
        if kennung:
            for andere in zusammen:
                if andere.profil == g.profil and andere.kennung == kennung:
                    andere.seiten.extend(g.seiten)
                    andere.grund += f" + {g.seiten} (gleiche nummer)"
                    break
            else:
                g.kennung = kennung
                zusammen.append(g)
        else:
            zusammen.append(g)
    for g in zusammen:
        g.seiten = sorted(set(g.seiten))
    return zusammen


def doppelte_belege(belege) -> list[tuple[int, int]]:
    """Paare (i, j) bei denen Beleg j einen bereits bekannten Beleg i doppelt."""
    paare = []
    for j, b in enumerate(belege):
        for i, a in enumerate(belege):
            if i >= j:
                continue
            if a.lieferant and b.lieferant and a.lieferant != b.lieferant:
                continue
            if a.rechnungsnummer and b.rechnungsnummer and a.rechnungsnummer != b.rechnungsnummer:
                continue
            if a.brutto is not None and b.brutto is not None and a.brutto != b.brutto:
                continue
            if a.belegdatum == b.belegdatum:
                continue
            if a.rechnungsnummer and b.rechnungsnummer and a.rechnungsnummer == b.rechnungsnummer:
                paare.append((i, j))
                break
    return paare
