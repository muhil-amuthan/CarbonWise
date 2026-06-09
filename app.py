"""
CarbonWise ⚡ v3 — Real-Time Grid Carbon Intensity Scheduler
============================================================
UI overhaul:
  • Aurora-gradient dark theme (deep navy → teal → emerald)
  • All headings use styled HTML banners — no plain st.subheader collisions
  • KPI cards with proper min-height, truncation, and flex layout
  • Recommendation cards use CSS grid — no column overflow
  • Heatmap and chart containers have explicit spacing
  • Ticker bar wraps gracefully on smaller screens
  • Section headers use eyebrow + title pattern with divider lines
  • Badge grid uses auto-fill columns — no overlap
  • All overlapping text fixed via overflow:hidden + text-overflow:ellipsis
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
STEP_MIN    = 15
LOG_FILE    = Path("logs/runs.jsonl")
CONFIG_FILE = Path("config/location.json")
DATA_DIR    = Path("data")
EM_API_BASE = "https://api-access.electricitymaps.com/free-tier"

SOURCE_FACTORS = {"thermal": 820.0, "hydro": 24.0, "nuclear": 12.0, "res": 50.0}

CO2_EQUIV = {
    "🌳 Tree-days":        0.021,
    "🚗 km not driven":    0.192,
    "✈️ Passenger-km":    0.255,
    "📱 Phone charges":    0.00844,
}

HOUR_DEMAND = [
    0.62, 0.58, 0.55, 0.53, 0.54, 0.62,
    0.78, 0.92, 1.08, 1.15, 1.10, 1.05,
    1.00, 0.97, 0.93, 0.94, 1.02, 1.18,
    1.25, 1.22, 1.15, 1.05, 0.92, 0.75,
]

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
    {"id": "first_save",   "name": "First Save",    "icon": "🌱", "desc": "Logged first run",         "threshold": 1},
    {"id": "green_week",   "name": "Green Week",     "icon": "🌿", "desc": "7-day streak",             "threshold": 7},
    {"id": "ton_saver",    "name": "Ton Saver",      "icon": "🏆", "desc": "Saved 1 kg CO₂ total",    "threshold": 1.0},
    {"id": "optimizer",    "name": "Optimizer",      "icon": "⚡", "desc": "10 recommended runs",     "threshold": 10},
    {"id": "eco_champion", "name": "Eco Champion",   "icon": "🌍", "desc": "Saved 10 kg CO₂ total",   "threshold": 10.0},
    {"id": "night_owl",    "name": "Night Owl",      "icon": "🦉", "desc": "Run at 22:00–05:00",      "threshold": 1},
    {"id": "solar_surfer", "name": "Solar Surfer",   "icon": "☀️", "desc": "Run at 10:00–16:00",     "threshold": 1},
    {"id": "early_bird",   "name": "Early Bird",     "icon": "🐦", "desc": "Run before 06:00",        "threshold": 1},
]

# ──────────────────────────────────────────────────────────────
# 2. Pure helpers
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
    return "#00e5a0" if ci < 200 else "#f5a623" if ci < 400 else "#ff5a5a"

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
    def windows(full: pd.DataFrame, duration_min: int, deadline: str, now: str, top_k: int) -> list[dict]:
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
# 4. Persistence
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
    if runs >= 1:       badges.append("first_save")
    if streak >= 7:     badges.append("green_week")
    if total_co2 >= 1:  badges.append("ton_saver")
    if rec >= 10:       badges.append("optimizer")
    if total_co2 >= 10: badges.append("eco_champion")
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
# 5. Global CSS — Aurora theme, no text overlap
# ──────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');

/* ═══════════════════════════════════════════
   BASE RESET & TYPOGRAPHY
═══════════════════════════════════════════ */
*, *::before, *::after {
  box-sizing: border-box;
  font-family: 'Plus Jakarta Sans', sans-serif !important;
}
code, pre, .mono { font-family: 'JetBrains Mono', monospace !important; }

/* ═══════════════════════════════════════════
   BACKGROUNDS — deep aurora palette
═══════════════════════════════════════════ */
.main,
[data-testid="stAppViewContainer"],
[data-testid="stHeader"] {
  background: #060c18 !important;
}
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #07101f 0%, #081428 100%) !important;
  border-right: 1px solid rgba(0,229,160,.10) !important;
}

/* ═══════════════════════════════════════════
   SECTION HEADING PATTERN
   eyebrow label + big title + teal underline
═══════════════════════════════════════════ */
.section-header {
  margin: 2rem 0 1.2rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid rgba(0,229,160,.15);
}
.section-header .eyebrow {
  display: block;
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #00e5a0;
  margin-bottom: 4px;
}
.section-header h2 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 800;
  color: #f0f6ff;
  line-height: 1.25;
  /* prevent overflow on narrow viewports */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.section-header p {
  margin: 5px 0 0;
  font-size: 0.78rem;
  color: #566880;
  line-height: 1.5;
}

/* ═══════════════════════════════════════════
   HERO BANNER
═══════════════════════════════════════════ */
.hero {
  background: linear-gradient(135deg, #06101e 0%, #0b1d35 45%, #081528 100%);
  padding: 2.2rem 2.5rem 2rem;
  border-radius: 20px;
  margin-bottom: 1.5rem;
  border: 1px solid rgba(0,229,160,.18);
  position: relative;
  overflow: hidden;
}
.hero::before {
  content: '';
  position: absolute;
  top: -60px; right: -60px;
  width: 320px; height: 320px;
  background: radial-gradient(circle, rgba(0,229,160,.09) 0%, transparent 70%);
  border-radius: 50%;
  pointer-events: none;
}
.hero::after {
  content: '';
  position: absolute;
  bottom: -80px; left: 30%;
  width: 260px; height: 260px;
  background: radial-gradient(circle, rgba(99,102,241,.07) 0%, transparent 70%);
  border-radius: 50%;
  pointer-events: none;
}
.hero-title {
  font-size: 2.4rem;
  font-weight: 800;
  color: #f0f6ff;
  margin: 0 0 6px;
  line-height: 1.1;
  letter-spacing: -0.5px;
}
.hero-title span { color: #00e5a0; }
.hero-sub {
  color: #6b8099;
  font-size: 0.95rem;
  max-width: 580px;
  margin: 0 0 1.1rem;
  line-height: 1.6;
}
.hero-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.pill {
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.pill-green  { background: rgba(0,229,160,.12); color: #00e5a0; border: 1px solid rgba(0,229,160,.28); }
.pill-blue   { background: rgba(99,102,241,.12); color: #818cf8; border: 1px solid rgba(99,102,241,.25); }
.pill-amber  { background: rgba(245,166,35,.10); color: #f5a623; border: 1px solid rgba(245,166,35,.22); }
.pill-muted  { color: #3d5166; font-size: 0.75rem; }

/* ═══════════════════════════════════════════
   LIVE TICKER
═══════════════════════════════════════════ */
.ticker {
  background: linear-gradient(90deg, rgba(0,229,160,.05) 0%, transparent 100%);
  border: 1px solid rgba(0,229,160,.16);
  border-radius: 12px;
  padding: 0.7rem 1.2rem;
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  overflow: hidden;
  margin-bottom: 1.2rem;
}
.ticker-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: #00e5a0;
  flex-shrink: 0;
  animation: pulse-dot 1.6s ease-in-out infinite;
}
.ticker-item {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.78rem;
  white-space: nowrap;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.4; transform: scale(1.4); }
}

/* ═══════════════════════════════════════════
   KPI CARDS — fixed height, no overflow
═══════════════════════════════════════════ */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
  margin-bottom: 1.5rem;
}
@media (max-width: 1200px) {
  .kpi-grid { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 700px) {
  .kpi-grid { grid-template-columns: repeat(2, 1fr); }
}
.kpi {
  background: linear-gradient(145deg, #0d1b2e, #0a1320);
  border: 1px solid rgba(0,229,160,.10);
  border-left: 3px solid #00e5a0;
  border-radius: 14px;
  padding: 1rem 0.9rem 0.85rem;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 100px;
  overflow: hidden;
  transition: transform 0.18s, border-color 0.18s;
}
.kpi:hover {
  transform: translateY(-2px);
  border-color: rgba(0,229,160,.35);
}
.kpi-lbl {
  font-size: 0.62rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: #3d5166;
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.kpi-val {
  font-size: 1.15rem;
  font-weight: 800;
  color: #f0f6ff;
  line-height: 1.15;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kpi-sub {
  font-size: 0.63rem;
  color: #334456;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ═══════════════════════════════════════════
   RECOMMENDATION CARDS — 3-col CSS grid
   prevents overlap on any screen size
═══════════════════════════════════════════ */
.rec-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
  margin-bottom: 1.5rem;
}
.rec-card {
  background: linear-gradient(145deg, #0d1b2e, #0a1320);
  border: 1px solid rgba(0,229,160,.12);
  border-radius: 16px;
  padding: 1.2rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: transform 0.18s, box-shadow 0.18s;
  overflow: hidden;
}
.rec-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 12px 32px rgba(0,229,160,.08);
}
.rec-card.best {
  border-color: #00e5a0;
  box-shadow: 0 0 0 1px rgba(0,229,160,.25), 0 8px 24px rgba(0,229,160,.12);
}
.rec-rank {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.rank-badge {
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 0.68rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  white-space: nowrap;
}
.rank-best { background: #00e5a0; color: #030e18; }
.rank-n    { background: rgba(99,102,241,.15); color: #818cf8; border: 1px solid rgba(99,102,241,.25); }
.conf-tag {
  font-size: 0.62rem;
  font-weight: 600;
  color: #00e5a0;
  background: rgba(0,229,160,.08);
  padding: 2px 7px;
  border-radius: 4px;
  white-space: nowrap;
}
.rec-time {
  font-size: 1.1rem;
  font-weight: 800;
  color: #f0f6ff;
  text-align: center;
  padding: 8px 0;
  border-top: 1px solid rgba(255,255,255,.05);
  border-bottom: 1px solid rgba(255,255,255,.05);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rec-stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.rec-stat {
  background: rgba(0,0,0,.25);
  border-radius: 8px;
  padding: 6px 5px;
  text-align: center;
  overflow: hidden;
}
.rec-stat-lbl {
  font-size: 0.58rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #3d5166;
  margin-bottom: 2px;
  white-space: nowrap;
}
.rec-stat-val {
  font-size: 0.88rem;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rec-saving {
  background: rgba(0,229,160,.07);
  border: 1px solid rgba(0,229,160,.18);
  border-radius: 8px;
  padding: 7px 9px;
  font-size: 0.76rem;
  font-weight: 600;
  color: #00e5a0;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rec-equiv {
  font-size: 0.65rem;
  color: #3d5166;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ═══════════════════════════════════════════
   WEEKLY DAY CARDS — uniform grid
═══════════════════════════════════════════ */
.week-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 8px;
  margin-bottom: 1.5rem;
}
@media (max-width: 900px) {
  .week-grid { grid-template-columns: repeat(4, 1fr); }
}
.day-card {
  background: #0d1b2e;
  border: 1px solid rgba(0,229,160,.10);
  border-radius: 12px;
  padding: 10px 6px;
  text-align: center;
  overflow: hidden;
  transition: transform 0.15s;
}
.day-card:hover { transform: translateY(-2px); }
.day-card.today { border-color: #00e5a0; }
.day-label   { font-size: 0.62rem; font-weight: 700; text-transform: uppercase; color: #3d5166; letter-spacing: .06em; }
.day-icon    { font-size: 1.15rem; margin: 4px 0; }
.day-ci      { font-size: 0.92rem; font-weight: 800; }
.day-unit    { font-size: 0.55rem; color: #3d5166; }
.day-best    { background: rgba(0,229,160,.09); border-radius: 4px; padding: 2px 4px; margin-top: 4px; font-size: 0.58rem; color: #00e5a0; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ═══════════════════════════════════════════
   BADGE GRID — auto-fill, no overlap
═══════════════════════════════════════════ */
.badge-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 1.5rem;
}
.badge-card {
  background: #0d1b2e;
  border: 1px solid #111f34;
  border-radius: 14px;
  padding: 16px 12px;
  text-align: center;
  overflow: hidden;
  transition: transform 0.18s, border-color 0.18s;
}
.badge-card.earned {
  border-color: rgba(0,229,160,.38);
  background: rgba(0,229,160,.04);
}
.badge-card:hover { transform: scale(1.03); }
.badge-icon  { font-size: 2rem; margin-bottom: 6px; }
.badge-name  {
  font-size: 0.74rem;
  font-weight: 700;
  margin-bottom: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.badge-desc  {
  font-size: 0.62rem;
  color: #3d5166;
  line-height: 1.4;
  /* allow 2 lines, clip at 3 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.badge-status { font-size: 0.62rem; font-weight: 700; margin-top: 5px; }

/* ═══════════════════════════════════════════
   IMPACT CARDS
═══════════════════════════════════════════ */
.impact-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin-bottom: 1.2rem;
}
@media (max-width: 700px) {
  .impact-grid { grid-template-columns: 1fr; }
}
.impact-card {
  background: #0d1b2e;
  border: 1px solid #111f34;
  border-radius: 14px;
  padding: 22px 16px;
  text-align: center;
  overflow: hidden;
}
.impact-icon  { font-size: 2.2rem; margin-bottom: 8px; }
.impact-val   { font-size: 1.55rem; font-weight: 800; margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.impact-label { font-size: 0.77rem; color: #566880; line-height: 1.4; }

/* ═══════════════════════════════════════════
   ALERT BANNERS
═══════════════════════════════════════════ */
.alert {
  padding: 0.8rem 1.1rem;
  border-radius: 12px;
  margin-bottom: 0.75rem;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  overflow: hidden;
}
.alert-icon { font-size: 1.1rem; flex-shrink: 0; margin-top: 1px; }
.alert-body { flex: 1; min-width: 0; }
.alert-title { font-size: 0.83rem; font-weight: 700; margin-bottom: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.alert-desc  { font-size: 0.77rem; color: #6b8099; line-height: 1.4; }
.alert-g { background: rgba(0,229,160,.07);  border: 1px solid rgba(0,229,160,.25);  border-left: 4px solid #00e5a0; }
.alert-r { background: rgba(255,90,90,.07);  border: 1px solid rgba(255,90,90,.25);  border-left: 4px solid #ff5a5a; }
.alert-y { background: rgba(245,166,35,.07); border: 1px solid rgba(245,166,35,.22); border-left: 4px solid #f5a623; }

/* ═══════════════════════════════════════════
   PROGRESS BAR
═══════════════════════════════════════════ */
.pb      { height: 6px; border-radius: 999px; background: #0d1b2e; margin: 6px 0; overflow: hidden; }
.pb-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #00e5a0, #06b6d4); }

/* ═══════════════════════════════════════════
   LIVE CLOCK
═══════════════════════════════════════════ */
.live-clock {
  background: rgba(0,229,160,.06);
  border: 1px solid rgba(0,229,160,.22);
  border-radius: 10px;
  padding: 0.65rem 1rem;
  color: #00e5a0;
  font-weight: 700;
  text-align: center;
  font-size: 1rem;
  letter-spacing: 0.05em;
  font-family: 'JetBrains Mono', monospace !important;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tz-sub {
  color: #3d5166;
  font-size: 0.68rem;
  text-align: center;
  margin-top: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ═══════════════════════════════════════════
   TIPS
═══════════════════════════════════════════ */
.tip-card {
  border-radius: 10px;
  padding: 10px 14px;
  margin-bottom: 7px;
  font-size: 0.82rem;
  line-height: 1.55;
  color: #6b8099;
  border-left: 3px solid;
  overflow: hidden;
}

/* ═══════════════════════════════════════════
   INFO BOXES (co2 equiv, config panel)
═══════════════════════════════════════════ */
.info-box {
  background: #0d1b2e;
  border: 1px solid #111f34;
  border-radius: 12px;
  padding: 13px 14px;
  overflow: hidden;
}
.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 5px 0;
  border-bottom: 1px solid #111f34;
  gap: 8px;
  min-width: 0;
}
.info-row:last-child { border-bottom: none; }
.info-row-label { font-size: 0.74rem; color: #566880; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.info-row-val   { font-size: 0.78rem; font-weight: 700; color: #f0f6ff; flex-shrink: 0; }

/* ═══════════════════════════════════════════
   SIDEBAR CUSTOMISATION
═══════════════════════════════════════════ */
[data-testid="stSidebar"] .block-container { padding-top: 1rem !important; }
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
  color: #f0f6ff !important;
  font-weight: 700 !important;
}

/* ═══════════════════════════════════════════
   DIVIDER
═══════════════════════════════════════════ */
hr { border-color: rgba(0,229,160,.10) !important; margin: 1.25rem 0 !important; }

/* ═══════════════════════════════════════════
   STAT LOCATION INFO
═══════════════════════════════════════════ */
.loc-info {
  background: #111f34;
  padding: 9px 12px;
  border-radius: 8px;
  margin-top: 6px;
  overflow: hidden;
}
.loc-key { color: #3d5166; font-size: 0.68rem; text-transform: uppercase; letter-spacing: .07em; }
.loc-val { color: #00e5a0; font-size: 0.82rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.loc-sub { color: #3d5166; font-size: 0.67rem; margin-top: 2px; }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# 6. Session state
# ──────────────────────────────────────────────────────────────

_SS_DEFAULTS = {
    "appliance":       "Water Motor",
    "kw":              0.75,
    "duration":        30,
    "deadline":        "08:00",
    "loc_mode":        "Auto-Detect",
    "region":          "India",
    "zone":            "North India (NR)",
    "lat":             28.6139,
    "lon":             77.2090,
    "tz":              "Asia/Kolkata",
    "data_source":     "Automatic (API)",
    "api_token":       "",
    "live_mode":       True,
    "refresh_s":       300,
    "alert_enabled":   True,
    "alert_thresh":    200,
    "daily_budget_kg": 1.0,
    "sel_window":      0,
    "upload_hash":     "",
    "upload_df_json":  "",
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
    st.markdown("## ⚙️ Configuration")

    # ── Live Mode ────────────────────────────────────────────
    st.markdown("### 🔄 Live Mode")
    st.session_state.live_mode = st.toggle("Auto-refresh", value=st.session_state.live_mode, key="_live")
    if st.session_state.live_mode:
        _ri_map = {60: "1 min", 120: "2 min", 300: "5 min", 600: "10 min"}
        st.session_state.refresh_s = st.select_slider(
            "Interval", options=list(_ri_map.keys()),
            value=st.session_state.refresh_s,
            format_func=lambda x: _ri_map[x],
        )
        st.markdown(
            f'<div style="background:#0b1d35;border:1px solid rgba(0,229,160,.22);'
            f'border-radius:8px;padding:7px 11px;margin-top:4px;">'
            f'<span style="color:#00e5a0;font-size:.74rem;font-weight:700;">'
            f'🟢 Live — every {_ri_map[st.session_state.refresh_s]}</span></div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Location ─────────────────────────────────────────────
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
                st.session_state.lat = _loc["lat"]
                st.session_state.lon = _loc["lon"]
                st.session_state.tz  = _loc.get("timezone", "UTC")
                st.success(f"📍 {_loc['city']}, {_loc['country']}")
                st.rerun()
            else:
                st.error("Detection failed — try Manual Select.")
        st.markdown(
            f'<div class="loc-info">'
            f'<div class="loc-key">Coordinates</div>'
            f'<div class="loc-val">{st.session_state.lat:.4f}, {st.session_state.lon:.4f}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    elif st.session_state.loc_mode == "GPS (Browser)":
        components.html("""
