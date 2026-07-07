import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
import requests
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="AI IDX Trading Terminal",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

ISSI_STOCKS = ["ADRO.JK", "ANTM.JK", "BRIS.JK", "INCO.JK", "TLKM.JK", "MDKA.JK", "PGAS.JK"]

TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN", "ISI_TOKEN_LO")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "ISI_CHAT_ID_LO")

# ============================================================
#  THEME / CSS  — dark "trading dojo" look
# ============================================================
st.markdown("""
<style>
    .stApp {
        background: radial-gradient(circle at 20% 0%, #14181f 0%, #0b0d12 55%, #05060a 100%);
        color: #e8eaf0;
    }
    #MainMenu, footer, header {visibility: hidden;}

    .terminal-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 18px 26px;
        border-radius: 14px;
        background: linear-gradient(135deg, rgba(255,140,0,0.10), rgba(0,255,163,0.06));
        border: 1px solid rgba(255,255,255,0.08);
        margin-bottom: 18px;
    }
    .terminal-title {
        font-size: 26px;
        font-weight: 800;
        letter-spacing: 0.5px;
        color: #ffffff;
    }
    .terminal-sub {
        font-size: 13px;
        color: #8a93a6;
        margin-top: 2px;
    }
    .pulse-dot {
        height: 9px; width: 9px; border-radius: 50%;
        background: #00ffa3; display: inline-block; margin-right: 6px;
        box-shadow: 0 0 8px #00ffa3;
        animation: pulse 1.5s ease-in-out infinite;
    }
    @keyframes pulse {
        0% { opacity: 1; }
        50% { opacity: 0.35; }
        100% { opacity: 1; }
    }

    .update-strip {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 8px 18px;
        border-radius: 10px;
        background: rgba(255,255,255,0.02);
        border: 1px solid rgba(255,255,255,0.06);
        margin-bottom: 16px;
        font-size: 13px;
        color: #8a93a6;
    }
    .update-strip b { color: #e8eaf0; }

    /* action buttons row */
    div.stButton > button {
        width: 100%;
        border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.10);
        background: linear-gradient(180deg, #1a1f29, #12151b);
        color: #e8eaf0;
        font-weight: 700;
        padding: 0.65em 0.4em;
        transition: 0.15s ease;
    }
    div.stButton > button:hover {
        border-color: #00ffa3;
        color: #00ffa3;
        transform: translateY(-1px);
        box-shadow: 0 4px 14px rgba(0,255,163,0.15);
    }
    div.stButton > button:active { transform: translateY(0px); }

    .card {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 14px;
        padding: 16px 18px;
        margin-bottom: 10px;
    }
    .badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 700;
        margin-right: 6px;
    }
    .badge-buy { background: rgba(0,255,163,0.15); color: #00ffa3; }
    .badge-wait { background: rgba(255,193,7,0.15); color: #ffc107; }
    .badge-sell { background: rgba(255,82,82,0.15); color: #ff5252; }
    .gain-up { color: #00ffa3; font-weight: 700; }
    .gain-down { color: #ff5252; font-weight: 700; }
    .stDataFrame { border-radius: 12px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)


# ============================================================
#  AUTO-REFRESH CONTROL
# ============================================================
REFRESH_OPTIONS = {"30 detik": 30, "60 detik": 60, "Manual (off)": 0}

with st.sidebar:
    st.markdown("### ⚙️ Pengaturan")
    refresh_label = st.radio("Interval auto-refresh", list(REFRESH_OPTIONS.keys()), index=1)
    refresh_seconds = REFRESH_OPTIONS[refresh_label]
    st.caption(
        "Catatan: data harga saham (yfinance) untuk saham .JK umumnya delay "
        "15-20 menit dan granularitas terkecilnya per-menit. Refresh lebih cepat "
        "dari interval ini tidak akan menghasilkan data baru — cek 'Update terakhir' "
        "di bagian atas untuk tahu kapan data benar-benar berubah."
    )

if refresh_seconds > 0:
    st_autorefresh(interval=refresh_seconds * 1000, key="auto_refresh_ticker")


# ============================================================
#  DATA + INDICATORS
# ============================================================
@st.cache_data(ttl=60)
def get_data(stock):
    df = yf.download(stock, period="3mo", interval="1d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close", "Open", "High", "Low", "Volume"])
    return df


def support_resistance(df):
    support = df['Low'].rolling(20).min().iloc[-1]
    resistance = df['High'].rolling(20).max().iloc[-1]
    return support, resistance


def trend_strength(df):
    ma20 = df['Close'].rolling(20).mean()
    ma50 = df['Close'].rolling(50).mean()
    if ma20.iloc[-1] > ma50.iloc[-1]:
        return "Bullish Strong"
    elif ma20.iloc[-1] < ma50.iloc[-1]:
        return "Bearish"
    else:
        return "Sideways"


def volume_spike(df):
    return df['Volume'].iloc[-1] > df['Volume'].rolling(10).mean().iloc[-1] * 1.5


def breakout_valid(df, resistance):
    return df['Close'].iloc[-1] > resistance and df['Close'].iloc[-2] < resistance


def swing_detector(df):
    high = df['High'].rolling(10).max().iloc[-10]
    now = df['Close'].iloc[-1]
    drop = (high - now) / high * 100
    return drop > 25, drop


def pct_change(df):
    prev = df['Close'].iloc[-2]
    now = df['Close'].iloc[-1]
    return (now - prev) / prev * 100


def scoring(df, support, resistance):
    score = 0
    price = df['Close'].iloc[-1]
    if price <= support * 1.05:
        score += 25
    if breakout_valid(df, resistance):
        score += 30
    if volume_spike(df):
        score += 20
    if "Bullish" in trend_strength(df):
        score += 25
    return score


def signal(score):
    if score >= 75:
        return "STRONG BUY 🚀"
    elif score >= 50:
        return "BUY"
    elif score >= 30:
        return "WAIT"
    else:
        return "SELL"


def signal_badge(sig):
    if "BUY" in sig:
        cls = "badge-buy"
    elif "WAIT" in sig:
        cls = "badge-wait"
    else:
        cls = "badge-sell"
    return f'<span class="badge {cls}">{sig}</span>'


def entry_exit(df, support, resistance):
    return support * 1.02, resistance * 0.98, support * 0.95


def bandar_detection(df):
    vol = df['Volume']
    close = df['Close']
    if vol.iloc[-1] > vol.rolling(10).mean().iloc[-1] * 1.5:
        change = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
        if 0 < change < 3:
            return "🟢 AKUMULASI"
        if change >= 3:
            return "🚀 MARKUP"
        if change < 0:
            return "🔴 DISTRIBUSI"
    return "NETRAL"


def fake_breakout_detector(df, resistance):
    return df['Close'].iloc[-2] > resistance and df['Close'].iloc[-1] < resistance


def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
        return True
    except Exception:
        return False


def build_full_scan():
    """Runs once, computes everything for every stock. Cached in session_state
    so each action button can slice/filter it differently without re-downloading."""
    results = []
    for stock in ISSI_STOCKS:
        try:
            df = get_data(stock)
            if len(df) < 30:
                continue
            support, resistance = support_resistance(df)
            score = scoring(df, support, resistance)
            sig = signal(score)
            trend = trend_strength(df)
            swing, drop = swing_detector(df)
            entry, tp, sl = entry_exit(df, support, resistance)
            bandar = bandar_detection(df)
            fake_break = fake_breakout_detector(df, resistance)
            change_pct = pct_change(df)
            results.append({
                "stock": stock,
                "price": round(float(df['Close'].iloc[-1]), 2),
                "change_pct": round(float(change_pct), 2),
                "score": score,
                "signal": sig,
                "trend": trend,
                "entry": round(float(entry), 2),
                "tp": round(float(tp), 2),
                "sl": round(float(sl), 2),
                "swing": swing,
                "drop": round(float(drop), 2),
                "bandar": bandar,
                "fake_breakout": fake_break,
            })
        except Exception:
            continue
    return pd.DataFrame(results).sort_values(by="score", ascending=False).reset_index(drop=True)


# ============================================================
#  HEADER
# ============================================================
st.markdown("""
<div class="terminal-header">
    <div>
        <div class="terminal-title">🔥 AI IDX TRADING TERMINAL</div>
        <div class="terminal-sub"><span class="pulse-dot"></span>Live screener • ISSI watchlist</div>
    </div>
    <div class="terminal-sub">Data: Yahoo Finance • Bukan rekomendasi finansial</div>
</div>
""", unsafe_allow_html=True)

# ---- Force fresh fetch on every autorefresh cycle ----
if "scan_df" not in st.session_state:
    st.session_state.scan_df = None
if "last_updated" not in st.session_state:
    st.session_state.last_updated = None


def ensure_scanned(force=False):
    if force or st.session_state.scan_df is None:
        with st.spinner("Menarik data & menghitung indikator..."):
            st.session_state.scan_df = build_full_scan()
            st.session_state.last_updated = datetime.now()
    return st.session_state.scan_df


# Re-scan automatically on every autorefresh tick (respects the 60s cache above,
# so this is cheap when the underlying data hasn't actually changed yet)
if refresh_seconds > 0:
    ensure_scanned(force=True)
else:
    ensure_scanned(force=False)

last_upd_str = (
    st.session_state.last_updated.strftime("%H:%M:%S")
    if st.session_state.last_updated else "-"
)
st.markdown(
    f'<div class="update-strip">'
    f'<span>🕒 Update terakhir: <b>{last_upd_str}</b> WIB (server)</span>'
    f'<span>Auto-refresh: <b>{refresh_label}</b></span>'
    f'</div>',
    unsafe_allow_html=True,
)

# ============================================================
#  TOP GAINERS / TOP LOSERS STRIP (within watchlist)
# ============================================================
df_scan_preview = st.session_state.scan_df
if df_scan_preview is not None and not df_scan_preview.empty:
    st.markdown("#### 📈 Top Gainer / 📉 Top Loser (dalam watchlist)")
    ranked = df_scan_preview.sort_values("change_pct", ascending=False)
    gainers = ranked.head(3)
    losers = ranked.tail(3).sort_values("change_pct")

    gcol, lcol = st.columns(2)
    with gcol:
        st.markdown("**Top Gainers**")
        for _, r in gainers.iterrows():
            css = "gain-up" if r["change_pct"] >= 0 else "gain-down"
            st.markdown(
                f'<div class="card">{r["stock"]} — {r["price"]} '
                f'<span class="{css}">({r["change_pct"]:+.2f}%)</span></div>',
                unsafe_allow_html=True,
            )
    with lcol:
        st.markdown("**Top Losers**")
        for _, r in losers.iterrows():
            css = "gain-up" if r["change_pct"] >= 0 else "gain-down"
            st.markdown(
                f'<div class="card">{r["stock"]} — {r["price"]} '
                f'<span class="{css}">({r["change_pct"]:+.2f}%)</span></div>',
                unsafe_allow_html=True,
            )

st.divider()

# ============================================================
#  ACTION BUTTONS ROW — each button = one command
# ============================================================
c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    btn_scan = st.button("🔍 SCAN MARKET")
with c2:
    btn_bandar = st.button("🐋 BANDAR DETECTOR")
with c3:
    btn_breakout = st.button("🚀 BREAKOUT SCANNER")
with c4:
    btn_swing = st.button("📉 SWING ALERT")
with c5:
    btn_fake = st.button("⚠️ FAKE BREAKOUT")
with c6:
    btn_telegram = st.button("📲 SEND BEST TO TG")

# ---- SCAN MARKET : full table, every stock, every metric ----
if btn_scan:
    df_scan = ensure_scanned()
    st.subheader("📊 Hasil Scan Penuh")
    st.dataframe(df_scan, use_container_width=True)
    if not df_scan.empty:
        best = df_scan.iloc[0]
        st.markdown(
            f'<div class="card"><b>BEST PICK:</b> {best["stock"]} — '
            f'{signal_badge(best["signal"])} @ {best["price"]}</div>',
            unsafe_allow_html=True,
        )

# ---- BANDAR DETECTOR : only stocks with non-neutral bandar activity ----
if btn_bandar:
    df_scan = ensure_scanned()
    st.subheader("🐋 Deteksi Aktivitas Bandar")
    bandar_hits = df_scan[df_scan["bandar"] != "NETRAL"]
    if bandar_hits.empty:
        st.info("Tidak ada aktivitas bandar signifikan hari ini.")
    else:
        for _, r in bandar_hits.iterrows():
            st.markdown(
                f'<div class="card"><b>{r["stock"]}</b> — {r["bandar"]} '
                f'(price: {r["price"]}) {signal_badge(r["signal"])}</div>',
                unsafe_allow_html=True,
            )

# ---- BREAKOUT SCANNER : stocks with a fresh valid breakout ----
if btn_breakout:
    df_scan = ensure_scanned()
    st.subheader("🚀 Saham Breakout Valid")
    breakouts = df_scan[df_scan["score"] >= 50]
    if breakouts.empty:
        st.info("Belum ada breakout kuat terdeteksi.")
    else:
        st.dataframe(
            breakouts[["stock", "price", "score", "signal", "entry", "tp", "sl"]],
            use_container_width=True,
        )

# ---- SWING ALERT : stocks that dropped >25% from recent swing high ----
if btn_swing:
    df_scan = ensure_scanned()
    st.subheader("📉 Swing Drop Alert (>25% dari high)")
    swing_hits = df_scan[df_scan["swing"] == True]
    if swing_hits.empty:
        st.info("Tidak ada saham dengan swing drop signifikan.")
    else:
        st.dataframe(
            swing_hits[["stock", "price", "drop", "trend", "signal"]],
            use_container_width=True,
        )

# ---- FAKE BREAKOUT : stocks whose breakout just failed ----
if btn_fake:
    df_scan = ensure_scanned()
    st.subheader("⚠️ Fake Breakout Warning")
    fake_hits = df_scan[df_scan["fake_breakout"] == True]
    if fake_hits.empty:
        st.info("Tidak ada fake breakout terdeteksi saat ini.")
    else:
        st.dataframe(fake_hits[["stock", "price", "trend", "signal"]], use_container_width=True)

# ---- SEND BEST TO TELEGRAM ----
if btn_telegram:
    df_scan = ensure_scanned()
    if df_scan.empty:
        st.warning("Belum ada data untuk dikirim.")
    else:
        best = df_scan.iloc[0]
        msg = (
            f"BEST PICK: {best['stock']}\n"
            f"Signal: {best['signal']}\n"
            f"Price: {best['price']}\n"
            f"Entry: {best['entry']} | TP: {best['tp']} | SL: {best['sl']}"
        )
        ok = send_telegram(msg)
        if ok:
            st.success("Terkirim ke Telegram ✅")
        else:
            st.error("Gagal kirim ke Telegram — cek TELEGRAM_TOKEN / CHAT_ID di Secrets.")

if not any([btn_scan, btn_bandar, btn_breakout, btn_swing, btn_fake, btn_telegram]):
    st.markdown(
        '<div class="card" style="text-align:center; color:#8a93a6;">'
        "Pilih salah satu tombol aksi di atas untuk mulai. "
        "Panel Top Gainer/Loser di atas sudah auto-update sesuai interval refresh."
        "</div>",
        unsafe_allow_html=True,
    )
