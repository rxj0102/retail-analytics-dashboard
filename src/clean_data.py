"""
Clean the raw retail export in data/raw/ and write analysis-ready CSVs to
data/processed/, plus a markdown data quality report.

Fixes applied:
* names normalized to title case
* mixed date formats parsed to ISO (YYYY-MM-DD); unparseable values flagged
* state values normalized to two-letter USPS codes
* missing emails flagged and counted (rows kept)
* near-duplicate customers merged (same normalized name + state), keeping
  the earliest customer_id and remapping their orders
* currency strings in prices converted to floats
* line items with zero or negative quantity dropped
* line_revenue recomputed as quantity * unit_price_at_sale

Usage:
    python src/clean_data.py
"""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
REPORT_PATH = PROCESSED_DIR / "data_quality_report.md"

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y")

STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
VALID_STATE_CODES = set(STATE_NAME_TO_CODE.values())

STATE_TO_REGION = {
    **dict.fromkeys(["NY", "MA", "PA", "NJ", "CT"], "Northeast"),
    **dict.fromkeys(["FL", "GA", "NC", "VA", "TN"], "Southeast"),
    **dict.fromkeys(["IL", "OH", "MI", "MN", "WI"], "Midwest"),
    **dict.fromkeys(["TX", "AZ", "NM", "OK", "NV"], "Southwest"),
    **dict.fromkeys(["CA", "WA", "OR", "CO", "UT"], "West"),
}


# ---------------------------------------------------------------------------
# Helper functions (unit tested in tests/test_cleaning.py)
# ---------------------------------------------------------------------------

def parse_date(value):
    """Parse a date in any supported format and return an ISO string.

    Returns None when the value is blank or cannot be parsed, so the caller
    can flag it instead of silently guessing.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_state(value):
    """Map any spelling of a US state ("ny", " NY", "New York") to its code.

    Returns None when the value is blank or not a recognizable state.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    if text.upper() in VALID_STATE_CODES:
        return text.upper()
    return STATE_NAME_TO_CODE.get(text.lower())


def clean_price(value):
    """Convert a price like "$1,099.00", " 24.99 " or 24.99 to a float.

    Returns None for blanks or values that are not numeric after stripping
    currency symbols and thousands separators.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_name(value):
    """Trim whitespace and convert a name to title case ("mARY" -> "Mary")."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().title()


# ---------------------------------------------------------------------------
# Table cleaning
# ---------------------------------------------------------------------------

def clean_customers(raw, stats):
    df = raw.copy()
    stats["customers_raw"] = len(df)

    before_names = df[["first_name", "last_name"]].copy()
    df["first_name"] = df["first_name"].map(normalize_name)
    df["last_name"] = df["last_name"].map(normalize_name)
    stats["names_recased"] = int(
        ((before_names["first_name"] != df["first_name"])
         | (before_names["last_name"] != df["last_name"])).sum()
    )

    raw_dates = df["signup_date"].astype(str).str.strip()
    df["signup_date"] = df["signup_date"].map(parse_date)
    stats["signup_non_iso"] = int((~raw_dates.str.match(r"^\d{4}-\d{2}-\d{2}$")).sum())
    bad_dates = df[df["signup_date"].isna()]
    stats["signup_unparseable"] = len(bad_dates)
    stats["signup_unparseable_values"] = raw.loc[bad_dates.index, "signup_date"].tolist()
    df["signup_date_valid"] = df["signup_date"].notna().astype(int)

    raw_states = df["state"].astype(str)
    df["state"] = df["state"].map(normalize_state)
    stats["state_variants_raw"] = int(raw_states.nunique())
    stats["state_values_changed"] = int((raw_states != df["state"]).sum())
    stats["state_unmapped"] = int(df["state"].isna().sum())
    df["region"] = df["state"].map(STATE_TO_REGION).fillna(df["region"])

    df["email"] = df["email"].fillna("").astype(str).str.strip().str.lower()
    df["email_missing"] = (df["email"] == "").astype(int)
    df.loc[df["email_missing"] == 1, "email"] = None

    # Deduplicate: same normalized name + state is treated as the same person.
    df["_id_num"] = df["customer_id"].str[1:].astype(int)
    df = df.sort_values("_id_num")
    key = ["first_name", "last_name", "state"]
    df["surviving_id"] = df.groupby(key)["customer_id"].transform("first")
    dup_rows = df[df["customer_id"] != df["surviving_id"]]
    id_map = dict(zip(dup_rows["customer_id"], dup_rows["surviving_id"]))
    stats["duplicate_customers"] = len(dup_rows)
    stats["duplicate_examples"] = (
        dup_rows[["customer_id", "surviving_id", "first_name", "last_name", "state"]]
        .head(5).to_dict("records")
    )

    # Survivors inherit an email / signup date from a duplicate if missing.
    survivors = df[df["customer_id"] == df["surviving_id"]].set_index("customer_id")
    for _, dup in dup_rows.iterrows():
        sid = dup["surviving_id"]
        if pd.isna(survivors.at[sid, "email"]) and pd.notna(dup["email"]):
            survivors.at[sid, "email"] = dup["email"]
            survivors.at[sid, "email_missing"] = 0
            stats["emails_recovered_from_duplicates"] = stats.get("emails_recovered_from_duplicates", 0) + 1
    stats.setdefault("emails_recovered_from_duplicates", 0)

    clean = survivors.reset_index()[
        ["customer_id", "first_name", "last_name", "email", "email_missing",
         "signup_date", "signup_date_valid", "state", "region"]
    ].sort_values("customer_id").reset_index(drop=True)
    stats["emails_missing_raw"] = int((raw["email"].astype(str).str.strip() == "").sum())
    stats["emails_missing_clean"] = int(clean["email_missing"].sum())
    stats["customers_clean"] = len(clean)
    return clean, id_map


