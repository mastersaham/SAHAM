import streamlit as st
import json
import os
import re
import html
import hashlib
import secrets as secrets_lib
import requests
import xml.etree.ElementTree as ET
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from streamlit_autorefresh import st_autorefresh
from streamlit_cookies_manager import EncryptedCookieManager

st.set_page_config(
    page_title="AI IDX Trading Terminal",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
#  CONFIG
# ============================================================
# Daftar username yang otomatis dapat akses "Owner" (bukan berdasarkan email
# lagi, karena sistem login sekarang cuma username + password). Sesuaikan
# daftar ini dengan username yang kamu pakai sendiri saat daftar di app.
OWNER_USERNAMES = [
    "hajiannugraha",
    "hajianclashofclans",
    "widya.nurulmustofa",
]

# File tempat menyimpan status langganan agar TIDAK hilang setiap kali
# Streamlit menjalankan ulang script (yang terjadi di HAMPIR setiap interaksi).
# Ini masih penyimpanan lokal sederhana (bukan database asli), tapi jauh lebih
# aman daripada dict biasa (USER_DB = {}) yang direset tiap rerun/restart.
USER_DB_FILE = "user_db.json"

TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN", "ISI_TOKEN_LO")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "ISI_CHAT_ID_LO")

# Kunci rahasia buat enkripsi cookie sesi login. GANTI ini dengan string acak
# panjang punya kamu sendiri (taruh di .streamlit/secrets.toml sebagai
# COOKIE_PASSWORD), jangan pakai nilai default ini di produksi.
COOKIE_PASSWORD = st.secrets.get("COOKIE_PASSWORD", "ganti-dengan-kunci-rahasia-acak-punya-kamu")

# Berapa lama sesi "tetap login" bertahan sebelum wajib login ulang (dalam hari).
SESSION_DURATION_DAYS = 30

# ============================================================
#  COOKIE MANAGER — supaya user tetap login walau tab/browser
#  ditutup dan dibuka lagi (gak perlu login ulang tiap buka app),
#  sampai dia benar-benar pencet tombol Logout atau cookie expired.
# ============================================================
cookies = EncryptedCookieManager(prefix="aiidx_", password=COOKIE_PASSWORD)
if not cookies.ready():
    # Komponen cookie butuh 1x render awal buat sinkron ke browser.
    st.stop()


def save_login_cookie(username):
    """Simpan sesi login terenkripsi ke cookie browser."""
    session_data = {
        "username": username,
        "issued_at": datetime.now().isoformat(),
    }
    cookies["auth_session"] = json.dumps(session_data)
    cookies.save()


def load_login_cookie(user_db):
    """Baca & validasi cookie sesi login. Return username kalau valid, None kalau tidak."""
    raw = cookies.get("auth_session")
    if not raw:
        return None
    try:
        session_data = json.loads(raw)
        username = session_data.get("username", "")
        issued_at = datetime.fromisoformat(session_data.get("issued_at", ""))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None

    # Cek masa berlaku sesi.
    if datetime.now() - issued_at > timedelta(days=SESSION_DURATION_DAYS):
        return None

    # Pastikan akunnya masih valid/terdaftar (owner atau ada di user_db).
    if username in OWNER_USERNAMES or f"user:{username}" in user_db:
        return username
    return None


def clear_login_cookie():
    """Hapus sesi login dari cookie (dipakai saat logout)."""
    if "auth_session" in cookies:
        del cookies["auth_session"]
        cookies.save()

ISSI_FALLBACK_STOCKS = [
    "AALI.JK", "ABMM.JK", "ACES.JK", "ADHI.JK", "ADMR.JK", "ADRO.JK", "AGII.JK",
    "AGRO.JK", "AIMS.JK", "AKRA.JK", "ALKA.JK", "AMAG.JK", "AMFG.JK", "AMIN.JK",
    "AMRT.JK", "ANTM.JK", "APEX.JK", "APLI.JK", "APLN.JK", "ARCI.JK", "ARGO.JK",
    "ARKA.JK", "ARTO.JK", "ASGR.JK", "ASJT.JK", "ASMI.JK", "ASRI.JK", "ASRM.JK",
    "ATIC.JK", "AUTO.JK", "AVIA.JK", "BAJA.JK", "BALI.JK", "BAPA.JK", "BATA.JK",
    "BAYU.JK", "BBSS.JK", "BCIP.JK", "BEEF.JK", "BEST.JK", "BFIN.JK", "BGTG.JK",
    "BIKA.JK", "BIRD.JK", "BISI.JK", "BKDP.JK", "BKSL.JK", "BLTA.JK", "BLTZ.JK",
    "BLUE.JK", "BMAS.JK", "BMSR.JK", "BMTR.JK", "BNBA.JK", "BNBR.JK", "BOGA.JK",
    "BOLA.JK", "BOLT.JK", "BOSS.JK", "BPFI.JK", "BPII.JK", "BPTR.JK", "BREN.JK",
    "BRIS.JK", "BRMS.JK", "BSDE.JK", "BSSR.JK", "BTPS.JK", "BUKA.JK", "BUKK.JK",
    "BUMI.JK", "BUVA.JK", "BVIC.JK", "BWPT.JK", "BYAN.JK", "CAMP.JK", "CARS.JK",
    "CASA.JK", "CASH.JK", "CASS.JK", "CEKA.JK", "CENT.JK", "CFIN.JK", "CINT.JK",
    "CLAY.JK", "CLEO.JK", "CLPI.JK", "CMNP.JK", "COCO.JK", "CPIN.JK", "CPRO.JK",
    "CSAP.JK", "CSIS.JK", "CSRA.JK", "CTRA.JK", "CTTH.JK", "DADA.JK", "DEFI.JK",
    "DEWA.JK", "DFAM.JK", "DGIK.JK", "DHHL.JK", "DILD.JK", "DIVA.JK", "DKFT.JK",
    "DMAS.JK", "DMMX.JK", "DNAR.JK", "DNET.JK", "DOID.JK", "DPUM.JK", "DSFI.JK",
    "DVLA.JK", "DYAN.JK", "EAST.JK", "ECII.JK", "EKAW.JK", "ELSA.JK", "EMTK.JK",
    "ENRG.JK", "EPMT.JK", "ERAA.JK", "ESIP.JK", "ESSA.JK", "ESTI.JK", "ETWA.JK",
    "EXCL.JK", "FAST.JK", "FASW.JK", "FILM.JK", "FITT.JK", "FLMC.JK", "FMII.JK",
    "FORU.JK", "GDST.JK", "GDYR.JK", "GEMA.JK", "GHON.JK", "GJTL.JK", "GLOB.JK",
    "GMFI.JK", "GMTD.JK", "GOLD.JK", "GOLL.JK", "GOTO.JK", "GPRA.JK", "GSMF.JK",
    "GWSA.JK", "GZCO.JK", "HAIS.JK", "HDFA.JK", "HEAL.JK", "HELI.JK", "HERO.JK",
    "HEXA.JK", "HITS.JK", "HOKI.JK", "HOME.JK", "HOTL.JK", "HRTA.JK", "HRUM.JK",
    "IATA.JK", "IBFN.JK", "IBST.JK", "ICBP.JK", "IKAI.JK", "IKAN.JK", "IMAS.JK",
    "IMJS.JK", "IMPC.JK", "INAF.JK", "INCF.JK", "INDF.JK", "INDO.JK", "INDX.JK",
    "INDY.JK", "INKP.JK", "INPP.JK", "INPS.JK", "INRU.JK", "INTD.JK", "INTP.JK",
    "IPCC.JK", "IPCM.JK", "IPOL.JK", "IPTV.JK", "IRRA.JK", "ISSP.JK", "ITMA.JK",
    "ITMG.JK", "JAWA.JK", "JECC.JK", "JIHD.JK", "JKON.JK", "JKSW.JK", "JMAS.JK",
    "JPFA.JK", "JRPT.JK", "JSMR.JK", "JTPE.JK", "KAEF.JK", "KIJA.JK", "KKGI.JK",
    "KLBF.JK", "KPIG.JK", "KRAS.JK", "LINK.JK", "LPCK.JK", "LPKR.JK", "LPPF.JK",
    "LSIP.JK", "MAIN.JK", "MAPA.JK", "MAPI.JK", "MARK.JK", "MBMA.JK", "MDKA.JK",
    "MEDC.JK", "MEDI.JK", "MIKA.JK", "MLPT.JK", "MNCN.JK", "MPMX.JK", "MTDL.JK",
    "MYOR.JK", "OASA.JK", "PANR.JK", "PBSA.JK", "PGAS.JK", "PNBS.JK", "PPAT.JK",
    "PPRO.JK", "PTBA.JK", "PTPP.JK", "PWON.JK", "RAJA.JK", "RALS.JK", "SAMF.JK",
    "SCMA.JK", "SIDO.JK", "SIMP.JK", "SMCB.JK", "SMGR.JK", "SMRA.JK", "SMSM.JK",
    "SPTO.JK", "SRTG.JK", "SSMS.JK", "TAPG.JK", "TBLA.JK", "TCID.JK", "TGKA.JK",
    "TIMS.JK", "TINS.JK", "TKIM.JK", "TLKM.JK", "TMAS.JK", "TOBA.JK", "TOTL.JK",
    "TPIA.JK", "TSPC.JK", "ULTJ.JK", "UNIC.JK", "UNTR.JK", "URBN.JK", "VRNA.JK",
    "WEGE.JK", "WIFI.JK", "WIIM.JK", "WIKA.JK", "WOOD.JK", "WSKT.JK", "YPAS.JK",
    "ZATA.JK",
]


def load_user_db():
    if os.path.exists(USER_DB_FILE):
        try:
            with open(USER_DB_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_user_db(db):
    with open(USER_DB_FILE, "w") as f:
        json.dump(db, f)


# ============================================================
#  PASSWORD HASHING — untuk akun "daftar manual" (tanpa Gmail)
# ============================================================
# Password TIDAK PERNAH disimpan dalam bentuk teks biasa. Dipakai PBKDF2-HMAC
# (100rb iterasi) + salt acak per-user, cukup standar untuk skala kecil.
def hash_password(password, salt=None):
    if salt is None:
        salt = secrets_lib.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 100_000
    ).hex()
    return pwd_hash, salt


def verify_password(password, stored_hash, salt):
    if not stored_hash or not salt:
        return False
    test_hash, _ = hash_password(password, salt)
    return secrets_lib.compare_digest(test_hash, stored_hash)


@st.cache_data(ttl=86400, show_spinner=False)
def get_issi_stocks():
    """Coba ambil daftar konstituen ISSI live dari IDX. Kalau IDX
    block/rate-limit request ini (umum terjadi di shared cloud IP) atau
    format response berubah, fallback ke daftar blue-chip statis."""
    try:
        resp = requests.get(
            "https://www.idx.co.id/umbraco/Surface/StockData/GetConstituent",
            params={"indexCode": "ISSI"},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://www.idx.co.id/id/idx-syariah/indeks-syariah/",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        codes = [row["Code"].strip() for row in data if row.get("Code")]
        tickers = sorted(set(f"{c}.JK" for c in codes if c))
        if len(tickers) >= 50:
            return tickers, True
    except Exception:
        pass
    return ISSI_FALLBACK_STOCKS, False


ISSI_STOCKS, _issi_live = get_issi_stocks()

# ============================================================
#  NEWS FETCHING (Google News RSS per saham + sentimen keyword)
# ============================================================
# Sumber berita: Google News RSS search, per saham. Ini dipakai (bukan RSS
# portal lokal seperti Kontan) karena formatnya publik, terdokumentasi, dan
# stabil: https://news.google.com/rss/search?q=<query>&hl=id&gl=ID&ceid=ID:id
GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"

# Kata kunci sederhana untuk menilai sentimen berita. Ini pendekatan
# berbasis kata kunci (bukan AI/NLP model), jadi masih bisa kurang akurat
# untuk kalimat yang ambigu atau bernada sarkasme.
POSITIVE_KEYWORDS = [
    "naik", "menguat", "profit", "laba", "untung", "melesat", "rekor",
    "akuisisi", "ekspansi", "dividen", "buyback", "tumbuh", "positif",
    "melonjak", "meningkat", "surplus", "upgrade",
]
NEGATIVE_KEYWORDS = [
    "turun", "melemah", "rugi", "anjlok", "gugatan", "delisting",
    "penurunan", "downgrade", "default", "phk", "bangkrut", "negatif",
    "tersendat", "tertekan", "net sell", "koreksi",
]


@st.cache_data(ttl=55, show_spinner=False)
def fetch_stock_news(ticker, query, max_items=5):
    params = {"q": query, "hl": "id", "gl": "ID", "ceid": "ID:id"}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AITradingTerminal/1.0)"}
    articles = []
    try:
        resp = requests.get(GOOGLE_NEWS_RSS_BASE, params=params, headers=headers, timeout=8)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item")[:max_items]:
            title = html.unescape((item.findtext("title") or "").strip())
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            description = html.unescape(re.sub("<[^<]+?>", "", description))
            pub_date = (item.findtext("pubDate") or "").strip()
            source_el = item.find("source")
            source = (source_el.text or "").strip() if source_el is not None else ""
            articles.append({
                "title": title,
                "link": link,
                "description": description,
                "pub_date": pub_date,
                "source": source,
            })
    except (requests.RequestException, ET.ParseError):
        # Kalau Google News lagi tidak bisa diakses/timeout, kembalikan list
        # kosong untuk saham ini daripada bikin seluruh app error.
        pass
    return articles


def analyze_sentiment(text):
    text_lower = text.lower()
    pos_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
    neg_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)
    if pos_hits > neg_hits:
        return "POSITIF", "🟢"
    elif neg_hits > pos_hits:
        return "NEGATIF", "🔴"
    else:
        return "NETRAL", "⚪"


