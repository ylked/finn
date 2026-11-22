import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowRightLeft, Bot, CheckCircle2, Link2, Lock, Paperclip, Plus, Scale, Sparkles, Trash2, Unlink, UserPlus, Wand2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { cents, cls, fmtDate, money, num, parseAmount } from '../lib/format'
import { accountLabel, useAccounts, useRefreshLedger } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { Account, BankEntry, Transaction } from '../lib/types'
import { AccountPicker } from './AccountPicker'
import { Badge, Field, Money, Segmented, Sheet, useToast } from './ui'

const PAYMENT = ['bank', 'credit_card', 'cash']

export function txAmount(tx: Transaction): number {
  const pay = tx.lines.find((l) => l.bank_entry_id) ?? tx.lines.find((l) => PAYMENT.includes(l.account_kind ?? ''))
  if (pay) return num(pay.amount)
  return tx.lines.filter((l) => num(l.amount) > 0).reduce((s, l) => s + num(l.amount), 0)
}

export function counterLabel(tx: Transaction): string {
  const pay = tx.lines.find((l) => l.bank_entry_id) ?? tx.lines.find((l) => PAYMENT.includes(l.account_kind ?? ''))
  const others = tx.lines.filter((l) => l !== pay)
  if (others.length === 1) return `${others[0].account_code} ${others[0].account_name}`
  return others.map((l) => l.account_code).join(' · ')
}

export function StateBadge({ tx }: { tx: Transaction }) {
  const { t } = useI18n()
  if (tx.ai_status === 'pending') return <Badge color="violet"><Bot className="h-3 w-3" /> IA</Badge>
  if (tx.state === 'auto') return <Badge color="amber"><Sparkles className="h-3 w-3" /> {t('state_auto')}</Badge>
  if (tx.state === 'unclassified') return <Badge color="red">{t('state_unclassified')}</Badge>
  return null
}

export function TxRow({ tx, onClick, selected, onSelect }: { tx: Transaction; onClick?: () => void; selected?: boolean; onSelect?: (v: boolean) => void }) {
  const { locale } = useI18n()
  const amt = txAmount(tx)
  return (
    <div className="flex items-center gap-3 px-3 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/50 md:px-4">
      {onSelect && <input type="checkbox" className="h-4 w-4 accent-brand-700" checked={!!selected} onChange={(e) => onSelect(e.target.checked)} />}
      <button className="flex min-w-0 flex-1 items-center gap-3 text-left" onClick={onClick}>
        <div className="w-12 shrink-0 text-xs leading-tight text-slate-500 tnum">{fmtDate(tx.date, locale).slice(0, 5)}</div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{tx.counterparty || tx.description}</div>
          <div className="flex items-center gap-1.5 truncate text-xs text-slate-500">
            <StateBadge tx={tx} />
            {tx.receipts.length > 0 && <Paperclip className="h-3 w-3 shrink-0" />}
            {!tx.reconciled && tx.source === 'manual' && tx.lines.some((l) => PAYMENT.includes(l.account_kind ?? '')) && <Link2 className="h-3 w-3 shrink-0 text-amber-500" />}
            <span className="truncate">{counterLabel(tx)}</span>
          </div>
        </div>
        <Money value={amt} sign colored className="text-sm font-medium" />
      </button>
    </div>
  )
}

// ------------------------------------------------------------------ editor
type EditLine = { key: string; account_id: number | null; debit: string; credit: string; memo: string; bank_entry_id: number | null; orig_amount?: string | null; orig_currency?: string | null }

let keySeq = 0
const k = () => `l${++keySeq}`

function toEdit(tx: Transaction): EditLine[] {
  return tx.lines.map((l) => ({
    key: k(),
    account_id: l.account_id,
    debit: num(l.amount) > 0 ? cents(num(l.amount)) : '',
    credit: num(l.amount) < 0 ? cents(-num(l.amount)) : '',
    memo: l.memo ?? '',
    bank_entry_id: l.bank_entry_id ?? null,
    orig_amount: l.orig_amount,
    orig_currency: l.orig_currency,
  }))
}

function lineAmount(l: EditLine): number {
  return (parseAmount(l.debit) ?? 0) - (parseAmount(l.credit) ?? 0)
}

