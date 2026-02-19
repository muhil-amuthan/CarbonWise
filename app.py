import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta

from src.ci import compute_ci_g_per_kwh
from src.recommend import rank_windows, window_avg_ci
from src.io_utils import ensure_log_file, append_log, read_log

st.set_page_config(page_title="CarbonWise", layout="wide")
st.title("CarbonWise — Grid Carbon Intensity Advisor (Prototype)")
st.write("Upload NPP generation mix CSV (15-min) or use the sample file.")

# ---------- Load CSV ----------
use_sample = st.checkbox("Use sample data (data/sample_mix.csv)", value=True)
uploaded = None
if not use_sample:
    uploaded = st.file_uploader("Upload CSV", type=["csv"])

def load_df():
    if use_sample:
        return pd.read_csv("data/sample_mix.csv")
    if uploaded is None:
        st.stop()
    return pd.read_csv(uploaded)

df = load_df()

required_cols = {"time", "thermal_mw", "hydro_mw", "nuclear_mw", "res_mw"}
missing = required_cols - set(df.columns)
if missing:
    st.error(f"Missing columns in CSV: {missing}")
    st.stop()

# Drop blank rows (important if you generated full-day template and filled only few rows)
df = df.dropna(subset=["thermal_mw", "hydro_mw", "nuclear_mw", "res_mw"]).copy()

# Compute CI
df["ci"] = df.apply(lambda r: compute_ci_g_per_kwh(
    r["thermal_mw"], r["hydro_mw"], r["nuclear_mw"], r["res_mw"]
), axis=1)

df["time"] = df["time"].astype(str)
df = df.sort_values("time").reset_index(drop=True)

# ---------- UI Layout ----------
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Carbon Intensity (gCO₂/kWh)")
    fig = px.line(df, x="time", y="ci", markers=True)
    st.plotly_chart(fig, width="stretch")
    st.dataframe(df[["time", "thermal_mw", "hydro_mw", "nuclear_mw", "res_mw", "ci"]], width="stretch")

with col2:
    st.subheader("Appliance Advisor")

    appliance = st.selectbox("Appliance", ["Geyser", "Washing Machine", "Iron Box", "Water Motor"])

    presets = {
        "Geyser": {"kw": 2.0, "duration": 30, "deadline": "11:00"},
        "Washing Machine": {"kw": 0.5, "duration": 60, "deadline": "11:00"},
        "Iron Box": {"kw": 1.0, "duration": 30, "deadline": "11:00"},
        "Water Motor": {"kw": 0.75, "duration": 30, "deadline": "11:00"},
    }

    kw = st.number_input("Power (kW)", value=float(presets[appliance]["kw"]), step=0.05)
    duration = st.selectbox("Duration (minutes)", [30, 45, 60, 90],
                            index=[30, 45, 60, 90].index(presets[appliance]["duration"]))
    deadline = st.text_input("Deadline (HH:MM)", presets[appliance]["deadline"])

    top = rank_windows(df[["time", "ci"]], duration_minutes=duration, deadline_time=deadline, top_k=5)

    if not top:
        st.warning("Not enough CI points before deadline. Fill more 15-min rows in your CSV.")
    else:
        st.success("Top-5 low-CO₂ windows:")
        st.table(pd.DataFrame(top))

        chosen = st.selectbox(
            "Select window",
            options=list(range(len(top))),
            format_func=lambda i: f"{top[i]['start']}–{top[i]['end']} (AvgCI {top[i]['avg_ci']:.1f})"
        )

        best = top[chosen]
        st.info(f"Recommended start: {best['start']} | End: {best['end']}")
        st.metric("Average CI in window (gCO₂/kWh)", f"{best['avg_ci']:.1f}")

        hours = duration / 60.0
        U_est = kw * hours
        co2_est = (U_est * best["avg_ci"]) / 1000.0
        st.metric("Estimated CO₂ for this run (kg)", f"{co2_est:.3f}")

st.divider()

# ---------- Run Logger ----------
st.subheader("Run Logger (Smart Meter Proof) — saves to logs/geyser_runs.csv")
ensure_log_file()

c1, c2, c3 = st.columns(3)

with c1:
    run_date = st.date_input("Date", value=pd.Timestamp.today())
    run_type = st.selectbox("Run Type", ["baseline", "recommended"])
    run_appliance = st.selectbox("Appliance for logging", ["Geyser", "Washing Machine", "Iron Box", "Water Motor"])

with c2:
    start_time = st.text_input("Actual Start Time (HH:MM)", value="09:00")
    run_duration = st.selectbox("Actual Duration (minutes)", [30, 45, 60, 90], index=0)
    notes = st.text_input("Notes (optional)", value="")

with c3:
    meter_before = st.number_input("Smart Meter Before (kWh)", value=0.0, step=0.01, format="%.2f")
    meter_after = st.number_input("Smart Meter After (kWh)", value=0.0, step=0.01, format="%.2f")

if st.button("Save Run Log"):
    kwh_used = meter_after - meter_before
    if kwh_used <= 0:
        st.error("kWh used must be > 0. Check meter readings.")
    else:
        avg_ci = window_avg_ci(df[["time", "ci"]], start_time=start_time, duration_minutes=run_duration)
        if avg_ci is None:
            st.error("CI not found for the full run window. Ensure your CSV contains CI rows covering this time window.")
        else:
            co2_kg = (kwh_used * avg_ci) / 1000.0
            end_time = (datetime.strptime(start_time, "%H:%M") + timedelta(minutes=run_duration)).strftime("%H:%M")

            row = {
                "date": str(run_date),
                "run_type": run_type,
                "appliance": run_appliance,
                "start_time": start_time,
                "end_time": end_time,
                "meter_before_kwh": meter_before,
                "meter_after_kwh": meter_after,
                "kwh_used": round(kwh_used, 4),
                "avg_ci_g_per_kwh": round(avg_ci, 2),
                "co2_kg": round(co2_kg, 4),
                "notes": notes
            }
            append_log(row)
            st.success(f"Saved. CO₂ = {co2_kg:.4f} kg, AvgCI = {avg_ci:.1f} g/kWh")

st.divider()

# ---------- Results Summary ----------
st.subheader("Results Summary (Baseline vs Recommended)")
log_df = read_log()

if len(log_df) == 0:
    st.info("No runs logged yet.")
else:
    st.dataframe(log_df, width="stretch")

    grp = log_df.groupby("run_type")["co2_kg"].mean().reset_index()
    st.write("Average CO₂ by run type:")
    st.table(grp)

    fig2 = px.bar(grp, x="run_type", y="co2_kg", title="Average CO₂ (kg) — Baseline vs Recommended")
    st.plotly_chart(fig2, width="stretch")

    if set(grp["run_type"]) >= {"baseline", "recommended"}:
        base = float(grp[grp["run_type"] == "baseline"]["co2_kg"].iloc[0])
        rec = float(grp[grp["run_type"] == "recommended"]["co2_kg"].iloc[0])
        saving = base - rec
        pct = (saving / base * 100.0) if base > 0 else 0.0
        st.metric("CO₂ Saved (kg)", f"{saving:.4f}")
        st.metric("CO₂ Saved (%)", f"{pct:.2f}%")

st.caption("Note: CI is estimated from NPP generation mix using fixed emission factors (prototype).")