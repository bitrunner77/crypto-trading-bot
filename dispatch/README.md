# Truer Foods Dispatch

Daily route builder for Truer Foods (Richmond, BC). Takes a CSV of
stops, sorts them into a sensible Vancouver-area drive order, splits
them across however many drivers you have today, and produces an Excel
workbook with one route sheet per driver plus a summary tab.

## One-time setup

```bash
pip install -r requirements.txt
```

## Daily use — three ways

### Option A: Screenshot or paste (fastest when orders come in messy)

If an order comes in as a phone-camera photo, an email screenshot, a
clip of a Google Sheet, or just text on the clipboard, hand it to
Claude:

```bash
python extract.py --image screenshot.png        # PNG/JPG/WEBP/GIF
python extract.py --text orders.txt             # any text file
python extract.py --paste                       # paste, then Ctrl+D
```

The tool sends the input to Claude, gets back a clean list of stops,
prints them for review (with the detected zone for each), and asks
before appending to `orders.csv`. Then it offers to generate the
Excel right away.

Requirements:
- `ANTHROPIC_API_KEY` in your environment or in the project's `.env`
  (the same key the trading bot uses).
- The `anthropic` package: `pip install anthropic` (already in the
  dispatch `requirements.txt`).

Flags:
- `--file today.csv` — append to a different CSV.
- `--yes` / `-y` — skip the confirm prompt (good for automation).
- `--no-dispatch` — skip the "generate Excel?" question.

The model defaults to Claude Sonnet 4.6, which is plenty for this and
cheap. Override with `DISPATCH_MODEL=claude-opus-4-7` if you ever
need it.

### Option B: Interactive entry (easiest for typed orders)

Just type each stop as it comes in. The zone is detected from the
address's postal code as you go.

```bash
python add.py
```

You'll be prompted for each stop. Speed shortcut: type
`Customer @ Address` on the first prompt to fill both at once.

```
--- Stop #1 (blank or 'done' to finish) ---
Customer (or 'Customer @ Address' to speed-enter): Tojo's @ 1133 W Broadway Vancouver BC V6H 1G1
  -> Zone: Vancouver West (FSA V6H)
Boxes [1]: 3
Product (frozen/fresh/dry/mixed): fresh
Time window (e.g. AM only, after 2pm): AM only
Notes: Buzz at back door
```

Press Enter on a blank customer (or type `done`) to finish. You'll be
asked if you want to generate the Excel right away; say yes, give the
driver count, and you're done. Stops are saved to `orders.csv` as you
go, so you can quit and resume later (re-run `python add.py` and it
loads what's already there).

Useful flags:
- `--file today.csv` — keep separate lists per day instead of overwriting.
- `--no-dispatch` — skip the "generate Excel?" prompt.

### Option C: Prepare a CSV yourself

If you already have orders in a spreadsheet, save it as CSV with these
columns (see `sample_orders.csv`):

- **Required:** `customer`, `address`
- **Optional:** `postal_code`, `boxes`, `product_type`, `window`, `notes`

Then run:

```bash
python dispatch.py orders.csv --drivers 3 --out today.xlsx
```

- `--drivers` — how many drivers/trucks you're sending out today.
- `--out` — Excel file to write (default `routes.xlsx`).

### The output

The first tab of the Excel is a Summary (driver counts, zones,
boxes); each driver gets their own tab with stop number, address,
boxes, notes, and a clickable Google Maps link per stop.

## How routing works

Stops are bucketed into Vancouver-area zones using the FSA (first 3
characters) of the Canadian postal code, then visited in this order
(a sensible loop from Richmond):

> Richmond → Vancouver West → Downtown → East Van → North/West Van →
> Burnaby → New West → Coquitlam/PoMo → Maple Ridge → Langley →
> Surrey → White Rock → Delta

Within each zone, stops are sorted by postal code so neighbouring
streets stay together. Drivers are assigned contiguous zones so each
truck stays in one part of town instead of crossing the city twice.

Stops whose postal code we don't recognize land in "Out of Area" at
the end of the last driver's route, and the script prints a warning so
you can fix the address.

## Tweaking the zones

Open `zones.py` and edit the `ZONES` list. Each entry is
`(zone_name, drive_order, tuple_of_FSAs)`. Lower `drive_order` runs
earlier in the day.
