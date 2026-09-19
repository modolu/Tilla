"""Strategic objectives -> concrete unit jobs. May use pathing; no market economics.

Milestone 4 multi-unit planner:

* ``generate_jobs`` splits every objective target into one ``Job`` with a
  stable key (kind, target, item, unit), keeping the plan's priority order and
  dropping duplicates (the higher-priority copy wins).
* ``assign_jobs`` gives each of our units at most one job, deterministically:
  jobs in priority order, persisted jobs of the same priority first
  (movement-to-task persistence), the nearest capable unit by BFS distance
  including any shed detour to fetch a required item, ties by unit index.
  Shared consumables (shed wheat/fertilizer/animals, seeds) are reserved as
  they are assigned so two units never count the same unit of stock.
  Higher-priority jobs are assigned before lower ones, so a unit walking to
  economic work is preempted when it is the nearest unit to urgent work.
* ``unit_job`` executes one job for one unit: act on the tile, fetch a
  required item at the shed first, or take one shortest-path step.

Assignments are stored in ``EpisodeMemory.unit_assignments`` for the current
day only and revalidated every turn against the freshly generated jobs.
"""

from __future__ import annotations

from kaggriculture_bot.constants import HAND_ACTIONS_PER_JOB, PROMOTION_SLACK_TURNS, TURNS_PER_DAY
from kaggriculture_bot.features import carried, carried_total, usable_shed_access
from kaggriculture_bot.models import (
    PASS_UNIT_ACTION,
    PRIORITY_IDLE,
    PRIORITY_SURVIVAL,
    EpisodeMemory,
    GameState,
    Job,
    JobKind,
    Objective,
    ObjectiveKind,
    Position,
    StrategicPlan,
    TurnAction,
    UnitAction,
    UnitOp,
    UnitState,
)
from kaggriculture_bot.pathing import nearest, next_step_toward, shortest_paths

_OBJECTIVE_JOBS: dict[ObjectiveKind, JobKind] = {
    ObjectiveKind.WATER_CROP: JobKind.WATER,
    ObjectiveKind.FEED_ANIMAL: JobKind.FEED,
    ObjectiveKind.HARVEST: JobKind.HARVEST,
    ObjectiveKind.COLLECT_FERTILIZER: JobKind.COLLECT,
    ObjectiveKind.PLANT: JobKind.PLANT,
    ObjectiveKind.BUILD_STRUCTURE: JobKind.BUILD,
    ObjectiveKind.PLACE_ANIMAL: JobKind.PLACE,
    ObjectiveKind.FERTILIZE_CROP: JobKind.FERTILIZE,
    ObjectiveKind.REPOSITION: JobKind.MOVE,
}

# Carried item a job needs (fetched from the shed when the unit lacks it).
_REQUIRES: dict[JobKind, str | None] = {JobKind.FEED: "WHEAT", JobKind.FERTILIZE: "FERTILIZER"}

_ON_TILE_OPS = {
    JobKind.WATER: UnitOp.WATER,
    JobKind.FEED: UnitOp.FEED,
    JobKind.HARVEST: UnitOp.HARVEST,
    JobKind.COLLECT: UnitOp.COLLECT_FERTILIZER,
    JobKind.FERTILIZE: UnitOp.FERTILIZE,
    JobKind.DELIVER: UnitOp.DROP,
}
_BUILD_OPS = {"COOP": UnitOp.BUILD_COOP, "PASTURE": UnitOp.BUILD_PASTURE}
# Single-use tile work: at most one job per tile per turn whatever the item.
_ONE_PER_TILE = {JobKind.PLACE, JobKind.BUILD, JobKind.PLANT}


# --- Job generation ------------------------------------------------------------------------------


def generate_jobs(state: GameState, plan: StrategicPlan) -> list[Job]:
    """One job per objective target in plan order; duplicates keep the first."""
    jobs: list[Job] = []
    seen: set[tuple] = set()
    tiles = state.me.tiles
    for objective in plan.all_objectives():
        if objective.kind is ObjectiveKind.DELIVER:
            for unit in state.me.units:
                if carried_total(unit) <= 0:
                    continue
                found = nearest(tiles, unit.position, objective.targets)
                if found is None:
                    continue
                job = Job(JobKind.DELIVER, found[0], objective.priority, unit=unit.index)
                if job.key not in seen:
                    seen.add(job.key)
                    jobs.append(job)
            continue
        kind = _OBJECTIVE_JOBS.get(objective.kind)
        if kind is None:
            continue
        requires = objective.item if kind is JobKind.PLACE else _REQUIRES.get(kind)
        for target in objective.targets:
            job = Job(
                kind,
                target,
                objective.priority,
                item=objective.item,
                quantity=objective.quantity,
                deadline_hour=objective.deadline_hour,
                requires=requires,
            )
            # One structure/planting per tile: a second animal or crop wanting
            # the same tile waits its turn (the strategy allocates tiles
            # exclusively; this guards the invariant at the job level).
            tile_key = (kind.value, target.x, target.y) if kind in _ONE_PER_TILE else None
            if job.key in seen or tile_key in seen:
                continue
            seen.add(job.key)
            if tile_key is not None:
                seen.add(tile_key)
            jobs.append(job)
    return jobs


