import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listen } from '@tauri-apps/api/event'
import {
  bucheBelege,
  downloadBelegCsv,
  getBelegCacheInfo,
  getBelegListen,
  isTauri,
  leseBelegPdf,
  leseGedroppteDatei,
  merkBelegKonto,
  setzeBelegLernenZurueck,
  type BelegBuchAntwort,
  type BelegBuchErgebnis,
  type BelegKonto,
  type BelegLeseErgebnis,
  type BelegZeile,
} from '../../api/client'
import { useMxAuto } from '../../hooks/useAnsicht'

// Ein PDF mit vielen Belegen (Sammel-Download vom Online-Händler, Kreditkarten-Belegordner …)
// wird seitenweise erkannt, in eine Liste zerlegt und nach dem Korrigieren direkt ins Journal
// gebucht oder als CSV mit Buchungssätzen ausgegeben.
// Die Texterkennung läuft auf dem eigenen Rechner; hochgeladen wird nichts.

const MAX_ZEILEN_ANZEIGE = 400   // wie beim Bank-Import: sehr lange Listen nur teilweise rendern

function dauerFmt(sekunden: number): string {
  if (sekunden < 60) return `${Math.round(sekunden)} Sekunden`
  const minuten = Math.floor(sekunden / 60)
  return `${minuten} Minuten ${Math.round(sekunden - minuten * 60)} Sekunden`
}

