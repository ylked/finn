# Finn: personal double-entry accounting with AI assistance

Self-hosted web app (installable PWA on iPhone, Android and desktop) for keeping personal books
in **double-entry accounting** (comptabilité en partie double), fed by bank statements.

- **Imports**: CAMT.053 (ISO 20022 XML, versions .02 to .08, several files or a `.zip` at once) and **generic CSV**
  with saved column-mapping profiles (credit cards, other banks). Dedup works per statement line and per file.
  Each statement's closing balance is reconciled.
- **AI classification** through any OpenAI-compatible API. Imported lines are booked against a suspense
  account, then classified and marked **Auto** for your review. This can be turned off.
- **Manual entries and receipts are matched** to the statement line when it gets imported (same account,
  same amount, date within a tolerance). Receipts (photo/HEIC/PDF) are read by a vision model, which also proposes a split.
- **Ventilation**: any number of lines per transaction, so one payment can be split over several expense accounts.
- **Paid for someone**: part or all of a payment goes to a per-person receivable sub-account (asset to asset).
  The repayment (e.g. TWINT) is recognised automatically from the person's name.
- **Credit cards**: a card is a liability account. Purchases increase it, and the bank payment to the card is a
  transfer from bank to card that reconciles with both the bank statement and the card CSV.
- **Reports** (screen, PDF, CSV): balance sheet, income statement, general ledger, journal, monthly table.
- **OIDC login** (authentik or any OIDC provider), FR/EN interface, mobile-first.
- **MCP server** at `/mcp`, so AI assistants (Claude, ChatGPT, Cursor…) can browse the books, answer
  questions and record or classify entries.

## Quick start

```bash
cp .env.example .env
# fill SECRET_KEY, POSTGRES_PASSWORD, OIDC_* and AI_* (see below)
docker compose up -d --build
```

The app listens on `127.0.0.1:8080` (`APP_BIND` / `APP_PORT`). Put your reverse proxy in front of it with
HTTPS on `APP_BASE_URL`. HTTPS is required for OIDC cookies and for installing the app on iOS.

Database migrations run automatically at container start.

### authentik

1. *Applications → Providers → Create → OAuth2/OpenID Provider*
   - Client type: **Confidential**
   - Redirect URI: `https://finn.example.com/api/auth/callback` (strict)
   - Scopes: `openid`, `email`, `profile` (the default mappings)
   - Signing key: any
2. *Applications → Create*, link it to the provider. The slug (e.g. `finn`) defines the issuer.
3. In `.env`:
   ```
   OIDC_ISSUER=https://auth.example.com/application/o/finn/
   OIDC_CLIENT_ID=...
   OIDC_CLIENT_SECRET=...
   ```
4. Optional: restrict access with authentik policies/bindings, or with `OIDC_ALLOWED_GROUPS` /
   `OIDC_ALLOWED_EMAILS`.

Each user gets their own ledger on first login. The data model is multi-ledger (`ledger_members` with roles
owner / editor / viewer), so shared ledgers can be enabled later without a migration of the business tables.

### AI

Any OpenAI-compatible endpoint works (OpenAI, OpenRouter, Mistral, Ollama, vLLM, LiteLLM…):

```
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=sk-...
AI_TEXT_MODEL=gpt-4o-mini        # classification
AI_VISION_MODEL=gpt-4o-mini      # receipts (must accept images)
```

Without AI configured, the app still works: rules classify entries, and receipt data is entered by hand.
If the server rejects `response_format`, the app retries without it and extracts the JSON from the answer.

### MCP (AI assistants)