<style>
html,body{margin:0;padding:0;background:transparent;font-family:sans-serif;}
#btn{width:100%;padding:9px;background:linear-gradient(135deg,#0b1d35,#060c18);
     color:#00e5a0;border:1.5px solid rgba(0,229,160,.4);border-radius:8px;
     cursor:pointer;font-weight:700;font-size:13px;}
#btn:hover{background:rgba(0,229,160,.1);}#btn:disabled{opacity:.5;}
#msg{color:#566880;font-size:11px;margin-top:5px;text-align:center;min-height:16px;}
.c{background:#0b1d35;padding:8px;border-radius:6px;margin-top:6px;
   color:#00e5a0;font-size:12px;font-weight:700;display:none;}
</style>
<button id="btn" onclick="go()">📡 Use GPS</button>
<div id="msg"></div><div id="c" class="c"></div>
<script>
function go(){
  var btn=document.getElementById('btn'),msg=document.getElementById('msg'),c=document.getElementById('c');
  if(!navigator.geolocation){msg.innerHTML='<span style="color:#ff5a5a">Not supported</span>';return;}
  btn.textContent='⏳ Locating…';btn.disabled=true;msg.textContent='Waiting…';
  navigator.geolocation.getCurrentPosition(
    p=>{btn.textContent='✅ Done';c.style.display='block';
        c.innerHTML='📍 '+p.coords.latitude.toFixed(5)+', '+p.coords.longitude.toFixed(5);},
    e=>{btn.textContent='📡 Use GPS';btn.disabled=false;
        msg.innerHTML='<span style="color:#ff5a5a">'+(e.code===1?'Permission denied':'Error')+'</span>';},
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
            f'<div class="loc-info">'
            f'<div class="loc-key">Zone Avg CI</div>'
            f'<div class="loc-val">{_zd["ci_avg"]} gCO₂/kWh</div>'
            f'<div class="loc-sub">{GRID_ZONES[_reg]["voltage"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        _cc, _cd = st.columns(2)
        with _cc: st.session_state.lat = st.number_input("Lat",  -90.0,  90.0, st.session_state.lat, format="%.4f")
        with _cd: st.session_state.lon = st.number_input("Lon", -180.0, 180.0, st.session_state.lon, format="%.4f")
        _tz_list = pytz.common_timezones
        st.session_state.tz = st.selectbox(
            "Timezone", _tz_list,
            index=_tz_list.index(st.session_state.tz) if st.session_state.tz in _tz_list else 0,
        )

    st.divider()

    # ── Clock ────────────────────────────────────────────────
    st.markdown(f'<div class="live-clock">🕐 {exact_time_str(st.session_state.tz)}</div>', unsafe_allow_html=True)
    _slot = slot_str(st.session_state.tz)
    st.markdown(f'<div class="tz-sub">{st.session_state.tz} · slot {to_12h(_slot)}</div>', unsafe_allow_html=True)

    st.divider()

    # ── Data Source ──────────────────────────────────────────
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
        st.warning("Expects columns: time, thermal_mw, hydro_mw, nuclear_mw, res_mw")
        _upload_file = st.file_uploader("CSV", type=["csv"], label_visibility="collapsed")
    else:
        st.caption("Built-in synthetic grid profile.")

    st.divider()

    # ── Appliance ────────────────────────────────────────────
    st.markdown("### 🔌 Appliance")
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
        st.error("⚠️ Use HH:MM (24-h)")

    _avail = (hm_to_mins(st.session_state.deadline) - hm_to_mins(_slot)) % 1440
    st.info(f"⏱️ **{_avail} min** until deadline ({_avail // 60}h {_avail % 60}m)")

    if st.button("↩️ Reset Defaults", use_container_width=True):
        _ap = APPLIANCES[st.session_state.appliance]
        st.session_state.kw       = _ap["kw"]
        st.session_state.duration = _ap["dur"]
        st.session_state.deadline = _ap["deadline"]
        st.rerun()

    st.divider()

    # ── Optimisation ─────────────────────────────────────────
    st.markdown("### ⚡ Optimisation")
    _top_k = st.slider("Top windows", 3, 12, 5)

    st.divider()

    # ── Alerts & Budget ──────────────────────────────────────
    st.markdown("### 🔔 Alerts & Budget")
    st.session_state.alert_enabled = st.toggle("Enable CI alerts", value=st.session_state.alert_enabled)
    if st.session_state.alert_enabled:
        st.session_state.alert_thresh = st.slider("Alert below (gCO₂/kWh)", 50, 400, st.session_state.alert_thresh, 25)
    st.session_state.daily_budget_kg = st.number_input(
        "Daily CO₂ budget (kg)", 0.1, 10.0,
        float(st.session_state.daily_budget_kg), 0.1, "%.1f"
    )

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
else:
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

if "ci" not in _raw.columns:
    _raw["ci"] = _raw.apply(
        lambda r: compute_ci(r["thermal_mw"], r["hydro_mw"], r["nuclear_mw"], r["res_mw"]), axis=1
    )

_full     = DataEngine.full_day(_raw)
_now_slot = slot_str(st.session_state.tz)
_now_12   = to_12h(_now_slot)
_dl_12    = to_12h(st.session_state.deadline)
_windows  = DataEngine.windows(
    _full, st.session_state.duration, st.session_state.deadline, _now_slot, _top_k
)
_cur_ci   = DataEngine.ci_at(_full, _now_slot) or float(_full["ci"].mean())
_best     = _windows[0] if _windows else None
_nrg      = st.session_state.kw * (ceil15(st.session_state.duration) / 60)
_pot_pct  = (((_cur_ci - _best["avg_ci"]) / _cur_ci) * 100) if (_best and _cur_ci > 0) else 0
_weekly   = DataEngine.weekly_forecast(float(_full["ci"].mean()))
_log_df   = read_log()
_stats    = compute_stats(_log_df)

_loc_lbl = (
    st.session_state.zone if st.session_state.loc_mode == "Manual Select"
    else "GPS"             if st.session_state.loc_mode == "GPS (Browser)"
    else f"{st.session_state.lat:.2f}°, {st.session_state.lon:.2f}°"
)

# ──────────────────────────────────────────────────────────────
# 10. HERO
# ──────────────────────────────────────────────────────────────
_live_pill = (
    '<span class="pill pill-green">'
    '<span style="width:7px;height:7px;border-radius:50%;background:#00e5a0;'
    'display:inline-block;animation:pulse-dot 1.6s infinite;"></span> LIVE</span>'
    if st.session_state.live_mode else ""
)

st.markdown(f"""
<div class="hero">
  <div class="hero-title">🌍 Carbon<span>Wise</span></div>
  <p class="hero-sub">
    Schedule appliances during the cleanest grid windows.
    Real-time carbon intensity tracking to shrink your footprint automatically.
  </p>
  <div class="hero-pills">
    {_live_pill}
    <span class="pill pill-blue">⚡ Smart Scheduler</span>
    <span class="pill pill-amber">🏅 Gamified</span>
    <span class="pill-muted">🕐 {exact_time_str(st.session_state.tz)} &nbsp;·&nbsp; {_loc_lbl}</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# 11. Alert banner
# ──────────────────────────────────────────────────────────────
if st.session_state.alert_enabled:
    if _cur_ci <= st.session_state.alert_thresh:
        st.markdown(f"""
<div class="alert alert-g">
  <div class="alert-icon">🟢</div>
  <div class="alert-body">
    <div class="alert-title" style="color:#00e5a0;">GREEN GRID ALERT</div>
    <div class="alert-desc">
      CI is <strong style="color:#00e5a0;">{_cur_ci:.0f} gCO₂/kWh</strong> —
      below your {st.session_state.alert_thresh} g threshold.
      Great time to run appliances!
    </div>
  </div>
</div>""", unsafe_allow_html=True)
    elif _cur_ci > 400:
        st.markdown(f"""
<div class="alert alert-r">
  <div class="alert-icon">🔴</div>
  <div class="alert-body">
    <div class="alert-title" style="color:#ff5a5a;">HIGH CARBON ALERT</div>
    <div class="alert-desc">
      CI is <strong style="color:#ff5a5a;">{_cur_ci:.0f} gCO₂/kWh</strong> —
      consider delaying non-urgent appliances.
    </div>
  </div>
</div>""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# 12. Live ticker
# ──────────────────────────────────────────────────────────────
_trend = "↑" if _cur_ci > float(_full["ci"].mean()) else "↓"
_tc    = ci_color(_cur_ci)

st.markdown(f"""
<div class="ticker">
  <div class="ticker-dot"></div>
  <span class="ticker-item" style="color:#3d5166;font-weight:700;letter-spacing:.06em;font-size:.7rem;">LIVE GRID</span>
  <span class="ticker-item" style="color:{_tc};font-weight:800;">
    {ci_emoji(_cur_ci)} {_cur_ci:.0f} gCO₂/kWh {_trend}
  </span>
  <span style="color:#1a2d42;">|</span>
  <span class="ticker-item" style="color:#818cf8;">Best: {to_12h(_best["start"]) if _best else "--"}</span>
  <span style="color:#1a2d42;">|</span>
  <span class="ticker-item" style="color:#f5a623;">🔥 {_stats["streak"]}-day streak</span>
  <span style="color:#1a2d42;">|</span>
  <span class="ticker-item" style="color:#00e5a0;">Saved {_stats["co2"]:.3f} kg CO₂</span>
  <span style="color:#1a2d42;">|</span>
  <span class="ticker-item" style="color:#3d5166;">{st.session_state.data_source}</span>
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# 13. KPI grid (pure HTML — no Streamlit column sizing issues)
# ──────────────────────────────────────────────────────────────
_icon = APPLIANCES[st.session_state.appliance]["icon"]
_sc   = "#00e5a0" if _stats["streak"] >= 3 else "#f5a623" if _stats["streak"] else "#566880"

st.markdown(f"""
<div class="kpi-grid">

  <div class="kpi" style="border-left-color:#818cf8;">
    <div class="kpi-lbl">🌍 Location</div>
    <div class="kpi-val" style="color:#818cf8;">{_loc_lbl[:22]}</div>
    <div class="kpi-sub">{st.session_state.tz}</div>
  </div>

  <div class="kpi" style="border-left-color:#06b6d4;">
    <div class="kpi-lbl">🕐 Current Time</div>
    <div class="kpi-val" style="color:#06b6d4;">{_now_12}</div>
    <div class="kpi-sub">{'🟢 Live auto-refresh' if st.session_state.live_mode else '⚪ Static mode'}</div>
  </div>

  <div class="kpi" style="border-left-color:#f5a623;">
    <div class="kpi-lbl">🔌 Appliance</div>
    <div class="kpi-val" style="color:#f5a623;">{_icon} {st.session_state.appliance.split("/")[0][:14]}</div>
    <div class="kpi-sub">{st.session_state.kw:.2f} kW · {ceil15(st.session_state.duration)} min</div>
  </div>

  <div class="kpi" style="border-left-color:{ci_color(_cur_ci)};">
    <div class="kpi-lbl">⚡ CI Right Now</div>
    <div class="kpi-val" style="color:{ci_color(_cur_ci)};">{_cur_ci:.0f}</div>
    <div class="kpi-sub">{ci_label(_cur_ci)} · gCO₂/kWh</div>
  </div>

  <div class="kpi" style="border-left-color:{'#00e5a0' if _best else '#566880'};">
    <div class="kpi-lbl">✨ Best Window</div>
    <div class="kpi-val" style="color:{'#00e5a0' if _best else '#566880'};">
      {to_12h(_best["start"]) if _best else "--:--"}
    </div>
    <div class="kpi-sub">{'Save ~' + f"{_pot_pct:.1f}%" if _pot_pct > 0 else 'No window found'}</div>
  </div>

  <div class="kpi" style="border-left-color:{_sc};">
    <div class="kpi-lbl">🔥 Streak</div>
    <div class="kpi-val" style="color:{_sc};">{_stats["streak"]} days</div>
    <div class="kpi-sub">{len(_stats["badges"])} badges · {_stats["runs"]} runs logged</div>
  </div>

</div>
""", unsafe_allow_html=True)

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
    st.markdown(f"""
<div class="section-header">
  <span class="eyebrow">Real-time monitoring</span>
  <h2>📈 24-Hour Carbon Intensity · {st.session_state.tz}</h2>
</div>
""", unsafe_allow_html=True)

    col_main, col_side = st.columns([2, 1], gap="large")

    with col_main:
        fig_g = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=_cur_ci,
            delta={"reference": float(_full["ci"].mean()), "valueformat": ".0f", "suffix": " avg"},
            title={"text": "Current CI (gCO₂/kWh)", "font": {"color": "#566880", "size": 12}},
            number={"font": {"color": ci_color(_cur_ci), "size": 38}},
            gauge={
                "axis":    {"range": [0, 700], "tickcolor": "#1e3251", "tickfont": {"color": "#1e3251", "size": 10}},
                "bar":     {"color": ci_color(_cur_ci), "thickness": 0.22},
                "bgcolor": "#0d1b2e", "borderwidth": 0,
                "steps": [
                    {"range": [0,   200], "color": "rgba(0,229,160,.08)"},
                    {"range": [200, 400], "color": "rgba(245,166,35,.07)"},
                    {"range": [400, 700], "color": "rgba(255,90,90,.07)"},
                ],
                "threshold": {"line": {"color": ci_color(_cur_ci), "width": 2}, "thickness": 0.7, "value": _cur_ci},
            },
        ))
        fig_g.update_layout(
            paper_bgcolor="#0a1320", font={"color": "#94a3b8"},
            height=195, margin={"l": 30, "r": 30, "t": 25, "b": 5},
        )
        st.plotly_chart(fig_g, use_container_width=True)

        fig_l = go.Figure()
        fig_l.add_trace(go.Scatter(
            x=_full["time"], y=_full["ci"],
            fill="tozeroy", fillcolor="rgba(0,229,160,.05)",
            line={"color": "#00e5a0", "width": 2.5},
            hovertemplate="<b>%{x}</b><br>CI: %{y:.1f} gCO₂/kWh<extra></extra>",
        ))
        fig_l.add_hrect(y0=0,   y1=200, fillcolor="#00e5a0", opacity=0.025, line_width=0)
        fig_l.add_hrect(y0=200, y1=400, fillcolor="#f5a623", opacity=0.025, line_width=0)
        fig_l.add_hrect(y0=400, y1=900, fillcolor="#ff5a5a", opacity=0.025, line_width=0)
        for wi, w in enumerate((_windows or [])[:3]):
            fig_l.add_vrect(
                x0=w["start"], x1=w["end"],
                fillcolor=w["color"], opacity=0.12,
                line_width=2 if wi == 0 else 1, line_color=w["color"], layer="below",
            )
        _ymax = float(_full["ci"].max()) * 1.12
        fig_l.add_vline(x=_now_slot, line_dash="dash", line_color="#00e5a0", line_width=1.6)
        fig_l.add_vline(x=st.session_state.deadline, line_dash="dash", line_color="#ff5a5a", line_width=1.6)
        fig_l.add_annotation(x=_now_slot, y=_ymax, text="NOW",
                              showarrow=False, font={"color": "#00e5a0", "size": 10},
                              bgcolor="rgba(6,12,24,.85)")
        fig_l.add_annotation(x=st.session_state.deadline, y=_ymax, text="DEADLINE",
                              showarrow=False, font={"color": "#ff5a5a", "size": 10},
                              bgcolor="rgba(6,12,24,.85)")
        fig_l.add_hline(y=float(_full["ci"].mean()), line_dash="dot", line_color="#818cf8",
                        line_width=1, annotation_text="Daily avg",
                        annotation_font_color="#818cf8")
        fig_l.update_layout(
            plot_bgcolor="#0a1320", paper_bgcolor="#0a1320", font={"color": "#94a3b8"},
            xaxis={"gridcolor": "#0d1b2e", "title": "Time of Day", "tickangle": -45, "nticks": 13},
            yaxis={"gridcolor": "#0d1b2e", "title": "gCO₂/kWh", "range": [0, _ymax * 1.1]},
            height=355, margin={"l": 55, "r": 30, "t": 20, "b": 55}, showlegend=False,
        )
        st.plotly_chart(fig_l, use_container_width=True)

    with col_side:
        st.markdown('<div class="section-header"><span class="eyebrow">Generation breakdown</span><h2 style="font-size:1.1rem;">Grid Mix</h2></div>', unsafe_allow_html=True)
        _last = _raw.iloc[-1]
        _vals = [safe_float(_last.get(c, 0)) for c in ["thermal_mw", "hydro_mw", "nuclear_mw", "res_mw"]]
        _tot  = sum(_vals) or 1
        fig_p = go.Figure(data=[go.Pie(
            labels=["Thermal", "Hydro", "Nuclear", "Renewable"], values=_vals,
            hole=0.62,
            marker_colors=["#ff5a5a", "#818cf8", "#a78bfa", "#00e5a0"],
            textinfo="label+percent",
            textfont={"color": "white", "size": 9},
        )])
        fig_p.update_layout(
            paper_bgcolor="#0a1320", height=270,
            showlegend=False, margin={"t": 10, "b": 10, "l": 10, "r": 10},
            annotations=[{"text": f"<b>{_tot/1000:.1f}</b><br><span style='font-size:11px'>GW</span>",
                           "x": 0.5, "y": 0.5, "font": {"size": 17, "color": "#f0f6ff"}, "showarrow": False}],
        )
        st.plotly_chart(fig_p, use_container_width=True)

        st.markdown('<div class="section-header" style="margin-top:1rem;"><span class="eyebrow">Environmental impact</span><h2 style="font-size:1.1rem;">CO₂ Equivalents</h2></div>', unsafe_allow_html=True)
        _co2_now = co2_kg(_nrg, _cur_ci)
        _co2_opt = co2_kg(_nrg, _best["avg_ci"]) if _best else _co2_now
        _saved   = max(0.0, _co2_now - _co2_opt)
        rows_html = ""
        for lbl, val in co2_equivalents(_saved).items():
            rows_html += f"""
<div class="info-row">
  <span class="info-row-label">{lbl}</span>
  <span class="info-row-val" style="color:#00e5a0;">{val:.2f}</span>
</div>"""
        st.markdown(f"""
<div class="info-box">
  <div style="color:#566880;font-size:.7rem;margin-bottom:8px;">
    Running now: <strong style="color:#ff5a5a;">{_co2_now:.3f} kg CO₂</strong>
    &nbsp;→&nbsp; Optimal: <strong style="color:#00e5a0;">{_co2_opt:.3f} kg CO₂</strong>
  </div>
  {rows_html}
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════
# TAB 2 — Smart Advisor
# ══════════════════════════════════════════════
with T2:
    st.markdown(f"""
<div class="section-header">
  <span class="eyebrow">AI scheduling engine</span>
  <h2>🎯 Optimal Windows · {_now_12} → {_dl_12}</h2>
  <p>Top {_top_k} lowest-carbon time slots for your {st.session_state.appliance}</p>
</div>
""", unsafe_allow_html=True)

    if not _windows:
        st.warning("⚠️ No valid windows found — extend the deadline or reduce duration.")
        st.info(
            f"Debug: now={_now_12} · deadline={_dl_12} · "
            f"duration={ceil15(st.session_state.duration)} min · available={_avail} min"
        )
    else:
        # Build all cards in a single HTML block — CSS grid handles layout
        cards_html = '<div class="rec-grid">'
        for _gi, _w in enumerate(_windows):
            _is_best = _gi == 0
            _rank    = _gi + 1
            _co2w = co2_kg(_nrg, _w["avg_ci"])
            _co2n = co2_kg(_nrg, _cur_ci)
            _sav  = max(0.0, _co2n - _co2w)
            _ppct = (_sav / _co2n * 100) if _co2n > 0 else 0
            _conf = max(60, min(99, 100 - int((_w["max_ci"] - _w["min_ci"]) / 5)))
            _eq_t = _sav / CO2_EQUIV["🌳 Tree-days"]
            _eq_k = _sav / CO2_EQUIV["🚗 km not driven"]
            cards_html += f"""
<div class="rec-card {'best' if _is_best else ''}">
  <div class="rec-rank">
    <span class="rank-badge {'rank-best' if _is_best else 'rank-n'}">
      {'🥇 Best' if _rank == 1 else f'#{_rank}'}
    </span>
    <span class="conf-tag">{_conf}% conf</span>
  </div>
  <div class="rec-time">{to_12h(_w["start"])} — {to_12h(_w["end"])}</div>
  <div class="rec-stats">
    <div class="rec-stat">
      <div class="rec-stat-lbl">Avg CI</div>
      <div class="rec-stat-val" style="color:{_w['color']};">{_w['avg_ci']:.0f}</div>
    </div>
    <div class="rec-stat">
      <div class="rec-stat-lbl">CO₂</div>
      <div class="rec-stat-val">{_co2w:.3f} kg</div>
    </div>
  </div>
  <div class="rec-saving">💰 Save {_sav:.3f} kg ({_ppct:.0f}%)</div>
  <div class="rec-equiv">≈ {_eq_t:.1f} tree-days · {_eq_k:.1f} km not driven</div>
</div>"""
        cards_html += '</div>'
        st.markdown(cards_html, unsafe_allow_html=True)

        # Select buttons in Streamlit columns (one per window)
        _btn_cols = st.columns(min(len(_windows), 5))
        for _gi, (_col, _w) in enumerate(zip(_btn_cols, _windows[:5])):
            with _col:
                if st.button(f"✅ #{_gi+1}", key=f"sel_{_gi}", use_container_width=True):
                    st.session_state.sel_window = _gi
                    st.success(f"Selected: {to_12h(_w['start'])} – {to_12h(_w['end'])}")

    st.markdown('<div class="section-header" style="margin-top:1.5rem;"><span class="eyebrow">Visual overview</span><h2>🗓️ Today\'s CI Heatmap</h2></div>', unsafe_allow_html=True)
    _hdata = _full["ci"].values.reshape(6, 16) if len(_full) == 96 else np.tile(_full["ci"].values[:1], (6, 16))
    fig_h = go.Figure(data=go.Heatmap(
        z=_hdata,
        colorscale=[[0, "#00e5a0"], [0.4, "#f5a623"], [1, "#ff5a5a"]],
        showscale=True,
        colorbar={"title": "gCO₂/kWh", "tickfont": {"color": "#566880"}, "titlefont": {"color": "#566880"}},
        hovertemplate="CI: %{z:.1f} gCO₂/kWh<extra></extra>",
    ))
    fig_h.update_layout(
        paper_bgcolor="#0a1320", height=185,
        margin={"l": 15, "r": 15, "t": 10, "b": 10},
        xaxis={"showticklabels": False}, yaxis={"showticklabels": False},
    )
    st.plotly_chart(fig_h, use_container_width=True)


# ══════════════════════════════════════════════
# TAB 3 — Weekly Forecast
# ══════════════════════════════════════════════
with T3:
    st.markdown("""
<div class="section-header">
  <span class="eyebrow">7-day outlook</span>
  <h2>🌤️ Weekly Grid CI Forecast</h2>
  <p>Simulated forecast based on day-of-week demand patterns and current grid conditions</p>
</div>
""", unsafe_allow_html=True)

    fig_w = go.Figure()
    fig_w.add_trace(go.Bar(name="Night", x=_weekly["date"], y=_weekly["ci_night"],
                           marker_color="#00e5a0", opacity=0.82))
    fig_w.add_trace(go.Bar(name="Day",   x=_weekly["date"], y=_weekly["ci_day"],
                           marker_color="#818cf8", opacity=0.82))
    fig_w.add_trace(go.Bar(name="Peak",  x=_weekly["date"], y=_weekly["ci_peak"],
                           marker_color="#ff5a5a", opacity=0.82))
    fig_w.add_trace(go.Scatter(
        name="Daily avg", x=_weekly["date"], y=_weekly["avg"],
        mode="lines+markers", line={"color": "#f5a623", "width": 2, "dash": "dot"},
        marker={"size": 7, "color": "#f5a623"},
    ))
    fig_w.update_layout(
        barmode="group", plot_bgcolor="#0a1320", paper_bgcolor="#0a1320",
        font={"color": "#94a3b8"}, height=360,
        legend={"orientation": "h", "y": 1.04, "x": 0.5, "xanchor": "center",
                "bgcolor": "rgba(0,0,0,0)"},
        xaxis={"gridcolor": "#0d1b2e"},
        yaxis={"gridcolor": "#0d1b2e", "title": "gCO₂/kWh"},
        margin={"l": 50, "r": 30, "t": 45, "b": 40},
    )
    st.plotly_chart(fig_w, use_container_width=True)

    st.markdown('<div class="section-header"><span class="eyebrow">Daily outlook</span><h2>📅 Day-by-Day Breakdown</h2></div>', unsafe_allow_html=True)
    day_cards = '<div class="week-grid">'
    for _di, _dr in enumerate(_weekly.itertuples()):
        _best_p = (
            "Night" if _dr.ci_night <= _dr.ci_day and _dr.ci_night <= _dr.ci_peak
            else "Day" if _dr.ci_day <= _dr.ci_peak
            else "Peak"
        )
        _icon_d = "🌙" if _best_p == "Night" else "☀️" if _best_p == "Day" else "⚡"
        day_cards += f"""
<div class="day-card {'today' if _di == 0 else ''}">
  <div class="day-label">{_dr.label}</div>
  <div class="day-icon">{_icon_d}</div>
  <div class="day-ci" style="color:{ci_color(_dr.avg)};">{_dr.avg:.0f}</div>
  <div class="day-unit">gCO₂/kWh</div>
  <div class="day-best">Best: {_best_p}</div>
</div>"""
    day_cards += '</div>'
    st.markdown(day_cards, unsafe_allow_html=True)

    st.markdown('<div class="section-header"><span class="eyebrow">Global comparison</span><h2>🗺️ Regional CI Comparison</h2></div>', unsafe_allow_html=True)
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
        textfont={"color": "#566880", "size": 10},
    ))
    fig_reg.update_layout(
        plot_bgcolor="#0a1320", paper_bgcolor="#0a1320", font={"color": "#94a3b8"},
        height=555, margin={"l": 185, "r": 60, "t": 20, "b": 40},
        xaxis={"gridcolor": "#0d1b2e", "title": "Avg CI (gCO₂/kWh)"},
        yaxis={"gridcolor": "#0d1b2e"},
    )
    st.plotly_chart(fig_reg, use_container_width=True)


# ══════════════════════════════════════════════
# TAB 4 — Analytics
# ══════════════════════════════════════════════
with T4:
    st.markdown("""
<div class="section-header">
  <span class="eyebrow">Emissions analysis</span>
  <h2>📊 Baseline vs Optimised</h2>
</div>
""", unsafe_allow_html=True)

    _co2_now = co2_kg(_nrg, _cur_ci)
    _co2_opt = co2_kg(_nrg, _best["avg_ci"]) if _best else _co2_now
    _proj    = max(0.0, _co2_now - _co2_opt)
    _proj_p  = (_proj / _co2_now * 100) if _co2_now > 0 else 0

    _ac, _bc = st.columns([3, 2], gap="large")
    with _ac:
        fig_c = go.Figure()
        fig_c.add_trace(go.Bar(
            name="Baseline (Now)", x=["CO₂ Emissions"], y=[_co2_now],
            marker_color="#ff5a5a", text=[f"{_co2_now:.3f} kg"],
            textposition="outside", width=0.26,
        ))
        fig_c.add_trace(go.Bar(
            name="Optimised (Best)", x=["CO₂ Emissions"], y=[_co2_opt],
            marker_color="#00e5a0", text=[f"{_co2_opt:.3f} kg"],
            textposition="outside", width=0.26,
        ))
        fig_c.update_layout(
            barmode="group", plot_bgcolor="#0a1320", paper_bgcolor="#0a1320",
            font={"color": "#94a3b8"}, height=300,
            legend={"orientation": "h", "y": 1.04, "x": 0.5, "xanchor": "center",
                    "bgcolor": "rgba(0,0,0,0)"},
            yaxis={"title": "CO₂ (kg)", "gridcolor": "#0d1b2e",
                   "range": [0, max(_co2_now, _co2_opt) * 1.5]},
            margin={"l": 50, "r": 30, "t": 45, "b": 40},
            title={"text": f"💰 Potential saving: {_proj:.3f} kg CO₂ ({_proj_p:.1f}%)",
                   "font": {"size": 13, "color": "#00e5a0"}},
        )
        st.plotly_chart(fig_c, use_container_width=True)

        _m1, _m2, _m3, _m4 = st.columns(4)
        _m1.metric("🔴 Baseline",   f"{_co2_now:.3f} kg")
        _m2.metric("🟢 Optimised",  f"{_co2_opt:.3f} kg")
        _m3.metric("💰 CO₂ Saved",  f"{_proj:.3f} kg",   delta=f"-{_proj_p:.1f}%", delta_color="inverse")
        _m4.metric("⚡ Energy",     f"{_nrg:.3f} kWh")

    with _bc:
        st.markdown('<div class="section-header" style="margin-top:0;"><span class="eyebrow">Budget tracker</span><h2 style="font-size:1.1rem;">Daily CO₂ Budget</h2></div>', unsafe_allow_html=True)
        _today_str = datetime.now().strftime("%Y-%m-%d")
        _today_co2 = 0.0
        if not _log_df.empty and "date" in _log_df and "co2_kg" in _log_df:
            _today_co2 = float(_log_df[_log_df["date"].astype(str) == _today_str]["co2_kg"].sum())
        _budget    = st.session_state.daily_budget_kg
        _pct_used  = min(100.0, _today_co2 / max(_budget, 1e-6) * 100)
        _remaining = max(0.0, _budget - _today_co2)
        _bar_clr   = "#00e5a0" if _pct_used < 60 else "#f5a623" if _pct_used < 90 else "#ff5a5a"
        st.markdown(f"""
<div class="info-box">
  <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
    <span style="color:#566880;font-size:.77rem;">Daily Budget</span>
    <span style="color:#f0f6ff;font-size:.8rem;font-weight:700;">{_budget:.1f} kg CO₂</span>
  </div>
  <div class="pb"><div class="pb-fill" style="width:{_pct_used:.0f}%;background:{_bar_clr};"></div></div>
  <div style="display:flex;justify-content:space-between;margin-top:6px;">
    <span style="color:{_bar_clr};font-size:.74rem;font-weight:700;">Used {_today_co2:.3f} kg</span>
    <span style="color:#3d5166;font-size:.74rem;">Left {_remaining:.3f} kg</span>
  </div>
  <div style="margin-top:12px;padding-top:10px;border-top:1px solid #111f34;">
    <div style="color:#3d5166;font-size:.67rem;margin-bottom:5px;text-transform:uppercase;letter-spacing:.07em;">This Session</div>
    <div style="display:flex;justify-content:space-between;">
      <span style="color:#ff5a5a;font-size:.75rem;">Now {_co2_now:.3f} kg</span>
      <span style="color:#00e5a0;font-size:.75rem;">Optimal {_co2_opt:.3f} kg</span>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

        st.markdown('<div style="margin-top:1rem;"></div>', unsafe_allow_html=True)
        st.markdown('<div class="section-header" style="margin-top:.5rem;"><span class="eyebrow">Equivalents</span><h2 style="font-size:1.05rem;">🌍 {:.3f} kg Savings Equals…</h2></div>'.format(_proj), unsafe_allow_html=True)
        eq_rows = ""
        for lbl, val in co2_equivalents(_proj).items():
            eq_rows += f'<div class="info-row"><span class="info-row-label">{lbl}</span><span class="info-row-val" style="color:#00e5a0;">{val:.2f}</span></div>'
        st.markdown(f'<div class="info-box">{eq_rows}</div>', unsafe_allow_html=True)

    if not _log_df.empty and "timestamp" in _log_df and "co2_kg" in _log_df:
        st.markdown('<div class="section-header"><span class="eyebrow">History</span><h2>📋 Cumulative CO₂ Over Time</h2></div>', unsafe_allow_html=True)
        _ldf = _log_df.copy()
        _ldf["ts"] = pd.to_datetime(_ldf["timestamp"], errors="coerce")
        _ldf = _ldf.dropna(subset=["ts"]).sort_values("ts")
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(
            x=_ldf["ts"], y=_ldf["co2_kg"].cumsum(),
            mode="lines+markers", line={"color": "#00e5a0", "width": 2},
            fill="tozeroy", fillcolor="rgba(0,229,160,.05)",
        ))
        fig_hist.update_layout(
            plot_bgcolor="#0a1320", paper_bgcolor="#0a1320", font={"color": "#94a3b8"},
            height=240, margin={"l": 50, "r": 30, "t": 20, "b": 40},
            xaxis={"gridcolor": "#0d1b2e"},
            yaxis={"gridcolor": "#0d1b2e", "title": "kg CO₂ (cumulative)"},
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
    st.markdown("""
<div class="section-header">
  <span class="eyebrow">Gamification hub</span>
  <h2>🏅 Achievements & Eco-Milestones</h2>
</div>
""", unsafe_allow_html=True)

    # Stats row
    st.markdown(f"""
<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr);">
  <div class="kpi">
    <div class="kpi-lbl">Total Runs</div>
    <div class="kpi-val">{_stats["runs"]}</div>
    <div class="kpi-sub">{_stats["rec"]} recommended</div>
  </div>
  <div class="kpi" style="border-left-color:#ff5a5a;">
    <div class="kpi-lbl">CO₂ Tracked</div>
    <div class="kpi-val" style="color:#ff5a5a;">{_stats["co2"]:.3f}</div>
    <div class="kpi-sub">kg CO₂ total</div>
  </div>
  <div class="kpi" style="border-left-color:#f5a623;">
    <div class="kpi-lbl">🔥 Streak</div>
    <div class="kpi-val" style="color:#f5a623;">{_stats["streak"]}</div>
    <div class="kpi-sub">consecutive days</div>
  </div>
  <div class="kpi" style="border-left-color:#a78bfa;">
    <div class="kpi-lbl">⚡ Energy Used</div>
    <div class="kpi-val" style="color:#a78bfa;">{_stats["kwh"]:.2f}</div>
    <div class="kpi-sub">kWh total</div>
  </div>
</div>
""", unsafe_allow_html=True)

    st.markdown('<div class="section-header"><span class="eyebrow">Progress</span><h2>🏆 Badges</h2></div>', unsafe_allow_html=True)
    badge_html = '<div class="badge-grid">'
    for _bdg in BADGES:
        _earned = _bdg["id"] in _stats["badges"]
        badge_html += f"""
<div class="badge-card {'earned' if _earned else ''}" style="opacity:{'1' if _earned else '0.4'};">
  <div class="badge-icon">{_bdg['icon']}</div>
  <div class="badge-name" style="color:{'#00e5a0' if _earned else '#566880'};">{_bdg['name']}</div>
  <div class="badge-desc">{_bdg['desc']}</div>
  <div class="badge-status" style="color:{'#00e5a0' if _earned else '#3d5166'};">
    {'✓ EARNED' if _earned else 'LOCKED'}
  </div>
</div>"""
    badge_html += '</div>'
    st.markdown(badge_html, unsafe_allow_html=True)

    st.markdown('<div class="section-header"><span class="eyebrow">Your footprint</span><h2>📈 Cumulative Impact</h2></div>', unsafe_allow_html=True)
    st.markdown(f"""
<div class="impact-grid">
  <div class="impact-card">
    <div class="impact-icon">🌳</div>
    <div class="impact-val" style="color:#00e5a0;">{_stats["co2"] / CO2_EQUIV["🌳 Tree-days"]:.1f}</div>
    <div class="impact-label">tree-days of CO₂ absorption offset</div>
  </div>
  <div class="impact-card">
    <div class="impact-icon">🚗</div>
    <div class="impact-val" style="color:#818cf8;">{_stats["co2"] / CO2_EQUIV["🚗 km not driven"]:.1f}</div>
    <div class="impact-label">km of driving avoided in total</div>
  </div>
  <div class="impact-card">
    <div class="impact-icon">📱</div>
    <div class="impact-val" style="color:#a78bfa;">{_stats["co2"] / CO2_EQUIV["📱 Phone charges"]:.0f}</div>
    <div class="impact-label">phone charges equivalent saved</div>
  </div>
</div>
""", unsafe_allow_html=True)

    st.markdown('<div class="section-header"><span class="eyebrow">Recommendations</span><h2>💡 Smart Tips</h2></div>', unsafe_allow_html=True)
    _min_t = _full.loc[_full["ci"].idxmin(), "time"]
    _max_t = _full.loc[_full["ci"].idxmax(), "time"]
    _avg_c = float(_full["ci"].mean())
    for _tip, _clr in [
        (f"🌙 Best time today is <strong>{to_12h(_min_t)}</strong> — grid is cleanest then.", "#00e5a0"),
        (f"🔥 Avoid <strong>{to_12h(_max_t)}</strong> — the dirtiest grid window of the day.", "#ff5a5a"),
        (f"📊 Today's average CI: <strong>{_avg_c:.0f} gCO₂/kWh</strong> — "
         f"{'below' if _avg_c < 300 else 'above'} the 300 g benchmark.", "#f5a623"),
        ("🔄 Enable Live Mode in the sidebar to keep data fresh automatically.", "#818cf8"),
        ("🏅 Log your runs to build your streak and unlock badges!", "#a78bfa"),
    ]:
        st.markdown(
            f'<div class="tip-card" style="background:#0d1b2e;border-left-color:{_clr};color:#6b8099;">{_tip}</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════
# TAB 6 — Logger
# ══════════════════════════════════════════════
with T6:
    st.markdown(f"""
<div class="section-header">
  <span class="eyebrow">Manual entry</span>
  <h2>📝 Log an Appliance Run</h2>
  <p>System time: {exact_time_str(st.session_state.tz)} ({st.session_state.tz})</p>
</div>
""", unsafe_allow_html=True)

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

        st.markdown("##### ⚡ Meter Readings")
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
    st.markdown("""
<div class="section-header">
  <span class="eyebrow">Developer view</span>
  <h2>🔍 Raw Grid Data & Configuration</h2>
</div>
""", unsafe_allow_html=True)

    with st.expander("📍 Current Configuration", expanded=False):
        st.json({
            "Mode":              st.session_state.loc_mode,
            "Latitude":          st.session_state.lat,
            "Longitude":         st.session_state.lon,
            "Timezone":          st.session_state.tz,
            "Data Source":       st.session_state.data_source,
            "Live Mode":         st.session_state.live_mode,
            "Refresh (s)":       st.session_state.refresh_s,
            "Alert Enabled":     st.session_state.alert_enabled,
            "Alert Threshold":   st.session_state.alert_thresh,
            "Daily Budget (kg)": st.session_state.daily_budget_kg,
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
    st.markdown("""
<div class="section-header">
  <span class="eyebrow">Architecture notes</span>
  <h2>ℹ️ Simulation Engine</h2>
</div>
""", unsafe_allow_html=True)
    st.markdown(f"""
<div class="info-box" style="font-size:.82rem;color:#566880;line-height:1.75;">
  <strong style="color:#00e5a0;">DataEngine.fetch_live()</strong> first attempts the
  Electricity Maps free-tier API. On failure it falls back to
  <code style="color:#818cf8;">_simulate_profile()</code>, a time-seeded NumPy simulation
  that reproduces real demand curves (night troughs, morning/evening peaks, midday solar suppression).
  <br><br>
  The <strong style="color:#00e5a0;">refresh seed</strong> rotates every
  <strong style="color:#f0f6ff;">{st.session_state.refresh_s} s</strong>, so data evolves naturally
  between pages. All 96 fifteen-minute slots are computed in one pass — no gaps, no duplicate slots.
</div>
""", unsafe_allow_html=True)
