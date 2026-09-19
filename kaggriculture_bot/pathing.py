"""Deterministic movement and shortest-path primitives.

Board facts used here (TILLA_RULES.md §3, §5): NORTH is y-1, SOUTH y+1, EAST
x+1, WEST x-1; moving off-board or onto a locked tile is a no-op, so locked
tiles are obstacles (a unit standing on one may still leave it); units never
block each other. Ties between equal-length paths are broken by the fixed
direction order NORTH, SOUTH, EAST, WEST at every expansion, so the first move
returned is fully deterministic.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from kaggriculture_bot.models import Position, Tile, TileKind, UnitOp

MOVE_VECTORS: dict[UnitOp, tuple[int, int]] = {
    UnitOp.NORTH: (0, -1),
    UnitOp.SOUTH: (0, 1),
    UnitOp.EAST: (1, 0),
    UnitOp.WEST: (-1, 0),
}
DIRECTION_ORDER: tuple[UnitOp, ...] = tuple(MOVE_VECTORS)

Board = tuple[tuple[Tile, ...], ...]


def board_size(tiles: Board) -> int:
    return len(tiles)


def in_bounds(pos: Position, size: int) -> bool:
    return 0 <= pos.x < size and 0 <= pos.y < size


def is_passable(tiles: Board, pos: Position) -> bool:
    """A unit may move onto ``pos``: on the board and not locked."""
    return in_bounds(pos, board_size(tiles)) and tiles[pos.y][pos.x].kind is not TileKind.LOCKED


def neighbors(tiles: Board, pos: Position) -> list[tuple[UnitOp, Position]]:
    """Legal single moves from ``pos`` in stable direction order."""
    result = []
    for op in DIRECTION_ORDER:
        dx, dy = MOVE_VECTORS[op]
        nxt = Position(pos.x + dx, pos.y + dy)
        if is_passable(tiles, nxt):
            result.append((op, nxt))
    return result


def manhattan(a: Position, b: Position) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def shortest_paths(tiles: Board, start: Position) -> dict[Position, tuple[int, UnitOp | None]]:
    """BFS from ``start``: ``{reachable position: (distance, first move)}``.

    ``start`` maps to ``(0, None)``. Locked tiles are never entered, but
    ``start`` itself may be locked (a hand spawned there can still leave).
    """
    reached: dict[Position, tuple[int, UnitOp | None]] = {start: (0, None)}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        dist, first = reached[current]
        for op, nxt in neighbors(tiles, current):
            if nxt in reached:
                continue
            reached[nxt] = (dist + 1, first if first is not None else op)
            queue.append(nxt)
    return reached


def distance(tiles: Board, start: Position, target: Position) -> int | None:
    """Shortest legal move count from ``start`` to ``target``; ``None`` if unreachable."""
    entry = shortest_paths(tiles, start).get(target)
    return None if entry is None else entry[0]


def next_step_toward(tiles: Board, start: Position, target: Position) -> UnitOp | None:
    """First move of a shortest path to ``target``; ``None`` when already there or unreachable."""
    entry = shortest_paths(tiles, start).get(target)
    return None if entry is None else entry[1]


def nearest(
    tiles: Board, start: Position, targets: Iterable[Position]
) -> tuple[Position, int] | None:
    """Closest reachable target by (distance, y, x); ``None`` if none is reachable."""
    paths = shortest_paths(tiles, start)
    best = None
    for target in targets:
        entry = paths.get(target)
        if entry is None:
            continue
        key = (entry[0], target.y, target.x)
        if best is None or key < best[0]:
            best = (key, target)
    return None if best is None else (best[1], best[0][0])
