"""Konten-Vorschlaege fuer die Korrekturliste.

Zwei Arten von Konten, wie sie im Buchungssatz gebraucht werden:

  Sachkonto    = Soll-Konto, also die Aufwandsart (z. B. 3400 Wareneingang 19 %)
  Kreditorkonto = Haben-Konto, also das Lieferantenkonto (z. B. 70001 div. Lieferanten)

Die Vorschlaege stehen in der Datei konten.json neben dem Programm. Ist die Datei
noch nicht da, werden die Anfangswerte benutzt. Neue Konten, die in der Liste
eingetragen werden, kommen automatisch in diese Datei hinein - man muss also
niemals im Programm etwas eintippen.

Zusaetzlich merkt sich das Programm Konten **je Lieferant** (Abschnitt "gelernt"
in konten.json): einmal in der Liste geaendert, wird das Konto beim naechsten
PDF fur jeden Beleg mit diesem Lieferantennamen vorgeschlagen.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from difflib import SequenceMatcher
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
KONTEN_DATEI = WURZEL / "konten.json"

SACH = "sach"
KREDITOR = "kreditor"
GELERNT = "gelernt"


@dataclass
class Konto:
    nr: str
    text: str

    def __str__(self) -> str:
        return anzeige(self.nr, self.text)


def anzeige(nr: str, text: str = "") -> str:
    """Darstellung im Dropdown, z. B. '3400  -  Wareneingang 19 %'."""
    nr = (nr or "").strip()
    text = (text or "").strip()
    return f"{nr}  -  {text}" if text else nr


_TEILER = re.compile(r"^\s*([0-9][0-9 /]{1,9}|[A-Za-z0-9]{2,12})\s*(?:[-\u2013\u2014]\s*)?(.*)$")


def zerlege(eintrag: str) -> tuple[str, str]:
    """'3400  -  Wareneingang 19 %' -> ('3400', 'Wareneingang 19 %')."""
    s = (eintrag or "").strip()
    if not s:
        return "", ""
    m = _TEILER.match(s)
    if not m:
        return "", s
    return m.group(1).strip(), m.group(2).strip()


# Rechtsform-Zusaeze, die beim Vergleich von Lieferantennamen keine Rolle spielen.
RECHTSFORM = {"gmbh", "co", "kg", "ag", "ug", "ohg", "se", "eg", "inc", "llc",
              "ltd", "bv", "sa", "srl", "spa", "plc", "ab", "as"}


def schluessel(name: str) -> str:
    """Vergleichsform eines Lieferantennamens.

    Kleinbuchstaben, ohne Satzzeichen und ohne Rechtsform-Zusatz:
    'Telekom Deutschland GmbH' und 'telekom-deutschland-gmbh' ergeben beide
    'telekom deutschland'. Damit wirkt eine gemerkte Zuordnung auch dann, wenn
    die PDF den Namen etwas anders schreibt.
    """
    s = (name or "").casefold().replace("\u00df", "ss")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(w for w in s.split() if w not in RECHTSFORM)


# Anfangsbestzung - genau die beiden Wuensche vorn, dahinter die ublichen
# Aufwandsarten aus den Lieferanten-Profilen (SKR03).
ANFANG_SACH = [
    Konto("3400", "Wareneingang 19 %"),
    Konto("3401", "Wareneingang 7 %"),
    Konto("3420", "bezogene Leistungen 19 %"),
    Konto("4930", "Kfz-Kosten / Werkstatt"),
    Konto("4950", "Telekommunikation / Porto"),
    Konto("4800", "Werbe- und Betriebskosten"),
    Konto("6071", "Bewirtungskosten"),
]
ANFANG_KREDITOR = [
    Konto("70001", "div. Lieferanten"),
    Konto("3300", "Verbindlichkeiten aus LuL"),
]


@dataclass
class Konten:
    sach: list[Konto]
    kreditor: list[Konto]
    # gemerkte Zuordnung je Lieferantennamen: schluessel -> {"name", "sach", "kreditor"}
    gelernt: dict[str, dict[str, str]] = field(default_factory=dict)

    # ---- Zugriff -----------------------------------------------------------
    def liste(self, art: str) -> list[Konto]:
        return self.sach if art == SACH else self.kreditor

    def text_fuer(self, art: str, nr: str) -> str:
        nr = (nr or "").strip()
        for k in self.liste(art):
            if k.nr == nr:
                return k.text
        return ""

    def vorschlage(self, art: str) -> list[str]:
        return [anzeige(k.nr, k.text) for k in self.liste(art)]

    def kennt(self, art: str, nr: str) -> bool:
        nr = (nr or "").strip()
        return any(k.nr == nr for k in self.liste(art))

    def ergaenze(self, art: str, nr: str, text: str = "") -> bool:
        """Konto aufnehmen, wenn es die Nummer noch nicht gibt. True = neu."""
        nr = (nr or "").strip()
        if not nr or self.kennt(art, nr):
            return False
        self.liste(art).append(Konto(nr, (text or "").strip()))
        return True

    def text_setzen(self, art: str, nr: str, text: str) -> bool:
        """Kontotext aendern (True wenn das Konto existiert)."""
        nr = (nr or "").strip()
        for k in self.liste(art):
            if k.nr == nr:
                k.text = (text or "").strip()
                self.speichere()
                return True
        return False

    # ---- Gemerkte Zuordnungen je Lieferant ---------------------------------
    def merke(self, art: str, lieferant: str, nr: str) -> bool:
        """Konto fuer diesen Lieferanten merken. True wenn das ein neuer Merksatz ist."""
        sch = schluessel(lieferant)
        nr = (nr or "").strip()
        if not sch or not nr:
            return False
        vorher = self.gelernt.get(sch, {}).get(art, "")
        eintrag = self.gelernt.setdefault(sch, {})
        eintrag["name"] = (lieferant or "").strip()
        eintrag[art] = nr
        if vorher == nr:
            return False
        self.speichere()
        return True

    def gelernt_fuer(self, art: str, lieferant: str) -> str:
        """Gemerktes Konto zu diesem Lieferanten ('' wenn nichts gemerkt wurde).

        derselbe Name in anderer Schreibweise wird wiedererkannt; bei kleinen
        Unterschieden (etwa Buchstabenfehlern aus der OCR, ab rund 90 % gleich)
        ebenfalls.
        """
        sch = schluessel(lieferant)
        if not sch:
            return ""
        direkt = self.gelernt.get(sch, {}).get(art, "")
        if direkt:
            return direkt
        bestes, wert = 0.9, ""
        for key, eintrag in self.gelernt.items():
            nr = eintrag.get(art, "")
            if not nr:
                continue
            aehnlichkeit = SequenceMatcher(None, sch, key).ratio()
            if aehnlichkeit > bestes:
                bestes, wert = aehnlichkeit, nr
        return wert

    def gemerkte(self) -> list[dict[str, str]]:
        """Alle Merksaetze, fuer die Anzeige im Fenster."""
        return [eintrag for eintrag in self.gelernt.values() if eintrag]

    def lerne_zuruecksetzen(self) -> int:
        """Alle gemerkten Zuordnungen loeschen; Anzahl zurueckgeben."""
        anzahl = len(self.gelernt)
        self.gelernt = {}
        self.speichere()
        return anzahl

    # ---- Datei -------------------------------------------------------------
    def speichere(self, pfad: Path = KONTEN_DATEI) -> None:
        daten = {SACH: [asdict(k) for k in self.sach],
                 KREDITOR: [asdict(k) for k in self.kreditor],
                 GELERNT: self.gelernt}
        pfad.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")


def laden(pfad: Path = KONTEN_DATEI) -> Konten:
    """konten.json lesen; fehlt sie, mit den Anfangswerten starten."""
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Konten(list(ANFANG_SACH), list(ANFANG_KREDITOR))
    sach = [Konto(str(d.get("nr", "")).strip(), str(d.get("text", "")).strip())
            for d in daten.get(SACH, []) if str(d.get("nr", "")).strip()]
    kred = [Konto(str(d.get("nr", "")).strip(), str(d.get("text", "")).strip())
            for d in daten.get(KREDITOR, []) if str(d.get("nr", "")).strip()]
    if not sach:
        sach = list(ANFANG_SACH)
    if not kred:
        kred = list(ANFANG_KREDITOR)
    gelernt: dict[str, dict[str, str]] = {}
    roh = daten.get(GELERNT)
    if isinstance(roh, dict):
        for name, eintrag in roh.items():
            if not isinstance(eintrag, dict):
                continue
            werte = {k: str(eintrag.get(k, "")).strip()
                     for k in ("name", SACH, KREDITOR) if str(eintrag.get(k, "")).strip()}
            if werte:
                gelernt[schluessel(str(name))] = werte
    return Konten(sach, kred, gelernt)
