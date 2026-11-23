"""Beleg-Sammelimport (Sammel-PDF -> korrigierbare Buchungsliste -> CSV).

Die OCR selbst läuft in den Tests nicht (dauert Minuten und braucht die
Erkennungsmodelle); getestet wird alles, was die App daraus baut: Gruppierung
und Felder aus bereits erkannten OCR-Zeilen, die Konto-Vorschläge aus
Kategorien und Stammdaten, das Lernen je Lieferant und die CSV-Ausgabe.
"""
from decimal import Decimal
from io import BytesIO

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers, UploadFile

from api import beleg_sammelimport as api
from database.connection import Base
from database.models import Kategorie, Lieferant, Unternehmen
from utils.beleg_ocr import csv_export
from utils.beleg_ocr.konten import KREDITOR, SACH, Konto, Konten, laden, schluessel
from utils.beleg_ocr.model import Beleg
from utils.beleg_ocr.ocr import Line, PageText
from utils.beleg_ocr.pipeline import Ergebnis, belege_aus_seiten_objekten
import utils.beleg_ocr.pipeline as pipeline


# ---------------------------------------------------------------------------
# Hilfsmittel: OCR-Seiten, wie die Erkennung sie liefern würde
# ---------------------------------------------------------------------------

_AMAZON = [
    "amazon business",
    "Amazon Business EU S.a.r.l., Niederlassung Deutschland",
    "Rechnungsnummer DE1234567890AB",
    "Rechnungsdatum 05.11.2026",
    "Zwischensumme 100,00 €",
    "UST gesamt 19,00 €",
    "Zahlbetrag 119,00 €",
    "zahlbar innerhalb von 30 Tagen",
]

_CSL = [
    "CSL Computer GmbH",
    "Belegnummer 123456",
    "Rechnungsdatum 06.11.2026",
    "Gesamt netto 50,00 €",
    "Umsatzsteuer 9,50 €",
    "Summe brutto 59,50 €",
    "Seite 1 von 2",
]


def _seite(nr: int, texte: list[str], conf: float = 0.97) -> PageText:
    zeilen, y = [], 60.0
    for text in texte:
        zeilen.append(Line(text=text, x0=60.0, y0=y, x1=300.0, y1=y + 12.0, conf=conf))
        y += 18.0
    return PageText(page_no=nr, width=595.0, height=842.0, lines=zeilen, mean_conf=conf)


def _beleg(**werte) -> Beleg:
    grund = dict(seiten=[1], lieferant="Amazon Business EU S.a.r.l., Niederlassung Deutschland",
                 belegdatum="05.11.2026", rechnungsnummer="DE1234567890AB",
                 netto=Decimal("100.00"), ust_satz=19, ust_betrag=Decimal("19.00"),
                 brutto=Decimal("119.00"), soll_konto="3400", haben_konto="70001",
                 text="Amazon Business", belegfeld1="DE1234567890AB")
    grund.update(werte)
    return Beleg(**grund)


def _sitzung(rahmen: str = "SKR03"):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Unternehmen(firmenname="Test GmbH", strasse="Teststr.", hausnummer="1",
                            plz="12345", ort="Testort", kontenrahmen=rahmen))
    session.add_all([
        Kategorie(name="Wareneingang 19%", kontenart="Aufwand",
                  konto_skr03="3400", konto_skr04="5400"),
        Kategorie(name="Telefon und Internet", kontenart="Aufwand",
                  konto_skr03="4930", konto_skr04="6430"),
        Kategorie(name="Erloes 19%", kontenart="Erloes",
                  konto_skr03="8400", konto_skr04="3400"),
        Kategorie(name="Ohne Kontonummer", kontenart="Aufwand"),
    ])
    session.add(Lieferant(firmenname="Amazon Business EU S.a.r.l., Niederlassung Deutschland",
                          kreditor_nr="70100"))
    session.add(Lieferant(firmenname="", vorname="Nina", nachname="Netzwerk",
                          kreditor_nr="70101"))
    session.commit()
    return session


