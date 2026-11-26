"""Filtering and date interpretation for dashboard orders."""

import re
from datetime import date, datetime


def imported_date(value: str, assumed_year=None):
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", value)
    if not match:
        return None
    day, month, year = match.groups()
    year = int(year) if year else assumed_year
    if year is None:
        return None
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def within_dates(value, start, end):
    if not start and not end:
        return True
    return value is not None and (not start or value >= start) and (not end or value <= end)
