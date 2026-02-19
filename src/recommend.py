from datetime import datetime, timedelta

def _to_dt(t_str: str):
    return datetime.strptime(t_str, "%H:%M")

def rank_windows(df, duration_minutes: int, deadline_time: str, step_minutes: int = 15, top_k: int = 5):
    """
    df must have columns: time (HH:MM), ci (float)
    Returns top_k windows with minimum avg CI before deadline.
    """
    n = int(duration_minutes // step_minutes)
    if n <= 0:
        raise ValueError("duration_minutes must be >= step_minutes")

    x = df.copy()
    x["time"] = x["time"].astype(str)

    # Keep rows up to deadline
    x = x[x["time"] <= deadline_time].reset_index(drop=True)
    if len(x) < n:
        return []

    # Build lookup so we skip missing times (important when your CSV has gaps)
    lookup = {t: float(ci) for t, ci in zip(x["time"], x["ci"])}

    windows = []
    for start_time in x["time"]:
        start_dt = _to_dt(start_time)
        # Generate required time stamps for the window
        times = [(start_dt + timedelta(minutes=i*step_minutes)).strftime("%H:%M") for i in range(n)]

        # Ensure all timestamps exist in lookup
        if any(t not in lookup for t in times):
            continue

        avg_ci = sum(lookup[t] for t in times) / n
        end_time = (start_dt + timedelta(minutes=duration_minutes)).strftime("%H:%M")

        # Ensure end is not beyond deadline window requirement
        if end_time > deadline_time:
            continue

        windows.append({"start": start_time, "end": end_time, "avg_ci": float(avg_ci)})

    windows.sort(key=lambda d: d["avg_ci"])
    return windows[:top_k]

def window_avg_ci(df, start_time: str, duration_minutes: int, step_minutes: int = 15):
    """
    Compute avg CI for a specific run window.
    Returns None if any required 15-min time points are missing.
    """
    n = int(duration_minutes // step_minutes)
    if n <= 0:
        return None

    x = df.copy()
    x["time"] = x["time"].astype(str)
    lookup = {t: float(ci) for t, ci in zip(x["time"], x["ci"])}

    start_dt = _to_dt(start_time)
    times = [(start_dt + timedelta(minutes=i*step_minutes)).strftime("%H:%M") for i in range(n)]
    if any(t not in lookup for t in times):
        return None

    return sum(lookup[t] for t in times) / n