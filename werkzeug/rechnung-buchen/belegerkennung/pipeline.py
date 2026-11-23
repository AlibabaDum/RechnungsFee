"""Ablauf: PDF -> OCR -> Seiten gruppiert -> Belege -> Duplikate markiert."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .extract import belege_aus_seiten
from .fields import fold, fuzzy
from .grouping import SeitenGruppe, seiten_gruppieren
from .model import Beleg
from .ocr import PageText, ocr_pdf
from .profiles import passendes_profil


@dataclass
class Ergebnis:
    quelle: str = ""
    seiten: list[PageText] = field(default_factory=list)
    gruppen: list[SeitenGruppe] = field(default_factory=list)
    belege: list[Beleg] = field(default_factory=list)

    @property
    def aktive(self) -> list[Beleg]:
        """Belege, die gebucht werden sollen (Doppelgaenger aus)."""
        return [b for b in self.belege if b.duplizat_von is None]


def belege_aus_pdf(pdf_path: str, dpi: int = 300, use_cache: bool = True,
                   progress=None, profil_min: float = 0.80) -> Ergebnis:
    seiten = ocr_pdf(pdf_path, dpi=dpi, use_cache=use_cache, progress=progress)
    return belege_aus_seiten_objekten(str(pdf_path), seiten, profil_min=profil_min)


def belege_aus_seiten_objekten(quelle: str, seiten: list[PageText],
                               profil_min: float = 0.80) -> Ergebnis:
    profil_fuer_seite = {p.page_no: passendes_profil(fold(p.text)) for p in seiten}
    gruppen = seiten_gruppieren(seiten, profil_fuer_seite)
    belege = belege_aus_seiten(seiten, gruppen)

    # Nummer bekannt? Dann Gruppen zusammenfuehren, die zur selben Rechnung hoeren.
    # Nur wenn die Nummer sicher gelesen wurde und die Seiten unmittelbar beieinander
    # liegen - sonst verschmelzen zwei Rechnungen desselben Lieferaenders.
    kennung = {i: f"{b.profil}|{b.rechnungsnummer}" if b.rechnungsnummer else ""
               for i, b in enumerate(belege)}
    neue_gruppen: list[SeitenGruppe] = []
    for idx, g in enumerate(gruppen):
        k = kennung.get(idx, "")
        sicher = belege[idx].vertrauen.get("rechnungsnummer", 0.0) >= 0.75
        if k and sicher:
            for andere in neue_gruppen:
                if andere.kennung != k:
                    continue
                abstand = min(abs(a - b) for a in andere.seiten for b in g.seiten)
                if abstand <= 2:
                    andere.seiten.extend(g.seiten)
                    andere.grund += f" +{g.seiten}"
                    break
            else:
                g.kennung = k
                neue_gruppen.append(g)
        else:
            neue_gruppen.append(g)
    if len(neue_gruppen) != len(gruppen):
        gruppen = neue_gruppen
        belege = belege_aus_seiten(seiten, gruppen)

    _doppelte_markieren(belege)
    return Ergebnis(quelle=str(quelle), seiten=seiten, gruppen=gruppen, belege=belege)


def ergebnis_als_dict(ergebnis: Ergebnis, indent: int = 2) -> str:
    """JSON fuer Werkzeuge, Korrekturen und den Wiederaufbau der Liste."""
    daten = {
        "quelle": ergebnis.quelle,
        "seiten": [p.to_dict() for p in ergebnis.seiten],
        "belege": [b.to_dict() for b in ergebnis.belege],
    }
    return json.dumps(daten, ensure_ascii=False, indent=indent)


def ergebnis_laden(pfad: str) -> Ergebnis:
    ro = json.loads(Path(pfad).read_text(encoding="utf-8"))
    seiten = [PageText.from_dict(d) for d in ro.get("seiten", [])]
    belege = [Beleg.from_dict(d) for d in ro.get("belege", [])]
    return Ergebnis(quelle=ro.get("quelle", ""), seiten=seiten, gruppen=[], belege=belege)


def _doppelte_markieren(belege: list[Beleg]) -> None:
    """Erkannte Dubletten markieren (dieselbe Rechnung, erneut im PDF)."""
    for j, b in enumerate(belege):
        for i, a in enumerate(belege):
            if i >= j or a.duplizat_von is not None:
                continue
            gleicher_lieferant = bool(a.lieferant) and a.lieferant == b.lieferant
            gleiche_nr = bool(a.rechnungsnummer) and a.rechnungsnummer == b.rechnungsnummer
            ahnliche_nr = bool(a.rechnungsnummer) and bool(b.rechnungsnummer) and (
                a.rechnungsnummer != b.rechnungsnummer
                and fuzzy(fold(a.rechnungsnummer), fold(b.rechnungsnummer)) >= 0.90)
            same_brutto = (a.brutto is not None and b.brutto is not None
                           and a.brutto == b.brutto)
            gleiches_datum = bool(a.belegdatum) and a.belegdatum == b.belegdatum
            if (gleiche_nr or ahnliche_nr) and (gleicher_lieferant or same_brutto):
                grund = "gleiche Rechnungsnummer" if gleiche_nr else "aehnliche Rechnungsnummer"
            elif gleicher_lieferant and same_brutto and gleiches_datum:
                grund = "gleicher Lieferant, Betrag und Datum"
            else:
                continue
            b.duplizat_von = i
            b.flag.append(f"Dublette von Beleg {i + 1} (Seiten {','.join(map(str, a.seiten))})"
                          f" wegen {grund} - wird nicht gebucht")
            break
