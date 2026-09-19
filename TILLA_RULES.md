# TILLA_RULES.md

> **Audience:** AI coding agents.  
> **Purpose:** Condensed source of truth for Kaggriculture game mechanics used by this repo.  
> **Strategy-free:** This file says what the environment does, never what Tilla prefers.  
> **Verified:** 2026-09-19 against the official advanced Kaggriculture files in `Kaggle/kaggle-environments`.

If this file differs from the official environment, the official environment wins. Correct this file before adapting strategy.

---

## 1. Episode and objective

- Environment: `kaggriculture` (advanced), 2 players.
- Default episode: **720 turns**.
- **24 turns/day**, **30 days**.
- `day` and `hour` are 0-indexed.
- Default starting bank: **3000** coins/player.
- Default board: **10×10**.
- Kaggle config currently declares **1-second `actTimeout`**.
- Final reward/score is **bank money at end of game**.
- Highest bank wins; ties are possible.

---

## 2. Observation visibility

Top-level observation:

```text
player
step
day
hour
farms       # public, both players
private     # only current player's private state
market      # shared
town        # shared
```

Public farm state includes:

- bank money;
- board tiles;
- main farmer position;
- hired-hand positions;
- unlocked quadrants;
- hires made today.

Private state includes only our:

- shed counts;
- seed counts;
- per-unit carried inventories.

**Opponent shed, opponent seeds, and opponent carried inventories are not visible.**

Do not build logic that assumes access to them.

---

## 3. Farm and land

The 10×10 board is split into four 5×5 quadrants:

```text
NW | NE
---+---
SW | SE
```

- NW starts unlocked.
- Additional land unlock order is fixed: **NE, SW, SE**.
- Prices: **1000, 2000, 4000**.
- Locked tiles are passable by units.
- Most tile actions on locked land are no-ops.
- The shed is not a tile.

A tile is one of:

- `None`: empty unlocked;
- `"LOCKED"`;
- plant dict;
- weed dict;
- coop/pasture dict, optionally containing an animal.

Each crop/animal structure occupies one tile.

---

## 4. Shed and inventory

Default non-seed shed capacity: **100 items**.

- Seeds are stored separately and do not count toward 100.
- Harvest/pickup goes into the acting unit's carried inventory.
- At end of day, carried inventory is dropped into shed.
- Overflow beyond shed capacity is **discarded**.
- Carrying items does not bypass the cap because end-of-day drop still applies.

Shed access positions on default 10×10 board are the four center tiles:

```text
(4,4) (5,4)
(4,5) (5,5)
```

Only NW starts unlocked, but shed actions can work from the center access tiles even if that standing tile is locked.

### Shed actions

`PICKUP <item> [n]`
- requires shed-adjacent position;
- moves up to `n` from shed to unit inventory;
- seeds cannot be picked up.

`DROP`
- requires shed-adjacent position;
- dumps entire unit inventory to shed;
- overflow is discarded.

`PLACE <item> [n]`
- on matching structure: places one animal from carried inventory;
- when shed-adjacent: can move up to `n` carried items into shed.

---

## 5. Units and movement

Each player has:

- 1 permanent main farmer;
- 0+ hired hands for the current day.

Every unit may act once each turn.

Units may share a tile.

Movement actions:

```text
NORTH
SOUTH
EAST
WEST
PASS
```

Moving off-board is a no-op.

### Hiring

`HIRE` is a market action.

Daily hire costs follow Fibonacci starting:

```text
1, 1, 2, 3, 5, 8, 13, 21, ...
```

multiplied by `farmHandCostMult` (default 1).

- `hires_today` determines next cost.
- Cost sequence resets each day.
- Hands disappear at day end.
- Hands must be rehired next day.
- New hands spawn around the shed according to environment placement rules.
- Spawn can occur on a locked tile; locked tiles are passable.

---

## 6. Action shape

Tilla returns:

```json
{
  "farmer": ["PASS"],
  "hands": [],
  "market": []
}
```

- `farmer`: exactly one farmer action.
- `hands`: one action for each hired hand, in current hand order.
- `market`: ordered list of market actions.

Default maximum market orders processed per player per turn: **10**.

Orders past the cap are silently dropped.

Many illegal unit/game actions are silent no-ops. Our code should avoid relying on this.

---

## 7. Unit actions

### Plant/crop

```text
PLANT <crop>
WATER
HARVEST
FERTILIZE
DIG
```

### Animal/structure

```text
BUILD_COOP
BUILD_PASTURE
PLACE <animal>
FEED
HARVEST
CARE
COLLECT_FERTILIZER
DIG
```

`DIG` removes:

- plant;
- weed;
- empty coop/pasture.

A structure containing an animal cannot be dug.

---

## 8. Crop constants

Current official default crop table:

| Crop | Seed | Base sale price | First yield | Max-yield age | Production | Max yield / stored units |
|---|---:|---:|---:|---:|---|---:|
| Wheat | 10 | 25 | day 2 | day 4 | one-time | 6 fertilized; 4 without fertilizer |
| Carrot | 20 | 35 | day 2 | day 3 | one-time | 4 fertilized; 3 without fertilizer |
| Tomato | 50 | 60 | day 8 | day 11 | ongoing daily ×4 scheduled yields | 4 cumulative scheduled yields |
| Strawberry | 100 | 120 | day 10 | day 16 | ongoing every 2 days ×4 | 4 cumulative scheduled yields |
| Melon | 80 | 250 | day 10 | day 10 | one-time | 6 |

