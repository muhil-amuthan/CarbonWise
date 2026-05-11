import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import json
import requests
import pytz
import time
import math
import random
from typing import Optional, Dict, List
import streamlit.components.v1 as components

# ============================================================
# CI import with fallback
# ============================================================
try:
    from src.ci import compute_ci_g_per_kwh
except ImportError:
    def compute_ci_g_per_kwh(thermal, hydro, nuclear, res):
        thermal = float(thermal); hydro = float(hydro)
        nuclear = float(nuclear); res = float(res)
        total = thermal + hydro + nuclear + res
        if total == 0:
            return 400.0
        return (thermal * 800 + hydro * 24 + nuclear * 12 + res * 50) / total

# ============================================================
# Grid Zone Configuration
# ============================================================
GRID_ZONES = {
    "India": {
        "zones": {
            "North India (NR)":  {"lat": 28.6139, "lon": 77.2090,  "ci_avg": 450},
            "South India (SR)":  {"lat": 13.0827, "lon": 80.2707,  "ci_avg": 380},
            "West India (WR)":   {"lat": 19.0760, "lon": 72.8777,  "ci_avg": 420},
            "East India (ER)":   {"lat": 22.5726, "lon": 88.3639,  "ci_avg": 480},
            "North-East (NER)":  {"lat": 26.1445, "lon": 91.7362,  "ci_avg": 350},
        },
        "timezone": "Asia/Kolkata",
        "voltage": "230V/50Hz"
    },
    "Europe": {
        "zones": {
            "Germany (DE)":       {"lat": 51.1657, "lon": 10.4515,  "ci_avg": 276},
            "France (FR)":        {"lat": 46.2276, "lon":  2.2137,  "ci_avg":  16},
            "UK (GB)":            {"lat": 55.3781, "lon": -3.4360,  "ci_avg": 106},
            "Netherlands (NL)":   {"lat": 52.1326, "lon":  5.2913,  "ci_avg": 209},
            "Spain (ES)":         {"lat": 40.4637, "lon": -3.7492,  "ci_avg":  89},
            "Italy (IT)":         {"lat": 41.8719, "lon": 12.5674,  "ci_avg": 202},
            "Nordics (NO/SE/FI)": {"lat": 60.4720, "lon":  8.4689,  "ci_avg":  30},
        },
        "timezone": "Europe/Brussels",
        "voltage": "230V/50Hz"
    },
    "United States": {
        "zones": {
            "California (CAISO)": {"lat": 36.7783, "lon":-119.4179, "ci_avg": 200},
            "Texas (ERCOT)":      {"lat": 31.9686, "lon": -99.9018, "ci_avg": 350},
            "New York (NYISO)":   {"lat": 42.1657, "lon": -74.9481, "ci_avg": 250},
            "Midwest (MISO)":     {"lat": 41.8780, "lon": -93.0977, "ci_avg": 400},
            "PJM (East)":         {"lat": 39.8283, "lon": -77.5794, "ci_avg": 320},
            "Pacific Northwest":  {"lat": 45.5152, "lon":-122.6784, "ci_avg": 100},
        },
        "timezone": "America/New_York",
        "voltage": "120V/60Hz"
    },
    "Asia Pacific": {
        "zones": {
            "Australia (AUS)":   {"lat":-25.2744, "lon": 133.7751,  "ci_avg": 450},
            "Japan (JP)":        {"lat": 36.2048, "lon": 138.2529,  "ci_avg": 450},
            "Singapore (SG)":    {"lat":  1.3521, "lon": 103.8198,  "ci_avg": 367},
            "South Korea (KR)":  {"lat": 35.9078, "lon": 127.7669,  "ci_avg": 357},
        },
        "timezone": "Asia/Tokyo",
        "voltage": "100V-240V/50-60Hz"
    }
}

ELECTRICITY_MAPS_API = "https://api-access.electricitymaps.com/free-tier/"
LOG_FILE    = Path("logs/runs.jsonl")
CONFIG_FILE = Path("config/location.json")
DATA_DIR    = Path("data")
STEP_MIN    = 15

APPLIANCES = {
    "Geyser / Water Heater": {"kw": 2.0,  "duration_min":  30, "deadline": "11:00", "icon": "🚿"},
    "Washing Machine":        {"kw": 0.5,  "duration_min":  60, "deadline": "11:00", "icon": "👗"},
    "Iron Box":               {"kw": 1.0,  "duration_min":  30, "deadline": "11:00", "icon": "👔"},
    "Water Motor":            {"kw": 0.75, "duration_min":  30, "deadline": "08:00", "icon": "💧"},
    "EV Charger (3.3 kW)":   {"kw": 3.3,  "duration_min": 120, "deadline": "07:00", "icon": "⚡"},
    "EV Charger (7.0 kW)":   {"kw": 7.0,  "duration_min": 120, "deadline": "07:00", "icon": "🚗"},
    "Air Conditioner":        {"kw": 1.5,  "duration_min":  60, "deadline": "23:00", "icon": "❄️"},
    "Induction Cooktop":      {"kw": 1.8,  "duration_min":  30, "deadline": "21:00", "icon": "🍳"},
    "Microwave":              {"kw": 1.2,  "duration_min":  15, "deadline": "21:00", "icon": "📡"},
    "Laptop Charging":        {"kw": 0.06, "duration_min": 120, "deadline": "23:00", "icon": "💻"},
    "Custom":                 {"kw": 1.0,  "duration_min":  30, "deadline": "11:00", "icon": "🔌"},
}

# CO2 equivalents (kg CO2 per unit)
CO2_EQUIV = {
    "🌳 Trees (1 year)":    0.021,   # kg CO2 absorbed per tree per day ≈ 7.7 kg/year
    "🚗 Driving (km)":      0.192,   # kg CO2 per km average car
    "✈️ Flight (km)":       0.255,   # kg CO2 per passenger-km
    "🍔 Beef meals":        6.61,    # kg CO2 per 100g beef serving (3kg per 100g)
    "📱 Phone charges":     0.00844, # kg CO2 per full phone charge
}

# Badges
BADGES = [
    {"id": "first_save",    "name": "First Save",       "icon": "🌱", "desc": "Logged your first run",           "threshold": 1},
    {"id": "green_week",    "name": "Green Week",        "icon": "🌿", "desc": "7-day scheduling streak",         "threshold": 7},
    {"id": "ton_saver",     "name": "Ton Saver",         "icon": "🏆", "desc": "Saved 1 kg CO₂ total",           "threshold": 1.0},
    {"id": "optimizer",     "name": "Grid Optimizer",    "icon": "⚡", "desc": "10 recommended runs logged",     "threshold": 10},
    {"id": "eco_champion",  "name": "Eco Champion",      "icon": "🌍", "desc": "Saved 10 kg CO₂ total",          "threshold": 10.0},
    {"id": "night_owl",     "name": "Night Owl",         "icon": "🦉", "desc": "Scheduled during low-CI window", "threshold": 1},
    {"id": "solar_surfer",  "name": "Solar Surfer",      "icon": "☀️", "desc": "Ran appliance at solar peak",    "threshold": 1},
    {"id": "early_bird",    "name": "Early Bird",        "icon": "🐦", "desc": "Scheduled before 6 AM",          "threshold": 1},
]

# ============================================================
# Utilities
# ============================================================
def ensure_dirs():
    Path("logs").mkdir(exist_ok=True)
    Path("config").mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("", encoding="utf-8")

def safe_float(v, default=0.0):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return default
        return float(v)
    except:
        return default

def ceil_to_step(mins, step=15):
    mins = int(mins)
    return mins if mins % step == 0 else mins + (step - mins % step)

def get_ci_color(ci):
    ci = safe_float(ci, 500)
    return "#4ade80" if ci < 200 else "#fbbf24" if ci < 400 else "#f87171"

def get_ci_status(ci):
    ci = safe_float(ci, 500)
    return "🟢 Low" if ci < 200 else "🟡 Medium" if ci < 400 else "🔴 High"

def get_ci_status_plain(ci):
    ci = safe_float(ci, 500)
    return "Low" if ci < 200 else "Medium" if ci < 400 else "High"

def calculate_co2(kwh, ci):
    return (safe_float(kwh, 0) * safe_float(ci, 0)) / 1000.0

def co2_equivalents(co2_kg):
    return {k: co2_kg / v for k, v in CO2_EQUIV.items()}

def validate_time_format(t):
    try:
        h, m = map(int, t.split(":")); return 0 <= h < 24 and 0 <= m < 60
    except: return False

def time_to_minutes(t):
    try:
        h, m = map(int, t.split(":"))
        return h * 60 + m
    except:
        return 0

def minutes_to_time(mins):
    return f"{(mins // 60) % 24:02d}:{mins % 60:02d}"

def to_12hr(time_str):
    try:
        h, m = map(int, time_str.split(":"))
        period = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d} {period}"
    except: return time_str

def get_system_time(tz_str="UTC"):
    try: return datetime.now(pytz.timezone(tz_str))
    except: return datetime.now()

def get_current_time_24(tz_str="UTC", step=15):
    now = get_system_time(tz_str)
    m   = (now.minute // step) * step
    return f"{now.hour:02d}:{m:02d}"

def get_current_time_exact_12(tz_str="UTC"):
    now = get_system_time(tz_str)
    h, m, s = now.hour, now.minute, now.second
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}:{s:02d} {period}"

def append_log(row):
    ensure_dirs()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")

def save_location_config(cfg):
    ensure_dirs()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

@st.cache_data(ttl=300)
def read_log():
    ensure_dirs()
    rows = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try: rows.append(json.loads(line))
                except: pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def detect_location_ip():
    try:
        r = requests.get("https://ipapi.co/json/", timeout=5)
        if r.status_code == 200:
            d = r.json()
            return {
                "lat": d.get("latitude"), "lon": d.get("longitude"),
                "city": d.get("city"), "country": d.get("country_name"),
                "timezone": d.get("timezone")
            }
    except: pass
    return None

