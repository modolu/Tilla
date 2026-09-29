"""Tests for kaggriculture_bot.opponent (Milestone 6: public-state pipeline inference)
and its integration as confidence-weighted market pressure in economy.py."""

import ast
from pathlib import Path

import pytest

from kaggriculture_bot import economy
from kaggriculture_bot.constants import (
    ANIMALS,
    LAST_DAY,
    OPPONENT_CONFIDENCE_WEIGHTS,
    OPPONENT_FORECAST_DAYS,
    OPPONENT_HISTORY_LIMIT,
    OPPONENT_REALIZATION_WINDOW_TURNS,
    OPPONENT_RECENT_HARVEST_TURNS,
    TURNS_PER_DAY,
)
from kaggriculture_bot.models import (
    Confidence,
    MarketSnapshot,
    OpponentHarvestEvent,
    PipelineSource,
    Position,
)
from kaggriculture_bot.opponent import (
    estimate_animal_pipeline,
    estimate_crop_pipeline,
    lead_confidence,
    realization_share,
    update_model,
    visible_yield,
)
from kaggriculture_bot.runtime import reset_episode_memory
from kaggriculture_bot.strategy import choose_plan
from tests.conftest import load_fixture, make_state, raw_animal, raw_plant

W = OPPONENT_REALIZATION_WINDOW_TURNS


@pytest.fixture
def base():
    return load_fixture("obs_step0_no_hands.json")


def watered(crop, planted_day, yield_units=1):
    return raw_plant(
        crop, planted_day, watered_today=True, consecutive_unwatered=0, yield_units=yield_units
    )


def fed(animal, placed_day, yield_units=0):
    return raw_animal(animal, placed_day, fed_today=True, yield_units=yield_units)


def forecast_for(state):
    memory = reset_episode_memory(state.player_id)
    update_model(state, memory)
    return memory


def only(state, pos=(0, 0)):
    tile = state.opponent.tiles[pos[1]][pos[0]]
    if tile.kind.value == "PLANT":
        return estimate_crop_pipeline(Position(*pos), tile, state.day, state.step)
    return estimate_animal_pipeline(Position(*pos), tile, state.day, state.step)


# --- Observability ------------------------------------------------------------------------


def test_opponent_inventory_is_unknown_not_empty_and_stays_unknown(base):
    state = make_state(base, day=4, hour=3, opp_tiles={(0, 0): watered("WHEAT", 0, 4)})
    assert all(unit.inventory is None for unit in state.opponent.units)
    memory = forecast_for(state)
    choose_plan(state, memory)
    assert all(unit.inventory is None for unit in state.opponent.units)
    assert memory.opponent_forecast  # the visible tile produced an estimate


def test_opponent_module_never_reads_unit_inventories_or_private_state():
    """opponent.py reads the public farm and market only: no ``.inventory`` of
    units, no ``private`` block (our own), no shed/seeds attribute access."""
    source = Path(__file__).resolve().parents[1] / "kaggriculture_bot" / "opponent.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"private", "shed", "seeds", "hands", "farmer"}
            if node.attr == "units":  # unit objects (and their inventories) are never read
                assert not (
                    isinstance(node.value, ast.Name) and node.value.id in {"farm", "opponent"}
                )
                assert not (isinstance(node.value, ast.Attribute) and node.value.attr == "opponent")
            if node.attr == "inventory":
                assert isinstance(node.value, ast.Attribute) and node.value.attr == "market"


def test_forecast_ignores_our_own_private_state(base):
    tiles = {(0, 0): watered("MELON", 2)}
    a = make_state(base, day=4, hour=0, opp_tiles=tiles)
    b = make_state(base, day=4, hour=0, opp_tiles=tiles, shed={"MELON": 50}, seeds={"MELON": 9})
    assert forecast_for(a).opponent_forecast == forecast_for(b).opponent_forecast


# --- Visible crop pipeline -------------------------------------------------------------------


def test_visible_mature_crop_is_high_confidence_now(base):
    state = make_state(base, day=4, hour=3, opp_tiles={(0, 0): watered("WHEAT", 0, 4)})
    (est,) = only(state)
    assert est.source is PipelineSource.READY_CROP and est.confidence is Confidence.HIGH
    assert est.units == 4 and est.earliest_step == state.step
    assert est.latest_step == state.step + W - 1 and est.position == Position(0, 0)


def test_decaying_crop_is_visibly_neglected_low(base):
    state = make_state(base, day=6, hour=0, opp_tiles={(0, 0): watered("WHEAT", 0, 3)})
    (est,) = only(state)
    assert est.confidence is Confidence.LOW and "neglected" in est.reason


