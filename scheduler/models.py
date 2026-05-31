"""
scheduler/models.py
-------------------
All data model types used across the scheduler system.
Everything is a dataclass — immutable where possible, serializable to/from JSON.

Design philosophy:
  - No hardcoded route constants, distances, or physics values.
  - Every tuneable value (speed, battery range, charge time, weights) lives in the
    scenario file and flows through these types.
  - Adding a new field to the world (e.g. bus priority level, charger power rating,
    electricity cost per station) requires only a new field here and in the JSON —
    no engine rewrites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Route / World configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Segment:
    """One road segment between two consecutive stations."""
    from_station: str
    to_station: str
    distance_km: float


@dataclass(frozen=True)
class Route:
    """
    Ordered sequence of segments defining the full corridor.

    The list of unique station names (in order) is derived from segments.
    Both directions share the same route object — the bus direction determines
    which end it starts from.
    """
    segments: List[Segment]

    @property
    def stations(self) -> List[str]:
        """All station names in order from origin to destination."""
        result = [self.segments[0].from_station]
        for seg in self.segments:
            result.append(seg.to_station)
        return result

    def distance_between(self, from_station: str, to_station: str) -> float:
        """
        Cumulative distance between any two stations.

        Direction-agnostic: works for both BK and KB buses.
        Distances are symmetric (road segments have the same length both ways).
        """
        stations = self.stations
        start_idx = stations.index(from_station)
        end_idx = stations.index(to_station)
        # Swap so we always slice forward through the canonical segment list
        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx
        if start_idx == end_idx:
            return 0.0
        total = 0.0
        for seg in self.segments[start_idx:end_idx]:
            total += seg.distance_km
        return total

    def segment_distance(self, from_station: str, to_station: str) -> float:
        """Distance of a single segment (must be adjacent)."""
        for seg in self.segments:
            if seg.from_station == from_station and seg.to_station == to_station:
                return seg.distance_km
        raise ValueError(f"No direct segment: {from_station} → {to_station}")


@dataclass(frozen=True)
class StationConfig:
    """
    A scheduling station along the route.

    `chargers` is the number of parallel chargers at this station.
    Increasing it from 1 to N requires only a data change — the engine
    models each charger as an independent slot.
    """
    id: str
    chargers: int = 1
    # Future extensibility fields (ignored by engine if absent):
    # electricity_cost_per_kwh: Optional[float] = None
    # priority_lane: bool = False


@dataclass(frozen=True)
class PhysicsConfig:
    """
    Physical constants for this scenario.

    All values are data-driven — changing battery range, charge duration,
    or bus speed requires only a JSON edit.
    """
    battery_range_km: float    # Maximum range on a full charge
    charge_duration_min: float  # Fixed charge duration (always to full)
    speed_kmh: float            # Uniform bus speed (no traffic model)

    def travel_time_min(self, distance_km: float) -> float:
        """Minutes to travel a given distance at uniform speed."""
        return (distance_km / self.speed_kmh) * 60.0


@dataclass(frozen=True)
class Weights:
    """
    Tunable weights for the three soft optimisation objectives.

    Each weight scales its corresponding rule's contribution to the
    total cost used for plan selection and conflict resolution.

    Changing a weight: edit the `weights` block in the scenario JSON.
    """
    individual: float = 1.0   # Penalise long wait times for individual buses
    operator: float = 1.0     # Penalise uneven waits across an operator's fleet
    overall: float = 1.0      # Penalise total elapsed time across the whole network


# ---------------------------------------------------------------------------
# Bus / Input configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bus:
    """
    A single bus scheduled for a trip.

    Direction is encoded as a two-letter string matching the JSON:
      "BK" = Bengaluru → Kochi
      "KB" = Kochi → Bengaluru

    Adding a new operator requires only adding the string to `operators[]`
    in the scenario JSON — the engine treats it identically.
    """
    id: str
    operator: str
    direction: str       # "BK" or "KB"
    departure_min: float  # Minutes since midnight

    @property
    def origin(self) -> str:
        """Name of the station this bus departs from."""
        return "Bengaluru" if self.direction == "BK" else "Kochi"

    @property
    def destination(self) -> str:
        """Name of the terminal station this bus is heading to."""
        return "Kochi" if self.direction == "BK" else "Bengaluru"

    def stations_in_order(self, route: Route) -> List[str]:
        """
        All route stations in the order this bus visits them,
        including origin and destination.
        """
        stations = route.stations
        if self.direction == "KB":
            stations = list(reversed(stations))
        return stations

    def intermediate_stations(self, route: Route) -> List[str]:
        """Scheduling stations only (excludes origin and destination endpoints)."""
        all_stations = self.stations_in_order(route)
        return all_stations[1:-1]  # strip origin and destination


# ---------------------------------------------------------------------------
# Scheduler output types
# ---------------------------------------------------------------------------

@dataclass
class ChargingStop:
    """
    A single charging event in a bus's final schedule.

    All times are minutes since midnight.
    `wait_min` is how long the bus had to wait for the charger to become free.
    """
    station_id: str
    arrival_min: float        # When the bus physically arrives at the station
    wait_min: float           # Time spent waiting for the charger
    charge_start_min: float   # When charging actually begins
    charge_end_min: float     # When the bus departs the station
    km_at_arrival: float      # Distance covered since last charge (or departure)


@dataclass
class BusSchedule:
    """
    Complete resolved schedule for one bus.

    `charging_stops` lists every station where the bus charges, in order.
    `total_wait_min` is the sum of all waits across all stops.
    `arrival_min` is when the bus reaches its final destination.
    """
    bus: Bus
    charging_stops: List[ChargingStop]
    departure_min: float
    arrival_min: float

    @property
    def total_wait_min(self) -> float:
        return sum(stop.wait_min for stop in self.charging_stops)

    @property
    def total_trip_min(self) -> float:
        return self.arrival_min - self.departure_min

    def stations_used(self) -> List[str]:
        return [stop.station_id for stop in self.charging_stops]


@dataclass
class StationSlot:
    """
    One charger usage record at a station — used to build per-station views.
    """
    bus_id: str
    operator: str
    direction: str
    charge_start_min: float
    charge_end_min: float
    wait_min: float


@dataclass
class StationSchedule:
    """
    All charging events at a single station, sorted by charge start time.
    """
    station_id: str
    slots: List[StationSlot] = field(default_factory=list)

    def add_slot(self, slot: StationSlot) -> None:
        self.slots.append(slot)
        self.slots.sort(key=lambda s: s.charge_start_min)


@dataclass
class ScheduleResult:
    """
    Full output of the scheduler for one scenario run.

    Contains per-bus schedules and per-station views.
    This is the single object passed to the UI layer.
    """
    bus_schedules: List[BusSchedule]
    station_schedules: List[StationSchedule]
    scenario_name: str
    weights: Weights

    def get_station_schedule(self, station_id: str) -> Optional[StationSchedule]:
        for ss in self.station_schedules:
            if ss.station_id == station_id:
                return ss
        return None


# ---------------------------------------------------------------------------
# Full scenario configuration (loaded from JSON)
# ---------------------------------------------------------------------------

@dataclass
class ScenarioConfig:
    """
    Complete configuration for one scenario, loaded from a JSON file.

    This is the single source of truth for everything the engine and UI need.
    No values are hardcoded in the engine — they all come from here.
    """
    scenario_id: str
    name: str
    description: str
    route: Route
    physics: PhysicsConfig
    station_configs: List[StationConfig]
    operators: List[str]
    weights: Weights
    buses: List[Bus]

    def get_station_config(self, station_id: str) -> Optional[StationConfig]:
        for sc in self.station_configs:
            if sc.id == station_id:
                return sc
        return None

    def scheduling_stations(self, bus: Bus) -> List[str]:
        """
        Returns the scheduling stations (not endpoints) in the order
        this specific bus encounters them.
        """
        intermediate = bus.intermediate_stations(self.route)
        # Only include stations that appear in station_configs (schedulable)
        schedulable_ids = {sc.id for sc in self.station_configs}
        return [s for s in intermediate if s in schedulable_ids]