@pytest.fixture
def db():
    session = _sitzung()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Engine: Seiten -> Belege (ohne OCR)
# ---------------------------------------------------------------------------

def test_sammel_pdf_wird_zu_belegen_und_dublette():
    seiten = [_seite(1, _AMAZON), _seite(2, _CSL),
              _seite(3, ["weitere Positionen", "Seite 2 von 2"]), _seite(4, _AMAZON)]
    ergebnis = belege_aus_seiten_objekten("sammel.pdf", seiten)

    assert len(ergebnis.belege) == 3, [(b.profil, b.seiten, b.grund) for b in ergebnis.belege]
    erste, csl, zweite = ergebnis.belege
    assert erste.profil == "amazon_business"
    assert erste.seiten == [1]
    assert csl.profil == "csl_computer"
    assert csl.seiten == [2, 3]
    # gleiche Rechnungsnummer erneut im selben PDF -> Dublette, wird nicht gebucht
    assert zweite.duplizat_von == 0
    assert not zweite.buchbar
    assert "Dublette" in zweite.pruef_text()


def test_selbstpruefung_netto_ust_brutto():
    assert _beleg().pruef_text() == "OK"
    kaputt = _beleg(ust_betrag=Decimal("10.00"))
    assert "netto + USt = brutto stimmt nicht" in kaputt.pruef_text()
    assert "fehlt: belegdatum" in _beleg(belegdatum="").pruef_text()


def test_cache_ordner_wird_durchgereicht(monkeypatch, tmp_path):
    """Die App will den OCR-Cache im Profildatenordner haben, nicht neben dem Code."""
    gesehen: dict = {}

    def fake_ocr(pdf_path, dpi=300, use_cache=True, progress=None, cache_dir=None):
        gesehen.update(cache_dir=cache_dir, pdf_path=pdf_path)
        return [_seite(1, _AMAZON)]

    monkeypatch.setattr(pipeline, "ocr_pdf", fake_ocr)
    pipeline.belege_aus_pdf("irgendein.pdf", cache_dir=tmp_path)
    assert gesehen["cache_dir"] == tmp_path
    assert gesehen["pdf_path"] == "irgendein.pdf"


# ---------------------------------------------------------------------------
# Konten lernen (Datei liegt im Profildatenordner)
# ---------------------------------------------------------------------------

def test_konten_pfad_und_lernen(tmp_path):
    datei = tmp_path / "unter" / "ordner" / "konten.json"
    konten = laden(datei)
    konten.speichere()                          # legt den Ordner selbst an
    assert datei.exists()

    konten.merke(SACH, "Amazon Business EU S.a.r.l.", "4930")
    konten.merke(KREDITOR, "Amazon Business EU S.a.r.l.", "70100")
    konten.speichere()

    wieder = laden(datei)
    assert wieder.gelernt_fuer(SACH, "amazon business eu s.a.r.l.") == "4930"
    assert wieder.gelernt_fuer(KREDITOR, "Amazon Business EU S.a.r.l.") == "70100"
    assert wieder.pfad == datei
    assert wieder.lerne_zuruecksetzen() == 1
    assert laden(datei).gelernt_fuer(SACH, "amazon business") == ""
    # neue Nummer landet automatisch in der Liste, ohne dass man sie eintippt
    assert wieder.ergaenze(SACH, "9999", "Sonstiges")
    wieder.speichere()
    assert [k.nr for k in laden(datei).sach][-1] == "9999"


# ---------------------------------------------------------------------------
# Datenbank: Kategorien (Sachkonto) und Lieferanten (Kreditoren-Konto)
# ---------------------------------------------------------------------------

def test_sachkonten_kommen_aus_den_kategorien(db):
    assert {k.nr for k in api.sachkonten_aus_db(db, "SKR03")} == {"3400", "4930"}
    assert {k.nr for k in api.sachkonten_aus_db(db, "SKR04")} == {"5400", "6430"}
    # Erlöskategorien und Kategorien ohne Nummer gehören nicht in die Vorschlagsliste
    assert all(k.text for k in api.sachkonten_aus_db(db, "SKR03"))


