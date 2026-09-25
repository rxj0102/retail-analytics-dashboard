"""
Retail Analytics Dashboard (Streamlit + Plotly).

Reads the cleaned SQLite database at data/retail.db and renders four tabs:
Overview, Products & Regions, Customers and Data Quality.

Run:
    streamlit run app.py
"""

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "retail.db"
REPORT_PATH = ROOT / "data" / "processed" / "data_quality_report.md"

# Validated categorical palette, assigned in fixed order to a fixed entity list
# so a filter never repaints the survivors.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
REGIONS = ["Northeast", "Southeast", "Midwest", "Southwest", "West"]
REGION_COLORS = dict(zip(REGIONS, SERIES))
SEGMENTS = ["High", "Mid", "Low"]
SEGMENT_COLORS = dict(zip(SEGMENTS, SERIES))
PRIMARY = SERIES[0]
MUTED = "#8a8984"
ALERT = "#e34948"

st.set_page_config(page_title="Retail Analytics Dashboard", layout="wide")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data
def load_data(db_path=str(DB_PATH)):
    with sqlite3.connect(db_path) as conn:
        orders = pd.read_sql_query(
            """
            SELECT o.order_id, o.order_date, o.status, o.customer_id,
                   o.store_id, s.store_name, s.region,
                   COALESCE(SUM(oi.line_revenue), 0) AS order_total,
                   COALESCE(SUM(oi.quantity), 0)     AS units
            FROM orders o
            JOIN stores s ON s.store_id = o.store_id
            LEFT JOIN order_items oi ON oi.order_id = o.order_id
            GROUP BY o.order_id
            """,
            conn,
        )
        items = pd.read_sql_query(
            """
            SELECT oi.order_id, p.product_id, p.product_name, p.category,
                   oi.quantity, oi.line_revenue
            FROM order_items oi
            JOIN products p ON p.product_id = oi.product_id
            """,
            conn,
        )
        stores = pd.read_sql_query("SELECT * FROM stores ORDER BY store_id", conn)
    orders["order_date"] = pd.to_datetime(orders["order_date"])
    return orders, items, stores


@st.cache_data
def detect_outages(orders, threshold=0.15):
    """Return the worst 7-day window per store that ran below `threshold` of normal.

    Normal is the store's trailing 4-week average ahead of the window, so
    seasonal swings do not trigger false alarms. Same rule as query 5b.
    """
    days = pd.date_range(orders["order_date"].min(), orders["order_date"].max())
    daily = (orders.groupby(["order_date", "store_id"]).size()
             .unstack("store_id").reindex(days, fill_value=0).fillna(0))
    rolling = daily.rolling(7).sum()
    expected = daily.shift(7).rolling(28).mean() * 7
    low = rolling < threshold * expected
    rows = []
    for store in daily.columns:
        if not low[store].any():
            continue
        ratio = (rolling[store] / expected[store])[low[store]]
        worst_end = ratio.idxmin()
        rows.append({
            "store_id": store,
            "start": worst_end - pd.Timedelta(days=6),
            "end": worst_end,
            "orders": int(rolling.at[worst_end, store]),
            "expected": expected.at[worst_end, store],
        })
    return pd.DataFrame(rows)


def style_fig(fig, height=360):
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=10, r=10, t=40, b=10),
        font=dict(size=13),
        hoverlabel=dict(font_size=13),
        legend=dict(orientation="h", yanchor="top", y=-0.18, x=0, title_text=""),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.18)", zeroline=False)
    return fig


def money(x):
    return f"${x:,.0f}"


if not DB_PATH.exists():
    st.error("data/retail.db not found. Run the pipeline in src/ first (see README).")
    st.stop()

orders, items, stores = load_data()
outages = detect_outages(orders)
store_names = dict(zip(stores["store_id"], stores["store_name"]))

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

st.sidebar.header("Filters")
sel_regions = st.sidebar.multiselect("Region", REGIONS, default=REGIONS)
statuses = ["completed", "refunded", "cancelled"]
sel_status = st.sidebar.multiselect("Order status", statuses, default=["completed"],
                                    help="Revenue figures include only the statuses selected here.")
