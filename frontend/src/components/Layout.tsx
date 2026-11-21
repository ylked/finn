import { BarChart3, BookOpen, CheckCheck, FileUp, Home, List, LogOut, Menu, Plus, Receipt, Settings } from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useMe } from '../lib/hooks'
import { useI18n, type Key } from '../lib/i18n'
import { cls } from '../lib/format'

const items: { to: string; key: Key; icon: typeof Home }[] = [
  { to: '/', key: 'nav_dashboard', icon: Home },
  { to: '/review', key: 'nav_review', icon: CheckCheck },
  { to: '/transactions', key: 'nav_transactions', icon: List },
  { to: '/new', key: 'nav_new', icon: Plus },
  { to: '/imports', key: 'nav_imports', icon: FileUp },
  { to: '/receipts', key: 'nav_receipts', icon: Receipt },
  { to: '/accounts', key: 'nav_accounts', icon: BookOpen },
  { to: '/reports', key: 'nav_reports', icon: BarChart3 },
  { to: '/settings', key: 'nav_settings', icon: Settings },
]

function useReviewCount() {
  const q = useQuery({
    queryKey: ['stats'],
    queryFn: () => api.get<{ by_state: Record<string, number>; ai_pending: number }>('/api/transactions/stats'),
    refetchInterval: (q) => (q.state.data?.ai_pending ? 4000 : 60_000),
  })
  const s = q.data?.by_state ?? {}
  return (s.auto ?? 0) + (s.unclassified ?? 0)
}

export function Layout() {
  const { t } = useI18n()
  const me = useMe()
  const count = useReviewCount()
  const loc = useLocation()
  const mobileTabs = [items[0], items[1], items[3], items[2]]
  const moreActive = !mobileTabs.some((i) => (i.to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(i.to)))

  return (
    <div className="min-h-dvh md:flex">
      {/* desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r border-slate-200 bg-white px-3 py-4 dark:border-slate-800 dark:bg-slate-900 md:flex">
        <div className="mb-6 flex items-center gap-2 px-2">
          <img src="/favicon.svg" className="h-8 w-8" alt="" />
          <div>
            <div className="font-semibold leading-tight">Finn</div>
            <div className="max-w-[10rem] truncate text-xs text-slate-500">{me.data?.ledger.name}</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5">
          {items.map(({ to, key, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) => cls('flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium', isActive ? 'bg-brand-50 text-brand-800 dark:bg-brand-900/40 dark:text-brand-100' : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800')}
            >
              <Icon className="h-4 w-4" />
              <span className="flex-1">{t(key)}</span>
              {key === 'nav_review' && count > 0 && <span className="rounded-full bg-amber-500 px-1.5 text-[11px] font-semibold text-white">{count}</span>}
            </NavLink>
          ))}
        </nav>
        {me.data?.features.auth === 'oidc' && (
          <a href="/api/auth/logout" className="flex items-center gap-3 rounded-xl px-3 py-2 text-sm text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">
            <LogOut className="h-4 w-4" /> {t('logout')}
          </a>
        )}
      </aside>

      <main className="pt-safe min-w-0 flex-1 px-4 pb-28 pt-4 md:px-8 md:pb-10 md:pt-8">
        <div className="mx-auto max-w-6xl">
          <Outlet />
        </div>
      </main>

      {/* mobile tab bar */}
      <nav className="pb-safe fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 backdrop-blur dark:border-slate-800 dark:bg-slate-900/95 md:hidden">
        <div className="grid grid-cols-5">
          {mobileTabs.map(({ to, key, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => cls('relative flex flex-col items-center gap-0.5 py-2 text-[11px]', isActive ? 'text-brand-700 dark:text-brand-200' : 'text-slate-500')}>
              {key === 'nav_new' ? (
                <span className="-mt-1 flex h-9 w-9 items-center justify-center rounded-full bg-brand-700 text-white shadow">
                  <Icon className="h-5 w-5" />
                </span>
              ) : (
                <Icon className="h-5 w-5" />
              )}
              {key !== 'nav_new' && t(key)}
              {key === 'nav_review' && count > 0 && <span className="absolute right-[22%] top-1 rounded-full bg-amber-500 px-1.5 text-[10px] font-semibold text-white">{count}</span>}
            </NavLink>
          ))}
          <NavLink to="/more" className={cls('flex flex-col items-center gap-0.5 py-2 text-[11px]', moreActive ? 'text-brand-700 dark:text-brand-200' : 'text-slate-500')}>
            <Menu className="h-5 w-5" />
            {t('nav_more')}
          </NavLink>
        </div>
      </nav>
    </div>
  )
}

export function MoreMenu() {
  const { t } = useI18n()
  const me = useMe()
  return (
    <div className="space-y-2">
      <h1 className="mb-4 text-xl font-semibold">{t('nav_more')}</h1>
      <div className="card divide-y divide-slate-100 dark:divide-slate-800">
        {items.slice(4).map(({ to, key, icon: Icon }) => (
          <NavLink key={to} to={to} className="flex items-center gap-3 px-4 py-3.5">
            <Icon className="h-5 w-5 text-slate-500" /> {t(key)}
          </NavLink>
        ))}
        {me.data?.features.auth === 'oidc' && (
          <a href="/api/auth/logout" className="flex items-center gap-3 px-4 py-3.5 text-red-600">
            <LogOut className="h-5 w-5" /> {t('logout')}
          </a>
        )}
      </div>
      <p className="px-1 pt-2 text-xs text-slate-500">{t('install_app')}</p>
    </div>
  )
}
