"""
CarbonWise ⚡ — Real-Time Grid Carbon Intensity Scheduler
Enhanced UI: Better spacing, typography, and modern attractive theme
"""

import math
import json
import time
import hashlib
import logging
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytz
import requests
import streamlit as st
import streamlit.components.v1 as components

# ──────────────────────────────────────────────────────────────
# 0. App-level config
# ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CarbonWise ⚡",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("carbonwise")

# ──────────────────────────────────────────────────────────────
# 1. Constants & lookup tables
# ──────────────────────────────────────────────────────────────
STEP_MIN    = 15          # scheduling granularity
LOG_FILE    = Path("logs/runs.jsonl")
CONFIG_FILE = Path("config/location.json")
DATA_DIR    = Path("data")
EM_API_BASE = "https://api-access.electricitymaps.com/free-tier"

# Weighted g CO₂ / kWh per source
SOURCE_FACTORS = {"thermal": 820.0, "hydro": 24.0, "nuclear": 12.0, "res": 50.0}

# CO₂ equivalence denominators  (kg CO₂ per unit)
CO2_EQUIV = {
    "🌳 Tree-days (absorption)": 0.021,
    "🚗 km not driven":          0.192,
    "✈️ passenger-km saved":    0.255,
    "📱 phone charges":          0.00844,
}

# Hour-of-day demand multipliers (0–23)
HOUR_DEMAND = [
    0.62, 0.58, 0.55, 0.53, 0.54, 0.62,
    0.78, 0.92, 1.08, 1.15, 1.10, 1.05,
    1.00, 0.97, 0.93, 0.94, 1.02, 1.18,
    1.25, 1.22, 1.15, 1.05, 0.92, 0.75,
]

# Day-of-week (Mon=0) demand multipliers
DOW_DEMAND = {0: 1.05, 1: 1.08, 2: 1.06, 3: 1.04, 4: 1.03, 5: 0.88, 6: 0.82}

GRID_ZONES = {
    "India": {
        "timezone": "Asia/Kolkata", "voltage": "230 V / 50 Hz",
        "zones": {
            "North India (NR)": {"lat": 28.6139, "lon":  77.2090, "ci_avg": 450},
            "South India (SR)": {"lat": 13.0827, "lon":  80.2707, "ci_avg": 380},
            "West India (WR)":  {"lat": 19.0760, "lon":  72.8777, "ci_avg": 420},
            "East India (ER)":  {"lat": 22.5726, "lon":  88.3639, "ci_avg": 480},
            "North-East (NER)": {"lat": 26.1445, "lon":  91.7362, "ci_avg": 350},
        },
    },
    "Europe": {
        "timezone": "Europe/Brussels", "voltage": "230 V / 50 Hz",
        "zones": {
            "Germany (DE)":       {"lat": 51.1657, "lon": 10.4515, "ci_avg": 276},
            "France (FR)":        {"lat": 46.2276, "lon":  2.2137, "ci_avg":  16},
            "UK (GB)":            {"lat": 55.3781, "lon": -3.4360, "ci_avg": 106},
            "Netherlands (NL)":   {"lat": 52.1326, "lon":  5.2913, "ci_avg": 209},
            "Spain (ES)":         {"lat": 40.4637, "lon": -3.7492, "ci_avg":  89},
            "Italy (IT)":         {"lat": 41.8719, "lon": 12.5674, "ci_avg": 202},
            "Nordics (NO/SE/FI)": {"lat": 60.4720, "lon":  8.4689, "ci_avg":  30},
        },
    },
    "United States": {
        "timezone": "America/New_York", "voltage": "120 V / 60 Hz",
        "zones": {
            "California (CAISO)": {"lat": 36.7783, "lon": -119.4179, "ci_avg": 200},
            "Texas (ERCOT)":      {"lat": 31.9686, "lon":  -99.9018, "ci_avg": 350},
            "New York (NYISO)":   {"lat": 42.1657, "lon":  -74.9481, "ci_avg": 250},
            "Midwest (MISO)":     {"lat": 41.8780, "lon":  -93.0977, "ci_avg": 400},
            "PJM (East)":         {"lat": 39.8283, "lon":  -77.5794, "ci_avg": 320},
            "Pacific Northwest":  {"lat": 45.5152, "lon": -122.6784, "ci_avg": 100},
        },
    },
    "Asia Pacific": {
        "timezone": "Asia/Tokyo", "voltage": "100–240 V / 50–60 Hz",
        "zones": {
            "Australia (AUS)":  {"lat": -25.2744, "lon": 133.7751, "ci_avg": 450},
            "Japan (JP)":       {"lat":  36.2048, "lon": 138.2529, "ci_avg": 450},
            "Singapore (SG)":   {"lat":   1.3521, "lon": 103.8198, "ci_avg": 367},
            "South Korea (KR)": {"lat":  35.9078, "lon": 127.7669, "ci_avg": 357},
        },
    },
}

APPLIANCES = {
    "Geyser / Water Heater": {"kw": 2.0,  "dur": 30,  "deadline": "11:00", "icon": "🚿"},
    "Washing Machine":        {"kw": 0.5,  "dur": 60,  "deadline": "11:00", "icon": "👗"},
    "Iron Box":               {"kw": 1.0,  "dur": 30,  "deadline": "11:00", "icon": "👔"},
    "Water Motor":            {"kw": 0.75, "dur": 30,  "deadline": "08:00", "icon": "💧"},
    "EV Charger 3.3 kW":     {"kw": 3.3,  "dur": 120, "deadline": "07:00", "icon": "⚡"},
    "EV Charger 7.0 kW":     {"kw": 7.0,  "dur": 120, "deadline": "07:00", "icon": "🚗"},
    "Air Conditioner":        {"kw": 1.5,  "dur": 60,  "deadline": "23:00", "icon": "❄️"},
    "Induction Cooktop":      {"kw": 1.8,  "dur": 30,  "deadline": "21:00", "icon": "🍳"},
    "Microwave":              {"kw": 1.2,  "dur": 15,  "deadline": "21:00", "icon": "📡"},
    "Laptop Charging":        {"kw": 0.06, "dur": 120, "deadline": "23:00", "icon": "💻"},
    "Custom":                 {"kw": 1.0,  "dur": 30,  "deadline": "11:00", "icon": "🔌"},
}

BADGES = [
    {"id": "first_save",   "name": "First Save",     "icon": "🌱", "desc": "Logged your first run",        "threshold": 1},
    {"id": "green_week",   "name": "Green Week",      "icon": "🌿", "desc": "7-day scheduling streak",      "threshold": 7},
    {"id": "ton_saver",    "name": "Ton Saver",       "icon": "🏆", "desc": "Saved 1 kg CO₂ total",        "threshold": 1.0},
    {"id": "optimizer",    "name": "Grid Optimizer",  "icon": "⚡", "desc": "10 recommended runs logged",  "threshold": 10},
    {"id": "eco_champion", "name": "Eco Champion",    "icon": "🌍", "desc": "Saved 10 kg CO₂ total",       "threshold": 10.0},
    {"id": "night_owl",    "name": "Night Owl",       "icon": "🦉", "desc": "Run scheduled 22:00–05:00",   "threshold": 1},
    {"id": "solar_surfer", "name": "Solar Surfer",    "icon": "☀️", "desc": "Run during 10:00–16:00",      "threshold": 1},
    {"id": "early_bird",   "name": "Early Bird",      "icon": "🐦", "desc": "Run scheduled before 06:00",  "threshold": 1},
]

# ──────────────────────────────────────────────────────────────
# 2. Pure helper functions
# ──────────────────────────────────────────────────────────────

