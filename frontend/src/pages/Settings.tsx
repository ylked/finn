import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, KeyRound, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Field, PageTitle, Segmented, useToast } from '../components/ui'
import { api } from '../lib/api'
import { fmtDate } from '../lib/format'
import { accountLabel, useAccounts, useMe } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { CsvProfile, Rule } from '../lib/types'

export function SettingsPage() {
  const { t, locale, setLocale } = useI18n()
  const toast = useToast()
  const qc = useQueryClient()
  const me = useMe()
  const { byId } = useAccounts()
  const s = (me.data?.ledger.settings ?? {}) as Record<string, number | boolean>
  const [name, setName] = useState('')
  const [tol, setTol] = useState('7')
  const [minConf, setMinConf] = useState('0')
  useEffect(() => {
    if (me.data) {
      setName(me.data.ledger.name)
      setTol(String(s.match_tolerance_days ?? 7))
      setMinConf(String(s.ai_min_confidence ?? 0))
    }
  }, [me.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const patch = useMutation({
    mutationFn: (body: object) => api.patch('/api/ledger', body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['me'] }); toast(t('saved')) },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  const rules = useQuery({ queryKey: ['rules'], queryFn: () => api.get<Rule[]>('/api/rules') })
  const profiles = useQuery({ queryKey: ['csv-profiles'], queryFn: () => api.get<CsvProfile[]>('/api/csv-profiles') })
  const delRule = useMutation({ mutationFn: (id: number) => api.del(`/api/rules/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ['rules'] }) })
  const delProfile = useMutation({ mutationFn: (id: number) => api.del(`/api/csv-profiles/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ['csv-profiles'] }) })
  const f = me.data?.features

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <PageTitle>{t('settings')}</PageTitle>
      <div className="card space-y-4 p-4">
        <Field label={t('language')}>
          <Segmented value={locale} onChange={(l) => { setLocale(l); api.patch('/api/me', { locale: l }) }} options={[{ value: 'fr', label: 'Français' }, { value: 'en', label: 'English' }]} />
        </Field>
        <Field label={t('ledger_name')}>
          <div className="flex gap-2">
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            <button className="btn-secondary" onClick={() => patch.mutate({ name })}>{t('save')}</button>
          </div>
        </Field>
      </div>

      <div className="card space-y-3 p-4">
        <div className="font-medium">{t('ai_settings')}</div>
        {f && <div className="text-xs text-slate-500">{t('ai_status', { a: f.ai ? t('configured') : t('not_configured'), v: f.vision ? t('configured') : t('not_configured') })}</div>}
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5 accent-brand-700" checked={s.auto_classify !== false} onChange={(e) => patch.mutate({ settings: { auto_classify: e.target.checked } })} />
          {t('auto_classify')}
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5 accent-brand-700" checked={s.auto_match !== false} onChange={(e) => patch.mutate({ settings: { auto_match: e.target.checked } })} />
          {t('auto_match')}
        </label>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('match_tolerance')}><input type="number" min={0} max={60} className="input" value={tol} onChange={(e) => setTol(e.target.value)} onBlur={() => patch.mutate({ settings: { match_tolerance_days: Number(tol) || 0 } })} /></Field>
          <Field label={t('ai_min_confidence')}><input type="number" min={0} max={1} step={0.05} className="input" value={minConf} onChange={(e) => setMinConf(e.target.value)} onBlur={() => patch.mutate({ settings: { ai_min_confidence: Number(minConf) || 0 } })} /></Field>
        </div>
      </div>

      <div className="card p-4">
        <div className="font-medium">{t('rules')}</div>
        <p className="mb-2 text-xs text-slate-500">{t('rules_help')}</p>
        <div className="divide-y divide-slate-100 dark:divide-slate-800">
          {rules.data?.map((r) => (
            <div key={r.id} className="flex items-center gap-2 py-2 text-sm">
              <div className="min-w-0 flex-1">
                <div className="truncate">« {r.pattern} » → {accountLabel(byId.get(r.target_account_id))}</div>
                <div className="text-xs text-slate-500">{t(`dir_${r.direction}`)} · {t('hits')}: {r.hits}{r.confirm && ` · ${t('auto_confirm')}`}</div>
              </div>
              <button className="btn-ghost p-1.5 text-slate-400 hover:text-red-600" onClick={() => delRule.mutate(r.id)}><Trash2 className="h-4 w-4" /></button>
            </div>
          ))}
          {!rules.data?.length && <div className="py-2 text-sm text-slate-400">—</div>}
        </div>
      </div>

      <div className="card p-4">
        <div className="mb-2 font-medium">{t('csv_profiles')}</div>
        {profiles.data?.map((p) => (
          <div key={p.id} className="flex items-center justify-between border-t border-slate-100 py-2 text-sm dark:border-slate-800">
            <span>{p.name}</span>
            <button className="btn-ghost p-1.5 text-slate-400 hover:text-red-600" onClick={() => delProfile.mutate(p.id)}><Trash2 className="h-4 w-4" /></button>
          </div>
        ))}
        {!profiles.data?.length && <div className="text-sm text-slate-400">—</div>}
      </div>
      <McpCard />
      <p className="px-1 text-xs text-slate-500">{t('install_app')}</p>
    </div>
  )
}

