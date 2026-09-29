"""Milestone 8 tournament / promotion-gate infrastructure (tools/tournament.py).

Terminology: an *episode* is one game with the candidate in one seat; a *paired seed* is a
seed played twice with the seats swapped; a *completed pair* has one valid episode per seat.

Most runner tests use ``fake_episode`` (below) as the episode function: it exercises the real
worker processes, pipes, locking and storage without playing 20-second official episodes.
Its behaviour is steered through ``behaviour.json`` in the run directory.
"""

from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

import pytest

from tools import tournament as t

ROOT = Path(__file__).resolve().parents[1]
FAKE_EPISODE = "tests.test_tournament:fake_episode"
# A deliberately small policy so fake runs can pass the stable eligibility gate and reach
# the holdout. It is weaker than the Tilla gate, so a final verdict under it always FAILs.
SMALL_POLICY = t.PromotionPolicy(
    name="test-small",
    min_paired_seeds=3,
    min_episodes=6,
    min_stable_paired_seeds=2,
    min_holdout_paired_seeds=1,
)


# --- fake episode function (runs inside worker processes) --------------------------------------


def fake_margin(seed: int, seat: int, bias: int = 0) -> int:
    return (seed * 37 + seat * 11) % 21 - 8 + bias


def fake_episode(job: dict, agents: dict, options: dict) -> dict:
    run_dir = Path(options["run_dir"])
    behaviour_file = run_dir / "behaviour.json"
    behaviour = json.loads(behaviour_file.read_text()) if behaviour_file.exists() else {}
    key = job["key"]
    marker = run_dir / f"seen-{key.replace(':', '_')}"
    first_attempt = not marker.exists()
    marker.write_text("x")
    if key in behaviour.get("crash_once", []) and first_attempt:
        os._exit(3)
    if key in behaviour.get("hang", []):
        time.sleep(3600)
    time.sleep(behaviour.get("sleep", 0.0))
    margin = fake_margin(job["seed"], job["seat"], behaviour.get("bias", 0))
    fields = {
        "terminal_valid": True,
        "validation_errors": [],
        "steps": options["episode_steps"],
        "final_step": options["episode_steps"] - 1,
        "candidate_status": "DONE",
        "incumbent_status": "DONE",
        "candidate_failure": None,
        "incumbent_failure": None,
        "candidate_bank": 5000 + margin,
        "incumbent_bank": 5000,
        "invalid_actions": {"env_invalid": 0, "shape_violations": 0, "examples": []},
        "timing": {
            "act_timeout_s": 1.0,
            "candidate_call": t.duration_stats([5.0, 7.0, 9.0 + job["seed"] % 3]),
            "incumbent_call": t.duration_stats([4.0, 6.0]),
            "candidate_calls_over_act_timeout": 0,
            "candidate_overage_used_s": 0.0,
            "candidate_remaining_overage_s": 60,
            "turn": t.duration_stats([10.0, 12.0]),
            "env_make_s": 0.01,
            "episode_s": 0.02,
            "episode_wall_s": 0.02,
            "host_suspended": False,
        },
        "counters": {"candidate": {"crops_lost_unwatered": job["seed"] % 2}, "incumbent": {}},
        "replay": {"saved": None},
    }
    status = behaviour.get("status", {}).get(key)
    if status is None and first_attempt:
        status = behaviour.get("status_once", {}).get(key)
    if status:
        role, _, kind = status.partition("_")
        fields[f"{role}_failure"] = {"step": 10, "status": kind.upper()}
        fields["terminal_valid"] = False
    t.finalize_outcome(fields, run_error=None)
    return fields


# --- helpers -------------------------------------------------------------------------------------


def fake_config(stable=(1000, 3), holdout=None, policy=t.TILLA_PROMOTION_POLICY, **kw):
    parts = [t.Partition(t.STABLE, *stable)]
    if holdout:
        parts.append(t.Partition(t.HOLDOUT, *holdout))
    return t.RunConfig(
        partitions=tuple(parts),
        candidate=kw.get("candidate", "pass"),
        incumbent=kw.get("incumbent", "starter"),
        episode_fn=FAKE_EPISODE,
        policy=policy,
    )


def options(**kwargs) -> t.RunOptions:
    kwargs.setdefault("allow_temp", True)
    kwargs.setdefault("workers", 2)
    kwargs.setdefault("heartbeat_s", 0.2)
    return t.RunOptions(**kwargs)


def set_behaviour(run_dir: Path, **behaviour) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "behaviour.json").write_text(json.dumps(behaviour))


def keys_in(run_dir: Path, partition: str = t.STABLE) -> list[str]:
    return [r["key"] for r in t.read_jsonl(t.results_path(run_dir, partition)).records]


def stub_manifest(episode_fn=t.DEFAULT_EPISODE_FN, steps=t.EPISODE_STEPS) -> dict:
    return {
        "config": {"episode_fn": episode_fn, "episode_steps": steps},
        "identity": {"candidate": {"digest": "c" * 64}, "incumbent": {"digest": "i" * 64}},
    }


def record(seed, seat, margin=10, partition="stable", status="ok"):
    ok = status == "ok"
    return {
        "key": t.job_key(partition, seed, seat),
        "partition": partition,
        "seed": seed,
        "seat": seat,
        "status": status,
        "margin": margin if ok else None,
        "outcome": t.outcome_of(margin) if ok else None,
        "candidate_bank": 1000 + margin,
        "incumbent_bank": 1000,
    }


def winning_margin(seed: int, seat: int) -> int:
    """60% of episodes won by 10, 40% lost by 10."""
    return 10 if (seed + seat) % 5 < 3 else -10


def pairs_for(partition: str, first: int, count: int, margin=winning_margin, seats=(0, 1)):
    return [
        record(s, seat, margin(s, seat), partition)
        for s in range(first, first + count)
        for seat in seats
    ]


SCENARIOS_PASS = {"status": t.PASS, "reason": "ok"}
STABLE_PLAN = t.Partition(t.STABLE, 0, 1500)
HOLDOUT_PLAN = t.Partition(t.HOLDOUT, 900_000, 500)


def evaluate(
    stable_records,
    holdout_records,
    stable_plan=STABLE_PLAN,
    holdout_plan=HOLDOUT_PLAN,
    policy=t.TILLA_PROMOTION_POLICY,
    scenarios=SCENARIOS_PASS,
    manifest=None,
):
    stable = t.summarize(stable_records, [stable_plan])
    eligibility = t.stable_eligibility(stable, policy)
    holdout = None
    plans = [stable_plan]
    if holdout_records is not None:
        holdout = t.summarize(holdout_records, [holdout_plan])
        plans.append(holdout_plan)
    combined = t.summarize(stable_records + (holdout_records or []), plans)
    gate = t.evaluate_gate(
        stable, holdout, combined, eligibility, policy, scenarios, manifest or stub_manifest()
    )
    return gate, eligibility, combined


def failed(gate) -> set[str]:
    return set(gate["failed_checks"])


def full_evidence():
    return pairs_for("stable", 0, 1500), pairs_for("holdout", 900_000, 500)


# --- statistics ----------------------------------------------------------------------------------


def test_wilson_interval_matches_reference_values():
    lo, hi = t.wilson_interval(50, 100)
    assert lo == pytest.approx(0.403832, abs=1e-6) and hi == pytest.approx(0.596168, abs=1e-6)
    lo, hi = t.wilson_interval(1060, 2000)
    assert lo == pytest.approx(0.508090, abs=1e-5) and hi == pytest.approx(0.551790, abs=1e-5)
    assert t.wilson_interval(0, 0) == (0.0, 1.0)
    assert t.wilson_interval(0, 10)[0] == 0.0
    assert t.wilson_interval(10, 10)[1] == pytest.approx(1.0)
    with pytest.raises(ValueError):
        t.wilson_interval(11, 10)


