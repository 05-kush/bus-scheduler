"""
scheduler/engine.py
-------------------
The scheduling engine: loads scenario configs and produces full schedules.

Algorithm
─────────
For each bus (sorted by departure time):

  1. Enumerate all valid charging plans.
     A plan is an ordered subset of intermediate stations where:
       - No leg (start→first charge, charge→charge, last charge→end) exceeds
         battery_range_km.
       - Stations are visited in the bus's travel order (no backtracking).

  2. For each valid plan, build a *candidate* BusSchedule by simulating
     the bus's timeline assuming it charges at those stations.
     At each station the bus waits behind any bus already in the charger queue.

  3. Score each candidate using the rule engine (weighted sum).

  4. Select the lowest-cost plan.

  5. Commit the selected plan:
     - Register the bus in each station's charger queue.
     - Record the BusSchedule for use by subsequent buses' rule scoring.

Charger queue
─────────────
Each station maintains one FIFO queue per charger slot. A bus joins the queue
at its arrival time. If the slot is free, it charges immediately; otherwise it
waits until the current occupant finishes.

This is O(B² * P) where B = buses and P = valid plans per bus. For the problem
sizes described (≤20 buses, ≤4 stations, ≤2^4 plans) this is trivially fast.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from scheduler.models import (
    Bus,
    BusSchedule,
    ChargingStop,
    PhysicsConfig,
    Route,
    ScenarioConfig,
    ScheduleResult,
    Segment,
    StationConfig,
    StationSchedule,
    StationSlot,
    Weights,
)
from scheduler.rules import Rule, RuleRegistry, compute_total_cost


# ---------------------------------------------------------------------------
# Scenario loader
# ---------------------------------------------------------------------------

def _parse_time(t: str) -> float:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _minutes_to_hhmm(minutes: float) -> str:
    """Convert minutes since midnight to 'HH:MM' string."""
    total = int(round(minutes))
    return f"{total // 60:02d}:{total % 60:02d}"


def load_scenario(path: Path) -> ScenarioConfig:
    """
    Parse a scenario JSON file into a fully-typed ScenarioConfig.

    The JSON schema is documented in ARCHITECTURE.md. All physical constants,
    route geometry, weights, and bus data come from the file — nothing is
    hardcoded in the engine.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Build route
    segments = [
        Segment(
            from_station=s["from"],
            to_station=s["to"],
            distance_km=s["distance_km"],
        )
        for s in data["route"]["segments"]
    ]
    route = Route(segments=segments)

    # Physics
    phys = data["physics"]
    physics = PhysicsConfig(
        battery_range_km=phys["battery_range_km"],
        charge_duration_min=phys["charge_duration_min"],
        speed_kmh=phys["speed_kmh"],
    )

    # Stations (only intermediate schedulable stations)
    station_configs = [
        StationConfig(
            id=sc["id"],
            chargers=sc.get("chargers", 1),
        )
        for sc in data["stations"]
    ]

    # Weights
    w = data.get("weights", {})
    weights = Weights(
        individual=w.get("individual", 1.0),
        operator=w.get("operator", 1.0),
        overall=w.get("overall", 1.0),
    )

    # Operators
    operators = data.get("operators", [])

    # Buses
    buses = [
        Bus(
            id=b["id"],
            operator=b["operator"],
            direction=b["direction"],
            departure_min=_parse_time(b["departure"]),
        )
        for b in data["buses"]
    ]

    return ScenarioConfig(
        scenario_id=data["scenario_id"],
        name=data["name"],
        description=data["description"],
        route=route,
        physics=physics,
        station_configs=station_configs,
        operators=operators,
        weights=weights,
        buses=buses,
    )


# ---------------------------------------------------------------------------
# Charger queue state
# ---------------------------------------------------------------------------

