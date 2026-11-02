# Plan: Integrate Past (Paid) Payables into the Payable Dashboard

**Generated**: 2026-11-02
**Estimated Complexity**: Medium
**Execution model**: Opus subagents, parallel by layer, one reviewer at the end

## Overview

Today the Streamlit dashboard reads `GET /api/dashboard/payable/`, which returns only designs with
status Approved (2) or Pending (0). The moment accounting clicks **Complete** in Merchwizard the
design moves to status Completed (5) and disappears from the dashboard. Result: the budget tracker
undercounts YTD spend, there is no paid-vs-outstanding view, and nothing in the dashboard matches
what PayPal actually paid out.

Since the Oct 13 2026 approved-date release accounting has been clearing payables through the app,
so paid history now accumulates in Merchwizard as Completed designs plus `payments` rows (PayPal
batch id, method, amount, timestamp).

Approach, in three layers that can be built in parallel against a contract fixed in this document:

1. **Backend** (`~/Projects/Manhead/merchwizard-backend`, Laravel 10, Bitbucket, Forge auto-deploy):
   add `designs.completed_at` (stamped by `complete()`, backfilled from the activity log exactly like
   `approved_at` was), and a new `GET /api/dashboard/payable/history` endpoint returning Completed
   designs with full fees, dates and their payment records.
2. **Streamlit data layer** (this repo, new `mw_api.py`): fetch open + paid payables, normalize into
   one DataFrame with a `Payment Status` column and paid/approved dates. Fixture-tested without
   network.
3. **Streamlit UI** (`app.py`): true YTD spend (paid + outstanding), paid/outstanding split and
   aging, payment detail per design, and a monthly paid-out report by artist (client) with CSV export.

A fourth **reviewer** agent verifies the integration end to end once the three land.

### Why `completed_at` and not `payments.created_at` or `updated_at`

- `payments` rows only exist when a payment was recorded in the app. Checks/cash marked Complete
  without a payment record would have no paid date.
- `updated_at` moves on any edit (invoice number, description), so it is not a paid date.
- `approved_at` already set the precedent: a dedicated timestamp, stamped on transition, backfilled
  from `activity_log`. Same pattern, same migration shape, same reviewer expectations.

## Prerequisites

- **Fresh Merchwizard cookie.** The `remember_web_*` cookie in `.env` is expired (payable endpoint
  returns 401 as of 2026-11-02). Re-extract from a logged-in browser session and update both `.env`
  and the Streamlit Cloud secrets before the data-layer or reviewer agents can hit the live API.
  The production dashboard is almost certainly showing "Failed to fetch data" right now.
- Backend repo cloned at `~/Projects/Manhead/merchwizard-backend` on `master`, clean tree. Read its
  `CLAUDE.md` first (statuses, money in cents, activity log, downstream-consumer warning).
- Local PHP 8.2 + composer for `php artisan test` / `route:list`. A DB for feature tests is optional;
  the backend agent must say which checks it actually ran.
- Python 3.9 compatible code (Streamlit Cloud). `pytest` added to a dev requirements file, not to
  `requirements.txt`.

## Contracts (fixed before agents start)

### A. New endpoint: `GET /api/dashboard/payable/history`

- Auth: same `auth:sanctum` + `permission:dashboard-access` group; controller middleware
  `permission:payable-index`.
- Query params (all optional):
  - `from`, `to` — ISO dates, filter on `completed_at` (inclusive, whole days).
  - `undated` — `1` to include Completed designs whose `completed_at` is NULL (default `0`; the
    dashboard labels them "Paid (date unknown)"). When `from`/`to` are given, undated rows are only
    included if `undated=1`, appended after the dated rows.
  - `per_page` — default 500, max 1000. Standard Laravel pagination envelope (`data`, `links`, `meta`).
- Row filter mirrors `PayableController::index`: status Completed, at least one fee > 0, has designer
  or separator. Ordered by `completed_at DESC NULLS LAST`, then `created_at DESC`.
