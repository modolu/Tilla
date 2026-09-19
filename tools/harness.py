"""Run local Kaggriculture matches with instrumentation (offline only).

Provides:

* ``run_game``: one official 720-turn episode between two agents with
  per-agent instrumentation for the *observed* agent: timing, validator
  fallbacks, malformed outputs, parse failures, hires, hand-action
  utilization, care losses (crops lost to missed watering, animals escaped,
  fresh plantings that failed same-day watering), decay losses, and
  same-turn conflicts on single-use work (duplicate planner assignments,
  duplicate emitted actions, mixed-op no-ops, redundant care actions).
* ``hiring_disabled``: an ablation wrapper that runs the same agent but
  removes only ``HIRE`` market orders (Milestone 4 control).
* ``paired``: seat-swapped paired games over a seed range.

    python -m tools.harness --candidate main --opponent baseline --seeds 4000 4039
    python -m tools.harness --candidate main --opponent control --seeds 4000 4039

Never imported by the submitted runtime.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable

from kaggriculture_bot.parser import parse_observation
from kaggriculture_bot.runtime import _MEMORIES
from kaggriculture_bot.validator import validate_or_fallback

PASS_ACTION = {"farmer": ["PASS"], "hands": [], "market": []}
MOVE_OPS = {"NORTH", "SOUTH", "EAST", "WEST"}
LOGISTIC_OPS = {"PICKUP", "DROP"}
# Unit ops that consume a single-use resource on their tile: a second one on
# the same tile in the same turn can only be an environment no-op.
SINGLE_USE_OPS = {
    "WATER",
    "HARVEST",
    "FEED",
    "COLLECT_FERTILIZER",
    "FERTILIZE",
    "BUILD_COOP",
    "BUILD_PASTURE",
    "PLACE",
    "PLANT",
}
# Job kinds that are single-use on their target tile (mirrors SINGLE_USE_OPS).
SINGLE_USE_JOBS = {"WATER", "HARVEST", "FEED", "COLLECT", "FERTILIZE", "BUILD", "PLACE", "PLANT"}
# Ops that turn an empty tile into something: two different ones on one tile
# in one turn means the later is a no-op.
TILE_CLAIM_OPS = {"PLANT", "BUILD_COOP", "BUILD_PASTURE"}
# Care ops that are only meaningful once per tile per day.
ONCE_PER_DAY_OPS = {"WATER", "FEED", "COLLECT_FERTILIZER"}
CARE_OPS = {"WATER", "FEED"}
HARVEST_OPS = {"HARVEST", "COLLECT_FERTILIZER"}
PRODUCTION_OPS = {"PLANT", "BUILD_COOP", "BUILD_PASTURE", "PLACE", "FERTILIZE"}


def hiring_disabled(agent: Callable) -> Callable:
    """Same agent, but every HIRE market order is removed. Nothing else changes."""

    def control(obs):
        action = agent(obs)
        if isinstance(action, dict) and isinstance(action.get("market"), list):
            action = dict(action)
            action["market"] = [
                order
                for order in action["market"]
                if not (isinstance(order, list) and order and order[0] == "HIRE")
            ]
        return action

    control.__name__ = f"{getattr(agent, '__name__', 'agent')}_no_hire"
    return control


def _tile_is(tile, kind):
    return isinstance(tile, dict) and tile.get("kind") == kind


def _care_losses(steps, seat: int) -> dict[str, int]:
    """Count avoidable losses on ``seat``'s farm across recorded steps."""
    losses = Counter()
    for a, b in zip(steps, steps[1:], strict=False):
        oa, ob = a[0]["observation"], b[0]["observation"]
        ta, tb = oa["farms"][seat]["tiles"], ob["farms"][seat]["tiles"]
        day_changed = ob["day"] != oa["day"]
        for y in range(len(ta)):
            for x in range(len(ta[y])):
                before, after = ta[y][x], tb[y][x]
                if _tile_is(before, "PLANT") and _tile_is(after, "WEED"):
                    if day_changed and not before["watered_today"]:
                        losses["crops_lost_unwatered"] += 1
                        if before["planted_day"] == oa["day"]:
                            losses["fresh_plantings_unwatered"] += 1
                    elif before["yield_units"] <= 1 and not day_changed:
                        losses["crops_lost_decay"] += 1
                if isinstance(before, dict) and "animal" in before:
                    if isinstance(after, dict) and "animal" not in after and day_changed:
                        losses["animals_escaped"] += 1
    return dict(losses)


