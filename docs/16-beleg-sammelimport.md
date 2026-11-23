# 16 – Beleg-Sammelimport (PDF mit mehreren Rechnungen → Buchungssätze)

**Kategorie 2 (Beleg-Import), umgesetzt am 23.11.2026.** Ein PDF-Ordner mit vielen
Belegen – so wie Online-Händler (Amazon Business, CSL, Office-Partner),
Kreditkartenanbieter und Lieferdienste sie als Sammel-Download schicken – wird in
eine korrigierbare Liste von Buchungssätzen umgewandelt und als CSV exportiert.

Grundlage war das eigenständige Zusatz-Werkzeug `werkzeug/rechnung-buchen`
(siehe `werkzeug/rechnung-buchen/ANDERUNGEN.md`); die Erkennungs-Engine ist jetzt
als Paket `src/backend/utils/beleg_ocr/` in die App eingebunden. Der
Werkzeug-Ordner bleibt unverändert benutzbar.

---

## Warum ein eigener Weg und nicht der vorhandene Rechnungs-Import?

`utils/rechnungs_parser.py` (Einzelrechnung, pytesseract/ocrmypdf) geht davon aus,
dass ein PDF genau eine Rechnung enthält. Ein Sammel-PDF mit 27 Seiten und 17
Belegen braucht andere Schritte: Texterkennung für jede Seite, Seiten zu Belegen
gruppieren, Dubletten erkennen, Beträge je Beleg prüfen. Beide Wege nebeneinander
zu betreiben ist billiger, als den bestehenden Import umzubauen – und der
bestehende Import (Zugferd/XRechnung, E-Mail-Postfach) bleibt unberührt.

## Ablauf

1. PDF hochladen (oder in der Desktop-App auf die Seite ziehen). Prüfung auf
   PDF-Typ (`%PDF`-Magic, maximal 60 MB), Ablage im Profildatenordner.
2. Jede Seite wird mit PyMuPDF mit 300 dpi gerastert, RapidOCR (ONNX, offline)
   liefert Text mit Koordinaten. Keine Cloud, kein Tesseract.
3. Der erkannte Text wird pro PDF-Inhalt zwischengespeichert
   (`sha1(PDF-Byte + dpi)`). Derselbe Ordner ist beim zweiten Mal in Sekunden
   fertig; der erste Durchlauf dauert pro Seite einige Sekunden.
4. Seiten werden zu Belegen gruppiert (gleiche Nummer + Seitenabstand ≤ 2),
   Duplikate werden als Dublette markiert statt doppelt gebucht.
5. Je Beleg: Lieferant, Datum, Rechnungs-/Bestellnummer, netto, USt-Satz,
   USt-Betrag, brutto. Es wird geprüft, ob `netto + USt = brutto` passt – bei
   ungenauem OCR-Text wird die Zeile als „bitte prüfen" markiert, nie still-
   schweigend als korrekt verkauft.
6. Ergebnis ist eine editierbare Liste (Frontend) und wird auf Wunsch als CSV
   mit Buchungssätzen heruntergeladen.

## Endpunkte (`src/backend/api/beleg_sammelimport.py`)

| Endpunkt | Zweck |
|---|---|
| `POST /api/beleg-sammelimport/lesen` | PDF (multipart-Feld `datei`) → Liste, Kontenvorschläge, Dauer, Hinweis |
| `POST /api/beleg-sammelimport/csv` | editierbare Zeilen → CSV (`utf-8-sig`, Semikolon, Dezimalkomma) |
| `GET  /api/beleg-sammelimport/listen` | Kontenrahmen, Aufwand- und Kreditorenkonten, gemerkte Zuordnungen |
| `POST /api/beleg-sammelimport/merken` | eine Zuordnung „Lieferant → Konto" merken |
| `POST /api/beleg-sammelimport/zuruecksetzen` | alle gemerkten Zuordnungen löschen |
| `GET  /api/beleg-sammelimport/caches` | Anzahl gespeicherter OCR-Ergebnisse und Größe |

Alle Endpunkte laufen durch die normale Sitzungstoken-Prüfung
(`utils/sicherheit_middleware.py`) und hängen an `main.py` wie jeder andere
Router.

