"""Offline tests for mw_api (Contract A parsing, Contract B output). No network."""
import json
import math
import os
import sys
from datetime import date
from unittest import mock

import pandas as pd
import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, ROOT)

import mw_api  # noqa: E402

TODAY = date(2026, 11, 2)
KEYWORDS = ["manhead", "mallory strong", "andrew chavez", "ali cook", "maria fulan"]

CONTRACT_B_COLUMNS = [
    # existing
    "ID", "Name", "Client", "Designer", "In-House", "Submission", "Balance", "Separation",
    "Submission Invoice", "Balance Invoice", "Separation Invoice", "Status", "Created",
    "Created Date", "Month", "Updated", "Total",
    # new
    "Payment Status", "Approved Date", "Approved Month", "Paid Date", "Paid Month",
    "Days Outstanding", "Days to Pay", "Paid Amount", "Payment Method", "PayPal Batch ID",
    "Paid By", "Budget Year", "Separator Only",
]


def _load(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def open_rows():
    return _load("open_payables_sample.json")["data"]


@pytest.fixture(scope="module")
def paid_pages():
    return _load("paid_payables_sample.json")["pages"]


@pytest.fixture(scope="module")
def paid_rows(paid_pages):
    return [row for page in paid_pages for row in page["data"]]


@pytest.fixture(scope="module")
def df(open_rows, paid_rows):
    return mw_api.normalize(open_rows, paid_rows, KEYWORDS, TODAY)


def _row(df, design_id):
    match = df[df["ID"] == design_id]
    assert len(match) == 1, design_id
    return match.iloc[0]


def _legacy_fetch(items):
    """The pre-mw_api app.py transformation, kept verbatim to pin existing columns."""
    rows = []
    for item in items:
        designer_name = item["designer"]["name"] if item.get("designer") else None
        is_in_house = False
        if designer_name:
            is_in_house = any(kw in designer_name.lower() for kw in KEYWORDS)
        rows.append(
            {
                "ID": item["id"],
                "Name": item["name"],
                "Client": item["client"]["title"] if item.get("client") else None,
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
        )
    df = pd.DataFrame(rows)
    df["Created"] = pd.to_datetime(df["Created"])
    df["Created Date"] = df["Created"].dt.date
    df["Month"] = df["Created"].dt.to_period("M").astype(str)
    df["Updated"] = pd.to_datetime(df["Updated"]).dt.date
    df["Total"] = df["Submission"] + df["Balance"] + df["Separation"]
    return df


# === CONTRACT B SHAPE ===


def test_every_contract_b_column_present(df):
    assert set(CONTRACT_B_COLUMNS) <= set(df.columns)
    assert len(df.columns) == len(CONTRACT_B_COLUMNS)


@pytest.mark.filterwarnings("ignore:Converting to PeriodArray")
def test_existing_columns_match_legacy_app(open_rows, df):
    legacy = _legacy_fetch(open_rows)
    new = df[df["Status"] != 5].reset_index(drop=True)
    assert list(new.columns[: len(legacy.columns)]) == list(legacy.columns)
    for col in legacy.columns:
        assert new[col].dtype == legacy[col].dtype, col
        if col == "Designer":
            continue  # separator-only rows now carry the separator name (checked below)
        pd.testing.assert_series_equal(new[col], legacy[col], check_names=True)
    # Designer only differs where the legacy code had None.
    diff = new["Designer"].fillna("<none>") != legacy["Designer"].fillna("<none>")
    assert legacy.loc[diff, "Designer"].isna().all()
    assert new.loc[diff, "Separator Only"].all()


def test_new_column_dtypes(df):
    assert df["Payment Status"].map(type).eq(str).all()
    assert df["Paid Amount"].dtype == float
    assert df["Days Outstanding"].dtype == float
    assert df["Days to Pay"].dtype == float
    assert str(df["Budget Year"].dtype) == "int64"
    assert df["Separator Only"].dtype == bool
    assert all(isinstance(v, date) or pd.isna(v) for v in df["Approved Date"])
    assert all(isinstance(v, date) or pd.isna(v) for v in df["Paid Date"])


# === STATUS MAPPING ===


def test_status_mapping(df):
    mapping = {0: "Awaiting Approval", 2: "Outstanding", 5: "Paid"}
    for status, label in mapping.items():
        subset = df[df["Status"] == status]
        assert not subset.empty, status
        assert (subset["Payment Status"] == label).all()
    assert set(df["Payment Status"]) == set(mapping.values())


def test_awaiting_approval_has_no_approved_fields(df):
    pending = df[df["Status"] == 0]
    assert pending["Approved Date"].isna().all()
    assert pending["Approved Month"].isna().all()
    assert pending["Days Outstanding"].isna().all()


# === DATES ===


def test_days_to_pay(df):
    row = _row(df, 300001)
    assert row["Approved Date"] == date(2026, 10, 1)
    assert row["Paid Date"] == date(2026, 10, 15)
    assert row["Days to Pay"] == 14
    # Only Paid rows get Days to Pay.
    assert df.loc[df["Status"] != 5, "Days to Pay"].isna().all()


def test_days_outstanding_only_on_outstanding(df):
    outstanding = df[df["Status"] == 2]
    assert outstanding["Days Outstanding"].notna().all()
    recent = outstanding[outstanding["Approved Date"] == date(2026, 10, 20)]
    assert not recent.empty
    assert (recent["Days Outstanding"] == 13).all()
    assert df.loc[df["Status"] != 2, "Days Outstanding"].isna().all()


def test_ny_month_bucketing_near_midnight(df):
    # completed_at 2026-11-01T03:30Z is 2026-10-31 23:30 in New York.
    row = _row(df, 300005)
    assert row["Paid Date"] == date(2026, 10, 31)
    assert row["Paid Month"] == "2026-10"


def test_ny_year_boundary(df):
    # completed_at 2026-01-01T02:15Z is 2025-12-31 21:15 in New York.
    row = _row(df, 300006)
    assert row["Paid Date"] == date(2025, 12, 31)
    assert row["Paid Month"] == "2025-12"
    assert row["Approved Month"] == "2025-12"
    assert row["Budget Year"] == 2025


def test_undated_paid(df):
    row = _row(df, 300008)
    assert row["Payment Status"] == "Paid"
    assert pd.isna(row["Paid Date"])
    assert row["Paid Month"] is None or pd.isna(row["Paid Month"])
    assert math.isnan(row["Days to Pay"])
    # No approved_at either: Budget Year falls back to Created Date.
    assert row["Budget Year"] == 2024
    assert row["Paid Amount"] == 300.0
    assert row["Payment Method"] == "Cash"


# === PAYMENTS ===


def test_paid_amount_sum(df):
    assert _row(df, 300001)["Paid Amount"] == 300.0
    assert _row(df, 300003)["Paid Amount"] == 200.0  # fees 250, only 200 recorded
    assert df.loc[df["Status"] != 5, "Paid Amount"].eq(0.0).all()


def test_payment_method_precedence(df):
    # Two PayPal payments: latest batch id and payer win.
    row = _row(df, 300001)
    assert row["Payment Method"] == "PayPal"
    assert row["PayPal Batch ID"] == "BATCH-B2"
    assert row["Paid By"] == "Eugene L"
    # Older PayPal + newer check (listed first): method from newest, batch from the PayPal one.
    row = _row(df, 300003)
    assert row["Payment Method"] == "Check"
    assert row["PayPal Batch ID"] == "BATCH-C3"
    assert row["Paid By"] == "Karina Ruiz"
    # Check with null paymentId.
    row = _row(df, 300002)
    assert row["Payment Method"] == "Check"
    assert row["PayPal Batch ID"] == ""
    assert _row(df, 300006)["Payment Method"] == "Bank Transfer"


def test_no_payments_not_recorded(df):
    row = _row(df, 300004)
    assert row["Payment Status"] == "Paid"
    assert row["Paid Amount"] == 0.0
    assert row["Payment Method"] == "Not Recorded"
    assert row["PayPal Batch ID"] == ""
    assert row["Paid By"] == ""


def test_missing_amount_counts_as_zero():
    summary = mw_api.summarize_payments(
        [{"id": 1, "paymentMethod": 1, "paymentId": "X", "createdAt": "01-01-2026 00:00:00"}]
    )
    assert summary["Paid Amount"] == 0.0
    assert summary["Payment Method"] == "PayPal"


# === DESIGNERS ===


def test_separator_only_and_in_house(df):
    row = _row(df, 300007)
    assert row["Designer"] == "Alex Wexler"
    assert bool(row["Separator Only"]) is True
    assert bool(_row(df, 300001)["Separator Only"]) is False
    assert bool(_row(df, 300004)["In-House"]) is True
    assert bool(_row(df, 300001)["In-House"]) is False
    assert df["Designer"].notna().all()


def test_duplicate_id_keeps_paid_row(open_rows, paid_rows):
    dup = dict(open_rows[0])
    paid = dict(paid_rows[0], id=dup["id"])
    out = mw_api.normalize([dup], [paid], KEYWORDS, TODAY)
    assert len(out) == 1
    assert out.iloc[0]["Payment Status"] == "Paid"


def test_empty_inputs_have_all_columns():
    out = mw_api.normalize([], [], KEYWORDS, TODAY)
    assert out.empty
    assert set(CONTRACT_B_COLUMNS) <= set(out.columns)


# === HTTP ===


def _response(status, payload=None):
    resp = mock.Mock(spec=requests.Response)
    resp.status_code = status
    resp.json.return_value = payload
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(str(status))
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.fixture
def cookie_env(monkeypatch):
    monkeypatch.setenv("MW_COOKIE_NAME", "remember_web_test")
    monkeypatch.setenv("MW_COOKIE_VALUE", "abc")
    monkeypatch.delenv("MW_FIXTURE_PATH", raising=False)


def test_headers(cookie_env):
    headers = mw_api._headers()
    assert headers["Cookie"] == "remember_web_test=abc"
    assert headers["Referer"] == "https://merchwizard.com/dashboard/payable/"
    assert headers["Origin"] == "https://merchwizard.com"


def test_401_raises_auth_error(cookie_env):
    session = mock.Mock()
    session.get.return_value = _response(401, {"message": "Unauthenticated."})
    with pytest.raises(mw_api.MerchwizardAuthError) as exc:
        mw_api.fetch_open_payables(session)
    assert "re-extract" in str(exc.value)
    assert "MW_COOKIE_VALUE" in str(exc.value)
    assert "remember_web" in str(exc.value)
    session.get.return_value = _response(401, {"message": "Unauthenticated."})
    with pytest.raises(mw_api.MerchwizardAuthError):
        mw_api.fetch_paid_payables(session)


def test_missing_cookie_raises_auth_error(monkeypatch):
    monkeypatch.delenv("MW_COOKIE_NAME", raising=False)
    monkeypatch.delenv("MW_COOKIE_VALUE", raising=False)
    with pytest.raises(mw_api.MerchwizardAuthError):
        mw_api._headers()


def test_other_http_errors_propagate(cookie_env):
    session = mock.Mock()
    session.get.return_value = _response(500)
    with pytest.raises(requests.HTTPError):
        mw_api.fetch_open_payables(session)


def test_two_page_pagination_followed(cookie_env, paid_pages, paid_rows):
    session = mock.Mock()
    session.get.side_effect = [_response(200, paid_pages[0]), _response(200, paid_pages[1])]
    rows = mw_api.fetch_paid_payables(session, from_date=date(2026, 1, 1))
    assert [r["id"] for r in rows] == [r["id"] for r in paid_rows]
    assert session.get.call_count == 2

    first, second = session.get.call_args_list
    assert first.args[0] == mw_api.HISTORY_URL
    assert first.kwargs["params"] == {"per_page": 500, "undated": 0, "from": "2026-01-01"}
    # Page 2: filters re-sent alongside the link's own page param.
    assert second.args[0] == mw_api.HISTORY_URL
    assert second.kwargs["params"] == {
        "per_page": 500, "undated": 0, "from": "2026-01-01", "page": "2",
    }
    assert second.kwargs["headers"]["Cookie"] == "remember_web_test=abc"


def test_paid_params_all_history(cookie_env, paid_pages):
    session = mock.Mock()
    session.get.return_value = _response(200, paid_pages[1])
    mw_api.fetch_paid_payables(session, to_date=date(2026, 3, 31), include_undated=True)
    params = session.get.call_args.kwargs["params"]
    assert params == {"per_page": 500, "undated": 1, "to": "2026-03-31"}


def test_open_payables_unpaginated(cookie_env, open_rows):
    session = mock.Mock()
    session.get.return_value = _response(200, {"data": open_rows})
    rows = mw_api.fetch_open_payables(session)
    assert len(rows) == len(open_rows)
    assert session.get.call_args.args[0] == mw_api.API_URL


# === load_payables ===


def test_load_payables_network_default_window(cookie_env, open_rows, paid_pages):
    session = mock.Mock()
    session.get.side_effect = [
        _response(200, {"data": open_rows}),
        _response(200, paid_pages[0]),
        _response(200, paid_pages[1]),
    ]
    out = mw_api.load_payables(today=TODAY, in_house_keywords=KEYWORDS, session=session)
    assert session.get.call_args_list[1].kwargs["params"]["from"] == "2026-01-01"
    assert session.get.call_args_list[1].kwargs["params"]["undated"] == 0
    # 8 paid rows returned; 300006 (paid 2025-12-31 New York) is dropped.
    assert len(out) == len(open_rows) + 7
    assert 300006 not in set(out["ID"])


def test_load_payables_all_history_params(cookie_env, open_rows, paid_pages):
    session = mock.Mock()
    session.get.side_effect = [
        _response(200, {"data": open_rows}),
        _response(200, paid_pages[1]),
    ]
    mw_api.load_payables(True, today=TODAY, in_house_keywords=KEYWORDS, session=session)
    params = session.get.call_args_list[1].kwargs["params"]
    assert "from" not in params
    assert params["undated"] == 1


def test_load_payables_fixture(monkeypatch):
    monkeypatch.setenv("MW_FIXTURE_PATH", os.path.join(FIXTURES, "ui_fixture.json"))
    with mock.patch.object(mw_api.requests, "Session") as session_cls:
        current = mw_api.load_payables(today=TODAY, in_house_keywords=KEYWORDS)
        everything = mw_api.load_payables(True, today=TODAY, in_house_keywords=KEYWORDS)
    session_cls.assert_not_called()
    paid_current = current[current["Payment Status"] == "Paid"]
    paid_all = everything[everything["Payment Status"] == "Paid"]
    # Default: completed since 2026-01-01 (UTC day, like the endpoint), undated excluded,
    # then 300006 (2026-01-01T02:15Z = 2025-12-31 New York) dropped as last year's payment.
    assert set(paid_current["ID"]) == {300001, 300002, 300003, 300004, 300005, 300007}
    assert set(paid_all["ID"]) == set(paid_current["ID"]) | {300006, 300008}
    assert set(CONTRACT_B_COLUMNS) <= set(current.columns)


def test_default_window_drops_prior_ny_year_paid_rows(cookie_env, paid_rows):
    # Fetched by from=2026-01-01 (UTC day) but paid 2025-12-31 in New York.
    boundary = [r for r in paid_rows if r["id"] == 300006]
    session = mock.Mock()
    session.get.side_effect = [
        _response(200, {"data": []}),
        _response(200, {"data": boundary, "links": {"next": None}}),
    ]
    out = mw_api.load_payables(today=TODAY, in_house_keywords=KEYWORDS, session=session)
    assert out.empty
    assert set(CONTRACT_B_COLUMNS) <= set(out.columns)

    session.get.side_effect = [
        _response(200, {"data": []}),
        _response(200, {"data": boundary, "links": {"next": None}}),
    ]
    everything = mw_api.load_payables(True, today=TODAY, in_house_keywords=KEYWORDS, session=session)
    assert list(everything["ID"]) == [300006]
    assert everything.iloc[0]["Paid Month"] == "2025-12"
