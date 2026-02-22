import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import json

# ============================================================
# CI import + fallback
# ============================================================
try:
    from src.ci import compute_ci_g_per_kwh
except ImportError:
    def compute_ci_g_per_kwh(thermal, hydro, nuclear, res):
        total = thermal + hydro + nuclear + res
        if total == 0:
            return 400.0
        return (thermal * 800 + hydro * 24 + nuclear * 12 + res * 50) / total

# ============================================================
# ✅ Force JSONL logging ALWAYS (Run Logger + Results Summary)
# ============================================================
LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "runs.jsonl"

def ensure_log_file():
    LOG_DIR.mkdir(exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("", encoding="utf-8")

def append_log(row: dict):
    ensure_log_file()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")

def read_log() -> pd.DataFrame:
    ensure_log_file()
    rows = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pd.DataFrame(rows) if rows else pd.DataFrame()

# ============================================================
# Page config
# ============================================================
st.set_page_config(page_title="CarbonWise", page_icon="🌱", layout="wide")

# ============================================================
# Enhanced CSS (your UI)
# ============================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

/* IMPORTANT: Do not force Inter on '*' (breaks icons in some builds) */
body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
  font-family: 'Inter', sans-serif !important;
}

.hero { 
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%); 
    padding: 2.5rem; 
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

