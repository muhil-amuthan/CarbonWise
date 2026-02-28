import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import json
import requests
import pytz
from typing import Optional, Dict, List, Tuple

# ============================================================
# CI import with fallback
# ============================================================
try:
    from src.ci import compute_ci_g_per_kwh
except ImportError:
    def compute_ci_g_per_kwh(thermal, hydro, nuclear, res):
        thermal = float(thermal); hydro = float(hydro); nuclear = float(nuclear); res = float(res)
        total = thermal + hydro + nuclear + res
        if total == 0:
            return 400.0
        return (thermal * 800 + hydro * 24 + nuclear * 12 + res * 50) / total

# ============================================================
# Location & Zone Configuration
# ============================================================
GRID_ZONES = {
    "India": {
        "zones": {
            "North India (NR)": {"lat": 28.6139, "lon": 77.2090, "ci_avg": 450},
            "South India (SR)": {"lat": 13.0827, "lon": 80.2707, "ci_avg": 380},
            "West India (WR)": {"lat": 19.0760, "lon": 72.8777, "ci_avg": 420},
            "East India (ER)": {"lat": 22.5726, "lon": 88.3639, "ci_avg": 480},
            "North-East (NER)": {"lat": 26.1445, "lon": 91.7362, "ci_avg": 350},
        },
        "timezone": "Asia/Kolkata",
        "voltage": "230V/50Hz"
    },
    "Europe": {
        "zones": {
            "Germany (DE)": {"lat": 51.1657, "lon": 10.4515, "ci_avg": 276},
            "France (FR)": {"lat": 46.2276, "lon": 2.2137, "ci_avg": 16},
            "UK (GB)": {"lat": 55.3781, "lon": -3.4360, "ci_avg": 106},
            "Netherlands (NL)": {"lat": 52.1326, "lon": 5.2913, "ci_avg": 209},
            "Spain (ES)": {"lat": 40.4637, "lon": -3.7492, "ci_avg": 89},
            "Italy (IT)": {"lat": 41.8719, "lon": 12.5674, "ci_avg": 202},
            "Nordics (NO/SE/FI)": {"lat": 60.4720, "lon": 8.4689, "ci_avg": 30},
        },
        "timezone": "Europe/Brussels",
        "voltage": "230V/50Hz"
    },
    "United States": {
        "zones": {
            "California (CAISO)": {"lat": 36.7783, "lon": -119.4179, "ci_avg": 200},
            "Texas (ERCOT)": {"lat": 31.9686, "lon": -99.9018, "ci_avg": 350},
            "New York (NYISO)": {"lat": 42.1657, "lon": -74.9481, "ci_avg": 250},
            "Midwest (MISO)": {"lat": 41.8780, "lon": -93.0977, "ci_avg": 400},
            "PJM (East)": {"lat": 39.8283, "lon": -77.5794, "ci_avg": 320},
            "Pacific Northwest": {"lat": 45.5152, "lon": -122.6784, "ci_avg": 100},
        },
        "timezone": "America/New_York",
        "voltage": "120V/60Hz"
    },
    "Asia Pacific": {
        "zones": {
            "Australia (AUS)": {"lat": -25.2744, "lon": 133.7751, "ci_avg": 450},
            "Japan (JP)": {"lat": 36.2048, "lon": 138.2529, "ci_avg": 450},
            "Singapore (SG)": {"lat": 1.3521, "lon": 103.8198, "ci_avg": 367},
            "South Korea (KR)": {"lat": 35.9078, "lon": 127.7669, "ci_avg": 357},
        },
        "timezone": "Asia/Tokyo",
        "voltage": "100V-240V/50-60Hz"
    }
}

# ============================================================
# API Configuration
# ============================================================
ELECTRICITY_MAPS_API = "https://api-access.electricitymaps.com/free-tier/"
WATTTIME_API = "https://api2.watttime.org/v2/"

# ============================================================
# Logging functions
# ============================================================
LOG_FILE = Path("logs/runs.jsonl")
CONFIG_FILE = Path("config/location.json")

def ensure_dirs():
    Path("logs").mkdir(exist_ok=True)
    Path("config").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("", encoding="utf-8")

def save_location_config(config: dict):
    ensure_dirs()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

def load_location_config() -> Optional[dict]:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def append_log(row: dict):
    ensure_dirs()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")

@st.cache_data(ttl=300)
def read_log():
    ensure_dirs()
    rows = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()

# ============================================================
# Location & Time Functions
# ============================================================
def get_system_time(timezone_str: str = "UTC") -> datetime:
    """Get current system time converted to specified timezone"""
    try:
        tz = pytz.timezone(timezone_str)
        return datetime.now(tz)
    except:
        return datetime.now()

