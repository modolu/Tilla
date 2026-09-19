"""Strategic objectives -> concrete unit jobs. May use pathing; no market economics.

Milestone 2: single-unit execution. The farmer works the plan's objective;
hired hands (not planned until Milestone 4) legally PASS.
"""

from __future__ import annotations

from kaggriculture_bot.models import (
    PASS_UNIT_ACTION,
    GameState,
    Objective,
    ObjectiveKind,
    StrategicPlan,
    TurnAction,
    UnitAction,
    UnitOp,
)
from kaggriculture_bot.pathing import nearest, next_step_toward

_ON_TILE_OPS = {
    ObjectiveKind.WATER_CROP: UnitOp.WATER,
    ObjectiveKind.FEED_ANIMAL: UnitOp.FEED,
    ObjectiveKind.HARVEST: UnitOp.HARVEST,
    ObjectiveKind.DELIVER: UnitOp.DROP,
}


def _on_tile_action(objective: Objective, hour: int) -> UnitAction:
    kind = objective.kind
    if kind is ObjectiveKind.PLANT:
        if objective.item is None:
            return PASS_UNIT_ACTION
        if objective.deadline_hour is not None and hour > objective.deadline_hour:
            return PASS_UNIT_ACTION  # too late to water it today; do not plant
        return UnitAction(UnitOp.PLANT, objective.item)
    if kind is ObjectiveKind.FETCH_FEED:
        if objective.item is None:
            return PASS_UNIT_ACTION
        return UnitAction(UnitOp.PICKUP, objective.item, objective.quantity)
    op = _ON_TILE_OPS.get(kind)
    return UnitAction(op) if op is not None else PASS_UNIT_ACTION


def farmer_job(state: GameState, objective: Objective) -> UnitAction:
    """The farmer's concrete action for ``objective``: act if standing on the
    nearest reachable target, otherwise take one shortest-path step toward it."""
    if objective.kind is ObjectiveKind.PASS or not objective.targets:
        return PASS_UNIT_ACTION
    tiles = state.me.tiles
    start = state.me.farmer.position
    found = nearest(tiles, start, objective.targets)
    if found is None:
        return PASS_UNIT_ACTION  # no target reachable: degrade safely
    target, _ = found
    if target == start:
        return _on_tile_action(objective, state.hour)
    step = next_step_toward(tiles, start, target)
    return UnitAction(step) if step is not None else PASS_UNIT_ACTION


def assign_jobs(state: GameState, plan: StrategicPlan) -> TurnAction:
    return TurnAction(
        farmer=farmer_job(state, plan.objective),
        hands=tuple(PASS_UNIT_ACTION for _ in state.me.hands),
        market=plan.market,
    )
