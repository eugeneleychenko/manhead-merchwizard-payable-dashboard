import streamlit as st
import pandas as pd
import os
from datetime import datetime
from dotenv import load_dotenv

import mw_api

load_dotenv()

st.set_page_config(page_title="Merchwizard Payables", layout="wide")
st.title("Merchwizard Payable Designs")

COOKIE_NAME = os.getenv("MW_COOKIE_NAME")
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


if not FIXTURE_PATH and (not COOKIE_NAME or not COOKIE_VALUE):
    st.error("Missing MW_COOKIE_NAME or MW_COOKIE_VALUE in .env file.")
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

if st.button("Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# --- Sidebar Filters ---
st.sidebar.header("Filters")

clients = sorted(df["Client"].dropna().unique())
selected_clients = st.sidebar.multiselect("Client", clients)

designers = sorted(df["Designer"].dropna().unique())
selected_designers = st.sidebar.multiselect("Designer", designers)

search_name = st.sidebar.text_input("Search Design Name")

min_date = df["Created Date"].min()
max_date = df["Created Date"].max()
date_range = st.sidebar.date_input(
    "Created Date Range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)

in_house_filter = st.sidebar.radio(
    "Designer Type", ["All", "Freelancer Only", "In-House Only"], index=0
)

min_total = st.sidebar.number_input("Min Total ($)", min_value=0, value=0, step=50)

# --- Apply filters ---
filtered = df.copy()

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

if in_house_filter == "Freelancer Only":
    filtered = filtered[~filtered["In-House"]]
elif in_house_filter == "In-House Only":
    filtered = filtered[filtered["In-House"]]

if min_total > 0:
    filtered = filtered[filtered["Total"] >= min_total]

# Freelancer-only spending (for budget tracking)
freelancer_df = filtered[~filtered["In-House"]]

# === BUDGET TRACKER ===
st.header("Budget Tracker")
st.caption(
    f"Annual Budget: ${ANNUAL_BUDGET:,} | Monthly Target: ${MONTHLY_BUDGET:,.0f}"
)

ytd_freelancer_spend = freelancer_df["Total"].sum()
current_month = datetime.now().month
expected_spend = MONTHLY_BUDGET * current_month
budget_remaining = ANNUAL_BUDGET - ytd_freelancer_spend

col1, col2, col3, col4 = st.columns(4)
col1.metric("YTD Freelancer Spend", f"${ytd_freelancer_spend:,.0f}")
col2.metric(
    "Budget Remaining",
    f"${budget_remaining:,.0f}",
    delta=f"{'On Track' if ytd_freelancer_spend <= expected_spend else 'Over Budget'}",
    delta_color="normal" if ytd_freelancer_spend <= expected_spend else "inverse",
)
col3.metric("Expected by Now", f"${expected_spend:,.0f}")
col4.metric(
    "Avg Monthly Spend",
    f"${ytd_freelancer_spend / max(current_month, 1):,.0f}",
)

# === MONTHLY SPENDING CHART ===
st.header("Monthly Spending")

monthly = (
    freelancer_df.groupby("Month")
    .agg({"Submission": "sum", "Balance": "sum", "Separation": "sum", "Total": "sum", "ID": "count"})
    .rename(columns={"ID": "# Designs"})
    .sort_index()
)

if not monthly.empty:
    chart_data = monthly[["Submission", "Balance", "Separation"]].copy()
    st.bar_chart(chart_data)

    monthly_display = monthly.copy()
    monthly_display["Budget"] = MONTHLY_BUDGET
    monthly_display["Over/Under"] = MONTHLY_BUDGET - monthly_display["Total"]
    st.dataframe(monthly_display, use_container_width=True)

# === SUMMARY METRICS ===
st.header("Filtered Summary")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Records", len(filtered))
col2.metric("Total Submission", f"${filtered['Submission'].sum():,.0f}")
col3.metric("Total Balance", f"${filtered['Balance'].sum():,.0f}")
col4.metric("Total Separation", f"${filtered['Separation'].sum():,.0f}")
col5.metric("Grand Total", f"${filtered['Total'].sum():,.0f}")

col_a, col_b = st.columns(2)
col_a.metric("Freelancer Designs", len(filtered[~filtered["In-House"]]))
col_b.metric("In-House Designs", len(filtered[filtered["In-House"]]))

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

available_months = sorted(filtered["Month"].unique())
export_month = st.selectbox("Select Month for Export", ["All Months"] + available_months)

if export_month == "All Months":
    export_df = filtered.drop(columns=["Created"])
    export_label = "all"
else:
    export_df = filtered[filtered["Month"] == export_month].drop(columns=["Created"])
    export_label = export_month

csv = export_df.to_csv(index=False)
st.download_button(
    label=f"Download {export_month} Data as CSV",
    data=csv,
    file_name=f"payable_designs_{export_label}_{datetime.now().strftime('%Y%m%d')}.csv",
    mime="text/csv",
)
st.caption(f"{len(export_df)} records in export")