def get_current_time(timezone_str: str = "UTC", step: int = 15) -> str:
    """Get current time rounded to nearest step in HH:MM format"""
    now = get_system_time(timezone_str)
    minutes = (now.minute // step) * step
    return f"{now.hour:02d}:{minutes:02d}"

def get_current_time_exact(timezone_str: str = "UTC") -> str:
    """Get exact current time in HH:MM:SS format"""
    now = get_system_time(timezone_str)
    return f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"

def detect_location_ip() -> Optional[Dict]:
    """Attempt to detect location via IP geolocation (free service)"""
    try:
        response = requests.get("https://ipapi.co/json/", timeout=5)
        if response.status_code == 200:
            data = response.json()
            return {
                "lat": data.get("latitude"),
                "lon": data.get("longitude"),
                "city": data.get("city"),
                "region": data.get("region"),
                "country": data.get("country_name"),
                "timezone": data.get("timezone"),
                "source": "IP Geolocation"
            }
    except Exception as e:
        st.warning(f"IP detection failed: {e}")
    return None

def fetch_electricity_maps_data(lat: float, lon: float, api_token: Optional[str] = None) -> Optional[pd.DataFrame]:
    """Fetch real-time carbon intensity from Electricity Maps API"""
    try:
        headers = {}
        if api_token:
            headers["auth-token"] = api_token
        
        # Get current carbon intensity
        url = f"{ELECTRICITY_MAPS_API}carbon-intensity/latest"
        params = {"lat": lat, "lon": lon}
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            ci = data.get("carbonIntensity", 400)
            
            # Generate 24h forecast based on typical daily patterns
            times, cis = [], []
            base_ci = ci
            
            for h in range(24):
                for m in [0, 15, 30, 45]:
                    t = f"{h:02d}:{m:02d}"
                    hour_factor = 1.0
                    if 0 <= h < 6:
                        hour_factor = 0.7
                    elif 6 <= h < 10:
                        hour_factor = 1.2
                    elif 10 <= h < 16:
                        hour_factor = 1.0
                    elif 16 <= h < 22:
                        hour_factor = 1.3
                    else:
                        hour_factor = 0.8
                    
                    times.append(t)
                    cis.append(base_ci * hour_factor)
            
            df = pd.DataFrame({
                "time": times,
                "ci": cis,
                "thermal_mw": [5000 * (c/base_ci) for c in cis],
                "hydro_mw": [2000] * len(times),
                "nuclear_mw": [1500] * len(times),
                "res_mw": [max(0, 3000 - 5000*(c/base_ci-0.5)) for c in cis]
            })
            return df
            
    except Exception as e:
        st.error(f"Electricity Maps API error: {e}")
    
    return None

# ============================================================
# Page config
# ============================================================
st.set_page_config(page_title="CarbonWise | Location-Aware", page_icon="🌍", layout="wide")

# ============================================================
# Enhanced CSS Styles
# ============================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

* { font-family: 'Inter', sans-serif !important; }

.main { background-color: #0a0e17; }

.hero { 
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%); 
    padding: 2rem; 
    border-radius: 16px; 
    margin-bottom: 2rem;
    border: 1px solid rgba(74, 222, 128, 0.2);
}
.hero h1 { 
    color: #4ade80; 
    margin: 0; 
    font-size: 2.5rem;
    font-weight: 800;
}
.hero p { 
    color: #94a3b8; 
    margin: 0.5rem 0 0 0;
    font-size: 1.1rem;
}

.location-badge {
    background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
    color: white;
    padding: 0.5rem 1rem;
    border-radius: 8px;
    font-size: 0.875rem;
    font-weight: 600;
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    border: 1px solid rgba(59, 130, 246, 0.3);
}

.kpi { 
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); 
    padding: 1.5rem; 
    border-radius: 12px; 
    text-align: center; 
    border-left: 4px solid #4ade80;
}
.metric-value { 
    font-size: 1.75rem; 
    font-weight: 800; 
    color: white;
    margin-top: 0.5rem;
}
.metric-label { 
    color: #94a3b8; 
    font-size: 0.875rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

.card { 
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); 
    padding: 1.5rem; 
    border-radius: 12px; 
    border: 1px solid rgba(148, 163, 184, 0.1);
}

