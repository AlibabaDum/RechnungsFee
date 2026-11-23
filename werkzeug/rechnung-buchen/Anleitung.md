# Belegerkennung: Rechnungs-PDF -> Buchungssaetze (mit Korrekturliste)

Ein Desktop-Programm fuer Windows. Es liest ein PDF mit einer oder mehreren
Rechnungen (auch eingescannte), erkennt Lieferant, Datum, Rechnungsnummer und
die Betraege und zeigt alles in einer **korrigierbaren Liste**. Danach wird eine
**CSV-Datei** mit den Buchungssaetzen geschrieben.

## Portable Fassung: alles in einem Ordner

Die noetige Python samt Bibliotheken liegt im Unterordner `python` desselben
Ordners - ein installiertes Python ist **nicht** noetig. Man kann den Ordner
beliebig verschieben, auf einen USB-Stick kopieren oder auf einem anderen
Windows-PC starten. Angefangen wird immer mit `Rechnung buchen.vbs` im
Hauptordner (doppeltes Klicken oder PDF darauf ziehen).

- Im Hauptordner: `buchen.py` (Startpunkt), Unterordner `belegerkennung`
  (Programm), `konten.json` (eigene Konten), diese Anleitung, Start-Datei.
- Unterordner `.cache` entsteht beim ersten Lesen: dort liegt der gelesene Text
  pro PDF. Erkannt wird die PDF am Inhalt, nicht am Pfad - der Ordner kann also
  verschoben und umbenannt werden, der bereits gelesene Text bleibt nutzbar und
  ein schon gelesenes PDF ist beim naechsten Mal sofort fertig.
- `konten.json` wandert mit: einmal eingegebene Kontonummern sind auch auf einem
  anderen Rechner gleich wieder da.
- Die erste OCR dauert etwa 3 Minuten fuer 27 Seiten, danach geht es schnell.
- Wenn auf einem anderen PC eine Meldung kommt, dass eine `vcruntime140.dll`
  oder `api-ms-win-...dll` fehlt: dort die kostenlose "Visual C++ 2015-2022 x64"
  Laufzeitumgebung von Microsoft installieren (Oberflaeche und OCR brauchen sie).
  Auf diesem Rechner ist sie schon vorhanden.
- Der Ordner ist etwa 470 MB gross.

## Starten

Doppelklick auf **`Rechnung buchen.vbs`** im Hauptordner: die Liste oeffnet sich
**ohne schwarzes Konsolenfenster**. Eine PDF-Datei kann direkt auf diese Datei
gezogen werden - dann wird sie sofort gelesen. Fuer eine Verknuepfung auf dem
Schreibtisch: Rechtsklick auf die Datei -> *Senden an* -> *Desktop (Verknuepfung
erstellen)*.

Zwei weitere Startmoeglichkeiten:

- **`Rechnung buchen (mit Konsole).bat`** (portable Fassung) startet dasselbe,
  aber mit Konsolenfenster, in dem alle Meldungen stehen - nuetzlich, wenn etwas
  nicht klappt.
- **`Belegerkennung starten.bat`** ist die Entwicklungsfassung (nutzt ein
  installiertes Python statt des Unterordners `python`).

Klappt der Start nicht, erscheint eine Meldung auf dem Bildschirm, und die
Einzelheiten stehen in der Datei **`letzter Fehler.txt`** im Hauptordner. Das
gleiche gilt, waehrend ein PDF gelesen wird.

Ohne PDF oeffnet sich das Fenster zuerst; dort steht der Knopf **PDF oeffnen…**.
Wird eine gezogene PDF-Datei nicht gefunden, sagt das Programm Bescheid und die
Liste bleibt offen. Das OCR laeuft im Hintergrund, oben ist ein
Fortschrittsbalken. 27 Seiten dauern beim ersten Durchlauf etwa drei Minuten;
danach liegt das Ergebnis im Cache (Ordner `.cache`) und ist sofort wieder da.

## Die Liste

| Spalte | Bedeutung |
|---|---|
| **Sachkonto** | Auswahl-Feld (ganz links). Vorn dabei: `3400 - Wareneingang 19 %`, `3401 - Wareneingang 7 %`, `3420 - bezogene Leistungen 19 %`, `4930 - Kfz-Kosten / Werkstatt`, `4950 - Telekommunikation / Porto`, `4800 - Werbe- und Betriebskosten`, `6071 - Bewirtungskosten` |
| Sachkonto-Text | Der Text zum gewaehlten Sachkonto (mit aenderbar) |
| Seiten | Aus welchen PDF-Seiten der Beleg stammt |
| Lieferant | Firmenname, editierbar |
| **Lieferanten-Konto** | Auswahl-Feld direkt neben dem Lieferanten: `70001 - div. Lieferanten`, `3300 - Verbindlichkeiten aus LuL` |
| Konto-Text Lieferant | Text zum Kreditoren-Konto |
| Profil | Welches Erkennungsmuster verwendet wurde |
| Belegdatum, Rechnungsnummer, netto, USt %, USt-Betrag, brutto | editierbar; Datum akzeptiert auch `5. Mai 2026` |
| Buchungstext | Text fuer die Buchung |
| Dublette | ankreuzen = wird nicht gebucht (echte Doppelgaenger werden automatisch angekreuzt) |
| Pruefung | `OK` oder was auffaellig ist (rot hinterlegt) |

