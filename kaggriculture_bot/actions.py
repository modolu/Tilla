"""Internal typed actions -> Kaggle action shape. No decision policy.

Only this module emits Kaggle action lists. The official contract is::

    {"farmer": [op, ...args], "hands": [[op, ...args], ...], "market": [[op, ...args], ...]}

with exactly one farmer action, one action per currently hired hand (in hand
order), and at most the configured number of market orders.
"""

from __future__ import annotations

from kaggriculture_bot.models import (
    MarketOrder,
    TurnAction,
    UnitAction,
    UnitOp,
    pass_turn_action,
)


def format_unit_action(action: UnitAction) -> list:
    """``[op]``, ``[op, item]`` or ``[op, item, quantity]`` as the environment expects."""
    parts: list = [action.op.value]
    if action.item is not None:
        parts.append(action.item)
        if action.quantity is not None:
            parts.append(action.quantity)
    return parts


def format_market_order(order: MarketOrder) -> list:
    """``[op]`` for HIRE/BUY_LAND, otherwise ``[op, item, quantity]``."""
    parts: list = [order.op.value]
    if order.item is not None:
        parts.append(order.item)
        if order.quantity is not None:
            parts.append(order.quantity)
    return parts


def build_action(turn: TurnAction) -> dict:
    """Format a typed turn into a fresh Kaggle action dict."""
    return {
        "farmer": format_unit_action(turn.farmer),
        "hands": [format_unit_action(a) for a in turn.hands],
        "market": [format_market_order(o) for o in turn.market],
    }


def build_pass_action(hand_count: int) -> dict:
    """Structurally valid action in which every unit passes (typed path)."""
    return build_action(pass_turn_action(hand_count))


def fallback_pass_action(hand_count: int = 0) -> dict:
    """PASS action used at the outer safety boundary.

    Built from literals only so it cannot fail. ``hand_count`` is the number of
    hands known to be hired when the failure occurred; anything that is not a
    non-negative ``int`` is treated as zero so the fallback never raises.
    """
    if not isinstance(hand_count, int) or isinstance(hand_count, bool) or hand_count < 0:
        hand_count = 0
    op = UnitOp.PASS.value
    return {"farmer": [op], "hands": [[op] for _ in range(hand_count)], "market": []}
