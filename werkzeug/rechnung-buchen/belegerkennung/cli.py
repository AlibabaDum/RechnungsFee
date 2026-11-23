"""Start: ohne Argumente oeffnet sich die Bildschirmliste, mit PDF-Angabe laeuft
die Kommandozeile (Tabelle + CSV/JSON), --gui zeigt die Liste zum Korrigieren."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .model import Beleg
from .pipeline import belege_aus_pdf, ergebnis_als_dict


def _tabelle(belege: list[Beleg]) -> str:
    kopf = ["Nr", "Seiten", "Sachkonto", "Profil", "Datum", "Rechnungsnummer", "netto",
            "%", "USt", "brutto", "Kreditor", "Pruefung"]
    zeilen = []
    for i, b in enumerate(belege, 1):
        zeilen.append([
            str(i) + ("*" if not b.buchbar else ""),
            ",".join(map(str, b.seiten)),
            b.soll_konto, b.profil[:16], b.belegdatum or "-",
            (b.rechnungsnummer or "-")[:22],
            f"{b.netto:>} " if b.netto is not None else "  -  ",
            str(b.ust_satz) if b.ust_satz is not None else "-",
            f"{b.ust_betrag:>} " if b.ust_betrag is not None else "  -  ",
            f"{b.brutto:>} " if b.brutto is not None else "  -  ",
            b.haben_konto, b.pruef_text()[:40],
        ])
    breiten = [max(len(z[j]) for z in [kopf] + zeilen) for j in range(len(kopf))]

    def fmt(zellen):
        return " | ".join(z.rjust(breiten[j]) if j in (6, 7, 8, 9) else z.ljust(breiten[j])
                          for j, z in enumerate(zellen))

    aus = [fmt(kopf), "-+-".join("-" * w for w in breiten)]
    aus += [fmt(z) for z in zeilen]
    return "\n".join(aus)


def oberflaeche(pdf: str | None = None) -> int:
    """Bildschirmliste starten (dort kann man alles korrigieren).

    Eine genannte, aber fehlende PDF-Datei fuehrt zu einer Meldung; die Liste
    bleibt offen, damit man die Datei dort selbst oeffnen kann.
    """
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .gui import HauptFenster

    app = QApplication.instance() or QApplication(list(sys.argv[:1]))
    fenster = HauptFenster()
    fenster.show()
    if pdf:
        pfeil = Path(pdf)
        if not pfeil.is_file():
            QMessageBox.warning(
                fenster, "Datei nicht gefunden",
                f"Diese Datei gibt es nicht:\n{pdf}\n\n"
                "Bitte oeffnen Sie die PDF-Datei ueber 'PDF oeffnen…'.")
        else:
            fenster.laden(pfeil)
    return app.exec()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="belegerkennung",
        description="Rechnungs-PDF (auch Scan) -> Liste mit Buchungssaetzen zum Korrigieren")
    ap.add_argument("pdf", nargs="?", help="PDF-Datei mit einer oder mehreren Rechnungen")
    ap.add_argument("--gui", action="store_true",
                    help="Liste auf dem Bildschirm oeffnen (Standard ohne PDF-Angabe)")
    ap.add_argument("--csv", help="CSV-Datei fuer die Buchungsliste schreiben")
    ap.add_argument("--json", help="JSON-Datei mit allen Details schreiben")
    ap.add_argument("--konto", help="Sachkonto fuer alle Zeilen vorsetzen, z. B. 3400")
    ap.add_argument("--dpi", type=int, default=300, help="Scan-Aufloesung fuer OCR")
    ap.add_argument("--frisch", action="store_true", help="OCR-Cache ignorieren")
    ap.add_argument("--alle", action="store_true", help="auch Doppelgaenger anzeigen")
    args = ap.parse_args(argv)

    if args.pdf is None or args.gui:
        # Eine fehlende Datei meldet sich im Fenster, nicht in der Konsole.
        return oberflaeche(args.pdf)

    if not Path(args.pdf).is_file():
        print(f"Datei nicht gefunden: {args.pdf}", file=sys.stderr)
        return 2

    def fortschritt(seite, gesamt):
        print(f"  OCR Seite {seite}/{gesamt}", file=sys.stderr, flush=True)

    print(f"Einlesen: {args.pdf}", file=sys.stderr)
    ergebnis = belege_aus_pdf(args.pdf, dpi=args.dpi, use_cache=not args.frisch,
                              progress=fortschritt)
    belege = ergebnis.belege if args.alle else ergebnis.aktive
    from .konten import KREDITOR, SACH, laden as konten_laden
    konten = konten_laden()
    for b in belege:
        gemerkt = konten.gelernt_fuer(SACH, b.lieferant)
        if gemerkt and (not b.soll_konto or b.soll_konto == b.soll_konto_vorschlag):
            b.soll_konto = gemerkt
        elif not b.soll_konto:
            b.soll_konto = b.soll_konto_vorschlag or konten.sach[0].nr
        if not b.haben_konto:
            b.haben_konto = (konten.gelernt_fuer(KREDITOR, b.lieferant)
                             or konten.kreditor[0].nr)
        if not b.text:
            b.text = f"Rechnung {b.lieferant or b.profil}"[:60]
    if args.konto:
        for b in belege:
            b.soll_konto = args.konto
    print(f"\n{len(ergebnis.seiten)} Seiten, {len(ergebnis.belege)} Belege "
          f"({len(ergebnis.aktive)} zu buchen, "
          f"{len(ergebnis.belege) - len(ergebnis.aktive)} Doppelgaenger)\n", file=sys.stderr)
    print(_tabelle(belege))
    print("\n* = Doppelgaenger, steht nicht in der Buchungsliste")

    if args.csv:
        from . import csv_export
        pfad = csv_export.schreibe(belege, args.csv, konten, nur_buchbare=False)
        print(f"CSV geschrieben: {pfad}")
    if args.json:
        Path(args.json).write_text(ergebnis_als_dict(ergebnis), encoding="utf-8", newline="")
        print(f"JSON geschrieben: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
