# Repository Guidelines

## Project Overview

Streamlit dashboard that pulls freelancer payable data from the Merchwizard API and provides budget tracking, monthly spending breakdowns, and filterable reports for Manhead's creative team.

## Project Structure

```
.
├── app.py                              # Streamlit application (single-file app)
├── .env                                # Auth credentials (MW_COOKIE_NAME, MW_COOKIE_VALUE)
├── transcript.md                       # Original requirements from Karina huddle
├── Monthly Art Budgets - January 2026.csv  # Reference spreadsheet
└── payable_data.json                   # Cached API snapshot (45 records)
```

## Setup & Running

```bash
# Install dependencies
python3 -m pip install streamlit pandas requests python-dotenv

# Run the app
python3 -m streamlit run app.py --server.port 8501
```

The `.env` file must contain `MW_COOKIE_NAME` and `MW_COOKIE_VALUE` for API auth. The `remember_web` cookie from Merchwizard is valid for approximately one year. If the API returns `Unauthenticated`, re-extract the cookie from a logged-in browser session.

## API Details

- **Endpoint:** `GET https://api.merchwizard.com/api/dashboard/payable/`
- **Auth:** Single `remember_web_*` cookie sent via `Cookie` header. Also requires `Referer: https://merchwizard.com/dashboard/payable/` and `Origin: https://merchwizard.com` headers.
- **Response:** JSON with `data` array containing design objects (id, name, client, designer, submission_fee, balance_fee, separation_fee, invoices, status, timestamps).

## Configuration

Key constants in `app.py`:

- `ANNUAL_BUDGET` (default `250_000`) -- yearly freelancer budget cap.
- `MONTHLY_BUDGET` -- auto-calculated as `ANNUAL_BUDGET / 12`.
- `IN_HOUSE_KEYWORDS` -- lowercase designer name substrings that flag a design as in-house (not counted toward freelancer budget). Update this list as in-house staff changes.

## Coding Style

- Python 3.9 compatible.
- Use `snake_case` for variables and functions.
- Streamlit sections are separated by `# === SECTION NAME ===` comments.
- Keep the app as a single `app.py` file unless complexity warrants splitting.

## Data Notes

- Data is cached for 5 minutes (`@st.cache_data(ttl=300)`). Use the "Refresh Data" button to force a re-fetch.
- The `In-House` boolean column is derived from designer name matching, not from a Merchwizard field. Accuracy depends on keeping `IN_HOUSE_KEYWORDS` current.
