# Merchwizard Payable Dashboard

## Overview
Streamlit dashboard that pulls freelancer payable data from the Merchwizard API and provides budget tracking, monthly spending breakdowns, and filterable reports for Manhead's creative team.

## Status
Live

## GitHub
https://github.com/eugeneleychenko/manhead-merchwizard-payable-dashboard

## Deployment
Streamlit Cloud

## Production URL(s)
https://manhead-merchwizard-payable-dashboard.streamlit.app/

## How to Deploy
Push to `main` branch — Streamlit Cloud auto-deploys.

## Tech Stack
Python / Streamlit / Pandas

## Env Vars
- `MW_API_TOKEN` — preferred. Sanctum bearer token issued on the API server with
  `php artisan app:dashboard-token <staff email>` (Forge > site > Commands). Does not expire;
  re-running the command revokes the previous `payable-dashboard` token.
- `MW_COOKIE_NAME` / `MW_COOKIE_VALUE` — fallback only: a staff user's `remember_web_*` cookie.
  It is invalidated whenever that user logs out or opens a login link, so expect it to break.

If the API returns `Unauthenticated`, issue a new token and update both `.env` and the Streamlit
Cloud secrets. In local dev, `MW_FIXTURE_PATH=tests/fixtures/ui_fixture.json` runs the app offline.

## Build / Dev Commands
```bash
# Install dependencies
python3 -m pip install streamlit pandas requests python-dotenv

# Run the app
python3 -m streamlit run app.py --server.port 8501
```

## Related Manhead Universe Doc
<!-- Link to project doc in Manhead Universe if one exists -->

## Old Iterations
- `~/Downloads/Manhead-Karina-Request/` → moved to `zOLD/Manhead-Karina-Request`

---

<coding_guidelines>

## Project Structure

```
.
├── app.py                              # Streamlit application (UI; fetch is a thin cached wrapper)
├── mw_api.py                           # Merchwizard data layer: fetch open + paid payables, normalize to one DataFrame
├── tests/                              # pytest suite for mw_api.py (fixtures only, no network)
│   └── fixtures/                       # Sample API responses (e.g. paid_payables_sample.json)
├── .env                                # Auth credentials (MW_API_TOKEN, or cookie fallback)
├── transcript.md                       # Original requirements from Karina huddle
├── Monthly Art Budgets - January 2026.csv  # Reference spreadsheet
└── payable_data.json                   # Cached API snapshot of open payables (45 records)
```

## API Details

- **Open payables:** `GET https://api.merchwizard.com/api/dashboard/payable/` — designs with status Pending (0) or Approved (2).
- **Paid history:** `GET https://api.merchwizard.com/api/dashboard/payable/history` — Completed (5) designs with at least one fee and a designer or separator, ordered by `completed_at DESC NULLS LAST`.
  - Query params (all optional): `from`, `to` (ISO dates, inclusive, filter on `completed_at`); `undated=1` to also return Completed designs with `completed_at` NULL (appended after dated rows); `per_page` (default 500, max 1000). Standard Laravel pagination envelope (`data`, `links`, `meta`); follow `links.next`.
  - Rows are `DesignResource` plus `completed_at` (ISO-8601 or null) and `payments[]`: `{id, designerId, paymentId, paymentMethod, paymentType, payeeName, paidByName, status, invoice, amount, createdAt}`. `paymentMethod`: 1 PayPal, 2 Check, 3 Cash, 4 BankTransfer. `paymentType`: 1 Submission, 2 Balance, 3 Separation. `amount` is in dollars; `createdAt` is `m-d-Y H:i:s`.
- **Auth:** Single `remember_web_*` cookie sent via `Cookie` header. Also requires `Referer: https://merchwizard.com/dashboard/payable/` and `Origin: https://merchwizard.com` headers. A 401 means the cookie expired.
- **Response:** JSON with `data` array containing design objects (id, name, client, designer, submission_fee, balance_fee, separation_fee, invoices, status, approved_at, completed_at, timestamps).

## Configuration

Key constants in `app.py`:

- `ANNUAL_BUDGET` (default `250_000`) — yearly freelancer budget cap.
- `MONTHLY_BUDGET` — auto-calculated as `ANNUAL_BUDGET / 12`.
- `IN_HOUSE_KEYWORDS` — lowercase designer name substrings that flag a design as in-house (not counted toward freelancer budget). Update this list as in-house staff changes.

## Coding Style

- Python 3.9 compatible.
- Use `snake_case` for variables and functions.
- Streamlit sections are separated by `# === SECTION NAME ===` comments.
- Keep the app as a single `app.py` file unless complexity warrants splitting.

## Data Notes

- Data is cached for 5 minutes (`@st.cache_data(ttl=300)`). Use the "Refresh Data" button to force a re-fetch.
- The `In-House` boolean column is derived from designer name matching, not from a Merchwizard field. Accuracy depends on keeping `IN_HOUSE_KEYWORDS` current.
- `Payment Status` maps the Merchwizard status: 0 → `Awaiting Approval`, 2 → `Outstanding`, 5 → `Paid`. Budget spend = Paid + Outstanding; Awaiting Approval is shown but never counted.
- Three dates, three meanings:
  - `Created Date` / `Month` — when the design was uploaded (sidebar: "Created Date Range (upload)", export basis "Created Month").
  - `Approved Date` / `Approved Month` — when it became payable. Budget and Monthly Spending attribute spend to the approval month; `Budget Year` is the approval year, falling back to the upload year.
  - `Paid Date` / `Paid Month` — `completed_at`, when accounting clicked Complete. Drives Paid History, the Monthly Paid-Out Report by Artist and the "Paid Month" export basis. The sidebar "Paid Date Range" only narrows Paid rows.
  - Months are bucketed in America/New_York (API timestamps are UTC).
- `Paid Amount` is the sum of recorded `payments`; it is 0 when none were recorded (common for checks/cash). It is never substituted for `Total`; the UI highlights rows where they differ and the by-artist report shows a `Difference` column.
- Undated paid designs (`completed_at` NULL, mostly legacy) show as "Paid (date unknown)". They count toward the budget via `Budget Year`, are excluded from the monthly paid-out report, and are never backfilled from `created_at`.
- Default history window is the current year only (New York time: the endpoint filters `from` on UTC days, so `mw_api.load_payables` drops paid rows whose New York Paid Date is still last year). The sidebar "Load all paid history" checkbox fetches every Completed design, including undated ones (cached separately).

</coding_guidelines>
