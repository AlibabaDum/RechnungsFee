# Änderungen im Repo – 23.11.2026: neues Werkzeug `rechnung-buchen`

Repo-Stand beim Eintragen: `main` @ `362518c6` („Release v0.7.13 vorbereitet").
Der Eintrag liegt als **ein Commit** darüber (Meldung: „neues Werkzeug:
rechnung-buchen (PDF-Rechnungen per Offline-OCR in CSV)") und ist noch **nicht**
auf GitHub hochgeladen. Als Absender ist die Kennung `AlibabaDum
<AlibabaDum@users.noreply.github.com>` **nur für dieses Repo** gesetzt (genau wie
in deinem anderen Repo); die globale git-Einstellung bleibt unverändert.

## 1. Neu: Ordner `werkzeug/rechnung-buchen`

Ein eigenständiges Programm (PySide6-Oberfläche, OCR offline), das ein PDF mit
mehreren Rechnungen in eine korrigierbare Liste von Buchungssätzen und danach
in eine CSV umwandelt. Beschreibung: [README.md](README.md), Bedienung:
[Anleitung.md](Anleitung.md).

| Datei | Inhalt |
|---|---|
| `buchen.py` | Startpunkt; erkennt, ob Bildschirmliste oder Kommandozeile gemeint ist |
| `belegerkennung/cli.py` | Argumente (`--csv`, `--json`, `--konto`, `--frisch`, `--alle`, `--dpi`, `--gui`) |
| `belegerkennung/gui.py` | Liste zum Korrigieren, Dropdowns für Sach-/Kreditorenkonto, „Merke-Konten…" |
| `belegerkennung/konten.py` | Kontenrahmen SKR03 und das Lernen pro Lieferant (`konten.json`) |
| `belegerkennung/ocr.py` | PyMuPDF (300 dpi) + RapidOCR/ONNX, PDF-Inhalt als Cache-Schlüssel |
| `belegerkennung/extract.py` | Felder aus dem Text: Datum, Nummer, brutto/netto/USt, Lieferant |
| `belegerkennung/grouping.py` | Seiten zu Belegen gruppieren, Dubletten erkennen |
| `belegerkennung/fields.py`, `model.py`, `profiles.py`, `pipeline.py` | Hilfen, Datenstruktur, Lieferantenprofile, Ablauf |
| `belegerkennung/csv_export.py` | CSV der geprüften Liste |
| `belegerkennung/datev.py` | DATEV-ASCII-Export, im Programm noch **nicht** freigeschaltet |
| `Rechnung buchen.vbs` | Start ohne Konsolenfenster (nutzt `pythonw.exe`) |
| `Rechnung buchen (mit Konsole).bat` | Start mit Konsolenfenster (zum Sehen von Meldungen) |
| `requirements.txt` | Die fünf Pakete mit getesteten Versionen |
| `.gitignore` | Hält `konten.json`, `.cache/`, `python\` und Exporte aus dem Repo |
| `README.md`, `ANDERUNGEN.md` | Beschreibung bzw. diese Dokumentation |

## 2. Absichtlich **nicht** übernommen

| Inhalt | Grund |
|---|---|
| `python\` des portablen Pakets (eingebettetes Python 3.13 + Pakete, rund 458 MB / 6600 Dateien) | gehört nicht in ein Git-Repo; installierbar über `requirements.txt` |
| `.cache\` (OCR-Rohtext, 220 KB) | enthält den gesamten Text der Beispiel-Rechnungen |
| `konten.json` | enthält echte Lieferanten-Namen und die gemerkten Konten |
| `rechnungen_okt_nov_26.pdf` (607 KB), `Amazon_busi.pdf`, `rechnungen_okt_nov_26_buchungen.csv`, `ocr_dump.txt`, `beispiele\` (Screenshots) | echte Belege mit echten Namen und Beträgen |
| `__pycache__\` | nur lokale Laufzeitreste |

Die laufende Installation in `E:\Buchen` wurde für diesen Eintrag **nicht**
verändert. Die kopierten Programmdateien wurden vorher Datei für Datei per
SHA-256 mit der Installation in `E:\Buchen` verglichen: alle 17 Dateien gleich.

## 3. Vorhandene Dateien geändert (2)

- `README.md` (oben im Repo): neuer kurzer Abschnitt „🧰 Zusatz-Werkzeuge" mit
  Verweis auf `werkzeug/rechnung-buchen`, damit man den Ordner findet.
- `CHANGELOG.md`: neuer Abschnitt `[Unreleased]` mit dem Eintrag zum neuen
  Werkzeug.

An `src\`, `src-tauri\`, `web\`, `docs\`, `vorlagen\`, `.github\`, dem Backend
(`requirements.txt` dort) und der Datenbank wurde **nichts** geändert.

## 4. Getestet am 23.11.2026 (alles aus dem neuen Ordner heraus)

- `python buchen.py --help` → Argumente werden korrekt angezeigt.
- Vollständiger Durchlauf mit der 27-Seiten-Beispiel-PDF: 17 Belege erkannt,
  16 buchbar, 1 Dublette, alle Zeilen mit „OK" in der Prüfspalte
  (netto + USt = brutto), CSV mit 16 Zeilen und 18 Spalten geschrieben.
  Dauer mit OCR-Cache etwa 2 Sekunden, ohne Cache rund 3 Minuten.
- Bildschirmliste aus dem neuen Ordner gestartet, und zwar mit `pythonw.exe`
  (ohne Konsole – genau so startet auch `Rechnung buchen.vbs`): ein Fenster
  „Belegerkennung - Rechnungen ansehen und Buchungsaetze korrigieren", kein
  Konsolenfenster, keine Datei „letzter Fehler.txt".
- Hinweis zum Startskript: `Rechnung buchen.vbs` sucht `python\pythonw.exe`
  direkt neben sich. Diesen Unterordner (458 MB) enthält der Repo-Ordner
  bewusst nicht, das Skript sagt das in einem Dialog. Für den portablen Betrieb
  den `python\`-Ordner dazulegen, Anleitung.md beschreibt, wie man ihn selbst
  baut.
- Testreste (`.cache\`, `konten.json`, Test-CSV, `__pycache__`) wurden danach
  wieder gelöscht; der Ordner enthält nur die Dateien aus der Tabelle oben.

## 5. Offene Punkte (bewusst nicht erledigt)

1. **Ansprache**: Im Repo gilt „Du", die Oberfläche dieses Werkzeugs sagt noch
   „Sie". Umbau der Dialogtexte ist eine eigene, kleine Aufgabe.
2. **`THIRD_PARTY_LICENSES.txt`**: enthält PyMuPDF (AGPL-3.0), PySide6/shiboken6
   (LGPL-3.0), rapidocr-onnxruntime (Apache-2.0), onnxruntime (MIT),
   pdfplumber/pdfminer.six (MIT) noch nicht. Aufzählung steht in
   `werkzeug/rechnung-buchen/README.md`, übernommen ist sie dort noch nicht.
3. **PyMuPDF-Version**: Backend pinnt `pymupdf<2` (PyInstaller-Sidecar), dieses
   Werkzeug braucht `PyMuPDF 2.1`. Beide Umgebungen getrennt halten.
4. **`CLAUDE.md` / `claude.md`**: Im Repo liegen zwei Dateien, die sich nur durch
   Groß-/Kleinschreibung unterscheiden. Unter Windows kann das nicht
   nebeneinander existieren, deshalb meldet `git status` nach dem Klonen
   `M CLAUDE.md`. Dieser Eintrag wurde **nicht** mit committet. Sinnvolle Lösung
   wäre, eine der beiden Dateien im Repo zu entfernen (dafür besser ein Issue).
5. **Hochladen**: Der Commit liegt nur auf `E:\develop\RechnungsFee`. Das Repo
   ist öffentlich – vor dem Push prüfen, dass keine Beispiel-PDFs, `konten.json`
   oder OCR-Caches mitlaufen.

## 6. Noch am 23.11.2026 ergänzt: der Ordner ist direkt startbar

Aus `E:\Buchen` habe ich die Python-Laufzeit in diesen Ordner kopiert:
`python\` mit 457,6 MB und 6569 Dateien (eingebettetes Python 3.13 inklusive der
in `requirements.txt` genannten Pakete). Damit funktioniert hier der Doppelklick
auf `Rechnung buchen.vbs` ohne weitere Installation.

- Diese 457 MB sind **nicht** Teil des Commits: `werkzeug/rechnung-buchen/.gitignore`
  schließt `python/` aus, nachgewiesen mit `git check-ignore`
  (Fundstelle: Zeile 8 dieser Datei). Der Ordner lässt sich also trotzdem per
  git verteilen – nur die Pakete müssen dann mit `pip install -r requirements.txt`
  geholt werden.
- Getestet am 23.11.2026: das vbs-Skript aus diesem Ordner startet
  `werkzeug\rechnung-buchen\python\pythonw.exe`, ein Fenster
  „Belegerkennung - Rechnungen ansehen und Buchungsaetze korrigieren",
  kein Konsolenprozess, keine Datei „letzter Fehler.txt".
- `konten.json` (Konten + gemerkte Lieferanten) und `.cache\` (OCR-Texte)
  entstehen beim ersten Start von selbst und bleiben ebenfalls außerhalb von git.
  Die gemerkten Konten aus `E:\Buchen` wurden **nicht** herüberkopiert – der
  Ordner hier fängt bei den Standardkonten 3400 / 70001 an.
- Zum Mitnehmen auf einen anderen Rechner: den ganzen Ordner inklusive `python\`
  kopieren (oder nur den Quelltext und dort die Pakete installieren).

## 7. Noch am 23.11.2026: dieselbe Erkennung jetzt in der App (RechnungsFee)

Auf Wunsch wurde die Belegerkennung **zusätzlich** in das eigentliche Programm
eingebaut – dieser Ordner hier bleibt unverändert liegen und funktioniert weiter
(auch `E:\Buchen` wurde nicht angefasst). Beschreibung der neuen Seite:
[`docs/16-beleg-sammelimport.md`](../../docs/16-beleg-sammelimport.md).

| Datei | Inhalt |
|---|---|
| `src/backend/utils/beleg_ocr/` | die Engine aus diesem Ordner (`ocr`, `pipeline`, `extract`, `fields`, `grouping`, `model`, `profiles`, `konten`, `csv_export`), ohne `gui.py`/`cli.py`/`datev.py` |
| `src/backend/api/beleg_sammelimport.py` | neuer Router `/api/beleg-sammelimport` (lesen, csv, buchen, listen, merken, zuruecksetzen, caches) |
| `src/backend/main.py` | Router eingetragen; `--selbsttest` prüft jetzt auch die OCR-Modelle im fertigen Sidecar |
| `src/backend/requirements.txt` | `rapidocr-onnxruntime==1.2.3`, `onnxruntime==1.32.0` |
| `src/backend/tests/test_beleg_sammelimport.py` | 23 neue Tests |
| `src/frontend/src/pages/beleg-sammelimport/BelegSammelImportPage.tsx` | neue Seite „Beleg-Import" (Hochladen/Ziehen, Tabelle zum Korrigieren, „Ins Journal buchen", gemerkte Zuordnungen) |
| `src/frontend/src/api/client.ts`, `src/App.tsx`, `src/components/AppLayout.tsx` | Typen + 7 Aufrufe, Route `/beleg-import`, Menüeintrag unter „Buchhaltung" |
| `scripts/build-sidecar.ps1` | nimmt OCR-Bibliotheken und -Modelle ins PyInstaller-Bundle auf |

Anders als in der eigenständigen Version:

1. **Konten aus der Datenbank** statt der festen SKR03-Liste: Aufwandskonten aus den
   aktiven Kategorien passend zu `Unternehmen.kontenrahmen` (SKR03/SKR04/SKR49),
   Kreditoren aus `Lieferant.kreditor_nr`. Der Recognizer-Rahmen wird also je Firma
   richtig gesetzt, nicht hart eingetragen.
2. **Standard-Kreditor wird ersetzt**: die Engine trägt als Haben `70001`
   („div. Lieferanten") ein. Die App ersetzt diese Nummer durch das echte
   Kreditorenkonto aus den Stammdaten, wenn der Name eindeutig passt
   (`GRUND_KREDITOR` in `api/beleg_sammelimport.py`).
3. **Lernen liegt im Profildatenordner** (`beleg_ocr/konten.json` daneben den
   PDFs und dem OCR-Zwischenspeicher), nicht neben dem Programm – die
   App-Datenbank bleibt unverändert, es gibt keine Migration und kein Feature-Flag.
4. **CSV und Direktbuchung**: die App bucht jeden Beleg auf Knopfdruck auch direkt
   ins Journal (Abschnitt 8). Das Kreditorenkonto aus der Liste braucht weiterhin
   nur die CSV, weil das Journal der App kategorienbasiert arbeitet. Ein
   DATEV-Export existiert weiterhin nicht.
5. Der Selftest des Sidecars (`main.py --selbsttest`) wurde erweitert, weil
   RapidOCR-Modelle Datendateien sind: ohne `--collect-all rapidocr_onnxruntime`
   würde der erste Sammel-ORDNER beim Nutzer fehlschlagen. Preis: das Sidecar ist
   jetzt 165 MB groß, der gebaute Installer 172,9 MB.

Nachgewiesen am 23.11.2026: `pytest tests/test_beleg_sammelimport.py` 15 passed,
gesamte Backend-Suite ohne neuen Fehler (vorher wie nachher ein bereits bekannter
Schlüsselbund-Test), `npx tsc --noEmit` ohne Fehler.

## 8. Noch am 23.11.2026: der Import bucht direkt ins Journal

Die Seite war getestet, aber der Weg führte weiter über die CSV in den
Bank-/CSV-Import. Gewünscht war ein zusätzlicher Knopf **nach dem Bearbeiten** –
deshalb jetzt „Ins Journal buchen" direkt über der Liste.

| Datei | Inhalt |
|---|---|
| `src/backend/api/beleg_sammelimport.py` | neuer Endpunkt `POST /buchen`: je Beleg ein `Journaleintrag` über `api/journal.py::create_eintrag` (Kategorie aus dem Aufwandskonto, USt/Vorsteuer, Nummernkreis, GoBD-Signatur, Lieferant aus den Stammdaten, Rechnungsnummer als `externe_belegnr`). Zusätzliche Helfer: `_geld` (Kommazahlen aus dem Formular), `_datum`, `_zahlungsart`, `_kategorien_lookup`, `_lieferanten_lookup`, `_bereits_gebucht` (Dublettenschutz ohne neue Tabelle) |
| `src/frontend/src/pages/beleg-sammelimport/BelegSammelImportPage.tsx` | Knopf „Ins Journal buchen", Auswahl der Standard-Zahlungsart, Häkchen für Zeilen mit schiefer Summenprüfung, Zusammenfassung (gebucht/übersprungen/fehlgeschlagen, Summe, Gründe), Status-Badge je Zeile und „löschen" je Zeile (wie „Zeile loeschen" in diesem Werkzeug) |
| `src/frontend/src/api/client.ts` | `bucheBelege()` plus Typen `BelegBuchAntwort`/`BelegBuchErgebnis` |
| `src/backend/tests/test_beleg_sammelimport.py` | 8 neue Tests (Felder des Journals, Dublettenschutz beim zweiten Klick, fehlendes Konto, schiefe Summen, Zahlungsart, Kommazahlen) |

Wichtige Entscheidungen, damit nichts Halbes gebucht wird: Dubletten, fremde
Währung, fehlendes Datum, fehlender Betrag, ein Sachkonto ohne Kategorie, Bar über
dem Kassenstand und Zeilen mit schiefer Summenprüfung werden übersprungen und
verständlich begründet; schon genauso gebuchte Zeilen werden am Datum, dem Betrag
und der externen Belegnr erkannt. Eine eigene Herkunftstabelle (die das eleganter
gelöst hätte) wäre eine Datenbank-Migration gewesen und wurde nicht angefasst.
Das Werkzeug in diesem Ordner bleibt bei seiner CSV – es hat kein Journal.

Nachgewiesen am 23.11.2026: `pytest tests/test_beleg_sammelimport.py` 23 passed,
gesamte Backend-Suite 1440 passed / 1 bekannter Schlüsselbund-Test / 4 skipped,
`npx tsc --noEmit` ohne Fehler.

## 9. Noch am 23.11.2026: eigenes Repo „Belegerkennung", README berichtigt

Das eigenständige Programm liegt jetzt zusätzlich als eigener GitHub-Ordner
[AlibabaDum/Belegerkennung](https://github.com/AlibabaDum/Belegerkennung):
derselbe Quelltext aus diesem Ordner (Prüfsummen verglichen, inhaltlich identisch),
dazu README, Anleitung, dieses Protokoll, `requirements.txt` und beide Startdateien.
Lizenz **AGPL-3.0** (`LICENSE`, amtlicher Text von gnu.org) – wie RechnungsFee.
**Hauptversion bleibt dieser Ordner**, der andere Repo ist der fertige Stand zum
Herunterladen; beides steht jetzt jeweils im eigenen README.

Nicht mit hochgeladen: echte Beleg-PDFs, `konten.json`, `.cache/`, erzeugte
CSV-Dateien, Beispiel-Screenshots und die rund 457 MB Python-Laufzeit. In dem
anderen Repo sind zusätzlich `*.pdf` und `beispiele/` in der `.gitignore`, damit
kein Beleg versehentlich mitgeht. Nachgesehen an der Dateiliste des GitHub-Ordners:
21 Dateien, rund 0,1 MB, kein Beleg und kein Zwischenspeicher dabei. Frisch
geklont und ausprobiert: Kommandozeile mit `--help` und die Bildschirmliste laufen
mit der Python-Umgebung aus `E:\Buchen`.

Berichtigt im README hier: es behauptete, der `python\`-Ordner sei „in diesem
Repo-Ordner schon vorhanden". Er steht aber in der `.gitignore` und fehlt deshalb
nach jedem Klonen – jetzt steht dort, wie er gebaut wird oder wie Variante B mit
eigener Umgebung funktioniert.
