"""Tests for kaggriculture_bot.tasks: Milestone 4 multi-unit job planner.

Job generation, deterministic capability-aware assignment, consumable
reservation, movement-to-task persistence, preemption, day-boundary cleanup,
deadline handling and single-unit execution.
"""

from kaggriculture_bot.actions import build_action
from kaggriculture_bot.constants import PROMOTION_SLACK_TURNS
from kaggriculture_bot.models import (
    PASS_OBJECTIVE,
    PRIORITY_DAILY_WORK,
    PRIORITY_ECONOMIC,
    PRIORITY_IDLE,
    PRIORITY_SURVIVAL,
    Job,
    JobKind,
    Objective,
    ObjectiveKind,
    Position,
    StrategicPlan,
    UnitAction,
    UnitOp,
)
from kaggriculture_bot.runtime import reset_episode_memory
from kaggriculture_bot.tasks import assign_jobs, assign_units, farmer_job, generate_jobs, unit_job
from kaggriculture_bot.validator import validate_or_fallback
from tests.conftest import make_state, raw_animal, raw_plant

P = Position


def water(*targets, priority=PRIORITY_DAILY_WORK):
    return Objective(ObjectiveKind.WATER_CROP, tuple(targets), priority=priority)


def feed(*targets, priority=PRIORITY_DAILY_WORK):
    return Objective(ObjectiveKind.FEED_ANIMAL, tuple(targets), "WHEAT", 1, None, priority)


def plan(*objectives):
    return StrategicPlan(objectives=tuple(objectives))


def memory_for(state):
    return reset_episode_memory(state.player_id)


# --- Job generation --------------------------------------------------------------------------


def test_jobs_are_one_per_target_with_stable_keys_and_plan_order(obs_no_hands):
    state = make_state(obs_no_hands)
    jobs = generate_jobs(state, plan(water(P(1, 1), P(2, 2)), feed(P(3, 3))))
    assert [j.kind for j in jobs] == [JobKind.WATER, JobKind.WATER, JobKind.FEED]
    assert [j.target for j in jobs] == [P(1, 1), P(2, 2), P(3, 3)]
    assert jobs[0].key == ("WATER", 1, 1, None, None)
    assert jobs[2].key == ("FEED", 3, 3, "WHEAT", None)
    assert jobs[2].requires == "WHEAT"  # capability the assignment must satisfy
    assert generate_jobs(state, plan(water(P(1, 1), P(2, 2)), feed(P(3, 3)))) == jobs


def test_duplicate_targets_keep_the_higher_priority_job(obs_no_hands):
    state = make_state(obs_no_hands)
    jobs = generate_jobs(
        state, plan(water(P(1, 1), priority=PRIORITY_SURVIVAL), water(P(1, 1), P(2, 2)))
    )
    assert [(j.target, j.priority) for j in jobs] == [
        (P(1, 1), PRIORITY_SURVIVAL),
        (P(2, 2), PRIORITY_DAILY_WORK),
    ]


def test_place_animal_requires_the_carried_animal(obs_no_hands):
    state = make_state(obs_no_hands)
    place = Objective(ObjectiveKind.PLACE_ANIMAL, (P(2, 2),), "GOOSE", 1, None, PRIORITY_DAILY_WORK)
    (job,) = generate_jobs(state, plan(place))
    assert job.kind is JobKind.PLACE and job.requires == "GOOSE" and job.item == "GOOSE"


