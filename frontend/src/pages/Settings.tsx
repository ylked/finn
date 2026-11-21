import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Field, PageTitle, Segmented, useToast } from '../components/ui'
import { api } from '../lib/api'
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
      <p className="px-1 text-xs text-slate-500">{t('install_app')}</p>
    </div>
  )
}
