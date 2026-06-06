// Formatting helpers — mirror the proto's Intl es-ES formatting.

const nf2 = new Intl.NumberFormat('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const nf0 = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 0 })

const MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

export function eur(s: string | number | null): string {
  if (s == null) return '—'
  return nf2.format(Number(s)) + ' €'
}

export function eurDay(s: string | number | null): string {
  if (s == null) return '—'
  return nf2.format(Number(s)) + ' €/d'
}

/** Market range like "60–105 €" (rounded), or "60 €" when lo≈hi. */
export function marketRange(lo: string | null, hi: string | null): string {
  if (lo == null || hi == null) return '—'
  const a = Number(lo)
  const b = Number(hi)
  return Math.round(a) === Math.round(b)
    ? `${nf0.format(a)} €`
    : `${nf0.format(a)}–${nf0.format(b)} €`
}

/** "02 Jun 2026" from an ISO date string. */
export function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const day = String(d.getUTCDate()).padStart(2, '0')
  return `${day} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

/** "hace 2 h" / "hace 30 min" / "hace 3 d" from an ISO timestamp. */
export function fmtAgo(iso: string | null): string {
  if (!iso) return 'sin datos'
  const then = new Date(iso).getTime()
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000))
  if (mins < 60) return `hace ${mins} min`
  const hrs = Math.round(mins / 60)
  if (hrs < 48) return `hace ${hrs} h`
  return `hace ${Math.round(hrs / 24)} d`
}

export const BASE_LABELS: Record<string, string> = {
  min: 'el más barato', med: 'la mediana', avg: 'la media', max: 'el más caro',
}
export const BASE_LABELS_NOUN: Record<string, string> = {
  min: 'mínimo', med: 'mediana', avg: 'media', max: 'máximo',
}
