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

Ergebnis ist eine korrigierbare Liste, und die wandert auf zwei Wegen in die
Buchhaltung: direkt ins **Journal** der App (Knopf „Ins Journal buchen“ – ein
Journaleintrag je Beleg, angelegt exakt wie eine manuell erfasste Ausgabe, also
mit Kategorie, Zahlungsart, Lieferant und GoBD-Signatur) oder als **CSV-Datei**
(Semikolon, deutsche Kommazahlen) für das eigene Buchhaltungssystem. Das
Lieferanten-Konto braucht nur die CSV: im Journal steht stattdessen die
Kategorie, und der Lieferant wird über die Stammdaten verknüpft.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.connection import APP_DATA_DIR, get_db
from database.models import Journaleintrag, Kategorie, Lieferant, Unternehmen
from utils.beleg_ocr import csv_export
from utils.beleg_ocr.fields import parse_date as ocr_datum
from utils.beleg_ocr.konten import KREDITOR, SACH, Konto, Konten, laden, schluessel
from utils.beleg_ocr.model import Beleg
from utils.beleg_ocr.pipeline import Ergebnis, belege_aus_pdf
from .journal import _get_bar_kassenstand, create_eintrag
from .schemas import JournalEintragCreate

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


class BuchenRequest(BaseModel):
    """Die Liste, so wie sie nach dem Bearbeiten auf dem Bildschirm steht."""
    zeilen: list[BelegZeile]
    zahlungsart: str = "Bank"        # Standard, wenn der Beleg selbst nichts hergibt
    auch_ohne_pruefung: bool = False # Zeilen mit Schieflage bei den Summen bewusst mitbuchen


class BuchErgebnis(BaseModel):
    index: int                       # Position in der gesendeten Liste
    status: str                      # gebucht | uebersprungen | fehlgeschlagen
    belegnr: str = ""                # Journal-Belegnummer
    begruendung: str = ""            # warum nicht gebucht
    kategorie: str = ""              # gewählte Kategorie (zur Kontrolle)
    zahlungsart: str = ""
    hinweis: str = ""                # z. B. "kein Lieferant in den Stammdaten"


class BuchAntwort(BaseModel):
    gebucht: int = 0
    uebersprungen: int = 0
    fehlgeschlagen: int = 0
    summe: Decimal = Decimal("0.00")
    ergebnisse: list[BuchErgebnis] = []


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


def _ahnlichster_schluessel(sch: str, schluesel: dict) -> str:
    """Der Schlüssel aus `schluesel`, der am ehesten zu `sch` passt – '' bei Zweifel.

    Erst exakt, dann ähnlich ('Telekom Deutschland GmbH' trifft
    'telekom-deutschland'), dann als Teil: die Texterkennung liest von einer
    Adresse oft nur den oberen Teil und liefert damit 'amazon business eu
    s.a.r.l.' statt des vollen Namens. Treffen mehrere gleich gut, wird nicht
    geraten - dann entscheidet selbst, was auf dem Bildschirm steht.
    """
    if not sch:
        return ""
    if sch in schluesel:
        return sch
    treffer: list[tuple[float, str]] = []
    for key in schluesel:
        ahnlichkeit = SequenceMatcher(None, sch, key).ratio()
        if len(sch) >= 8 and (sch in key or key in sch):
            ahnlichkeit = max(ahnlichkeit, 0.95)
        if ahnlichkeit > 0.9:
            treffer.append((ahnlichkeit, key))
    if not treffer:
        return ""
    bester = max(w for w, _ in treffer)
    if sum(1 for w, _ in treffer if w == bester) > 1:
        return ""
    return max(treffer)[1]


def _passender_kreditor(lieferant: str, lookup: dict[str, str]) -> str:
    """Kreditoren-Nummer zu einem (OCR-)Lieferantennamen, notfalls ähnlich."""
    return lookup.get(_ahnlichster_schluessel(schluessel(lieferant), lookup), "")


