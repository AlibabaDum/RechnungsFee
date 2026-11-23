"""Startpunkt: Bibliotheken des Projektordners einbinden und starten.

Ohne Argumente oeffnet sich die Bildschirmliste. Mit Argumenten:
    python buchen.py rechnungen.pdf --csv buchungen.csv

Konsole braucht man dafuer nicht: "Rechnung buchen.vbs" (doppeltes Klicken oder
PDF darauf ziehen) startet dieses Programm mit der mitgebrachten pythonw.exe,
also ganz ohne schwarzes Fenster. Falls dabei etwas schiefgeht, erscheint eine
Meldung auf dem Bildschirm und die Einzelheiten stehen in "letzter Fehler.txt"
neben diesem Programm.
"""
import sys
import traceback
from pathlib import Path

WURZEL = Path(__file__).resolve().parent
sys.path.insert(0, str(WURZEL))
libs = WURZEL / "libs"
if libs.is_dir():
    sys.path.insert(0, str(libs))

LOG = WURZEL / "letzter Fehler.txt"
# Vor dem Umhaengen pruefen: ohne Konsole (pythonw.exe) gibt es kein Ausgabefenster.
OHNE_KONSOLE = sys.stdout is None or sys.stderr is None


def stumm_schalten() -> None:
    """Ausgaben ins Leere lenken, damit nichts an der fehlenden Konsole scheitert."""
    import os
    for strom in ("stdout", "stderr"):
        if getattr(sys, strom, None) is None:
            setattr(sys, strom, open(os.devnull, "w", encoding="utf-8"))


def melden(grund: str, einzelnheiten: str = "") -> None:
    """Problem melden, ohne dass ein Konsolenfenster vorhanden sein muss."""
    if not OHNE_KONSOLE:
        return
    try:
        LOG.write_text(f"{grund}\n\n{einzelnheiten}", encoding="utf-8", newline="")
    except OSError:
        pass
    try:
        import ctypes
        text = grund if not einzelnheiten else grund + "\n\n" + einzelnheiten[-1500:]
        ctypes.windll.user32.MessageBoxW(
            None, f"{text}\n\nEinzelheiten: {LOG}", "Rechnungen buchen", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    stumm_schalten()
    code = 0
    try:
        from belegerkennung.cli import main
        code = main()
    except SystemExit:
        raise
    except Exception:
        melden("Etwas ist beim Lesen oder Speichern schiefgelaufen.",
               traceback.format_exc())
        code = 1
    raise SystemExit(code)