def safe_float(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def ceil15(minutes: int) -> int:
    m = int(minutes)
    return m if m % STEP_MIN == 0 else m + (STEP_MIN - m % STEP_MIN)


def hm_to_mins(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def mins_to_hm(total: int) -> str:
    total = int(total) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def to_12h(hhmm: str) -> str:
    try:
        h, m = map(int, hhmm.split(":"))
        p = "AM" if h < 12 else "PM"
        return f"{(h % 12) or 12}:{m:02d} {p}"
    except ValueError:
        return hhmm


def valid_hhmm(s: str) -> bool:
    try:
        h, m = map(int, s.split(":"))
        return 0 <= h < 24 and 0 <= m < 60
    except (ValueError, AttributeError):
        return False


def ci_color(ci: float) -> str:
    ci = safe_float(ci, 500)
    return "#10b981" if ci < 200 else "#f59e0b" if ci < 400 else "#ef4444"


def ci_label(ci: float) -> str:
    ci = safe_float(ci, 500)
    return "Low" if ci < 200 else "Medium" if ci < 400 else "High"


def ci_emoji(ci: float) -> str:
    return "🟢" if ci < 200 else "🟡" if ci < 400 else "🔴"


def compute_ci(thermal: float, hydro: float, nuclear: float, res: float) -> float:
    vals = {
        "thermal": safe_float(thermal),
        "hydro":   safe_float(hydro),
        "nuclear": safe_float(nuclear),
        "res":     safe_float(res),
    }
    total = sum(vals.values())
    if total <= 0:
        return 400.0
    return sum(vals[k] * SOURCE_FACTORS[k] for k in vals) / total


def co2_kg(kwh: float, ci_g_kwh: float) -> float:
    return safe_float(kwh) * safe_float(ci_g_kwh) / 1000.0


def co2_equivalents(saved_kg: float) -> dict:
    return {k: saved_kg / v for k, v in CO2_EQUIV.items()}


def now_tz(tz_str: str) -> datetime:
    try:
        return datetime.now(pytz.timezone(tz_str))
    except Exception:
        return datetime.now(pytz.UTC)


def slot_str(tz_str: str) -> str:
    n = now_tz(tz_str)
    return f"{n.hour:02d}:{(n.minute // STEP_MIN) * STEP_MIN:02d}"


def exact_time_str(tz_str: str) -> str:
    n = now_tz(tz_str)
    p = "AM" if n.hour < 12 else "PM"
    return f"{(n.hour % 12) or 12}:{n.minute:02d}:{n.second:02d} {p}"


# ──────────────────────────────────────────────────────────────
# 3. DataEngine
# ──────────────────────────────────────────────────────────────

class DataEngine:

    @staticmethod
    def _simulate_profile(base_ci: float, seed: int) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        rows = []
        for h in range(24):
            f = HOUR_DEMAND[h]
            for qi, q in enumerate([0, 15, 30, 45]):
                intra_wave = math.sin(qi * math.pi / 4) * 0.015
                noise      = rng.normal(0, 0.035)
                multiplier = max(0.25, f + intra_wave + noise)
                ci  = round(max(10.0, min(900.0, base_ci * multiplier)), 2)

                thermal  = ci * 7.0 * max(0.3, f)
                hydro    = base_ci * 1.8 * (1 - f * 0.25)
                nuclear  = base_ci * 1.6
                solar_pk = base_ci * 2.5 if 10 <= h <= 16 else 0
                res      = max(0.0, base_ci * 2.8 * (1 - f * 0.5) + solar_pk)

                rows.append({
                    "time":       f"{h:02d}:{q:02d}",
                    "ci":         ci,
                    "thermal_mw": round(thermal, 1),
                    "hydro_mw":   round(hydro,   1),
                    "nuclear_mw": round(nuclear,  1),
                    "res_mw":     round(res,      1),
                })
        return pd.DataFrame(rows)

    @staticmethod
    @st.cache_data(ttl=300, show_spinner=False)
    def fetch_live(lat: float, lon: float, token: str, seed: int) -> pd.DataFrame:
        if token:
            try:
                r = requests.get(
                    f"{EM_API_BASE}/carbon-intensity/latest",
                    headers={"auth-token": token},
                    params={"lat": lat, "lon": lon},
                    timeout=8,
                )
                if r.status_code == 200:
                    base_ci = safe_float(r.json().get("carbonIntensity"), 400)
                    return DataEngine._simulate_profile(base_ci, seed)
            except requests.RequestException as exc:
                log.warning("EM API error: %s", exc)
        base_ci = DataEngine._nearest_zone_ci(lat, lon)
        return DataEngine._simulate_profile(base_ci, seed)

    @staticmethod
    @st.cache_data(ttl=300, show_spinner=False)
    def load_sample(seed: int) -> pd.DataFrame:
        return DataEngine._simulate_profile(420.0, seed)

    @staticmethod
    def from_upload(csv_bytes: bytes) -> pd.DataFrame:
        df = pd.read_csv(pd.io.common.BytesIO(csv_bytes))
        required = {"time", "thermal_mw", "hydro_mw", "nuclear_mw", "res_mw"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing columns: {missing}")
        df["ci"] = df.apply(
            lambda r: compute_ci(r["thermal_mw"], r["hydro_mw"], r["nuclear_mw"], r["res_mw"]),
            axis=1,
        )
        return df.sort_values("time").reset_index(drop=True)

    @staticmethod
    def full_day(raw: pd.DataFrame) -> pd.DataFrame:
        ci_map   = dict(zip(raw["time"], raw["ci"]))
        mean_ci  = safe_float(raw["ci"].mean(), 400)
        rows = [
            {
                "time":   f"{h:02d}:{m:02d}",
                "ci":     safe_float(ci_map.get(f"{h:02d}:{m:02d}", mean_ci), mean_ci),
                "color":  ci_color(ci_map.get(f"{h:02d}:{m:02d}", mean_ci)),
                "label":  ci_label(ci_map.get(f"{h:02d}:{m:02d}", mean_ci)),
            }
            for h in range(24)
            for m in [0, 15, 30, 45]
        ]
        return pd.DataFrame(rows)

    @staticmethod
    def ci_at(full: pd.DataFrame, hhmm: str) -> Optional[float]:
        mask = full["time"] == hhmm
        return float(full.loc[mask, "ci"].iloc[0]) if mask.any() else None

    @staticmethod
    def windows(
        full: pd.DataFrame,
        duration_min: int,
        deadline: str,
        now: str,
        top_k: int,
    ) -> list[dict]:
        dur   = ceil15(duration_min)
        nslot = dur // STEP_MIN

        now_m  = hm_to_mins(now)
        dead_m = hm_to_mins(deadline)
        if dead_m <= now_m:
            dead_m += 1440

        times = full["time"].tolist()
        cis   = full["ci"].tolist()
        wins  = []

        for i in range(len(times) - nslot + 1):
            sh, sm   = map(int, times[i].split(":"))
            start_m  = sh * 60 + sm
            adj_start = start_m if start_m >= now_m else start_m + 1440
            end_m     = adj_start + dur

            if adj_start < now_m:
                continue
            if end_m > dead_m:
                continue

            window_cis = [safe_float(cis[j]) for j in range(i, min(i + nslot, len(cis)))]
            avg        = sum(window_cis) / len(window_cis)
            end_slot   = mins_to_hm(adj_start + dur)

            wins.append({
                "start":   times[i],
                "end":     end_slot,
                "avg_ci":  avg,
                "min_ci":  min(window_cis),
                "max_ci":  max(window_cis),
                "color":   ci_color(avg),
                "label":   ci_label(avg),
            })

        wins.sort(key=lambda x: x["avg_ci"])
        return wins[:top_k]

    @staticmethod
    def weekly_forecast(base_ci: float) -> pd.DataFrame:
        today = datetime.now()
        seed  = int(today.strftime("%Y%m%d"))
        rng   = np.random.default_rng(seed)
        rows  = []
        for d in range(7):
            day = today + timedelta(days=d)
            f   = DOW_DEMAND[day.weekday()]
            n   = rng.normal(0, 0.05)
            rows.append({
                "date":      day.strftime("%a %d %b"),
                "label":     "Today" if d == 0 else ("Tomorrow" if d == 1 else day.strftime("%a")),
                "ci_night":  round(max(10, base_ci * max(0.3, f * 0.65 + n)), 1),
                "ci_day":    round(max(10, base_ci * max(0.5, f + n)),         1),
                "ci_peak":   round(max(10, base_ci * max(0.7, f * 1.25 + n)),  1),
            })
            rows[-1]["avg"] = round(
                (rows[-1]["ci_night"] + rows[-1]["ci_day"] + rows[-1]["ci_peak"]) / 3, 1
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _nearest_zone_ci(lat: float, lon: float) -> float:
        best_ci   = 400.0
        best_dist = 1e9
        for region in GRID_ZONES.values():
            for z in region["zones"].values():
                d = (z["lat"] - lat) ** 2 + (z["lon"] - lon) ** 2
                if d < best_dist:
                    best_dist = d
                    best_ci   = z["ci_avg"]
        return best_ci


# ──────────────────────────────────────────────────────────────
# 4. Persistence helpers
# ──────────────────────────────────────────────────────────────

def ensure_dirs():
    for p in [LOG_FILE.parent, CONFIG_FILE.parent, DATA_DIR]:
        p.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("", encoding="utf-8")


def append_log(row: dict):
    ensure_dirs()
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


@st.cache_data(ttl=60)
def read_log() -> pd.DataFrame:
    ensure_dirs()
    rows = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def compute_stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"runs": 0, "rec": 0, "co2": 0.0, "kwh": 0.0, "streak": 0, "badges": [], "log_dates": []}

    runs      = len(df)
    rec       = int((df.get("run_type", pd.Series(dtype=str)) == "recommended").sum())
    total_co2 = safe_float(df["co2_kg"].sum()) if "co2_kg" in df else 0.0
    total_kwh = safe_float(df["kwh_used"].sum()) if "kwh_used" in df else 0.0

    streak = 0
    log_dates: list[date] = []
    if "date" in df:
        for d in df["date"].dropna().unique():
            try:
                log_dates.append(datetime.strptime(str(d)[:10], "%Y-%m-%d").date())
            except ValueError:
                pass
        log_dates = sorted(set(log_dates), reverse=True)
        check = datetime.now().date()
        for d in log_dates:
            if d == check or d == check - timedelta(days=streak):
                streak += 1
                check = d - timedelta(days=1)
            else:
                break

    badges = []
    if runs >= 1:      badges.append("first_save")
    if streak >= 7:    badges.append("green_week")
    if total_co2 >= 1: badges.append("ton_saver")
    if rec >= 10:      badges.append("optimizer")
    if total_co2 >= 10:badges.append("eco_champion")
    if "start_time" in df:
        hours = df["start_time"].dropna().apply(lambda t: int(str(t)[:2]) if len(str(t)) >= 2 else -1)
        if any((h >= 22 or h < 5) for h in hours):  badges.append("night_owl")
        if any(10 <= h <= 16 for h in hours):         badges.append("solar_surfer")
        if any(h < 6 for h in hours):                 badges.append("early_bird")

    return {"runs": runs, "rec": rec, "co2": total_co2, "kwh": total_kwh,
            "streak": streak, "badges": badges, "log_dates": log_dates}


def detect_ip_location() -> Optional[dict]:
    try:
        r = requests.get("https://ipapi.co/json/", timeout=5)
        if r.status_code == 200:
            d = r.json()
            return {
                "lat": d.get("latitude"), "lon": d.get("longitude"),
                "city": d.get("city"), "country": d.get("country_name"),
                "timezone": d.get("timezone"),
            }
    except requests.RequestException:
        pass
    return None


# ──────────────────────────────────────────────────────────────
# 5. Enhanced Global CSS — Modern, Attractive, Well-Spaced Theme
# ──────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;700&display=swap');

/* ── CSS Variables ── */
:root {
    --bg-primary: #0a0f1a;
    --bg-secondary: #111827;
    --bg-card: #151d2e;
    --bg-card-hover: #1a2335;
    --bg-sidebar: #0d1321;
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --border-color: #1e293b;
    --border-light: #1a2535;
    --accent-green: #10b981;
    --accent-green-soft: rgba(16, 185, 129, 0.12);
    --accent-green-glow: rgba(16, 185, 129, 0.15);
    --accent-blue: #3b82f6;
    --accent-blue-soft: rgba(59, 130, 246, 0.12);
    --accent-cyan: #06b6d4;
    --accent-amber: #f59e0b;
    --accent-amber-soft: rgba(245, 158, 11, 0.12);
    --accent-purple: #8b5cf6;
    --accent-red: #ef4444;
    --shadow-card: 0 4px 20px rgba(0, 0, 0, 0.3);
    --shadow-glow: 0 0 40px rgba(16, 185, 129, 0.08);
}

/* ── Base Reset ── */
* { box-sizing: border-box; }

.main, [data-testid="stAppViewContainer"] {
    font-family: 'Inter', sans-serif !important;
    background: var(--bg-primary) !important;
    color: var(--text-primary);
}

h1, h2, h3, h4, h5, h6 {
    font-family: 'Inter', sans-serif !important;
    font-weight: 700 !important;
    letter-spacing: 0.02em !important;
    line-height: 1.3 !important;
    margin-bottom: 1rem !important;
    color: var(--text-primary) !important;
}

h2 { font-size: 1.5rem !important; }
h3 { font-size: 1.15rem !important; font-weight: 600 !important; }

p, span, div, label {
    font-family: 'Inter', sans-serif !important;
    line-height: 1.6;
}

code, pre, .mono {
    font-family: 'JetBrains Mono', monospace !important;
    background: var(--bg-secondary) !important;
    padding: 0.2rem 0.4rem !important;
    border-radius: 4px !important;
    font-size: 0.85em !important;
}

/* ── Header & Sidebar ── */
[data-testid="stHeader"] {
    background: transparent !important;
}

[data-testid="stSidebar"] {
    background: var(--bg-sidebar) !important;
    border-right: 1px solid var(--border-color) !important;
    padding: 1.5rem 1rem !important;
}

/* ── Tabs ── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    padding: 0;
    background: var(--bg-secondary) !important;
    border-radius: 12px !important;
    padding: 6px !important;
    gap: 4px !important;
    border: 1px solid var(--border-color) !important;
    margin-bottom: 1.5rem !important;
}

[data-testid="stTabs"] [data-baseweb="tab"] {
    background: transparent !important;
    border-radius: 8px !important;
    padding: 10px 20px !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    color: var(--text-secondary) !important;
    border: none !important;
    transition: all 0.2s ease !important;
}

[data-testid="stTabs"] [data-baseweb="tab"]:hover {
    background: var(--bg-card-hover) !important;
    color: var(--text-primary) !important;
}

[data-testid="stTabs"] [aria-selected="true"] {
    background: linear-gradient(135deg, var(--accent-green), var(--accent-cyan)) !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
}

/* ── Buttons ── */
.stButton > button {
    background: linear-gradient(135deg, var(--accent-green), #059669) !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 0.6rem 1.2rem !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    transition: all 0.2s ease !important;
    box-shadow: 0 2px 10px rgba(16, 185, 129, 0.2) !important;
}

.stButton > button:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 4px 20px rgba(16, 185, 129, 0.3) !important;
}

.stButton > button:active {
    transform: translateY(0) !important;
}

/* ── Inputs ── */
.stTextInput > div > div > input,
.stNumberInput > div > div > input,
.stSelectbox > div > div > div,
.stSlider > div {
    background: var(--bg-secondary) !important;
    border: 1px solid var(--border-color) !important;
    border-radius: 10px !important;
    color: var(--text-primary) !important;
    padding: 0.6rem 0.8rem !important;
}

.stTextInput > div > div > input:focus,
.stNumberInput > div > div > input:focus {
    border-color: var(--accent-green) !important;
    box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.1) !important;
}

/* ── Hero Banner ── */
.hero {
    background: linear-gradient(135deg, #0f172a, #1e293b 55%, #111827);
    padding: 2.5rem 2.5rem !important;
    border-radius: 20px !important;
    margin-bottom: 2rem !important;
    border: 1px solid var(--border-color) !important;
    position: relative;
    overflow: hidden;
    box-shadow: var(--shadow-glow);
}

.hero::before {
    content: '';
    position: absolute;
    top: -50%;
    right: -10%;
    width: 500px;
    height: 500px;
    background: radial-gradient(circle, var(--accent-green-glow), transparent 70%);
    border-radius: 50%;
    pointer-events: none;
    animation: float 6s ease-in-out infinite;
}

@keyframes float {
    0%, 100% { transform: translateY(0) scale(1); }
    50% { transform: translateY(-20px) scale(1.05); }
}

.hero h1 {
    background: linear-gradient(135deg, #10b981, #3b82f6, #8b5cf6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    font-size: 2.8rem !important;
    font-weight: 800 !important;
    margin: 0 0 0.75rem 0 !important;
    position: relative;
    z-index: 1;
}

.hero p {
    color: var(--text-secondary) !important;
    margin: 0 !important;
    font-size: 1.1rem !important;
    line-height: 1.6 !important;
    max-width: 600px;
    position: relative;
    z-index: 1;
}

.hero-pills {
    margin-top: 1.25rem !important;
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    position: relative;
    z-index: 1;
}

.pill {
    padding: 6px 14px !important;
    border-radius: 999px !important;
    font-size: 0.8rem !important;
    font-weight: 600 !important;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    backdrop-filter: blur(10px);
}

.pill-green {
    background: var(--accent-green-soft) !important;
    color: var(--accent-green) !important;
    border: 1px solid rgba(16, 185, 129, 0.3) !important;
    box-shadow: 0 0 15px rgba(16, 185, 129, 0.15);
}

.pill-blue {
    background: var(--accent-blue-soft) !important;
    color: var(--accent-blue) !important;
    border: 1px solid rgba(59, 130, 246, 0.3) !important;
}

.pill-amber {
    background: var(--accent-amber-soft) !important;
    color: var(--accent-amber) !important;
    border: 1px solid rgba(245, 158, 11, 0.3) !important;
}

.pill-purple {
    background: rgba(139, 92, 246, 0.15) !important;
    color: var(--accent-purple) !important;
    border: 1px solid rgba(139, 92, 246, 0.3) !important;
}

.pill-muted {
    color: var(--text-muted) !important;
    font-size: 0.85rem !important;
    padding: 6px 0 !important;
}

/* ── Section Headings ── */
.section-heading {
    font-size: 1.4rem !important;
    font-weight: 700 !important;
    color: var(--text-primary) !important;
    margin: 2rem 0 1.25rem 0 !important;
    padding-bottom: 0.5rem !important;
    border-bottom: 2px solid var(--border-color) !important;
    display: flex;
    align-items: center;
    gap: 10px;
}

.section-heading::before {
    content: '';
    width: 4px;
    height: 24px;
    background: linear-gradient(180deg, var(--accent-green), var(--accent-cyan));
    border-radius: 2px;
}

.sub-heading {
    font-size: 1.1rem !important;
    font-weight: 600 !important;
    color: var(--text-secondary) !important;
    margin: 1.5rem 0 0.75rem 0 !important;
}

/* ── KPI Grid ── */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
}

.kpi {
    background: var(--bg-card) !important;
    padding: 1.5rem 1.25rem !important;
    border-radius: 16px !important;
    text-align: center;
    border: 1px solid var(--border-color) !important;
    min-height: 130px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    transition: all 0.3s ease;
    position: relative;
    overflow: hidden;
}

.kpi::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: linear-gradient(90deg, var(--accent-green), var(--accent-cyan));
    opacity: 0.7;
}

.kpi:hover {
    transform: translateY(-3px);
    box-shadow: var(--shadow-card);
    border-color: var(--accent-green) !important;
}

.kpi-lbl {
    color: var(--text-muted) !important;
    font-size: 0.7rem !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-weight: 600 !important;
    margin-bottom: 8px !important;
}

.kpi-val {
    color: var(--text-primary) !important;
    font-weight: 800 !important;
    font-size: 1.5rem !important;
    line-height: 1.2 !important;
    margin-bottom: 4px !important;
}

.kpi-sub {
    color: var(--text-muted) !important;
    font-size: 0.75rem !important;
    margin-top: 4px !important;
}

/* ── Recommendation Cards ── */
.rec-card {
    background: var(--bg-card) !important;
    padding: 1.5rem !important;
    border-radius: 16px !important;
    margin-bottom: 1rem !important;
    border: 1px solid var(--border-color) !important;
    transition: all 0.3s ease;
    position: relative;
    overflow: hidden;
}

.rec-card:hover {
    transform: translateY(-3px);
    box-shadow: var(--shadow-card);
    border-color: var(--accent-green) !important;
}

.rec-card.best {
    border: 2px solid var(--accent-green) !important;
    background: linear-gradient(135deg, var(--bg-card), rgba(16, 185, 129, 0.05)) !important;
    box-shadow: 0 0 30px rgba(16, 185, 129, 0.15);
}

.rec-card.best::after {
    content: 'BEST CHOICE';
    position: absolute;
    top: 12px;
    right: -35px;
    background: var(--accent-green);
    color: white;
    font-size: 0.65rem;
    font-weight: 700;
    padding: 4px 40px;
    transform: rotate(45deg);
    letter-spacing: 0.05em;
}

/* ── Ticker ── */
.ticker {
    background: var(--bg-card) !important;
    border: 1px solid var(--border-color) !important;
    border-radius: 14px !important;
    padding: 1rem 1.5rem !important;
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 1.5rem !important;
    box-shadow: var(--shadow-card);
}

.ticker-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--accent-green);
    flex-shrink: 0;
    animation: pulse-dot 2s ease-in-out infinite;
    box-shadow: 0 0 10px rgba(16, 185, 129, 0.5);
}

