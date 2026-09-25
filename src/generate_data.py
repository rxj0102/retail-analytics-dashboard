"""
Generate a synthetic, intentionally messy retail sales dataset.

The output mimics a raw export from a point-of-sale / CRM system for a
mid-sized home and lifestyle retailer with 10 stores. Several realistic data
quality problems are injected on purpose so that clean_data.py has real work
to do:

* mixed date formats (YYYY-MM-DD, MM/DD/YYYY, DD-Mon-YYYY)
* inconsistent state formatting ("NY", "ny", "New York", " NY")
* missing customer emails (~7%)
* near-duplicate customer records (same person, different name casing)
* line items with zero or negative quantity (~2%)
* prices stored as currency strings like "$24.99" (~10%)
* stale line_revenue values computed before discounts were applied (~4%)
* a week-long order outage at one Midwest store in Feb 2025

Only the Python standard library is used. A fixed seed keeps output
reproducible.

Usage:
    python src/generate_data.py
"""

import bisect
import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"

START_DATE = date(2024, 1, 1)
END_DATE = date(2025, 12, 31)

# Store that goes dark for a week (simulated POS outage).
OUTAGE_STORE_ID = "S06"
OUTAGE_START = date(2025, 2, 10)
OUTAGE_END = date(2025, 2, 16)

N_UNIQUE_CUSTOMERS = 505
N_DUPLICATE_CUSTOMERS = 15
BASE_ORDERS_PER_DAY = 24

REGION_STATES = {
    "Northeast": ["NY", "MA", "PA", "NJ", "CT"],
    "Southeast": ["FL", "GA", "NC", "VA", "TN"],
    "Midwest": ["IL", "OH", "MI", "MN", "WI"],
    "Southwest": ["TX", "AZ", "NM", "OK", "NV"],
    "West": ["CA", "WA", "OR", "CO", "UT"],
}

STATE_NAMES = {
    "NY": "New York", "MA": "Massachusetts", "PA": "Pennsylvania",
    "NJ": "New Jersey", "CT": "Connecticut", "FL": "Florida",
    "GA": "Georgia", "NC": "North Carolina", "VA": "Virginia",
    "TN": "Tennessee", "IL": "Illinois", "OH": "Ohio", "MI": "Michigan",
    "MN": "Minnesota", "WI": "Wisconsin", "TX": "Texas", "AZ": "Arizona",
    "NM": "New Mexico", "OK": "Oklahoma", "NV": "Nevada",
    "CA": "California", "WA": "Washington", "OR": "Oregon",
    "CO": "Colorado", "UT": "Utah",
}

# store_id, name, city, state, region, open_date, relative traffic weight
STORES = [
    ("S01", "Manhattan Flagship", "New York", "NY", "Northeast", "2015-03-14", 1.35),
    ("S02", "Boston Back Bay", "Boston", "MA", "Northeast", "2017-09-02", 0.95),
    ("S03", "Atlanta Midtown", "Atlanta", "GA", "Southeast", "2016-05-21", 1.05),
    ("S04", "Miami Brickell", "Miami", "FL", "Southeast", "2019-11-09", 0.90),
    ("S05", "Chicago Loop", "Chicago", "IL", "Midwest", "2014-06-01", 1.20),
    ("S06", "Columbus Short North", "Columbus", "OH", "Midwest", "2020-04-18", 0.85),
    ("S07", "Dallas Uptown", "Dallas", "TX", "Southwest", "2018-08-11", 1.00),
    ("S08", "Phoenix Scottsdale", "Phoenix", "AZ", "Southwest", "2021-02-27", 0.80),
    ("S09", "Los Angeles Arts District", "Los Angeles", "CA", "West", "2016-10-15", 1.25),
    ("S10", "Seattle Capitol Hill", "Seattle", "WA", "West", "2019-03-30", 0.90),
]

