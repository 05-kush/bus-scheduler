"""
app.py
------
Bus Charging Scheduler — Streamlit UI

Entry point. Run with:
    streamlit run app.py

Layout:
  ① Scenario dropdown at the top
  ② Scenario input view — what's being fed in (buses, weights, route)
  ③ Per-bus timetable — full timeline for each bus
  ④ Per-station view — charger queue order for each of A, B, C, D
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List

import pandas as pd
import streamlit as st

from scheduler.engine import SchedulerEngine, minutes_to_hhmm
from scheduler.models import BusSchedule, ChargingStop, ScenarioConfig, ScheduleResult

# ────────────────────────────────────────────────────────────────────────────
# Page config
# ────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Bus Charging Scheduler",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ────────────────────────────────────────────────────────────────────────────
# CSS — premium dark theme
# ────────────────────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    /* ── Google Font ── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    /* ── Global ── */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    .stApp {
        background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #0d1117 100%);
        min-height: 100vh;
    }

    /* ── Hide Streamlit chrome ── */
    #MainMenu, footer, header { visibility: hidden; }

    /* ── Hero header ── */
    .hero {
        background: linear-gradient(135deg, #1a237e 0%, #283593 40%, #1565c0 80%, #0d47a1 100%);
        border-radius: 16px;
        padding: 2.5rem 3rem;
        margin-bottom: 2rem;
        position: relative;
        overflow: hidden;
        box-shadow: 0 8px 32px rgba(13, 71, 161, 0.4);
    }
    .hero::before {
        content: '';
        position: absolute;
        top: -50%;
        right: -10%;
        width: 400px;
        height: 400px;
        background: radial-gradient(circle, rgba(100,181,246,0.15) 0%, transparent 70%);
        pointer-events: none;
    }
    .hero-title {
        font-size: 2.4rem;
        font-weight: 700;
        color: #fff;
        margin: 0;
        letter-spacing: -0.5px;
    }
    .hero-subtitle {
        font-size: 1.05rem;
        color: rgba(255,255,255,0.72);
        margin-top: 0.4rem;
        font-weight: 400;
    }
    .hero-badge {
        display: inline-block;
        background: rgba(255,255,255,0.15);
        border: 1px solid rgba(255,255,255,0.25);
        border-radius: 20px;
        padding: 4px 14px;
        font-size: 0.78rem;
        color: rgba(255,255,255,0.9);
        margin-top: 1rem;
        letter-spacing: 0.3px;
    }

    /* ── Section cards ── */
    .section-card {
        background: rgba(22, 27, 34, 0.85);
        border: 1px solid rgba(48, 54, 61, 0.8);
        border-radius: 12px;
        padding: 1.5rem 2rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        backdrop-filter: blur(8px);
    }
    .section-title {
        font-size: 1.15rem;
        font-weight: 600;
        color: #e6edf3;
        margin-bottom: 1rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .section-title .icon {
        font-size: 1.25rem;
    }

    /* ── Scenario description box ── */
    .scenario-desc {
        background: linear-gradient(135deg, rgba(21, 101, 192, 0.18) 0%, rgba(26, 35, 126, 0.18) 100%);
        border: 1px solid rgba(66, 165, 245, 0.3);
        border-radius: 10px;
        padding: 1rem 1.4rem;
        color: #90caf9;
        font-size: 0.92rem;
        line-height: 1.6;
        margin-bottom: 1.2rem;
    }

    /* ── Metrics row ── */
    .metrics-row {
        display: flex;
        gap: 1rem;
        flex-wrap: wrap;
        margin-bottom: 1.2rem;
    }
    .metric-chip {
        background: rgba(30, 37, 46, 0.9);
        border: 1px solid rgba(48, 54, 61, 0.8);
        border-radius: 8px;
        padding: 0.6rem 1.1rem;
        display: flex;
        flex-direction: column;
        gap: 2px;
        min-width: 130px;
    }
    .metric-label {
        font-size: 0.72rem;
        color: #8b949e;
        text-transform: uppercase;
        letter-spacing: 0.6px;
        font-weight: 500;
    }
    .metric-value {
        font-size: 1.1rem;
        font-weight: 600;
        color: #e6edf3;
    }
    .metric-value.blue  { color: #64b5f6; }
    .metric-value.green { color: #81c784; }
    .metric-value.amber { color: #ffb74d; }
    .metric-value.purple{ color: #ce93d8; }

    /* ── Weight pills ── */
    .weights-row {
        display: flex;
        gap: 0.7rem;
        flex-wrap: wrap;
        margin: 0.8rem 0;
    }
    .weight-pill {
        background: rgba(100, 181, 246, 0.12);
        border: 1px solid rgba(100, 181, 246, 0.3);
        border-radius: 20px;
        padding: 5px 14px;
        font-size: 0.83rem;
        color: #90caf9;
        font-weight: 500;
    }
    .weight-pill.elevated {
        background: rgba(255, 183, 77, 0.15);
        border-color: rgba(255, 183, 77, 0.4);
        color: #ffb74d;
    }

    /* ── Operator badges ── */
    .badge-kpn      { background: rgba(100,181,246,0.18); color:#64b5f6; border:1px solid rgba(100,181,246,0.35); border-radius:6px; padding:2px 8px; font-size:0.78rem; font-weight:600; }
    .badge-freshbus { background: rgba(129,199,132,0.18); color:#81c784; border:1px solid rgba(129,199,132,0.35); border-radius:6px; padding:2px 8px; font-size:0.78rem; font-weight:600; }
    .badge-flixbus  { background: rgba(206,147,216,0.18); color:#ce93d8; border:1px solid rgba(206,147,216,0.35); border-radius:6px; padding:2px 8px; font-size:0.78rem; font-weight:600; }
    .badge-bk { background: rgba(255,138,101,0.15); color:#ff8a65; border:1px solid rgba(255,138,101,0.3); border-radius:6px; padding:2px 8px; font-size:0.75rem; }
    .badge-kb { background: rgba(77,182,172,0.15); color:#4db6ac; border:1px solid rgba(77,182,172,0.3); border-radius:6px; padding:2px 8px; font-size:0.75rem; }

    /* ── Bus timeline ── */
    .bus-card {
        background: rgba(22, 27, 34, 0.9);
        border: 1px solid rgba(48, 54, 61, 0.7);
        border-radius: 10px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 0.9rem;
        transition: border-color 0.2s ease;
    }
    .bus-card:hover {
        border-color: rgba(100, 181, 246, 0.4);
    }
    .bus-header {
        display: flex;
        align-items: center;
        gap: 0.7rem;
        margin-bottom: 0.9rem;
    }
    .bus-id {
        font-size: 1rem;
        font-weight: 700;
        color: #e6edf3;
    }

    /* ── Timeline steps ── */
    .timeline {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 0;
        padding: 0.4rem 0;
    }
    .tl-node {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 3px;
    }
    .tl-circle {
        width: 36px;
        height: 36px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: -0.5px;
    }
    .tl-circle.origin   { background: rgba(77,182,172,0.25); border:2px solid #4db6ac; color:#4db6ac; }
    .tl-circle.charge   { background: rgba(255,183,77,0.2);  border:2px solid #ffb74d; color:#ffb74d; }
    .tl-circle.dest     { background: rgba(129,199,132,0.2); border:2px solid #81c784; color:#81c784; }
    .tl-label { font-size: 0.68rem; color: #8b949e; text-align: center; max-width: 56px; }
    .tl-time  { font-size: 0.7rem; color: #e6edf3; font-weight: 600; text-align: center; }
    .tl-wait  { font-size: 0.62rem; color: #ff8a65; font-weight: 500; text-align: center; }

    .tl-connector {
        flex: 1;
        min-width: 20px;
        height: 2px;
        background: linear-gradient(90deg, rgba(48,54,61,0.8), rgba(100,181,246,0.3), rgba(48,54,61,0.8));
        margin: 0 4px;
        align-self: center;
        margin-bottom: 20px;
    }

    /* ── Station view ── */
    .station-header {
        font-size: 1.05rem;
        font-weight: 700;
        color: #e6edf3;
        margin-bottom: 0.6rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .station-dot {
        width: 12px;
        height: 12px;
        border-radius: 50%;
        display: inline-block;
    }
    .queue-row {
        display: flex;
        align-items: center;
        gap: 0.8rem;
        padding: 0.55rem 0.8rem;
        border-radius: 8px;
        background: rgba(30, 37, 46, 0.7);
        margin-bottom: 0.4rem;
        font-size: 0.85rem;
    }
    .queue-pos {
        color: #8b949e;
        font-weight: 600;
        font-size: 0.8rem;
        min-width: 22px;
    }
    .queue-time {
        color: #e6edf3;
        font-weight: 500;
        min-width: 120px;
        font-size: 0.82rem;
    }
    .queue-wait {
        color: #ff8a65;
        font-size: 0.78rem;
        font-weight: 500;
    }
    .queue-wait.none { color: #81c784; }

    /* ── Streamlit overrides ── */
    .stSelectbox > div > div {
        background: rgba(22, 27, 34, 0.9) !important;
        border-color: rgba(48, 54, 61, 0.8) !important;
        color: #e6edf3 !important;
    }
    .stTabs [data-baseweb="tab-list"] {
        background: rgba(22, 27, 34, 0.5);
        border-radius: 10px;
        padding: 4px;
        gap: 4px;
        border: 1px solid rgba(48, 54, 61, 0.6);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        color: #8b949e !important;
        font-weight: 500;
    }
    .stTabs [aria-selected="true"] {
        background: rgba(21, 101, 192, 0.35) !important;
        color: #90caf9 !important;
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid rgba(48, 54, 61, 0.6);
        border-radius: 8px;
        overflow: hidden;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ────────────────────────────────────────────────────────────────────────────
# Data loading helpers
# ────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data"

SCENARIO_FILES = {
    "Scenario 1 — Even Spacing":            "scenario1.json",
    "Scenario 2 — Bunched Start":            "scenario2.json",
    "Scenario 3 — Asymmetric Load":          "scenario3.json",
    "Scenario 4 — Operator-Heavy (KPN)":     "scenario4.json",
    "Scenario 5 — Worst-Case Convergence":   "scenario5.json",
}

OPERATOR_COLORS = {
    "kpn":      "#64b5f6",
    "freshbus": "#81c784",
    "flixbus":  "#ce93d8",
}
STATION_COLORS = {
    "A": "#ff8a65",
    "B": "#ffb74d",
    "C": "#81c784",
    "D": "#64b5f6",
}


@st.cache_data(show_spinner=False)
def run_scenario(filename: str) -> tuple[ScenarioConfig, ScheduleResult]:
    engine = SchedulerEngine()
    scenario = engine.load_scenario(DATA_DIR / filename)
    result = engine.schedule(scenario)
    return scenario, result


# ────────────────────────────────────────────────────────────────────────────
# Rendering helpers
# ────────────────────────────────────────────────────────────────────────────

def operator_badge(op: str) -> str:
    cls = f"badge-{op.lower()}"
    return f'<span class="{cls}">{op.upper()}</span>'


def direction_badge(direction: str) -> str:
    if direction == "BK":
        return '<span class="badge-bk">BKL → KCH</span>'
    return '<span class="badge-kb">KCH → BKL</span>'


def fmt_wait(wait_min: float) -> str:
    if wait_min < 0.5:
        return '<span class="queue-wait none">no wait</span>'
    return f'<span class="queue-wait">+{wait_min:.0f} min wait</span>'


def render_hero() -> None:
    st.markdown(
        """
        <div class="hero">
            <div class="hero-title">⚡ Bus Charging Scheduler</div>
            <div class="hero-subtitle">
                Rule-engine driven charging plan optimiser for electric bus fleets
            </div>
            <div>
                <span class="hero-badge">🚌 Bengaluru → A → B → C → D → Kochi</span>
                &nbsp;&nbsp;
                <span class="hero-badge">240 km range · 25 min charge · 60 km/h</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_weights(weights) -> None:
    def pill(label: str, val: float, elevated: bool = False) -> str:
        cls = "weight-pill elevated" if elevated else "weight-pill"
        return f'<span class="{cls}">{label}: <strong>{val}</strong></span>'

    elevated_individual = weights.individual != 1.0
    elevated_operator   = weights.operator   != 1.0
    elevated_overall    = weights.overall    != 1.0

    st.markdown(
        f"""
        <div class="weights-row">
            {pill("Individual", weights.individual, elevated_individual)}
            {pill("Operator",   weights.operator,   elevated_operator)}
            {pill("Overall",    weights.overall,    elevated_overall)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_scenario_input(scenario: ScenarioConfig) -> None:
    """Render the scenario input view — what is being fed into the scheduler."""
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-title"><span class="icon">📋</span>Scenario Input</div>',
        unsafe_allow_html=True,
    )

    st.markdown(f'<div class="scenario-desc">{scenario.description}</div>', unsafe_allow_html=True)

    # Weights
    st.markdown("**Optimisation Weights**")
    render_weights(scenario.weights)

    # Route summary
    col1, col2 = st.columns([1, 2])
    with col1:
        st.markdown("**Route Segments**")
        seg_data = [
            {
                "From":        s.from_station,
                "To":          s.to_station,
                "Distance km": s.distance_km,
                "Travel (min @60)": int(s.distance_km / scenario.physics.speed_kmh * 60),
            }
            for s in scenario.route.segments
        ]
        st.dataframe(
            pd.DataFrame(seg_data),
            width='stretch',
            hide_index=True,
        )
    with col2:
        st.markdown("**Departure Schedule**")
        bus_data = []
        for b in sorted(scenario.buses, key=lambda x: x.departure_min):
            hhmm = f"{int(b.departure_min // 60):02d}:{int(b.departure_min % 60):02d}"
            direction_str = "Bengaluru → Kochi" if b.direction == "BK" else "Kochi → Bengaluru"
            bus_data.append(
                {
                    "Bus ID":    b.id,
                    "Operator":  b.operator.upper(),
                    "Direction": direction_str,
                    "Departure": hhmm,
                }
            )
        st.dataframe(
            pd.DataFrame(bus_data),
            width='stretch',
            hide_index=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)


def render_bus_timeline(bs: BusSchedule, scenario: ScenarioConfig) -> None:
    """Render a single bus's charging timeline as a visual card."""
    op_badge  = operator_badge(bs.bus.operator)
    dir_badge = direction_badge(bs.bus.direction)

    origin_name = bs.bus.origin
    dest_name   = bs.bus.destination
    dep_time    = minutes_to_hhmm(bs.departure_min)
    arr_time    = minutes_to_hhmm(bs.arrival_min)
    wait_total  = bs.total_wait_min
    trip_total  = bs.total_trip_min
    wait_color  = '#ff8a65' if wait_total > 0 else '#81c784'

    # Build the entire card as ONE flat HTML string.
    # Streamlit's markdown renderer escapes HTML that appears inside
    # interpolated multiline {variable} blocks. Keeping everything
    # in a single concatenated string avoids that problem entirely.

    def _node(circle_cls, label, abbr, time_str, wait_str, wait_col):
        return (
            f'<div class="tl-node">'
            f'<div class="tl-circle {circle_cls}">{abbr}</div>'
            f'<div class="tl-label">{label}</div>'
            f'<div class="tl-time">{time_str}</div>'
            f'<div class="tl-wait" style="color:{wait_col};">{wait_str}</div>'
            f'</div>'
        )

    connector = '<div class="tl-connector"></div>'

    parts = [_node('origin', origin_name, origin_name[:3].upper(), dep_time, '', '#8b949e')]

    for stop in bs.charging_stops:
        arrive_str     = minutes_to_hhmm(stop.arrival_min)
        charge_end_str = minutes_to_hhmm(stop.charge_end_min)
        w_str = f'+{stop.wait_min:.0f}m wait' if stop.wait_min >= 0.5 else 'no wait'
        w_col = '#ff8a65' if stop.wait_min >= 0.5 else '#81c784'
        parts.append(connector)
        parts.append(_node('charge', f'Stn {stop.station_id}', stop.station_id,
                           f'{arrive_str}–{charge_end_str}', w_str, w_col))

    parts.append(connector)
    parts.append(_node('dest', dest_name, dest_name[:3].upper(), arr_time, '', '#8b949e'))

    timeline_html = ''.join(parts)

    card_html = (
        f'<div class="bus-card">'
        f'<div class="bus-header">'
        f'<span class="bus-id">{bs.bus.id}</span>'
        f'{op_badge}&nbsp;{dir_badge}'
        f'<span style="margin-left:auto;font-size:0.8rem;color:#8b949e;">'
        f'Trip: <strong style="color:#e6edf3;">{trip_total:.0f} min</strong>'
        f'&nbsp;|&nbsp;'
        f'Total wait: <strong style="color:{wait_color};">{wait_total:.0f} min</strong>'
        f'&nbsp;|&nbsp;'
        f'Charges: <strong style="color:#ffb74d;">{len(bs.charging_stops)}</strong>'
        f'</span>'
        f'</div>'
        f'<div class="timeline">{timeline_html}</div>'
        f'</div>'
    )

    st.markdown(card_html, unsafe_allow_html=True)


def render_per_bus_timetable(result: ScheduleResult, scenario: ScenarioConfig) -> None:
    """Render the per-bus timetable section."""
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-title"><span class="icon">🚌</span>Per-Bus Timetable</div>',
        unsafe_allow_html=True,
    )

    # Summary metrics
    total_wait = sum(bs.total_wait_min for bs in result.bus_schedules)
    max_wait = max((bs.total_wait_min for bs in result.bus_schedules), default=0)
    buses_with_wait = sum(1 for bs in result.bus_schedules if bs.total_wait_min > 0)

    st.markdown(
        f"""
        <div class="metrics-row">
            <div class="metric-chip">
                <span class="metric-label">Total buses</span>
                <span class="metric-value blue">{len(result.bus_schedules)}</span>
            </div>
            <div class="metric-chip">
                <span class="metric-label">Total wait (all buses)</span>
                <span class="metric-value amber">{total_wait:.0f} min</span>
            </div>
            <div class="metric-chip">
                <span class="metric-label">Max single-bus wait</span>
                <span class="metric-value {'amber' if max_wait > 0 else 'green'}">{max_wait:.0f} min</span>
            </div>
            <div class="metric-chip">
                <span class="metric-label">Buses with any wait</span>
                <span class="metric-value purple">{buses_with_wait}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Filter/sort controls
    col_f1, col_f2, col_f3 = st.columns([2, 2, 2])
    with col_f1:
        ops = ["All operators"] + sorted({bs.bus.operator for bs in result.bus_schedules})
        sel_op = st.selectbox("Filter by operator", ops, key="filter_op")
    with col_f2:
        dirs_raw = sorted({bs.bus.direction for bs in result.bus_schedules})
        dir_labels = {"BK": "Bengaluru → Kochi", "KB": "Kochi → Bengaluru"}
        dirs = ["All directions"] + [dir_labels[d] for d in dirs_raw]
        sel_dir = st.selectbox("Filter by direction", dirs, key="filter_dir")
    with col_f3:
        sort_by = st.selectbox("Sort by", ["Departure time", "Total wait (desc)", "Arrival time"], key="sort_by")

    # Apply filters
    buses = list(result.bus_schedules)
    if sel_op != "All operators":
        buses = [b for b in buses if b.bus.operator == sel_op]
    if sel_dir != "All directions":
        sel_dir_code = "BK" if "Bengaluru" in sel_dir else "KB"
        buses = [b for b in buses if b.bus.direction == sel_dir_code]

    # Apply sort
    if sort_by == "Departure time":
        buses.sort(key=lambda b: b.departure_min)
    elif sort_by == "Total wait (desc)":
        buses.sort(key=lambda b: b.total_wait_min, reverse=True)
    elif sort_by == "Arrival time":
        buses.sort(key=lambda b: b.arrival_min)

    if not buses:
        st.info("No buses match the current filters.")
    else:
        for bs in buses:
            render_bus_timeline(bs, scenario)

    st.markdown("</div>", unsafe_allow_html=True)


def render_per_station_view(result: ScheduleResult, scenario: ScenarioConfig) -> None:
    """Render the per-station charger queue view."""
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-title"><span class="icon">🔌</span>Per-Station Charger Queue</div>',
        unsafe_allow_html=True,
    )

    station_ids = [sc.id for sc in scenario.station_configs]

    cols = st.columns(len(station_ids))
    for col, station_id in zip(cols, station_ids):
        ss = result.get_station_schedule(station_id)
        dot_color = STATION_COLORS.get(station_id, "#8b949e")

        with col:
            num_chargers = scenario.get_station_config(station_id).chargers if scenario.get_station_config(station_id) else 1
            charger_label = f"{num_chargers} charger{'s' if num_chargers > 1 else ''}"
            st.markdown(
                f"""
                <div class="station-header">
                    <span class="station-dot" style="background:{dot_color};"></span>
                    Station {station_id}
                    <span style="font-size:0.72rem;color:#8b949e;font-weight:400;">· {charger_label}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if not ss or not ss.slots:
                st.markdown(
                    '<div style="color:#8b949e;font-size:0.85rem;padding:0.5rem 0;">No buses charged here.</div>',
                    unsafe_allow_html=True,
                )
                continue

            for i, slot in enumerate(ss.slots):
                op_color = OPERATOR_COLORS.get(slot.operator, "#8b949e")
                dir_arrow = "→ KCH" if slot.direction == "BK" else "→ BKL"
                start_str = minutes_to_hhmm(slot.charge_start_min)
                end_str   = minutes_to_hhmm(slot.charge_end_min)
                wait_html = fmt_wait(slot.wait_min)
                st.markdown(
                    f"""
                    <div class="queue-row">
                        <span class="queue-pos">#{i+1}</span>
                        <span style="color:{op_color};font-weight:600;font-size:0.8rem;min-width:70px;">{slot.bus_id.split('-',1)[1] if '-' in slot.bus_id else slot.bus_id}</span>
                        <span style="font-size:0.72rem;color:#8b949e;">{dir_arrow}</span>
                        <span class="queue-time">{start_str}–{end_str}</span>
                        {wait_html}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # Utilisation bar
            if ss.slots:
                first_start = ss.slots[0].charge_start_min
                last_end    = ss.slots[-1].charge_end_min
                total_span  = max(last_end - first_start, 1)
                total_charge_time = sum(
                    s.charge_end_min - s.charge_start_min for s in ss.slots
                )
                utilisation = min(total_charge_time / total_span * 100, 100)
                st.markdown(
                    f"""
                    <div style="margin-top:0.8rem;">
                        <div style="font-size:0.7rem;color:#8b949e;margin-bottom:4px;">
                            Charger utilisation: <strong style="color:#e6edf3;">{utilisation:.0f}%</strong>
                            &nbsp;·&nbsp; {len(ss.slots)} buses served
                        </div>
                        <div style="background:rgba(48,54,61,0.5);border-radius:4px;height:6px;overflow:hidden;">
                            <div style="width:{utilisation:.0f}%;height:100%;background:linear-gradient(90deg,{dot_color},{dot_color}88);border-radius:4px;"></div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.markdown("</div>", unsafe_allow_html=True)


def render_tabular_summary(result: ScheduleResult) -> None:
    """Render a compact flat table for quick review."""
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-title"><span class="icon">📊</span>Full Schedule Table</div>',
        unsafe_allow_html=True,
    )

    rows = []
    for bs in sorted(result.bus_schedules, key=lambda b: b.departure_min):
        stops_str = " → ".join(bs.stations_used()) if bs.stations_used() else "—"
        rows.append(
            {
                "Bus ID":          bs.bus.id,
                "Operator":        bs.bus.operator.upper(),
                "Direction":       "BKL→KCH" if bs.bus.direction == "BK" else "KCH→BKL",
                "Departure":       minutes_to_hhmm(bs.departure_min),
                "Stations Charged": stops_str,
                "Charges":         len(bs.charging_stops),
                "Total Wait (min)": f"{bs.total_wait_min:.0f}",
                "Trip Duration (min)": f"{bs.total_trip_min:.0f}",
                "Arrival":         minutes_to_hhmm(bs.arrival_min),
            }
        )

    st.dataframe(
        pd.DataFrame(rows),
        width='stretch',
        hide_index=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────

def main() -> None:
    render_hero()

    # ── Scenario selector ──────────────────────────────────────────────────
    scenario_names = list(SCENARIO_FILES.keys())
    selected_name = st.selectbox(
        "Select a scenario to schedule",
        scenario_names,
        index=0,
        key="scenario_select",
    )

    filename = SCENARIO_FILES[selected_name]

    with st.spinner("Running scheduler…"):
        try:
            scenario, result = run_scenario(filename)
        except Exception as e:
            st.error(f"Scheduler error: {e}")
            st.exception(e)
            return

    # ── Three tabs ─────────────────────────────────────────────────────────
    tab_input, tab_buses, tab_stations, tab_table = st.tabs(
        ["📋 Scenario Input", "🚌 Per-Bus Timetable", "🔌 Per-Station View", "📊 Full Table"]
    )

    with tab_input:
        render_scenario_input(scenario)

    with tab_buses:
        render_per_bus_timetable(result, scenario)

    with tab_stations:
        render_per_station_view(result, scenario)

    with tab_table:
        render_tabular_summary(result)


if __name__ == "__main__":
    main()