min_d, max_d = orders["order_date"].min().date(), orders["order_date"].max().date()
date_range = st.sidebar.date_input("Date range", value=(min_d, max_d), min_value=min_d, max_value=max_d)
if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d, end_d = min_d, max_d
st.sidebar.caption("Revenue excludes refunded and cancelled orders unless you add them above.")

mask = (
    orders["region"].isin(sel_regions)
    & orders["status"].isin(sel_status)
    & orders["order_date"].between(pd.Timestamp(start_d), pd.Timestamp(end_d))
)
f_orders = orders[mask]
f_items = items.merge(f_orders[["order_id", "region", "order_date"]], on="order_id")

st.title("Retail Analytics Dashboard")
st.caption(f"10 stores, 25 products, {orders['customer_id'].nunique()} customers. "
           f"Showing {start_d:%b %d, %Y} to {end_d:%b %d, %Y}.")

if f_orders.empty:
    st.warning("No orders match the current filters.")
    st.stop()

tab_overview, tab_products, tab_customers, tab_quality = st.tabs(
    ["Overview", "Products & Regions", "Customers", "Data Quality"]
)

# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

with tab_overview:
    revenue = f_orders["order_total"].sum()
    n_orders = len(f_orders)
    with_items = f_orders[f_orders["units"] > 0]
    aov = with_items["order_total"].mean() if len(with_items) else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Total revenue", money(revenue))
    c2.metric("Orders", f"{n_orders:,}")
    c3.metric("Avg order value", f"${aov:,.2f}")

    monthly = (f_orders.set_index("order_date").resample("MS")["order_total"].sum()
               .rename("revenue").reset_index())
    fig = px.line(monthly, x="order_date", y="revenue", markers=True,
                  title="Monthly revenue", color_discrete_sequence=[PRIMARY])
    fig.update_traces(line_width=2, marker_size=8,
                      hovertemplate="%{x|%b %Y}<br>Revenue: $%{y:,.0f}<extra></extra>")
    fig.update_yaxes(tickprefix="$", tickformat=",.0f", title="")
    fig.update_xaxes(title="")
    st.plotly_chart(style_fig(fig), use_container_width=True)

    st.subheader("Daily order volume")
    store_options = ["All stores"] + [f"{sid} {store_names[sid]}" for sid in stores["store_id"]]
    default_idx = 0
    choice = st.selectbox("Store", store_options, index=default_idx,
                          help="Pick a single store to inspect its daily activity.")
    daily_src = f_orders if choice == "All stores" else f_orders[f_orders["store_id"] == choice.split()[0]]
    days = pd.date_range(pd.Timestamp(start_d), pd.Timestamp(end_d))
    daily = daily_src.groupby("order_date").size().reindex(days, fill_value=0)
    fig = go.Figure(go.Bar(x=daily.index, y=daily.values, marker_color=PRIMARY,
                           hovertemplate="%{x|%a %b %d, %Y}<br>Orders: %{y}<extra></extra>"))
    fig.update_layout(title="Orders per day", bargap=0.1)
    for _, o in outages.iterrows():
        relevant = choice == "All stores" or choice.startswith(o["store_id"])
        in_range = o["end"].date() >= start_d and o["start"].date() <= end_d
        if relevant and in_range and store_names.get(o["store_id"]):
            fig.add_vrect(x0=o["start"] - pd.Timedelta(hours=12), x1=o["end"] + pd.Timedelta(hours=12),
                          fillcolor=ALERT, opacity=0.15, line_width=0,
                          annotation_text=f"{o['store_id']} outage", annotation_position="top left")
    st.plotly_chart(style_fig(fig), use_container_width=True)

    if not outages.empty:
        for _, o in outages.iterrows():
            st.warning(
                f"Anomaly: {store_names[o['store_id']]} ({o['store_id']}) recorded "
                f"{o['orders']} order(s) from {o['start']:%b %d} to {o['end']:%b %d, %Y}, "
                f"against a normal {o['expected']:.0f} per week. Select that store above, or "
                "look at the heatmap below, to see the gap.",
                icon="⚠️",
            )
        o = outages.iloc[0]
        month_start = o["start"].replace(day=1)
        month_end = month_start + pd.offsets.MonthEnd(0)
        month_days = pd.date_range(month_start, month_end)
        heat = (orders[orders["order_date"].between(month_start, month_end)]
                .groupby(["store_id", "order_date"]).size().unstack("order_date")
                .reindex(index=stores["store_id"], columns=month_days, fill_value=0).fillna(0))
        fig = go.Figure(go.Heatmap(
            z=heat.values, x=heat.columns, y=[f"{s} {store_names[s]}" for s in heat.index],
            colorscale=[[0, "#f4f3ef"], [1, PRIMARY]], xgap=2, ygap=2,
            hovertemplate="%{y}<br>%{x|%b %d}: %{z} orders<extra></extra>",
            colorbar=dict(title="Orders"),
        ))
        fig.update_layout(title=f"Orders per store per day, {month_start:%B %Y} (all statuses, all regions)")
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(style_fig(fig, height=420), use_container_width=True)

