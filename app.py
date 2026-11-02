import streamlit as st
import pandas as pd
import os
from datetime import datetime
from dotenv import load_dotenv

import mw_api

load_dotenv()

st.set_page_config(page_title="Merchwizard Payables", layout="wide")
st.title("Merchwizard Payable Designs")

API_TOKEN = os.getenv("MW_API_TOKEN")  # preferred: Sanctum token from `php artisan app:dashboard-token`
COOKIE_NAME = os.getenv("MW_COOKIE_NAME")  # fallback: borrowed remember_web cookie
COOKIE_VALUE = os.getenv("MW_COOKIE_VALUE")
FIXTURE_PATH = os.getenv("MW_FIXTURE_PATH")  # offline mode: load rows from a JSON fixture
API_URL = mw_api.API_URL

ANNUAL_BUDGET = 250_000
MONTHLY_BUDGET = ANNUAL_BUDGET / 12

IN_HOUSE_KEYWORDS = ["manhead", "mallory strong", "andrew chavez", "ali cook", "maria fulan"]


@st.cache_data(ttl=300)
def fetch_payable_data(load_all_history: bool = False):
    """Open + paid payables as one DataFrame (see mw_api / Contract B)."""
    return mw_api.load_payables(load_all_history, in_house_keywords=IN_HOUSE_KEYWORDS)


if not FIXTURE_PATH and not API_TOKEN and (not COOKIE_NAME or not COOKIE_VALUE):
    st.error("Missing credentials: set MW_API_TOKEN (preferred) or MW_COOKIE_NAME + MW_COOKIE_VALUE.")
    st.stop()

# The "Load all paid history" sidebar checkbox (UI section) should use
# key="load_all_history"; its value is read here, before the fetch, on each rerun.
load_all_history = bool(st.session_state.get("load_all_history", False))

try:
    df = fetch_payable_data(load_all_history)
except mw_api.MerchwizardAuthError as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(f"Failed to fetch data: {e}")
    st.stop()

if FIXTURE_PATH:
    st.info(f"Offline mode: data loaded from fixture `{FIXTURE_PATH}` (MW_FIXTURE_PATH).")