# --- Assignment ----------------------------------------------------------------------------------


class _Pools:
    """Shared consumables reserved during one assignment pass."""

    def __init__(self, state: GameState):
        self.shed = dict(state.private.shed)
        self.seeds = dict(state.private.seeds)

    def take(self, store: dict[str, int], item: str) -> bool:
        if store.get(item, 0) <= 0:
            return False
        store[item] -= 1
        return True


class _Routes:
    """One BFS per unit and per usable shed access tile, shared by the pass."""

    def __init__(self, state: GameState):
        self.tiles = state.me.tiles
        self.access = usable_shed_access(state.me)
        self.from_unit = {u.index: shortest_paths(self.tiles, u.position) for u in state.me.units}
        self.from_access = {pos: shortest_paths(self.tiles, pos) for pos in self.access}

    def nearest_access(self, unit: UnitState) -> tuple[Position, int] | None:
        paths = self.from_unit[unit.index]
        best = None
        for pos in self.access:
            entry = paths.get(pos)
            if entry is None:
                continue
            key = (entry[0], pos.y, pos.x)
            if best is None or key < best[0]:
                best = (key, pos)
        return None if best is None else (best[1], best[0][0])


def _route_cost(
    state: GameState, unit: UnitState, job: Job, pools: _Pools, routes: _Routes
) -> tuple[int, bool] | None:
    """Turns for ``unit`` to reach the job (with a shed detour when it must
    fetch ``job.requires``), or None when the job is infeasible for it."""
    if job.unit is not None and job.unit != unit.index:
        return None
    if job.kind is JobKind.PLANT and job.item is not None and pools.seeds.get(job.item, 0) <= 0:
        return None
    direct = routes.from_unit[unit.index].get(job.target)
    if direct is None:
        return None
    needs_fetch = bool(job.requires) and carried(unit, job.requires) < 1
    if not needs_fetch:
        cost, fetch = direct[0], False
    else:
        if pools.shed.get(job.requires, 0) <= 0:
            return None
        found = routes.nearest_access(unit)
        if found is None:
            return None
        onward = routes.from_access[found[0]].get(job.target)
        if onward is None:
            return None
        cost, fetch = found[1] + 1 + onward[0], True
    # The action itself must happen today: hands vanish and carried items drop
    # into the shed at the day refresh, so a route that cannot arrive by the
    # last hour is worthless (TILLA_RULES.md §4, §5).
    deadline = TURNS_PER_DAY - 1 if job.deadline_hour is None else job.deadline_hour
    if state.hour + cost > deadline:
        return None
    return cost, fetch


def _reserve(unit: UnitState, job: Job, pools: _Pools) -> None:
    if job.kind is JobKind.PLANT and job.item is not None:
        pools.take(pools.seeds, job.item)
    if job.requires and carried(unit, job.requires) < 1:
        pools.take(pools.shed, job.requires)


def _effective_priority(
    state: GameState, job: Job, jobs: list[Job], pools: _Pools, routes: _Routes
) -> int:
    """Deadline promotion: a job that must start now to finish today is
    scheduled with the survival tier, provided the other units can still
    cover every higher-priority job today. Otherwise higher tiers would
    absorb every unit until the job is unreachable, leaving the workforce
    idle at the end of the day. Idle work is never promoted."""
    if job.priority <= PRIORITY_SURVIVAL or job.priority >= PRIORITY_IDLE:
        return job.priority
    costs = [
        route[0]
        for unit in state.me.units
        if (route := _route_cost(state, unit, job, pools, routes)) is not None
    ]
    if not costs:
        return job.priority
    deadline = TURNS_PER_DAY - 1 if job.deadline_hour is None else job.deadline_hour
    if deadline - state.hour - min(costs) > PROMOTION_SLACK_TURNS:
        return job.priority
    higher = sum(1 for other in jobs if other.priority < job.priority)
    other_capacity = (len(state.me.units) - 1) * (TURNS_PER_DAY - state.hour)
    if other_capacity < higher * HAND_ACTIONS_PER_JOB:
        return job.priority
    return PRIORITY_SURVIVAL


