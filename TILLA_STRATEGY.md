# TILLA_STRATEGY.md

> **Audience:** AI coding agents implementing or modifying decision policy.  
> **Purpose:** Define what Tilla chooses to do. `TILLA_RULES.md` defines what is true; `TILLA_ARCHITECTURE.md` defines where code lives.  
> **Status:** Initial strategy v1. Every material policy change must be benchmarked against the frozen incumbent.

---

## 1. Objective

The only competition objective is:

> **Finish the season with more banked money than the opponent.**

Do not optimize aesthetics, farm size, gross production, theoretical asset value, or average yield unless they improve head-to-head terminal cash.

Primary strategy metric:

1. paired win rate vs incumbent.

Secondary:

2. median terminal cash margin.

Guardrails:

- zero crashes;
- zero malformed actions;
- zero timeouts;
- no severe mandatory-scenario regression.

A strategy that looks smarter in code is not better until it wins more matches.

---

## 2. Tilla identity

We are building a:

> **deterministic, stateful, market-aware economic planning agent with lightweight opponent forecasting.**

The policy has four responsibilities:

1. protect existing productive assets from avoidable loss;
2. allocate cash/land/labor to the best remaining-season opportunities;
3. time inventory sales against shared market supply and town demand;
4. alter decisions when the opponent's visible production pipeline changes expected future prices.

Tilla is **not** an RL policy and does not learn during a match.

---

## 3. Decision hierarchy

Every turn, priorities are considered in this order:

```text
1. Survival / irreversible-loss prevention
2. Mandatory completion of already-profitable near-term work
3. Inventory/capacity protection
4. Immediate high-value harvest/collection/sale
5. Planned production and expansion
6. Opportunistic market timing
7. Speculative expansion
8. PASS
```

Lower priorities must not cause a higher-priority failure.

Examples:

- Do not send the only reachable worker to plant melon if a valuable crop will die tonight without water.
- Do not buy an animal if doing so prevents feeding existing animals.
- Do not hoard inventory into shed overflow.
- Do not unlock land merely because cash is available.

---

## 4. Season phases

Phases are strategic biases, not hard-coded scripts.

### Phase A — Compound: days 0–7

Goal: turn starting cash and 25 unlocked tiles into a productive base without exhausting liquidity.

Biases:

- favor short/medium payback;
- establish a reliable crop cycle;
- hire cheap daily hands when they enable multiple productive actions;
- avoid overcommitting to premium products before town demand and opponent direction are visible;
- buy first land only when current unlocked land is a real constraint and expected remaining-season return clears cost.

### Phase B — Scale: days 8–20

Goal: maximize remaining-season economic throughput.

Biases:

- ongoing production and animals become more attractive because enough season remains to repay setup;
- expand land when marginal unlocked tiles have profitable planned use;
- exploit town shop composition;
- diversify away from obvious opponent-driven premium-product gluts;
- manage animals only when feed/labor costs are covered by expected product + fertilizer value.

### Phase C — Harvest value: days 21–26

Goal: reduce duration risk and convert mature systems into cash.

Biases:

- require faster payback for new investment;
- avoid setup-heavy animals unless benchmarked exceptions exist;
- favor harvesting and selling existing production;
- reduce inventory exposure when future demand cannot justify holding;
- maintain only assets likely to yield before season end.

### Phase D — Liquidate: days 27–29

Goal: maximize terminal bank.

Biases:

- no new land;
- no long-horizon planting;
- no new livestock;
- no speculative fertilizer purchases;
- prioritize harvest, collection, transport to shed, and sale;
- sell inventory even at suboptimal prices if there is insufficient time for credible recovery;
- never finish with valuable sellable inventory because of avoidable logistics.

Phase boundaries may move later only through benchmarked policy changes.

---

## 5. Mandatory survival policy

### Crops

A newly planted crop counts planting day as its first unwatered day. Therefore:

> **Every new planting must have a credible same-day watering plan.**

Never plant if no unit can water it before day refresh.

For existing plants:

- if `consecutive_unwatered == 1` and not watered today, watering is critical;
- protect higher expected remaining value first if labor cannot save everything;
- do not waste repeated water actions on a tile already watered today.

### Animals

Animals should be fed daily.

If labor or wheat is constrained:

1. secure wheat;
2. prioritize animals by expected remaining production value;
3. do not add animals while existing feed obligations are at risk.

