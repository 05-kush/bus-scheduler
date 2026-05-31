# Architecture — Bus Charging Scheduler

---

## 1. Framework / Approach — and Why

### Choice: Weighted Cost Rule Engine + Priority-Queue Simulation

The scheduler uses a **rule-engine** architecture where:

1. **Rules are pure, independent scoring functions** — each implements a single
   soft objective (individual fairness, operator fairness, network efficiency).
2. **The engine knows nothing about rule internals** — it calls `rule.score()` on
   every rule for every candidate plan and sums the weighted results.
3. **Charger queues are modelled explicitly** — each charger slot at each station
   is a FIFO queue. Buses wait behind earlier arrivals. No buses are teleported or
   magically resolved; the simulation is physically faithful.

### Why this fits the problem

The assignment explicitly states three concerns that will evolve independently:

| Concern | How the rule engine handles it |
|---------|-------------------------------|
| Weights will be tuned in the field | Weights live in JSON — zero code changes |
| New rules will be added as you learn more | Add one class to `rules.py`, register it — engine unchanged |
| World will grow (buses, stations, operators, routes) | Everything is data-driven — add JSON, not code |

**Alternative considered: constraint solver (e.g. OR-Tools / PuLP)**

A constraint solver produces a globally optimal schedule but:
- Requires translating every new rule into a solver constraint — high friction
- Tuning weights means rebuilding the objective function — brittle
- Adding a rule mid-field requires solver expertise to encode correctly

The rule engine trades global optimality for **operational agility** — the right
trade-off when the rules themselves are still being discovered.

---

## 2. Data Structure Design

### Principle

> Every value that can change operationally lives in the scenario JSON.
> The engine contains zero hardcoded constants.

### Scenario JSON Schema

```json
{
  "scenario_id": "string",
  "name": "Human-readable name",
  "description": "What this scenario tests",

  "route": {
    "stations": ["Bengaluru", "A", "B", "C", "D", "Kochi"],
    "segments": [
      {"from": "Bengaluru", "to": "A", "distance_km": 100},
      ...
    ]
  },

  "physics": {
    "battery_range_km": 240,
    "charge_duration_min": 25,
    "speed_kmh": 60
  },

  "stations": [
    {"id": "A", "chargers": 1},
    ...
  ],

  "operators": ["kpn", "freshbus", "flixbus"],

  "weights": {
    "individual": 1.0,
    "operator":   1.0,
    "overall":    1.0
  },

  "buses": [
    {
      "id": "bus-BK-01",
      "operator": "kpn",
      "direction": "BK",
      "departure": "19:00"
    }
  ]
}
```

### Why this structure

- **`route.segments[]`** — arbitrary corridor topology; not locked to 5 stations.
- **`physics`** — all physical constants in one place; change speed or range via data.
- **`stations[].chargers`** — per-station charger count; increase without touching engine.
- **`operators[]`** — new operators are strings; no enum to update.
- **`weights`** — three floats; all rule weights in one obvious place.
- **`buses[].direction`** — a string code (`"BK"` / `"KB"`); extendable to multi-route.
- **`buses[].departure`** — `"HH:MM"` string; clean, human-readable.

### Output types (computed by the engine)

```
BusSchedule
  bus: Bus
  charging_stops: [ChargingStop]   ← one per station where the bus charges
  departure_min, arrival_min
  total_wait_min, total_trip_min

ChargingStop
  station_id, arrival_min, wait_min, charge_start_min, charge_end_min, km_at_arrival

StationSchedule
  station_id
  slots: [StationSlot]             ← one per bus that charged here, sorted by time

ScheduleResult
  bus_schedules: [BusSchedule]
  station_schedules: [StationSchedule]
  scenario_name, weights
```

---

## 3. Anticipated Future Changes

This section documents every change that was anticipated during design and how
the current data structure and engine handle it **without code changes**.

### 3.1 Adding a new intermediate station

