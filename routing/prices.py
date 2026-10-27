"""Charging prices: what each charger is likely to cost, and where that came from.

Three sources, in order:

1. The driver's own price for a network (routing.prices in config.local.yaml,
   or the phone's Route settings), matched against the charger's operator --
   their subscription or app rate is what they actually pay.
2. The price listed on Open Charge Map (UsageCost). Community-entered free
   text such as "£0.79/kWh; other tariffs available" or "DC: £0.79/kWh, AC:
   £0.49/kWh"; often right, sometimes out of date. Only pounds and pence are
   read -- another currency, or text this does not understand, is no price.
3. A default price for everything else.

A charger "listed as free" is not believed: rapid charging that is still free
is rare, and such entries are usually stale. It gets the default instead, and
says so.

The phone ports this file (route/Prices.kt); both are held to the
"usage_cost" cases in tests/data/route_vectors.json.
"""
from __future__ import annotations

import re

MIN_PRICE, MAX_PRICE = 0.05, 2.00        # £/kWh outside this is a misreading

_NUM = r"(\d+(?:\.\d+)?)"
# "£0.79/kwh", "£0.30p/kwh", "29p/kwh", "0.69 gbp/kwh" (after normalising).
_PER_KWH = re.compile(
    rf"(?:£\s*{_NUM}\s*p?|{_NUM}\s*p|{_NUM}\s*gbp)\s*/\s*kwh")
# "£1.00 fee", "50p fee", "£1.00 connection fee", "£1.00 + £0.30/kwh".
_FEE = re.compile(
    rf"(?:£\s*{_NUM}|{_NUM}\s*p)\s*(?:(?:connection|session|unlock)\s+)?fee"
    rf"|£\s*{_NUM}\s*\+")
_AC = re.compile(r"\bac\b")


def parse_usage_cost(text: str | None) -> tuple[float, float] | None:
    """(£ per kWh, £ fee per session) from Open Charge Map's UsageCost text,
    or None when it gives no usable per-kWh price in pounds."""
    if not text:
        return None
    s = " ".join(text.lower().split())
    s = s.replace("per kwh", "/kwh").replace("kw/h", "kwh")
    parts = [p for p in re.split(r"[;,]", s) if p.strip()]
    # DC (rapid) prices are the ones that matter; drop the AC part of a
    # mixed entry such as "Rapid £1.00 fee then 30p/kWh AC socket ...".
    dc = [p for p in parts if "dc" in p or "rapid" in p]
    chosen = dc or [p for p in parts if not _AC.search(p)] or parts
    for part in chosen:
        if dc:
            m = _AC.search(part)
            if m and m.start() > 0:
                part = part[:m.start()]
        price = _PER_KWH.search(part)
        if not price:
            continue
        pounds, pence, gbp = price.group(1), price.group(2), price.group(3)
        per_kwh = float(pounds) if pounds is not None else (
            float(pence) / 100.0 if pence is not None else float(gbp))
        if not MIN_PRICE <= per_kwh <= MAX_PRICE:
            return None
        fee = 0.0
        # The fee is usually beside the price; else in a part of its own
        # ("50p connection fee, 45p/kWh").
        f = _FEE.search(part) or next(
            (m for m in (_FEE.search(q) for q in chosen if not _PER_KWH.search(q)) if m),
            None)
        if f:
            a, b, c = f.group(1), f.group(2), f.group(3)
            fee = float(a) if a is not None else (
                float(b) / 100.0 if b is not None else float(c))
        return round(per_kwh, 4), round(fee, 2)
    return None


def own_prices(raw) -> list[tuple[str, float]]:
    """The driver's network prices as (lower-case name, £/kWh), from a mapping
    {"Ionity": 0.69} or lines "Ionity = 0.69"; unusable entries are dropped."""
    items = raw.items() if isinstance(raw, dict) else (
        line.split("=", 1) for line in str(raw or "").splitlines() if "=" in line)
    out = []
    for name, value in items:
        name = str(name).strip().lower()
        try:
            v = float(str(value).strip().lstrip("£"))
        except ValueError:
            continue
        if name and MIN_PRICE <= v <= MAX_PRICE:
            out.append((name, v))
    return out


def price_for(operator: str, usage_cost: str | None,
              own: list[tuple[str, float]], default: float) -> tuple[float, float, str]:
    """(£ per kWh, £ fee, where it came from) for one charger."""
    op = (operator or "").lower()
    for name, v in own:
        if name in op:
            return v, 0.0, f"your price for {name.title()}"
    parsed = parse_usage_cost(usage_cost)
    if parsed:
        return parsed[0], parsed[1], "Open Charge Map"
    if usage_cost and "free" in usage_cost.lower():
        return default, 0.0, "listed as free (often out of date); default used"
    return default, 0.0, "default price"
