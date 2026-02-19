import pandas as pd

def recommend_best_window(df, duration_minutes, deadline_time):
    """
    df columns: time (HH:MM), ci (float)
    duration_minutes: int (e.g., 30, 60)
    deadline_time: str "HH:MM" (e.g., "07:30")

    Assumes data is at 15-minute intervals.
    """
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
        start = window.iloc[0]["time"]
        end = window.iloc[-1]["time"]
        if best is None or avg_ci < best["avg_ci"]:
            best = {"start": start, "end": end, "avg_ci": avg_ci}
    return best