import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from pathlib import Path
import numpy as np
import json
import requests
import pytz
from typing import Optional, Dict
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
            "North India (NR)":  {"lat": 28.6139, "lon": 77.2090, "ci_avg": 450},
            "South India (SR)":  {"lat": 13.0827, "lon": 80.2707, "ci_avg": 380},
            "West India (WR)":   {"lat": 19.0760, "lon": 72.8777, "ci_avg": 420},
            "East India (ER)":   {"lat": 22.5726, "lon": 88.3639, "ci_avg": 480},
            "North-East (NER)":  {"lat": 26.1445, "lon": 91.7362, "ci_avg": 350},
        },
        "timezone": "Asia/Kolkata",
        "voltage": "230V/50Hz"
    },
    "Europe": {
        "zones": {
            "Germany (DE)":       {"lat": 51.1657, "lon": 10.4515, "ci_avg": 276},
            "France (FR)":        {"lat": 46.2276, "lon":  2.2137, "ci_avg":  16},
            "UK (GB)":            {"lat": 55.3781, "lon": -3.4360, "ci_avg": 106},
            "Netherlands (NL)":   {"lat": 52.1326, "lon":  5.2913, "ci_avg": 209},
            "Spain (ES)":         {"lat": 40.4637, "lon": -3.7492, "ci_avg":  89},
            "Italy (IT)":         {"lat": 41.8719, "lon": 12.5674, "ci_avg": 202},
            "Nordics (NO/SE/FI)": {"lat": 60.4720, "lon":  8.4689, "ci_avg":  30},
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
            "Australia (AUS)":   {"lat":-25.2744, "lon": 133.7751, "ci_avg": 450},
            "Japan (JP)":        {"lat": 36.2048, "lon": 138.2529, "ci_avg": 450},
            "Singapore (SG)":    {"lat":  1.3521, "lon": 103.8198, "ci_avg": 367},
            "South Korea (KR)":  {"lat": 35.9078, "lon": 127.7669, "ci_avg": 357},
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
    "Geyser / Water Heater": {"kw": 2.0,  "duration_min":  30, "deadline": "11:00"},
    "Washing Machine":        {"kw": 0.5,  "duration_min":  60, "deadline": "11:00"},
    "Iron Box":               {"kw": 1.0,  "duration_min":  30, "deadline": "11:00"},
    "Water Motor":            {"kw": 0.75, "duration_min":  30, "deadline": "08:00"},
    "EV Charger (3.3 kW)":   {"kw": 3.3,  "duration_min": 120, "deadline": "07:00"},
    "EV Charger (7.0 kW)":   {"kw": 7.0,  "duration_min": 120, "deadline": "07:00"},
    "Air Conditioner":        {"kw": 1.5,  "duration_min":  60, "deadline": "23:00"},
    "Induction Cooktop":      {"kw": 1.8,  "duration_min":  30, "deadline": "21:00"},
    "Microwave":              {"kw": 1.2,  "duration_min":  15, "deadline": "21:00"},
    "Laptop Charging":        {"kw": 0.06, "duration_min": 120, "deadline": "23:00"},
    "Custom":                 {"kw": 1.0,  "duration_min":  30, "deadline": "11:00"},
}

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
    return "Low" if ci < 200 else "Medium" if ci < 400 else "High"

def calculate_co2(kwh, ci):
    return (safe_float(kwh, 0) * safe_float(ci, 0)) / 1000.0

def validate_time_format(t):
    try:
        h, m = map(int, t.split(":"))
        return 0 <= h < 24 and 0 <= m < 60
    except:
        return False

def time_to_minutes(t):
    try:
        h, m = map(int, t.split(":"))
        return h * 60 + m
    except:
        return 0

def minutes_to_time(mins):
    return f"{(mins//60)%24:02d}:{mins%60:02d}"

def to_12hr(time_str):
    """HH:MM → h:MM AM/PM"""
    try:
        h, m = map(int, time_str.split(":"))
        period = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d} {period}"
    except:
        return time_str

def get_system_time(tz_str="UTC"):
    try:
        return datetime.now(pytz.timezone(tz_str))
    except:
        return datetime.now()

