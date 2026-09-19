"""Strategic objectives -> concrete unit jobs. May use pathing; no market economics.

Single-unit execution: the farmer works the plan's objective; hired hands
(not planned until Milestone 4) legally PASS.
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
    ObjectiveKind.COLLECT_FERTILIZER: UnitOp.COLLECT_FERTILIZER,
    ObjectiveKind.FERTILIZE_CROP: UnitOp.FERTILIZE,
    ObjectiveKind.DELIVER: UnitOp.DROP,
}

_BUILD_OPS = {"COOP": UnitOp.BUILD_COOP, "PASTURE": UnitOp.BUILD_PASTURE}


def _on_tile_action(objective: Objective, hour: int) -> UnitAction:
    kind = objective.kind
    if kind is ObjectiveKind.PLANT:
        if objective.item is None:
            return PASS_UNIT_ACTION
        if objective.deadline_hour is not None and hour > objective.deadline_hour:
            return PASS_UNIT_ACTION  # too late to water it today; do not plant
        return UnitAction(UnitOp.PLANT, objective.item)
    if kind in (ObjectiveKind.FETCH_FEED, ObjectiveKind.FETCH_ITEM):
        if objective.item is None:
            return PASS_UNIT_ACTION
        return UnitAction(UnitOp.PICKUP, objective.item, objective.quantity)
    if kind is ObjectiveKind.BUILD_STRUCTURE:
        op = _BUILD_OPS.get(objective.item or "")
        return UnitAction(op) if op is not None else PASS_UNIT_ACTION
    if kind is ObjectiveKind.PLACE_ANIMAL:
        if objective.item is None:
            return PASS_UNIT_ACTION
        return UnitAction(UnitOp.PLACE, objective.item)
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


def choose_nearest_objective(state: GameState, plan: StrategicPlan) -> Objective:
    """Among the plan's equal-priority objectives, the one whose nearest
    reachable target is closest (ties keep plan order)."""
    candidates = (plan.objective, *plan.equal_priority)
    tiles = state.me.tiles
    start = state.me.farmer.position
    best, best_distance = plan.objective, None
    for objective in candidates:
        if not objective.targets:
            continue
        found = nearest(tiles, start, objective.targets)
        if found is None:
            continue
        if best_distance is None or found[1] < best_distance:
            best, best_distance = objective, found[1]
    return best


def assign_jobs(state: GameState, plan: StrategicPlan) -> TurnAction:
    return TurnAction(
        farmer=farmer_job(state, choose_nearest_objective(state, plan)),
        hands=tuple(PASS_UNIT_ACTION for _ in state.me.hands),
        market=plan.market,
    )