export function TxEditor({ tx, onDone, compact = false }: { tx: Transaction; onDone?: (tx: Transaction | null) => void; compact?: boolean }) {
  const { t, locale } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const { byId } = useAccounts()
  const [date, setDate] = useState(tx.date)
  const [description, setDescription] = useState(tx.description)
  const [counterparty, setCounterparty] = useState(tx.counterparty ?? '')
  const [notes, setNotes] = useState(tx.notes ?? '')
  const [lines, setLines] = useState<EditLine[]>(() => toEdit(tx))
  const [sheet, setSheet] = useState<null | 'person' | 'rule' | 'match'>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setDate(tx.date)
    setDescription(tx.description)
    setCounterparty(tx.counterparty ?? '')
    setNotes(tx.notes ?? '')
    setLines(toEdit(tx))
  }, [tx.id, tx.updated_at]) // eslint-disable-line react-hooks/exhaustive-deps

  const diff = useMemo(() => Math.round(lines.reduce((s, l) => s + lineAmount(l), 0) * 100) / 100, [lines])
  const anySuspense = lines.some((l) => l.account_id && byId.get(l.account_id)?.kind === 'suspense')
  const valid = diff === 0 && lines.length >= 2 && lines.every((l) => l.account_id && lineAmount(l) !== 0)
  const locked = tx.source === 'opening'
  const payLine = lines.find((l) => l.bank_entry_id) ?? lines.find((l) => l.account_id && PAYMENT.includes(byId.get(l.account_id)?.kind ?? ''))

  const upd = (key: string, patch: Partial<EditLine>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)))
  const setAmount = (key: string, side: 'debit' | 'credit', v: string) => upd(key, side === 'debit' ? { debit: v, credit: '' } : { credit: v, debit: '' })
  const addLine = () =>
    setLines((ls) => [...ls, { key: k(), account_id: null, debit: diff < 0 ? cents(-diff) : '', credit: diff > 0 ? cents(diff) : '', memo: '', bank_entry_id: null }])
  const absorb = (key: string) =>
    setLines((ls) =>
      ls.map((l) => {
        if (l.key !== key) return l
        const n = lineAmount(l) - diff
        return { ...l, debit: n > 0 ? cents(n) : '', credit: n < 0 ? cents(-n) : '' }
      }),
    )

  const save = useMutation({
    mutationFn: (confirm: boolean) =>
      api.put<Transaction>(`/api/transactions/${tx.id}`, {
        date,
        description,
        counterparty: counterparty || null,
        notes: notes || null,
        confirm,
        lines: lines.map((l) => ({ account_id: l.account_id, amount: cents(lineAmount(l)), memo: l.memo || null, bank_entry_id: l.bank_entry_id, orig_amount: l.orig_amount ?? null, orig_currency: l.orig_currency ?? null })),
      }),
    onSuccess: (r) => {
      toast(t('saved'))
      refresh()
      onDone?.(r)
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })

  const action = useMutation({
    mutationFn: async (a: 'reclassify' | 'delete' | { unlink: number } | { link: [number, number] }) => {
      if (a === 'reclassify') return api.post<Transaction>(`/api/transactions/${tx.id}/reclassify`)
      if (a === 'delete') return api.del(`/api/transactions/${tx.id}`).then(() => null)
      if ('unlink' in a) return api.post<Transaction>(`/api/transactions/${tx.id}/lines/${a.unlink}/unlink`)
      return api.post<Transaction>(`/api/bank-entries/${a.link[0]}/link/${a.link[1]}`)
    },
    onSuccess: (r) => {
      refresh()
      setSheet(null)
      onDone?.(r as Transaction | null)
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })

  const upload = useMutation({
    mutationFn: (files: FileList) => {
      const fd = new FormData()
      Array.from(files).forEach((f) => fd.append('files', f))
      fd.append('transaction_id', String(tx.id))
      return api.post('/api/receipts', fd)
    },
    onSuccess: () => {
      refresh()
      onDone?.(tx)
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })

  const bankEntries = tx.bank_entries ?? []

  return (
    <div className="space-y-4">
      {/* header info */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge>{t(`source_${tx.source}`)}</Badge>
        <StateBadge tx={tx} />
        {tx.state === 'confirmed' && <Badge color="green"><CheckCircle2 className="h-3 w-3" /> {t('state_confirmed')}</Badge>}
        {tx.lines.some((l) => PAYMENT.includes(l.account_kind ?? '')) && (tx.reconciled ? <Badge color="blue"><Link2 className="h-3 w-3" /> {t('reconciled')}</Badge> : <Badge color="amber">{t('not_reconciled')}</Badge>)}
        <span className="text-slate-400">#{tx.id}</span>
      </div>

      {tx.ai_reason && (
        <div className="flex gap-2 rounded-xl bg-violet-50 p-3 text-sm text-violet-900 dark:bg-violet-950/50 dark:text-violet-200">
          <Bot className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            {tx.ai_reason}
            {tx.ai_confidence != null && <span className="ml-2 text-xs opacity-70">({t('confidence')} {Math.round(tx.ai_confidence * 100)}%)</span>}
          </div>
        </div>
      )}
      {tx.ai_status === 'pending' && <div className="text-sm text-violet-600">{t('ai_pending')}</div>}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Field label={t('date')}>
          <input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} disabled={locked} />
        </Field>
        <Field label={t('counterparty')} className="md:col-span-1">
          <input className="input" value={counterparty} onChange={(e) => setCounterparty(e.target.value)} disabled={locked} />
        </Field>
        <Field label={t('description')} className="col-span-2">
          <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} disabled={locked} />
        </Field>
      </div>

      {/* lines */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <div className="label mb-0">{t('lines')}</div>
          <div className={cls('text-xs font-medium', diff === 0 ? 'text-emerald-600' : 'text-red-600')}>
            {diff === 0 ? `✓ ${t('balanced')}` : t('unbalanced_by', { v: money(diff) })}
          </div>
        </div>
        <div className="space-y-2">
          <div className="hidden grid-cols-[1fr_7rem_7rem_2rem] gap-2 px-1 text-xs text-slate-500 md:grid">
            <span>{t('account')}</span>
            <span className="text-right">{t('debit')}</span>
            <span className="text-right">{t('credit')}</span>
            <span />
          </div>
          {lines.map((l) => {
            const isLocked = !!l.bank_entry_id || locked
            return (
              <div key={l.key} className={cls('rounded-xl p-2 md:p-1', isLocked ? 'bg-slate-50 dark:bg-slate-800/50' : 'ring-1 ring-slate-200 dark:ring-slate-800 md:ring-0')}>
                <div className="grid grid-cols-2 gap-2 md:grid-cols-[1fr_7rem_7rem_2rem] md:items-center">
                  <div className="col-span-2 md:col-span-1">
                    {isLocked ? (
                      <div className="flex items-center gap-2 px-2 py-2 text-sm">
                        <Lock className="h-3.5 w-3.5 text-slate-400" />
                        <span className="truncate">{accountLabel(l.account_id ? byId.get(l.account_id) : undefined)}</span>
                      </div>
                    ) : (
                      <AccountPicker value={l.account_id} onChange={(id) => upd(l.key, { account_id: id })} />
                    )}
                  </div>
                  <input inputMode="decimal" className="input text-right tnum" placeholder={t('debit')} value={l.debit} disabled={isLocked} onChange={(e) => setAmount(l.key, 'debit', e.target.value)} />
                  <input inputMode="decimal" className="input text-right tnum" placeholder={t('credit')} value={l.credit} disabled={isLocked} onChange={(e) => setAmount(l.key, 'credit', e.target.value)} />
                  <div className="col-span-2 flex justify-end gap-1 md:col-span-1">
                    {!isLocked && diff !== 0 && (
                      <button type="button" title="=" className="btn-ghost p-1.5" onClick={() => absorb(l.key)}>
                        <Scale className="h-4 w-4" />
                      </button>
                    )}
                    {!isLocked && lines.length > 2 && (
                      <button type="button" className="btn-ghost p-1.5 text-red-500" onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}>
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                </div>
                {!compact && !isLocked && (
                  <input className="input mt-2 py-1.5 text-sm md:mt-1" placeholder={t('memo')} value={l.memo} onChange={(e) => upd(l.key, { memo: e.target.value })} />
                )}
                {isLocked && l.orig_amount && (
                  <div className="px-2 pt-1 text-xs text-slate-500">
                    {money(l.orig_amount)} {l.orig_currency}
                  </div>
                )}
              </div>
            )
          })}
        </div>
        {!locked && (
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" className="btn-secondary" onClick={addLine}>
              <Plus className="h-4 w-4" /> {t('split')}
            </button>
            {payLine && (
              <button type="button" className="btn-secondary" onClick={() => setSheet('person')}>
                <UserPlus className="h-4 w-4" /> {t('advance_for')}
              </button>
            )}
            {payLine && (
              <button type="button" className="btn-secondary" onClick={() => setSheet('rule')}>
                <Wand2 className="h-4 w-4" /> {t('make_rule')}
              </button>
            )}
          </div>
        )}
      </div>

      {!compact && (
        <Field label={t('notes')}>
          <textarea className="input min-h-[4rem]" value={notes} onChange={(e) => setNotes(e.target.value)} disabled={locked} />
        </Field>
      )}

      {/* save */}
      {!locked && (
        <div className="sticky -bottom-4 z-10 -mx-4 flex flex-wrap gap-2 border-t border-slate-100 bg-white px-4 pb-4 pt-3 dark:border-slate-800 dark:bg-slate-900/95 md:static md:mx-0 md:border-0 md:bg-transparent md:p-0">
          <button className="btn-primary flex-1 md:flex-none" disabled={!valid || anySuspense || save.isPending} onClick={() => save.mutate(true)}>
            <CheckCircle2 className="h-4 w-4" /> {t('save_confirm')}
          </button>
          <button className="btn-secondary" disabled={!valid || save.isPending} onClick={() => save.mutate(false)}>
            {t('save_draft')}
          </button>
        </div>
      )}

      {/* statement details */}
      {!compact && bankEntries.length > 0 && (
        <div className="rounded-xl bg-slate-50 p-3 text-sm dark:bg-slate-800/50">
          <div className="label">{t('bank_details')}</div>
          {bankEntries.map((be) => (
            <div key={be.id} className="space-y-0.5 text-xs text-slate-600 dark:text-slate-300">
              <div className="flex justify-between gap-2">
                <span>{fmtDate(be.booking_date, locale)}{be.value_date && be.value_date !== be.booking_date && ` (val. ${fmtDate(be.value_date, locale)})`}</span>
                <Money value={be.amount} sign colored />
              </div>
              {be.counterparty && <div className="font-medium">{be.counterparty}{be.counterparty_iban && ` · ${be.counterparty_iban}`}</div>}
              <div className="break-words">{be.description}</div>
              {be.remittance_info && <div className="break-words">{be.remittance_info}</div>}
              <div className="text-slate-400">{be.tx_code} · {be.bank_ref}</div>
              {tx.source !== 'bank' && tx.source !== 'csv' && (
                <button className="btn-ghost mt-1 px-2 py-1 text-xs" title={t('unlink_help')} onClick={() => { const ln = tx.lines.find((l) => l.bank_entry_id === be.id); if (ln?.id && confirm(t('unlink_help'))) action.mutate({ unlink: ln.id }) }}>
                  <Unlink className="h-3.5 w-3.5" /> {t('unlink')}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* receipts */}
      {!compact && (
        <div className="flex flex-wrap items-center gap-2">
          {tx.receipts.map((r) => (
            <a key={r.id} href={`/api/receipts/${r.id}/file`} target="_blank" rel="noreferrer" className="btn-secondary text-xs">
              <Paperclip className="h-3.5 w-3.5" /> {r.filename}
            </a>
          ))}
          <input ref={fileRef} type="file" accept="image/*,application/pdf" multiple hidden onChange={(e) => e.target.files?.length && upload.mutate(e.target.files)} />
          <button className="btn-ghost text-xs" onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
            <Paperclip className="h-3.5 w-3.5" /> {t('attach_receipt')}
          </button>
          {!tx.reconciled && tx.source === 'manual' && payLine && (
            <button className="btn-ghost text-xs" onClick={() => setSheet('match')}>
              <Link2 className="h-3.5 w-3.5" /> {t('find_statement')}
            </button>
          )}
        </div>
      )}

      {/* secondary actions */}
      {!compact && !locked && (
        <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
          {payLine && (tx.source === 'bank' || tx.source === 'csv') && (
            <button className="btn-ghost text-sm" onClick={() => action.mutate('reclassify')} disabled={action.isPending}>
              <Bot className="h-4 w-4" /> {t('reclassify_ai')}
            </button>
          )}
          {!tx.lines.some((l) => l.bank_entry_id) && (
            <button className="btn-danger text-sm" onClick={() => confirm(t('delete_tx_confirm')) && action.mutate('delete')}>
              <Trash2 className="h-4 w-4" /> {t('delete')}
            </button>
          )}
        </div>
      )}

      <PersonSheet
        open={sheet === 'person'}
        onClose={() => setSheet(null)}
        lines={lines}
        payKey={payLine?.key}
        onApply={(ls) => {
          setLines(ls)
          setSheet(null)
        }}
      />
      {sheet === 'rule' && <RuleSheet tx={tx} lines={lines} payKey={payLine?.key} onClose={() => setSheet(null)} />}
      {sheet === 'match' && <MatchSheet tx={tx} onClose={() => setSheet(null)} onLink={(be, line) => action.mutate({ link: [be, line] })} />}
    </div>
  )
}

function PersonSheet({ open, onClose, lines, payKey, onApply }: { open: boolean; onClose: () => void; lines: EditLine[]; payKey?: string; onApply: (ls: EditLine[]) => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const pay = lines.find((l) => l.key === payKey)
  const total = pay ? -lineAmount(pay) : 0 // >0 = I paid
  const [person, setPerson] = useState<number | null>(null)
  const [newName, setNewName] = useState('')
  const [share, setShare] = useState('')
  useEffect(() => {
    if (open) setShare(cents(Math.abs(total)))
  }, [open, total])

  const createPerson = useMutation({
    mutationFn: () => api.post<Account>('/api/accounts/persons', { name: newName, owed_to_me: total > 0 }),
    onSuccess: (a) => {
      refresh()
      setPerson(a.id)
      setNewName('')
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })

  const apply = () => {
    const s = parseAmount(share)
    if (!person || !s) return
    const signed = total > 0 ? s : -s // debit receivable when I paid; credit when I received
    const others = lines.filter((l) => l.key !== payKey && !l.bank_entry_id)
    let remaining = s
    const out: EditLine[] = []
    for (const l of lines) {
      if (!others.includes(l) || remaining <= 0) {
        out.push(l)
        continue
      }
      const a = lineAmount(l)
      const cut = Math.min(Math.abs(a), remaining)
      remaining = Math.round((remaining - cut) * 100) / 100
      const left = Math.sign(a) * (Math.abs(a) - cut)
      if (Math.abs(left) >= 0.005) out.push({ ...l, debit: left > 0 ? cents(left) : '', credit: left < 0 ? cents(-left) : '' })
    }
    out.push({ key: k(), account_id: person, debit: signed > 0 ? cents(signed) : '', credit: signed < 0 ? cents(-signed) : '', memo: '', bank_entry_id: null })
    onApply(out)
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('advance_for')}>
      <p className="mb-3 text-sm text-slate-500">{t('advance_help')}</p>
      <div className="space-y-3">
        <Field label={t('person')}>
          <AccountPicker value={person} onChange={(id) => setPerson(id)} kinds={['receivable']} />
        </Field>
        <div className="flex gap-2">
          <input className="input" placeholder={t('new_person')} value={newName} onChange={(e) => setNewName(e.target.value)} />
          <button className="btn-secondary" disabled={!newName.trim() || createPerson.isPending} onClick={() => createPerson.mutate()}>
            <UserPlus className="h-4 w-4" />
          </button>
        </div>
        <Field label={t('share_for_person')}>
          <input inputMode="decimal" className="input text-right" value={share} onChange={(e) => setShare(e.target.value)} />
        </Field>
        <button className="btn-primary w-full" disabled={!person || !parseAmount(share)} onClick={apply}>
          {t('apply')}
        </button>
      </div>
    </Sheet>
  )
}

function RuleSheet({ tx, lines, payKey, onClose }: { tx: Transaction; lines: EditLine[]; payKey?: string; onClose: () => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const pay = lines.find((l) => l.key === payKey)
  const others = lines.filter((l) => l.key !== payKey)
  const [pattern, setPattern] = useState((tx.counterparty || tx.description).slice(0, 40))
  const [target, setTarget] = useState<number | null>(others.length === 1 ? others[0].account_id : null)
  const [direction, setDirection] = useState<'any' | 'in' | 'out'>(pay && lineAmount(pay) > 0 ? 'in' : 'out')
  const [confirmDirect, setConfirmDirect] = useState(false)
  const m = useMutation({
    mutationFn: () => api.post(`/api/transactions/${tx.id}/rule`, { pattern, target_account_id: target, direction, confirm: confirmDirect }),
    onSuccess: () => {
      toast(t('rule_created'))
      onClose()
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  return (
    <Sheet open onClose={onClose} title={t('make_rule')}>
      <div className="space-y-3">
        <Field label={t('rule_pattern')}>
          <input className="input" value={pattern} onChange={(e) => setPattern(e.target.value)} />
        </Field>
        <Field label={t('account')}>
          <AccountPicker value={target} onChange={(id) => setTarget(id)} />
        </Field>
        <Field label={t('direction')}>
          <select className="input" value={direction} onChange={(e) => setDirection(e.target.value as 'any')}>
            <option value="any">{t('dir_any')}</option>
            <option value="in">{t('dir_in')}</option>
            <option value="out">{t('dir_out')}</option>
          </select>
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-brand-700" checked={confirmDirect} onChange={(e) => setConfirmDirect(e.target.checked)} /> {t('auto_confirm')}
        </label>
        <button className="btn-primary w-full" disabled={!target || pattern.trim().length < 2 || m.isPending} onClick={() => m.mutate()}>
          {t('create')}
        </button>
      </div>
    </Sheet>
  )
}

function MatchSheet({ tx, onClose, onLink }: { tx: Transaction; onClose: () => void; onLink: (beId: number, lineId: number) => void }) {
  const { t, locale } = useI18n()
  const q = useQuery({
    queryKey: ['match', tx.id],
    queryFn: () => api.get<{ line_id: number; entries: BankEntry[] }[]>(`/api/transactions/${tx.id}/match-candidates`),
  })
  return (
    <Sheet open onClose={onClose} title={t('candidates')}>
      {q.data?.every((x) => !x.entries.length) && <p className="text-sm text-slate-500">{t('no_candidates')}</p>}
      {q.data?.map((x) =>
        x.entries.map((e) => (
          <div key={e.id} className="flex items-center gap-3 border-b border-slate-100 py-2 text-sm dark:border-slate-800">
            <div className="min-w-0 flex-1">
              <div className="font-medium">{e.counterparty || e.description}</div>
              <div className="text-xs text-slate-500">{fmtDate(e.booking_date, locale)} · {e.description}</div>
            </div>
            <Money value={e.amount} sign colored />
            <button className="btn-primary px-2.5 py-1 text-xs" onClick={() => onLink(e.id, x.line_id)}>
              {t('link')}
            </button>
          </div>
        )),
      )}
    </Sheet>
  )
}

export function TxSheet({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ['transaction', id], queryFn: () => api.get<Transaction>(`/api/transactions/${id}`), enabled: !!id })
  return (
    <Sheet open={!!id} onClose={onClose} title={q.data ? q.data.counterparty || q.data.description : t('tx_detail')} wide>
      {q.data ? <TxEditor tx={q.data} onDone={(r) => (r === null || r?.state === 'confirmed' ? onClose() : q.refetch())} /> : <div className="py-10 text-center text-slate-400">{t('loading')}</div>}
      {q.data && (
        <div className="mt-3 text-right">
          <Link to={`/transactions/${q.data.id}`} className="text-xs text-slate-400 underline">#{q.data.id}</Link>
        </div>
      )}
    </Sheet>
  )
}

// ------------------------------------------------------------------ multi-selection
/** Selection state for lists of entries. */
export function useSelection() {
  const [active, setActive] = useState(false)
  const [ids, setIds] = useState<Set<number>>(new Set())
  const toggle = (id: number, on?: boolean) =>
    setIds((s) => {
      const n = new Set(s)
      if (on ?? !n.has(id)) n.add(id)
      else n.delete(id)
      return n
    })
  const stop = () => {
    setActive(false)
    setIds(new Set())
  }
  return { active, start: () => setActive(true), stop, ids, toggle, setIds, has: (id: number) => ids.has(id) }
}
export type Selection = ReturnType<typeof useSelection>

/** Floating bar shown while selecting: count, select all, move to another account. */
export function BulkBar({ sel, allIds }: { sel: Selection; allIds: number[] }) {
  const { t } = useI18n()
  const [moving, setMoving] = useState(false)
  if (!sel.active) return null
  const all = allIds.length > 0 && allIds.every((id) => sel.ids.has(id))
  return (
    <>
      <div className="pb-safe fixed inset-x-0 bottom-16 z-40 px-3 md:bottom-6 md:left-60 md:px-8">
        <div className="mx-auto flex max-w-3xl items-center gap-2 rounded-2xl bg-slate-900 p-2 pl-4 text-white shadow-xl dark:bg-slate-100 dark:text-slate-900">
          <span className="min-w-0 flex-1 truncate text-sm font-medium">{t('n_selected', { n: sel.ids.size })}</span>
          <button className="rounded-lg px-2 py-1.5 text-sm opacity-80 hover:opacity-100" onClick={() => sel.setIds(all ? new Set() : new Set(allIds))}>
            {all ? t('select_none') : t('select_all')}
          </button>
          <button className="btn-primary px-3" aria-label={t('bulk_move')} disabled={!sel.ids.size} onClick={() => setMoving(true)}>
            <ArrowRightLeft className="h-4 w-4" /> <span className="hidden sm:inline">{t('bulk_move')}</span>
          </button>
          <button className="rounded-lg p-2 opacity-80 hover:opacity-100" onClick={sel.stop} aria-label={t('close')}>
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
      <BulkMoveSheet open={moving} ids={[...sel.ids]} onClose={() => setMoving(false)} onDone={() => { setMoving(false); sel.stop() }} />
    </>
  )
}

const SKIP_REASONS = ['split', 'no_payment_side', 'account_not_used', 'statement_line', 'same_account', 'opening'] as const

function BulkMoveSheet({ open, ids, onClose, onDone }: { open: boolean; ids: number[]; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const [mode, setMode] = useState<'counter' | 'replace'>('counter')
  const [from, setFrom] = useState<number | null>(null)
  const [target, setTarget] = useState<number | null>(null)
  const [confirm, setConfirm] = useState(true)
  const run = useMutation({
    mutationFn: () =>
      api.post<{ updated: number; skipped: { id: number; reason: string }[] }>('/api/transactions/bulk-account', {
        ids, account_id: target, from_account_id: mode === 'replace' ? from : null, confirm,
      }),
    onSuccess: (r) => {
      refresh()
      const reasons = SKIP_REASONS.map((k) => [k, r.skipped.filter((s) => s.reason === k).length] as const).filter(([, n]) => n)
      const skipped = reasons.map(([k, n]) => `${n} ${t(`skip_${k}`)}`).join(', ')
      toast(t('bulk_done', { n: r.updated }) + (skipped ? ` · ${t('bulk_skipped')}: ${skipped}` : ''), r.updated ? 'ok' : 'error')
      if (r.updated) onDone()
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  const ready = !!target && (mode === 'counter' || (!!from && from !== target))
  return (
    <Sheet open={open} onClose={onClose} title={t('bulk_title', { n: ids.length })}>
      <div className="space-y-4">
        <Segmented value={mode} onChange={setMode} options={[{ value: 'counter', label: t('bulk_mode_counter') }, { value: 'replace', label: t('bulk_mode_replace') }]} />
        <p className="text-xs text-slate-500">{mode === 'counter' ? t('bulk_counter_help') : t('bulk_replace_help')}</p>
        {mode === 'replace' && (
          <Field label={t('bulk_from')}>
            <AccountPicker value={from} onChange={(id) => setFrom(id)} />
          </Field>
        )}
        <Field label={t('bulk_target')}>
          <AccountPicker value={target} onChange={(id) => setTarget(id)} exclude={from ? [from] : []} />
        </Field>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5 accent-brand-700" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
          {t('bulk_confirm')}
        </label>
        <button className="btn-primary w-full" disabled={!ready || run.isPending} onClick={() => run.mutate()}>
          {t('bulk_apply', { n: ids.length })}
        </button>
      </div>
    </Sheet>
  )
}
