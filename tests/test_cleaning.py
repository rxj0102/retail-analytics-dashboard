"""
Tests for the cleaning helpers in src/clean_data.py.

No pytest needed. Run standalone:
    python tests/test_cleaning.py
(pytest will also discover the test_ functions if it is installed.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clean_data import clean_price, normalize_name, normalize_state, parse_date  # noqa: E402


def test_parse_date_supported_formats():
    assert parse_date("2025-02-14") == "2025-02-14"
    assert parse_date("02/14/2025") == "2025-02-14"
    assert parse_date("14-Feb-2025") == "2025-02-14"
    assert parse_date("  03/01/2024 ") == "2024-03-01"
    assert parse_date("29-Feb-2024") == "2024-02-29"  # leap day


def test_parse_date_rejects_bad_values():
    assert parse_date("unknown") is None
    assert parse_date("N/A") is None
    assert parse_date("2024-13-45") is None  # impossible month and day
    assert parse_date("29-Feb-2025") is None  # not a leap year
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date(float("nan")) is None


def test_normalize_state_variants():
    for raw in ["NY", "ny", " NY", "NY ", "New York", "new york", "NEW YORK", " new  york "]:
        assert normalize_state(raw) == "NY", raw
    assert normalize_state("north carolina") == "NC"
    assert normalize_state("Washington") == "WA"


def test_normalize_state_rejects_unknown():
    assert normalize_state("XX") is None
    assert normalize_state("Atlantis") is None
    assert normalize_state("") is None
    assert normalize_state(None) is None


def test_clean_price():
    assert clean_price("$24.99") == 24.99
    assert clean_price("24.99") == 24.99
    assert clean_price(" $1,099.00 ") == 1099.0
    assert clean_price(19.5) == 19.5
    assert clean_price(7) == 7.0
    assert isinstance(clean_price("$5"), float)


def test_clean_price_rejects_bad_values():
    assert clean_price("") is None
    assert clean_price("$") is None
    assert clean_price("free") is None
    assert clean_price(None) is None


def test_normalize_name():
    assert normalize_name("mARY") == "Mary"
    assert normalize_name("  JOHN ") == "John"
    assert normalize_name("mary ann") == "Mary Ann"
    assert normalize_name(None) == ""


def main():
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}  {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