Finn serves a [Model Context Protocol](https://modelcontextprotocol.io) server at
`https://finn.example.com/mcp` (streamable HTTP). In *Settings → AI assistant access*, create a token,
read-only or read/write. The token is shown once. Then add the server to your client:

```bash
# Claude Code
claude mcp add --transport http finn https://finn.example.com/mcp --header "Authorization: Bearer finn_..."
```

```json
// Claude Desktop, Cursor, … (clients that take a URL + headers)
{ "mcpServers": { "finn": { "type": "http", "url": "https://finn.example.com/mcp",
                            "headers": { "Authorization": "Bearer finn_..." } } } }
```

Tools: `ledger_overview`, `list_accounts`, `search_transactions`, `get_transaction`, `balance_sheet`,
`income_statement`, `monthly_report`, `account_ledger`, `receivables`, `list_rules` (read), and
`create_transaction`, `classify_transaction` (single account or split), `update_transaction_lines`,
`confirm_transactions`, `create_rule`, `create_person` (write tokens only). They go through the same
validation as the web app: entries must balance, and statement lines keep their account and amount.

**OAuth (claude.ai custom connectors).** With `MCP_OAUTH=true`, `/mcp` also accepts access tokens that
authentik issues for Finn's OIDC application, and it advertises authentik through
`/.well-known/oauth-protected-resource/mcp`. Add the client's callback
(`https://claude.ai/api/mcp/auth_callback`) to the provider's redirect URIs. In the connector's advanced
settings, enter Finn's client ID and secret. The authentik user must have logged in to Finn once.

### Local development

```bash
# backend (needs a Postgres; tests create/drop their own database "finn_test")
cd backend && uv sync
DATABASE_URL=postgresql+psycopg://finn:finn@127.0.0.1:5432/finn AUTH_MODE=disabled SECRET_KEY=dev \
  STATIC_DIR=../frontend/dist uv run uvicorn app.main:app --reload --port 8080
DATABASE_URL=postgresql+psycopg://finn:finn@127.0.0.1:5432/finn_test uv run pytest

# frontend (proxies /api to :8080)
cd frontend && npm install && npm run dev
```

`AUTH_MODE=disabled` logs everyone in as a single local user. Never expose it.

## How the accounting works

| Concept | Implementation |
|---|---|
| 4 account types | `asset` (actifs), `liability` (passifs, incl. equity), `income` (produits), `expense` (charges); tree of group accounts, fully editable |
| Journal line | signed amount: **+ debit / − credit** |
| Balance | lines of each transaction sum to 0, checked by the app **and** by a deferred Postgres constraint trigger |
| Account *kind* | `bank`, `credit_card`, `cash`, `receivable` (per person), `equity`, `suspense`, `other`; drives matching and AI hints |
| Statement line | stored once, immutable (`bank_entries`), linked 1:1 to the journal line it reconciles; its account and amount cannot be edited |
| Opening balance | taken from the first CAMT statement (OPBD) against `2800 Capital / soldes initiaux`; for accounts without statements (prepaid, laundry card…) set it on the account |
| Result | balance sheet shows the cumulative result (income − expenses) so that assets = liabilities + result |

### Typical workflow

1. **Import** the CAMT files (Imports page; a ZIP of many days works). An unknown IBAN asks you to map it
   or create the bank account. Each line is either reconciled with an existing entry or booked as
   `bank ↔ 1090 suspense`.
2. **Rules** (Settings, or "Always classify like this" on an entry) run first. Then the AI proposes the
   counter-account. The entry becomes **Auto**.
3. **Review** (À valider): change the account in one tap, open an entry to split it or mark part as paid for
   someone, then confirm one by one or all at once.
4. **Before the statement arrives**: enter an expense (Saisir) or snap a receipt. It is reconciled on import
   instead of creating a duplicate. A wrong match can be undone with *Unlink from statement*.
5. **Credit card**: create (or use) `2051 Carte de crédit`, import the card CSV into it with a profile
   (*invert sign* when purchases are positive). Classify the bank payment to the card issuer to the card account.
6. **Reports**: export PDF/CSV.

Each import shows whether its closing balance matches. *Statement lines without entry* lists any statement
line whose transaction was removed, so nothing silently disappears.

## Backup

```bash
docker compose exec db pg_dump -U finn finn | gzip > finn-$(date +%F).sql.gz
docker run --rm -v finn_uploads:/u -v "$PWD":/b alpine tar czf /b/uploads-$(date +%F).tgz -C /u .
```