def test_two_animals_never_get_jobs_on_the_same_structure_tile(obs_no_hands):
    state = make_state(obs_no_hands, tiles={(3, 4): {"kind": "PASTURE"}})
    cow = Objective(ObjectiveKind.PLACE_ANIMAL, (P(3, 4),), "COW", 1, None, PRIORITY_DAILY_WORK)
    sheep = Objective(ObjectiveKind.PLACE_ANIMAL, (P(3, 4),), "SHEEP", 1, None, PRIORITY_DAILY_WORK)
    jobs = generate_jobs(state, plan(cow, sheep))
    assert [(j.kind, j.item) for j in jobs] == [(JobKind.PLACE, "COW")]
    coop = Objective(ObjectiveKind.BUILD_STRUCTURE, (P(4, 4),), "COOP")
    pasture = Objective(ObjectiveKind.BUILD_STRUCTURE, (P(4, 4),), "PASTURE")
    assert [j.item for j in generate_jobs(state, plan(coop, pasture))] == ["COOP"]


def test_deliver_creates_one_pinned_job_per_carrying_unit(obs_two_hands):
    state = make_state(
        obs_two_hands,
        farmer=(2, 2),
        hands=[(1, 1), (3, 3)],
        inventory={"WHEAT": 2},
        hand_inventories={1: {"EGG": 1}},  # second hand carries; first carries nothing
    )
    deliver = Objective(ObjectiveKind.DELIVER, (P(4, 4),), priority=PRIORITY_IDLE)
    jobs = generate_jobs(state, plan(deliver))
    assert [(j.kind, j.unit) for j in jobs] == [(JobKind.DELIVER, 0), (JobKind.DELIVER, 2)]


def test_pass_and_empty_objectives_produce_no_jobs(obs_no_hands):
    state = make_state(obs_no_hands)
    assert generate_jobs(state, plan(PASS_OBJECTIVE)) == []
    assert generate_jobs(state, plan(Objective(ObjectiveKind.HARVEST, ()))) == []
    assert generate_jobs(state, StrategicPlan()) == []


# --- Assignment ------------------------------------------------------------------------------


def test_nearest_unit_takes_each_job_and_no_job_is_shared(obs_two_hands):
    state = make_state(obs_two_hands, farmer=(4, 4), hands=[(0, 0), (4, 0)])
    jobs = generate_jobs(state, plan(water(P(0, 1), P(4, 1), P(4, 3))))
    assigned = assign_units(state, jobs, memory_for(state))
    assert {idx: job.target for idx, job in assigned.items()} == {
        0: P(4, 3),
        1: P(0, 1),
        2: P(4, 1),
    }
    assert len({job.key for job in assigned.values()}) == 3


def test_equal_distance_ties_break_on_job_order_then_unit_index(obs_two_hands):
    state = make_state(obs_two_hands, farmer=(2, 2), hands=[(2, 2), (2, 2)])
    jobs = generate_jobs(state, plan(water(P(2, 1), P(1, 2), P(3, 2))))
    assigned = assign_units(state, jobs, memory_for(state))
    assert assigned[0].target == P(2, 1)  # first job -> lowest unit index
    assert assigned[1].target == P(1, 2)
    assert assigned[2].target == P(3, 2)


def test_assignment_is_deterministic(obs_two_hands):
    state = make_state(obs_two_hands, farmer=(1, 3), hands=[(3, 1), (0, 4)])
    jobs = generate_jobs(state, plan(water(P(0, 0), P(2, 2), P(4, 0), P(4, 4)), feed(P(1, 1))))
    first = assign_units(state, jobs, memory_for(state))
    for _ in range(5):
        assert assign_units(state, jobs, memory_for(state)) == first


def test_higher_priority_jobs_are_staffed_first_even_when_farther(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(4, 4))
    jobs = generate_jobs(state, plan(water(P(0, 0), priority=PRIORITY_SURVIVAL), water(P(4, 3))))
    assigned = assign_units(state, jobs, memory_for(state))
    assert assigned[0].target == P(0, 0)


def test_within_a_tier_the_cheapest_pair_is_fixed_first(obs_no_hands):
    """A lone farmer adjacent to an at-risk plant waters it before a same-tier
    feed job that needs a shed detour, whatever the objective order."""
    state = make_state(obs_no_hands, farmer=(4, 3), shed={"WHEAT": 3})
    jobs = generate_jobs(
        state,
        plan(feed(P(1, 1), priority=PRIORITY_SURVIVAL), water(P(4, 2), priority=PRIORITY_SURVIVAL)),
    )
    assigned = assign_units(state, jobs, memory_for(state))
    assert assigned[0].kind is JobKind.WATER


