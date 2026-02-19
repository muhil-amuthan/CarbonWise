def compute_ci_g_per_kwh(
    thermal_mw, hydro_mw, nuclear_mw, res_mw,
    ef_thermal=950.0, ef_hydro=20.0, ef_nuclear=12.0, ef_res=20.0
):
    """
    Compute Carbon Intensity CI (gCO2/kWh) from generation mix (MW).
    CI = sum(P_s * EF_s) / sum(P_s)
    """
    thermal_mw = float(thermal_mw)
    hydro_mw = float(hydro_mw)
    nuclear_mw = float(nuclear_mw)
    res_mw = float(res_mw)

    total = thermal_mw + hydro_mw + nuclear_mw + res_mw
    if total <= 0:
        return None

    ci = (thermal_mw * ef_thermal +
          hydro_mw * ef_hydro +
          nuclear_mw * ef_nuclear +
          res_mw * ef_res) / total
    return float(ci)