"""Validate the legacy Google Sheets CSV without changing its contents."""

import csv
import io

HEADERS = ("Date", "Order No.", "DN Line", "Items", "Link", "Nominal", "Arrived Date", "Status", "Box")


def parse_csv_orders(text):
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")), strict=True)
    try:
        headers = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise ValueError("The CSV has no readable header.") from exc
    if len(headers) < len(HEADERS) or headers[0].strip() not in ("Date", "8") or tuple(headers[1:9]) != HEADERS[1:]:
        raise ValueError("CSV headers must match the original Orders sheet exactly.")
    records = []
    try:
        for values in reader:
            if len(records) >= 1000:
                raise ValueError("Import at most 1,000 rows at a time.")
            if len(values) != len(headers):
                raise ValueError(f"Row {reader.line_num} has the wrong number of columns.")
            row = dict(zip(HEADERS, values[:len(HEADERS)]))
            if len(values) > len(HEADERS):
                row["Extra columns"] = [{"header": header, "value": value} for header, value in zip(headers[9:], values[9:])]
            number = row["Order No."].strip()
            if not number.isascii() or not number.isdigit() or not 0 < int(number) <= 9223372036854775807:
                raise ValueError(f"Row {reader.line_num} needs a positive Order No.")
            number = int(number)
            if not row["DN Line"].strip():
                raise ValueError(f"Row {reader.line_num} needs a DN Line display name.")
            records.append(row)
    except csv.Error as exc:
        raise ValueError("The CSV could not be read. Check its formatting.") from exc
    if not records:
        raise ValueError("The CSV contains no order rows.")
    return records
