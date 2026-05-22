"""Interactive stop entry for Truer Foods dispatch.

Type each stop and the delivery zone is detected live from the address's
postal code. Stops are appended to orders.csv (or --file). When you
finish, you can generate the route Excel right away.

Speed-entry: type 'Customer @ Address' on the first prompt to enter both
at once. Press Enter on a blank customer (or type 'done') to finish.

Usage:
    python add.py                       # appends to orders.csv
    python add.py --file today.csv      # use a different file
    python add.py --no-dispatch         # skip the dispatch prompt at the end
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import dispatch as dispatch_mod
from zones import extract_fsa, zone_for

CSV_COLUMNS = ["customer", "address", "postal_code", "boxes",
               "product_type", "window", "notes"]
DONE_TOKENS = {"done", "quit", "exit", "q"}


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return ""
    return val or default


def _read_existing(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in CSV_COLUMNS})


def prompt_stop(idx: int) -> dict | None:
    """Prompt for one stop. Returns None to signal end of entry."""
    print(f"\n--- Stop #{idx} (blank or 'done' to finish) ---")
    raw = _ask("Customer (or 'Customer @ Address' to speed-enter)")
    if not raw or raw.lower() in DONE_TOKENS:
        return None

    if "@" in raw:
        customer, address = (p.strip() for p in raw.split("@", 1))
        if not address:
            address = _ask("Address")
    else:
        customer = raw
        address = _ask("Address")

    if not address:
        print("  (skipped: no address given)")
        return prompt_stop(idx)

    fsa = extract_fsa(address) or ""
    zone, _ = zone_for(address)
    if zone == "Out of Area":
        print("  ! No recognized postal code in address — will land in 'Out of Area' "
              "at the end of the last route. Add the postal code to fix.")
    else:
        print(f"  -> Zone: {zone}" + (f" (FSA {fsa})" if fsa else ""))

    boxes = _ask("Boxes", "1")
    product = _ask("Product (frozen/fresh/dry/mixed)")
    window = _ask("Time window (e.g. AM only, after 2pm)")
    notes = _ask("Notes")
    return {
        "customer": customer,
        "address": address,
        "postal_code": fsa,
        "boxes": boxes,
        "product_type": product,
        "window": window,
        "notes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Interactive order entry for Truer Foods dispatch")
    p.add_argument("--file", type=Path, default=Path("orders.csv"),
                   help="CSV to append to (default orders.csv)")
    p.add_argument("--no-dispatch", action="store_true",
                   help="Don't ask to generate routes at the end")
    args = p.parse_args(argv)

    rows = _read_existing(args.file)
    if rows:
        print(f"Loaded {len(rows)} existing stop(s) from {args.file}.")
    else:
        print(f"Starting a new order list at {args.file}.")
    print("Tip: type 'Customer @ Address' to enter both at once.")

    idx = len(rows) + 1
    added = 0
    while True:
        stop = prompt_stop(idx)
        if stop is None:
            break
        rows.append(stop)
        _write_rows(args.file, rows)
        idx += 1
        added += 1

    print(f"\nDone. {added} new stop(s) added. {len(rows)} total in {args.file}.")
    if added == 0 or args.no_dispatch:
        return 0

    if _ask("Generate route Excel now? (y/N)", "n").lower().startswith("y"):
        drivers_in = _ask("How many drivers?", "1")
        try:
            drivers = max(1, int(drivers_in))
        except ValueError:
            drivers = 1
        out = _ask("Output filename", "routes.xlsx") or "routes.xlsx"
        return dispatch_mod.main([str(args.file), "--drivers", str(drivers), "--out", out])
    return 0


if __name__ == "__main__":
    sys.exit(main())