def test_visible_immature_crop_projects_its_harvest_window(base):
    # Melon: first yield 10, max-yield day 12, cap reached at age 10 with daily water.
    state = make_state(base, day=3, hour=5, opp_tiles={(0, 0): watered("MELON", 2)})
    (est,) = only(state)
    assert est.source is PipelineSource.GROWING_CROP
    assert est.confidence is Confidence.MEDIUM  # maintained, within one crop cycle
    assert est.units == 6
    assert est.earliest_step == 12 * TURNS_PER_DAY  # planted day 2 + harvest age 10
    assert est.earliest_step < est.likely_step < est.latest_step


def test_unwatered_at_risk_crop_is_downgraded(base):
    at_risk = raw_plant("MELON", 2, watered_today=False, consecutive_unwatered=1)
    state = make_state(base, day=3, hour=5, opp_tiles={(0, 0): at_risk})
    (est,) = only(state)
    assert est.confidence is Confidence.LOW and "may die" in est.reason


def test_ongoing_crop_yields_ready_units_then_each_scheduled_production(base):
    # Tomato: first yield age 8, then daily, 4 scheduled productions (ages 8-11).
    state = make_state(base, day=8, hour=2, opp_tiles={(0, 0): watered("TOMATO", 0, 1)})
    ests = only(state)
    assert [e.source for e in ests] == [PipelineSource.READY_CROP] + [
        PipelineSource.GROWING_CROP
    ] * 3
    assert ests[0].confidence is Confidence.HIGH and ests[0].units == 1
    assert [e.earliest_step for e in ests[1:]] == [d * TURNS_PER_DAY for d in (9, 10, 11)]
    assert all(e.units == 1 for e in ests[1:])


def test_crop_output_after_the_season_is_not_forecast(base):
    state = make_state(base, day=25, hour=0, opp_tiles={(0, 0): watered("MELON", 22)})
    assert only(state) == []  # harvest age 10 -> day 32 > LAST_DAY


# --- Visible animal pipeline -------------------------------------------------------------------


