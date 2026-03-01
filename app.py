# ============================================================
# KPI Dashboard
# ============================================================
st.markdown("### 📊 Current Session Overview")

kpi_cols = st.columns(5)
with kpi_cols[0]:
    st.markdown(f"""
    <div class="kpi">
        <div class="metric-label">🌍 Location</div>
        <div class="metric-value" style="font-size: 1.2rem;">{st.session_state.selected_zone if st.session_state.location_mode == "Manual Select" else "Auto-Detected"}</div>
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
    st.markdown(f"""
    <div class="kpi" style="border-left-color: {best_color}">
        <div class="metric-label">✨ Best Window</div>
        <div class="metric-value" style="color: {best_color}">{best["start"] if best else "--:--"}</div>
        <div class="metric-sub">{f"Save ~{potential_savings:.1f}%" if potential_savings > 0 else "No savings"}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")
