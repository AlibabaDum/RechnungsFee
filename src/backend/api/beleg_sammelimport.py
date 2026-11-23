"""Beleg-Sammelimport: eine PDF mit mehreren Rechnungen → Buchungsliste → CSV.

Anders als der Beleg-Import im Postfach (eine Datei = ein Beleg, geht direkt ins
Journal) stehen hier zwei Dinge im Vordergrund:

1. **Sammel-PDFs auseinandernehmen.** Ein Scan mit 27 Seiten enthält oft 17
   Rechnungen. Die Seiten werden erkannt (Offline-OCR über
   ``utils/beleg_ocr``, ohne Tesseract und ohne Internet), zu Belegen gruppiert
   und Doppelgänger markiert.
2. **Vor dem Buchen korrigieren.** Die Liste zeigt jeden Beleg mit seiner
   Selbstprüfung (netto + USt = brutto) und zwei Dropdowns: Sachkonto und
   Lieferanten-Konto. Sachkonten kommen aus den eigenen Kategorien (Kontonummer
   im Kontenrahmen des Unternehmens), Lieferanten-Konten aus den Stammdaten.
   Was hier korrigiert wird, merkt sich das Programm je Lieferantennamen.

Ergebnis ist eine CSV-Datei (Semikolon, deutsche Kommazahlen) zum Import ins
eigene Buchhaltungssystem. Die Belege werden **nicht** ins Journal gebucht –
das ist ein bewusster Zwischenstand, siehe auch
``werkzeug/rechnung-buchen/ANDERUNGEN.md``.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.connection import APP_DATA_DIR, get_db
from database.models import Kategorie, Lieferant, Unternehmen
from utils.beleg_ocr import csv_export
from utils.beleg_ocr.konten import KREDITOR, SACH, Konto, Konten, laden, schluessel
from utils.beleg_ocr.model import Beleg
from utils.beleg_ocr.pipeline import Ergebnis, belege_aus_pdf

router = APIRouter(prefix="/api/beleg-sammelimport", tags=["Beleg-Sammelimport"])
_LOG = logging.getLogger("beleg_sammelimport")

MAX_PDF_GROESSE = 60 * 1024 * 1024  # 60 MB pro Datei, mehr braucht kein Sammel-Scan
DPI = 300

# Der OCR-Lauf dauert beim ersten Lesen (keine Datei wurde je zuvor erkannt).
HINWEIS_OCR = ("Das erste Lesen einer PDF-Datei rechnet jede Seite und braucht "
               "etwa 5–10 Sekunden pro Seite. Danach ist sie gespeichert und "
               "dieselbe Datei ist in Sekunden fertig.")


# ---------------------------------------------------------------------------
# Ablageorte (im Profildatenordner, wie Uploads und Backups)
# ---------------------------------------------------------------------------

def _ordner() -> Path:
    return Path(APP_DATA_DIR) / "beleg_ocr"


def _cache_ordner() -> Path:
    return _ordner() / "cache"


def _konten_datei() -> Path:
    return _ordner() / "konten.json"


def _hochgeladene_pdf(pruefsumme: str) -> Path:
    """Hochgeladene PDF ablegen. Der Name folgt dem Inhalt, doppelte Dateien
    landen deshalb immer auf derselben Scheibe und im selben OCR-Cache."""
    ziel_ordner = _ordner() / "pdfs"
    ziel_ordner.mkdir(parents=True, exist_ok=True)
    return ziel_ordner / f"{pruefsumme[:24]}.pdf"


def _konten() -> Konten:
    return laden(_konten_datei())


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class BelegZeile(BaseModel):
    """Ein Beleg in der Korrekturliste – so, wie das Formular ihn zeigt."""
    seiten: list[int] = []
    profil: str = ""
    grund: str = ""
    duplizat_von: Optional[int] = None
    lieferant: str = ""
    belegdatum: str = ""
    leistungsdatum: str = ""
    rechnungsnummer: str = ""
    bestellnummer: str = ""
    netto: str = ""
    ust_satz: Optional[int] = None
    ust_betrag: str = ""
    brutto: str = ""
    waehrung: str = "EUR"
    soll_konto: str = ""
    soll_konto_vorschlag: str = ""
    haben_konto: str = ""
    haben_konto_vorschlag: str = ""
    ust_konto: str = ""
    belegfeld1: str = ""
    text: str = ""
    zahlungsart: str = ""
    flag: list[str] = []
    vertrauen: dict[str, float] = {}
    pruefung: str = ""
    konfidenz: float = 0.0


class LeseErgebnis(BaseModel):
    dateiname: str
    seitenzahl: int
    belege: int
    buchbar: int
    dubletten: int
    kontenrahmen: str
    dauer_sekunden: float
    zeilen: list[BelegZeile]
    sachkonten: list[Konto]
    kreditorkonten: list[Konto]
    gelernt: list[dict[str, str]]
    hinweis: str = HINWEIS_OCR


class ZeilenRequest(BaseModel):
    zeilen: list[BelegZeile]
    nur_buchbare: bool = True
    dateiname: str = "buchungen"


class MerkenRequest(BaseModel):
    name: str
    sach: str = ""
    kreditor: str = ""


# ---------------------------------------------------------------------------
# Konten herbeiholen: Kategorien und Stammdaten der App
# ---------------------------------------------------------------------------

# Die Belegerkennung trägt als Haben-Konto die Sammel-Nummer "div. Lieferanten" ein,
# solange sie den Lieferanten nicht kennt. Diese Nummer ersetzt die App, sobald aus den
# Stammdaten (oder gemerkt) ein echtes Kreditorenkonto zu dem Namen gehört.
GRUND_KREDITOR = "70001"

_KONTO_SPALTE = {
    "SKR03": "konto_skr03",
    "SKR04": "konto_skr04",
    "SKR49": "konto_skr49",
}


def _rahmen(db: Session) -> str:
    unt = db.query(Unternehmen).first()
    return getattr(unt, "kontenrahmen", None) or "SKR03"


def _spalte(fuer_rahmen: str) -> str:
    return _KONTO_SPALTE.get(fuer_rahmen, "konto_skr03")


def sachkonten_aus_db(db: Session, rahmen: str) -> list[Konto]:
    """Aufwandskategorien mit Kontonummer im gewählten Kontenrahmen."""
    spalte = _spalte(rahmen)
    out: list[Konto] = []
    for k in (db.query(Kategorie)
              .filter(Kategorie.aktiv.is_(True), Kategorie.kontenart == "Aufwand")
              .order_by(Kategorie.name).all()):
        nr = (getattr(k, spalte, None) or "").strip()
        if nr:
            out.append(Konto(nr, k.name))
    return out


def _lieferanten_name(lf: Lieferant) -> str:
    """Anzeigename wie in den Stammdaten: Firma, sonst Vor- und Nachname."""
    return (lf.firmenname or " ".join(p for p in (lf.vorname, lf.nachname) if p) or "").strip()


def kreditorkonten_aus_db(db: Session) -> list[Konto]:
    """Lieferanten mit Kreditoren-Nummer aus den Stammdaten."""
    out: list[Konto] = []
    for lf in db.query(Lieferant).order_by(Lieferant.firmenname).all():
        nr = (lf.kreditor_nr or "").strip()
        name = _lieferanten_name(lf)
        if nr and name:
            out.append(Konto(nr, name))
    return out


def _kombinierte_konten(db: Session, art: str, rahmen: str, konten: Konten) -> list[Konto]:
    """Konten der App vorn, darunter was in konten.json ergänzt wurde."""
    aus_db = sachkonten_aus_db(db, rahmen) if art == SACH else kreditorkonten_aus_db(db)
    merged: list[Konto] = list(aus_db)
    for k in konten.liste(art):
        if not any(m.nr == k.nr for m in merged):
            merged.append(k)
    return merged


def _kreditoren_lookup(db: Session) -> dict[str, str]:
    """Vergleichsname des Lieferanten -> Kreditoren-Nummer (Stammdaten)."""
    out: dict[str, str] = {}
    for lf in db.query(Lieferant).all():
        nr = (lf.kreditor_nr or "").strip()
        sch = schluessel(_lieferanten_name(lf))
        if nr and sch:
            out[sch] = nr
    return out


def _passender_kreditor(lieferant: str, lookup: dict[str, str]) -> str:
    """Kreditoren-Nummer zu einem (OCR-)Lieferantennamen, notfalls ähnlich.

    Erst exakt, dann ähnlich ('Telekom Deutschland GmbH' trifft
    'telekom-deutschland'), dann als Teil des Stammdaten-Namens: die
    Texterkennung liest von einer Adresse oft nur den oberen Teil und liefert
    damit 'amazon business eu s.a.r.l.' statt des vollen Namens. Treffen mehrere
    Lieferanten gleich gut, wird nicht geraten - dann wählt die Liste selbst.
    """
    sch = schluessel(lieferant)
    if not sch:
        return ""
    if sch in lookup:
        return lookup[sch]
    treffer: list[tuple[float, str]] = []
    for key, nr in lookup.items():
        ahnlichkeit = SequenceMatcher(None, sch, key).ratio()
        if len(sch) >= 8 and (sch in key or key in sch):
            ahnlichkeit = max(ahnlichkeit, 0.95)
        if ahnlichkeit > 0.9:
            treffer.append((ahnlichkeit, nr))
    if not treffer:
        return ""
    bester = max(w for w, _ in treffer)
    if sum(1 for w, _ in treffer if w == bester) > 1:
        return ""
    return max(treffer)[1]


# ---------------------------------------------------------------------------
# Übersetzung Engine <-> Formular
# ---------------------------------------------------------------------------

def _zeile(b: Beleg, konten: Konten) -> BelegZeile:
    d = b.to_dict()
    conf = min(b.vertrauen.values()) if b.vertrauen else 0.0
    return BelegZeile(
        seiten=d.get("seiten", []),
        profil=d.get("profil", ""),
        grund=d.get("grund", ""),
        duplizat_von=d.get("duplizat_von"),
        lieferant=d.get("lieferant", ""),
        belegdatum=d.get("belegdatum", ""),
        leistungsdatum=d.get("leistungsdatum", ""),
        rechnungsnummer=d.get("rechnungsnummer", ""),
        bestellnummer=d.get("bestellnummer", ""),
        netto=d.get("netto", ""),
        ust_satz=d.get("ust_satz"),
        ust_betrag=d.get("ust_betrag", ""),
        brutto=d.get("brutto", ""),
        waehrung=d.get("waehrung", "EUR"),
        soll_konto=d.get("soll_konto", ""),
        soll_konto_vorschlag=d.get("soll_konto_vorschlag", ""),
        haben_konto=d.get("haben_konto", ""),
        haben_konto_vorschlag=d.get("haben_konto", ""),
        ust_konto=d.get("ust_konto", ""),
        belegfeld1=d.get("belegfeld1", ""),
        text=d.get("text", ""),
        zahlungsart=d.get("zahlungsart", ""),
        flag=d.get("flag", []),
        vertrauen=d.get("vertrauen", {}),
        pruefung=b.pruef_text(),
        konfidenz=conf,
    )


def _beleg(z: BelegZeile) -> Beleg:
    d = z.model_dump()
    d.pop("pruefung", None)
    d.pop("konfidenz", None)
    d.pop("haben_konto_vorschlag", None)
    return Beleg.from_dict(d)


def _vorausfuellen(ergebnis: Ergebnis, konten: Konten, db: Session, rahmen: str) -> None:
    """Soll-/Haben-Konto einsetzen: erst gemerkt, dann Stammdaten, dann Profil."""
    known_sach = {k.nr for k in _kombinierte_konten(db, SACH, rahmen, konten)}
    known_kred = {k.nr for k in _kombinierte_konten(db, KREDITOR, rahmen, konten)}
    kred_lookup = _kreditoren_lookup(db)
    for b in ergebnis.belege:
        gelernt_sach = konten.gelernt_fuer(SACH, b.lieferant)
        gelernt_kred = konten.gelernt_fuer(KREDITOR, b.lieferant)
        if gelernt_sach and (not b.soll_konto or b.soll_konto == b.soll_konto_vorschlag):
            b.soll_konto = gelernt_sach
        if not b.soll_konto:
            vorschlag = b.soll_konto_vorschlag or ""
            if vorschlag and vorschlag in known_sach:
                b.soll_konto = vorschlag
            elif vorschlag:
                # Profil schlägt ein Konto vor, das im eigenen Kontenrahmen
                # nicht vorkommt (z. B. SKR03-Vorschlag bei SKR04-Buchung).
                b.soll_konto = vorschlag
                konten.ergaenze(SACH, vorschlag, f"Vorschlag Profil {b.profil}")
                if b.profil not in " ".join(b.flag):
                    b.flag.append(
                        f"Konto {vorschlag} ist im Kontenrahmen {rahmen} nicht unter den "
                        "Kategorien – bitte prüfen"
                    )
        if gelernt_kred:
            b.haben_konto = gelernt_kred
        elif not b.haben_konto or b.haben_konto == GRUND_KREDITOR:
            b.haben_konto = _passender_kreditor(b.lieferant, kred_lookup) or b.haben_konto
        if b.haben_konto and b.haben_konto not in known_kred:
            konten.ergaenze(KREDITOR, b.haben_konto, b.lieferant)


# ---------------------------------------------------------------------------
# Endpunkte
# ---------------------------------------------------------------------------

@router.post("/lesen", response_model=LeseErgebnis)
def lesen(datei: UploadFile = File(...), db: Session = Depends(get_db)) -> LeseErgebnis:
    """PDF-Datei hochladen, OCRen, Belege als korrigierbare Liste zurückgeben."""
    if (datei.content_type or "").split(";")[0].strip().lower() not in ("application/pdf", ""):
        raise HTTPException(status_code=400, detail="Bitte eine PDF-Datei hochladen.")
    inhalt = datei.file.read()
    if not inhalt:
        raise HTTPException(status_code=400, detail="Die Datei ist leer.")
    if len(inhalt) > MAX_PDF_GROESSE:
        raise HTTPException(status_code=413,
                            detail=f"PDF ist größer als {MAX_PDF_GROESSE // (1024 * 1024)} MB.")
    if not inhalt[:5].startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Das ist keine PDF-Datei.")

    import time
    start = time.time()
    pruefsumme = hashlib.sha256(inhalt).hexdigest()
    ziel = _hochgeladene_pdf(pruefsumme)
    ziel.write_bytes(inhalt)

    def _fortschritt(seite: int, gesamt: int) -> None:
        _LOG.info("Beleg-Sammelimport %s: OCR Seite %d/%d", datei.filename, seite, gesamt)

    try:
        ergebnis = belege_aus_pdf(ziel, dpi=DPI, progress=_fortschritt,
                                  cache_dir=_cache_ordner())
    except Exception as exc:  # OCR-Schritte können viele Fehlerarten haben
        _LOG.exception("Beleg-Sammelimport fehlgeschlagen: %s", datei.filename)
        raise HTTPException(status_code=500,
                            detail=f"Erkennung fehlgeschlagen: {exc}") from exc

    konten = _konten()
    rahmen = _rahmen(db)
    _vorausfuellen(ergebnis, konten, db, rahmen)
    konten.speichere()

    zeilen = [_zeile(b, konten) for b in ergebnis.belege]
    dauer = round(time.time() - start, 1)
    _LOG.info("Beleg-Sammelimport %s: %d Seiten, %d Belege in %.1f s",
              datei.filename, len(ergebnis.seiten), len(zeilen), dauer)
    return LeseErgebnis(
        dateiname=datei.filename or ziel.name,
        seitenzahl=len(ergebnis.seiten),
        belege=len(zeilen),
        buchbar=sum(1 for b in ergebnis.belege if b.buchbar),
        dubletten=sum(1 for b in ergebnis.belege if not b.buchbar),
        kontenrahmen=rahmen,
        dauer_sekunden=dauer,
        zeilen=zeilen,
        sachkonten=_kombinierte_konten(db, SACH, rahmen, konten),
        kreditorkonten=_kombinierte_konten(db, KREDITOR, rahmen, konten),
        gelernt=konten.gemerkte(),
    )


@router.post("/csv")
def csv_download(req: ZeilenRequest) -> Response:
    """Korrigierte Liste als CSV (Semikolon, deutsche Kommazahlen, UTF-8 mit BOM)."""
    konten = _konten()
    belege = [_beleg(z) for z in req.zeilen]
    text = csv_export.csv_text(belege, konten, nur_buchbare=req.nur_buchbare)
    stamm = "".join(c for c in Path(req.dateiname).stem if c not in '\\/:*?"<>|').strip()
    dateiname = f"{stamm or 'buchungen'}_buchungen.csv"
    return Response(
        content=text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{dateiname}"'},
    )


@router.get("/listen")
def listen(db: Session = Depends(get_db)) -> dict:
    """Dropdown-Inhalte und gemerkte Zuordnungen (ohne neue OCR-Läufe)."""
    konten = _konten()
    rahmen = _rahmen(db)
    return {
        "kontenrahmen": rahmen,
        "sachkonten": [asdict(k) for k in _kombinierte_konten(db, SACH, rahmen, konten)],
        "kreditorkonten": [asdict(k) for k in _kombinierte_konten(db, KREDITOR, rahmen, konten)],
        "gelernt": konten.gemerkte(),
        "hinweis": HINWEIS_OCR,
    }


@router.post("/merken")
def merken(req: MerkenRequest) -> dict:
    """Konto je Lieferant merken (wirkt auf alle künftigen PDFs mit diesem Namen)."""
    konten = _konten()
    for art, nr in ((SACH, req.sach), (KREDITOR, req.kreditor)):
        nr = (nr or "").strip()
        if not nr:
            continue
        konten.ergaenze(art, nr)
        konten.merke(art, req.name, nr)
    konten.speichere()
    return {"gelernt": konten.gemerkte()}


@router.post("/zuruecksetzen")
def zuruecksetzen() -> dict:
    """Alle gemerkten Lieferanten-Zuordnungen löschen."""
    konten = _konten()
    anzahl = konten.lerne_zuruecksetzen()
    return {"geloescht": anzahl, "gelernt": konten.gemerkte()}


@router.get("/caches")
def caches() -> dict:
    """Welche PDFs schon erkannt sind (OCR-Cache, zur Kontrolle aufräumbar)."""
    ordner = _cache_ordner()
    dateien = sorted(ordner.glob("ocr_*.json")) if ordner.exists() else []
    return {
        "ordner": str(ordner),
        "gefundene_pdfs": len(dateien),
        "groesse_kb": sum(p.stat().st_size for p in dateien) // 1024 if dateien else 0,
    }
