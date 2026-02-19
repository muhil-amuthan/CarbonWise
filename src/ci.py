def compute_ci_g_per_kwh(thermal_mw, hydro_mw, nuclear_mw, res_mw,
                         ef_thermal=950, ef_hydro=20, ef_nuclear=12, ef_res=20):
    """
    Compute grid carbon intensity (gCO2/kWh) from generation mix (MW).
    Uses weighted average of emission factors.
    """
    total = float(thermal_mw) + float(hydro_mw) + float(nuclear_mw) + float(res_mw)
    if total <= 0:
        return None
    ci = (thermal_mw*ef_thermal + hydro_mw*ef_hydro + nuclear_mw*ef_nuclear + res_mw*ef_res) / total
    return float(ci)