"""
Run every query in sql/queries.sql against data/retail.db and print a
labeled plain-text summary, including the store outage finding.

Usage:
    python src/analysis.py
"""

import re
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "retail.db"
SQL_PATH = ROOT / "sql" / "queries.sql"

WIDTH = 78


def load_queries(path=SQL_PATH):
    """Split queries.sql on '-- name: <id>' markers into {id: sql}."""
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"^--\s*name:\s*(\w+)\s*$", text, flags=re.MULTILINE)
    # parts = [preamble, name1, body1, name2, body2, ...]
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def header(title):
    print()
    print("=" * WIDTH)
    print(title)
    print("=" * WIDTH)


def money(x):
    return f"${x:,.2f}"


def show(df, formatters=None):
    print(df.to_string(index=False, formatters=formatters or {}))


def main():
    queries = load_queries()
    with sqlite3.connect(DB_PATH) as conn:
        results = {name: pd.read_sql_query(sql, conn) for name, sql in queries.items()}

    pd.set_option("display.width", WIDTH + 40)
    money_cols = lambda df: {c: money for c in df.columns
                             if c in {"revenue", "avg_order_value", "min_spend", "avg_spend",
                                      "total_spend", "order_value"}}

    print("RETAIL ANALYTICS SUMMARY")
    print(f"Source: {DB_PATH.relative_to(ROOT)}  |  Queries: {SQL_PATH.relative_to(ROOT)}")

    # 1. Monthly revenue ------------------------------------------------------
    monthly = results["monthly_revenue"]
    header("1. MONTHLY REVENUE TREND (completed orders)")
    show(monthly, money_cols(monthly))
    monthly["year"] = monthly["month"].str[:4]
    by_year = monthly.groupby("year")["revenue"].sum()
    peak = monthly.loc[monthly["revenue"].idxmax()]
    low = monthly.loc[monthly["revenue"].idxmin()]
    print()
    for year, rev in by_year.items():
        print(f"  {year} revenue: {money(rev)}")
    if len(by_year) >= 2:
        growth = by_year.iloc[-1] / by_year.iloc[-2] - 1
        print(f"  Year over year growth: {growth:+.1%}")
    novdec = monthly[monthly["month"].str[5:].isin(["11", "12"])]["revenue"].sum()
    print(f"  Nov + Dec share of total revenue: {novdec / monthly['revenue'].sum():.1%} "
          f"(4 of 24 months)")
    print(f"  Peak month: {peak['month']} at {money(peak['revenue'])}; "
          f"slowest: {low['month']} at {money(low['revenue'])}")

    # 2. Top products ---------------------------------------------------------
    top = results["top_products"]
    header("2. TOP 10 PRODUCTS BY REVENUE")
    show(top, money_cols(top))
    print(f"\n  Top 10 products account for {top['pct_of_revenue'].sum():.1f}% of revenue.")

    # 3. Regions --------------------------------------------------------------
    regions = results["regional_performance"]
    header("3. REGIONAL PERFORMANCE (by store region)")
    show(regions, money_cols(regions))
    best, worst = regions.iloc[0], regions.iloc[-1]
    print(f"\n  {best['region']} leads with {best['pct_of_revenue']:.1f}% of revenue; "
          f"{worst['region']} trails at {worst['pct_of_revenue']:.1f}%.")

    # 4. Customer segments ----------------------------------------------------
    seg = results["customer_segments"]
    header("4. CUSTOMER VALUE SEGMENTS (spend terciles)")
    show(seg, money_cols(seg))
    high = seg[seg["segment"] == "High"].iloc[0]
    print(f"\n  The High segment ({int(high['customers'])} customers) drives "
          f"{high['pct_of_revenue']:.1f}% of revenue at {money(high['avg_spend'])} per customer.")

    # 5. Anomaly --------------------------------------------------------------
    daily = results["anomaly_daily_store_orders"]
    windows = results["anomaly_outage_windows"]
    header("5. ANOMALY DETECTION: DAILY ORDERS PER STORE, FEB 2025")
    pivot = daily.pivot(index="day", columns="store_id", values="order_count")
    pivot.index = pivot.index.str[5:]
    print(pivot.to_string())
    print("\n  7-day windows running below 15% of the store's normal weekly volume:")
    if windows.empty:
        print("  none found")
    else:
        show(windows)
        flagged = windows["store_id"].unique()
        worst = windows.iloc[0]
        store_days = daily[daily["store_id"] == worst["store_id"]]
        in_window = store_days["day"].between(worst["window_start"], worst["window_end"])
        normal_rate = store_days.loc[~in_window, "order_count"].mean()
        print()
        print(f"  >> ANOMALY: {worst['store_name']} ({worst['store_id']}, {worst['region']}) "
              f"recorded {int(worst['orders_7d'])} order(s) from {worst['window_start']} to "
              f"{worst['window_end']},")
        print(f"     {worst['pct_of_expected']:.0f}% of its normal {worst['expected_7d']:.1f} orders per "
              f"week ({normal_rate:.1f} per day the rest of February).")
        if len(flagged) == 1:
            print("     No other store was flagged, so this looks like a POS or system outage at one")
            print("     location rather than a demand drop, and those sales are likely unrecorded.")
        else:
            print(f"     Stores flagged: {', '.join(flagged)}. Check whether these share a cause.")

    # 6. Status ---------------------------------------------------------------
    status = results["order_status_breakdown"]
    header("6. ORDER STATUS BREAKDOWN")
    show(status, money_cols(status))
    lost = status[status["status"] != "completed"]
    print(f"\n  {lost['pct_of_orders'].sum():.1f}% of orders were refunded or cancelled, "
          f"worth {money(lost['order_value'].sum())} in gross order value.")

    # Headline KPIs -----------------------------------------------------------
    header("HEADLINE KPIs (completed orders, 2024 to 2025)")
    total_rev = monthly["revenue"].sum()
    total_orders = monthly["orders"].sum()  # completed orders with at least one valid line item
    print(f"  Total revenue:        {money(total_rev)}")
    print(f"  Completed orders:     {total_orders:,}")
    print(f"  Average order value:  {money(total_rev / total_orders)}")
    print()


if __name__ == "__main__":
    main()
