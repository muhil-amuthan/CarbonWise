# CarbonWise — Grid Carbon Intensity Advisor (Prototype)

CarbonWise is a software prototype that estimates **grid carbon intensity (CI in gCO₂/kWh)** from electricity generation mix data and recommends **low‑CO₂ time windows** to run flexible home loads (e.g., geyser, washing machine). It also includes a **Run Logger** to record smart‑meter readings and compute **CO₂ savings** (Baseline vs Recommended).

## Problem
Electricity consumption is measured in kWh (units), but the **CO₂ emissions per kWh are not constant**. Grid emissions change throughout the day based on the **generation mix** (thermal vs renewables). Users can reduce emissions by shifting flexible loads to cleaner time windows.

## Solution (What this prototype does)
1. Takes generation mix data (Thermal, Hydro, Nuclear, RES) in MW.
2. Computes **Carbon Intensity (CI)** for each 15‑minute interval.
3. Displays CI as a **graph and table**.
4. Ranks **Top‑5 low‑CO₂ windows** before a user deadline.
5. Estimates CO₂ for an appliance run.
6. Logs real/simulated smart‑meter readings and shows **Baseline vs Recommended** CO₂ savings.

## Data Source (for real implementation)
Generation mix values are collected from India’s public dashboards such as:
- **National Power Portal (NPP)** — Real‑time data (Source‑Merit India)

> Note: In this prototype, CI is computed using fixed emission factors. Accuracy depends on data availability, granularity, and update frequency of the source.

## Key Formulas
### (1) Carbon Intensity (from generation mix)
If `P_s` is power (MW) from source `s` and `EF_s` is the emission factor (gCO₂/kWh):

\[
CI = \frac{\sum (P_s \times EF_s)}{\sum P_s}
\]

### (2) Appliance energy
\[
U(kWh) = kW \times hours
\]

### (3) CO₂ for a run
\[
CO₂(kg) = \frac{U \times CI}{1000}
\]

### (4) Savings
\[
Savings = CO₂_{baseline} - CO₂_{recommended}
\]

## Project Structure