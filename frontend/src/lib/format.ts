const nf = new Intl.NumberFormat('de-CH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function money(v: string | number | null | undefined, opts: { sign?: boolean } = {}): string {
  if (v === null || v === undefined || v === '') return ''
  const n = typeof v === 'number' ? v : Number(v)
  const s = nf.format(n)
  return opts.sign && n > 0 ? `+${s}` : s
}

export function num(v: string | number | null | undefined): number {
  if (v === null || v === undefined || v === '') return 0
  return typeof v === 'number' ? v : Number(v)
}

/** Round to cents and format as API string. */
export function cents(n: number): string {
  return (Math.round(n * 100) / 100).toFixed(2)
}

/** Parse user input like "12,50", "1'234.5", "-3" */
export function parseAmount(s: string): number | null {
  const t = s.replace(/[\s'’]/g, '').replace(',', '.')
  if (!t) return null
  const n = Number(t)
  return Number.isFinite(n) ? Math.round(n * 100) / 100 : null
}

export function fmtDate(d: string | null | undefined, locale: string): string {
  if (!d) return ''
  const [y, m, day] = d.slice(0, 10).split('-')
  return locale === 'fr' ? `${day}.${m}.${y}` : `${y}-${m}-${day}`
}

export function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function monthStart(offset = 0): string {
  const d = new Date()
  const x = new Date(d.getFullYear(), d.getMonth() + offset, 1)
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-01`
}

export function monthEnd(offset = 0): string {
  const d = new Date()
  const x = new Date(d.getFullYear(), d.getMonth() + offset + 1, 0)
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`
}

export function cls(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(' ')
}

/** Lower-case and strip accents for search: "Électronique" -> "electronique", "Œuvre" -> "oeuvre". */
export function fold(s: string | null | undefined): string {
  return (s ?? '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/œ/gi, 'oe')
    .replace(/æ/gi, 'ae')
    .toLowerCase()
}