def _duplicate_checks(obs, units, acts, same_day_repeats: Counter) -> list[dict]:
    """Classify same-turn conflicts on single-use work for the observed agent.

    * ``duplicate_single_use_assignment``: two of our units hold planner jobs
      of a single-use kind on the same tile (read from the runtime memory the
      agent just wrote; only Tilla populates it).
    * ``duplicate_single_use_action``: two units emitted the same single-use
      op on the same tile this turn (the later one is an environment no-op).
    * ``same_turn_noop``: two units emitted different tile-claiming ops on
      one empty tile this turn (e.g. PLANT and BUILD_COOP): the later is a
      no-op.
    * ``redundant_care_action``: a once-per-day care op emitted on a tile the
      observation already shows as done today (watered / fed / fertilizer
      collected): an environment no-op, i.e. a wasted unit action.
    """
    events: list[dict] = []
    base = {"step": obs["step"], "day": obs["day"], "hour": obs["hour"]}
    farm = obs["farms"][obs["player"]]
    memory = _MEMORIES.get(obs["player"])
    if memory is not None and memory.assignment_day == obs["day"]:
        by_resource: dict[tuple, list[tuple[int, str]]] = {}
        for idx, job in memory.unit_assignments.items():
            if job.kind.value in SINGLE_USE_JOBS:
                resource = (job.kind.value, job.target.x, job.target.y)
                by_resource.setdefault(resource, []).append((idx, str(job.key)))
        for resource, holders in by_resource.items():
            if len(holders) > 1:
                events.append(
                    {
                        **base,
                        "kind": "duplicate_single_use_assignment",
                        "resource": resource,
                        "jobs": holders,
                    }
                )
    seen_same: dict[tuple, int] = {}
    seen_tile: dict[tuple, tuple[int, str]] = {}
    for idx, (pos, act) in enumerate(zip(units, acts, strict=False)):
        op = act[0] if act else "PASS"
        if op not in SINGLE_USE_OPS:
            continue
        tile = tuple(pos)
        if (tile, op) in seen_same:
            events.append(
                {
                    **base,
                    "kind": "duplicate_single_use_action",
                    "tile": tile,
                    "units": [seen_same[(tile, op)], idx],
                    "op": op,
                }
            )
        elif tile in seen_tile and op in TILE_CLAIM_OPS and seen_tile[tile][1] in TILE_CLAIM_OPS:
            events.append(
                {
                    **base,
                    "kind": "same_turn_noop",
                    "tile": tile,
                    "units": [seen_tile[tile][0], idx],
                    "ops": [seen_tile[tile][1], op],
                }
            )
        seen_same.setdefault((tile, op), idx)
        seen_tile.setdefault(tile, (idx, op))
        if op in ONCE_PER_DAY_OPS and _already_done_today(farm["tiles"][tile[1]][tile[0]], op):
            same_day_repeats[(obs["day"], tile, op)] += 1
            events.append({**base, "kind": "redundant_care_action", "tile": tile, "op": op})
    return events


def _already_done_today(tile, op: str) -> bool:
    if not isinstance(tile, dict):
        return False
    if op == "WATER":
        return bool(tile.get("watered_today"))
    if op == "FEED":
        return "animal" in tile and bool(tile.get("fed_today"))
    if op == "COLLECT_FERTILIZER":
        return "animal" in tile and not tile.get("fertilizer_available", True)
    return False