if df.empty:
    # The sidebar date pickers below need at least one row to set their bounds.
    st.info("Merchwizard returned no payable or paid designs.")
    if st.button("Refresh Data", key="refresh_empty"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

if st.button("Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# --- Sidebar Filters ---
st.sidebar.header("Filters")

PAYMENT_STATUSES = ["Awaiting Approval", "Outstanding", "Paid"]
# New York time, matching mw_api's month bucketing and default paid-history window.
current_year = datetime.now(mw_api.NY_TZ).year

# Read by the fetch call above via st.session_state["load_all_history"]; toggling it
# reruns the script, so the new value is in session state before the fetch runs.
load_all_history = st.sidebar.checkbox(
    "Load all paid history",
    value=False,
    key="load_all_history",
    help=(
        "Off: paid designs from the current year only. "
        "On: every paid design, including legacy ones with no paid date."
    ),
)

selected_statuses = st.sidebar.multiselect(
    "Payment Status", PAYMENT_STATUSES, default=PAYMENT_STATUSES
)

clients = sorted(df["Client"].dropna().unique())
selected_clients = st.sidebar.multiselect("Client", clients)

designers = sorted(df["Designer"].dropna().unique())
selected_designers = st.sidebar.multiselect("Designer", designers)

search_name = st.sidebar.text_input("Search Design Name")

min_date = df["Created Date"].min()
max_date = df["Created Date"].max()
date_range = st.sidebar.date_input(
    "Created Date Range (upload)",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)

# Paid Date Range only narrows Paid rows; other statuses are never dropped by it.
paid_dates_all = pd.to_datetime(
    df.loc[df["Payment Status"] == "Paid", "Paid Date"], errors="coerce"
).dropna()
paid_range = ()
paid_range_active = False
if not paid_dates_all.empty:
    min_paid = paid_dates_all.min().date()
    max_paid = paid_dates_all.max().date()
    paid_range = st.sidebar.date_input(
        "Paid Date Range",
        value=(min_paid, max_paid),
        min_value=min_paid,
        max_value=max_paid,
        help="Applies to Paid designs only.",
    )
    paid_range_active = len(paid_range) == 2 and (
        paid_range[0] != min_paid or paid_range[1] != max_paid
    )
    if paid_range_active:
        st.sidebar.caption(
            "Paid rows with no paid date are hidden while a paid date range is set."
        )
else:
    st.sidebar.caption("Paid Date Range: no dated paid designs loaded.")

in_house_filter = st.sidebar.radio(
    "Designer Type", ["All", "Freelancer Only", "In-House Only"], index=0
)

min_total = st.sidebar.number_input("Min Total ($)", min_value=0, value=0, step=50)

# --- Apply filters ---
filtered = df.copy()

filtered = filtered[filtered["Payment Status"].isin(selected_statuses)]

if selected_clients:
    filtered = filtered[filtered["Client"].isin(selected_clients)]

if selected_designers:
    filtered = filtered[filtered["Designer"].isin(selected_designers)]

if search_name:
    filtered = filtered[
        filtered["Name"].str.contains(search_name, case=False, na=False)
    ]

if len(date_range) == 2:
    filtered = filtered[
        (filtered["Created Date"] >= date_range[0])
        & (filtered["Created Date"] <= date_range[1])
    ]

if paid_range_active:
    paid_ts = pd.to_datetime(filtered["Paid Date"], errors="coerce")
    in_paid_range = (paid_ts >= pd.Timestamp(paid_range[0])) & (
        paid_ts <= pd.Timestamp(paid_range[1])
    )
    filtered = filtered[(filtered["Payment Status"] != "Paid") | in_paid_range]

if in_house_filter == "Freelancer Only":
    filtered = filtered[~filtered["In-House"]]
elif in_house_filter == "In-House Only":
    filtered = filtered[filtered["In-House"]]

if min_total > 0:
    filtered = filtered[filtered["Total"] >= min_total]

# Freelancer-only spending (for budget tracking)
freelancer_df = filtered[~filtered["In-House"]]

is_paid = filtered["Payment Status"] == "Paid"
is_outstanding = filtered["Payment Status"] == "Outstanding"
is_awaiting = filtered["Payment Status"] == "Awaiting Approval"


def money_sum(frame, status):
    return frame.loc[frame["Payment Status"] == status, "Total"].sum()


# === BUDGET TRACKER ===
st.header("Budget Tracker")
st.caption(
    f"{current_year} freelancer budget | Annual Budget: ${ANNUAL_BUDGET:,} | "
    f"Monthly Target: ${MONTHLY_BUDGET:,.0f}"
)

budget_df = freelancer_df[freelancer_df["Budget Year"] == current_year]

paid_ytd = money_sum(budget_df, "Paid")
outstanding_ytd = money_sum(budget_df, "Outstanding")
awaiting_ytd = money_sum(budget_df, "Awaiting Approval")
ytd_freelancer_spend = paid_ytd + outstanding_ytd
current_month = datetime.now(mw_api.NY_TZ).month
expected_spend = MONTHLY_BUDGET * current_month
budget_remaining = ANNUAL_BUDGET - ytd_freelancer_spend
on_track = ytd_freelancer_spend <= expected_spend

col1, col2, col3 = st.columns(3)
col1.metric("YTD Spend (Paid + Outstanding)", f"${ytd_freelancer_spend:,.0f}")
col2.metric("Paid YTD", f"${paid_ytd:,.0f}")
col3.metric("Outstanding", f"${outstanding_ytd:,.0f}")

col4, col5, col6 = st.columns(3)
col4.metric("Awaiting Approval (not counted)", f"${awaiting_ytd:,.0f}")
col5.metric(
    "Budget Remaining",
    f"${budget_remaining:,.0f}",
    delta="On Track" if on_track else "Over Budget",
    delta_color="normal" if on_track else "inverse",
)
col6.metric("Expected by Now", f"${expected_spend:,.0f}")

budget_caption = (
    "Spend is attributed to the month a design was approved, not the month it was paid "
    "(designs with no approval date fall back to their upload year). "
    "Awaiting Approval designs are shown for reference and not counted. "
    "Figures respect the sidebar filters."
)
if not load_all_history:
    budget_caption += (
        " Only designs paid this year (New York time) are loaded; earlier and undated "
        "legacy paid items are not loaded. Tick 'Load all paid history' in the sidebar "
        "to include them."
    )
st.caption(budget_caption)

# === MONTHLY SPENDING CHART ===
st.header("Monthly Spending")
st.caption("Freelancer spend by approval month.")

committed_df = freelancer_df[
    freelancer_df["Payment Status"].isin(["Paid", "Outstanding"])
]
dated_committed = committed_df[committed_df["Approved Month"].notna()]

if not dated_committed.empty:
    monthly = dated_committed.pivot_table(
        index="Approved Month",
        columns="Payment Status",
        values="Total",
        aggfunc="sum",
        fill_value=0,
    ).reindex(columns=["Paid", "Outstanding"], fill_value=0)
    monthly["Total"] = monthly["Paid"] + monthly["Outstanding"]
    monthly["# Designs"] = dated_committed.groupby("Approved Month")["ID"].count()
    monthly = monthly.sort_index()
    monthly.index.name = "Approved Month"
    monthly.columns.name = None

    st.bar_chart(monthly[["Paid", "Outstanding"]], color=["#2e7d32", "#f9a825"])

    monthly_display = monthly.copy()
    monthly_display["Budget"] = MONTHLY_BUDGET
    monthly_display["Over/Under"] = MONTHLY_BUDGET - monthly_display["Total"]
    st.dataframe(monthly_display.round(2), use_container_width=True)
else:
    st.info("No paid or outstanding freelancer designs with an approval date in the current filter.")

awaiting_fl = freelancer_df[freelancer_df["Payment Status"] == "Awaiting Approval"]
footnotes = []
if not awaiting_fl.empty:
    footnotes.append(
        f"{len(awaiting_fl)} design(s) awaiting approval (\\${awaiting_fl['Total'].sum():,.0f}) "
        "are not in the chart because they have no approval month yet."
    )
undated_committed = committed_df[committed_df["Approved Month"].isna()]
if not undated_committed.empty:
    footnotes.append(
        f"{len(undated_committed)} paid/outstanding design(s) "
        f"(\\${undated_committed['Total'].sum():,.0f}) have no approval date and are not charted."
    )
if footnotes:
    st.caption(" ".join(footnotes))

# === OUTSTANDING AGING ===
st.header("Outstanding Aging")
st.caption("Approved but not yet paid, by days since approval.")

outstanding_df = filtered[is_outstanding].copy()
if not outstanding_df.empty:
    days_out = pd.to_numeric(outstanding_df["Days Outstanding"], errors="coerce")
    aging_buckets = [
        ("< 30 days", days_out < 30),
        ("30–59 days", (days_out >= 30) & (days_out < 60)),
        ("60+ days", days_out >= 60),
    ]
    aging_cols = st.columns(len(aging_buckets))
    for aging_col, (bucket_label, bucket_mask) in zip(aging_cols, aging_buckets):
        bucket_df = outstanding_df[bucket_mask]
        aging_col.metric(
            bucket_label,
            f"${bucket_df['Total'].sum():,.0f}",
            delta=f"{len(bucket_df)} design(s)",
            delta_color="off",
        )
    unknown_age = outstanding_df[days_out.isna()]
    if not unknown_age.empty:
        st.caption(
            f"{len(unknown_age)} outstanding design(s) "
            f"(\\${unknown_age['Total'].sum():,.0f}) have no approval date and are not aged."
        )

    overdue = outstanding_df[days_out >= 60].copy()
    if not overdue.empty:
        st.subheader("60+ days outstanding (oldest first)")
        overdue["Days Outstanding"] = pd.to_numeric(
            overdue["Days Outstanding"], errors="coerce"
        )
        st.dataframe(
            overdue.sort_values("Days Outstanding", ascending=False)[
                [
                    "Approved Date",
                    "Days Outstanding",
                    "Client",
                    "Name",
                    "Designer",
                    "Total",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
else:
    st.info("No outstanding designs in the current filter.")

# === PAID HISTORY ===
st.header("Paid History")

PAID_HISTORY_COLUMNS = [
    "Paid Date",
    "Client",
    "Name",
    "Designer",
    "Total",
    "Paid Amount",
    "Payment Method",
    "PayPal Batch ID",
    "Paid By",
    "Days to Pay",
]

paid_df = filtered[is_paid].copy()
if not paid_df.empty:
    paid_df["_paid_ts"] = pd.to_datetime(paid_df["Paid Date"], errors="coerce")
    undated_paid = paid_df[paid_df["_paid_ts"].isna()]

    col_p1, col_p2, col_p3 = st.columns(3)
    col_p1.metric("Paid Designs (dated)", len(paid_df) - len(undated_paid))
    col_p2.metric("Paid (date unknown)", len(undated_paid))
    col_p3.metric("Recorded Paid Amount", f"${paid_df['Paid Amount'].sum():,.0f}")

    paid_table = paid_df.sort_values(
        ["_paid_ts", "Created"], ascending=[False, False], na_position="last"
    )[PAID_HISTORY_COLUMNS].reset_index(drop=True)

    def highlight_mismatch(row):
        mismatch = abs(float(row["Paid Amount"]) - float(row["Total"])) > 0.005
        style = "background-color: #fff3cd; color: #664d03" if mismatch else ""
        return [style] * len(row)

    # Styler rendering has a cell cap; fall back to a plain table on very large histories.
    if paid_table.size <= 200_000:
        paid_view = paid_table.style.apply(highlight_mismatch, axis=1).format(
            {
                "Paid Date": lambda d: "date unknown" if pd.isna(d) else str(d)[:10],
                "Total": lambda v: f"${v:,.2f}",
                "Paid Amount": lambda v: f"${v:,.2f}",
                "Days to Pay": lambda v: "" if pd.isna(v) else f"{v:,.0f}",
            }
        )
    else:
        paid_view = paid_table
    st.dataframe(paid_view, use_container_width=True, hide_index=True)
    st.caption(
        "Highlighted rows: Paid Amount differs from Total (fees edited after payment, "
        "partial payment, or no payment recorded, e.g. check/cash)."
    )
else:
    st.info("No paid designs in the current filter.")

# === MONTHLY PAID-OUT REPORT BY ARTIST ===
st.header("Monthly Paid-Out Report by Artist")

report_base = filtered[is_paid & filtered["Paid Month"].notna()].copy()
undated_paid_count = int((is_paid & filtered["Paid Month"].isna()).sum())

if not report_base.empty:
    paid_months = sorted(report_base["Paid Month"].unique(), reverse=True)
    report_month = st.selectbox("Paid Month", paid_months, index=0)
    month_rows = report_base[report_base["Paid Month"] == report_month].copy()
    month_rows["Client"] = month_rows["Client"].fillna("(No client)")
    month_rows["Designer"] = month_rows["Designer"].fillna("(No designer)")

    report_agg = {
        "ID": "count",
        "Submission": "sum",
        "Balance": "sum",
        "Separation": "sum",
        "Total": "sum",
        "Paid Amount": "sum",
    }
    by_artist = (
        month_rows.groupby("Client")
        .agg(report_agg)
        .rename(columns={"ID": "# Designs"})
        .sort_values("Total", ascending=False)
    )
    by_artist["Difference"] = by_artist["Paid Amount"] - by_artist["Total"]

    grand_total = by_artist.sum(numeric_only=True)
    grand_total.name = "Grand Total"
    by_artist_with_total = pd.concat([by_artist, grand_total.to_frame().T])
    by_artist_with_total.index.name = "Client"
    by_artist_with_total["# Designs"] = by_artist_with_total["# Designs"].astype(int)

    col_r1, col_r2, col_r3, col_r4 = st.columns(4)
    col_r1.metric("Designs Paid", int(grand_total["# Designs"]))
    col_r2.metric("Total Fees", f"${grand_total['Total']:,.2f}")
    col_r3.metric("Recorded Paid Amount", f"${grand_total['Paid Amount']:,.2f}")
    col_r4.metric("Difference", f"${grand_total['Difference']:,.2f}")

    st.dataframe(by_artist_with_total, use_container_width=True)
    st.caption(
        "Difference = Paid Amount (recorded payments) − Total (fees). Negative means less "
        "was recorded as paid than the fees on the design."
    )
    if undated_paid_count:
        st.caption(
            f"{undated_paid_count} paid design(s) with no paid date are excluded from this report."
        )

    st.subheader("Designer breakdown by artist")
    for client_name in by_artist.index:
        client_rows = month_rows[month_rows["Client"] == client_name]
        client_total = by_artist.loc[client_name, "Total"]
        with st.expander(
            f"{client_name}: {len(client_rows)} design(s), ${client_total:,.2f}"
        ):
            designer_breakdown = (
                client_rows.groupby("Designer")
                .agg(report_agg)
                .rename(columns={"ID": "# Designs"})
                .sort_values("Total", ascending=False)
            )
            designer_breakdown["Difference"] = (
                designer_breakdown["Paid Amount"] - designer_breakdown["Total"]
            )
            st.dataframe(designer_breakdown, use_container_width=True)

    REPORT_ROW_COLUMNS = [
        "Paid Date",
        "Client",
        "Name",
        "Designer",
        "Submission",
        "Balance",
        "Separation",
        "Total",
        "Paid Amount",
        "Payment Method",
        "PayPal Batch ID",
        "Paid By",
        "Submission Invoice",
        "Balance Invoice",
        "Separation Invoice",
        "ID",
    ]
    report_rows_export = month_rows.sort_values(["Client", "Paid Date"])[
        REPORT_ROW_COLUMNS
    ]

    col_d1, col_d2 = st.columns(2)
    col_d1.download_button(
        label=f"Download {report_month} by artist (CSV)",
        data=by_artist_with_total.to_csv(),
        file_name=f"paid_out_{report_month}_by_artist.csv",
        mime="text/csv",
    )
    col_d2.download_button(
        label=f"Download {report_month} paid rows for PayPal matching (CSV)",
        data=report_rows_export.to_csv(index=False),
        file_name=f"paid_out_{report_month}_rows.csv",
        mime="text/csv",
    )
else:
    st.info("No paid designs with a paid date in the current filter.")
    if undated_paid_count:
        st.caption(f"{undated_paid_count} paid design(s) have no paid date.")

# === SUMMARY METRICS ===
st.header("Filtered Summary")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Records", len(filtered))
col2.metric("Total Submission", f"${filtered['Submission'].sum():,.0f}")
col3.metric("Total Balance", f"${filtered['Balance'].sum():,.0f}")
col4.metric("Total Separation", f"${filtered['Separation'].sum():,.0f}")
col5.metric("Grand Total", f"${filtered['Total'].sum():,.0f}")

col_a, col_b, col_c, col_d, col_e = st.columns(5)
col_a.metric("Freelancer Designs", len(filtered[~filtered["In-House"]]))
col_b.metric("In-House Designs", len(filtered[filtered["In-House"]]))
col_c.metric("Awaiting Approval", int(is_awaiting.sum()))
col_d.metric("Outstanding", int(is_outstanding.sum()))
col_e.metric("Paid", int(is_paid.sum()))

# === DATA TABLE ===
st.header("All Payable Designs")
st.dataframe(
    filtered.drop(columns=["Created"]).sort_values("Created Date", ascending=False),
    use_container_width=True,
    hide_index=True,
)

# === BY CLIENT ===
st.header("By Client")
client_summary = (
    filtered.groupby("Client")
    .agg(
        {
            "Submission": "sum",
            "Balance": "sum",
            "Separation": "sum",
            "Total": "sum",
            "ID": "count",
        }
    )
    .rename(columns={"ID": "# Designs"})
    .sort_values("Total", ascending=False)
)
st.dataframe(client_summary, use_container_width=True)

# === IN-HOUSE DESIGNER TOTALS ===
st.header("In-House Designer Totals")
in_house_df = filtered[filtered["In-House"] & filtered["Designer"].notna()]
if not in_house_df.empty:
    in_house_summary = (
        in_house_df.groupby("Designer")
        .agg(
            {
                "Submission": "sum",
                "Balance": "sum",
                "Separation": "sum",
                "Total": "sum",
                "ID": "count",
            }
        )
        .rename(columns={"ID": "# Designs"})
        .sort_values("Total", ascending=False)
    )
    col_ih1, col_ih2 = st.columns(2)
    col_ih1.metric("In-House Total Spend", f"${in_house_df['Total'].sum():,.0f}")
    col_ih2.metric("In-House Design Count", len(in_house_df))
    st.dataframe(in_house_summary, use_container_width=True)
else:
    st.info("No in-house designs in the current filter.")

# === BY DESIGNER ===
st.header("By Designer")
designer_summary = (
    filtered[filtered["Designer"].notna()]
    .groupby(["Designer", "In-House"])
    .agg(
        {
            "Submission": "sum",
            "Balance": "sum",
            "Separation": "sum",
            "Total": "sum",
            "ID": "count",
        }
    )
    .rename(columns={"ID": "# Designs"})
    .sort_values("Total", ascending=False)
)
st.dataframe(designer_summary, use_container_width=True)

# === CSV EXPORT ===
st.header("Export")

export_basis = st.radio(
    "Month basis", ["Created Month", "Paid Month"], index=0, horizontal=True
)

if export_basis == "Paid Month":
    export_pool = filtered[is_paid]
    month_column = "Paid Month"
    basis_label = "paid_"
else:
    export_pool = filtered
    month_column = "Month"
    basis_label = ""

available_months = sorted(export_pool[month_column].dropna().unique())
export_month = st.selectbox("Select Month for Export", ["All Months"] + available_months)

if export_month == "All Months":
    export_df = export_pool.drop(columns=["Created"])
    export_label = "all"
else:
    export_df = export_pool[export_pool[month_column] == export_month].drop(
        columns=["Created"]
    )
    export_label = export_month

if export_basis == "Paid Month" and export_month == "All Months":
    st.caption("Paid Month basis with All Months includes paid designs with no paid date.")

csv = export_df.to_csv(index=False)
st.download_button(
    label=f"Download {export_month} Data as CSV",
    data=csv,
    file_name=f"payable_designs_{basis_label}{export_label}_{datetime.now().strftime('%Y%m%d')}.csv",
    mime="text/csv",
)
st.caption(f"{len(export_df)} records in export ({export_basis} basis)")
