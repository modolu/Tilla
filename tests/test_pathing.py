"""Tests for kaggriculture_bot.pathing (Milestone 2: deterministic BFS movement)."""

from kaggriculture_bot.models import Position, TileKind, UnitOp
from kaggriculture_bot.parser import parse_observation
from kaggriculture_bot.pathing import (
    DIRECTION_ORDER,
    MOVE_VECTORS,
    distance,
    is_passable,
    manhattan,
    nearest,
    neighbors,
    next_step_toward,
    shortest_paths,
)

P = Position


def test_move_vectors_match_official_directions():
    assert MOVE_VECTORS == {
        UnitOp.NORTH: (0, -1),
        UnitOp.SOUTH: (0, 1),
        UnitOp.EAST: (1, 0),
        UnitOp.WEST: (-1, 0),
    }
    assert DIRECTION_ORDER == (UnitOp.NORTH, UnitOp.SOUTH, UnitOp.EAST, UnitOp.WEST)


def test_same_position_has_zero_distance_and_no_step(obs_no_hands):
    tiles = parse_observation(obs_no_hands).me.tiles
    assert distance(tiles, P(4, 4), P(4, 4)) == 0
    assert next_step_toward(tiles, P(4, 4), P(4, 4)) is None


def test_adjacent_and_multi_turn_targets(obs_no_hands):
    tiles = parse_observation(obs_no_hands).me.tiles
    assert next_step_toward(tiles, P(4, 4), P(4, 3)) is UnitOp.NORTH
    assert next_step_toward(tiles, P(4, 4), P(3, 4)) is UnitOp.WEST
    assert distance(tiles, P(4, 4), P(0, 0)) == 8 == manhattan(P(4, 4), P(0, 0))
    # Walk the returned steps and arrive in exactly `distance` moves.
    pos, steps = P(4, 4), 0
    while pos != P(0, 0):
        op = next_step_toward(tiles, pos, P(0, 0))
        dx, dy = MOVE_VECTORS[op]
        pos, steps = P(pos.x + dx, pos.y + dy), steps + 1
        assert steps <= 8
    assert steps == 8


def test_board_edges_and_no_off_board_steps(obs_no_hands):
    tiles = parse_observation(obs_no_hands).me.tiles
    assert [op for op, _ in neighbors(tiles, P(0, 0))] == [UnitOp.SOUTH, UnitOp.EAST]
    assert not is_passable(tiles, P(-1, 0)) and not is_passable(tiles, P(0, 10))
    for pos, reached in shortest_paths(tiles, P(0, 0)).items():
        assert 0 <= pos.x < 10 and 0 <= pos.y < 10
        assert reached[0] >= 0


def test_locked_tiles_are_obstacles_but_a_locked_start_can_be_left(obs_no_hands):
    tiles = parse_observation(obs_no_hands).me.tiles
    assert tiles[4][5].kind is TileKind.LOCKED
    assert not is_passable(tiles, P(5, 4))
    assert distance(tiles, P(4, 4), P(5, 4)) is None
    assert next_step_toward(tiles, P(4, 4), P(9, 9)) is None  # whole quadrant locked
    assert all(
        tiles[p.y][p.x].kind is not TileKind.LOCKED
        for p in shortest_paths(tiles, P(4, 4))
        if p != P(4, 4)
    )
    # A hand spawned on locked (5,4) can step WEST onto (4,4) and then reach the field.
    assert next_step_toward(tiles, P(5, 4), P(4, 4)) is UnitOp.WEST
    assert distance(tiles, P(5, 4), P(0, 0)) == 9


def test_units_do_not_block_paths(obs_two_hands):
    state = parse_observation(obs_two_hands)
    tiles = state.me.tiles
    occupied = {u.position for u in state.me.units}
    assert P(4, 4) in occupied
    # A route through the farmer's own tile is still the shortest one.
    assert distance(tiles, P(3, 4), P(4, 3)) == 2
    assert next_step_toward(tiles, P(3, 4), P(4, 3)) in (UnitOp.NORTH, UnitOp.EAST)


def test_equal_length_ties_follow_direction_order(obs_no_hands):
    tiles = parse_observation(obs_no_hands).me.tiles
    # (4,4) -> (2,2): NORTH-first BFS expansion wins over WEST.
    assert next_step_toward(tiles, P(4, 4), P(2, 2)) is UnitOp.NORTH
    # (2,2) -> (4,4): SOUTH before EAST.
    assert next_step_toward(tiles, P(2, 2), P(4, 4)) is UnitOp.SOUTH
    assert next_step_toward(tiles, P(4, 4), P(2, 2)) is next_step_toward(tiles, P(4, 4), P(2, 2))


def test_nearest_target_set_routing_is_deterministic(obs_no_hands):
    tiles = parse_observation(obs_no_hands).me.tiles
    assert nearest(tiles, P(4, 4), [P(0, 0), P(4, 2), P(2, 4)]) == (P(4, 2), 2)  # (y, x) tie-break
    assert nearest(tiles, P(4, 4), [P(2, 4), P(4, 2)]) == (P(4, 2), 2)
    assert nearest(tiles, P(4, 4), [P(4, 4), P(4, 3)]) == (P(4, 4), 0)
    assert nearest(tiles, P(4, 4), [P(9, 9), P(5, 4)]) is None  # all locked / unreachable
    assert nearest(tiles, P(4, 4), []) is None
