# 16 – Beleg-Sammelimport (PDF mit mehreren Rechnungen → Buchungssätze)

**Kategorie 2 (Beleg-Import), umgesetzt am 23.11.2026.** Ein PDF-Ordner mit vielen
Belegen – so wie Online-Händler (Amazon Business, CSL, Office-Partner),
Kreditkartenanbieter und Lieferdienste sie als Sammel-Download schicken – wird in
eine korrigierbare Liste von Buchungssätzen umgewandelt. Nach dem Prüfen geht es
weiter: direkt ins Journal (Knopf „Ins Journal buchen") oder als CSV.

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
6. Ergebnis ist eine editierbare Liste (Frontend). Danach wird auf Wunsch direkt
   ins Journal gebucht (Abschnitt „Direkt ins Journal buchen") oder als CSV mit
   Buchungssätzen heruntergeladen.

## Endpunkte (`src/backend/api/beleg_sammelimport.py`)

| Endpunkt | Zweck |
|---|---|
| `POST /api/beleg-sammelimport/lesen` | PDF (multipart-Feld `datei`) → Liste, Kontenvorschläge, Dauer, Hinweis |
| `POST /api/beleg-sammelimport/csv` | editierbare Zeilen → CSV (`utf-8-sig`, Semikolon, Dezimalkomma) |
| `POST /api/beleg-sammelimport/buchen` | editierbare Zeilen → ein `Journaleintrag` je Beleg |
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

## Direkt ins Journal buchen

Der Knopf „Ins Journal buchen" unter der Liste schreibt jeden Beleg als normalen
`Journaleintrag` – **derselbe Code wie eine von Hand erfasste Ausgabe**
(`api/journal.py::create_eintrag`): die Kategorie kommt aus dem Aufwandskonto der
Liste (`kategorie_id`, Konten-Snapshot auf die Kategorie-Kontonummer), USt-Satz und
Vorsteuer aus den erkannten Werten, netto rechnet das Journal aus dem Bruttobetrag,
die Belegnummer holt der Nummernkreis, am Ende steht die GoBD-Signatur. Der
Lieferant der Stammdaten wird verknüpft, wenn der Name (unscharf) eindeutig passt;
die Rechnungsnummer wandert in `externe_belegnr`.

Wichtig für das Verständnis: das Journal der App ist **kategorienbasiert**
(Kasse/Bank/PayPal + Aufwandskategorie), keine doppelte Buchungszeile mit
Gegenkonto. Das Kreditorenkonto aus der Liste braucht deshalb nur die CSV (und ein
künftiger DATEV-Export); im Journal steht es nicht noch einmal.

Was **nicht** gebucht, sondern mit deutscher Begründung übersprungen wird –
halbe Buchungen gibt es hier nicht:

- Dubletten (derselbe Beleg weiter oben in derselben Liste)
- fremde Währung (das Journal rechnet in Euro)
- fehlendes oder unlesbares Datum, fehlender oder nulliger Bruttobetrag
- Zeilen, bei denen `netto + USt = brutto` nicht aufgeht (außer der Nutzer nimmt
  sie mit dem Häkchen „Zeilen mit Schieflage bei den Summen mitbuchen" bewusst)
- Aufwandskonto, das zu keiner Kategorie der App gehört
- Bar-Ausgabe über dem Kassenstand (gleiche Prüfung wie im Buchungsformular)
- Zeilen, die genauso schon im Journal stehen: gleiches Datum, gleicher Betrag und
  gleiche `externe_belegnr` (bzw. gleicher Buchungstext). Mehrfach klicken ist
  dadurch ungefährlich; eine eigene Herkunftstabelle wäre nur mit einer
  Datenbank-Migration möglich und wurde bewusst nicht eingebaut.

Die Liste zeigt nach dem Buchen pro Zeile „gebucht {Belegnr}", „nicht gebucht" oder
„fehlgeschlagen" (mit Grund im Detailbereich), darüber eine Zusammenfassung mit
Anzahl und Summe. Journal, Monatsübersicht und Kassenstand werden im Frontend neu
geladen.

**Zeile löschen** (ebenfalls in der Liste): Zeilen, die nicht hierher gehören – ein
Beleg für eine andere Firma, ein Rest, den die Erkennung falsch zusammengewürfelt
hat – werden mit „löschen" aus der Liste genommen, nach Rückfrage mit Lieferant,
Betrag und Seitenzahl. Das PDF, der OCR-Zwischenspeicher und das Journal bleiben unberührt; ist
die Zeile vorher schon gebucht, sagt die Rückfrage, dass der Eintrag im Journal
bestehen bleibt und dort storniert werden muss. Alles darunter rückt nach oben, die
Rückmeldungen des Journals und die Zahlen der Zusammenfassung bleiben richtig.
Dasselbe kann das Zusatz-Werkzeug schon länger („Zeile loeschen" in der
Desktop-Oberfläche).

## Was (noch) nicht dazugehört

- **Keine eigene Belegtabelle**: die Buchung erkennt Dubletten über das Journal
  selbst. Die geplante Tabelle `belege` nach `docs/v0.2-beleg-import-plan.md`
  (Herkunft, Anhang je Buchung) wäre eine Datenbank-Migration und ist nicht angefasst.
- **Kein Abgleich mit Kontoauszügen**: gebucht wird, was auf dem Beleg steht. Ob die
  Zahlung schon auf der Bank angekommen ist, prüft weiterhin der Bank-Import.
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

- `src/backend/tests/test_beleg_sammelimport.py`: 23 Tests (Gruppierung,
  Dubletten, Vorschau-Konten, Lernen, unscharfer Lieferanten-Abgleich,
  CSV-Format, SKR04/anderer Rahmen, Cache-Aufräumung, Hochlade-Prüfungen und das
  Buchen ins Journal: Felder des Journals, Dublettenschutz beim zweiten Klick,
  fehlendes Konto, schiefe Summen, Zahlungsart, Kommazahlen aus dem Formular).
- Praxisordner (27 Seiten, 17 Belege, 1 Dublette): erste Erkennung rund
  3 Minuten, zweiter Durchlauf aus dem Zwischenspeicher heraus etwa 2 Sekunden,
  alle Zeilen mit Prüfung „OK".
- `npx tsc --noEmit` im Frontend ohne Fehler.
