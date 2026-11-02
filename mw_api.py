"""Merchwizard payables data layer.

Fetches open payables (``GET /api/dashboard/payable/``) and paid history
(``GET /api/dashboard/payable/history``) and normalizes both into the single
DataFrame described as Contract B in past-payables-integration-plan.md.

No Streamlit import here: app.py wraps ``load_payables()`` in its own cache.
Python 3.9 compatible.
"""
import json
import os
import warnings
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlsplit, urlunsplit

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    from backports.zoneinfo import ZoneInfo  # type: ignore

import pandas as pd
import requests

# === CONSTANTS ===

API_URL = "https://api.merchwizard.com/api/dashboard/payable/"
HISTORY_URL = "https://api.merchwizard.com/api/dashboard/payable/history"
PER_PAGE = 500
MAX_PAGES = 500  # safety stop if links.next ever loops
REQUEST_TIMEOUT = 60

NY_TZ = ZoneInfo("America/New_York")

PAYMENT_STATUS_LABELS = {0: "Awaiting Approval", 2: "Outstanding", 5: "Paid"}
PAYMENT_METHOD_LABELS = {1: "PayPal", 2: "Check", 3: "Cash", 4: "Bank Transfer"}
PAYPAL_METHOD = 1
NOT_RECORDED = "Not Recorded"
PAYMENT_CREATED_FORMAT = "%m-%d-%Y %H:%M:%S"

AUTH_INSTRUCTIONS = (
    "Merchwizard returned Unauthenticated (401): the dashboard's credentials are missing, "
    "expired or revoked. Preferred: set MW_API_TOKEN to a token issued on the API server with "
    "`php artisan app:dashboard-token <staff email>` (Forge > site > Commands), in .env and in "
    "the Streamlit Cloud secrets. Fallback: MW_COOKIE_NAME / MW_COOKIE_VALUE from a logged-in "
    "browser session's `remember_web_*` cookie, which dies whenever that user logs out or opens "
    "a login link."
)

# Existing columns, in the order the original app.py produced them. Do not rename.
BASE_COLUMNS = [
    "ID",
    "Name",
    "Client",
    "Designer",
    "In-House",
    "Submission",
    "Balance",
    "Separation",
    "Submission Invoice",
    "Balance Invoice",
    "Separation Invoice",
    "Status",
    "Created",
    "Updated",
]

NEW_COLUMNS = [
    "Payment Status",
    "Approved Date",
    "Approved Month",
    "Paid Date",
    "Paid Month",
    "Days Outstanding",
    "Days to Pay",
    "Paid Amount",
    "Payment Method",
    "PayPal Batch ID",
    "Paid By",
    "Budget Year",
    "Separator Only",
]


class MerchwizardAuthError(RuntimeError):
    """Raised when the Merchwizard API rejects the token or cookie (HTTP 401/419)."""

    def __init__(self, message: str = AUTH_INSTRUCTIONS):
        super().__init__(message)


# === HTTP ===


def _headers() -> Dict[str, str]:
    """Auth headers: a Sanctum bearer token when MW_API_TOKEN is set, else the cookie.

    The bearer request deliberately carries no Referer/Origin so Sanctum takes its token
    path instead of the stateful (browser-session) path.
    """
    token = (os.getenv("MW_API_TOKEN") or "").strip()
    if token:
        return {
            "Authorization": "Bearer {}".format(token),
            "Accept": "application/json",
        }
    cookie_name = os.getenv("MW_COOKIE_NAME")
    cookie_value = os.getenv("MW_COOKIE_VALUE")
    if not cookie_name or not cookie_value:
        raise MerchwizardAuthError(
            "Missing MW_API_TOKEN (or MW_COOKIE_NAME / MW_COOKIE_VALUE). " + AUTH_INSTRUCTIONS
        )
    return {
        "Cookie": "{}={}".format(cookie_name, cookie_value),
        "Accept": "application/json",
        "Referer": "https://merchwizard.com/dashboard/payable/",
        "Origin": "https://merchwizard.com",
    }