- Row shape: `DesignResource` (unchanged fields) plus:
  - `completed_at` — ISO-8601 or null (always present in `DesignResource` from now on; null for
    open payables, which keeps the existing `index` response backward compatible).
  - `payments` — array, present only when the relation is loaded (history endpoint loads it).
    Each item is `DesignPaymentsResource` plus a new `paidByName` field:
    `{id, designerId, paymentId, paymentMethod, paymentType, payeeName, paidByName, status, invoice, amount, createdAt}`.
    `paymentMethod`: 1 PayPal, 2 Check, 3 Cash, 4 BankTransfer. `paymentType`: 1 Submission,
    2 Balance, 3 Separation. `amount` in dollars (resource divides cents by 100). `createdAt` is
    `m-d-Y H:i:s` (existing format, do not change).

### B. Normalized DataFrame (output of `mw_api.load_payables()`)

Existing columns stay exactly as they are (`ID, Name, Client, Designer, In-House, Submission,
Balance, Separation, *_Invoice, Status, Created, Created Date, Month, Updated, Total`). New columns:

| Column | Type | Source |
| --- | --- | --- |
| `Payment Status` | str | 0 → `Awaiting Approval`, 2 → `Outstanding`, 5 → `Paid` |
| `Approved Date` | date or NaT | `approved_at` |
| `Approved Month` | str `YYYY-MM` or None | from `Approved Date` |
| `Paid Date` | date or NaT | `completed_at` |
| `Paid Month` | str `YYYY-MM` or None | from `Paid Date` |
| `Days Outstanding` | int or NaN | today − `Approved Date`, Outstanding rows only |
| `Days to Pay` | int or NaN | `Paid Date` − `Approved Date`, Paid rows only |
| `Paid Amount` | float | sum of `payments[].amount`; 0 when none recorded |
| `Payment Method` | str | from the most recent payment; `Not Recorded` when none |
| `PayPal Batch ID` | str | `paymentId` of the most recent PayPal payment, else `""` |
| `Paid By` | str | `paidByName` of the most recent payment, else `""` |
| `Budget Year` | int | year of `Approved Date`, falling back to `Created Date` |

Month bucketing uses America/New_York (Manhead is in New York; API timestamps are UTC).

## Sprint 1: Backend — `completed_at` + history endpoint
**Owner**: Opus agent "backend" in `~/Projects/Manhead/merchwizard-backend`
**Goal**: paid history is queryable with one request, with a real paid date.
**Demo/Validation**:
- `php artisan route:list | grep payable` shows `GET api/dashboard/payable/history`.
- `php artisan test` passes (new feature tests included) or, if no test DB is available locally,
  the agent reports exactly that and shows `php -l` + `route:list` + a tinker dry run of the query.
- After deploy: `curl` with a staff cookie returns Completed designs with `completed_at` and
  `payments`; `GET /api/dashboard/payable/` response is byte-compatible for existing fields.

### Task 1.1: Migration `add_completed_at_to_designs_table`
- **Location**: `database/migrations/2026_11_02_000000_add_completed_at_to_designs_table.php`
- **Description**: Nullable `timestamp('completed_at')->after('approved_at')`, guarded with
  `Schema::hasColumn` so it is re-runnable (copy the structure of
  `2026_10_09_000000_add_approved_at_to_designs_table.php`). Backfill for designs currently
  Completed: `MAX(activity_log.created_at)` over `event='updated'` rows where
  `attributes.status = 5`. No `created_at` fallback: legacy-imported designs were created with their
  final status, so a Completed design with no log row stays NULL and surfaces as "date unknown".
- **Dependencies**: none
- **Acceptance Criteria**: migration up/down idempotent; backfill touches only status 5 rows.
- **Validation**: feature test seeds a design + an activity_log row and asserts `completed_at`.

### Task 1.2: Model + transitions
- **Location**: `app/Models/Design.php`, `app/Http/Controllers/DesignController.php`
- **Description**: add `completed_at` to `$fillable` and `$casts` (datetime). `complete()` sets
  `completed_at = now()`. `payable()` (Completed → Approved) sets `completed_at = null`.
  `reset()` and `reject()` set it null. `approve()` sets it null (a re-approved design is unpaid).
- **Dependencies**: 1.1
- **Validation**: feature tests per transition asserting the column.

### Task 1.3: Resource additions (additive only)
- **Location**: `app/Http/Resources/Designs/DesignResource.php`,
  `app/Http/Resources/Dashboard/DesignPaymentsResource.php`
- **Description**: `DesignResource` emits `completed_at` always, and
  `'payments' => $this->whenLoaded('payments', fn () => DesignPaymentsResource::collection($this->payments))`.
  `DesignPaymentsResource` adds `paidByName` from the `user` relation (add `Payment::user()` if
  missing; guard null with `?->name`).