def test_kreditorkonten_kommen_aus_den_stammdaten(db):
    assert {k.nr: k.text for k in api.kreditorkonten_aus_db(db)} == {
        "70100": "Amazon Business EU S.a.r.l., Niederlassung Deutschland",
        "70101": "Nina Netzwerk",               # Firmenloser Eintrag: Vor- + Nachname
    }


def test_kreditor_wird_ueber_aehnlichen_namen_gefunden(db):
    lookup = api._kreditoren_lookup(db)
    # Die OCR liest Lieferantennamen oft verkürzt oder mit Lesefehler
    assert api._passender_kreditor("Amazon Business EU S.a.r.l.", lookup) == "70100"
    assert api._passender_kreditor(
        "Amazon Business EU S.a.r.l., Niederlassung Deutschand", lookup) == "70100"
    assert api._passender_kreditor("Nina Netzwerk", lookup) == "70101"
    assert api._passender_kreditor("Telekom Deutschland GmbH", lookup) == ""


def test_vorausfuellen_ohne_gelerntes(db):
    konten = Konten([], [])
    ergebnis = Ergebnis(quelle="s.pdf", seiten=[], gruppen=[],
                        belege=[_beleg(soll_konto="", soll_konto_vorschlag="3400",
                                       haben_konto="")])
    api._vorausfuellen(ergebnis, konten, db, "SKR03")
    assert ergebnis.belege[0].soll_konto == "3400"
    assert ergebnis.belege[0].haben_konto == "70100"


def test_vorausfuellen_gelerntes_sticht_profil(db):
    name = "Amazon Business EU S.a.r.l., Niederlassung Deutschland"
    konten = Konten([], [], gelernt={schluessel(name): {"name": name, "sach": "4930"}})
    ergebnis = Ergebnis(quelle="s.pdf", seiten=[], gruppen=[],
                        belege=[_beleg(soll_konto="3400", soll_konto_vorschlag="3400")])
    api._vorausfuellen(ergebnis, konten, db, "SKR03")
    assert ergebnis.belege[0].soll_konto == "4930"


def test_vorausfuellen_fremder_kontenrahmen_wird_gemerkt():
    """Profil schlägt 3400 vor (SKR03); gebucht wird aber nach SKR04 -> Hinweis statt stumm."""
    db_skr04 = _sitzung("SKR04")
    try:
        konten = Konten([], [])
        ergebnis = Ergebnis(quelle="s.pdf", seiten=[], gruppen=[],
                            belege=[_beleg(soll_konto="", soll_konto_vorschlag="3400",
                                           profil="amazon_business")])
        api._vorausfuellen(ergebnis, konten, db_skr04, "SKR04")
        assert ergebnis.belege[0].soll_konto == "3400"
        assert any("Kontenrahmen SKR04" in f for f in ergebnis.belege[0].flag)
        assert any(k.nr == "3400" for k in konten.sach)   # für das Dropdown geöffnet
    finally:
        db_skr04.close()


# ---------------------------------------------------------------------------
# Endpunkte (ohne OCR, ohne HTTP-Transport)
# ---------------------------------------------------------------------------

@pytest.fixture
def im_profildatenordner(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "APP_DATA_DIR", tmp_path)
    return tmp_path


def _hochladen(inhalt: bytes = b"%PDF-1.7\nstumpf") -> UploadFile:
    return UploadFile(filename="sammelrechnungen.pdf", file=BytesIO(inhalt),
                      headers=Headers({"content-type": "application/pdf"}))