class ChargerQueue:
    """
    Models the queue of one charger slot at one station.

    Tracks when the charger will next become free. When a bus arrives:
      - If charger is free → charge starts immediately.
      - If charger is busy → bus waits; charge starts when current job ends.
    """

    def __init__(self) -> None:
        self._free_at: float = 0.0  # Minutes since midnight

    def request(self, arrival_min: float, charge_duration_min: float) -> Tuple[float, float]:
        """
        Request the charger at `arrival_min`. Returns (wait_min, charge_end_min).
        Updates internal state so the next caller sees the correct free time.
        """
        charge_start = max(self._free_at, arrival_min)
        wait = charge_start - arrival_min
        charge_end = charge_start + charge_duration_min
        self._free_at = charge_end
        return wait, charge_end

    def peek_wait(self, arrival_min: float) -> float:
        """Check the wait time without committing — used for plan scoring."""
        return max(0.0, self._free_at - arrival_min)

    @property
    def free_at(self) -> float:
        return self._free_at

    def clone(self) -> "ChargerQueue":
        """Return a copy of this queue state for simulation."""
        q = ChargerQueue()
        q._free_at = self._free_at
        return q


class StationQueues:
    """
    All charger queues for all scheduling stations in a scenario.
    Supports both committed state (for scheduling) and snapshot/restore
    for candidate plan evaluation.
    """

    def __init__(self, station_configs: List[StationConfig]) -> None:
        # station_id → list of ChargerQueue (one per charger slot)
        self._queues: Dict[str, List[ChargerQueue]] = {
            sc.id: [ChargerQueue() for _ in range(sc.chargers)]
            for sc in station_configs
        }

    def next_available_slot(
        self, station_id: str, arrival_min: float
    ) -> Tuple[int, float]:
        """
        Return (slot_index, free_at) for the charger slot that will be
        available soonest at or after `arrival_min`.
        """
        slots = self._queues[station_id]
        # Prefer a slot that's already free; otherwise pick the one free soonest
        best_idx = min(range(len(slots)), key=lambda i: slots[i].free_at)
        return best_idx, slots[best_idx].free_at

    def request(
        self, station_id: str, arrival_min: float, charge_duration_min: float
    ) -> Tuple[float, float]:
        """
        Commit a charge request at the best available slot.
        Returns (wait_min, charge_end_min).
        """
        idx, _ = self.next_available_slot(station_id, arrival_min)
        return self._queues[station_id][idx].request(arrival_min, charge_duration_min)

    def simulate_request(
        self, station_id: str, arrival_min: float, charge_duration_min: float,
        clone_state: Dict[str, List[ChargerQueue]]
    ) -> Tuple[float, float]:
        """
        Simulate a charge request against a cloned state (non-destructive).
        Returns (wait_min, charge_end_min). Modifies clone_state in place.
        """
        slots = clone_state[station_id]
        best_idx = min(range(len(slots)), key=lambda i: slots[i].free_at)
        return slots[best_idx].request(arrival_min, charge_duration_min)

    def clone_state(self) -> Dict[str, List[ChargerQueue]]:
        """Snapshot current queue state for candidate simulation."""
        return {
            station_id: [q.clone() for q in queues]
            for station_id, queues in self._queues.items()
        }


# ---------------------------------------------------------------------------
# Charging plan enumeration
# ---------------------------------------------------------------------------

def enumerate_valid_plans(
    bus: Bus,
    scenario: ScenarioConfig,
) -> List[List[str]]:
    """
    Return all valid charging plans for a bus.

    A plan is an ordered list of intermediate stations to charge at.
    Validity requires that no leg of the journey exceeds battery_range_km.

    Legs are:
      origin → first_charge : distance must be ≤ battery_range_km
      charge_i → charge_{i+1}: distance must be ≤ battery_range_km
      last_charge → destination: distance must be ≤ battery_range_km
    """
    route = scenario.route
    physics = scenario.physics
    schedulable = scenario.scheduling_stations(bus)
    all_stations = bus.stations_in_order(route)
    origin = all_stations[0]
    destination = all_stations[-1]

    # Build a lookup for the bus's direction-specific station order
    station_index = {s: i for i, s in enumerate(all_stations)}

    valid_plans: List[List[str]] = []

    # Try all non-empty subsets of schedulable stations
    for r in range(1, len(schedulable) + 1):
        for combo in itertools.combinations(schedulable, r):
            plan = list(combo)
            # Stations must be in route order for this bus
            plan.sort(key=lambda s: station_index[s])

            # Check every leg
            legs = [origin] + plan + [destination]
            feasible = True
            for i in range(len(legs) - 1):
                dist = route.distance_between(legs[i], legs[i + 1])
                if dist > physics.battery_range_km:
                    feasible = False
                    break

            if feasible:
                valid_plans.append(plan)

    return valid_plans