def test_extra_units_idle_when_there_is_no_work(obs_two_hands):
    state = make_state(obs_two_hands)
    assert assign_units(state, [], memory_for(state)) == {}
    turn = assign_jobs(state, StrategicPlan(), memory_for(state))
    assert turn.farmer == UnitAction(UnitOp.PASS)
    assert turn.hands == (UnitAction(UnitOp.PASS), UnitAction(UnitOp.PASS))


# --- Capability and consumables -------------------------------------------------------------


def test_unit_already_carrying_wheat_is_preferred_for_feeding(obs_two_hands):
    """The farmer is closer to the animal, but the hand carries wheat: the
    farmer would first need a shed detour, which costs more turns."""
    state = make_state(
        obs_two_hands,
        farmer=(1, 2),
        hands=[(1, 4), (4, 4)],
        shed={"WHEAT": 5},
        hand_inventories={0: {"WHEAT": 1}},
    )
    jobs = generate_jobs(state, plan(feed(P(1, 1))))
    assigned = assign_units(state, jobs, memory_for(state))
    assert list(assigned) == [1]


def test_feed_without_any_wheat_is_infeasible(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(1, 2))
    jobs = generate_jobs(state, plan(feed(P(1, 1))))
    assert assign_units(state, jobs, memory_for(state)) == {}


def test_shed_wheat_is_reserved_so_two_units_never_count_the_same_unit(obs_two_hands):
    state = make_state(obs_two_hands, farmer=(4, 4), hands=[(4, 4), (4, 4)], shed={"WHEAT": 1})
    jobs = generate_jobs(state, plan(feed(P(1, 1), P(3, 3))))
    assigned = assign_units(state, jobs, memory_for(state))
    assert len(assigned) == 1
    two = make_state(obs_two_hands, farmer=(4, 4), hands=[(4, 4), (4, 4)], shed={"WHEAT": 2})
    assert len(assign_units(two, jobs, memory_for(two))) == 2


def test_seeds_are_reserved_per_planting_job(obs_two_hands):
    state = make_state(obs_two_hands, farmer=(4, 4), hands=[(3, 4), (4, 3)], seeds={"WHEAT": 1})
    plant = Objective(ObjectiveKind.PLANT, (P(4, 4), P(3, 4), P(4, 3)), "WHEAT", None, 22)
    jobs = generate_jobs(state, plan(plant))
    assigned = assign_units(state, jobs, memory_for(state))
    assert len(assigned) == 1


# --- Persistence, preemption, day boundary ------------------------------------------------


def test_unit_stays_on_its_job_when_another_unit_becomes_nearer(obs_two_hands):
    state = make_state(obs_two_hands, farmer=(4, 4), hands=[(0, 4), (4, 0)])
    memory = memory_for(state)
    jobs = generate_jobs(state, plan(water(P(0, 0))))
    first = assign_units(state, jobs, memory)
    assert list(first) == [1]  # the hand at (0,4) is nearest
    assert memory.unit_assignments == first and memory.assignment_day == state.day
    # Next turn: that hand advanced one step, but the other hand is now equally close.
    later = make_state(obs_two_hands, farmer=(4, 4), hands=[(0, 3), (0, 3)], hour=1)
    jobs = generate_jobs(later, plan(water(P(0, 0))))
    assert list(assign_units(later, jobs, memory)) == [1]


