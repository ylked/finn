export type AccountType = 'asset' | 'liability' | 'income' | 'expense'
export type AccountKind = 'bank' | 'credit_card' | 'cash' | 'receivable' | 'equity' | 'suspense' | 'other'

export interface Account {
  id: number
  parent_id: number | null
  code: string
  name: string
  type: AccountType
  kind: AccountKind
  is_group: boolean
  is_active: boolean
  currency: string
  iban: string | null
  description: string | null
  keywords: string | null
  opening_balance: string | null
  opening_date: string | null
  balance: string
}

export interface Line {
  id?: number
  account_id: number
  account_code?: string
  account_name?: string
  account_kind?: AccountKind
  amount: string
  memo?: string | null
  orig_amount?: string | null
  orig_currency?: string | null
  bank_entry_id?: number | null
}

export interface BankEntry {
  id: number
  account_id: number
  batch_id: number
  booking_date: string
  value_date: string | null
  amount: string
  currency: string
  orig_amount: string | null
  orig_currency: string | null
  description: string
  counterparty: string | null
  counterparty_iban: string | null
  remittance_info: string | null
  bank_ref: string | null
  tx_code: string | null
}

export type TxState = 'unclassified' | 'auto' | 'confirmed'

export interface Transaction {
  id: number
  date: string
  description: string
  counterparty: string | null
  notes: string | null
  source: 'bank' | 'csv' | 'manual' | 'receipt' | 'opening'
  state: TxState
  ai_status: string
  ai_confidence: number | null
  ai_reason: string | null
  lines: Line[]
  receipts: { id: number; filename: string; mime_type: string; status: string }[]
  reconciled: boolean
  bank_entries?: BankEntry[]
  created_at: string
  updated_at: string
}

export interface Receipt {
  id: number
  transaction_id: number | null
  filename: string
  mime_type: string
  status: 'pending' | 'unmatched' | 'matched' | 'failed' | 'manual'
  extracted: {
    merchant?: string
    date?: string
    total?: string
    currency?: string
    items?: { description: string; amount: number }[]
    splits?: { account_code: string; amount: string; label?: string }[]
    confidence?: number
  } | null
  error: string | null
  payment_account_id: number | null
  created_at: string
}

export interface Batch {
  id: number
  account_id: number
  kind: 'camt053' | 'csv'
  filename: string
  statement_id: string | null
  from_date: string | null
  to_date: string | null
  opening_balance: string | null
  closing_balance: string | null
  entries_total: number
  entries_new: number
  entries_matched: number
  created_at: string
  computed_balance: string | null
  ledger_balance: string | null
  balance_ok: boolean | null
}

export interface Rule {
  id: number
  pattern: string
  direction: 'any' | 'in' | 'out'
  source_account_id: number | null
  target_account_id: number
  priority: number
  confirm: boolean
  hits: number
}

export interface CsvMapping {
  delimiter: string
  encoding: string
  skip_rows: number
  has_header: boolean
  date_column: string
  date_format: string
  value_date_column?: string | null
  description_columns: string[]
  counterparty_column?: string | null
  amount_mode: 'single' | 'debit_credit'
  amount_column?: string | null
  debit_column?: string | null
  credit_column?: string | null
  invert_sign: boolean
  decimal_separator: string
  currency_column?: string | null
  orig_amount_column?: string | null
  orig_currency_column?: string | null
  reference_column?: string | null
}

export interface CsvProfile { id: number; name: string; config: CsvMapping }

export interface Me {
  user: { id: number; name: string | null; email: string | null; locale: 'fr' | 'en' }
  ledger: { id: number; name: string; base_currency: string; locale: string; settings: Record<string, unknown> }
  role: 'owner' | 'editor' | 'viewer'
  ledgers: { id: number; name: string; role: string }[]
  features: { ai: boolean; vision: boolean; auth: string }
}
