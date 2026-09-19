"""Final action legality and sanity layer.

Validates the Kaggle-shaped action emitted by ``actions.py`` against the
official contract and the current hand count. It sanitizes deterministically:

* a malformed or unknown unit action is downgraded to ``PASS``;
* the hands list is forced to exactly one action per hired hand, in order
  (extra entries dropped, missing entries filled with ``PASS``);
* malformed market orders are dropped and the list is trimmed to the official
  cap, keeping the earliest orders (list order is the explicit priority);
* anything not shaped like an action at all becomes an all-PASS turn.

It never chooses what Tilla should do.
"""

from __future__ import annotations

from kaggriculture_bot.constants import MAX_MARKET_ORDERS_PER_TURN
from kaggriculture_bot.models import (
    MARKET_OPS_WITHOUT_ITEM,
    UNIT_OPS_WITH_ITEM,
    MarketOp,
    UnitOp,
)

_PASS = [UnitOp.PASS.value]


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def sanitize_unit_action(raw) -> list:
    """Return a copy of a well-formed unit action, else ``["PASS"]``."""
    if not isinstance(raw, list) or not raw or not isinstance(raw[0], str):
        return list(_PASS)
    try:
        op = UnitOp(raw[0])
    except ValueError:
        return list(_PASS)
    if op in UNIT_OPS_WITH_ITEM:
        if len(raw) < 2 or not isinstance(raw[1], str):
            return list(_PASS)
        if len(raw) == 2:
            return [op.value, raw[1]]
        if len(raw) == 3 and _is_count(raw[2]):
            return [op.value, raw[1], raw[2]]
        return list(_PASS)
    if len(raw) != 1:
        return list(_PASS)
    return [op.value]


def sanitize_market_order(raw) -> list | None:
    """Return a copy of a well-formed market order, else ``None`` (dropped)."""
    if not isinstance(raw, list) or not raw or not isinstance(raw[0], str):
        return None
    try:
        op = MarketOp(raw[0])
    except ValueError:
        return None
    if op in MARKET_OPS_WITHOUT_ITEM:
        return [op.value] if len(raw) == 1 else None
    if len(raw) == 3 and isinstance(raw[1], str) and _is_count(raw[2]):
        return [op.value, raw[1], raw[2]]
    return None


def validate_or_fallback(action, hand_count: int) -> dict:
    """Return a fresh, structurally legal Kaggle action for ``hand_count`` hands."""
    if hand_count < 0:
        hand_count = 0
    if not isinstance(action, dict):
        action = {}

    farmer = sanitize_unit_action(action.get("farmer"))

    raw_hands = action.get("hands")
    if not isinstance(raw_hands, list):
        raw_hands = []
    hands = [sanitize_unit_action(h) for h in raw_hands[:hand_count]]
    hands.extend(list(_PASS) for _ in range(hand_count - len(hands)))

    raw_market = action.get("market")
    if not isinstance(raw_market, list):
        raw_market = []
    market = []
    for raw in raw_market:
        order = sanitize_market_order(raw)
        if order is not None:
            market.append(order)
        if len(market) == MAX_MARKET_ORDERS_PER_TURN:
            break

    return {"farmer": farmer, "hands": hands, "market": market}
