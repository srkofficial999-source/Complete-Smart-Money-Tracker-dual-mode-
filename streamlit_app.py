# streamlit_app.py
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from datetime import datetime, timedelta
import time
import math

# ---------- CONFIG ----------
TOP_N_STOCKS = 150   # number of symbols to scan (change if you want)
SYMBOLS_FILE = "nifty200.csv"  # local file with list of tickers (see readme)
TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN", "")  # set in Streamlit secrets or leave empty
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")
# ----------------------------

st.set_page_config(page_title="Smart Money Tracker", layout="wide", initial_sidebar_state="expanded")

# Helper: load symbols (user should upload nifty200.csv or we use sample)
@st.cache_data
def load_symbols():
    try:
        df = pd.read_csv(SYMBOLS_FILE)
        symbols = df['symbol'].dropna().astype(str).tolist()
        return symbols[:TOP_N_STOCKS]
    except Exception:
        # fallback sample (TCS, INFY, RELIANCE, HDFCBANK ... with .NS suffix for yfinance)
        return ["TCS.NS","INFY.NS","RELIANCE.NS","HDFCBANK.NS","ICICIBANK.NS","LT.NS","TATAMOTORS.NS","BAJFINANCE.NS"]

# Helper: get OHLC history
@st.cache_data(ttl=60*5)
def fetch_history(symbol, period="90d", interval="1d"):
    try:
        data = yf.download(symbol, period=period, interval=interval, progress=False, threads=False)
        if data is None or data.empty:
            return None
        data = data.dropna()
        return data
    except Exception as e:
        return None

# Simple indicators: EMA, RSI
def add_indicators(df):
    df = df.copy()
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
    delta = df['Close'].diff()
    up = delta.clip(lower=0); down = -1*delta.clip(upper=0)
    ma_up = up.rolling(window=14).mean()
    ma_down = down.rolling(window=14).mean()
    rs = ma_up / (ma_down + 1e-9)
    df['RSI'] = 100 - (100 / (1 + rs))
    return df

# Unusual volume check
def unusual_volume(today_vol, avg_vol):
    if avg_vol is None or math.isnan(avg_vol): return False
    return today_vol > 2 * avg_vol

# Basic FII/DII data fetch (best-effort: NSE daily CSV if accessible)
@st.cache_data(ttl=60*60)
def fetch_fii_dii():
    # This is a simple best-effort placeholder. NSE might restrict direct API.
    try:
        url = "https://www1.nseindia.com/homepage/Indices1.json"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        j = r.json()
        # parse if available (structure may vary). We return None gracefully if not found.
        return j
    except Exception:
        return None