# product_id, name, category, base price
PRODUCTS = [
    ("P001", "Linen Throw Blanket", "Home Goods", 49.99),
    ("P002", "Ceramic Table Lamp", "Home Goods", 79.00),
    ("P003", "Scented Soy Candle Set", "Home Goods", 24.99),
    ("P004", "Woven Storage Basket", "Home Goods", 34.50),
    ("P005", "Framed Wall Mirror", "Home Goods", 129.00),
    ("P006", "Wireless Earbuds", "Electronics", 89.99),
    ("P007", "Bluetooth Speaker", "Electronics", 59.99),
    ("P008", "Smart LED Bulb 4-Pack", "Electronics", 39.99),
    ("P009", "Portable Power Bank", "Electronics", 29.99),
    ("P010", "Ultrabook Laptop 14in", "Electronics", 1099.00),
    ("P011", "Organic Cotton Tee", "Apparel", 22.00),
    ("P012", "Merino Wool Sweater", "Apparel", 94.00),
    ("P013", "Waterproof Rain Jacket", "Apparel", 139.00),
    ("P014", "Everyday Canvas Sneakers", "Apparel", 64.99),
    ("P015", "Relaxed Fit Chinos", "Apparel", 58.00),
    ("P016", "Cast Iron Skillet 12in", "Kitchen", 44.95),
    ("P017", "Chef Knife 8in", "Kitchen", 69.00),
    ("P018", "Pour Over Coffee Set", "Kitchen", 38.00),
    ("P019", "Stand Mixer", "Kitchen", 349.99),
    ("P020", "Glass Meal Prep Containers", "Kitchen", 27.99),
    ("P021", "Two Person Camping Tent", "Outdoor", 179.00),
    ("P022", "Insulated Water Bottle", "Outdoor", 32.00),
    ("P023", "Trail Daypack 24L", "Outdoor", 84.00),
    ("P024", "Folding Camp Chair", "Outdoor", 45.00),
    ("P025", "Portable Propane Grill", "Outdoor", 219.00),
]

# How often each product shows up in a basket (cheap items sell more often).
PRODUCT_POPULARITY = {
    "P001": 1.1, "P002": 0.7, "P003": 1.8, "P004": 1.0, "P005": 0.4,
    "P006": 1.0, "P007": 0.9, "P008": 1.2, "P009": 1.4, "P010": 0.15,
    "P011": 1.9, "P012": 0.8, "P013": 0.6, "P014": 1.0, "P015": 0.9,
    "P016": 0.9, "P017": 0.7, "P018": 0.9, "P019": 0.25, "P020": 1.3,
    "P021": 0.35, "P022": 1.6, "P023": 0.6, "P024": 0.8, "P025": 0.3,
}

# Category seasonality: Outdoor peaks in summer, Apparel/Electronics at holidays.
CATEGORY_SEASON = {
    "Outdoor": {4: 1.4, 5: 1.8, 6: 2.0, 7: 1.9, 8: 1.5, 11: 0.6, 12: 0.7, 1: 0.5, 2: 0.5},
    "Electronics": {11: 1.6, 12: 1.8},
    "Apparel": {9: 1.2, 10: 1.2, 11: 1.3, 12: 1.4},
}

MONTH_LIFT = {1: 0.85, 2: 0.88, 3: 0.97, 4: 1.0, 5: 1.05, 6: 1.05, 7: 1.0,
              8: 1.02, 9: 0.96, 10: 1.02, 11: 1.45, 12: 1.75}
WEEKDAY_LIFT = {0: 0.85, 1: 0.85, 2: 0.9, 3: 0.95, 4: 1.1, 5: 1.35, 6: 1.2}

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
    "Linda", "David", "Elizabeth", "William", "Barbara", "Richard", "Susan",
    "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Daniel",
    "Lisa", "Matthew", "Nancy", "Anthony", "Betty", "Mark", "Sandra",
    "Steven", "Ashley", "Andrew", "Kimberly", "Joshua", "Emily", "Kevin",
    "Donna", "Brian", "Michelle", "George", "Carol", "Timothy", "Amanda",
    "Ryan", "Melissa", "Jacob", "Deborah", "Nicole", "Priya", "Wei", "Carlos",
    "Sofia", "Omar", "Aisha", "Hiroshi", "Mei", "Diego", "Fatima", "Noah",
    "Olivia", "Liam", "Emma", "Mateo", "Ava", "Elena", "Kwame", "Zara",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
    "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green",
    "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Patel", "Chen", "Kim", "Okafor", "Singh", "Murphy", "Tanaka", "Cohen",
]

EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "icloud.com", "hotmail.com"]

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def messy_date(d, rng):
    """Render a date in one of three formats, mostly ISO."""
    r = rng.random()
    if r < 0.78:
        return d.isoformat()
    if r < 0.90:
        return f"{d.month:02d}/{d.day:02d}/{d.year}"
    return f"{d.day:02d}-{MONTH_ABBR[d.month - 1]}-{d.year}"