def _get_json(session: requests.Session, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    resp = session.get(url, headers=_headers(), params=params, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (401, 419):
        raise MerchwizardAuthError()
    resp.raise_for_status()
    return resp.json()


def _split_next(next_url: str, base_params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Return (url without query, params) for a pagination link.

    Laravel's ``links.next`` does not always carry the original filters, so the
    original params are re-sent and the link's own query (``page``) overrides them.
    """
    parts = urlsplit(next_url)
    params = dict(base_params)
    params.update(dict(parse_qsl(parts.query)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")), params


def _fetch_all_pages(session: requests.Session, url: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []  # type: List[Dict[str, Any]]
    seen = set()
    current_url, current_params = url, dict(params)
    for _ in range(MAX_PAGES):
        payload = _get_json(session, current_url, current_params)
        if isinstance(payload, list):  # unwrapped collection
            rows.extend(payload)
            break
        rows.extend(payload.get("data") or [])
        next_url = (payload.get("links") or {}).get("next")
        if not next_url or next_url in seen:
            break
        seen.add(next_url)
        current_url, current_params = _split_next(next_url, params)
    return rows


def fetch_open_payables(session: requests.Session) -> List[Dict[str, Any]]:
    """Designs with status Approved (2) or Pending (0)."""
    return _fetch_all_pages(session, API_URL, {})


def fetch_paid_payables(
    session: requests.Session,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    include_undated: bool = False,
) -> List[Dict[str, Any]]:
    """Completed (5) designs with payments, following ``links.next`` to the end."""
    params = {"per_page": PER_PAGE, "undated": 1 if include_undated else 0}  # type: Dict[str, Any]
    if from_date is not None:
        params["from"] = from_date.isoformat()
    if to_date is not None:
        params["to"] = to_date.isoformat()
    return _fetch_all_pages(session, HISTORY_URL, params)


# === NORMALIZATION HELPERS ===


def _to_ny(value: Any) -> Optional[datetime]:
    """Parse an API timestamp (ISO-8601, UTC) and convert it to New York time."""
    if value is None or value == "":
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    dt = ts.to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(NY_TZ)


def _ny_date(value: Any) -> Any:
    dt = _to_ny(value)
    return dt.date() if dt is not None else pd.NaT


def _month(d: Any) -> Optional[str]:
    if d is None or pd.isna(d):
        return None
    return "{:04d}-{:02d}".format(d.year, d.month)


def _payment_sort_key(payment: Dict[str, Any]) -> Tuple[datetime, int]:
    raw = payment.get("createdAt") or ""
    try:
        created = datetime.strptime(raw, PAYMENT_CREATED_FORMAT)
    except (TypeError, ValueError):
        created = datetime.min
    try:
        pid = int(payment.get("id") or 0)
    except (TypeError, ValueError):
        pid = 0
    return created, pid


def _method_label(code: Any) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "Unknown"
    return PAYMENT_METHOD_LABELS.get(code, "Unknown ({})".format(code))


def summarize_payments(payments: Optional[Iterable[Dict[str, Any]]]) -> Dict[str, Any]:
    """Paid Amount / Payment Method / PayPal Batch ID / Paid By from a payments list."""
    payments = sorted(payments or [], key=_payment_sort_key)
    total = 0.0
    for p in payments:
        try:
            total += float(p.get("amount") or 0)
        except (TypeError, ValueError):
            pass
    if not payments:
        return {
            "Paid Amount": 0.0,
            "Payment Method": NOT_RECORDED,
            "PayPal Batch ID": "",
            "Paid By": "",
        }
    latest = payments[-1]
    paypal = [p for p in payments if _safe_int(p.get("paymentMethod")) == PAYPAL_METHOD]
    return {
        "Paid Amount": round(total, 2),
        "Payment Method": _method_label(latest.get("paymentMethod")),
        "PayPal Batch ID": str(paypal[-1].get("paymentId") or "") if paypal else "",
        "Paid By": str(latest.get("paidByName") or ""),
    }


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _name_of(obj: Any) -> Optional[str]:
    """Designer/client/separator may be an object ({name, title}) or a plain string."""
    if not obj:
        return None
    if isinstance(obj, dict):
        return obj.get("name") or obj.get("title")
    return str(obj)


def _base_row(item: Dict[str, Any], in_house_keywords: Sequence[str]) -> Dict[str, Any]:
    designer_name = _name_of(item.get("designer"))
    separator_only = False
    if not designer_name:
        separator_name = _name_of(item.get("separator"))
        if separator_name:
            designer_name = separator_name
            separator_only = True

    is_in_house = False
    if designer_name:
        is_in_house = any(kw in designer_name.lower() for kw in in_house_keywords)

    client = item.get("client")
    if isinstance(client, dict):
        client_name = client.get("title") or client.get("name")
    else:
        client_name = client or None

    row = {
        "ID": item["id"],
        "Name": item["name"],
        "Client": client_name,
        "Designer": designer_name,
        "In-House": is_in_house,
        "Submission": item.get("submission_fee", 0),
        "Balance": item.get("balance_fee", 0),
        "Separation": item.get("separation_fee", 0),
        "Submission Invoice": item.get("submission_invoice", ""),
        "Balance Invoice": item.get("balance_invoice", ""),
        "Separation Invoice": item.get("separation_invoice", ""),
        "Status": item.get("status"),
        "Created": item.get("created_at", ""),
        "Updated": item.get("updated_at", ""),
    }
    row["Separator Only"] = separator_only
    return row


def _extra_fields(item: Dict[str, Any], today: date) -> Dict[str, Any]:
    status = _safe_int(item.get("status"))
    approved = _ny_date(item.get("approved_at"))
    paid = _ny_date(item.get("completed_at")) if status == 5 else pd.NaT

    days_outstanding = float("nan")
    if status == 2 and not pd.isna(approved):
        days_outstanding = float((today - approved).days)

    days_to_pay = float("nan")
    if status == 5 and not pd.isna(approved) and not pd.isna(paid):
        days_to_pay = float((paid - approved).days)

    extra = {
        "Payment Status": PAYMENT_STATUS_LABELS.get(status, "Unknown"),
        "Approved Date": approved,
        "Approved Month": _month(approved),
        "Paid Date": paid,
        "Paid Month": _month(paid),
        "Days Outstanding": days_outstanding,
        "Days to Pay": days_to_pay,
    }
    extra.update(summarize_payments(item.get("payments")))
    return extra


# === NORMALIZE ===


def normalize(
    open_rows: Optional[Iterable[Dict[str, Any]]],
    paid_rows: Optional[Iterable[Dict[str, Any]]],
    in_house_keywords: Sequence[str],
    today: Optional[date] = None,
) -> pd.DataFrame:
    """Combine open + paid API rows into the Contract B DataFrame (pure function)."""
    if today is None:
        today = datetime.now(NY_TZ).date()
    keywords = [kw.lower() for kw in (in_house_keywords or [])]

    # Paid rows go last so that a design completed between the two requests
    # is kept once, as Paid.
    items = list(open_rows or []) + list(paid_rows or [])
    rows = []
    seen_index = {}  # type: Dict[Any, int]
    for item in items:
        row = _base_row(item, keywords)
        row.update(_extra_fields(item, today))
        if row["ID"] in seen_index:
            rows[seen_index[row["ID"]]] = row
        else:
            seen_index[row["ID"]] = len(rows)
            rows.append(row)

    df = pd.DataFrame(rows, columns=BASE_COLUMNS + NEW_COLUMNS)

    # Existing derived columns, computed exactly as the original app.py did.
    df["Created"] = pd.to_datetime(df["Created"])
    df["Created Date"] = df["Created"].dt.date
    with warnings.catch_warnings():
        # to_period drops the UTC tz (original behaviour); silence the notice.
        warnings.simplefilter("ignore", UserWarning)
        df["Month"] = df["Created"].dt.to_period("M").astype(str)
    df["Updated"] = pd.to_datetime(df["Updated"]).dt.date
    df["Total"] = df["Submission"] + df["Balance"] + df["Separation"]

    # New columns: explicit dtypes so empty and non-empty frames match.
    df["Approved Date"] = df["Approved Date"].astype(object)
    df["Paid Date"] = df["Paid Date"].astype(object)
    df["Days Outstanding"] = df["Days Outstanding"].astype(float)
    df["Days to Pay"] = df["Days to Pay"].astype(float)
    df["Paid Amount"] = df["Paid Amount"].astype(float)
    df["In-House"] = df["In-House"].astype(bool)
    df["Separator Only"] = df["Separator Only"].astype(bool)

    budget_year = [
        a.year if not pd.isna(a) else (c.year if not pd.isna(c) else today.year)
        for a, c in zip(df["Approved Date"], df["Created Date"])
    ]
    df["Budget Year"] = pd.Series(budget_year, index=df.index, dtype="int64")

    ordered = (
        BASE_COLUMNS[:12]
        + ["Created", "Updated", "Created Date", "Month", "Total"]
        + NEW_COLUMNS
    )
    return df[ordered]


# === ENTRY POINT ===


def _load_fixture(path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not os.path.isabs(path) and not os.path.exists(path):
        candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
        if os.path.exists(candidate):
            path = candidate
    with open(path) as fh:
        payload = json.load(fh)
    return list(payload.get("open") or []), list(payload.get("paid") or [])


def _filter_paid_window(
    rows: Iterable[Dict[str, Any]],
    from_date: Optional[date],
    to_date: Optional[date],
    include_undated: bool,
) -> List[Dict[str, Any]]:
    """Mimic the history endpoint's from/to/undated filter (completed_at, UTC days)."""
    kept = []
    for row in rows:
        raw = row.get("completed_at")
        if not raw:
            if include_undated:
                kept.append(row)
            continue
        ts = pd.Timestamp(raw)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC")
        d = ts.date()
        if from_date is not None and d < from_date:
            continue
        if to_date is not None and d > to_date:
            continue
        kept.append(row)
    return kept


def _drop_prior_year_paid(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Drop Paid rows whose New York paid date falls before ``year``.

    The history endpoint filters ``from`` on UTC days, so a design completed on
    Dec 31 after 19:00 New York time (Jan 1 UTC) is fetched by the current-year
    window but belongs to the previous year's Paid Month. Undated rows are kept.
    """
    paid_years = pd.to_datetime(df["Paid Date"], errors="coerce").dt.year
    prior = (df["Payment Status"] == "Paid") & (paid_years < year)
    return df[~prior].reset_index(drop=True)


def load_payables(
    load_all_history: bool = False,
    today: Optional[date] = None,
    in_house_keywords: Optional[Sequence[str]] = None,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Fetch open + paid payables and return the Contract B DataFrame.

    Default window: paid designs completed since Jan 1 of the current New York
    year, undated ones excluded. The endpoint filters on UTC days, so paid rows
    whose New York Paid Date is still in the previous year (completed Dec 31
    evening NY) are dropped after normalizing. ``load_all_history=True``
    fetches every Completed design, undated included, and drops nothing.

    If ``MW_FIXTURE_PATH`` is set, rows come from that JSON file
    (``{"open": [...], "paid": [...]}``) instead of the network.
    """
    if today is None:
        today = datetime.now(NY_TZ).date()
    from_date = None if load_all_history else date(today.year, 1, 1)
    include_undated = bool(load_all_history)
    keywords = list(in_house_keywords or [])

    fixture_path = os.getenv("MW_FIXTURE_PATH")
    if fixture_path:
        open_rows, paid_rows = _load_fixture(fixture_path)
        paid_rows = _filter_paid_window(paid_rows, from_date, None, include_undated)
    else:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:  # pragma: no cover
            pass

        own_session = session is None
        session = session or requests.Session()
        try:
            open_rows = fetch_open_payables(session)
            paid_rows = fetch_paid_payables(
                session, from_date=from_date, include_undated=include_undated
            )
        finally:
            if own_session:
                session.close()

    df = normalize(open_rows, paid_rows, keywords, today)
    if not load_all_history:
        df = _drop_prior_year_paid(df, today.year)
    return df
