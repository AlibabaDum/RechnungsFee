"""Lieferanten-Profile: Wer ist der Absender, welche Beschriftungen nutzt er,
welche Standards passen (Konto, USt-Saetze, Nummerformat).

Ein Profil liefert nur Hinweise; gefunden wird immer mit dem allgemeinen
Mechanismus aus fields.py plus der Betrags-Gegenprobe (netto + USt = brutto).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Beleg  # noqa: F401  (Typreferenz, Doku)


@dataclass
class Profil:
    name: str
    anbieter: str = ""                       # fester Lieferantenname
    signatur: list[str] = field(default_factory=list)   # eindeutige Woerter im Text
    nummer_label: list[str] = field(default_factory=list)
    nummer_regex: str = ""                   # Wert der Rechnungsnummer
    datum_label: list[str] = field(default_factory=list)
    netto_label: list[str] = field(default_factory=list)
    ust_label: list[str] = field(default_factory=list)
    brutto_label: list[str] = field(default_factory=list)
    ustsaetze: list[int] = field(default_factory=lambda: [19])
    konto_soll: str = "3400"
    hinweis: str = ""

    def beschreibung(self) -> str:
        return self.hinweis or self.name


# ------------------------------------------------------------------ Vorlagen

_COMMON_DATUM = ["rechnungsdatum", "datum der rechnung", "rechnungsdatum:", "datum"]

PROFILES: list[Profil] = [
    Profil(
        name="amazon_business",
        anbieter="Amazon Business EU S.a.r.l., Niederlassung Deutschland",
        signatur=["amazon business", "zahlbar innerhalb von 30 tagen"],
        nummer_label=["rechnungsnummer", "referenznummer"],
        nummer_regex=r"\bDE[0-9A-Z]{10,16}\b",
        datum_label=["rechnungsdatum"],
        netto_label=["zwischensumme", "ohne ust", "netto"],
        ust_label=["ust gesamt", "ust.", "mwst"],
        brutto_label=["zahlbetrag", "gesamtbetrag", "zahlungsbetrag"],
        ustsaetze=[19, 0],
        konto_soll="3400",
        hinweis="Amazon Business / Marketplace, Nummer beginnt meist mit DE",
    ),
    Profil(
        name="csl_computer",
        anbieter="CSL Computer GmbH",
        signatur=["csl computer", "csl computor", "hanseatenstra"],
        nummer_label=["belegnummer", "rechnungsnummer", "auftragsnummer"],
        nummer_regex=r"\b\d{6,12}\b",
        datum_label=["rechnungsdatum", "datum"],
        netto_label=["gesamt netto", "netto", "summe netto"],
        ust_label=["umsatzsteuer", "ust", "mwst"],
        brutto_label=["summe brutto", "gesamtbetrag", "rechnungsbetrag", "bruttobetrag"],
        ustsaetze=[19],
        konto_soll="3400",
    ),
    Profil(
        name="mediamarkt",
        anbieter="MMS E-Commerce GmbH (MediaMarkt/Saturn)",
        signatur=["mediamarkt", "media markt", "mms e commerce", "media saturn"],
        nummer_label=["rechnungsnummer", "vertragsnummer", "auftragsnummer"],
        nummer_regex=r"\b\d{6,14}\b",
        datum_label=["rechnungsdatum", "datum"],
        netto_label=["nettobetrag", "netto"],
        ust_label=["steuerbetrag", "ust", "mwst"],
        brutto_label=["bruttobetrag", "gesamtbetrag", "rechnungsbetrag", "gesamt"],
        ustsaetze=[19, 0],
        konto_soll="3400",
        hinweis="MMS E-Commerce: Reklamation/Rueckerstattung, Betrag negativ pruefen",
    ),
    Profil(
        name="telekom_festnetz",
        anbieter="Telekom Deutschland GmbH",
        signatur=["telekom deutschland", "festnetz-rechnung", "meinmagenta"],
        nummer_label=["rechnungsnummer", "verrechnungsnummer", "verrechnungsnr"],
        nummer_regex=r"\b\d{3}[ .]?\d{3}[ .]?\d{4}\b",
        datum_label=["datum", "rechnungsdatum"],
        netto_label=["summe betrag", "grundpreise", "netto"],
        ust_label=["ust", "mwst", "umlage"],
        brutto_label=["rechnungsbetrag", "summe", "gesamtbetrag"],
        ustsaetze=[19],
        konto_soll="3420",
        hinweis="Telekom Festnetz: Grundgebuehren, Konto 3420 (Telefon) ueblich",
    ),
    Profil(
        name="klarmobil",
        anbieter="klarmobil GmbH",
        signatur=["klarmobil", "mobiler funkt"],
        nummer_label=["rechnungsnummer", "vertragsnummer", "kundennummer"],
        nummer_regex=r"\b\d{8,14}\b",
        datum_label=["rechnungsdatum", "datum"],
        netto_label=["rechnungsbetrag netto", "netto"],
        ust_label=["ust-betrag", "ust", "mwst"],
        brutto_label=["rechnungsbetrag", "gesamtbetrag", "zahlbar"],
        ustsaetze=[19],
        konto_soll="3420",
        hinweis="Mobilfunk: 19 % auf Bruttopreise, Brutto = Netto + USt",
    ),
    Profil(
        name="boettcher",
        anbieter="Buero- und Objekt-Bueroeinrichtungsgesellschaft mbH (Boettcher AG)",
        signatur=["boettcher", "buero- und objekt", "stadtrodaer landstr"],
        nummer_label=["rechnung nr", "rechnungsnr", "rechnungsnummer"],
        nummer_regex=r"\b\d{9,14}\b",
        datum_label=["rechnungsdatum", "datum"],
        netto_label=["netto"],
        ust_label=["mwst 19", "mwst", "ust"],
        brutto_label=["gesamtwert", "gesarntwert", "gesamtbetrag", "rechnungsbetrag"],
        ustsaetze=[19, 7, 16, 0],
        konto_soll="3400",
        hinweis="Steuerkraeute 19/7/16/0 pro Zeile, Bruttosumme unten rechts",
    ),
    Profil(
        name="reichelt",
        anbieter="reichelt elektronik GmbH & Co. KG",
        signatur=["reichelt", "retcheltelektronik", "elektronikpark", "26452 sande"],
        nummer_label=["rechnungsnummer", "rechnungsnr", "auftragsnummer", "kundennummer"],
        nummer_regex=r"\b\d{5,10}\b",
        datum_label=["rechnungsdatum", "datum"],
        netto_label=["nettowert", "netto"],
        ust_label=["mwst 19", "mwst", "ust"],
        brutto_label=["endbetrag", "gesamtbetrag", "rechnungsbetrag", "bruttosumme"],
        ustsaetze=[19, 16, 7, 0],
        konto_soll="3400",
    ),
    Profil(
        name="europlot",
        anbieter="Europlot GmbH",
        signatur=["europlot", "europlo", "bergesfeld"],
        nummer_label=["rechnungsnummer", "belegnummer", "kundennummer"],
        nummer_regex=r"\bRE\d{5,10}\b|\b\d{5,10}\b",
        datum_label=["rechnungsdatum", "datum"],
        netto_label=["netto", "nettobetrag", "rechnungsbetrag netto"],
        ust_label=["umsatzsteuer", "ust", "mwst"],
        brutto_label=["bruttosumme", "gesamtbetrag", "endbetrag", "betrag eur", "rechnungsbetrag"],
        ustsaetze=[19, 16, 7],
        konto_soll="4930",
        hinweis="Software/Plotter-Service, haufig Konto 4930 (Betriebsstoffe)",
    ),
    Profil(
        name="apm_autoteile",
        anbieter="APM Autoteile GmbH",
        signatur=["apm autoteile", "apm autoteie", "bielefelderstr", "sulzetal"],
        nummer_label=["rechnung", "rechnungsnummer"],
        nummer_regex=r"\b\d{12,24}\b",
        datum_label=["datum", "rechnungsdatum"],
        netto_label=["nettobetrag", "netto"],
        ust_label=["umsatzsteuer 19", "umsatzsteuer", "ust"],
        brutto_label=["rechnungsbetrag", "gesamtbetrag", "gesamt"],
        ustsaetze=[19],
        konto_soll="3400",
        hinweis="eBay-Kauf (APM Autoteile), lange eBay-Bestellnummer",
    ),
    Profil(
        name="fbs_allgaeu",
        anbieter="FBS Allgaeu GmbH (softwarebilliger.de)",
        signatur=["fbs allgau", "softwarebilliger", "sedanstr", "alfeld"],
        nummer_label=["rechnungsnummer", "customer nr", "bestellnummer"],
        nummer_regex=r"\b\d{4,12}\b",
        datum_label=["rechnungsdatum", "datum", "date"],
        netto_label=["gesamt ohne mwst", "net amount", "netto"],
        ust_label=["mwst", "vat", "ust"],
        brutto_label=["gesamt inkl", "total", "gesamtbetrag", "endbetrag"],
        ustsaetze=[19, 0],
        konto_soll="4930",
        hinweis="Software, teils Export/steuerfrei - USt-Satz pruefen",
    ),
]


def passendes_profil(text_gefaltet, profil_liste: list[Profil] | None = None) -> tuple[Profil | None, float]:
    """text_gefaltet: gefalteter Seitentext (fields.fold). Bestes Profil + Trefferwert."""
    from .fields import fold, fuzzy

    beste, bestscore = None, 0.0
    for p in profil_liste or PROFILES:
        punkte = 0.0
        for sig in p.signatur:
            fs = fold(sig)
            if not fs:
                continue
            if fs in text_gefaltet:
                r = 1.0
            else:
                r = fuzzy(fs, text_gefaltet) if len(fs) > 12 else 0.0
            punkte = max(punkte, r)
        if punkte > bestscore:
            beste, bestscore = p, punkte
    return (beste, bestscore) if bestscore >= 0.80 else (None, bestscore)
