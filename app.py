import streamlit as st
import pandas as pd
import plotly.express as px

from src.ci import compute_ci_g_per_kwh
from src.recommend import recommend_best_window

st.set_page_config(page_title="CarbonWise", layout="wide")
st.title("CarbonWise — Grid Carbon Intensity Advisor (Prototype)")

st.write("Upload NPP generation mix CSV (15-min) or use the sample file.")

use_sample = st.checkbox("Use sample data (data/sample_mix.csv)", value=True)

uploaded = None
if not use_sample:
    uploaded = st.file_uploader("Upload CSV", type=["csv"])

if use_sample:
    df = pd.read_csv("data/sample_mix.csv")
else:
    if uploaded is None:
        st.stop()
    df = pd.read_csv(uploaded)

# Expected columns for this simple version:
# time, thermal_mw, hydro_mw, nuclear_mw, res_mw
required_cols = {"time", "thermal_mw", "hydro_mw", "nuclear_mw", "res_mw"}
missing = required_cols - set(df.columns)
if missing:
    st.error(f"Missing columns: {missing}")
    st.stop()

df["ci"] = df.apply(lambda r: compute_ci_g_per_kwh(
    r["thermal_mw"], r["hydro_mw"], r["nuclear_mw"], r["res_mw"]
), axis=1)

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Carbon Intensity (gCO₂/kWh)")
    fig = px.line(df, x="time", y="ci", markers=True)
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(df[["time", "thermal_mw", "hydro_mw", "nuclear_mw", "res_mw", "ci"]])

with col2:
    st.subheader("Appliance Advisor")

    appliance = st.selectbox("Appliance", ["Geyser", "Washing Machine", "Iron Box", "Water Motor"])

    presets = {
        "Geyser": {"kw": 2.0, "duration": 30, "deadline": "07:30"},
        "Washing Machine": {"kw": 0.5, "duration": 60, "deadline": "10:00"},
        "Iron Box": {"kw": 1.0, "duration": 30, "deadline": "08:30"},   # rounded to 30
        "Water Motor": {"kw": 0.75, "duration": 30, "deadline": "08:00"},
    }

    st.caption("You can edit power/duration if needed.")
    kw = st.number_input("Power (kW)", value=presets[appliance]["kw"], step=0.05)
    duration = st.selectbox("Duration (minutes)", [30, 45, 60, 90], index=[30,45,60,90].index(presets[appliance]["duration"]))
    deadline = st.text_input("Deadline (HH:MM)", presets[appliance]["deadline"])

    best = recommend_best_window(df[["time", "ci"]], duration_minutes=duration, deadline_time=deadline)

    if best is None:
        st.warning("Not enough CI data points before the deadline.")
    else:
        st.success(f"Recommended start: {best['start']}  |  End: {best['end']}")
        st.metric("Average CI in window (gCO₂/kWh)", f"{best['avg_ci']:.1f}")

        # Emissions estimate
        hours = duration / 60.0
        U = kw * hours
        co2_kg = (U * best["avg_ci"]) / 1000.0
        st.metric("Estimated CO₂ for this run (kg)", f"{co2_kg:.3f}")

st.caption("Note: CI is estimated from generation mix using fixed emission factors (prototype).")