- **Dependencies**: 1.2
- **Acceptance Criteria**: `GET /api/dashboard/payable/` gains exactly one new key
  (`completed_at: null`) and nothing else changes. Note the downstream-consumer warning in AGENTS.md.
- **Validation**: feature test snapshot of the index response keys.

### Task 1.4: `PayableController::history()` + route
- **Location**: `app/Http/Controllers/Dashboard/PayableController.php`, `routes/api.php`
- **Description**: implement Contract A. Register
  `Route::get('payable/history', [PayableController::class, 'history'])->name('payable.history');`
  above the `apiResource('payable', ...)` line. Eager load `client, designer, separator,
  payments.designer, payments.user`. Validate `from`/`to` as dates, `per_page` as int 1..1000.
- **Dependencies**: 1.3
- **Validation**: feature tests for: default returns only status 5 with fees; `from`/`to` filter;
  pagination meta; 403 without `payable-index`.

### Task 1.5: Docs + deploy
- **Location**: `CLAUDE.md` (statuses/designs section, downstream consumers), commit, push `master`
- **Description**: document `completed_at` semantics and the history endpoint. Push triggers Forge
  deploy which runs `migrate --force` (backfill runs in prod). Confirm in Forge deploy log.
- **Dependencies**: 1.1–1.4
- **Validation**: live `curl` of both endpoints with a valid staff cookie.

## Sprint 2: Streamlit data layer — `mw_api.py`
**Owner**: Opus agent "data" in this repo. Can start immediately against Contract A using a fixture.
**Goal**: one function returns the normalized DataFrame (Contract B), tested offline.
**Demo/Validation**:
- `python3 -m pytest tests/` passes using fixtures only.
- `python3 -c "import mw_api; print(mw_api.load_payables().shape)"` works once the cookie and the
  backend endpoint are live.

### Task 2.1: Fixture for paid payables
- **Location**: `tests/fixtures/paid_payables_sample.json`, reuse `payable_data.json` for open ones
- **Description**: hand-write ~8 Completed rows matching Contract A: mixed PayPal/check, one with no
  payments, one with `completed_at: null`, one separator-only design, one in-house designer, dates
  spanning two months and a year boundary. Include a two-page pagination envelope.
- **Dependencies**: none
- **Validation**: JSON validates; used by 2.3 tests.

### Task 2.2: Fetch functions
- **Location**: `mw_api.py`
- **Description**: `fetch_open_payables(session)` (existing request moved here),
  `fetch_paid_payables(session, from_date=None, to_date=None, include_undated=False)` following
  `links.next` until done (default caller passes `from_date=Jan 1 of current year`; the "all history"
  mode passes `from_date=None, include_undated=True`),
  shared `_headers()` and a `MerchwizardAuthError` raised on 401 with the re-extract-cookie
  instructions from AGENTS.md. Cookie and header constants read from env, same names as today.
- **Dependencies**: Contract A
- **Validation**: unit tests with `requests` mocked (`responses` or `unittest.mock`), including a
  401 path and a two-page path.

### Task 2.3: Normalization
- **Location**: `mw_api.py` — `normalize(open_rows, paid_rows, in_house_keywords, today) -> DataFrame`
- **Description**: produce Contract B. Keep the current column names and dtypes untouched so
  `app.py` keeps working before Sprint 3. `IN_HOUSE_KEYWORDS` stays defined in `app.py` and is passed
  in. Pure function, no Streamlit import, no network.
- **Dependencies**: 2.1
- **Validation**: tests assert every Contract B column, the status mapping, `Days to Pay`,
  `Paid Amount` sum, `Payment Method` precedence, NY-timezone month bucketing, undated-paid handling.

### Task 2.4: `load_payables()` entry point + cache wiring in `app.py`
- **Location**: `mw_api.py`, `app.py` (fetch function only, no UI change)
- **Description**: `load_payables()` composes 2.2 + 2.3. In `app.py`, `fetch_payable_data()` becomes a
  thin `@st.cache_data(ttl=300)` wrapper over `mw_api.load_payables()`. Catch `MerchwizardAuthError`
  and show the cookie instructions instead of a raw traceback.
