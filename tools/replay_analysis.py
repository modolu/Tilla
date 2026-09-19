"""Explain Tilla's decisions for selected turns (offline only).

Runs one official episode (or takes recorded observations) and prints, for the
requested steps, the ranked opportunities with their revenue, costs, labor,
land, net value and score, the cash reserve and shed pressure, every objective
the strategy listed, the hiring decision with its reasoning, the generated jobs
and which unit was assigned to which job (and which assignments were carried
over from the previous turn). Never imported by the submitted runtime.

    python -m tools.replay_analysis --opponent pass --seed 2026 --steps 0 24 240
"""

from __future__ import annotations

import argparse
import copy
import sys
from collections.abc import Iterable

from kaggriculture_bot import economy
from kaggriculture_bot.features import shed_occupancy, shed_pressure
from kaggriculture_bot.models import EpisodeMemory, GameState, MarketOp
from kaggriculture_bot.parser import parse_observation
from kaggriculture_bot.runtime import get_episode_memory, reset_episode_memory
from kaggriculture_bot.strategy import PRIORITY_ECONOMIC, choose_plan, hiring_decision
from kaggriculture_bot.tasks import assign_units, generate_jobs, unit_job


def explain_state(state: GameState, memory: EpisodeMemory | None = None, top: int = 8) -> str:
    """Human-readable explanation of one turn. ``memory`` (copied, never
    mutated) supplies the persisted assignments; a fresh memory is used otherwise."""
    memory = copy.deepcopy(memory) if memory is not None else reset_episode_memory(state.player_id)
    carried_over = dict(memory.unit_assignments) if memory.assignment_day == state.day else {}
    plan = choose_plan(state, memory)
    objectives = list(plan.all_objectives())
    reserve = economy.cash_reserve(state)
    committed = sum(economy.order_cost(state, o) for o in plan.market if o.op is not MarketOp.HIRE)
    decision = hiring_decision(state, objectives, reserve + committed)
    jobs = generate_jobs(state, plan)
    assigned = assign_units(state, jobs, memory)
    lines = [
        f"step {state.step} day {state.day} hour {state.hour} player {state.player_id} "
        f"money {state.me.money} reserve {reserve} "
        f"shed {shed_occupancy(state)} pressure {shed_pressure(state):.2f} "
        f"labor_price {economy.labor_price(state):.1f} "
        f"capacity {economy.labor_capacity_remaining(state):.1f} units {len(state.me.units)}",
        f"  market {[(o.op.value, o.item, o.quantity) for o in plan.market]}",
        "  objectives:",
    ]
    for o in objectives:
        lines.append(
            f"    p{o.priority} {o.kind.value:<18} item {o.item} qty {o.quantity} "
            f"deadline {o.deadline_hour} targets {[(t.x, t.y) for t in o.targets][:6]}"
            + (" ..." if len(o.targets) > 6 else "")
        )
    lines.append(
        f"  hiring: hires {decision.hires} costs {decision.costs} "
        f"values {tuple(round(v, 1) for v in decision.values)} next_cost {decision.next_cost} "
        f"| backlog {decision.backlog_actions:.1f} (care {decision.care_actions:.1f}) "
        f"capacity {decision.existing_capacity} uncovered {decision.uncovered_actions:.1f} "
        f"action_value {decision.action_value:.2f} remaining {decision.remaining_turns} "
        f"spawns {[(p.x, p.y) for p in decision.spawns]} | {decision.reason}"
    )
    economic = sum(1 for j in jobs if j.priority >= PRIORITY_ECONOMIC)
    lines.append(f"  jobs ({len(jobs)}, economic or lower {economic}):")
    for j in jobs[:20]:
        lines.append(
            f"    p{j.priority} {j.kind.value:<9} ({j.target.x},{j.target.y}) item {j.item} "
            f"requires {j.requires} deadline {j.deadline_hour} unit {j.unit}"
        )
    if len(jobs) > 20:
        lines.append(f"    ... {len(jobs) - 20} more")
    lines.append("  assignments:")
    for unit in state.me.units:
        job = assigned.get(unit.index)
        if job is None:
            lines.append(
                f"    unit {unit.index} at ({unit.position.x},{unit.position.y}): idle -> PASS"
            )
            continue
        action = unit_job(state, unit, job)
        previous = carried_over.get(unit.index)
        tag = (
            "kept"
            if previous is not None and previous.key == job.key
            else ("switched" if previous is not None else "new")
        )
        lines.append(
            f"    unit {unit.index} at ({unit.position.x},{unit.position.y}): "
            f"{job.kind.value} ({job.target.x},{job.target.y}) [{tag}] -> "
            f"{action.op.value} {action.item or ''} {action.quantity or ''}".rstrip()
        )
    lines.append(
        "  kind      product     score      net    revenue  byprod  setup  input  labor   land  "
        "risk units turns  p  reason"
    )
    for e in economy.rank_opportunities(state)[:top]:
        lines.append(
            f"  {e.kind:<9} {e.product:<10} {e.score:8.3f} {e.expected_net_value:8.1f} "
            f"{e.expected_revenue:8.1f} {e.byproduct_value:7.1f} {e.setup_cost:6.0f} "
            f"{e.input_cost:6.1f} {e.labor_cost:6.1f} {e.land_cost:6.1f} {e.execution_risk:5.1f} "
            f"{e.expected_units:5} {e.turns_to_realize:5} {e.realization_probability:3.1f}  "
            f"{e.reason}"
        )
    return "\n".join(lines)


def explain_observation(obs, memory: EpisodeMemory | None = None) -> str:
    return explain_state(parse_observation(obs), memory)


def explain_episode(opponent: str, seed: int, seat: int, steps: Iterable[int]) -> str:
    """Play Tilla (main.agent) against a built-in opponent and explain the chosen steps."""
    from kaggle_environments import make

    import main

    wanted = set(steps)
    captured = {}

    def tilla(obs):
        if obs["step"] in wanted:
            memory = get_episode_memory(obs["player"], obs["step"])  # the live memory, copied
            captured[obs["step"]] = explain_observation(obs, memory)
        return main.agent(obs)

    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": seed}, debug=True)
    env.run([tilla, opponent] if seat == 0 else [opponent, tilla])
    rewards = [s["reward"] for s in env.steps[-1]]
    report = [captured[s] for s in sorted(captured)]
    report.append(f"terminal rewards {rewards}")
    return "\n\n".join(report)


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opponent", default="pass")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--seat", type=int, default=0, choices=(0, 1))
    parser.add_argument("--steps", type=int, nargs="+", default=[0, 24, 120, 264, 480, 696])
    args = parser.parse_args(argv)
    print(explain_episode(args.opponent, args.seed, args.seat, args.steps))
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