def messy_state(code, rng):
    """Render a state code the way different clerks might type it."""
    r = rng.random()
    if r < 0.70:
        return code
    if r < 0.78:
        return code.lower()
    if r < 0.86:
        return STATE_NAMES[code]
    if r < 0.91:
        return STATE_NAMES[code].lower()
    if r < 0.96:
        return " " + code
    return code + " "


def messy_case(name, rng, p=0.06):
    """Occasionally store a name in all lower or all upper case."""
    r = rng.random()
    if r < p / 2:
        return name.lower()
    if r < p:
        return name.upper()
    return name


def random_date(start, end, rng):
    return start + timedelta(days=rng.randint(0, (end - start).days))


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def build_customers(rng):
    """Return (customer rows, list of (signup_date, customer_id, home_store))."""
    region_of_state = {s: reg for reg, states in REGION_STATES.items() for s in states}
    stores_by_region = {}
    for s in STORES:
        stores_by_region.setdefault(s[4], []).append(s[0])

    seen = set()
    people = []
    while len(people) < N_UNIQUE_CUSTOMERS:
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        region = rng.choice(list(REGION_STATES))
        state = rng.choice(REGION_STATES[region])
        key = (first, last, state)
        if key in seen:
            continue
        seen.add(key)
        signup = random_date(date(2021, 6, 1), date(2025, 10, 31), rng)
        people.append({"first": first, "last": last, "state": state,
                       "region": region, "signup": signup})

    # Sort by signup so customer ids increase over time, like a real CRM.
    people.sort(key=lambda p: p["signup"])
    for i, p in enumerate(people, start=1):
        p["id"] = f"C{i:04d}"
        p["email"] = (f"{p['first'].lower()}.{p['last'].lower()}"
                      f"{rng.randint(1, 99)}@{rng.choice(EMAIL_DOMAINS)}")

    # Near duplicates: the same person re-entered later with different casing.
    dup_sources = rng.sample(people[:-60], N_DUPLICATE_CUSTOMERS)
    next_id = len(people) + 1
    duplicates = []
    for src in dup_sources:
        style = rng.choice(["upper", "lower", "mixed"])
        if style == "upper":
            first, last = src["first"].upper(), src["last"].upper()
        elif style == "lower":
            first, last = src["first"].lower(), src["last"].lower()
        else:
            first, last = src["first"].lower(), src["last"].upper()
        signup = src["signup"] + timedelta(days=rng.randint(30, 400))
        signup = min(signup, date(2025, 11, 30))
        duplicates.append({
            "id": f"C{next_id:04d}", "first": first, "last": last,
            "state": src["state"], "region": src["region"], "signup": signup,
            "email": src["email"] if rng.random() < 0.5 else "",
            "is_dup": True,
        })
        next_id += 1

    all_customers = people + duplicates

    rows = []
    pool = []
    for p in all_customers:
        email = p["email"]
        if not p.get("is_dup") and rng.random() < 0.07:
            email = ""
        first = p["first"] if p.get("is_dup") else messy_case(p["first"], rng)
        last = p["last"] if p.get("is_dup") else messy_case(p["last"], rng)
        signup_raw = messy_date(p["signup"], rng)
        rows.append([p["id"], first, last, email, signup_raw,
                     messy_state(p["state"], rng), region_of_state[p["state"]]])
        home_region_stores = stores_by_region[p["region"]]
        pool.append((p["signup"], p["id"], home_region_stores))

    # A handful of signup dates were typed in free text and cannot be parsed.
    for idx in rng.sample(range(len(rows)), 3):
        rows[idx][4] = rng.choice(["unknown", "N/A", "2024-13-45"])

    rng.shuffle(rows)
    pool.sort()
    return rows, pool