.kpi { 
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); 
    padding: 1.5rem; 
    border-radius: 12px; 
    text-align: center; 
    border-left: 4px solid #4ade80;
    transition: all 0.3s ease;
}
.kpi:hover {
    transform: translateY(-4px);
    box-shadow: 0 10px 30px -10px rgba(74, 222, 128, 0.3);
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
.metric-sub {
    color: #64748b;
    font-size: 0.875rem;
    margin-top: 0.25rem;
}

.card { 
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); 
    padding: 1.5rem; 
    border-radius: 12px; 
    margin-bottom: 1rem;
    border: 1px solid rgba(148, 163, 184, 0.1);
    transition: all 0.3s ease;
}
.card:hover {
    border-color: rgba(74, 222, 128, 0.3);
}
.best-window { 
    border: 2px solid #4ade80; 
    background: linear-gradient(135deg, rgba(74, 222, 128, 0.1) 0%, #0f172a 100%);
}

.comparison-card {
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
    border-radius: 16px;
    padding: 2rem;
    border: 1px solid rgba(74, 222, 128, 0.3);
    text-align: center;
}

.savings-big {
    font-size: 4rem;
    font-weight: 800;
    color: #4ade80;
    line-height: 1;
}

.badge {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 1rem;
    border-radius: 9999px;
    font-size: 0.875rem;
    font-weight: 600;
}
.badge-success { 
    background: rgba(74, 222, 128, 0.15); 
    color: #4ade80; 
    border: 1px solid rgba(74, 222, 128, 0.3);
}

.progress-bg {
    background: #334155;
    border-radius: 9999px;
    height: 8px;
    overflow: hidden;
    margin: 0.5rem 0;
}
.progress-fill {
    height: 100%;
    border-radius: 9999px;
    transition: width 0.5s ease;
}

.stTabs [data-baseweb="tab-list"] {
    gap: 0 !important;
    border-bottom: 1px solid #334155 !important;
}
.stTabs [data-baseweb="tab"] {
    padding: 1rem 1.5rem !important;
    font-weight: 600 !important;
    color: #94a3b8 !important;
}
.stTabs [data-baseweb="tab"]:hover { color: white !important; }
.stTabs [aria-selected="true"] {
    color: #4ade80 !important;
    border-bottom-color: #4ade80 !important;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# Constants + Appliances
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
# Helpers
# ============================================================
def ceil_to_step(mins, step=15):
    mins = int(mins)
    return mins if mins % step == 0 else mins + (step - mins % step)

def safe_float(v, default=0.0):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return default
        return float(v)
    except:
        return default

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
    return (safe_float(kwh, 0) * safe_float(ci, 0)) / 1000.0

def make_full_day_ci(df_ci):
    times, cis = [], []
    df_sorted = df_ci.sort_values("time").reset_index(drop=True)
    ci_dict = dict(zip(df_sorted["time"], df_sorted["ci"]))
    mean_ci = safe_float(df_sorted["ci"].mean(), 400)

    for h in range(24):
        for m in [0, 15, 30, 45]:
            t = f"{h:02d}:{m:02d}"
            times.append(t)
            cis.append(safe_float(ci_dict.get(t, mean_ci), mean_ci))

    df = pd.DataFrame({"time": times, "ci": cis})
    df["color"] = df["ci"].apply(get_ci_color)
    df["status"] = df["ci"].apply(get_ci_status)
    return df

def recommend_windows(full_day_ci, duration_min, deadline, top_k, now_time):
    duration_min = ceil_to_step(duration_min, STEP_MIN)
    n_slots = duration_min // STEP_MIN

    def to_mins(hhmm):
        try:
            h, m = map(int, hhmm.split(":"))
            return h * 60 + m
        except:
            return 0

    deadline_mins = to_mins(deadline)
    now_mins = to_mins(now_time)

    windows = []
    times = full_day_ci["time"].tolist()
    cis = full_day_ci["ci"].tolist()

    for i in range(len(times) - n_slots + 1):
        start_time = times[i]
        start_mins = to_mins(start_time)
        if start_mins < now_mins:
            continue

        end_mins = start_mins + duration_min
        if not ((end_mins <= deadline_mins) or (end_mins <= deadline_mins + 1440)):
            continue

        # ✅ Correct end time
        end_h = (end_mins // 60) % 24
        end_m = end_mins % 60
        end_time = f"{end_h:02d}:{end_m:02d}"

        window_cis = [safe_float(x, 500) for x in cis[i:i+n_slots]]
        avg_ci = sum(window_cis) / len(window_cis)

        windows.append({
            "start": start_time,
            "end": end_time,
            "avg_ci": avg_ci,
            "min_ci": min(window_cis),
            "max_ci": max(window_cis),
            "color": get_ci_color(avg_ci),
            "status": get_ci_status(avg_ci)
        })

    windows.sort(key=lambda x: x["avg_ci"])
    return windows[:top_k]

def find_ci_at_time(full_day_ci, time_str):
    mask = full_day_ci["time"] == time_str
    if mask.any():
        return float(full_day_ci[mask]["ci"].iloc[0])
    return None

def time_to_minutes(time_str):
    try:
        h, m = map(int, time_str.split(":"))
        return h * 60 + m
    except:
        return 0

# ============================================================
# Session state init
# ============================================================
if "appliance" not in st.session_state:
    st.session_state.appliance = "Water Motor"
    st.session_state.kw = 0.75
    st.session_state.duration = 30
    st.session_state.deadline = "08:00"
    st.session_state.now_time = datetime.now().strftime("%H:%M")

# ============================================================
# Hero
# ============================================================
st.markdown("""
<div class="hero">
    <h1>🌱 CarbonWise</h1>
    <p>Intelligent carbon intensity optimization for smart homes. Schedule appliances during low-carbon periods.</p>
    <span class="badge badge-success">⚡ Live Grid Optimization Active</span>
</div>
""", unsafe_allow_html=True)

# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.header("⚙️ Configuration")

    with st.expander("📁 Data Source", expanded=True):
        data_mode = st.radio("Select Mode", ["Sample Data", "Upload CSV"], index=0)
        uploaded = None
        if data_mode == "Upload CSV":
            uploaded = st.file_uploader("Upload CSV (time, thermal_mw, hydro_mw, nuclear_mw, res_mw)", type=["csv"])

    with st.expander("🔌 Appliance Settings", expanded=True):
        appliance = st.selectbox("Appliance", list(APPLIANCES.keys()))
        if appliance != st.session_state.appliance:
            info = APPLIANCES[appliance]
            st.session_state.kw = info["kw"]
            st.session_state.duration = info["duration_min"]
            st.session_state.deadline = info["deadline"]
            st.session_state.appliance = appliance

        kw = st.number_input("Power (kW)", 0.001, value=float(st.session_state.kw), step=0.05, format="%.3f")
        duration = st.number_input("Duration (minutes)", 15, value=int(st.session_state.duration), step=15)
        deadline = st.text_input("Deadline (HH:MM)", str(st.session_state.deadline))

    with st.expander("⚡ Optimization", expanded=True):
        top_k = st.slider("Top Recommendations", 3, 12, 5)
        now_time = st.text_input("Current Time (HH:MM)", st.session_state.now_time)

    st.session_state.kw = kw
    st.session_state.duration = duration
    st.session_state.deadline = deadline
    st.session_state.now_time = now_time

# ============================================================
# Load data
# ============================================================
@st.cache_data(ttl=300)
def load_data(mode, uploaded_file):
    if mode == "Sample Data":
        DATA_DIR.mkdir(exist_ok=True)
        sample_file = DATA_DIR / "sample.csv"
        if not sample_file.exists():
            data = []
            for h in range(24):
                for m in [0, 15, 30, 45]:
                    t = f"{h:02d}:{m:02d}"
                    thermal = 4500 if 18 <= h <= 22 else 3200
                    res = 3000 if 10 <= h <= 16 else 1200
                    hydro = 1200
                    nuclear = 900
                    data.append({"time": t, "thermal_mw": thermal, "hydro_mw": hydro, "nuclear_mw": nuclear, "res_mw": res})
            pd.DataFrame(data).to_csv(sample_file, index=False)
        return pd.read_csv(sample_file)
    else:
        return pd.read_csv(uploaded_file) if uploaded_file else None

raw = load_data(data_mode, uploaded)
if raw is None:
    st.error("❌ Please upload a CSV file or select Sample Data")
    st.stop()

missing = {"time", "thermal_mw", "hydro_mw", "nuclear_mw", "res_mw"} - set(raw.columns)
if missing:
    st.error(f"❌ Missing columns: {missing}")
    st.stop()

# Robust numeric conversion
for c in ["thermal_mw","hydro_mw","nuclear_mw","res_mw"]:
    raw[c] = pd.to_numeric(raw[c], errors="coerce")

raw = raw.dropna(subset=["thermal_mw","hydro_mw","nuclear_mw","res_mw"]).copy()
raw["time"] = raw["time"].astype(str)

raw["ci"] = raw.apply(lambda r: compute_ci_g_per_kwh(r["thermal_mw"], r["hydro_mw"], r["nuclear_mw"], r["res_mw"]), axis=1)
df_ci = raw[["time","ci"]].dropna().sort_values("time").reset_index(drop=True)

if len(df_ci) < 4:
    st.error("❌ Insufficient data points (minimum 4 required)")
    st.stop()

full_day_ci = make_full_day_ci(df_ci)
windows = recommend_windows(full_day_ci, duration, deadline, top_k, now_time)

current_ci = find_ci_at_time(full_day_ci, now_time)
if current_ci is None:
    current_ci = float(full_day_ci["ci"].mean())

best = windows[0] if windows else None
potential_savings = ((current_ci - best["avg_ci"]) / current_ci * 100) if best and current_ci > 0 else 0

# ============================================================
# KPI
# ============================================================
st.markdown("### 📊 Current Session Overview")

cols = st.columns(4)
with cols[0]:
    st.markdown(f"""
    <div class="kpi">
        <div class="metric-label">🔌 Appliance</div>
        <div class="metric-value">{appliance}</div>
        <div class="metric-sub">{kw:.2f} kW rated</div>
    </div>
    """, unsafe_allow_html=True)

with cols[1]:
    st.markdown(f"""
    <div class="kpi">
        <div class="metric-label">⏱️ Duration</div>
        <div class="metric-value">{ceil_to_step(duration)} min</div>
        <div class="metric-sub">Deadline: {deadline}</div>
    </div>
    """, unsafe_allow_html=True)

with cols[2]:
    ccol = get_ci_color(current_ci)
    st.markdown(f"""
    <div class="kpi" style="border-left-color: {ccol}">
        <div class="metric-label">🌍 Current CI</div>
        <div class="metric-value" style="color: {ccol}">{current_ci:.0f}</div>
        <div class="metric-sub">{get_ci_status(current_ci)} carbon intensity</div>
    </div>
    """, unsafe_allow_html=True)

with cols[3]:
    best_color = "#4ade80" if best else "#64748b"
    st.markdown(f"""
    <div class="kpi" style="border-left-color: {best_color}">
        <div class="metric-label">✨ Best Window</div>
        <div class="metric-value" style="color: {best_color}">{best["start"] if best else "--:--"}</div>
        <div class="metric-sub">{f"Save ~{potential_savings:.1f}%" if potential_savings > 0 else "No savings available"}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")

# ============================================================
# Tabs
# ============================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📈 Dashboard", "🎯 Smart Advisor", "📊 Analytics & Comparison", "📝 Run Logger", "🔍 Data Explorer"]
)

with tab1:
    col1, col2 = st.columns([2, 1])
    with col1:
        st.subheader("24-Hour Carbon Intensity Forecast")
        fig = px.line(full_day_ci, x="time", y="ci")
        fig.update_layout(height=420)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Current Grid Mix")
        latest = raw.iloc[-1]
        mix_data = pd.DataFrame({
            "Source": ["Thermal", "Hydro", "Nuclear", "Renewable"],
            "MW": [latest["thermal_mw"], latest["hydro_mw"], latest["nuclear_mw"], latest["res_mw"]],
            "Color": ["#f87171", "#60a5fa", "#a78bfa", "#4ade80"]
        })
        fig2 = go.Figure(data=[go.Pie(labels=mix_data["Source"], values=mix_data["MW"], hole=0.6, marker_colors=mix_data["Color"])])
        fig2.update_layout(height=380, showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)

with tab2:
    st.subheader("🏆 Top Recommended Time Windows")
    if not windows:
        st.warning("⚠️ No valid windows found before the deadline.")
    else:
        cols = st.columns(min(3, len(windows)))
        for i, (col, w) in enumerate(zip(cols, windows[:3])):
            with col:
                hours = ceil_to_step(duration) / 60
                energy = float(kw) * hours
                co2 = calculate_co2(energy, w["avg_ci"])
                st.markdown(f"""
                <div class="card {'best-window' if i==0 else ''}">
                    <div style="font-size: 1.6rem; font-weight: 800; color: white; text-align:center;">
                      {w["start"]} → {w["end"]}
                    </div>
                    <div style="color:#94a3b8; text-align:center; margin-top:6px;">
                      Avg CI: <b style="color:{w['color']}">{w["avg_ci"]:.0f}</b> g/kWh
                    </div>
                    <div style="color:white; text-align:center; margin-top:8px; font-weight:800;">
                      Est CO₂: {co2:.3f} kg
                    </div>
                </div>
                """, unsafe_allow_html=True)

with tab3:
    st.subheader("📊 Analytics & Comparison")
    log_df = read_log()

    if len(log_df) == 0:
        st.info("No runs logged yet. Log baseline + recommended runs to see savings.")
    else:
        for col in ["co2_kg", "kwh_used", "avg_ci_g_per_kwh"]:
            if col in log_df.columns:
                log_df[col] = pd.to_numeric(log_df[col], errors="coerce").fillna(0)

        if "date" in log_df.columns:
            log_df["date"] = pd.to_datetime(log_df["date"], errors="coerce")
        else:
            log_df["date"] = pd.Timestamp.today()

        grp = log_df.groupby("run_type")["co2_kg"].mean().reset_index() if "run_type" in log_df.columns else pd.DataFrame()
        if not grp.empty:
            fig_comp = px.bar(grp, x="run_type", y="co2_kg", title="Average CO₂ (kg)")
            fig_comp.update_layout(height=320)
            st.plotly_chart(fig_comp, use_container_width=True)

        if "kwh_used" not in log_df.columns:
            log_df["kwh_used"] = 0.0
        log_df["size"] = np.clip(10 + log_df["kwh_used"].fillna(0).values * 50, 10, 50)

        trend_fig = px.scatter(
            log_df,
            x="date",
            y="co2_kg",
            color="run_type" if "run_type" in log_df.columns else None,
            size="size",
            hover_data=["appliance", "start_time", "kwh_used"] if "appliance" in log_df.columns else None,
            title="CO₂ Emissions Over Time"
        )
        trend_fig.update_layout(height=360)
        st.plotly_chart(trend_fig, use_container_width=True)

with tab4:
    st.subheader("📝 Run Logger")
    ensure_log_file()

    with st.form("run_logger"):
        c1, c2, c3 = st.columns(3)
        with c1:
            run_date = st.date_input("📅 Date", datetime.now())
            run_type = st.selectbox("🏷️ Run Type", ["recommended", "baseline", "test"])
        with c2:
            start_time = st.text_input("⏰ Start Time (HH:MM)", value=windows[0]["start"] if windows else "09:00")
            run_duration = st.number_input("⏱️ Duration (minutes)", min_value=15, value=ceil_to_step(int(duration)), step=15)
        with c3:
            run_appliance = st.selectbox("🔌 Appliance", list(APPLIANCES.keys()))
            notes = st.text_input("📝 Notes (optional)", value="")

        m1, m2 = st.columns(2)
        with m1:
            meter_before = st.number_input("📊 Meter Before (kWh)", min_value=0.0, value=0.0, step=0.01, format="%.3f")
        with m2:
            meter_after = st.number_input("📊 Meter After (kWh)", min_value=0.0, value=0.0, step=0.01, format="%.3f")

        submitted = st.form_submit_button("💾 Save Run Log", use_container_width=True)

        if submitted:
            kwh_used = meter_after - meter_before
            if kwh_used <= 0:
                st.error("❌ Invalid readings: 'After' must be greater than 'Before'")
            else:
                avg_ci = find_ci_at_time(full_day_ci, start_time)
                if avg_ci is None:
                    avg_ci = float(full_day_ci["ci"].mean())

                start_mins = time_to_minutes(start_time)
                end_mins = start_mins + ceil_to_step(int(run_duration))
                end_h = (end_mins // 60) % 24
                end_m = end_mins % 60
                end_time = f"{end_h:02d}:{end_m:02d}"
                co2_out = calculate_co2(kwh_used, avg_ci)

                row = {
                    "timestamp": datetime.now().isoformat(),
                    "date": str(run_date),
                    "run_type": run_type,
                    "appliance": run_appliance,
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration_min": ceil_to_step(int(run_duration)),
                    "meter_before_kwh": round(float(meter_before), 4),
                    "meter_after_kwh": round(float(meter_after), 4),
                    "kwh_used": round(float(kwh_used), 4),
                    "avg_ci_g_per_kwh": round(float(avg_ci), 2),
                    "co2_kg": round(float(co2_out), 4),
                    "notes": notes
                }

                append_log(row)
                st.success(f"✅ Saved! kWh={kwh_used:.3f}, AvgCI={avg_ci:.1f}, CO₂={co2_out:.4f} kg")

with tab5:
    st.subheader("🔍 Data Explorer")
    show_cols = ["time", "thermal_mw", "hydro_mw", "nuclear_mw", "res_mw", "ci"]
    st.dataframe(raw[show_cols].sort_values("time"), use_container_width=True, hide_index=True)
    st.download_button("📥 Download Full Data (CSV)",
                       data=raw[show_cols].to_csv(index=False).encode("utf-8"),
                       file_name="carbonwise_data.csv",
                       mime="text/csv")