"""DATEV-Buchungsstapel im EXTF-Format (CSV).

WICHTIG: DATEV liest diesen Stapel nach POSITION, nicht nach Spaltennamen. Die
Kopfzeilen und Spaltenbelegungen sind hier aus der oeffentlichen EXTF-Beschreibung
abgeleitet. Bitte einmal mit einem echten DATEV-Export deiner Kanzlei abgleichen:

    python -m belegerkennung.cli datei.pdf --datev stapel.csv --datev-vorlage datev_export.csv

Mit --datev-vorlage werden die zwei Kopfzeilen und die Spaltenbelegung aus einer
bestehenden DATEV-Exportdatei uebernommen; dann passen Formatversion und
Spaltennamen garantiert.

Fuellpositionen (1-basiert, EXTF Formatversion 13, 125 Felder pro Zeile):
   1 Umsatz (Betrag, Komma)      2 Sollkennzeichen S/H (gilt fuer Feld 7)
   7 Konto                       8 Gegenkonto
   9 BU-Schluessel (bei Automatikkonten leer)
  10 Belegdatum TTMM             11 Belegfeld 1 (max 36)   12 Belegfeld 2 (max 12)
  14 Buchungstext (max 60)       37/38 KOST1/KOST2         40 EU-Land (ISO, DE leer)
 114 Festschreibung 0           118 Generalumkehr ""
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .model import Beleg

ANZAHL_FELDER = 125
FELD = {
    "umsatz": 1,
    "sollkenn": 2,
    "konto": 7,
    "gegenkonto": 8,
    "bu": 9,
    "belegdatum": 10,
    "belegfeld1": 11,
    "belegfeld2": 12,
    "text": 14,
    "kost1": 37,
    "kost2": 38,
    "eu_land": 40,
    "festschreibung": 114,
    "generalumkehr": 118,
}

# BU-Schluessel fuer Vorsteuer (SKR03/04 gleich); Automatikkonten: keine Angabe
BU_FUER_SATZ = {19: "9", 16: "3", 7: "8", 5: "1", 0: ""}

# Konten, bei denen DATEV die Vorsteuer automatisch verbucht ( Automatikkonten )
AUTOMATIKKONTEN = {"3400", "3401", "3403", "3404", "3405", "3406", "3407", "3408",
                   "3409", "3410", "3411", "3413", "3415", "3417", "3419", "3421",
                   "3423", "3424", "3425", "3427", "3429", "3431", "3433", "3435",
                   "3437", "3439", "3441", "3445", "3449", "3451", "3455", "3463",
                   "3467", "3471", "3475", "3489", "3499", "4930", "4940", "4950"}

VORSTEUER_KONTO = "1401"   # Abziehbare Vorsteuer 19 % (SKR03)


@dataclass
class DatevEinstellung:
    berater_nr: str = ""
    mandant_nr: str = ""
    jahr: int | None = None          # Buchungsjahr; None = aus den Belegen
    kontenlaenge: str = "4"
    rechnungslegung: str = "0"       # 0 = Einnahmen/Ausgaben, 30/50 = Bilanz
    wahrung: str = "EUR"
    modus: str = "A"                 # A = 1 Zeile brutto, B = netto + Vorsteuer
    festschreibung: str = "0"

    def to_dict(self) -> dict:
        return {"berater_nr": self.berater_nr, "mandant_nr": self.mandant_nr,
                "jahr": self.jahr, "kontenlaenge": self.kontenlaenge,
                "rechnungslegung": self.rechnungslegung, "wahrung": self.wahrung,
                "modus": self.modus, "festschreibung": self.festschreibung}


def kopfzeilen(e: DatevEinstellung, jahr: int) -> list[str]:
    """Die zwei Kopfzeilen des EXTF-Stapels (Meta-Zeile + Spaltennamen)."""
    meta = ['"EXTF"', "700", "21", '"Buchungsstapel"', "13", "", "", '""', "",
            '""', e.berater_nr or '""', e.mandant_nr or '""',
            f'"0101{jahr}"', f'"3112{jahr}"', e.kontenlaenge, "", "", "", "",
            "1", "1", "1", str(jahr), e.rechnungslegung, "", f'"{e.wahrung}"',
            "", "", "", "", ""]
    while len(meta) < 31:
        meta.append('""')
    spalten = [f'"Spalte {i}"' for i in range(1, ANZAHL_FELDER + 1)]
    for name, pos in FELD.items():
        spalten[pos - 1] = f'"{name}"'
    return [";".join(meta), ";".join(spalten)]


def _ttmm(datum: str) -> str:
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", datum or "")
    return f"{m.group(1)}{m.group(2)}" if m else ""


def _betrag(w: Decimal | None) -> str:
    if w is None:
        return ""
    return f"{abs(w):.2f}".replace(".", ",")


def _text(w: str, maxlaenge: int) -> str:
    w = (w or "")[:maxlaenge].replace("_", "-")
    return '"' + w.replace('"', "'") + '"'


def _zeile(e: DatevEinstellung, feld_werte: dict[str, object]) -> str:
    felder = [None] * (ANZAHL_FELDER + 1)
    for name, wert in feld_werte.items():
        pos = FELD.get(name)
        if pos:
            felder[pos] = wert
    out = []
    for i in range(1, ANZAHL_FELDER + 1):
        v = felder[i]
        if v is None or v == "":
            out.append("")
        elif isinstance(v, str) and v.startswith('"'):
            out.append(v)
        else:
            out.append(str(v))
    return ";".join(out)


def buchungszeilen(belege: list[Beleg], e: DatevEinstellung) -> list[str]:
    zeilen = []
    for b in belege:
        if b.duplizat_von is not None:
            continue
        datum = _ttmm(b.belegdatum)
        feld1 = _text(b.belegfeld1 or b.rechnungsnummer, 36)
        feld2 = _text(b.belegfeld2, 12)
        text = _text(b.text or f"Rechnung {b.lieferant or b.profil}", 60)
        konto = b.soll_konto or "3400"
        haben = b.haben_konto or ""
        automatik = konto in AUTOMATIKKONTEN
        common = {"belegdatum": datum, "belegfeld1": feld1, "belegfeld2": feld2,
                  "text": text, "kost1": "", "kost2": "", "eu_land": "",
                  "festschreibung": e.festschreibung, "generalumkehr": '""'}
        if e.modus.upper() == "B" and b.netto is not None:
            ust_konto = b.ust_konto or VORSTEUER_KONTO
            zeilen.append(_zeile(e, {**common, "umsatz": _betrag(b.netto),
                                     "sollkenn": "S" if konto else "",
                                     "konto": konto, "gegenkonto": haben,
                                     "bu": "" if automatik else BU_FUER_SATZ.get(b.ust_satz or 0, "")}))
            if b.ust_betrag:
                zeilen.append(_zeile(e, {**common, "umsatz": _betrag(b.ust_betrag),
                                         "sollkenn": "S", "konto": ust_konto,
                                         "gegenkonto": haben, "bu": ""}))
        else:
            betrag = b.brutto if b.brutto is not None else b.netto
            zeilen.append(_zeile(e, {**common, "umsatz": _betrag(betrag),
                                     "sollkenn": "S" if konto else "",
                                     "konto": konto, "gegenkonto": haben,
                                     "bu": "" if automatik else BU_FUER_SATZ.get(b.ust_satz or 0, "")}))
    return zeilen


def lese_vorlage(pfad: str | Path) -> tuple[list[str], dict[str, int]]:
    """Kopfzeilen + Spaltenbelegung aus einem echten DATEV-Export uebernehmen."""
    zeilen = Path(pfad).read_text(encoding="iso-8859-1").splitlines()
    if len(zeilen) < 2:
        raise ValueError("Vorlage enthaelt keine Kopfzeilen")
    spalten = [s.strip().strip('"').lower() for s in zeilen[1].split(";")]
    rueck = {}
    suchwort = {"umsatz": ("umsatz", "betrag"), "sollkenn": ("sollkennzeichen", "s/h"),
                "konto": ("konto", "sollkonto"), "gegenkonto": ("gegenkonto",),
                "bu": ("bu-schluessel", "bu schluessel", "buenschluessel"),
                "belegdatum": ("belegdatum",), "belegfeld1": ("belegfeld 1", "belegfeld1"),
                "belegfeld2": ("belegfeld 2", "belegfeld2"),
                "text": ("buchungstext", "text"), "kost1": ("kost1", "kostenstelle 1"),
                "kost2": ("kost2", "kostenstelle 2"), "eu_land": ("eu-land", "ust-land"),
                "festschreibung": ("festschreibung",), "generalumkehr": ("generalumkehr",)}
    for name, worte in suchwort.items():
        for i, sp in enumerate(spalten, 1):
            if sp in worte:
                rueck[name] = i
                break
    if len(zeilen[1].split(";")) > ANZAHL_FELDER:
        globals()["ANZAHL_FELDER"] = len(zeilen[1].split(";"))
    return zeilen[:2], rueck


def schreibe(belege: list[Beleg], pfad: str | Path, e: DatevEinstellung | None = None,
             vorlage: str | Path | None = None) -> Path:
    e = e or DatevEinstellung()
    jahre = [int(b.belegdatum[-4:]) for b in belege if re.match(r"\d{2}\.\d{2}\.\d{4}", b.belegdatum or "")]
    jahr = e.jahr or (max(set(jahre), key=jahre.count) if jahre else 2026)
    global FELD, ANZAHL_FELDER
    if vorlage:
        koepfe, gefundene = lese_vorlage(vorlage)
        if gefundene:
            FELD = gefundene
    else:
        koepfe = kopfzeilen(e, jahr)
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    text = "\r\n".join(koepfe + buchungszeilen(belege, e)) + "\r\n"
    pfad.write_bytes(text.encode("iso-8859-1"))
    return pfad