def test_summary_counts_only_completed_pairs():
    records = [
        record(1, 0, 30),
        record(1, 1, -10),  # split pair, pair margin +20
        record(2, 0, 5),
        record(2, 1, 7),  # both won, +12
        record(3, 0, -4),
        record(3, 1, 0),  # loss + tie, -4
        record(4, 0, 100),  # seat 1 missing: not a completed pair
        record(5, 0, 50),
        record(5, 1, 0, status="candidate_error"),  # non-ok episode: not a completed pair
    ]  # seed 6 planned but never played
    s = t.summarize(list(reversed(records)), [t.Partition("stable", 1, 6)])
    p = s["pairs"]
    counts = (p["completed_pairs"], p["episodes"], p["wins"], p["losses"], p["ties"])
    assert counts == (3, 6, 3, 2, 1)
    assert p["median_margin"] == 2.5  # margins -10,-4,0,5,7,30 -> (0+5)/2
    assert p["per_seed"]["median_pair_margin"] == 12
    assert (p["per_seed"]["pairs_won"], p["per_seed"]["pairs_lost"]) == (2, 1)
    assert s["by_seat"]["0"]["wins"] == 2 and s["by_seat"]["1"]["ties"] == 1
    reasons = {i["seed"]: i["reason"] for i in s["incomplete_seeds"]}
    assert reasons == {4: "missing seat 1", 5: "non-ok episode", 6: "missing seat 0, 1"}
    assert (s["planned_paired_seeds"], s["planned_episodes"], s["recorded_episodes"]) == (6, 12, 9)
    assert s["failures"]["missing_episodes"] == 3 and s["failures"]["candidate_crashes"] == 1


def test_duplicate_seat_results_never_count_toward_pairs():
    records = [
        record(1, 0, 10),
        record(1, 0, 10),  # duplicate seat 0
        record(1, 1, 10),
        record(2, 0, 10),
        record(2, 0, 10),  # two seat-0 results are not a pair
        record(3, 0, 10),
        record(3, 1, 10),
    ]
    s = t.summarize(records, [t.Partition("stable", 1, 3)])
    assert s["pairs"]["completed_pairs"] == 1 and s["pairs"]["episodes"] == 2
    assert s["failures"]["duplicate_results"] == 2
    assert {i["seed"]: i["reason"] for i in s["incomplete_seeds"]} == {
        1: "duplicate seat result",
        2: "duplicate seat result",
    }
    checks = {c["name"]: c for c in t._integrity_checks(s, t.TILLA_PROMOTION_POLICY, "")}
    assert not checks["harness_integrity"]["passed"]


def test_seed_generation_is_deterministic_and_seat_paired():
    parts = [t.Partition("holdout", 900_000, 2), t.Partition("stable", 10, 2)]
    jobs = t.plan_jobs(parts)
    assert [j.key for j in jobs] == [
        "stable:10:0",
        "stable:10:1",
        "stable:11:0",
        "stable:11:1",
        "holdout:900000:0",
        "holdout:900000:1",
        "holdout:900001:0",
        "holdout:900001:1",
    ]
    assert t.plan_jobs(parts) == jobs
    assert t.holdout_partition(0, 500) == t.Partition("holdout", 900_000, 500)
    assert t.holdout_partition(3, 500).first == 930_000
    with pytest.raises(ValueError, match="overlap"):
        t.validate_partitions([t.Partition("stable", 0, 10), t.Partition("holdout", 9, 5)])
    with pytest.raises(ValueError, match="stable"):
        t.validate_partitions([t.Partition("holdout", 0, 10)])
    with pytest.raises(ValueError):
        t.validate_partitions([t.Partition("stable", 0, 1), t.Partition("stable", 5, 1)])


def test_histogram_merge_is_conservative_and_exact_at_max():
    a = t.duration_stats([1.0, 2.0, 3.0, 100.0])
    b = t.duration_stats([4.0, 5.0])
    assert a["p50_ms"] == 2.0 and a["max_ms"] == 100.0
    merged = t.merge_duration_stats([a, b])
    assert merged["n"] == 6 and merged["max_ms"] == 100.0
    assert merged["mean_ms"] == pytest.approx(115.0 / 6, abs=1e-3)
    assert 3.0 <= merged["p50_ms"] <= 3.0 * 1.13  # upper bucket edge, ~12% resolution
    assert merged["p99_ms"] == 100.0
    assert t.merge_duration_stats([])["n"] == 0


# --- policy and paired-seed counting -------------------------------------------------------------


def test_default_policy_matches_the_strategy_document_and_m5_plan():
    text = (ROOT / "TILLA_STRATEGY.md").read_text()
    for phrase in (
        "At least 2,000 paired games.",
        "candidate win rate > 53%;",
        "95% Wilson lower bound > 50%;",
        "median terminal cash margin > 0;",
        "0 crashes;",
        "0 timeouts;",
    ):
        assert phrase in text, phrase
    policy = t.TILLA_PROMOTION_POLICY
    assert (policy.min_paired_seeds, policy.min_episodes) == (2000, 4000)
    assert (policy.min_stable_paired_seeds, policy.min_holdout_paired_seeds) == (1500, 500)
    assert policy.win_rate_above == Fraction(53, 100)
    assert policy.wilson_lower_above == Fraction(1, 2)
    assert policy.median_margin_above == 0
    assert policy.max_candidate_crashes == policy.max_candidate_timeouts == 0
    assert policy.max_candidate_invalid_actions == 0
    assert policy.max_incumbent_failures == policy.max_harness_failures == 0
    assert policy.require_complete_evidence and policy.require_stable_eligibility
    assert policy.require_mandatory_scenarios and not policy.mandatory_scenarios_declared_empty
    assert t.PromotionPolicy.from_dict(policy.as_dict()) == policy


def test_2000_completed_pairs_and_4000_episodes_can_pass():
    stable, holdout = full_evidence()
    gate, eligibility, combined = evaluate(stable, holdout)
    assert eligibility["eligible"], eligibility["failed"]
    assert (combined["pairs"]["completed_pairs"], combined["pairs"]["episodes"]) == (2000, 4000)
    assert gate["verdict"] == "PASS", gate["failed_checks"]


def test_1999_completed_pairs_and_3998_episodes_fail():
    stable, _ = full_evidence()
    holdout = pairs_for("holdout", 900_000, 499)
    plan = t.Partition("holdout", 900_000, 499)
    gate, _, combined = evaluate(stable, holdout, holdout_plan=plan)
    assert (combined["pairs"]["completed_pairs"], combined["pairs"]["episodes"]) == (1999, 3998)
    assert failed(gate) == {"min_paired_seeds", "min_episodes", "min_holdout_paired_seeds"}
    # The same shortfall from one incomplete planned pair also fails completeness.
    gate, _, _ = evaluate(stable, holdout)
    assert {"min_paired_seeds", "min_episodes", "evidence_complete"} <= failed(gate)


def test_1000_seeds_with_2000_episodes_never_satisfy_the_gate():
    stable = pairs_for("stable", 0, 1000)
    plan = t.Partition("stable", 0, 1000)
    gate, eligibility, combined = evaluate(stable, None, stable_plan=plan)
    assert combined["pairs"]["episodes"] == 2000
    assert {"min_paired_seeds", "min_episodes", "min_stable_paired_seeds"} <= failed(gate)
    assert "stable_min_paired_seeds" in eligibility["failed"]


