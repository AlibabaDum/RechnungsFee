"""Rechnungs-Extraktion: aus den OCR-Seiten eines Belegs einen Buchungssatz machen.

Kernstueck ist die Betrags-Gegenprobe: netto + USt muss (auf 2 Cent) genau
brutto ergeben. Alle drei Groessen werden deshalb aus dem Zahlen-Vorrat des
Belegs kombiniert und nur die Kombination uebernommen, die aufgeht. Das
faengt OCR-Fehler ab, bei denen Komma und Punkt verwechselt werden.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from .fields import (_beste_treffer, all_amounts, amounts_of, dominant_year,
                     find_labels, fold, parse_date, rate_of, read_date_multi,
                     value_below, value_right)
from .grouping import SeitenGruppe
from .model import Beleg
from .ocr import Line, PageText
from .profiles import Profil, PROFILES

GENERIC = Profil(
    name="generisch",
    nummer_label=["rechnungsnummer", "rechnungs-nr", "rechnungsnr", "rechnung nr",
                  "rechnung nr.", "belegnummer", "beleg nr", "vorgangsnummer",
                  "invoice number", "invoice no"],
    nummer_regex=r"\b[0-9A-Z][0-9A-Z/.-]{4,24}\b",
    datum_label=["rechnungsdatum", "datum", "datum der rechnung", "rechnungsdatum:"],
    netto_label=["netto", "nettobetrag", "nettowert", "gesamt netto", "summe netto",
                 "zwischensumme", "rechnungsbetrag netto", "summe ohne", "gesamt ohne",
                 "net amount"],
    ust_label=["umsatzsteuer", "ust", "ust-betrag", "mwst", "steuerbetrag", "ust gesamt",
               "umsatzsteuer 19", "ust betrag"],
    brutto_label=["gesamtbetrag", "endbetrag", "endsumme", "rechnungsbetrag",
                  "summe brutto", "bruttobetrag", "zahlbetrag", "gesamtwert",
                  "bruttosumme", "zahlungsbetrag", "rechnungsbetrag gesamt", "summe",
                  "gesamt"],
    ustsaetze=[19, 16, 7, 5, 0],
    konto_soll="3400",
)

# Nummern, die auf Rechnungen oft daneben stehen, aber nicht die Belegnummer sind:
# nur als letzte Rettung benutzen (sonst verschmelzen zwei Rechnungen desselben
# Lieferaenders zu einem Beleg).
NUMMER_RESTFALL = ["vertragsnummer", "bestellnummer", "auftragsnummer", "kundennummer",
                   "kundennr", "auftrags-nr", "bestell-nr"]


def _profil_der_gruppe(gruppe: SeitenGruppe, seiten: dict[int, PageText]) -> Profil:
    for p in PROFILES:
        if p.name == gruppe.profil:
            return p
    text = fold(gruppe.seiten_text([seiten[n] for n in sorted(gruppe.seiten) if n in seiten]))
    for p in PROFILES:
        for sig in p.signatur:
            fs = fold(sig)
            if fs and fs in text:
                return p
    return GENERIC


def _beschriftungs_werte(lines: list[Line], labels: list[str]) -> list[tuple[Decimal, float, str]]:
    """(wert, konfidenz, beschriftung) - Werte, die an einer Beschriftung haengen."""
    out: list[tuple[Decimal, float, str]] = []
    for h in find_labels(lines, labels, min_score=0.76):
        for txt, basis in ((h.rest(), 1.0), (value_right(h, lines)[0], 0.9),
                           (value_below(h, lines)[0], 0.85)):
            if not txt:
                continue
            for v in amounts_of(txt):
                out.append((v, basis * h.score, h.keyword))
    return out


def _betraege(page_lines: list[list[Line]], profil: Profil):
    """netto, ust-satz, ust, brutto, konfidenz, flagen.

    Herzstueck: gesucht wird das Tripel netto + USt = brutto. Kandidaten sind alle
    Geldbeträge der Seiten; bevorzugt wird das Tripel, dessen Werte an den
    ueblichen Beschriftungen haengen und dessen Bruttowert der groesste Betrag ist.
    """
    flag: list[str] = []
    pool: dict[Decimal, float] = {}
    beschr: dict[str, dict[Decimal, float]] = {"netto": {}, "ust": {}, "brutto": {}}
    groesster: Decimal | None = None

    def add(v, conf: float):
        nonlocal groesster
        if v is None or v <= 0 or v > Decimal("200000"):
            return
        pool[v] = max(pool.get(v, 0.0), conf)
        if groesster is None or v > groesster:
            groesster = v

    for lines in page_lines:
        for art, labels in (("netto", profil.netto_label), ("ust", profil.ust_label),
                            ("brutto", profil.brutto_label)):
            for v, conf, _kw in _beschriftungs_werte(lines, labels):
                beschr[art][v] = max(beschr[art].get(v, 0.0), conf)
                add(v, conf + 0.30)
        for l, v in all_amounts(lines):
            add(v, 0.30 + 0.45 * float(l.conf))

    if not pool:
        return None, None, None, None, 0.0, ["keine Betraege im Text erkannt"]

    werte = sorted(pool)
    saetze = profil.ustsaetze or [19]
    toleranz = Decimal("0.02")

    def near(art: str, v: Decimal) -> float:
        for b, c in beschr[art].items():
            if abs(b - v) <= toleranz:
                return c
        return 0.0

    def kandidat(netto, ust, brutto, rate, abzog: float = 0.0):
        labels = [near("netto", netto), near("ust", ust), near("brutto", brutto)]
        conf = (pool[netto] + pool[ust] + pool[brutto]) / 3

        def mit_cents(v: Decimal) -> int:
            return 1 if -v.as_tuple().exponent == 2 else 0

        ranking = (
            sum(1 for c in labels if c > 0),                       # moeglichst gut beschriftet
            1 if rate in saetze else 0,                            # Satz passt zum Anbieter
            # Betrage mit Cent-Stelle sind die echten Geldbetraege; ohne diese
            # Regel wuerde aus "42,83" auch die Zahl 4283 als Betrag durchgehen.
            mit_cents(netto) + mit_cents(ust) + mit_cents(brutto),
            1 if groesster is not None and abs(brutto - groesster) <= toleranz else 0,
            round(conf + sum(labels) / 3 - abzog, 4),              # OCR-Sicherheit
            brutto,                                                # im Zweifel der groessere Beleg
        )
        return [ranking, netto, ust, brutto, rate, conf]

    beste = None

    def offer(k):
        nonlocal beste
        if beste is None or k[0] > beste[0]:
            beste = k

    # 1) netto + ust = brutto (exakt, mit Toleranz)
    for netto in werte:
        for ust in werte:
            if ust <= 0 or ust > netto * 2 + 1:
                continue
            rate = rate_of(netto, ust)
            if rate is None:
                continue
            brutto = next((b for b in werte if abs(b - (netto + ust)) <= toleranz), None)
            if brutto is None:
                continue
            offer(kandidat(netto, ust, brutto, rate))

    # 2) Bruttoware: die USt steckt im Endbetrag (netto und USt nur indirekt)
    for brutto in werte:
        for rate in [r for r in saetze if r]:
            netto = (brutto / (Decimal(1) + Decimal(rate) / Decimal(100))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            ust = brutto - netto
            n_hit = next((v for v in werte if abs(v - netto) <= toleranz), None)
            u_hit = next((v for v in werte if abs(v - ust) <= toleranz), None)
            if n_hit is None or u_hit is None:
                continue
            offer(kandidat(n_hit, u_hit, brutto, rate, abzog=0.15))

    if beste:
        _ranking, netto, ust, brutto, rate, conf = beste
        if abs(netto + ust - brutto) > toleranz:
            flag.append("Betriebspruefung netto+USt=brutto nicht erfuellt")
        if rate is not None and rate not in (0, 3, 5, 7, 16, 19):
            flag.append(f"USt-Satz {rate} % unplausibel - bitte pruefen")
        return netto, rate, ust, brutto, min(1.0, conf), flag

    # 3) Notloesung: nur was eindeutig an einer Beschriftung hängt
    def best_of(art: str) -> Decimal | None:
        if not beschr[art]:
            return None
        return max(beschr[art].items(), key=lambda kv: kv[1])[0]

    netto, ust, brutto = best_of("netto"), best_of("ust"), best_of("brutto")
    rate = rate_of(netto, ust) if (netto and ust is not None) else None
    if rate is None:
        for r in saetze:
            if r and brutto and ust is not None and abs(brutto / (1 + r / 100) * (r / 100) - ust) <= Decimal("0.05"):
                rate = r
                break
    if brutto is None and netto is not None and ust is not None:
        brutto = netto + ust
        flag.append("Bruttobetrag errechnet (netto + USt)")
    if ust is None and netto is not None and brutto is not None:
        ust = brutto - netto
        rate = rate_of(netto, ust)
        flag.append("USt-Betrag errechnet (brutto - netto)")
    if netto is None and ust is not None and brutto is not None:
        netto = brutto - ust
        flag.append("Nettobetrag errechnet (brutto - USt)")
    if netto is None and ust is None and brutto is None:
        flag.append("Betraege nicht sicher erkannt - bitte manuell eintragen")
    else:
        flag.append("Betriebspruefung netto+USt=brutto nicht erfuellt")
    return netto, rate, ust, brutto, 0.3, flag


def _sauber(txt: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", txt or "")


def _nummer_wert(txt: str, regexes: list[str]) -> str:
    """Belegnummer aus einem Textstueck holen; leer, wenn nichts plausibel ist."""
    if not txt or parse_date(txt):
        return ""          # ein Datum ist keine Belegnummer
    for rx in regexes:
        for m in re.finditer(rx, txt):
            k = m.group(0).strip(" .-/")
            if parse_date(k):
                continue
            z = _sauber(k)
            if len(z) >= 5 and any(c.isdigit() for c in z) and not z.isalpha():
                return k
    z = _sauber(txt)
    worter = txt.split()
    if (5 <= len(z) <= 24 and any(c.isdigit() for c in z) and len(worter) <= 3
            and all(any(c.isdigit() for c in w) for w in worter)):
        return txt.strip(" .-/")
    return ""


def _rechnungsnummer(page_lines: list[list[Line]], profil: Profil) -> tuple[str, float]:
    regexes = [r for r in (profil.nummer_regex, GENERIC.nummer_regex) if r]
    datum_woerter = [fold(k) for k in (profil.datum_label or []) + GENERIC.datum_label]

    def ist_datumszeile(zeile: Line) -> bool:
        """Eine Zeile wie 'Rechnungsdatum:' ist kein Platz fuer eine Belegnummer.

        Wichtig ist die Richtung: 'Rechnung:' enthaelt nicht 'rechnungsdatum' und
        darf deshalb als Nummer herhalten - das Wort 'Rechnungsdatum' dagegen schon.
        """
        text = fold(zeile.text)
        return any(k and k in text for k in datum_woerter)

    # 1) Beschriftungen der Reihe nach: die erste passende Beschriftung gewinnt.
    #    (Kunden- und Bestellnummern ganz zuletzt - sie sind nicht die Belegnummer.)
    stufen = [list(profil.nummer_label or []), GENERIC.nummer_label, NUMMER_RESTFALL]
    for stufe in stufen:
        for kw in stufe:
            for lines in page_lines:
                for h in _beste_treffer(lines, [kw], 0.76):
                    if ist_datumszeile(h.line):
                        continue
                    txt, conf = h.rest(), h.score
                    if not _nummer_wert(txt, regexes):
                        txt, conf = value_right(h, lines)
                    wert = _nummer_wert(txt, regexes)
                    if wert:
                        return wert, conf * h.score
    # 2) Nummer in der Kopfzeile der Seite, z. B. "Rechnung 29479011 / Seite 2 von 3"
    beste: tuple[tuple[float, float], str, float] | None = None
    for lines in page_lines:
        hoehe = max((l.y1 for l in lines), default=842.0)
        for l in lines:
            if l.y0 > 0.40 * hoehe:
                continue
            f = fold(l.text)
            if not any(w in f for w in ("rechnung", "beleg", "nr", "invoice", "vorgang")):
                continue
            for rx in regexes:
                for m in re.finditer(rx, l.text):
                    wert = _nummer_wert(m.group(0), regexes)
                    if not wert:
                        continue
                    punkte = (float(l.conf), -l.y0)
                    if beste is None or punkte > beste[0]:
                        beste = (punkte, wert, 0.70)
    if beste:
        return beste[1], beste[2]
    # 3) letzte Rettung: Nummer nahe einer (auch unscharfen) Beschriftung
    for lines in page_lines:
        for rx in regexes:
            for h in find_labels(lines, (profil.nummer_label or []) + GENERIC.nummer_label,
                                 min_score=0.70):
                for m in re.finditer(rx, h.line.text):
                    wert = _nummer_wert(m.group(0), regexes)
                    if wert:
                        return wert, 0.45
    return "", 0.0


def beleg_aus_gruppe(gruppe: SeitenGruppe, seiten: dict[int, PageText],
                     jahr_hinweis: int | None = None) -> Beleg:
    page_lines = [seiten[n].lines for n in sorted(gruppe.seiten) if n in seiten]
    profil = _profil_der_gruppe(gruppe, seiten)
    b = Beleg(
        seiten=list(gruppe.seiten),
        profil=profil.name,
        grund=gruppe.grund,
        lieferant=profil.anbieter or (profil.name if profil is not GENERIC else ""),
        soll_konto=profil.konto_soll,
        soll_konto_vorschlag=profil.konto_soll,
    )
    if profil is GENERIC:
        b.flag.append("kein Lieferanten-Profil gefunden: Werte bitte pruefen")

    # Belegdatum
    datum, dconf, _kw = read_date_multi(page_lines,
                                        (profil.datum_label or []) + GENERIC.datum_label,
                                        jahr_hinweis)
    b.belegdatum = datum
    if datum:
        b.vertrauen["belegdatum"] = round(dconf, 2)
    else:
        b.flag.append("Belegdatum nicht gefunden")

    # Rechnungsnummer
    nr, nconf = _rechnungsnummer(page_lines, profil)
    b.rechnungsnummer = nr
    b.belegfeld1 = nr.replace("_", "-")[:36]
    if nr:
        b.vertrauen["rechnungsnummer"] = round(nconf, 2)
    else:
        b.flag.append("Rechnungsnummer nicht gefunden")

    # Betraege
    netto, rate, ust, brutto, bconf, fl = _betraege(page_lines, profil)
    b.netto, b.ust_satz, b.ust_betrag, b.brutto = netto, rate, ust, brutto
    b.flag.extend(fl)
    if brutto is not None:
        b.vertrauen["betraege"] = round(bconf, 2)
    if rate and rate not in (profil.ustsaetze or []):
        b.flag.append(f"USt-Satz {rate} % unueblich fuer {profil.name}")
    if brutto is not None and (rate or 0) == 0:
        b.flag.append("Umsatz ohne USt ausgewiesen - Satz bitte pruefen")
    if rate and rate > 0:
        b.ust_konto = "1401"
    if not b.text:
        b.text = f"Rechnung {profil.anbieter or profil.name}"[:60]
    return b


def belege_aus_seiten(seiten: list[PageText], gruppen: list[SeitenGruppe]) -> list[Beleg]:
    by_no = {p.page_no: p for p in seiten}
    jahr = dominant_year([p.lines for p in seiten])
    return [beleg_aus_gruppe(g, by_no, jahr) for g in gruppen]
