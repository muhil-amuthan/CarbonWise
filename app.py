import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from pathlib import Path
import numpy as np
import json
import requests
import pytz
from typing import Optional

# ============================================================
# CI import with fallback
# ============================================================
try:
    from src.ci import compute_ci_g_per_kwh
except ImportError:
    def compute_ci_g_per_kwh(thermal, hydro, nuclear, res):
        total = float(thermal)+float(hydro)+float(nuclear)+float(res)
        if total == 0: return 400.0
        return (float(thermal)*800+float(hydro)*24+float(nuclear)*12+float(res)*50)/total

GRID_ZONES = {
    "India": {"zones": {"North India (NR)":{"lat":28.6139,"lon":77.2090,"ci_avg":450},"South India (SR)":{"lat":13.0827,"lon":80.2707,"ci_avg":380},"West India (WR)":{"lat":19.0760,"lon":72.8777,"ci_avg":420},"East India (ER)":{"lat":22.5726,"lon":88.3639,"ci_avg":480},"North-East (NER)":{"lat":26.1445,"lon":91.7362,"ci_avg":350}},"timezone":"Asia/Kolkata","voltage":"230V/50Hz"},
    "Europe": {"zones": {"Germany (DE)":{"lat":51.1657,"lon":10.4515,"ci_avg":276},"France (FR)":{"lat":46.2276,"lon":2.2137,"ci_avg":16},"UK (GB)":{"lat":55.3781,"lon":-3.4360,"ci_avg":106},"Netherlands (NL)":{"lat":52.1326,"lon":5.2913,"ci_avg":209},"Spain (ES)":{"lat":40.4637,"lon":-3.7492,"ci_avg":89},"Italy (IT)":{"lat":41.8719,"lon":12.5674,"ci_avg":202},"Nordics (NO/SE/FI)":{"lat":60.4720,"lon":8.4689,"ci_avg":30}},"timezone":"Europe/Brussels","voltage":"230V/50Hz"},
    "United States": {"zones": {"California (CAISO)":{"lat":36.7783,"lon":-119.4179,"ci_avg":200},"Texas (ERCOT)":{"lat":31.9686,"lon":-99.9018,"ci_avg":350},"New York (NYISO)":{"lat":42.1657,"lon":-74.9481,"ci_avg":250},"Midwest (MISO)":{"lat":41.8780,"lon":-93.0977,"ci_avg":400},"PJM (East)":{"lat":39.8283,"lon":-77.5794,"ci_avg":320},"Pacific Northwest":{"lat":45.5152,"lon":-122.6784,"ci_avg":100}},"timezone":"America/New_York","voltage":"120V/60Hz"},
    "Asia Pacific": {"zones": {"Australia (AUS)":{"lat":-25.2744,"lon":133.7751,"ci_avg":450},"Japan (JP)":{"lat":36.2048,"lon":138.2529,"ci_avg":450},"Singapore (SG)":{"lat":1.3521,"lon":103.8198,"ci_avg":367},"South Korea (KR)":{"lat":35.9078,"lon":127.7669,"ci_avg":357}},"timezone":"Asia/Tokyo","voltage":"100V-240V/50-60Hz"},
}

ELECTRICITY_MAPS_API = "https://api-access.electricitymaps.com/free-tier/"
LOG_FILE   = Path("logs/runs.jsonl")
CONFIG_FILE= Path("config/location.json")
DATA_DIR   = Path("data")
STEP_MIN   = 15

APPLIANCES = {
    "Geyser / Water Heater":{"kw":2.0,"duration_min":30,"deadline":"11:00"},
    "Washing Machine":{"kw":0.5,"duration_min":60,"deadline":"11:00"},
    "Iron Box":{"kw":1.0,"duration_min":30,"deadline":"11:00"},
    "Water Motor":{"kw":0.75,"duration_min":30,"deadline":"08:00"},
    "EV Charger (3.3 kW)":{"kw":3.3,"duration_min":120,"deadline":"07:00"},
    "EV Charger (7.0 kW)":{"kw":7.0,"duration_min":120,"deadline":"07:00"},
    "Air Conditioner":{"kw":1.5,"duration_min":60,"deadline":"23:00"},
    "Induction Cooktop":{"kw":1.8,"duration_min":30,"deadline":"21:00"},
    "Microwave":{"kw":1.2,"duration_min":15,"deadline":"21:00"},
    "Laptop Charging":{"kw":0.06,"duration_min":120,"deadline":"23:00"},
    "Custom":{"kw":1.0,"duration_min":30,"deadline":"11:00"},
}

# ── helpers ──────────────────────────────────────────────────
def ensure_dirs():
    for d in ["logs","config","data"]: Path(d).mkdir(exist_ok=True)
    if not LOG_FILE.exists(): LOG_FILE.write_text("")

def append_log(row):
    ensure_dirs()
    with open(LOG_FILE,"a") as f: f.write(json.dumps(row)+"\n")

def save_location_config(cfg):
    ensure_dirs()
    with open(CONFIG_FILE,"w") as f: json.dump(cfg,f,indent=2)

@st.cache_data(ttl=300)
def read_log():
    ensure_dirs()
    rows=[]
    with open(LOG_FILE) as f:
        for line in f:
            line=line.strip()
            if line:
                try: rows.append(json.loads(line))
                except: pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def safe_float(v,d=0.0):
    try:
        if v is None or (isinstance(v,float) and np.isnan(v)): return d
        return float(v)
    except: return d

def ceil_to_step(m,s=15):
    m=int(m); return m if m%s==0 else m+(s-m%s)

def get_ci_color(ci):
    ci=safe_float(ci,500)
    return "#4ade80" if ci<200 else ("#fbbf24" if ci<400 else "#f87171")

def get_ci_status(ci):
    ci=safe_float(ci,500)
    return "Low" if ci<200 else ("Medium" if ci<400 else "High")

def calculate_co2(kwh,ci): return (safe_float(kwh)*safe_float(ci))/1000.0

def format_time_12h(h,m):
    p="AM" if h<12 else "PM"; h12=h%12 or 12; return f"{h12}:{m:02d} {p}"

def get_system_time(tz="UTC"):
    try: return datetime.now(pytz.timezone(tz))
    except: return datetime.now()