@keyframes pulse-dot {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(1.5); }
}

/* ── Alert Banners ── */
.alert {
    padding: 1rem 1.25rem !important;
    border-radius: 12px !important;
    margin-bottom: 1rem !important;
    border: 1px solid !important;
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 0.95rem !important;
    line-height: 1.5 !important;
}

.alert-g {
    background: rgba(16, 185, 129, 0.08) !important;
    border-color: rgba(16, 185, 129, 0.4) !important;
    border-left: 4px solid var(--accent-green) !important;
}

.alert-r {
    background: rgba(239, 68, 68, 0.08) !important;
    border-color: rgba(239, 68, 68, 0.4) !important;
    border-left: 4px solid var(--accent-red) !important;
}

.alert-y {
    background: rgba(245, 158, 11, 0.08) !important;
    border-color: rgba(245, 158, 11, 0.4) !important;
    border-left: 4px solid var(--accent-amber) !important;
}

/* ── Progress Bar ── */
.pb {
    height: 8px;
    border-radius: 999px;
    background: var(--bg-primary);
    margin: 10px 0;
    overflow: hidden;
    border: 1px solid var(--border-color);
}

.pb-fill {
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, var(--accent-green), var(--accent-cyan));
    transition: width 0.5s ease;
    box-shadow: 0 0 10px rgba(16, 185, 129, 0.3);
}

/* ── Badge Cards ── */
.badge-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 1rem;
    margin: 1.5rem 0;
}

.badge-card {
    background: var(--bg-card) !important;
    border-radius: 16px !important;
    padding: 1.5rem 1rem !important;
    text-align: center;
    border: 1px solid var(--border-color) !important;
    transition: all 0.3s ease;
}

.badge-card.earned {
    border-color: var(--accent-green) !important;
    background: linear-gradient(135deg, var(--bg-card), rgba(16, 185, 129, 0.08)) !important;
    box-shadow: 0 0 20px rgba(16, 185, 129, 0.1);
}

.badge-card:hover {
    transform: scale(1.05);
    box-shadow: var(--shadow-card);
}

.badge-icon {
    font-size: 2.5rem !important;
    margin-bottom: 0.5rem !important;
    display: block;
}

.badge-name {
    font-weight: 700 !important;
    font-size: 0.85rem !important;
    margin-bottom: 4px !important;
}

.badge-desc {
    font-size: 0.7rem !important;
    color: var(--text-muted) !important;
    margin-bottom: 8px !important;
    line-height: 1.4 !important;
}

.badge-status {
    font-size: 0.7rem !important;
    font-weight: 700 !important;
    padding: 3px 10px;
    border-radius: 999px;
    display: inline-block;
}

.badge-status.earned {
    background: var(--accent-green-soft);
    color: var(--accent-green);
}

.badge-status.locked {
    background: var(--bg-primary);
    color: var(--text-muted);
}

/* ── Live Clock ── */
.live-time {
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.1), rgba(6, 182, 212, 0.1)) !important;
    border: 1px solid rgba(16, 185, 129, 0.25) !important;
    padding: 1rem !important;
    border-radius: 14px !important;
    color: var(--accent-green) !important;
    font-weight: 700 !important;
    text-align: center;
    font-size: 1.2rem !important;
    letter-spacing: 0.05em;
    font-family: 'JetBrains Mono', monospace !important;
    box-shadow: 0 0 20px rgba(16, 185, 129, 0.1);
    margin: 1rem 0 !important;
}

.tz-sub {
    color: var(--text-muted) !important;
    font-size: 0.75rem !important;
    text-align: center;
    margin-top: 6px !important;
    letter-spacing: 0.02em;
}

/* ── Info Cards ── */
.info-card {
    background: var(--bg-card) !important;
    border-radius: 16px !important;
    padding: 1.5rem !important;
    border: 1px solid var(--border-color) !important;
    margin-bottom: 1rem !important;
}

.info-card h4 {
    margin-top: 0 !important;
    margin-bottom: 1rem !important;
    color: var(--text-primary) !important;
}

/* ── Impact Cards ── */
.impact-card {
    background: linear-gradient(135deg, var(--bg-card), rgba(16, 185, 129, 0.05)) !important;
    border-radius: 16px !important;
    padding: 2rem 1.5rem !important;
    text-align: center;
    border: 1px solid var(--border-color) !important;
    transition: all 0.3s ease;
}