def _kategorien_lookup(db: Session, rahmen: str) -> dict[str, tuple[Kategorie, str]]:
    """Kontonummer -> Kategorie im gewählten Kontenrahmen.

    Eine Nummer kann zu mehreren Kategorien gehören (dann trifft sie die
    Aufwandskategorie am ehesten). Der zweite Wert der Tuple ist ein Hinweis für
    die Rückmeldung, '' wenn alles eindeutig ist.
    """
    spalte = _spalte(rahmen)
    out: dict[str, tuple[Kategorie, str]] = {}
    for k in (db.query(Kategorie)
              .filter(Kategorie.aktiv.is_(True))
              .order_by(Kategorie.name).all()):
        nr = (getattr(k, spalte, None) or "").strip()
        if not nr:
            continue
        bisher = out.get(nr)
        if bisher is None:
            out[nr] = (k, "")
        elif bisher[0].kontenart != "Aufwand" and k.kontenart == "Aufwand":
            out[nr] = (k, f"Kontonummer {nr} steht mehrfach in den Kategorien – „{k.name}“ gewählt")
        else:
            out[nr] = (bisher[0], bisher[1] or f"Kontonummer {nr} steht mehrfach in den Kategorien – „{bisher[0].name}“ gewählt")
    return out


def _lieferanten_lookup(db: Session) -> dict[str, Lieferant]:
    """Vergleichsname -> Lieferant (Stammdaten), für die Verknüpfung der Buchung."""
    out: dict[str, Lieferant] = {}
    for lf in db.query(Lieferant).all():
        sch = schluessel(_lieferanten_name(lf))
        if sch and sch not in out:
            out[sch] = lf
    return out


def _passender_lieferant(schluesel: dict[str, Lieferant], name: str) -> Optional[Lieferant]:
    return schluesel.get(_ahnlichster_schluessel(schluessel(name), schluesel))


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


def _geld(rohtext: str) -> Optional[Decimal]:
    """Zahl aus dem Formular: '42,83', '42.83', '1.234,56', '1234.56 €'.

    Die Liste darf der Nutzer frei berichtigen, deshalb ist hier alles erlaubt,
    was man in Deutschland eintippt. None, wenn nichts Sinnvolles drinsteht.
    """
    text = (rohtext or "").replace("\u00a0", " ")
    text = "".join(ch for ch in text if not ch.isalpha()).strip()
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    else:
        ganzz, _, nachkomma = text.partition(".")
        if nachkomma.isdigit() and len(nachkomma) == 3 and ganzz.isdigit():
            text = ganzz + nachkomma  # Tausenderpunkt: 1.500 = 1500
    try:
        return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _datum(rohtext: str) -> Optional[date]:
    """Datum aus der Liste ('05.11.26', '05.11.2026', '2026-11-05'), notfalls
    so lesen wie die Texterkennung es würde."""
    text = (rohtext or "").strip()
    if not text:
        return None
    for format in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, format).date()
        except ValueError:
            continue
    gelesen = ocr_datum(text, year_hint=date.today().year)
    try:
        return datetime.strptime(gelesen, "%d.%m.%Y").date() if gelesen else None
    except ValueError:
        return None


def _beleg(z: BelegZeile) -> Beleg:
    d = z.model_dump()
    d.pop("pruefung", None)
    d.pop("konfidenz", None)
    d.pop("haben_konto_vorschlag", None)
    for feld in ("netto", "ust_betrag", "brutto"):
        text = str(d.get(feld) or "").strip()
        if text:
            wert = _geld(text)
            d[feld] = "" if wert is None else str(wert)
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


# ---------------------------------------------------------------------------
# Direkt ins Journal buchen
# ---------------------------------------------------------------------------

# Zahlungsarten des Journals, in dieser Reihenfolge geprüft: 'Kreditkarte' vor
# 'Bank', damit eine Kartenzahlung nicht als Überweisung landet.
_ZAHLUNGSARTEN: dict[str, tuple[str, ...]] = {
    "Bar": ("bar", "barzahlung", "cash"),
    "Karte": ("karte", "credit", "visa", "mastercard", "amex", "american express"),
    "PayPal": ("paypal", "pay pal"),
    "Bank": ("lastschrift", "überweisung", "ueberweisung", "bank", "abbuchung",
             "kontoauszug", "sepa", "dauerauftrag"),
}
# Was das Journal überhaupt kennt (siehe api/schemas.py::JournalEintragCreate).
ERLAUBTE_ZAHLUNGSARTEN = ("Bar", "Karte", "Bank", "PayPal", "Keine")