- **Dependencies**: 2.2, 2.3
- **Validation**: `streamlit run app.py` renders the existing dashboard unchanged against the live
  API (or the fixture via an `MW_FIXTURE_PATH` env var, which also helps the UI agent).

## Sprint 3: Streamlit UI
**Owner**: Opus agent "ui" in this repo, editing `app.py` only. Starts in parallel using
`MW_FIXTURE_PATH` with a hand-built DataFrame matching Contract B; rebases on `mw_api.py` when
Sprint 2 lands.
**Goal**: the four outcomes the user asked for.
**Demo/Validation**: screenshots of each new section against fixture data; numbers cross-checked by
hand against the fixture; existing sections still render.

### Task 3.1: Sidebar filters
- **Description**: `Payment Status` multiselect (default all three), `Paid Date Range` (only applies
  to Paid rows), a `Load all paid history` checkbox (default off: current year only; on: every
  Completed design including undated), keep every existing filter. Replace the `Created Date Range` label with
  `Created Date Range (upload)` so the three dates are not confused.
- **Validation**: filter combinations do not drop rows with NaT dates unexpectedly.

### Task 3.2: Budget Tracker rewrite
- **Description**: freelancer-only, `Budget Year == current year`:
  `YTD Spend = Paid + Outstanding` (committed spend), plus `Paid YTD`, `Outstanding`,
  `Awaiting Approval (not counted)`, `Budget Remaining`, `Expected by Now`. Caption explains that
  spend is attributed to the approval month, not the paid month.
- **Dependencies**: Contract B
- **Validation**: fixture totals match by hand.

### Task 3.3: Monthly Spending chart split
- **Description**: stacked bar by `Approved Month` with series Paid / Outstanding; table gains
  `Paid`, `Outstanding`, `Budget`, `Over/Under`. Rows with no `Approved Month` (Pending) go to an
  "Awaiting approval" footnote count instead of a fake month.

### Task 3.4: Outstanding aging section
- **Description**: counts and dollar totals in buckets `< 30`, `30–59`, `60+` days from
  `Approved Date` (same thresholds as the Merchwizard payable page), plus a table of the 60+ items
  oldest first.

### Task 3.5: Paid history section
- **Description**: table of Paid rows: `Paid Date, Client, Name, Designer, Total, Paid Amount,
  Payment Method, PayPal Batch ID, Paid By, Days to Pay`. Highlight rows where
  `Paid Amount != Total` (fees changed after payment, or no payment record) with a short legend.
  Count of "Paid (date unknown)" items shown separately.

### Task 3.6: Monthly Paid-Out Report by Artist
- **Description**: month picker on `Paid Month`; pivot by `Client` (artist) with
  `# Designs, Submission, Balance, Separation, Total, Paid Amount, Difference`; expandable
  per-client designer breakdown; grand total line; CSV download named
  `paid_out_{YYYY-MM}_by_artist.csv`. Second CSV: the underlying rows for that month (for matching
  against the PayPal export).
- **Validation**: fixture month totals match the per-design table.

### Task 3.7: Existing export
- **Description**: `Export` month picker gets a basis radio: `Created Month` (current behaviour)
  or `Paid Month`. New columns included in the CSV.

### Task 3.8: Docs
- **Description**: update `AGENTS.md` (project structure now includes `mw_api.py` and `tests/`,
  API details list the history endpoint, Data Notes explain `Payment Status` and date fields).

## Sprint 4: Review and ship
**Owner**: Opus agent "reviewer", read-only until findings are agreed.
- Run `pytest`, run the app against the live API with the new cookie, and compare: dashboard
  `Paid` count for last month vs. Merchwizard's payable page count of Completed items; dashboard
  `Paid Amount` vs. the PayPal payments CSV from `POST /api/dashboard/payable/export` for the same
  range.
- Confirm `GET /api/dashboard/payable/` still parses with the pre-change `app.py` (git stash test).
- Report any contract drift between Sprint 1 output and Sprint 2 parsing; fix in the layer that is
  wrong, not by special-casing.
- Push `main` (Streamlit Cloud auto-deploys), update Streamlit Cloud secrets if the cookie changed.

## Decisions taken (2026-11-02)

- Undated legacy paid designs: shown as "Paid (date unknown)", counted in budget by `Budget Year`,
  excluded from the monthly paid-out report, never backfilled from `created_at`.