[data-testid="stSidebar"] { background: #111827 !important; }
[data-testid="stSidebar"] .stRadio > div { background: #1f2937 !important; border-radius: 8px; padding: 0.5rem; }

.live-time {
    background: rgba(74, 222, 128, 0.1);
    border: 1px solid #4ade80;
    padding: 0.75rem;
    border-radius: 8px;
    color: #4ade80;
    font-weight: 700;
    text-align: center;
    font-size: 1.1rem;
    margin-bottom: 1rem;
}

.timezone-info {
    color: #64748b;
    font-size: 0.75rem;
    text-align: center;
    margin-top: 0.25rem;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# Constants
# ============================================================
STEP_MIN = 15
DATA_DIR = Path("data")

APPLIANCES = {
    "Geyser / Water Heater": {"kw": 2.0, "duration_min": 30, "deadline": "11:00"},
    "Washing Machine": {"kw": 0.5, "duration_min": 60, "deadline": "11:00"},
    "Iron Box": {"kw": 1.0, "duration_min": 30, "deadline": "11:00"},
    "Water Motor": {"kw": 0.75, "duration_min": 30, "deadline": "08:00"},
    "EV Charger (3.3 kW)": {"kw": 3.3, "duration_min": 120, "deadline": "07:00"},
    "EV Charger (7.0 kW)": {"kw": 7.0, "duration_min": 120, "deadline": "07:00"},
    "Air Conditioner": {"kw": 1.5, "duration_min": 60, "deadline": "23:00"},
    "Induction Cooktop": {"kw": 1.8, "duration_min": 30, "deadline": "21:00"},
    "Microwave": {"kw": 1.2, "duration_min": 15, "deadline": "21:00"},
    "Laptop Charging": {"kw": 0.06, "duration_min": 120, "deadline": "23:00"},
    "Custom": {"kw": 1.0, "duration_min": 30, "deadline": "11:00"},
}

# ============================================================
# Helper functions
# ============================================================
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
    if ci < 200: return "#4ade80"
    elif ci < 400: return "#fbbf24"
    else: return "#f87171"

def get_ci_status(ci):
    ci = safe_float(ci, 500)
    if ci < 200: return "Low"
    elif ci < 400: return "Medium"
    else: return "High"

def calculate_co2(kwh, ci):
    return (safe_float(kwh,0) * safe_float(ci,0)) / 1000.0

def validate_time_format(time_str):
    try:
        h, m = map(int, time_str.split(":"))
        return 0 <= h < 24 and 0 <= m < 60
    except:
        return False

def time_to_minutes(time_str):
    try:
        h, m = map(int, time_str.split(":"))
        return h * 60 + m
    except:
        return 0

def minutes_to_time(mins):
    return f"{(mins // 60) % 24:02d}:{mins % 60:02d}"

def make_full_day_ci(df_ci):
    times, cis = [], []
    df_sorted = df_ci.sort_values("time").reset_index(drop=True)
    ci_dict = dict(zip(df_sorted["time"], df_sorted["ci"]))
    mean_ci = safe_float(df_sorted["ci"].mean(), 400)

    for h in range(24):
        for m in [0, 15, 30, 45]:
            t = f"{h:02d}:{m:02d}"
            ci = ci_dict.get(t, mean_ci)
            times.append(t)
            cis.append(safe_float(ci, mean_ci))

    df = pd.DataFrame({"time": times, "ci": cis})
    df["color"] = df["ci"].apply(get_ci_color)
    df["status"] = df["ci"].apply(get_ci_status)
    return df

def recommend_windows(full_day_ci, duration_min, deadline, top_k, now_time):
    duration_min = ceil_to_step(duration_min, STEP_MIN)
    n_slots = duration_min // STEP_MIN

    try:
        dh, dm = map(int, deadline.split(":"))
        deadline_mins = dh * 60 + dm
    except:
        deadline_mins = 23 * 60 + 59

    try:
        nh, nm = map(int, now_time.split(":"))
        now_mins = nh * 60 + nm
    except:
        now_mins = 0

    if deadline_mins <= now_mins:
        deadline_mins += 1440

    windows = []
    times = full_day_ci["time"].tolist()
    cis = full_day_ci["ci"].tolist()

    for i in range(len(times) - n_slots + 1):
        start_time = times[i]
        sh, sm = map(int, start_time.split(":"))
        start_mins = sh * 60 + sm

        adjusted_start_mins = start_mins
        if start_mins < now_mins:
            adjusted_start_mins = start_mins + 1440

        if adjusted_start_mins < now_mins:
            continue

        end_mins_exact = adjusted_start_mins + duration_min

        if end_mins_exact > deadline_mins:
            continue

        end_h = (end_mins_exact // 60) % 24
        end_m = end_mins_exact % 60
        end_time = f"{end_h:02d}:{end_m:02d}"

        window_cis = [safe_float(x, 500) for x in cis[i:i+n_slots]]
        avg_ci = sum(window_cis) / len(window_cis) if window_cis else 500

        windows.append({
            "start": start_time,
            "end": end_time,
            "avg_ci": avg_ci,
            "min_ci": min(window_cis) if window_cis else 0,
            "max_ci": max(window_cis) if window_cis else 1000,
            "color": get_ci_color(avg_ci),
            "status": get_ci_status(avg_ci),
            "start_mins": adjusted_start_mins
        })

    windows.sort(key=lambda x: x["avg_ci"])
    return windows[:top_k]

def find_ci_at_time(full_day_ci, time_str):
    mask = full_day_ci["time"] == time_str
    if mask.any():
        return float(full_day_ci[mask]["ci"].iloc[0])
    return None

# ============================================================
# Session state initialization
# ============================================================
if "appliance" not in st.session_state:
    st.session_state.appliance = "Water Motor"
    st.session_state.kw = 0.75
    st.session_state.duration = 30
    st.session_state.deadline = "08:00"
    st.session_state.selected_window = 0
    st.session_state.location_mode = "Auto-Detect"
    st.session_state.selected_region = "India"
    st.session_state.selected_zone = "North India (NR)"
    st.session_state.lat = 28.6139
    st.session_state.lon = 77.2090
    st.session_state.timezone = "Asia/Kolkata"
    st.session_state.data_source = "Automatic (API)"

# ============================================================
# Hero Section
# ============================================================
st.markdown("""
<div class="hero">
    <h1>🌍 CarbonWise</h1>
    <p>Location-aware carbon intensity optimization. Automatically detects your grid region and optimizes appliance scheduling for minimal CO₂ impact.</p>
    <div style="margin-top: 1rem;">
        <span class="badge-success">⚡ Live Grid Optimization</span>
        <span style="margin-left: 0.5rem; color: #64748b; font-size: 0.9rem;">Powered by Electricity Maps & System Time</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ============================================================
# Sidebar - Location & Configuration
# ============================================================
with st.sidebar:
    st.title("⚙️ Configuration")
    
    # ==================== LOCATION SECTION ====================
    st.markdown("### 📍 Location Settings")
    
    location_mode = st.radio(
        "Location Mode",
        ["Auto-Detect", "Manual Select", "Custom Coordinates"],
        index=["Auto-Detect", "Manual Select", "Custom Coordinates"].index(st.session_state.location_mode),
        help="Auto-Detect uses IP geolocation. Manual Select lets you choose your grid zone."
    )
    st.session_state.location_mode = location_mode
    
    detected_location = None
    
    if location_mode == "Auto-Detect":
        if st.button("🔍 Detect My Location", use_container_width=True):
            with st.spinner("Detecting location..."):
                detected_location = detect_location_ip()
                if detected_location:
                    st.session_state.lat = detected_location["lat"]
                    st.session_state.lon = detected_location["lon"]
                    st.session_state.timezone = detected_location.get("timezone", "UTC")
                    st.success(f"📍 {detected_location['city']}, {detected_location['country']}")
                    st.rerun()
                else:
                    st.error("Detection failed. Switch to Manual Select.")
        
        st.markdown(f"""
        <div style="background: #1f2937; padding: 0.75rem; border-radius: 8px; margin-top: 0.5rem;">
            <div style="color: #94a3b8; font-size: 0.75rem;">Current Coordinates</div>
            <div style="color: #4ade80; font-size: 0.875rem; font-weight: 600;">
                {st.session_state.lat:.4f}, {st.session_state.lon:.4f}
            </div>
        </div>
        """, unsafe_allow_html=True)
        
    elif location_mode == "Manual Select":
        selected_region = st.selectbox(
            "Region", 
            list(GRID_ZONES.keys()),
            index=list(GRID_ZONES.keys()).index(st.session_state.selected_region) if st.session_state.selected_region in GRID_ZONES else 0
        )
        st.session_state.selected_region = selected_region
        
        zones = GRID_ZONES[selected_region]["zones"]
        selected_zone = st.selectbox(
            "Grid Zone",
            list(zones.keys()),
            index=list(zones.keys()).index(st.session_state.selected_zone) if st.session_state.selected_zone in zones else 0
        )
        st.session_state.selected_zone = selected_zone
        
        zone_data = zones[selected_zone]
        st.session_state.lat = zone_data["lat"]
        st.session_state.lon = zone_data["lon"]
        st.session_state.timezone = GRID_ZONES[selected_region]["timezone"]
        
        st.markdown(f"""
        <div style="background: #1f2937; padding: 0.75rem; border-radius: 8px; margin-top: 0.5rem;">
            <div style="color: #94a3b8; font-size: 0.75rem;">Grid Zone Info</div>
            <div style="color: #4ade80; font-size: 0.875rem; font-weight: 600;">
                Avg CI: {zone_data['ci_avg']} gCO₂/kWh
            </div>
            <div style="color: #64748b; font-size: 0.75rem; margin-top: 0.25rem;">
                {GRID_ZONES[selected_region]["voltage"]}
            </div>
        </div>
        """, unsafe_allow_html=True)
        
    else:  # Custom Coordinates
        lat_col, lon_col = st.columns(2)
        with lat_col:
            st.session_state.lat = st.number_input("Latitude", -90.0, 90.0, value=st.session_state.lat, format="%.4f")
        with lon_col:
            st.session_state.lon = st.number_input("Longitude", -180.0, 180.0, value=st.session_state.lon, format="%.4f")
        
        tz_options = pytz.common_timezones
        current_tz = st.session_state.timezone if st.session_state.timezone in tz_options else "UTC"
        st.session_state.timezone = st.selectbox("Timezone", tz_options, index=tz_options.index(current_tz) if current_tz in tz_options else 0)
    
    location_display = st.session_state.selected_zone if location_mode == "Manual Select" else f"{st.session_state.lat:.2f}, {st.session_state.lon:.2f}"
    st.markdown(f"""
    <div style="margin-top: 1rem;">
        <span class="location-badge">🌍 {location_display}</span>
    </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    # ==================== TIME DISPLAY ====================
    current_time_exact = get_current_time_exact(st.session_state.timezone)
    current_time_rounded = get_current_time(st.session_state.timezone, STEP_MIN)
    
    st.markdown(f"""
    <div class="live-time">
        🕐 {current_time_exact}
    </div>
    <div class="timezone-info">
        System Time ({st.session_state.timezone})<br>
        Rounded: {current_time_rounded} (15-min intervals)
    </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    # ==================== DATA SOURCE ====================
    st.subheader("📁 Data Source")
    
    data_source = st.radio(
        "Select Mode",
        ["Automatic (API)", "Sample Data", "Upload CSV (Admin)"],
        index=["Automatic (API)", "Sample Data", "Upload CSV (Admin)"].index(st.session_state.data_source) if st.session_state.data_source in ["Automatic (API)", "Sample Data", "Upload CSV (Admin)"] else 0,
        help="Automatic fetches real-time data from Electricity Maps. Sample uses synthetic data. Upload CSV for manual admin entry."
    )
    st.session_state.data_source = data_source
    
    uploaded_file = None
    api_token = None
    
    if data_source == "Automatic (API)":
        st.info("🌐 Fetches real-time grid data based on your location")
        api_token = st.text_input("Electricity Maps API Token (Optional)", type="password", help="Free tier token for higher rate limits")
        if st.button("🔄 Refresh Grid Data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
            
    elif data_source == "Upload CSV (Admin)":
        st.warning("👤 Admin Mode: Upload grid generation data")
        uploaded_file = st.file_uploader("Upload CSV", type=["csv"], help="Required: time, thermal_mw, hydro_mw, nuclear_mw, res_mw")
        
        with st.expander("📋 CSV Format Guide"):
            st.markdown("""
            **Required Columns:**
            - `time`: HH:MM format (00:00 to 23:45)
            - `thermal_mw`: Thermal generation in MW
            - `hydro_mw`: Hydro generation in MW  
            - `nuclear_mw`: Nuclear generation in MW
            - `res_mw`: Renewable generation in MW
            
            **Optional:**
            - `ci`: Pre-calculated carbon intensity (gCO₂/kWh)
            """)
    
    st.divider()
    
    # ==================== APPLIANCE SETTINGS ====================
    st.subheader("🔌 Appliance Settings")
    
    appliance = st.selectbox("Select Appliance", list(APPLIANCES.keys()), label_visibility="collapsed")
    
    if appliance != st.session_state.appliance:
        info = APPLIANCES[appliance]
        st.session_state.kw = info["kw"]
        st.session_state.duration = info["duration_min"]
        st.session_state.deadline = info["deadline"]
        st.session_state.appliance = appliance
    
    kw = st.number_input("Power (kW)", 0.001, value=float(st.session_state.kw), step=0.05, format="%.3f")
    duration = st.number_input("Duration (minutes)", 15, value=int(st.session_state.duration), step=15)
    
    deadline = st.text_input("Deadline (HH:MM)", value=st.session_state.deadline, 
                             help=f"Latest time to finish (System time: {st.session_state.timezone})")
    
    if not validate_time_format(deadline):
        st.error("⚠️ Invalid format. Use HH:MM")
        deadline = st.session_state.deadline
    else:
        st.session_state.deadline = deadline
    
    now_mins = time_to_minutes(current_time_rounded)
    deadline_mins = time_to_minutes(deadline)
    
    if deadline_mins > now_mins:
        available_mins = deadline_mins - now_mins
    else:
        available_mins = (1440 - now_mins) + deadline_mins
    
    st.info(f"⏱️ Available: **{available_mins} min** ({available_mins//60}h {available_mins%60}m)")
    
    if st.button("🔄 Reset to Default"):
        info = APPLIANCES[appliance]
        st.session_state.kw = info["kw"]
        st.session_state.duration = info["duration_min"]
        st.session_state.deadline = info["deadline"]
        st.rerun()
    
    st.divider()
    
    # ==================== OPTIMIZATION ====================
    st.subheader("⚡ Optimization")
    top_k = st.slider("Top Recommendations", 3, 12, 5)
    
    st.session_state.kw = kw
    st.session_state.duration = duration

# ============================================================
# Data Loading Logic
# ============================================================
@st.cache_data(ttl=300)
def load_data_auto(lat: float, lon: float, api_token: Optional[str] = None) -> Optional[pd.DataFrame]:
    """Load data from Electricity Maps API"""
    return fetch_electricity_maps_data(lat, lon, api_token)

@st.cache_data(ttl=300)
def load_data_sample() -> pd.DataFrame:
    """Generate sample data based on typical grid patterns"""
    DATA_DIR.mkdir(exist_ok=True)
    sample_file = DATA_DIR / "sample.csv"
    
    if not sample_file.exists():
        data = []
        for h in range(24):
            for m in [0, 15, 30, 45]:
                t = f"{h:02d}:{m:02d}"
                base_load = 3000
                if 0 <= h < 5: load_factor = 0.6
                elif 5 <= h < 8: load_factor = 0.8
                elif 8 <= h < 12: load_factor = 1.0
                elif 12 <= h < 17: load_factor = 1.1
                elif 17 <= h < 21: load_factor = 1.2
                else: load_factor = 0.9
                
                thermal = int(base_load * load_factor * 0.6)
                hydro = 1500
                nuclear = 2000
                res = int(800 + (1200 if 10 <= h <= 16 else 0))
                
                data.append({
                    "time": t,
                    "thermal_mw": thermal,
                    "hydro_mw": hydro,
                    "nuclear_mw": nuclear,
                    "res_mw": res
                })
        pd.DataFrame(data).to_csv(sample_file, index=False)
    
    return pd.read_csv(sample_file)

def load_data_uploaded(file) -> Optional[pd.DataFrame]:
    """Load admin-uploaded CSV data"""
    try:
        df = pd.read_csv(file)
        required = {"time", "thermal_mw", "hydro_mw", "nuclear_mw", "res_mw"}
        missing = required - set(df.columns)
        if missing:
            st.error(f"Missing columns: {', '.join(missing)}")
            return None
        return df
    except Exception as e:
        st.error(f"Error reading CSV: {e}")
        return None

# Load data based on selected mode
raw = None
if st.session_state.data_source == "Automatic (API)":
    with st.spinner("🌐 Fetching real-time grid data..."):
        raw = load_data_auto(st.session_state.lat, st.session_state.lon, api_token)
    if raw is None:
        st.warning("⚠️ API fetch failed. Falling back to sample data.")
        raw = load_data_sample()
        
elif st.session_state.data_source == "Sample Data":
    raw = load_data_sample()
    
else:  # Upload CSV
    if uploaded_file:
        raw = load_data_uploaded(uploaded_file)
    else:
        st.info("👤 Please upload a CSV file or switch to Automatic/Sample mode")
        st.stop()

if raw is None or raw.empty:
    st.error("❌ No data available. Please check your data source.")
    st.stop()

# Calculate CI if not present
if "ci" not in raw.columns:
    raw["ci"] = raw.apply(lambda r: compute_ci_g_per_kwh(
        safe_float(r["thermal_mw"]),
        safe_float(r["hydro_mw"]),
        safe_float(r["nuclear_mw"]),
        safe_float(r["res_mw"])
    ), axis=1)

df_ci = raw[["time", "ci"]].dropna().sort_values("time").reset_index(drop=True)

if len(df_ci) < 4:
    st.error("❌ Insufficient data points")
    st.stop()

# Generate full day CI data with 15-min intervals
full_day_ci = make_full_day_ci(df_ci)

# Get current time based on system timezone
now_time = get_current_time(st.session_state.timezone, STEP_MIN)

# Find recommendations
windows = recommend_windows(full_day_ci, duration, deadline, top_k, now_time)

current_ci = find_ci_at_time(full_day_ci, now_time)
if current_ci is None:
    current_ci = float(full_day_ci["ci"].mean())

best = windows[0] if windows else None
potential_savings = 0
if best and current_ci > 0:
    potential_savings = ((current_ci - best["avg_ci"]) / current_ci) * 100

# ============================================================
# KPI Dashboard
# ============================================================
st.markdown("### 📊 Current Session Overview")

kpi_cols = st.columns(5)

with kpi_cols[0]:
    location_label = st.session_state.selected_zone if st.session_state.location_mode == "Manual Select" else "Auto-Detected"
    st.markdown(f"""
    <div class="kpi">
        <div class="metric-label">🌍 Location</div>
        <div class="metric-value" style="font-size: 1.2rem;">{location_label}</div>
        <div class="metric-sub">{st.session_state.timezone}</div>
    </div>
    """, unsafe_allow_html=True)

with kpi_cols[1]:
    st.markdown(f"""
    <div class="kpi">
        <div class="metric-label">🕐 System Time</div>
        <div class="metric-value">{now_time}</div>
        <div class="metric-sub">Live ({st.session_state.data_source.split(' ')[0]})</div>
    </div>
    """, unsafe_allow_html=True)

with kpi_cols[2]:
    st.markdown(f"""
    <div class="kpi">
        <div class="metric-label">🔌 Appliance</div>
        <div class="metric-value" style="font-size: 1.2rem;">{appliance.split('/')[0].strip()}</div>
        <div class="metric-sub">{kw:.2f} kW • {ceil_to_step(duration)} min</div>
    </div>
    """, unsafe_allow_html=True)

with kpi_cols[3]:
    ci_color = get_ci_color(current_ci)
    st.markdown(f"""
    <div class="kpi" style="border-left-color: {ci_color}">
        <div class="metric-label">🌍 Current CI</div>
        <div class="metric-value" style="color: {ci_color}">{current_ci:.0f}</div>
        <div class="metric-sub">{get_ci_status(current_ci)} intensity</div>
    </div>
    """, unsafe_allow_html=True)

with kpi_cols[4]:
    best_color = "#4ade80" if best else "#64748b"
    best_start = best["start"] if best else "--:--"
    savings_text = f"Save ~{potential_savings:.1f}%" if potential_savings > 0 else "No savings"
    st.markdown(f"""
    <div class="kpi" style="border-left-color: {best_color}">
        <div class="metric-label">✨ Best Window</div>
        <div class="metric-value" style="color: {best_color}">{best_start}</div>
        <div class="metric-sub">{savings_text}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")

# ============================================================
# Tabs
# ============================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📈 Dashboard", "🎯 Smart Advisor", "📊 Analytics", "📝 Logger", "🔍 Data"])

# ================== TAB 1: Dashboard ==================
with tab1:
    dash_cols = st.columns([2, 1])
    
    with dash_cols[0]:
        st.subheader(f"24-Hour Carbon Intensity Forecast • {st.session_state.timezone}")
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=full_day_ci["time"],
            y=full_day_ci["ci"],
            fill='tozeroy',
            fillcolor='rgba(74, 222, 128, 0.1)',
            line=dict(color='#4ade80', width=3),
            hovertemplate='Time: %{x}<br>CI: %{y:.1f} gCO₂/kWh<extra></extra>',
            name='Carbon Intensity'
        ))
        
        fig.add_hrect(y0=0, y1=200, fillcolor="#4ade80", opacity=0.05, line_width=0, name="Low")
        fig.add_hrect(y0=200, y1=400, fillcolor="#fbbf24", opacity=0.05, line_width=0, name="Medium")
        fig.add_hrect(y0=400, y1=800, fillcolor="#f87171", opacity=0.05, line_width=0, name="High")
        
        if windows:
            for i, w in enumerate(windows[:3]):
                fig.add_vrect(
                    x0=w["start"],
                    x1=w["end"],
                    fillcolor=w["color"],
                    opacity=0.25,
                    line_width=2 if i == 0 else 1,
                    line_color=w["color"],
                    layer="below",
                    name=f"Window {i+1}"
                )
        
        fig.add_vline(x=now_time, line_dash="dash", line_color="#4ade80", line_width=2)
        fig.add_vline(x=deadline, line_dash="dash", line_color="#f87171", line_width=2)
        
        max_ci_val = max(full_day_ci["ci"]) * 1.05
        fig.add_annotation(x=now_time, y=max_ci_val, text="NOW", showarrow=False, 
                          font=dict(color="#4ade80", size=11))
        fig.add_annotation(x=deadline, y=max_ci_val, text="DEADLINE", showarrow=False,
                          font=dict(color="#f87171", size=11))
        
        fig.update_layout(
            plot_bgcolor="#0f172a",
            paper_bgcolor="#0f172a",
            font=dict(color="#f8fafc"),
            xaxis=dict(gridcolor="#334155", title="Time of Day", tickangle=-45, nticks=12),
            yaxis=dict(gridcolor="#334155", title="Carbon Intensity (gCO₂/kWh)", 
                      range=[0, max(full_day_ci["ci"]) * 1.15]),
            height=450,
            margin=dict(l=60, r=40, t=40, b=60),
            showlegend=False,
            title=dict(text=f"Location: {st.session_state.lat:.2f}°N, {st.session_state.lon:.2f}°E", 
                      font=dict(size=12, color="#64748b"))
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with dash_cols[1]:
        st.subheader("Current Grid Mix")
        
        latest = raw.iloc[-1]
        total = safe_float(latest["thermal_mw"]) + safe_float(latest["hydro_mw"]) + \
                safe_float(latest["nuclear_mw"]) + safe_float(latest["res_mw"])
        
        mix_data = pd.DataFrame({
            "Source": ["Thermal", "Hydro", "Nuclear", "Renewable"],
            "MW": [safe_float(latest["thermal_mw"]), safe_float(latest["hydro_mw"]), 
                   safe_float(latest["nuclear_mw"]), safe_float(latest["res_mw"])],
            "Color": ["#f87171", "#60a5fa", "#a78bfa", "#4ade80"]
        })
        
        fig2 = go.Figure(data=[go.Pie(
            labels=mix_data["Source"],
            values=mix_data["MW"],
            hole=0.65,
            marker_colors=mix_data["Color"],
            textinfo="label+percent",
            textfont=dict(color="white", size=11),
        )])
        
        fig2.update_layout(
            plot_bgcolor="#0f172a",
            paper_bgcolor="#0f172a",
            font=dict(color="#f8fafc"),
            height=380,
            showlegend=False,
            margin=dict(t=20, b=20, l=20, r=20),
            annotations=[dict(
                text=f"<b>{total/1000:.1f}</b><br>GW",
                x=0.5, y=0.5,
                font_size=20,
                font_color="#f8fafc",
                showarrow=False
            )]
        )
        st.plotly_chart(fig2, use_container_width=True)

# ================== TAB 2: Smart Advisor ==================
with tab2:
    st.subheader(f"🏆 Top {top_k} Windows ({now_time} → {deadline}) • {st.session_state.timezone}")
    
    if not windows:
        st.warning(f"⚠️ No valid windows found! Try extending deadline or reducing duration.")
        st.info(f"""
        **Debug Info:**
        - Current time: {now_time}
        - Deadline: {deadline}
        - Duration: {ceil_to_step(duration)} minutes
        - Available window: {available_mins} minutes
        """)
    else:
        for row_start in range(0, len(windows), 3):
            row_windows = windows[row_start:row_start + 3]
            rec_cols = st.columns(len(row_windows))
            
            for i, (col, w) in enumerate(zip(rec_cols, row_windows)):
                global_index = row_start + i
                rank = global_index + 1
                
                with col:
                    hours = ceil_to_step(duration) / 60
                    energy = float(kw) * hours
                    co2 = calculate_co2(energy, w["avg_ci"])
                    co2_now = calculate_co2(energy, current_ci)
                    savings = co2_now - co2
                    
                    is_best = global_index == 0
                    border_style = "border: 2px solid #4ade80;" if is_best else "border: 1px solid #374151;"
                    
                    st.markdown(f"""
                    <div class="card" style="{border_style}">
                        <div style="text-align: center; margin-bottom: 0.5rem;">
                            <span style="background: {'#4ade80' if is_best else '#374151'}; color: {'#0f172a' if is_best else 'white'}; 
                                      padding: 0.25rem 0.75rem; border-radius: 6px; font-size: 0.75rem; font-weight: 700;">
                                {'🥇 BEST' if rank == 1 else f'#{rank}'}
                            </span>
                        </div>
                        <div style="font-size: 1.5rem; font-weight: 800; color: white; text-align: center;">
                            {w["start"]} → {w["end"]}
                        </div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; margin-top: 0.75rem;">
                            <div style="background: #0f172a; padding: 0.5rem; border-radius: 8px; text-align: center;">
                                <div style="color: #64748b; font-size: 0.7rem;">CI</div>
                                <div style="color: {w['color']}; font-size: 1rem; font-weight: 700;">{w["avg_ci"]:.0f}</div>
                            </div>
                            <div style="background: #0f172a; padding: 0.5rem; border-radius: 8px; text-align: center;">
                                <div style="color: #64748b; font-size: 0.7rem;">CO₂</div>
                                <div style="color: white; font-size: 1rem; font-weight: 700;">{co2:.3f}kg</div>
                            </div>
                        </div>
                        <div style="background: rgba(74, 222, 128, 0.1); padding: 0.4rem; border-radius: 6px; text-align: center; margin-top: 0.5rem;">
                            <span style="color: #4ade80; font-size: 0.8rem;">💰 Save {savings:.3f} kg</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if st.button(f"✅ Select", key=f"sel_{global_index}", use_container_width=True):
                        st.session_state.selected_window = global_index
                        st.success(f"✅ Selected: {w['start']} - {w['end']}")

# ================== TAB 3: Analytics ==================
with tab3:
    st.subheader("📊 Baseline vs Optimized")
    
    hours = ceil_to_step(duration) / 60
    energy = float(kw) * hours
    co2_now = calculate_co2(energy, current_ci)
    co2_best = calculate_co2(energy, best["avg_ci"]) if best else co2_now
    projected_savings = co2_now - co2_best
    savings_pct = (projected_savings / co2_now * 100) if co2_now > 0 else 0
    
    location_label = st.session_state.selected_zone if st.session_state.location_mode == "Manual Select" else f"Lat: {st.session_state.lat:.2f}, Lon: {st.session_state.lon:.2f}"
    st.markdown(f"""
    <div style="background: #1e293b; padding: 1rem; border-radius: 8px; margin-bottom: 1rem;">
        <div style="color: #94a3b8; font-size: 0.875rem;">Location Context</div>
        <div style="color: white; font-size: 1rem;">
            {location_label} • {st.session_state.timezone} • Data: {st.session_state.data_source}
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    fig_comp = go.Figure()
    fig_comp.add_trace(go.Bar(
        name='Baseline (Now)',
        x=['CO₂ Emissions'],
        y=[co2_now],
        marker_color='#f87171',
        text=[f'{co2_now:.3f} kg'],
        textposition='outside',
        width=0.35
    ))
    fig_comp.add_trace(go.Bar(
        name='Optimized (Best)',
        x=['CO₂ Emissions'],
        y=[co2_best],
        marker_color='#4ade80',
        text=[f'{co2_best:.3f} kg'],
        textposition='outside',
        width=0.35
    ))
    
    fig_comp.update_layout(
        barmode='group',
        plot_bgcolor="#0f172a",
        paper_bgcolor="#0f172a",
        font=dict(color="#f8fafc", size=14),
        height=350,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        yaxis=dict(title="CO₂ (kg)", gridcolor="#334155"),
        title=dict(text=f"💰 Potential Savings: {projected_savings:.3f} kg ({savings_pct:.1f}%)", 
                   font=dict(size=16, color="#4ade80"))
    )
    st.plotly_chart(fig_comp, use_container_width=True)
    
    stat_cols = st.columns(3)
    with stat_cols[0]:
        st.metric("🔴 Baseline", f"{co2_now:.3f} kg", help=f"CI={current_ci:.0f} at {now_time}")
    with stat_cols[1]:
        st.metric("🟢 Optimized", f"{co2_best:.3f} kg", help=f"CI={best['avg_ci']:.0f}" if best else "N/A")
    with stat_cols[2]:
        st.metric("💰 Savings", f"{savings_pct:.1f}%", delta=f"-{projected_savings:.3f} kg", delta_color="inverse")
    
    log_df = read_log()
    if len(log_df) > 0:
        with st.expander("📋 Run History"):
            st.dataframe(log_df.sort_values("timestamp", ascending=False), 
                        use_container_width=True, hide_index=True)

# ================== TAB 4: Logger ==================
with tab4:
    ensure_dirs()
    st.subheader("📝 Log Appliance Run")
    
    with st.form("run_form"):
        st.markdown(f"**System Time:** {get_current_time_exact(st.session_state.timezone)} ({st.session_state.timezone})")
        
        form_cols = st.columns(3)
        with form_cols[0]:
            run_date = st.date_input("Date", datetime.now())
            run_type = st.selectbox("Run Type", ["recommended", "baseline", "test"])
        with form_cols[1]:
            default_start = windows[st.session_state.selected_window]["start"] if windows and st.session_state.selected_window < len(windows) else now_time
            start_time = st.text_input("Start Time", default_start)
            run_duration = st.number_input("Duration (min)", 15, value=ceil_to_step(int(duration)), step=15)
        with form_cols[2]:
            run_appliance = st.selectbox("Appliance", list(APPLIANCES.keys()))
            notes = st.text_input("Notes", f"Location: {st.session_state.selected_zone if st.session_state.location_mode == 'Manual Select' else 'Auto'}")
        
        meter_cols = st.columns(2)
        with meter_cols[0]:
            meter_before = st.number_input("Meter Before (kWh)", 0.0, format="%.3f")
        with meter_cols[1]:
            meter_after = st.number_input("Meter After (kWh)", 0.0, format="%.3f")
        
        submitted = st.form_submit_button("💾 Save", use_container_width=True)
        
        if submitted:
            kwh_used = meter_after - meter_before
            if kwh_used <= 0:
                st.error("❌ 'After' must be > 'Before'")
            else:
                avg_ci = find_ci_at_time(full_day_ci, start_time)
                if avg_ci is None:
                    avg_ci = float(full_day_ci["ci"].mean())
                
                start_mins = time_to_minutes(start_time)
                end_mins = start_mins + ceil_to_step(int(run_duration))
                end_time = minutes_to_time(end_mins)
                
                co2_out = calculate_co2(kwh_used, avg_ci)
                
                row = {
                    "timestamp": datetime.now().isoformat(),
                    "date": str(run_date),
                    "run_type": run_type,
                    "appliance": run_appliance,
                    "start_time": start_time,
                    "end_time": end_time,
                    "kwh_used": round(float(kwh_used), 4),
                    "avg_ci_g_per_kwh": round(float(avg_ci), 2),
                    "co2_kg": round(float(co2_out), 4),
                    "location_zone": st.session_state.selected_zone if st.session_state.location_mode == "Manual Select" else f"{st.session_state.lat:.2f},{st.session_state.lon:.2f}",
                    "timezone": st.session_state.timezone,
                    "notes": notes
                }
                
                append_log(row)
                read_log.clear()
                st.success(f"✅ Saved! {kwh_used:.3f} kWh, {co2_out:.4f} kg CO₂ at {st.session_state.timezone}")
                if run_type == "recommended":
                    st.balloons()

# ================== TAB 5: Data ==================
with tab5:
    st.subheader("🔍 Raw Data & Configuration")
    
    with st.expander("📍 Current Location Configuration"):
        config_data = {
            "Mode": st.session_state.location_mode,
            "Region": st.session_state.selected_region if st.session_state.location_mode == "Manual Select" else "N/A",
            "Zone": st.session_state.selected_zone if st.session_state.location_mode == "Manual Select" else "N/A",
            "Latitude": st.session_state.lat,
            "Longitude": st.session_state.lon,
            "Timezone": st.session_state.timezone,
            "Data Source": st.session_state.data_source
        }
        st.json(config_data)
        
        if st.button("💾 Save Config"):
            save_location_config(config_data)
            st.success("Configuration saved!")
    
    show_cols = ["time", "thermal_mw", "hydro_mw", "nuclear_mw", "res_mw", "ci"]
    show_cols = [c for c in show_cols if c in raw.columns]
    
    st.dataframe(raw[show_cols].sort_values("time"), use_container_width=True, hide_index=True)
    
    col1, col2 = st.columns(2)
    with col1:
        st.download_button("📥 Download CSV", 
                           data=raw[show_cols].to_csv(index=False).encode("utf-8"),
                           file_name=f"carbonwise_{st.session_state.lat:.2f}_{st.session_state.lon:.2f}.csv",
                           mime="text/csv")
    with col2:
        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()
