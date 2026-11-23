"""OCR-Text -> Feldwerte: Beschriftung finden, Wert ableiten, Zahlen/Daten deuten."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from difflib import SequenceMatcher

from .ocr import Line

# --------------------------------------------------------------- Normalisieren

_TRANSLATE = {
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss",
}


def fold(s: str) -> str:
    """Vergleichsform: Kleinbuchstaben, Umlaute aufgelöst, Sonderzeichen weg."""
    s = unicodedata.normalize("NFKC", str(s))
    for k, v in _TRANSLATE.items():
        s = s.replace(k, v)
    s = s.lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def fuzzy(a: str, b: str) -> float:
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 1.0
    if len(fa) >= 4 and (fa in fb or fb in fa):
        return 0.95
    return SequenceMatcher(None, fa, fb).ratio()


@dataclass
class LabelHit:
    line: Line
    keyword: str
    score: float
    word: str

    def rest(self) -> str:
        """Zeilentext ohne das Trefferwort (Rest steht oft direkt am Label)."""
        parts, dropped = [], False
        for w in self.line.text.split():
            if not dropped and fold(w) == fold(self.word):
                dropped = True
                continue
            parts.append(w)
        return " ".join(parts).strip(" :;,.")


def find_labels(lines: list[Line], keywords: list[str],
                min_score: float = 0.80) -> list[LabelHit]:
    """Zeilen finden, in denen eine gesuchte Beschriftung steht (unscharf)."""
    kws = [(k, fold(k)) for k in keywords]
    hits: list[LabelHit] = []
    for line in lines:
        words = line.text.split()
        if not words:
            continue
        for kw, fk in kws:
            best, bw = 0.0, ""
            if fold(line.text) == fk:
                best, bw = 1.0, words[0]
            else:
                for w in words:
                    fw = fold(w)
                    if not fw:
                        continue
                    if len(fk) >= 6 and len(fw) < 3:
                        continue
                    r = fuzzy(w, fk)
                    if r > best:
                        best, bw = r, w
            if best >= min_score:
                hits.append(LabelHit(line, kw, best, bw))
                break
    hits.sort(key=lambda h: (h.line.y0, h.line.x0))
    return hits


def _row_overlap(a: Line, b: Line) -> float:
    ov = min(a.y1, b.y1) - max(a.y0, b.y0)
    return ov / min(a.height, b.height) if ov > 0 else 0.0


def value_right(hit: LabelHit, lines: list[Line]) -> tuple[str, float]:
    """Wert rechts neben der Beschriftung, gleiche Zeile (auch weit rechts)."""
    lab = hit.line
    cands = [l for l in lines
             if l is not lab and _row_overlap(lab, l) >= 0.45 and l.x0 > lab.x1 - 2]
    if not cands:
        return "", 0.0
    cands.sort(key=lambda l: l.x0)
    picked = [cands[0]]
    for l in cands[1:]:
        if l.x0 - picked[-1].x1 < 3.0 * l.height:
            picked.append(l)
        else:
            break
    return " ".join(l.text for l in picked).strip(" :;,.") , min(l.conf for l in picked)


def value_below(hit: LabelHit, lines: list[Line], max_dy_factor: float = 3.0) -> tuple[str, float]:
    """Naechster Wert unter der Beschriftung (gleiche Spalte)."""
    lab = hit.line
    cands = []
    for l in lines:
        if l is lab:
            continue
        xov = min(l.x1, lab.x1) - max(l.x0, lab.x0)
        if xov < 0.2 * min(l.width, lab.width):
            continue
        dy = l.y0 - lab.y1
        if -0.3 * lab.height < dy < max_dy_factor * lab.height:
            cands.append((dy, l))
    if not cands:
        return "", 0.0
    cands.sort(key=lambda t: t[0])
    return cands[0][1].text.strip(" :;,"), cands[0][1].conf


def _beste_treffer(lines: list[Line], keywords: list[str], min_score: float) -> list[LabelHit]:
    """Treffer in der Reihenfolge: bester Label-Match zuerst, dann Lesereihenfolge."""
    return sorted(find_labels(lines, keywords, min_score=min_score),
                  key=lambda h: (-h.score, h.line.y0, h.line.x0))


def read_value(lines: list[Line], keywords: list[str], *, min_score: float = 0.80,
               below: bool = True) -> tuple[str, float, str]:
    """(wert, konfidenz, gefundenes schluesselwort). Zuerst rechts, dann unten."""
    for h in _beste_treffer(lines, keywords, min_score):
        for fn in ((value_right, value_below) if below else (value_right,)):
            txt, conf = fn(h, lines)
            if txt:
                return txt, conf * h.score, h.keyword
        rest = h.rest()
        if rest:
            return rest, h.score, h.keyword
    return "", 0.0, ""


def read_value_multi(pages_lines: list[list[Line]], keywords: list[str],
                     **kw) -> tuple[str, float, str]:
    """Ersten Wert in einer Liste von Seitenblöcken finden."""
    for lines in pages_lines:
        v = read_value(lines, keywords, **kw)
        if v[0]:
            return v
    return "", 0.0, ""


# --------------------------------------------------------------------- Zahlen

_OCR_NUM_FIX = str.maketrans({
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "l": "1", "I": "1", "i": "1", "|": "1", "!": "1", "[": "1",
    "S": "5", "B": "8", "Z": "2",
    ";": ",", ":": ".", "'": " ", "`": ".", "^": " ", "“": ",",
    "–": "-", "—": "-", "，": ",", "．": ".",
})

_NUM_TOKEN = re.compile(r"\d[\d .,\u00a0]*\d|\d")


def clean_number(raw: str) -> str:
    return str(raw).translate(_OCR_NUM_FIX)


def amount_variants(raw: str) -> list[Decimal]:
    """Beträge aus einem Zahl-Token; mehrdeutige Trennzeichen als Alternativen."""
    s = clean_number(raw)
    s = re.sub(r"\s+", "", s)
    if not re.fullmatch(r"[\d,.]+", s):
        return []
    out: list[Decimal] = []

    def add(t: str):
        t = t.strip(".,")
        if not t or not re.fullmatch(r"\d+(?:\.\d+)?", t):
            return
        try:
            v = Decimal(t)
        except InvalidOperation:
            return
        if v.is_finite():
            out.append(v)

    if "," in s and "." in s:
        last = max(s.rfind(","), s.rfind("."))
        head, tail = s[:last], s[last + 1:]
        add(re.sub(r"[.,]", "", head) + "." + tail)
        add(re.sub(r"[.,]", "", s))          # notfalls gar keine Dezimalstelle
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 2:
            add("".join(parts[:-1]) + "." + parts[-1])   # 1.168,35 / 125,21
        add(re.sub(r",", "", s))                          # Komma als Tausender
    elif "." in s:
        parts = s.split(".")
        if len(parts) == 2 and len(parts[1]) == 2:
            add(s)                                        # OCR: Komma als Punkt
        if all(len(p) == 3 for p in parts[1:]):
            add("".join(parts))                           # 1.168 -> 1168
        add(re.sub(r"\.", "", s))
    else:
        add(s)
    seen, uniq = set(), []
    for v in out:
        if v not in seen and v >= 0:
            seen.add(v)
            uniq.append(v)
    return uniq


def amounts_of(text: str) -> list[Decimal]:
    """Zahlen in einem Text als Betrage; Prozentwerte werden ueberlesen."""
    out: list[Decimal] = []
    t = str(text)
    for m in re.finditer(r"(?<![\dA-Za-z])\d[\d .,\u00a0]*\d(?![\dA-Za-z])", t):
        if re.match(r"\s*[%\u00a5]", t[m.end():m.end() + 3]):
            continue
        out.extend(amount_variants(m.group(0)))
    return out


def _money_like(raw: str) -> bool:
    """Wirkt die Zahl wie ein Geldbetrag (Cent-Stelle oder Tausenderpunkt)?

    Adressen, Postleitzahlen, Seriennummern und Prozentwerte fallen damit raus.
    """
    t = re.sub(r"[\s\u00a0]", "", raw)
    if re.fullmatch(r"\d{1,3}(?:[.\u00a0]\d{3})+", t):
        return True
    if re.search(r"[.,]\d{2}$", t):
        return True
    return False


def all_amounts(lines: list[Line]) -> list[tuple[Line, Decimal]]:
    """Alle Zahlen im Text als Betrag-Kandidaten (Zeile, Wert).

    Nur Werte mit Cent-Stelle oder Tausenderpunkt; Prozentzahlen, Seitenzahlen,
    Postleitzahlen und Seriennummern werden nicht als Betrag angeboten.
    """
    out = []
    for l in lines:
        t = re.sub(r"(?i)\b(eur|euro|€)\b", " ", l.text)
        if re.search(r"(?i)\b(iban|bic|ust-id|ustid|telefon|tel\.|fax|hrb|hr b)\b", l.text):
            continue
        for m in re.finditer(r"(?<![\dA-Za-z])\d[\d .,\u00a0]*\d(?![\dA-Za-z])", t):
            raw = m.group(0)
            if re.match(r"\s*[%\u00a5]", t[m.end():m.end() + 3]):
                continue
            if not _money_like(raw):
                continue
            for v in amount_variants(raw):
                if Decimal("0.01") <= v <= Decimal("200000"):
                    out.append((l, v))
    return out


def rate_of(netto: Decimal, ust: Decimal) -> int | None:
    if not netto:
        return None
    r = (ust / netto * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)
    for known in (19, 16, 7, 5, 0):
        if abs(r - known) <= 0.6:
            return known
    return int(r)


def expected_vat(netto: Decimal, rate: int) -> Decimal:
    return (netto * Decimal(rate) / Decimal(100)).quantize(Decimal("0.01"), ROUND_HALF_UP)


# ---------------------------------------------------------------------- Datum

_MONATE = {
    "januar": 1, "februar": 2, "maerz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
    "dezember": 12, "jan": 1, "feb": 2, "maerz": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "okt": 10, "nov": 11, "dez": 12,
}

_DATE_FULL = re.compile(r"\b(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(20\d{2})\b")
_DATE_MONTHNAME = re.compile(r"\b(\d{1,2})\.?\s+(januar|februar|maerz|april|mai|juni|juli|"
                             r"august|september|oktober|november|dezember|"
                             r"jan|feb|mar|apr|jun|jul|aug|sep|sept|okt|nov|dez)\.?\s*(20\d{2})?", re.I)
_DATE_SQUEEZED = re.compile(r"\b(\d{2})(\d{2})(20\d{2})\b")
_DATE_SHORT = re.compile(r"\b(\d{1,2})[./-](\d{1,2})\b")


def parse_date(raw, year_hint: int | None = None) -> str:
    if raw in (None, ""):
        return ""
    rohtext = str(raw)
    for s in (rohtext, _dat_num_fix(rohtext)):
        d = _dat_from(s, year_hint)
        if d:
            return d
    return ""


def _dat_num_fix(s: str) -> str:
    """Ziffern-Korrekturen, aber ohne die Buchstaben von Monatsnamen zu zerstoeren."""
    out, wort = [], []
    for ch in s:
        if ch.isalpha():
            wort.append(ch)
            continue
        if wort:
            out.append("".join(wort))
            wort = []
        out.append(ch.translate(_OCR_DATE_FIX))
    if wort:
        out.append("".join(wort))
    return "".join(out)


_OCR_DATE_FIX = str.maketrans({";": ",", ":": ".", "–": "-", "—": "-", "。": ".",
                               "，": ",", "．": ".", "'": " ", "`": "."})


def _dat_from(s: str, year_hint: int | None) -> str:
    s = re.sub(r"[;,]", ",", s)
    s = re.sub(r"[,]", ".", s)
    s = re.sub(r"\s+", " ", s).strip()
    for m in _DATE_FULL.finditer(s):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{d:02d}.{mo:02d}.{y}"
    m = _DATE_MONTHNAME.search(s.lower())
    if m:
        d = int(m.group(1))
        mo = _MONATE.get(fold(m.group(2)), 0)
        y = int(m.group(3)) if m.group(3) else (year_hint or 0)
        if mo and y and 1 <= d <= 31:
            return f"{d:02d}.{mo:02d}.{y}"
    m = _DATE_SQUEEZED.search(re.sub(r"\s", "", s))
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{d:02d}.{mo:02d}.{y}"
    m = _DATE_SHORT.search(s)
    if m and year_hint:
        d, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{d:02d}.{mo:02d}.{year_hint}"
    return ""


def read_date(lines: list[Line], keywords: list[str],
              year_hint: int | None = None) -> tuple[str, float, str]:
    """Datum zu einer Beschriftung lesen: im Rest der Zeile, rechts oder darunter."""
    return read_date_multi([lines], keywords, year_hint)


def read_date_multi(pages_lines: list[list[Line]], keywords: list[str],
                    year_hint: int | None = None) -> tuple[str, float, str]:
    """Datum suchen. Die Reihenfolge der gesuchten Beschriftungenzaehlt:

    "rechnungsdatum" vor "datum" - sonst gewinnt das Bestelldatum, weil es die
    bessere OCR-Rueckgabe hat.
    """
    for kw in keywords:
        for lines in pages_lines:
            for h in _beste_treffer(lines, [kw], 0.78):
                for txt, conf in ((h.rest(), h.score), *(_v for _v in
                                 (value_right(h, lines), value_below(h, lines)))):
                    d = parse_date(txt, year_hint)
                    if d:
                        return d, conf, h.keyword
    return "", 0.0, ""


def dominant_year(lines_blocks: list[list[Line]]) -> int | None:
    counts: dict[int, int] = {}
    for lines in lines_blocks:
        for l in lines:
            for m in re.finditer(r"\b(20\d{2})\b", l.text):
                counts[int(m.group(1))] = counts.get(int(m.group(1)), 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0] if counts else None