**Data change only:**
Add a segment entry to `route.segments` and an entry to `stations[]`.
The engine enumerates valid charging plans from the route segments — it has
no hardcoded station list. The UI renders whatever stations come back from
the schedule.

```json
// Before: Bengaluru → A → B → C → D → Kochi
// After:  Bengaluru → A → B → E → C → D → Kochi
"segments": [
  ...,
  {"from": "B", "to": "E", "distance_km": 50},
  {"from": "E", "to": "C", "distance_km": 50},
  ...
],
"stations": [..., {"id": "E", "chargers": 1}]
```

### 3.2 Multiple chargers at a station

**Data change only:**
Set `chargers` > 1 for that station. The engine models each as an independent
FIFO queue. Buses are dispatched to the soonest-available slot.

```json
{"id": "B", "chargers": 3}
```

### 3.3 Adding a new bus operator

**Data change only:**
Add the operator name to `operators[]` and assign buses to it. The engine
and UI treat operators as strings — no enum or conditional logic to update.

```json
"operators": ["kpn", "freshbus", "flixbus", "nuegobus"]
```

### 3.4 More buses (50, 200, 1000)

**No change.** Buses are a list — add entries. The O(B² × P) algorithm
is adequate for hundreds of buses; if it becomes a bottleneck, the greedy
assignment loop can be parallelised without touching the rule interface.

### 3.5 Changing segment distances

**Data change only:**
Edit `distance_km` values in `route.segments`. The engine recomputes all
valid plans based on those distances vs `battery_range_km`.

### 3.6 Changing battery range or charge duration

**Data change only:**
Edit `physics.battery_range_km` or `physics.charge_duration_min`.
All plan validity checks and timeline calculations use these values at runtime.

### 3.7 Different bus speeds (per-bus or per-segment)

**Minor data + model extension (no engine rewrite):**
Add `speed_kmh` to individual segment objects or bus objects. The engine's
`travel_time_min()` already delegates to `PhysicsConfig` — add an override
path there. Rules and the queue simulation do not need to change.

### 3.8 Priority buses (VIP / emergency)

**One new rule + minor model extension:**
Add `is_priority: bool = False` to the `Bus` dataclass and a `PriorityBusRule`
in `rules.py`. Non-priority buses get a cost penalty when they would block a
priority bus. Engine unchanged; see README for the exact code.

### 3.9 Time-of-day electricity pricing

**One new rule:**
Electricity price would come from either:
- A `time_pricing` block in the scenario JSON (a list of `{start, end, cost_per_kwh}`),
- Or a simple per-station pricing field.
A `ElectricityCostRule` would score plans that charge during expensive windows
more heavily. The engine calls it automatically once registered.

### 3.10 Driver shift constraints

**One new rule + bus model field:**
Add `driver_shift_end_min` to `Bus`. A `DriverShiftRule` penalises plans
where the arrival time exceeds the shift end. No engine changes.

### 3.11 Multiple routes sharing stations

**Data change only:**
Routes are defined by their `segments` list. A station like "B" can appear
in multiple scenario files with different surrounding segments. The engine
treats each scenario independently. For multi-route scheduling in a single
run, add a `route_id` to buses and partition queues by route — a modest engine
extension that doesn't touch the rule interface.

### 3.12 Real-time bus delays (buses running late)

**Engine extension — arrival time injection:**
Add an optional `actual_departure_min` to `Bus` (overrides `departure_min`
when present). The engine already uses `departure_min` as the base time —
substituting it is a one-line change. Rules don't need to know.

### 3.13 Partial charges (charge to X%, not 100%)

**Physics model extension:**
Add `charge_to_pct: float = 1.0` to each charging stop or to physics config.
Update `charge_duration_min` to scale linearly with target percentage.
Rules remain unchanged.

### 3.14 Changing or adding soft weights

**Data change only:**
Add a new key to the `weights` block in the scenario JSON and a matching field
to the `Weights` dataclass (with a default so existing scenarios still load).
Any rule that references the new weight key reads it automatically.