def assign_units(state: GameState, jobs: list[Job], memory: EpisodeMemory) -> dict[int, Job]:
    """Deterministic greedy assignment; returns {unit index: job}.

    Tier by tier (priority ascending), repeatedly take the cheapest feasible
    (job, unit) pair: a unit persisted on that job first, then the shortest
    route (including a shed detour for a required item), then job order, then
    unit index. Consumables are reserved as pairs are fixed. Because higher
    tiers are settled first, urgent work preempts a unit's lower-tier job.
    """
    units = state.me.units
    if memory.assignment_day != state.day:
        memory.unit_assignments = {}
    valid = {job.key: job for job in jobs}
    persisted = {
        idx: valid[job.key]
        for idx, job in memory.unit_assignments.items()
        if idx < len(units) and job.key in valid
    }
    pools = _Pools(state)
    routes = _Routes(state)
    assigned: dict[int, Job] = {}
    free = [u.index for u in units]
    tiers: dict[int, list[tuple[int, Job]]] = {}
    for order, job in enumerate(jobs):
        tiers.setdefault(_effective_priority(state, job, jobs, pools, routes), []).append(
            (order, job)
        )
    for priority in sorted(tiers):
        pending = list(tiers[priority])
        while pending and free:
            best = None
            for order, job in pending:
                for idx in free:
                    route = _route_cost(state, units[idx], job, pools, routes)
                    if route is None:
                        continue
                    sticky = 0 if persisted.get(idx) is job else 1
                    key = (sticky, route[0], order, idx)
                    if best is None or key < best[0]:
                        best = (key, job, idx)
            if best is None:
                break  # nothing in this tier is feasible for the remaining units
            _, job, idx = best
            _reserve(units[idx], job, pools)
            assigned[idx] = job
            free.remove(idx)
            pending = [(o, j) for o, j in pending if j.key != job.key]
    memory.unit_assignments = dict(assigned)
    memory.assignment_day = state.day
    return assigned


# --- Execution ---------------------------------------------------------------------


def _on_tile_action(job: Job, hour: int) -> UnitAction:
    kind = job.kind
    if kind is JobKind.PLANT:
        if job.item is None or (job.deadline_hour is not None and hour > job.deadline_hour):
            return PASS_UNIT_ACTION
        return UnitAction(UnitOp.PLANT, job.item)
    if kind is JobKind.BUILD:
        op = _BUILD_OPS.get(job.item or "")
        return UnitAction(op) if op is not None else PASS_UNIT_ACTION
    if kind is JobKind.PLACE:
        return UnitAction(UnitOp.PLACE, job.item) if job.item else PASS_UNIT_ACTION
    if kind is JobKind.MOVE:
        return PASS_UNIT_ACTION
    op = _ON_TILE_OPS.get(kind)
    return UnitAction(op) if op is not None else PASS_UNIT_ACTION


def _step(state: GameState, start: Position, target: Position) -> UnitAction:
    step = next_step_toward(state.me.tiles, start, target)
    return UnitAction(step) if step is not None else PASS_UNIT_ACTION


def unit_job(state: GameState, unit: UnitState, job: Job) -> UnitAction:
    """Concrete action for one unit: fetch a required item, act on the tile, or move."""
    if job.requires and carried(unit, job.requires) < 1:
        access = usable_shed_access(state.me)
        found = nearest(state.me.tiles, unit.position, access)
        if found is None:
            return PASS_UNIT_ACTION
        if found[0] == unit.position:
            return UnitAction(UnitOp.PICKUP, job.requires, job.quantity or 1)
        return _step(state, unit.position, found[0])
    if unit.position == job.target:
        return _on_tile_action(job, state.hour)
    return _step(state, unit.position, job.target)


def assign_jobs(state: GameState, plan: StrategicPlan, memory: EpisodeMemory) -> TurnAction:
    jobs = generate_jobs(state, plan)
    assigned = assign_units(state, jobs, memory)
    units = state.me.units
    actions = [
        unit_job(state, unit, assigned[unit.index]) if unit.index in assigned else PASS_UNIT_ACTION
        for unit in units
    ]
    return TurnAction(farmer=actions[0], hands=tuple(actions[1:]), market=plan.market)


def farmer_job(state: GameState, objective: Objective) -> UnitAction:
    """Single-unit execution of one objective (kept for diagnostics/tests)."""
    plan = StrategicPlan(objectives=(objective,))
    jobs = generate_jobs(state, plan)
    farmer = state.me.farmer
    memory = EpisodeMemory(player_id=state.player_id)
    assigned = assign_units(state, jobs, memory)
    return unit_job(state, farmer, assigned[0]) if 0 in assigned else PASS_UNIT_ACTION