def test_survival_work_preempts_a_unit_walking_to_economic_work(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(2, 2))
    memory = memory_for(state)
    fertilize = Objective(
        ObjectiveKind.FERTILIZE_CROP, (P(0, 0),), "FERTILIZER", 1, None, PRIORITY_ECONOMIC
    )
    carrying = make_state(obs_no_hands, farmer=(2, 2), inventory={"FERTILIZER": 1})
    assert assign_units(carrying, generate_jobs(carrying, plan(fertilize)), memory)[0].kind is (
        JobKind.FERTILIZE
    )
    urgent = make_state(obs_no_hands, farmer=(2, 2), inventory={"FERTILIZER": 1}, hour=1)
    jobs = generate_jobs(urgent, plan(water(P(2, 3), priority=PRIORITY_SURVIVAL), fertilize))
    assigned = assign_units(urgent, jobs, memory)
    assert assigned[0].kind is JobKind.WATER


def test_completed_or_vanished_jobs_are_dropped_from_memory(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(4, 4))
    memory = memory_for(state)
    assign_units(state, generate_jobs(state, plan(water(P(4, 3)))), memory)
    assert 0 in memory.unit_assignments
    assign_units(state, [], memory)  # the plant was watered: no job any more
    assert memory.unit_assignments == {}


def test_assignments_are_cleared_at_the_day_boundary(obs_two_hands):
    state = make_state(obs_two_hands, day=3, hour=23, farmer=(4, 4), hands=[(0, 4), (4, 0)])
    memory = memory_for(state)
    assign_units(state, generate_jobs(state, plan(water(P(0, 0), P(4, 4)))), memory)
    assert memory.assignment_day == 3 and memory.unit_assignments
    # Hands vanish overnight and the farmer respawns; nothing carries over.
    dawn = make_state(obs_two_hands, day=4, hour=0, farmer=(4, 4), hands=[])
    assigned = assign_units(dawn, generate_jobs(dawn, plan(water(P(4, 3)))), memory)
    assert memory.assignment_day == 4 and set(memory.unit_assignments) == {0}
    assert assigned[0].target == P(4, 3)


def test_stale_hand_indices_never_break_assignment(obs_two_hands):
    state = make_state(obs_two_hands, farmer=(4, 4), hands=[(0, 4), (4, 0)])
    memory = memory_for(state)
    assign_units(state, generate_jobs(state, plan(water(P(0, 0), P(4, 1), P(4, 3)))), memory)
    fewer = make_state(obs_two_hands, farmer=(4, 4), hands=[(0, 4)], hour=1)
    assigned = assign_units(fewer, generate_jobs(fewer, plan(water(P(0, 0), P(4, 3)))), memory)
    assert set(assigned) <= {0, 1}


# --- Deadlines --------------------------------------------------------------------------------


def test_jobs_that_cannot_be_reached_today_are_not_assigned(obs_no_hands):
    state = make_state(obs_no_hands, hour=21, farmer=(4, 4))
    jobs = generate_jobs(state, plan(water(P(0, 0)), water(P(4, 2))))  # 8 and 2 moves away
    assigned = assign_units(state, jobs, memory_for(state))
    assert assigned[0].target == P(4, 2)


def test_plant_deadline_hour_bounds_the_route(obs_no_hands):
    state = make_state(obs_no_hands, hour=21, farmer=(4, 4), seeds={"WHEAT": 2})
    plant = Objective(ObjectiveKind.PLANT, (P(4, 2), P(4, 3)), "WHEAT", None, 22)
    assigned = assign_units(state, generate_jobs(state, plan(plant)), memory_for(state))
    assert assigned[0].target == P(4, 3)  # (4,2) would be planted at hour 23