- Default history window: current year only, sidebar toggle to load everything.
- `approve()` clears `completed_at` (re-approved design is owed again). Confirm with accounting later.

## Agent orchestration

Launch with the Agent tool, `model: "opus"`, three in one message, then the reviewer:

| Agent | Working dir | Writes | Must read first |
| --- | --- | --- | --- |
| backend | `~/Projects/Manhead/merchwizard-backend` | migration, model, controller, resources, routes, CLAUDE.md | its `CLAUDE.md`; `2026_10_09_*approved_at*` migration; this plan's Contract A |
| data | this repo | `mw_api.py`, `tests/`, `requirements-dev.txt`, the fetch function in `app.py` | `app.py`; `payable_data.json`; Contracts A and B |
| ui | this repo | `app.py` (UI sections only), `AGENTS.md` | `app.py`; `transcript.md` (Karina's asks); Contract B |
| reviewer | both | nothing until findings are listed | all of the above plus the three agents' reports |

Conflict rule: `data` owns everything above the `# --- Sidebar Filters ---` line in `app.py`;
`ui` owns everything below it. Each agent commits on its own branch
(`feat/completed-at-history`, `feat/mw-api`, `feat/paid-history-ui`); the reviewer merges
`feat/mw-api` first, then `feat/paid-history-ui`.

Each brief must include: the contract section verbatim, the file list it owns, "do not change
existing response keys / column names", the Python 3.9 constraint, and "report exactly which
validations you ran and which you could not".

## Testing Strategy

- Backend: PHPUnit feature tests per task (the repo only has example tests today, so the backend
  agent sets up `RefreshDatabase` against the `testing` DB defined in `phpunit.xml` / docker).
- Data layer: pytest with fixtures and mocked HTTP; zero network in tests.
- UI: manual against `MW_FIXTURE_PATH`, then live; screenshots saved to `app_screenshot.png`
  (replace the stale one).
- Integration: reviewer's reconciliation against Merchwizard counts and the PayPal CSV.

## Potential Risks & Gotchas

- **Cookie expiry blocks everything live.** Sprints 1–3 are designed to proceed on fixtures; only
  1.5 and Sprint 4 need the cookie. Also check whether Streamlit Cloud needs the new cookie now.
- **Legacy Completed designs have no paid date.** Backfill leaves them NULL by design. The budget
  tracker still counts them via `Budget Year` (approval or creation year), but the monthly paid-out
  report cannot place them. Expect a large "date unknown" bucket for pre-2026 history.
- **Payment records are incomplete.** Checks and cash were often marked Complete without a
  `payments` row, so `Paid Amount` will be 0 for many paid designs. The report shows both `Total`
  (fees) and `Paid Amount` (recorded) and never silently substitutes one for the other.
- **Fee totals vs payouts differ** when a fee was edited after payment or only one of three fees was
  paid. Hence the `Difference` column rather than a single number.
- **Volume.** Years of Completed designs may be thousands of rows. Pagination at 500/page and a
  default `from` of Jan 1 of the **current year** in the dashboard (sidebar toggle "Load all paid
  history" fetches everything, cached separately) keeps the 5-minute cache refresh fast. Undated
  paid rows (`completed_at` NULL) are only returned when `undated=1`, which the "all history" mode
  sends; the default mode leaves them out and the Budget Tracker caption says so.
- **Backfill in production** runs inside the Forge deploy via `migrate --force`. The guarded,
  re-runnable migration shape exists precisely because the `approved_at` backfill once failed
  part-way. Keep that shape.
- **Response-shape compatibility.** `DesignResource` is used by the web app too. Only add keys;
  `payments` must be `whenLoaded` so list endpoints do not N+1 or change.
- **Timezones.** UTC timestamps near midnight land in the wrong month without a NY conversion.
- **`approve()` clearing `completed_at`** is a judgement call: re-approving a paid design means it is
  owed again. Confirm with Karina/accounting; it is a one-line change either way.

## Rollback Plan

- Backend: `php artisan migrate:rollback --step=1` drops `completed_at`; revert the commit removes
  the route and resource keys. The index response is additive-only, so the old dashboard keeps
  working at every point.
- Streamlit: revert the merge commit on `main`; Streamlit Cloud redeploys the previous version. The
  previous `app.py` still works against the new backend because no existing keys change.
