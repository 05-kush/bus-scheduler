"""
scheduler/rules.py
------------------
Rule engine: abstract base class + all concrete soft-rule implementations.

Design contract:
  - Every rule is a class with a `score(bus_schedule, all_schedules, weights)` method.
  - The engine knows nothing about rule internals — it just calls `score()` and
    multiplies by the rule's weight factor.
  - Adding a new rule requires ONLY:
      1. Creating a new class here that inherits from `Rule`.
      2. Registering it in `RuleRegistry`.
      3. Referencing it by name in the scenario JSON (optional — defaults work too).
  - No engine changes are ever required.

Current rules
─────────────
  IndividualWaitRule   → penalises total wait time for a single bus
  OperatorFairnessRule → penalises variance in wait times across an operator's fleet
  OverallEfficiencyRule → penalises the total cumulative wait across all buses
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Type

from scheduler.models import BusSchedule, Weights


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class Rule(ABC):
    """
    Abstract base for all scheduling soft-rules.

    A rule produces a non-negative float score for a *candidate* bus schedule.
    Lower score = better outcome for this rule.

    The engine combines scores from all active rules into a single cost:
        total_cost = Σ weight_i * rule_i.score(...)

    Rules are evaluated against *in-progress* schedules — they must handle
    the case where `all_schedules` is a partial list (not all buses resolved yet).
    """

    @abstractmethod
    def score(
        self,
        candidate: BusSchedule,
        all_schedules: List[BusSchedule],
        weights: Weights,
    ) -> float:
        """
        Return a non-negative cost score for `candidate`.

        Args:
            candidate:     The bus schedule being evaluated.
            all_schedules: Already-resolved schedules (may be partial).
            weights:       The weight configuration from the scenario.

        Returns:
            A float ≥ 0. Lower is better.
        """
        ...

    @property
    @abstractmethod
    def weight_key(self) -> str:
        """
        Which weight from `Weights` this rule uses.
        Must match an attribute name on the `Weights` dataclass.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete rule: Individual wait penalty
# ---------------------------------------------------------------------------

class IndividualWaitRule(Rule):
    """
    Penalises a bus for accumulating long waits at chargers.

    Score = total_wait_min for this bus.

    This keeps individual passengers from experiencing unreasonable delays.
    The `individual` weight controls how strongly this is enforced relative
    to operator and overall fairness.
    """

    @property
    def weight_key(self) -> str:
        return "individual"

    def score(
        self,
        candidate: BusSchedule,
        all_schedules: List[BusSchedule],
        weights: Weights,
    ) -> float:
        return candidate.total_wait_min * weights.individual


# ---------------------------------------------------------------------------
# Concrete rule: Operator fleet fairness
# ---------------------------------------------------------------------------

class OperatorFairnessRule(Rule):
    """
    Penalises plans that cause one bus's wait to diverge far from the mean
    wait time of its operator's already-scheduled fleet.

    Score = abs(bus_wait - operator_mean_wait)

    When the `operator` weight is high (e.g. Scenario 4), the scheduler
    strongly prefers plans that keep an operator's fleet wait times uniform.
    When it is low, the scheduler is more indifferent to per-operator equity.

    If no other buses from the same operator are scheduled yet, score = 0
    (no reference point available — don't penalise unfairly).
    """

    @property
    def weight_key(self) -> str:
        return "operator"

    def score(
        self,
        candidate: BusSchedule,
        all_schedules: List[BusSchedule],
        weights: Weights,
    ) -> float:
        operator = candidate.bus.operator
        peer_waits = [
            s.total_wait_min
            for s in all_schedules
            if s.bus.operator == operator
        ]
        if not peer_waits:
            return 0.0
        mean_peer_wait = sum(peer_waits) / len(peer_waits)
        deviation = abs(candidate.total_wait_min - mean_peer_wait)
        return deviation * weights.operator


# ---------------------------------------------------------------------------
# Concrete rule: Overall network efficiency
# ---------------------------------------------------------------------------

class OverallEfficiencyRule(Rule):
    """
    Penalises plans that add to the total cumulative delay across the network.

    Score = total_wait_min for this bus (contributes to the network total).

    This rule is similar to IndividualWaitRule in its per-bus measurement,
    but semantically it represents the system-wide objective: minimise total
    passenger-minutes lost to charging queues across all buses.

    The `overall` weight lets operators tune how much system-level efficiency
    matters compared to individual and per-operator fairness.
    """

    @property
    def weight_key(self) -> str:
        return "overall"

    def score(
        self,
        candidate: BusSchedule,
        all_schedules: List[BusSchedule],
        weights: Weights,
    ) -> float:
        return candidate.total_wait_min * weights.overall


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

class RuleRegistry:
    """
    Central registry mapping rule names to their classes.

    Usage:
        registry = RuleRegistry()
        rule = registry.get("individual_wait")()

    Adding a new rule:
        1. Define your class (inherits from Rule) above.
        2. Add it to _REGISTRY below — that's it.
    """

    _REGISTRY: Dict[str, Type[Rule]] = {
        "individual_wait": IndividualWaitRule,
        "operator_fairness": OperatorFairnessRule,
        "overall_efficiency": OverallEfficiencyRule,
    }

    @classmethod
    def get(cls, name: str) -> Type[Rule]:
        if name not in cls._REGISTRY:
            raise KeyError(
                f"Unknown rule '{name}'. "
                f"Available rules: {list(cls._REGISTRY.keys())}"
            )
        return cls._REGISTRY[name]

    @classmethod
    def all_rules(cls) -> List[Rule]:
        """Return one instance of every registered rule."""
        return [rule_cls() for rule_cls in cls._REGISTRY.values()]

    @classmethod
    def register(cls, name: str, rule_cls: Type[Rule]) -> None:
        """
        Dynamically register a new rule at runtime.
        Useful for plugin-style extensions without modifying this file.
        """
        cls._REGISTRY[name] = rule_cls


# ---------------------------------------------------------------------------
# Cost calculator (used by the engine)
# ---------------------------------------------------------------------------

def compute_total_cost(
    candidate: BusSchedule,
    all_schedules: List[BusSchedule],
    weights: Weights,
    rules: Optional[List[Rule]] = None,
) -> float:
    """
    Sum the weighted scores of all active rules for a candidate schedule.

    If `rules` is None, uses all registered rules.
    The engine calls this to rank candidate charging plans.
    """
    active_rules = rules if rules is not None else RuleRegistry.all_rules()
    return sum(rule.score(candidate, all_schedules, weights) for rule in active_rules)