def run_game(candidate: Callable, opponent, seed: int, seat: int, instrument: bool = True) -> dict:
    """Play one official episode; ``candidate`` sits in ``seat`` (0 or 1)."""
    from kaggle_environments import make

    stats = Counter()
    timings: list[float] = []
    hand_ops = Counter()
    hand_work = Counter()  # CARE / HARVEST / PRODUCTION actions performed by hands
    hires_by_day: Counter = Counter()
    hire_spend = 0
    duplicates = Counter()  # see _duplicate_checks
    duplicate_events: list[dict] = []
    same_day_repeats: Counter = Counter()  # (day, tile, op) -> redundant emissions

    def observed(obs):
        nonlocal hire_spend
        t0 = time.perf_counter()
        action = candidate(obs)
        timings.append((time.perf_counter() - t0) * 1000)
        n_hands = len(obs["farms"][obs["player"]]["hands"])
        if validate_or_fallback(action, n_hands) != action:
            stats["malformed"] += 1
        try:
            parse_observation(obs)
        except Exception:
            stats["parse_failures"] += 1
        if instrument and isinstance(action, dict):
            farm = obs["farms"][obs["player"]]
            hires = sum(1 for o in action.get("market", []) if o and o[0] == "HIRE")
            if hires:
                hires_by_day[obs["day"]] += hires
                # sequential Fibonacci cost from today's count
                a, b = 1, 1
                for _ in range(farm["hires_today"]):
                    a, b = b, a + b
                for _ in range(hires):
                    hire_spend += a
                    a, b = b, a + b
            for h in action.get("hands", []):
                op = h[0] if h else "PASS"
                hand_ops[
                    "PASS"
                    if op == "PASS"
                    else "MOVE"
                    if op in MOVE_OPS
                    else "LOGISTIC"
                    if op in LOGISTIC_OPS
                    else "PRODUCTIVE"
                ] += 1
                if op in CARE_OPS:
                    hand_work["CARE"] += 1
                elif op in HARVEST_OPS:
                    hand_work["HARVEST"] += 1
                elif op in PRODUCTION_OPS:
                    hand_work["PRODUCTION"] += 1
            units = [farm["farmer"], *farm["hands"]]
            acts = [action["farmer"], *action.get("hands", [])]
            for event in _duplicate_checks(obs, units, acts, same_day_repeats):
                duplicates[event["kind"]] += 1
                if len(duplicate_events) < 200:
                    duplicate_events.append(event)
        return action

    agents = [observed, opponent] if seat == 0 else [opponent, observed]
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": seed}, debug=True)
    env.run(agents)
    final = env.steps[-1]
    statuses = [s["status"] for step in env.steps for s in step]
    timings.sort()
    total_hand = sum(hand_ops.values())
    result = {
        "seed": seed,
        "seat": seat,
        "steps": len(env.steps),
        "done": env.done,
        "statuses": [s["status"] for s in final],
        "candidate": final[seat]["reward"],
        "opponent": final[1 - seat]["reward"],
        "TIMEOUT": statuses.count("TIMEOUT"),
        "ERROR": statuses.count("ERROR"),
        "INVALID": statuses.count("INVALID"),
        "malformed": stats["malformed"],
        "parse_failures": stats["parse_failures"],
        "ms_median": statistics.median(timings) if timings else 0.0,
        "ms_p95": timings[int(len(timings) * 0.95) - 1] if timings else 0.0,
        "ms_p99": timings[int(len(timings) * 0.99) - 1] if timings else 0.0,
        "ms_max": timings[-1] if timings else 0.0,
        "hires_total": sum(hires_by_day.values()),
        "hires_by_day": dict(sorted(hires_by_day.items())),
        "hire_spend": hire_spend,
        "hand_actions": dict(hand_ops),
        "hand_pass_rate": (hand_ops["PASS"] / total_hand) if total_hand else 0.0,
        "hand_work": dict(hand_work),
        "duplicate_single_use_assignments": duplicates["duplicate_single_use_assignment"],
        "duplicate_single_use_actions": duplicates["duplicate_single_use_action"],
        "same_turn_noops": duplicates["same_turn_noop"],
        "redundant_care_actions": duplicates["redundant_care_action"],
        "duplicate_events": duplicate_events,
        **_care_losses(env.steps, seat),
        "opponent_care_losses": _care_losses(env.steps, 1 - seat),
    }
    return result


def paired(candidate: Callable, opponent, seeds: range) -> list[dict]:
    return [run_game(candidate, opponent, seed, seat) for seed in seeds for seat in (0, 1)]