def test_lesen_liefert_korrigierbare_listenspalten(monkeypatch, im_profildatenordner, db):
    def fake(pdf_path, dpi=300, use_cache=True, progress=None, profil_min=0.80,
             cache_dir=None):
        assert cache_dir == im_profildatenordner / "beleg_ocr" / "cache"
        assert pdf_path.exists()                     # PDF wurde im Profil abgelegt
        return Ergebnis(quelle=str(pdf_path), seiten=[_seite(1, _AMAZON)], gruppen=[],
                        belege=[_beleg(soll_konto="", soll_konto_vorschlag="3400")])

    monkeypatch.setattr(api, "belege_aus_pdf", fake)
    ergebnis = api.lesen(_hochladen(), db=db)

    assert ergebnis.belege == 1 and ergebnis.buchbar == 1 and ergebnis.dubletten == 0
    assert ergebnis.kontenrahmen == "SKR03"
    assert {"3400", "4930"} <= {k.nr for k in ergebnis.sachkonten}
    assert {"70001", "70100", "70101"} <= {k.nr for k in ergebnis.kreditorkonten}
    zeile = ergebnis.zeilen[0]
    assert zeile.pruefung == "OK" and zeile.netto == "100.00"
    assert zeile.soll_konto == "3400" and zeile.haben_konto == "70100"


def test_lesen_lehnt_keine_pdf_ab(im_profildatenordner, db):
    with pytest.raises(api.HTTPException) as falsch:
        api.lesen(UploadFile(filename="foto.jpg", file=BytesIO(b"GIF89a"),
                             headers=Headers({"content-type": "image/jpeg"})), db=db)
    assert falsch.value.status_code == 400
    with pytest.raises(api.HTTPException) as zu_gross:
        api.lesen(_hochladen(b"%PDF-1.7\n" + b"x" * (api.MAX_PDF_GROESSE + 1)), db=db)
    assert zu_gross.value.status_code == 413


def test_csv_download_deutsche_zahlen_und_semikolon(im_profildatenordner):
    antwort = api.csv_download(api.ZeilenRequest(
        zeilen=[api.BelegZeile(**_beleg().to_dict()),
                api.BelegZeile(**_beleg(seiten=[2], duplizat_von=0).to_dict())],
        dateiname="sammelrechnungen.pdf"))
    text = antwort.body.decode("utf-8-sig")
    assert antwort.media_type == "text/csv; charset=utf-8"
    assert "sammelrechnungen_buchungen.csv" in antwort.headers["content-disposition"]
    assert text.splitlines()[0].startswith("Sachkonto;Sachkonto-Text;Seiten;Lieferant")
    assert "119,00" in text
    assert text.count("\r\n") == 2          # Kopf + eine buchbare Zeile (Dublette bleibt raus)
    # ...und mit der Dublette, wenn sie mitgeliefert werden soll
    alle = api.csv_download(api.ZeilenRequest(
        zeilen=[api.BelegZeile(**_beleg(duplizat_von=0).to_dict())],
        nur_buchbare=False)).body.decode("utf-8-sig")
    assert "Dublette" in alle


def test_merken_und_zuruecksetzen(im_profildatenordner, db):
    assert api.listen(db=db)["gelernt"] == []
    nachher = api.merken(api.MerkenRequest(name="Amazon Business EU S.a.r.l.",
                                           sach="4930", kreditor="70100"))
    assert nachher["gelernt"][0]["sach"] == "4930"
    assert (im_profildatenordner / "beleg_ocr" / "konten.json").exists()
    assert api.listen(db=db)["gelernt"][0]["kreditor"] == "70100"
    assert api.zuruecksetzen()["geloescht"] == 1
    assert api.listen(db=db)["gelernt"] == []


def test_csv_liest_wieder_was_hinausgeschrieben_wurde(im_profildatenordner):
    """Die CSV ist so gebaut, dass sie erneut eingelesen werden kann."""
    pfad = im_profildatenordner / "buchungen.csv"
    text = csv_export.csv_text([_beleg()], Konten([], []), nur_buchbare=True)
    pfad.write_text(text, encoding="utf-8-sig", newline="")
    zurueck = csv_export.lese(pfad)
    assert len(zurueck) == 1
    assert zurueck[0].soll_konto == "3400"
    assert zurueck[0].brutto == Decimal("119.00")
