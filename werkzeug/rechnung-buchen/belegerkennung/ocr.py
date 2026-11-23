"""PDF -> OCR-Text mit Koordinaten.

Jede Seite wird mit PyMuPDF gerastert (Default 300 dpi) und mit RapidOCR
(PP-OCR, laeuft vollstaendig offline ueber onnxruntime) erkannt. Die
Ergebnisse werden pro PDF in einem JSON-Cache abgelegt, damit Anzeigeregeln
schnell neu ausgewertet werden koennen, ohne erneut zu OCRn.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import pymupdf as fitz

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


@dataclass
class Line:
    """Eine OCR-Zeile mit Begrenzungsrechteck in Punkt der Seite."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 1.0)

    @property
    def width(self) -> float:
        return max(self.x1 - self.x0, 1.0)


@dataclass
class PageText:
    page_no: int  # 1-basiert
    width: float
    height: float
    lines: list[Line]
    mean_conf: float

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)

    def raw_lines(self) -> list[str]:
        return [l.text for l in self.lines]

    def to_dict(self) -> dict:
        return {"page_no": self.page_no, "width": self.width, "height": self.height,
                "mean_conf": self.mean_conf, "lines": [asdict(l) for l in self.lines]}

    @classmethod
    def from_dict(cls, d: dict) -> "PageText":
        return cls(d["page_no"], d["width"], d["height"],
                   [Line(**l) for l in d["lines"]], d["mean_conf"])


# ---------------------------------------------------------------- Ocr-Zeile

def _row_key(l: Line) -> float:
    return l.cy


def group_rows(lines: list[Line], tol_factor: float = 0.55) -> list[list[Line]]:
    """OCR-Fragmente zu Zeilen zusammenfassen (gleiche y-Hoehe)."""
    if not lines:
        return []
    ordered = sorted(lines, key=_row_key)
    rows: list[list[Line]] = [[ordered[0]]]
    for l in ordered[1:]:
        row = rows[-1]
        h = sum(i.height for i in row) / len(row)
        cy = sum(i.cy for i in row) / len(row)
        if abs(l.cy - cy) <= tol_factor * h:
            row.append(l)
        else:
            rows.append([l])
    for row in rows:
        row.sort(key=lambda l: l.x0)
    rows.sort(key=lambda r: min(l.y0 for l in r))
    return rows


def reading_order(lines: list[Line]) -> list[Line]:
    out: list[Line] = []
    for row in group_rows(lines):
        out.extend(row)
    return out


def merge_row_gaps(lines: list[Line], gap_factor: float = 1.2) -> list[Line]:
    """Kleine Luecken innerhalb einer OCR-Zeile schliessen (zerrissene Woerter)."""
    out: list[Line] = []
    for row in group_rows(lines):
        cur = row[0]
        for l in row[1:]:
            gap = l.x0 - cur.x1
            if gap < gap_factor * min(cur.height, l.height):
                cur = Line(cur.text + " " + l.text, cur.x0, min(cur.y0, l.y0),
                           max(cur.x1, l.x1), max(cur.y1, l.y1),
                           min(cur.conf, l.conf))
            else:
                out.append(cur)
                cur = l
        out.append(cur)
    return out


# ---------------------------------------------------------------- OCR

_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def _detect_page(page: fitz.Page, dpi: int) -> list[Line]:
    pm = page.get_pixmap(dpi=dpi)
    import numpy as np
    import cv2  # cv2 wird von rapidocr mitgebracht (opencv-python)
    img = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, pm.n)
    if pm.n == 4:
        img = img[:, :, :3]
    res, _ = _engine()(img)
    lines: list[Line] = []
    for box, text, conf in res:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        scale = 72.0 / dpi  # Pixel -> Punkt
        lines.append(Line(str(text).strip(), min(xs) * scale, min(ys) * scale,
                          max(xs) * scale, max(ys) * scale, float(conf)))
    return reading_order(lines)


def _cache_key(path: Path, dpi: int) -> str:
    """Schluessel aus dem Inhalt der PDF und der Aufloesung.

    Der Inhalt zaehlt, nicht der Pfad: der Ordner (und damit der Cache) kann
    verschoben, umbenannt oder auf einen USB-Stab kopiert werden, ohne dass ein
    bereits gelesenes PDF noch einmal erkannt werden muss.
    """
    hasher = hashlib.sha1()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            hasher.update(block)
    hasher.update(f"|{dpi}".encode("utf-8"))
    return hasher.hexdigest()[:16]


def ocr_pdf(pdf_path: str | Path, dpi: int = 300, use_cache: bool = True,
            progress=None) -> list[PageText]:
    """PDF einlesen und als OCR-Seiten zurueckgeben (progress(page, total))."""
    pdf_path = Path(pdf_path)
    cache = CACHE_DIR / f"ocr_{_cache_key(pdf_path, dpi)}.json"
    if use_cache and cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        pages = [PageText(p["page_no"], p["width"], p["height"],
                          [Line(**l) for l in p["lines"]], p["mean_conf"])
                 for p in data["pages"]]
        if progress:
            progress(len(pages), len(pages))
        return pages

    doc = fitz.open(pdf_path)
    pages: list[PageText] = []
    t0 = time.time()
    for i, page in enumerate(doc):
        lines = _detect_page(page, dpi)
        conf = sum(l.conf for l in lines) / len(lines) if lines else 0.0
        pages.append(PageText(i + 1, float(page.rect.width), float(page.rect.height),
                              lines, conf))
        if progress:
            progress(i + 1, doc.page_count)
        print(f"  OCR Seite {i + 1}/{doc.page_count}  "
              f"({time.time() - t0:.0f}s, {len(lines)} Zeilen, "
              f"Konfidenz {conf:.2f})", flush=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(
        {"pdf": str(pdf_path), "dpi": dpi,
         "pages": [{"page_no": p.page_no, "width": p.width, "height": p.height,
                    "mean_conf": p.mean_conf,
                    "lines": [asdict(l) for l in p.lines]} for p in pages]},
        ensure_ascii=False), encoding="utf-8")
    return pages
