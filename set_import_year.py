"""Assign a year to matching imported CSV rows without changing original data."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import tempfile

from csv_orders import parse_csv_orders


def row_key(row):
    return json.dumps(row, sort_keys=True, ensure_ascii=False).replace("\\r\\n", "\\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", default="/private/tmp")
    args = parser.parse_args()
    if not 1 <= args.year <= 9999:
        parser.error("Year must be between 1 and 9999.")
    if not Path(args.database).is_file():
        raise SystemExit("Database file does not exist.")
    csv_rows = parse_csv_orders(Path(args.csv).read_text(encoding="utf-8-sig"))
    counts = Counter(row_key(row) for row in csv_rows)
    with sqlite3.connect(args.database, timeout=10) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(imported_orders)")}
        if not columns:
            raise SystemExit("The database has no imported orders table.")
        year_column = "assumed_year" if "assumed_year" in columns else "NULL"
        records = db.execute(f"SELECT id, original_json, {year_column} FROM imported_orders ORDER BY id").fetchall()
        matches = []
        already_assigned = 0
        for order_id, raw, current in records:
            key = row_key(json.loads(raw))
            if counts[key]:
                counts[key] -= 1
                if current is None:
                    matches.append((args.year, order_id))
                else:
                    already_assigned += 1
        unmatched = sum(counts.values())
        print(f"Matching rows to update: {len(matches)}; already assigned: {already_assigned}; CSV rows not matched: {unmatched}.")
        if not args.apply:
            return
        if unmatched:
            raise SystemExit("Not every CSV row matched; nothing was changed.")
        if not matches:
            print("Nothing to update.")
            return
        with tempfile.NamedTemporaryFile(prefix="nitipyuq-backup-", suffix=".sqlite3", dir=args.backup_dir, delete=False) as backup_file:
            backup_path = backup_file.name
        with sqlite3.connect(backup_path) as backup:
            db.backup(backup)
        db.execute("BEGIN IMMEDIATE")
        if "assumed_year" not in columns:
            db.execute("ALTER TABLE imported_orders ADD COLUMN assumed_year INTEGER")
        db.executemany("UPDATE imported_orders SET assumed_year = ? WHERE id = ? AND assumed_year IS NULL", matches)
        db.commit()
        print(f"Updated {len(matches)} rows. Database backup: {backup_path}")


if __name__ == "__main__":
    main()
