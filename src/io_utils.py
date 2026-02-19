import os
import pandas as pd

LOG_PATH = "logs/geyser_runs.csv"

def ensure_log_file():
    os.makedirs("logs", exist_ok=True)
    if not os.path.exists(LOG_PATH):
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("date,run_type,appliance,start_time,end_time,meter_before_kwh,meter_after_kwh,kwh_used,avg_ci_g_per_kwh,co2_kg,notes\n")

def append_log(row: dict):
    ensure_log_file()
    pd.DataFrame([row]).to_csv(LOG_PATH, mode="a", header=False, index=False)

def read_log():
    ensure_log_file()
    return pd.read_csv(LOG_PATH)