### 3.15 A/B testing different rule sets

**Injection at engine construction:**
`SchedulerEngine(rules=[...])` accepts a custom rule list. Pass different
subsets of rules to compare outcomes without modifying any rule or scenario code.

---

## 4. How to Change a Weight

All weights live in `data/scenarioN.json` under the `weights` key:

```json
"weights": {
  "individual": 1.0,
  "operator":   1.0,
  "overall":    1.0
}
```

**Example — prioritise operator fairness in Scenario 4:**

```json
"weights": {
  "individual": 1.0,
  "operator":   3.0,
  "overall":    0.5
}
```

Save the file, reload the scenario in the app. No code changes, no restart required.

---

## 5. How to Add a New Rule

### Step 1 — Write the rule class (`scheduler/rules.py`)

```python
class ElectricityCostRule(Rule):
    """
    Penalises charging at stations during expensive electricity windows.
    Requires a 'time_pricing' block in the scenario JSON.
    """

    @property
    def weight_key(self) -> str:
        return "electricity"   # add this to Weights if needed

    def score(
        self,
        candidate: BusSchedule,
        all_schedules: list[BusSchedule],
        weights: Weights,
    ) -> float:
        cost = 0.0
        for stop in candidate.charging_stops:
            # Example: peak pricing 18:00–22:00 (1080–1320 min)
            if 1080 <= stop.charge_start_min <= 1320:
                cost += 50.0  # flat penalty per peak-window charge
        return cost * getattr(weights, "electricity", 1.0)
```

### Step 2 — Register it

```python
# In RuleRegistry._REGISTRY:
_REGISTRY = {
    "individual_wait":    IndividualWaitRule,
    "operator_fairness":  OperatorFairnessRule,
    "overall_efficiency": OverallEfficiencyRule,
    "electricity_cost":   ElectricityCostRule,   # ← one line
}
```

That's everything. The engine iterates all registered rules automatically.

---

## 6. Assumptions Made

| Assumption | Rationale |
|-----------|-----------|
| All buses depart with a full charge (240 km) | Stated in spec: "every bus leaves its origin with a 240 km range" |
| Charging always fills to 100% | Stated in spec: "Charging always fills the battery back to full" |
| Buses travel at a constant 60 km/h — no traffic | Stated in spec: "All buses travel at the same speed" |
| The engine schedules buses in departure-time order | Earliest-departure buses have the highest claim on charger slots; this is operationally natural |
| When two plans have equal cost, the plan with fewer charges is preferred (fewer stops = faster trip) | Plans are enumerated in ascending cardinality order; ties fall to simpler plans |
| Endpoints (Bengaluru and Kochi) are not modelled as scheduling stations | Stated in spec: "Only A, B, C, and D are scheduling charging stations" |
| A bus must charge at least twice (trip is 540 km > 240 km range) | Derived from battery physics: 540/240 > 1 |
| A bus cannot charge at A then D without charging at B or C (A→D = 340 km > 240 km) | Derived from distances: A→B→C→D = 340 km total; must stop at B or C in between |
| Charger queues are FIFO within same arrival minute | Natural fairness assumption; no tiebreaking complexity needed |
| The operator fairness rule has no reference mean for the first bus of each operator | Score = 0 for the first bus (no peer data yet); this is a deliberate choice to avoid penalising early buses unfairly |

---

## 7. Code Quality Principles

- **No hardcoded constants anywhere in engine code** — every physical constant,
  route segment, weight, and station configuration flows from the scenario JSON.
- **No TODOs, no stubs** — every function is fully implemented.
- **Single responsibility** — `models.py` is pure data, `rules.py` is pure scoring,
  `engine.py` is pure orchestration. No cross-cutting concerns.
- **Typed throughout** — all public functions have type annotations; dataclasses are
  used for all structured data.
- **Tested by the 5 scenarios** — each scenario exercises a different stress pattern
  on the scheduler (even load, bunched, asymmetric, operator-dominated, worst-case).