Two consecutive missed feeds cause escape. Avoid preventable escape.

### Shed

Non-seed capacity is 100.

When shed occupancy approaches capacity:

- sell low-strategic-value product;
- schedule drops/harvests so end-of-day auto-drop does not discard high-value inventory;
- do not interpret carried inventory as free overflow capacity, because it is dropped at end of day.

Initial operational threshold:

- `shed >= 85`: capacity pressure starts affecting hold/sell scores;
- `shed >= 95`: liquidation/space creation becomes a high priority.

These are strategy parameters, not game rules, and may be tuned.

---

## 6. Cash policy

Cash has option value. Tilla must maintain a reserve rather than spending to zero.

Initial reserve model:

```text
reserve =
  expected next-day care/feed purchases
+ likely seed replenishment
+ cheap-hand budget
+ emergency market buffer
```

Implementation should estimate reserve dynamically.

Until calibrated, never intentionally reduce bank below:

- `300` coins in days 0–20;
- `200` in days 21–26;
- no fixed reserve during final liquidation except required task costs.

Exceptions are allowed only for an immediately realized positive-cash action in the same turn.

Affordability is not profitability.

---

## 7. Opportunity scoring

All expansion choices are compared through a common score.

For an opportunity `o`:

```text
expected_net_value(o)
= expected_sale_revenue
+ expected_byproduct_value
- purchase/setup cost
- expected feed/input cost
- labor opportunity cost
- land opportunity cost
- market-glut penalty
- execution-risk penalty
```

Then apply time:

```text
score(o)
= expected_net_value(o)
  * realization_probability
  * phase_weight
  / max(1, turns_to_realize_value)
```

This is not a requirement to predict perfectly. It is a requirement to compare alternatives consistently.

Never compare:

- seed cost alone;
- base market price alone;
- nominal maximum yield alone.

### Initial Milestone 3 economic parameters

The economic model (`economy.py`) instantiates the equation above with the parameters below. They are **initial, benchmark-tunable strategy choices made by Tilla, not game rules**; none is claimed optimal. Values live once in `constants.py` and change only with benchmark evidence recorded in §21.

Mechanics-derived inputs are not listed here: crop/animal tables, land prices, shed capacity, product list, season length, turn count, first-yield/bonus-window/yield-cap timing and fertilizer duration come from `TILLA_RULES.md` and are verified by conformance tests.