def clean_orders(raw, id_map, stats):
    df = raw.copy()
    stats["orders_raw"] = len(df)
    raw_dates = df["order_date"].astype(str).str.strip()
    stats["order_dates_non_iso"] = int((~raw_dates.str.match(r"^\d{4}-\d{2}-\d{2}$")).sum())
    df["order_date"] = df["order_date"].map(parse_date)
    stats["order_dates_unparseable"] = int(df["order_date"].isna().sum())
    df = df[df["order_date"].notna()]

    remapped = df["customer_id"].isin(id_map)
    stats["orders_remapped"] = int(remapped.sum())
    df["customer_id"] = df["customer_id"].replace(id_map)

    df["status"] = df["status"].astype(str).str.strip().str.lower()
    df = df.drop_duplicates("order_id")
    stats["orders_clean"] = len(df)
    return df.sort_values(["order_date", "order_id"]).reset_index(drop=True)


def clean_order_items(raw, valid_orders, stats):
    df = raw.copy()
    stats["items_raw"] = len(df)

    is_currency_string = df["unit_price_at_sale"].astype(str).str.contains(r"[$,]", regex=True)
    stats["prices_currency_strings"] = int(is_currency_string.sum())
    df["unit_price_at_sale"] = df["unit_price_at_sale"].map(clean_price)
    stats["prices_unparseable"] = int(df["unit_price_at_sale"].isna().sum())
    df = df[df["unit_price_at_sale"].notna()]

    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    bad_qty = df["quantity"].isna() | (df["quantity"] <= 0)
    stats["items_bad_quantity"] = int(bad_qty.sum())
    stats["items_zero_qty"] = int((df["quantity"] == 0).sum())
    stats["items_negative_qty"] = int((df["quantity"] < 0).sum())
    df = df[~bad_qty].copy()
    df["quantity"] = df["quantity"].astype(int)

    orphan = ~df["order_id"].isin(valid_orders)
    stats["items_orphaned"] = int(orphan.sum())
    df = df[~orphan].copy()

    raw_rev = pd.to_numeric(df["line_revenue"], errors="coerce")
    df["line_revenue"] = (df["quantity"] * df["unit_price_at_sale"]).round(2)
    stats["line_revenue_corrected"] = int(((raw_rev - df["line_revenue"]).abs() > 0.005).sum())
    stats["items_clean"] = len(df)
    stats["orders_without_items"] = len(valid_orders - set(df["order_id"]))
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(stats, customers, orders, items):
    pct = lambda n, d: f"{100 * n / d:.1f}%" if d else "n/a"
    lines = [
        "# Data Quality Report",
        "",
        "Generated by `src/clean_data.py` from the raw export in `data/raw/`.",
        "",
        "## Summary",
        "",
        "| Table | Raw rows | Clean rows | Removed |",
        "|---|---:|---:|---:|",
        f"| customers | {stats['customers_raw']:,} | {stats['customers_clean']:,} | {stats['customers_raw'] - stats['customers_clean']:,} |",
        f"| orders | {stats['orders_raw']:,} | {stats['orders_clean']:,} | {stats['orders_raw'] - stats['orders_clean']:,} |",
        f"| order_items | {stats['items_raw']:,} | {stats['items_clean']:,} | {stats['items_raw'] - stats['items_clean']:,} |",
        "",
        "## Issues found and fixed",
        "",
        "| # | Issue | Records affected | Action taken |",
        "|---|---|---:|---|",
        f"| 1 | Inconsistent name casing (e.g. `MARY`, `mary`) | {stats['names_recased']:,} customers | Converted to title case |",
        f"| 2 | Signup dates not in ISO format (MM/DD/YYYY, DD-Mon-YYYY) | {stats['signup_non_iso']:,} customers | Parsed to YYYY-MM-DD |",
        f"| 3 | Signup dates that could not be parsed | {stats['signup_unparseable']:,} customers | Set to null, flagged with `signup_date_valid = 0` |",
        f"| 4 | Order dates not in ISO format | {stats['order_dates_non_iso']:,} orders ({pct(stats['order_dates_non_iso'], stats['orders_raw'])}) | Parsed to YYYY-MM-DD |",
        f"| 5 | Inconsistent state values ({stats['state_variants_raw']} raw variants for 25 states) | {stats['state_values_changed']:,} customers | Normalized to two-letter codes, region re-derived from state |",
        f"| 6 | Missing email address | {stats['emails_missing_raw']:,} raw rows ({pct(stats['emails_missing_raw'], stats['customers_raw'])}), {stats['emails_missing_clean']:,} after dedup ({pct(stats['emails_missing_clean'], stats['customers_clean'])}) | Kept rows, flagged with `email_missing = 1` |",
        f"| 7 | Near-duplicate customer records (same name + state, different casing) | {stats['duplicate_customers']:,} customers | Merged into the earliest `customer_id` |",
        f"| 8 | Orders pointing at a removed duplicate customer | {stats['orders_remapped']:,} orders | Remapped to the surviving `customer_id` |",
        f"| 9 | Prices stored as currency strings (e.g. `$24.99`) | {stats['prices_currency_strings']:,} line items ({pct(stats['prices_currency_strings'], stats['items_raw'])}) | Stripped `$` and `,`, cast to float |",
        f"| 10 | Zero or negative quantity | {stats['items_bad_quantity']:,} line items ({stats['items_zero_qty']} zero, {stats['items_negative_qty']} negative) | Dropped |",
        f"| 11 | Stored line_revenue inconsistent with quantity x price | {stats['line_revenue_corrected']:,} line items | Recomputed as `quantity * unit_price_at_sale` |",
        "",
        "## Notes",
        "",
        f"* Unparseable signup date values seen: {', '.join('`' + str(v) + '`' for v in stats['signup_unparseable_values']) or 'none'}.",
        f"* Emails recovered by merging a duplicate record into its survivor: {stats['emails_recovered_from_duplicates']}.",
        f"* Order dates that failed to parse: {stats['order_dates_unparseable']}. Prices that failed to parse: {stats['prices_unparseable']}.",
        f"* Line items referencing an unknown order: {stats['items_orphaned']}.",
        f"* Orders left with no valid line items after dropping bad quantities: {stats['orders_without_items']:,}. "
        "The order headers are kept (the transaction happened) but they carry no revenue.",
        "",
        "### Example duplicate merges",
        "",
        "| Removed id | Kept id | Name | State |",
        "|---|---|---|---|",
    ]
    for ex in stats["duplicate_examples"]:
        lines.append(f"| {ex['customer_id']} | {ex['surviving_id']} | {ex['first_name']} {ex['last_name']} | {ex['state']} |")

    lines += [
        "",
        "## Clean dataset at a glance",
        "",
        f"* Customers: {len(customers):,} unique",
        f"* Orders: {len(orders):,} between {orders['order_date'].min()} and {orders['order_date'].max()}",
        f"* Line items: {len(items):,}",
        f"* Gross line revenue (all statuses): ${items['line_revenue'].sum():,.2f}",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    stats = {}

    stores = pd.read_csv(RAW_DIR / "stores.csv", dtype=str)
    stores["state"] = stores["state"].map(normalize_state)
    stores["open_date"] = stores["open_date"].map(parse_date)

    products = pd.read_csv(RAW_DIR / "products.csv", dtype=str)
    products["base_price"] = products["base_price"].map(clean_price)

    customers, id_map = clean_customers(pd.read_csv(RAW_DIR / "customers.csv", dtype=str, keep_default_na=False), stats)
    orders = clean_orders(pd.read_csv(RAW_DIR / "orders.csv", dtype=str), id_map, stats)
    items = clean_order_items(pd.read_csv(RAW_DIR / "order_items.csv", dtype=str),
                              set(orders["order_id"]), stats)

    stores.to_csv(PROCESSED_DIR / "stores.csv", index=False)
    products.to_csv(PROCESSED_DIR / "products.csv", index=False)
    customers.to_csv(PROCESSED_DIR / "customers.csv", index=False)
    orders.to_csv(PROCESSED_DIR / "orders.csv", index=False)
    items.to_csv(PROCESSED_DIR / "order_items.csv", index=False)
    write_report(stats, customers, orders, items)

    print(f"Wrote cleaned data to {PROCESSED_DIR.relative_to(ROOT)}/")
    print(f"  customers    {stats['customers_raw']:>7,} -> {stats['customers_clean']:>7,}  "
          f"({stats['duplicate_customers']} duplicates merged, {stats['emails_missing_clean']} missing emails flagged)")
    print(f"  orders       {stats['orders_raw']:>7,} -> {stats['orders_clean']:>7,}  "
          f"({stats['order_dates_non_iso']:,} dates reformatted, {stats['orders_remapped']} remapped to surviving customer)")
    print(f"  order_items  {stats['items_raw']:>7,} -> {stats['items_clean']:>7,}  "
          f"({stats['prices_currency_strings']:,} currency strings fixed, {stats['items_bad_quantity']} bad quantities dropped)")
    print(f"  report       {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