def _zahlungsart(rohtext: str, standard: str) -> str:
    """Zahlungsart vom Belegtext auf den Begriff des Journals bringen."""
    text = (rohtext or "").strip().lower()
    for art, worter in _ZAHLUNGSARTEN.items():
        if any(w in text for w in worter):
            return art
    return standard


def _buch_text(z: BelegZeile) -> str:
    """Beschreibung im Journal: Lieferant, Belegtext, Bestellnummer - ohne Doppelung."""
    teile: list[str] = []
    for teil in (z.lieferant, z.text, z.bestellnummer):
        teil = (teil or "").strip()
        if teil and teil not in teile:
            teile.append(teil)
    return " · ".join(teile)[:500] or "Beleg-Sammelimport"


def _bereits_gebucht(db: Session, *, datum: date, nummer: str, brutto: Decimal, text: str) -> str:
    """Belegnummer eines Eintrags, der genauso schon im Journal steht - '' wenn nicht.

    Damit darf ruhig zweimal geklickt werden, auch wenn dieselbe PDF-Datei
    morgen noch einmal auf der Seite liegt.
    """
    suche = (db.query(Journaleintrag.belegnr)
             .filter(Journaleintrag.datum == datum, Journaleintrag.brutto_betrag == brutto))
    suche = (suche.filter(Journaleintrag.externe_belegnr == nummer) if nummer
             else suche.filter(Journaleintrag.beschreibung == text))
    treffer = suche.order_by(Journaleintrag.id).first()
    return treffer[0] if treffer else ""