# ---------------------------------------------------------------------------
# Candidate schedule builder
# ---------------------------------------------------------------------------

def build_candidate_schedule(
    bus: Bus,
    plan: List[str],
    scenario: ScenarioConfig,
    queue_state: Dict[str, List[ChargerQueue]],
) -> BusSchedule:
    """
    Simulate a bus following `plan` against a (possibly cloned) queue state.

    Returns a BusSchedule with realistic times including queuing waits.
    This is used both for scoring (against a clone) and for commitment.
    """
    route = scenario.route
    physics = scenario.physics
    all_stations = bus.stations_in_order(route)

    current_time = bus.departure_min
    current_station = all_stations[0]
    charging_stops: List[ChargingStop] = []
    km_since_last_charge = 0.0

    for charge_station in plan:
        # Travel to this charging station
        dist = route.distance_between(current_station, charge_station)
        travel_time = physics.travel_time_min(dist)
        arrival_min = current_time + travel_time
        km_since_last_charge += dist

        # Get the charger (from the provided state — may be cloned or live)
        slots = queue_state[charge_station]
        best_idx = min(range(len(slots)), key=lambda i: slots[i].free_at)
        wait_min, charge_end_min = slots[best_idx].request(
            arrival_min, physics.charge_duration_min
        )

        charging_stops.append(
            ChargingStop(
                station_id=charge_station,
                arrival_min=arrival_min,
                wait_min=wait_min,
                charge_start_min=arrival_min + wait_min,
                charge_end_min=charge_end_min,
                km_at_arrival=km_since_last_charge,
            )
        )

        current_time = charge_end_min
        current_station = charge_station
        km_since_last_charge = 0.0  # Battery reset to full after charge

    # Travel to destination
    dist_to_end = route.distance_between(current_station, all_stations[-1])
    travel_time_to_end = physics.travel_time_min(dist_to_end)
    arrival_at_destination = current_time + travel_time_to_end

    return BusSchedule(
        bus=bus,
        charging_stops=charging_stops,
        departure_min=bus.departure_min,
        arrival_min=arrival_at_destination,
    )


# ---------------------------------------------------------------------------
# Main scheduler engine
# ---------------------------------------------------------------------------

