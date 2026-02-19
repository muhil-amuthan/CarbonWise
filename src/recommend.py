import pandas as pd
from datetime import datetime, timedelta

def recommend_best_window(df, duration_minutes, deadline_time):
    step_minutes = 15
    n = int(duration_minutes // step_minutes)
    if n <= 0:
        raise ValueError("duration_minutes must be >= 15")

    x = df.copy()
    x["time"] = x["time"].astype(str)

    # keep rows up to deadline
    x = x[x["time"] <= deadline_time].reset_index(drop=True)
    if len(x) < n:
        return None

    best = None
    for i in range(0, len(x) - n + 1):
        window = x.iloc[i:i+n]
        avg_ci = float(window["ci"].mean())

        start_str = window.iloc[0]["time"]
        start_dt = datetime.strptime(start_str, "%H:%M")
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        end_str = end_dt.strftime("%H:%M")

        if best is None or avg_ci < best["avg_ci"]:
            best = {"start": start_str, "end": end_str, "avg_ci": avg_ci}

    return best