def parse_pub_date(pub_date_str):
    """Untuk mengurutkan berita dari yang terbaru. Kalau format tanggal
    tidak dikenali, taruh di paling bawah daripada bikin app crash."""
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(pub_date_str)
    except (TypeError, ValueError):
        import datetime as _dt
        return _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)


def get_all_stock_news(stock_queries, max_items_per_stock=5):
    """Ambil berita untuk tiap saham di stock_queries, tandai sentimennya,
    lalu gabungkan semua dan urutkan dari yang paling baru.

    CATATAN PERBAIKAN: sebelumnya stock_queries di-hardcode ke 3 saham
    (ADRO/BRIS/TLKM) meskipun scanner sudah mencakup ~200 saham ISSI.
    Sekarang dipanggil dengan daftar dinamis (top saham hasil scan),
    lihat tombol "📰 NEWS" di bawah.
    """
    all_news = []
    for ticker, query in stock_queries.items():
        articles = fetch_stock_news(ticker, query, max_items_per_stock)
        for article in articles:
            combined_text = f"{article['title']} {article['description']}"
            sentiment, emoji = analyze_sentiment(combined_text)
            all_news.append({
                **article,
                "matched_stocks": [ticker],
                "sentiment": sentiment,
                "sentiment_emoji": emoji,
            })
    all_news.sort(key=lambda a: parse_pub_date(a["pub_date"]), reverse=True)
    return all_news


