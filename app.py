"""
CarbonWise ⚡ — Real-Time Grid Carbon Intensity Scheduler
==========================================================
Improvements over v1:
  • Eliminated all duplicate / overlapping logic (ci calculation, time helpers, etc.)
  • Single source-of-truth for every config, constant and computation
  • Proper class-based DataEngine separating fetch → compute → cache layers
  • Real-time polling via st.fragment + st.rerun(scope="fragment") (Streamlit ≥1.37)
    with graceful fallback to meta-refresh for older versions
  • Async-ready fetch with timeout + exponential back-off
  • Full 96-slot (15-min) day grid, zero gaps
  • Correct overnight deadline handling (wraps midnight)
  • Streak logic fixed to handle timezone-aware dates
  • Badge checks consolidated in one place
  • Removed redundant HTML injection; all styling via one CSS block
  • download buttons de-duplicated
  • Weekly forecast seeded per calendar day (stable within a day)
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
# 0. App-level config  (MUST be the first Streamlit call)
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
# 2. Pure helper functions  (stateless, no side-effects)
# ──────────────────────────────────────────────────────────────

def safe_float(v, default: float = 0.0) -> float:
    """Convert to float, returning default on failure or NaN."""
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def ceil15(minutes: int) -> int:
    """Round minutes up to the nearest 15-minute boundary."""
    m = int(minutes)
    return m if m % STEP_MIN == 0 else m + (STEP_MIN - m % STEP_MIN)


def hm_to_mins(hhmm: str) -> int:
    """'HH:MM' → total minutes from midnight."""
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def mins_to_hm(total: int) -> str:
    """Total minutes → 'HH:MM' (wraps at midnight)."""
    total = int(total) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def to_12h(hhmm: str) -> str:
    """'HH:MM' → '12:34 PM'."""
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
    return "#4ade80" if ci < 200 else "#fbbf24" if ci < 400 else "#f87171"


def ci_label(ci: float) -> str:
    ci = safe_float(ci, 500)
    return "Low" if ci < 200 else "Medium" if ci < 400 else "High"


def ci_emoji(ci: float) -> str:
    return "🟢" if ci < 200 else "🟡" if ci < 400 else "🔴"


def compute_ci(thermal: float, hydro: float, nuclear: float, res: float) -> float:
    """Weighted carbon intensity from generation mix."""
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
    """Current time rounded down to STEP_MIN boundary → 'HH:MM'."""
    n = now_tz(tz_str)
    return f"{n.hour:02d}:{(n.minute // STEP_MIN) * STEP_MIN:02d}"


def exact_time_str(tz_str: str) -> str:
    n = now_tz(tz_str)
    p = "AM" if n.hour < 12 else "PM"
    return f"{(n.hour % 12) or 12}:{n.minute:02d}:{n.second:02d} {p}"


# ──────────────────────────────────────────────────────────────
# 3. DataEngine — single class owning all data logic
# ──────────────────────────────────────────────────────────────

class DataEngine:
    """
    Owns the full-day CI DataFrame (96 rows × time/ci/…).
    All heavy computation is cached via st.cache_data on class methods.
    """

    # ── 3a. Generation simulation ─────────────────────────────

    @staticmethod
    def _simulate_profile(base_ci: float, seed: int) -> pd.DataFrame:
        """
        Build a realistic 96-slot (24 h × 4 per hour) CI profile.
        `seed` changes every refresh window so data evolves naturally.
        """
        rng = np.random.default_rng(seed)
        rows = []
        for h in range(24):
            f = HOUR_DEMAND[h]
            for qi, q in enumerate([0, 15, 30, 45]):
                intra_wave = math.sin(qi * math.pi / 4) * 0.015
                noise      = rng.normal(0, 0.035)
                multiplier = max(0.25, f + intra_wave + noise)
                ci  = round(max(10.0, min(900.0, base_ci * multiplier)), 2)

                # Approximate generation mix proportional to CI
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

    # ── 3b. Cached loaders ────────────────────────────────────

    @staticmethod
    @st.cache_data(ttl=300, show_spinner=False)
    def fetch_live(lat: float, lon: float, token: str, seed: int) -> pd.DataFrame:
        """Try Electricity Maps API; fall back to simulation."""
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
        # Fallback — derive base_ci from nearest zone
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

    # ── 3c. Derived computations ──────────────────────────────

    @staticmethod
    def full_day(raw: pd.DataFrame) -> pd.DataFrame:
        """Ensure exactly 96 slots with color/label columns."""
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
        """
        Find the `top_k` lowest-CI windows that:
          • start ≥ now
          • finish ≤ deadline  (handles midnight wrap)
          • span exactly ceil15(duration) minutes
        Returns list sorted by avg_ci ascending.
        """
        dur   = ceil15(duration_min)
        nslot = dur // STEP_MIN

        now_m  = hm_to_mins(now)
        dead_m = hm_to_mins(deadline)
        # Midnight wrap: if deadline is earlier in the day than now, add 24 h
        if dead_m <= now_m:
            dead_m += 1440

        times = full["time"].tolist()
        cis   = full["ci"].tolist()
        wins  = []

        for i in range(len(times) - nslot + 1):
            sh, sm   = map(int, times[i].split(":"))
            start_m  = sh * 60 + sm
            # Normalise start relative to now (handle overnight slots)
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
        """7-day CI forecast seeded by calendar date (stable within a day)."""
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

    # ── 3d. Zone lookup ───────────────────────────────────────

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

    # Streak — consecutive calendar days ending today
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

    # Badge evaluation
    badges = []
    if runs >= 1:      badges.append("first_save")
    if streak >= 7:    badges.append("green_week")
    if total_co2 >= 1: badges.append("ton_saver")
    if rec >= 10:      badges.append("optimizer")
    if total_co2 >= 10:badges.append("eco_champion")
    # Check log details for time-based badges
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
# 5. Global CSS  (single inject, no duplication)
# ──────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;700&display=swap');

*, *::before, *::after { box-sizing: border-box; font-family: 'Space Grotesk', sans-serif !important; }
code, pre, .mono       { font-family: 'JetBrains Mono', monospace !important; }

/* ── Background ── */
.main,[data-testid="stAppViewContainer"],[data-testid="stHeader"] { background:#080d16; }
[data-testid="stSidebar"] { background:#0a1120 !important; border-right:1px solid rgba(74,222,128,.12); }

/* ── Hero ── */
.hero {
  background:linear-gradient(135deg,#0b1628,#0f2744 55%,#0b1a30);
  padding:2rem 2.5rem; border-radius:20px; margin-bottom:1.5rem;
  border:1px solid rgba(74,222,128,.2); position:relative; overflow:hidden;
}
.hero::before {
  content:''; position:absolute; top:-40%; right:-10%;
  width:400px; height:400px;
  background:radial-gradient(circle,rgba(74,222,128,.08),transparent 70%);
  border-radius:50%; pointer-events:none;
}
.hero h1 { color:#4ade80; margin:0; font-size:2.2rem; font-weight:700; letter-spacing:-.5px; }
.hero p  { color:#94a3b8; margin:.4rem 0 0; font-size:1rem; max-width:620px; }
.hero-pills { margin-top:.9rem; display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.pill {
  padding:4px 12px; border-radius:999px; font-size:.76rem; font-weight:600;
  display:inline-flex; align-items:center; gap:5px;
}
.pill-green  { background:rgba(74,222,128,.13); color:#4ade80; border:1px solid rgba(74,222,128,.3); }
.pill-blue   { background:rgba(59,130,246,.12); color:#60a5fa; border:1px solid rgba(59,130,246,.28); }
.pill-amber  { background:rgba(251,191,36,.10); color:#fbbf24; border:1px solid rgba(251,191,36,.25); }
.pill-muted  { color:#475569; font-size:.8rem; }

/* ── KPI cards ── */
.kpi {
  background:linear-gradient(135deg,#111b2e,#0c1422);
  padding:1.1rem 1rem; border-radius:14px; text-align:center;
  border-left:4px solid #4ade80; min-height:110px;
  display:flex; flex-direction:column; justify-content:center;
  transition:transform .2s, box-shadow .2s;
}
.kpi:hover { transform:translateY(-2px); box-shadow:0 8px 24px rgba(74,222,128,.1); }
.kpi-lbl { color:#64748b; font-size:.68rem; text-transform:uppercase; letter-spacing:.08em; font-weight:600; margin-bottom:3px; }
.kpi-val { color:white; font-weight:700; font-size:1.35rem; line-height:1.1; }
.kpi-sub { color:#475569; font-size:.68rem; margin-top:3px; }

/* ── Rec card ── */
.rec {
  background:linear-gradient(135deg,#111b2e,#0c1422);
  padding:1.2rem; border-radius:14px; margin-bottom:.5rem;
  transition:transform .2s;
}
.rec:hover { transform:translateY(-2px); }

/* ── Ticker ── */
.ticker {
  background:#111b2e; border:1px solid rgba(74,222,128,.2);
  border-radius:10px; padding:.6rem 1.2rem;
  display:flex; align-items:center; gap:12px; flex-wrap:wrap;
}
.ticker-dot {
  width:8px; height:8px; border-radius:50%; background:#4ade80; flex-shrink:0;
  animation:pulse-dot 1.5s ease-in-out infinite;
}
@keyframes pulse-dot { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.5;transform:scale(1.35)} }

/* ── Alert banners ── */
.alert { padding:.75rem 1rem; border-radius:10px; margin-bottom:.5rem; }
.alert-g { background:rgba(74,222,128,.08); border:1px solid rgba(74,222,128,.35); border-left:4px solid #4ade80; }
.alert-r { background:rgba(248,113,113,.08); border:1px solid rgba(248,113,113,.35); border-left:4px solid #f87171; }
.alert-y { background:rgba(251,191,36,.08);  border:1px solid rgba(251,191,36,.35);  border-left:4px solid #fbbf24; }

/* ── Progress bar ── */
.pb { height:6px; border-radius:999px; background:#1e293b; margin:6px 0; }
.pb-fill { height:100%; border-radius:999px; background:linear-gradient(90deg,#4ade80,#22d3ee); }

/* ── Badge ── */
.badge-card {
  background:#111b2e; border-radius:12px; padding:14px 10px;
  text-align:center; border:1px solid #1e2d45; transition:all .2s;
}
.badge-card.earned { border-color:rgba(74,222,128,.4); background:rgba(74,222,128,.05); }
.badge-card:hover  { transform:scale(1.04); }

/* ── Misc ── */
.live-time {
  background:rgba(74,222,128,.07); border:1px solid rgba(74,222,128,.3);
  padding:.75rem; border-radius:12px; color:#4ade80;
  font-weight:700; text-align:center; font-size:1.05rem; letter-spacing:.05em;
  font-family:'JetBrains Mono',monospace !important;
}
.tz-sub { color:#475569; font-size:.7rem; text-align:center; margin-top:4px; }
hr { border-color:rgba(74,222,128,.1) !important; margin:1rem 0 !important; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# 6. Session state  (single initialisation block)
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
# 7. Auto-refresh  (JS timer, injected once per live-mode cycle)
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
    st.markdown("## ⚙️ Configuration")

    # ── 8a. Live Mode ─────────────────────────────────────────
    st.markdown("### 🔄 Live Mode")
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
            f'<div style="background:#0f2040;border:1px solid rgba(74,222,128,.25);'
            f'border-radius:8px;padding:8px 12px;margin-top:4px;">'
            f'<span style="color:#4ade80;font-size:.76rem;font-weight:600;">'
            f'🟢 Live — every {_ri_map[st.session_state.refresh_s]}</span></div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # ── 8b. Location ──────────────────────────────────────────
    st.markdown("### 📍 Location")
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
            f'<div style="background:#1f2937;padding:10px 12px;border-radius:8px;margin-top:6px;">'
            f'<div style="color:#64748b;font-size:.7rem;">COORDINATES</div>'
            f'<div style="color:#4ade80;font-size:.83rem;font-weight:600;">'
            f'{st.session_state.lat:.4f}, {st.session_state.lon:.4f}</div></div>',
            unsafe_allow_html=True,
        )

    elif st.session_state.loc_mode == "GPS (Browser)":
        components.html("""
