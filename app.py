Here's your **completely redesigned CarbonWise UI** with **no text overlapping**, **better heading hierarchy**, and a **modern, attractive theme** that's visually appealing while maintaining all functionality:

---

```python
"""
CarbonWise ⚡ — Real-Time Grid Carbon Intensity Scheduler (v2.0)
==========================================================
✨ UI Improvements:
  • Completely redesigned theme with modern color palette
  • Proper heading hierarchy (h1 > h2 > h3)
  • Increased spacing between all elements (no overlaps)
  • Attractive card designs with depth and shadows
  • Consistent component styling
  • Responsive layout for all screen sizes
  • Professional typography with improved readability
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
# 0. App Config (MUST be first Streamlit call)
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
# 1. Constants & Lookups (Unchanged)
# ──────────────────────────────────────────────────────────────
STEP_MIN = 15
LOG_FILE = Path("logs/runs.jsonl")
CONFIG_FILE = Path("config/location.json")
DATA_DIR = Path("data")
EM_API_BASE = "https://api-access.electricitymaps.com/free-tier"

SOURCE_FACTORS = {"thermal": 820.0, "hydro": 24.0, "nuclear": 12.0, "res": 50.0}
CO2_EQUIV = {
    "🌳 Tree-days": 0.021,
    "🚗 km not driven": 0.192,
    "✈️ passenger-km": 0.255,
    "📱 phone charges": 0.00844,
}
HOUR_DEMAND = [
    0.62, 0.58, 0.55, 0.53, 0.54, 0.62, 0.78, 0.92, 1.08, 1.15, 1.10, 1.05,
    1.00, 0.97, 0.93, 0.94, 1.02, 1.18, 1.25, 1.22, 1.15, 1.05, 0.92, 0.75,
]
DOW_DEMAND = {0: 1.05, 1: 1.08, 2: 1.06, 3: 1.04, 4: 1.03, 5: 0.88, 6: 0.82}

GRID_ZONES = {
    "India": {
        "timezone": "Asia/Kolkata", "voltage": "230 V / 50 Hz",
        "zones": {
            "North India (NR)": {"lat": 28.6139, "lon": 77.2090, "ci_avg": 450},
            "South India (SR)": {"lat": 13.0827, "lon": 80.2707, "ci_avg": 380},
            "West India (WR)": {"lat": 19.0760, "lon": 72.8777, "ci_avg": 420},
            "East India (ER)": {"lat": 22.5726, "lon": 88.3639, "ci_avg": 480},
            "North-East (NER)": {"lat": 26.1445, "lon": 91.7362, "ci_avg": 350},
        },
    },
    "Europe": {
        "timezone": "Europe/Brussels", "voltage": "230 V / 50 Hz",
        "zones": {
            "Germany (DE)": {"lat": 51.1657, "lon": 10.4515, "ci_avg": 276},
            "France (FR)": {"lat": 46.2276, "lon": 2.2137, "ci_avg": 16},
            "UK (GB)": {"lat": 55.3781, "lon": -3.4360, "ci_avg": 106},
            "Netherlands (NL)": {"lat": 52.1326, "lon": 5.2913, "ci_avg": 209},
            "Spain (ES)": {"lat": 40.4637, "lon": -3.7492, "ci_avg": 89},
            "Italy (IT)": {"lat": 41.8719, "lon": 12.5674, "ci_avg": 202},
            "Nordics (NO/SE/FI)": {"lat": 60.4720, "lon": 8.4689, "ci_avg": 30},
        },
    },
    "United States": {
        "timezone": "America/New_York", "voltage": "120 V / 60 Hz",
        "zones": {
            "California (CAISO)": {"lat": 36.7783, "lon": -119.4179, "ci_avg": 200},
            "Texas (ERCOT)": {"lat": 31.9686, "lon": -99.9018, "ci_avg": 350},
            "New York (NYISO)": {"lat": 42.1657, "lon": -74.9481, "ci_avg": 250},
            "Midwest (MISO)": {"lat": 41.8780, "lon": -93.0977, "ci_avg": 400},
            "PJM (East)": {"lat": 39.8283, "lon": -77.5794, "ci_avg": 320},
            "Pacific Northwest": {"lat": 45.5152, "lon": -122.6784, "ci_avg": 100},
        },
    },
    "Asia Pacific": {
        "timezone": "Asia/Tokyo", "voltage": "100–240 V / 50–60 Hz",
        "zones": {
            "Australia (AUS)": {"lat": -25.2744, "lon": 133.7751, "ci_avg": 450},
            "Japan (JP)": {"lat": 36.2048, "lon": 138.2529, "ci_avg": 450},
            "Singapore (SG)": {"lat": 1.3521, "lon": 103.8198, "ci_avg": 367},
            "South Korea (KR)": {"lat": 35.9078, "lon": 127.7669, "ci_avg": 357},
        },
    },
}

APPLIANCES = {
    "Geyser / Water Heater": {"kw": 2.0, "dur": 30, "deadline": "11:00", "icon": "🚿"},
    "Washing Machine": {"kw": 0.5, "dur": 60, "deadline": "11:00", "icon": "👗"},
    "Iron Box": {"kw": 1.0, "dur": 30, "deadline": "11:00", "icon": "👔"},
    "Water Motor": {"kw": 0.75, "dur": 30, "deadline": "08:00", "icon": "💧"},
    "EV Charger 3.3 kW": {"kw": 3.3, "dur": 120, "deadline": "07:00", "icon": "⚡"},
    "EV Charger 7.0 kW": {"kw": 7.0, "dur": 120, "deadline": "07:00", "icon": "🚗"},
    "Air Conditioner": {"kw": 1.5, "dur": 60, "deadline": "23:00", "icon": "❄️"},
    "Induction Cooktop": {"kw": 1.8, "dur": 30, "deadline": "21:00", "icon": "🍳"},
    "Microwave": {"kw": 1.2, "dur": 15, "deadline": "21:00", "icon": "📡"},
    "Laptop Charging": {"kw": 0.06, "dur": 120, "deadline": "23:00", "icon": "💻"},
    "Custom": {"kw": 1.0, "dur": 30, "deadline": "11:00", "icon": "🔌"},
}

BADGES = [
    {"id": "first_save", "name": "First Save", "icon": "🌱", "desc": "Logged your first run", "threshold": 1},
    {"id": "green_week", "name": "Green Week", "icon": "🌿", "desc": "7-day scheduling streak", "threshold": 7},
    {"id": "ton_saver", "name": "Ton Saver", "icon": "🏆", "desc": "Saved 1 kg CO₂ total", "threshold": 1.0},
    {"id": "optimizer", "name": "Grid Optimizer", "icon": "⚡", "desc": "10 recommended runs logged", "threshold": 10},
    {"id": "eco_champion", "name": "Eco Champion", "icon": "🌍", "desc": "Saved 10 kg CO₂ total", "threshold": 10.0},
    {"id": "night_owl", "name": "Night Owl", "icon": "🦉", "desc": "Run scheduled 22:00–05:00", "threshold": 1},
    {"id": "solar_surfer", "name": "Solar Surfer", "icon": "☀️", "desc": "Run during 10:00–16:00", "threshold": 1},
    {"id": "early_bird", "name": "Early Bird", "icon": "🐦", "desc": "Run scheduled before 06:00", "threshold": 1},
]

# ──────────────────────────────────────────────────────────────
# 2. Helper Functions (Unchanged)
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
    vals = {"thermal": safe_float(thermal), "hydro": safe_float(hydro),
            "nuclear": safe_float(nuclear), "res": safe_float(res)}
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
# 3. DataEngine Class (Unchanged)
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
                noise = rng.normal(0, 0.035)
                multiplier = max(0.25, f + intra_wave + noise)
                ci = round(max(10.0, min(900.0, base_ci * multiplier)), 2)
                thermal = ci * 7.0 * max(0.3, f)
                hydro = base_ci * 1.8 * (1 - f * 0.25)
                nuclear = base_ci * 1.6
                solar_pk = base_ci * 2.5 if 10 <= h <= 16 else 0
                res = max(0.0, base_ci * 2.8 * (1 - f * 0.5) + solar_pk)
                rows.append({
                    "time": f"{h:02d}:{q:02d}",
                    "ci": ci,
                    "thermal_mw": round(thermal, 1),
                    "hydro_mw": round(hydro, 1),
                    "nuclear_mw": round(nuclear, 1),
                    "res_mw": round(res, 1),
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
            lambda r: compute_ci(r["thermal_mw"], r["hydro_mw"], r["nuclear_mw"], r["res_mw"]), axis=1
        )
        return df.sort_values("time").reset_index(drop=True)

    @staticmethod
    def full_day(raw: pd.DataFrame) -> pd.DataFrame:
        ci_map = dict(zip(raw["time"], raw["ci"]))
        mean_ci = safe_float(raw["ci"].mean(), 400)
        rows = [
            {
                "time": f"{h:02d}:{m:02d}",
                "ci": safe_float(ci_map.get(f"{h:02d}:{m:02d}", mean_ci), mean_ci),
                "color": ci_color(ci_map.get(f"{h:02d}:{m:02d}", mean_ci)),
                "label": ci_label(ci_map.get(f"{h:02d}:{m:02d}", mean_ci)),
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
        dur = ceil15(duration_min)
        nslot = dur // STEP_MIN
        now_m = hm_to_mins(now)
        dead_m = hm_to_mins(deadline)
        if dead_m <= now_m:
            dead_m += 1440
        times = full["time"].tolist()
        cis = full["ci"].tolist()
        wins = []
        for i in range(len(times) - nslot + 1):
            sh, sm = map(int, times[i].split(":"))
            start_m = sh * 60 + sm
            adj_start = start_m if start_m >= now_m else start_m + 1440
            end_m = adj_start + dur
            if adj_start < now_m:
                continue
            if end_m > dead_m:
                continue
            window_cis = [safe_float(cis[j]) for j in range(i, min(i + nslot, len(cis)))]
            avg = sum(window_cis) / len(window_cis)
            end_slot = mins_to_hm(adj_start + dur)
            wins.append({
                "start": times[i],
                "end": end_slot,
                "avg_ci": avg,
                "min_ci": min(window_cis),
                "max_ci": max(window_cis),
                "color": ci_color(avg),
                "label": ci_label(avg),
            })
        wins.sort(key=lambda x: x["avg_ci"])
        return wins[:top_k]

    @staticmethod
    def weekly_forecast(base_ci: float) -> pd.DataFrame:
        today = datetime.now()
        seed = int(today.strftime("%Y%m%d"))
        rng = np.random.default_rng(seed)
        rows = []
        for d in range(7):
            day = today + timedelta(days=d)
            f = DOW_DEMAND[day.weekday()]
            n = rng.normal(0, 0.05)
            rows.append({
                "date": day.strftime("%a %d %b"),
                "label": "Today" if d == 0 else ("Tomorrow" if d == 1 else day.strftime("%a")),
                "ci_night": round(max(10, base_ci * max(0.3, f * 0.65 + n)), 1),
                "ci_day": round(max(10, base_ci * max(0.5, f + n)), 1),
                "ci_peak": round(max(10, base_ci * max(0.7, f * 1.25 + n)), 1),
            })
            rows[-1]["avg"] = round((rows[-1]["ci_night"] + rows[-1]["ci_day"] + rows[-1]["ci_peak"]) / 3, 1)
        return pd.DataFrame(rows)

    @staticmethod
    def _nearest_zone_ci(lat: float, lon: float) -> float:
        best_ci = 400.0
        best_dist = 1e9
        for region in GRID_ZONES.values():
            for z in region["zones"].values():
                d = (z["lat"] - lat) ** 2 + (z["lon"] - lon) ** 2
                if d < best_dist:
                    best_dist = d
                    best_ci = z["ci_avg"]
        return best_ci

# ──────────────────────────────────────────────────────────────
# 4. Persistence Helpers (Unchanged)
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
    runs = len(df)
    rec = int((df.get("run_type", pd.Series(dtype=str)) == "recommended").sum())
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
    if runs >= 1: badges.append("first_save")
    if streak >= 7: badges.append("green_week")
    if total_co2 >= 1: badges.append("ton_saver")
    if rec >= 10: badges.append("optimizer")
    if total_co2 >= 10: badges.append("eco_champion")
    if "start_time" in df:
        hours = df["start_time"].dropna().apply(lambda t: int(str(t)[:2]) if len(str(t)) >= 2 else -1)
        if any((h >= 22 or h < 5) for h in hours): badges.append("night_owl")
        if any(10 <= h <= 16 for h in hours): badges.append("solar_surfer")
        if any(h < 6 for h in hours): badges.append("early_bird")
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
# 5. NEW MODERN CSS THEME (Completely Redesigned)
# ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ===== CSS Variables for Modern Theme ===== */
:root {
  --bg-primary: #0f172a;
  --bg-secondary: #1e293b;
  --bg-tertiary: #334155;
  --text-primary: #f8fafc;
  --text-secondary: #cbd5e1;
  --text-muted: #64748b;
  --accent-green: #10b981;
  --accent-blue: #3b82f6;
  --accent-purple: #8b5cf6;
  --accent-yellow: #f59e0b;
  --accent-red: #ef4444;
  --border-glow: rgba(16, 185, 129, 0.2);
  --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.2);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.4);
  --radius-sm: 0.375rem;
  --radius-md: 0.5rem;
  --radius-lg: 0.75rem;
  --radius-xl: 1rem;
  --radius-full: 9999px;
}

/* ===== Base Styles ===== */
*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html, body, [data-testid="stAppViewContainer"] {
  background-color: var(--bg-primary) !important;
  color: var(--text-primary) !important;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
  line-height: 1.6;
  min-height: 100vh;
}

[data-testid="stHeader"] {
  background-color: var(--bg-primary) !important;
  border-bottom: 1px solid var(--border-glow) !important;
}

/* Sidebar Styling */
[data-testid="stSidebar"] {
  background-color: var(--bg-secondary) !important;
  border-right: 1px solid var(--border-glow) !important;
  padding: 1.5rem !important;
}

/* ===== Typography ===== */
h1, h2, h3, h4, h5, h6 {
  font-family: 'Inter', sans-serif;
  font-weight: 700;
  line-height: 1.2;
  margin-bottom: 0.75rem;
  color: var(--text-primary);
}

h1 {
  font-size: 2.25rem;
  border-bottom: 2px solid var(--accent-green);
  padding-bottom: 0.75rem;
  margin-top: 1.5rem;
}

h2 {
  font-size: 1.875rem;
  border-bottom: 1px solid var(--border-glow);
  padding-bottom: 0.5rem;
  margin-top: 2rem;
  margin-bottom: 1.5rem;
}

h3 {
  font-size: 1.5rem;
  margin-top: 1.5rem;
  margin-bottom: 1rem;
}

h4 {
  font-size: 1.25rem;
  margin-top: 1.25rem;
  margin-bottom: 0.75rem;
}

p {
  color: var(--text-secondary) !important;
  margin-bottom: 1rem;
  line-height: 1.7;
}

code {
  background: var(--bg-tertiary);
  padding: 0.2rem 0.4rem;
  border-radius: var(--radius-sm);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.9em;
}

/* ===== Spacing Utilities ===== */
.st-emotion-cache-1v0mbdj {
  margin-top: 1.5rem !important;
  margin-bottom: 1.5rem !important;
}

.st-emotion-cache-1dg5y3q {
  margin-bottom: 1.5rem !important;
}

/* ===== Form Elements ===== */
.stTextInput, .stNumberInput, .stSelectbox, .stDateInput {
  background-color: var(--bg-tertiary) !important;
  border: 1px solid var(--border-glow) !important;
  border-radius: var(--radius-md) !important;
  color: var(--text-primary) !important;
  padding: 0.5rem !important;
}

.stTextInput input, .stNumberInput input, .stSelectbox select {
  background-color: transparent !important;
  color: var(--text-primary) !important;
}

/* ===== Buttons ===== */
.stButton > button {
  background: linear-gradient(135deg, var(--accent-green), #059669) !important;
  color: white !important;
  border: none !important;
  border-radius: var(--radius-md) !important;
  padding: 0.6rem 1.5rem !important;
  font-weight: 600 !important;
  font-size: 0.95rem !important;
  transition: all 0.3s ease !important;
  box-shadow: var(--shadow-md) !important;
  margin-top: 0.5rem !important;
}

.stButton > button:hover {
  transform: translateY(-2px) !important;
  box-shadow: var(--shadow-lg) !important;
}

.stButton > button:active {
  transform: translateY(0) !important;
}

/* ===== Tabs ===== */
.stTabs [data-baseweb="tab-list"] {
  gap: 0.5rem !important;
  background-color: var(--bg-secondary) !important;
  padding: 0.5rem !important;
  border-radius: var(--radius-lg) !important;
  border: 1px solid var(--border-glow) !important;
  margin-bottom: 1.5rem !important;
}

.stTabs [data-baseweb="tab"] {
  background-color: transparent !important;
  border-radius: var(--radius-md) !important;
  padding: 0.6rem 1.25rem !important;
  color: var(--text-secondary) !important;
  font-weight: 500 !important;
  transition: all 0.2s ease !important;
}

.stTabs [aria-selected="true"] {
  background: linear-gradient(135deg, var(--accent-green), #059669) !important;
  color: white !important;
}

/* ===== Alerts ===== */
.stAlert {
  border-radius: var(--radius-lg) !important;
  border-left: 4px solid !important;
  padding: 1rem 1.25rem !important;
  margin-bottom: 1.5rem !important;
  box-shadow: var(--shadow-md) !important;
}

/* ===== Metrics ===== */
.st-emotion-cache-1y445vx {
  background-color: var(--bg-secondary) !important;
  border: 1px solid var(--border-glow) !important;
  border-radius: var(--radius-lg) !important;
  padding: 1.25rem !important;
  margin-bottom: 1rem !important;
  box-shadow: var(--shadow-sm) !important;
}

/* ===== Dataframes ===== */
.stDataFrame {
  background-color: var(--bg-secondary) !important;
  border-radius: var(--radius-md) !important;
  overflow: hidden !important;
  border: 1px solid var(--border-glow) !important;
}

/* ===== Dividers ===== */
hr {
  border: none !important;
  border-top: 1px solid var(--border-glow) !important;
  margin: 2rem 0 !important;
}

/* ===== Plotly Charts ===== */
.js-plotly-plot {
  background-color: var(--bg-secondary) !important;
  border-radius: var(--radius-md) !important;
  box-shadow: var(--shadow-sm) !important;
}

/* ===== Custom Components ===== */
/* Hero Section */
.hero {
  background: linear-gradient(135deg, #1e3a5f, #0f172a);
  padding: 2.5rem;
  border-radius: var(--radius-xl);
  margin-bottom: 2rem;
  border: 1px solid var(--border-glow);
  box-shadow: var(--shadow-lg);
  position: relative;
  overflow: hidden;
}

.hero::before {
  content: '';
  position: absolute;
  top: -50%;
  right: -20%;
  width: 500px;
  height: 500px;
  background: radial-gradient(circle, rgba(16, 185, 129, 0.1), transparent 70%);
  border-radius: 50%;
}

.hero h1 {
  color: var(--accent-green) !important;
  font-size: 2.5rem !important;
  margin-bottom: 1rem !important;
  letter-spacing: -0.025em;
}

.hero p {
  color: var(--text-secondary) !important;
  font-size: 1.125rem !important;
  max-width: 700px;
  margin-bottom: 1.5rem !important;
}

.hero-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: center;
}

/* Pills */
.pill {
  padding: 0.5rem 1rem;
  border-radius: var(--radius-full);
  font-size: 0.875rem;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}

.pill-green {
  background: rgba(16, 185, 129, 0.15);
  color: var(--accent-green);
  border: 1px solid rgba(16, 185, 129, 0.3);
}

.pill-blue {
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent-blue);
  border: 1px solid rgba(59, 130, 246, 0.3);
}

.pill-amber {
  background: rgba(245, 158, 11, 0.15);
  color: var(--accent-yellow);
  border: 1px solid rgba(245, 158, 11, 0.3);
}

.pill-muted {
  color: var(--text-muted);
  font-size: 0.875rem;
}

/* KPI Cards */
.kpi {
  background: linear-gradient(135deg, var(--bg-secondary), var(--bg-tertiary));
  padding: 1.5rem;
  border-radius: var(--radius-lg);
  text-align: center;
  border: 1px solid var(--border-glow);
  min-height: 140px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  transition: all 0.3s ease;
  box-shadow: var(--shadow-sm);
  margin-bottom: 1rem;
}

.kpi:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  border-color: var(--accent-green);
}

.kpi-lbl {
  color: var(--text-muted);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-weight: 600;
  margin-bottom: 0.5rem;
}

.kpi-val {
  color: var(--text-primary);
  font-weight: 700;
  font-size: 1.75rem;
  line-height: 1.2;
  margin-bottom: 0.5rem;
}

.kpi-sub {
  color: var(--text-muted);
  font-size: 0.875rem;
}

/* Ticker */
.ticker {
  background: var(--bg-secondary);
  border: 1px solid var(--border-glow);
  border-radius: var(--radius-lg);
  padding: 1rem 1.5rem;
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
  margin-bottom: 1.5rem;
  box-shadow: var(--shadow-sm);
}

.ticker-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--accent-green);
  flex-shrink: 0;
  animation: pulse-dot 1.5s ease-in-out infinite;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.25); }
}

/* Rec Cards */
.rec {
  background: linear-gradient(135deg, var(--bg-secondary), var(--bg-tertiary));
  padding: 1.5rem;
  border-radius: var(--radius-lg);
  margin-bottom: 1.5rem;
  transition: all 0.3s ease;
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border-glow);
}

.rec:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  border-color: var(--accent-green);
}

/* Badge Cards */
.badge-card {
  background: var(--bg-secondary);
  border-radius: var(--radius-lg);
  padding: 1.25rem;
  text-align: center;
  border: 1px solid var(--border-glow);
  transition: all 0.3s ease;
  box-shadow: var(--shadow-sm);
  margin-bottom: 1rem;
}

.badge-card.earned {
  border-color: var(--accent-green);
  background: rgba(16, 185, 129, 0.05);
  box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2);
}

.badge-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-md);
}

/* Progress Bar */
.pb {
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--bg-tertiary);
  margin: 0.75rem 0;
  overflow: hidden;
}

.pb-fill {
  height: 100%;
  border-radius: var(--radius-full);
  background: linear-gradient(90deg, var(--accent-green), var(--accent-blue));
  transition: width 0.5s ease;
}

/* Live Time */
.live-time {
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid var(--border-glow);
  padding: 1rem;
  border-radius: var(--radius-md);
  color: var(--accent-green);
  font-weight: 700;
  text-align: center;
  font-size: 1.125rem;
  letter-spacing: 0.05em;
  box-shadow: var(--shadow-sm);
  margin-bottom: 1rem;
}

.tz-sub {
  color: var(--text-muted);
  font-size: 0.875rem;
  text-align: center;
  margin-top: 0.5rem;
}

/* Alerts */
.alert {
  padding: 1rem 1.25rem;
  border-radius: var(--radius-lg);
  margin-bottom: 1.5rem;
  box-shadow: var(--shadow-sm);
}

.alert-g {
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.35);
  border-left: 4px solid var(--accent-green);
}

.alert-r {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.35);
  border-left: 4px solid var(--accent-red);
}

.alert-y {
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.35);
  border-left: 4px solid var(--accent-yellow);
}

/* ===== Responsive Design ===== */
@media (max-width: 768px) {
  .hero h1 {
    font-size: 1.875rem !important;
  }
  .hero p {
    font-size: 1rem !important;
  }
  .kpi {
    min-height: 120px;
    padding: 1rem;
  }
  .kpi-val {
    font-size: 1.5rem;
  }
  .rec {
    padding: 1rem;
  }
  [data-testid="stSidebar"] {
    padding: 1rem !important;
  }
}
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# 6. Session State Initialization
# ──────────────────────────────────────────────────────────────
_SS_DEFAULTS = {
    "appliance": "Water Motor",
    "kw": 0.75,
    "duration": 30,
    "deadline": "08:00",
    "loc_mode": "Auto-Detect",
    "region": "India",
    "zone": "North India (NR)",
    "lat": 28.6139,
    "lon": 77.2090,
    "tz": "Asia/Kolkata",
    "data_source": "Automatic (API)",
    "api_token": "",
    "live_mode": True,
    "refresh_s": 300,
    "alert_enabled": True,
    "alert_thresh": 200,
    "daily_budget_kg": 1.0,
    "sel_window": 0,
    "upload_hash": "",
    "upload_df_json": "",
}

for _k, _v in _SS_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ──────────────────────────────────────────────────────────────
# 7. Auto-Refresh
# ──────────────────────────────────────────────────────────────
if st.session_state.live_mode:
    _ms = int(st.session_state.refresh_s) * 1000
    components.html(
        f"<script>setTimeout(()=>window.parent.location.reload(),{_ms});</script>",
        height=0,
    )

# ──────────────────────────────────────────────────────────────
# 8. SIDEBAR (Improved Spacing & Organization)
# ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Configuration")
    st.markdown("---")

    # Live Mode
    st.markdown("### 🔄 Live Mode")
    st.session_state.live_mode = st.toggle(
        "Auto-refresh", value=st.session_state.live_mode, key="_live"
    )
    if st.session_state.live_mode:
        _ri_map = {60: "1 min", 120: "2 min", 300: "5 min", 600: "10 min"}
        st.session_state.refresh_s = st.select_slider(
            "Refresh Interval",
            options=list(_ri_map.keys()),
            value=st.session_state.refresh_s,
            format_func=lambda x: _ri_map[x],
        )
        st.markdown(
            f'<div style="background:rgba(16,185,129,0.1);border:1px solid var(--border-glow);'
            f'border-radius:var(--radius-md);padding:0.75rem 1rem;margin-top:0.75rem;">'
            f'<span style="color:var(--accent-green);font-size:0.875rem;font-weight:600;">'
            f'🟢 Live — every {_ri_map[st.session_state.refresh_s]}</span></div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # Location
    st.markdown("### 📍 Location Settings")
    _LOC_MODES = ["Auto-Detect", "GPS (Browser)", "Manual Select", "Custom Coordinates"]
    st.session_state.loc_mode = st.radio(
        "Detection Method",
        _LOC_MODES,
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
                st.session_state.tz = _loc.get("timezone", "UTC")
                st.success(f"📍 {_loc['city']}, {_loc['country']}")
                st.rerun()
            else:
                st.error("Detection failed — try Manual Select.")
        st.markdown(
            f'<div style="background:var(--bg-tertiary);padding:0.75rem 1rem;border-radius:var(--radius-md);margin-top:0.75rem;">'
            f'<div style="color:var(--text-muted);font-size:0.75rem;">COORDINATES</div>'
            f'<div style="color:var(--accent-green);font-size:0.95rem;font-weight:600;">'
            f'{st.session_state.lat:.4f}, {st.session_state.lon:.4f}</div></div>',
            unsafe_allow_html=True,
        )

    elif st.session_state.loc_mode == "GPS (Browser)":
        components.html("""
<style>
html,body{margin:0;padding:0;background:transparent;font-family:sans-serif;}
#btn{width:100%;padding:0.75rem;background:var(--bg-tertiary);
     color:var(--accent-green);border:1.5px solid var(--border-glow);border-radius:var(--radius-md);
     cursor:pointer;font-weight:600;font-size:0.95rem;}
#btn:hover{background:rgba(16,185,129,0.1);}#btn:disabled{opacity:.6;}
#msg{color:var(--text-muted);font-size:0.875rem;margin-top:0.5rem;text-align:center;min-height:16px;}
.c{background:var(--bg-secondary);padding:0.75rem;border-radius:var(--radius-sm);margin-top:0.5rem;
   color:var(--accent-green);font-size:0.9rem;font-weight:600;display:none;}
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
        with _ga: _glat = st.number_input("Latitude", -90.0, 90.0, st.session_state.lat, 0.0001, "%.5f")
        with _gb: _glon = st.number_input("Longitude", -180.0, 180.0, st.session_state.lon, 0.0001, "%.5f")
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
        _zone = st.selectbox(
            "Zone", list(_zones.keys()),
            index=list(_zones.keys()).index(st.session_state.zone)
                  if st.session_state.zone in _zones else 0,
        )
        st.session_state.zone = _zone
        _zd = _zones[_zone]
        st.session_state.lat, st.session_state.lon = _zd["lat"], _zd["lon"]
        st.session_state.tz = GRID_ZONES[_reg]["timezone"]
        st.markdown(
            f'<div style="background:var(--bg-tertiary);padding:0.75rem 1rem;border-radius:var(--radius-md);margin-top:0.75rem;">'
            f'<div style="color:var(--text-muted);font-size:0.75rem;">ZONE AVG CI</div>'
            f'<div style="color:var(--accent-green);font-size:0.95rem;font-weight:600;">{_zd["ci_avg"]} gCO₂/kWh</div>'
            f'<div style="color:var(--text-muted);font-size:0.75rem;margin-top:0.25rem;">{GRID_ZONES[_reg]["voltage"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    else:  # Custom Coordinates
        _cc, _cd = st.columns(2)
        with _cc: st.session_state.lat = st.number_input("Latitude", -90.0, 90.0, st.session_state.lat, format="%.4f")
        with _cd: st.session_state.lon = st.number_input("Longitude", -180.0, 180.0, st.session_state.lon, format="%.4f")
        _tz_list = pytz.common_timezones
        st.session_state.tz = st.selectbox(
            "Timezone", _tz_list,
            index=_tz_list.index(st.session_state.tz) if st.session_state.tz in _tz_list else 0,
        )

    st.markdown("---")

    # Clock
    st.markdown("### 🕐 Current Time")
    st.markdown(f'<div class="live-time">🕐 {exact_time_str(st.session_state.tz)}</div>', unsafe_allow_html=True)
    _slot = slot_str(st.session_state.tz)
    st.markdown(f'<div class="tz-sub">{st.session_state.tz} &nbsp;|&nbsp; slot {to_12h(_slot)}</div>', unsafe_allow_html=True)

    st.markdown("---")

    # Data Source
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
        st.caption("Uses Electricity Maps API + real-time simulation")
        st.session_state.api_token = st.text_input(
            "API Token (optional)", type="password",
            value=st.session_state.api_token, placeholder="Free-tier token"
        )
        if st.button("🔄 Force Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    elif st.session_state.data_source == "Upload CSV":
        st.warning("Upload mode expects: time, thermal_mw, hydro_mw, nuclear_mw, res_mw")
        _upload_file = st.file_uploader("CSV File", type=["csv"], label_visibility="collapsed")

    else:
        st.caption("Built-in synthetic grid profile")

    st.markdown("---")

    # Appliance
    st.markdown("### 🔌 Appliance Settings")
    _prev = st.session_state.appliance
    st.session_state.appliance = st.selectbox(
        "Appliance Type", list(APPLIANCES.keys()),
        index=list(APPLIANCES.keys()).index(st.session_state.appliance),
        label_visibility="collapsed",
    )
    if st.session_state.appliance != _prev:
        _ap = APPLIANCES[st.session_state.appliance]
        st.session_state.kw = _ap["kw"]
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
        st.error("⚠️ Invalid time — use HH:MM (24-h format)")

    _avail = (hm_to_mins(st.session_state.deadline) - hm_to_mins(_slot)) % 1440
    st.info(f"⏱️ **{_avail} min** until deadline ({_avail // 60}h {_avail % 60}m)")

    if st.button("↩️ Reset to Defaults", use_container_width=True):
        _ap = APPLIANCES[st.session_state.appliance]
        st.session_state.kw = _ap["kw"]
        st.session_state.duration = _ap["dur"]
        st.session_state.deadline = _ap["deadline"]
        st.rerun()

    st.markdown("---")

    # Optimisation
    st.markdown("### ⚡ Optimization")
    _top_k = st.slider("Top windows to show", 3, 12, 5)

    st.markdown("---")

    # Alerts & Budget
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
# 9. Data Loading
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
                st.session_state.upload_hash = _file_hash
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

# Build derived datasets
_full = DataEngine.full_day(_raw)
_now_slot = slot_str(st.session_state.tz)
_now_12 = to_12h(_now_slot)
_dl_12 = to_12h(st.session_state.deadline)
_windows = DataEngine.windows(_full, st.session_state.duration, st.session_state.deadline, _now_slot, _top_k)
_cur_ci = DataEngine.ci_at(_full, _now_slot) or float(_full["ci"].mean())
_best = _windows[0] if _windows else None
_nrg = st.session_state.kw * (ceil15(st.session_state.duration) / 60)
_pot_pct = (((_cur_ci - _best["avg_ci"]) / _cur_ci) * 100) if (_best and _cur_ci > 0) else 0
_weekly = DataEngine.weekly_forecast(float(_full["ci"].mean()))
_log_df = read_log()
_stats = compute_stats(_log_df)

_loc_lbl = (
    st.session_state.zone if st.session_state.loc_mode == "Manual Select"
    else "GPS" if st.session_state.loc_mode == "GPS (Browser)"
    else f"{st.session_state.lat:.2f}°, {st.session_state.lon:.2f}°"
)

# ──────────────────────────────────────────────────────────────
# 10. HERO BANNER (Improved Design)
# ──────────────────────────────────────────────────────────────
_live_pill = (
    '<span class="pill pill-green"><span style="width:7px;height:7px;border-radius:50%;'
    'background:#10b981;display:inline-block;animation:pulse-dot 1.5s infinite;"></span> LIVE</span>'
    if st.session_state.live_mode else ""
)

st.markdown(f"""
<div class="hero">
  <h1>🌍 CarbonWise</h1>
  <p>Real-time carbon intensity scheduler — run appliances during the cleanest grid windows to shrink your CO₂ footprint automatically.</p>
  <div class="hero-pills">
    {_live_pill}
    <span class="pill pill-blue">⚡ Smart Scheduler</span>
    <span class="pill pill-amber">🏅 Gamified</span>
    <span class="pill-muted">🕐 {exact_time_str(st.session_state.tz)} &nbsp;·&nbsp; {_loc_lbl}</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# 11. Alert Banner
# ──────────────────────────────────────────────────────────────
if st.session_state.alert_enabled:
    if _cur_ci <= st.session_state.alert_thresh:
        st.markdown(
            f'<div class="alert alert-g">'
            f'<strong style="color:var(--accent-green);">🟢 GREEN GRID ALERT!</strong>'
            f'<span style="color:var(--text-secondary);"> CI is <strong style="color:var(--accent-green);">{_cur_ci:.0f}</strong> gCO₂/kWh'
            f' — below your {st.session_state.alert_thresh} g threshold. Great time to run appliances!</span></div>',
            unsafe_allow_html=True,
        )
    elif _cur_ci > 400:
        st.markdown(
            f'<div class="alert alert-r">'
            f'<strong style="color:var(--accent-red);">🔴 HIGH CARBON ALERT</strong>'
            f'<span style="color:var(--text-secondary);"> CI is <strong style="color:var(--accent-red);">{_cur_ci:.0f}</strong> gCO₂/kWh'
            f' — consider delaying non-urgent appliances.</span></div>',
            unsafe_allow_html=True,
        )

# ──────────────────────────────────────────────────────────────
# 12. Live Ticker
# ──────────────────────────────────────────────────────────────
_trend = "↑" if _cur_ci > float(_full["ci"].mean()) else "↓"
_tc = ci_color(_cur_ci)

st.markdown(f"""
<div class="ticker">
  <div class="ticker-dot"></div>
  <span style="color:var(--text-muted);font-size:0.875rem;font-weight:600;white-space:nowrap;">LIVE GRID</span>
  <span style="color:{_tc};font-weight:700;font-size:1rem;">
    {ci_emoji(_cur_ci)} {_cur_ci:.0f} gCO₂/kWh {_trend}
  </span>
  <span style="color:var(--text-muted);">·</span>
  <span style="color:var(--accent-blue);font-size:0.875rem;">Best: {to_12h(_best["start"]) if _best else "--"}</span>
  <span style="color:var(--text-muted);">·</span>
  <span style="color:var(--accent-yellow);font-size:0.875rem;">🔥 {_stats["streak"]}-day streak</span>
  <span style="color:var(--text-muted);">·</span>
  <span style="color:var(--accent-green);font-size:0.875rem;">Saved {_stats["co2"]:.3f} kg CO₂</span>
  <span style="color:var(--text-muted);">·</span>
  <span style="color:var(--text-secondary);font-size:0.875rem;">{st.session_state.data_source}</span>
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# 13. KPI Row (Better Spacing)
# ──────────────────────────────────────────────────────────────
st.markdown("## 📊 Session Overview")
_k = st.columns(6)

def _kpi(col, label, value, sub, border="#10b981", val_color="#10b981"):
    col.markdown(
        f'<div class="kpi" style="border-left:4px solid {border};">'
        f'<div class="kpi-lbl">{label}</div>'
        f'<div class="kpi-val" style="color:{val_color};">{value}</div>'
        f'<div class="kpi-sub">{sub}</div></div>',
        unsafe_allow_html=True,
    )

_kpi(_k[0], "🌍 Location", _loc_lbl[:22], st.session_state.tz)
_kpi(_k[1], "🕐 Current Time", _now_12, "🟢 Live" if st.session_state.live_mode else "Static")
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

st.markdown("---")

# ──────────────────────────────────────────────────────────────
# 14. MAIN TABS (Better Organization)
# ──────────────────────────────────────────────────────────────
T1, T2, T3, T4, T5, T6, T7 = st.tabs([
    "📈 Dashboard", "🎯 Smart Advisor", "🌤️ Weekly Forecast",
    "📊 Analytics", "🏅 Achievements", "📝 Logger", "🔍 Data"
])

# ===== TAB 1 - Dashboard =====
with T1:
    st.markdown("## 📊 Real-Time Dashboard")
    col_main, col_side = st.columns([2, 1])

    with col_main:
        st.markdown("### 📉 Carbon Intensity Gauge")
        fig_g = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=_cur_ci,
            delta={"reference": float(_full["ci"].mean()), "valueformat": ".0f", "suffix": " avg"},
            title={"text": "Current CI (gCO₂/kWh)", "font": {"color": "#cbd5e1", "size": 14}},
            number={"font": {"color": ci_color(_cur_ci), "size": 40}},
            gauge={
                "axis": {"range": [0, 700], "tickcolor": "#64748b", "tickfont": {"color": "#64748b", "size": 11}},
                "bar": {"color": ci_color(_cur_ci), "thickness": 0.25},
                "bgcolor": "#1e293b", "borderwidth": 0,
                "steps": [
                    {"range": [0, 200], "color": "rgba(16,185,129,0.1)"},
                    {"range": [200, 400], "color": "rgba(245,158,11,0.1)"},
                    {"range": [400, 700], "color": "rgba(239,68,68,0.1)"},
                ],
                "threshold": {"line": {"color": "#10b981", "width": 2}, "thickness": 0.75, "value": _cur_ci},
            },
        ))
        fig_g.update_layout(
            paper_bgcolor="#1e293b", font={"color": "#cbd5e1"},
            height=220, margin={"l": 30, "r": 30, "t": 40, "b": 20},
        )
        st.plotly_chart(fig_g, use_container_width=True)

        st.markdown("### 📈 24-Hour CI Trend")
        fig_l = go.Figure()
        fig_l.add_trace(go.Scatter(
            x=_full["time"], y=_full["ci"],
            fill="tozeroy", fillcolor="rgba(16,185,129,0.1)",
            line={"color": "#10b981", "width": 2.5},
            hovertemplate="<b>%{x}</b><br>CI: %{y:.1f} gCO₂/kWh<extra></extra>",
        ))
        fig_l.add_hrect(y0=0, y1=200, fillcolor="#10b981", opacity=0.05, line_width=0)
        fig_l.add_hrect(y0=200, y1=400, fillcolor="#f59e0b", opacity=0.05, line_width=0)
        fig_l.add_hrect(y0=400, y1=900, fillcolor="#ef4444", opacity=0.05, line_width=0)
        for wi, w in enumerate((_windows or [])[:3]):
            fig_l.add_vrect(
                x0=w["start"], x1=w["end"],
                fillcolor=w["color"], opacity=0.2,
                line_width=2 if wi == 0 else 1, line_color=w["color"], layer="below",
            )
        _ymax = float(_full["ci"].max()) * 1.12
        fig_l.add_vline(x=_now_slot, line_dash="dash", line_color="#10b981", line_width=1.8)
        fig_l.add_vline(x=st.session_state.deadline, line_dash="dash", line_color="#ef4444", line_width=1.8)
        fig_l.add_annotation(x=_now_slot, y=_ymax, text="NOW",
                              showarrow=False, font={"color": "#10b981", "size": 11},
                              bgcolor="rgba(15,23,42,0.8)")
        fig_l.add_annotation(x=st.session_state.deadline, y=_ymax, text="DEADLINE",
                              showarrow=False, font={"color": "#ef4444", "size": 11},
                              bgcolor="rgba(15,23,42,0.8)")
        fig_l.add_hline(y=float(_full["ci"].mean()), line_dash="dot", line_color="#3b82f6",
                        line_width=1, annotation_text="Daily avg",
                        annotation_font_color="#3b82f6")
        fig_l.update_layout(
            plot_bgcolor="#1e293b", paper_bgcolor="#1e293b", font={"color": "#cbd5e1"},
            xaxis={"gridcolor": "#334155", "title": "Time of Day", "tickangle": -45, "nticks": 13},
            yaxis={"gridcolor": "#334155", "title": "gCO₂/kWh", "range": [0, _ymax * 1.1]},
            height=400, margin={"l": 55, "r": 30, "t": 30, "b": 60}, showlegend=False,
        )
        st.plotly_chart(fig_l, use_container_width=True)

    with col_side:
        st.markdown("### 🔋 Grid Mix")
        _last = _raw.iloc[-1]
        _vals = [safe_float(_last.get(c, 0)) for c in ["thermal_mw", "hydro_mw", "nuclear_mw", "res_mw"]]
        _tot = sum(_vals) or 1
        fig_p = go.Figure(data=[go.Pie(
            labels=["Thermal", "Hydro", "Nuclear", "Renewable"], values=_vals,
            hole=0.65, marker_colors=["#ef4444", "#3b82f6", "#8b5cf6", "#10b981"],
            textinfo="label+percent", textfont={"color": "white", "size": 10},
        )])
        fig_p.update_layout(
            paper_bgcolor="#1e293b", height=280,
            showlegend=False, margin={"t": 20, "b": 20, "l": 20, "r": 20},
            annotations=[{"text": f"<b>{_tot/1000:.1f}</b><br>GW",
                           "x": 0.5, "y": 0.5, "font": {"size": 18, "color": "#f8fafc"}, "showarrow": False}],
        )
        st.plotly_chart(fig_p, use_container_width=True)

        st.markdown("### 🌿 CO₂ Equivalents")
        _co2_now = co2_kg(_nrg, _cur_ci)
        _co2_opt = co2_kg(_nrg, _best["avg_ci"]) if _best else _co2_now
        _saved = max(0.0, _co2_now - _co2_opt)
        st.markdown(
            f'<div style="background:#1e293b;border-radius:var(--radius-md);padding:1rem;border:1px solid var(--border-glow);">'
            f'<div style="color:var(--text-muted);font-size:0.8rem;margin-bottom:0.5rem;">Running now: {_co2_now:.3f} kg CO₂</div>'
            f'<div style="color:var(--accent-green);font-size:0.9rem;font-weight:600;margin-bottom:0.5rem;">Optimising saves:</div>',
            unsafe_allow_html=True,
        )
        for lbl, val in co2_equivalents(_saved).items():
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;padding:0.5rem 0;'
                f'border-bottom:1px solid #334155;font-size:0.875rem;">'
                f'<span style="color:var(--text-secondary);">{lbl}</span>'
                f'<span style="color:var(--text-primary);font-weight:600;">{val:.2f}</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

# ===== TAB 2 - Smart Advisor =====
with T2:
    st.markdown("## 🎯 Optimal Scheduling Advisor")
    st.markdown(f"### Top {_top_k} Windows · {_now_12} → {_dl_12}")

    if not _windows:
        st.warning("⚠️ No valid windows found — extend the deadline or reduce duration.")
        st.info(f"Debug: now={_now_12} · deadline={_dl_12} · duration={ceil15(st.session_state.duration)} min")
    else:
        for _ri in range(0, len(_windows), 3):
            _row_wins = _windows[_ri : _ri + 3]
            _cols = st.columns(len(_row_wins))
            for _ci2, (_col, _w) in enumerate(zip(_cols, _row_wins)):
                _gi = _ri + _ci2
                _rank = _gi + 1
                _is_best = _gi == 0
                _co2w = co2_kg(_nrg, _w["avg_ci"])
                _co2n = co2_kg(_nrg, _cur_ci)
                _sav = max(0.0, _co2n - _co2w)
                _ppct = (_sav / _co2n * 100) if _co2n > 0 else 0
                _conf = max(60, min(99, 100 - int((_w["max_ci"] - _w["min_ci"]) / 5)))
                _eq_t = _sav / CO2_EQUIV["🌳 Tree-days"]
                _eq_k = _sav / CO2_EQUIV["🚗 km not driven"]
                _bdr = "2px solid #10b981" if _is_best else "1px solid #334155"
                with _col:
                    st.markdown(f"""
<div class="rec" style="border:{_bdr};">
  <div style="text-align:center;margin-bottom:0.75rem;">
    <span style="background:{'#10b981' if _is_best else '#334155'};
          color:{'#0f172a' if _is_best else '#cbd5e1'};
          padding:0.3rem 0.8rem;border-radius:var(--radius-sm);font-size:0.75rem;font-weight:700;">
      {'🥇 BEST' if _rank==1 else f'#{_rank}'}
    </span>
    <span style="background:rgba(16,185,129,0.1);color:#10b981;
          padding:0.2rem 0.6rem;border-radius:var(--radius-sm);font-size:0.65rem;font-weight:600;margin-left:0.5rem;">
      {_conf}% conf.
    </span>
  </div>
  <div style="font-size:1.1rem;font-weight:700;color:var(--text-primary);text-align:center;margin-bottom:0.75rem;">
    {to_12h(_w['start'])} — {to_12h(_w['end'])}
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;margin-bottom:0.75rem;">
    <div style="background:#1e293b;padding:0.6rem;border-radius:var(--radius-sm);text-align:center;">
      <div style="color:var(--text-muted);font-size:0.65rem;margin-bottom:0.2rem;">CI avg</div>
      <div style="color:{_w['color']};font-size:1rem;font-weight:700;">{_w['avg_ci']:.0f}</div>
    </div>
    <div style="background:#1e293b;padding:0.6rem;border-radius:var(--radius-sm);text-align:center;">
      <div style="color:var(--text-muted);font-size:0.65rem;margin-bottom:0.2rem;">CO₂</div>
      <div style="color:var(--text-primary);font-size:1rem;font-weight:700;">{_co2w:.3f} kg</div>
    </div>
  </div>
  <div style="background:rgba(16,185,129,0.08);padding:0.5rem;border-radius:var(--radius-sm);
       border:1px solid rgba(16,185,129,0.2);margin-bottom:0.5rem;">
    <span style="color:#10b981;font-size:0.85rem;font-weight:600;">
      💰 Save {_sav:.3f} kg ({_ppct:.0f}%)
    </span>
  </div>
  <div style="font-size:0.75rem;color:var(--text-muted);text-align:center;">
    ≈ {_eq_t:.1f} tree-days · {_eq_k:.1f} km not driven
  </div>
</div>""", unsafe_allow_html=True)
                    if st.button("✅ Select", key=f"sel_{_gi}", use_container_width=True):
                        st.session_state.sel_window = _gi
                        st.success(f"Selected: {to_12h(_w['start'])} – {to_12h(_w['end'])}")

    st.markdown("---")
    st.markdown("### 🗓️ Today's CI Heatmap")
    _hdata = _full["ci"].values.reshape(6, 16) if len(_full) == 96 else np.tile(_full["ci"].values[:1], (6, 16))
    fig_h = go.Figure(data=go.Heatmap(
        z=_hdata,
        colorscale=[[0, "#10b981"], [0.4, "#f59e0b"], [1, "#ef4444"]],
        showscale=True,
        colorbar={"title": "gCO₂/kWh", "tickfont": {"color": "#94a3b8"}},
        hovertemplate="CI: %{z:.1f} gCO₂/kWh<extra></extra>",
    ))
    fig_h.update_layout(
        paper_bgcolor="#1e293b", height=200,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
        xaxis={"showticklabels": False}, yaxis={"showticklabels": False},
    )
    st.plotly_chart(fig_h, use_container_width=True)

# ===== TAB 3 - Weekly Forecast =====
with T3:
    st.markdown("## 🌤️ 7-Day Grid Forecast")
    st.caption("Simulated forecast based on day-of-week demand patterns + current grid conditions")

    fig_w = go.Figure()
    fig_w.add_trace(go.Bar(name="Night", x=_weekly["date"], y=_weekly["ci_night"],
                           marker_color="#10b981", opacity=0.8))
    fig_w.add_trace(go.Bar(name="Day", x=_weekly["date"], y=_weekly["ci_day"],
                           marker_color="#3b82f6", opacity=0.8))
    fig_w.add_trace(go.Bar(name="Peak", x=_weekly["date"], y=_weekly["ci_peak"],
                           marker_color="#ef4444", opacity=0.8))
    fig_w.add_trace(go.Scatter(
        name="Daily avg", x=_weekly["date"], y=_weekly["avg"],
        mode="lines+markers", line={"color": "#f59e0b", "width": 2, "dash": "dot"},
        marker={"size": 7},
    ))
    fig_w.update_layout(
        barmode="group", plot_bgcolor="#1e293b", paper_bgcolor="#1e293b",
        font={"color": "#cbd5e1"}, height=380,
        legend={"orientation": "h", "y": 1.05, "x": 0.5, "xanchor": "center"},
        xaxis={"gridcolor": "#334155"},
        yaxis={"gridcolor": "#334155", "title": "gCO₂/kWh"},
        margin={"l": 50, "r": 30, "t": 60, "b": 40},
    )
    st.plotly_chart(fig_w, use_container_width=True)

    st.markdown("### 📅 Daily Outlook")
    _dc = st.columns(7)
    for _di, (_dcol, _dr) in enumerate(zip(_dc, _weekly.itertuples())):
        with _dcol:
            _best_p = (
                "Night" if _dr.ci_night <= _dr.ci_day and _dr.ci_night <= _dr.ci_peak
                else "Day" if _dr.ci_day <= _dr.ci_peak
                else "Peak"
            )
            _icon_d = "🌙" if _best_p == "Night" else "☀️" if _best_p == "Day" else "⚡"
            _bdr_d = "2px solid #10b981" if _di == 0 else "1px solid #334155"
            st.markdown(f"""
<div style="background:#1e293b;border-radius:var(--radius-md);padding:0.75rem;
     text-align:center;border:{_bdr_d};box-shadow:var(--shadow-sm);">
  <div style="color:var(--text-muted);font-size:0.7rem;font-weight:600;">{_dr.label}</div>
  <div style="font-size:1.2rem;margin:0.4rem 0;">{_icon_d}</div>
  <div style="color:{ci_color(_dr.avg)};font-size:1rem;font-weight:700;">{_dr.avg:.0f}</div>
  <div style="color:var(--text-muted);font-size:0.65rem;">gCO₂/kWh</div>
  <div style="background:rgba(16,185,129,0.1);border-radius:var(--radius-sm);
       padding:0.2rem 0.4rem;margin-top:0.4rem;font-size:0.6rem;color:#10b981;">
    Best: {_best_p}
  </div>
</div>""", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 🗺️ Regional Comparison")
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
        plot_bgcolor="#1e293b", paper_bgcolor="#1e293b", font={"color": "#cbd5e1"},
        height=580, margin={"l": 190, "r": 60, "t": 30, "b": 40},
        xaxis={"gridcolor": "#334155", "title": "Avg CI (gCO₂/kWh)"},
        yaxis={"gridcolor": "#334155"},
    )
    st.plotly_chart(fig_reg, use_container_width=True)

# ===== TAB 4 - Analytics =====
with T4:
    st.markdown("## 📊 Impact Analytics")

    _co2_now = co2_kg(_nrg, _cur_ci)
    _co2_opt = co2_kg(_nrg, _best["avg_ci"]) if _best else _co2_now
    _proj = max(0.0, _co2_now - _co2_opt)
    _proj_p = (_proj / _co2_now * 100) if _co2_now > 0 else 0

    _ac, _bc = st.columns([3, 2])
    with _ac:
        fig_c = go.Figure()
        fig_c.add_trace(go.Bar(
            name="Baseline (Now)", x=["CO₂ Emissions"], y=[_co2_now],
            marker_color="#ef4444", text=[f"{_co2_now:.3f} kg"], textposition="outside", width=0.28,
        ))
        fig_c.add_trace(go.Bar(
            name="Optimised", x=["CO₂ Emissions"], y=[_co2_opt],
            marker_color="#10b981", text=[f"{_co2_opt:.3f} kg"], textposition="outside", width=0.28,
        ))
        fig_c.update_layout(
            barmode="group", plot_bgcolor="#1e293b", paper_bgcolor="#1e293b",
            font={"color": "#cbd5e1"}, height=320,
            legend={"orientation": "h", "y": 1.05, "x": 0.5, "xanchor": "center"},
            yaxis={"title": "CO₂ (kg)", "gridcolor": "#334155",
                   "range": [0, max(_co2_now, _co2_opt) * 1.45]},
            margin={"l": 50, "r": 30, "t": 60, "b": 40},
            title={"text": f"💰 Potential saving: {_proj:.3f} kg CO₂ ({_proj_p:.1f}%)",
                   "font": {"size": 14, "color": "#10b981"}},
        )
        st.plotly_chart(fig_c, use_container_width=True)

    with _bc:
        st.markdown("### 💰 Daily CO₂ Budget")
        _today_str = datetime.now().strftime("%Y-%m-%d")
        _today_co2 = 0.0
        if not _log_df.empty and "date" in _log_df and "co2_kg" in _log_df:
            _today_co2 = float(_log_df[_log_df["date"].astype(str) == _today_str]["co2_kg"].sum())
        _budget = st.session_state.daily_budget_kg
        _pct_used = min(100.0, _today_co2 / max(_budget, 1e-6) * 100)
        _remaining = max(0.0, _budget - _today_co2)
        _bar_clr = "#10b981" if _pct_used < 60 else "#f59e0b" if _pct_used < 90 else "#ef4444"
        st.markdown(f"""
<div style="background:#1e293b;border-radius:var(--radius-md);padding:1.25rem;border:1px solid var(--border-glow);box-shadow:var(--shadow-sm);">
  <div style="display:flex;justify-content:space-between;margin-bottom:0.5rem;">
    <span style="color:var(--text-muted);font-size:0.85rem;">Daily Budget</span>
    <span style="color:var(--text-primary);font-size:0.95rem;font-weight:600;">{_budget:.1f} kg CO₂</span>
  </div>
  <div class="pb"><div class="pb-fill" style="width:{_pct_used:.0f}%;background:{_bar_clr};"></div></div>
  <div style="display:flex;justify-content:space-between;margin-top:0.5rem;">
    <span style="color:{_bar_clr};font-size:0.85rem;font-weight:600;">Used {_today_co2:.3f} kg</span>
    <span style="color:var(--text-muted);font-size:0.85rem;">Left {_remaining:.3f} kg</span>
  </div>
  <div style="margin-top:1rem;padding-top:1rem;border-top:1px solid #334155;">
    <div style="color:var(--text-muted);font-size:0.75rem;margin-bottom:0.5rem;">This session:</div>
    <div style="display:flex;justify-content:space-between;">
      <span style="color:#ef4444;font-size:0.85rem;">Now {_co2_now:.3f} kg</span>
      <span style="color:#10b981;font-size:0.85rem;">Optimal {_co2_opt:.3f} kg</span>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

        st.markdown(f"### 🌍 {_proj:.3f} kg Savings Equals…")
        for lbl, val in co2_equivalents(_proj).items():
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:0.75rem;padding:0.6rem 0;'
                f'border-bottom:1px solid #334155;">'
                f'<span style="color:var(--text-secondary);font-size:0.9rem;flex:1;">{lbl}</span>'
                f'<span style="color:var(--accent-green);font-weight:700;font-size:0.95rem;">{val:.2f}</span></div>',
                unsafe_allow_html=True,
            )

    _m1, _m2, _m3, _m4 = st.columns(4)
    _m1.metric("🔴 Baseline", f"{_co2_now:.3f} kg")
    _m2.metric("🟢 Optimised", f"{_co2_opt:.3f} kg")
    _m3.metric("💰 CO₂ Saved", f"{_proj:.3f} kg", delta=f"-{_proj_p:.1f}%", delta_color="inverse")
    _m4.metric("⚡ Energy", f"{_nrg:.3f} kWh")

    if not _log_df.empty and "timestamp" in _log_df and "co2_kg" in _log_df:
        st.markdown("---")
        st.markdown("### 📈 Historical CO₂ Savings")
        _ldf = _log_df.copy()
        _ldf["ts"] = pd.to_datetime(_ldf["timestamp"], errors="coerce")
        _ldf = _ldf.dropna(subset=["ts"]).sort_values("ts")
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(
            x=_ldf["ts"], y=_ldf["co2_kg"].cumsum(),
            mode="lines+markers", line={"color": "#10b981", "width": 2},
            fill="tozeroy", fillcolor="rgba(16,185,129,0.1)",
        ))
        fig_hist.update_layout(
            plot_bgcolor="#1e293b", paper_bgcolor="#1e293b", font={"color": "#cbd5e1"},
            height=250, margin={"l": 50, "r": 30, "t": 30, "b": 40},
            xaxis={"gridcolor": "#334155"},
            yaxis={"gridcolor": "#334155", "title": "kg CO₂ (cumulative)"},
        )
        st.plotly_chart(fig_hist, use_container_width=True)
        st.markdown("### 📋 Run History")
        st.dataframe(
            _log_df.sort_values("timestamp", ascending=False),
            use_container_width=True, hide_index=True,
        )

# ===== TAB 5 - Achievements =====
with T5:
    st.markdown("## 🏅 Achievements & Eco-Milestones")

    _s1, _s2, _s3, _s4 = st.columns(4)
    _kpi(_s1, "Total Runs", str(_stats["runs"]), f'{_stats["rec"]} recommended')
    _kpi(_s2, "CO₂ Tracked", f'{_stats["co2"]:.3f}', "kg CO₂ total", border="#ef4444", val_color="#ef4444")
    _kpi(_s3, "🔥 Streak", str(_stats["streak"]), "consecutive days", border="#f59e0b", val_color="#f59e0b")
    _kpi(_s4, "⚡ Energy Used", f'{_stats["kwh"]:.2f}', "kWh total", border="#8b5cf6", val_color="#8b5cf6")

    st.markdown("---")
    st.markdown("## 🏆 Your Badges")
    _bc_cols = st.columns(4)
    for _bi, _bdg in enumerate(BADGES):
        with _bc_cols[_bi % 4]:
            _earned = _bdg["id"] in _stats["badges"]
            st.markdown(f"""
<div class="badge-card {'earned' if _earned else ''}" style="opacity:{'1' if _earned else '0.4'};">
  <div style="font-size:2rem;">{_bdg['icon']}</div>
  <div style="color:{'#10b981' if _earned else '#94a3b8'};font-weight:600;font-size:0.85rem;margin-top:0.5rem;">
    {_bdg['name']}
  </div>
  <div style="color:var(--text-muted);font-size:0.7rem;margin-top:0.4rem;">{_bdg['desc']}</div>
  <div style="margin-top:0.5rem;">
    {'<span style="color:#10b981;font-size:0.7rem;font-weight:700;">✓ EARNED</span>'
     if _earned else
     '<span style="color:var(--text-muted);font-size:0.7rem;">Locked</span>'}
  </div>
</div>""", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("## 💡 Your Impact Summary")
    _imp1, _imp2, _imp3 = st.columns(3)
    def _impact_card(col, icon, val, label, clr):
        col.markdown(f"""
<div style="background:#1e293b;border-radius:var(--radius-lg);padding:1.5rem;text-align:center;border:1px solid var(--border-glow);box-shadow:var(--shadow-sm);">
  <div style="font-size:2.5rem;">{icon}</div>
  <div style="color:{clr};font-size:1.6rem;font-weight:700;margin:0.5rem 0;">{val}</div>
  <div style="color:var(--text-muted);font-size:0.85rem;">{label}</div>
</div>""", unsafe_allow_html=True)

    _impact_card(_imp1, "🌳",
                 f'{_stats["co2"] / CO2_EQUIV["🌳 Tree-days"]:.1f}',
                 "tree-days of CO₂ absorption", "#10b981")
    _impact_card(_imp2, "🚗",
                 f'{_stats["co2"] / CO2_EQUIV["🚗 km not driven"]:.1f}',
                 "km of driving avoided", "#3b82f6")
    _impact_card(_imp3, "📱",
                 f'{_stats["co2"] / CO2_EQUIV["📱 phone charges"]:.0f}',
                 "phone charges equivalent", "#8b5cf6")

    st.markdown("---")
    st.markdown("## 💡 Smart Tips")
    _min_t = _full.loc[_full["ci"].idxmin(), "time"]
    _max_t = _full.loc[_full["ci"].idxmax(), "time"]
    _avg_c = float(_full["ci"].mean())
    for _tip, _clr in [
        (f"🌙 **Best time today: {to_12h(_min_t)}** — grid is cleanest then.", "#10b981"),
        (f"🔥 **Avoid {to_12h(_max_t)}** — dirtiest grid of the day.", "#ef4444"),
        (f"📊 **Today's avg CI: {_avg_c:.0f} gCO₂/kWh** — "
         f"{'below' if _avg_c < 300 else 'above'} the 300 g benchmark.", "#f59e0b"),
        ("🔄 Enable **Live Mode** in the sidebar to keep data fresh automatically.", "#3b82f6"),
        ("🏅 **Log your runs** to build your streak and unlock badges!", "#8b5cf6"),
    ]:
        st.markdown(
            f'<div style="background:#1e293b;border-left:3px solid {_clr};border-radius:var(--radius-sm);'
            f'padding:1rem 1.25rem;margin-bottom:0.75rem;color:var(--text-secondary);font-size:0.95rem;box-shadow:var(--shadow-sm);">{_tip}</div>',
            unsafe_allow_html=True,
        )

# ===== TAB 6 - Logger =====
with T6:
    st.markdown("## 📝 Log Appliance Run")
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
            _f_dur = st.number_input("Duration (min)", 15,
                                       value=ceil15(int(st.session_state.duration)), step=15)
        with _fc:
            _f_app = st.selectbox("Appliance", list(APPLIANCES.keys()))
            _f_notes = st.text_input("Notes", value=f"Zone: {_loc_lbl}")

        st.markdown("### 📊 Meter Readings")
        _m_a, _m_b = st.columns(2)
        with _m_a: _mb = st.number_input("Before (kWh)", min_value=0.0, format="%.3f")
        with _m_b: _ma = st.number_input("After (kWh)", min_value=0.0, format="%.3f")
        _submit = st.form_submit_button("💾 Save Run", use_container_width=True)

    if _submit:
        _kwh_used = _ma - _mb
        if _kwh_used <= 0:
            st.error("❌ 'After' reading must exceed 'Before'.")
        else:
            _ci_run = DataEngine.ci_at(_full, _f_start) or float(_full["ci"].mean())
            _et = mins_to_hm(hm_to_mins(_f_start) + ceil15(int(_f_dur)))
            append_log({
                "timestamp": datetime.now().isoformat(),
                "date": str(_f_date),
                "run_type": _f_type,
                "appliance": _f_app,
                "start_time": _f_start,
                "end_time": _et,
                "kwh_used": round(float(_kwh_used), 4),
                "avg_ci_g_per_kwh": round(float(_ci_run), 2),
                "co2_kg": round(co2_kg(_kwh_used, _ci_run), 4),
                "location": _loc_lbl,
                "timezone": st.session_state.tz,
                "notes": _f_notes,
            })
            read_log.clear()
            st.success(
                f"✅ Logged — {_kwh_used:.3f} kWh · "
                f"{co2_kg(_kwh_used, _ci_run):.4f} kg CO₂"
            )
            if _f_type == "recommended":
                st.balloons()

# ===== TAB 7 - Data =====
with T7:
    st.markdown("## 🔍 Data & Configuration")

    with st.expander("📍 Current Configuration", expanded=False):
        st.json({
            "Mode": st.session_state.loc_mode,
            "Latitude": st.session_state.lat,
            "Longitude": st.session_state.lon,
            "Timezone": st.session_state.tz,
            "Data Source": st.session_state.data_source,
            "Live Mode": st.session_state.live_mode,
            "Refresh (s)": st.session_state.refresh_s,
            "Alert Enabled": st.session_state.alert_enabled,
            "Alert Threshold": st.session_state.alert_thresh,
            "Daily Budget (kg)": st.session_state.daily_budget_kg,
        })
        if st.button("💾 Save Config"):
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps({"lat": st.session_state.lat,
                             "lon": st.session_state.lon,
                             "tz": st.session_state.tz}, indent=2),
                encoding="utf-8",
            )
            st.success("✅ Saved to config/location.json")

    st.markdown("---")
    st.markdown("### 📊 Raw Grid Data")
    _cols_show = [c for c in ["time","thermal_mw","hydro_mw","nuclear_mw","res_mw","ci"] if c in _raw.columns]
    st.dataframe(
        _raw[_cols_show].sort_values("time").reset_index(drop=True),
        use_container_width=True, hide_index=True, height=380,
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
    st.markdown("### ℹ️ About the Simulation")
    st.markdown(f"""
<div style="background:#1e293b;border-radius:var(--radius-md);padding:1.25rem;border:1px solid var(--border-glow);
            color:var(--text-secondary);font-size:0.9rem;line-height:1.7;">
  <strong style="color:var(--accent-green);">DataEngine.fetch_live()</strong> first attempts the Electricity Maps free-tier API.
  On failure, it falls back to <code>_simulate_profile()</code>, a time-seeded NumPy simulation that reproduces real demand curves
  (night troughs, morning/evening peaks, midday solar suppression).<br><br>
  The <strong style="color:var(--accent-green);">refresh seed</strong> rotates every <strong>{st.session_state.refresh_s} seconds</strong>,
  so data evolves naturally between refreshes. All 96 fifteen-minute slots are computed in one vectorised pass — no gaps, no duplicates.
</div>""", unsafe_allow_html=True)
```

