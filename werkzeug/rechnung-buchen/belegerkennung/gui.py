"""Bildschirmliste: erkannte Rechnungen ansehen, korrigieren, Konten waehlen.

Aufbau der Liste (links nach rechts):
  Sachkonto (Auswahl)  Sachkonto-Text  Seiten  Lieferant  Lieferanten-Konto
  (Auswahl)  Konto-Text  Profil  Belegdatum  Rechnungsnummer  netto  USt %
  USt-Betrag  brutto  Buchungstext  Dublette (ankreuzbar)  Pruefung

Die beiden Auswahl-Felder liegen, wie gewuenscht: das Sachkonto ganz links und
das Kreditoren-Konto direkt neben dem Lieferanten. Neue Kontonummern, die man
eintippt, bleiben in konten.json erhalten.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QThread, Qt, Signal, Slot)
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog,
                               QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QStyledItemDelegate, QTableView,
                               QVBoxLayout, QWidget)

from . import csv_export
from .konten import KREDITOR, SACH, Konten, anzeige, laden, schluessel, zerlege
from .model import Beleg
from .pipeline import belege_aus_pdf, ergebnis_laden
from .fields import parse_date

# Spaltennummern
C_SACH, C_SACHTEXT, C_SEITEN, C_LIEF, C_KRED, C_KREDTEXT, C_PROFIL, C_DATUM, C_NR, \
    C_NETTO, C_SATZ, C_UST, C_BRUTTO, C_TEXT, C_DUP, C_PRUEF = range(16)

KOPF = ["Sachkonto", "Sachkonto-Text", "Seiten", "Lieferant", "Lieferanten-Konto",
        "Konto-Text Lieferant", "Profil", "Belegdatum", "Rechnungsnummer", "netto",
        "USt %", "USt-Betrag", "brutto", "Buchungstext", "Dublette", "Pruefung"]

_BEARBEITBAR = {C_SACH, C_SACHTEXT, C_LIEF, C_KRED, C_KREDTEXT, C_PROFIL, C_DATUM,
                C_NR, C_NETTO, C_SATZ, C_UST, C_BRUTTO, C_TEXT}


def _betrag_text(wert) -> str:
    return "" if wert is None else str(wert).replace(".", ",")


def _betrag_lesen(text: str) -> Decimal | None:
    return csv_export._dec(text or "")


class KontoAuswahl(QStyledItemDelegate):
    """Auswahl-Feld fuer eine Kontonummer (freie Eingabe zusaetzlich erlaubt)."""

    def __init__(self, fenster, art: str, parent=None):
        super().__init__(parent)
        self.fenster = fenster
        self.art = art

    def createEditor(self, parent, option, index):
        feld = QComboBox(parent)
        feld.setEditable(True)
        feld.setInsertPolicy(QComboBox.NoInsert)
        feld.setMinimumWidth(230)
        feld.addItems(self.fenster.konten.vorschlage(self.art))
        return feld

    def setEditorData(self, feld, index):
        nr = index.data(Qt.ItemDataRole.DisplayRole) or ""
        feld.setCurrentText(anzeige(nr, self.fenster.konten.text_fuer(self.art, nr)))
        if not nr:
            feld.setCurrentIndex(-1)
        feld.lineEdit().selectAll()

    def setModelData(self, feld, modell, index):
        nr, text = zerlege(feld.currentText())
        if nr and self.fenster.konten.ergaenze(self.art, nr, text):
            self.fenster.konten.speichere()
            self.fenster.zeige_status(f"Konto {nr} neu in die Liste aufgenommen")
        weitere = modell.setze_konto(index.row(), index.column(), nr)
        name = (modell.belege[index.row()].lieferant or "").strip()
        if nr and name:
            was = "Aufwandskonto" if self.art == SACH else "Lieferanten-Konto"
            zusaetzlich = (f", ausserdem auf {weitere} weitere(n) Zeile(n) mit diesem "
                           f"Lieferanten gesetzt" if weitere else "")
            self.fenster.zeige_status(
                f"{name}: {was} {nr} gemerkt{zusaetzlich}. Gilt auch fuer kuenftige PDFs.")


class BelegModell(QAbstractTableModel):
    """Tabelle ueber der Liste der Belege; jede Aenderung geht in den Beleg zurueck."""

    def __init__(self, belege: list[Beleg], konten: Konten):
        super().__init__()
        self.belege = belege
        self.konten = konten

    # ---- Grundgeruest -----------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.belege)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(KOPF)

    def headerData(self, section, orient, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orient == Qt.Orientation.Horizontal:
            return KOPF[section]
        if role == Qt.ItemDataRole.TextAlignmentRole and orient == Qt.Orientation.Horizontal:
            return Qt.AlignmentFlag.AlignLeft
        return None

    def flags(self, index):
        grund = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == C_DUP:
            return grund | Qt.ItemFlag.ItemIsUserCheckable
        if index.column() in _BEARBEITBAR:
            return grund | Qt.ItemFlag.ItemIsEditable
        return grund

    # ---- Inhalte ----------------------------------------------------------
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        b = self.belege[index.row()]
        col = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == C_SACH:
                return b.soll_konto
            if col == C_SACHTEXT:
                return self.konten.text_fuer(SACH, b.soll_konto)
            if col == C_SEITEN:
                return ",".join(str(s) for s in b.seiten)
            if col == C_LIEF:
                return b.lieferant
            if col == C_KRED:
                return b.haben_konto
            if col == C_KREDTEXT:
                return self.konten.text_fuer(KREDITOR, b.haben_konto)
            if col == C_PROFIL:
                return b.profil
            if col == C_DATUM:
                return b.belegdatum
            if col == C_NR:
                return b.rechnungsnummer
            if col == C_NETTO:
                return _betrag_text(b.netto)
            if col == C_SATZ:
                return "" if b.ust_satz is None else str(b.ust_satz)
            if col == C_UST:
                return _betrag_text(b.ust_betrag)
            if col == C_BRUTTO:
                return _betrag_text(b.brutto)
            if col == C_TEXT:
                return b.text
            if col == C_PRUEF:
                return b.pruef_text()
            return None
        if role == Qt.ItemDataRole.CheckStateRole and col == C_DUP:
            return Qt.CheckState.Checked if not b.buchbar else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.BackgroundRole:
            if col == C_PRUEF and b.pruef_text() != "OK":
                return QColor("#ffd8d8")
            if not b.buchbar:
                return QColor("#ececec")
            if col in (C_NETTO, C_UST, C_BRUTTO) and not b.summen_ok:
                return QColor("#ffeec2")
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == C_PRUEF and b.pruef_text() != "OK":
                return "Bitte pruefen: " + b.pruef_text()
            if col == C_SEITEN and b.seiten:
                return "Seiten " + ", ".join(str(s) for s in b.seiten)
        return None

    def setData(self, index, wert, role=Qt.ItemDataRole.DisplayRole) -> bool:
        if not index.isValid():
            return False
        b = self.belege[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and col == C_DUP:
            b.duplizat_von = -1 if wert == Qt.CheckState.Checked else None
            self.dataChanged.emit(index.siblingAtColumn(0), index.siblingAtColumn(len(KOPF) - 1))
            return True
        if role != Qt.ItemDataRole.DisplayRole or col not in _BEARBEITBAR:
            return False
        text = (str(wert) if wert is not None else "").strip()
        if col == C_SACH:
            self.setze_konto(index.row(), C_SACH, text)
            return True
        if col == C_KRED:
            self.setze_konto(index.row(), C_KRED, text)
            return True
        if col == C_SACHTEXT:
            self.konten.text_setzen(SACH, b.soll_konto, text)
        elif col == C_KREDTEXT:
            self.konten.text_setzen(KREDITOR, b.haben_konto, text)
        elif col == C_NETTO:
            b.netto = _betrag_lesen(text)
        elif col == C_UST:
            b.ust_betrag = _betrag_lesen(text)
        elif col == C_BRUTTO:
            b.brutto = _betrag_lesen(text)
        elif col == C_SATZ:
            z = "".join(c for c in text if c.isdigit())
            b.ust_satz = int(z) if z else None
        elif col == C_DATUM:
            b.belegdatum = parse_date(text, self.jahr_hinweis()) or text
        elif col == C_LIEF:
            b.lieferant = text
        elif col == C_PROFIL:
            b.profil = text
        elif col == C_NR:
            b.rechnungsnummer = text
            b.belegfeld1 = text.replace("_", "-")[:36]
        elif col == C_TEXT:
            b.text = text
        self.dataChanged.emit(index.siblingAtColumn(0), index.siblingAtColumn(len(KOPF) - 1))
        return True

    # ---- Hilfe -------------------------------------------------------------
    def jahr_hinweis(self) -> int:
        jahre = [int(d[-4:]) for d in (b.belegdatum for b in self.belege)
                 if len(d) == 10 and d[-4:].isdigit()]
        if not jahre:
            from datetime import date
            return date.today().year
        return max(set(jahre), key=jahre.count)

    def setze_konto(self, zeile: int, spalte: int, nr: str) -> int:
        """Kontonummer setzen, den Text daneben mitnehmen und sie behalten.

        Eine Kontonummer gilt fuer den ganzen Lieferanten: sie wird auch in alle
        anderen Belege mit demselben Namen gesetzt und fuer kuenftige PDFs gemerkt
        (konten.json, Abschnitt "gelernt"). Rueckgabe: wie viele andere Zeilen
        dadurch ebenfalls umgestellt wurden.
        """
        b = self.belege[zeile]
        art = SACH if spalte == C_SACH else KREDITOR
        feld = "soll_konto" if art == SACH else "haben_konto"
        setattr(b, feld, nr)
        if nr and not self.konten.kennt(art, nr):
            self.konten.ergaenze(art, nr, "")
            self.konten.speichere()
        weitere = 0
        name = (b.lieferant or "").strip()
        if nr and name:
            merk = schluessel(name)
            for i, anderer in enumerate(self.belege):
                if i != zeile and schluessel(anderer.lieferant) == merk:
                    setattr(anderer, feld, nr)
                    weitere += 1
            self.konten.merke(art, name, nr)
        self.dataChanged.emit(self.index(0, 0),
                              self.index(max(0, len(self.belege) - 1), len(KOPF) - 1))
        return weitere

    def zeche_einfuegen(self) -> None:
        self.beginInsertRows(QModelIndex(), len(self.belege), len(self.belege))
        self.belege.append(Beleg(seiten=[], lieferant="",
                                 soll_konto=self.konten.sach[0].nr,
                                 haben_konto=self.konten.kreditor[0].nr,
                                 text="neue Zeile"))
        self.endInsertRows()


class OcrFaden(QThread):
    """OCR laeuft nebenbei, damit das Fenster nichthaengt."""

    fortschritt = Signal(int, int)
    fertig = Signal(object)
    fehler = Signal(str)

    def __init__(self, pfad: Path, dpi: int, frisch: bool, parent=None):
        super().__init__(parent)
        self.pfad, self.dpi, self.frisch = pfad, dpi, frisch

    def run(self):
        try:
            ergebnis = belege_aus_pdf(self.pfad, dpi=self.dpi, use_cache=not self.frisch,
                                      progress=lambda s, g: self.fortschritt.emit(s, g))
        except Exception as fehler:  # noqa: BLE001 - dem Nutzer zeigen
            self.fehler.emit(f"{type(fehler).__name__}: {fehler}")
            return
        self.fertig.emit(ergebnis)


class HauptFenster(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Belegerkennung - Rechnungen ansehen und Buchungsaetze korrigieren")
        self.konten = laden()
        self.belege: list[Beleg] = []
        self.ergebnis = None
        self.pfad_pdf: Path | None = None
        self.faden: OcrFaden | None = None

        self.tisch = QTableView(self)
        self.modell = BelegModell(self.belege, self.konten)
        self.tisch.setModel(self.modell)
        self.tisch.setItemDelegateForColumn(C_SACH, KontoAuswahl(self, SACH, self.tisch))
        self.tisch.setItemDelegateForColumn(C_KRED, KontoAuswahl(self, KREDITOR, self.tisch))
        self.tisch.verticalHeader().setVisible(True)
        self.tisch.verticalHeader().setDefaultSectionSize(24)
        self.tisch.horizontalHeader().setHighlightSections(False)
        self.tisch.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.tisch.setAlternatingRowColors(True)
        self.tisch.setWordWrap(False)
        self.tisch.setColumnWidth(C_SACH, 190)
        self.tisch.setColumnWidth(C_SACHTEXT, 170)
        self.tisch.setColumnWidth(C_SEITEN, 60)
        self.tisch.setColumnWidth(C_LIEF, 210)
        self.tisch.setColumnWidth(C_KRED, 190)
        self.tisch.setColumnWidth(C_KREDTEXT, 170)
        self.tisch.setColumnWidth(C_PROFIL, 120)
        self.tisch.setColumnWidth(C_DATUM, 90)
        self.tisch.setColumnWidth(C_NR, 150)
        for c in (C_NETTO, C_SATZ, C_UST, C_BRUTTO):
            self.tisch.setColumnWidth(c, 85)
        self.tisch.setColumnWidth(C_TEXT, 210)
        self.tisch.setColumnWidth(C_DUP, 70)
        self.tisch.setColumnWidth(C_PRUEF, 200)

        knopf = {}
        def knopf_reihenfolge(name):
            b = QPushButton(name, self)
            b.setMinimumWidth(110)
            knopf[name] = b
            return b

        self.oeffnen = knopf_reihenfolge("PDF oeffnen…")
        self.speichern = knopf_reihenfolge("CSV speichern…")
        self.einfuegen = knopf_reihenfolge("Zeile einfuegen")
        self.loeschen = knopf_reihenfolge("Zeile loeschen")
        self.merken = knopf_reihenfolge("Merke-Konten…")
        self.nur_buchbare = QPushButton("Dubletten weglassen: ja", self)
        self.nur_buchbare.setCheckable(True)
        self.nur_buchbare.setChecked(True)
        self.frisch = QPushButton("OCR neu", self)
        self.frisch.setCheckable(True)

        zeile = QHBoxLayout()
        for w in (self.oeffnen, self.speichern, self.einfuegen, self.loeschen,
                  self.merken, self.nur_buchbare, self.frisch):
            zeile.addWidget(w)
        zeile.addStretch(1)
        self.titel = QLabel("Bitte ein PDF mit Rechnungen oeffnen  (Stichwort: Datei oeffnen)", self)
        self.balken = QProgressBar(self)
        self.balken.setMaximumWidth(220)
        self.balken.setVisible(False)
        oben = QHBoxLayout()
        oben.addWidget(self.titel)
        oben.addStretch(1)
        oben.addWidget(self.balken)

        unten = QWidget(self)
        unten.setLayout(zeile)
        radmen = QVBoxLayout()
        radmen.addLayout(oben)
        radmen.addWidget(self.tisch)
        radmen.addWidget(unten)
        rahmen = QWidget(self)
        rahmen.setLayout(radmen)
        self.setCentralWidget(rahmen)
        self.statusBar().showMessage("bereit")

        self.oeffnen.clicked.connect(self.pdf_oeffnen)
        self.speichern.clicked.connect(self.csv_speichern)
        self.einfuegen.clicked.connect(self._zeile_einfuegen)
        self.loeschen.clicked.connect(self._zeile_loeschen)
        self.merken.clicked.connect(self._gemerkte_anzeigen)
        self.nur_buchbare.toggled.connect(
            lambda an: self.nur_buchbare.setText("Dubletten weglassen: ja" if an
                                                 else "Dubletten weglassen: nein"))
        self.frisch.toggled.connect(lambda an: self.frisch.setText("OCR neu: ja" if an
                                                                   else "OCR neu"))
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.pdf_oeffnen)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.csv_speichern)
        self.resize(1500, 700)

    # ---- Bedienung --------------------------------------------------------
    def pdf_oeffnen(self) -> None:
        start = str(self.pfad_pdf.parent) if self.pfad_pdf else str(Path.home())
        pfad, _ = QFileDialog.getOpenFileName(self, "PDF mit Rechnungen", start,
                                              "PDF-Dateien (*.pdf)")
        if pfad:
            self.laden(Path(pfad))

    def laden(self, pfad: Path) -> None:
        if self.faden is not None and self.faden.isRunning():
            QMessageBox.information(self, "Moment", "Das PDF wird noch gelesen.")
            return
        self.pfad_pdf = pfad
        self.titel.setText(f"lese {pfad.name} …")
        self.balken.setVisible(True)
        self.balken.setRange(0, 1)
        self.balken.setValue(0)
        self.faden = OcrFaden(pfad, 300, self.frisch.isChecked(), self)
        self.faden.fortschritt.connect(self._fortschritt)
        self.faden.fertig.connect(self._fertig)
        self.faden.fehler.connect(self._fehler)
        self.faden.start()

    @Slot(int, int)
    def _fortschritt(self, seite: int, gesamt: int) -> None:
        self.balken.setRange(0, max(gesamt, 1))
        self.balken.setValue(seite)
        self.statusBar().showMessage(f"OCR läuft: Seite {seite} von {gesamt}")

    @Slot(object)
    def _fertig(self, ergebnis) -> None:
        self.ergebnis = ergebnis
        self.belege = list(ergebnis.belege)
        self.modell.beginResetModel()
        self.modell.belege = self.belege
        self._vorkonten()
        self.modell.endResetModel()
        self.modell.layoutAboutToBeChanged.emit()
        self.balken.setVisible(False)
        aktiv = sum(1 for b in self.belege if b.buchbar)
        self.titel.setText(f"{self.pfad_pdf.name}: {len(ergebnis.seiten)} Seiten, "
                           f"{len(self.belege)} Belege, davon {aktiv} buchbar")
        self.zeige_status(f"{aktiv} Belege zum Buchen vorbereitet - "
                          f"Bitte pruefen und Konten waehlen")

    @Slot(str)
    def _fehler(self, text: str) -> None:
        self.balken.setVisible(False)
        self.titel.setText("Fehler beim Lesen")
        QMessageBox.warning(self, "PDF konnte nicht gelesen werden", text)

    def _vorkonten(self) -> None:
        """Konten vorfuellen: zuerst das gemerkte Konto des Lieferanten, dann der
        Profilvorschlag, dann der erste Eintrag der Liste.

        Ein gemerktes Konto ueberschreibt den Profilvorschlag; ein Wert, der
        deutlich vom Vorschlag abweicht (also schon korrigiert wurde), bleibt stehen.
        """
        erste_sach, erste_kred = self.konten.sach[0], self.konten.kreditor[0]
        for b in self.belege:
            gemerkt = self.konten.gelernt_fuer(SACH, b.lieferant)
            if gemerkt and (not b.soll_konto or b.soll_konto == b.soll_konto_vorschlag):
                b.soll_konto = gemerkt
            elif not b.soll_konto:
                b.soll_konto = b.soll_konto_vorschlag or erste_sach.nr
            if not self.konten.kennt(SACH, b.soll_konto):
                self.konten.ergaenze(SACH, b.soll_konto, "")
            if not b.haben_konto:
                b.haben_konto = (self.konten.gelernt_fuer(KREDITOR, b.lieferant)
                                 or erste_kred.nr)
            if not b.text:
                b.text = f"Rechnung {b.lieferant or b.profil}"[:60]
        self.konten.speichere()

    # ---- Speichern / Zeilen ----------------------------------------------
    def csv_speichern(self) -> None:
        if not self.belege:
            QMessageBox.information(self, "Nichts zu speichern",
                                    "Erst ein PDF lesen, dann kann gespeichert werden.")
            return
        grund = self.pfad_pdf if self.pfad_pdf else Path("buchungen")
        vorschlag = grund.with_name(grund.stem + "_buchungen.csv")
        pfad, _ = QFileDialog.getSaveFileName(self, "CSV speichern", str(vorschlag),
                                              "CSV-Dateien (*.csv)")
        if not pfad:
            return
        written = csv_export.schreibe(self.belege, pfad, self.konten,
                                      nur_buchbare=self.nur_buchbare.isChecked())
        anzahl = sum(1 for b in self.belege
                     if b.buchbar or not self.nur_buchbare.isChecked())
        self.zeige_status(f"{anzahl} Zeilen geschrieben: {written.name}")

    def _zeile_einfuegen(self) -> None:
        self.modell.zeche_einfuegen()
        self.tisch.selectRow(len(self.belege) - 1)
        self.tisch.edit(self.modell.index(len(self.belege) - 1, C_LIEF))

    def _zeile_loeschen(self) -> None:
        zeilen = sorted({i.row() for i in self.tisch.selectionModel().selectedRows()}, reverse=True)
        if not zeilen:
            QMessageBox.information(self, "Keine Zeile gewaehlt",
                                    "Bitte zuerst eine Zeile in der Liste anklicken.")
            return
        self.modell.beginResetModel()
        for z in zeilen:
            self.modell.belege.pop(z)
        self.belege = self.modell.belege
        self.modell.endResetModel()
        self.zeige_status(f"{len(zeilen)} Zeile(n) entfernt")

    def _gemerkte_anzeigen(self) -> None:
        """Zeigen, welche Konten sich das Programm je Lieferant gemerkt hat."""
        saetze = self.konten.gemerkte()
        if not saetze:
            QMessageBox.information(
                self, "Gemerkte Konten",
                "Das Programm hat sich noch kein Konto je Lieferant gemerkt.\n\n"
                "Sobald Sie bei einem Beleg mit Lieferantennamen in der Liste ein\n"
                "Konto aendern, gilt dieses Konto fuer alle Belege dieses\n"
                "Lieferanten - auch beim naechsten PDF.")
            return
        zeilen = [f"  {e.get('name', '')}\n      Aufwandskonto "
                  f"{e.get(SACH) or '-'}   Lieferanten-Konto {e.get(KREDITOR) or '-'}"
                  for e in saetze]
        antwort = QMessageBox.question(
            self, "Gemerkte Konten je Lieferant",
            "Das sind die gemerkten Zuordnungen:\n\n" + "\n".join(zeilen)
            + "\n\nDiese Zuordnungen alle loeschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if antwort == QMessageBox.StandardButton.Yes:
            anzahl = self.konten.lerne_zuruecksetzen()
            self.zeige_status(f"{anzahl} Zuordnung(en) geloescht - die Vorschlaege "
                              "kommen wieder aus den Lieferanten-Profilen")

    # ---- Kleinigkeiten ----------------------------------------------------
    def zeige_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 12000)

    def liste_aus_datei(self, pfad: Path) -> None:
        """Frueher gespeicherte Liste wieder oeffnen (JSON-Zwischenspeicher)."""
        selbst = ergebnis_laden(pfad)
        self.belege = selbst.belege
        self.modell.beginResetModel()
        self.modell.belege = self.belege
        self._vorkonten()
        self.modell.endResetModel()