def test_job_that_must_start_now_is_promoted_when_care_stays_covered(obs_two_hands):
    """A far fertilize job is normally last; when waiting one more turn would
    make it impossible today and the other units cover the care backlog, one
    unit starts it now instead of idling later."""
    fertilize = Objective(
        ObjectiveKind.FERTILIZE_CROP, (P(0, 0),), "FERTILIZER", 1, None, PRIORITY_ECONOMIC
    )
    care = water(P(4, 3), P(3, 4), P(4, 2))
    # Farmer at (4,4) carrying fertilizer: 8 moves to (0,0). Deadline 23.
    hour = 23 - 8 - PROMOTION_SLACK_TURNS
    state = make_state(
        obs_two_hands,
        hour=hour,
        farmer=(4, 4),
        hands=[(4, 4), (4, 4)],
        inventory={"FERTILIZER": 1},
    )
    assigned = assign_units(state, generate_jobs(state, plan(care, fertilize)), memory_for(state))
    assert assigned[0].kind is JobKind.FERTILIZE
    # Earlier in the day there is slack: care first, the fertilize job waits.
    early = make_state(
        obs_two_hands, hour=hour - 3, farmer=(4, 4), hands=[(4, 4), (4, 4)],
        inventory={"FERTILIZER": 1},
    )  # fmt: skip
    assigned = assign_units(early, generate_jobs(early, plan(care, fertilize)), memory_for(early))
    assert assigned[0].kind is JobKind.WATER
    # Without other units to cover the care backlog it is not promoted.
    alone = make_state(obs_no_hands_like(obs_two_hands), hour=hour, farmer=(4, 4),
                       inventory={"FERTILIZER": 1})  # fmt: skip
    assigned = assign_units(alone, generate_jobs(alone, plan(care, fertilize)), memory_for(alone))
    assert assigned[0].kind is JobKind.WATER


def obs_no_hands_like(obs):
    import copy

    out = copy.deepcopy(obs)
    out["farms"][out["player"]]["hands"] = []
    out["private"]["inventories"] = out["private"]["inventories"][:1]
    return out


# --- Execution --------------------------------------------------------------------------------


def test_objective_on_current_tile_becomes_direct_action(obs_no_hands):
    state = make_state(obs_no_hands, tiles={(4, 4): raw_plant()}, inventory={"WHEAT": 1})
    assert farmer_job(state, water(P(4, 4))) == UnitAction(UnitOp.WATER)
    assert farmer_job(state, Objective(ObjectiveKind.HARVEST, (P(4, 4),))) == UnitAction(
        UnitOp.HARVEST
    )
    assert farmer_job(state, feed(P(4, 4))) == UnitAction(UnitOp.FEED)
    assert farmer_job(state, Objective(ObjectiveKind.DELIVER, (P(4, 4),))) == UnitAction(
        UnitOp.DROP
    )
    plant = Objective(ObjectiveKind.PLANT, (P(4, 4),), "WHEAT", None, 22)
    assert farmer_job(make_state(obs_no_hands, seeds={"WHEAT": 1}), plant) == UnitAction(
        UnitOp.PLANT, "WHEAT"
    )
    build = Objective(ObjectiveKind.BUILD_STRUCTURE, (P(4, 4),), "COOP")
    assert farmer_job(make_state(obs_no_hands), build) == UnitAction(UnitOp.BUILD_COOP)


def test_required_item_is_fetched_at_the_nearest_usable_access_tile(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(4, 4), shed={"WHEAT": 3})
    assert farmer_job(state, feed(P(1, 1))) == UnitAction(UnitOp.PICKUP, "WHEAT", 1)
    away = make_state(obs_no_hands, farmer=(2, 4), shed={"WHEAT": 3})
    assert farmer_job(away, feed(P(1, 1))) == UnitAction(UnitOp.EAST)  # walk to (4,4) first
    fed = make_state(obs_no_hands, farmer=(4, 4), inventory={"WHEAT": 1})
    assert farmer_job(fed, feed(P(1, 1))) == UnitAction(UnitOp.NORTH)  # already carrying


