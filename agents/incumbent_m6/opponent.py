"""Public-state opponent inference (Milestone 6). Estimates only; never private state as fact.

The opponent farm is public (TILLA_RULES.md §2): every plant's crop, planting
day, watering state and on-tile yield, and every animal's type, placement day,
feeding state and on-tile product. Opponent shed, seeds and carried inventories
are private: opponent units carry ``inventory = None`` and nothing here reads,
reconstructs or defaults them.

From the visible farm this module forecasts output that may reach the shared
market (``OpponentPipelineEstimate``), each with a realization window and a
confidence, plus bounded memory of visible yield that disappeared through
harvest. It produces estimates only; ``economy.opponent_supply`` turns them into
confidence-weighted market pressure (TILLA_STRATEGY.md §15). Deterministic:
estimates are built in stable (product, earliest step, source, y, x) order.
"""

from __future__ import annotations

from agents.incumbent_m6.constants import (
    ANIMALS,
    CROPS,
    LAST_DAY,
    OPPONENT_CONFIDENCE_WEIGHTS,
    OPPONENT_FORECAST_DAYS,
    OPPONENT_REALIZATION_WINDOW_TURNS,
    OPPONENT_RECENT_HARVEST_TURNS,
    PRODUCTS,
    TURNS_PER_DAY,
    WHEAT,
)
from agents.incumbent_m6.features import (
    animals,
    market_residuals,
    one_time_harvest_age,
    one_time_units_at_age,
    plant_age,
    plants,
)
from agents.incumbent_m6.models import (
    Confidence,
    EpisodeMemory,
    FarmState,
    GameState,
    MarketSnapshot,
    OpponentHarvestEvent,
    OpponentPipelineEstimate,
    PipelineSource,
    PlantTile,
    Position,
    StructureTile,
    TileKind,
)

_DOWNGRADE = {
    Confidence.HIGH: Confidence.MEDIUM,
    Confidence.MEDIUM: Confidence.LOW,
    Confidence.LOW: Confidence.LOW,
}


def confidence_weight(confidence: Confidence) -> float:
    return OPPONENT_CONFIDENCE_WEIGHTS[confidence.value]


def lead_confidence(lead_days: int) -> Confidence:
    """Confidence in visibly maintained production due ``lead_days`` from today:
    harvestable today is HIGH, within one crop cycle MEDIUM, beyond it LOW."""
    if lead_days <= 0:
        return Confidence.HIGH
    if lead_days <= OPPONENT_FORECAST_DAYS:
        return Confidence.MEDIUM
    return Confidence.LOW


def realization_share(earliest_step: int, latest_step: int, sale_step: int) -> float:
    """Share of an estimate's units realized by ``sale_step`` when realization is
    uniform over ``[earliest_step, latest_step]`` (a linear CDF, 0 before, 1 after)."""
    if sale_step < earliest_step:
        return 0.0
    if sale_step >= latest_step:
        return 1.0
    return (sale_step - earliest_step + 1) / (latest_step - earliest_step + 1)


def _estimate(
    product: str,
    source: PipelineSource,
    units: int,
    earliest: int,
    confidence: Confidence,
    position: Position | None,
    reason: str,
    window: int = OPPONENT_REALIZATION_WINDOW_TURNS,
) -> OpponentPipelineEstimate:
    latest = earliest + window - 1
    return OpponentPipelineEstimate(
        product=product,
        source=source,
        units=units,
        earliest_step=earliest,
        likely_step=(earliest + latest) // 2,
        latest_step=latest,
        confidence=confidence,
        position=position,
        reason=reason,
    )


def _plant_at_risk(plant: PlantTile) -> bool:
    return not plant.watered_today and plant.consecutive_unwatered >= 1


