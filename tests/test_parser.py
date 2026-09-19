"""Tests for kaggriculture_bot.parser (Milestone 1: observation -> GameState normalization)."""

import copy
import dataclasses
import json

import pytest

from kaggriculture_bot.models import (
    EMPTY_TILE,
    LOCKED_TILE,
    WEED_TILE,
    AnimalState,
    GameState,
    PlantTile,
    Position,
    StructureTile,
    TileKind,
)
from kaggriculture_bot.parser import ObservationError, as_int, parse_observation, parse_tile
from tests.conftest import OFFICIAL_FIXTURES, load_fixture

# --- Every official fixture parses ------------------------------------------------------


@pytest.mark.parametrize("name", OFFICIAL_FIXTURES)
def test_every_official_fixture_parses_to_consistent_state(name):
    obs = load_fixture(name)
    state = parse_observation(obs)
    assert isinstance(state, GameState)
    assert state.player_id == obs["player"]
    assert (state.step, state.day, state.hour) == (obs["step"], obs["day"], obs["hour"])
    assert state.me.player_id == obs["player"]
    assert state.opponent.player_id == 1 - obs["player"]
    for farm in (state.me, state.opponent):
        assert farm.board_size == 10
        assert all(len(row) == 10 for row in farm.tiles)
        assert isinstance(farm.money, int)
        assert farm.units[0].index == 0
        assert [u.index for u in farm.hands] == list(range(1, len(farm.units)))
    # Visibility contract: our inventories are observable dicts; the opponent's are None.
    assert all(isinstance(u.inventory, dict) for u in state.me.units)
    assert all(u.inventory is None for u in state.opponent.units)
    assert set(state.market.inventory) == set(state.market.prices)
    assert all(isinstance(v, int) for v in state.market.inventory.values())
    assert all(isinstance(v, int) for v in state.market.prices.values())


def test_all_fixtures_are_real_official_observations_without_hidden_state():
    for name in OFFICIAL_FIXTURES:
        obs = load_fixture(name)
        assert set(obs) == {"player", "step", "day", "hour", "farms", "private", "market", "town"}
        assert len(obs["farms"]) == 2
        assert set(obs["private"]) == {"shed", "seeds", "inventories"}


# --- Seat mapping ---------------------------------------------------------------------


def test_seat0_maps_me_and_opponent(obs_two_hands):
    state = parse_observation(obs_two_hands)
    assert state.player_id == 0
    assert state.me.player_id == 0 and state.opponent.player_id == 1
    assert len(state.me.hands) == 2
    assert len(state.opponent.hands) == 0
    assert state.me.hires_today == 2 and state.opponent.hires_today == 0


def test_seat1_maps_me_and_opponent(obs_seat1_step1):
    state = parse_observation(obs_seat1_step1)
    assert state.player_id == 1
    assert state.me.player_id == 1 and state.opponent.player_id == 0
    assert len(state.me.hands) == 0
    assert len(state.opponent.hands) == 2
    assert state.me.money == 3000 and state.opponent.money == 2998


def test_seat1_midgame_maps_private_state_to_own_farm(obs_midgame_p1):
    state = parse_observation(obs_midgame_p1)
    assert state.player_id == 1
    assert state.me.money == 1262 and state.opponent.money == 3015
    assert state.me.unlocked_quadrants == frozenset({"NW", "NE"})
    assert state.opponent.unlocked_quadrants == frozenset({"NW"})


# --- Integer normalization ----------------------------------------------------------------


def test_official_float_money_is_normalized_to_int(obs_midgame_p1):
    assert isinstance(obs_midgame_p1["farms"][1]["money"], float)  # 1262.0 as emitted
    state = parse_observation(obs_midgame_p1)
    assert state.me.money == 1262 and type(state.me.money) is int
    assert type(state.opponent.money) is int


@pytest.mark.parametrize("value,expected", [(3000.0, 3000), (4.0, 4), (0, 0), (-1.0, -1), (7, 7)])
def test_as_int_accepts_integral_numbers(value, expected):
    result = as_int(value, "x")
    assert result == expected and type(result) is int


@pytest.mark.parametrize("value", [3000.5, 0.1, True, False, "3000", None, [3000]])
def test_as_int_rejects_non_integral_or_non_numeric(value):
    with pytest.raises(ObservationError):
        as_int(value, "x")


def test_non_integral_money_is_rejected_not_truncated(deep):
    deep["farms"][0]["money"] = 3000.5
    with pytest.raises(ObservationError, match="money"):
        parse_observation(deep)


def test_non_integral_count_in_private_is_rejected(deep):
    deep["private"]["shed"]["WHEAT"] = 2.5
    with pytest.raises(ObservationError, match="shed"):
        parse_observation(deep)