def summarize(results: list[dict]) -> dict:
    margins = [r["candidate"] - r["opponent"] for r in results]
    hires = [r["hires_total"] / 30 for r in results]
    return {
        "games": len(results),
        "wins": sum(m > 0 for m in margins),
        "ties": sum(m == 0 for m in margins),
        "losses": sum(m < 0 for m in margins),
        "seat0": [
            sum(1 for r in results if r["seat"] == 0 and r["candidate"] > r["opponent"]),
            sum(1 for r in results if r["seat"] == 0 and r["candidate"] == r["opponent"]),
            sum(1 for r in results if r["seat"] == 0 and r["candidate"] < r["opponent"]),
        ],
        "seat1": [
            sum(1 for r in results if r["seat"] == 1 and r["candidate"] > r["opponent"]),
            sum(1 for r in results if r["seat"] == 1 and r["candidate"] == r["opponent"]),
            sum(1 for r in results if r["seat"] == 1 and r["candidate"] < r["opponent"]),
        ],
        "median_margin": statistics.median(margins),
        "mean_margin": statistics.mean(margins),
        "min_margin": min(margins),
        "max_margin": max(margins),
        "candidate_bank": [
            min(r["candidate"] for r in results),
            max(r["candidate"] for r in results),
        ],
        "opponent_bank": [min(r["opponent"] for r in results), max(r["opponent"] for r in results)],
        "avg_hires_per_day": statistics.mean(hires),
        "median_hires_per_day": statistics.median(hires),
        "max_hires_per_day": max(max(r["hires_by_day"].values(), default=0) for r in results),
        "total_hire_spend": sum(r["hire_spend"] for r in results),
        "hand_actions": dict(sum((Counter(r["hand_actions"]) for r in results), Counter())),
        "hand_pass_rate": statistics.mean(r["hand_pass_rate"] for r in results),
        "crops_lost_unwatered": sum(r.get("crops_lost_unwatered", 0) for r in results),
        "crops_lost_decay": sum(r.get("crops_lost_decay", 0) for r in results),
        "fresh_plantings_unwatered": sum(r.get("fresh_plantings_unwatered", 0) for r in results),
        "animals_escaped": sum(r.get("animals_escaped", 0) for r in results),
        "opponent_care_losses": dict(
            sum((Counter(r["opponent_care_losses"]) for r in results), Counter())
        ),
        "hand_work": dict(sum((Counter(r["hand_work"]) for r in results), Counter())),
        "duplicate_single_use_assignments": sum(
            r["duplicate_single_use_assignments"] for r in results
        ),
        "duplicate_single_use_actions": sum(r["duplicate_single_use_actions"] for r in results),
        "same_turn_noops": sum(r["same_turn_noops"] for r in results),
        "redundant_care_actions": sum(r["redundant_care_actions"] for r in results),
        "crashes": sum(r["ERROR"] + r["INVALID"] for r in results),
        "timeouts": sum(r["TIMEOUT"] for r in results),
        "malformed": sum(r["malformed"] for r in results),
        "parse_failures": sum(r["parse_failures"] for r in results),
        "all_complete": all(
            r["steps"] == 720 and r["statuses"] == ["DONE", "DONE"] for r in results
        ),
        "ms_median": statistics.median(r["ms_median"] for r in results),
        "ms_p95_max": max(r["ms_p95"] for r in results),
        "ms_p99_max": max(r["ms_p99"] for r in results),
        "ms_max": max(r["ms_max"] for r in results),
    }


def _resolve(name: str) -> Callable | str:
    if name == "main":
        import main

        return main.agent
    if name == "control":
        import main

        return hiring_disabled(main.agent)
    if name == "baseline":
        from agents.baseline import agent

        return agent
    return name  # built-in: pass / random / starter


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", default="main")
    parser.add_argument("--opponent", default="pass")
    parser.add_argument(
        "--seeds", type=int, nargs=2, default=[4000, 4039], metavar=("FIRST", "LAST")
    )
    parser.add_argument("--json", action="store_true", help="print per-game results too")
    args = parser.parse_args(argv)
    results = paired(
        _resolve(args.candidate), _resolve(args.opponent), range(args.seeds[0], args.seeds[1] + 1)
    )
    if args.json:
        print(json.dumps(results))
    print(json.dumps(summarize(results), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