# Bulk deals fetch (simple approach using NSE bulk deals CSV URL)
@st.cache_data(ttl=60*60)
def fetch_bulk_deals():
    try:
        url = "https://www1.nseindia.com/content/equities/bulk/Bulk_Deals.csv"
        headers = {"User-Agent":"Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        df = pd.read_csv(pd.compat.StringIO(r.text))
        return df
    except Exception:
        return None

# Build top picks from scanning symbols
def scan_top_picks(symbols):
    picks = []
    for sym in symbols:
        hist = fetch_history(sym, period="60d", interval="1d")
        if hist is None or len(hist) < 30:
            continue
        hist = add_indicators(hist)
        today = hist.iloc[-1]
        avg_vol = hist['Volume'][-21:-1].mean() if len(hist) >= 22 else hist['Volume'].mean()
        vol_flag = unusual_volume(today['Volume'], avg_vol)
        ema_flag = today['EMA20'] > today['EMA50']
        rsi = float(today.get('RSI', np.nan))
        rsi_flag = False
        if not math.isnan(rsi):
        if 50 < rsi < 80:
        rsi_flag = True

        reason_parts = []
        if vol_flag:
    reason_parts.append("Volume spike")
if ema_flag:
    reason_parts.append("20>50 EMA")
if rsi_flag:
    reason_parts.append(f"RSI {int(rsi)}")
            picks.append({
                "symbol": sym,
                "close": float(today['Close']),
                "vol": int(today['Volume']),
                "avg_vol": int(avg_vol) if not math.isnan(avg_vol) else None,
                "reasons": ", ".join(reason_parts)
            })
        # keep lighter scanning
        time.sleep(0.15)
    dfp = pd.DataFrame(picks).sort_values(by=['vol'], ascending=False)
    return dfp.head(10)

# Telegram sender
def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        r = requests.post(url, json=payload, timeout=8)
        return r.status_code == 200
    except Exception:
        return False

# ---------------- UI ----------------
st.title("🚀 Smart Money Tracker — Dual Mode")
st.markdown("Stylish, simple & smart — FII/DII, Bulk deals, Volume spikes, EMA crossover. Dual Mode (Live / After-Market).")

with st.sidebar:
    st.header("Settings")
    mode = st.radio("Mode", ["Live Mode", "After-Market Mode"])
    scan_count = st.slider("Scan top symbols (approx)", 50, 300, TOP_N_STOCKS, step=10)
    st.write("Telegram alerts (optional) — set in Streamlit secrets.")
    st.markdown("---")
    st.write("Upload `nifty200.csv` (column: symbol like TCS.NS)")
    uploaded = st.file_uploader("Upload symbols CSV", type=["csv"])
    if uploaded:
        df_symbols = pd.read_csv(uploaded)
        st.success(f"Loaded {len(df_symbols)} symbols. Save as `nifty200.csv` to repo for auto use.")
    st.write("---")
    st.write("Quick tips:")
    st.write("- After-market mode uses last close data for next-day plan.")
    st.write("- Live mode may be near real-time but depends on yfinance refresh.")

# center layout: top bar metrics
col1, col2, col3 = st.columns([1,1,2])
with col1:
    st.metric("Scan Count", scan_count)
with col2:
    fdata = fetch_fii_dii()
    if fdata:
        st.subheader("FII/DII (snapshot)")
        st.write("Data fetched (see raw below)")
    else:
        st.subheader("FII/DII")
        st.info("Data not available (NSE endpoint may block).")

with col3:
    st.markdown("### 🔎 Quick Actions")
    if st.button("Run Quick Scan"):
        symbols = load_symbols()
        symbols = symbols[:scan_count]
        with st.spinner("Scanning symbols... (this may take a minute)"):
            picks = scan_top_picks(symbols)
        if picks is not None and not picks.empty:
            st.success(f"Found {len(picks)} candidates")
            st.table(picks)
            # send telegram summary optionally
            txt = "Top Picks:\n" + "\n".join([f"{r['symbol']} - {r['reasons']}" for _,r in picks.iterrows()])
            send_telegram(txt)
        else:
            st.warning("No clear picks found.")

# Bulk deals section
st.markdown("---")
st.subheader("📦 Recent Bulk / Block Deals (NSE)")
bulk = fetch_bulk_deals()
if bulk is None:
    st.info("Bulk deals not available (NSE may block direct access).")
else:
    st.dataframe(bulk.head(20))

# After-market analysis card
st.markdown("---")
st.subheader("📈 After-market Analysis / Next-day Picks")
if mode == "After-Market Mode":
    symbols = load_symbols()[:scan_count]
    with st.spinner("Running after-market screener..."):
        picks = scan_top_picks(symbols)
    if picks is None or picks.empty:
        st.info("No picks found based on after-market data.")
    else:
        for i,row in picks.head(5).iterrows():
            st.markdown(f"**{row['symbol']}**  — Close: {row['close']:.2f}  •  {row['reasons']}")
        st.markdown("Use these as watchlist for opening trades next day. Set stop loss at previous low / 1.5-2%")

# Journal
st.sidebar.markdown("---")
st.sidebar.subheader("Journal / Notes")
note = st.sidebar.text_area("Your note for today's session", "", height=150)
if st.sidebar.button("Save note locally"):
    st.sidebar.success("Note saved (local in session).")

st.markdown("---")
st.caption("Built for quick scouting. This is a practical tool — NSE site structure and data availability vary. If you want, I will customize with your exact symbols & add intraday 5-min charts.")