function geldFmt(wert: string | number): string {
  const zahl = geldZahl(wert)
  return zahl === null ? String(wert) : zahl.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** Betrag aus der Liste als Zahl – das Feld darf auch „119,00" aus dem Formular meinen. */
function geldZahl(wert: string | number): number | null {
  if (typeof wert === 'number') return Number.isFinite(wert) ? wert : null
  const text = wert.trim()
  if (!text) return null
  const zahl = Number(text.includes(',') ? text.replace(/\./g, '').replace(',', '.') : text)
  return Number.isFinite(zahl) ? zahl : null
}

/** Text der Texterkennung ist ungenau: Beträge dürfen nie stillschweigend stimmen. */
function pruefungsBadge(wert: string) {
  if (!wert || wert === 'OK') {
    return <span className="px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300 text-[11px]">geprüft</span>
  }
  return (
    <span title={wert} className="px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 text-[11px]">
      bitte prüfen
    </span>
  )
}

/** Was das Journal zu einer Zeile gesagt hat – nach dem Klick auf „Ins Journal buchen“. */
function buchungsBadge(ergebnis?: BelegBuchErgebnis) {
  if (!ergebnis) return null
  if (ergebnis.status === 'gebucht') {
    return (
      <span title={`${ergebnis.kategorie} · ${ergebnis.zahlungsart}${ergebnis.hinweis ? ` · ${ergebnis.hinweis}` : ''}`}
        className="px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 text-[11px]">
        gebucht {ergebnis.belegnr}
      </span>
    )
  }
  if (ergebnis.status === 'uebersprungen') {
    return (
      <span title={ergebnis.begruendung}
        className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700/60 text-slate-600 dark:text-slate-300 text-[11px]">
        nicht gebucht
      </span>
    )
  }
  return (
    <span title={ergebnis.begruendung}
      className="px-2 py-0.5 rounded-full bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 text-[11px]">
      fehlgeschlagen
    </span>
  )
}

/** Was das Journal zu dieser Zeile gesagt hat – im Detailbereich der Liste. */
function JournalRueckmeldung({ ergebnis }: { ergebnis: BelegBuchErgebnis }) {
  const text = ergebnis.status === 'gebucht'
    ? `gebucht als ${ergebnis.belegnr} · ${ergebnis.kategorie} · ${ergebnis.zahlungsart}`
    : `${ergebnis.status === 'fehlgeschlagen' ? 'fehlgeschlagen' : 'nicht gebucht'} – ${ergebnis.begruendung}`
  return (
    <p className="mt-3 text-xs text-slate-600 dark:text-slate-300">
      Journal: {text}
      {ergebnis.hinweis && <span className="text-amber-700 dark:text-amber-300"> · {ergebnis.hinweis}</span>}
    </p>
  )
}

function KontoAuswahl({ wert, listen, onChange }: {
  wert: string
  listen: BelegKonto[]
  onChange: (nr: string) => void
}) {
  const vorhanden = listen.some(k => k.nr === wert)
  return (
    <select
      value={vorhanden ? wert : (wert ? '__eigenes__' : '')}
      onChange={e => { if (e.target.value !== '__eigenes__') onChange(e.target.value) }}
      className="w-full text-xs border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-1 py-1 text-slate-800 dark:text-slate-100"
    >
      <option value="">–</option>
      {listen.map(k => (
        <option key={k.nr} value={k.nr}>{k.nr} · {k.text}</option>
      ))}
      {wert && !vorhanden && <option value="__eigenes__">{wert} (eigene Nummer)</option>}
    </select>
  )
}

export function BelegSammelImportPage() {
  const mxAuto = useMxAuto()
  const queryClient = useQueryClient()
  const dateiEingabe = useRef<HTMLInputElement>(null)

  const [ergebnis, setErgebnis] = useState<BelegLeseErgebnis | null>(null)
  const [zeilen, setZeilen] = useState<BelegZeile[]>([])
  const [amLesen, setAmLesen] = useState(false)
  const [belegname, setBelegname] = useState('')
  const [fehler, setFehler] = useState<string | null>(null)
  const [meldung, setMeldung] = useState<string | null>(null)
  const [nurBuchbare, setNurBuchbare] = useState(true)
  const [automatischMerken, setAutomatischMerken] = useState(true)
  const [geoeffnet, setGeoeffnet] = useState<number | null>(null)
  // Direkt ins Journal buchen: Standard-Zahlungsart, wenn der Beleg selbst keine hergibt
  const [zahlungsart, setZahlungsart] = useState('Bank')
  const [auchOhnePruefung, setAuchOhnePruefung] = useState(false)
  const [amBuchen, setAmBuchen] = useState(false)
  const [buchAntwort, setBuchAntwort] = useState<BelegBuchAntwort | null>(null)

  // Dropdown-Konten gibt es schon bevor das erste PDF gelesen wurde (Kategorien, Stammdaten)
  const { data: vorliegendeListen } = useQuery({
    queryKey: ['beleg-sammelimport-listen'],
    queryFn: getBelegListen,
  })
  const { data: cacheInfo } = useQuery({
    queryKey: ['beleg-ocr-cache'],
    queryFn: getBelegCacheInfo,
  })

  const sachkonten: BelegKonto[] = ergebnis?.sachkonten ?? vorliegendeListen?.sachkonten ?? []
  const kreditorkonten: BelegKonto[] = ergebnis?.kreditorkonten ?? vorliegendeListen?.kreditorkonten ?? []
  const gelernt = ergebnis?.gelernt ?? vorliegendeListen?.gelernt ?? []

  const angezeigt = useMemo(() => zeilen.slice(0, MAX_ZEILEN_ANZEIGE), [zeilen])
  const dubletten = zeilen.filter(z => z.duplizat_von !== null).length
  const buchbar = zeilen.filter(z => z.duplizat_von === null).length
  // Zeilennummer -> Rückmeldung des Journals, damit die Liste zeigt, was passiert ist
  const buchungen = useMemo(() => {
    const karte = new Map<number, BelegBuchErgebnis>()
    for (const e of buchAntwort?.ergebnisse ?? []) karte.set(e.index, e)
    return karte
  }, [buchAntwort])
  // Zählung der Zusammenfassung aus der aktuellen Liste: nach dem Löschen einer Zeile
  // stimmt sie wieder, ohne dass eine Zahl von Hand nachgetragen werden muss
  const buchStatistik = useMemo(() => {
    let gebucht = 0
    let uebersprungen = 0
    let fehlgeschlagen = 0
    let summe = 0
    for (const e of buchAntwort?.ergebnisse ?? []) {
      if (e.status === 'uebersprungen') uebersprungen += 1
      else if (e.status === 'fehlgeschlagen') fehlgeschlagen += 1
      else {
        gebucht += 1
        const betrag = geldZahl(zeilen[e.index]?.brutto ?? '')
        if (betrag !== null) summe += betrag
      }
    }
    return { gebucht, uebersprungen, fehlgeschlagen, summe }
  }, [buchAntwort, zeilen])

  function setzeFeld(zeile: number, feld: keyof BelegZeile, wert: unknown) {
    setZeilen(prev => prev.map((z, n) => (n === zeile ? { ...z, [feld]: wert } as BelegZeile : z)))
  }

  /** Zeile aus dieser Liste nehmen – das PDF, die erkannten Seiten und das Journal bleiben
   *  unberührt. Was darunter steht rückt nach oben, also wandern die geöffnete Detailzeile
   *  und die Rückmeldungen des Journals mit. */
  function zeileLoeschen(zeile: number) {
    const z = zeilen[zeile]
    if (!z) return
    const bereitsGebucht = buchungen.get(zeile)?.status === 'gebucht'
    const betrag = z.brutto ? `${geldFmt(z.brutto)} €` : 'ohne Betrag'
    if (!confirm(`Zeile ${zeile + 1} aus der Liste nehmen? `
      + `(${z.lieferant || 'ohne Lieferant'}, ${betrag}, Seite ${z.seiten.join(', ')}.) `
      + (bereitsGebucht
        ? 'Der Eintrag im Journal bleibt bestehen und muss dort storniert werden. '
        : '')
      + 'Im PDF bleibt der Beleg unverändert enthalten.')) return
    setZeilen(prev => prev.filter((_, n) => n !== zeile))
    setGeoeffnet(prev => (prev === null ? null : prev === zeile ? null : prev > zeile ? prev - 1 : prev))
    setBuchAntwort(prev => {
      if (!prev) return prev
      const ergebnisse = prev.ergebnisse
        .filter(e => e.index !== zeile)
        .map(e => (e.index > zeile ? { ...e, index: e.index - 1 } : e))
      return { ...prev, ergebnisse }
    })
    setMeldung(`Zeile ${zeile + 1} ist aus der Liste genommen – sie wird weder gebucht noch steht sie in der CSV.`)
  }

  async function lesePdf(datei: File) {
    setFehler(null)
    setMeldung(null)
    setAmLesen(true)
    setGeoeffnet(null)
    try {
      const r = await leseBelegPdf(datei)
      setErgebnis(r)
      setZeilen(r.zeilen)
      setBelegname(r.dateiname.replace(/\.pdf$/i, ''))
      queryClient.invalidateQueries({ queryKey: ['beleg-ocr-cache'] })
      queryClient.invalidateQueries({ queryKey: ['beleg-sammelimport-listen'] })
    } catch (e: unknown) {
      setFehler((e as Error).message)
    } finally {
      setAmLesen(false)
    }
  }

  function DateiAuswahl(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (f) void lesePdf(f)
  }

  // Desktop-App: Tauri meldet Dateien per nativem Drop (unter Windows kommt kein HTML5-Drop an)
  useEffect(() => {
    if (!isTauri()) return
    let unlisten: (() => void) | undefined
    listen<{ paths: string[] } | string[]>('tauri://drag-drop', async (event) => {
      const payload = event.payload
      const pfade = Array.isArray(payload) ? payload : (payload?.paths ?? [])
      const pdf = (Array.isArray(pfade) ? pfade : []).find((p: string) => /\.pdf$/i.test(p))
      if (!pdf) return
      try {
        const datei = await leseGedroppteDatei(pdf)
        void lesePdf(datei)
      } catch (e: unknown) {
        setFehler((e as Error).message)
      }
    }).then((fn) => { unlisten = fn })
    return () => { unlisten?.() }
  }, [])

  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f && /\.pdf$/i.test(f.name)) void lesePdf(f)
  }

  /** Was hier korrigiert wurde, gilt künftig für denselben Lieferantennamen – in allen PDFs. */
  async function kontenJeLieferantMerken() {
    const gemerkt = new Set<string>()
    for (const z of zeilen) {
      const name = z.lieferant.trim()
      if (z.duplizat_von !== null || !name || gemerkt.has(name)) continue
      gemerkt.add(name)
      await merkBelegKonto(name, z.soll_konto, z.haben_konto)
    }
    queryClient.invalidateQueries({ queryKey: ['beleg-sammelimport-listen'] })
  }

  async function csvHerunterladen() {
    if (!ergebnis) return
    setFehler(null)
    try {
      await downloadBelegCsv(zeilen, belegname || 'buchungen', nurBuchbare)
      if (automatischMerken) await kontenJeLieferantMerken()
      setMeldung(nurBuchbare
        ? `CSV ist da: ${zeilen.length - dubletten} Belege, Dubletten nicht enthalten.`
        : `CSV ist da: ${zeilen.length} Zeilen einschließlich Dubletten.`)
    } catch (e: unknown) {
      setFehler((e as Error).message)
    }
  }

  /** Direkt buchen, ohne Umweg über die CSV: genau der Weg einer von Hand erfassten Ausgabe
   *  (Kategorie aus dem Aufwandskonto, USt-Satz, Nummernkreis, GoBD-Signatur). Was die Liste
   *  nicht sauber erklären kann, lässt das Journal aus – eine halbe Buchung gibt es nicht. */
  async function insJournalBuchen() {
    if (!ergebnis) return
    if (buchbar === 0) {
      setFehler('Nur Dubletten in der Liste – da gibt es nichts zu buchen.')
      return
    }
    if (!confirm(`Wirklich ${buchbar} Beleg${buchbar === 1 ? '' : 'e'} ins Journal buchen? `
      + 'Gebuchte Einträge sind unveränderlich (GoBD) und nur per Storno zu korrigieren.')) return
    setFehler(null)
    setAmBuchen(true)
    try {
      const r = await bucheBelege(zeilen, zahlungsart, auchOhnePruefung)
      setBuchAntwort(r)
      if (automatischMerken) await kontenJeLieferantMerken()
      // Journal, Monatsübersicht und Kasse sollen die neuen Einträge sofort zeigen
      queryClient.invalidateQueries({ queryKey: ['journal'] })
      queryClient.invalidateQueries({ queryKey: ['monats-uebersicht'] })
      queryClient.invalidateQueries({ queryKey: ['kassenstand'] })
      const teile = [`${r.gebucht} von ${r.ergebnisse.length} Zeilen gebucht`,
                     `Summe ${geldFmt(r.summe)} €`]
      if (r.uebersprungen) teile.push(`${r.uebersprungen} übersprungen`)
      if (r.fehlgeschlagen) teile.push(`${r.fehlgeschlagen} fehlgeschlagen`)
      setMeldung(`Im Journal: ${teile.join(', ')}. In der Liste steht bei jeder Zeile, was passiert ist.`)
    } catch (e: unknown) {
      setFehler((e as Error).message)
    } finally {
      setAmBuchen(false)
    }
  }

  async function lernenZuruecksetzen() {
    if (!confirm('Alle gemerkten Lieferanten-Zuordnungen löschen? Die Konten selbst bleiben erhalten.')) return
    try {
      const r = await setzeBelegLernenZurueck()
      setMeldung(`${r.geloescht} gemerkte Zuordnung${r.geloescht === 1 ? '' : 'en'} gelöscht.`)
      queryClient.invalidateQueries({ queryKey: ['beleg-sammelimport-listen'] })
    } catch (e: unknown) {
      setFehler((e as Error).message)
    }
  }

  return (
    <div className={`max-w-[1400px] ${mxAuto} px-6 py-8`}>
      <h1 className="text-2xl font-bold text-slate-800 dark:text-slate-100 mb-1">Beleg-Sammelimport</h1>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">
        Ein PDF mit mehreren Rechnungen und Kaufbelegen – so wie Online-Händler und Kreditkartenanbieter
        sie schicken. Das Programm erkennt die einzelnen Belege, zeigt sie korrigierbar an und bucht sie
        auf Wunsch direkt ins Journal oder als CSV mit Buchungssätzen. Die Texterkennung läuft auf diesem
        Rechner, es wird nichts hochgeladen.
      </p>

      <div
        className="bg-white dark:bg-slate-800 border-2 border-dashed border-slate-300 dark:border-slate-600 rounded-xl p-6 mb-6 text-center"
        onDrop={handleDrop}
        onDragOver={e => e.preventDefault()}
      >
        <p className="text-sm text-slate-600 dark:text-slate-300 mb-3">
          {amLesen
            ? 'Der Belegordner wird gelesen. Das braucht einen Moment – der Text wird seitenweise erkannt.'
            : 'PDF mit Belegen hierher ziehen oder unten auswählen.'}
        </p>
        <input
          ref={dateiEingabe}
          type="file"
          accept="application/pdf,.pdf"
          onChange={DateiAuswahl}
          className="hidden"
        />
        <button
          type="button"
          onClick={() => dateiEingabe.current?.click()}
          disabled={amLesen}
          className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          PDF auswählen
        </button>
        {amLesen && (
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-3">
            Der erste Durchlauf braucht pro Seite einige Sekunden (300 dpi, Offline-Erkennung). Derselbe
            Ordner ist danach in wenigen Sekunden fertig, weil der erkannte Text gespeichert bleibt.
          </p>
        )}
        {!amLesen && cacheInfo && cacheInfo.gefundene_pdfs > 0 && (
          <p className="text-xs text-slate-400 dark:text-slate-500 mt-3">
            Schon erkannt: {cacheInfo.gefundene_pdfs} PDF-Ordner ({cacheInfo.groesse_kb} KB Zwischenspeicher).
          </p>
        )}
      </div>

      {fehler && (
        <div className="mb-6 px-4 py-3 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-300">
          {fehler}
        </div>
      )}
      {meldung && (
        <div className="mb-6 px-4 py-3 rounded-lg bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 text-sm text-green-700 dark:text-green-300">
          {meldung}
        </div>
      )}

      {ergebnis && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 mb-6">
            {[
              { label: 'Seiten', wert: String(ergebnis.seitenzahl) },
              { label: 'Belege', wert: String(ergebnis.belege) },
              { label: 'davon Dubletten', wert: String(ergebnis.dubletten) },
              { label: 'Kontenrahmen', wert: ergebnis.kontenrahmen },
              { label: 'Lesezeit', wert: dauerFmt(ergebnis.dauer_sekunden) },
            ].map(k => (
              <div key={k.label} className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-4">
                <p className="text-xs text-slate-500 dark:text-slate-400 uppercase tracking-wide">{k.label}</p>
                <p className="text-lg font-bold text-slate-800 dark:text-slate-100 mt-1">{k.wert}</p>
              </div>
            ))}
          </div>

          {ergebnis.hinweis && (
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">{ergebnis.hinweis}</p>
          )}

          <div className="flex flex-wrap items-center gap-4 mb-3">
            <label className="text-sm text-slate-600 dark:text-slate-300 flex items-center gap-2">
              <input type="checkbox" checked={nurBuchbare} onChange={e => setNurBuchbare(e.target.checked)} />
              Dubletten in der CSV weglassen
            </label>
            <label className="text-sm text-slate-600 dark:text-slate-300 flex items-center gap-2">
              <input type="checkbox" checked={automatischMerken} onChange={e => setAutomatischMerken(e.target.checked)} />
              Konten je Lieferant merken
            </label>
            <label className="text-sm text-slate-600 dark:text-slate-300 flex items-center gap-2">
              Zahlungsart
              <select
                value={zahlungsart}
                onChange={e => setZahlungsart(e.target.value)}
                className="text-xs border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-2 py-1 text-slate-800 dark:text-slate-100"
              >
                {['Bank', 'Bar', 'Karte', 'PayPal', 'Keine'].map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
            <label className="text-sm text-slate-600 dark:text-slate-300 flex items-center gap-2"
              title="Steht auf dem Beleg keine Zahlungsart, wird diese genommen. Steht dort etwas wie 'Lastschrift' oder 'Kreditkarte', entscheidet der Beleg.">
              <input type="checkbox" checked={auchOhnePruefung} onChange={e => setAuchOhnePruefung(e.target.checked)} />
              Zeilen mit Schieflage bei den Summen mitbuchen
            </label>
            <label className="text-sm text-slate-600 dark:text-slate-300 flex items-center gap-2">
              Dateiname
              <input
                value={belegname}
                onChange={e => setBelegname(e.target.value)}
                className="text-xs border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-2 py-1 text-slate-800 dark:text-slate-100"
              />
            </label>
            <button
              type="button"
              onClick={() => void insJournalBuchen()}
              disabled={amBuchen || buchbar === 0}
              title="Jeder Beleg wird ein Eintrag im Journal – wie eine von Hand erfasste Ausgabe. Unveränderlich (GoBD)."
              className="ml-auto px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {amBuchen ? 'Wird gebucht …' : `Ins Journal buchen (${buchbar})`}
            </button>
            <button
              type="button"
              onClick={() => void csvHerunterladen()}
              className="px-4 py-2 rounded-lg bg-green-600 text-white text-sm font-medium hover:bg-green-700"
            >
              CSV mit Buchungssätzen herunterladen
            </button>
          </div>

          {buchAntwort && (
            <div className="mb-4 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                {[
                  { label: 'gebucht', wert: String(buchStatistik.gebucht) },
                  { label: 'übersprungen', wert: String(buchStatistik.uebersprungen) },
                  { label: 'fehlgeschlagen', wert: String(buchStatistik.fehlgeschlagen) },
                  { label: 'Summe gebucht', wert: `${geldFmt(buchStatistik.summe)} €` },
                ].map(k => (
                  <div key={k.label}>
                    <p className="text-xs text-slate-500 dark:text-slate-400 uppercase tracking-wide">{k.label}</p>
                    <p className="text-lg font-bold text-slate-800 dark:text-slate-100 mt-1">{k.wert}</p>
                  </div>
                ))}
              </div>
              <ul className="mt-3 space-y-1">
                {buchAntwort.ergebnisse
                  .filter(e => e.status !== 'gebucht')
                  .map(e => (
                    <li key={e.index} className="text-xs text-slate-600 dark:text-slate-300">
                      <span className={e.status === 'fehlgeschlagen' ? 'text-red-600 dark:text-red-400' : 'text-slate-500 dark:text-slate-400'}>
                        Zeile {e.index + 1}
                      </span>
                      {' '}– {e.begruendung || e.status}
                      {e.belegnr && <span className="text-slate-400 dark:text-slate-500"> (Belegnr {e.belegnr})</span>}
                    </li>
                  ))}
              </ul>
              <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
                Gebuchte Belege finden sich im Journal unter Buchhaltung → Journal. Korrigieren lässt sich
                ein gebuchter Eintrag nur mit Storno und Neubuchung – deshalb vorher die Liste prüfen.
              </p>
            </div>
          )}

          <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">
            Was korrigiert werden muss, wird in den Feldern berichtigt. Zeilen, die mit
            „löschen" entfernt werden, fehlen danach in Journal und CSV – im PDF bleibt
            der Beleg unverändert enthalten.
          </p>

          <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl overflow-x-auto mb-6">
            <table className="w-full text-sm min-w-[1180px]">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-900 text-slate-500 dark:text-slate-400 text-[10px] uppercase tracking-wide">
                  <th className="text-left px-2 py-2">Seiten</th>
                  <th className="text-left px-2 py-2">Prüfung</th>
                  <th className="text-left px-2 py-2">Lieferant</th>
                  <th className="text-left px-2 py-2">Datum</th>
                  <th className="text-left px-2 py-2">Rechnungs-Nr.</th>
                  <th className="text-right px-2 py-2">Netto</th>
                  <th className="text-right px-2 py-2">USt %</th>
                  <th className="text-right px-2 py-2">USt-Betrag</th>
                  <th className="text-right px-2 py-2">Brutto</th>
                  <th className="text-left px-2 py-2">Soll (Aufwand)</th>
                  <th className="text-left px-2 py-2">Haben (Kreditoren)</th>
                  <th className="text-left px-2 py-2">Buchungstext</th>
                  <th className="px-2 py-2" />
                </tr>
              </thead>
              <tbody>
                {angezeigt.map((z, i) => (
                  <tr key={i} className={z.duplizat_von !== null
                    ? 'bg-slate-50 dark:bg-slate-900/60 text-slate-400 dark:text-slate-500'
                    : 'border-t border-slate-100 dark:border-slate-700'}>
                    <td className="px-2 py-1 text-xs whitespace-nowrap">
                      {z.seiten.join(', ')}
                      {z.duplizat_von !== null && <span className="ml-1 text-[10px]">Dublette</span>}
                    </td>
                    <td className="px-2 py-1">
                      <div className="flex flex-col items-start gap-1">
                        {pruefungsBadge(z.pruefung)}
                        {buchungsBadge(buchungen.get(i))}
                      </div>
                    </td>
                    <td className="px-2 py-1">
                      <input
                        value={z.lieferant}
                        onChange={e => setzeFeld(i, 'lieferant', e.target.value)}
                        className="w-44 text-xs border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-1 py-1 text-slate-800 dark:text-slate-100"
                      />
                    </td>
                    <td className="px-2 py-1">
                      <input
                        value={z.belegdatum}
                        onChange={e => setzeFeld(i, 'belegdatum', e.target.value)}
                        className="w-24 text-xs border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-1 py-1 text-slate-800 dark:text-slate-100"
                      />
                    </td>
                    <td className="px-2 py-1">
                      <input
                        value={z.rechnungsnummer}
                        onChange={e => setzeFeld(i, 'rechnungsnummer', e.target.value)}
                        className="w-32 text-xs border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-1 py-1 text-slate-800 dark:text-slate-100"
                      />
                    </td>
                    <td className="px-2 py-1 text-right">
                      <input
                        value={z.netto}
                        onChange={e => setzeFeld(i, 'netto', e.target.value)}
                        className="w-20 text-xs text-right border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-1 py-1 text-slate-800 dark:text-slate-100"
                      />
                    </td>
                    <td className="px-2 py-1 text-right">
                      <input
                        value={z.ust_satz === null ? '' : String(z.ust_satz)}
                        onChange={e => setzeFeld(i, 'ust_satz', e.target.value === '' ? null : Number(e.target.value))}
                        className="w-12 text-xs text-right border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-1 py-1 text-slate-800 dark:text-slate-100"
                      />
                    </td>
                    <td className="px-2 py-1 text-right">
                      <input
                        value={z.ust_betrag}
                        onChange={e => setzeFeld(i, 'ust_betrag', e.target.value)}
                        className="w-20 text-xs text-right border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-1 py-1 text-slate-800 dark:text-slate-100"
                      />
                    </td>
                    <td className="px-2 py-1 text-right font-medium">
                      <input
                        value={z.brutto}
                        onChange={e => setzeFeld(i, 'brutto', e.target.value)}
                        className="w-20 text-xs text-right border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-1 py-1 text-slate-800 dark:text-slate-100"
                      />
                    </td>
                    <td className="px-2 py-1 w-44">
                      <KontoAuswahl
                        wert={z.soll_konto}
                        listen={sachkonten}
                        onChange={nr => setzeFeld(i, 'soll_konto', nr)}
                      />
                    </td>
                    <td className="px-2 py-1 w-44">
                      <KontoAuswahl
                        wert={z.haben_konto}
                        listen={kreditorkonten}
                        onChange={nr => setzeFeld(i, 'haben_konto', nr)}
                      />
                    </td>
                    <td className="px-2 py-1">
                      <input
                        value={z.text}
                        onChange={e => setzeFeld(i, 'text', e.target.value)}
                        className="w-40 text-xs border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-900 px-1 py-1 text-slate-800 dark:text-slate-100"
                      />
                    </td>
                    <td className="px-2 py-1 whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => setGeoeffnet(geoeffnet === i ? null : i)}
                          className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                        >
                          {geoeffnet === i ? 'zu' : 'mehr'}
                        </button>
                        <button
                          type="button"
                          onClick={() => zeileLoeschen(i)}
                          title="Nur aus dieser Liste entfernen – im PDF bleibt der Beleg enthalten und nichts wird gebucht."
                          className="text-xs text-red-600 dark:text-red-400 hover:underline"
                        >
                          löschen
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {angezeigt.map((z, i) => geoeffnet === i && (
                  <tr key={`detail-${i}`} className="bg-slate-50 dark:bg-slate-900/40">
                    <td colSpan={13} className="px-4 py-3">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs text-slate-600 dark:text-slate-300">
                        {[
                          { label: 'Belegart (Erkennung)', wert: `${z.profil || '—'}${z.grund ? ` · ${z.grund}` : ''}` },
                          { label: 'Leistungsdatum', wert: z.leistungsdatum || '—' },
                          { label: 'Bestellnummer', wert: z.bestellnummer || '—' },
                          { label: 'Währung', wert: z.waehrung },
                          { label: 'USt-Konto', wert: z.ust_konto || '—' },
                          { label: 'Belegfeld 1', wert: z.belegfeld1 || '—' },
                          { label: 'Zahlungsart', wert: z.zahlungsart || '—' },
                          { label: 'Sicherheit der Erkennung', wert: `${Math.round(z.konfidenz * 100)} %` },
                        ].map(d => (
                          <div key={d.label}>
                            <p className="text-[10px] uppercase tracking-wide text-slate-400">{d.label}</p>
                            <p className="mt-0.5">{d.wert}</p>
                          </div>
                        ))}
                      </div>
                      {z.flag.length > 0 && (
                        <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
                          {z.flag.join(' · ')}
                        </p>
                      )}
                      {buchungen.get(i) && <JournalRueckmeldung ergebnis={buchungen.get(i)!} />}
                      {z.soll_konto_vorschlag && z.soll_konto !== z.soll_konto_vorschlag && (
                        <p className="mt-2 text-xs">
                          <button
                            type="button"
                            onClick={() => setzeFeld(i, 'soll_konto', z.soll_konto_vorschlag)}
                            className="text-blue-600 dark:text-blue-400 hover:underline"
                          >
                            Vorschlag {z.soll_konto_vorschlag} übernehmen
                          </button>
                        </p>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {zeilen.length > MAX_ZEILEN_ANZEIGE && (
              <p className="px-4 py-3 text-xs text-slate-500 dark:text-slate-400 border-t border-slate-100 dark:border-slate-700">
                Zeige {MAX_ZEILEN_ANZEIGE} von {zeilen.length} Belegen – die CSV enthält alle.
              </p>
            )}
          </div>
        </>
      )}

      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">
            Gemerkte Zuordnungen ({gelernt.length})
          </h2>
          {gelernt.length > 0 && (
            <button
              type="button"
              onClick={() => void lernenZuruecksetzen()}
              className="text-xs text-red-600 dark:text-red-400 hover:underline"
            >
              Lernen zurücksetzen
            </button>
          )}
        </div>
        {gelernt.length === 0 ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Noch nichts gemerkt. Einmal korrigiert und gebucht (oder als CSV heruntergeladen), verwendet
            das Programm dasselbe Konto künftig für jeden Beleg mit diesem Lieferantennamen.
          </p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 dark:text-slate-400 text-[10px] uppercase tracking-wide">
                <th className="text-left py-1">Lieferant</th>
                <th className="text-left py-1">Aufwandskonto</th>
                <th className="text-left py-1">Kreditorenkonto</th>
              </tr>
            </thead>
            <tbody>
              {gelernt.map(g => (
                <tr key={g.name} className="border-t border-slate-100 dark:border-slate-700 text-slate-600 dark:text-slate-300">
                  <td className="py-1 pr-2">{g.name}</td>
                  <td className="py-1 pr-2">{g.sach || '—'}</td>
                  <td className="py-1">{g.kreditor || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