Important:

- Tomato and strawberry are ongoing but **not indefinite**.
- Once they have completed their capped scheduled productions, they later decay.
- Melon bonus window extends further than the age needed to hit its yield cap; extra watering after cap does not add yield.

---

## 9. Watering and crop death

Plants should be watered every day.

Death rule:

- two consecutive missed end-of-day watering refreshes turn a plant into a weed;
- **planting day counts as the first unwatered day**;
- a newly planted seed that is not watered on planting day becomes a weed at that night's refresh.

`WATER` only needs to succeed once per day. Repeated watering that day is a no-op.

This same-day watering requirement for fresh plantings is critical.

---

## 10. Crop yield rules

### One-time crops

Wheat, carrot, melon:

- base harvestable yield starts at 1;
- bonus window begins at `ceil(max_yield_day / 2)`;
- watering during bonus window adds +1 yield/day;
- if fertilized and watered during the bonus period, that day's bonus is +2 instead;
- yield is capped by crop maximum.

### Ongoing crops

Tomato and strawberry:

- production occurs on fixed scheduled days;
- base scheduled production = 1;
- if plant is both fertilized and watered that production day, scheduled yield = 2;
- cumulative scheduled production count is capped by crop rules.

### Decay

After max lifespan:

- `yield_units` decreases by 1 every other turn;
- when it reaches 0, tile becomes a weed.

One-time crops begin decay one day after max-yield age.

Ongoing crops begin decay one day after their cumulative production cap has been reached.

---

## 11. Fertilizer

Market purchase:

- Fertilizer can be bought via `BUY_PRODUCT FERTILIZER`.
- Current base market price is 100, but actual buy price follows market inventory.

`FERTILIZE`:

- boosts eligible crop yield effects for the next 3 days;
- yield bonus still requires basic watering;
- does not replace watering.

Animals can also generate fertilizer; see §14.

---

## 12. Animal constants

| Animal | Buy cost | Product | Base product price | Structure | First production | Interval | Max unharvested product |
|---|---:|---|---:|---|---:|---:|---:|
| Goose | 300 | Egg | 50 | Coop | day 4 | daily | 4 |
| Cow | 400 | Milk | 160 | Pasture | day 8 | every 2 days | 6 |
| Sheep | 500 | Wool | 200 | Pasture | day 6 | every 3 days | 6 |

Animals can produce indefinitely for the season if they remain on farm and are maintained.

`max_held` is an unharvested-on-tile cap, not lifetime production.

---

## 13. Animal feeding and escape

Animals should be fed every day using wheat.

- A newly placed animal starts with `consecutive_unfed = 0`.
- Two consecutive missed end-of-day feeds make it escape permanently.
- `FEED` once/day is enough.
- Wheat may come from our stock or be bought from market via `BUY_PRODUCT WHEAT`.

Production-day nuance:

- if animal is unfed on a scheduled production day, base production can still occur;
- however, care bonus is not applied and banked care bonus resets as defined below;
- repeated lack of feed still risks escape at day refresh.

---

## 14. Animal CARE and fertilizer

`CARE` can be performed once per animal per day.

At end of day:

- if animal was both fed and cared for, `pending_care_bonus += 1`;
- unfed care does not bank the bonus.

On scheduled production:

- if fed, base production + all banked care bonus is produced, subject to max-held cap;
- care bonus bank resets;
- if unfed on production day, base production occurs but banked bonus is not applied and resets.

Fertilizer:

- every surviving animal makes 1 fertilizer available at end of each day;
- this occurs whether or not it was fed/cared for;
- only 1 can be waiting;
- uncollected fertilizer does not accumulate over multiple days;
- `COLLECT_FERTILIZER` collects it.

---

## 15. Weeds

Default per-empty-unlocked-tile weed spawn chance at end of day: **0.005**.

A weed blocks normal use of its tile.

Use `DIG` to clear it.

Do not assume an empty tile remains empty across day refresh.

---

## 16. Market purchases

Fixed-price categories at source level:

- seeds have fixed purchase prices;
- animals have fixed purchase prices.

Dynamic `BUY_PRODUCT` applies only to:

- `WHEAT`;
- `FERTILIZER`.

Every product can be sold with `SELL`.

Buying products removes units from shared market inventory.

Selling usually adds units to shared market inventory.

---

## 17. Market processing

Market actions are an ordered list.

Players' market queues are processed concurrently/interleaved one unit at a time.

Consequences:

- realized price may change during a multi-unit order;
- both players can influence the price while orders are being processed;
- if buyer runs out of money mid-order, remaining quantity stops.

Special floor rule:

- sell price floor is **1**;
- if price is already 1, a sold unit is purchased but is **not added** to market inventory.

Buy quote uses post-buy inventory and sell quote uses pre-sell inventory; immediate buy-then-sell against unchanged market gives zero net arbitrage.