# ============================================================
#  THEME / CSS  — dark "trading dojo" look
# ============================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap');

    html, body, [class*="css"] { font-family: 'Poppins', sans-serif; }

    .stApp {
        background:
            radial-gradient(circle at 15% -10%, rgba(255,170,50,0.20) 0%, rgba(255,170,50,0) 45%),
            radial-gradient(circle at 100% 0%, rgba(255,120,30,0.10) 0%, rgba(255,120,30,0) 40%),
            #0b0c16;
        color: #f3f2ef;
    }
    #MainMenu, footer, header {visibility: hidden;}

    @keyframes floaty {
        0%   { transform: translateY(0px); }
        50%  { transform: translateY(-8px); }
        100% { transform: translateY(0px); }
    }
    @keyframes glow-pulse {
        0%   { opacity: 0.55; }
        50%  { opacity: 0.9; }
        100% { opacity: 0.55; }
    }

    /* ---------------------------------------------------------
       TOP HEADER — pita judul dengan nuansa mengambang, dreamy
    --------------------------------------------------------- */
    .orange-topbar {
        margin: -1rem -1rem 26px -1rem;
        padding: 50px 26px 60px 26px;
        background: transparent;
        position: relative;
        overflow: visible;
        text-align: center;
    }
    .orange-topbar-brand {
        font-size: 14px;
        font-weight: 700;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: #ffb35a;
    }
    .orange-topbar-title {
        font-size: 30px;
        font-weight: 800;
        color: #ffffff;
        margin-top: 8px;
        letter-spacing: 0.3px;
    }
    .orange-topbar-sub {
        font-size: 14px;
        color: #a9a7c4;
        margin-top: 6px;
    }

    .terminal-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 18px 26px;
        border-radius: 22px;
        background: linear-gradient(135deg, rgba(255,170,60,0.14), rgba(255,170,60,0.03));
        border: 1px solid rgba(255,170,60,0.20);
        box-shadow: 0 18px 40px -18px rgba(255,150,30,0.35);
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
        color: #a9a7c4;
        margin-top: 2px;
    }
    .pulse-dot {
        height: 9px; width: 9px; border-radius: 50%;
        background: #ffb35a; display: inline-block; margin-right: 6px;
        box-shadow: 0 0 10px #ffb35a;
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
        padding: 10px 20px;
        border-radius: 16px;
        background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.07);
        margin-bottom: 16px;
        font-size: 13px;
        color: #a9a7c4;
    }
    .update-strip b { color: #f3f2ef; }

    /* action buttons — pil oranye-kuning cerah, mengambang dengan glow */
    div.stButton > button {
        width: 100%;
        border-radius: 50px;
        border: none;
        background: linear-gradient(135deg, #ffc25c, #ff8a1f);
        color: #241300;
        font-weight: 700;
        padding: 0.75em 0.6em;
        box-shadow: 0 14px 28px -10px rgba(255,140,20,0.55);
        transition: 0.2s ease;
    }
    div.stButton > button:hover {
        transform: translateY(-3px);
        box-shadow: 0 20px 34px -10px rgba(255,140,20,0.65);
        color: #241300;
    }
    div.stButton > button:active { transform: translateY(-1px); }

    .card {
        background: rgba(255,255,255,0.045);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 22px;
        padding: 18px 20px;
        margin-bottom: 12px;
        box-shadow: 0 16px 32px -20px rgba(0,0,0,0.6);
    }
    .badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 700;
        margin-right: 6px;
    }
    .badge-buy { background: rgba(0,224,140,0.15); color: #00e08c; }
    .badge-wait { background: rgba(255,193,7,0.15); color: #ffc107; }
    .badge-sell { background: rgba(255,82,82,0.15); color: #ff5252; }
    .gain-up { color: #00e08c; font-weight: 700; }
    .gain-down { color: #ff5252; font-weight: 700; }
    .stDataFrame { border-radius: 16px; overflow: hidden; }

    /* ---------------------------------------------------------
       LOGIN / DAFTAR PANEL — card oranye dreamy, mengambang
    --------------------------------------------------------- */
    .auth-wrap {
        max-width: 460px;
        margin: 0 auto;
        position: relative;
        z-index: 5;
        padding: 0 12px;
    }
    .auth-logo-badge {
        width: 68px; height: 68px;
        margin: 0 auto 18px auto;
        border-radius: 24px;
        background: linear-gradient(145deg, #ffc25c, #ff8a1f);
        display: flex; align-items: center; justify-content: center;
        font-size: 32px;
        box-shadow: 0 18px 36px -8px rgba(255,140,20,0.55);
        animation: floaty 4.5s ease-in-out infinite;
    }
    .auth-panel {
        max-width: 340px;
        margin: 0 auto;
        padding: 30px 26px 24px 26px;
        border-radius: 32px;
        background: linear-gradient(160deg, #ffd28a 0%, #ff9a3d 55%, #ff7a1a 100%);
        border: 1px solid rgba(255,255,255,0.25);
        box-shadow:
            0 40px 70px -20px rgba(255,130,20,0.45),
            0 25px 50px -15px rgba(0,0,0,0.5);
    }
    .auth-title {
        text-align: center;
        font-size: 21px;
        font-weight: 800;
        color: #3a1c00;
        margin-bottom: 2px;
    }
    .auth-caption {
        text-align: center;
        font-size: 12.5px;
        color: #6b3a10;
        margin-bottom: 18px;
    }

    .auth-panel .stTextInput input {
        border-radius: 14px;
        background-color: rgba(255,255,255,0.85);
        border: 1px solid rgba(255,255,255,0.6);
        color: #3a1c00;
        padding: 0.6em 0.85em;
    }
    .auth-panel .stTextInput input::placeholder { color: #b07a45; }
    .auth-panel .stTextInput input:focus {
        border-color: #3a1c00;
        box-shadow: 0 0 0 3px rgba(58,28,0,0.18);
    }
    .auth-panel label { color: #4a2400 !important; font-weight: 600 !important; font-size: 13px !important; }

    /* di HP/tablet portrait: kolom form full lebar, stack ke bawah (bawaan Streamlit) */
    /* di desktop/tablet landscape (>640px): field disejajarkan kiri-kanan */
    @media (max-width: 640px) {
        div[data-testid="stHorizontalBlock"] {
            flex-direction: column !important;
            gap: 0 !important;
        }
        div[data-testid="stHorizontalBlock"] > div {
            width: 100% !important;
        }
        .auth-panel { max-width: 100%; }
    }
    @media (min-width: 641px) {
        .auth-wrap { max-width: 560px; }
        .auth-panel { max-width: 460px; }
    }

    /* tab styling — pil lembut di atas card oranye */
    .auth-panel .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        background: rgba(255,255,255,0.35);
        padding: 6px;
        border-radius: 50px;
    }
    .auth-panel .stTabs [data-baseweb="tab"] {
        border-radius: 50px;
        color: #6b3a10;
        font-weight: 600;
    }
    .auth-panel .stTabs [aria-selected="true"] {
        background: #2c1500;
        color: #ffd28a !important;
        box-shadow: 0 8px 18px -6px rgba(0,0,0,0.35);
    }

    .auth-panel div.stFormSubmitButton > button {
        border-radius: 50px;
        border: none;
        background: #2c1500;
        color: #ffd28a;
        font-weight: 800;
        padding: 0.75em 0.6em;
        box-shadow: 0 16px 30px -10px rgba(0,0,0,0.45);
    }
    .auth-panel div.stFormSubmitButton > button:hover {
        box-shadow: 0 22px 38px -10px rgba(0,0,0,0.55);
        transform: translateY(-3px);
        color: #ffd28a;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="orange-topbar">
    <div class="orange-topbar-brand">AI IDX Terminal</div>
    <div class="orange-topbar-title">🚀 AI IDX Trading Terminal PRO</div>
    <div class="orange-topbar-sub">khusus saham syariah, semoga berkah</div>
</div>
""", unsafe_allow_html=True)

# ============================================================
#  AUTH / SUBSCRIPTION GATE
# ============================================================
# Login pakai akun sendiri: username + password. Password di-hash (PBKDF2 +
# salt), tidak pernah disimpan dalam bentuk teks biasa.
#
# PERBAIKAN vs versi lama: sebelumnya SIAPA PUN bisa ketik email apa saja
# (termasuk email owner) dan langsung dianggap sah, tanpa verifikasi sama
# sekali. Sekarang akun diverifikasi dengan password hash yang tersimpan,
# dan username baru harus daftar dulu sebelum bisa masuk.
def get_user_status(identifier, user_db):
    username = identifier.replace("user:", "", 1)
    if username in OWNER_USERNAMES:
        return "owner"
    return user_db.get(identifier, {}).get("status", "inactive")


def render_auth_panel(user_db):
    st.markdown('<div class="auth-wrap">', unsafe_allow_html=True)
    st.markdown('<div class="auth-logo-badge">🔐</div>', unsafe_allow_html=True)
    st.markdown('<div class="auth-panel">', unsafe_allow_html=True)
    st.markdown('<div class="auth-title">Masuk untuk Melanjutkan</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="auth-caption">Belum punya akun? Daftar dulu — gratis, '
        'cukup username &amp; password, tidak perlu email.</div>',
        unsafe_allow_html=True,
    )

    tab_masuk, tab_daftar = st.tabs(["Masuk", "Daftar Baru"])

    with tab_masuk:
        with st.form("form_masuk_username", clear_on_submit=False):
            col_u, col_p = st.columns(2)
            with col_u:
                login_username = st.text_input("Username", key="login_username_input")
            with col_p:
                login_password = st.text_input("Password", type="password", key="login_password_input")
            submit_masuk = st.form_submit_button("Masuk", use_container_width=True)

        if submit_masuk:
            uname = login_username.strip().lower()
            record = user_db.get(f"user:{uname}")
            if not uname or not login_password:
                st.error("Username dan password wajib diisi.")
            elif uname not in OWNER_USERNAMES and (
                not record
                or not verify_password(
                    login_password, record.get("password_hash", ""), record.get("salt", "")
                )
            ):
                st.error("Username atau password salah.")
            elif uname in OWNER_USERNAMES and not record:
                # Owner belum pernah daftar password — arahkan ke tab Daftar dulu.
                st.error("Akun owner ini belum terdaftar. Silakan daftar dulu di tab 'Daftar Baru'.")
            elif uname in OWNER_USERNAMES and not verify_password(
                login_password, record.get("password_hash", ""), record.get("salt", "")
            ):
                st.error("Username atau password salah.")
            else:
                st.session_state["auth_identifier"] = f"user:{uname}"
                st.session_state["auth_display_name"] = uname
                save_login_cookie(uname)
                st.rerun()

    with tab_daftar:
        with st.form("form_daftar_username", clear_on_submit=False):
            new_username = st.text_input("Buat username")
            col_np, col_np2 = st.columns(2)
            with col_np:
                new_password = st.text_input("Buat password (min. 6 karakter)", type="password")
            with col_np2:
                new_password2 = st.text_input("Ulangi password", type="password")
            submit_daftar = st.form_submit_button("Daftar Sekarang", use_container_width=True)

        if submit_daftar:
            uname = new_username.strip().lower()
            key = f"user:{uname}"
            if not uname or not new_password:
                st.error("Username dan password wajib diisi.")
            elif len(new_password) < 6:
                st.error("Password minimal 6 karakter.")
            elif new_password != new_password2:
                st.error("Password dan ulangi password tidak sama.")
            elif key in user_db:
                st.error("Username sudah dipakai, coba username lain.")
            else:
                pwd_hash, salt = hash_password(new_password)
                user_db[key] = {
                    "type": "username",
                    "password_hash": pwd_hash,
                    "salt": salt,
                    "status": "inactive",
                    "created_at": datetime.now().isoformat(),
                }
                save_user_db(user_db)
                if uname in OWNER_USERNAMES:
                    st.success("Akun owner berhasil dibuat! Silakan masuk di tab 'Masuk'.")
                else:
                    st.success(
                        "Akun berhasil dibuat! Silakan masuk di tab 'Masuk'. "
                        "Hubungi admin untuk aktivasi langganan."
                    )

    st.markdown("</div>", unsafe_allow_html=True)  # tutup .auth-panel
    st.markdown("</div>", unsafe_allow_html=True)  # tutup .auth-wrap


# ---- Tentukan identitas user yang sedang login (kalau ada) ----
identifier = st.session_state.get("auth_identifier")
display_name = st.session_state.get("auth_display_name", identifier)

if not identifier:
    # Belum ada sesi di session_state (misal karena tab baru / reload) —
    # coba pulihkan otomatis dari cookie sebelum minta login ulang.
    _user_db_for_cookie_check = load_user_db()
    _uname_from_cookie = load_login_cookie(_user_db_for_cookie_check)
    if _uname_from_cookie:
        identifier = f"user:{_uname_from_cookie}"
        display_name = _uname_from_cookie
        st.session_state["auth_identifier"] = identifier
        st.session_state["auth_display_name"] = display_name

if not identifier:
    render_auth_panel(load_user_db())
    st.stop()

# ---- User sudah punya identitas, cek status langganan ----
user_db = load_user_db()
status = get_user_status(identifier, user_db)

col_status, col_logout = st.columns([5, 1])
with col_status:
    if status == "owner":
        st.success(f"👑 Owner access granted — {display_name}")
    elif status == "active":
        st.success(f"✅ Subscription aktif — {display_name}")
    else:
        st.warning(f"❌ Belum berlangganan — {display_name}")
with col_logout:
    if st.button("Logout", use_container_width=True):
        st.session_state.pop("auth_identifier", None)
        st.session_state.pop("auth_display_name", None)
        clear_login_cookie()
        st.rerun()

if status not in ("owner", "active"):
    st.stop()

# ============================================================
#  AUTO-REFRESH CONTROL (non-blocking)
# ============================================================
# PERBAIKAN: versi lama (PRO.py / PRO_v2.py) pakai time.sleep(60) + st.rerun(),
# yang MEMBLOKIR seluruh server thread selama 60 detik (app freeze total buat
# semua user). Ini pakai streamlit_autorefresh yang jalan di sisi browser,
# server tetap responsif.
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
    # PERBAIKAN: waktu mode "Manual (off)" dipilih, tidak ada cara lain untuk
    # memicu re-scan (sebelumnya bug: data jadi stuck selamanya di mode manual
    # karena ensure_scanned() hanya jalan sekali). Tombol ini forces re-scan.
    manual_refresh_clicked = st.button("🔄 Refresh Now", use_container_width=True)

if refresh_seconds > 0:
    st_autorefresh(interval=refresh_seconds * 1000, key="auto_refresh_ticker")

# ============================================================
#  DATA + INDIKATOR TEKNIKAL
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
    """Jalan sekali, hitung semua metrik untuk semua saham. Disimpan di
    session_state supaya tiap action button bisa slice/filter tanpa
    download ulang."""
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

_src_label = "🟢 Live dari IDX" if _issi_live else "🟡 Fallback list (IDX tidak terjangkau)"
st.caption(f"ISSI watchlist: {len(ISSI_STOCKS)} saham • {_src_label}")

# ---- Force fresh fetch di tiap autorefresh cycle / klik manual refresh ----
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


# Re-scan otomatis tiap autorefresh tick (tetap murah karena cache get_data
# ttl=60s), atau kalau tombol "Refresh Now" (mode manual) baru diklik.
if refresh_seconds > 0 or manual_refresh_clicked:
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
#  TOP GAINERS / TOP LOSERS STRIP (dalam watchlist)
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
#  ACTION BUTTONS ROW — tiap tombol = satu command
# ============================================================
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
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
with c7:
    btn_news = st.button("📰 NEWS")

# ---- SCAN MARKET : tabel penuh, semua saham, semua metrik ----
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

# ---- BANDAR DETECTOR : hanya saham dengan aktivitas bandar non-netral ----
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

# ---- BREAKOUT SCANNER : saham dengan breakout valid ----
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

# ---- SWING ALERT : saham yang drop >25% dari swing high ----
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

# ---- FAKE BREAKOUT : breakout yang baru saja gagal ----
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

# ---- NEWS : berita real (Google News RSS) untuk top saham hasil scan ----
# PERBAIKAN: sebelumnya query berita di-hardcode ke 3 saham (ADRO/BRIS/TLKM).
# Sekarang otomatis ambil top-5 saham skor tertinggi dari hasil scan, jadi
# selalu relevan dengan watchlist ~200 saham ISSI, bukan cuma 3 saham tetap.
if btn_news:
    df_scan = ensure_scanned()
    st.subheader("📰 Berita Saham Terkini (Top 5 skor tertinggi)")
    if df_scan.empty:
        st.info("Belum ada hasil scan untuk dicarikan beritanya.")
    else:
        top_tickers = df_scan.head(5)["stock"].tolist()
        stock_news_queries = {
            t: f"{t.replace('.JK', '')} saham" for t in top_tickers
        }
        with st.spinner("Mengambil berita terbaru..."):
            news_items = get_all_stock_news(stock_news_queries)

        if not news_items:
            st.info(
                "Belum ada berita terbaru untuk saham top watchlist kamu saat ini, "
                "atau Google News sedang tidak bisa diakses."
            )
        else:
            for news in news_items:
                stocks_str = ", ".join(t.replace(".JK", "") for t in news["matched_stocks"])
                with st.container(border=True):
                    st.markdown(f"**{news['title']}**")
                    cols = st.columns([1, 1, 2])
                    cols[0].markdown(f"📈 Saham: `{stocks_str}`")
                    cols[1].markdown(f"{news['sentiment_emoji']} {news['sentiment']}")
                    meta = " · ".join(x for x in [news["source"], news["pub_date"]] if x)
                    if meta:
                        cols[2].caption(meta)
                    if news["description"]:
                        st.caption(news["description"][:220] + ("..." if len(news["description"]) > 220 else ""))
                    if news["link"]:
                        st.markdown(f"[Baca selengkapnya]({news['link']})")

if not any([btn_scan, btn_bandar, btn_breakout, btn_swing, btn_fake, btn_telegram, btn_news]):
    st.markdown(
        '<div class="card" style="text-align:center; color:#a9a7c4;">'
        "Pilih salah satu tombol aksi di atas untuk mulai. "
        "Panel Top Gainer/Loser di atas sudah auto-update sesuai interval refresh."
        "</div>",
        unsafe_allow_html=True,
    )

# ================== STRIPE WEBHOOK ==================
# CATATAN PENTING: Streamlit TIDAK BISA menerima HTTP webhook secara langsung
# (tidak ada route/endpoint di app Streamlit). Fungsi di bawah ini hanya
# contoh LOGIKA-nya saja. Untuk implementasi nyata, kamu perlu:
#   1. Server terpisah (Flask/FastAPI) yang punya endpoint /stripe-webhook
#   2. Server itu memverifikasi signature webhook dari Stripe
#   3. Server itu menulis ke USER_DB_FILE (atau database asli seperti
#      Postgres/Supabase) — bukan ke dict Python yang cuma hidup di memory
#   4. App Streamlit ini membaca dari storage yang sama (sudah dilakukan
#      lewat load_user_db() di atas)
#
# PERBAIKAN: karena sistem login sekarang berbasis username (bukan email),
# saat bikin Stripe Checkout Session kamu WAJIB isi `client_reference_id`
# dengan username user yang sedang login (format "user:<username>", sama
# seperti key yang dipakai user_db), supaya webhook ini tahu akun mana yang
# harus diaktifkan setelah bayar. Contoh saat bikin checkout session:
#   stripe.checkout.Session.create(..., client_reference_id=identifier)
def stripe_webhook(event):
    user_db = load_user_db()
    if event["type"] == "checkout.session.completed":
        user_key = event["data"]["object"].get("client_reference_id")
        if user_key:
            existing = user_db.get(user_key, {})
            existing["status"] = "active"
            user_db[user_key] = existing
    elif event["type"] == "invoice.payment_failed":
        user_key = event["data"]["object"].get("client_reference_id")
        if user_key:
            existing = user_db.get(user_key, {})
            existing["status"] = "inactive"
            user_db[user_key] = existing
    save_user_db(user_db)