type ApiToken = { id: number; name: string; prefix: string; can_write: boolean; created_at: string; last_used_at: string | null; expires_at: string | null }
type McpInfo = { enabled: boolean; url: string; oauth: boolean }

function CopyButton({ text }: { text: string }) {
  const { t } = useI18n()
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      className="btn-secondary shrink-0 px-2.5"
      onClick={() => navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500) })}
      aria-label={t('token_copy')}
    >
      {done ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
    </button>
  )
}

function McpCard() {
  const { t, locale } = useI18n()
  const toast = useToast()
  const qc = useQueryClient()
  const info = useQuery({ queryKey: ['mcp-info'], queryFn: () => api.get<McpInfo>('/api/tokens/info') })
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: () => api.get<ApiToken[]>('/api/tokens') })
  const [name, setName] = useState('')
  const [write, setWrite] = useState(false)
  const [days, setDays] = useState('')
  const [created, setCreated] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () => api.post<ApiToken & { token: string }>('/api/tokens', { name: name.trim(), can_write: write, expires_days: days ? Number(days) : null }),
    onSuccess: (r) => { setCreated(r.token); setName(''); setDays(''); qc.invalidateQueries({ queryKey: ['tokens'] }) },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  const revoke = useMutation({
    mutationFn: (id: number) => api.del(`/api/tokens/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tokens'] }),
  })
  if (info.data && !info.data.enabled) {
    return <div className="card p-4"><div className="font-medium">{t('mcp')}</div><p className="mt-1 text-xs text-slate-500">{t('mcp_disabled')}</p></div>
  }
  return (
    <div className="card space-y-3 p-4">
      <div className="flex items-center gap-2 font-medium"><KeyRound className="h-4 w-4" />{t('mcp')}</div>
      <p className="text-xs text-slate-500">{t('mcp_help')}</p>
      {info.data && (
        <Field label={t('mcp_url')}>
          <div className="flex gap-2">
            <input className="input font-mono text-xs" readOnly value={info.data.url} onFocus={(e) => e.target.select()} />
            <CopyButton text={info.data.url} />
          </div>
        </Field>
      )}
      {info.data?.oauth && <p className="text-xs text-slate-500">{t('mcp_oauth_on')}</p>}

      <div className="divide-y divide-slate-100 dark:divide-slate-800">
        {tokens.data?.map((k) => (
          <div key={k.id} className="flex items-center gap-2 py-2 text-sm">
            <div className="min-w-0 flex-1">
              <div className="truncate">{k.name} <span className="font-mono text-xs text-slate-400">{k.prefix}…</span></div>
              <div className="text-xs text-slate-500">
                {k.can_write ? t('token_rw') : t('token_read')} · {k.last_used_at ? `${t('token_last_used')} ${fmtDate(k.last_used_at.slice(0, 10), locale)}` : t('token_never_used')}
                {k.expires_at && ` · ${t('token_expires_on')} ${fmtDate(k.expires_at.slice(0, 10), locale)}`}
              </div>
            </div>
            <button className="btn-ghost p-1.5 text-slate-400 hover:text-red-600" onClick={() => window.confirm(t('token_revoke_confirm')) && revoke.mutate(k.id)} aria-label={t('delete')}>
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>

      {created && (
        <div className="space-y-2 rounded-xl bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          <div>{t('token_created')}</div>
          <div className="flex gap-2">
            <input className="input font-mono text-xs" readOnly value={created} onFocus={(e) => e.target.select()} />
            <CopyButton text={created} />
          </div>
        </div>
      )}

      <form className="space-y-3 border-t border-slate-100 pt-3 dark:border-slate-800" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate() }}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={t('token_name')}><input className="input" value={name} maxLength={100} placeholder="Claude" onChange={(e) => setName(e.target.value)} /></Field>
          <Field label={t('token_expires')}><input type="number" min={1} max={3650} className="input" value={days} onChange={(e) => setDays(e.target.value)} /></Field>
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5 accent-brand-700" checked={write} onChange={(e) => setWrite(e.target.checked)} />
          {t('token_write')}
        </label>
        <button className="btn-primary" disabled={!name.trim() || create.isPending}>{t('token_create')}</button>
      </form>
    </div>
  )
}
