"""Explain Tilla's economic decisions for selected turns (offline only).

Runs one official episode (or takes recorded observations) and prints, for the
requested steps, the ranked opportunities with their revenue, costs, labor,
land, net value and score, plus the cash reserve, shed pressure and the
objective the strategy selected. Never imported by the submitted runtime.

    python -m tools.replay_analysis --opponent pass --seed 2026 --steps 0 24 240
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable

from kaggriculture_bot import economy
from kaggriculture_bot.features import shed_occupancy, shed_pressure
from kaggriculture_bot.models import GameState
from kaggriculture_bot.parser import parse_observation
from kaggriculture_bot.runtime import reset_episode_memory
from kaggriculture_bot.strategy import choose_plan
from kaggriculture_bot.tasks import choose_nearest_objective


def explain_state(state: GameState, top: int = 8) -> str:
    """Human-readable economic explanation of one turn."""
    plan = choose_plan(state, reset_episode_memory(state.player_id))
    executed = choose_nearest_objective(state, plan)
    lines = [
        f"step {state.step} day {state.day} hour {state.hour} player {state.player_id} "
        f"money {state.me.money} reserve {economy.cash_reserve(state)} "
        f"shed {shed_occupancy(state)} pressure {shed_pressure(state):.2f} "
        f"labor_price {economy.labor_price(state):.1f} "
        f"capacity {economy.labor_capacity_remaining(state):.1f}",
        f"  objective {plan.objective.kind} targets {plan.objective.targets[:3]} "
        f"item {plan.objective.item} | executed {executed.kind} | market {list(plan.market)}",
        "  kind      product     score      net    revenue  byprod  setup  input  labor   land  "
        "risk units turns  p  reason",
    ]
    for e in economy.rank_opportunities(state)[:top]:
        lines.append(
            f"  {e.kind:<9} {e.product:<10} {e.score:8.3f} {e.expected_net_value:8.1f} "
            f"{e.expected_revenue:8.1f} {e.byproduct_value:7.1f} {e.setup_cost:6.0f} "
            f"{e.input_cost:6.1f} {e.labor_cost:6.1f} {e.land_cost:6.1f} {e.execution_risk:5.1f} "
            f"{e.expected_units:5} {e.turns_to_realize:5} {e.realization_probability:3.1f}  "
            f"{e.reason}"
        )
    return "\n".join(lines)


def explain_observation(obs) -> str:
    return explain_state(parse_observation(obs))


def explain_episode(opponent: str, seed: int, seat: int, steps: Iterable[int]) -> str:
    """Play Tilla (main.agent) against a built-in opponent and explain the chosen steps."""
    from kaggle_environments import make

    import main

    wanted = set(steps)
    captured = {}

    def tilla(obs):
        if obs["step"] in wanted:
            captured[obs["step"]] = explain_observation(obs)
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