---

## 18. Market price function

Every product begins around equilibrium inventory `I0 = 10,000`.

For product inventory:

- below `I0` → scarcity → price rises;
- above `I0` → glut → price falls;
- price is rounded to nearest coin;
- floor = 1.

General form:

```text
price(inv) = base + sign * amp * f(|inv - I0|)

sign = +1 below I0
sign = -1 above I0

amp = target * base / f(T)
```

Shape function depends on product/side and is one of the official configured functions.

Current default reference points:

| Product | Base | T | Scarcity shape | Scarcity target | Glut shape | Glut target |
|---|---:|---:|---|---:|---|---:|
| Wheat | 25 | 400 | sqrt | 0.80 | log | 0.20 |
| Carrot | 35 | 450 | log | 0.20 | sqrt | 0.70 |
| Tomato | 60 | 200 | linear | 0.40 | sqrt | 0.60 |
| Strawberry | 120 | 100 | sqrt | 0.70 | linear | 1.60 |
| Melon | 250 | 300 | log | 0.20 | sq | 3.60 |
| Egg | 50 | 332 | linear | 0.40 | log | 0.20 |
| Milk | 160 | 122 | sqrt | 0.60 | linear | 1.60 |
| Wool | 200 | 105 | log | 0.20 | sq | 3.20 |
| Fertilizer | 100 | 200 | linear | 0.40 | linear | 0.40 |

Premium goods—strawberry, melon, milk, wool—are especially vulnerable to oversupply and can fall rapidly to the 1-coin floor.

Do not duplicate the exact market formula independently in multiple modules.

---

## 19. Town demand

Town demand removes items from shared market inventory and therefore can increase future prices.

### Town center

Default (`townCenterSellInterval = 12`):

- ticks every **12 turns**, on turns where `step % 12 == 0` (including turn 0), i.e. twice per 24-turn day;
- each tick removes the same quantity of **every non-fertilizer product**;
- quantity per product per tick scales with the day: **1** on days 0–9, **2** on days 10–19, **4** on days 20–29 (`TOWN_CENTER_DEMAND_SCHEDULE = [(20, 4), (10, 2), (0, 1)]`);
- fertilizer is never consumed by the town center.

Verified against installed `kaggle-environments==1.30.2` (`kaggriculture.py` `_town_consume`, `kaggriculture.json`, `README.md`, `AGENTS.md`) and by `tests/test_rules_conformance.py`.

### Shops

- new shop unlock every **3 days** by default;
- selection is uniform **with replacement**;
- duplicate shop instances are possible;
- maximum 8 unlocked instances;
- once unlocked, a shop stays active;
- each instance consumes on every **4-turn** shop tick;
- each requested product normally consumes 1;
- single-product shops consume 2×.

Shop demand:

| Shop | Products |
|---|---|
| Bakery | Egg, Wheat |
| Pizza Shop | Milk, Tomato, Wheat |
| Brunch Spot | Egg, Wheat, Strawberry |
| Yarn Store | Wool (2×) |
| Ice Cream Shop | Strawberry, Milk, Wheat |
| Pet Cafe | Carrot (2×) |
| Smoothie Shop | Strawberry, Milk |
| Farmers Market | Wheat, Carrot, Tomato, Strawberry |

Duplicate shops consume independently.

---

## 20. Turn processing order

Current official high-level order:

1. validate actions;
2. apply player/unit actions;
3. process market action queues;
4. process town consumption;
5. update observations/state, including applicable day refresh and price/state updates.

Strategy should not assume a different order.

When exact same-turn behavior matters, verify against `kaggriculture.py` and add a conformance test rather than guessing.

---

## 21. End-of-day effects that matter

At day transition:

- carried inventory is returned toward shed handling, subject to capacity;
- hands disappear;
- daily hire count resets;
- crop watering status refreshes and missed-watering death is evaluated;
- animal feeding/care daily state refreshes and escape/care effects are evaluated;
- weeds may spawn on empty unlocked tiles;
- animal fertilizer availability updates;
- units start the new day around the shed according to environment rules.

Exact ordering edge cases should be tested against official environment before encoding strategic assumptions.

---

## 22. Submission contract

Kaggle requires a root:

```text
main.py
```

containing:

```python
def agent(obs):
    ...
```

Single-file submission is allowed.

Multi-file submission is allowed as `.tar.gz` with `main.py` at archive root.

Official local environment supports:

```python
env = make("kaggriculture", configuration={"episodeSteps": 720}, debug=True)
env.run([agent_a, agent_b])
```

Built-in agent names include:

- `pass`;
- `random`;
- `starter`.

---

## 23. Rule verification policy

When an AI agent discovers a possible discrepancy:

1. do not guess;
2. inspect official `README.md`;
3. inspect `AGENTS.md`;
4. inspect `kaggriculture.py` for implementation truth;
5. inspect `kaggriculture.json` for configuration/schema;
6. write a minimal local environment test if behavior still matters;
7. update this file and the conformance test together.

Never change `TILLA_STRATEGY.md` to compensate for a misunderstood rule before verifying the rule.
