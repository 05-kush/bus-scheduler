# Bus Charging Scheduler

A production-quality electric bus charging scheduler built with Python and Streamlit.
Schedules charging stops for 20 electric buses along a fixed 540 km corridor
(Bengaluru → A → B → C → D → Kochi) using a rule-engine with tunable weights.

## Live Demo

🚀 Streamlit App:
[https://bus-scheduler.streamlit.app](https://bus-scheduler.streamlit.app/)

📂 GitHub Repository:
https://github.com/05-kush/bus-scheduler


---

## How to Run Locally

### Prerequisites

- Python 3.10 or higher
- `pip`

### Install & Run

```bash
# Clone the repo
git clone <your-repo-url>
cd bus_scheduler

# Create virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
.venv\Scripts\activate         # Windows PowerShell

# Install dependencies
pip install -r requirements.txt

# Run
streamlit run app.py
```

The app will open at `http://localhost:8501`. Select a scenario from the dropdown.

---

## How to Change a Weight

Weights live **entirely in the scenario JSON files** — no code changes required.

Open any `data/scenarioN.json` and edit the `weights` block:

```json
"weights": {
  "individual": 1.0,
  "operator":   2.0,
  "overall":    1.0
}
```

| Weight | Effect |
|--------|--------|
| `individual` | Penalises long wait times for individual buses. Higher = protect passengers. |
| `operator` | Penalises uneven wait times across an operator's fleet. Higher = fleet fairness. |
| `overall` | Penalises total cumulative delay across the network. Higher = system efficiency. |

**Example — favour operator fairness over individual wait:**

```json
"weights": {
  "individual": 0.5,
  "operator":   3.0,
  "overall":    1.0
}
```

Refresh the app and re-select the scenario — the new weights take effect immediately
(the result is cached; clear Streamlit's cache with the top-right menu if needed).

---

## How to Add a New Rule

Adding a rule requires **two changes** — one new class, one registry entry.
The engine, scenarios, and UI require zero modifications.

### Step 1 — Define the rule in `scheduler/rules.py`

```python
class PriorityBusRule(Rule):
    """
    Example: penalise plans where a priority bus waits behind non-priority buses.
    Priority flag would be carried in the Bus dataclass.
    """

    @property
    def weight_key(self) -> str:
        return "priority"   # matches a new field you'd add to Weights

    def score(
        self,
        candidate: BusSchedule,
        all_schedules: list[BusSchedule],
        weights: Weights,
    ) -> float:
        if not getattr(candidate.bus, "is_priority", False):
            return 0.0
        return candidate.total_wait_min * weights.priority
```

### Step 2 — Register it in `RuleRegistry._REGISTRY`

```python
_REGISTRY: dict[str, Type[Rule]] = {
    "individual_wait":   IndividualWaitRule,
    "operator_fairness": OperatorFairnessRule,
    "overall_efficiency": OverallEfficiencyRule,
    "priority_bus":      PriorityBusRule,   # ← add this line
}
```

That's it. The engine automatically calls all registered rules on every
candidate plan evaluation.

### Step 3 — Optionally extend the data model

If your rule needs a new bus attribute (e.g. `is_priority`), add it to:

1. `Bus` dataclass in `scheduler/models.py` (with a default value so existing
   scenarios still load without changes):
   ```python
   is_priority: bool = False
   ```

2. The bus loader in `scheduler/engine.py` (`load_scenario`) — read the field
   from JSON if present, default otherwise:
   ```python
   Bus(
       ...
       is_priority=b.get("is_priority", False),
   )
   ```

3. Add the field to any scenario JSON buses that need it:
   ```json
   {"id": "bus-BK-01", ..., "is_priority": true}
   ```

---

## How to Add a New Station

Edit the scenario JSON — **no code changes** required:

```json
"route": {
    "segments": [
      {"from": "Bengaluru", "to": "A",  "distance_km": 100},
      {"from": "A",         "to": "E",  "distance_km": 60},   // ← new station E
      {"from": "E",         "to": "B",  "distance_km": 60},   // ← segment split
      ...
    ]
},
"stations": [
    {"id": "A", "chargers": 1},
    {"id": "E", "chargers": 2},   // ← new scheduling station
    {"id": "B", "chargers": 1},
    ...
]
```

## How to Add More Chargers at a Station

```json
{"id": "B", "chargers": 2}
```

The engine models each charger as an independent queue slot automatically.

## How to Add a New Operator

Add the string to the `operators` list in the scenario JSON and assign buses to it:

```json
"operators": ["kpn", "freshbus", "flixbus", "nuegobus"],
"buses": [
    {"id": "bus-BK-11", "operator": "nuegobus", "direction": "BK", "departure": "22:00"}
]
```

No engine changes required.

---

## Deploying to Streamlit Community Cloud

1. Push this repository to a **public** GitHub repo.
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. Click **New app** → select your repo → set **Main file path** to `app.py`.
4. Click **Deploy**. Streamlit installs `requirements.txt` automatically.

The app will be live at `https://<your-app>.streamlit.app`.

---

## Project Structure

```
bus_scheduler/
├── app.py                  # Streamlit UI entry point
├── requirements.txt
├── README.md
├── ARCHITECTURE.md
├── scheduler/
│   ├── __init__.py
│   ├── models.py           # Dataclass types (immutable, typed)
│   ├── rules.py            # Rule engine — abstract base + concrete rules
│   └── engine.py           # Scheduler: plan enumeration, queue simulation, scoring
└── data/
    ├── scenario1.json      # Even spacing (baseline)
    ├── scenario2.json      # Bunched start (early contention)
    ├── scenario3.json      # Asymmetric load (10 vs 4 buses)
    ├── scenario4.json      # Operator-heavy KPN (operator weight = 2.0)
    └── scenario5.json      # Worst-case convergence (all 20 in 72 min)
```