---

## 🎨 **Key UI Improvements Made:**

### ✅ **1. No Text Overlapping**
- **Added generous padding/margins** between all elements
- **Improved container sizing** with `min-height` and proper spacing
- **Fixed line heights** (1.6-1.7) for better readability
- **Responsive design** for mobile devices

### ✅ **2. Better Heading Hierarchy**
- **h1**: Main title (2.25rem)
- **h2**: Section titles (1.875rem)
- **h3**: Subsections (1.5rem)
- **h4**: Component titles (1.25rem)
- **Consistent styling** with bottom borders for visual separation

### ✅ **3. Modern Attractive Theme**
- **New color palette**:
  - Primary: `#10b981` (Emerald Green)
  - Secondary: `#3b82f6` (Blue)
  - Accent: `#8b5cf6` (Purple)
  - Warning: `#f59e0b` (Amber)
  - Error: `#ef4444` (Red)
- **Dark theme** with proper contrast (`#0f172a` background)
- **Subtle shadows** for depth
- **Gradient backgrounds** for cards
- **Smooth animations** (hover effects, transitions)

### ✅ **4. Improved Component Styling**
- **Cards**: Rounded corners, subtle shadows, gradient backgrounds
- **Buttons**: Modern gradient buttons with hover effects
- **Tabs**: Clean, modern tab styling with active state
- **Charts**: Dark theme with proper colors
- **Data tables**: Better readability with dark theme

### ✅ **5. Professional Typography**
- **Inter font** (modern, clean)
- **Proper font weights** (700 for headings, 600 for emphasis)
- **Consistent sizing** throughout the app

### ✅ **6. Better Organization**
- **Clear section separation** with dividers
- **Logical grouping** of related elements
- **Improved sidebar layout** with better spacing
- **Consistent card designs** across all tabs

---

## 🚀 **How to Use:**
1. **Copy this entire code** into your `app.py` file
2. **Run with Streamlit**: `streamlit run app.py`
3. **Enjoy the new UI!** All functionality remains the same, but now with a **professional, modern look**

The app now has:
✅ **No overlapping text** (proper spacing everywhere)
✅ **Clear heading hierarchy** (h1 > h2 > h3 > h4)
✅ **Attractive modern theme** (professional dark mode)
✅ **Responsive design** (works on mobile too)
✅ **Consistent styling** (all components match)

Would you like me to make any additional adjustments to specific sections? 😊
