"""Extract stops from a screenshot or pasted text using Claude.

Hands a screenshot, photo, or pasted text block to Claude and asks for
a strict JSON list of customers + addresses, then appends them to
orders.csv. Useful when an order list comes in as a phone-camera shot,
a forwarded email, a screenshot of a Google Sheet, or just text on
the clipboard.

Usage:
    python extract.py --image screenshot.png
    python extract.py --text orders.txt
    python extract.py --paste              # read from stdin
    python extract.py --image scr.png --file today.csv --yes --no-dispatch
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import dispatch as dispatch_mod
from add import _ask, _read_existing, _write_rows
from zones import extract_fsa, zone_for

DEFAULT_MODEL = os.environ.get("DISPATCH_MODEL", "claude-sonnet-4-6")

SYSTEM_PROMPT = """You are an order-extraction assistant for a Vancouver
food warehouse (Truer Foods). The user gives you a screenshot, photo, or
pasted text containing a list of delivery stops — restaurants, grocery
stores, or sushi shops in the Lower Mainland of BC.

Extract each stop and return ONLY a JSON object of the form:

{"stops": [
  {"customer": "Tojo's Restaurant",
   "address": "1133 W Broadway, Vancouver, BC V6H 1G1",
   "postal_code": "V6H 1G1",
   "boxes": 3,
   "product_type": "fresh",
   "window": "AM only",
   "notes": "Buzz at back door"}
]}

Rules:
- Output JSON only — no commentary, no markdown fences, no preamble.
- "customer" and "address" are required. Other fields are optional;
  omit them entirely if not visible in the input (don't invent values).
- Always include the BC postal code in "address" if you can see it.
- "boxes" must be an integer when present.
- "product_type" should be one of frozen, fresh, dry, mixed when
  mentioned; otherwise omit.
- If a row is illegible or has no usable address, skip it.
- Preserve apostrophes and special characters in customer names.
"""

USER_INSTRUCTION = "Extract the delivery stops as JSON."


def _client():
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise SystemExit(
            "The 'anthropic' package is required for extraction.\n"
            "Install it with: pip install anthropic"
        ) from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Add it to .env or export it:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-..."
        )
    return Anthropic()


def _image_block(image_path: Path) -> dict:
    suffix = image_path.suffix.lower().lstrip(".")
    media = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
             "gif": "gif", "webp": "webp"}.get(suffix)
    if not media:
        raise SystemExit(f"Unsupported image type: {image_path.suffix}. "
                         "Use PNG, JPG, GIF, or WEBP.")
    data = base64.b64encode(image_path.read_bytes()).decode()
    return {"type": "image",
            "source": {"type": "base64",
                       "media_type": f"image/{media}", "data": data}}


def _message_content(image_path: Path | None, text: str | None) -> list[dict]:
    if image_path is not None:
        return [_image_block(image_path), {"type": "text", "text": USER_INSTRUCTION}]
    return [{"type": "text", "text": f"{USER_INSTRUCTION}\n\n{text}"}]


def call_claude(image_path: Path | None, text: str | None, model: str = DEFAULT_MODEL) -> str:
    client = _client()
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _message_content(image_path, text)}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def parse_response(raw: str) -> list[dict]:
    """Parse Claude's JSON output into a list of stop dicts."""
    cleaned = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Claude returned non-JSON output:\n{raw[:500]}") from e
    raw_stops = data.get("stops") if isinstance(data, dict) else data
    if not isinstance(raw_stops, list):
        raise SystemExit(f"Expected a list of stops; got {type(raw_stops).__name__}.")

    result: list[dict] = []
    for s in raw_stops:
        if not isinstance(s, dict):
            continue
        customer = str(s.get("customer", "")).strip()
        address = str(s.get("address", "")).strip()
        if not customer or not address:
            continue
        postal = str(s.get("postal_code", "") or "").strip().upper()
        fsa = extract_fsa(postal) or extract_fsa(address) or ""
        boxes_raw = s.get("boxes", "")
        if isinstance(boxes_raw, (int, float)):
            boxes = str(int(boxes_raw))
        else:
            boxes = str(boxes_raw).strip() or "1"
        result.append({
            "customer": customer,
            "address": address,
            "postal_code": fsa,
            "boxes": boxes,
            "product_type": str(s.get("product_type", "") or "").strip(),
            "window": str(s.get("window", "") or "").strip(),
            "notes": str(s.get("notes", "") or "").strip(),
        })
    return result


def preview(stops: list[dict]) -> None:
    print(f"\nExtracted {len(stops)} stop(s):")
    for i, s in enumerate(stops, start=1):
        zone, _ = zone_for(s["postal_code"] or s["address"])
        warn = "   <-- OUT OF AREA" if zone == "Out of Area" else ""
        extras = f"  Boxes: {s['boxes']}"
        if s["product_type"]:
            extras += f"  Product: {s['product_type']}"
        if s["window"]:
            extras += f"  Window: {s['window']}"
        print(f"\n  {i}. {s['customer']}")
        print(f"     {s['address']}")
        print(f"     Zone: {zone}{extras}{warn}")
        if s["notes"]:
            print(f"     Notes: {s['notes']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract stops from an image or text via Claude")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", type=Path, help="Path to a screenshot or photo (PNG/JPG/WEBP/GIF)")
    src.add_argument("--text", type=Path, help="Path to a text file with the order list")
    src.add_argument("--paste", action="store_true", help="Read order text from stdin")
    p.add_argument("--file", type=Path, default=Path("orders.csv"),
                   help="CSV to append to (default orders.csv)")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip the confirm-before-appending prompt")
    p.add_argument("--no-dispatch", action="store_true",
                   help="Don't ask to generate routes after appending")
    args = p.parse_args(argv)

    if args.image:
        if not args.image.exists():
            raise SystemExit(f"Image not found: {args.image}")
        print(f"Sending {args.image} to Claude...")
        raw = call_claude(args.image, None)
    elif args.text:
        if not args.text.exists():
            raise SystemExit(f"Text file not found: {args.text}")
        print(f"Sending {args.text} to Claude...")
        raw = call_claude(None, args.text.read_text())
    else:
        print("Paste your order list, then press Ctrl+D when done:")
        text = sys.stdin.read()
        if not text.strip():
            raise SystemExit("No text received.")
        print("Sending pasted text to Claude...")
        raw = call_claude(None, text)

    stops = parse_response(raw)
    if not stops:
        raise SystemExit("Claude didn't return any usable stops.")
    preview(stops)

    if not args.yes:
        answer = _ask(f"\nAppend these {len(stops)} stop(s) to {args.file}? (y/N)", "n")
        if not answer.lower().startswith("y"):
            print("Aborted. Nothing written.")
            return 1

    rows = _read_existing(args.file)
    rows.extend(stops)
    _write_rows(args.file, rows)
    print(f"Wrote {len(stops)} new stop(s) to {args.file} ({len(rows)} total).")

    if args.no_dispatch:
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