.impact-card:hover {
    transform: translateY(-3px);
    box-shadow: var(--shadow-card);
    border-color: var(--accent-green) !important;
}

.impact-icon {
    font-size: 3rem !important;
    margin-bottom: 0.75rem !important;
    display: block;
}

.impact-value {
    font-size: 1.8rem !important;
    font-weight: 800 !important;
    margin-bottom: 0.25rem !important;
}

.impact-label {
    font-size: 0.8rem !important;
    color: var(--text-secondary) !important;
}

/* ── Tip Cards ── */
.tip-card {
    background: var(--bg-card) !important;
    border-left: 4px solid var(--accent-green) !important;
    border-radius: 0 12px 12px 0 !important;
    padding: 1rem 1.25rem !important;
    margin-bottom: 0.75rem !important;
    color: var(--text-secondary) !important;
    font-size: 0.9rem !important;
    line-height: 1.6 !important;
    transition: all 0.2s ease;
}

.tip-card:hover {
    background: var(--bg-card-hover) !important;
    padding-left: 1.5rem !important;
}

/* ── Divider ── */
hr {
    border: none !important;
    height: 1px !important;
    background: linear-gradient(90deg, transparent, var(--border-color), transparent) !important;
    margin: 2rem 0 !important;
}

/* ── Sidebar Styling ── */
.sidebar-section {
    margin-bottom: 1.5rem !important;
    padding-bottom: 1rem !important;
    border-bottom: 1px solid var(--border-color) !important;
}

.sidebar-section:last-child {
    border-bottom: none !important;
}

.sidebar-title {
    font-size: 0.85rem !important;
    font-weight: 700 !important;
    color: var(--text-primary) !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 0.75rem !important;
    display: flex;
    align-items: center;
    gap: 8px;
}

/* ── Data Table Styling ── */
.stDataFrame td, .stDataFrame th {
    padding: 12px 16px !important;
    font-size: 0.9rem !important;
}

.stDataFrame th {
    background: var(--bg-secondary) !important;
    font-weight: 600 !important;
    color: var(--text-primary) !important;
    border-bottom: 2px solid var(--border-color) !important;
}

.stDataFrame td {
    border-bottom: 1px solid var(--border-light) !important;
    color: var(--text-secondary) !important;
}

.stDataFrame tr:hover td {
    background: var(--bg-card-hover) !important;
    color: var(--text-primary) !important;
}

/* ── Metric Styling ── */
[data-testid="stMetricValue"] {
    font-size: 1.5rem !important;
    font-weight: 700 !important;
}

[data-testid="stMetricLabel"] {
    font-size: 0.8rem !important;
    color: var(--text-muted) !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: var(--bg-primary);
}

::-webkit-scrollbar-thumb {
    background: var(--border-color);
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: var(--text-muted);
}

/* ── Animations ── */
@keyframes fadeIn {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
}

.stTabs [data-baseweb="tab-panel"] {
    animation: fadeIn 0.3s ease;
}

/* ── Responsive ── */
@media (max-width: 768px) {
    .hero { padding: 1.5rem !important; }
    .hero h1 { font-size: 2rem !important; }
    .kpi-grid { grid-template-columns: repeat(2, 1fr); }
    .badge-grid { grid-template-columns: repeat(2, 1fr); }
    .ticker { padding: 0.75rem 1rem !important; }
}
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# 6. Session state
# ──────────────────────────────────────────────────────────────

_SS_DEFAULTS = {
    "appliance":        "Water Motor",
    "kw":               0.75,
    "duration":         30,
    "deadline":         "08:00",
    "loc_mode":         "Auto-Detect",
    "region":           "India",
    "zone":             "North India (NR)",
    "lat":              28.6139,
    "lon":              77.2090,
    "tz":               "Asia/Kolkata",
    "data_source":      "Automatic (API)",
    "api_token":        "",
    "live_mode":        True,
    "refresh_s":        300,
    "alert_enabled":    True,
    "alert_thresh":     200,
    "daily_budget_kg":  1.0,
    "sel_window":       0,
    "upload_hash":      "",
    "upload_df_json":   "",
}

for _k, _v in _SS_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ──────────────────────────────────────────────────────────────
# 7. Auto-refresh
# ──────────────────────────────────────────────────────────────

if st.session_state.live_mode:
    _ms = int(st.session_state.refresh_s) * 1000
    components.html(
        f"<script>setTimeout(()=>window.parent.location.reload(),{_ms});</script>",
        height=0,
    )