def test_place_animal_fetches_it_from_the_shed_first(obs_no_hands):
    state = make_state(
        obs_no_hands, farmer=(4, 4), shed={"GOOSE": 1}, tiles={(2, 2): {"kind": "COOP"}}
    )
    place = Objective(ObjectiveKind.PLACE_ANIMAL, (P(2, 2),), "GOOSE", 1, None, PRIORITY_DAILY_WORK)
    assert farmer_job(state, place) == UnitAction(UnitOp.PICKUP, "GOOSE", 1)
    carried = make_state(
        obs_no_hands, farmer=(2, 2), inventory={"GOOSE": 1}, tiles={(2, 2): {"kind": "COOP"}}
    )
    assert farmer_job(carried, place) == UnitAction(UnitOp.PLACE, "GOOSE")


def test_distant_objective_becomes_one_deterministic_step(obs_no_hands):
    state = make_state(obs_no_hands)
    assert farmer_job(state, water(P(4, 2))) == UnitAction(UnitOp.NORTH)
    assert farmer_job(state, water(P(2, 2))) == UnitAction(UnitOp.NORTH)
    assert farmer_job(state, water(P(2, 4))) == UnitAction(UnitOp.WEST)


def test_nearest_of_several_targets_is_chosen(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(2, 2))
    assert farmer_job(state, water(P(4, 4), P(2, 3), P(0, 0))) == UnitAction(UnitOp.SOUTH)


def test_locked_tiles_are_not_entered_but_a_locked_start_is_left(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(5, 4), inventory={"WHEAT": 1})  # spawned on NE tile
    assert farmer_job(state, Objective(ObjectiveKind.DELIVER, (P(4, 4),))) == UnitAction(
        UnitOp.WEST
    )
    state = make_state(obs_no_hands, farmer=(4, 4))
    assert farmer_job(state, water(P(5, 4))) == UnitAction(UnitOp.PASS)


def test_unit_never_walks_off_board(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(0, 0))
    assert farmer_job(state, water(P(0, 3))) == UnitAction(UnitOp.SOUTH)
    assert farmer_job(state, water(P(3, 0))) == UnitAction(UnitOp.EAST)


def test_unreachable_or_empty_targets_degrade_to_pass(obs_no_hands):
    state = make_state(obs_no_hands)
    assert farmer_job(state, Objective(ObjectiveKind.HARVEST, (P(9, 9),))) == UnitAction(
        UnitOp.PASS
    )
    assert farmer_job(state, Objective(ObjectiveKind.HARVEST, ())) == UnitAction(UnitOp.PASS)
    assert farmer_job(state, PASS_OBJECTIVE) == UnitAction(UnitOp.PASS)
    assert farmer_job(state, Objective(ObjectiveKind.REPOSITION, (P(4, 4),))) == UnitAction(
        UnitOp.PASS
    )


def test_plant_after_deadline_is_not_executed(obs_no_hands):
    state = make_state(obs_no_hands, hour=23, seeds={"WHEAT": 1})
    plant = Objective(ObjectiveKind.PLANT, (P(4, 4),), "WHEAT", None, 22)
    assert farmer_job(state, plant) == UnitAction(UnitOp.PASS)
    job = Job(JobKind.PLANT, P(4, 4), PRIORITY_DAILY_WORK, item="WHEAT", deadline_hour=22)
    assert unit_job(state, state.me.farmer, job) == UnitAction(UnitOp.PASS)


def test_hands_work_and_output_stays_legal(obs_two_hands):
    state = make_state(
        obs_two_hands,
        farmer=(4, 4),
        hands=[(4, 3), (3, 4)],
        tiles={(4, 4): raw_plant(), (4, 3): raw_plant(), (3, 4): raw_animal()},
        hand_inventories={1: {"WHEAT": 1}},
    )
    turn = assign_jobs(state, plan(water(P(4, 4), P(4, 3)), feed(P(3, 4))), memory_for(state))
    assert turn.farmer == UnitAction(UnitOp.WATER)
    assert turn.hands == (UnitAction(UnitOp.WATER), UnitAction(UnitOp.FEED))
    action = build_action(turn)
    assert action["hands"] == [["WATER"], ["FEED"]]
    assert validate_or_fallback(action, 2) == action