def test_animal_product_on_tile_and_scheduled_production(base):
    cow = ANIMALS["COW"]
    state = make_state(base, day=9, hour=0, opp_tiles={(0, 0): fed("COW", 0, yield_units=1)})
    ests = only(state)
    assert ests[0].source is PipelineSource.READY_ANIMAL and ests[0].confidence is Confidence.HIGH
    days = [e.earliest_step // TURNS_PER_DAY for e in ests[1:]]
    assert days == list(range(cow.first_yield_day + cow.interval, LAST_DAY + 1, cow.interval))
    for e in ests[1:]:
        lead = e.earliest_step // TURNS_PER_DAY - state.day
        assert e.confidence is (
            Confidence.MEDIUM if lead <= OPPONENT_FORECAST_DAYS else Confidence.LOW
        )


def test_capped_animal_product_is_visibly_uncollected_low(base):
    goose = ANIMALS["GOOSE"]
    state = make_state(base, day=12, hour=0, opp_tiles={(0, 0): fed("GOOSE", 0, goose.max_held)})
    assert only(state)[0].confidence is Confidence.LOW


def test_unfed_animal_at_risk_downgrades_scheduled_production(base):
    unfed = raw_animal("GOOSE", 0, fed_today=False, consecutive_unfed=1)
    state = make_state(base, day=5, hour=0, opp_tiles={(0, 0): unfed})
    ests = only(state)
    assert ests and all(e.confidence is Confidence.LOW for e in ests)


def test_animal_production_past_the_season_is_not_forecast(base):
    state = make_state(base, day=26, hour=0, opp_tiles={(0, 0): fed("GOOSE", 26)})
    assert only(state) == []  # first egg on day 30 > LAST_DAY


# --- Windows and confidence --------------------------------------------------------------------


def test_realization_share_boundaries():
    assert realization_share(100, 123, 99) == 0.0
    assert realization_share(100, 123, 100) == pytest.approx(1 / 24)
    assert realization_share(100, 123, 111) == pytest.approx(12 / 24)
    assert realization_share(100, 123, 123) == 1.0
    assert realization_share(100, 123, 500) == 1.0


def test_lead_confidence_boundaries_and_weights_are_ordered():
    assert lead_confidence(0) is Confidence.HIGH
    assert lead_confidence(1) is Confidence.MEDIUM
    assert lead_confidence(OPPONENT_FORECAST_DAYS) is Confidence.MEDIUM
    assert lead_confidence(OPPONENT_FORECAST_DAYS + 1) is Confidence.LOW
    w = OPPONENT_CONFIDENCE_WEIGHTS
    assert 0 < w["LOW"] < w["MEDIUM"] < w["HIGH"] <= 1


def test_day_boundary_production_starts_exactly_at_the_day(base):
    last = make_state(base, day=10, hour=23, opp_tiles={(0, 0): fed("GOOSE", 0)})
    first = make_state(base, day=11, hour=0, opp_tiles={(0, 0): fed("GOOSE", 0)})
    assert only(last)[0].earliest_step == 11 * TURNS_PER_DAY == first.step
    assert only(first)[0].earliest_step == 12 * TURNS_PER_DAY  # day 11's egg is already laid


# --- Market pressure (economy integration) -----------------------------------------------------


def test_timing_relevance_of_supply(base):
    state = make_state(base, day=3, hour=0, opp_tiles={(0, 0): watered("CARROT", 2)})
    memory = forecast_for(state)
    (est,) = memory.opponent_forecast["CARROT"]
    before = est.earliest_step - state.step - 1
    after = est.latest_step - state.step
    weight = OPPONENT_CONFIDENCE_WEIGHTS[est.confidence.value]
    assert economy.opponent_supply(state, memory, "CARROT", before) == 0.0
    assert economy.opponent_supply(state, memory, "CARROT", after) == pytest.approx(
        est.units * weight
    )


def test_no_supply_without_this_turns_forecast_or_with_influence_off(base, monkeypatch):
    state = make_state(base, day=4, hour=3, opp_tiles={(0, 0): watered("WHEAT", 0, 4)})
    assert economy.opponent_supply(state, None, "WHEAT", 24) == 0.0
    stale = reset_episode_memory(state.player_id)  # never updated this turn
    assert economy.opponent_supply(state, stale, "WHEAT", 24) == 0.0
    memory = forecast_for(state)
    assert economy.opponent_supply(state, memory, "WHEAT", 24) > 0
    monkeypatch.setattr(economy, "OPPONENT_INFLUENCE", 0.0)
    assert economy.opponent_supply(state, memory, "WHEAT", 24) == 0.0


def test_low_confidence_signal_has_bounded_impact(base):
    tiles = {(x, 0): raw_plant("CARROT", 2, consecutive_unwatered=1) for x in range(5)}
    state = make_state(base, day=3, hour=0, opp_tiles=tiles)
    memory = forecast_for(state)
    assert {e.confidence for e in memory.opponent_forecast["CARROT"]} == {Confidence.LOW}
    units = sum(e.units for e in memory.opponent_forecast["CARROT"])
    supply = economy.opponent_supply(state, memory, "CARROT", 10 * TURNS_PER_DAY)
    assert supply == pytest.approx(units * OPPONENT_CONFIDENCE_WEIGHTS["LOW"])


def test_premium_products_ignore_low_confidence_evidence(base):
    risky = {(x, 0): raw_plant("MELON", 2, consecutive_unwatered=1) for x in range(5)}
    kept = {(x, 0): watered("MELON", 2) for x in range(5)}
    horizon = 12 * TURNS_PER_DAY
    low = make_state(base, day=3, hour=0, opp_tiles=risky)
    assert economy.opponent_supply(low, forecast_for(low), "MELON", horizon) == 0.0
    med = make_state(base, day=3, hour=0, opp_tiles=kept)
    assert economy.opponent_supply(med, forecast_for(med), "MELON", horizon) > 0


def test_positive_trend_and_opponent_pipeline_are_not_summed(base, monkeypatch):
    state = make_state(base, day=4, hour=3, opp_tiles={(0, 0): watered("WHEAT", 0, 4)})
    memory = forecast_for(state)
    pressure = economy.opponent_supply(state, memory, "WHEAT", 24)
    monkeypatch.setattr(economy, "market_trend", lambda m, p: 100.0 / 24)  # +100 units over 24
    with_opp = economy.projected_inventory(state, memory, "WHEAT", 24)
    monkeypatch.setattr(economy, "OPPONENT_INFLUENCE", 0.0)
    without = economy.projected_inventory(state, memory, "WHEAT", 24)
    assert pressure < 100 and with_opp == without  # the larger view (trend) already counts it
    monkeypatch.setattr(economy, "market_trend", lambda m, p: 0.0)
    monkeypatch.setattr(economy, "OPPONENT_INFLUENCE", 1.0)
    assert economy.projected_inventory(state, memory, "WHEAT", 24) == round(
        economy.projected_inventory(state, None, "WHEAT", 24) + pressure
    )


def test_high_confidence_near_term_pipeline_changes_the_ranking(base):
    """A large, visibly maintained opponent melon field due with our harvest makes
    new melon worth less; a goose flock does the same for geese."""
    plain = make_state(base, day=2, hour=1)
    memory = forecast_for(plain)
    ours = {e.product: e for e in economy.rank_opportunities(plain, memory)}
    field = {(x, y): watered("MELON", 2) for x in range(5) for y in range(5)}
    rival = make_state(base, day=2, hour=1, opp_tiles=field)
    theirs = {e.product: e for e in economy.rank_opportunities(rival, forecast_for(rival))}
    assert theirs["MELON"].market_penalty > ours["MELON"].market_penalty
    assert theirs["MELON"].score < ours["MELON"].score
    flock = {(x, y): fed("GOOSE", 0) for x in range(5) for y in range(5)}
    geese = make_state(base, day=2, hour=1, opp_tiles=flock)
    goose = {e.product: e for e in economy.rank_opportunities(geese, forecast_for(geese))}["GOOSE"]
    assert goose.market_penalty > ours["GOOSE"].market_penalty
    assert goose.score < ours["GOOSE"].score


def test_survival_and_care_still_come_first_under_heavy_opponent_pressure(base):
    field = {(x, y): watered("MELON", 0, 6) for x in range(5) for y in range(5)}
    ours = {(1, 1): raw_plant("WHEAT", 9, watered_today=False, consecutive_unwatered=1)}
    state = make_state(base, day=10, hour=5, tiles=ours, opp_tiles=field)
    plan = choose_plan(state, forecast_for(state))
    first = plan.all_objectives()[0]
    assert first.kind.value == "WATER_CROP" and Position(1, 1) in first.targets


def test_premium_stock_is_not_dumped_on_low_confidence_evidence(base):
    risky = {(x, 0): raw_plant("STRAWBERRY", 0, consecutive_unwatered=1) for x in range(5)}
    kw = dict(day=9, hour=5, shed={"STRAWBERRY": 10})
    plain = make_state(base, **kw)
    rival = make_state(base, opp_tiles=risky, **kw)
    a = economy.market_outlook(plain, forecast_for(plain), "STRAWBERRY", 10)
    b = economy.market_outlook(rival, forecast_for(rival), "STRAWBERRY", 10)
    assert (a.sell_now, a.hold) == (b.sell_now, b.hold)


# --- Harvest detection and memory -------------------------------------------------------------


def _two_turns(base, before_tile, after_tile, day=5, gap=1, pos=(0, 0)):
    first = make_state(base, day=day, hour=3, opp_tiles={pos: before_tile})
    second = make_state(base, day=day, hour=3 + gap, opp_tiles={pos: after_tile})
    memory = reset_episode_memory(first.player_id)
    update_model(first, memory)
    update_model(second, memory)
    return second, memory


def test_one_time_crop_that_empties_is_a_harvest_but_a_weed_is_not(base):
    _, memory = _two_turns(base, watered("WHEAT", 0, 4), None)
    assert list(memory.opponent_history) == [OpponentHarvestEvent(5 * 24 + 4, "WHEAT", 4)]
    _, memory = _two_turns(base, watered("WHEAT", 0, 1), {"kind": "WEED"})
    assert not memory.opponent_history


def test_animal_yield_to_zero_is_a_harvest_but_an_escape_is_not(base):
    _, memory = _two_turns(base, fed("GOOSE", 0, 3), fed("GOOSE", 0, 0))
    assert [(e.product, e.units) for e in memory.opponent_history] == [("EGG", 3)]
    _, memory = _two_turns(base, fed("GOOSE", 0, 3), {"kind": "COOP"})
    assert not memory.opponent_history


def test_immature_crop_removal_and_non_consecutive_turns_are_not_harvests(base):
    _, memory = _two_turns(base, watered("MELON", 3), None)  # dug before first yield
    assert not memory.opponent_history
    _, memory = _two_turns(base, watered("WHEAT", 0, 4), None, gap=2)
    assert not memory.opponent_history


def test_disappearance_never_becomes_known_opponent_inventory(base):
    state, memory = _two_turns(base, fed("GOOSE", 0, 3), fed("GOOSE", 0, 0))
    assert all(unit.inventory is None for unit in state.opponent.units)
    (recent,) = [
        e for e in memory.opponent_forecast["EGG"] if e.source is PipelineSource.RECENT_HARVEST
    ]
    assert recent.confidence is Confidence.MEDIUM and recent.position is None
    assert recent.units == 3 and "not yet seen" in recent.reason


def test_recent_harvest_is_reduced_by_observed_market_inflow_and_expires(base):
    state, memory = _two_turns(base, fed("GOOSE", 0, 3), fed("GOOSE", 0, 0))
    event_step = state.step
    # Two eggs entered the market during the turn that ended at the harvest observation.
    inv = dict(state.market.inventory)
    memory.market_history.clear()
    memory.market_history.append(
        MarketSnapshot(event_step - 1, state.day, {**inv, "EGG": inv["EGG"] - 2}, ())
    )
    memory.opponent_step = -1
    update_model(state, memory)
    (recent,) = [
        e for e in memory.opponent_forecast["EGG"] if e.source is PipelineSource.RECENT_HARVEST
    ]
    assert recent.units == 1  # 3 harvested - 2 already sold: never counted twice
    late = make_state(
        base, step=event_step + OPPONENT_RECENT_HARVEST_TURNS, opp_tiles={(0, 0): fed("GOOSE", 0)}
    )
    memory.opponent_step = -1
    update_model(late, memory)
    assert all(
        e.source is not PipelineSource.RECENT_HARVEST
        for e in memory.opponent_forecast.get("EGG", ())
    )


def test_wheat_harvest_is_low_confidence_because_it_may_feed_animals(base):
    _, memory = _two_turns(base, watered("WHEAT", 0, 4), None)
    (recent,) = [
        e for e in memory.opponent_forecast["WHEAT"] if e.source is PipelineSource.RECENT_HARVEST
    ]
    assert recent.confidence is Confidence.LOW


def test_memory_stays_bounded(base):
    tiles = {(x, y): fed("GOOSE", 0, 2) for x in range(5) for y in range(5)}
    memory = reset_episode_memory(0)
    for k in range(200):
        state = make_state(
            base,
            step=100 + k,
            opp_tiles=tiles
            if k % 2 == 0
            else {(x, y): fed("GOOSE", 0, 0) for x in range(5) for y in range(5)},
        )
        update_model(state, memory)
    assert len(memory.opponent_history) == OPPONENT_HISTORY_LIMIT
    assert len(memory.opponent_visible_yield) <= 100


def test_update_is_idempotent_within_a_turn(base):
    state, memory = _two_turns(base, fed("GOOSE", 0, 3), fed("GOOSE", 0, 0))
    events, forecast = list(memory.opponent_history), memory.opponent_forecast
    update_model(state, memory)
    assert list(memory.opponent_history) == events and memory.opponent_forecast is forecast


def test_visible_yield_counts_only_harvestable_output(base):
    tiles = {
        (0, 0): watered("MELON", 3),
        (1, 0): watered("WHEAT", 0, 4),
        (2, 0): fed("GOOSE", 0, 0),
    }
    state = make_state(base, day=5, hour=0, opp_tiles=tiles)
    assert visible_yield(state.opponent, state.day) == {(1, 0): ("WHEAT", 4, False)}


# --- Determinism and terminal compatibility ----------------------------------------------------


def test_forecast_and_action_are_deterministic(base):
    import main

    tiles = {(x, y): watered("CARROT", 3) for x in range(3) for y in range(3)}
    tiles.update({(4, y): fed("COW", 0, 2) for y in range(4)})
    state = make_state(base, day=5, hour=6, opp_tiles=tiles)
    a, b = forecast_for(state), forecast_for(state)
    assert a.opponent_forecast == b.opponent_forecast
    order = [
        (e.product, e.earliest_step, e.source.value)
        for items in a.opponent_forecast.values()
        for e in items
    ]
    assert order == sorted(order)
    assert list(a.opponent_forecast) == [p for p in economy.PRODUCTS if p in a.opponent_forecast]
    raw = load_fixture("obs_midgame_p1_populated.json")
    from kaggriculture_bot.runtime import clear_all_memory

    first = main.agent(raw)
    clear_all_memory()
    assert main.agent(raw) == first


def test_liquidation_days_sell_everything_regardless_of_opponent_pressure(base):
    field = {(x, y): watered("CARROT", 26, 1) for x in range(5) for y in range(5)}
    state = make_state(base, day=LAST_DAY, hour=10, opp_tiles=field, shed={"CARROT": 12})
    outlook = economy.market_outlook(state, forecast_for(state), "CARROT", 12)
    assert outlook.sell_now == 12 and outlook.hold == 0