# ──────────────────────────────────────────────────────────────
# 8. SIDEBAR
# ──────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("<div class='sidebar-section'>", unsafe_allow_html=True)
    st.markdown("<div class='sidebar-title'>🔄 Live Mode</div>", unsafe_allow_html=True)
    st.session_state.live_mode = st.toggle(
        "Auto-refresh", value=st.session_state.live_mode, key="_live"
    )
    if st.session_state.live_mode:
        _ri_map = {60: "1 min", 120: "2 min", 300: "5 min", 600: "10 min"}
        st.session_state.refresh_s = st.select_slider(
            "Interval",
            options=list(_ri_map.keys()),
            value=st.session_state.refresh_s,
            format_func=lambda x: _ri_map[x],
        )
        st.markdown(
            f"<div style='background: linear-gradient(135deg, rgba(16, 185, 129, 0.1), rgba(6, 182, 212, 0.1)); "
            f"border: 1px solid rgba(16, 185, 129, 0.25); border-radius: 10px; padding: 10px 14px; margin-top: 8px;'>"
            f"<span style='color: #10b981; font-size: 0.8rem; font-weight: 600;'>"
            f"🟢 Live — every {_ri_map[st.session_state.refresh_s]}</span></div>",
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='sidebar-section'>", unsafe_allow_html=True)
    st.markdown("<div class='sidebar-title'>📍 Location</div>", unsafe_allow_html=True)
    _LOC_MODES = ["Auto-Detect", "GPS (Browser)", "Manual Select", "Custom Coordinates"]
    st.session_state.loc_mode = st.radio(
        "Mode", _LOC_MODES,
        index=_LOC_MODES.index(st.session_state.loc_mode)
              if st.session_state.loc_mode in _LOC_MODES else 0,
        label_visibility="collapsed",
    )

    if st.session_state.loc_mode == "Auto-Detect":
        if st.button("🔍 Detect via IP", use_container_width=True):
            with st.spinner("Locating…"):
                _loc = detect_ip_location()
            if _loc:
                st.session_state.lat  = _loc["lat"]
                st.session_state.lon  = _loc["lon"]
                st.session_state.tz   = _loc.get("timezone", "UTC")
                st.success(f"📍 {_loc['city']}, {_loc['country']}")
                st.rerun()
            else:
                st.error("Detection failed — try Manual Select.")
        st.markdown(
            f"<div style='background: var(--bg-secondary); padding: 12px 14px; border-radius: 10px; margin-top: 8px; border: 1px solid var(--border-color);'>"
            f"<div style='color: var(--text-muted); font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px;'>Coordinates</div>"
            f"<div style='color: #10b981; font-size: 0.9rem; font-weight: 600;'>"
            f"{st.session_state.lat:.4f}, {st.session_state.lon:.4f}</div></div>",
            unsafe_allow_html=True,
        )

    elif st.session_state.loc_mode == "GPS (Browser)":
        components.html("""
<style>
html,body{margin:0;padding:10px;background:transparent;font-family:'Inter',sans-serif;}
#btn{width:100%;padding:10px;background:linear-gradient(135deg,#1e3a5f,#0f172a);
     color:#10b981;border:1.5px solid rgba(16,185,129,.45);border-radius:10px;
     cursor:pointer;font-weight:600;font-size:13px;transition:all .2s;}
#btn:hover{background:rgba(16,185,129,.12);transform:translateY(-1px);}
#btn:disabled{opacity:.6;}
#msg{color:#64748b;font-size:11px;margin-top:5px;text-align:center;min-height:16px;}
.c{background:#1f2937;padding:10px;border-radius:8px;margin-top:8px;
   color:#10b981;font-size:12px;font-weight:600;display:none;border:1px solid rgba(16,185,129,.2);}
</style>
<button id="btn" onclick="go()">📡 Use GPS</button>
<div id="msg"></div><div id="c" class="c"></div>
<script>
function go(){
  var btn=document.getElementById('btn'),msg=document.getElementById('msg'),c=document.getElementById('c');
  if(!navigator.geolocation){msg.innerHTML='<span style="color:#ef4444">Not supported</span>';return;}
  btn.textContent='⏳ Locating…';btn.disabled=true;msg.textContent='Waiting…';
  navigator.geolocation.getCurrentPosition(
    p=>{btn.textContent='✅ Done';c.style.display='block';
        c.innerHTML='📍 '+p.coords.latitude.toFixed(5)+', '+p.coords.longitude.toFixed(5);},
    e=>{btn.textContent='📡 Use GPS';btn.disabled=false;
        msg.innerHTML='<span style="color:#ef4444">'+(e.code===1?'Permission denied':'Error')+'</span>';},
    {enableHighAccuracy:true,timeout:12000,maximumAge:0}
  );
}
</script>""", height=120)
        _ga, _gb = st.columns(2)
        with _ga: _glat = st.number_input("Lat",  -90.0,  90.0, st.session_state.lat, 0.0001, "%.5f")
        with _gb: _glon = st.number_input("Lon", -180.0, 180.0, st.session_state.lon, 0.0001, "%.5f")
        if st.button("✅ Apply GPS", use_container_width=True):
            st.session_state.lat = _glat
            st.session_state.lon = _glon
            st.rerun()

    elif st.session_state.loc_mode == "Manual Select":
        _reg = st.selectbox(
            "Region", list(GRID_ZONES.keys()),
            index=list(GRID_ZONES.keys()).index(st.session_state.region)
                  if st.session_state.region in GRID_ZONES else 0,
        )
        st.session_state.region = _reg
        _zones = GRID_ZONES[_reg]["zones"]
        _zone  = st.selectbox(
            "Zone", list(_zones.keys()),
            index=list(_zones.keys()).index(st.session_state.zone)
                  if st.session_state.zone in _zones else 0,
        )
        st.session_state.zone = _zone
        _zd = _zones[_zone]
        st.session_state.lat, st.session_state.lon = _zd["lat"], _zd["lon"]
        st.session_state.tz = GRID_ZONES[_reg]["timezone"]
        st.markdown(
            f"<div style='background: var(--bg-secondary); padding: 12px 14px; border-radius: 10px; margin-top: 8px; border: 1px solid var(--border-color);'>"
            f"<div style='color: var(--text-muted); font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px;'>Zone Avg CI</div>"
            f"<div style='color: #10b981; font-size: 1rem; font-weight: 600;'>{_zd['ci_avg']} gCO₂/kWh</div>"
            f"<div style='color: var(--text-muted); font-size: 0.7rem; margin-top: 4px;'>{GRID_ZONES[_reg]['voltage']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    else:  # Custom Coordinates
        _cc, _cd = st.columns(2)
        with _cc: st.session_state.lat = st.number_input("Lat",  -90.0,  90.0, st.session_state.lat, format="%.4f")
        with _cd: st.session_state.lon = st.number_input("Lon", -180.0, 180.0, st.session_state.lon, format="%.4f")
        _tz_list = pytz.common_timezones
        st.session_state.tz = st.selectbox(
            "Timezone", _tz_list,
            index=_tz_list.index(st.session_state.tz) if st.session_state.tz in _tz_list else 0,
        )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='sidebar-section'>", unsafe_allow_html=True)
    st.markdown(f"<div class='live-time'>🕐 {exact_time_str(st.session_state.tz)}</div>", unsafe_allow_html=True)
    _slot = slot_str(st.session_state.tz)
    st.markdown(f"<div class='tz-sub'>{st.session_state.tz} &nbsp;|&nbsp; slot {to_12h(_slot)}</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='sidebar-section'>", unsafe_allow_html=True)
    st.markdown("<div class='sidebar-title'>📡 Data Source</div>", unsafe_allow_html=True)
    _DS = ["Automatic (API)", "Sample Data", "Upload CSV"]
    st.session_state.data_source = st.radio(
        "Source", _DS,
        index=_DS.index(st.session_state.data_source)
              if st.session_state.data_source in _DS else 0,
        label_visibility="collapsed",
    )
    _upload_file = None

    if st.session_state.data_source == "Automatic (API)":
        st.caption("Electricity Maps API + real-time simulation.")
        st.session_state.api_token = st.text_input(
            "API Token (optional)", type="password",
            value=st.session_state.api_token, placeholder="Free-tier token"
        )
        if st.button("🔄 Force Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    elif st.session_state.data_source == "Upload CSV":
        st.warning("Upload mode — expects columns: time, thermal_mw, hydro_mw, nuclear_mw, res_mw")
        _upload_file = st.file_uploader("CSV", type=["csv"], label_visibility="collapsed")

    else:
        st.caption("Built-in synthetic grid profile.")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='sidebar-section'>", unsafe_allow_html=True)
    st.markdown("<div class='sidebar-title'>🔌 Appliance</div>", unsafe_allow_html=True)
    _prev = st.session_state.appliance
    st.session_state.appliance = st.selectbox(
        "Type", list(APPLIANCES.keys()),
        index=list(APPLIANCES.keys()).index(st.session_state.appliance),
        label_visibility="collapsed",
    )
    if st.session_state.appliance != _prev:
        _ap = APPLIANCES[st.session_state.appliance]
        st.session_state.kw       = _ap["kw"]
        st.session_state.duration = _ap["dur"]
        st.session_state.deadline = _ap["deadline"]

    st.session_state.kw = st.number_input(
        "Power (kW)", min_value=0.001, value=float(st.session_state.kw), step=0.05, format="%.3f"
    )
    st.session_state.duration = st.number_input(
        "Duration (min)", min_value=15, value=int(st.session_state.duration), step=15
    )
    _dl = st.text_input("Deadline (HH:MM)", value=st.session_state.deadline)
    if valid_hhmm(_dl):
        st.session_state.deadline = _dl
    else:
        st.error("⚠️ Invalid time — use HH:MM (24-h)")

    _avail = (hm_to_mins(st.session_state.deadline) - hm_to_mins(_slot)) % 1440
    st.info(f"⏱️ **{_avail} min** until deadline ({_avail // 60}h {_avail % 60}m)")

    if st.button("↩️ Reset Defaults", use_container_width=True):
        _ap = APPLIANCES[st.session_state.appliance]
        st.session_state.kw       = _ap["kw"]
        st.session_state.duration = _ap["dur"]
        st.session_state.deadline = _ap["deadline"]
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='sidebar-section'>", unsafe_allow_html=True)
    st.markdown("<div class='sidebar-title'>⚡ Optimisation</div>", unsafe_allow_html=True)
    _top_k = st.slider("Top windows", 3, 12, 5)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='sidebar-section'>", unsafe_allow_html=True)
    st.markdown("<div class='sidebar-title'>🔔 Alerts & Budget</div>", unsafe_allow_html=True)
    st.session_state.alert_enabled = st.toggle(
        "Enable CI alerts", value=st.session_state.alert_enabled
    )
    if st.session_state.alert_enabled:
        st.session_state.alert_thresh = st.slider(
            "Alert below (gCO₂/kWh)", 50, 400, st.session_state.alert_thresh, 25
        )
    st.session_state.daily_budget_kg = st.number_input(
        "Daily CO₂ budget (kg)", 0.1, 10.0,
        float(st.session_state.daily_budget_kg), 0.1, "%.1f"
    )
    st.markdown("</div>", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# 9. Data loading
# ──────────────────────────────────────────────────────────────

ensure_dirs()
_seed = int(time.time() // st.session_state.refresh_s)

if st.session_state.data_source == "Automatic (API)":
    with st.spinner("🌐 Fetching real-time grid data…"):
        _raw = DataEngine.fetch_live(
            st.session_state.lat, st.session_state.lon,
            st.session_state.api_token, _seed,
        )
elif st.session_state.data_source == "Sample Data":
    _raw = DataEngine.load_sample(_seed)
else:  # Upload CSV
    if _upload_file is not None:
        _file_hash = hashlib.md5(_upload_file.getvalue()).hexdigest()
        if _file_hash != st.session_state.upload_hash:
            try:
                _upload_df = DataEngine.from_upload(_upload_file.getvalue())
                st.session_state.upload_hash    = _file_hash
                st.session_state.upload_df_json = _upload_df.to_json(orient="records")
            except ValueError as exc:
                st.error(str(exc))
                st.stop()
        _raw = pd.read_json(st.session_state.upload_df_json)
    elif st.session_state.upload_df_json:
        _raw = pd.read_json(st.session_state.upload_df_json)
    else:
        st.info("⬆️ Upload a CSV or switch to Sample / Automatic mode.")
        st.stop()

if _raw is None or _raw.empty:
    st.error("❌ No grid data available.")
    st.stop()

# Ensure CI column exists
if "ci" not in _raw.columns:
    _raw["ci"] = _raw.apply(
        lambda r: compute_ci(r["thermal_mw"], r["hydro_mw"], r["nuclear_mw"], r["res_mw"]), axis=1
    )

# Build derived datasets
_full      = DataEngine.full_day(_raw)
_now_slot  = slot_str(st.session_state.tz)
_now_12    = to_12h(_now_slot)
_dl_12     = to_12h(st.session_state.deadline)
_windows   = DataEngine.windows(
    _full, st.session_state.duration, st.session_state.deadline, _now_slot, _top_k
)
_cur_ci    = DataEngine.ci_at(_full, _now_slot) or float(_full["ci"].mean())
_best      = _windows[0] if _windows else None
_nrg       = st.session_state.kw * (ceil15(st.session_state.duration) / 60)
_pot_pct   = (((_cur_ci - _best["avg_ci"]) / _cur_ci) * 100) if (_best and _cur_ci > 0) else 0
_weekly    = DataEngine.weekly_forecast(float(_full["ci"].mean()))
_log_df    = read_log()
_stats     = compute_stats(_log_df)

# Location label
_loc_lbl = (
    st.session_state.zone  if st.session_state.loc_mode == "Manual Select"
    else "GPS"             if st.session_state.loc_mode == "GPS (Browser)"
    else f"{st.session_state.lat:.2f}°, {st.session_state.lon:.2f}°"
)


# ──────────────────────────────────────────────────────────────
# 10. HERO BANNER
# ──────────────────────────────────────────────────────────────

_live_pill = (
    '<span class="pill pill-green"><span style="width:8px;height:8px;border-radius:50%;'
    'background:#10b981;display:inline-block;animation:pulse-dot 2s infinite;box-shadow:0 0 8px rgba(16,185,129,.5);"></span> LIVE</span>'
    if st.session_state.live_mode else ""
)

st.markdown(f"""
<div class="hero">
  <h1>🌍 CarbonWise</h1>
  <p>Real-time carbon intensity scheduler — run appliances during the cleanest
     grid windows to shrink your CO₂ footprint automatically.</p>
  <div class="hero-pills">
    {_live_pill}
    <span class="pill pill-blue">⚡ Smart Scheduler</span>
    <span class="pill pill-amber">🏅 Gamified</span>
    <span class="pill pill-purple">📊 Analytics</span>
    <span class="pill-muted">🕐 {exact_time_str(st.session_state.tz)} &nbsp;·&nbsp; {_loc_lbl}</span>
  </div>
</div>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# 11. Alert banner
# ──────────────────────────────────────────────────────────────

if st.session_state.alert_enabled:
    if _cur_ci <= st.session_state.alert_thresh:
        st.markdown(
            f'<div class="alert alert-g">'
            f'<strong style="color:#10b981;font-size:1.1rem;">🟢 GREEN GRID ALERT!</strong>'
            f'<span style="color:#94a3b8;margin-left:8px;"> CI is <strong style="color:#10b981;">{_cur_ci:.0f}</strong> gCO₂/kWh'
            f' — below your {st.session_state.alert_thresh} g threshold. Great time to run appliances!</span></div>',
            unsafe_allow_html=True,
        )
    elif _cur_ci > 400:
        st.markdown(
            f'<div class="alert alert-r">'
            f'<strong style="color:#ef4444;font-size:1.1rem;">🔴 HIGH CARBON ALERT</strong>'
            f'<span style="color:#94a3b8;margin-left:8px;"> CI is <strong style="color:#ef4444;">{_cur_ci:.0f}</strong> gCO₂/kWh'
            f' — consider delaying non-urgent appliances.</span></div>',
            unsafe_allow_html=True,
        )


# ──────────────────────────────────────────────────────────────
# 12. Live ticker
# ──────────────────────────────────────────────────────────────

_trend = "↑" if _cur_ci > float(_full["ci"].mean()) else "↓"
_tc    = ci_color(_cur_ci)

st.markdown(f"""
<div class="ticker">
  <div class="ticker-dot"></div>
  <span style="color:var(--text-muted);font-size:.8rem;font-weight:600;white-space:nowrap;">LIVE GRID</span>
  <span style="color:{_tc};font-weight:700;font-size:.9rem;">
    {ci_emoji(_cur_ci)} {_cur_ci:.0f} gCO₂/kWh {_trend}
  </span>
  <span style="color:var(--text-muted);">·</span>
  <span style="color:var(--accent-blue);font-size:.8rem;">Best: {to_12h(_best["start"]) if _best else "--"}</span>
  <span style="color:var(--text-muted);">·</span>
  <span style="color:var(--accent-amber);font-size:.8rem;">🔥 {_stats["streak"]}-day streak</span>
  <span style="color:var(--text-muted);">·</span>
  <span style="color:var(--accent-green);font-size:.8rem;">Saved {_stats["co2"]:.3f} kg CO₂ total</span>
  <span style="color:var(--text-muted);">·</span>
  <span style="color:var(--text-secondary);font-size:.8rem;">{st.session_state.data_source}</span>
</div>
<div style="margin-bottom:1rem;"></div>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# 13. KPI row
# ──────────────────────────────────────────────────────────────

st.markdown("<div class='section-heading'>📊 Session Overview</div>", unsafe_allow_html=True)

_k = st.columns(6)

def _kpi(col, label, value, sub, border="#10b981", val_color="white"):
    col.markdown(
        f'<div class="kpi" style="border-top-color:{border};">'
        f'<div class="kpi-lbl">{label}</div>'
        f'<div class="kpi-val" style="color:{val_color};">{value}</div>'
        f'<div class="kpi-sub">{sub}</div></div>',
        unsafe_allow_html=True,
    )

_kpi(_k[0], "🌍 Location", _loc_lbl[:22], st.session_state.tz)
_kpi(_k[1], "🕐 Now", _now_12, "🟢 Live" if st.session_state.live_mode else "Static")
_icon = APPLIANCES[st.session_state.appliance]["icon"]
_kpi(_k[2], "🔌 Appliance",
     f'{_icon} {st.session_state.appliance.split("/")[0][:14]}',
     f'{st.session_state.kw:.2f} kW · {ceil15(st.session_state.duration)} min')
_kpi(_k[3], "⚡ CI Now", f"{_cur_ci:.0f}", f"{ci_label(_cur_ci)} · gCO₂/kWh",
     border=ci_color(_cur_ci), val_color=ci_color(_cur_ci))
_kpi(_k[4], "✨ Best Window",
     to_12h(_best["start"]) if _best else "--:--",
     f"Save ~{_pot_pct:.1f}%" if _pot_pct > 0 else "No gap",
     border="#10b981" if _best else "#64748b",
     val_color="#10b981" if _best else "#64748b")
_sc = "#10b981" if _stats["streak"] >= 3 else "#f59e0b" if _stats["streak"] else "#64748b"
_kpi(_k[5], "🔥 Streak", f'{_stats["streak"]} days',
     f'{len(_stats["badges"])} badges · {_stats["runs"]} runs', border=_sc, val_color=_sc)

st.markdown("<hr>", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# 14. MAIN TABS
# ──────────────────────────────────────────────────────────────

T1, T2, T3, T4, T5, T6, T7 = st.tabs([
    "📈 Dashboard", "🎯 Smart Advisor", "🌤️ Weekly Forecast",
    "📊 Analytics", "🏅 Achievements", "📝 Logger", "🔍 Data",
])


# ══════════════════════════════════════════════
# TAB 1 — Dashboard
# ══════════════════════════════════════════════
with T1:
    col_main, col_side = st.columns([2, 1])

    with col_main:
        st.markdown("<div class='sub-heading'>24-Hour Carbon Intensity · {st.session_state.tz}</div>", unsafe_allow_html=True)

        # Gauge
        fig_g = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=_cur_ci,
            delta={"reference": float(_full["ci"].mean()), "valueformat": ".0f", "suffix": " avg"},
            title={"text": "Current CI (gCO₂/kWh)", "font": {"color": "#94a3b8", "size": 13}},
            number={"font": {"color": ci_color(_cur_ci), "size": 36}},
            gauge={
                "axis":    {"range": [0, 700], "tickcolor": "#475569", "tickfont": {"color": "#475569", "size": 10}},
                "bar":     {"color": ci_color(_cur_ci), "thickness": 0.25},
                "bgcolor": "#111827", "borderwidth": 0,
                "steps":   [
                    {"range": [0, 200],   "color": "rgba(16,185,129,.1)"},
                    {"range": [200, 400], "color": "rgba(245,158,11,.08)"},
                    {"range": [400, 700], "color": "rgba(239,68,68,.08)"},
                ],
                "threshold": {"line": {"color": "#10b981", "width": 2}, "thickness": 0.75, "value": _cur_ci},
            },
        ))
        fig_g.update_layout(
            paper_bgcolor="#0a0f1a", font={"color": "#cbd5e1"},
            height=200, margin={"l": 30, "r": 30, "t": 30, "b": 10},
        )
        st.plotly_chart(fig_g, use_container_width=True)

        # 24-h line chart
        fig_l = go.Figure()
        fig_l.add_trace(go.Scatter(
            x=_full["time"], y=_full["ci"],
            fill="tozeroy", fillcolor="rgba(16,185,129,.06)",
            line={"color": "#10b981", "width": 2.5},
            hovertemplate="<b>%{x}</b><br>CI: %{y:.1f} gCO₂/kWh<extra></extra>",
        ))
        fig_l.add_hrect(y0=0,   y1=200, fillcolor="#10b981", opacity=0.03, line_width=0)
        fig_l.add_hrect(y0=200, y1=400, fillcolor="#f59e0b", opacity=0.03, line_width=0)
        fig_l.add_hrect(y0=400, y1=900, fillcolor="#ef4444", opacity=0.03, line_width=0)
        for wi, w in enumerate((_windows or [])[:3]):
            fig_l.add_vrect(
                x0=w["start"], x1=w["end"],
                fillcolor=w["color"], opacity=0.15,
                line_width=2 if wi == 0 else 1, line_color=w["color"], layer="below",
            )
        _ymax = float(_full["ci"].max()) * 1.12
        fig_l.add_vline(x=_now_slot, line_dash="dash", line_color="#10b981", line_width=1.8)
        fig_l.add_vline(x=st.session_state.deadline, line_dash="dash", line_color="#ef4444", line_width=1.8)
        fig_l.add_annotation(x=_now_slot, y=_ymax, text="NOW",
                              showarrow=False, font={"color": "#10b981", "size": 11},
                              bgcolor="rgba(10,15,26,.9)")
        fig_l.add_annotation(x=st.session_state.deadline, y=_ymax, text="DEADLINE",
                              showarrow=False, font={"color": "#ef4444", "size": 11},
                              bgcolor="rgba(10,15,26,.9)")
        fig_l.add_hline(y=float(_full["ci"].mean()), line_dash="dot", line_color="#3b82f6",
                        line_width=1, annotation_text="Daily avg",
                        annotation_font_color="#3b82f6")
        fig_l.update_layout(
            plot_bgcolor="#0a0f1a", paper_bgcolor="#0a0f1a", font={"color": "#cbd5e1"},
            xaxis={"gridcolor": "#1e293b", "title": "Time of Day", "tickangle": -45, "nticks": 13},
            yaxis={"gridcolor": "#1e293b", "title": "gCO₂/kWh", "range": [0, _ymax * 1.1]},
            height=360, margin={"l": 55, "r": 30, "t": 20, "b": 55}, showlegend=False,
        )
        st.plotly_chart(fig_l, use_container_width=True)

    with col_side:
        st.markdown("<div class='sub-heading'>Grid Mix</div>", unsafe_allow_html=True)
        _last = _raw.iloc[-1]
        _vals = [safe_float(_last.get(c, 0)) for c in ["thermal_mw", "hydro_mw", "nuclear_mw", "res_mw"]]
        _tot  = sum(_vals) or 1
        fig_p = go.Figure(data=[go.Pie(
            labels=["Thermal", "Hydro", "Nuclear", "Renewable"], values=_vals,
            hole=0.65, marker_colors=["#ef4444", "#3b82f6", "#8b5cf6", "#10b981"],
            textinfo="label+percent", textfont={"color": "white", "size": 9},
        )])
        fig_p.update_layout(
            paper_bgcolor="#0a0f1a", height=275,
            showlegend=False, margin={"t": 10, "b": 10, "l": 10, "r": 10},
            annotations=[{"text": f"<b>{_tot/1000:.1f}</b><br>GW",
                           "x": 0.5, "y": 0.5, "font": {"size": 16, "color": "#f8fafc"}, "showarrow": False}],
        )
        st.plotly_chart(fig_p, use_container_width=True)

        # CO₂ equivalents
        st.markdown("<div class='sub-heading'>🌿 CO₂ Equivalents</div>", unsafe_allow_html=True)
        _co2_now = co2_kg(_nrg, _cur_ci)
        _co2_opt = co2_kg(_nrg, _best["avg_ci"]) if _best else _co2_now
        _saved   = max(0.0, _co2_now - _co2_opt)
        st.markdown(
            f'<div class="info-card" style="padding: 14px !important;">'
            f'<div style="color:var(--text-muted);font-size:.75rem;margin-bottom:8px;">Running now: {_co2_now:.3f} kg CO₂</div>'
            f'<div style="color:var(--accent-green);font-size:.85rem;font-weight:600;margin-bottom:8px;">Optimising saves:</div>',
            unsafe_allow_html=True,
        )
        for lbl, val in co2_equivalents(_saved).items():
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;padding:6px 0;'
                f'border-bottom:1px solid var(--border-light);font-size:.8rem;">'
                f'<span style="color:var(--text-secondary);">{lbl}</span>'
                f'<span style="color:white;font-weight:600;">{val:.2f}</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════
# TAB 2 — Smart Advisor
# ══════════════════════════════════════════════
with T2:
    st.markdown(f"<div class='section-heading'>🏆 Top {_top_k} Optimal Windows · {_now_12} → {_dl_12}</div>", unsafe_allow_html=True)

    if not _windows:
        st.warning("⚠️ No valid windows found — extend the deadline or reduce duration.")
        st.info(
            f"Debug: now={_now_12} · deadline={_dl_12} · "
            f"duration={ceil15(st.session_state.duration)} min · available={_avail} min"
        )
    else:
        for _ri in range(0, len(_windows), 3):
            _row_wins = _windows[_ri : _ri + 3]
            _cols     = st.columns(len(_row_wins))
            for _ci2, (_col, _w) in enumerate(zip(_cols, _row_wins)):
                _gi   = _ri + _ci2
                _rank = _gi + 1
                _is_best = _gi == 0
                _co2w = co2_kg(_nrg, _w["avg_ci"])
                _co2n = co2_kg(_nrg, _cur_ci)
                _sav  = max(0.0, _co2n - _co2w)
                _ppct = (_sav / _co2n * 100) if _co2n > 0 else 0
                _conf = max(60, min(99, 100 - int((_w["max_ci"] - _w["min_ci"]) / 5)))
                _eq_t = _sav / CO2_EQUIV["🌳 Tree-days (absorption)"]
                _eq_k = _sav / CO2_EQUIV["🚗 km not driven"]
                _cls = "rec-card best" if _is_best else "rec-card"
                with _col:
                    st.markdown(f"""
<div class="{_cls}">
  <div style="text-align:center;margin-bottom:12px;">
    <span style="background:{'#10b981' if _is_best else '#1e3a5f'};
          color:{'#0f172a' if _is_best else '#94a3b8'};
          padding:4px 12px;border-radius:6px;font-size:.75rem;font-weight:700;">
      {'🥇 BEST' if _rank==1 else f'#{_rank}'}
    </span>
    <span style="background:rgba(16,185,129,.1);color:#10b981;
          padding:3px 8px;border-radius:4px;font-size:.68rem;font-weight:600;margin-left:6px;">
      {_conf}% conf.
    </span>
  </div>
  <div style="font-size:1.15rem;font-weight:700;color:white;text-align:center;margin-bottom:12px;">
    {to_12h(_w['start'])} — {to_12h(_w['end'])}
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px;">
    <div style="background:var(--bg-secondary);padding:10px 6px;border-radius:10px;text-align:center;">
      <div style="color:var(--text-muted);font-size:.65rem;margin-bottom:3px;">CI avg</div>
      <div style="color:{_w['color']};font-size:1rem;font-weight:700;">{_w['avg_ci']:.0f}</div>
    </div>
    <div style="background:var(--bg-secondary);padding:10px 6px;border-radius:10px;text-align:center;">
      <div style="color:var(--text-muted);font-size:.65rem;margin-bottom:3px;">CO₂</div>
      <div style="color:white;font-size:1rem;font-weight:700;">{_co2w:.3f} kg</div>
    </div>
  </div>
  <div style="background:rgba(16,185,129,.08);padding:8px 10px;border-radius:10px;
       border:1px solid rgba(16,185,129,.2);margin-bottom:6px;text-align:center;">
    <span style="color:var(--accent-green);font-size:.82rem;font-weight:600;">
      💰 Save {_sav:.3f} kg ({_ppct:.0f}%)
    </span>
  </div>
  <div style="font-size:.72rem;color:var(--text-muted);text-align:center;">
    ≈ {_eq_t:.1f} tree-days · {_eq_k:.1f} km not driven
  </div>
</div>""", unsafe_allow_html=True)
                    if st.button("✅ Select", key=f"sel_{_gi}", use_container_width=True):
                        st.session_state.sel_window = _gi
                        st.success(f"Selected: {to_12h(_w['start'])} – {to_12h(_w['end'])}")

    # Heatmap
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<div class='sub-heading'>🗓️ Today's CI Heatmap (24 h)</div>", unsafe_allow_html=True)
    _hdata = _full["ci"].values.reshape(6, 16) if len(_full) == 96 else np.tile(_full["ci"].values[:1], (6, 16))
    fig_h = go.Figure(data=go.Heatmap(
        z=_hdata,
        colorscale=[[0, "#10b981"], [0.4, "#f59e0b"], [1, "#ef4444"]],
        showscale=True,
        colorbar={"title": "gCO₂/kWh", "tickfont": {"color": "#94a3b8"}},
        hovertemplate="CI: %{z:.1f} gCO₂/kWh<extra></extra>",
    ))
    fig_h.update_layout(
        paper_bgcolor="#0a0f1a", height=190,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
        xaxis={"showticklabels": False}, yaxis={"showticklabels": False},
    )
    st.plotly_chart(fig_h, use_container_width=True)


# ══════════════════════════════════════════════
# TAB 3 — Weekly Forecast
# ══════════════════════════════════════════════
with T3:
    st.markdown("<div class='section-heading'>🌤️ 7-Day Grid CI Forecast</div>", unsafe_allow_html=True)
    st.caption("Simulated forecast based on day-of-week demand patterns + current grid conditions.")

    fig_w = go.Figure()
    fig_w.add_trace(go.Bar(name="Night", x=_weekly["date"], y=_weekly["ci_night"],
                           marker_color="#10b981", opacity=0.8))
    fig_w.add_trace(go.Bar(name="Day",   x=_weekly["date"], y=_weekly["ci_day"],
                           marker_color="#3b82f6", opacity=0.8))
    fig_w.add_trace(go.Bar(name="Peak",  x=_weekly["date"], y=_weekly["ci_peak"],
                           marker_color="#ef4444", opacity=0.8))
    fig_w.add_trace(go.Scatter(
        name="Daily avg", x=_weekly["date"], y=_weekly["avg"],
        mode="lines+markers", line={"color": "#f59e0b", "width": 2, "dash": "dot"},
        marker={"size": 7},
    ))
    fig_w.update_layout(
        barmode="group", plot_bgcolor="#0a0f1a", paper_bgcolor="#0a0f1a",
        font={"color": "#cbd5e1"}, height=370,
        legend={"orientation": "h", "y": 1.05, "x": 0.5, "xanchor": "center"},
        xaxis={"gridcolor": "#1e293b"},
        yaxis={"gridcolor": "#1e293b", "title": "gCO₂/kWh"},
        margin={"l": 50, "r": 30, "t": 50, "b": 40},
    )
    st.plotly_chart(fig_w, use_container_width=True)

    st.markdown("<div class='sub-heading'>📅 Daily Outlook</div>", unsafe_allow_html=True)
    _dc = st.columns(7)
    for _di, (_dcol, _dr) in enumerate(zip(_dc, _weekly.itertuples())):
        with _dcol:
            _best_p = (
                "Night" if _dr.ci_night <= _dr.ci_day and _dr.ci_night <= _dr.ci_peak
                else "Day" if _dr.ci_day <= _dr.ci_peak
                else "Peak"
            )
            _icon_d = "🌙" if _best_p == "Night" else "☀️" if _best_p == "Day" else "⚡"
            _bdr_d  = "2px solid #10b981" if _di == 0 else "1px solid var(--border-color)"
            st.markdown(f"""
<div style="background:var(--bg-card);border-radius:12px;padding:12px 8px;
     text-align:center;border:{_bdr_d};">
  <div style="color:var(--text-muted);font-size:.7rem;font-weight:600;">{_dr.label}</div>
  <div style="font-size:1.2rem;margin:6px 0;">{_icon_d}</div>
  <div style="color:{ci_color(_dr.avg)};font-size:1rem;font-weight:700;">{_dr.avg:.0f}</div>
  <div style="color:var(--text-muted);font-size:.65rem;">gCO₂/kWh</div>
  <div style="background:rgba(16,185,129,.1);border-radius:4px;
       padding:3px 6px;margin-top:6px;font-size:.62rem;color:#10b981;">
    Best: {_best_p}
  </div>
</div>""", unsafe_allow_html=True)

    # Regional comparison
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<div class='section-heading'>🗺️ Regional CI Comparison</div>", unsafe_allow_html=True)
    _flat = [
        {"zone": z, "region": r, "ci": zd["ci_avg"]}
        for r, rd in GRID_ZONES.items()
        for z, zd in rd["zones"].items()
    ]
    _df_reg = pd.DataFrame(_flat).sort_values("ci")
    fig_reg = go.Figure(go.Bar(
        x=_df_reg["ci"], y=_df_reg["zone"], orientation="h",
        marker_color=[ci_color(c) for c in _df_reg["ci"]],
        text=_df_reg["ci"].astype(int), textposition="outside",
        textfont={"color": "#94a3b8", "size": 10},
    ))
    fig_reg.update_layout(
        plot_bgcolor="#0a0f1a", paper_bgcolor="#0a0f1a", font={"color": "#cbd5e1"},
        height=560, margin={"l": 190, "r": 60, "t": 20, "b": 40},
        xaxis={"gridcolor": "#1e293b", "title": "Avg CI (gCO₂/kWh)"},
        yaxis={"gridcolor": "#1e293b"},
    )
    st.plotly_chart(fig_reg, use_container_width=True)


# ══════════════════════════════════════════════
# TAB 4 — Analytics
# ══════════════════════════════════════════════
with T4:
    st.markdown("<div class='section-heading'>📊 Baseline vs Optimised</div>", unsafe_allow_html=True)

    _co2_now = co2_kg(_nrg, _cur_ci)
    _co2_opt = co2_kg(_nrg, _best["avg_ci"]) if _best else _co2_now
    _proj    = max(0.0, _co2_now - _co2_opt)
    _proj_p  = (_proj / _co2_now * 100) if _co2_now > 0 else 0

    _ac, _bc = st.columns([3, 2])
    with _ac:
        fig_c = go.Figure()
        fig_c.add_trace(go.Bar(
            name="Baseline (Now)", x=["CO₂ Emissions"], y=[_co2_now],
            marker_color="#ef4444", text=[f"{_co2_now:.3f} kg"], textposition="outside", width=0.28,
        ))
        fig_c.add_trace(go.Bar(
            name="Optimised (Best)", x=["CO₂ Emissions"], y=[_co2_opt],
            marker_color="#10b981", text=[f"{_co2_opt:.3f} kg"], textposition="outside", width=0.28,
        ))
        fig_c.update_layout(
            barmode="group", plot_bgcolor="#0a0f1a", paper_bgcolor="#0a0f1a",
            font={"color": "#cbd5e1"}, height=310,
            legend={"orientation": "h", "y": 1.05, "x": 0.5, "xanchor": "center"},
            yaxis={"title": "CO₂ (kg)", "gridcolor": "#1e293b",
                   "range": [0, max(_co2_now, _co2_opt) * 1.45]},
            margin={"l": 50, "r": 30, "t": 50, "b": 40},
            title={"text": f"💰 Potential saving: {_proj:.3f} kg CO₂ ({_proj_p:.1f}%)",
                   "font": {"size": 13, "color": "#10b981"}},
        )
        st.plotly_chart(fig_c, use_container_width=True)

    with _bc:
        st.markdown("<div class='sub-heading'>💰 Daily CO₂ Budget</div>", unsafe_allow_html=True)
        _today_str = datetime.now().strftime("%Y-%m-%d")
        _today_co2 = 0.0
        if not _log_df.empty and "date" in _log_df and "co2_kg" in _log_df:
            _today_co2 = float(_log_df[_log_df["date"].astype(str) == _today_str]["co2_kg"].sum())
        _budget    = st.session_state.daily_budget_kg
        _pct_used  = min(100.0, _today_co2 / max(_budget, 1e-6) * 100)
        _remaining = max(0.0, _budget - _today_co2)
        _bar_clr   = "#10b981" if _pct_used < 60 else "#f59e0b" if _pct_used < 90 else "#ef4444"
        st.markdown(f"""
<div class="info-card" style="padding: 18px !important;">
  <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
    <span style="color:var(--text-secondary);font-size:.85rem;">Daily Budget</span>
    <span style="color:white;font-size:.88rem;font-weight:600;">{_budget:.1f} kg CO₂</span>
  </div>
  <div class="pb"><div class="pb-fill" style="width:{_pct_used:.0f}%;background:{_bar_clr};"></div></div>
  <div style="display:flex;justify-content:space-between;margin-top:6px;">
    <span style="color:{_bar_clr};font-size:.8rem;font-weight:600;">Used {_today_co2:.3f} kg</span>
    <span style="color:var(--text-muted);font-size:.8rem;">Left {_remaining:.3f} kg</span>
  </div>
  <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border-light);">
    <div style="color:var(--text-muted);font-size:.72rem;margin-bottom:4px;">This session:</div>
    <div style="display:flex;justify-content:space-between;">
      <span style="color:#ef4444;font-size:.8rem;">Now {_co2_now:.3f} kg</span>
      <span style="color:#10b981;font-size:.8rem;">Optimal {_co2_opt:.3f} kg</span>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

        st.markdown(f"<div class='sub-heading'>🌍 {_proj:.3f} kg Savings Equals…</div>", unsafe_allow_html=True)
        for lbl, val in co2_equivalents(_proj).items():
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:12px;padding:8px 0;'
                f'border-bottom:1px solid var(--border-light);">'
                f'<span style="color:var(--text-secondary);font-size:.85rem;flex:1;">{lbl}</span>'
                f'<span style="color:var(--accent-green);font-weight:700;font-size:.88rem;">{val:.2f}</span></div>',
                unsafe_allow_html=True,
            )

    _m1, _m2, _m3, _m4 = st.columns(4)
    _m1.metric("🔴 Baseline",  f"{_co2_now:.3f} kg")
    _m2.metric("🟢 Optimised", f"{_co2_opt:.3f} kg")
    _m3.metric("💰 CO₂ Saved", f"{_proj:.3f} kg",  delta=f"-{_proj_p:.1f}%", delta_color="inverse")
    _m4.metric("⚡ Energy",    f"{_nrg:.3f} kWh")

    # Historical chart
    if not _log_df.empty and "timestamp" in _log_df and "co2_kg" in _log_df:
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown("<div class='section-heading'>📋 Cumulative CO₂ Over Time</div>", unsafe_allow_html=True)
        _ldf = _log_df.copy()
        _ldf["ts"] = pd.to_datetime(_ldf["timestamp"], errors="coerce")
        _ldf = _ldf.dropna(subset=["ts"]).sort_values("ts")
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(
            x=_ldf["ts"], y=_ldf["co2_kg"].cumsum(),
            mode="lines+markers", line={"color": "#10b981", "width": 2},
            fill="tozeroy", fillcolor="rgba(16,185,129,.06)",
        ))
        fig_hist.update_layout(
            plot_bgcolor="#0a0f1a", paper_bgcolor="#0a0f1a", font={"color": "#cbd5e1"},
            height=240, margin={"l": 50, "r": 30, "t": 20, "b": 40},
            xaxis={"gridcolor": "#1e293b"},
            yaxis={"gridcolor": "#1e293b", "title": "kg CO₂ (cumulative)"},
        )
        st.plotly_chart(fig_hist, use_container_width=True)
        st.dataframe(
            _log_df.sort_values("timestamp", ascending=False),
            use_container_width=True, hide_index=True,
        )


# ══════════════════════════════════════════════
# TAB 5 — Achievements
# ══════════════════════════════════════════════
with T5:
    st.markdown("<div class='section-heading'>🏅 Achievements & Eco-Milestones</div>", unsafe_allow_html=True)

    _s1, _s2, _s3, _s4 = st.columns(4)
    _kpi(_s1, "Total Runs",    str(_stats["runs"]),  f'{_stats["rec"]} recommended')
    _kpi(_s2, "CO₂ Tracked",   f'{_stats["co2"]:.3f}', "kg CO₂ total",     border="#ef4444", val_color="#ef4444")
    _kpi(_s3, "🔥 Streak",     str(_stats["streak"]), "consecutive days",  border="#f59e0b", val_color="#f59e0b")
    _kpi(_s4, "⚡ Energy Used", f'{_stats["kwh"]:.2f}', "kWh total",        border="#8b5cf6", val_color="#8b5cf6")

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<div class='sub-heading'>🏆 Badges</div>", unsafe_allow_html=True)
    st.markdown("<div class='badge-grid'>", unsafe_allow_html=True)
    _bc_cols = st.columns(4)
    for _bi, _bdg in enumerate(BADGES):
        with _bc_cols[_bi % 4]:
            _earned = _bdg["id"] in _stats["badges"]
            _name_clr = "#10b981" if _earned else "#94a3b8"
            _status_cls = "earned" if _earned else "locked"
            _status_txt = "✓ EARNED" if _earned else "Locked"
            st.markdown(f"""
<div class="badge-card {'earned' if _earned else ''}" style="opacity:{'1' if _earned else '.4'};">
  <span class="badge-icon">{_bdg['icon']}</span>
  <div class="badge-name" style="color:{_name_clr};">
    {_bdg['name']}
  </div>
  <div class="badge-desc">{_bdg['desc']}</div>
  <span class="badge-status {_status_cls}">{_status_txt}</span>
</div>""", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<div class='sub-heading'>📈 Your Impact Summary</div>", unsafe_allow_html=True)
    _imp1, _imp2, _imp3 = st.columns(3)
    def _impact_card(col, icon, val, label, clr):
        col.markdown(f"""
<div class="impact-card">
  <span class="impact-icon">{icon}</span>
  <div class="impact-value" style="color:{clr};">{val}</div>
  <div class="impact-label">{label}</div>
</div>""", unsafe_allow_html=True)

    _impact_card(_imp1, "🌳",
                 f'{_stats["co2"] / CO2_EQUIV["🌳 Tree-days (absorption)"]:.1f}',
                 "tree-days of CO₂ absorption", "#10b981")
    _impact_card(_imp2, "🚗",
                 f'{_stats["co2"] / CO2_EQUIV["🚗 km not driven"]:.1f}',
                 "km of driving avoided", "#3b82f6")
    _impact_card(_imp3, "📱",
                 f'{_stats["co2"] / CO2_EQUIV["📱 phone charges"]:.0f}',
                 "phone charges equivalent", "#8b5cf6")

    # Tips
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<div class='sub-heading'>💡 Smart Tips</div>", unsafe_allow_html=True)
    _min_t = _full.loc[_full["ci"].idxmin(), "time"]
    _max_t = _full.loc[_full["ci"].idxmax(), "time"]
    _avg_c = float(_full["ci"].mean())
    for _tip, _clr in [
        (f"🌙 Best time today: <strong>{to_12h(_min_t)}</strong> — grid is cleanest then.", "#10b981"),
        (f"🔥 Avoid <strong>{to_12h(_max_t)}</strong> — dirtiest grid of the day.", "#ef4444"),
        (f"📊 Today's avg CI: <strong>{_avg_c:.0f} gCO₂/kWh</strong> — "
         f"{'below' if _avg_c < 300 else 'above'} the 300 g benchmark.", "#f59e0b"),
        ("🔄 Enable Live Mode in the sidebar to keep data fresh automatically.", "#3b82f6"),
        ("🏅 Log your runs to build your streak and unlock badges!", "#8b5cf6"),
    ]:
        st.markdown(
            f'<div class="tip-card" style="border-left-color:{_clr} !important;">{_tip}</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════
# TAB 6 — Logger
# ══════════════════════════════════════════════
with T6:
    st.markdown("<div class='section-heading'>📝 Log an Appliance Run</div>", unsafe_allow_html=True)
    st.caption(f"System time: **{exact_time_str(st.session_state.tz)}** ({st.session_state.tz})")

    with st.form("run_form", clear_on_submit=False):
        _fa, _fb, _fc = st.columns(3)
        with _fa:
            _f_date = st.date_input("Date", datetime.now())
            _f_type = st.selectbox("Run Type", ["recommended", "baseline", "test"])
        with _fb:
            _def_start = (
                _windows[st.session_state.sel_window]["start"]
                if _windows and st.session_state.sel_window < len(_windows)
                else _now_slot
            )
            _f_start = st.text_input("Start (HH:MM)", value=_def_start)
            _f_dur   = st.number_input("Duration (min)", 15,
                                       value=ceil15(int(st.session_state.duration)), step=15)
        with _fc:
            _f_app   = st.selectbox("Appliance", list(APPLIANCES.keys()))
            _f_notes = st.text_input("Notes", value=f"Zone: {_loc_lbl}")

        st.markdown("<div class='sub-heading'>Meter Readings</div>", unsafe_allow_html=True)
        _m_a, _m_b = st.columns(2)
        with _m_a: _mb = st.number_input("Before (kWh)", min_value=0.0, format="%.3f")
        with _m_b: _ma = st.number_input("After (kWh)",  min_value=0.0, format="%.3f")
        _submit = st.form_submit_button("💾 Save Run", use_container_width=True)

    if _submit:
        _kwh_used = _ma - _mb
        if _kwh_used <= 0:
            st.error("❌ 'After' reading must exceed 'Before'.")
        else:
            _ci_run = DataEngine.ci_at(_full, _f_start) or float(_full["ci"].mean())
            _et     = mins_to_hm(hm_to_mins(_f_start) + ceil15(int(_f_dur)))
            append_log({
                "timestamp":        datetime.now().isoformat(),
                "date":             str(_f_date),
                "run_type":         _f_type,
                "appliance":        _f_app,
                "start_time":       _f_start,
                "end_time":         _et,
                "kwh_used":         round(float(_kwh_used), 4),
                "avg_ci_g_per_kwh": round(float(_ci_run), 2),
                "co2_kg":           round(co2_kg(_kwh_used, _ci_run), 4),
                "location":         _loc_lbl,
                "timezone":         st.session_state.tz,
                "notes":            _f_notes,
            })
            read_log.clear()
            st.success(
                f"✅ Logged — {_kwh_used:.3f} kWh · "
                f"{co2_kg(_kwh_used, _ci_run):.4f} kg CO₂"
            )
            if _f_type == "recommended":
                st.balloons()


# ══════════════════════════════════════════════
# TAB 7 — Data
# ══════════════════════════════════════════════
with T7:
    st.markdown("<div class='section-heading'>🔍 Raw Grid Data & Config</div>", unsafe_allow_html=True)

    with st.expander("📍 Current Configuration", expanded=False):
        st.json({
            "Mode":             st.session_state.loc_mode,
            "Latitude":         st.session_state.lat,
            "Longitude":        st.session_state.lon,
            "Timezone":         st.session_state.tz,
            "Data Source":      st.session_state.data_source,
            "Live Mode":        st.session_state.live_mode,
            "Refresh (s)":      st.session_state.refresh_s,
            "Alert Enabled":    st.session_state.alert_enabled,
            "Alert Threshold":  st.session_state.alert_thresh,
            "Daily Budget (kg)":st.session_state.daily_budget_kg,
        })
        if st.button("💾 Save Config"):
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps({"lat": st.session_state.lat,
                             "lon": st.session_state.lon,
                             "tz":  st.session_state.tz}, indent=2),
                encoding="utf-8",
            )
            st.success("✅ Saved to config/location.json")

    st.markdown("<hr>", unsafe_allow_html=True)
    _cols_show = [c for c in ["time","thermal_mw","hydro_mw","nuclear_mw","res_mw","ci"] if c in _raw.columns]
    st.dataframe(
        _raw[_cols_show].sort_values("time").reset_index(drop=True),
        use_container_width=True, hide_index=True, height=360,
    )

    st.markdown("<hr>", unsafe_allow_html=True)
    _csv_raw = _raw[_cols_show].to_csv(index=False).encode()
    _c7a, _c7b = st.columns(2)
    with _c7a:
        st.download_button(
            "📥 Download Grid CSV", _csv_raw,
            file_name=f"carbonwise_{st.session_state.lat:.2f}_{st.session_state.lon:.2f}.csv",
            mime="text/csv", use_container_width=True,
        )
    with _c7b:
        if not _log_df.empty:
            st.download_button(
                "📥 Download Run History", _log_df.to_csv(index=False).encode(),
                file_name="carbonwise_runs.csv",
                mime="text/csv", use_container_width=True,
            )

    if st.button("🔄 Clear Cache & Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<div class='sub-heading'>ℹ️ Simulation Architecture</div>", unsafe_allow_html=True)
    st.markdown(f"""
<div class="info-card" style="color:var(--text-secondary);font-size:.85rem;line-height:1.8;">
  <strong style="color:var(--accent-green);">DataEngine.fetch_live()</strong> first attempts the Electricity Maps free-tier API.
  On failure it falls back to <code>_simulate_profile()</code>, a time-seeded NumPy simulation that
  reproduces real demand curves (night troughs, morning/evening peaks, midday solar suppression).<br><br>
  The <strong style="color:var(--accent-green);">refresh seed</strong> rotates every <strong>{st.session_state.refresh_s} s</strong>,
  so data evolves naturally between pages without identical repeats.
  All 96 fifteen-minute slots are computed in one vectorised pass — no gaps, no duplicate slots.
</div>""", unsafe_allow_html=True)