def get_current_time_24(tz_str="UTC", step=15):
    now = get_system_time(tz_str)
    m = (now.minute // step) * step
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
                try:
                    rows.append(json.loads(line))
                except:
                    pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def detect_location_ip():
    try:
        r = requests.get("https://ipapi.co/json/", timeout=5)
        if r.status_code == 200:
            d = r.json()
            return {"lat": d.get("latitude"), "lon": d.get("longitude"),
                    "city": d.get("city"), "country": d.get("country_name"),
                    "timezone": d.get("timezone")}
    except:
        pass
    return None

def fetch_electricity_maps_data(lat, lon, api_token=None):
    try:
        headers = {"auth-token": api_token} if api_token else {}
        r = requests.get(f"{ELECTRICITY_MAPS_API}carbon-intensity/latest",
                         headers=headers, params={"lat": lat, "lon": lon}, timeout=10)
        if r.status_code == 200:
            base_ci = r.json().get("carbonIntensity", 400)
            rows = []
            for h in range(24):
                for m in [0, 15, 30, 45]:
                    f = 0.7 if h<6 else 1.2 if h<10 else 1.0 if h<16 else 1.3 if h<22 else 0.8
                    ci = base_ci * f
                    rows.append({"time": f"{h:02d}:{m:02d}", "ci": ci,
                                 "thermal_mw": 5000*(ci/base_ci), "hydro_mw": 2000,
                                 "nuclear_mw": 1500, "res_mw": max(0, 3000-5000*(ci/base_ci-0.5))})
            return pd.DataFrame(rows)
    except:
        pass
    return None

def make_full_day_ci(df_ci):
    ci_dict = dict(zip(df_ci["time"], df_ci["ci"]))
    mean_ci = safe_float(df_ci["ci"].mean(), 400)
    rows = []
    for h in range(24):
        for m in [0, 15, 30, 45]:
            t  = f"{h:02d}:{m:02d}"
            ci = safe_float(ci_dict.get(t, mean_ci), mean_ci)
            rows.append({"time": t, "ci": ci,
                         "color": get_ci_color(ci), "status": get_ci_status(ci)})
    return pd.DataFrame(rows)

def recommend_windows(full_day_ci, duration_min, deadline, top_k, now_time):
    duration_min = ceil_to_step(duration_min, STEP_MIN)
    n_slots = duration_min // STEP_MIN
    try:
        dh, dm = map(int, deadline.split(":")); deadline_mins = dh*60+dm
    except:
        deadline_mins = 23*60+59
    try:
        nh, nm = map(int, now_time.split(":")); now_mins = nh*60+nm
    except:
        now_mins = 0
    if deadline_mins <= now_mins:
        deadline_mins += 1440
    times = full_day_ci["time"].tolist()
    cis   = full_day_ci["ci"].tolist()
    wins  = []
    for i in range(len(times)-n_slots+1):
        sh, sm = map(int, times[i].split(":"))
        s_mins = sh*60+sm
        adj = s_mins if s_mins >= now_mins else s_mins+1440
        if adj < now_mins: continue
        if adj+duration_min > deadline_mins: continue
        em = adj+duration_min
        et = f"{(em//60)%24:02d}:{em%60:02d}"
        wci = [safe_float(x, 500) for x in cis[i:i+n_slots]]
        avg = sum(wci)/len(wci)
        wins.append({"start": times[i], "end": et, "avg_ci": avg,
                     "color": get_ci_color(avg), "status": get_ci_status(avg)})
    wins.sort(key=lambda x: x["avg_ci"])
    return wins[:top_k]

def find_ci_at_time(full_day_ci, time_str):
    mask = full_day_ci["time"] == time_str
    return float(full_day_ci[mask]["ci"].iloc[0]) if mask.any() else None

# ============================================================
# Page config
# ============================================================
st.set_page_config(page_title="CarbonWise", page_icon="🌍", layout="wide")

# ============================================================
# Global CSS
# ============================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
* { font-family: 'Inter', sans-serif !important; }
.main { background-color: #0a0e17; }

/* Hide native Streamlit sidebar collapse arrow buttons */
[data-testid="collapsedControl"],
button[kind="header"],
[data-testid="stSidebarCollapseButton"] { display: none !important; }

.hero {
    background: linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);
    padding: 2rem; border-radius: 16px; margin-bottom: 2rem;
    border: 1px solid rgba(74,222,128,0.2);
}
.hero h1 { color:#4ade80;margin:0;font-size:2.5rem;font-weight:800; }
.hero p  { color:#94a3b8;margin:0.5rem 0 0;font-size:1.1rem; }

.location-badge {
    background:linear-gradient(135deg,#3b82f6,#1d4ed8);
    color:white;padding:0.5rem 1rem;border-radius:8px;
    font-size:0.875rem;font-weight:600;display:inline-flex;
    align-items:center;gap:0.5rem;border:1px solid rgba(59,130,246,0.3);
}
.kpi {
    background:linear-gradient(135deg,#1e293b,#0f172a);
    padding:1.5rem;border-radius:12px;text-align:center;
    border-left:4px solid #4ade80;
}
.metric-value { font-size:1.75rem;font-weight:800;color:white;margin-top:0.5rem; }
.metric-label { color:#94a3b8;font-size:0.875rem;text-transform:uppercase;
                letter-spacing:0.05em;font-weight:600; }
.card {
    background:linear-gradient(135deg,#1e293b,#0f172a);
    padding:1.5rem;border-radius:12px;
    border:1px solid rgba(148,163,184,0.1);
}
[data-testid="stSidebar"] { background:#111827 !important; }
.live-time {
    background:rgba(74,222,128,0.1);border:1px solid #4ade80;
    padding:0.75rem;border-radius:8px;color:#4ade80;
    font-weight:700;text-align:center;font-size:1.1rem;margin-bottom:1rem;
}
.timezone-info { color:#64748b;font-size:0.75rem;text-align:center;margin-top:0.25rem; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# Floating Action Buttons via components.html
# (renders in its own iframe so CSS/JS works perfectly)
# ============================================================
components.html("""
<!DOCTYPE html>
<html>
<head>
<style>
  body { margin:0; padding:0; background:transparent; overflow:hidden; }
  #fab {
    position: fixed;
    top: 8px; right: 8px;
    display: flex; gap: 6px; align-items: center;
    z-index: 2147483647;
  }
  .fb {
    width: 40px; height: 40px;
    background: linear-gradient(135deg,#1e293b,#0f172a);
    border: 1.5px solid rgba(74,222,128,0.55);
    border-radius: 10px;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: all .18s ease;
    box-shadow: 0 4px 16px rgba(0,0,0,.6);
    padding: 0;
  }
  .fb:hover {
    background: rgba(74,222,128,0.15);
    border-color: #4ade80;
    transform: scale(1.1);
    box-shadow: 0 6px 22px rgba(74,222,128,.3);
  }
  svg { width:18px;height:18px;fill:none;stroke:#4ade80;
        stroke-width:2;stroke-linecap:round;stroke-linejoin:round;pointer-events:none; }
</style>
</head>
<body>
<div id="fab">

  <!-- Sidebar toggle -->
  <button class="fb" id="btn-sb" title="Toggle Sidebar">
    <svg viewBox="0 0 24 24">
      <rect x="3" y="3" width="18" height="18" rx="2"/>
      <line x1="9" y1="3" x2="9" y2="21"/>
    </svg>
  </button>

  <!-- Fullscreen toggle -->
  <button class="fb" id="btn-fs" title="Toggle Fullscreen">
    <svg id="ic-exp" viewBox="0 0 24 24">
      <path d="M8 3H5a2 2 0 0 0-2 2v3"/>
      <path d="M21 8V5a2 2 0 0 0-2-2h-3"/>
      <path d="M3 16v3a2 2 0 0 0 2 2h3"/>
      <path d="M16 21h3a2 2 0 0 0 2-2v-3"/>
    </svg>
    <svg id="ic-cmp" viewBox="0 0 24 24" style="display:none">
      <path d="M8 3v3a2 2 0 0 1-2 2H3"/>
      <path d="M21 8h-3a2 2 0 0 1-2-2V3"/>
      <path d="M3 16h3a2 2 0 0 1 2 2v3"/>
      <path d="M16 21v-3a2 2 0 0 1 2-2h3"/>
    </svg>
  </button>

</div>
<script>
var pd = window.parent.document;

/* ── Sidebar ── */
document.getElementById('btn-sb').addEventListener('click', function(){
  var btn =
    pd.querySelector('[data-testid="collapsedControl"]') ||
    pd.querySelector('[data-testid="stSidebarCollapseButton"]') ||
    pd.querySelector('button[aria-label="Close sidebar"]') ||
    pd.querySelector('button[aria-label="Open sidebar"]');
  if(btn){ btn.click(); return; }
  var sb = pd.querySelector('[data-testid="stSidebar"]');
  if(sb){ sb.style.display = sb.style.display==='none' ? '' : 'none'; }
});

/* ── Fullscreen ── */
document.getElementById('btn-fs').addEventListener('click', function(){
  var exp=document.getElementById('ic-exp'), cmp=document.getElementById('ic-cmp');
  if(!pd.fullscreenElement){
    pd.documentElement.requestFullscreen()
      .then(function(){ exp.style.display='none'; cmp.style.display=''; })
      .catch(function(e){ console.warn(e); });
  } else {
    pd.exitFullscreen()
      .then(function(){ exp.style.display=''; cmp.style.display='none'; });
  }
});

/* Reset icon on Esc */
pd.addEventListener('fullscreenchange', function(){
  if(!pd.fullscreenElement){
    var exp=document.getElementById('ic-exp'), cmp=document.getElementById('ic-cmp');
    if(exp){ exp.style.display=''; } if(cmp){ cmp.style.display='none'; }
  }
});
</script>
</body>
</html>
""", height=0, scrolling=False)

# ============================================================
# Session state init
# ============================================================
if "appliance" not in st.session_state:
    st.session_state.appliance       = "Water Motor"
    st.session_state.kw              = 0.75
    st.session_state.duration        = 30
    st.session_state.deadline        = "08:00"
    st.session_state.selected_window = 0
    st.session_state.location_mode   = "Auto-Detect"
    st.session_state.selected_region = "India"
    st.session_state.selected_zone   = "North India (NR)"
    st.session_state.lat             = 28.6139
    st.session_state.lon             = 77.2090
    st.session_state.timezone        = "Asia/Kolkata"
    st.session_state.data_source     = "Automatic (API)"

# ============================================================
# Hero
# ============================================================
st.markdown("""
<div class="hero">
  <h1>🌍 CarbonWise</h1>
  <p>Location-aware carbon intensity optimization. Automatically detects your grid region
     and optimizes appliance scheduling for minimal CO₂ impact.</p>
  <div style="margin-top:1rem;">
    <span style="background:rgba(74,222,128,0.15);color:#4ade80;
                 border:1px solid rgba(74,222,128,0.3);padding:0.5rem 1rem;
                 border-radius:9999px;font-size:0.875rem;font-weight:600;">
      ⚡ Live Grid Optimization
    </span>
    <span style="margin-left:0.5rem;color:#64748b;font-size:0.9rem;">
      Powered by Electricity Maps &amp; System Time
    </span>
  </div>
</div>
""", unsafe_allow_html=True)

# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.title("⚙️ Configuration")
    st.markdown("### 📍 Location Settings")

    MODES = ["Auto-Detect", "GPS Location", "Manual Select", "Custom Coordinates"]
    location_mode = st.radio(
        "Location Mode", MODES,
        index=MODES.index(st.session_state.location_mode)
              if st.session_state.location_mode in MODES else 0
    )
    st.session_state.location_mode = location_mode

    # ── AUTO DETECT ──────────────────────────
    if location_mode == "Auto-Detect":
        if st.button("🔍 Detect via IP", use_container_width=True):
            with st.spinner("Detecting..."):
                loc = detect_location_ip()
                if loc:
                    st.session_state.lat      = loc["lat"]
                    st.session_state.lon      = loc["lon"]
                    st.session_state.timezone = loc.get("timezone","UTC")
                    st.success(f"📍 {loc['city']}, {loc['country']}")
                    st.rerun()
                else:
                    st.error("Detection failed.")
        st.markdown(f"""
        <div style="background:#1f2937;padding:.75rem;border-radius:8px;margin-top:.5rem;">
            <div style="color:#94a3b8;font-size:.75rem;">Detected Coordinates</div>
            <div style="color:#4ade80;font-size:.875rem;font-weight:600;">
                {st.session_state.lat:.4f}, {st.session_state.lon:.4f}
            </div>
        </div>""", unsafe_allow_html=True)

    # ── GPS LOCATION ─────────────────────────
    elif location_mode == "GPS Location":
        st.markdown("<div style='color:#94a3b8;font-size:.85rem;margin-bottom:6px;'>"
                    "Click the button below — your browser will ask for location permission.</div>",
                    unsafe_allow_html=True)

        # GPS HTML widget inside sidebar
        components.html("""
<!DOCTYPE html><html><head>
<style>
  body{margin:0;padding:0;background:transparent;font-family:Inter,sans-serif;}
  #btn{width:100%;padding:10px;background:linear-gradient(135deg,#1e3a5f,#0f172a);
       color:#4ade80;border:1.5px solid rgba(74,222,128,0.45);border-radius:8px;
       cursor:pointer;font-weight:600;font-size:13px;transition:all .2s;}
  #btn:hover{background:rgba(74,222,128,0.15);}
  #st{color:#64748b;font-size:12px;margin-top:5px;text-align:center;min-height:16px;}
</style>
</head><body>
<button id="btn" onclick="getGPS()">📡 Use GPS / Current Location</button>
<div id="st"></div>
<script>
function getGPS(){
  var btn=document.getElementById('btn'), st=document.getElementById('st');
  if(!navigator.geolocation){
    st.innerHTML='<span style="color:#f87171">Not supported by browser</span>'; return;
  }
  btn.textContent='⏳ Detecting...'; btn.disabled=true;
  navigator.geolocation.getCurrentPosition(
    function(p){
      var lat=p.coords.latitude.toFixed(6), lon=p.coords.longitude.toFixed(6);
      btn.textContent='✅ Location Captured';
      st.innerHTML='<span style="color:#4ade80">📍 '+lat+', '+lon+'</span><br>'
                  +'<span style="color:#64748b;font-size:11px;">Enter these below and click Apply</span>';
      /* copy to hidden inputs so user can see them */
      var li=document.getElementById('lat-in'), lo=document.getElementById('lon-in');
      if(li) li.value=lat; if(lo) lo.value=lon;
    },
    function(e){
      btn.textContent='📡 Use GPS / Current Location'; btn.disabled=false;
      st.innerHTML='<span style="color:#f87171">'+(e.code===1?'Permission denied':'Error getting location')+'</span>';
    },
    {enableHighAccuracy:true,timeout:10000}
  );
}
</script>
</body></html>""", height=90)

        st.markdown("<div style='color:#64748b;font-size:.75rem;margin-top:4px;'>Enter captured coordinates:</div>",
                    unsafe_allow_html=True)
        gps_lat_in = st.number_input("GPS Latitude",  -90.0,  90.0, value=st.session_state.lat, format="%.6f")
        gps_lon_in = st.number_input("GPS Longitude",-180.0, 180.0, value=st.session_state.lon, format="%.6f")
        if st.button("✅ Apply GPS Coordinates", use_container_width=True):
            st.session_state.lat = gps_lat_in
            st.session_state.lon = gps_lon_in
            loc = detect_location_ip()
            if loc and loc.get("timezone"):
                st.session_state.timezone = loc["timezone"]
            st.success(f"📍 GPS applied: {gps_lat_in:.4f}, {gps_lon_in:.4f}")
            st.rerun()

    # ── MANUAL SELECT ─────────────────────────
    elif location_mode == "Manual Select":
        sel_region = st.selectbox("Region", list(GRID_ZONES.keys()),
            index=list(GRID_ZONES.keys()).index(st.session_state.selected_region)
                  if st.session_state.selected_region in GRID_ZONES else 0)
        st.session_state.selected_region = sel_region

        zones = GRID_ZONES[sel_region]["zones"]
        sel_zone = st.selectbox("Grid Zone", list(zones.keys()),
            index=list(zones.keys()).index(st.session_state.selected_zone)
                  if st.session_state.selected_zone in zones else 0)
        st.session_state.selected_zone = sel_zone

        zd = zones[sel_zone]
        st.session_state.lat      = zd["lat"]
        st.session_state.lon      = zd["lon"]
        st.session_state.timezone = GRID_ZONES[sel_region]["timezone"]

        st.markdown(f"""
        <div style="background:#1f2937;padding:.75rem;border-radius:8px;margin-top:.5rem;">
            <div style="color:#94a3b8;font-size:.75rem;">Grid Zone Info</div>
            <div style="color:#4ade80;font-size:.875rem;font-weight:600;">Avg CI: {zd['ci_avg']} gCO₂/kWh</div>
            <div style="color:#64748b;font-size:.75rem;margin-top:.25rem;">{GRID_ZONES[sel_region]["voltage"]}</div>
        </div>""", unsafe_allow_html=True)

    # ── CUSTOM COORDINATES ───────────────────
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.session_state.lat = st.number_input("Latitude",  -90.0,  90.0,
                                                    value=st.session_state.lat, format="%.4f")
        with c2:
            st.session_state.lon = st.number_input("Longitude",-180.0, 180.0,
                                                    value=st.session_state.lon, format="%.4f")
        tz_opts = pytz.common_timezones
        cur_tz  = st.session_state.timezone if st.session_state.timezone in tz_opts else "UTC"
        st.session_state.timezone = st.selectbox("Timezone", tz_opts, index=tz_opts.index(cur_tz))

    loc_label = st.session_state.selected_zone \
                if location_mode == "Manual Select" \
                else ("GPS" if location_mode == "GPS Location" else f"{st.session_state.lat:.2f}, {st.session_state.lon:.2f}")
    st.markdown(f"""
    <div style="margin-top:1rem;">
        <span class="location-badge">🌍 {loc_label}</span>
    </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Live Clock (12-hr) ───────────────────
    exact_12 = get_current_time_exact_12(st.session_state.timezone)
    now24    = get_current_time_24(st.session_state.timezone, STEP_MIN)
    now12    = to_12hr(now24)

    st.markdown(f"""
    <div class="live-time">🕐 {exact_12}</div>
    <div class="timezone-info">
        {st.session_state.timezone}<br>
        Current slot: {now12}
    </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Data Source ──────────────────────────
    st.subheader("📁 Data Source")
    DS_OPTS = ["Automatic (API)", "Sample Data", "Upload CSV (Admin)"]
    data_source = st.radio("Select Mode", DS_OPTS,
        index=DS_OPTS.index(st.session_state.data_source)
              if st.session_state.data_source in DS_OPTS else 0)
    st.session_state.data_source = data_source

    uploaded_file = None
    api_token     = None

    if data_source == "Automatic (API)":
        st.info("🌐 Fetches real-time grid data based on your location")
        api_token = st.text_input("API Token (Optional)", type="password")
        if st.button("🔄 Refresh Grid Data", use_container_width=True):
            st.cache_data.clear(); st.rerun()
    elif data_source == "Upload CSV (Admin)":
        st.warning("👤 Admin Mode")
        uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
        with st.expander("📋 Format"):
            st.markdown("**Required:** `time`, `thermal_mw`, `hydro_mw`, `nuclear_mw`, `res_mw`")

    st.divider()

    # ── Appliance ───────────────────────────
    st.subheader("🔌 Appliance Settings")
    appliance = st.selectbox("Appliance", list(APPLIANCES.keys()), label_visibility="collapsed")
    if appliance != st.session_state.appliance:
        info = APPLIANCES[appliance]
        st.session_state.kw = info["kw"]; st.session_state.duration = info["duration_min"]
        st.session_state.deadline = info["deadline"]; st.session_state.appliance = appliance

    kw       = st.number_input("Power (kW)",        0.001, value=float(st.session_state.kw),    step=0.05, format="%.3f")
    duration = st.number_input("Duration (minutes)",   15, value=int(st.session_state.duration), step=15)
    deadline = st.text_input("Deadline (HH:MM)",        value=st.session_state.deadline)

    if not validate_time_format(deadline):
        st.error("⚠️ Use HH:MM format"); deadline = st.session_state.deadline
    else:
        st.session_state.deadline = deadline

    now_mins      = time_to_minutes(now24)
    deadline_mins = time_to_minutes(deadline)
    avail = (deadline_mins - now_mins) if deadline_mins > now_mins else (1440 - now_mins + deadline_mins)
    st.info(f"⏱️ Available: **{avail} min** ({avail//60}h {avail%60}m)")

    if st.button("🔄 Reset to Default"):
        info = APPLIANCES[appliance]
        st.session_state.kw = info["kw"]; st.session_state.duration = info["duration_min"]
        st.session_state.deadline = info["deadline"]; st.rerun()

    st.divider()
    st.subheader("⚡ Optimization")
    top_k = st.slider("Top Recommendations", 3, 12, 5)
    st.session_state.kw = kw; st.session_state.duration = duration

# ============================================================
# Data loading
# ============================================================
@st.cache_data(ttl=300)
def load_data_auto(lat, lon, tok=None):
    return fetch_electricity_maps_data(lat, lon, tok)

@st.cache_data(ttl=300)
def load_data_sample():
    DATA_DIR.mkdir(exist_ok=True)
    sf = DATA_DIR / "sample.csv"
    if not sf.exists():
        rows = []
        for h in range(24):
            for m in [0,15,30,45]:
                lf = 0.6 if h<5 else 0.8 if h<8 else 1.0 if h<12 else 1.1 if h<17 else 1.2 if h<21 else 0.9
                rows.append({"time":f"{h:02d}:{m:02d}",
                             "thermal_mw":int(3000*lf*0.6), "hydro_mw":1500,
                             "nuclear_mw":2000, "res_mw":int(800+(1200 if 10<=h<=16 else 0))})
        pd.DataFrame(rows).to_csv(sf, index=False)
    return pd.read_csv(sf)

raw = None
if st.session_state.data_source == "Automatic (API)":
    with st.spinner("🌐 Fetching grid data..."):
        raw = load_data_auto(st.session_state.lat, st.session_state.lon, api_token)
    if raw is None:
        st.warning("⚠️ API failed — using sample data.")
        raw = load_data_sample()
elif st.session_state.data_source == "Sample Data":
    raw = load_data_sample()
else:
    if uploaded_file:
        try:
            raw = pd.read_csv(uploaded_file)
            req = {"time","thermal_mw","hydro_mw","nuclear_mw","res_mw"}
            miss = req - set(raw.columns)
            if miss: st.error(f"Missing: {miss}"); st.stop()
        except Exception as e:
            st.error(str(e)); st.stop()
    else:
        st.info("Please upload a CSV or switch data source."); st.stop()

if raw is None or raw.empty:
    st.error("No data available."); st.stop()

if "ci" not in raw.columns:
    raw["ci"] = raw.apply(lambda r: compute_ci_g_per_kwh(
        safe_float(r["thermal_mw"]), safe_float(r["hydro_mw"]),
        safe_float(r["nuclear_mw"]), safe_float(r["res_mw"])), axis=1)

df_ci       = raw[["time","ci"]].dropna().sort_values("time").reset_index(drop=True)
full_day_ci = make_full_day_ci(df_ci)
now24       = get_current_time_24(st.session_state.timezone, STEP_MIN)
now12       = to_12hr(now24)
windows     = recommend_windows(full_day_ci, duration, deadline, top_k, now24)
current_ci  = find_ci_at_time(full_day_ci, now24) or float(full_day_ci["ci"].mean())
best        = windows[0] if windows else None
pot_savings = ((current_ci - best["avg_ci"])/current_ci*100) if (best and current_ci>0) else 0

# ============================================================
# KPI row
# ============================================================
st.markdown("### 📊 Current Session Overview")
k1,k2,k3,k4,k5 = st.columns(5)

with k1:
    lbl = (st.session_state.selected_zone if location_mode=="Manual Select"
           else "GPS" if location_mode=="GPS Location" else "Auto-Detected")
    st.markdown(f"""<div class="kpi">
        <div class="metric-label">🌍 Location</div>
        <div class="metric-value" style="font-size:1.1rem;">{lbl}</div>
        <div style="color:#64748b;font-size:.72rem;">{st.session_state.timezone}</div>
    </div>""", unsafe_allow_html=True)

with k2:
    st.markdown(f"""<div class="kpi">
        <div class="metric-label">🕐 System Time</div>
        <div class="metric-value" style="font-size:1.35rem;">{now12}</div>
        <div style="color:#64748b;font-size:.72rem;">Live • 12-hr</div>
    </div>""", unsafe_allow_html=True)

with k3:
    st.markdown(f"""<div class="kpi">
        <div class="metric-label">🔌 Appliance</div>
        <div class="metric-value" style="font-size:1.1rem;">{appliance.split('/')[0].strip()}</div>
        <div style="color:#64748b;font-size:.72rem;">{kw:.2f} kW • {ceil_to_step(duration)} min</div>
    </div>""", unsafe_allow_html=True)

with k4:
    cc = get_ci_color(current_ci)
    st.markdown(f"""<div class="kpi" style="border-left-color:{cc}">
        <div class="metric-label">⚡ Current CI</div>
        <div class="metric-value" style="color:{cc}">{current_ci:.0f}</div>
        <div style="color:#64748b;font-size:.72rem;">{get_ci_status(current_ci)} intensity</div>
    </div>""", unsafe_allow_html=True)

with k5:
    bc = "#4ade80" if best else "#64748b"
    bt = to_12hr(best["start"]) if best else "--:--"
    st.markdown(f"""<div class="kpi" style="border-left-color:{bc}">
        <div class="metric-label">✨ Best Window</div>
        <div class="metric-value" style="color:{bc};font-size:1.25rem;">{bt}</div>
        <div style="color:#64748b;font-size:.72rem;">
            {f"Save ~{pot_savings:.1f}%" if pot_savings>0 else "No savings"}
        </div>
    </div>""", unsafe_allow_html=True)

st.markdown("---")

# ============================================================
# Tabs
# ============================================================
tab1,tab2,tab3,tab4,tab5 = st.tabs(["📈 Dashboard","🎯 Smart Advisor","📊 Analytics","📝 Logger","🔍 Data"])

# ── TAB 1 ─────────────────────────────────────────────────
with tab1:
    c1,c2 = st.columns([2,1])
    with c1:
        st.subheader(f"24-Hour Carbon Intensity • {st.session_state.timezone}")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=full_day_ci["time"], y=full_day_ci["ci"],
            fill='tozeroy', fillcolor='rgba(74,222,128,0.1)',
            line=dict(color='#4ade80',width=3),
            hovertemplate='Time: %{x}<br>CI: %{y:.1f} gCO₂/kWh<extra></extra>'))
        fig.add_hrect(y0=0,   y1=200, fillcolor="#4ade80", opacity=0.05, line_width=0)
        fig.add_hrect(y0=200, y1=400, fillcolor="#fbbf24", opacity=0.05, line_width=0)
        fig.add_hrect(y0=400, y1=800, fillcolor="#f87171", opacity=0.05, line_width=0)
        for i,w in enumerate((windows or [])[:3]):
            fig.add_vrect(x0=w["start"],x1=w["end"],fillcolor=w["color"],
                          opacity=0.25,line_width=2 if i==0 else 1,line_color=w["color"],layer="below")
        fig.add_vline(x=now24,    line_dash="dash",line_color="#4ade80",line_width=2)
        fig.add_vline(x=deadline, line_dash="dash",line_color="#f87171",line_width=2)
        mx = max(full_day_ci["ci"])*1.05
        fig.add_annotation(x=now24,    y=mx, text="NOW",      showarrow=False, font=dict(color="#4ade80",size=11))
        fig.add_annotation(x=deadline, y=mx, text="DEADLINE", showarrow=False, font=dict(color="#f87171",size=11))
        fig.update_layout(plot_bgcolor="#0f172a",paper_bgcolor="#0f172a",
                          font=dict(color="#f8fafc"),
                          xaxis=dict(gridcolor="#334155",title="Time",tickangle=-45,nticks=12),
                          yaxis=dict(gridcolor="#334155",title="gCO₂/kWh",
                                     range=[0,max(full_day_ci["ci"])*1.15]),
                          height=450,margin=dict(l=60,r=40,t=40,b=60),showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.subheader("Grid Mix")
        latest = raw.iloc[-1]
        total  = sum(safe_float(latest[c]) for c in ["thermal_mw","hydro_mw","nuclear_mw","res_mw"])
        fig2 = go.Figure(data=[go.Pie(
            labels=["Thermal","Hydro","Nuclear","Renewable"],
            values=[safe_float(latest["thermal_mw"]),safe_float(latest["hydro_mw"]),
                    safe_float(latest["nuclear_mw"]),safe_float(latest["res_mw"])],
            hole=0.65, marker_colors=["#f87171","#60a5fa","#a78bfa","#4ade80"],
            textinfo="label+percent",textfont=dict(color="white",size=11))])
        fig2.update_layout(plot_bgcolor="#0f172a",paper_bgcolor="#0f172a",
                           font=dict(color="#f8fafc"),height=380,showlegend=False,
                           margin=dict(t=20,b=20,l=20,r=20),
                           annotations=[dict(text=f"<b>{total/1000:.1f}</b><br>GW",
                                             x=0.5,y=0.5,font_size=20,font_color="#f8fafc",showarrow=False)])
        st.plotly_chart(fig2, use_container_width=True)

# ── TAB 2 ─────────────────────────────────────────────────
with tab2:
    st.subheader(f"🏆 Top {top_k} Windows ({now12} → {to_12hr(deadline)}) • {st.session_state.timezone}")
    if not windows:
        st.warning("⚠️ No valid windows. Try extending deadline or reducing duration.")
    else:
        for row_start in range(0, len(windows), 3):
            rw   = windows[row_start:row_start+3]
            cols = st.columns(len(rw))
            for i,(col,w) in enumerate(zip(cols,rw)):
                gi   = row_start+i
                rank = gi+1
                with col:
                    hrs  = ceil_to_step(duration)/60
                    nrg  = float(kw)*hrs
                    co2  = calculate_co2(nrg, w["avg_ci"])
                    co2n = calculate_co2(nrg, current_ci)
                    sav  = co2n - co2
                    ib   = gi==0
                    brd  = "border:2px solid #4ade80;" if ib else "border:1px solid #374151;"
                    st.markdown(f"""
                    <div class="card" style="{brd}">
                      <div style="text-align:center;margin-bottom:.5rem;">
                        <span style="background:{'#4ade80' if ib else '#374151'};
                                     color:{'#0f172a' if ib else 'white'};
                                     padding:.25rem .75rem;border-radius:6px;
                                     font-size:.75rem;font-weight:700;">
                          {'🥇 BEST' if rank==1 else f'#{rank}'}
                        </span>
                      </div>
                      <div style="font-size:1.2rem;font-weight:800;color:white;text-align:center;">
                        {to_12hr(w["start"])} → {to_12hr(w["end"])}
                      </div>
                      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-top:.75rem;">
                        <div style="background:#0f172a;padding:.5rem;border-radius:8px;text-align:center;">
                          <div style="color:#64748b;font-size:.7rem;">CI</div>
                          <div style="color:{w['color']};font-size:1rem;font-weight:700;">{w["avg_ci"]:.0f}</div>
                        </div>
                        <div style="background:#0f172a;padding:.5rem;border-radius:8px;text-align:center;">
                          <div style="color:#64748b;font-size:.7rem;">CO₂</div>
                          <div style="color:white;font-size:1rem;font-weight:700;">{co2:.3f}kg</div>
                        </div>
                      </div>
                      <div style="background:rgba(74,222,128,.1);padding:.4rem;border-radius:6px;
                                  text-align:center;margin-top:.5rem;">
                        <span style="color:#4ade80;font-size:.8rem;">💰 Save {sav:.3f} kg</span>
                      </div>
                    </div>""", unsafe_allow_html=True)
                    if st.button("✅ Select", key=f"sel_{gi}", use_container_width=True):
                        st.session_state.selected_window = gi
                        st.success(f"Selected: {to_12hr(w['start'])} - {to_12hr(w['end'])}")

# ── TAB 3 ─────────────────────────────────────────────────
with tab3:
    st.subheader("📊 Baseline vs Optimized")
    hrs  = ceil_to_step(duration)/60
    nrg  = float(kw)*hrs
    co2n = calculate_co2(nrg, current_ci)
    co2b = calculate_co2(nrg, best["avg_ci"]) if best else co2n
    proj = co2n - co2b
    pct  = (proj/co2n*100) if co2n>0 else 0

    st.markdown(f"""
    <div style="background:#1e293b;padding:1rem;border-radius:8px;margin-bottom:1rem;">
      <div style="color:#94a3b8;font-size:.875rem;">Location</div>
      <div style="color:white;font-size:1rem;">
        {st.session_state.selected_zone if location_mode=="Manual Select"
         else f"Lat:{st.session_state.lat:.2f} Lon:{st.session_state.lon:.2f}"}
        • {st.session_state.timezone}
      </div>
    </div>""", unsafe_allow_html=True)

    fc = go.Figure()
    fc.add_trace(go.Bar(name='Baseline (Now)',    x=['CO₂'],y=[co2n],
                        marker_color='#f87171',text=[f'{co2n:.3f} kg'],textposition='outside',width=0.35))
    fc.add_trace(go.Bar(name='Optimized (Best)', x=['CO₂'],y=[co2b],
                        marker_color='#4ade80',text=[f'{co2b:.3f} kg'],textposition='outside',width=0.35))
    fc.update_layout(barmode='group',plot_bgcolor="#0f172a",paper_bgcolor="#0f172a",
                     font=dict(color="#f8fafc",size=14),height=350,showlegend=True,
                     legend=dict(orientation="h",yanchor="bottom",y=1.02,xanchor="center",x=0.5),
                     yaxis=dict(title="CO₂ (kg)",gridcolor="#334155"),
                     title=dict(text=f"💰 Savings: {proj:.3f} kg ({pct:.1f}%)",
                                font=dict(size=16,color="#4ade80")))
    st.plotly_chart(fc, use_container_width=True)

    s1,s2,s3 = st.columns(3)
    with s1: st.metric("🔴 Baseline",  f"{co2n:.3f} kg")
    with s2: st.metric("🟢 Optimized", f"{co2b:.3f} kg")
    with s3: st.metric("💰 Savings",   f"{pct:.1f}%", delta=f"-{proj:.3f} kg", delta_color="inverse")

    log_df = read_log()
    if len(log_df)>0:
        with st.expander("📋 Run History"):
            st.dataframe(log_df.sort_values("timestamp",ascending=False),
                         use_container_width=True, hide_index=True)

# ── TAB 4 ─────────────────────────────────────────────────
with tab4:
    ensure_dirs()
    st.subheader("📝 Log Appliance Run")
    with st.form("run_form"):
        st.markdown(f"**System Time:** {get_current_time_exact_12(st.session_state.timezone)} ({st.session_state.timezone})")
        fc1,fc2,fc3 = st.columns(3)
        with fc1:
            run_date = st.date_input("Date", datetime.now())
            run_type = st.selectbox("Run Type",["recommended","baseline","test"])
        with fc2:
            def_start  = (windows[st.session_state.selected_window]["start"]
                          if windows and st.session_state.selected_window < len(windows) else now24)
            start_time = st.text_input("Start Time (HH:MM)", def_start)
            run_dur    = st.number_input("Duration (min)", 15, value=ceil_to_step(int(duration)), step=15)
        with fc3:
            run_app = st.selectbox("Appliance", list(APPLIANCES.keys()))
            notes   = st.text_input("Notes",
                                    f"Loc:{st.session_state.selected_zone if location_mode=='Manual Select' else 'Auto'}")
        mc1,mc2 = st.columns(2)
        with mc1: mb = st.number_input("Meter Before (kWh)", 0.0, format="%.3f")
        with mc2: ma = st.number_input("Meter After (kWh)",  0.0, format="%.3f")
        if st.form_submit_button("💾 Save", use_container_width=True):
            kwh = ma - mb
            if kwh <= 0:
                st.error("'After' must be > 'Before'")
            else:
                ci_v = find_ci_at_time(full_day_ci, start_time) or float(full_day_ci["ci"].mean())
                et   = minutes_to_time(time_to_minutes(start_time) + ceil_to_step(int(run_dur)))
                row  = {"timestamp":datetime.now().isoformat(),"date":str(run_date),
                        "run_type":run_type,"appliance":run_app,
                        "start_time":start_time,"end_time":et,
                        "kwh_used":round(float(kwh),4),
                        "avg_ci_g_per_kwh":round(float(ci_v),2),
                        "co2_kg":round(calculate_co2(kwh,ci_v),4),
                        "location":(st.session_state.selected_zone if location_mode=="Manual Select"
                                    else f"{st.session_state.lat:.2f},{st.session_state.lon:.2f}"),
                        "timezone":st.session_state.timezone,"notes":notes}
                append_log(row); read_log.clear()
                st.success(f"✅ {kwh:.3f} kWh · {calculate_co2(kwh,ci_v):.4f} kg CO₂")
                if run_type=="recommended": st.balloons()

# ── TAB 5 ─────────────────────────────────────────────────
with tab5:
    st.subheader("🔍 Raw Data & Config")
    with st.expander("📍 Location Config"):
        st.json({"Mode":location_mode,"Lat":st.session_state.lat,
                 "Lon":st.session_state.lon,"Timezone":st.session_state.timezone,
                 "Data Source":st.session_state.data_source})
        if st.button("💾 Save Config"):
            save_location_config({"lat":st.session_state.lat,"lon":st.session_state.lon,
                                  "timezone":st.session_state.timezone})
            st.success("Saved!")

    cols_show = [c for c in ["time","thermal_mw","hydro_mw","nuclear_mw","res_mw","ci"] if c in raw.columns]
    st.dataframe(raw[cols_show].sort_values("time"), use_container_width=True, hide_index=True)

    d1,d2 = st.columns(2)
    with d1:
        st.download_button("📥 Download CSV",
                           data=raw[cols_show].to_csv(index=False).encode(),
                           file_name=f"carbonwise_{st.session_state.lat:.2f}_{st.session_state.lon:.2f}.csv",
                           mime="text/csv")
    with d2:
        if st.button("🔄 Refresh Data"):
            st.cache_data.clear(); st.rerun()