@router.post("/buchen", response_model=BuchAntwort)
def buchen(req: BuchenRequest, db: Session = Depends(get_db)) -> BuchAntwort:
    """Korrigierte Liste direkt ins Journal schreiben – ein Eintrag je Beleg.

    Der Weg ist genau der einer von Hand erfassten Ausgabe:
    ``api/journal.py::create_eintrag`` rechnet netto/USt vor, prüft Kategorie,
    Zahlungsart und Barkasse, zieht die Nummer aus dem Nummernkreis und
    unterschreibt GoBD. Was die Liste nicht sauber erklären kann (Dublette,
    fehlender Betrag, Konto ohne Kategorie), wird übersprungen und begründet –
    halbe Buchungen gibt es hier nicht.
    """
    rahmen = _rahmen(db)
    kategorien = _kategorien_lookup(db, rahmen)
    lieferanten = _lieferanten_lookup(db)
    standard = req.zahlungsart if req.zahlungsart in ERLAUBTE_ZAHLUNGSARTEN else "Bank"
    antwort = BuchAntwort()

    def _melde(index: int, status: str, *, belegnr: str = "", grund: str = "",
               kategorie: str = "", zahlungsart: str = "", hinweis: str = "") -> None:
        antwort.ergebnisse.append(BuchErgebnis(
            index=index, status=status, belegnr=belegnr, begruendung=grund,
            kategorie=kategorie, zahlungsart=zahlungsart, hinweis=hinweis))
        if status == "uebersprungen":
            antwort.uebersprungen += 1
        elif status == "fehlgeschlagen":
            antwort.fehlgeschlagen += 1

    for laufend, z in enumerate(req.zeilen):
        beleg = _beleg(z)
        if not beleg.buchbar:
            _melde(laufend, "uebersprungen",
                   grund="Dublette – derselbe Beleg steht schon weiter oben in der Liste")
            continue
        waehrung = (z.waehrung or "EUR").strip().upper()
        if waehrung not in ("EUR", ""):
            _melde(laufend, "uebersprungen",
                   grund=f"Währung {z.waehrung}: das Journal rechnet in Euro – "
                         "Betrag bitte in Euro eintragen")
            continue
        datum = _datum(beleg.belegdatum)
        if datum is None:
            _melde(laufend, "uebersprungen",
                   grund="Belegdatum fehlt oder ist unlesbar – bitte in der Liste eintragen")
            continue
        brutto = _geld(z.brutto)
        if brutto is None or brutto <= 0:
            _melde(laufend, "uebersprungen",
                   grund="Bruttobetrag fehlt oder ist keine Zahl – bitte eintragen")
            continue
        if not beleg.summen_ok and not req.auch_ohne_pruefung:
            _melde(laufend, "uebersprungen",
                   grund="netto + USt = brutto stimmt nicht – bitte berichtigen "
                         "oder Zeile bewusst mitnehmen")
            continue
        konto = (beleg.soll_konto or "").strip()
        kat, hinweis = kategorien.get(konto, (None, ""))  # type: ignore[arg-type]
        if kat is None:
            _melde(laufend, "uebersprungen",
                   grund=f"Sachkonto {konto or '(leer)'} gehört zu keiner Kategorie der App – "
                         "bitte eines der Konten aus der Liste wählen")
            continue
        zahlungsart = _zahlungsart(beleg.zahlungsart, standard)
        if zahlungsart == "Bar" and brutto > _get_bar_kassenstand(db):
            _melde(laufend, "uebersprungen",
                   grund="Bar-Ausgabe wäre höher als der Kassenstand – Zahlungsart ändern")
            continue
        nummer = (beleg.rechnungsnummer or beleg.belegfeld1 or "").strip()[:100]
        text = _buch_text(z)
        schon = _bereits_gebucht(db, datum=datum, nummer=nummer, brutto=brutto, text=text)
        if schon:
            _melde(laufend, "uebersprungen", belegnr=schon, kategorie=kat.name,
                   zahlungsart=zahlungsart,
                   grund="genau so steht die Buchung schon im Journal")
            continue
        lieferant = _passender_lieferant(lieferanten, beleg.lieferant)
        satz = Decimal(beleg.ust_satz or 0)
        daten = JournalEintragCreate(
            datum=datum,
            beschreibung=text,
            kategorie_id=kat.id,
            lieferant_id=lieferant.id if lieferant else None,
            zahlungsart=zahlungsart,
            art="Ausgabe",
            brutto_betrag=brutto,
            ust_satz=satz,
            vorsteuerabzug=satz > 0,
            externe_belegnr=nummer or None,
        )
        try:
            eintrag = create_eintrag(daten, db)
        except HTTPException as exc:      # z. B. Barkasse, Nummernkreis
            db.rollback()
            _melde(laufend, "fehlgeschlagen", kategorie=kat.name, zahlungsart=zahlungsart,
                   grund=str(exc.detail)[:200])
            continue
        except Exception as exc:          # alles andere bleibt nachlesbar
            db.rollback()
            _LOG.exception("Beleg-Sammelimport: Journal-Buchung Zeile %d fehlgeschlagen", laufend)
            _melde(laufend, "fehlgeschlagen", kategorie=kat.name, zahlungsart=zahlungsart,
                   grund=f"Journal hat abgelehnt: {str(exc)[:200]}")
            continue
        antwort.gebucht += 1
        antwort.summe = (antwort.summe + brutto).quantize(Decimal("0.01"))
        zusatz = [hinweis] if hinweis else []
        if lieferant is None:
            zusatz.append("kein Lieferant in den Stammdaten – Name ggf. ergänzen")
        if not beleg.summen_ok:
            zusatz.append("Summenprüfung war nicht in Ordnung")
        if not nummer:
            zusatz.append("ohne externe Belegnummer gebucht")
        _melde(laufend, "gebucht", belegnr=eintrag.belegnr,
               kategorie=f"{kat.name} ({konto})", zahlungsart=zahlungsart,
               hinweis="; ".join(zusatz))

    _LOG.info("Beleg-Sammelimport ins Journal: %d gebucht, %d übersprungen, %d fehlgeschlagen",
              antwort.gebucht, antwort.uebersprungen, antwort.fehlgeschlagen)
    return antwort


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
