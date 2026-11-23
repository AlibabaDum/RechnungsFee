"""Datenmodell: ein erkannter Beleg (= spaeter ein Buchungssatz-Paar)."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def money(value) -> Decimal | None:
    if value in (None, ""):
        return None
    d = Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Beleg:
    # Herkunft
    seiten: list[int] = field(default_factory=list)
    profil: str = ""
    grund: str = ""                 # warum diese Seiten zusammengehoeren
    duplizat_von: int | None = None # Index des Originalbelegs
    # Kopfdaten
    lieferant: str = ""
    lieferant_idnr: str = ""
    belegdatum: str = ""          # TT.MM.JJJJ
    leistungsdatum: str = ""
    rechnungsnummer: str = ""
    bestellnummer: str = ""
    # Betraege
    netto: Decimal | None = None
    ust_satz: int | None = None   # 19, 7, 0
    ust_betrag: Decimal | None = None
    brutto: Decimal | None = None
    waehrung: str = "EUR"
    # Buchnung (vom Nutzer beeinflussbar)
    soll_konto: str = ""
    soll_konto_vorschlag: str = ""  # vom Profil empfohlene Kontonummer
    haben_konto: str = ""
    ust_konto: str = ""
    belegfeld1: str = ""          # Rechnungsnummer (max 36 Zeichen)
    belegfeld2: str = ""          # Kunde / Vorgang (max 12 Zeichen)
    text: str = ""                # Buchungstext (max 60 Zeichen)
    zahlungsart: str = ""
    # Qualitaet
    vertrauen: dict[str, float] = field(default_factory=dict)
    flag: list[str] = field(default_factory=list)
    quelle: str = ""              # Rohtext-Auszuege zur Kontrolle

    # ---- abgeleitete Werte ------------------------------------------------
    @property
    def summen_ok(self) -> bool:
        if None in (self.netto, self.ust_betrag, self.brutto):
            return False
        return abs((self.netto + self.ust_betrag) - self.brutto) <= Decimal("0.02")

    def anzahl_unklar(self) -> int:
        return len(self.flag)

    def fehlende_felder(self) -> list[str]:
        out = []
        for name in ("belegdatum", "rechnungsnummer", "brutto", "netto", "ust_betrag"):
            if not getattr(self, name):
                out.append(name)
        return out

    # ---- Darstellung ------------------------------------------------------
    # Reihenfolge fuer die Korrekturliste: erst das Sachkonto (Soll), dann der
    # Lieferant mit seinem Kreditoren-Konto (Haben), dann die Zahlen.
    CSV_SPALTEN = [
        "Sachkonto", "Sachkonto-Text", "Seiten", "Lieferant",
        "Lieferanten-Konto", "Lieferanten-Konto-Text", "Profil",
        "Belegdatum", "Rechnungsnummer",
        "Netto", "USt-Satz %", "USt-Betrag", "Brutto",
        "Belegfeld 1", "Buchungstext", "Dublette", "Pruefung", "Konfidenz",
    ]

    @property
    def buchbar(self) -> bool:
        return self.duplizat_von is None

    def pruef_text(self) -> str:
        """Kurze Warnung fuer die Liste; 'OK' wenn alles zusammenpasst."""
        melden = []
        if self.duplizat_von is not None:
            melden.append("Dublette")
        if not self.summen_ok:
            melden.append("netto + USt = brutto stimmt nicht")
        fehlend = self.fehlende_felder()
        if fehlend:
            melden.append("fehlt: " + ", ".join(fehlend))
        for f in self.flag:
            if f not in melden:
                melden.append(f)
        return "; ".join(melden) if melden else "OK"

    def als_zeile(self, konten=None) -> list[str]:
        conf = min(self.vertrauen.values()) if self.vertrauen else 0.0
        text_sach = konten.text_fuer("sach", self.soll_konto) if konten else ""
        text_kred = konten.text_fuer("kreditor", self.haben_konto) if konten else ""
        return [
            self.soll_konto, text_sach,
            ",".join(str(s) for s in self.seiten), self.lieferant,
            self.haben_konto, text_kred, self.profil,
            self.belegdatum, self.rechnungsnummer,
            _dec(self.netto), "" if self.ust_satz is None else str(self.ust_satz),
            _dec(self.ust_betrag), _dec(self.brutto),
            self.belegfeld1, self.text,
            "" if self.buchbar else "ja", self.pruef_text(), f"{conf:.2f}",
        ]

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("netto", "ust_betrag", "brutto"):
            d[k] = str(d[k]) if d[k] is not None else ""
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Beleg":
        feld = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for k in ("netto", "ust_betrag", "brutto"):
            feld[k] = money(feld.get(k)) if feld.get(k) not in (None, "") else None
        return cls(**feld)


def _dec(v) -> str:
    return "" if v is None else str(v)