def test_4000_episodes_without_seat_counterparts_fail():
    stable = pairs_for("stable", 0, 3000, seats=(0,))
    holdout = pairs_for("holdout", 900_000, 1000, seats=(1,))
    gate, eligibility, combined = evaluate(
        stable,
        holdout,
        stable_plan=t.Partition("stable", 0, 3000),
        holdout_plan=t.Partition("holdout", 900_000, 1000),
    )
    assert combined["recorded_episodes"] == 4000 and combined["pairs"]["completed_pairs"] == 0
    assert combined["pairs"]["episodes"] == 0
    assert not eligibility["eligible"]
    expected = {"min_paired_seeds", "min_episodes", "evidence_complete", "stable_eligibility"}
    assert expected <= failed(gate)


def test_duplicates_cannot_pad_the_pair_count():
    stable, holdout = full_evidence()
    holdout = holdout[:-1] + [holdout[-2]]  # last seed: seat 0 twice, no seat 1
    gate, _, combined = evaluate(stable, holdout)
    assert combined["pairs"]["completed_pairs"] == 1999
    assert {"min_paired_seeds", "harness_integrity"} <= failed(gate)


# --- thresholds ----------------------------------------------------------------------------------


def _with_wins(winning_pairs: int):
    """Full evidence where the first ``winning_pairs`` pairs are both won, the rest both lost."""

    def margin(seed, seat):
        index = seed if seed < 1500 else 1500 + seed - 900_000
        return 10 if index < winning_pairs else -10

    return pairs_for("stable", 0, 1500, margin), pairs_for("holdout", 900_000, 500, margin)


def test_clear_pass_passes():
    stable, holdout = full_evidence()
    assert evaluate(stable, holdout)[0]["verdict"] == "PASS"


def test_win_rate_of_exactly_53_percent_fails():
    stable, holdout = _with_wins(1060)  # 2120 / 4000 episodes = 53.0%
    gate, _, combined = evaluate(stable, holdout)
    assert combined["pairs"]["win_rate"] == 0.53
    assert "win_rate" in failed(gate)
    stable, holdout = _with_wins(1061)
    gate, _, _ = evaluate(stable, holdout)
    assert "win_rate" not in failed(gate)


def _summary_with_wilson(lower: float) -> dict:
    stable, holdout = full_evidence()
    s = t.summarize(stable + holdout, [STABLE_PLAN, HOLDOUT_PLAN])
    s["pairs"]["wilson95"] = [lower, 0.9]
    return s


def test_wilson_lower_bound_of_exactly_50_percent_fails():
    policy = t.TILLA_PROMOTION_POLICY

    def wilson_check(lower):
        checks = t._statistical_checks(_summary_with_wilson(lower), policy, "")
        return next(c for c in checks if c["name"] == "wilson95_lower")

    assert not wilson_check(0.5)["passed"]
    assert wilson_check(0.5000001)["passed"]


@pytest.mark.parametrize(("margin", "passes"), [(1, True), (0, False), (-1, False)])
def test_median_margin_must_be_strictly_positive(margin, passes):
    stable = pairs_for("stable", 0, 1500, lambda s, seat: margin)
    holdout = pairs_for("holdout", 900_000, 500, lambda s, seat: margin)
    gate, _, _ = evaluate(stable, holdout)
    assert ("median_margin" not in failed(gate)) is passes


def test_even_count_median_of_one_half_is_positive():
    assert t.exact_median([-1, 2]) == Fraction(1, 2)


@pytest.mark.parametrize("status", ["candidate_error", "candidate_timeout", "candidate_invalid"])
def test_any_candidate_failure_fails(status):
    stable, holdout = full_evidence()
    holdout[0] = record(900_000, 0, partition="holdout", status=status)
    gate, _, _ = evaluate(stable, holdout)
    assert gate["verdict"] == "FAIL"
    name = {"candidate_error": "candidate_crashes", "candidate_timeout": "candidate_timeouts"}
    assert name.get(status, "candidate_invalid_actions") in failed(gate)


def test_weaker_policy_can_never_pass_but_stricter_can():
    stable, holdout = full_evidence()
    weak = t.PromotionPolicy(name="weak", min_paired_seeds=10, win_rate_above=Fraction(1, 2))
    assert t.weaker_than_default(weak) == ["min_paired_seeds", "win_rate_above"]
    gate, _, _ = evaluate(stable, holdout, policy=weak)
    assert failed(gate) == {"policy_not_weaker_than_tilla_gate"}
    strict = t.PromotionPolicy(name="strict", win_rate_above=Fraction(54, 100))
    assert t.weaker_than_default(strict) == []
    assert evaluate(stable, holdout, policy=strict)[0]["verdict"] == "PASS"
    declared = t.PromotionPolicy(mandatory_scenarios_declared_empty=True)
    assert t.weaker_than_default(declared) == ["mandatory_scenarios_declared_empty"]


def test_unofficial_runner_fails():
    stable, holdout = full_evidence()
    gate, _, _ = evaluate(stable, holdout, manifest=stub_manifest(episode_fn=FAKE_EPISODE))
    assert failed(gate) == {"official_episode_runner"}


def test_calls_over_act_timeout_are_an_advisory_not_a_timeout():
    stable, holdout = full_evidence()
    holdout[0]["timing"] = {"candidate_calls_over_act_timeout": 3, "act_timeout_s": 1.0}
    gate, _, _ = evaluate(stable, holdout)
    assert gate["verdict"] == "PASS"
    advisory = {a["name"]: a for a in gate["advisories"]}
    assert advisory["calls_within_act_timeout"]["passed"] is False


# --- stable -> holdout -> combined -------------------------------------------------------------


def test_strong_holdout_cannot_rescue_an_ineligible_stable_partition():
    stable = pairs_for("stable", 0, 1500, lambda s, seat: -5)  # stable loses everything
    holdout = pairs_for("holdout", 900_000, 500, lambda s, seat: 10_000)
    gate, eligibility, _ = evaluate(stable, holdout)
    assert not eligibility["eligible"]
    assert {"stable_win_rate", "stable_median_margin"} <= set(eligibility["failed"])
    assert "stable_eligibility" in failed(gate) and gate["verdict"] == "FAIL"


def test_missing_holdout_fails_the_final_verdict():
    stable, _ = full_evidence()
    gate, eligibility, _ = evaluate(stable, None)
    assert eligibility["eligible"]
    assert failed(gate) == {
        "holdout_revealed",
        "min_paired_seeds",
        "min_episodes",
        "min_holdout_paired_seeds",
    }


# --- opponent and harness failures -----------------------------------------------------------


@pytest.mark.parametrize(
    "status", ["incumbent_error", "incumbent_timeout", "incumbent_invalid", "harness_error"]
)
def test_opponent_or_harness_failure_never_becomes_a_candidate_win(status):
    base = [record(1, 0, 10), record(1, 1, -10), record(2, 0, -10), record(2, 1, -10)]
    before = t.summarize(base, [t.Partition("stable", 1, 2)])
    # Seat 1 of seed 2 now "ends" with the candidate richer because the opponent failed.
    failed_episode = record(2, 1, 500, status=status)
    after = t.summarize(base[:3] + [failed_episode], [t.Partition("stable", 1, 2)])
    assert after["pairs"]["wins"] <= before["pairs"]["wins"]
    assert after["all_ok_episodes"]["wins"] <= before["all_ok_episodes"]["wins"]
    assert after["pairs"]["completed_pairs"] == 1
    checks = {c["name"]: c for c in t._integrity_checks(after, t.TILLA_PROMOTION_POLICY, "")}
    key = "incumbent_integrity" if status.startswith("incumbent") else "harness_integrity"
    assert not checks[key]["passed"] and not checks["evidence_complete"]["passed"]


