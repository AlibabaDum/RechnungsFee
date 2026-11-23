"""CSV-Ausgabe der Korrekturliste (offenes Format, in Excel oeffnbar).

Trenner ';' und deutsche Kommazahlen, damit Excel die Spalten sofort erkennt.
Spaltenfolge wie in der Bildschirmliste: Sachkonto vorn, Lieferanten-Konto
direkt neben dem Lieferanten.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from .model import Beleg

_SPALTE_KOMMA = {"Netto", "USt-Betrag", "Brutto"}


def _komma(wert: str) -> str:
    return wert.replace(".", ",") if wert else wert


def csv_zeilen(belege: list[Beleg], konten=None, nur_buchbare: bool = False) -> list[list[str]]:
    zeilen = [list(Beleg.CSV_SPALTEN)]
    spalten = Beleg.CSV_SPALTEN
    for b in belege:
        if nur_buchbare and not b.buchbar:
            continue
        w = b.als_zeile(konten)
        zeilen.append([_komma(v) if s in _SPALTE_KOMMA else v
                       for s, v in zip(spalten, w)])
    return zeilen


def csv_text(belege: list[Beleg], konten=None, nur_buchbare: bool = False) -> str:
    puffer = io.StringIO()
    writer = csv.writer(puffer, delimiter=";", lineterminator="\r\n")
    writer.writerows(csv_zeilen(belege, konten, nur_buchbare))
    return puffer.getvalue()


def schreibe(belege: list[Beleg], pfad: str | Path, konten=None,
             nur_buchbare: bool = False) -> Path:
    pfad = Path(pfad)
    if pfad.parent != Path(""):
        pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(csv_text(belege, konten, nur_buchbare), encoding="utf-8-sig", newline="")
    return pfad


def lese(pfad: str | Path) -> list[Beleg]:
    """Korrigierte CSV-Liste zuruecklesen (fuer spaetere Bearbeutung)."""
    rows = list(csv.reader(Path(pfad).read_text(encoding="utf-8-sig").splitlines(),
                           delimiter=";"))
    if not rows:
        return []
    idx = {name: i for i, name in enumerate(rows[0])}

    def hol(row, name):
        i = idx.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    out = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        seiten = [int(s) for s in hol(row, "Seiten").split(",") if s.strip().isdigit()]
        flag = [f.strip() for f in hol(row, "Pruefung").split(";")
                if f.strip() and f.strip() != "OK"]
        out.append(Beleg(
            seiten=seiten,
            profil=hol(row, "Profil"),
            lieferant=hol(row, "Lieferant"),
            belegdatum=hol(row, "Belegdatum"),
            rechnungsnummer=hol(row, "Rechnungsnummer"),
            netto=_dec(hol(row, "Netto")),
            ust_satz=int(hol(row, "USt-Satz %")) if hol(row, "USt-Satz %").isdigit() else None,
            ust_betrag=_dec(hol(row, "USt-Betrag")),
            brutto=_dec(hol(row, "Brutto")),
            soll_konto=hol(row, "Sachkonto"),
            haben_konto=hol(row, "Lieferanten-Konto"),
            belegfeld1=hol(row, "Belegfeld 1"),
            text=hol(row, "Buchungstext"),
            duplizat_von=-1 if hol(row, "Dublette").lower().startswith("j") else None,
            flag=flag,
        ))
    return out


def _dec(wert: str):
    from decimal import Decimal
    wert = (wert or "").strip().replace(" ", "")
    if not wert:
        return None
    if "," in wert:
        wert = wert.replace(".", "").replace(",", ".")
    return Decimal(wert)
