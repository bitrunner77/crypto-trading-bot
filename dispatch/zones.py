"""Vancouver Lower Mainland zone mapping for warehouse dispatch.

Maps Canadian postal-code FSAs (first 3 characters) to delivery zones and
assigns a drive order that forms a sensible loop starting and ending at
Truer Foods in Richmond, BC.
"""
from __future__ import annotations

import re

HOME_BASE = "Truer Foods, Richmond, BC"

# (zone_name, drive_order, fsas) — drive_order is the position in the
# Richmond-anchored loop; lower numbers run earlier in the day.
ZONES: list[tuple[str, int, tuple[str, ...]]] = [
    ("Richmond",           1,  ("V6V", "V6W", "V6X", "V6Y", "V7A", "V7B", "V7C", "V7E")),
    ("Vancouver West",     2,  ("V6H", "V6J", "V6K", "V6L", "V6M", "V6N", "V6P", "V6R", "V6S", "V6T", "V6Z")),
    ("Vancouver Downtown", 3,  ("V6A", "V6B", "V6C", "V6E", "V6G", "V5Y", "V5Z")),
    ("Vancouver East",     4,  ("V5K", "V5L", "V5M", "V5N", "V5P", "V5R", "V5S", "V5T", "V5V", "V5W", "V5X")),
    ("North/West Van",     5,  ("V7G", "V7H", "V7J", "V7K", "V7L", "V7M", "V7N", "V7P", "V7R", "V7S", "V7T", "V7V", "V7W")),
    ("Burnaby",            6,  ("V5A", "V5B", "V5C", "V5E", "V5G", "V5H", "V5J")),
    ("New Westminster",    7,  ("V3L", "V3M")),
    ("Coquitlam/PoMo",     8,  ("V3B", "V3C", "V3E", "V3H", "V3J", "V3K")),
    ("Maple Ridge",        9,  ("V2W", "V2X", "V3Y", "V4R")),
    ("Langley",            10, ("V1M", "V2Y", "V2Z", "V3A")),
    ("Surrey",             11, ("V3R", "V3S", "V3T", "V3V", "V3W", "V3X", "V4N", "V4P")),
    ("White Rock",         12, ("V4A", "V4B")),
    ("Delta/Tsawwassen",   13, ("V4C", "V4E", "V4G", "V4K", "V4L", "V4M")),
]

_FSA_TO_ZONE: dict[str, tuple[str, int]] = {}
for _name, _order, _fsas in ZONES:
    for _fsa in _fsas:
        _FSA_TO_ZONE[_fsa] = (_name, _order)

_POSTAL_RE = re.compile(r"\b([A-Z]\d[A-Z])\s?\d[A-Z]\d\b", re.IGNORECASE)
_FSA_ONLY_RE = re.compile(r"^[A-Z]\d[A-Z]$", re.IGNORECASE)


def extract_fsa(address_or_postal: str) -> str | None:
    """Return the FSA (e.g. 'V6X') from a full postal code, a bare FSA, or a free-form address."""
    if not address_or_postal:
        return None
    s = address_or_postal.strip().upper()
    if _FSA_ONLY_RE.match(s):
        return s
    m = _POSTAL_RE.search(s)
    return m.group(1).upper() if m else None


def zone_for(address_or_postal: str) -> tuple[str, int]:
    """Map an address or postal code to (zone_name, drive_order).

    Unknown postal codes fall into 'Out of Area' (drive_order 99) so they
    surface at the end of the route for the dispatcher to fix.
    """
    fsa = extract_fsa(address_or_postal)
    if fsa and fsa in _FSA_TO_ZONE:
        return _FSA_TO_ZONE[fsa]
    return ("Out of Area", 99)