def test_incumbent_failure_blocks_promotion_even_with_full_counts():
    stable, holdout = full_evidence()
    holdout.append(record(900_500, 0, partition="holdout", status="incumbent_error"))
    holdout.append(record(900_500, 1, partition="holdout"))
    plan = t.Partition("holdout", 900_000, 501)
    gate, _, combined = evaluate(stable, holdout, holdout_plan=plan)
    assert combined["pairs"]["completed_pairs"] == 2000
    assert combined["failures"]["incumbent_failures"] == 1
    assert {"incumbent_integrity", "evidence_complete"} <= failed(gate)


def test_finalize_outcome_gives_no_margin_to_non_candidate_failures():
    for failure, run_error, expected in (
        ({"incumbent_failure": {"step": 3, "status": "ERROR"}}, None, "incumbent_error"),
        ({}, "RuntimeError: boom", "harness_error"),
        ({"terminal_valid": False}, None, "incomplete"),
    ):
        fields = {
            "terminal_valid": True,
            "candidate_failure": None,
            "incumbent_failure": None,
            "candidate_bank": 9000,
            "incumbent_bank": 100,
            **failure,
        }
        t.finalize_outcome(fields, run_error)
        assert fields["status"] == expected
        assert fields["margin"] is None and fields["outcome"] is None
    assert t.is_replayable_failure("incumbent_timeout") and t.is_replayable_failure("incomplete")
    assert not t.is_replayable_failure("candidate_error") and not t.is_replayable_failure("ok")


# --- scenarios -----------------------------------------------------------------------------------


def test_scenario_evidence_fails_closed(tmp_path):
    manifest = stub_manifest()
    defs = tmp_path / "scenarios.json"
    results = tmp_path / "results.json"
    ids = {"candidate_id": "c" * 64, "incumbent_id": "i" * 64}

    def check(payload=None, suite=None, policy=t.TILLA_PROMOTION_POLICY):
        defs.write_text(json.dumps({"scenarios": suite if suite is not None else [{"name": "a"}]}))
        if payload is None:
            return t.check_scenarios(None, manifest, policy, defs)
        results.write_text(json.dumps(payload))
        return t.check_scenarios(results, manifest, policy, defs)

    missing_defs = t.check_scenarios(None, manifest, definitions=tmp_path / "none.json")
    assert missing_defs["status"] == t.MISSING_EVIDENCE and "not found" in missing_defs["reason"]
    empty = check(suite=[])
    assert empty["status"] == t.MISSING_EVIDENCE and "0 mandatory" in empty["reason"]
    assert check({**ids, "scenarios": []}, suite=[])["status"] == t.MISSING_EVIDENCE
    optional_only = check(suite=[{"name": "x", "mandatory": False}])
    assert optional_only["status"] == t.MISSING_EVIDENCE
    declared = t.PromotionPolicy(mandatory_scenarios_declared_empty=True)
    assert check(suite=[], policy=declared)["status"] == t.PASS
    no_file = check()
    assert no_file["status"] == t.MISSING_EVIDENCE and "--scenario-results" in no_file["reason"]
    assert check({**ids, "scenarios": []})["missing"] == ["a"]
    assert check({**ids, "scenarios": [{"name": "a"}]})["unjudged"] == ["a"]
    other = check({**ids, "candidate_id": "x" * 64, "scenarios": []})
    assert other["status"] == t.MISSING_EVIDENCE and "another candidate" in other["reason"]
    regression = check({**ids, "scenarios": [{"name": "a", "material_regression": True}]})
    assert regression["status"] == t.FAIL and regression["regressions"] == ["a"]
    ok = check({**ids, "scenarios": [{"name": "a", "material_regression": False}]})
    assert ok["status"] == t.PASS


def test_missing_scenario_evidence_fails_the_gate():
    stable, holdout = full_evidence()
    missing = {"status": t.MISSING_EVIDENCE, "reason": "no scenario results supplied"}
    gate, _, _ = evaluate(stable, holdout, scenarios=missing)
    assert failed(gate) == {"mandatory_scenarios"}
    check = next(c for c in gate["checks"] if c["name"] == "mandatory_scenarios")
    assert check["actual"] == "MISSING_EVIDENCE: no scenario results supplied"


def test_the_repository_scenario_suite_currently_yields_missing_evidence():
    assert t.check_scenarios(None, stub_manifest())["status"] == t.MISSING_EVIDENCE


# --- identity ------------------------------------------------------------------------------------


def test_import_closure_covers_the_runtime_and_the_frozen_incumbent():
    main_files = t.import_closure("main")
    assert "main.py" in main_files and "kaggriculture_bot/strategy.py" in main_files
    assert not any(f.startswith(("tools/", "tests/", "agents/")) for f in main_files)
    inc = t.import_closure("agents.incumbent")
    # The current champion (agents.incumbent) is the frozen Milestone 7 snapshot.
    assert "agents/incumbent_m7/strategy.py" in inc and "agents/__init__.py" in inc
    assert not any(f.startswith("kaggriculture_bot/") or f == "main.py" for f in inc)
    # The Milestone 6 care-checker correction is shared with tools.harness, so the
    # harness is part of the runner's identity: a care-checker change invalidates a run.
    assert "tools/harness.py" in t.import_closure("tools.tournament")


def _write_agent(root: Path):
    pkg = root / "fakebot"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "helper.py").write_text("VALUE = 1\n")
    (root / "fakeagent.py").write_text(
        "from fakebot import helper\n\n\ndef agent(obs):\n"
        "    return {'farmer': ['PASS'], 'hands': [], 'market': []}\n"
    )


def test_agent_identity_tracks_every_imported_file(tmp_path):
    _write_agent(tmp_path)
    first = t.agent_identity("fakeagent:agent", tmp_path)
    assert set(first["files"]) == {"fakeagent.py", "fakebot/__init__.py", "fakebot/helper.py"}
    (tmp_path / "fakebot" / "helper.py").write_text("VALUE = 2\n")
    assert t.agent_identity("fakeagent:agent", tmp_path)["digest"] != first["digest"]
    assert t.agent_identity("pass")["kind"] == "builtin"
    assert t.agent_identity("random")["deterministic"] is False
    with pytest.raises(ValueError):
        t.agent_identity("missing_module:agent", tmp_path)
    with pytest.raises(ValueError):
        t.parse_agent_spec("no_attribute")


def test_resume_refuses_changed_candidate_code_config_or_policy(tmp_path):
    root, run_dir = tmp_path / "repo", tmp_path / "run"
    _write_agent(root)
    config = fake_config(stable=(1, 1), candidate="fakeagent:agent")
    assert t.run_tournament(run_dir, config, options(workers=1), root=root).state == "finished"
    other_policy = fake_config(stable=(1, 1), candidate="fakeagent:agent", policy=SMALL_POLICY)
    with pytest.raises(t.ConfigMismatchError):
        t.run_tournament(run_dir, other_policy, options(), root=root)
    (root / "fakebot" / "helper.py").write_text("VALUE = 99\n")
    with pytest.raises(t.IdentityMismatchError, match="fakebot/helper.py"):
        t.run_tournament(run_dir, config, options(workers=1), root=root)


def test_records_with_foreign_identity_are_rejected(tmp_path):
    run_dir = tmp_path / "run"
    t.run_tournament(run_dir, fake_config(stable=(1, 1)), options())
    path = t.results_path(run_dir, t.STABLE)
    records = t.read_jsonl(path).records
    records[0]["candidate_id"] = "0" * 64
    path.write_text("".join(t.canonical_json(r) + "\n" for r in records))
    with pytest.raises(t.IdentityMismatchError):
        t.build_report(run_dir, ledger=tmp_path / "ledger.jsonl")


# --- storage -------------------------------------------------------------------------------------


