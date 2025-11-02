# streamlit_app.py
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import time
import math
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# ---------- CONFIG ----------
TOP_N_STOCKS = 150
SYMBOLS_FILE = "nifty200.csv"
AUTO_REFRESH_MINUTES = 30  # Auto scan interval in minutes (you can change)
SL_PCT = 2.0   # Stop Loss percent
TP_PCT = 5.0   # Target Profit percent
TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")
# ----------------------------

st.set_page_config(page_title="Smart Money Tracker v2", layout="wide")

# ---------- UTIL / HELPERS ----------
@st.cache_data
def load_symbols():
    try:
        df = pd.read_csv(SYMBOLS_FILE)
        symbols = df['symbol'].dropna().astype(str).tolist()
        return symbols[:TOP_N_STOCKS]
    except Exception:
        # fallback sample list
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

def compute_signals_for_symbol(sym):
    hist = fetch_history(sym, period="60d", interval="1d")
    if hist is None or len(hist) < 30:
        return None
    hist = add_indicators(hist)
    today = hist.iloc[-1]

    # numeric single values
    try:
        today_close = float(today['Close'])
        today_vol = float(today['Volume'])
        avg_vol = float(hist['Volume'][-21:-1].mean()) if len(hist) >= 22 else float(hist['Volume'].mean())
        ema20 = float(today['EMA20'])
        ema50 = float(today['EMA50'])
        rsi = float(today.get('RSI', np.nan))
    except Exception:
        return None

    vol_spike = (avg_vol > 0) and (today_vol > 2 * avg_vol)
    ema_up = ema20 > ema50
    ema_down = ema20 < ema50
    rsi_val = None if math.isnan(rsi) else rsi

    # Signals logic
    buy_cond = ema_up and (rsi_val is not None and rsi_val > 55) and vol_spike
    sell_cond = ema_down and (rsi_val is not None and rsi_val < 45) and vol_spike

    reasons = []
    if vol_spike:
        reasons.append("Volume spike")
    if ema_up:
        reasons.append("20>50 EMA")
    if ema_down:
        reasons.append("20<50 EMA")
    if rsi_val is not None:
        reasons.append(f"RSI {int(rsi_val)}")

    signal = None
    if buy_cond:
        signal = "BUY"
    elif sell_cond:
        signal = "SELL"

    if signal is None:
        return None

    # SL / TP calculation
    if signal == "BUY":
        sl = round(today_close * (1 - SL_PCT / 100), 2)
        tp = round(today_close * (1 + TP_PCT / 100), 2)
    else:  # SELL
        sl = round(today_close * (1 + SL_PCT / 100), 2)
        tp = round(today_close * (1 - TP_PCT / 100), 2)

    return {
        "symbol": sym,
        "signal": signal,
        "price": round(today_close, 2),
        "sl": sl,
        "tp": tp,
        "vol": int(today_vol),
        "avg_vol": int(avg_vol) if not math.isnan(avg_vol) else None,
        "reasons": ", ".join(reasons),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def scan_symbols(symbols, limit=150):
    results = []
    for sym in symbols[:limit]:
        res = compute_signals_for_symbol(sym)
        if res:
            results.append(res)
        # gentle throttling for API friendliness
        time.sleep(0.08)
    return results

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

def format_alert_message(items):
    lines = []
    lines.append("🚨 SmartMoney Alert")
    lines.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    for it in items:
        lines.append(f"{it['symbol']} — {it['signal']} @ ₹{it['price']} — SL: ₹{it['sl']} | TP: ₹{it['tp']}")
        lines.append(f"Reasons: {it['reasons']}")
        lines.append("")
    return "\n".join(lines)

# ---------- UI & Session Initialization ----------
if "sent_signals" not in st.session_state:
    st.session_state.sent_signals = {}  # key: symbol, value: last time sent

st.title("🚀 Smart Money Tracker v2 — Auto Alerts + SL/TP")
st.markdown("Auto-scan every 30 min. Signals include BUY/SELL with SL (2%) & TP (5%). Use Telegram secrets to get alerts.")

with st.sidebar:
    st.header("Settings")
    auto_mode = st.checkbox("Enable Auto Mode (every 30 min)", value=True)
    scan_count = st.slider("Scan top symbols", 50, 300, TOP_N_STOCKS, step=10)
    st.write("SL:", f"{SL_PCT}%  |  TP:", f"{TP_PCT}%")
    st.markdown("---")
    st.write("Add TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in Streamlit Secrets to enable alerts.")
    uploaded = st.file_uploader("Upload custom stock list (CSV w/ column 'symbol')", type=["csv"])
    if uploaded:
        df_sym = pd.read_csv(uploaded)
        st.success(f"Loaded {len(df_sym)} symbols. (Session only)")

col1, col2 = st.columns([2,1])
with col1:
    st.subheader("Quick Actions")
    start_manual = st.button("Run Manual Scan Now")
with col2:
    st.subheader("Status")
    st.write("Auto Mode:", "ON" if auto_mode else "OFF")
    st.write("Last run:", st.session_state.get("last_run", "Never"))

# ---------- Auto-refresh trigger ----------
# Use st_autorefresh only if auto_mode True
if auto_mode:
    # convert minutes -> milliseconds
    refresh_ms = int(AUTO_REFRESH_MINUTES * 60 * 1000)
    # Trigger a rerun every refresh_ms — this will re-run the script and perform auto scan below
    count = st_autorefresh(interval=refresh_ms, limit=None, key="autorefresh")

do_scan = False
if start_manual:
    do_scan = True
# If auto_mode and triggered by autorefresh, also run scan
# st_autorefresh increments 'count' each refresh; for initial page load count is 0
# we run scan when count > 0 (i.e., after first refresh) OR when manual clicked.
if auto_mode:
    # perform a scan on every run when auto_mode is True (including first load) to keep it proactive
    do_scan = True

# ---------- Run Scan if requested ----------
if do_scan:
    # Decide which symbol list to use
    if uploaded:
        symbols = df_sym['symbol'].dropna().astype(str).tolist()
    else:
        symbols = load_symbols()

    symbols = symbols[:scan_count]
    st.info(f"Scanning {len(symbols)} symbols... This may take a little while.")
    with st.spinner("Scanning..."):
        results = scan_symbols(symbols, limit=scan_count)

    st.session_state.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not results:
        st.warning("No BUY/SELL signals detected in this run.")
    else:
        # Prepare display table
        df_results = pd.DataFrame(results)
        st.success(f"{len(df_results)} signal(s) detected.")
        st.dataframe(df_results[["symbol","signal","price","sl","tp","vol","reasons","time"]])

        # Filter new signals (dedupe: only send if not sent in this session before)
        new_alerts = []
        for r in results:
            sym = r['symbol']
            last_sent = st.session_state.sent_signals.get(sym)
            # if never sent before OR sent more than 24 hours ago -> consider new
            send_allowed = False
            if not last_sent:
                send_allowed = True
            else:
                try:
                    last_dt = datetime.strptime(last_sent, "%Y-%m-%d %H:%M:%S")
                    # only re-send after 24 hours to avoid spam; adjust if needed
                    if (datetime.now() - last_dt).total_seconds() > 24 * 3600:
                        send_allowed = True
                except Exception:
                    send_allowed = True

            if send_allowed:
                new_alerts.append(r)
                st.session_state.sent_signals[sym] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if new_alerts:
            msg = format_alert_message(new_alerts)
            sent = send_telegram(msg)
            if sent:
                st.success(f"Telegram alert sent for {len(new_alerts)} new signal(s).")
                st.write("Alert preview:")
                st.code(msg)
            else:
                st.error("Telegram alert failed (check TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in Secrets).")
                st.write("Preview (not sent):")
                st.code(msg)
        else:
            st.info("No new signals to alert (duplicates filtered).")

st.markdown("---")
st.caption("Notes: Auto Mode triggers a full re-run of this script every AUTO_REFRESH_MINUTES. Use Telegram secrets for alerts. This tool is for learning & analysis only — not financial advice.")