# ============================================================
# ★ ENHANCED: Real-time CI simulation with noise
# ============================================================
def generate_realtime_ci_profile(base_ci: float, refresh_seed: int = None) -> pd.DataFrame:
    """
    Generates a realistic 24-hour CI profile that changes every refresh cycle.
    Uses a time-seeded RNG so data evolves naturally between refreshes.
    """
    if refresh_seed is None:
        refresh_seed = int(time.time() // 300)  # Changes every 5 min
    rng = np.random.default_rng(refresh_seed)

    # Typical daily demand curve factors
    hour_factors = {
        0: 0.62, 1: 0.58, 2: 0.55, 3: 0.53, 4: 0.54, 5: 0.62,
        6: 0.78, 7: 0.92, 8: 1.08, 9: 1.15, 10: 1.10, 11: 1.05,
        12: 1.00, 13: 0.97, 14: 0.93, 15: 0.94, 16: 1.02, 17: 1.18,
        18: 1.25, 19: 1.22, 20: 1.15, 21: 1.05, 22: 0.92, 23: 0.75
    }

    rows = []
    for h in range(24):
        for mi, m in enumerate([0, 15, 30, 45]):
            f = hour_factors[h]
            # Add smooth intra-hour variation + session noise
            intra = math.sin(mi * math.pi / 4) * 0.02
            noise = rng.normal(0, 0.04)
            ci = base_ci * max(0.3, f + intra + noise)
            ci = max(10.0, min(900.0, ci))

            thermal  = ci * 6.5 * max(0.4, f)
            hydro    = base_ci * 2.0 * (1 - f * 0.3)
            nuclear  = base_ci * 1.8
            res      = max(0, base_ci * 3.0 * (1 - f * 0.6) + (
                base_ci * 2.0 if 10 <= h <= 16 else 0))

            rows.append({
                "time": f"{h:02d}:{m:02d}",
                "ci": round(ci, 2),
                "thermal_mw":  round(thermal, 1),
                "hydro_mw":    round(hydro, 1),
                "nuclear_mw":  round(nuclear, 1),
                "res_mw":      round(res, 1),
            })
    return pd.DataFrame(rows)

def fetch_electricity_maps_data(lat, lon, api_token=None):
    try:
        headers = {"auth-token": api_token} if api_token else {}
        r = requests.get(
            f"{ELECTRICITY_MAPS_API}carbon-intensity/latest",
            headers=headers, params={"lat": lat, "lon": lon}, timeout=10
        )
        if r.status_code == 200:
            base_ci = r.json().get("carbonIntensity", 400)
            return generate_realtime_ci_profile(base_ci)
    except: pass
    return None

def make_full_day_ci(df_ci):
    ci_dict  = dict(zip(df_ci["time"], df_ci["ci"]))
    mean_ci  = safe_float(df_ci["ci"].mean(), 400)
    rows = []
    for h in range(24):
        for m in [0, 15, 30, 45]:
            t  = f"{h:02d}:{m:02d}"
            ci = safe_float(ci_dict.get(t, mean_ci), mean_ci)
            rows.append({"time": t, "ci": ci,
                         "color": get_ci_color(ci), "status": get_ci_status_plain(ci)})
    return pd.DataFrame(rows)

def recommend_windows(full_day_ci, duration_min, deadline, top_k, now_time):
    duration_min = ceil_to_step(duration_min, STEP_MIN)
    n_slots = duration_min // STEP_MIN
    try:
        dh, dm = map(int, deadline.split(":")); deadline_mins = dh * 60 + dm
    except: deadline_mins = 23 * 60 + 59
    try:
        nh, nm = map(int, now_time.split(":")); now_mins = nh * 60 + nm
    except: now_mins = 0
    if deadline_mins <= now_mins:
        deadline_mins += 1440
    times = full_day_ci["time"].tolist()
    cis   = full_day_ci["ci"].tolist()
    wins  = []
    for i in range(len(times) - n_slots + 1):
        sh, sm = map(int, times[i].split(":"))
        s_mins = sh * 60 + sm
        adj    = s_mins if s_mins >= now_mins else s_mins + 1440
        if adj < now_mins: continue
        if adj + duration_min > deadline_mins: continue
        em = adj + duration_min
        et = f"{(em // 60) % 24:02d}:{em % 60:02d}"
        wci = [safe_float(x, 500) for x in cis[i:i + n_slots]]
        avg = sum(wci) / len(wci)
        wins.append({
            "start": times[i], "end": et, "avg_ci": avg,
            "color": get_ci_color(avg), "status": get_ci_status_plain(avg),
            "min_ci": min(wci), "max_ci": max(wci)
        })
    wins.sort(key=lambda x: x["avg_ci"])
    return wins[:top_k]

def find_ci_at_time(full_day_ci, time_str):
    mask = full_day_ci["time"] == time_str
    return float(full_day_ci[mask]["ci"].iloc[0]) if mask.any() else None

# ============================================================
# ★ NEW: Weekly 7-day CI forecast
# ============================================================
def generate_weekly_forecast(base_ci: float) -> pd.DataFrame:
    """Simulate 7-day CI forecast with day-of-week patterns."""
    today  = datetime.now()
    dow_factors = {0: 1.05, 1: 1.08, 2: 1.06, 3: 1.04, 4: 1.03, 5: 0.88, 6: 0.82}
    rows = []
    rng  = np.random.default_rng(int(today.strftime("%Y%m%d")))
    for d in range(7):
        day    = today + timedelta(days=d)
        dow    = day.weekday()
        f      = dow_factors[dow]
        noise  = rng.normal(0, 0.06)
        ci_day = base_ci * max(0.5, f + noise)
        ci_night = base_ci * max(0.3, f * 0.65 + noise)
        ci_peak  = base_ci * max(0.7, f * 1.25 + noise)
        rows.append({
            "date":      day.strftime("%a %d %b"),
            "day_idx":   d,
            "ci_day":    round(ci_day, 1),
            "ci_night":  round(ci_night, 1),
            "ci_peak":   round(ci_peak, 1),
            "avg":       round((ci_day + ci_night + ci_peak) / 3, 1),
            "label":     "Today" if d == 0 else ("Tomorrow" if d == 1 else day.strftime("%a")),
        })
    return pd.DataFrame(rows)

# ============================================================
# ★ NEW: Gamification helpers
# ============================================================
def compute_stats(log_df: pd.DataFrame) -> dict:
    if log_df.empty:
        return {"total_runs": 0, "rec_runs": 0, "total_co2_saved": 0.0,
                "streak": 0, "badges": [], "total_kwh": 0.0}
    total_runs = len(log_df)
    rec_runs   = len(log_df[log_df.get("run_type", pd.Series()) == "recommended"]) if "run_type" in log_df else 0
    total_co2  = float(log_df["co2_kg"].sum()) if "co2_kg" in log_df else 0.0
    total_kwh  = float(log_df["kwh_used"].sum()) if "kwh_used" in log_df else 0.0

    # Streak (consecutive days with a log)
    streak = 0
    if "date" in log_df:
        dates = sorted(log_df["date"].unique(), reverse=True)
        check = datetime.now().date()
        for d in dates:
            try:
                dd = datetime.strptime(str(d), "%Y-%m-%d").date()
                if dd == check or dd == check - timedelta(days=1):
                    streak += 1
                    check = dd - timedelta(days=1)
                else: break
            except: continue

    # Badges earned
    earned = []
    if total_runs >= 1:         earned.append("first_save")
    if streak >= 7:             earned.append("green_week")
    if total_co2 >= 1.0:        earned.append("ton_saver")
    if rec_runs >= 10:          earned.append("optimizer")
    if total_co2 >= 10.0:       earned.append("eco_champion")
    return {
        "total_runs": total_runs, "rec_runs": rec_runs,
        "total_co2_saved": total_co2, "streak": streak,
        "badges": earned, "total_kwh": total_kwh
    }

# ============================================================
# ★ NEW: Alert system helpers
# ============================================================
def check_alerts(current_ci: float, alert_threshold: int) -> bool:
    return current_ci <= alert_threshold

# ============================================================
# Page config — MUST be first Streamlit call
# ============================================================
st.set_page_config(page_title="CarbonWise ⚡", page_icon="🌍", layout="wide")

# ============================================================
# Global CSS
# ============================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;700&display=swap');

* { font-family: 'Space Grotesk', sans-serif !important; box-sizing: border-box; }
code, .mono { font-family: 'JetBrains Mono', monospace !important; }

.main, [data-testid="stAppViewContainer"] { background-color: #080d16; }
[data-testid="stHeader"]                  { background-color: #080d16; }
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapseButton"],
button[kind="header"] { display: none !important; }

/* Hero */
.hero {
    background: linear-gradient(135deg, #0b1628 0%, #0f2744 50%, #0b1a30 100%);
    padding: 2rem 2.5rem; border-radius: 20px; margin-bottom: 1.5rem;
    border: 1px solid rgba(74,222,128,0.2);
    position: relative; overflow: hidden;
}
.hero::before {
    content: '';
    position: absolute; top: -40%; right: -10%; width: 400px; height: 400px;
    background: radial-gradient(circle, rgba(74,222,128,0.08) 0%, transparent 70%);
    border-radius: 50%; pointer-events: none;
}
.hero h1 { color: #4ade80; margin: 0; font-size: 2.4rem; font-weight: 700; letter-spacing: -0.5px; }
.hero p  { color: #94a3b8; margin: 0.4rem 0 0; font-size: 1rem; max-width: 600px; }

/* Sidebar */
[data-testid="stSidebar"] {
    background: #0a1120 !important;
    border-right: 1px solid rgba(74,222,128,0.12);
}

/* KPI cards */
.kpi {
    background: linear-gradient(135deg, #111b2e 0%, #0c1422 100%);
    padding: 1.2rem 1rem; border-radius: 14px; text-align: center;
    border-left: 4px solid #4ade80; height: 100%; min-height: 110px;
    display: flex; flex-direction: column; justify-content: center;
    transition: transform 0.2s, box-shadow 0.2s;
}
.kpi:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(74,222,128,0.1); }
.kpi .metric-label { color: #64748b; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; margin-bottom: 4px; }
.kpi .metric-value { color: white; font-weight: 700; line-height: 1.1; margin: 2px 0; font-size: 1.4rem; }
.kpi .metric-sub   { color: #475569; font-size: 0.7rem; margin-top: 3px; }

/* Rec cards */
.rec-card {
    background: linear-gradient(135deg, #111b2e 0%, #0c1422 100%);
    padding: 1.25rem; border-radius: 14px; margin-bottom: 0.5rem;
    transition: transform 0.2s;
}
.rec-card:hover { transform: translateY(-2px); }

/* Live time */
.live-time {
    background: rgba(74,222,128,0.07); border: 1px solid rgba(74,222,128,0.35);
    padding: 0.8rem; border-radius: 12px; color: #4ade80;
    font-weight: 700; text-align: center; font-size: 1.1rem; letter-spacing: 0.05em;
    font-family: 'JetBrains Mono', monospace !important;
}
.tz-sub { color: #475569; font-size: 0.72rem; text-align: center; margin-top: 4px; }

/* Location badge */
.loc-badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: linear-gradient(135deg, #1d4ed8, #2563eb);
    color: white; padding: 5px 12px; border-radius: 8px;
    font-size: 0.8rem; font-weight: 600;
    border: 1px solid rgba(99,179,237,0.25);
}

/* Alert banner */
.alert-green {
    background: rgba(74,222,128,0.1); border: 1px solid rgba(74,222,128,0.4);
    border-left: 4px solid #4ade80;
    padding: 0.8rem 1rem; border-radius: 10px; margin-bottom: 0.5rem;
}
.alert-red {
    background: rgba(248,113,113,0.1); border: 1px solid rgba(248,113,113,0.4);
    border-left: 4px solid #f87171;
    padding: 0.8rem 1rem; border-radius: 10px; margin-bottom: 0.5rem;
}
.alert-yellow {
    background: rgba(251,191,36,0.1); border: 1px solid rgba(251,191,36,0.4);
    border-left: 4px solid #fbbf24;
    padding: 0.8rem 1rem; border-radius: 10px; margin-bottom: 0.5rem;
}

/* Badge cards */
.badge-card {
    background: #111b2e; border-radius: 12px; padding: 14px 12px;
    text-align: center; border: 1px solid #1e2d45;
    transition: all 0.2s;
}
.badge-card.earned { border-color: rgba(74,222,128,0.4); background: rgba(74,222,128,0.05); }
.badge-card:hover  { transform: scale(1.04); }

/* Ticker */
.ticker {
    background: #111b2e; border: 1px solid rgba(74,222,128,0.2);
    border-radius: 10px; padding: 0.6rem 1.2rem;
    display: flex; align-items: center; gap: 12px; overflow: hidden;
}
.ticker-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #4ade80; animation: pulse 1.5s infinite;
    flex-shrink: 0;
}
@keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.5;transform:scale(1.3)} }

/* Gauge container */
.gauge-wrap { background: #111b2e; border-radius: 14px; padding: 1rem; border: 1px solid #1e2d45; }

/* Countdown pill */
.countdown {
    background: rgba(74,222,128,0.1); border: 1px solid rgba(74,222,128,0.3);
    border-radius: 999px; padding: 4px 14px; font-size: 0.8rem;
    color: #4ade80; font-weight: 600; display: inline-flex; align-items: center; gap: 6px;
}

/* Progress bar */
.prog-bar {
    height: 6px; border-radius: 999px; background: #1e293b; margin: 6px 0;
}
.prog-bar-fill {
    height: 100%; border-radius: 999px;
    background: linear-gradient(90deg, #4ade80, #22d3ee);
}

/* Comparison table row */
.cmp-row {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 0; border-bottom: 1px solid #1e293b;
}

hr { border-color: rgba(74,222,128,0.1) !important; margin: 1rem 0 !important; }
[data-testid="stDataFrame"] { width: 100% !important; }
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] { padding: 8px 16px; }
[data-testid="stDownloadButton"] > button,
.stButton > button { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# FAB — Sidebar toggle + Fullscreen + Auto-refresh indicator
# ============================================================
components.html("""
<!DOCTYPE html><html><head>
<style>
  html,body{margin:0;padding:0;overflow:hidden;background:transparent;}
  #fab{position:fixed;top:10px;right:14px;display:flex;gap:7px;z-index:2147483647;}
  .fb{
    width:38px;height:38px;
    background:linear-gradient(145deg,#111b2e,#080d16);
    border:1.5px solid rgba(74,222,128,0.5);border-radius:10px;cursor:pointer;
    display:flex;align-items:center;justify-content:center;
    transition:all .18s;box-shadow:0 3px 12px rgba(0,0,0,.55);padding:0;outline:none;
  }
  .fb:hover{background:rgba(74,222,128,0.15);border-color:#4ade80;transform:scale(1.08);}
  .fb:active{transform:scale(0.97);}
  svg{width:17px;height:17px;fill:none;stroke:#4ade80;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;pointer-events:none;}
</style></head><body>
<div id="fab">
  <button class="fb" id="btn-sb" title="Toggle Sidebar">
    <svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2.5"/><line x1="9" y1="3" x2="9" y2="21"/></svg>
  </button>
  <button class="fb" id="btn-fs" title="Fullscreen">
    <svg id="ic-exp" viewBox="0 0 24 24"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg>
    <svg id="ic-cmp" viewBox="0 0 24 24" style="display:none"><path d="M8 3v3a2 2 0 0 1-2 2H3"/><path d="M21 8h-3a2 2 0 0 1-2-2V3"/><path d="M3 16h3a2 2 0 0 1 2 2v3"/><path d="M16 21v-3a2 2 0 0 1 2-2h3"/></svg>
  </button>
</div>
<script>
(function(){
  var pd=window.parent.document;
  document.getElementById('btn-sb').onclick=function(){
    var b=pd.querySelector('[data-testid="collapsedControl"]')||pd.querySelector('[data-testid="stSidebarCollapseButton"]')||pd.querySelector('button[aria-label="Close sidebar"]')||pd.querySelector('button[aria-label="Open sidebar"]');
    if(b){b.click();return;}
    var sb=pd.querySelector('[data-testid="stSidebar"]');
    if(sb){sb.style.display=sb.style.display==='none'?'':'none';}
  };
  document.getElementById('btn-fs').onclick=function(){
    var exp=document.getElementById('ic-exp'),cmp=document.getElementById('ic-cmp');
    if(!pd.fullscreenElement){pd.documentElement.requestFullscreen().then(function(){exp.style.display='none';cmp.style.display='';}).catch(function(e){console.warn(e);});}
    else{pd.exitFullscreen().then(function(){exp.style.display='';cmp.style.display='none';});}
  };
  pd.addEventListener('fullscreenchange',function(){
    if(!pd.fullscreenElement){var exp=document.getElementById('ic-exp'),cmp=document.getElementById('ic-cmp');if(exp)exp.style.display='';if(cmp)cmp.style.display='none';}
  });
})();
</script></body></html>
""", height=0, scrolling=False)


# ============================================================
# Session state init
# ============================================================
_defaults = dict(
    appliance="Water Motor", kw=0.75, duration=30, deadline="08:00",
    selected_window=0, location_mode="Auto-Detect",
    selected_region="India", selected_zone="North India (NR)",
    lat=28.6139, lon=77.2090, timezone="Asia/Kolkata",
    data_source="Automatic (API)",
    live_mode=True, refresh_interval=300,
    alert_threshold=200, alert_enabled=True,
    daily_budget_kg=1.0,
    last_refresh_ts=0.0,
)
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ============================================================
# ★ AUTO-REFRESH: trigger page reload via JS timer
# ============================================================
if st.session_state.live_mode:
    ri = int(st.session_state.refresh_interval) * 1000
    components.html(f"""
<script>
(function(){{
  setTimeout(function(){{
    window.parent.location.reload();
  }}, {ri});
}})();
</script>
""", height=0, scrolling=False)

# ============================================================
# Hero
# ============================================================
_exact12 = get_current_time_exact_12(st.session_state.timezone)
live_badge = (
    '<span style="background:rgba(74,222,128,0.15);color:#4ade80;border:1px solid rgba(74,222,128,0.35);'
    'padding:4px 12px;border-radius:999px;font-size:0.78rem;font-weight:700;'
    'display:inline-flex;align-items:center;gap:6px;">'
    '<span style="width:7px;height:7px;border-radius:50%;background:#4ade80;'
    'animation:pulse 1.5s infinite;display:inline-block;"></span> LIVE</span>'
    if st.session_state.live_mode else ""
)

st.markdown(f"""
<div class="hero">
  <h1>🌍 CarbonWise</h1>
  <p>Location-aware carbon intensity optimization — schedule appliances during the
     cleanest grid windows to minimize your CO₂ footprint in real time.</p>
  <div style="margin-top:0.9rem;display:flex;flex-wrap:wrap;gap:8px;align-items:center;">
    {live_badge}
    <span style="background:rgba(59,130,246,0.12);color:#60a5fa;border:1px solid rgba(59,130,246,0.3);
                 padding:4px 12px;border-radius:999px;font-size:0.78rem;font-weight:600;">
      ⚡ Smart Scheduler
    </span>
    <span style="background:rgba(251,191,36,0.1);color:#fbbf24;border:1px solid rgba(251,191,36,0.25);
                 padding:4px 12px;border-radius:999px;font-size:0.78rem;font-weight:600;">
      🏅 Gamified
    </span>
    <span style="color:#475569;font-size:0.82rem;margin-left:4px;">
      🕐 {_exact12} &nbsp;·&nbsp; Powered by real-time grid simulation + Electricity Maps
    </span>
  </div>
</div>
""", unsafe_allow_html=True)


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown("## ⚙️ Configuration")

    # ── LIVE MODE ─────────────────────────────────────────────
    st.markdown("### 🔄 Live Mode")
    live_mode = st.toggle("Auto-refresh data", value=st.session_state.live_mode)
    st.session_state.live_mode = live_mode
    if live_mode:
        ri_opts = {60: "1 min", 120: "2 min", 300: "5 min", 600: "10 min"}
        ri_val  = st.select_slider(
            "Refresh every",
            options=list(ri_opts.keys()),
            value=st.session_state.refresh_interval,
            format_func=lambda x: ri_opts[x]
        )
        st.session_state.refresh_interval = ri_val
        st.markdown(f"""
        <div style="background:#0f2040;border:1px solid rgba(74,222,128,0.25);border-radius:8px;padding:8px 12px;margin-top:4px;">
          <span style="color:#4ade80;font-size:.78rem;font-weight:600;">
            🟢 Live — refreshes every {ri_opts[ri_val]}
          </span>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # ── LOCATION ──────────────────────────────────────────────
    st.markdown("### 📍 Location")
    MODES = ["Auto-Detect", "GPS Location", "Manual Select", "Custom Coordinates"]
    location_mode = st.radio(
        "Mode", MODES,
        index=MODES.index(st.session_state.location_mode)
              if st.session_state.location_mode in MODES else 0,
        label_visibility="collapsed"
    )
    st.session_state.location_mode = location_mode

    if location_mode == "Auto-Detect":
        if st.button("🔍 Detect via IP", use_container_width=True):
            with st.spinner("Detecting..."):
                loc = detect_location_ip()
                if loc:
                    st.session_state.lat      = loc["lat"]
                    st.session_state.lon      = loc["lon"]
                    st.session_state.timezone = loc.get("timezone", "UTC")
                    st.success(f"📍 {loc['city']}, {loc['country']}")
                    st.rerun()
                else:
                    st.error("Detection failed. Try Manual Select.")
        st.markdown(f"""
        <div style="background:#1f2937;padding:10px 12px;border-radius:8px;margin-top:6px;">
          <div style="color:#64748b;font-size:.72rem;margin-bottom:2px;">COORDINATES</div>
          <div style="color:#4ade80;font-size:.85rem;font-weight:600;">
            {st.session_state.lat:.4f}, {st.session_state.lon:.4f}
          </div>
        </div>""", unsafe_allow_html=True)

    elif location_mode == "GPS Location":
        st.caption("Browser will request location permission.")
        components.html("""
<!DOCTYPE html><html><head>
<style>
html,body{margin:0;padding:0;background:transparent;font-family:'Space Grotesk',sans-serif;}
#btn{width:100%;padding:9px 12px;background:linear-gradient(135deg,#1e3a5f,#0f172a);color:#4ade80;border:1.5px solid rgba(74,222,128,0.45);border-radius:8px;cursor:pointer;font-weight:600;font-size:13px;transition:background .2s;}
#btn:hover{background:rgba(74,222,128,0.12);}#btn:disabled{opacity:.6;cursor:default;}
#msg{color:#64748b;font-size:11.5px;margin-top:5px;text-align:center;min-height:18px;}
.coord{background:#1f2937;padding:8px 10px;border-radius:6px;margin-top:6px;color:#4ade80;font-size:12px;font-weight:600;display:none;}
</style></head><body>
<button id="btn" onclick="doGPS()">📡 Use GPS / Current Location</button>
<div id="msg"></div><div id="coord-box" class="coord"></div>
<script>
function doGPS(){
  var btn=document.getElementById('btn'),msg=document.getElementById('msg'),box=document.getElementById('coord-box');
  if(!navigator.geolocation){msg.innerHTML='<span style="color:#f87171">Not supported</span>';return;}
  btn.textContent='⏳ Locating...';btn.disabled=true;msg.textContent='Waiting for GPS…';
  navigator.geolocation.getCurrentPosition(
    function(p){var lat=p.coords.latitude.toFixed(6),lon=p.coords.longitude.toFixed(6);btn.textContent='✅ Captured!';msg.innerHTML='';box.style.display='block';box.innerHTML='📍 '+lat+', '+lon+'<br><span style="color:#64748b;font-size:10.5px;">Copy into the fields below → Apply</span>';},
    function(e){btn.textContent='📡 Use GPS / Current Location';btn.disabled=false;msg.innerHTML='<span style="color:#f87171">'+(e.code===1?'Permission denied':'Could not get location')+'</span>';},
    {enableHighAccuracy:true,timeout:12000,maximumAge:0}
  );
}
</script></body></html>""", height=115)
        col_a, col_b = st.columns(2)
        with col_a: gps_lat = st.number_input("Latitude",  -90.0,  90.0, value=st.session_state.lat, format="%.6f", key="gps_lat")
        with col_b: gps_lon = st.number_input("Longitude",-180.0, 180.0, value=st.session_state.lon, format="%.6f", key="gps_lon")
        if st.button("✅ Apply GPS Coordinates", use_container_width=True):
            st.session_state.lat = gps_lat; st.session_state.lon = gps_lon
            loc = detect_location_ip()
            if loc and loc.get("timezone"): st.session_state.timezone = loc["timezone"]
            st.success(f"📍 Set: {gps_lat:.4f}, {gps_lon:.4f}"); st.rerun()

    elif location_mode == "Manual Select":
        sel_region = st.selectbox("Region", list(GRID_ZONES.keys()),
            index=list(GRID_ZONES.keys()).index(st.session_state.selected_region)
                  if st.session_state.selected_region in GRID_ZONES else 0)
        st.session_state.selected_region = sel_region
        zones    = GRID_ZONES[sel_region]["zones"]
        sel_zone = st.selectbox("Grid Zone", list(zones.keys()),
            index=list(zones.keys()).index(st.session_state.selected_zone)
                  if st.session_state.selected_zone in zones else 0)
        st.session_state.selected_zone = sel_zone
        zd = zones[sel_zone]
        st.session_state.lat = zd["lat"]; st.session_state.lon = zd["lon"]
        st.session_state.timezone = GRID_ZONES[sel_region]["timezone"]
        st.markdown(f"""
        <div style="background:#1f2937;padding:10px 12px;border-radius:8px;margin-top:6px;">
          <div style="color:#64748b;font-size:.72rem;margin-bottom:2px;">ZONE INFO</div>
          <div style="color:#4ade80;font-size:.85rem;font-weight:600;">Avg CI: {zd['ci_avg']} gCO₂/kWh</div>
          <div style="color:#64748b;font-size:.72rem;margin-top:2px;">{GRID_ZONES[sel_region]["voltage"]}</div>
        </div>""", unsafe_allow_html=True)

    else:
        col_c, col_d = st.columns(2)
        with col_c: st.session_state.lat = st.number_input("Latitude",  -90.0,  90.0, value=st.session_state.lat, format="%.4f")
        with col_d: st.session_state.lon = st.number_input("Longitude",-180.0, 180.0, value=st.session_state.lon, format="%.4f")
        tz_opts = pytz.common_timezones
        cur_tz  = st.session_state.timezone if st.session_state.timezone in tz_opts else "UTC"
        st.session_state.timezone = st.selectbox("Timezone", tz_opts, index=tz_opts.index(cur_tz))

    _lbl = (st.session_state.selected_zone if location_mode == "Manual Select"
            else "GPS" if location_mode == "GPS Location"
            else f"{st.session_state.lat:.2f}°, {st.session_state.lon:.2f}°")
    st.markdown(f'<div style="margin-top:10px;"><span class="loc-badge">🌍 {_lbl}</span></div>', unsafe_allow_html=True)

    st.divider()

    # ── Live Clock ────────────────────────────────────────────
    _exact12 = get_current_time_exact_12(st.session_state.timezone)
    _now24   = get_current_time_24(st.session_state.timezone, STEP_MIN)
    _now12   = to_12hr(_now24)
    st.markdown(f"""
    <div class="live-time">🕐 {_exact12}</div>
    <div class="tz-sub">{st.session_state.timezone} &nbsp;|&nbsp; Slot: {_now12}</div>
    """, unsafe_allow_html=True)

    st.divider()

    # ── Data Source ───────────────────────────────────────────
    st.markdown("### 📁 Data Source")
    DS_OPTS = ["Automatic (API)", "Sample Data", "Upload CSV (Admin)"]
    data_source = st.radio("Source", DS_OPTS,
        index=DS_OPTS.index(st.session_state.data_source)
              if st.session_state.data_source in DS_OPTS else 0,
        label_visibility="collapsed")
    st.session_state.data_source = data_source
    uploaded_file = None; api_token = None
    if data_source == "Automatic (API)":
        st.caption("Real-time data from Electricity Maps + live simulation.")
        api_token = st.text_input("API Token (Optional)", type="password", placeholder="Free-tier token")
        if st.button("🔄 Refresh Now", use_container_width=True):
            st.cache_data.clear(); st.rerun()
    elif data_source == "Upload CSV (Admin)":
        st.warning("Admin mode — upload grid generation CSV.")
        uploaded_file = st.file_uploader("Upload CSV", type=["csv"], label_visibility="collapsed")
        with st.expander("📋 Required format"):
            st.markdown("Columns: `time` (HH:MM), `thermal_mw`, `hydro_mw`, `nuclear_mw`, `res_mw`")
    else:
        st.caption("Built-in synthetic grid profile with live simulation.")

    st.divider()

    # ── Appliance Settings ────────────────────────────────────
    st.markdown("### 🔌 Appliance")
    appliance = st.selectbox("Appliance", list(APPLIANCES.keys()), label_visibility="collapsed")
    if appliance != st.session_state.appliance:
        info = APPLIANCES[appliance]
        st.session_state.kw = info["kw"]; st.session_state.duration = info["duration_min"]
        st.session_state.deadline = info["deadline"]; st.session_state.appliance = appliance

    kw       = st.number_input("Power (kW)",        0.001, value=float(st.session_state.kw),    step=0.05, format="%.3f")
    duration = st.number_input("Duration (minutes)",   15, value=int(st.session_state.duration), step=15)
    deadline = st.text_input("Deadline (HH:MM)", value=st.session_state.deadline)
    if not validate_time_format(deadline):
        st.error("⚠️ Use HH:MM (24-hour)"); deadline = st.session_state.deadline
    else:
        st.session_state.deadline = deadline

    _now_mins      = time_to_minutes(_now24)
    _deadline_mins = time_to_minutes(deadline)
    _avail = (_deadline_mins - _now_mins) if _deadline_mins > _now_mins else (1440 - _now_mins + _deadline_mins)
    st.info(f"⏱️ **{_avail} min** available ({_avail // 60}h {_avail % 60}m)")
    if st.button("↩️ Reset to Defaults", use_container_width=True):
        info = APPLIANCES[appliance]
        st.session_state.kw = info["kw"]; st.session_state.duration = info["duration_min"]
        st.session_state.deadline = info["deadline"]; st.rerun()

    st.divider()

    # ── Optimization ──────────────────────────────────────────
    st.markdown("### ⚡ Optimization")
    top_k = st.slider("Top Windows to Show", 3, 12, 5)

    st.divider()

    # ── ★ NEW: Alerts & Budget ────────────────────────────────
    st.markdown("### 🔔 Alerts & Budget")
    alert_enabled = st.toggle("Enable CI alerts", value=st.session_state.alert_enabled)
    st.session_state.alert_enabled = alert_enabled
    if alert_enabled:
        alert_threshold = st.slider("Alert when CI below (gCO₂/kWh)", 50, 400,
                                    st.session_state.alert_threshold, step=25)
        st.session_state.alert_threshold = alert_threshold

    daily_budget = st.number_input("Daily CO₂ budget (kg)",
                                   min_value=0.1, max_value=10.0,
                                   value=float(st.session_state.daily_budget_kg),
                                   step=0.1, format="%.1f")
    st.session_state.daily_budget_kg = daily_budget

    st.session_state.kw = kw; st.session_state.duration = duration

# ──────────────────────  end sidebar  ───────────────────────


# ============================================================
# Data loading  (with real-time simulation)
# ============================================================
@st.cache_data(ttl=300)
def load_data_auto(lat, lon, tok, refresh_seed):
    """Fetch from API first, fall back to simulated profile."""
    df = fetch_electricity_maps_data(lat, lon, tok)
    if df is not None: return df
    # Estimate base CI from zone or default
    base = 400.0
    return generate_realtime_ci_profile(base, refresh_seed)

@st.cache_data(ttl=300)
def load_data_sample(refresh_seed):
    base = 420.0
    return generate_realtime_ci_profile(base, refresh_seed)

# Determine refresh seed so data evolves every N-minutes
_refresh_seed = int(time.time() // st.session_state.refresh_interval)

raw = None
if st.session_state.data_source == "Automatic (API)":
    with st.spinner("🌐 Fetching / simulating real-time grid data…"):
        raw = load_data_auto(st.session_state.lat, st.session_state.lon, api_token, _refresh_seed)
elif st.session_state.data_source == "Sample Data":
    raw = load_data_sample(_refresh_seed)
else:
    if uploaded_file:
        try:
            raw  = pd.read_csv(uploaded_file)
            miss = {"time","thermal_mw","hydro_mw","nuclear_mw","res_mw"} - set(raw.columns)
            if miss: st.error(f"Missing columns: {miss}"); st.stop()
        except Exception as e: st.error(f"CSV error: {e}"); st.stop()
    else:
        st.info("⬆️ Please upload a CSV file or switch to Sample / Automatic mode."); st.stop()

if raw is None or raw.empty: st.error("❌ No data available."); st.stop()

if "ci" not in raw.columns:
    raw["ci"] = raw.apply(lambda r: compute_ci_g_per_kwh(
        safe_float(r["thermal_mw"]), safe_float(r["hydro_mw"]),
        safe_float(r["nuclear_mw"]), safe_float(r["res_mw"])), axis=1)

df_ci       = raw[["time","ci"]].dropna().sort_values("time").reset_index(drop=True)
full_day_ci = make_full_day_ci(df_ci)

now24      = get_current_time_24(st.session_state.timezone, STEP_MIN)
now12      = to_12hr(now24)
windows    = recommend_windows(full_day_ci, duration, deadline, top_k, now24)
current_ci = find_ci_at_time(full_day_ci, now24) or float(full_day_ci["ci"].mean())
best       = windows[0] if windows else None
pot_sav    = ((current_ci - best["avg_ci"]) / current_ci * 100) if (best and current_ci > 0) else 0

# Derived energy
_hrs = ceil_to_step(duration) / 60
_nrg = float(kw) * _hrs

# Log data for stats
log_df = read_log()
stats  = compute_stats(log_df)

# Weekly forecast
weekly = generate_weekly_forecast(float(full_day_ci["ci"].mean()))


# ============================================================
# ★ LIVE ALERT BANNER
# ============================================================
if st.session_state.alert_enabled:
    if check_alerts(current_ci, st.session_state.alert_threshold):
        st.markdown(f"""
        <div class="alert-green">
          <strong style="color:#4ade80;">🟢 GREEN GRID ALERT!</strong>
          <span style="color:#94a3b8;"> Current CI is <strong style="color:#4ade80;">{current_ci:.0f} gCO₂/kWh</strong>
          — below your {st.session_state.alert_threshold} threshold. Great time to run appliances!</span>
        </div>""", unsafe_allow_html=True)
    elif current_ci > 400:
        st.markdown(f"""
        <div class="alert-red">
          <strong style="color:#f87171;">🔴 HIGH CARBON ALERT</strong>
          <span style="color:#94a3b8;"> CI is <strong style="color:#f87171;">{current_ci:.0f} gCO₂/kWh</strong>
          — consider delaying non-urgent appliances.</span>
        </div>""", unsafe_allow_html=True)


# ============================================================
# ★ LIVE TICKER
# ============================================================
ci_trend = "↑" if current_ci > float(full_day_ci["ci"].mean()) else "↓"
_tc = get_ci_color(current_ci)
st.markdown(f"""
<div class="ticker">
  <div class="ticker-dot"></div>
  <span style="color:#64748b;font-size:.78rem;font-weight:600;white-space:nowrap;">LIVE GRID</span>
  <span style="color:{_tc};font-weight:700;font-size:.88rem;">CI: {current_ci:.0f} gCO₂/kWh {ci_trend}</span>
  <span style="color:#475569;font-size:.75rem;">·</span>
  <span style="color:#94a3b8;font-size:.78rem;">Status: {get_ci_status(current_ci)}</span>
  <span style="color:#475569;font-size:.75rem;">·</span>
  <span style="color:#60a5fa;font-size:.78rem;">Best window: {to_12hr(best['start']) if best else '--'}</span>
  <span style="color:#475569;font-size:.75rem;">·</span>
  <span style="color:#94a3b8;font-size:.78rem;">Streak: {stats['streak']} days 🔥</span>
  <span style="color:#475569;font-size:.75rem;">·</span>
  <span style="color:#4ade80;font-size:.78rem;">Total saved: {stats['total_co2_saved']:.3f} kg CO₂</span>
</div>
""", unsafe_allow_html=True)

st.markdown("<div style='margin-bottom:0.8rem;'></div>", unsafe_allow_html=True)


# ============================================================
# KPI Row
# ============================================================
st.markdown("### 📊 Session Overview")
k1, k2, k3, k4, k5, k6 = st.columns(6)

with k1:
    _loc_lbl = (st.session_state.selected_zone if location_mode == "Manual Select"
                else "GPS" if location_mode == "GPS Location" else "Auto-Detect")
    st.markdown(f"""
    <div class="kpi">
      <div class="metric-label">🌍 Location</div>
      <div class="metric-value" style="font-size:.9rem;">{_loc_lbl}</div>
      <div class="metric-sub">{st.session_state.timezone}</div>
    </div>""", unsafe_allow_html=True)

with k2:
    st.markdown(f"""
    <div class="kpi">
      <div class="metric-label">🕐 Time</div>
      <div class="metric-value" style="font-size:1.1rem;">{now12}</div>
      <div class="metric-sub">{'🟢 Live' if st.session_state.live_mode else 'Static'}</div>
    </div>""", unsafe_allow_html=True)

with k3:
    icon = APPLIANCES[appliance]["icon"]
    st.markdown(f"""
    <div class="kpi">
      <div class="metric-label">🔌 Appliance</div>
      <div class="metric-value" style="font-size:.95rem;">{icon} {appliance.split('/')[0].strip()}</div>
      <div class="metric-sub">{kw:.2f} kW · {ceil_to_step(duration)} min</div>
    </div>""", unsafe_allow_html=True)

with k4:
    _cc = get_ci_color(current_ci)
    st.markdown(f"""
    <div class="kpi" style="border-left-color:{_cc};">
      <div class="metric-label">⚡ Carbon Intensity</div>
      <div class="metric-value" style="color:{_cc};">{current_ci:.0f}</div>
      <div class="metric-sub">{get_ci_status_plain(current_ci)} · gCO₂/kWh</div>
    </div>""", unsafe_allow_html=True)

with k5:
    _bc = "#4ade80" if best else "#64748b"
    _bt = to_12hr(best["start"]) if best else "--:--"
    st.markdown(f"""
    <div class="kpi" style="border-left-color:{_bc};">
      <div class="metric-label">✨ Best Window</div>
      <div class="metric-value" style="color:{_bc};font-size:1.05rem;">{_bt}</div>
      <div class="metric-sub">{f"Save ~{pot_sav:.1f}%" if pot_sav > 0 else "No savings"}</div>
    </div>""", unsafe_allow_html=True)

with k6:
    _sc = "#4ade80" if stats["streak"] >= 3 else "#fbbf24" if stats["streak"] >= 1 else "#64748b"
    st.markdown(f"""
    <div class="kpi" style="border-left-color:{_sc};">
      <div class="metric-label">🔥 Streak</div>
      <div class="metric-value" style="color:{_sc};">{stats['streak']} days</div>
      <div class="metric-sub">{len(stats['badges'])} badges · {stats['total_runs']} runs</div>
    </div>""", unsafe_allow_html=True)

st.markdown("---")


# ============================================================
# TABS
# ============================================================
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📈 Dashboard", "🎯 Smart Advisor", "🌤️ Weekly Forecast",
    "📊 Analytics", "🏅 Achievements", "📝 Logger", "🔍 Data"
])


# ─────────────────────────────────────────────────────────────
# TAB 1 — Dashboard
# ─────────────────────────────────────────────────────────────
with tab1:
    col_chart, col_right = st.columns([2, 1])

    with col_chart:
        st.subheader(f"24-Hour Carbon Intensity  ·  {st.session_state.timezone}")

        # CI gauge
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=current_ci,
            delta={"reference": float(full_day_ci["ci"].mean()),
                   "valueformat": ".0f", "suffix": " avg"},
            title={"text": "Current CI (gCO₂/kWh)", "font": {"color": "#94a3b8", "size": 13}},
            number={"font": {"color": get_ci_color(current_ci), "size": 36}, "suffix": ""},
            gauge={
                "axis": {"range": [0, 700], "tickcolor": "#475569", "tickfont": {"color":"#475569","size":10}},
                "bar":  {"color": get_ci_color(current_ci), "thickness": 0.25},
                "bgcolor": "#111b2e", "borderwidth": 0,
                "steps": [
                    {"range": [0,   200], "color": "rgba(74,222,128,0.12)"},
                    {"range": [200, 400], "color": "rgba(251,191,36,0.10)"},
                    {"range": [400, 700], "color": "rgba(248,113,113,0.10)"},
                ],
                "threshold": {"line": {"color": "#4ade80", "width": 2},
                              "thickness": 0.75, "value": current_ci}
            }
        ))
        fig_gauge.update_layout(
            paper_bgcolor="#0c1422", font=dict(color="#cbd5e1"),
            height=200, margin=dict(l=30, r=30, t=30, b=10)
        )
        st.plotly_chart(fig_gauge, use_container_width=True)

        # 24-h line chart
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=full_day_ci["time"], y=full_day_ci["ci"],
            fill="tozeroy", fillcolor="rgba(74,222,128,0.07)",
            line=dict(color="#4ade80", width=2.5),
            hovertemplate="<b>%{x}</b><br>CI: %{y:.1f} gCO₂/kWh<extra></extra>"
        ))
        # CI zone bands
        fig.add_hrect(y0=0,   y1=200, fillcolor="#4ade80", opacity=0.04, line_width=0)
        fig.add_hrect(y0=200, y1=400, fillcolor="#fbbf24", opacity=0.04, line_width=0)
        fig.add_hrect(y0=400, y1=900, fillcolor="#f87171", opacity=0.04, line_width=0)
        # Highlight top windows
        for i, w in enumerate((windows or [])[:3]):
            fig.add_vrect(x0=w["start"], x1=w["end"], fillcolor=w["color"],
                          opacity=0.18, line_width=2 if i == 0 else 1,
                          line_color=w["color"], layer="below")
        _mx = max(full_day_ci["ci"]) * 1.08
        fig.add_vline(x=now24,    line_dash="dash", line_color="#4ade80", line_width=1.8)
        fig.add_vline(x=deadline, line_dash="dash", line_color="#f87171", line_width=1.8)
        fig.add_annotation(x=now24,    y=_mx, text="NOW",      showarrow=False,
                           font=dict(color="#4ade80", size=11), bgcolor="rgba(8,13,22,.8)")
        fig.add_annotation(x=deadline, y=_mx, text="DEADLINE", showarrow=False,
                           font=dict(color="#f87171", size=11), bgcolor="rgba(8,13,22,.8)")
        # Daily average line
        fig.add_hline(y=float(full_day_ci["ci"].mean()), line_dash="dot",
                      line_color="#60a5fa", line_width=1,
                      annotation_text="Daily avg", annotation_font_color="#60a5fa")

        fig.update_layout(
            plot_bgcolor="#0c1422", paper_bgcolor="#0c1422",
            font=dict(color="#cbd5e1"),
            xaxis=dict(gridcolor="#1a2535", title="Time of Day", tickangle=-45, nticks=13),
            yaxis=dict(gridcolor="#1a2535", title="gCO₂/kWh",
                       range=[0, max(full_day_ci["ci"]) * 1.18]),
            height=360, margin=dict(l=55, r=30, t=20, b=55), showlegend=False
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.subheader("Grid Mix")
        latest = raw.iloc[-1]
        vals   = [safe_float(latest[c]) for c in ["thermal_mw","hydro_mw","nuclear_mw","res_mw"]]
        total  = sum(vals) or 1
        fig2 = go.Figure(data=[go.Pie(
            labels=["Thermal","Hydro","Nuclear","Renewable"], values=vals,
            hole=0.65, marker_colors=["#f87171","#60a5fa","#a78bfa","#4ade80"],
            textinfo="label+percent", textfont=dict(color="white", size=10)
        )])
        fig2.update_layout(
            plot_bgcolor="#0c1422", paper_bgcolor="#0c1422", font=dict(color="#cbd5e1"),
            height=280, showlegend=False, margin=dict(t=10,b=10,l=10,r=10),
            annotations=[dict(text=f"<b>{total/1000:.1f}</b><br>GW",
                              x=0.5, y=0.5, font_size=16, font_color="#f8fafc", showarrow=False)]
        )
        st.plotly_chart(fig2, use_container_width=True)

        # ★ CO2 Equivalents
        st.markdown("#### 🌿 CO₂ Equivalents")
        _co2_now = calculate_co2(_nrg, current_ci)
        _co2_opt = calculate_co2(_nrg, best["avg_ci"]) if best else _co2_now
        _saved   = max(0, _co2_now - _co2_opt)
        st.markdown(f"""
        <div style="background:#111b2e;border-radius:10px;padding:12px 14px;border:1px solid #1e2d45;">
          <div style="color:#64748b;font-size:.7rem;margin-bottom:6px;text-transform:uppercase;letter-spacing:.06em;">
            Running now emits {_co2_now:.3f} kg CO₂
          </div>
          <div style="color:#4ade80;font-size:.78rem;margin-bottom:4px;font-weight:600;">
            By optimizing you save:
          </div>
        """, unsafe_allow_html=True)
        equivs = co2_equivalents(_saved)
        for label, val in equivs.items():
            st.markdown(f"""
          <div style="display:flex;justify-content:space-between;align-items:center;
                      padding:4px 0;border-bottom:1px solid #1a2535;font-size:.78rem;">
            <span style="color:#94a3b8;">{label}</span>
            <span style="color:white;font-weight:600;">{val:.2f}</span>
          </div>""", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # Zone avg
        _zone_ci = (GRID_ZONES.get(st.session_state.selected_region, {})
                    .get("zones", {}).get(st.session_state.selected_zone, {})
                    .get("ci_avg", "—"))
        st.markdown(f"""
        <div style="background:#111b2e;border-radius:10px;padding:12px 14px;
                    margin-top:10px;border:1px solid #1e2d45;">
          <div style="color:#64748b;font-size:.7rem;margin-bottom:3px;text-transform:uppercase;">ZONE AVG CI</div>
          <div style="color:#fbbf24;font-size:1.4rem;font-weight:700;">{_zone_ci}</div>
          <div style="color:#64748b;font-size:.7rem;">gCO₂/kWh historical avg</div>
        </div>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# TAB 2 — Smart Advisor
# ─────────────────────────────────────────────────────────────
with tab2:
    st.subheader(f"🏆 Top {top_k} Optimal Windows  ·  {now12} → {to_12hr(deadline)}")

    if not windows:
        st.warning("⚠️ No valid windows found — try extending the deadline or reducing duration.")
        st.info(f"**Debug:** Current: {now12} · Deadline: {to_12hr(deadline)} · Duration: {ceil_to_step(duration)} min · Available: {_avail} min")
    else:
        for row_start in range(0, len(windows), 3):
            rw   = windows[row_start:row_start + 3]
            cols = st.columns(len(rw))
            for i, (col, w) in enumerate(zip(cols, rw)):
                gi   = row_start + i
                rank = gi + 1
                with col:
                    co2  = calculate_co2(_nrg, w["avg_ci"])
                    co2n = calculate_co2(_nrg, current_ci)
                    sav  = co2n - co2
                    pct_sav = (sav / co2n * 100) if co2n > 0 else 0
                    ib   = gi == 0
                    brd  = "border:2px solid #4ade80;" if ib else "border:1px solid #1e2d45;"
                    # Confidence score (lower CI variance = higher confidence)
                    ci_range = w["max_ci"] - w["min_ci"]
                    conf = max(60, min(99, int(100 - ci_range / 5)))

                    # CO2 equivalents for savings
                    eq_trees = sav / CO2_EQUIV["🌳 Trees (1 year)"]
                    eq_km    = sav / CO2_EQUIV["🚗 Driving (km)"]

                    st.markdown(f"""
<div class="rec-card" style="{brd}">
  <div style="text-align:center;margin-bottom:8px;">
    <span style="background:{'#4ade80' if ib else '#1e3a5f'};
                 color:{'#0f172a' if ib else '#94a3b8'};
                 padding:3px 10px;border-radius:5px;font-size:.72rem;font-weight:700;">
      {'🥇 BEST' if rank==1 else f'#{rank}'}
    </span>
    <span style="background:rgba(74,222,128,0.1);color:#4ade80;
                 padding:2px 7px;border-radius:4px;font-size:.65rem;font-weight:600;margin-left:4px;">
      {conf}% conf.
    </span>
  </div>
  <div style="font-size:1.1rem;font-weight:700;color:white;text-align:center;margin-bottom:10px;">
    {to_12hr(w["start"])} — {to_12hr(w["end"])}
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px;">
    <div style="background:#0c1422;padding:8px 4px;border-radius:8px;text-align:center;">
      <div style="color:#64748b;font-size:.62rem;margin-bottom:2px;">CI (avg)</div>
      <div style="color:{w['color']};font-size:.95rem;font-weight:700;">{w["avg_ci"]:.0f}</div>
    </div>
    <div style="background:#0c1422;padding:8px 4px;border-radius:8px;text-align:center;">
      <div style="color:#64748b;font-size:.62rem;margin-bottom:2px;">CO₂</div>
      <div style="color:white;font-size:.95rem;font-weight:700;">{co2:.3f} kg</div>
    </div>
  </div>
  <div style="background:rgba(74,222,128,0.07);padding:6px 8px;border-radius:8px;
              border:1px solid rgba(74,222,128,0.2);margin-bottom:6px;">
    <span style="color:#4ade80;font-size:.78rem;font-weight:600;">
      💰 Save {sav:.3f} kg ({pct_sav:.0f}%)
    </span>
  </div>
  <div style="font-size:.7rem;color:#475569;text-align:center;">
    ≈ {eq_trees:.2f} tree-days · {eq_km:.1f} km not driven
  </div>
</div>""", unsafe_allow_html=True)

                    if st.button("✅ Select", key=f"sel_{gi}", use_container_width=True):
                        st.session_state.selected_window = gi
                        st.success(f"✅ Selected: {to_12hr(w['start'])} – {to_12hr(w['end'])}")

    # ★ CI Mini-heatmap for the day
    st.markdown("---")
    st.markdown("#### 🗓️ Today's CI Heatmap (24h)")
    heat_data = full_day_ci.set_index("time")["ci"].values.reshape(6, 16)
    fig_heat  = go.Figure(data=go.Heatmap(
        z=heat_data,
        colorscale=[[0,"#4ade80"],[0.4,"#fbbf24"],[1,"#f87171"]],
        showscale=True, colorbar=dict(title="gCO₂/kWh", tickfont=dict(color="#94a3b8")),
        hovertemplate="CI: %{z:.1f} gCO₂/kWh<extra></extra>"
    ))
    fig_heat.update_layout(
        paper_bgcolor="#0c1422", plot_bgcolor="#0c1422",
        font=dict(color="#cbd5e1"), height=200,
        margin=dict(l=20, r=20, t=20, b=20),
        xaxis=dict(showticklabels=False), yaxis=dict(showticklabels=False)
    )
    st.plotly_chart(fig_heat, use_container_width=True)


# ─────────────────────────────────────────────────────────────
# TAB 3 — ★ NEW: Weekly Forecast
# ─────────────────────────────────────────────────────────────
with tab3:
    st.subheader("🌤️ 7-Day Grid CI Forecast")
    st.caption("Forecast based on historical day-of-week patterns + current grid conditions.")

    # Weekly bar chart
    fig_week = go.Figure()
    fig_week.add_trace(go.Bar(
        name="Night (Low)", x=weekly["date"], y=weekly["ci_night"],
        marker_color="#4ade80", opacity=0.8,
        hovertemplate="%{x}<br>Night CI: %{y:.0f} gCO₂/kWh<extra></extra>"
    ))
    fig_week.add_trace(go.Bar(
        name="Daytime", x=weekly["date"], y=weekly["ci_day"],
        marker_color="#60a5fa", opacity=0.8,
        hovertemplate="%{x}<br>Day CI: %{y:.0f} gCO₂/kWh<extra></extra>"
    ))
    fig_week.add_trace(go.Bar(
        name="Peak", x=weekly["date"], y=weekly["ci_peak"],
        marker_color="#f87171", opacity=0.8,
        hovertemplate="%{x}<br>Peak CI: %{y:.0f} gCO₂/kWh<extra></extra>"
    ))
    fig_week.add_trace(go.Scatter(
        name="Daily Avg", x=weekly["date"], y=weekly["avg"],
        mode="lines+markers", line=dict(color="#fbbf24", width=2, dash="dot"),
        marker=dict(size=7), hovertemplate="%{x}<br>Avg: %{y:.0f}<extra></extra>"
    ))
    fig_week.update_layout(
        barmode="group", plot_bgcolor="#0c1422", paper_bgcolor="#0c1422",
        font=dict(color="#cbd5e1"), height=380,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5,
                    font=dict(size=11)),
        xaxis=dict(gridcolor="#1a2535"), yaxis=dict(gridcolor="#1a2535", title="gCO₂/kWh"),
        margin=dict(l=50, r=30, t=50, b=40)
    )
    st.plotly_chart(fig_week, use_container_width=True)

    # Day cards
    st.markdown("#### 📅 Daily Outlook")
    day_cols = st.columns(7)
    for i, (col, row) in enumerate(zip(day_cols, weekly.itertuples())):
        with col:
            best_period = "Night" if row.ci_night < row.ci_day and row.ci_night < row.ci_peak \
                          else "Day" if row.ci_day < row.ci_peak else "Peak"
            _bc = get_ci_color(row.avg)
            _border = "2px solid #4ade80" if i == 0 else "1px solid #1e2d45"
            st.markdown(f"""
            <div style="background:#111b2e;border-radius:12px;padding:10px 8px;
                        text-align:center;border:{_border};">
              <div style="color:#64748b;font-size:.68rem;font-weight:600;">{row.label}</div>
              <div style="font-size:1.2rem;margin:4px 0;">{'☀️' if best_period=='Day' else '🌙' if best_period=='Night' else '⚡'}</div>
              <div style="color:{_bc};font-size:.95rem;font-weight:700;">{row.avg:.0f}</div>
              <div style="color:#475569;font-size:.62rem;">gCO₂/kWh</div>
              <div style="background:rgba(74,222,128,0.1);border-radius:4px;
                          padding:2px 4px;margin-top:5px;font-size:.6rem;color:#4ade80;">
                Best: {best_period}
              </div>
            </div>""", unsafe_allow_html=True)

    # ★ Regional Comparison
    st.markdown("---")
    st.subheader("🗺️ Regional CI Comparison")
    regions_flat = []
    for region, rdata in GRID_ZONES.items():
        for zone, zdata in rdata["zones"].items():
            regions_flat.append({
                "zone": zone, "region": region,
                "ci": zdata["ci_avg"], "color": get_ci_color(zdata["ci_avg"])
            })
    df_reg = pd.DataFrame(regions_flat).sort_values("ci")
    fig_reg = go.Figure(go.Bar(
        x=df_reg["ci"], y=df_reg["zone"], orientation="h",
        marker_color=[get_ci_color(c) for c in df_reg["ci"]],
        hovertemplate="<b>%{y}</b><br>CI: %{x} gCO₂/kWh<extra></extra>",
        text=df_reg["ci"].astype(int), textposition="outside",
        textfont=dict(color="#94a3b8", size=10)
    ))
    fig_reg.update_layout(
        plot_bgcolor="#0c1422", paper_bgcolor="#0c1422", font=dict(color="#cbd5e1"),
        height=550, margin=dict(l=180, r=60, t=20, b=40),
        xaxis=dict(gridcolor="#1a2535", title="Average CI (gCO₂/kWh)"),
        yaxis=dict(gridcolor="#1a2535")
    )
    st.plotly_chart(fig_reg, use_container_width=True)


# ─────────────────────────────────────────────────────────────
# TAB 4 — Analytics
# ─────────────────────────────────────────────────────────────
with tab4:
    st.subheader("📊 Baseline vs Optimized Analysis")

    co2n = calculate_co2(_nrg, current_ci)
    co2b = calculate_co2(_nrg, best["avg_ci"]) if best else co2n
    proj = co2n - co2b
    pct  = (proj / co2n * 100) if co2n > 0 else 0

    # Context banner
    _loc_str = (st.session_state.selected_zone if location_mode == "Manual Select"
                else f"{st.session_state.lat:.3f}°, {st.session_state.lon:.3f}°")
    st.markdown(f"""
    <div style="background:#111b2e;border-radius:10px;padding:10px 16px;
                margin-bottom:1rem;border-left:3px solid #3b82f6;">
      <span style="color:#94a3b8;font-size:.8rem;">📍 {_loc_str}</span>
      <span style="color:#475569;font-size:.8rem;margin:0 8px;">|</span>
      <span style="color:#94a3b8;font-size:.8rem;">🕐 {st.session_state.timezone}</span>
      <span style="color:#475569;font-size:.8rem;margin:0 8px;">|</span>
      <span style="color:#94a3b8;font-size:.8rem;">📡 {st.session_state.data_source}</span>
    </div>""", unsafe_allow_html=True)

    c_left, c_right = st.columns([3, 2])
    with c_left:
        fc = go.Figure()
        fc.add_trace(go.Bar(
            name="Baseline (Now)", x=["CO₂ Emissions"], y=[co2n],
            marker_color="#f87171", text=[f"{co2n:.3f} kg"], textposition="outside", width=0.28
        ))
        fc.add_trace(go.Bar(
            name="Optimized (Best)", x=["CO₂ Emissions"], y=[co2b],
            marker_color="#4ade80", text=[f"{co2b:.3f} kg"], textposition="outside", width=0.28
        ))
        fc.update_layout(
            barmode="group", plot_bgcolor="#0c1422", paper_bgcolor="#0c1422",
            font=dict(color="#cbd5e1", size=13), height=320,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
            yaxis=dict(title="CO₂ (kg)", gridcolor="#1a2535", range=[0, max(co2n,co2b)*1.4]),
            margin=dict(l=50,r=30,t=50,b=40),
            title=dict(text=f"💰 Potential savings: {proj:.3f} kg CO₂ ({pct:.1f}%)",
                       font=dict(size=13, color="#4ade80"))
        )
        st.plotly_chart(fc, use_container_width=True)

    with c_right:
        # ★ Daily budget tracker
        st.markdown("#### 💰 CO₂ Budget Tracker")
        today_co2 = 0.0
        if not log_df.empty and "date" in log_df and "co2_kg" in log_df:
            today_str = datetime.now().strftime("%Y-%m-%d")
            mask      = log_df["date"].astype(str) == today_str
            today_co2 = float(log_df[mask]["co2_kg"].sum())

        budget    = st.session_state.daily_budget_kg
        pct_used  = min(100.0, today_co2 / max(budget, 0.001) * 100)
        remaining = max(0, budget - today_co2)
        bar_color = "#4ade80" if pct_used < 60 else "#fbbf24" if pct_used < 90 else "#f87171"

        st.markdown(f"""
        <div style="background:#111b2e;border-radius:12px;padding:16px;border:1px solid #1e2d45;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <span style="color:#94a3b8;font-size:.8rem;">Daily Budget</span>
            <span style="color:white;font-size:.85rem;font-weight:600;">{budget:.1f} kg CO₂</span>
          </div>
          <div class="prog-bar">
            <div class="prog-bar-fill" style="width:{pct_used:.0f}%;background:{bar_color};"></div>
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:6px;">
            <span style="color:{bar_color};font-size:.78rem;font-weight:600;">Used: {today_co2:.3f} kg</span>
            <span style="color:#64748b;font-size:.78rem;">Left: {remaining:.3f} kg</span>
          </div>
          <div style="margin-top:10px;padding-top:10px;border-top:1px solid #1a2535;">
            <div style="color:#64748b;font-size:.7rem;margin-bottom:4px;">This session:</div>
            <div style="display:flex;justify-content:space-between;">
              <span style="color:#f87171;font-size:.78rem;">Now: {co2n:.3f} kg</span>
              <span style="color:#4ade80;font-size:.78rem;">Optimal: {co2b:.3f} kg</span>
            </div>
          </div>
        </div>""", unsafe_allow_html=True)

        # CO2 equivalents table
        st.markdown("#### 🌍 What {:.3f} kg CO₂ Saves".format(proj))
        equivs = co2_equivalents(proj)
        for label, val in equivs.items():
            st.markdown(f"""
            <div class="cmp-row">
              <span style="color:#94a3b8;font-size:.82rem;flex:1;">{label}</span>
              <span style="color:#4ade80;font-weight:700;font-size:.85rem;">{val:.2f}</span>
            </div>""", unsafe_allow_html=True)

    m1, m2, m3, m4 = st.columns(4)
    with m1: st.metric("🔴 Baseline",  f"{co2n:.3f} kg",  help=f"Running now at CI={current_ci:.0f}")
    with m2: st.metric("🟢 Optimized", f"{co2b:.3f} kg",  help=f"Best window CI={best['avg_ci']:.0f}" if best else "N/A")
    with m3: st.metric("💰 CO₂ Saved", f"{proj:.3f} kg",  delta=f"-{pct:.1f}%", delta_color="inverse")
    with m4: st.metric("⚡ Energy",    f"{_nrg:.3f} kWh", help=f"{kw} kW × {ceil_to_step(duration)/60:.2f} h")

    # History chart
    if not log_df.empty and "timestamp" in log_df and "co2_kg" in log_df:
        st.markdown("---")
        st.subheader("📋 CO₂ Savings Over Time")
        log_df["ts"] = pd.to_datetime(log_df["timestamp"], errors="coerce")
        log_df_sorted = log_df.dropna(subset=["ts"]).sort_values("ts")
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(
            x=log_df_sorted["ts"], y=log_df_sorted["co2_kg"].cumsum(),
            mode="lines+markers", line=dict(color="#4ade80", width=2),
            fill="tozeroy", fillcolor="rgba(74,222,128,0.07)",
            name="Cumulative CO₂"
        ))
        fig_hist.update_layout(
            plot_bgcolor="#0c1422", paper_bgcolor="#0c1422", font=dict(color="#cbd5e1"),
            height=250, margin=dict(l=50,r=30,t=30,b=40),
            xaxis=dict(gridcolor="#1a2535"), yaxis=dict(gridcolor="#1a2535", title="kg CO₂ (cumulative)")
        )
        st.plotly_chart(fig_hist, use_container_width=True)
        st.dataframe(log_df.sort_values("timestamp", ascending=False),
                     use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────
# TAB 5 — ★ NEW: Achievements / Gamification
# ─────────────────────────────────────────────────────────────
with tab5:
    st.subheader("🏅 Achievements & Eco-Milestones")

    # Stats row
    s1, s2, s3, s4 = st.columns(4)
    with s1:
        st.markdown(f"""
        <div class="kpi">
          <div class="metric-label">Total Runs</div>
          <div class="metric-value">{stats['total_runs']}</div>
          <div class="metric-sub">{stats['rec_runs']} recommended</div>
        </div>""", unsafe_allow_html=True)
    with s2:
        st.markdown(f"""
        <div class="kpi" style="border-left-color:#f87171;">
          <div class="metric-label">CO₂ Tracked</div>
          <div class="metric-value" style="color:#f87171;">{stats['total_co2_saved']:.3f}</div>
          <div class="metric-sub">kg CO₂ total</div>
        </div>""", unsafe_allow_html=True)
    with s3:
        st.markdown(f"""
        <div class="kpi" style="border-left-color:#fbbf24;">
          <div class="metric-label">🔥 Streak</div>
          <div class="metric-value" style="color:#fbbf24;">{stats['streak']}</div>
          <div class="metric-sub">consecutive days</div>
        </div>""", unsafe_allow_html=True)
    with s4:
        st.markdown(f"""
        <div class="kpi" style="border-left-color:#a78bfa;">
          <div class="metric-label">⚡ Energy Used</div>
          <div class="metric-value" style="color:#a78bfa;">{stats['total_kwh']:.2f}</div>
          <div class="metric-sub">kWh total</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 🏆 Badges")

    badge_cols = st.columns(4)
    for i, badge in enumerate(BADGES):
        with badge_cols[i % 4]:
            earned = badge["id"] in stats["badges"]
            opacity = "1" if earned else "0.35"
            glow    = "box-shadow:0 0 18px rgba(74,222,128,0.25);" if earned else ""
            st.markdown(f"""
            <div class="badge-card {'earned' if earned else ''}" style="opacity:{opacity};{glow}">
              <div style="font-size:2rem;">{badge['icon']}</div>
              <div style="color:{'#4ade80' if earned else '#94a3b8'};font-weight:600;font-size:.8rem;margin-top:6px;">
                {badge['name']}
              </div>
              <div style="color:#475569;font-size:.68rem;margin-top:3px;">{badge['desc']}</div>
              <div style="margin-top:6px;">
                {'<span style="color:#4ade80;font-size:.68rem;font-weight:700;">✓ EARNED</span>'
                 if earned else
                 '<span style="color:#475569;font-size:.68rem;">Locked</span>'}
              </div>
            </div>""", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 📈 Your Impact Summary")

    total_trees = stats["total_co2_saved"] / CO2_EQUIV["🌳 Trees (1 year)"]
    total_km    = stats["total_co2_saved"] / CO2_EQUIV["🚗 Driving (km)"]
    total_phone = stats["total_co2_saved"] / CO2_EQUIV["📱 Phone charges"]

    im1, im2, im3 = st.columns(3)
    with im1:
        st.markdown(f"""
        <div style="background:#111b2e;border-radius:14px;padding:20px;text-align:center;border:1px solid #1e2d45;">
          <div style="font-size:2.5rem;">🌳</div>
          <div style="color:#4ade80;font-size:1.6rem;font-weight:700;margin:8px 0;">{total_trees:.1f}</div>
          <div style="color:#94a3b8;font-size:.82rem;">tree-days of absorption</div>
        </div>""", unsafe_allow_html=True)
    with im2:
        st.markdown(f"""
        <div style="background:#111b2e;border-radius:14px;padding:20px;text-align:center;border:1px solid #1e2d45;">
          <div style="font-size:2.5rem;">🚗</div>
          <div style="color:#60a5fa;font-size:1.6rem;font-weight:700;margin:8px 0;">{total_km:.1f}</div>
          <div style="color:#94a3b8;font-size:.82rem;">km of driving avoided</div>
        </div>""", unsafe_allow_html=True)
    with im3:
        st.markdown(f"""
        <div style="background:#111b2e;border-radius:14px;padding:20px;text-align:center;border:1px solid #1e2d45;">
          <div style="font-size:2.5rem;">📱</div>
          <div style="color:#a78bfa;font-size:1.6rem;font-weight:700;margin:8px 0;">{total_phone:.0f}</div>
          <div style="color:#94a3b8;font-size:.82rem;">phone charges equivalent</div>
        </div>""", unsafe_allow_html=True)

    # ★ CI Alert History / Tips
    st.markdown("---")
    st.markdown("#### 💡 Smart Tips")
    avg_ci = float(full_day_ci["ci"].mean())
    min_ci_time = full_day_ci.loc[full_day_ci["ci"].idxmin(), "time"]
    max_ci_time = full_day_ci.loc[full_day_ci["ci"].idxmax(), "time"]

    tips = [
        (f"🌙 Best time today is around **{to_12hr(min_ci_time)}** with lowest CI — schedule heavy appliances then.",
         "#4ade80"),
        (f"🔥 Avoid running appliances around **{to_12hr(max_ci_time)}** — grid is dirtiest then.",
         "#f87171"),
        (f"📊 Today's average CI is **{avg_ci:.0f} gCO₂/kWh** — {'below' if avg_ci < 300 else 'above'} the 300 benchmark.",
         "#fbbf24"),
        ("🔄 Enable Live Mode in the sidebar to keep data fresh and get real-time alerts.", "#60a5fa"),
        ("🏅 Log your runs regularly to build your streak and unlock badges!", "#a78bfa"),
    ]
    for tip, color in tips:
        st.markdown(f"""
        <div style="background:#111b2e;border-left:3px solid {color};border-radius:8px;
                    padding:10px 14px;margin-bottom:6px;color:#94a3b8;font-size:.85rem;">
          {tip}
        </div>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# TAB 6 — Logger
# ─────────────────────────────────────────────────────────────
with tab6:
    ensure_dirs()
    st.subheader("📝 Log an Appliance Run")
    st.caption(f"System time: **{get_current_time_exact_12(st.session_state.timezone)}** ({st.session_state.timezone})")

    with st.form("run_form", clear_on_submit=False):
        r1, r2, r3 = st.columns(3)
        with r1:
            run_date = st.date_input("Date", datetime.now())
            run_type = st.selectbox("Run Type", ["recommended", "baseline", "test"])
        with r2:
            _def_start = (windows[st.session_state.selected_window]["start"]
                          if windows and st.session_state.selected_window < len(windows) else now24)
            start_time = st.text_input("Start Time (HH:MM)", value=_def_start)
            run_dur    = st.number_input("Duration (min)", 15, value=ceil_to_step(int(duration)), step=15)
        with r3:
            run_app = st.selectbox("Appliance", list(APPLIANCES.keys()))
            notes   = st.text_input("Notes",
                value=f"Zone: {st.session_state.selected_zone if location_mode == 'Manual Select' else 'Auto'}")

        st.markdown("##### Meter Reading")
        m1c, m2c = st.columns(2)
        with m1c: mb = st.number_input("Before (kWh)", min_value=0.0, format="%.3f")
        with m2c: ma = st.number_input("After (kWh)",  min_value=0.0, format="%.3f")
        submitted = st.form_submit_button("💾 Save Run", use_container_width=True)

    if submitted:
        kwh = ma - mb
        if kwh <= 0:
            st.error("❌ 'After' reading must be greater than 'Before'.")
        else:
            ci_v = find_ci_at_time(full_day_ci, start_time) or float(full_day_ci["ci"].mean())
            et   = minutes_to_time(time_to_minutes(start_time) + ceil_to_step(int(run_dur)))
            _row = {
                "timestamp":        datetime.now().isoformat(),
                "date":             str(run_date),
                "run_type":         run_type,
                "appliance":        run_app,
                "start_time":       start_time,
                "end_time":         et,
                "kwh_used":         round(float(kwh), 4),
                "avg_ci_g_per_kwh": round(float(ci_v), 2),
                "co2_kg":           round(calculate_co2(kwh, ci_v), 4),
                "location":         (st.session_state.selected_zone if location_mode == "Manual Select"
                                     else f"{st.session_state.lat:.2f},{st.session_state.lon:.2f}"),
                "timezone":         st.session_state.timezone,
                "notes":            notes
            }
            append_log(_row)
            read_log.clear()
            st.success(f"✅ Saved — {kwh:.3f} kWh · {calculate_co2(kwh, ci_v):.4f} kg CO₂")
            if run_type == "recommended": st.balloons()


# ─────────────────────────────────────────────────────────────
# TAB 7 — Data
# ─────────────────────────────────────────────────────────────
with tab7:
    st.subheader("🔍 Raw Grid Data & Configuration")

    with st.expander("📍 Current Location Configuration", expanded=False):
        st.json({
            "Mode": location_mode, "Latitude": st.session_state.lat,
            "Longitude": st.session_state.lon, "Timezone": st.session_state.timezone,
            "Data Source": st.session_state.data_source,
            "Live Mode": st.session_state.live_mode,
            "Refresh Interval": f"{st.session_state.refresh_interval}s"
        })
        if st.button("💾 Save Config to File"):
            save_location_config({"lat": st.session_state.lat, "lon": st.session_state.lon,
                                   "timezone": st.session_state.timezone})
            st.success("✅ Configuration saved to config/location.json")

    st.markdown("---")
    cols_show = [c for c in ["time","thermal_mw","hydro_mw","nuclear_mw","res_mw","ci"] if c in raw.columns]
    st.markdown("#### Grid Data Table")
    st.dataframe(raw[cols_show].sort_values("time").reset_index(drop=True),
                 use_container_width=True, hide_index=True, height=380)

    st.markdown("---")
    st.markdown("#### Export / Refresh")
    _csv_bytes = raw[cols_show].to_csv(index=False).encode()
    st.download_button(
        label="📥 Download Grid CSV",
        data=_csv_bytes,
        file_name=f"carbonwise_{st.session_state.lat:.2f}_{st.session_state.lon:.2f}.csv",
        mime="text/csv", use_container_width=True
    )
    if not log_df.empty:
        _log_csv = log_df.to_csv(index=False).encode()
        st.download_button(
            label="📥 Download Run History CSV",
            data=_log_csv, file_name="carbonwise_runs.csv",
            mime="text/csv", use_container_width=True
        )
    if st.button("🔄 Refresh All Data", use_container_width=True):
        st.cache_data.clear(); st.rerun()

    # Live simulation info
    st.markdown("---")
    st.markdown("#### ℹ️ Data Simulation Info")
    st.markdown(f"""
    <div style="background:#111b2e;border-radius:10px;padding:14px 16px;border:1px solid #1e2d45;
                color:#94a3b8;font-size:.82rem;line-height:1.7;">
      <strong style="color:#4ade80;">How real-time simulation works:</strong><br>
      The app uses a <strong>time-seeded RNG</strong> that changes every
      <strong>{st.session_state.refresh_interval}s</strong>, producing naturally evolving CI data.
      The daily demand curve follows real-world patterns (night troughs, morning/evening peaks,
      midday solar dip). When the Electricity Maps API returns a live base CI,
      the simulation scales accordingly. Without an API token, the simulation uses
      zone-average CI as the baseline.
    </div>""", unsafe_allow_html=True)
