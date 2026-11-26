"""Validate the legacy Google Sheets CSV without changing its contents."""

import csv
import io

HEADERS = ("Date", "Order No.", "DN Line", "Items", "Link", "Nominal", "Arrived Date", "Status", "Box")


def parse_csv_orders(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), strict=True)
    if reader.fieldnames != list(HEADERS):
        raise ValueError("CSV headers must match the original Orders sheet exactly.")
    records = []
    numbers = set()
    try:
        for row in reader:
            if len(records) >= 1000:
                raise ValueError("Import at most 1,000 rows at a time.")
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Row {reader.line_num} has the wrong number of columns.")
            number = row["Order No."].strip()
            if not number.isascii() or not number.isdigit() or not 0 < int(number) <= 9223372036854775807:
                raise ValueError(f"Row {reader.line_num} needs a positive Order No.")
            number = int(number)
            if number in numbers:
                raise ValueError(f"Order No. {number} appears more than once in the CSV.")
            if not row["DN Line"].strip():
                raise ValueError(f"Row {reader.line_num} needs a DN Line display name.")
            numbers.add(number)
            records.append(row)
    except csv.Error as exc:
        raise ValueError("The CSV could not be read. Check its formatting.") from exc
    if not records:
        raise ValueError("The CSV contains no order rows.")
    return records