# --- Units ----------------------------------------------------------------------------------


def test_units_are_farmer_then_hands_in_official_order(obs_midgame_p1):
    raw = obs_midgame_p1["farms"][1]
    state = parse_observation(obs_midgame_p1)
    assert state.me.farmer.index == 0
    assert state.me.farmer.position == Position(*raw["farmer"])
    assert [h.index for h in state.me.hands] == [1, 2]
    assert [h.position for h in state.me.hands] == [Position(*p) for p in raw["hands"]]
    assert state.me.units == (state.me.farmer, *state.me.hands)


def test_our_unit_inventories_come_from_private_inventories(obs_midgame_p1):
    state = parse_observation(obs_midgame_p1)
    assert state.me.farmer.inventory == {"WHEAT": 5}
    assert [h.inventory for h in state.me.hands] == [{}, {}]


def test_opponent_inventories_are_unobservable_not_empty(obs_seat1_step1):
    state = parse_observation(obs_seat1_step1)
    assert all(unit.inventory is None for unit in state.opponent.units)
    assert state.me.farmer.inventory == {}


def test_missing_trailing_inventory_entry_is_empty_like_the_environment(obs_two_hands):
    """kaggriculture.py _farmer_inventory grows the list on demand; a missing entry is empty."""
    obs = copy.deepcopy(obs_two_hands)
    obs["private"]["inventories"] = [{"WHEAT": 1}]
    state = parse_observation(obs)
    assert state.me.farmer.inventory == {"WHEAT": 1}
    assert [h.inventory for h in state.me.hands] == [{}, {}]


# --- Private, market, town ----------------------------------------------------------


def test_private_shed_and_seeds_parse(obs_midgame_p1):
    state = parse_observation(obs_midgame_p1)
    assert state.private.shed["WHEAT"] == 3 and state.private.shed["FERTILIZER"] == 4
    assert state.private.seeds == {
        "CARROT": 1,
        "MELON": 0,
        "STRAWBERRY": 0,
        "TOMATO": 0,
        "WHEAT": 2,
    }


def test_market_inventory_and_prices_parse(obs_midgame_p1):
    state = parse_observation(obs_midgame_p1)
    assert state.market.inventory["WOOL"] == 9963 and state.market.prices["WOOL"] == 231
    assert state.market.inventory["FERTILIZER"] == 9999 and state.market.prices["FERTILIZER"] == 100
    assert set(state.market.inventory) == {
        "WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER"
    }  # fmt: skip


def test_town_unlocked_shops_preserve_order_and_multiplicity(obs_midgame_p1, obs_final, deep):
    assert parse_observation(obs_midgame_p1).town.unlocked_shops == ("YARN_STORE",)
    final = parse_observation(obs_final).town.unlocked_shops
    assert len(final) == 8 and len(set(final)) == 8
    assert final == tuple(obs_final["town"]["unlocked_shops"])
    # The official environment never emits duplicates (see test_rules_conformance),
    # but the parser must not collapse them if it ever did.
    deep["town"]["unlocked_shops"] = ["BAKERY", "BAKERY"]
    assert parse_observation(deep).town.unlocked_shops == ("BAKERY", "BAKERY")


# --- Tiles ----------------------------------------------------------------------------------


def test_every_official_tile_variant_is_preserved(obs_midgame_p1):
    state = parse_observation(obs_midgame_p1)
    tiles = state.me.tiles  # tiles[y][x]
    assert tiles[4][0] == StructureTile(kind=TileKind.PASTURE, animal=None)
    assert tiles[4][1] == StructureTile(
        kind=TileKind.COOP,
        animal=AnimalState(
            animal="GOOSE",
            placed_day=0,
            yield_units=4,
            consecutive_unfed=0,
            fed_today=False,
            cared_today=False,
            fertilizer_available=True,
            pending_care_bonus=1,
        ),
    )
    assert tiles[4][2] == PlantTile(
        crop="TOMATO",
        planted_day=0,
        watered_today=False,
        consecutive_unwatered=0,
        yield_units=0,
        max_lifespan_step=-1,
        fertilized_until_day=-1,
    )
    assert tiles[4][3] is WEED_TILE
    assert tiles[4][4] is EMPTY_TILE
    assert tiles[9][9] is LOCKED_TILE
    counts = {}
    for row in tiles:
        for tile in row:
            counts[tile.kind] = counts.get(tile.kind, 0) + 1
    assert counts == {
        TileKind.EMPTY: 46,
        TileKind.LOCKED: 50,
        TileKind.PASTURE: 1,
        TileKind.COOP: 1,
        TileKind.PLANT: 1,
        TileKind.WEED: 1,
    }