| Parameter | Value | Controls | Why it exists |
|---|---:|---|---|
| `LABOR_COST_PER_ACTION` (idle floor) | 3.0 coins | Minimum charge per unit action (incl. travel steps) | Actions are never free even when the farmer is idle |
| Labor-price scaling (`economy.labor_price`) | linear in utilization | Marginal action value rises from the floor to the best feasible crop's gross value per action as committed daily actions approach the budget | A saturated farmer must price actions at what they could earn; keeps labor-heavy options from crowding out better per-action uses |
| `FARMER_DAILY_ACTION_BUDGET` | 20.0 actions/day | Amortized daily care the single farmer will commit to; an opportunity whose amortized daily actions exceed the remaining budget gets realization 0 | 24 turns/day minus slack for shed trips and the daily feed purchase turn |
| `PLANT_DAILY_ACTIONS` / `ANIMAL_DAILY_ACTIONS` | 2.0 / 3.0 | Daily actions charged for each existing plant / animal when computing utilization | Water+move; feed+collect+amortized harvest with batched travel |
| `LAND_SCARCITY_FREE_TILES` | 8 tiles | Land cost is zero while at least this many empty unlocked tiles remain, then rises linearly to full at zero | Tiles are only scarce when the field is nearly full; no land purchases are made in v1 |
| `LAND_TILE_DAY_VALUE` | 18.0 coins/tile-day | Land opportunity cost per occupied tile-day at full scarcity | Roughly one wheat cycle's net value per tile-day |
| `EXECUTION_RISK_PER_DAY` | 0.5 coins/day of occupancy | Execution-risk penalty | Longer exposure to weed/care/timing failure |
| `MARKET_GLUT_PENALTY` | 0.0 | Market-glut term | Neutral until the market model (Milestone 5) exists |
| `PHASE_WEIGHT` | 1.0 | Phase term | Neutral until the phase engine (Milestone 7) exists |
| Realization probability | 1.0 or 0.0 | Binary: 0 when the season, an empty tile or the labor budget makes the opportunity mechanically unrealizable, else 1 | Explainable; no learned or random probabilities |
| Last harvest day (`economy.LAST_HARVEST_DAY`) | day 28 | Production counted only if harvestable by day 28, leaving a day to deliver and sell | Conservative terminal-horizon guard |
| Animal setup slack | placement assumed next day when `hour >= 20` | Shifts the production schedule when the build→buy→fetch→place chain cannot finish today | Avoids counting a production event that setup cannot reach |
| `FERTILIZER_BYPRODUCT_REALIZATION` | 0.5 | Share of an animal's daily fertilizer unit assumed collected and sold, net of collection labor | Conservative expected value; the game rule is one unit per surviving animal per day, collection is our labor choice |
| Feed input cost | current wheat sell price × remaining days | Animal feed obligation in the estimate | Own wheat has the same opportunity value as bought wheat |
| `FEED_WHEAT_RESERVE_PER_ANIMAL` | 2 units | Shed wheat never sold while an animal exists (0 on the final day) | Today's and tomorrow's known feed obligation |
| Animal harvest threshold | `yield_units >= max_held - 1`, or any product from day 28 | When a trip to harvest animal product is worth taking | The next production would hit the tile cap and be lost; collect everything before the end |
| One-time crop harvest | at the yield cap, at `max_yield_day` once watered, or immediately when decaying | When a crop is harvested | No further watering adds yield after the cap |
| `RESERVE_EMERGENCY_BUFFER` | 50 coins | Emergency market buffer term of the dynamic reserve | Small cushion for unplanned feed/seed purchases |
| `RESERVE_HAND_BUDGET` | 0 coins | Cheap-hand budget term of the dynamic reserve | Placeholder until hiring is part of policy (Milestone 4) |
| Execution cutoff | `score > 0` and realization `> 0` and `money - setup_cash >= reserve` | Which ranked opportunity may be executed | Only positive, realizable, reserve-respecting investments |
| Planting slots per turn | `remaining labor capacity // amortized daily actions` (≥ 1), capped by empty tiles and affordable seed | How many seeds are bought / tiles targeted at once | Do not stockpile beyond what labor can service |

Survival and care remain above all of this: economics only chooses among opportunities after tier 1–4 work is satisfied.

---

## 8. Crop policy

Official crop mechanics are in `TILLA_RULES.md`. Strategy uses them as follows.

### Wheat

Role:

- early liquidity;
- animal feed;
- lower market-glut sensitivity than premium goods;
- short turnaround.

Use wheat when:

- we need reliable short-horizon cash;
- animal feed pipeline is insufficient;
- market scarcity makes sale attractive;
- town composition creates durable wheat demand.

Do not sell all wheat if animals need upcoming feed.

### Carrot

Role:

- fast crop with stronger oversupply downside than wheat.

Use when:

- short horizon matters;
- current/future carrot market is not already glutting;
- Pet Cafe demand is present or expected economics are superior to wheat.

Avoid large synchronized carrot production if opponent pipeline already points to a glut.

### Tomato

Role:

- delayed but repeated production.

Use mainly in Phase A/B when enough season remains to collect repeated yields.

Do not start tomatoes late merely because current tomato price is high.

### Strawberry

Role:

- delayed ongoing premium product with severe glut sensitivity.

Treat as a market-timing asset, not a default crop.

Prefer when:

- future town demand is strong;
- opponent visible strawberry pipeline is limited;
- projected sale windows avoid obvious glut;
- enough season remains for multiple productions.

### Melon

Role:

- high nominal value, slow one-time crop, very severe glut sensitivity.

Use selectively when:

- there is enough remaining season;
- projected market supply is favorable;
- opponent pipeline does not threaten a synchronized dump;
- labor can support the full care cycle.

Never choose melon from base price alone.

---

## 9. Fertilizer policy

Fertilizer is valuable only when its incremental yield is worth more than:

- fertilizer acquisition value;
- action cost;
- worker travel/action opportunity cost;
- added market-glut exposure.

Priority targets:

1. high-value one-time crops in their productive bonus window when extra yield will likely sell at a good price;
2. ongoing crops on scheduled production days when doubling yield has positive expected value.

Do not fertilize automatically.

Animal-produced fertilizer has an opportunity cost equal to what it could be sold for, not zero.