def get_current_time(tz="UTC",step=15):
    n=get_system_time(tz); return format_time_12h(n.hour,(n.minute//step)*step)

def get_current_time_exact(tz="UTC"):
    n=get_system_time(tz); p="AM" if n.hour<12 else "PM"; h=n.hour%12 or 12
    return f"{h}:{n.minute:02d}:{n.second:02d} {p}"

def to24(t):
    try:
        t=t.strip(); pm="PM" in t.upper()
        tp=t.upper().replace("AM","").replace("PM","").strip()
        h,m=map(int,tp.split(":")); 
        if pm and h!=12: h+=12
        elif not pm and h==12: h=0
        return f"{h:02d}:{m:02d}"
    except: return t

def to12(t):
    try: h,m=map(int,t.split(":")); return format_time_12h(h,m)
    except: return t

def t2m(t):
    try:
        t=t.strip().upper(); pm="PM" in t
        tp=t.replace("AM","").replace("PM","").strip(); h,m=map(int,tp.split(":"))
        if pm and h!=12: h+=12
        elif not pm and h==12: h=0
        return h*60+m
    except:
        try: h,m=map(int,t.split(":")): return h*60+m
        except: return 0

def m2t(mins): return format_time_12h((mins//60)%24,mins%60)

def validate_time(t):
    try:
        t=t.strip().upper()
        if "AM" in t or "PM" in t:
            tp=t.replace("AM","").replace("PM","").strip(); h,m=map(int,tp.split(":"))
            return 1<=h<=12 and 0<=m<60
        h,m=map(int,t.split(";")); return 0<=h<24 and 0<=m<60
    except: 
        try:
            tp=t.strip(); h,m=map(int,tp.split(":")): return 0<=h<24 and 0<=m<60
        except: return False

def detect_ip():
    try:
        r=requests.get("https://ipapi.co/json/",timeout=5)
        if r.status_code==200:
            d=r.json(); return {"lat":d.get("latitude"),"lon":d.get("longitude"),"city":d.get("city"),"country":d.get("country_name"),"timezone":d.get("timezone")}
    except: pass
    return None

def fetch_em_data(lat,lon,token=None):
    try:
        hdrs={"auth-token":token} if token else {}
        r=requests.get(f"{ELECTRICITY_MAPS_API}carbon-intensity/latest",headers=hdrs,params={"lat":lat,"lon":lon},timeout=10)
        if r.status_code==200:
            bc=r.json().get("carbonIntensity",400); rows=[]
            for h in range(24):
                for m in [0,15,30,45]:
                    f=0.7 if h<6 else (1.2 if h<10 else (1.0 if h<16 else (1.3 if h<22 else 0.8)))
                    ci=bc*f
                    rows.append({"time":f"{h:02d}:{m:02d}","ci":ci,"thermal_mw":5000*(ci/bc),"hydro_mw":2000,"nuclear_mw":1500,"res_mw":max(0,3000-5000*(ci/bc-0.5))})
            return pd.DataFrame(rows)
    except: pass
    return None

def make_full_day(df_ci):
    cd=dict(zip(df_ci["time"],df_ci["ci"])); mc=safe_float(df_ci["ci"].mean(),400)
    rows=[{"time":f"{h:02d}:{m:02d}","ci":safe_float(cd.get(f"{h:02d}:{m:02d}",mc),mc)} for h in range(24) for m in [0,15,30,45]]
    df=pd.DataFrame(rows); df["color"]=df["ci"].apply(get_ci_color); df["status"]=df["ci"].apply(get_ci_status)
    return df

def recommend(fdc,dur,dl,topk,now_t):
    dur=ceil_to_step(dur); ns=dur//15
    dlm=t2m(dl); nm=t2m(now_t)
    if dlm<=nm: dlm+=1440
    times=fdc["time"].tolist(); cis=fdc["ci"].tolist(); wins=[]
    for i in range(len(times)-ns+1):
        sh,sm=map(int,times[i].split(":")); sam=sh*60+sm
        if sam<nm: sam+=1440
        if sam<nm: continue
        em=sam+dur
        if em>dlm: continue
        wc=[safe_float(cis[j],500) for j in range(i,i+ns)]; avg=sum(wc)/len(wc)
        eh,emm=(em//60)%24,em%60
        wins.append({"start":times[i],"end":f"{eh:02d}:{emm:02d}","avg_ci":avg,"color":get_ci_color(avg),"status":get_ci_status(avg),"start_mins":sam})
    wins.sort(key=lambda x:x["avg_ci"]); return wins[:topk]

def find_ci(fdc,ts):
    t24=to24(ts); m=fdc["time"]==t24
    return float(fdc[m]["ci"].iloc[0]) if m.any() else None

# ============================================================
# Page config
# ============================================================
st.set_page_config(page_title="CarbonWise", page_icon="🌍", layout="wide")

# ── Session defaults ─────────────────────────────────────────
DEFS=dict(appliance="Water Motor",kw=0.75,duration=30,deadline="8:00 AM",
          selected_window=0,location_mode="Auto-Detect",selected_region="India",
          selected_zone="North India (NR)",lat=28.6139,lon=77.2090,timezone="Asia/Kolkata",
          data_source="Automatic (API)",
          user_name="",user_email="",user_joined="",user_preferred_zone="North India (NR)",
          show_user_dash=False)
for k,v in DEFS.items():
    if k not in st.session_state: st.session_state[k]=v

# ============================================================
# Global CSS
# ============================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
*{font-family:'Inter',sans-serif!important}
.main{background:#0a0e17}
header{visibility:hidden}.stApp>header{display:none}
.hero{background:linear-gradient(135deg,#0f172a,#1e3a5f);padding:2rem;border-radius:16px;margin-bottom:2rem;border:1px solid rgba(74,222,128,.2);margin-top:-60px}
.hero h1{color:#4ade80;margin:0;font-size:2.5rem;font-weight:800}
.hero p{color:#94a3b8;margin:.5rem 0 0;font-size:1.1rem}
.kpi{background:linear-gradient(135deg,#1e293b,#0f172a);padding:1.5rem;border-radius:12px;text-align:center;border-left:4px solid #4ade80;min-height:120px}
.metric-value{font-size:1.5rem;font-weight:800;color:white;margin-top:.5rem;word-wrap:break-word}
.metric-label{color:#94a3b8;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
.card{background:linear-gradient(135deg,#1e293b,#0f172a);padding:1.5rem;border-radius:12px;border:1px solid rgba(148,163,184,.1)}
.location-badge{background:linear-gradient(135deg,#3b82f6,#1d4ed8);color:white;padding:.5rem 1rem;border-radius:8px;font-size:.875rem;font-weight:600;display:inline-flex;gap:.5rem}
.live-time{background:rgba(74,222,128,.1);border:1px solid #4ade80;padding:.75rem;border-radius:8px;color:#4ade80;font-weight:700;text-align:center;font-size:1.1rem;margin-bottom:1rem}
.tz-info{color:#64748b;font-size:.75rem;text-align:center;margin-top:.25rem}
[data-testid="stSidebar"]{background:#111827!important}
[data-testid="stSidebar"] .stRadio>div{background:#1f2937!important;border-radius:8px;padding:.5rem}
.fc{position:fixed;top:1rem;right:1rem;z-index:999999;display:flex;gap:.5rem;background:rgba(15,23,42,.92);padding:.5rem;border-radius:12px;border:1px solid rgba(74,222,128,.3);backdrop-filter:blur(10px);box-shadow:0 4px 20px rgba(0,0,0,.5)}
.cbtn{background:linear-gradient(135deg,#1e293b,#0f172a);border:1px solid rgba(74,222,128,.25);color:#4ade80;width:42px;height:42px;border-radius:8px;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:all .25s;font-size:1.25rem;user-select:none}
.cbtn:hover{background:linear-gradient(135deg,#4ade80,#22c55e);color:#0f172a;transform:scale(1.08);box-shadow:0 0 14px rgba(74,222,128,.45)}
.udash{background:linear-gradient(135deg,#0f172a,#1e293b);border:2px solid rgba(74,222,128,.4);border-radius:20px;padding:2rem;margin-bottom:2rem}
.avatar{width:90px;height:90px;border-radius:50%;background:linear-gradient(135deg,#4ade80,#22c55e);display:flex;align-items:center;justify-content:center;font-size:2.2rem;font-weight:800;color:#0f172a;margin:0 auto 1rem;border:4px solid rgba(74,222,128,.5);box-shadow:0 0 24px rgba(74,222,128,.35)}
.sbox{background:rgba(30,41,59,.85);border:1px solid rgba(74,222,128,.2);border-radius:12px;padding:1rem;text-align:center}
.sval{font-size:1.6rem;font-weight:800;color:#4ade80}
.slbl{font-size:.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;margin-top:.25rem}
.bchip{display:inline-block;background:rgba(74,222,128,.15);border:1px solid rgba(74,222,128,.4);color:#4ade80;padding:.2rem .65rem;border-radius:9999px;font-size:.72rem;font-weight:600;margin:.15rem}
[data-testid="stSidebar"] .profile-btn button{background:linear-gradient(135deg,#052e16,#14532d)!important;color:#4ade80!important;border:2px solid #4ade80!important;border-radius:10px!important;font-weight:700!important;font-size:.95rem!important;box-shadow:0 0 14px rgba(74,222,128,.35)!important;min-height:2.8rem!important;}

/* ═══════════════════════════════════════
   LEFT PROFILE FAB BUTTON
═══════════════════════════════════════ */
.lpfab{position:fixed;left:0;top:50%;transform:translateY(-50%);z-index:99998;}
.lpfab-btn{
  width:44px;height:44px;
  background:linear-gradient(135deg,#14532d,#052e16);
  border:2px solid #4ade80;border-left:none;
  border-radius:0 12px 12px 0;
  display:flex;align-items:center;justify-content:center;
  cursor:pointer;outline:none;
  box-shadow:4px 0 24px rgba(74,222,128,.3);
  transition:width .25s ease,box-shadow .25s ease,background .2s;
  position:relative;overflow:visible;
}
.lpfab-btn:hover{width:56px;background:linear-gradient(135deg,#166534,#14532d);box-shadow:6px 0 32px rgba(74,222,128,.5);}
.lpfab-btn svg{color:#4ade80;width:22px;height:22px;pointer-events:none;flex-shrink:0;transition:transform .2s;}
.lpfab-btn:hover svg{transform:scale(1.12);}
.lpfab-pulse{
  position:absolute;left:0;top:-2px;bottom:-2px;right:-2px;
  border:2px solid #4ade80;border-left:none;border-radius:0 12px 12px 0;
  animation:lpPulse 2.5s ease-out infinite;pointer-events:none;
}
@keyframes lpPulse{0%{opacity:.6;transform:scale(1);}70%{opacity:0;transform:scale(1.55) translateX(8px);}100%{opacity:0;transform:scale(1.55) translateX(8px);}}
.lpfab-tip{
  position:absolute;left:52px;top:50%;transform:translateY(-50%) translateX(-6px);
  background:#1e293b;border:1px solid rgba(74,222,128,.3);
  color:#4ade80;font-size:.72rem;font-weight:600;
  white-space:nowrap;padding:.3rem .75rem;border-radius:7px;
  opacity:0;transition:opacity .2s,transform .2s;pointer-events:none;
}
.lpfab-btn:hover+.lpfab-tip{opacity:1;transform:translateY(-50%) translateX(0);}

/* ═══════════════════════════════════════
   OVERLAY
═══════════════════════════════════════ */
.lpoverlay{
  display:none;position:fixed;inset:0;
  background:rgba(0,0,0,.58);backdrop-filter:blur(5px);
  z-index:100000;
}
.lpoverlay.lpopen{display:block;}

/* ═══════════════════════════════════════
   SLIDE-IN PANEL
═══════════════════════════════════════ */
.lppanel{
  position:fixed;left:0;top:0;bottom:0;
  width:min(390px,93vw);
  background:linear-gradient(160deg,#0f172a 0%,#111827 55%,#091a10 100%);
  border-right:1px solid rgba(74,222,128,.2);
  z-index:100001;
  transform:translateX(-100%);
  transition:transform .35s cubic-bezier(.4,0,.2,1);
  display:flex;flex-direction:column;overflow:hidden;
}
.lppanel::before{
  content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,transparent,#4ade80,#22c55e,transparent);
}
.lppanel.lpopen{transform:translateX(0);}

/* Panel header */
.lp-hdr{display:flex;align-items:center;justify-content:space-between;padding:1.4rem 1.4rem 1rem;border-bottom:1px solid rgba(74,222,128,.1);}
.lp-hdr-title{color:#4ade80;font-size:.88rem;font-weight:700;letter-spacing:.06em;display:flex;align-items:center;gap:.5rem;}
.lp-close{width:32px;height:32px;background:#1e293b;border:1px solid rgba(148,163,184,.15);border-radius:8px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:#64748b;transition:all .2s;}
.lp-close:hover{background:rgba(248,113,113,.1);border-color:#f87171;color:#f87171;}

/* Panel body */
.lp-body{flex:1;overflow-y:auto;padding:1.3rem;scrollbar-width:thin;scrollbar-color:rgba(74,222,128,.2) transparent;}

/* Avatar row */
.lp-arow{display:flex;align-items:center;gap:1rem;margin-bottom:1.3rem;}
.lp-avatar{width:62px;height:62px;border-radius:50%;background:linear-gradient(135deg,#4ade80,#22c55e);display:flex;align-items:center;justify-content:center;font-size:1.4rem;font-weight:800;color:#0f172a;border:3px solid rgba(74,222,128,.38);box-shadow:0 0 20px rgba(74,222,128,.22);flex-shrink:0;transition:transform .2s;}
.lp-avatar:hover{transform:scale(1.07);}
.lp-aname{font-size:1rem;font-weight:700;color:white;}
.lp-asub{font-size:.76rem;color:#64748b;margin-top:.15rem;}
.lp-pill{display:inline-flex;align-items:center;gap:.32rem;background:rgba(74,222,128,.1);border:1px solid rgba(74,222,128,.28);color:#4ade80;font-size:.65rem;font-weight:600;padding:.18rem .55rem;border-radius:999px;margin-top:.38rem;}
.lp-dot{width:6px;height:6px;border-radius:50%;background:#4ade80;animation:lpblink 1.5s infinite;}
@keyframes lpblink{0%,100%{opacity:1}50%{opacity:.22}}

/* Stats */
.lp-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:.55rem;margin-bottom:1.3rem;}
.lp-sbox{background:#1e293b;border:1px solid rgba(74,222,128,.12);border-radius:10px;padding:.65rem .4rem;text-align:center;}
.lp-sval{font-size:1.15rem;font-weight:800;color:#4ade80;}
.lp-slbl{font-size:.6rem;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-top:.22rem;}

/* Section title */
.lp-section{font-size:.68rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin:1.1rem 0 .7rem;display:flex;align-items:center;gap:.5rem;}
.lp-section::after{content:'';flex:1;height:1px;background:rgba(148,163,184,.08);}

/* Fields */
.lp-lbl{font-size:.68rem;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:.35rem;}
.lp-inp{width:100%;background:#1e293b;border:1px solid rgba(148,163,184,.12);border-radius:9px;padding:.62rem .82rem;font-size:.875rem;color:#e2e8f0;outline:none;margin-bottom:.9rem;transition:border-color .2s,box-shadow .2s;font-family:inherit;}
.lp-inp:focus{border-color:rgba(74,222,128,.45);box-shadow:0 0 0 3px rgba(74,222,128,.07);}
.lp-inp::placeholder{color:#374151;}
select.lp-inp{appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2364748b' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right .8rem center;cursor:pointer;}

/* Badges */
.lp-badges{display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:.5rem;}
.lp-badge{display:inline-flex;align-items:center;gap:.3rem;background:rgba(74,222,128,.06);border:1px solid rgba(74,222,128,.18);color:#4ade80;font-size:.69rem;font-weight:600;padding:.22rem .55rem;border-radius:6px;opacity:.42;transition:opacity .25s,background .25s;}
.lp-badge.earned{opacity:1;background:rgba(74,222,128,.14);}

/* Save button */
.lp-save{width:100%;padding:.78rem;margin-top:.6rem;background:linear-gradient(135deg,#166534,#14532d);border:1px solid #4ade80;border-radius:9px;color:#4ade80;font-size:.88rem;font-weight:700;cursor:pointer;font-family:inherit;display:flex;align-items:center;justify-content:center;gap:.45rem;transition:all .2s;box-shadow:0 4px 14px rgba(74,222,128,.13);}
.lp-save:hover{background:linear-gradient(135deg,#15803d,#166534);box-shadow:0 6px 22px rgba(74,222,128,.28);transform:translateY(-1px);}
.lp-save:active{transform:translateY(0);}

/* Panel footer */
.lp-footer{padding:.9rem 1.3rem;border-top:1px solid rgba(74,222,128,.1);font-size:.68rem;color:#475569;display:flex;justify-content:space-between;}

/* Toast */
.lp-toast{position:fixed;bottom:2rem;left:50%;transform:translateX(-50%) translateY(90px);background:linear-gradient(135deg,#14532d,#052e16);border:1px solid #4ade80;color:#4ade80;font-size:.82rem;font-weight:600;padding:.68rem 1.5rem;border-radius:9px;display:flex;align-items:center;gap:.45rem;z-index:200000;box-shadow:0 8px 28px rgba(74,222,128,.2);transition:transform .32s cubic-bezier(.34,1.56,.64,1);}
.lp-toast.lpshow{transform:translateX(-50%) translateY(0);}
</style>
""", unsafe_allow_html=True)

# ── Left Profile FAB + Slide-in Panel (pure HTML/JS, no Streamlit widgets) ──
st.markdown("""
<!-- ═══ LEFT PROFILE FAB ═══ -->
<div class="lpfab">
  <button class="lpfab-btn" onclick="lpOpen()" title="My Profile">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="8" r="4"/>
      <path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>
    </svg>
    <div class="lpfab-pulse"></div>
  </button>
  <div class="lpfab-tip">My Profile</div>
</div>

<!-- ═══ OVERLAY ═══ -->
<div class="lpoverlay" id="lpOverlay" onclick="lpClose()"></div>

<!-- ═══ SLIDE-IN PANEL ═══ -->
<div class="lppanel" id="lpPanel">

  <div class="lp-hdr">
    <div class="lp-hdr-title">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>
      USER PROFILE
    </div>
    <div class="lp-close" onclick="lpClose()">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 6 6 18M6 6l12 12"/></svg>
    </div>
  </div>

  <div class="lp-body">

    <!-- Avatar row -->
    <div class="lp-arow">
      <div class="lp-avatar" id="lpAvatar">?</div>
      <div>
        <div class="lp-aname" id="lpDispName">Guest User</div>
        <div class="lp-asub"  id="lpDispEmail">No email set</div>
        <div class="lp-pill"><span class="lp-dot"></span>Active Session</div>
      </div>
    </div>

    <!-- Stats -->
    <div class="lp-stats">
      <div class="lp-sbox"><div class="lp-sval" id="lpRuns">0</div><div class="lp-slbl">Runs</div></div>
      <div class="lp-sbox"><div class="lp-sval" id="lpKwh">0.00</div><div class="lp-slbl">kWh</div></div>
      <div class="lp-sbox"><div class="lp-sval" id="lpCo2">0.000</div><div class="lp-slbl">CO₂ kg</div></div>
    </div>

    <!-- Edit section -->
    <div class="lp-section">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
      Edit Details
    </div>

    <div class="lp-lbl">Full Name</div>
    <input class="lp-inp" id="lpName" type="text" placeholder="Your full name" oninput="lpUpdateAv()">

    <div class="lp-lbl">Email Address</div>
    <input class="lp-inp" id="lpEmail" type="email" placeholder="you@example.com">

    <div class="lp-lbl">Preferred Grid Zone</div>
    <select class="lp-inp" id="lpZone">
      <optgroup label="🇮🇳 India">
        <option>North India (NR)</option>
        <option>South India (SR)</option>
        <option>West India (WR)</option>
        <option>East India (ER)</option>
        <option>North-East (NER)</option>
      </optgroup>
      <optgroup label="🇪🇺 Europe">
        <option>Germany (DE)</option>
        <option>France (FR)</option>
        <option>UK (GB)</option>
        <option>Netherlands (NL)</option>
        <option>Spain (ES)</option>
        <option>Italy (IT)</option>
        <option>Nordics (NO/SE/FI)</option>
      </optgroup>
      <optgroup label="🇺🇸 United States">
        <option>California (CAISO)</option>
        <option>Texas (ERCOT)</option>
        <option>New York (NYISO)</option>
        <option>Midwest (MISO)</option>
        <option>PJM (East)</option>
        <option>Pacific Northwest</option>
      </optgroup>
      <optgroup label="🌏 Asia Pacific">
        <option>Australia (AUS)</option>
        <option>Japan (JP)</option>
        <option>Singapore (SG)</option>
        <option>South Korea (KR)</option>
      </optgroup>
    </select>

    <!-- Badges section -->
    <div class="lp-section">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="6"/><path d="M15.477 12.89 17 22l-5-3-5 3 1.523-9.11"/></svg>
      Achievements
    </div>
    <div class="lp-badges">
      <div class="lp-badge" id="lpB1">🌱 Green Starter</div>
      <div class="lp-badge" id="lpB2">⚡ Eco Optimizer</div>
      <div class="lp-badge" id="lpB3">🌍 Carbon Hero</div>
      <div class="lp-badge" id="lpB4">📋 Profile Set</div>
    </div>

    <button class="lp-save" onclick="lpSave()">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
      Save Profile
    </button>

  </div>

  <div class="lp-footer">
    <span>🌍 CarbonWise</span>
    <span style="color:rgba(74,222,128,.35)">v2.1.0</span>
  </div>
</div>

<!-- Toast -->
<div class="lp-toast" id="lpToast">
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
  Profile saved successfully!
</div>

<script>
// ── state ──
var lpState = {
  name:'', email:'', zone:'North India (NR)',
  runs:0, kwh:0, co2:0
};

function lpOpen() {
  document.getElementById('lpPanel').classList.add('lpopen');
  document.getElementById('lpOverlay').classList.add('lpopen');
}
function lpClose() {
  document.getElementById('lpPanel').classList.remove('lpopen');
  document.getElementById('lpOverlay').classList.remove('lpopen');
}
document.addEventListener('keydown', function(e){ if(e.key==='Escape') lpClose(); });

function lpUpdateAv() {
  var n = document.getElementById('lpName').value.trim();
  var parts = n.split(/\\s+/).slice(0,2);
  var ini = parts.map(function(w){ return w[0] ? w[0].toUpperCase() : ''; }).join('') || '?';
  document.getElementById('lpAvatar').textContent = ini;
  document.getElementById('lpDispName').textContent = n || 'Guest User';
}

function lpSave() {
  var name  = document.getElementById('lpName').value.trim();
  var email = document.getElementById('lpEmail').value.trim();
  var zone  = document.getElementById('lpZone').value;

  // Update display
  document.getElementById('lpDispName').textContent  = name  || 'Guest User';
  document.getElementById('lpDispEmail').textContent = email || 'No email set';

  // Avatar initials
  var parts = (name||'').split(/\\s+/).slice(0,2);
  var ini = parts.map(function(w){ return w[0] ? w[0].toUpperCase() : ''; }).join('') || '?';
  document.getElementById('lpAvatar').textContent = ini;

  // Badges
  if(name && email) document.getElementById('lpB4').classList.add('earned');
  lpState.runs += 1;
  document.getElementById('lpRuns').textContent = lpState.runs;
  if(lpState.runs >= 1) document.getElementById('lpB1').classList.add('earned');
  if(lpState.runs >= 5) document.getElementById('lpB2').classList.add('earned');

  // Update kWh & CO2 mock
  lpState.kwh  = parseFloat((lpState.kwh + 0.5).toFixed(2));
  lpState.co2  = parseFloat((lpState.co2 + 0.1).toFixed(3));
  document.getElementById('lpKwh').textContent = lpState.kwh.toFixed(2);
  document.getElementById('lpCo2').textContent = lpState.co2.toFixed(3);
  if(lpState.co2 >= 1.0) document.getElementById('lpB3').classList.add('earned');

  lpShowToast();
}

function lpShowToast() {
  var t = document.getElementById('lpToast');
  t.classList.add('lpshow');
  setTimeout(function(){ t.classList.remove('lpshow'); }, 2700);
}
</script>
""", unsafe_allow_html=True)

# ── Floating sidebar/fullscreen controls ──
st.markdown("""
<div class="fc">
  <div class="cbtn" onclick="toggleSB()" title="Sidebar (F10)"><span id="sbi">☰</span></div>
  <div class="cbtn" onclick="toggleFS()" title="Fullscreen (F11)">⛶</div>
</div>
<script>
let sv=true;
function toggleSB(){const b=document.body,i=document.getElementById('sbi');
  if(sv){b.classList.add('sbh');i.innerHTML='→';}else{b.classList.remove('sbh');i.innerHTML='☰';}
  sv=!sv;setTimeout(()=>window.dispatchEvent(new Event('resize')),300);}
function toggleFS(){if(!document.fullscreenElement)document.documentElement.requestFullscreen();
  else document.exitFullscreen();}
document.addEventListener('keydown',e=>{
  if(e.key==='F11'){e.preventDefault();toggleFS();}
  if(e.key==='F10'){e.preventDefault();toggleSB();}
});
</script>
<style>
.sbh [data-testid="stSidebar"]{margin-left:-330px!important;transition:.3s}
.sbh .main .block-container{max-width:100%!important;padding:2rem!important}
</style>
""", unsafe_allow_html=True)

# ============================================================
# User dashboard as @st.dialog
# ============================================================
@st.dialog("👤 My Profile & Dashboard", width="large")
def show_user_dashboard():
    st.markdown("""
    <style>
    [data-testid="stDialog"]>div{background:#0f172a!important;border:2px solid rgba(74,222,128,.4)!important;border-radius:20px!important;max-width:820px!important}
    [data-testid="stDialog"] label,[data-testid="stDialog"] p,[data-testid="stDialog"] span{color:#e2e8f0!important}
    </style>""", unsafe_allow_html=True)

    log_df = read_log()
    total_runs = len(log_df) if not log_df.empty else 0
    total_kwh  = float(log_df["kwh_used"].sum()) if not log_df.empty and "kwh_used" in log_df.columns else 0.0
    total_co2  = float(log_df["co2_kg"].sum())   if not log_df.empty and "co2_kg"   in log_df.columns else 0.0

    initials = "".join(w[0].upper() for w in st.session_state.user_name.split()[:2]) if st.session_state.user_name else "?"

    ca, cs = st.columns([1,3], gap="medium")
    with ca:
        st.markdown(f"""
        <div class="avatar">{initials}</div>
        <div style="text-align:center;color:#4ade80;font-weight:700;font-size:1.1rem;">{st.session_state.user_name or "Guest User"}</div>
        <div style="text-align:center;color:#64748b;font-size:.8rem;margin-top:.2rem;">{st.session_state.user_email or "No email set"}</div>
        {"<div style='text-align:center;color:#475569;font-size:.72rem;margin-top:.2rem;'>Member since "+st.session_state.user_joined+"</div>" if st.session_state.user_joined else ""}
        """, unsafe_allow_html=True)

    with cs:
        s1,s2,s3 = st.columns(3)
        for col,val,lbl in zip([s1,s2,s3],[total_runs,f"{total_kwh:.2f}",f"{total_co2:.3f}"],["Runs Logged","kWh Used","CO₂ (kg)"]):
            with col:
                st.markdown(f'<div class="sbox"><div class="sval">{val}</div><div class="slbl">{lbl}</div></div>', unsafe_allow_html=True)

    st.divider()
    st.markdown("#### ✏️ Edit Profile")
    f1,f2 = st.columns(2)
    with f1:
        new_name  = st.text_input("Full Name",  value=st.session_state.user_name,  placeholder="Your name")
        new_email = st.text_input("Email",      value=st.session_state.user_email, placeholder="you@example.com")
    with f2:
        all_zones = [z for reg in GRID_ZONES.values() for z in reg["zones"]]
        pidx = all_zones.index(st.session_state.user_preferred_zone) if st.session_state.user_preferred_zone in all_zones else 0
        new_zone = st.selectbox("Preferred Grid Zone", all_zones, index=pidx)

    if st.button("💾  Save Profile", use_container_width=True, type="primary"):
        st.session_state.user_name           = new_name.strip()
        st.session_state.user_email          = new_email.strip()
        st.session_state.user_preferred_zone = new_zone
        if not st.session_state.user_joined:
            st.session_state.user_joined = datetime.now().strftime("%B %Y")
        st.success("✅ Profile saved successfully!")

    st.divider()
    st.markdown("#### 🏅 Achievement Badges")
    badges = [
        ("🌱","Green Starter",  total_runs>=1,  f"{total_runs}/1 run"),
        ("⚡","Eco Optimizer",  total_runs>=5,  f"{total_runs}/5 runs"),
        ("🌍","Carbon Hero",    total_co2>=1.0, f"{total_co2:.2f}/1 kg CO₂"),
        ("📧","Profile Complete",bool(st.session_state.user_name and st.session_state.user_email),"Fill name & email"),
    ]
    bc = st.columns(4)
    for col,(icon,name,earned,hint) in zip(bc,badges):
        with col:
            bg = "rgba(74,222,128,.18)" if earned else "#1e293b"
            bd = "#4ade80" if earned else "#374151"
            st.markdown(f"""
            <div style="background:{bg};border:1px solid {bd};border-radius:12px;padding:.9rem;text-align:center;">
                <div style="font-size:1.8rem;">{icon}</div>
                <div style="color:white;font-weight:700;font-size:.85rem;margin-top:.3rem;">{name}</div>
                <div style="color:{'#4ade80' if earned else '#64748b'};font-size:.7rem;margin-top:.2rem;">{'✅ Earned!' if earned else hint}</div>
            </div>""", unsafe_allow_html=True)

    st.divider()
    st.markdown("#### 🗓️ Recent Activity")
    if log_df.empty:
        st.info("No runs logged yet. Use the **Logger** tab to record your first run!")
    else:
        disp=[c for c in ["date","appliance","start_time","kwh_used","co2_kg","location_zone","run_type"] if c in log_df.columns]
        st.dataframe(log_df[disp].sort_values("date",ascending=False).head(8), use_container_width=True, hide_index=True)

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown('<div class="profile-btn">', unsafe_allow_html=True)
    btn_lbl = f"👤  {st.session_state.user_name}" if st.session_state.user_name else "👤  My Profile"
    if st.button(btn_lbl, use_container_width=True, key="open_user_dash"):
        show_user_dashboard()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<hr style='border-color:rgba(74,222,128,.2);margin:.5rem 0;'>", unsafe_allow_html=True)
    st.title("⚙️ Configuration")
    st.markdown("### 📍 Location Settings")

    location_mode = st.radio("Location Mode",["Auto-Detect","Manual Select","Custom Coordinates"],
                              index=["Auto-Detect","Manual Select","Custom Coordinates"].index(st.session_state.location_mode))
    st.session_state.location_mode = location_mode

    if location_mode=="Auto-Detect":
        if st.button("🔍 Detect My Location", use_container_width=True):
            with st.spinner("Detecting…"):
                loc=detect_ip()
                if loc:
                    st.session_state.lat=loc["lat"]; st.session_state.lon=loc["lon"]
                    st.session_state.timezone=loc.get("timezone","UTC")
                    st.success(f"📍 {loc['city']}, {loc['country']}"); st.rerun()
                else: st.error("Detection failed.")
        st.markdown(f'<div style="background:#1f2937;padding:.75rem;border-radius:8px;margin-top:.5rem;"><div style="color:#94a3b8;font-size:.75rem;">Coordinates</div><div style="color:#4ade80;font-size:.875rem;font-weight:600;">{st.session_state.lat:.4f}, {st.session_state.lon:.4f}</div></div>', unsafe_allow_html=True)

    elif location_mode=="Manual Select":
        sr=st.selectbox("Region",list(GRID_ZONES.keys()),index=list(GRID_ZONES.keys()).index(st.session_state.selected_region) if st.session_state.selected_region in GRID_ZONES else 0)
        st.session_state.selected_region=sr
        zones=GRID_ZONES[sr]["zones"]
        sz=st.selectbox("Grid Zone",list(zones.keys()),index=list(zones.keys()).index(st.session_state.selected_zone) if st.session_state.selected_zone in zones else 0)
        st.session_state.selected_zone=sz; zd=zones[sz]
        st.session_state.lat=zd["lat"]; st.session_state.lon=zd["lon"]; st.session_state.timezone=GRID_ZONES[sr]["timezone"]
        st.markdown(f'<div style="background:#1f2937;padding:.75rem;border-radius:8px;margin-top:.5rem;"><div style="color:#4ade80;font-size:.875rem;font-weight:600;">Avg CI: {zd["ci_avg"]} gCO₂/kWh</div><div style="color:#64748b;font-size:.75rem;">{GRID_ZONES[sr]["voltage"]}</div></div>', unsafe_allow_html=True)
    else:
        c1,c2=st.columns(2)
        with c1: st.session_state.lat=st.number_input("Lat",-90.0,90.0,st.session_state.lat,format="%.4f")
        with c2: st.session_state.lon=st.number_input("Lon",-180.0,180.0,st.session_state.lon,format="%.4f")
        tz_opts=pytz.common_timezones; cur_tz=st.session_state.timezone if st.session_state.timezone in tz_opts else "UTC"
        st.session_state.timezone=st.selectbox("Timezone",tz_opts,index=tz_opts.index(cur_tz))

    ld=st.session_state.selected_zone if location_mode=="Manual Select" else f"{st.session_state.lat:.2f}, {st.session_state.lon:.2f}"
    st.markdown(f'<div style="margin-top:1rem;"><span class="location-badge">🌍 {ld}</span></div>', unsafe_allow_html=True)
    st.divider()

    cte=get_current_time_exact(st.session_state.timezone); ctr=get_current_time(st.session_state.timezone,STEP_MIN)
    st.markdown(f'<div class="live-time">🕐 {cte}</div><div class="tz-info">System Time ({st.session_state.timezone})<br>Rounded: {ctr} (15-min)</div>', unsafe_allow_html=True)
    st.divider()

    st.subheader("📁 Data Source")
    data_source=st.radio("Select Mode",["Automatic (API)","Sample Data","Upload CSV (Admin)"],
                          index=["Automatic (API)","Sample Data","Upload CSV (Admin)"].index(st.session_state.data_source) if st.session_state.data_source in ["Automatic (API)","Sample Data","Upload CSV (Admin)"] else 0)
    st.session_state.data_source=data_source
    uploaded_file=None; api_token=None
    if data_source=="Automatic (API)":
        st.info("🌐 Real-time grid data")
        api_token=st.text_input("API Token (Optional)",type="password")
        if st.button("🔄 Refresh Grid Data",use_container_width=True): st.cache_data.clear(); st.rerun()
    elif data_source=="Upload CSV (Admin)":
        st.warning("👤 Admin Mode"); uploaded_file=st.file_uploader("Upload CSV",type=["csv"])
    st.divider()

    st.subheader("🔌 Appliance Settings")
    appliance=st.selectbox("Select Appliance",list(APPLIANCES.keys()),label_visibility="collapsed")
    if appliance!=st.session_state.appliance:
        info=APPLIANCES[appliance]; st.session_state.kw=info["kw"]; st.session_state.duration=info["duration_min"]
        h,m=info["deadline"].split(":"); hi=int(h); p="AM" if hi<12 else "PM"; h12=hi%12 or 12
        st.session_state.deadline=f"{h12}:{m} {p}"; st.session_state.appliance=appliance

    kw      =st.number_input("Power (kW)",0.001,value=float(st.session_state.kw),step=0.05,format="%.3f")
    duration=st.number_input("Duration (min)",15,value=int(st.session_state.duration),step=15)
    deadline=st.text_input("Deadline (HH:MM AM/PM)",value=st.session_state.deadline)

    if not validate_time(deadline): st.error("⚠️ Use HH:MM AM/PM"); deadline=st.session_state.deadline
    else: st.session_state.deadline=deadline

    current_time_rounded=ctr
    nm=t2m(ctr); dlm=t2m(deadline); avm=(dlm-nm) if dlm>nm else (1440-nm+dlm)
    st.info(f"⏱️ Available: **{avm} min** ({avm//60}h {avm%60}m)")
    if st.button("🔄 Reset Defaults"):
        info=APPLIANCES[appliance]; st.session_state.kw=info["kw"]; st.session_state.duration=info["duration_min"]
        h,m=info["deadline"].split(":"); hi=int(h); p="AM" if hi<12 else "PM"; h12=hi%12 or 12
        st.session_state.deadline=f"{h12}:{m} {p}"; st.rerun()
    st.divider()
    st.subheader("⚡ Optimization"); top_k=st.slider("Top Recommendations",3,12,5)
    st.session_state.kw=kw; st.session_state.duration=duration

# ============================================================
# Hero
# ============================================================
utag=f' &nbsp;<span style="background:rgba(74,222,128,.15);color:#4ade80;border:1px solid rgba(74,222,128,.3);padding:.25rem .75rem;border-radius:9999px;font-size:.85rem;font-weight:600;">👤 {st.session_state.user_name}</span>' if st.session_state.user_name else ""
st.markdown(f"""
<div class="hero">
    <h1>🌍 CarbonWise{utag}</h1>
    <p>Location-aware carbon intensity optimization — minimal CO₂, smarter scheduling.</p>
    <div style="margin-top:1rem;">
        <span style="background:rgba(74,222,128,.15);color:#4ade80;border:1px solid rgba(74,222,128,.3);padding:.5rem 1rem;border-radius:9999px;font-size:.875rem;font-weight:600;">⚡ Live Grid Optimization</span>
        <span style="margin-left:.5rem;color:#64748b;font-size:.9rem;">Powered by Electricity Maps & System Time</span>
    </div>
</div>""", unsafe_allow_html=True)

# ============================================================
# Data loading
# ============================================================
@st.cache_data(ttl=300)
def load_auto(lat,lon,token=None): return fetch_em_data(lat,lon,token)

@st.cache_data(ttl=300)
def load_sample():
    DATA_DIR.mkdir(exist_ok=True); sf=DATA_DIR/"sample.csv"
    if not sf.exists():
        rows=[]
        for h in range(24):
            for m in [0,15,30,45]:
                lf=.6 if h<5 else(.8 if h<8 else(1.0 if h<12 else(1.1 if h<17 else(1.2 if h<21 else .9))))
                rows.append({"time":f"{h:02d}:{m:02d}","thermal_mw":int(3000*lf*.6),"hydro_mw":1500,"nuclear_mw":2000,"res_mw":int(800+(1200 if 10<=h<=16 else 0))})
        pd.DataFrame(rows).to_csv(sf,index=False)
    return pd.read_csv(sf)

raw=None
if st.session_state.data_source=="Automatic (API)":
    with st.spinner("🌐 Fetching grid data…"): raw=load_auto(st.session_state.lat,st.session_state.lon,api_token)
    if raw is None: st.warning("⚠️ API failed — using sample data."); raw=load_sample()
elif st.session_state.data_source=="Sample Data":
    raw=load_sample()
else:
    if uploaded_file:
        try:
            raw=pd.read_csv(uploaded_file)
            if not {"time","thermal_mw","hydro_mw","nuclear_mw","res_mw"}.issubset(raw.columns): st.error("Missing columns"); raw=None
        except Exception as e: st.error(f"Error: {e}"); raw=None
    if raw is None: st.info("Upload CSV or switch mode"); st.stop()

if raw is None or raw.empty: st.error("❌ No data."); st.stop()
if "ci" not in raw.columns:
    raw["ci"]=raw.apply(lambda r:compute_ci_g_per_kwh(safe_float(r["thermal_mw"]),safe_float(r["hydro_mw"]),safe_float(r["nuclear_mw"]),safe_float(r["res_mw"])),axis=1)

df_ci=raw[["time","ci"]].dropna().sort_values("time").reset_index(drop=True)
if len(df_ci)<4: st.error("❌ Insufficient data"); st.stop()

fdc=make_full_day(df_ci)
now_time=get_current_time(st.session_state.timezone,STEP_MIN)
windows=recommend(fdc,duration,deadline,top_k,now_time)
current_ci=find_ci(fdc,now_time) or float(fdc["ci"].mean())
best=windows[0] if windows else None
ps=((current_ci-best["avg_ci"])/current_ci*100) if best and current_ci>0 else 0

# ── KPIs ──
st.markdown("### 📊 Current Session Overview")
k=st.columns(5)
with k[0]: st.markdown(f'<div class="kpi"><div class="metric-label">🌍 Location</div><div class="metric-value" style="font-size:1.1rem;">{st.session_state.selected_zone if st.session_state.location_mode=="Manual Select" else "Auto-Detected"}</div></div>',unsafe_allow_html=True)
with k[1]: st.markdown(f'<div class="kpi"><div class="metric-label">🕐 System Time</div><div class="metric-value" style="font-size:1.2rem;">{now_time}</div></div>',unsafe_allow_html=True)
with k[2]: st.markdown(f'<div class="kpi"><div class="metric-label">🔌 Appliance</div><div class="metric-value" style="font-size:1rem;">{appliance.split("/")[0].strip()}</div><div style="color:#64748b;font-size:.75rem;">{kw:.2f} kW • {ceil_to_step(duration)} min</div></div>',unsafe_allow_html=True)
with k[3]:
    cc=get_ci_color(current_ci)
    st.markdown(f'<div class="kpi" style="border-left-color:{cc}"><div class="metric-label">⚡ Current CI</div><div class="metric-value" style="color:{cc}">{current_ci:.0f}</div><div style="color:#64748b;font-size:.75rem;">{get_ci_status(current_ci)} intensity</div></div>',unsafe_allow_html=True)
with k[4]:
    bc="#4ade80" if best else "#64748b"; bd=to12(best["start"]) if best else "--:--"
    st.markdown(f'<div class="kpi" style="border-left-color:{bc}"><div class="metric-label">✨ Best Window</div><div class="metric-value" style="color:{bc};font-size:1.2rem;">{bd}</div><div style="color:#64748b;font-size:.75rem;">{"Save ~"+f"{ps:.1f}%" if ps>0 else "No savings"}</div></div>',unsafe_allow_html=True)

st.markdown("---")

# ── Tabs ──
tab1,tab2,tab3,tab4,tab5=st.tabs(["📈 Dashboard","🎯 Smart Advisor","📊 Analytics","📝 Logger","🔍 Data"])

with tab1:
    dc=st.columns([2,1])
    with dc[0]:
        st.subheader(f"24-Hour Carbon Intensity • {st.session_state.timezone}")
        fig=go.Figure()
        fig.add_trace(go.Scatter(x=fdc["time"],y=fdc["ci"],fill='tozeroy',fillcolor='rgba(74,222,128,.1)',line=dict(color='#4ade80',width=3),hovertemplate='%{x}: %{y:.1f} gCO₂/kWh<extra></extra>'))
        for y0,y1,c in [(0,200,"#4ade80"),(200,400,"#fbbf24"),(400,800,"#f87171")]: fig.add_hrect(y0=y0,y1=y1,fillcolor=c,opacity=.05,line_width=0)
        if windows:
            for i,w in enumerate(windows[:3]): fig.add_vrect(x0=w["start"],x1=w["end"],fillcolor=w["color"],opacity=.22,line_width=2 if i==0 else 1,line_color=w["color"],layer="below")
        n24=to24(now_time); d24=to24(deadline)
        fig.add_vline(x=n24,line_dash="dash",line_color="#4ade80",line_width=2)
        fig.add_vline(x=d24,line_dash="dash",line_color="#f87171",line_width=2)
        mx=max(fdc["ci"])*1.05
        fig.add_annotation(x=n24,y=mx,text="NOW",showarrow=False,font=dict(color="#4ade80",size=11))
        fig.add_annotation(x=d24,y=mx,text="DEADLINE",showarrow=False,font=dict(color="#f87171",size=11))
        fig.update_layout(plot_bgcolor="#0f172a",paper_bgcolor="#0f172a",font=dict(color="#f8fafc"),xaxis=dict(gridcolor="#334155",title="Time",tickangle=-45,nticks=12),yaxis=dict(gridcolor="#334155",title="gCO₂/kWh",range=[0,max(fdc["ci"])*1.15]),height=450,margin=dict(l=60,r=40,t=40,b=60),showlegend=False)
        st.plotly_chart(fig,use_container_width=True)
    with dc[1]:
        st.subheader("Current Grid Mix")
        lt=raw.iloc[-1]
        md=pd.DataFrame({"Source":["Thermal","Hydro","Nuclear","Renewable"],"MW":[safe_float(lt["thermal_mw"]),safe_float(lt["hydro_mw"]),safe_float(lt["nuclear_mw"]),safe_float(lt["res_mw"])],"Color":["#f87171","#60a5fa","#a78bfa","#4ade80"]})
        tot=md["MW"].sum()
        fig2=go.Figure(go.Pie(labels=md["Source"],values=md["MW"],hole=.65,marker_colors=md["Color"],textinfo="label+percent",textfont=dict(color="white",size=11)))
        fig2.update_layout(plot_bgcolor="#0f172a",paper_bgcolor="#0f172a",font=dict(color="#f8fafc"),height=380,showlegend=False,margin=dict(t=20,b=20,l=20,r=20),annotations=[dict(text=f"<b>{tot/1000:.1f}</b><br>GW",x=.5,y=.5,font_size=20,font_color="#f8fafc",showarrow=False)])
        st.plotly_chart(fig2,use_container_width=True)

with tab2:
    st.subheader(f"🏆 Top {top_k} Windows ({now_time} → {to12(deadline)}) • {st.session_state.timezone}")
    if not windows: st.warning("⚠️ No valid windows found! Extend deadline or reduce duration.")
    else:
        for rs in range(0,len(windows),3):
            rw=windows[rs:rs+3]; rc=st.columns(len(rw))
            for i,(col,w) in enumerate(zip(rc,rw)):
                gi=rs+i; rank=gi+1
                with col:
                    hrs=ceil_to_step(duration)/60; en=float(kw)*hrs
                    co2=calculate_co2(en,w["avg_ci"]); co2n=calculate_co2(en,current_ci); sv=co2n-co2
                    ib=gi==0; s12=to12(w["start"]); e12=to12(w["end"])
                    st.markdown(f"""<div class="card" style="{'border:2px solid #4ade80' if ib else 'border:1px solid #374151'};">
                        <div style="text-align:center;margin-bottom:.5rem;"><span style="background:{'#4ade80' if ib else '#374151'};color:{'#0f172a' if ib else 'white'};padding:.25rem .75rem;border-radius:6px;font-size:.75rem;font-weight:700;">{'🥇 BEST' if rank==1 else f'#{rank}'}</span></div>
                        <div style="font-size:1.3rem;font-weight:800;color:white;text-align:center;">{s12} → {e12}</div>
                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-top:.75rem;">
                            <div style="background:#0f172a;padding:.5rem;border-radius:8px;text-align:center;"><div style="color:#64748b;font-size:.7rem;">CI</div><div style="color:{w['color']};font-size:1rem;font-weight:700;">{w['avg_ci']:.0f}</div></div>
                            <div style="background:#0f172a;padding:.5rem;border-radius:8px;text-align:center;"><div style="color:#64748b;font-size:.7rem;">CO₂</div><div style="color:white;font-size:1rem;font-weight:700;">{co2:.3f}kg</div></div>
                        </div>
                        <div style="background:rgba(74,222,128,.1);padding:.4rem;border-radius:6px;text-align:center;margin-top:.5rem;"><span style="color:#4ade80;font-size:.8rem;">💰 Save {sv:.3f} kg</span></div>
                    </div>""", unsafe_allow_html=True)
                    if st.button("✅ Select",key=f"sel_{gi}",use_container_width=True):
                        st.session_state.selected_window=gi; st.success(f"Selected: {s12} – {e12}")

with tab3:
    st.subheader("📊 Baseline vs Optimised")
    hrs=ceil_to_step(duration)/60; en=float(kw)*hrs
    cn=calculate_co2(en,current_ci); cb=calculate_co2(en,best["avg_ci"]) if best else cn
    psav=cn-cb; spct=(psav/cn*100) if cn>0 else 0
    fig3=go.Figure()
    fig3.add_trace(go.Bar(name='Baseline',x=['CO₂'],y=[cn],marker_color='#f87171',text=[f'{cn:.3f} kg'],textposition='outside',width=.35))
    fig3.add_trace(go.Bar(name='Optimised',x=['CO₂'],y=[cb],marker_color='#4ade80',text=[f'{cb:.3f} kg'],textposition='outside',width=.35))
    fig3.update_layout(barmode='group',plot_bgcolor="#0f172a",paper_bgcolor="#0f172a",font=dict(color="#f8fafc",size=14),height=350,showlegend=True,legend=dict(orientation="h",yanchor="bottom",y=1.02,xanchor="center",x=.5),yaxis=dict(title="CO₂ (kg)",gridcolor="#334155"),title=dict(text=f"💰 Savings: {psav:.3f} kg ({spct:.1f}%)",font=dict(size=16,color="#4ade80")))
    st.plotly_chart(fig3,use_container_width=True)
    sc=st.columns(3)
    with sc[0]: st.metric("🔴 Baseline",f"{cn:.3f} kg")
    with sc[1]: st.metric("🟢 Optimised",f"{cb:.3f} kg")
    with sc[2]: st.metric("💰 Savings",f"{spct:.1f}%",delta=f"-{psav:.3f} kg",delta_color="inverse")
    ldf=read_log()
    if not ldf.empty:
        with st.expander("📋 Run History"): st.dataframe(ldf.sort_values("timestamp",ascending=False),use_container_width=True,hide_index=True)

with tab4:
    ensure_dirs(); st.subheader("📝 Log Appliance Run")
    if st.session_state.user_name: st.markdown(f'<span class="bchip">👤 {st.session_state.user_name}</span>',unsafe_allow_html=True)
    with st.form("run_form"):
        fc=st.columns(3)
        with fc[0]: run_date=st.date_input("Date",datetime.now()); run_type=st.selectbox("Run Type",["recommended","baseline","test"])
        with fc[1]:
            ds=windows[st.session_state.selected_window]["start"] if windows and st.session_state.selected_window<len(windows) else to24(now_time)
            start_time=st.text_input("Start Time (HH:MM AM/PM)",to12(ds)); run_dur=st.number_input("Duration (min)",15,value=ceil_to_step(int(duration)),step=15)
        with fc[2]:
            run_app=st.selectbox("Appliance",list(APPLIANCES.keys()))
            notes=st.text_input("Notes",f"Zone: {st.session_state.selected_zone if st.session_state.location_mode=='Manual Select' else 'Auto'}")
        mc=st.columns(2)
        with mc[0]: mb=st.number_input("Meter Before (kWh)",0.0,format="%.3f")
        with mc[1]: ma=st.number_input("Meter After (kWh)",0.0,format="%.3f")
        if st.form_submit_button("💾 Save",use_container_width=True):
            kwh=ma-mb
            if kwh<=0: st.error("❌ 'After' must be > 'Before'")
            else:
                aci=find_ci(fdc,start_time) or float(fdc["ci"].mean())
                sm=t2m(start_time); em=sm+ceil_to_step(int(run_dur))
                row={"timestamp":datetime.now().isoformat(),"date":str(run_date),"run_type":run_type,"appliance":run_app,"start_time":start_time,"end_time":m2t(em),"kwh_used":round(float(kwh),4),"avg_ci_g_per_kwh":round(float(aci),2),"co2_kg":round(calculate_co2(kwh,aci),4),"location_zone":st.session_state.selected_zone if st.session_state.location_mode=="Manual Select" else f"{st.session_state.lat:.2f},{st.session_state.lon:.2f}","timezone":st.session_state.timezone,"notes":notes,"user_name":st.session_state.user_name,"user_email":st.session_state.user_email}
                append_log(row); read_log.clear()
                st.success(f"✅ Saved! {kwh:.3f} kWh, {calculate_co2(kwh,aci):.4f} kg CO₂")
                if run_type=="recommended": st.balloons()

with tab5:
    st.subheader("🔍 Raw Data & Config")
    with st.expander("📍 Location Config"):
        st.json({"Mode":st.session_state.location_mode,"Zone":st.session_state.selected_zone,"Lat":st.session_state.lat,"Lon":st.session_state.lon,"TZ":st.session_state.timezone,"Source":st.session_state.data_source})
        if st.button("💾 Save Config"): save_location_config({"zone":st.session_state.selected_zone,"lat":st.session_state.lat,"lon":st.session_state.lon}); st.success("Saved!")
    if st.session_state.user_name:
        with st.expander("👤 User Details"):
            st.json({"name":st.session_state.user_name,"email":st.session_state.user_email})
            if st.button("🗑️ Clear User Data"): st.session_state.user_name=""; st.session_state.user_email=""; st.rerun()
    sc=[c for c in ["time","thermal_mw","hydro_mw","nuclear_mw","res_mw","ci"] if c in raw.columns]
    st.dataframe(raw[sc].sort_values("time"),use_container_width=True,hide_index=True)
    cc=st.columns(2)
    with cc[0]: st.download_button("📥 Download CSV",raw[sc].to_csv(index=False).encode(),f"carbonwise_{st.session_state.lat:.2f}_{st.session_state.lon:.2f}.csv","text/csv")
    with cc[1]:
        if st.button("🔄 Refresh"): st.cache_data.clear(); st.rerun()