def build_orders(rng, customer_pool):
    store_ids = [s[0] for s in STORES]
    store_weights = [s[6] for s in STORES]
    product_ids = [p[0] for p in PRODUCTS]
    product_info = {p[0]: p for p in PRODUCTS}

    signup_dates = [c[0] for c in customer_pool]

    orders, items = [], []
    order_num, item_num = 100000, 500000
    outage_manual_entry_done = False
    d = START_DATE
    while d <= END_DATE:
        growth = 1.08 if d.year == 2025 else 1.0
        expected = BASE_ORDERS_PER_DAY * MONTH_LIFT[d.month] * WEEKDAY_LIFT[d.weekday()] * growth
        n_orders = max(0, int(rng.gauss(expected, expected ** 0.5)))

        eligible = bisect.bisect_right(signup_dates, d)
        for _ in range(n_orders):
            if eligible == 0:
                break
            _, customer_id, home_stores = customer_pool[rng.randrange(eligible)]
            if rng.random() < 0.82:
                store_id = rng.choice(home_stores)
            else:
                store_id = rng.choices(store_ids, weights=store_weights)[0]
            # Simulated POS outage: the store records essentially nothing.
            # One order was keyed in manually mid-week, so it is near zero, not zero.
            if store_id == OUTAGE_STORE_ID and OUTAGE_START <= d <= OUTAGE_END:
                if d != date(2025, 2, 13) or outage_manual_entry_done:
                    continue
                outage_manual_entry_done = True

            r = rng.random()
            status = "completed" if r < 0.88 else ("refunded" if r < 0.95 else "cancelled")
            order_num += 1
            order_id = f"O{order_num}"
            orders.append([order_id, customer_id, store_id, messy_date(d, rng), status])

            weights = []
            for pid in product_ids:
                cat = product_info[pid][2]
                weights.append(PRODUCT_POPULARITY[pid] * CATEGORY_SEASON.get(cat, {}).get(d.month, 1.0))
            n_lines = rng.choices([1, 2, 3, 4], weights=[45, 32, 16, 7])[0]
            chosen = set()
            for _ in range(n_lines):
                pid = rng.choices(product_ids, weights=weights)[0]
                if pid in chosen:
                    continue
                chosen.add(pid)
                base = product_info[pid][3]
                discount = rng.choices([1.0, 0.9, 0.8, 0.75],
                                       weights=[70, 18, 8, 4])[0]
                if d.month in (11, 12) and rng.random() < 0.35:
                    discount = min(discount, 0.8)
                price = round(base * discount, 2)
                qty = rng.choices([1, 2, 3, 4], weights=[70, 20, 7, 3])[0]
                if rng.random() < 0.02:
                    qty = rng.choice([0, 0, -1, -2])
                line_revenue = round(qty * price, 2)
                # Some exports carry a stale revenue figure computed before discounts.
                if rng.random() < 0.04:
                    line_revenue = round(qty * base, 2)
                if rng.random() < 0.10:
                    price_out = f"${price:,.2f}"
                else:
                    price_out = f"{price:.2f}"
                item_num += 1
                items.append([f"L{item_num}", order_id, pid, qty, price_out, f"{line_revenue:.2f}"])
        d += timedelta(days=1)

    return orders, items


def main():
    rng = random.Random(SEED)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    write_csv(RAW_DIR / "stores.csv",
              ["store_id", "store_name", "city", "state", "region", "open_date"],
              [s[:6] for s in STORES])
    write_csv(RAW_DIR / "products.csv",
              ["product_id", "product_name", "category", "base_price"],
              [[p[0], p[1], p[2], f"{p[3]:.2f}"] for p in PRODUCTS])

    customer_rows, pool = build_customers(rng)
    write_csv(RAW_DIR / "customers.csv",
              ["customer_id", "first_name", "last_name", "email", "signup_date", "state", "region"],
              customer_rows)

    orders, items = build_orders(rng, pool)
    write_csv(RAW_DIR / "orders.csv",
              ["order_id", "customer_id", "store_id", "order_date", "status"], orders)
    write_csv(RAW_DIR / "order_items.csv",
              ["order_item_id", "order_id", "product_id", "quantity", "unit_price_at_sale", "line_revenue"],
              items)

    print(f"Wrote raw data to {RAW_DIR.relative_to(ROOT)}/")
    print(f"  stores.csv       {len(STORES):>7,} rows")
    print(f"  products.csv     {len(PRODUCTS):>7,} rows")
    print(f"  customers.csv    {len(customer_rows):>7,} rows")
    print(f"  orders.csv       {len(orders):>7,} rows")
    print(f"  order_items.csv  {len(items):>7,} rows")


if __name__ == "__main__":
    main()
