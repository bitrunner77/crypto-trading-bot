# Truer Foods Dispatch

Daily route builder for Truer Foods (Richmond, BC). Takes a CSV of
stops, sorts them into a sensible Vancouver-area drive order, splits
them across however many drivers you have today, and produces an Excel
workbook with one route sheet per driver plus a summary tab.

## One-time setup

```bash
pip install -r requirements.txt
```

## Daily use

1. Update `orders.csv` (or save your day's order list as a CSV anywhere).

   **Required columns:** `customer`, `address`
   **Optional columns:** `postal_code`, `boxes`, `product_type`, `window`, `notes`

   See `sample_orders.csv` for the format.

2. Run:

   ```bash
   python dispatch.py orders.csv --drivers 3 --out today.xlsx
   ```

   - `--drivers` — how many drivers/trucks you're sending out today.
   - `--out` — Excel file to write (default `routes.xlsx`).

3. Open `today.xlsx`. The first tab is a Summary; each driver gets
   their own tab with stop number, address, boxes, notes, and a
   clickable Google Maps link per stop.

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