def test_opponent_public_tiles_are_parsed(obs_midgame_p1):
    state = parse_observation(obs_midgame_p1)
    carrot = state.opponent.tiles[4][4]
    assert isinstance(carrot, PlantTile)
    assert carrot.crop == "CARROT" and carrot.planted_day == 3 and carrot.watered_today is True
    assert carrot.yield_units == 2 and carrot.max_lifespan_step == 168


def test_board_rows_are_y_major_and_locked_quadrants_match_unlocked_list(obs_no_hands):
    state = parse_observation(obs_no_hands)
    for y in range(10):
        for x in range(10):
            tile = state.me.tiles[y][x]
            expected = EMPTY_TILE if (x < 5 and y < 5) else LOCKED_TILE
            assert tile is expected, (x, y)


def test_parse_tile_rejects_unknown_kind_and_shape():
    with pytest.raises(ObservationError):
        parse_tile({"kind": "BARN"}, "t")
    with pytest.raises(ObservationError):
        parse_tile("PLANT", "t")
    with pytest.raises(ObservationError):
        parse_tile({"kind": "PLANT", "crop": "WHEAT"}, "t")  # missing required fields


def test_empty_structure_with_explicit_animal_none_is_empty():
    """README documents ``"animal": None`` for empty structures; source omits the key."""
    assert parse_tile({"kind": "COOP", "animal": None}, "t") == StructureTile(TileKind.COOP, None)
    assert parse_tile({"kind": "COOP"}, "t") == StructureTile(TileKind.COOP, None)


def test_non_square_board_is_rejected(deep):
    deep["farms"][0]["tiles"][3] = deep["farms"][0]["tiles"][3][:9]
    with pytest.raises(ObservationError, match="columns"):
        parse_observation(deep)


# --- Strictness and immutability ---------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o.pop("farms"),
        lambda o: o.pop("private"),
        lambda o: o.pop("market"),
        lambda o: o.pop("town"),
        lambda o: o.pop("step"),
        lambda o: o.__setitem__("player", 2),
        lambda o: o.__setitem__("farms", o["farms"][:1]),
        lambda o: o["farms"][0].pop("hands"),
        lambda o: o["farms"][0].__setitem__("farmer", [4]),
        lambda o: o["private"].pop("inventories"),
        lambda o: o["market"].pop("prices"),
        lambda o: o["town"].__setitem__("unlocked_shops", "BAKERY"),
    ],
    ids=[
        "no-farms", "no-private", "no-market", "no-town", "no-step", "player-2", "one-farm",
        "no-hands", "bad-farmer-pos", "no-inventories", "no-prices", "shops-not-list",
    ],
)  # fmt: skip
def test_missing_or_malformed_official_fields_raise(deep, mutate):
    mutate(deep)
    with pytest.raises(ObservationError):
        parse_observation(deep)


@pytest.mark.parametrize("bad", [None, [], "obs", 42])
def test_non_mapping_observation_raises(bad):
    with pytest.raises(ObservationError):
        parse_observation(bad)


def test_parser_ignores_unknown_extra_keys(deep):
    deep["remainingOverageTime"] = 60
    deep["farms"][0]["extra"] = 1
    deep["market"]["params"] = {}
    assert parse_observation(deep).step == 0


def test_parsed_state_does_not_alias_raw_observation(obs_midgame_p1):
    raw = copy.deepcopy(obs_midgame_p1)
    state = parse_observation(raw)
    snapshot = json.dumps(dataclasses.asdict(state), sort_keys=True, default=str)
    raw["private"]["shed"]["WHEAT"] = 999
    raw["private"]["inventories"][0]["WHEAT"] = 999
    raw["market"]["inventory"]["WOOL"] = 0
    raw["farms"][1]["tiles"][4][1]["animal"] = "COW"
    raw["farms"][1]["hands"].append([0, 0])
    raw["town"]["unlocked_shops"].append("BAKERY")
    assert json.dumps(dataclasses.asdict(state), sort_keys=True, default=str) == snapshot


def test_game_state_is_immutable(obs_no_hands):
    state = parse_observation(obs_no_hands)
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.step = 5
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.me.money = 0


def test_parse_does_not_mutate_observation(obs_midgame_p1):
    before = json.dumps(obs_midgame_p1, sort_keys=True)
    parse_observation(obs_midgame_p1)
    assert json.dumps(obs_midgame_p1, sort_keys=True) == before


def test_parsing_is_deterministic(obs_midgame_p1):
    assert parse_observation(obs_midgame_p1) == parse_observation(obs_midgame_p1)