## Konten kommen aus der eigenen Datenbank

Statt einer festen SKR03-Liste wie im Zusatz-Werkzeug:

- **Soll (Aufwand)**: aktive Kategorien (`Kategorie.aktiv`) mit `kontenart = "Aufwand"`
  aus der Spalte, die zum Kontenrahmen der Firma passt
  (`Unternehmen.kontenrahmen` → `konto_skr03` / `konto_skr04` / `konto_skr49`).
- **Haben (Kreditoren)**: `Lieferant.kreditor_nr`; Anzeigename ist
  `firmenname`, sonst `vorname nachname`.
- Passt ein erkannter Name (unscharf, ab 0,9 Ähnlichkeit; kürzere OCR-Namen werden
  als Teil der Stammdaten erkannt) eindeutig zu einem Lieferanten, wird dieses
  Konto vorgeschlagen. Zwei gleich gute Treffer werden **nicht** geraten.
- Vorschläge der Lieferantenprofile, die im eigenen Kontenrahmen nicht vorkommen,
  werden übernommen, aber ausdrücklich als „bitte prüfen" markiert.

## Lernen pro Lieferant

Eine Korrektur gilt für alle künftigen Belege mit diesem Lieferantennamen –
abgelegt als `beleg_ocr/konten.json` im Profildatenordner (nicht in der Datenbank,
damit die App-Datenbank davon unberührt bleibt). Die Seite zeigt die Liste an und
kann das Lernen zurücksetzen. Beim CSV-Download werden alle in der Liste korrigierten
Zuordnungen automatisch gemerkt (abschaltbar).

## Was (noch) nicht dazugehört

- **Direktbuchung ins Journal**: exportiert wird die CSV. Der Schritt „CSV →
  Journal" existiert bereits als Bank-/CSV-Import; eine eigene Tabelle `belege`
  nach `docs/v0.2-beleg-import-plan.md` ist für die Direktbuchung vorgesehen und
  hier bewusst noch nicht angefasst (dafür wäre eine Datenbank-Migration nötig).
- **DATEV-Format**: `werkzeug/rechnung-buchen/belegerkennung/datev.py` existiert,
  ist aber in beiden Programmen nicht freigeschaltet.
- **Kein Feature-Flag / keine Migration**: der Menüeintrag ist immer sichtbar.

## Build und Größe

`scripts/build-sidecar.ps1` nimmt die Erkennung ins PyInstaller-Bundle auf:
`--collect-all rapidocr_onnxruntime` (die ONNX-Modelle sind Datendateien),
`--collect-all onnxruntime`, `--collect-all pymupdf` (neben `fitz`) und
Hidden-Importe für `utils.beleg_ocr.*`, `cv2`, `numpy`. `main.py --selbsttest`
baut die Engine im fertigen Binary auf – fehlt eine Modelldatei, bricht der Bau ab,
statt erst der erste Belegordner beim Nutzer. Das Sidecar wird dadurch groß: gemessen
am 23.11.2026 **165 MB** Sidecar und **172,9 MB** NSIS-Installer (Windows x64). Der
durchgelaufene Bau unter Windows steht in `CONTRIBUTING.md`
(„Tauri-App unter Windows bauen").

`src/backend/requirements.txt` enthält jetzt `rapidocr-onnxruntime==1.2.3` und
`onnxruntime==1.32.0`. `pymupdf<2` bleibt wie in Issue #493 begründet getrennt.

## Nachgeprüft

- `src/backend/tests/test_beleg_sammelimport.py`: 15 Tests (Gruppierung,
  Dubletten, Vorschau-Konten, Lernen, unscharfer Lieferanten-Abgleich,
  CSV-Format, SKR04/anderer Rahmen, Cache-Aufräumung, Hochlade-Prüfungen).
- Praxisordner (27 Seiten, 17 Belege, 1 Dublette): erste Erkennung rund
  3 Minuten, zweiter Durchlauf aus dem Zwischenspeicher heraus etwa 2 Sekunden,
  alle Zeilen mit Prüfung „OK".
- `npx tsc --noEmit` im Frontend ohne Fehler.