---

## 10. Animal policy

Animals create persistent production but also create:

- structure setup actions;
- purchase cost;
- wheat feed demand;
- daily worker obligations;
- harvest/collection logistics;
- shed pressure.

Before buying an animal, calculate remaining-season payback including those costs.

### Goose

Generally the shortest animal payback. Eggs are less premium/glut-sensitive than milk/wool.

Potentially attractive from early/mid game if labor/feed capacity exists.

### Cow

Higher-value milk, but slower production and premium-product glut risk.

Buy only when:

- enough season remains;
- milk market/town demand supports it;
- wheat supply is sustainable;
- worker capacity can service it.

### Sheep

Wool has high nominal value but slow interval and strong glut exposure.

Treat as a selective strategy driven by Yarn Store demand and opponent supply, not a default purchase.

### CARE

CARE competes with every other unit action.

Use it when expected next-production bonus value exceeds the best alternative action for that worker, accounting for max-held caps.

### Fertilizer collection

Collect when:

- fertilizer can profitably improve crops;
- fertilizer sale value is worthwhile;
- capacity/logistics permit.

Do not send workers long distances for low-value fertilizer while higher-value farm work is pending.

---

## 11. Land policy

Land is purchased in fixed sequence/cost: 1000, 2000, 4000.

A land purchase is justified only if:

```text
expected incremental value of usable tiles before season end
> land cost + lost cash option value
```

Initial policy:

- never buy land solely because current quadrant is partly occupied;
- require a concrete planned use for a meaningful share of new tiles;
- first expansion may occur in Phase A/B when capacity is actually constraining profitable opportunities;
- second/third expansion require progressively stronger evidence;
- no land purchase in liquidation phase.

Land purchases should be rare enough to be explainable in replay analysis.

---

## 12. Hiring policy

Hands disappear each day and Fibonacci hire cost resets daily.

This makes the first few hands unusually cheap.

A hand should be hired when the **expected same-day marginal value of actions it enables** exceeds its hire cost.

Initial heuristic:

- cheap early hires are favored on days with multiple care/harvest/planting tasks;
- do not hire units that will mostly PASS or spend the day traveling without productive actions;
- hiring decision includes spawn location/travel cost;
- stop hiring when marginal hand value falls below next Fibonacci cost.

Task planner, not strategy, assigns specific work after the hire count is chosen.

---

## 13. Market policy

Market strategy uses **current price, inventory trajectory, town demand, our pipeline, and opponent pipeline**.

### Sell now when

- current price is attractive relative to expected near-term price;
- shed capacity pressure is high;
- endgame is near;
- opponent supply is likely to hit before town demand can recover price;
- product has low strategic use, for example excess wheat beyond feed reserve.

### Hold when

- credible town demand is about to reduce market inventory;
- opponent supply is not about to swamp that demand;
- shed has capacity;
- price recovery value exceeds holding risk.

### Premium goods

Strawberry, melon, milk, and wool can crash quickly toward the 1-coin floor.

Never bulk-produce or bulk-sell these without evaluating market inventory trajectory.

### Market manipulation

We may react strategically to shared-market mechanics, but v1 does **not** attempt expensive adversarial manipulation for its own sake.

Do not buy products merely to move price unless simulation proves net positive. Immediate buy/sell is not a free arbitrage.

---

## 14. Town-demand policy

Town shops are shared public information.

Each unlocked shop stays active for the rest of the season. Shops unlock without replacement from the not-yet-unlocked pool (verified, `TILLA_RULES.md` §19), so in the default pinned environment each shop type appears at most once; expected demand still accounts independently for every currently unlocked shop.

Maintain an expected demand rate by product from:

- town center;
- each currently unlocked shop;
- 2× consumption for single-product shops.

Use demand as a pressure term in future market inventory estimates.

Do not assume every product will recover merely because town consumes it; compare expected consumption against projected player supply.

---

## 15. Opponent model

The opponent model is a forecast, not hidden-state reconstruction.

Track publicly observable:

- crop type and planting age;
- animal/structure counts;
- visible unharvested yield;
- land unlocks;
- hires;
- money;
- tile changes;
- market inventory deltas around plausible harvest/sale windows.

Produce:

```text
opponent_supply_forecast[product] = {
    near_term_units,
    medium_term_units,
    confidence
}
```

