"""Belegerkennung für Sammel-PDFs (mehrere Rechnungen in einer PDF-Datei).

Diese Module sind eine Übernahme aus dem Zusatz-Werkzeug
`werkzeug/rechnung-buchen` (dort als eigenständiges Desktop-Programm mit
eigenem Fenster). Hier dienen sie der App-Funktion „Beleg-Sammelimport“:
eine PDF-Datei hochladen, alle gefundenen Belege in einer Liste prüfen und
korrigieren, als CSV herunterladen.

Ablauf: PDF → Seiten mit PyMuPDF rasterisieren (300 dpi) → RapidOCR
(PP-OCR über onnxruntime, komplett offline) → Seiten zu Belegen gruppieren →
Felder (Lieferant, Datum, Nummer, Beträge) extrahieren → Dubletten markieren.

Gegenüber der Werkzeug-Version bewusst geändert:
* `ocr.py`: Cache-Ordner wird von außen gesetzt (`cache_dir`), OCR-Fortschritt
  geht in das Backend-Protokoll statt auf die Konsole; PyMuPDF wird so
  importiert, dass auch Versionen < 1.24 funktionieren (`fitz`).
* `konten.py`: Die Datei mit den Kontenvorschlägen und den gemerkten
  Lieferanten liegt im Profildatenordner der App (`beleg_ocr/konten.json`) und
  nicht mehr neben dem Programm; jede Instanz merkt sich ihren Pfad.

Die OCR-Ergebnisse werden pro PDF-Inhalt in einem JSON-Cache abgelegt, damit
ein erneutes Anzeigen derselben Datei nicht wieder drei Minuten dauert.
"""
