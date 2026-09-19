"""Raw Kaggle observation -> typed ``GameState``. Parsing only; no decisions.

Full ``GameState`` parsing arrives in Milestone 1. Milestone 0 exposes only the
minimal observation reads needed to emit a structurally valid PASS action.
"""


def count_hired_hands(obs) -> int:
    """Return the number of hands currently hired by the observing player.

    Missing or malformed fields count as zero hands rather than raising, so a
    structurally valid action can still be produced.
    """
    if not isinstance(obs, dict):
        return 0
    player = obs.get("player", 0)
    farms = obs.get("farms") or []
    if not isinstance(player, int) or not isinstance(farms, list):
        return 0
    if player < 0 or player >= len(farms):
        return 0
    farm = farms[player]
    if not isinstance(farm, dict):
        return 0
    hands = farm.get("hands") or []
    return len(hands) if isinstance(hands, list) else 0
