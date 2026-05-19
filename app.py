import streamlit as st
import pandas as pd
import requests
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Merchwizard Payables", layout="wide")
st.title("Merchwizard Payable Designs")

COOKIE_NAME = os.getenv("MW_COOKIE_NAME")
COOKIE_VALUE = os.getenv("MW_COOKIE_VALUE")
API_URL = "https://api.merchwizard.com/api/dashboard/payable/"

ANNUAL_BUDGET = 250_000
MONTHLY_BUDGET = ANNUAL_BUDGET / 12

IN_HOUSE_KEYWORDS = ["manhead", "mallory strong", "andrew chavez"]


@st.cache_data(ttl=300)
def fetch_payable_data():
    resp = requests.get(
        API_URL,
        headers={
            "Cookie": f"{COOKIE_NAME}={COOKIE_VALUE}",
            "Accept": "application/json",
            "Referer": "https://merchwizard.com/dashboard/payable/",
            "Origin": "https://merchwizard.com",
        },
    )
    resp.raise_for_status()
    data = resp.json()["data"]

    rows = []
    for item in data:
        designer_name = item["designer"]["name"] if item.get("designer") else None
        is_in_house = False
        if designer_name:
            is_in_house = any(
                kw in designer_name.lower() for kw in IN_HOUSE_KEYWORDS
            )

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


if not COOKIE_NAME or not COOKIE_VALUE:
    st.error("Missing MW_COOKIE_NAME or MW_COOKIE_VALUE in .env file.")
    st.stop()

try:
    df = fetch_payable_data()
except Exception as e:
    st.error(f"Failed to fetch data: {e}")
    st.stop()

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
csv = filtered.drop(columns=["Created"]).to_csv(index=False)
st.download_button(
    label="Download Filtered Data as CSV",
    data=csv,
    file_name=f"payable_designs_{datetime.now().strftime('%Y%m%d')}.csv",
    mime="text/csv",
)
