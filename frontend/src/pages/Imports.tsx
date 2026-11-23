import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, FileUp, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { AccountPicker } from '../components/AccountPicker'
import { Badge, Field, Money, PageTitle, Sheet, useToast } from '../components/ui'
import { api } from '../lib/api'
import { cls, fmtDate } from '../lib/format'
import { useAccounts, useRefreshLedger } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { BankEntry, Batch, CsvMapping, CsvProfile } from '../lib/types'

const DEFAULT_MAPPING: CsvMapping = {
  delimiter: 'auto',
  encoding: 'auto',
  skip_rows: 0,
  has_header: true,
  date_column: '',
  date_format: '%d.%m.%Y',
  description_columns: [],
  amount_mode: 'single',
  amount_column: null,
  invert_sign: false,
  decimal_separator: '.',
}

interface UploadResult {
  results: { filename: string; status: 'ok' | 'duplicate' | 'error'; error?: string; new?: number; matched?: number; duplicates?: number; errors?: string[] }[]
  unknown_ibans: { iban: string; servicer: string | null; owner: string | null; currency: string; files: string[] }[]
}

export function Imports() {
  const { t } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const fileRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [accountId, setAccountId] = useState<number | null>(null)
  const [mapping, setMapping] = useState<CsvMapping>(DEFAULT_MAPPING)
  const [profileId, setProfileId] = useState<number | null>(null)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [ibanMap, setIbanMap] = useState<Record<string, string>>({})
  const hasCsv = files.some((f) => !/\.(xml|zip)$/i.test(f.name))

  const run = useMutation({
    mutationFn: (imap: Record<string, string>) => {
      const fd = new FormData()
      files.forEach((f) => fd.append('files', f))
      if (accountId) fd.append('account_id', String(accountId))
      if (hasCsv) fd.append('mapping', JSON.stringify(mapping))
      if (Object.keys(imap).length) fd.append('iban_map', JSON.stringify(imap))
      return api.post<UploadResult>('/api/imports', fd)
    },
    onSuccess: (r) => {
      setResult(r)
      refresh()
      if (!r.unknown_ibans.length) setFiles([])
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })

  return (
    <div className="space-y-6">
      <PageTitle>{t('imports')}</PageTitle>
      <div className="card space-y-4 p-4">
        <div>
          <div className="font-medium">{t('import_files')}</div>
          <p className="text-sm text-slate-500">{t('import_help')}</p>
        </div>
        <input ref={fileRef} type="file" multiple accept=".xml,.zip,.csv,.txt,text/csv,application/xml,text/xml,application/zip" hidden onChange={(e) => { setFiles(Array.from(e.target.files ?? [])); setResult(null); e.target.value = '' }} />
        <button
          className="flex w-full flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-slate-300 p-6 text-slate-500 hover:border-brand-600 hover:text-brand-700 dark:border-slate-700"
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); setFiles(Array.from(e.dataTransfer.files)); setResult(null) }}
        >
          <FileUp className="h-8 w-8" />
          <span className="text-sm">{files.length ? files.map((f) => f.name).slice(0, 4).join(', ') + (files.length > 4 ? ` +${files.length - 4}` : '') : t('choose_files')}</span>
        </button>
        <Field label={t('target_account')} hint={hasCsv ? t('credit_card_help') : undefined}>
          <div className="flex gap-2">
            <AccountPicker className="flex-1" value={accountId} onChange={(id) => setAccountId(id)} types={['asset', 'liability']} placeholder={t('target_auto')} />
            {accountId && <button className="btn-ghost" onClick={() => setAccountId(null)}>×</button>}
          </div>
        </Field>
        {hasCsv && files[0] && <CsvMapper file={files.find((f) => !/\.(xml|zip)$/i.test(f.name))!} mapping={mapping} setMapping={setMapping} profileId={profileId} setProfileId={setProfileId} />}
        <button className="btn-primary w-full" disabled={!files.length || run.isPending || (hasCsv && !accountId)} onClick={() => run.mutate(ibanMap)}>
          {run.isPending ? t('loading') : t('run_import')}
        </button>

        {result && (
          <div className="space-y-1 text-sm">
            {result.results.map((r, i) => (
              <div key={i} className={cls('flex items-start gap-2', r.status === 'error' && 'text-red-600')}>
                {r.status === 'ok' ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-600" /> : <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-500" />}
                <span>
                  {r.status === 'ok' && t('import_result', { file: r.filename, n: r.new ?? 0, m: r.matched ?? 0, d: r.duplicates ?? 0 })}
                  {r.status === 'duplicate' && t('duplicate_file', { file: r.filename })}
                  {r.status === 'error' && `${r.filename}: ${r.error}`}
                  {r.errors?.length ? <span className="block text-xs text-red-600">{r.errors.slice(0, 5).join(' · ')}</span> : null}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <UnknownIbans
        result={result}
        onCancel={() => setResult((r) => (r ? { ...r, unknown_ibans: [] } : r))}
        onSubmit={(m) => {
          setIbanMap(m)
          run.mutate(m)
        }}
      />
      <Unbooked />
      <History />
    </div>
  )
}

function UnknownIbans({ result, onSubmit, onCancel }: { result: UploadResult | null; onSubmit: (m: Record<string, string>) => void; onCancel: () => void }) {
  const { t } = useI18n()
  const [choice, setChoice] = useState<Record<string, string>>({})
  const list = result?.unknown_ibans ?? []
  useEffect(() => setChoice(Object.fromEntries(list.map((u) => [u.iban, 'new']))), [result]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Sheet open={list.length > 0} onClose={onCancel} title={t('unknown_iban', { iban: list[0]?.iban ?? '' })}>
      <p className="mb-3 text-sm text-slate-500">{t('unknown_iban_help')}</p>
      <div className="space-y-4">
        {list.map((u) => (
          <div key={u.iban} className="space-y-2">
            <div className="text-sm">
              <div className="font-mono">{u.iban}</div>
              <div className="text-xs text-slate-500">{[u.servicer, u.owner, u.currency].filter(Boolean).join(' · ')} · {u.files.length} file(s)</div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" className="accent-brand-700" checked={choice[u.iban] === 'new'} onChange={() => setChoice((c) => ({ ...c, [u.iban]: 'new' }))} /> {t('create_bank_account')}
            </label>
            <AccountPicker value={choice[u.iban] && choice[u.iban] !== 'new' ? Number(choice[u.iban]) : null} types={['asset', 'liability']} onChange={(id) => setChoice((c) => ({ ...c, [u.iban]: String(id) }))} />
          </div>
        ))}
        <button className="btn-primary w-full" onClick={() => onSubmit(choice)}>{t('run_import')}</button>
      </div>
    </Sheet>
  )
}

function CsvMapper({ file, mapping, setMapping, profileId, setProfileId }: { file: File; mapping: CsvMapping; setMapping: (m: CsvMapping) => void; profileId: number | null; setProfileId: (id: number | null) => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const qc = useQueryClient()
  const profiles = useQuery({ queryKey: ['csv-profiles'], queryFn: () => api.get<CsvProfile[]>('/api/csv-profiles') })
  const [profileName, setProfileName] = useState('')
  const preview = useQuery({
    queryKey: ['csv-preview', file.name, file.size, file.lastModified, mapping],
    queryFn: () => {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('mapping', JSON.stringify(mapping))
      return api.post<{ header: string[]; rows: string[][]; row_count: number; parsed: { date: string; amount: string; description: string; orig: string | null }[]; errors: string[] }>('/api/imports/csv/preview', fd)
    },
  })
  const header = preview.data?.header ?? []
  const set = (patch: Partial<CsvMapping>) => setMapping({ ...mapping, ...patch })
  const col = (key: keyof CsvMapping, label: string) => (
    <Field label={label}>
      <select className="input" value={(mapping[key] as string) ?? ''} onChange={(e) => set({ [key]: e.target.value || null } as Partial<CsvMapping>)}>
        <option value="">—</option>
        {header.map((h, i) => <option key={i} value={h}>{h}</option>)}
      </select>
    </Field>
  )
  const saveProfile = useMutation({
    mutationFn: () => (profileId ? api.put<CsvProfile>(`/api/csv-profiles/${profileId}`, { name: profileName, config: mapping }) : api.post<CsvProfile>('/api/csv-profiles', { name: profileName, config: mapping })),
    onSuccess: (p) => {
      setProfileId(p.id)
      qc.invalidateQueries({ queryKey: ['csv-profiles'] })
      toast(t('saved'))
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })

  return (
    <div className="space-y-3 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/40">
      <div className="text-sm font-medium">{t('csv_mapping')}</div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-[1fr_1fr_auto]">
        <select
          className="input"
          value={profileId ?? ''}
          onChange={(e) => {
            const p = profiles.data?.find((x) => x.id === Number(e.target.value))
            setProfileId(p?.id ?? null)
            if (p) {
              setMapping({ ...DEFAULT_MAPPING, ...p.config })
              setProfileName(p.name)
            }
          }}
        >
          <option value="">{t('new_profile')}</option>
          {profiles.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <input className="input" placeholder={t('profile_name')} value={profileName} onChange={(e) => setProfileName(e.target.value)} />
        <button className="btn-secondary" disabled={!profileName.trim()} onClick={() => saveProfile.mutate()}>{t('save_profile')}</button>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Field label={t('delimiter')}>
          <select className="input" value={mapping.delimiter} onChange={(e) => set({ delimiter: e.target.value })}>
            <option value="auto">auto</option><option value=";">;</option><option value=",">,</option><option value="\t">TAB</option><option value="|">|</option>
          </select>
        </Field>
        <Field label={t('encoding')}>
          <select className="input" value={mapping.encoding} onChange={(e) => set({ encoding: e.target.value })}>
            <option value="auto">auto</option><option value="utf-8">UTF-8</option><option value="cp1252">Windows-1252</option><option value="latin-1">ISO-8859-1</option>
          </select>
        </Field>
        <Field label={t('skip_rows')}>
          <input type="number" min={0} className="input" value={mapping.skip_rows} onChange={(e) => set({ skip_rows: Number(e.target.value) || 0 })} />
        </Field>
        <Field label={t('decimal_separator')}>
          <select className="input" value={mapping.decimal_separator} onChange={(e) => set({ decimal_separator: e.target.value })}>
            <option value=".">1'234.50</option><option value=",">1.234,50</option>
          </select>
        </Field>
        {col('date_column', t('date_column'))}
        <Field label={t('date_format')}>
          <select className="input" value={mapping.date_format} onChange={(e) => set({ date_format: e.target.value })}>
            <option value="%d.%m.%Y">31.12.2026</option><option value="%d.%m.%y">31.12.26</option><option value="%Y-%m-%d">2026-12-31</option><option value="%d/%m/%Y">31/12/2026</option><option value="%m/%d/%Y">12/31/2026</option>
          </select>
        </Field>
        <Field label={t('description_columns')} className="col-span-2">
          <select multiple className="input h-20" value={mapping.description_columns} onChange={(e) => set({ description_columns: Array.from(e.target.selectedOptions).map((o) => o.value) })}>
            {header.map((h, i) => <option key={i} value={h}>{h}</option>)}
          </select>
        </Field>
        <Field label={t('amount_mode')} className="col-span-2">
          <select className="input" value={mapping.amount_mode} onChange={(e) => set({ amount_mode: e.target.value as 'single' })}>
            <option value="single">{t('amount_single')}</option><option value="debit_credit">{t('amount_two')}</option>
          </select>
        </Field>
        {mapping.amount_mode === 'single' ? col('amount_column', t('amount_column')) : (<>{col('debit_column', t('debit_column'))}{col('credit_column', t('credit_column'))}</>)}
        {col('counterparty_column', t('counterparty_column'))}
        {col('currency_column', t('currency_column'))}
        {col('orig_amount_column', t('orig_amount_column'))}
        {col('orig_currency_column', t('orig_currency_column'))}
        {col('reference_column', t('reference_column'))}
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="accent-brand-700" checked={mapping.invert_sign} onChange={(e) => set({ invert_sign: e.target.checked })} /> {t('invert_sign')}
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="accent-brand-700" checked={mapping.has_header} onChange={(e) => set({ has_header: e.target.checked })} /> {t('has_header')}
      </label>

      {preview.data && (
        <div className="space-y-2">
          <div className="text-xs font-medium text-slate-500">{t('preview')} ({preview.data.row_count})</div>
          <div className="max-h-48 overflow-auto rounded-lg bg-white text-xs dark:bg-slate-900">
            <table className="w-full">
              <thead><tr>{header.map((h, i) => <th key={i} className="whitespace-nowrap px-2 py-1 text-left font-medium">{h}</th>)}</tr></thead>
              <tbody>{preview.data.rows.slice(0, 6).map((r, i) => <tr key={i} className="border-t border-slate-100 dark:border-slate-800">{r.map((c, j) => <td key={j} className="whitespace-nowrap px-2 py-1">{c}</td>)}</tr>)}</tbody>
            </table>
          </div>
          {preview.data.parsed.length > 0 && (
            <>
              <div className="text-xs font-medium text-slate-500">{t('parsed_preview')}</div>
              <div className="divide-y divide-slate-100 rounded-lg bg-white text-xs dark:divide-slate-800 dark:bg-slate-900">
                {preview.data.parsed.slice(0, 6).map((p, i) => (
                  <div key={i} className="flex gap-2 px-2 py-1">
                    <span className="w-20 shrink-0">{p.date}</span>
                    <span className="flex-1 truncate">{p.description}</span>
                    {p.orig && <span className="text-slate-400">{p.orig}</span>}
                    <Money value={p.amount} sign colored />
                  </div>
                ))}
              </div>
            </>
          )}
          {preview.data.errors.map((e, i) => <div key={i} className="text-xs text-red-600">{e}</div>)}
        </div>
      )}
    </div>
  )
}

function Unbooked() {
  const { t, locale } = useI18n()
  const refresh = useRefreshLedger()
  const q = useQuery({ queryKey: ['unbooked'], queryFn: () => api.get<BankEntry[]>('/api/bank-entries/unbooked') })
  const book = useMutation({ mutationFn: (id: number) => api.post(`/api/bank-entries/${id}/book`), onSuccess: () => refresh() })
  if (!q.data?.length) return null
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center gap-2 font-medium text-amber-700"><AlertTriangle className="h-4 w-4" /> {t('unbooked')}</div>
      {q.data.map((e) => (
        <div key={e.id} className="flex items-center gap-3 border-t border-slate-100 py-2 text-sm dark:border-slate-800">
          <span className="w-20 text-xs text-slate-500">{fmtDate(e.booking_date, locale)}</span>
          <span className="flex-1 truncate">{e.counterparty || e.description}</span>
          <Money value={e.amount} sign colored />
          <button className="btn-secondary px-2 py-1 text-xs" onClick={() => book.mutate(e.id)}>{t('book')}</button>
        </div>
      ))}
    </div>
  )
}

function History() {
  const { t, locale } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const { byId } = useAccounts()
  const q = useQuery({ queryKey: ['imports'], queryFn: () => api.get<Batch[]>('/api/imports') })
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/api/imports/${id}`),
    onSuccess: () => { toast(t('deleted')); refresh() },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  if (!q.data?.length) return null
  return (
    <div className="card overflow-hidden">
      <div className="px-4 pt-4 font-medium">{t('history')}</div>
      <div className="divide-y divide-slate-100 dark:divide-slate-800">
        {q.data.map((b) => (
          <div key={b.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            <div className="min-w-0 flex-1">
              <div className="truncate">{b.filename}</div>
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                <span>{byId.get(b.account_id)?.name}</span>
                <span>· {fmtDate(b.from_date, locale)}{b.to_date && b.to_date !== b.from_date ? ` → ${fmtDate(b.to_date, locale)}` : ''}</span>
                <span>· {b.entries_new}/{b.entries_total}{b.entries_matched ? ` (${b.entries_matched} ↔)` : ''}</span>
                {b.balance_ok === true && <Badge color="green">{t('balance_ok')}</Badge>}
                {b.balance_ok === false && <Badge color="red">{t('balance_ko')}: <Money value={b.computed_balance} /> ≠ <Money value={b.closing_balance} /></Badge>}
              </div>
            </div>
            {b.closing_balance && <Money value={b.closing_balance} className="hidden text-xs md:inline" />}
            <button className="btn-ghost p-1.5 text-slate-400 hover:text-red-600" onClick={() => confirm(t('delete_import_confirm')) && del.mutate(b.id)}>
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
