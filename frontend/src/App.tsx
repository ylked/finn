import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { useEffect } from 'react'
import { Layout, MoreMenu } from './components/Layout'
import { Spinner } from './components/ui'
import { useMe } from './lib/hooks'
import { useI18n } from './lib/i18n'
import { Dashboard } from './pages/Dashboard'
import { Transactions } from './pages/Transactions'
import { TransactionPage } from './pages/TransactionPage'
import { NewEntry } from './pages/NewEntry'
import { Review } from './pages/Review'
import { Imports } from './pages/Imports'
import { Receipts } from './pages/Receipts'
import { Accounts } from './pages/Accounts'
import { Reports } from './pages/Reports'
import { SettingsPage } from './pages/Settings'

export function App() {
  const me = useMe()
  const { locale, setLocale } = useI18n()
  useEffect(() => {
    const l = me.data?.user.locale
    if (l && l !== locale && !localStorage.getItem('finn.locale')) setLocale(l)
  }, [me.data]) // eslint-disable-line react-hooks/exhaustive-deps

  if (me.isLoading)
    return (
      <div className="flex h-dvh items-center justify-center">
        <Spinner className="h-8 w-8" />
      </div>
    )
  if (me.isError)
    return (
      <div className="flex h-dvh flex-col items-center justify-center gap-4 p-6 text-center">
        <img src="/favicon.svg" className="h-14 w-14" alt="" />
        <p className="text-slate-600">{String(me.error)}</p>
        <a className="btn-primary" href="/api/auth/login">Login</a>
      </div>
    )
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="review" element={<Review />} />
          <Route path="transactions" element={<Transactions />} />
          <Route path="transactions/:id" element={<TransactionPage />} />
          <Route path="new" element={<NewEntry />} />
          <Route path="imports" element={<Imports />} />
          <Route path="receipts" element={<Receipts />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="reports" element={<Reports />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="more" element={<MoreMenu />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
