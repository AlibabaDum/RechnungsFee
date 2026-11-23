# rechnung-buchen – Belegerkennung (PDF → Buchungs-CSV)

Eigenständiges Desktop-Programm: ein PDF mit mehreren Rechnungen (auch Scans)
werden eingelesen, die Belege erscheinen als korrigierbare Liste, und am Ende
steht eine CSV mit Buchungssätzen (SKR03).

Das Werkzeug läuft **vollständig für sich** – eigener Startknopf, eigene
Python-Umgebung, kein Kontakt zu FastAPI-Backend, Datenbank oder Frontend von
RechnungsFee. Es ist bewusst als eigener Ordner im Repo abgelegt, damit der
Quelltext an einer Stelle liegt, ohne Teil der Desktop-App zu sein.

## Was es kann

- **OCR komplett offline**: PyMuPDF rendert jede Seite mit 300 dpi, RapidOCR
  (ONNX) erkennt den Text. Kein Internet, kein installiertes Tesseract und kein
  Ghostscript nötig.
- **OCR-Cache**: Der Text jeder PDF wird in `.cache/` abgelegt. Schlüssel ist
  der Inhalt der PDF (Hash) plus Auflösung, nicht der Dateipfad – dieselbe
  PDF wiederholt zu lesen dauert deshalb ~2 Sekunden statt ~3 Minuten.
- **Mehrere Rechnungen pro PDF**: Die Seiten werden zu Belegen gruppiert,
  Dubletten (gleiche Rechnungsnummer/Beträge) werden erkannt und bleiben aus
  der Buchungsliste draußen.
- **Korrekturliste vor dem Export** (das ist der Kern): Datum,
  Rechnungsnummer, brutto / netto / USt, Buchungstext – und zwei Dropdowns pro
  Zeile für Sachkonto und Kreditoren-Konto (SKR03, voreingestellt
  „Wareneingang 19 % – 3400" und „div. Lieferanten – 70001"). Eine Spalte
  prüft selbst: stimmt `netto + USt = brutto` (±0,02 €)?
- **Lernen pro Lieferant**: Wählst du für eine „Telekom"-Zeile das Konto 4950,
  gilt das sofort für alle gleichnamigen Belege und wird in `konten.json`
  gemerkt – auch für künftige PDFs. Der Name wird normalisiert (Groß-/Kleinschreibung,
  „GmbH/AG/…" fallen weg) und unscharf verglichen (Übereinstimmung > 0,9),
  damit OCR-Fehler beim Namen nicht stören. Zurücksetzen über die Schaltfläche
  „Merke-Konten…".
- **CSV-Export** der geprüften Liste. Ein DATEV-Modul ist im Quelltext
  (`belegerkennung/datev.py`) vorhanden, aber bewusst noch nicht freigeschaltet.

## Starten

**A) Portable (kein installiertes Python nötig)**: In einem Ordner, der den
Unterordner `python\` enthält (eingebettetes Python 3.13 mit den Paketen aus
`requirements.txt`), doppelklicke `Rechnung buchen.vbs` (ohne Konsole) oder
`Rechnung buchen (mit Konsole).bat`. In diesem Repo-Ordner ist `python\` schon
vorhanden (457 MB, deshalb nicht mit gecommittet) – hier also einfach
`Rechnung buchen.vbs` doppelklicken. Wie du den `python\`-Ordner selbst baust,
steht in [Anleitung.md](Anleitung.md).

**B) Mit eigenem Python 3.12/3.13**

```bash
cd werkzeug/rechnung-buchen
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python buchen.py                # Liste auf dem Bildschirm
```

`Rechnung buchen.vbs` startet dasselbe mit `pythonw.exe`, damit neben dem
Fenster keine Konsole auftaucht.

## Kommandozeile (ohne Bildschirmliste)

```bash
python buchen.py rechnungen.pdf --csv buchungen.csv
        [--json details.json] [--konto 3400] [--dpi 300] [--frisch] [--alle]
```

`--frisch` ignoriert den OCR-Cache, `--alle` zeigt auch erkannte Dubletten,
`--konto` setzt ein Sachkonto für alle Zeilen.

## Datenschutz – was **nicht** ins Repo gehört

Der Ordner `.gitignore` hält bewusst Dinge draußen, die personengebundene Daten
enthalten oder nur lokal entstehen:

| Datei/Ordner | Warum nicht im Repo |
|---|---|
| `konten.json` | enthält deine Lieferanten-Namen und die gemerkten Konten |
| `.cache/` | kompletter OCR-Rohtext deiner Beleg-PDFs |
| `python/`, `libs/` | rund 460 MB Python-Laufzeit, installierbar über `requirements.txt` |
| `*.csv`, `letzter Fehler.txt` | Exportergebnisse und Fehlertexte von deinem Rechner |
| Beispiel-PDFs | echte Rechnungen mit echten Namen und Beträgen |

## Lizenzen der Abhängigkeiten

| Paket | Lizenz | Anmerkung |
|---|---|---|
| PyMuPDF | AGPL-3.0 | passt zur AGPL von RechnungsFee |
| PySide6-Essentials, shiboken6 | LGPL-3.0 | nur dynamisch eingebunden |
| rapidocr-onnxruntime | Apache-2.0 | OCR-Modelle sind im Paket enthalten |
| onnxruntime | MIT | Laufzeit für die OCR-Modelle |
| pdfplumber, pdfminer.six, pypdfium2 | MIT/BSD | Textebenen-Auswertung |
| numpy, pillow, opencv-python, shapely, pyclipper | BSD/MIT | Rechen- und Bildpakete |

Noch nicht in `THIRD_PARTY_LICENSES.txt` oben im Repo ergänzt – siehe
[ANDERUNGEN.md](ANDERUNGEN.md) unter „offene Punkte".

## Verhältnis zu RechnungsFee (offene Punkte)

- **Ansprache**: Im Repo gilt die „Du"-Richtlinie, die Oberfläche dieses
  Werkzeugs sagt bisher „Sie". Eine Umstellung ist noch nicht gemacht.
- **PyMuPDF-Version**: Das Backend pinnt `pymupdf<2` (der PyInstaller-Sidecar
  scheitert an den Modelldateien von `pymupdf-layout`). Dieses Werkzeug nutzt
  `PyMuPDF 2.1`. Die hier verwendeten Funktionen (Dokument öffnen, Seite als
  Bild rendern) gibt es auch in PyMuPDF 1.24+, getestet ist aber nur 2.1 –
  deshalb die Umgebungen **nicht** mischen.
- **OCR-Alternative**: Das Backend nutzt `pytesseract` plus installiertes
  Tesseract-Programm. RapidOCR bringt Modell und Laufzeit selbst mit und
  braucht keine Installation; es wäre eine Offline-Alternative in
  `src/backend/utils/rechnungs_parser.py`. Nicht umgesetzt, nur als Hinweis.