def test_malformed_middle_line_is_corruption(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text('{"a": 1}\nnot json\n{"a": 2}\n')
    with pytest.raises(t.ResultsCorruptError, match="r.jsonl:2"):
        t.read_jsonl(path)
    path.write_text('{"a": 1}\n[1, 2]\n')
    with pytest.raises(t.ResultsCorruptError, match="not a JSON object"):
        t.read_jsonl(path)


def test_partial_final_line_is_ignored_by_readers_and_quarantined_by_the_writer(tmp_path):
    path, quarantine = tmp_path / "r.jsonl", tmp_path / "q.jsonl"
    path.write_bytes(b'{"a": 1}\n{"a": 2}\n{"a": 3, "trunc')
    result = t.read_jsonl(path)
    assert [r["a"] for r in result.records] == [1, 2] and result.partial_tail
    assert t.read_jsonl(tmp_path / "missing.jsonl").records == []
    assert t.repair_partial_tail(path, quarantine) == b'{"a": 3, "trunc'
    assert path.read_bytes() == b'{"a": 1}\n{"a": 2}\n'
    assert json.loads(quarantine.read_text())["fragment"] == '{"a": 3, "trunc'
    assert t.repair_partial_tail(path, quarantine) == b""


def test_duplicate_results_in_a_file_are_rejected(tmp_path):
    run_dir = tmp_path / "run"
    t.run_tournament(run_dir, fake_config(stable=(1, 1)), options())
    path = t.results_path(run_dir, t.STABLE)
    first_line = path.read_bytes().split(b"\n")[0]
    duplicated_key = json.loads(first_line)["key"]  # workers finish in either seat order
    with open(path, "ab") as f:
        f.write(first_line + b"\n")
    with pytest.raises(t.DuplicateResultError, match=duplicated_key):
        t.build_report(run_dir, ledger=tmp_path / "ledger.jsonl")
    with pytest.raises(t.DuplicateResultError):
        t.run_tournament(run_dir, fake_config(stable=(1, 1)), options())


def test_temporary_run_directories_are_refused(tmp_path):
    with pytest.raises(t.TournamentError, match="temporary"):
        t.run_tournament(tmp_path / "run", fake_config(), t.RunOptions(allow_temp=False))
    with pytest.raises(t.TournamentError, match="temporary"):
        t.check_persistent_dir(Path("/tmp/some-run"))
    t.check_persistent_dir(ROOT / "benchmarks" / "results" / "x")


def test_run_lock_is_exclusive_and_released(tmp_path):
    lock = t.RunLock(tmp_path).acquire()
    try:
        with pytest.raises(t.RunLockedError, match=f"pid={os.getpid()}"):
            t.RunLock(tmp_path).acquire()
        assert t.is_locked(tmp_path)
        code = "import sys; from tools import tournament as t; t.RunLock(sys.argv[1]).acquire()"
        probe = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)], cwd=ROOT, capture_output=True, text=True
        )
        assert probe.returncode != 0 and "RunLockedError" in probe.stderr
        with pytest.raises(t.RunLockedError):
            t.run_tournament(tmp_path, fake_config(), options())
    finally:
        lock.release()
    assert not t.is_locked(tmp_path)
    t.RunLock(tmp_path).acquire().release()


# --- runner --------------------------------------------------------------------------------------


def test_eligible_stable_partition_unlocks_the_holdout(tmp_path):
    run_dir = tmp_path / "run"
    set_behaviour(run_dir, bias=100)
    config = fake_config(stable=(1000, 4), holdout=(900_000, 2), policy=SMALL_POLICY)
    outcome = t.run_tournament(run_dir, config, options(max_episodes_per_worker=3))
    assert outcome.state == "finished" and outcome.stable_eligible is True
    assert outcome.completed == outcome.total == 12
    assert sorted(keys_in(run_dir) + keys_in(run_dir, t.HOLDOUT)) == sorted(
        j.key for j in t.plan_jobs(config.partitions)
    )
    events = [e["event"] for e in t.read_jsonl(run_dir / "events.jsonl").records]
    assert "worker_recycled" in events and events[-1] == "session_end"
    records = t.read_jsonl(run_dir / "events.jsonl").records
    gate_at = next(i for i, e in enumerate(records) if e["event"] == "stable_gate")
    first_holdout = min(
        i
        for i, e in enumerate(records)
        if e["event"] == "worker_ready" and i > gate_at or e["event"] == "session_end"
    )
    assert gate_at < first_holdout
    gate_file = json.loads((run_dir / "stable_gate.json").read_text())
    assert gate_file["eligible"] is True and gate_file["policy"]["name"] == "test-small"
    beat = json.loads((run_dir / "heartbeat.json").read_text())
    assert beat["state"] == "finished" and beat["completed_episodes"] == 12
    assert not t.is_locked(run_dir)
    again = t.run_tournament(run_dir, config, options())
    assert again.state == "finished" and again.new_episodes == 0


def test_ineligible_stable_partition_stops_before_the_holdout(tmp_path):
    run_dir = tmp_path / "run"
    config = fake_config(stable=(1000, 3), holdout=(900_000, 2))  # Tilla policy: 3 < 1500
    outcome = t.run_tournament(run_dir, config, options())
    assert outcome.state == "stable_ineligible" and outcome.stable_eligible is False
    assert outcome.completed == 6 and len(outcome.pending) == 4
    assert not t.results_path(run_dir, t.HOLDOUT).exists()
    assert json.loads((run_dir / "stable_gate.json").read_text())["eligible"] is False
    # Rerunning does not sneak the holdout in either.
    assert t.run_tournament(run_dir, config, options()).state == "stable_ineligible"
    assert not t.results_path(run_dir, t.HOLDOUT).exists()
    report = t.build_report(run_dir, reveal_holdout=True, ledger=tmp_path / "ledger.jsonl")
    assert report["holdout"]["state"]["revealed"] is False
    assert "cannot rescue" in report["holdout"]["state"]["reason"]
    assert {"stable_eligibility", "holdout_revealed"} <= set(report["gate"]["failed_checks"])
    assert not (tmp_path / "ledger.jsonl").exists()


def test_heartbeat_never_carries_outcomes(tmp_path):
    run_dir = tmp_path / "run"
    set_behaviour(run_dir, bias=100)
    config = fake_config(stable=(1000, 2), holdout=(900_000, 2), policy=SMALL_POLICY)
    t.run_tournament(run_dir, config, options())
    beat = (run_dir / "heartbeat.json").read_text()
    for word in ("margin", "win", "loss", "bank", "outcome", "tie"):
        assert word not in beat, word
    status = json.dumps(t.run_status(run_dir))
    for word in ("margin", "outcome", "bank"):
        assert word not in status


def test_resume_plays_only_missing_episodes_and_keeps_existing_records(tmp_path):
    run_dir = tmp_path / "run"
    config = fake_config(stable=(1000, 4))
    first = t.run_tournament(run_dir, config, options(max_new_episodes=3))
    assert first.state == "incomplete" and first.new_episodes == 3
    assert not (run_dir / "stable_gate.json").exists()  # stable incomplete: no decision yet
    before = t.results_path(run_dir, t.STABLE).read_bytes()
    second = t.run_tournament(run_dir, config, options(workers=1))
    assert second.state == "finished" and second.new_episodes == 5
    after = t.results_path(run_dir, t.STABLE).read_bytes()
    assert after.startswith(before)  # append-only
    keys = keys_in(run_dir)
    assert len(keys) == len(set(keys)) == 8