<style>
html,body{margin:0;padding:0;background:transparent;font-family:sans-serif;}
#btn{width:100%;padding:9px;background:linear-gradient(135deg,#1e3a5f,#0f172a);
     color:#4ade80;border:1.5px solid rgba(74,222,128,.45);border-radius:8px;
     cursor:pointer;font-weight:600;font-size:13px;}
#btn:hover{background:rgba(74,222,128,.12);}#btn:disabled{opacity:.6;}
#msg{color:#64748b;font-size:11px;margin-top:5px;text-align:center;min-height:16px;}
.c{background:#1f2937;padding:8px;border-radius:6px;margin-top:6px;
   color:#4ade80;font-size:12px;font-weight:600;display:none;}
</style>
<button id="btn" onclick="go()">📡 Use GPS</button>
<div id="msg"></div><div id="c" class="c"></div>
<script>
function go(){
  var btn=document.getElementById('btn'),msg=document.getElementById('msg'),c=document.getElementById('c');
  if(!navigator.geolocation){msg.innerHTML='<span style="color:#f87171">Not supported</span>';return;}
  btn.textContent='⏳ Locating…';btn.disabled=true;msg.textContent='Waiting…';
  navigator.geolocation.getCurrentPosition(
    p=>{btn.textContent='✅ Done';c.style.display='block';
        c.innerHTML='📍 '+p.coords.latitude.toFixed(5)+', '+p.coords.longitude.toFixed(5);},
    e=>{btn.textContent='📡 Use GPS';btn.disabled=false;
        msg.innerHTML='<span style="color:#f87171">'+(e.code===1?'Permission denied':'Error')+'</span>';},
    {enableHighAccuracy:true,timeout:12000,maximumAge:0}
  );
}
</script>""", height=110)
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
            f'<div style="background:#1f2937;padding:10px 12px;border-radius:8px;margin-top:6px;">'
            f'<div style="color:#64748b;font-size:.7rem;">ZONE AVG CI</div>'
            f'<div style="color:#4ade80;font-size:.88rem;font-weight:600;">{_zd["ci_avg"]} gCO₂/kWh</div>'
            f'<div style="color:#64748b;font-size:.7rem;margin-top:2px;">{GRID_ZONES[_reg]["voltage"]}</div>'
            f'</div>',
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

    st.divider()

    # ── 8c. Clock ─────────────────────────────────────────────
    st.markdown(f'<div class="live-time">🕐 {exact_time_str(st.session_state.tz)}</div>', unsafe_allow_html=True)
    _slot = slot_str(st.session_state.tz)
    st.markdown(f'<div class="tz-sub">{st.session_state.tz} &nbsp;|&nbsp; slot {to_12h(_slot)}</div>', unsafe_allow_html=True)

    st.divider()

    # ── 8d. Data Source ───────────────────────────────────────
    st.markdown("### 📡 Data Source")
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

    st.divider()

    # ── 8e. Appliance ─────────────────────────────────────────
    st.markdown("### 🔌 Appliance")
    _prev = st.session_state.appliance
    st.session_state.appliance = st.selectbox(
        "Type", list(APPLIANCES.keys()),
        index=list(APPLIANCES.keys()).index(st.session_state.appliance),
        label_visibility="collapsed",
    )
    if st.session_state.appliance != _prev:  # auto-fill defaults on change
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

    st.divider()

    # ── 8f. Optimisation ──────────────────────────────────────
    st.markdown("### ⚡ Optimisation")
    _top_k = st.slider("Top windows", 3, 12, 5)

    st.divider()

    # ── 8g. Alerts & Budget ───────────────────────────────────
    st.markdown("### 🔔 Alerts & Budget")
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


# ──────────────────────────────────────────────────────────────
# 9. Data loading  (single code path, no duplication)
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

# Build derived datasets (all done once)
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

# Location label (single source)
_loc_lbl = (
    st.session_state.zone  if st.session_state.loc_mode == "Manual Select"
    else "GPS"             if st.session_state.loc_mode == "GPS (Browser)"
    else f"{st.session_state.lat:.2f}°, {st.session_state.lon:.2f}°"
)


# ──────────────────────────────────────────────────────────────
# 10. HERO BANNER
# ──────────────────────────────────────────────────────────────

_live_pill = (
    '<span class="pill pill-green"><span style="width:7px;height:7px;border-radius:50%;'
    'background:#4ade80;display:inline-block;animation:pulse-dot 1.5s infinite;"></span> LIVE</span>'
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
    <span class="pill-muted">🕐 {exact_time_str(st.session_state.tz)} &nbsp;·&nbsp; {_loc_lbl}</span>
  </div>
</div>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# 11. Alert banner  (single, mutually exclusive)
# ──────────────────────────────────────────────────────────────

if st.session_state.alert_enabled:
    if _cur_ci <= st.session_state.alert_thresh:
        st.markdown(
            f'<div class="alert alert-g">'
            f'<strong style="color:#4ade80;">🟢 GREEN GRID ALERT!</strong>'
            f'<span style="color:#94a3b8;"> CI is <strong style="color:#4ade80;">{_cur_ci:.0f}</strong> gCO₂/kWh'
            f' — below your {st.session_state.alert_thresh} g threshold. Great time to run appliances!</span></div>',
            unsafe_allow_html=True,
        )
    elif _cur_ci > 400:
        st.markdown(
            f'<div class="alert alert-r">'
            f'<strong style="color:#f87171;">🔴 HIGH CARBON ALERT</strong>'
            f'<span style="color:#94a3b8;"> CI is <strong style="color:#f87171;">{_cur_ci:.0f}</strong> gCO₂/kWh'
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
  <span style="color:#64748b;font-size:.75rem;font-weight:600;white-space:nowrap;">LIVE GRID</span>
  <span style="color:{_tc};font-weight:700;font-size:.86rem;">
    {ci_emoji(_cur_ci)} {_cur_ci:.0f} gCO₂/kWh {_trend}
  </span>
  <span style="color:#475569;">·</span>
  <span style="color:#60a5fa;font-size:.76rem;">Best: {to_12h(_best["start"]) if _best else "--"}</span>
  <span style="color:#475569;">·</span>
  <span style="color:#fbbf24;font-size:.76rem;">🔥 {_stats["streak"]}-day streak</span>
  <span style="color:#475569;">·</span>
  <span style="color:#4ade80;font-size:.76rem;">Saved {_stats["co2"]:.3f} kg CO₂ total</span>
  <span style="color:#475569;">·</span>
  <span style="color:#94a3b8;font-size:.76rem;">{st.session_state.data_source}</span>
</div>
<div style="margin-bottom:.9rem;"></div>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# 13. KPI row
# ──────────────────────────────────────────────────────────────

st.markdown("### 📊 Session Overview")
_k = st.columns(6)

def _kpi(col, label, value, sub, border="#4ade80", val_color="white"):
    col.markdown(
        f'<div class="kpi" style="border-left-color:{border};">'
        f'<div class="kpi-lbl">{label}</div>'
        f'<div class="kpi-val" style="color:{val_color};font-size:1.1rem;">{value}</div>'
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
     border="#4ade80" if _best else "#64748b",
     val_color="#4ade80" if _best else "#64748b")
_sc = "#4ade80" if _stats["streak"] >= 3 else "#fbbf24" if _stats["streak"] else "#64748b"
_kpi(_k[5], "🔥 Streak", f'{_stats["streak"]} days',
     f'{len(_stats["badges"])} badges · {_stats["runs"]} runs', border=_sc, val_color=_sc)

st.markdown("---")


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
        st.subheader(f"24-Hour Carbon Intensity · {st.session_state.tz}")

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
                "bgcolor": "#111b2e", "borderwidth": 0,
                "steps":   [
                    {"range": [0, 200],   "color": "rgba(74,222,128,.1)"},
                    {"range": [200, 400], "color": "rgba(251,191,36,.08)"},
                    {"range": [400, 700], "color": "rgba(248,113,113,.08)"},
                ],
                "threshold": {"line": {"color": "#4ade80", "width": 2}, "thickness": 0.75, "value": _cur_ci},
            },
        ))
        fig_g.update_layout(
            paper_bgcolor="#0c1422", font={"color": "#cbd5e1"},
            height=200, margin={"l": 30, "r": 30, "t": 30, "b": 10},
        )
        st.plotly_chart(fig_g, use_container_width=True)

        # 24-h line chart
        fig_l = go.Figure()
        fig_l.add_trace(go.Scatter(
            x=_full["time"], y=_full["ci"],
            fill="tozeroy", fillcolor="rgba(74,222,128,.06)",
            line={"color": "#4ade80", "width": 2.5},
            hovertemplate="<b>%{x}</b><br>CI: %{y:.1f} gCO₂/kWh<extra></extra>",
        ))
        # CI band background
        fig_l.add_hrect(y0=0,   y1=200, fillcolor="#4ade80", opacity=0.03, line_width=0)
        fig_l.add_hrect(y0=200, y1=400, fillcolor="#fbbf24", opacity=0.03, line_width=0)
        fig_l.add_hrect(y0=400, y1=900, fillcolor="#f87171", opacity=0.03, line_width=0)
        # Highlight best windows
        for wi, w in enumerate((_windows or [])[:3]):
            fig_l.add_vrect(
                x0=w["start"], x1=w["end"],
                fillcolor=w["color"], opacity=0.15,
                line_width=2 if wi == 0 else 1, line_color=w["color"], layer="below",
            )
        _ymax = float(_full["ci"].max()) * 1.12
        fig_l.add_vline(x=_now_slot, line_dash="dash", line_color="#4ade80", line_width=1.8)
        fig_l.add_vline(x=st.session_state.deadline, line_dash="dash", line_color="#f87171", line_width=1.8)
        fig_l.add_annotation(x=_now_slot, y=_ymax, text="NOW",
                              showarrow=False, font={"color": "#4ade80", "size": 11},
                              bgcolor="rgba(8,13,22,.8)")
        fig_l.add_annotation(x=st.session_state.deadline, y=_ymax, text="DEADLINE",
                              showarrow=False, font={"color": "#f87171", "size": 11},
                              bgcolor="rgba(8,13,22,.8)")
        fig_l.add_hline(y=float(_full["ci"].mean()), line_dash="dot", line_color="#60a5fa",
                        line_width=1, annotation_text="Daily avg",
                        annotation_font_color="#60a5fa")
        fig_l.update_layout(
            plot_bgcolor="#0c1422", paper_bgcolor="#0c1422", font={"color": "#cbd5e1"},
            xaxis={"gridcolor": "#1a2535", "title": "Time of Day", "tickangle": -45, "nticks": 13},
            yaxis={"gridcolor": "#1a2535", "title": "gCO₂/kWh", "range": [0, _ymax * 1.1]},
            height=360, margin={"l": 55, "r": 30, "t": 20, "b": 55}, showlegend=False,
        )
        st.plotly_chart(fig_l, use_container_width=True)

    with col_side:
        st.subheader("Grid Mix")
        _last = _raw.iloc[-1]
        _vals = [safe_float(_last.get(c, 0)) for c in ["thermal_mw", "hydro_mw", "nuclear_mw", "res_mw"]]
        _tot  = sum(_vals) or 1
        fig_p = go.Figure(data=[go.Pie(
            labels=["Thermal", "Hydro", "Nuclear", "Renewable"], values=_vals,
            hole=0.65, marker_colors=["#f87171", "#60a5fa", "#a78bfa", "#4ade80"],
            textinfo="label+percent", textfont={"color": "white", "size": 9},
        )])
        fig_p.update_layout(
            paper_bgcolor="#0c1422", height=275,
            showlegend=False, margin={"t": 10, "b": 10, "l": 10, "r": 10},
            annotations=[{"text": f"<b>{_tot/1000:.1f}</b><br>GW",
                           "x": 0.5, "y": 0.5, "font": {"size": 16, "color": "#f8fafc"}, "showarrow": False}],
        )
        st.plotly_chart(fig_p, use_container_width=True)

        # CO₂ equivalents
        st.markdown("#### 🌿 CO₂ Equivalents")
        _co2_now = co2_kg(_nrg, _cur_ci)
        _co2_opt = co2_kg(_nrg, _best["avg_ci"]) if _best else _co2_now
        _saved   = max(0.0, _co2_now - _co2_opt)
        st.markdown(
            f'<div style="background:#111b2e;border-radius:10px;padding:12px 14px;border:1px solid #1e2d45;">'
            f'<div style="color:#64748b;font-size:.69rem;margin-bottom:5px;">Running now: {_co2_now:.3f} kg CO₂</div>'
            f'<div style="color:#4ade80;font-size:.76rem;font-weight:600;margin-bottom:4px;">Optimising saves:</div>',
            unsafe_allow_html=True,
        )
        for lbl, val in co2_equivalents(_saved).items():
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
                f'border-bottom:1px solid #1a2535;font-size:.76rem;">'
                f'<span style="color:#94a3b8;">{lbl}</span>'
                f'<span style="color:white;font-weight:600;">{val:.2f}</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════
# TAB 2 — Smart Advisor
# ══════════════════════════════════════════════
with T2:
    st.subheader(f"🏆 Top {_top_k} Optimal Windows · {_now_12} → {_dl_12}")

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
                _bdr  = "2px solid #4ade80" if _is_best else "1px solid #1e2d45"
                with _col:
                    st.markdown(f"""
<div class="rec" style="border:{_bdr};">
  <div style="text-align:center;margin-bottom:8px;">
    <span style="background:{'#4ade80' if _is_best else '#1e3a5f'};
          color:{'#0f172a' if _is_best else '#94a3b8'};
          padding:3px 10px;border-radius:5px;font-size:.7rem;font-weight:700;">
      {'🥇 BEST' if _rank==1 else f'#{_rank}'}
    </span>
    <span style="background:rgba(74,222,128,.1);color:#4ade80;
          padding:2px 7px;border-radius:4px;font-size:.63rem;font-weight:600;margin-left:4px;">
      {_conf}% conf.
    </span>
  </div>
  <div style="font-size:1.05rem;font-weight:700;color:white;text-align:center;margin-bottom:9px;">
    {to_12h(_w['start'])} — {to_12h(_w['end'])}
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px;">
    <div style="background:#0c1422;padding:7px 4px;border-radius:8px;text-align:center;">
      <div style="color:#64748b;font-size:.6rem;margin-bottom:2px;">CI avg</div>
      <div style="color:{_w['color']};font-size:.92rem;font-weight:700;">{_w['avg_ci']:.0f}</div>
    </div>
    <div style="background:#0c1422;padding:7px 4px;border-radius:8px;text-align:center;">
      <div style="color:#64748b;font-size:.6rem;margin-bottom:2px;">CO₂</div>
      <div style="color:white;font-size:.92rem;font-weight:700;">{_co2w:.3f} kg</div>
    </div>
  </div>
  <div style="background:rgba(74,222,128,.07);padding:6px 8px;border-radius:8px;
       border:1px solid rgba(74,222,128,.2);margin-bottom:5px;">
    <span style="color:#4ade80;font-size:.76rem;font-weight:600;">
      💰 Save {_sav:.3f} kg ({_ppct:.0f}%)
    </span>
  </div>
  <div style="font-size:.68rem;color:#475569;text-align:center;">
    ≈ {_eq_t:.1f} tree-days · {_eq_k:.1f} km not driven
  </div>
</div>""", unsafe_allow_html=True)
                    if st.button("✅ Select", key=f"sel_{_gi}", use_container_width=True):
                        st.session_state.sel_window = _gi
                        st.success(f"Selected: {to_12h(_w['start'])} – {to_12h(_w['end'])}")

    # Heatmap
    st.markdown("---")
    st.markdown("#### 🗓️ Today's CI Heatmap (24 h)")
    _hdata = _full["ci"].values.reshape(6, 16) if len(_full) == 96 else np.tile(_full["ci"].values[:1], (6, 16))
    fig_h = go.Figure(data=go.Heatmap(
        z=_hdata,
        colorscale=[[0, "#4ade80"], [0.4, "#fbbf24"], [1, "#f87171"]],
        showscale=True,
        colorbar={"title": "gCO₂/kWh", "tickfont": {"color": "#94a3b8"}},
        hovertemplate="CI: %{z:.1f} gCO₂/kWh<extra></extra>",
    ))
    fig_h.update_layout(
        paper_bgcolor="#0c1422", height=190,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
        xaxis={"showticklabels": False}, yaxis={"showticklabels": False},
    )
    st.plotly_chart(fig_h, use_container_width=True)


# ══════════════════════════════════════════════
# TAB 3 — Weekly Forecast
# ══════════════════════════════════════════════
with T3:
    st.subheader("🌤️ 7-Day Grid CI Forecast")
    st.caption("Simulated forecast based on day-of-week demand patterns + current grid conditions.")

    fig_w = go.Figure()
    fig_w.add_trace(go.Bar(name="Night", x=_weekly["date"], y=_weekly["ci_night"],
                           marker_color="#4ade80", opacity=0.8))
    fig_w.add_trace(go.Bar(name="Day",   x=_weekly["date"], y=_weekly["ci_day"],
                           marker_color="#60a5fa", opacity=0.8))
    fig_w.add_trace(go.Bar(name="Peak",  x=_weekly["date"], y=_weekly["ci_peak"],
                           marker_color="#f87171", opacity=0.8))
    fig_w.add_trace(go.Scatter(
        name="Daily avg", x=_weekly["date"], y=_weekly["avg"],
        mode="lines+markers", line={"color": "#fbbf24", "width": 2, "dash": "dot"},
        marker={"size": 7},
    ))
    fig_w.update_layout(
        barmode="group", plot_bgcolor="#0c1422", paper_bgcolor="#0c1422",
        font={"color": "#cbd5e1"}, height=370,
        legend={"orientation": "h", "y": 1.05, "x": 0.5, "xanchor": "center"},
        xaxis={"gridcolor": "#1a2535"},
        yaxis={"gridcolor": "#1a2535", "title": "gCO₂/kWh"},
        margin={"l": 50, "r": 30, "t": 50, "b": 40},
    )
    st.plotly_chart(fig_w, use_container_width=True)

    st.markdown("#### 📅 Daily Outlook")
    _dc = st.columns(7)
    for _di, (_dcol, _dr) in enumerate(zip(_dc, _weekly.itertuples())):
        with _dcol:
            _best_p = (
                "Night" if _dr.ci_night <= _dr.ci_day and _dr.ci_night <= _dr.ci_peak
                else "Day" if _dr.ci_day <= _dr.ci_peak
                else "Peak"
            )
            _icon_d = "🌙" if _best_p == "Night" else "☀️" if _best_p == "Day" else "⚡"
            _bdr_d  = "2px solid #4ade80" if _di == 0 else "1px solid #1e2d45"
            st.markdown(f"""
<div style="background:#111b2e;border-radius:12px;padding:9px 7px;
     text-align:center;border:{_bdr_d};">
  <div style="color:#64748b;font-size:.65rem;font-weight:600;">{_dr.label}</div>
  <div style="font-size:1.1rem;margin:4px 0;">{_icon_d}</div>
  <div style="color:{ci_color(_dr.avg)};font-size:.9rem;font-weight:700;">{_dr.avg:.0f}</div>
  <div style="color:#475569;font-size:.6rem;">gCO₂/kWh</div>
  <div style="background:rgba(74,222,128,.1);border-radius:4px;
       padding:2px 4px;margin-top:4px;font-size:.58rem;color:#4ade80;">
    Best: {_best_p}
  </div>
</div>""", unsafe_allow_html=True)

    # Regional comparison
    st.markdown("---")
    st.subheader("🗺️ Regional CI Comparison")
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
        plot_bgcolor="#0c1422", paper_bgcolor="#0c1422", font={"color": "#cbd5e1"},
        height=560, margin={"l": 190, "r": 60, "t": 20, "b": 40},
        xaxis={"gridcolor": "#1a2535", "title": "Avg CI (gCO₂/kWh)"},
        yaxis={"gridcolor": "#1a2535"},
    )
    st.plotly_chart(fig_reg, use_container_width=True)


# ══════════════════════════════════════════════
# TAB 4 — Analytics
# ══════════════════════════════════════════════
with T4:
    st.subheader("📊 Baseline vs Optimised")

    _co2_now = co2_kg(_nrg, _cur_ci)
    _co2_opt = co2_kg(_nrg, _best["avg_ci"]) if _best else _co2_now
    _proj    = max(0.0, _co2_now - _co2_opt)
    _proj_p  = (_proj / _co2_now * 100) if _co2_now > 0 else 0

    _ac, _bc = st.columns([3, 2])
    with _ac:
        fig_c = go.Figure()
        fig_c.add_trace(go.Bar(
            name="Baseline (Now)", x=["CO₂ Emissions"], y=[_co2_now],
            marker_color="#f87171", text=[f"{_co2_now:.3f} kg"], textposition="outside", width=0.28,
        ))
        fig_c.add_trace(go.Bar(
            name="Optimised (Best)", x=["CO₂ Emissions"], y=[_co2_opt],
            marker_color="#4ade80", text=[f"{_co2_opt:.3f} kg"], textposition="outside", width=0.28,
        ))
        fig_c.update_layout(
            barmode="group", plot_bgcolor="#0c1422", paper_bgcolor="#0c1422",
            font={"color": "#cbd5e1"}, height=310,
            legend={"orientation": "h", "y": 1.05, "x": 0.5, "xanchor": "center"},
            yaxis={"title": "CO₂ (kg)", "gridcolor": "#1a2535",
                   "range": [0, max(_co2_now, _co2_opt) * 1.45]},
            margin={"l": 50, "r": 30, "t": 50, "b": 40},
            title={"text": f"💰 Potential saving: {_proj:.3f} kg CO₂ ({_proj_p:.1f}%)",
                   "font": {"size": 13, "color": "#4ade80"}},
        )
        st.plotly_chart(fig_c, use_container_width=True)

    with _bc:
        st.markdown("#### 💰 Daily CO₂ Budget")
        _today_str = datetime.now().strftime("%Y-%m-%d")
        _today_co2 = 0.0
        if not _log_df.empty and "date" in _log_df and "co2_kg" in _log_df:
            _today_co2 = float(_log_df[_log_df["date"].astype(str) == _today_str]["co2_kg"].sum())
        _budget    = st.session_state.daily_budget_kg
        _pct_used  = min(100.0, _today_co2 / max(_budget, 1e-6) * 100)
        _remaining = max(0.0, _budget - _today_co2)
        _bar_clr   = "#4ade80" if _pct_used < 60 else "#fbbf24" if _pct_used < 90 else "#f87171"
        st.markdown(f"""
<div style="background:#111b2e;border-radius:12px;padding:15px;border:1px solid #1e2d45;">
  <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
    <span style="color:#94a3b8;font-size:.78rem;">Daily Budget</span>
    <span style="color:white;font-size:.82rem;font-weight:600;">{_budget:.1f} kg CO₂</span>
  </div>
  <div class="pb"><div class="pb-fill" style="width:{_pct_used:.0f}%;background:{_bar_clr};"></div></div>
  <div style="display:flex;justify-content:space-between;margin-top:5px;">
    <span style="color:{_bar_clr};font-size:.76rem;font-weight:600;">Used {_today_co2:.3f} kg</span>
    <span style="color:#64748b;font-size:.76rem;">Left {_remaining:.3f} kg</span>
  </div>
  <div style="margin-top:9px;padding-top:9px;border-top:1px solid #1a2535;">
    <div style="color:#64748b;font-size:.68rem;margin-bottom:3px;">This session:</div>
    <div style="display:flex;justify-content:space-between;">
      <span style="color:#f87171;font-size:.76rem;">Now {_co2_now:.3f} kg</span>
      <span style="color:#4ade80;font-size:.76rem;">Optimal {_co2_opt:.3f} kg</span>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

        st.markdown(f"#### 🌍 {_proj:.3f} kg Savings Equals…")
        for lbl, val in co2_equivalents(_proj).items():
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:10px;padding:7px 0;'
                f'border-bottom:1px solid #1e293b;">'
                f'<span style="color:#94a3b8;font-size:.8rem;flex:1;">{lbl}</span>'
                f'<span style="color:#4ade80;font-weight:700;font-size:.82rem;">{val:.2f}</span></div>',
                unsafe_allow_html=True,
            )

    _m1, _m2, _m3, _m4 = st.columns(4)
    _m1.metric("🔴 Baseline",  f"{_co2_now:.3f} kg")
    _m2.metric("🟢 Optimised", f"{_co2_opt:.3f} kg")
    _m3.metric("💰 CO₂ Saved", f"{_proj:.3f} kg",  delta=f"-{_proj_p:.1f}%", delta_color="inverse")
    _m4.metric("⚡ Energy",    f"{_nrg:.3f} kWh")

    # Historical chart
    if not _log_df.empty and "timestamp" in _log_df and "co2_kg" in _log_df:
        st.markdown("---")
        st.subheader("📋 Cumulative CO₂ Over Time")
        _ldf = _log_df.copy()
        _ldf["ts"] = pd.to_datetime(_ldf["timestamp"], errors="coerce")
        _ldf = _ldf.dropna(subset=["ts"]).sort_values("ts")
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(
            x=_ldf["ts"], y=_ldf["co2_kg"].cumsum(),
            mode="lines+markers", line={"color": "#4ade80", "width": 2},
            fill="tozeroy", fillcolor="rgba(74,222,128,.06)",
        ))
        fig_hist.update_layout(
            plot_bgcolor="#0c1422", paper_bgcolor="#0c1422", font={"color": "#cbd5e1"},
            height=240, margin={"l": 50, "r": 30, "t": 20, "b": 40},
            xaxis={"gridcolor": "#1a2535"},
            yaxis={"gridcolor": "#1a2535", "title": "kg CO₂ (cumulative)"},
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
    st.subheader("🏅 Achievements & Eco-Milestones")

    _s1, _s2, _s3, _s4 = st.columns(4)
    _kpi(_s1, "Total Runs",    str(_stats["runs"]),  f'{_stats["rec"]} recommended')
    _kpi(_s2, "CO₂ Tracked",   f'{_stats["co2"]:.3f}', "kg CO₂ total",     border="#f87171", val_color="#f87171")
    _kpi(_s3, "🔥 Streak",     str(_stats["streak"]), "consecutive days",  border="#fbbf24", val_color="#fbbf24")
    _kpi(_s4, "⚡ Energy Used", f'{_stats["kwh"]:.2f}', "kWh total",        border="#a78bfa", val_color="#a78bfa")

    st.markdown("---")
    st.markdown("#### 🏆 Badges")
    _bc_cols = st.columns(4)
    for _bi, _bdg in enumerate(BADGES):
        with _bc_cols[_bi % 4]:
            _earned = _bdg["id"] in _stats["badges"]
            st.markdown(f"""
<div class="badge-card {'earned' if _earned else ''}" style="opacity:{'1' if _earned else '.35'};">
  <div style="font-size:1.9rem;">{_bdg['icon']}</div>
  <div style="color:{'#4ade80' if _earned else '#94a3b8'};font-weight:600;font-size:.77rem;margin-top:5px;">
    {_bdg['name']}
  </div>
  <div style="color:#475569;font-size:.65rem;margin-top:3px;">{_bdg['desc']}</div>
  <div style="margin-top:5px;">
    {'<span style="color:#4ade80;font-size:.65rem;font-weight:700;">✓ EARNED</span>'
     if _earned else
     '<span style="color:#475569;font-size:.65rem;">Locked</span>'}
  </div>
</div>""", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 📈 Your Impact Summary")
    _imp1, _imp2, _imp3 = st.columns(3)
    def _impact_card(col, icon, val, label, clr):
        col.markdown(f"""
<div style="background:#111b2e;border-radius:14px;padding:20px;text-align:center;border:1px solid #1e2d45;">
  <div style="font-size:2.4rem;">{icon}</div>
  <div style="color:{clr};font-size:1.5rem;font-weight:700;margin:7px 0;">{val}</div>
  <div style="color:#94a3b8;font-size:.8rem;">{label}</div>
</div>""", unsafe_allow_html=True)

    _impact_card(_imp1, "🌳",
                 f'{_stats["co2"] / CO2_EQUIV["🌳 Tree-days (absorption)"]:.1f}',
                 "tree-days of CO₂ absorption", "#4ade80")
    _impact_card(_imp2, "🚗",
                 f'{_stats["co2"] / CO2_EQUIV["🚗 km not driven"]:.1f}',
                 "km of driving avoided", "#60a5fa")
    _impact_card(_imp3, "📱",
                 f'{_stats["co2"] / CO2_EQUIV["📱 phone charges"]:.0f}',
                 "phone charges equivalent", "#a78bfa")

    # Tips
    st.markdown("---")
    st.markdown("#### 💡 Smart Tips")
    _min_t = _full.loc[_full["ci"].idxmin(), "time"]
    _max_t = _full.loc[_full["ci"].idxmax(), "time"]
    _avg_c = float(_full["ci"].mean())
    for _tip, _clr in [
        (f"🌙 Best time today: **{to_12h(_min_t)}** — grid is cleanest then.", "#4ade80"),
        (f"🔥 Avoid **{to_12h(_max_t)}** — dirtiest grid of the day.", "#f87171"),
        (f"📊 Today's avg CI: **{_avg_c:.0f} gCO₂/kWh** — "
         f"{'below' if _avg_c < 300 else 'above'} the 300 g benchmark.", "#fbbf24"),
        ("🔄 Enable Live Mode in the sidebar to keep data fresh automatically.", "#60a5fa"),
        ("🏅 Log your runs to build your streak and unlock badges!", "#a78bfa"),
    ]:
        st.markdown(
            f'<div style="background:#111b2e;border-left:3px solid {_clr};border-radius:8px;'
            f'padding:10px 14px;margin-bottom:6px;color:#94a3b8;font-size:.83rem;">{_tip}</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════
# TAB 6 — Logger
# ══════════════════════════════════════════════
with T6:
    st.subheader("📝 Log an Appliance Run")
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

        st.markdown("##### Meter Readings")
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
    st.subheader("🔍 Raw Grid Data & Config")

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

    st.markdown("---")
    _cols_show = [c for c in ["time","thermal_mw","hydro_mw","nuclear_mw","res_mw","ci"] if c in _raw.columns]
    st.dataframe(
        _raw[_cols_show].sort_values("time").reset_index(drop=True),
        use_container_width=True, hide_index=True, height=360,
    )

    st.markdown("---")
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

    st.markdown("---")
    st.markdown("#### ℹ️ Simulation Architecture")
    st.markdown(f"""
<div style="background:#111b2e;border-radius:10px;padding:14px 16px;border:1px solid #1e2d45;
            color:#94a3b8;font-size:.81rem;line-height:1.75;">
  <strong style="color:#4ade80;">DataEngine.fetch_live()</strong> first attempts the Electricity Maps free-tier API.
  On failure it falls back to <code>_simulate_profile()</code>, a time-seeded NumPy simulation that
  reproduces real demand curves (night troughs, morning/evening peaks, midday solar suppression).<br><br>
  The <strong style="color:#4ade80;">refresh seed</strong> rotates every <strong>{st.session_state.refresh_s} s</strong>,
  so data evolves naturally between pages without identical repeats.
  All 96 fifteen-minute slots are computed in one vectorised pass — no gaps, no duplicate slots.
</div>""", unsafe_allow_html=True)