def estimate_crop_pipeline(
    pos: Position, plant: PlantTile, day: int, step: int
) -> list[OpponentPipelineEstimate]:
    """Output one visible opponent plant may bring to market (TILLA_RULES.md §8, §10)."""
    spec = CROPS.get(plant.crop)
    if spec is None:
        return []
    age = plant_age(day, plant)
    at_risk = _plant_at_risk(plant)
    harvestable = age >= spec.first_yield_day and plant.yield_units > 0
    out: list[OpponentPipelineEstimate] = []
    if not spec.ongoing:
        harvest_age = one_time_harvest_age(spec)
        if harvestable and (plant.yield_units >= spec.max_yield or age >= harvest_age):
            confidence = Confidence.LOW if age > spec.max_yield_day else Confidence.HIGH
            reason = "decaying: visibly neglected" if confidence is Confidence.LOW else "mature"
            out.append(
                _estimate(
                    plant.crop,
                    PipelineSource.READY_CROP,
                    plant.yield_units,
                    step,
                    confidence,
                    pos,
                    reason,
                )
            )
            return out
        harvest_day = plant.planted_day + harvest_age
        if harvest_day > LAST_DAY:
            return out
        units = min(
            spec.max_yield,
            max(plant.yield_units, one_time_units_at_age(spec, harvest_age)),
        )
        confidence = lead_confidence(harvest_day - day)
        if at_risk:
            confidence = _DOWNGRADE[confidence]
        out.append(
            _estimate(
                plant.crop,
                PipelineSource.GROWING_CROP,
                units,
                max(step, harvest_day * TURNS_PER_DAY),
                confidence,
                pos,
                "unwatered: may die" if at_risk else "growing",
            )
        )
        return out
    # Ongoing crop: yield already on the tile, then each remaining scheduled production.
    if harvestable:
        last_production_age = spec.first_yield_day + (spec.max_yield - 1) * spec.interval
        decaying = age > last_production_age + 1
        out.append(
            _estimate(
                plant.crop,
                PipelineSource.READY_CROP,
                plant.yield_units,
                step,
                Confidence.LOW if decaying else Confidence.HIGH,
                pos,
                "decaying: visibly neglected" if decaying else "harvestable yield on tile",
            )
        )
    for k in range(spec.max_yield):
        production_age = spec.first_yield_day + k * spec.interval
        if production_age <= age:
            continue
        production_day = plant.planted_day + production_age
        if production_day > LAST_DAY:
            break
        confidence = lead_confidence(production_day - day)
        if at_risk:
            confidence = _DOWNGRADE[confidence]
        out.append(
            _estimate(
                plant.crop,
                PipelineSource.GROWING_CROP,
                1,
                production_day * TURNS_PER_DAY,
                confidence,
                pos,
                "scheduled production",
            )
        )
    return out


def estimate_animal_pipeline(
    pos: Position, structure: StructureTile, day: int, step: int
) -> list[OpponentPipelineEstimate]:
    """Output one visible opponent animal may bring to market (TILLA_RULES.md §12-§14)."""
    animal = structure.animal
    if animal is None or animal.animal not in ANIMALS:
        return []
    spec = ANIMALS[animal.animal]
    at_risk = not animal.fed_today and animal.consecutive_unfed >= 1
    out: list[OpponentPipelineEstimate] = []
    if animal.yield_units > 0:
        capped = animal.yield_units >= spec.max_held
        out.append(
            _estimate(
                spec.product,
                PipelineSource.READY_ANIMAL,
                animal.yield_units,
                step,
                Confidence.LOW if capped else Confidence.HIGH,
                pos,
                "at max_held: visibly uncollected" if capped else "product on tile",
            )
        )
    age = day - animal.placed_day
    production_age = spec.first_yield_day
    while True:
        production_day = animal.placed_day + production_age
        if production_day > LAST_DAY:
            break
        if production_age > age:
            confidence = lead_confidence(production_day - day)
            if at_risk:
                confidence = _DOWNGRADE[confidence]
            out.append(
                _estimate(
                    spec.product,
                    PipelineSource.SCHEDULED_ANIMAL,
                    1,
                    production_day * TURNS_PER_DAY,
                    confidence,
                    pos,
                    "unfed: may escape" if at_risk else "scheduled production",
                )
            )
        production_age += spec.interval
    return out


# --- Harvest detection (bounded memory) ------------------------------------------------------


def visible_yield(farm: FarmState, day: int) -> dict[tuple[int, int], tuple[str, int, bool]]:
    """Harvestable yield visible on each opponent tile: (x, y) -> (product,
    units, ongoing producer). Plants count only from their first yield day
    (HARVEST fails earlier); animals whenever product is on the tile."""
    seen: dict[tuple[int, int], tuple[str, int, bool]] = {}
    for pos, plant in plants(farm):
        spec = CROPS.get(plant.crop)
        if spec and plant.yield_units > 0 and plant_age(day, plant) >= spec.first_yield_day:
            seen[(pos.x, pos.y)] = (plant.crop, plant.yield_units, spec.ongoing)
    for pos, tile in animals(farm):
        a = tile.animal
        if a is not None and a.yield_units > 0 and a.animal in ANIMALS:
            seen[(pos.x, pos.y)] = (ANIMALS[a.animal].product, a.yield_units, True)
    return seen


def _producer_yield(farm: FarmState, x: int, y: int, product: str) -> int | None:
    """On-tile yield of the plant/animal producing ``product`` at (x, y), or
    None when that producer is no longer there (tile emptied, weed, escape)."""
    tile = farm.tiles[y][x]
    if tile.kind is TileKind.PLANT and tile.crop == product:
        return tile.yield_units
    if tile.kind in (TileKind.COOP, TileKind.PASTURE) and tile.animal is not None:
        spec = ANIMALS.get(tile.animal.animal)
        if spec is not None and spec.product == product:
            return tile.animal.yield_units
    return None