# ---------------------------------------------------------------------------
# Products & Regions
# ---------------------------------------------------------------------------

with tab_products:
    left, right = st.columns([3, 2])
    top = (f_items.groupby(["product_name", "category"], as_index=False)
           .agg(revenue=("line_revenue", "sum"), units=("quantity", "sum"))
           .sort_values("revenue", ascending=False).head(10))
    fig = px.bar(top.sort_values("revenue"), x="revenue", y="product_name", orientation="h",
                 title="Top 10 products by revenue", color_discrete_sequence=[PRIMARY],
                 custom_data=["category", "units"])
    fig.update_traces(hovertemplate="%{y}<br>%{customdata[0]}<br>Revenue: $%{x:,.0f}"
                                    "<br>Units: %{customdata[1]:,}<extra></extra>")
    fig.update_xaxes(tickprefix="$", tickformat=",.0f", title="")
    fig.update_yaxes(title="")
    left.plotly_chart(style_fig(fig, height=440), use_container_width=True)

    by_region = f_orders.groupby("region", as_index=False)["order_total"].sum()
    fig = px.pie(by_region, names="region", values="order_total", hole=0.5,
                 title="Revenue by region", color="region", color_discrete_map=REGION_COLORS,
                 category_orders={"region": REGIONS})
    fig.update_traces(textinfo="label+percent", marker_line_color="white", marker_line_width=2,
                      sort=False, direction="clockwise",
                      hovertemplate="%{label}<br>$%{value:,.0f} (%{percent})<extra></extra>")
    style_fig(fig, height=440).update_layout(showlegend=False)
    right.plotly_chart(fig, use_container_width=True)

    st.subheader("Revenue by category")
    cat = (f_items.groupby("category", as_index=False)
           .agg(revenue=("line_revenue", "sum"), units=("quantity", "sum"),
                orders=("order_id", "nunique")))
    cat["share"] = cat["revenue"] / cat["revenue"].sum()
    cat["avg_price"] = cat["revenue"] / cat["units"]
    cat = cat.sort_values("revenue", ascending=False)
    st.dataframe(
        cat, hide_index=True, use_container_width=True,
        column_config={
            "category": "Category",
            "revenue": st.column_config.NumberColumn("Revenue", format="dollar"),
            "units": st.column_config.NumberColumn("Units", format="localized"),
            "orders": st.column_config.NumberColumn("Orders", format="localized"),
            "share": st.column_config.ProgressColumn("Share of revenue", format="percent",
                                                     min_value=0, max_value=1),
            "avg_price": st.column_config.NumberColumn("Avg unit price", format="dollar"),
        },
    )

# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

