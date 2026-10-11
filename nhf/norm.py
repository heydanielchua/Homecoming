"""Parsing and normalising the shapes the HDB datasets actually come in.

The resale files span 2000 to now and are not consistent: older files carry
`lease_commence_date` but no remaining lease, newer ones carry
`remaining_lease` as free text like "61 years 04 months". Street names are
abbreviated ("ANG MO KIO AVE 10") and neither HDB dataset has a block id, so
a block is keyed by its block number and street name.
"""

from __future__ import annotations

import re
from datetime import date

HDB_LEASE_YEARS = 99

_WS = re.compile(r"\s+")
_LEASE_TEXT = re.compile(r"(?:(\d+)\s*years?)?\s*(?:(\d+)\s*months?)?", re.I)
_STOREY = re.compile(r"(\d+)\s*(?:TO|-|–)\s*(\d+)", re.I)


def clean(value: object) -> str:
    """Upper-case, collapse whitespace, strip punctuation noise."""
    text = "" if value is None else str(value)
    return _WS.sub(" ", text.replace(".", " ").replace(",", " ")).strip().upper()


def block_key(block: object, street_name: object) -> str:
    """Stable block identity shared by every dataset and the app."""
    return f"{clean(block)}|{clean(street_name)}"


def parse_month(value: object) -> str | None:
    """Normalise a month field to YYYY-MM.

    Handles "2017-01", "2017-1", "Jan-2017" and "2017-01-01".
    """
    text = str(value or "").strip()
    if not text:
        return None
    m = re.match(r"^(\d{4})[-/](\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.match(r"^([A-Za-z]{3})[-/\s](\d{4})$", text)
    if m:
        months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        try:
            month = months.index(m.group(1).upper()) + 1
        except ValueError:
            return None
        return f"{int(m.group(2)):04d}-{month:02d}"
    return None


def month_to_quarter(month: str) -> str:
    year, mm = month.split("-")
    return f"{year}-Q{(int(mm) - 1) // 3 + 1}"


def parse_quarter(value: object) -> str | None:
    """Normalise "2024-Q1", "2024Q1", "1Q2024" or "2024-01" to YYYY-Qn."""
    text = str(value or "").strip().upper().replace(" ", "")
    if not text:
        return None
    m = re.match(r"^(\d{4})-?Q([1-4])$", text)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"
    m = re.match(r"^([1-4])Q(\d{4})$", text)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"
    month = parse_month(text)
    return month_to_quarter(month) if month else None


def quarter_index(quarter: str) -> int:
    """Quarters as a single integer, so differences are easy."""
    year, q = quarter.split("-Q")
    return int(year) * 4 + int(q) - 1


def month_index(month: str) -> int:
    year, mm = month.split("-")
    return int(year) * 12 + int(mm) - 1


def months_between(earlier: str, later: str) -> int:
    return month_index(later) - month_index(earlier)


def parse_storey_range(value: object) -> tuple[int | None, int | None]:
    """"10 TO 12" -> (10, 12). Older files use "10 TO 12" too, some use a dash."""
    text = clean(value)
    m = _STOREY.search(text)
    if m:
        low, high = int(m.group(1)), int(m.group(2))
        return (min(low, high), max(low, high))
    m = re.search(r"(\d+)", text)
    if m:
        return (int(m.group(1)), int(m.group(1)))
    return (None, None)


def parse_lease_commence_year(value: object) -> int | None:
    m = re.search(r"(\d{4})", str(value or ""))
    return int(m.group(1)) if m else None


def parse_remaining_lease_months(value: object) -> int | None:
    """"61 years 04 months" -> 736. A bare number is read as whole years."""
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(round(float(text) * 12))
    m = _LEASE_TEXT.search(text)
    if not m or not (m.group(1) or m.group(2)):
        return None
    years = int(m.group(1) or 0)
    months = int(m.group(2) or 0)
    return years * 12 + months


def remaining_lease_months(
    lease_commence_year: int | None,
    as_of_month: str | None = None,
    lease_years: int = HDB_LEASE_YEARS,
) -> int | None:
    """Lease left at `as_of_month`, from the lease commencement year.

    Used for the older files, which carry no remaining-lease field, and to
    project a lease forward to a purchase or exit date.
    """
    if not lease_commence_year:
        return None
    if as_of_month is None:
        today = date.today()
        as_of_month = f"{today.year:04d}-{today.month:02d}"
    start = f"{lease_commence_year:04d}-01"
    used = months_between(start, as_of_month)
    return max(0, lease_years * 12 - used)


def flat_type_rooms(flat_type: object) -> int | None:
    """Rooms in an HDB flat type label, for the "3-room or larger" rule.

    EXECUTIVE and MULTI-GENERATION are larger than 5-room, so they sort above
    it rather than being dropped for having no digit.
    """
    text = clean(flat_type)
    if not text:
        return None
    if "MULTI" in text:
        return 7
    if "EXECUTIVE" in text:
        return 6
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else None


def canon_flat_type(flat_type: object) -> str:
    """"3 ROOM", "3-ROOM", "3 room" all become "3 ROOM"."""
    text = clean(flat_type).replace("-", " ")
    return _WS.sub(" ", text).strip()


def to_float(value: object) -> float | None:
    try:
        text = str(value).replace(",", "").replace("$", "").strip()
        return float(text) if text not in ("", "NA", "-", "None") else None
    except (TypeError, ValueError):
        return None


def to_int(value: object) -> int | None:
    f = to_float(value)
    return int(round(f)) if f is not None else None