def detect_harvests(
    previous: dict[tuple[int, int], tuple[str, int, bool]],
    farm: FarmState,
    step: int,
) -> list[OpponentHarvestEvent]:
    """Visible yield that disappeared since the previous turn through HARVEST:
    a one-time crop tile that became empty, or an ongoing crop / animal tile
    whose yield dropped to zero with the same producer still there (the
    official HARVEST takes all units, TILLA_RULES.md §7). Weeds (death, decay
    to zero) and escapes are not harvests. A DIG of a harvestable one-time
    crop is indistinguishable from a harvest and is counted as one."""
    harvested: dict[str, int] = {}
    for (x, y), (product, units, ongoing) in sorted(previous.items(), key=lambda i: i[0][::-1]):
        if ongoing:
            gone = _producer_yield(farm, x, y, product) == 0
        else:
            gone = farm.tiles[y][x].kind is TileKind.EMPTY
        if gone:
            harvested[product] = harvested.get(product, 0) + units
    return [
        OpponentHarvestEvent(step, product, harvested[product])
        for product in PRODUCTS
        if harvested.get(product)
    ]


def recent_harvest_estimates(
    state: GameState, memory: EpisodeMemory
) -> list[OpponentPipelineEstimate]:
    """Harvested opponent units not yet seen entering the market. The units'
    location is private; only the public market inflow observed since each
    harvest (positive residual inventory change, whoever sold it) is
    subtracted, oldest harvest first, so a realized sale is never counted
    again. What remains may reach the market until the window expires."""
    step = state.step
    out: list[OpponentPipelineEstimate] = []
    events = [e for e in memory.opponent_history if step - e.step < OPPONENT_RECENT_HARVEST_TURNS]
    # History ends at the previous turn; this turn's public market closes the last delta.
    snapshots = [s for s in memory.market_history if s.step < step]
    snapshots.append(
        MarketSnapshot(step, state.day, dict(state.market.inventory), state.town.unlocked_shops)
    )
    for product in PRODUCTS:
        mine = sorted((e for e in events if e.product == product), key=lambda e: e.step)
        if not mine:
            continue
        residuals = market_residuals(snapshots, product)
        consumed = 0
        for event in mine:
            inflow = sum(max(0, r) for after, r in residuals if after >= event.step)
            realized = max(0, inflow - consumed)
            remaining = max(0, event.units - realized)
            consumed += event.units - remaining
            if remaining <= 0:
                continue
            latest = event.step + OPPONENT_RECENT_HARVEST_TURNS - 1
            # Wheat is also animal feed: its sale is less certain.
            confidence = Confidence.LOW if product == WHEAT else Confidence.MEDIUM
            out.append(
                _estimate(
                    product,
                    PipelineSource.RECENT_HARVEST,
                    remaining,
                    step,
                    confidence,
                    None,
                    f"harvested at step {event.step}; sale not yet seen",
                    window=latest - step + 1,
                )
            )
    return out


# --- Forecast -----------------------------------------------------------------------------------


def estimate_pipeline(
    state: GameState, memory: EpisodeMemory
) -> dict[str, tuple[OpponentPipelineEstimate, ...]]:
    """Every visible opponent pipeline estimate, grouped by product in stable order."""
    farm, day, step = state.opponent, state.day, state.step
    found: list[OpponentPipelineEstimate] = []
    for pos, plant in plants(farm):
        found += estimate_crop_pipeline(pos, plant, day, step)
    for pos, tile in animals(farm):
        found += estimate_animal_pipeline(pos, tile, day, step)
    found += recent_harvest_estimates(state, memory)
    found.sort(
        key=lambda e: (
            e.product,
            e.earliest_step,
            e.source.value,
            e.position.y if e.position else -1,
            e.position.x if e.position else -1,
        )
    )
    grouped: dict[str, tuple[OpponentPipelineEstimate, ...]] = {}
    for product in PRODUCTS:
        items = tuple(e for e in found if e.product == product)
        if items:
            grouped[product] = items
    return grouped


def near_term_summary(
    forecast: dict[str, tuple[OpponentPipelineEstimate, ...]], step: int
) -> dict[str, float]:
    """Confidence-weighted units per product expected within one day (diagnostic)."""
    horizon = step + TURNS_PER_DAY
    summary: dict[str, float] = {}
    for product, items in forecast.items():
        total = sum(
            e.units
            * confidence_weight(e.confidence)
            * realization_share(e.earliest_step, e.latest_step, horizon)
            for e in items
        )
        if total > 0:
            summary[product] = round(total, 3)
    return summary


def update_model(
    state: GameState, memory: EpisodeMemory
) -> dict[str, tuple[OpponentPipelineEstimate, ...]]:
    """Once per turn: record harvests seen since the previous turn, then rebuild
    the forecast from the visible opponent farm. Idempotent within a turn.
    Harvests are detected only across consecutive observed turns."""
    if memory.opponent_step == state.step:
        return memory.opponent_forecast
    current = visible_yield(state.opponent, state.day)
    if memory.opponent_step == state.step - 1:
        memory.opponent_history.extend(
            detect_harvests(memory.opponent_visible_yield, state.opponent, state.step)
        )
    memory.opponent_visible_yield = current
    memory.opponent_step = state.step
    memory.opponent_forecast = estimate_pipeline(state, memory)
    memory.inferred_opponent_pipeline = near_term_summary(memory.opponent_forecast, state.step)
    return memory.opponent_forecast