with tab_customers:
    cust = (f_orders.groupby("customer_id")
            .agg(spend=("order_total", "sum"), orders=("order_id", "count"),
                 last_order=("order_date", "max"))
            .reset_index())
    cust["recency_days"] = (pd.Timestamp(end_d) - cust["last_order"]).dt.days
    # Equal thirds by spend, same rule as query 4 in sql/queries.sql.
    rank = cust["spend"].rank(method="first", ascending=False)
    cust["segment"] = pd.cut(rank, bins=3, labels=SEGMENTS).astype(str)

    seg = (cust.groupby("segment")
           .agg(customers=("customer_id", "count"), avg_spend=("spend", "mean"),
                total_spend=("spend", "sum"), avg_orders=("orders", "mean"),
                avg_recency_days=("recency_days", "mean"))
           .reindex(SEGMENTS).reset_index())
    seg["share"] = seg["total_spend"] / seg["total_spend"].sum()
    st.subheader("Customer value segments")
    st.caption("Customers with at least one order under the current filters, split into equal "
               "thirds by total spend.")
    st.dataframe(
        seg, hide_index=True, use_container_width=True,
        column_config={
            "segment": "Segment",
            "customers": st.column_config.NumberColumn("Customers", format="localized"),
            "avg_spend": st.column_config.NumberColumn("Avg spend", format="dollar"),
            "total_spend": st.column_config.NumberColumn("Total spend", format="dollar"),
            "share": st.column_config.ProgressColumn("Share of revenue", format="percent",
                                                     min_value=0, max_value=1),
            "avg_orders": st.column_config.NumberColumn("Avg orders", format="%.1f"),
            "avg_recency_days": st.column_config.NumberColumn("Avg days since last order", format="%.0f"),
        },
    )

    fig = px.histogram(cust, x="spend", color="segment", nbins=40,
                       category_orders={"segment": SEGMENTS}, color_discrete_map=SEGMENT_COLORS,
                       title="Distribution of customer spend")
    fig.update_traces(marker_line_color="white", marker_line_width=1,
                      hovertemplate="Spend $%{x:,.0f}<br>Customers: %{y}<extra></extra>")
    fig.update_layout(bargap=0.05)
    fig.update_xaxes(tickprefix="$", tickformat=",.0f", title="Total spend per customer")
    fig.update_layout(margin=dict(b=60))
    fig.update_yaxes(title="Customers")
    st.plotly_chart(style_fig(fig), use_container_width=True)

# ---------------------------------------------------------------------------
# Data Quality
# ---------------------------------------------------------------------------

with tab_quality:
    st.subheader("Why these fixes matter")
    st.markdown(
        """
| Issue | Why it matters for analysis |
|---|---|
| **Mixed date formats** | Text dates sort and group wrongly, so monthly trends and date filters silently drop or misplace orders. `03/04/2025` is also ambiguous between March and April. |
| **Inconsistent state values** | `NY`, `ny`, `New York` and ` NY` would count as four states, splitting regional totals and breaking the duplicate check. |
| **Missing emails** | These customers cannot be reached by marketing. The rows are kept because their purchases are real revenue; dropping them would understate sales. |
| **Near-duplicate customers** | One person counted twice inflates the customer count and deflates spend per customer, which pushes real high-value shoppers into lower segments. |
| **Currency strings in prices** | `$24.99` is text, so sums either fail or skip those rows, understating revenue by roughly a tenth. |
| **Zero or negative quantities** | Data entry errors that would subtract revenue and distort units sold. Real returns are tracked through the refunded status instead. |
| **Stale line revenue** | Some exported line totals were computed before discounts. Recomputing from quantity and price makes every revenue figure traceable. |
| **Store outage** | A week of missing orders at one store is a data gap, not a demand drop. Left unexplained it would show up as a false sales decline for that store and region. |
"""
    )
    st.divider()
    if REPORT_PATH.exists():
        st.markdown(REPORT_PATH.read_text(encoding="utf-8"))
    else:
        st.info("Run src/clean_data.py to generate the data quality report.")
