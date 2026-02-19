import pandas as pd
import matplotlib.pyplot as plt

log_df = pd.read_csv("logs/geyser_runs.csv")

grp = log_df.groupby("run_type")["co2_kg"].mean().reset_index()

plt.bar(grp["run_type"], grp["co2_kg"])
plt.title("Average CO₂ (kg) — Baseline vs Recommended")
plt.xlabel("Run Type")
plt.ylabel("Average CO₂ (kg)")

plt.savefig("docs/results_summary_final.png")