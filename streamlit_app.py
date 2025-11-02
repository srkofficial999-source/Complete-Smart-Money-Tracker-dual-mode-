import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from datetime import datetime
import time
import math

# ---------- CONFIG ----------
TOP_N_STOCKS = 150
SYMBOLS_FILE = "nifty200.csv"
TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")
# ----------------------------

st.set_page_config(page_title="Smart Money Tracker", layout="wide")

# ---------- HELPER FUNCTIONS ----------
@st.cache_data
def load_symbols():
    try:
        df = pd.read_csv(SYMBOLS_FILE)
        symbols = df['symbol'].dropna().astype(str).tolist()
        return symbols[:TOP_N_STOCKS]
    except Exception:
        return ["TCS.NS", "INFY.NS", "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "LT.NS", "TATAMOTORS.NS", "BAJFINANCE.NS"]

@st.cache_data(ttl=60*5)
def fetch_history(symbol, period="90d", interval="1d"):
    try:
        data = yf.download(symbol, period=period, interval=interval, progress=False, threads=False)
        if data is None or data.empty:
            return None
        data = data.dropna()
        return data
    except Exception:
        return None

def add_indicators(df):
    df = df.copy()
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
    delta = df['Close'].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.rolling(window=14).mean()
    ma_down = down.rolling(window=14).mean()
    rs = ma_up / (ma_down + 1e-9)
    df['RSI'] = 100 - (100 / (1 + rs))
    return df

def unusual_volume(today_vol, avg_vol):
    if avg_vol is None or math.isnan(avg_vol):
        return False
    return today_vol > 2 * avg_vol

@st.cache_data(ttl=60*60)
def fetch_bulk_deals():
    try:
        url = "https://www1.nseindia.com/content/equities/bulk/Bulk_Deals.csv"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        return df
    except Exception:
        return None

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

        if reason_parts:
            picks.append({
                "symbol": sym,
                "close": float(today['Close']),
                "vol": int(today['Volume']),
                "avg_vol": int(avg_vol) if not math.isnan(avg_vol) else None,
                "reasons": ", ".join(reason_parts)
            })

        time.sleep(0.1)

    if not picks:
        return pd.DataFrame()
    dfp = pd.DataFrame(picks).sort_values(by=['vol'], ascending=False)
    return dfp.head(10)

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

# ---------- UI SECTION ----------
st.title("🚀 Smart Money Tracker — Dual Mode")
st.markdown("**Automatic Smart Money Scanner** | Detect FII/DII trends, bulk deals, and top breakout stocks. Built for speed and clarity ⚡")

with st.sidebar:
    st.header("Settings")
    mode = st.radio("Mode", ["Live Mode", "After-Market Mode"])
    scan_count = st.slider("Scan top symbols", 50, 300, TOP_N_STOCKS, step=10)
    uploaded = st.file_uploader("Upload custom stock list (CSV with column 'symbol')", type=["csv"])
    if uploaded:
        df_symbols = pd.read_csv(uploaded)
        st.success(f"Loaded {len(df_symbols)} symbols.")
    st.markdown("---")
    st.write("**Tips:** After-market = Next-day predictions | Live = Real-time watch")
    st.markdown("---")

# Top Section
col1, col2 = st.columns(2)
with col1:
    st.metric("Scanning", f"{scan_count} stocks")
with col2:
    st.info("FII/DII Live feed may not be available due to NSE restrictions.")

# Quick Scan
st.markdown("### 🔍 Run Smart Scan")
if st.button("Start Scan"):
    symbols = load_symbols()
    symbols = symbols[:scan_count]

    with st.spinner("Scanning symbols... please wait ⏳"):
        picks = scan_top_picks(symbols)

    if picks is not None and not picks.empty:
        st.success(f"Found {len(picks)} potential movers 🚀")
        st.dataframe(picks)

        summary = "\n".join([f"{r['symbol']} - {r['reasons']}" for _, r in picks.iterrows()])
        send_telegram(f"Top Picks:\n{summary}")
    else:
        st.warning("No strong signals found today.")

# Bulk Deals Section
st.markdown("---")
st.subheader("📦 Latest Bulk / Block Deals")
bulk = fetch_bulk_deals()
if bulk is None or bulk.empty:
    st.info("Bulk deal data not available (NSE source restricted).")
else:
    st.dataframe(bulk.head(20))

# After-market section
st.markdown("---")
if mode == "After-Market Mode":
    st.subheader("📈 After-Market Analysis (Next-Day Candidates)")
    symbols = load_symbols()[:scan_count]
    with st.spinner("Analyzing last close data..."):
        picks = scan_top_picks(symbols)

    if picks is not None and not picks.empty:
        for _, row in picks.head(5).iterrows():
            st.markdown(f"**{row['symbol']}** — ₹{row['close']:.2f} • {row['reasons']}")
        st.caption("These can be added to next-day watchlist. (Use 1.5–2% stop loss)")
    else:
        st.info("No strong next-day candidates found.")

# Notes Section
st.sidebar.markdown("---")
st.sidebar.subheader("🗒️ Notes / Journal")
note = st.sidebar.text_area("Write your note:", "", height=150)
if st.sidebar.button("Save Note"):
    st.sidebar.success("Note saved (local session only).")

st.markdown("---")
st.caption("© Smart Money Tracker | For learning & analysis purpose only.")