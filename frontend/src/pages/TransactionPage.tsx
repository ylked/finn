import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { TxEditor } from '../components/Tx'
import { PageTitle, Spinner } from '../components/ui'
import { api } from '../lib/api'
import type { Transaction } from '../lib/types'

export function TransactionPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const q = useQuery({ queryKey: ['transaction', Number(id)], queryFn: () => api.get<Transaction>(`/api/transactions/${id}`) })
  if (!q.data) return <Spinner />
  return (
    <div className="card p-4 md:p-6">
      <PageTitle>{q.data.counterparty || q.data.description}</PageTitle>
      <TxEditor tx={q.data} onDone={(r) => (r === null ? nav('/transactions') : q.refetch())} />
    </div>
  )
}
