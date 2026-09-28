"""Paired, seeded candidate-vs-incumbent evaluation (offline only).

Minimal promotion-gate runner used from Milestone 5 on: plays every seed in a
range in both seats (candidate ``main.agent`` vs ``agents.incumbent.agent``)
across worker processes, then reports the source-of-truth gate statistics
(TILLA_STRATEGY.md §19): wins/ties/losses, win rate, the two-sided 95% Wilson
lower bound, seat records, cash margins, pair-level margins, crashes,
timeouts, malformed outputs and the care/duplicate guardrails. Milestone 8
owns the polished one-command tournament/report pipeline.

    python -m tools.tournament --seeds 10000 11499 --workers 3 --out gate.jsonl --resume
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

Z_95 = 1.959963984540054


def wilson_lower_bound(wins: int, games: int, z: float = Z_95) -> float:
    """Two-sided 95% Wilson score interval, lower bound (ties count as non-wins)."""
    if games == 0:
        return 0.0
    p = wins / games
    denom = 1 + z * z / games
    centre = p + z * z / (2 * games)
    spread = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games))
    return (centre - spread) / denom


def _play(args: tuple[int, int]) -> dict:
    seed, seat = args
    import main
    from agents.incumbent import agent as incumbent
    from tools.harness import run_game

    result = run_game(main.agent, incumbent, seed, seat)
    result.pop("duplicate_events", None)
    return result


def run(
    first: int, last: int, workers: int, out: str | None = None, resume: bool = False
) -> list[dict]:
    """Play every (seed, seat) job across ``workers`` processes. With ``out``
    each finished game is appended immediately as one JSON line, so a long
    gate survives interruption; ``resume`` skips games already in ``out``."""
    done: dict[tuple[int, int], dict] = {}
    if out and resume:
        try:
            with open(out, encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    done[(r["seed"], r["seat"])] = r
        except FileNotFoundError:
            pass
    jobs = [
        (seed, seat)
        for seed in range(first, last + 1)
        for seat in (0, 1)
        if (seed, seat) not in done
    ]
    results = list(done.values())
    sink = open(out, "a", encoding="utf-8") if out else None
    try:
        with ProcessPoolExecutor(max_workers=workers, max_tasks_per_child=50) as pool:
            for i, result in enumerate(pool.map(_play, jobs, chunksize=1), start=1):
                results.append(result)
                if sink:
                    sink.write(json.dumps(result) + "\n")
                    sink.flush()
                if i % 50 == 0 or i == len(jobs):
                    print(f"{i}/{len(jobs)} games done", file=sys.stderr, flush=True)
    finally:
        if sink:
            sink.close()
    return results


def gate_report(results: list[dict]) -> dict:
    margins = [r["candidate"] - r["opponent"] for r in results]
    wins = sum(m > 0 for m in margins)
    ties = sum(m == 0 for m in margins)
    losses = sum(m < 0 for m in margins)
    pairs: dict[int, float] = {}
    for r in results:
        pairs[r["seed"]] = pairs.get(r["seed"], 0.0) + r["candidate"] - r["opponent"]
    paired = sorted(pairs.items(), key=lambda kv: kv[1])

    def seat(s, sign):
        return sum(1 for r in results if r["seat"] == s and sign(r["candidate"] - r["opponent"]))

    def care(key):
        return sum(r.get(key, 0) for r in results)

    return {
        "games": len(results),
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "win_rate": wins / len(results) if results else 0.0,
        "wilson_lower_95": wilson_lower_bound(wins, len(results)),
        "seat0": [seat(0, lambda m: m > 0), seat(0, lambda m: m == 0), seat(0, lambda m: m < 0)],
        "seat1": [seat(1, lambda m: m > 0), seat(1, lambda m: m == 0), seat(1, lambda m: m < 0)],
        "median_margin": statistics.median(margins) if margins else 0.0,
        "mean_margin": statistics.mean(margins) if margins else 0.0,
        "min_margin": min(margins, default=0.0),
        "max_margin": max(margins, default=0.0),
        "candidate_bank": [
            min(r["candidate"] for r in results),
            max(r["candidate"] for r in results),
        ],
        "incumbent_bank": [
            min(r["opponent"] for r in results),
            max(r["opponent"] for r in results),
        ],
        "paired_positive": sum(1 for _, m in paired if m > 0),
        "paired_zero": sum(1 for _, m in paired if m == 0),
        "paired_negative": sum(1 for _, m in paired if m < 0),
        "median_paired_margin": statistics.median([m for _, m in paired]) if paired else 0.0,
        "worst_paired_seeds": paired[:10],
        "crashes": sum(r["ERROR"] + r["INVALID"] for r in results),
        "timeouts": sum(r["TIMEOUT"] for r in results),
        "malformed": sum(r["malformed"] for r in results),
        "parse_failures": sum(r["parse_failures"] for r in results),
        "all_complete": all(
            r["steps"] == 720 and r["statuses"] == ["DONE", "DONE"] for r in results
        ),
        "crops_lost_unwatered": care("crops_lost_unwatered"),
        "fresh_plantings_unwatered": care("fresh_plantings_unwatered"),
        "animals_escaped": care("animals_escaped"),
        "crops_lost_decay": care("crops_lost_decay"),
        "duplicate_single_use_assignments": care("duplicate_single_use_assignments"),
        "duplicate_single_use_actions": care("duplicate_single_use_actions"),
        "same_turn_noops": care("same_turn_noops"),
        "hand_pass_rate": statistics.mean(r["hand_pass_rate"] for r in results) if results else 0.0,
        "ms_median": statistics.median(r["ms_median"] for r in results) if results else 0.0,
        "ms_p95_max": max((r["ms_p95"] for r in results), default=0.0),
        "ms_p99_max": max((r["ms_p99"] for r in results), default=0.0),
        "ms_max": max((r["ms_max"] for r in results), default=0.0),
        "premium_investments_while_glutted": care("premium_investments_while_glutted"),
        "glut_protection_rejections": care("glut_protection_rejections"),
        "town_model_changed_sales": care("town_model_changed_sales"),
        "future_shops_changed_sales": care("future_shops_changed_sales"),
        "sales_checks": care("sales_checks"),
        "premium": _premium(results),
    }


def _premium(results: list[dict]) -> dict:
    from kaggriculture_bot.constants import PREMIUM_PRODUCTS

    out = {}
    for product in PREMIUM_PRODUCTS:
        sold = Counter()
        revenue = 0
        mins = []
        for r in results:
            sales = r["realized_sales"]
            sold[product] += sales["units_sold"].get(product, 0)
            revenue += sales["revenue"].get(product, 0)
            if product in sales["min_price"]:
                mins.append(sales["min_price"][product])
        units = sold[product]
        out[product] = {
            "units_sold": units,
            "units_held_at_end": sum(r["final_shed"].get(product, 0) for r in results),
            "avg_price": round(revenue / units, 1) if units else None,
            "min_price": min(mins) if mins else None,
            "floor_sales": sum(r["realized_sales"]["floor_sales"].get(product, 0) for r in results),
        }
    return out


def gate_passes(report: dict) -> bool:
    return (
        report["win_rate"] > 0.53
        and report["wilson_lower_95"] > 0.50
        and report["median_margin"] > 0
        and report["crashes"] == 0
        and report["timeouts"] == 0
    )


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs=2, required=True, metavar=("FIRST", "LAST"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", default=None, help="append per-game results as JSON lines")
    parser.add_argument("--resume", action="store_true", help="skip games already in --out")
    args = parser.parse_args(argv)
    results = run(args.seeds[0], args.seeds[1], args.workers, args.out, args.resume)
    report = gate_report(results)
    report["gate_passes"] = gate_passes(report)
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