Der wichtigsten Schutz: **netto + USt = brutto** wird fuer jede Zeile nachgerechnet.
Stimmt die Rechnung nicht auf den Cent, erscheint die Warnung und die Betraege
werden gelb hinterlegt. weil das OCR Zahlen verwechseln kann, sollten Zeilen mit
Warnung vor dem Buchen mit dem Original verglichen werden.

Eigene Kontonummern koennen einfach ins Auswahl-Feld getippt werden
(z. B. `4930 Werkstatt`); sie kommen in die Liste und bleiben dort
(`konten.json` im Projektordner - kann auch direkt bearbeitet werden).

## Das Programm merkt sich Konten je Lieferant

Wird bei einem Beleg das **Sachkonto** (oder das Lieferanten-Konto) geaendert und
die Zeile hat einen Lieferantennamen, passiert zweierlei:

1. Alle Belege **mit demselben Lieferanten** in der Liste bekommen dasselbe Konto.
2. Die Zuordnung wird gemerkt und beim naechsten PDF als erstes vorgeschlagen.

Reihenfolge der Vorschlaege: **gemerktes Konto** -> Konto aus dem Lieferanten-Profil
(`profiles.py`) -> erster Eintrag der Kontenliste. Die Merksaetze stehen in
`konten.json` im Abschnitt `gelernt`, zum Beispiel:

```json
"gelernt": {
  "telekom deutschland": {
    "name": "Telekom Deutschland GmbH",
    "sach": "4800"
  }
}
```

Verglichen wird der Name ohne Gross/klein, ohne Satzzeichen und ohne Rechtsform:
"Telekom Deutschland GmbH", "telekom-deutschland gmbh" und "Telekom Deutschland"
gelten als derselbe Lieferant; auch kleine OCR-Fehler (ein verlesener Buchstabe)
werden noch erkannt.

Der Knopf **Merke-Konten…** zeigt alle Merksaetze und loescht sie auf Wunsch
(danach kommen die Vorschlaege wieder aus den Profilen). Eine gemerkte Nummer gilt
fuer alle kuenftigen Rechnungen dieses Lieferanten - passt sie fuer eine einzige
Rechnung nicht, diese Zeile in der Liste oder in der fertigen CSV nachtraeglich
aendern (eine geaenderte Zeile merkt sich allerdings wieder neu ein).

## Speichern

**CSV speichern…** schreibt eine CSV mit Semikolon und Komma als
Dezimalkomma (oeffnet sauber in Excel). Standardname:
`<pdfname>_buchungen.csv`. Der Knopf **Dubletten weglassen** entscheidet,
ob angekreuzte Doppelgaenger mit in die Datei kommen.

Spalten: `Sachkonto; Sachkonto-Text; Seiten; Lieferant; Lieferanten-Konto;
Konto-Text Lieferant; Profil; Belegdatum; Rechnungsnummer; Netto; USt-Satz %;
USt-Betrag; Brutto; Belegfeld 1; Buchungstext; Dublette; Pruefung; Konfidenz`

## Kommandozeile (optional)

```
python buchen.py rechnungen.pdf --csv buchungen.csv      # Tabelle + CSV
python buchen.py rechnungen.pdf --gui                    # Liste zum Korrigieren
python buchen.py                                         # nur Liste oeffnen
```

`--alle` zeigt auch Doppelgaenger, `--frisch` ignoriert den OCR-Cache,
`--konto 3400` setzt ein Sachkonto fuer alle Zeilen, `--json` schreibt alle
Details als JSON.

## Was im Hintergrund passiert

1. `ocr.py` - jede PDF-Seite wird mit 300 dpi gerastert und offline OCR-gelesen
   (PyMuPDF + RapidOCR). Ergebnis liegt als JSON im Ordner `.cache`.
2. `profiles.py` / `extract.py` - pro Anbieter (Amazon, CSL, MediaMarkt, Telekom,
   reichelt, …) wird anhand der Beschriftungen gesucht; ueberall wird
   netto + USt = brutto als Selbstkontrolle benutzt.
3. `grouping.py` / `pipeline.py` - Seiten zu Belegen gruppiert ("Seite 1 von 3"),
   Doppelgaenger erkannt.
4. `gui.py` - die Liste; `csv_export.py` - die CSV-Datei.

Noch nicht dabei (auf Wunsch zurueckgestellt): DATEV-Export. Der Code dafuer
liegt als `datev.py` bereit.

## Bibliotheken

Python 3.13, Pakete: `pymupdf`, `rapidocr-onnxruntime` (beide in der
Python-Umgebung), `PySide6` im Unterordner `libs` (wird von `buchen.py`
automatisch gefunden). Kein Internet noetig.
