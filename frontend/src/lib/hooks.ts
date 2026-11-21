import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo } from 'react'
import { api } from './api'
import type { Account, Me } from './types'

export function useMe() {
  return useQuery({ queryKey: ['me'], queryFn: () => api.get<Me>('/api/me'), staleTime: 60_000 })
}

export function useAccounts() {
  const q = useQuery({ queryKey: ['accounts'], queryFn: () => api.get<Account[]>('/api/accounts'), staleTime: 15_000 })
  const byId = useMemo(() => new Map((q.data ?? []).map((a) => [a.id, a])), [q.data])
  return { ...q, accounts: q.data ?? [], byId }
}

/** Invalidate everything that depends on journal data. */
export function useRefreshLedger() {
  const qc = useQueryClient()
  return () => {
    for (const k of ['transactions', 'transaction', 'accounts', 'dashboard', 'stats', 'reports', 'imports', 'receipts', 'unbooked', 'receivables'])
      qc.invalidateQueries({ queryKey: [k] })
  }
}

export function accountLabel(a: Account | undefined) {
  return a ? `${a.code} ${a.name}` : ''
}
