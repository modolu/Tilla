"""Internal actions -> Kaggle action shape. No decision policy.

Only this module emits Kaggle action lists. The official contract is::

    {"farmer": [op, ...args], "hands": [[op, ...args], ...], "market": [[op, ...args], ...]}

with exactly one farmer action, one action per currently hired hand (in hand
order), and at most the configured number of market orders.
"""

from kaggriculture_bot.constants import PASS_OP


def build_pass_action(hand_count: int) -> dict:
    """Return a structurally valid action in which every unit passes.

    ``hand_count`` is the number of hands currently hired; each receives its own
    ``PASS`` entry so the hands list matches the observation.
    """
    if hand_count < 0:
        raise ValueError(f"hand_count must be non-negative, got {hand_count}")
    return {
        "farmer": [PASS_OP],
        "hands": [[PASS_OP] for _ in range(hand_count)],
        "market": [],
    }


def fallback_pass_action() -> dict:
    """Return the minimal PASS action used at the outer safety boundary.

    Built from literals only so it cannot fail; it assumes no hired hands, which
    the environment tolerates (missing hand actions are treated as no-ops).
    """
    return {"farmer": [PASS_OP], "hands": [], "market": []}