class SchedulerEngine:
    """
    The top-level scheduler.

    Usage:
        engine = SchedulerEngine()
        scenario = engine.load_scenario(Path("data/scenario1.json"))
        result = engine.schedule(scenario)

    The engine is stateless between runs — call `schedule()` as many times
    as needed with different scenarios.
    """

    def __init__(self, rules: Optional[List[Rule]] = None) -> None:
        """
        Args:
            rules: List of Rule instances to use. Defaults to all registered rules.
                   Inject custom rules here without touching the registry.
        """
        self._rules = rules if rules is not None else RuleRegistry.all_rules()

    def load_scenario(self, path: Path) -> ScenarioConfig:
        """Load and parse a scenario JSON file."""
        return load_scenario(path)

    def schedule(self, scenario: ScenarioConfig) -> ScheduleResult:
        """
        Run the scheduler for the given scenario and return a ScheduleResult.

        Buses are processed in departure-time order (earliest first).
        For each bus, all valid charging plans are evaluated and the
        lowest-cost plan (per the active rule set and scenario weights) is chosen.
        """
        # Initialise live charger queues
        queues = StationQueues(scenario.station_configs)

        # Sort buses by departure time (ascending)
        sorted_buses = sorted(scenario.buses, key=lambda b: b.departure_min)

        committed_schedules: List[BusSchedule] = []

        for bus in sorted_buses:
            valid_plans = enumerate_valid_plans(bus, scenario)

            if not valid_plans:
                raise ValueError(
                    f"Bus {bus.id} has no valid charging plan. "
                    "Check battery_range_km vs route distances."
                )

            best_schedule: Optional[BusSchedule] = None
            best_cost = float("inf")

            for plan in valid_plans:
                # Evaluate this plan against a cloned queue state (non-destructive)
                cloned_state = queues.clone_state()
                candidate = build_candidate_schedule(bus, plan, scenario, cloned_state)
                cost = compute_total_cost(
                    candidate, committed_schedules, scenario.weights, self._rules
                )
                if cost < best_cost:
                    best_cost = cost
                    best_schedule = candidate

            # Commit the best plan to the live queues
            assert best_schedule is not None
            live_state = queues.clone_state()  # will be mutated by commit
            committed = build_candidate_schedule(
                bus, best_schedule.stations_used(), scenario, live_state
            )
            # Update live queues from the committed simulation
            # We replay against the actual live queues to ensure consistency
            committed_live = self._commit_schedule(bus, best_schedule.stations_used(), scenario, queues)
            committed_schedules.append(committed_live)

        # Build per-station views
        station_schedules = self._build_station_schedules(
            committed_schedules, scenario
        )

        return ScheduleResult(
            bus_schedules=committed_schedules,
            station_schedules=station_schedules,
            scenario_name=scenario.name,
            weights=scenario.weights,
        )

    def _commit_schedule(
        self,
        bus: Bus,
        plan: List[str],
        scenario: ScenarioConfig,
        queues: StationQueues,
    ) -> BusSchedule:
        """
        Build a BusSchedule by committing charge requests to live queues.
        This mutates the queue state (intentionally).
        """
        route = scenario.route
        physics = scenario.physics
        all_stations = bus.stations_in_order(route)

        current_time = bus.departure_min
        current_station = all_stations[0]
        charging_stops: List[ChargingStop] = []
        km_since_last_charge = 0.0

        for charge_station in plan:
            dist = route.distance_between(current_station, charge_station)
            travel_time = physics.travel_time_min(dist)
            arrival_min = current_time + travel_time
            km_since_last_charge += dist

            wait_min, charge_end_min = queues.request(
                charge_station, arrival_min, physics.charge_duration_min
            )

            charging_stops.append(
                ChargingStop(
                    station_id=charge_station,
                    arrival_min=arrival_min,
                    wait_min=wait_min,
                    charge_start_min=arrival_min + wait_min,
                    charge_end_min=charge_end_min,
                    km_at_arrival=km_since_last_charge,
                )
            )

            current_time = charge_end_min
            current_station = charge_station
            km_since_last_charge = 0.0

        dist_to_end = route.distance_between(current_station, all_stations[-1])
        arrival_at_destination = current_time + physics.travel_time_min(dist_to_end)

        return BusSchedule(
            bus=bus,
            charging_stops=charging_stops,
            departure_min=bus.departure_min,
            arrival_min=arrival_at_destination,
        )

    def _build_station_schedules(
        self,
        bus_schedules: List[BusSchedule],
        scenario: ScenarioConfig,
    ) -> List[StationSchedule]:
        """Build per-station charge order views from committed bus schedules."""
        station_map: Dict[str, StationSchedule] = {
            sc.id: StationSchedule(station_id=sc.id)
            for sc in scenario.station_configs
        }

        for bs in bus_schedules:
            for stop in bs.charging_stops:
                if stop.station_id in station_map:
                    station_map[stop.station_id].add_slot(
                        StationSlot(
                            bus_id=bs.bus.id,
                            operator=bs.bus.operator,
                            direction=bs.bus.direction,
                            charge_start_min=stop.charge_start_min,
                            charge_end_min=stop.charge_end_min,
                            wait_min=stop.wait_min,
                        )
                    )

        return list(station_map.values())


# ---------------------------------------------------------------------------
# Convenience helpers for the UI layer
# ---------------------------------------------------------------------------

def minutes_to_hhmm(minutes: float) -> str:
    """Public alias for UI use."""
    return _minutes_to_hhmm(minutes)