Strategy uses it to modify our opportunity and sale scores.

Examples:

- large opponent strawberry field nearing production → reduce strawberry investment, increase urgency to sell existing strawberry if price is vulnerable;
- opponent has many cows + milk-demand shops absent → penalize new cow investment;
- opponent is cash-poor and underdeveloped → do not overreact; keep compounding profitable own farm.

Never chase the opponent so aggressively that we abandon positive expected value.

---

## 16. Task execution policy

Strategy chooses objectives; task planner executes them.

Daily/turn task priorities:

1. save at-risk crop/animal;
2. harvest/collect capped or high-value ready output;
3. clear imminent shed overflow;
4. complete already-started high-value production work;
5. execute planned planting/building/placement;
6. perform profitable care/fertilizer work;
7. reposition units toward next likely jobs;
8. PASS.

A worker's travel turns are costs. Prefer spatial batching:

- one unit services nearby plants;
- avoid two units crossing the whole farm for independent trivial tasks;
- consider returning loaded units toward shed before day end.

Do not duplicate unit actions on the same resource unless rules and plan explicitly require it.

---

## 17. Endgame policy

Terminal score is **bank money only**.

Therefore, near the end:

- unrealized farm assets have value only if they can generate sellable output before termination;
- shed inventory has value only if it can be sold before the end;
- carried inventory must be routed to a sellable state;
- long-lived production systems should not consume cash that cannot repay.

From day 27 onward:

- compute remaining turns explicitly for every investment;
- prioritize harvesting and market orders;
- progressively lower willingness to hold inventory for a better price;
- on final day, sell all sellable product unless an earlier same-day sale ordering yields a clearly better realized price;
- do not leave market orders beyond the 10-order cap.

A beautiful farm that loses on bank is a failed strategy.

---

## 18. Fallback behavior

When uncertain:

1. preserve assets;
2. preserve cash;
3. take a known positive immediate value action;
4. avoid irreversible speculative investment.

If strategy produces no valid objective, task planner may reposition or PASS.

If any layer fails unexpectedly, `main.py` returns valid PASS shape. This is a runtime safety fallback, not strategy.

---

## 19. Evaluation and promotion

Every material strategy change creates a candidate.

### Required sequence

1. unit + scenario tests;
2. full games against built-in `starter`;
3. games against representative fixed baselines;
4. paired seeded candidate-vs-incumbent tournament with seat swaps;
5. inspect representative wins/losses;
6. promote or revert.

### Default promotion gate

At least 2,000 paired games.

Promote only if:

- candidate win rate > 53%;
- 95% Wilson lower bound > 50%;
- median terminal cash margin > 0;
- 0 crashes;
- 0 timeouts;
- mandatory scenarios do not materially regress.

If a change targets a narrow exploit/opponent type but fails the general promotion gate, keep it out of Tilla unless we later design a reliable opponent-classification switch and benchmark the combined policy.

### Never optimize on one seed

Use a stable benchmark seed set plus a rotating holdout seed set.

Do not repeatedly tune on the holdout and continue calling it holdout.

---

## 20. Current policy parameters

These are initial values, not game rules.

```text
EARLY_PHASE_END_DAY = 7
SCALE_PHASE_END_DAY = 20
HARVEST_PHASE_END_DAY = 26
LIQUIDATE_START_DAY = 27

EARLY_MIN_CASH_RESERVE = 300
MID_MIN_CASH_RESERVE = 300
HARVEST_MIN_CASH_RESERVE = 200

SHED_PRESSURE_START = 85
SHED_EMERGENCY = 95
```

Milestone 3 economic parameters (labor, land, risk, byproduct realization, reserve components, harvest thresholds, execution cutoffs) are documented in §7 "Initial Milestone 3 economic parameters".

Do not scatter these values through strategy code. Define them once in `constants.py` and document changes here with benchmark evidence.

---

## 21. Strategy change record

Append concise entries only when a policy is actually promoted.

Format:

```text
YYYY-MM-DD — S-###
Change:
Reason:
Benchmark:
Result:
```

Initial record:

```text
2026-09-19 — S-001
Change: Establish deterministic economic planner with season phases, market awareness, and stateful opponent forecasting.
Reason: Strong transparent baseline before considering learning/search systems.
Benchmark: Pending implementation.
Result: Initial strategy source of truth.
```