def test_resume_after_a_crash_mid_write_replays_the_torn_episode(tmp_path):
    run_dir = tmp_path / "run"
    config = fake_config(stable=(1000, 2))
    t.run_tournament(run_dir, config, options())
    path = t.results_path(run_dir, t.STABLE)
    lines = path.read_bytes().split(b"\n")[:-1]
    torn_key = json.loads(lines[-1])["key"]
    path.write_bytes(b"\n".join(lines[:-1]) + b"\n" + lines[-1][: len(lines[-1]) // 2])
    report = t.build_report(run_dir, ledger=tmp_path / "ledger.jsonl")
    assert report["stable"]["summary"]["recorded_episodes"] == 3
    assert "partial final line" in report["notes"][0]
    outcome = t.run_tournament(run_dir, config, options())
    assert outcome.state == "finished" and outcome.new_episodes == 1
    assert keys_in(run_dir).count(torn_key) == 1
    assert json.loads((run_dir / "quarantine.jsonl").read_text())["file"] == path.name


def test_worker_crash_is_retried_and_recorded_once(tmp_path):
    run_dir = tmp_path / "run"
    set_behaviour(run_dir, crash_once=["stable:1000:1"])
    outcome = t.run_tournament(run_dir, fake_config(stable=(1000, 2)), options())
    assert outcome.state == "finished" and outcome.infra_failures == 1
    records = {r["key"]: r for r in t.read_jsonl(t.results_path(run_dir, t.STABLE)).records}
    assert len(records) == 4 and records["stable:1000:1"]["attempt"] == 2
    lost = [e for e in t.read_jsonl(run_dir / "events.jsonl").records if e["event"] == "job_lost"]
    assert [e["key"] for e in lost] == ["stable:1000:1"]


def test_hung_episode_is_killed_and_left_pending_after_max_attempts(tmp_path):
    run_dir = tmp_path / "run"
    set_behaviour(run_dir, hang=["stable:1000:0"])
    outcome = t.run_tournament(
        run_dir, fake_config(stable=(1000, 2)), options(episode_timeout_s=2.5, max_attempts=2)
    )
    assert outcome.state == "incomplete" and outcome.failed == ["stable:1000:0"]
    assert "stable:1000:0" not in keys_in(run_dir) and len(keys_in(run_dir)) == 3
    report = t.build_report(run_dir, ledger=tmp_path / "ledger.jsonl")
    assert "stable_evidence_complete" in report["stable"]["eligibility"]["failed"]
    set_behaviour(run_dir)
    assert t.run_tournament(run_dir, fake_config(stable=(1000, 2)), options()).state == "finished"


def test_transient_incumbent_failure_is_replayed_and_recovered(tmp_path):
    run_dir = tmp_path / "run"
    set_behaviour(run_dir, status_once={"stable:1000:1": "incumbent_timeout"})
    assert t.run_tournament(run_dir, fake_config(stable=(1000, 2)), options()).state == "finished"
    records = {r["key"]: r for r in t.read_jsonl(t.results_path(run_dir, t.STABLE)).records}
    recovered = records["stable:1000:1"]
    assert recovered["status"] == "ok" and recovered["attempt"] == 2
    assert [a["status"] for a in recovered["prior_attempts"]] == ["incumbent_timeout"]
    report = t.build_report(run_dir, ledger=tmp_path / "ledger.jsonl")
    assert [d["key"] for d in report["recovered_episodes"]] == ["stable:1000:1"]
    assert report["stable"]["summary"]["failures"]["recovered_episodes"] == 1


def test_persistent_opponent_failures_are_recorded_and_block_promotion(tmp_path):
    run_dir = tmp_path / "run"
    set_behaviour(
        run_dir,
        status={"stable:1000:0": "incumbent_error", "stable:1001:1": "candidate_timeout"},
    )
    t.run_tournament(run_dir, fake_config(stable=(1000, 2)), options(max_attempts=3))
    records = {r["key"]: r for r in t.read_jsonl(t.results_path(run_dir, t.STABLE)).records}
    assert records["stable:1000:0"]["attempt"] == 3  # replayed twice, then recorded
    assert records["stable:1000:0"]["margin"] is None
    assert records["stable:1001:1"]["attempt"] == 1  # candidate failures are never replayed
    report = t.build_report(run_dir, ledger=tmp_path / "ledger.jsonl")
    s = report["stable"]["summary"]
    assert s["failures"]["incumbent_failures"] == 1 and s["failures"]["candidate_timeouts"] == 1
    assert s["pairs"]["completed_pairs"] == 0
    assert {"stable_incumbent_integrity", "stable_candidate_timeouts"} <= set(
        report["stable"]["eligibility"]["failed"]
    )
    assert {d["key"] for d in report["problem_episodes"]} == {"stable:1000:0", "stable:1001:1"}


def test_sigint_interrupts_cleanly_and_the_same_command_resumes(tmp_path):
    run_dir = tmp_path / "run"
    set_behaviour(run_dir, sleep=0.4)
    cmd = [
        sys.executable, "-m", "tools.tournament", "run", "--run-dir", str(run_dir),
        "--candidate", "pass", "--incumbent", "starter", "--stable", "1000:6",
        "--workers", "2", "--episode-fn", FAKE_EPISODE, "--allow-temp",
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    path = t.results_path(run_dir, t.STABLE)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and (not path.exists() or len(keys_in(run_dir)) < 2):
        time.sleep(0.05)
    proc.send_signal(signal.SIGINT)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 130, err
    assert json.loads(out.decode().strip().splitlines()[-1])["state"] == "interrupted"
    done = keys_in(run_dir)
    assert 2 <= len(done) < 12 and len(done) == len(set(done))
    assert path.read_bytes().endswith(b"\n")  # no torn line
    assert not t.is_locked(run_dir)
    assert json.loads((run_dir / "heartbeat.json").read_text())["state"] == "interrupted"
    assert t.run_status(run_dir)["liveness"].startswith("STOPPED")
    set_behaviour(run_dir)
    resumed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert resumed.returncode == 0, resumed.stderr  # no holdout planned: finished
    keys = keys_in(run_dir)
    assert len(keys) == len(set(keys)) == 12 and keys[: len(done)] == done


def test_status_reports_a_dead_runner_as_stale(tmp_path):
    run_dir = tmp_path / "run"
    t.run_tournament(run_dir, fake_config(stable=(1, 1)), options())
    beat = json.loads((run_dir / "heartbeat.json").read_text())
    beat["state"] = "running"  # what a SIGKILLed runner leaves behind
    (run_dir / "heartbeat.json").write_text(json.dumps(beat))
    status = t.run_status(run_dir)
    assert status["liveness"].startswith("STALE")
    assert status["counts"]["stable"] == {
        "recorded_episodes": 2,
        "planned_episodes": 2,
        "planned_paired_seeds": 1,
    }


def test_agent_import_failure_aborts_without_recording(tmp_path):
    root, run_dir = tmp_path / "repo", tmp_path / "run"
    root.mkdir()
    (root / "brokenagent.py").write_text(
        "raise RuntimeError('boom')\n\n\ndef agent(obs):\n    pass\n"
    )
    config = fake_config(stable=(1, 2), candidate="brokenagent:agent")
    outcome = t.run_tournament(run_dir, config, options(), root=root)
    assert outcome.state == "aborted" and outcome.completed == 0
    assert "initialisation failed" in outcome.reason
    assert keys_in(run_dir) == [] and not t.is_locked(run_dir)
    events = [e["event"] for e in t.read_jsonl(run_dir / "events.jsonl").records]
    assert "worker_fatal" in events


# --- reports and holdout isolation ---------------------------------------------------------------


def _eligible_run(tmp_path, name="run", **kw) -> Path:
    run_dir = tmp_path / name
    set_behaviour(run_dir, bias=100)
    config = fake_config(stable=(1000, 2), holdout=(900_000, 1), policy=SMALL_POLICY, **kw)
    assert t.run_tournament(run_dir, config, options()).state == "finished"
    return run_dir


def test_default_report_never_opens_holdout_results(tmp_path):
    run_dir, ledger = _eligible_run(tmp_path), tmp_path / "ledger.jsonl"
    with open(t.results_path(run_dir, t.HOLDOUT), "ab") as f:
        f.write(b"garbage\n")  # a stable-only report must not even read it
    report = t.build_report(run_dir, ledger=ledger)
    assert report["scope"] == "stable" and report["holdout"]["summary"] is None
    assert "holdout" not in report["results_files"] and not ledger.exists()
    assert "--reveal-holdout not given" in report["holdout"]["state"]["reason"]
    assert all(r["partition"] == "stable" for r in report["worst_losses"])
    assert "holdout_revealed" in report["gate"]["failed_checks"]
    with pytest.raises(t.ResultsCorruptError):
        t.build_report(run_dir, reveal_holdout=True, ledger=ledger)


def test_revealed_report_has_stable_holdout_and_combined(tmp_path):
    run_dir, ledger = _eligible_run(tmp_path), tmp_path / "ledger.jsonl"
    report = t.build_report(run_dir, reveal_holdout=True, ledger=ledger)
    assert report["scope"] == "stable+holdout"
    assert report["stable"]["summary"]["pairs"]["completed_pairs"] == 2
    assert report["holdout"]["summary"]["pairs"]["completed_pairs"] == 1
    assert report["combined"]["summary"]["pairs"]["completed_pairs"] == 3
    assert report["combined"]["summary"]["pairs"]["episodes"] == 6
    # The counts pass under the small policy, but that policy itself, the fake episode runner
    # and the missing scenario evidence still fail the gate.
    assert set(report["gate"]["failed_checks"]) == {
        "policy_not_weaker_than_tilla_gate",
        "official_episode_runner",
        "mandatory_scenarios",
    }
    md = t.render_markdown(report)
    assert "Promotion verdict: FAIL" in md and "Stable eligibility: **ELIGIBLE**" in md
    assert "MISSING_EVIDENCE" in md and "completed pairs" in md


def test_holdout_reveal_is_logged_and_reuse_by_another_candidate_is_flagged(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    first = _eligible_run(tmp_path, "run1")
    assert t.build_report(first, reveal_holdout=True, ledger=ledger)["holdout"]["state"]["fresh"]
    assert t.build_report(first, reveal_holdout=True, ledger=ledger)["holdout"]["state"]["fresh"]
    second = _eligible_run(tmp_path, "run2", candidate="random")
    reused = t.build_report(second, reveal_holdout=True, ledger=ledger)
    assert reused["holdout"]["state"]["fresh"] is False
    assert "Holdout reuse" in t.render_markdown(reused)
    assert len(t.read_jsonl(ledger).records) == 3


def test_reports_are_reproducible_and_independent_of_record_order(tmp_path):
    run_dir, ledger = tmp_path / "run", tmp_path / "ledger.jsonl"
    t.run_tournament(run_dir, fake_config(stable=(1000, 5)), options())
    first = t.build_report(run_dir, ledger=ledger)
    json_path, md_path = t.write_report(run_dir, first)
    json_bytes, md_bytes = json_path.read_bytes(), md_path.read_bytes()
    t.write_report(run_dir, t.build_report(run_dir, ledger=ledger))
    assert json_path.read_bytes() == json_bytes and md_path.read_bytes() == md_bytes
    path = t.results_path(run_dir, t.STABLE)
    lines = path.read_bytes().split(b"\n")[:-1]
    path.write_bytes(b"\n".join(reversed(lines)) + b"\n")
    shuffled = t.build_report(run_dir, ledger=ledger)
    for key in ("stable", "combined", "gate", "worst_losses", "problem_episodes"):
        assert shuffled[key] == first[key], key


def test_report_contents_for_a_fake_run(tmp_path):
    run_dir = tmp_path / "run"
    t.run_tournament(run_dir, fake_config(stable=(1000, 5)), options())
    report = t.build_report(run_dir, ledger=tmp_path / "ledger.jsonl")
    margins = [fake_margin(s, seat) for s in range(1000, 1005) for seat in (0, 1)]
    p = report["combined"]["summary"]["pairs"]
    assert p["completed_pairs"] == 5 and p["episodes"] == 10
    assert p["wins"] == sum(m > 0 for m in margins)
    assert p["median_margin"] == t._number(t.exact_median(margins))
    assert report["gate"]["verdict"] == "FAIL"
    expected = {
        "min_paired_seeds",
        "min_episodes",
        "official_episode_runner",
        "mandatory_scenarios",
        "stable_eligibility",
        "holdout_revealed",
    }
    assert expected <= set(report["gate"]["failed_checks"])
    assert report["scenarios"]["status"] == t.MISSING_EVIDENCE
    losses = report["worst_losses"]
    assert losses == sorted(losses, key=lambda d: (d["margin"], d["key"]))
    required = {"seed", "seat", "candidate_bank", "incumbent_bank", "margin", "regenerate"}
    assert all(required <= set(d) for d in losses)
    md = t.render_markdown(report)
    assert "Promotion verdict: FAIL" in md and "Wilson 95%" in md and "agent call" in md


def test_cli_status_and_report(tmp_path, capsys):
    run_dir = tmp_path / "run"
    t.run_tournament(run_dir, fake_config(stable=(1000, 1)), options())
    assert t.main_cli(["status", "--run-dir", str(run_dir)]) == 0
    assert "STOPPED (finished)" in capsys.readouterr().out
    ledger = tmp_path / "ledger.jsonl"
    code = t.main_cli(["report", "--run-dir", str(run_dir), "--ledger", str(ledger)])
    out = capsys.readouterr().out
    assert code == 1 and "promotion verdict FAIL (stable)" in out and "mandatory_scenarios" in out
    assert (run_dir / "reports" / "REPORT.stable.md").exists()


# --- the official episode function -------------------------------------------------------------


def test_shape_violation_checks_the_official_action_shape():
    good = {"farmer": ["PASS"], "hands": [["WATER"]], "market": [["HIRE"]]}
    assert t.shape_violation(good, 1) is None
    assert t.shape_violation(good, 2) == "expected 2 hand actions"
    assert t.shape_violation({**good, "farmer": []}, 1) == "farmer action malformed"
    assert t.shape_violation({**good, "market": [["HIRE"]] * 11}, 1) is not None
    assert t.shape_violation(None, 0) == "action is not a dict"


def _state(status="DONE", reward=100, money=100, step=3, action=None, hands=0):
    obs = {"step": step, "farms": [{"money": money, "hands": [[4, 4]] * hands}] * 2}
    return {"status": status, "reward": reward, "observation": obs, "action": action}


def test_inspect_episode_validates_terminal_state():
    ok_action = {"farmer": ["PASS"], "hands": [], "market": []}
    steps = [[_state("ACTIVE", 0, 0, 0), _state("ACTIVE", 0, 0, 0)]]
    steps += [[_state("ACTIVE", 0, 0, i, ok_action)] * 2 for i in (1, 2)]
    final = [_state(reward=120, action=ok_action), _state(reward=100, action=ok_action)]
    final[0]["observation"] = {"step": 3, "farms": [{"money": 120}, {"money": 100}]}
    steps.append(final)
    logs = [[{"duration": 0.004}, {"duration": 0.002}]] * 3 + [[{"duration": 1.5}, {}]]
    f = t.inspect_episode(steps, logs, seat=0, episode_steps=4, act_timeout=1.0)
    assert f["status"] == "ok" and f["terminal_valid"] and f["margin"] == 20
    assert f["timing"]["candidate_calls_over_act_timeout"] == 1
    assert f["timing"]["candidate_overage_used_s"] == pytest.approx(0.5)
    assert f["timing"]["candidate_call"]["n"] == 4 and f["timing"]["incumbent_call"]["n"] == 3

    short = t.inspect_episode(steps[:-1], logs, seat=0, episode_steps=4, act_timeout=1.0)
    assert short["status"] == "incomplete" and not short["terminal_valid"]

    timeout = [row[:] for row in steps]
    timeout[2] = [_state("TIMEOUT", None, 0, 2), timeout[2][1]]
    timeout[3] = [_state("TIMEOUT", None, 0, 3), timeout[3][1]]
    f = t.inspect_episode(timeout, logs, seat=0, episode_steps=4, act_timeout=1.0)
    assert f["status"] == "candidate_timeout"
    assert f["candidate_failure"] == {"step": 2, "status": "TIMEOUT"}
    assert f["margin"] is None and f["outcome"] is None
    f = t.inspect_episode(timeout, logs, seat=1, episode_steps=4, act_timeout=1.0)
    assert f["status"] == "incumbent_timeout" and f["margin"] is None

    bad = {"farmer": ["PASS"], "hands": [["X"]], "market": []}
    bad_shape = [row[:] for row in steps]
    bad_shape[2] = [_state("ACTIVE", 0, 0, 2, bad)] * 2
    f = t.inspect_episode(bad_shape, logs, seat=0, episode_steps=4, act_timeout=1.0)
    assert f["invalid_actions"]["shape_violations"] == 1 and f["status"] == "ok"

    mismatch = [row[:] for row in steps]
    mismatch[-1] = [{**final[0], "reward": 119}, final[1]]
    f = t.inspect_episode(mismatch, logs, seat=0, episode_steps=4, act_timeout=1.0)
    assert f["status"] == "incomplete" and "final bank" in f["validation_errors"][0]


def test_inspect_episode_prefers_raw_actions_over_recorded_ones():
    recorded = {"farmer": ["PASS"], "hands": [], "market": []}
    steps = [[_state("ACTIVE", 0, 0, i, recorded)] * 2 for i in range(3)]
    raw = [({}, 0), (recorded, 0)]
    f = t.inspect_episode(steps, [], seat=0, episode_steps=3, act_timeout=1.0, raw_actions=raw)
    assert f["invalid_actions"]["shape_violations"] == 1
    assert f["invalid_actions"]["examples"] == [{"step": 1, "problem": "farmer action malformed"}]
    g = t.inspect_episode(steps, [], seat=0, episode_steps=3, act_timeout=1.0)
    assert g["invalid_actions"]["shape_violations"] == 0
    assert g["invalid_actions"]["shape_source"] == "env-recorded"


def test_care_loss_counters_read_public_tiles():
    plant = {"kind": "PLANT", "watered_today": False, "planted_day": 3, "yield_units": 1}
    animal = {"kind": "COOP", "animal": "GOOSE"}
    weed = {"kind": "WEED"}

    def step(day, tiles):
        return [{"observation": {"day": day, "farms": [{"tiles": [tiles]}, {"tiles": [[]]}]}}]

    steps = [step(3, [plant, animal, plant]), step(4, [weed, {"kind": "COOP"}, plant])]
    assert t.care_loss_counters(steps, 0) == {
        "animals_escaped": 1,
        "crops_lost_unwatered": 1,
        "fresh_plantings_unwatered": 1,
    }
    assert t.care_loss_counters([step(5, [plant]), step(5, [weed])], 0) == {"crops_lost_decay": 1}
    assert t.care_loss_counters(steps, 1) == {}


EPISODE_OPTIONS = {"episode_steps": t.EPISODE_STEPS, "save_replays": "none", "run_dir": "."}


def test_play_episode_runs_one_official_episode():
    """One real 720-turn built-in PASS-vs-PASS episode through the official episode function."""
    fields = t.play_episode(
        {"partition": "stable", "seed": 7, "seat": 1, "key": "stable:7:1"},
        {"candidate": "pass", "incumbent": "pass"},
        EPISODE_OPTIONS,
    )
    assert fields["status"] == "ok" and fields["terminal_valid"], fields["validation_errors"]
    assert fields["steps"] == t.EPISODE_STEPS and fields["final_step"] == t.EPISODE_STEPS - 1
    assert fields["candidate_bank"] == fields["incumbent_bank"] == 3000
    assert fields["margin"] == 0 and fields["outcome"] == "tie"
    timing = fields["timing"]
    assert timing["candidate_call"]["n"] == t.EPISODE_STEPS - 1
    assert timing["turn"]["n"] == t.EPISODE_STEPS - 1
    assert timing["act_timeout_s"] == 1.0 and timing["candidate_calls_over_act_timeout"] == 0
    assert fields["invalid_actions"]["shape_violations"] == 0
    assert fields["counters"] == {"candidate": {}, "incumbent": {}}


def _empty_action_agent(obs):
    return {}


def crashing_agent(obs):
    """An opponent that crashes from step 5 (a test and smoke-run incumbent)."""
    if obs["step"] >= 5:
        raise RuntimeError("incumbent crash")
    return {"farmer": ["PASS"], "hands": [], "market": []}


def test_play_episode_checks_the_candidates_raw_actions():
    """The environment fills schema defaults into recorded actions; the shape check must see
    what the candidate actually returned."""
    fields = t.play_episode(
        {"partition": "stable", "seed": 8, "seat": 0, "key": "stable:8:0"},
        {"candidate": _empty_action_agent, "incumbent": "pass"},
        EPISODE_OPTIONS,
    )
    invalid = fields["invalid_actions"]
    assert invalid["shape_source"] == "raw" and invalid["shape_checked"] == t.EPISODE_STEPS - 1
    assert invalid["shape_violations"] == t.EPISODE_STEPS - 1
    assert invalid["examples"][0] == {"step": 1, "problem": "farmer action malformed"}


@pytest.mark.parametrize("seat", [0, 1])
def test_real_incumbent_crash_yields_no_candidate_outcome(seat):
    fields = t.play_episode(
        {"partition": "stable", "seed": 9, "seat": seat, "key": f"stable:9:{seat}"},
        {"candidate": "pass", "incumbent": crashing_agent},
        EPISODE_OPTIONS,
    )
    assert fields["status"] == "incumbent_error"
    assert fields["incumbent_failure"]["status"] == "ERROR"
    assert fields["candidate_failure"] is None
    assert fields["margin"] is None and fields["outcome"] is None
    assert t.is_replayable_failure(fields["status"])


def test_care_loss_counters_keep_the_milestone_6_harvest_correction():
    """A one-time crop harvested on the day's last turn, whose emptied tile then gets
    the end-of-day weed, is not a crop lost to missed watering (tools.harness rule)."""
    from tests.conftest import load_fixture, raw_plant
    from tools import tournament as t

    obs = load_fixture("obs_step0_no_hands.json")
    before = copy.deepcopy(obs)
    before["day"], before["hour"] = 4, 23
    before["farms"][0]["tiles"][4][4] = raw_plant("WHEAT", 0, watered_today=False, yield_units=4)
    before["farms"][0]["farmer"] = [4, 4]
    after = copy.deepcopy(obs)
    after["day"], after["hour"] = 5, 0
    after["farms"][0]["tiles"][4][4] = {"kind": "WEED"}
    harvest = {"farmer": ["HARVEST"], "hands": [], "market": []}
    steps = [[{"observation": before}, {}], [{"observation": after, "action": harvest}, {}]]
    assert t.care_loss_counters(steps, 0) == {"harvested_then_weed_spawn": 1}
    idle = [
        [{"observation": before}, {}],
        [{"observation": after, "action": {"farmer": ["PASS"]}}, {}],
    ]
    assert t.care_loss_counters(idle, 0) == {"crops_lost_unwatered": 1}
