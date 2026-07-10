import streamlit as st
import json
import os
import re
import html
import hashlib
import secrets as secrets_lib
import requests
import time
import xml.etree.ElementTree as ET
import pandas as pd
import yfinance as yf
import streamlit.components.v1 as components
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
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

# ============================================================
#  GOAPI.IO — dipakai KHUSUS untuk fitur "Broker Summary" (kode
#  broker + volume beli/jual per saham per hari). Ini data yang
#  TIDAK tersedia gratis di yfinance/IDX resmi, tapi GOAPI.IO
#  punya endpoint khusus untuk ini di paket gratisnya (kuota
#  terbatas, cukup untuk update 1x/hari setelah bursa tutup).
#
#  Daftar akun gratis + ambil API key di: https://app.goapi.io
#
#  PENTING — WAJIB DICEK MANUAL SEBELUM DIPAKAI:
#  Dokumentasi endpoint GOAPI (goapi.io/docs) ada di balik login,
#  jadi path URL & format auth header di bawah ini adalah pola
#  paling umum dipakai REST API sejenis (base URL "api.goapi.io"
#  dikonfirmasi dari SDK resmi mereka). Setelah kamu daftar dan
#  login ke dashboard GOAPI, buka tab "Docs" -> cari endpoint
#  "Broker Summary", lalu cocokkan/ganti GOAPI_BROKER_ENDPOINT_TEMPLATE
#  dan header di fetch_broker_summary() kalau ternyata beda.
#  (Update: path & header di bawah sudah disesuaikan ke
#  /v1/stock/idx/{symbol}/broker_summary + header X-API-KEY,
#  hasil verifikasi manual terhadap dokumentasi GOAPI.)
GOAPI_API_KEY = st.secrets.get("GOAPI_API_KEY", "ISI_API_KEY_GOAPI_LO")
GOAPI_BASE_URL = "https://api.goapi.io"
# Endpoint pakai path parameter (bukan query param) untuk kode saham:
# https://api.goapi.io/v1/stock/idx/{symbol}/broker_summary?date=YYYY-MM-DD
GOAPI_BROKER_ENDPOINT_TEMPLATE = GOAPI_BASE_URL + "/v1/stock/idx/{symbol}/broker_summary"
# Endpoint histori harga & fundamental GOAPI (WADAH — path di bawah ini
# perkiraan mengikuti pola endpoint broker_summary di atas; cek dokumentasi
# GOAPI kamu dan sesuaikan kalau ternyata beda, lalu isi
# _fetch_history_goapi() / _fetch_fundamentals_goapi() di bagian halaman
# detail saham).
GOAPI_HISTORY_ENDPOINT_TEMPLATE = GOAPI_BASE_URL + "/v1/stock/idx/{symbol}/history"
GOAPI_FUNDAMENTAL_ENDPOINT_TEMPLATE = GOAPI_BASE_URL + "/v1/stock/idx/{symbol}/fundamental"

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
    "AALI.JK", "ABBA.JK", "ABMM.JK", "ACES.JK", "ACST.JK", "ADCP.JK", "ADHI.JK", "ADMG.JK",
    "ADMR.JK", "ADRO.JK", "AGII.JK", "AGRO.JK", "AIMS.JK", "AISA.JK", "AKKU.JK", "AKPI.JK",
    "AKRA.JK", "ALDO.JK", "ALKA.JK", "ALMI.JK", "ALTO.JK", "AMAG.JK", "AMAR.JK", "AMFG.JK",
    "AMIN.JK", "AMMN.JK", "AMOR.JK", "AMRT.JK", "ANDI.JK", "ANJT.JK", "ANTM.JK", "APEX.JK",
    "APLI.JK", "APLN.JK", "ARCI.JK", "ARGO.JK", "ARII.JK", "ARKA.JK", "ARMY.JK", "ARNA.JK",
    "ARTA.JK", "ARTO.JK", "ASGR.JK", "ASHA.JK", "ASII.JK", "ASJT.JK", "ASMI.JK", "ASRI.JK",
    "ASRM.JK", "ASSA.JK", "ATAP.JK", "ATIC.JK", "ATLI.JK", "AUTO.JK", "AVIA.JK", "AWAN.JK",
    "AXIO.JK", "AYAM.JK", "BAJA.JK", "BALI.JK", "BANK.JK", "BAPA.JK", "BAPK.JK", "BARI.JK",
    "BATA.JK", "BAUT.JK", "BAYU.JK", "BBSS.JK", "BCIC.JK", "BCIP.JK", "BDMN.JK", "BEEF.JK",
    "BEKS.JK", "BELI.JK", "BESS.JK", "BEST.JK", "BFIN.JK", "BGTG.JK", "BHIT.JK", "BIKA.JK",
    "BIMA.JK", "BINA.JK", "BIRD.JK", "BISI.JK", "BKDP.JK", "BKSL.JK", "BKSW.JK", "BLTA.JK",
    "BLTZ.JK", "BLUE.JK", "BMAS.JK", "BMSR.JK", "BMTR.JK", "BNBA.JK", "BNBR.JK", "BNGA.JK",
    "BNII.JK", "BNLI.JK", "BOBA.JK", "BOGA.JK", "BOLA.JK", "BOLT.JK", "BOMC.JK", "BOSS.JK",
    "BPFI.JK", "BPII.JK", "BPTR.JK", "BREN.JK", "BRIS.JK", "BRMS.JK", "BRNA.JK", "BRPT.JK",
    "BSDE.JK", "BSIM.JK", "BSSR.JK", "BSWD.JK", "BTEK.JK", "BTEL.JK", "BTPS.JK", "BUKA.JK",
    "BUKK.JK", "BULL.JK", "BUMI.JK", "BUVA.JK", "BVIC.JK", "BWPT.JK", "BYAN.JK", "CAKK.JK",
    "CAMP.JK", "CARS.JK", "CASA.JK", "CASH.JK", "CASS.JK", "CBMF.JK", "CCSI.JK", "CEKA.JK",
    "CENT.JK", "CESS.JK", "CFIN.JK", "CGCK.JK", "CHIP.JK", "CINT.JK", "CITA.JK", "CITY.JK",
    "CLAY.JK", "CLEO.JK", "CLPI.JK", "CMNP.JK", "CMNT.JK", "CMPP.JK", "COAL.JK", "COCO.JK",
    "CPIN.JK", "CPRO.JK", "CSAP.JK", "CSIS.JK", "CSRA.JK", "CTBN.JK", "CTRA.JK", "CTTH.JK",
    "CUAN.JK", "CYBER.JK", "DADA.JK", "DART.JK", "DAYA.JK", "DEAL.JK", "DEFI.JK", "DEWA.JK",
    "DFAM.JK", "DGIK.JK", "DHHL.JK", "DIAN.JK", "DILD.JK", "DIVA.JK", "DKFT.JK", "DLTA.JK",
    "DMAS.JK", "DMMX.JK", "DMND.JK", "DNAR.JK", "DNET.JK", "DOID.JK", "DPTC.JK", "DPUM.JK",
    "DRMA.JK", "DSFI.JK", "DSNG.JK", "DSSA.JK", "DUCK.JK", "DUTI.JK", "DVLA.JK", "DYAN.JK",
    "EAST.JK", "ECII.JK", "EDII.JK", "EEST.JK", "EKAD.JK", "EKAW.JK", "ELPI.JK", "ELSA.JK",
    "EMDE.JK", "EMTK.JK", "ENRG.JK", "ENVY.JK", "EPMT.JK", "ERAA.JK", "ERTX.JK", "ESIP.JK",
    "ESSA.JK", "ESTA.JK", "ESTI.JK", "ETWA.JK", "EXCL.JK", "FAST.JK", "FASW.JK", "FILM.JK",
    "FITT.JK", "FLMC.JK", "FMII.JK", "FOLK.JK", "FORU.JK", "FPNI.JK", "FRESH.JK", "FUTR.JK",
    "GDST.JK", "GDYR.JK", "GEAR.JK", "GEMA.JK", "GEMS.JK", "GGRP.JK", "GHON.JK", "GJTL.JK",
    "GLOB.JK", "GLVA.JK", "GMFI.JK", "GMTD.JK", "GOLD.JK", "GOLL.JK", "GOTO.JK", "GPRA.JK",
    "GPSO.JK", "GRIA.JK", "GRPM.JK", "GSMF.JK", "GTBO.JK", "GWSA.JK", "GZCO.JK", "HAIS.JK",
    "HALO.JK", "HATM.JK", "HDFA.JK", "HDIT.JK", "HEAL.JK", "HELI.JK", "HERO.JK", "HEXA.JK",
    "HIKAM.JK", "HITS.JK", "HMSP.JK", "HOKI.JK", "HOME.JK", "HOTL.JK", "HRTA.JK", "HRUM.JK",
    "IATA.JK", "IBFN.JK", "IBOS.JK", "IBST.JK", "ICBP.JK", "ICON.JK", "IDPR.JK", "IEXP.JK",
    "IFII.JK", "IKAI.JK", "IKAN.JK", "IMAS.JK", "IMJS.JK", "IMPC.JK", "INAF.JK", "INCF.JK",
    "INDF.JK", "INDO.JK", "INDX.JK", "INDY.JK", "INKP.JK", "INPP.JK", "INPS.JK", "INRU.JK",
    "INTA.JK", "INTD.JK", "INTP.JK", "IPCC.JK", "IPCM.JK", "IPOL.JK", "IPTV.JK", "IRRA.JK",
    "ISAT.JK", "ISSP.JK", "ITMA.JK", "ITMG.JK", "JAST.JK", "JAWA.JK", "JECC.JK", "JGLE.JK",
    "JIHD.JK", "JKON.JK", "JKSW.JK", "JMAS.JK", "JPFA.JK", "JRPT.JK", "JSMR.JK", "JTPE.JK",
    "KAEF.JK", "KAYU.JK", "KBAG.JK", "KBLI.JK", "KBLM.JK", "KBLV.JK", "KDSI.JK", "KEEN.JK",
    "KEJU.JK", "KIJA.JK", "KKGI.JK", "KLAS.JK", "KLBF.JK", "KOCI.JK", "KOKI.JK", "KONI.JK",
    "KOPI.JK", "KPAL.JK", "KPAS.JK", "KPIG.JK", "KRAH.JK", "KRAS.JK", "KREN.JK", "LINK.JK",
    "LION.JK", "LMAS.JK", "LMPI.JK", "LPCK.JK", "LPKR.JK", "LPLI.JK", "LPPF.JK", "LPPS.JK",
    "LRNU.JK", "LSIP.JK", "LTLS.JK", "LUCK.JK", "MAIN.JK", "MAMI.JK", "MAPA.JK", "MAPI.JK",
    "MARI.JK", "MARK.JK", "MASA.JK", "MAXI.JK", "MBAP.JK", "MBMA.JK", "MBSS.JK", "MCOL.JK",
    "MDIA.JK", "MDKA.JK", "MDKI.JK", "MDLN.JK", "MEDC.JK", "MEDI.JK", "MEDIA.JK", "MEJA.JK",
    "META.JK", "MFIN.JK", "MFMI.JK", "MGLV.JK", "MICE.JK", "MIDI.JK", "MIKA.JK", "MINA.JK",
    "MIRA.JK", "MITI.JK", "MKPI.JK", "MKTR.JK", "MLBI.JK", "MLIA.JK", "MLPT.JK", "MMIX.JK",
    "MNCN.JK", "MPMX.JK", "MPPA.JK", "MPRO.JK", "MSIN.JK", "MSKY.JK", "MTDL.JK", "MTEL.JK",
    "MTFN.JK", "MTLA.JK", "MTMH.JK", "MTPS.JK", "MTRA.JK", "MTSM.JK", "MURN.JK", "MYOR.JK",
    "MYRX.JK", "MYTX.JK", "NANO.JK", "NASA.JK", "NAYZ.JK", "NELY.JK", "NEON.JK", "NFCX.JK",
    "NIPS.JK", "NIRO.JK", "NKIL.JK", "NPGF.JK", "NRCA.JK", "NREC.JK", "NTBK.JK", "NUSA.JK",
    "NVOM.JK", "NZIA.JK", "OASA.JK", "OBMD.JK", "ODEC.JK", "OILS.JK", "OKAS.JK", "OMED.JK",
    "OPMS.JK", "PADI.JK", "PALM.JK", "PANR.JK", "PANS.JK", "PBRX.JK", "PBSA.JK", "PCAR.JK",
    "PDES.JK", "PEGE.JK", "PEHA.JK", "PGAS.JK", "PGUN.JK", "PICO.JK", "PIKA.JK", "PJAA.JK",
    "PKPK.JK", "PLIN.JK", "PLJA.JK", "PMJS.JK", "PMMP.JK", "PNBS.JK", "PNSE.JK", "POLA.JK",
    "POLI.JK", "POLL.JK", "POLY.JK", "POOL.JK", "PORT.JK", "POWR.JK", "PPAT.JK", "PPRE.JK",
    "PPRO.JK", "PRAS.JK", "PRDA.JK", "PRIM.JK", "PSAB.JK", "PSDN.JK", "PSGO.JK", "PSKT.JK",
    "PSSI.JK", "PTBA.JK", "PTDU.JK", "PTIS.JK", "PTMP.JK", "PTPP.JK", "PTSN.JK", "PTSP.JK",
    "PUDP.JK", "PURA.JK", "PURE.JK", "PUSH.JK", "PWON.JK", "PYFA.JK", "RAFI.JK", "RAJA.JK",
    "RALS.JK", "RAMA.JK", "RANC.JK", "RBMS.JK", "RCCC.JK", "RELI.JK", "REMD.JK", "RICK.JK",
    "RIGS.JK", "RIMO.JK", "RMBA.JK", "RMKO.JK", "RODA.JK", "SAGE.JK", "SAMF.JK", "SAMU.JK",
    "SAPX.JK", "SATU.JK", "SBAT.JK", "SBMA.JK", "SCCO.JK", "SCMA.JK", "SCNP.JK", "SDMU.JK",
    "SDPC.JK", "SEMA.JK", "SFAN.JK", "SGER.JK", "SGJL.JK", "SHID.JK", "SIAP.JK", "SIDO.JK",
    "SILO.JK", "SIMA.JK", "SIMP.JK", "SINI.JK", "SIPD.JK", "SKBM.JK", "SKLT.JK", "SKYB.JK",
    "SLIS.JK", "SMAA.JK", "SMAR.JK", "SMCB.JK", "SMDM.JK", "SMDR.JK", "SMGR.JK", "SMKL.JK",
    "SMKM.JK", "SMRA.JK", "SMSM.JK", "SNLK.JK", "SOCI.JK", "SOFE.JK", "SOHO.JK", "SONA.JK",
    "SOUL.JK", "SPMA.JK", "SPTO.JK", "SRSN.JK", "SRTG.JK", "SSIA.JK", "SSMS.JK", "SSTM.JK",
    "STAA.JK", "STAR.JK", "STTP.JK", "SUGI.JK", "SULI.JK", "SUNI.JK", "SURE.JK", "TALF.JK",
    "TAMU.JK", "TAPG.JK", "TARA.JK", "TAXI.JK", "TBIG.JK", "TBLA.JK", "TBMS.JK", "TCID.JK",
    "TCPI.JK", "TEBE.JK", "TECH.JK", "TELE.JK", "TFAS.JK", "TFCO.JK", "TGKA.JK", "TIFA.JK",
    "TINS.JK", "TIRA.JK", "TIRT.JK", "TKIM.JK", "TLKM.JK", "TMAS.JK", "TMPO.JK", "TNCA.JK",
    "TOBA.JK", "TOOL.JK", "TOPD.JK", "TOTL.JK", "TOWR.JK", "TPIA.JK", "TPMA.JK", "TRIL.JK",
    "TRIM.JK", "TRIN.JK", "TRIS.JK", "TRJA.JK", "TRJU.JK", "TRUE.JK", "TRUK.JK", "TRUS.JK",
    "TSPC.JK", "TUGU.JK", "TYRE.JK", "UBER.JK", "UCIDA.JK", "UFOE.JK", "ULTJ.JK", "UNIC.JK",
    "UNIQ.JK", "UNTR.JK", "UNVR.JK", "URBN.JK", "VATE.JK", "VICO.JK", "VINS.JK", "VIPT.JK",
    "VKTR.JK", "VOKS.JK", "VRNA.JK", "WAPO.JK", "WEGE.JK", "WEHA.JK", "WIFI.JK", "WIIM.JK",
    "WIKA.JK", "WINS.JK", "WIRG.JK", "WJSK.JK", "WMPP.JK", "WMUU.JK", "WOCK.JK", "WOOD.JK",
    "WOWS.JK", "WSKT.JK", "WTIK.JK", "WTON.JK", "YPAS.JK", "YULE.JK", "ZAGO.JK", "ZATA.JK",
    "ZBRA.JK",
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
    (ADRO/BRIS/TLKM) meskipun scanner sudah mencakup ~600 saham ISSI.
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

    /* PERBAIKAN: Streamlit versi baru (1.36+) kasih padding-top ~96px
       bawaan di block-container (dulu lebih kecil). Ini yang bikin ada
       jarak kosong gede banget di atas sebelum header muncul. Kita
       kecilkan supaya halaman pembuka nggak keliatan "ngambang". */
    div[data-testid="stAppViewBlockContainer"],
    .block-container {
        padding-top: 2.2rem !important;
        padding-bottom: 2rem !important;
    }

    /* ---------------------------------------------------------
       TABEL HASIL SCAN — header KAPITAL, rata tengah, lebih besar
    --------------------------------------------------------- */
    .scan-table-wrap {
        overflow-x: auto;
        border-radius: 12px;
        border: 1px solid rgba(255,255,255,0.08);
        margin-bottom: 14px;
    }
    .scan-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
        white-space: nowrap;
    }
    .scan-table thead th {
        text-transform: uppercase;
        text-align: center;
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.6px;
        color: #ffb35a;
        background: rgba(255,179,90,0.12);
        padding: 12px 14px;
        border-bottom: 1px solid rgba(255,179,90,0.28);
    }
    .scan-table tbody td {
        text-align: center;
        padding: 9px 14px;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        color: #f3f2ef;
    }
    .scan-table tbody tr:nth-child(even) { background: rgba(255,255,255,0.02); }
    .scan-table tbody tr:hover { background: rgba(255,179,90,0.06); }
    .stock-link {
        color: #ffb35a;
        font-weight: 700;
        text-decoration: none;
        cursor: pointer;
    }
    .stock-link:hover { text-decoration: underline; }

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
        margin: 0 -1rem 30px -1rem;
        padding: 8px 26px 34px 26px;
        background: transparent;
        position: relative;
        overflow: visible;
        text-align: center;
    }
    .orange-topbar-badge {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 2.5px;
        text-transform: uppercase;
        color: #ffb35a;
        background: rgba(255,179,90,0.10);
        border: 1px solid rgba(255,179,90,0.28);
        padding: 6px 18px;
        border-radius: 50px;
    }
    .orange-topbar-badge-dot {
        height: 6px; width: 6px; border-radius: 50%;
        background: #ffb35a;
        box-shadow: 0 0 8px #ffb35a;
        animation: pulse 1.5s ease-in-out infinite;
    }
    .orange-topbar-title {
        font-size: 30px;
        font-weight: 800;
        color: #ffffff;
        margin-top: 16px;
        letter-spacing: 0.3px;
        line-height: 1.25;
    }
    .orange-topbar-pro-chip {
        display: inline-block;
        font-size: 12px;
        font-weight: 800;
        background: linear-gradient(135deg, #ffc25c, #ff8a1f);
        color: #241300;
        padding: 2px 12px;
        border-radius: 20px;
        vertical-align: middle;
        margin-left: 8px;
        box-shadow: 0 8px 16px -6px rgba(255,140,20,0.6);
    }
    .orange-topbar-sub {
        font-size: 14px;
        color: #a9a7c4;
        margin-top: 8px;
    }
    .orange-topbar-divider {
        width: 64px;
        height: 3px;
        border-radius: 3px;
        margin: 18px auto 0 auto;
        background: linear-gradient(90deg, transparent, #ffb35a, transparent);
        opacity: 0.7;
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

    /* daftar Top Gainer/Loser: bisa nampung sampai 50 saham, tapi tampilan
       dibatasi ~5 baris kelihatan, sisanya di-scroll biar UI tidak panjang */
    .scroll-list {
        max-height: 232px;
        overflow-y: auto;
        padding-right: 6px;
    }
    .scroll-list::-webkit-scrollbar { width: 6px; }
    .scroll-list::-webkit-scrollbar-thumb {
        background: rgba(255,255,255,0.15);
        border-radius: 10px;
    }
    .scroll-item {
        background: rgba(255,255,255,0.045);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 14px;
        padding: 9px 14px;
        margin-bottom: 6px;
        font-size: 13px;
    }
    .scroll-item:last-child { margin-bottom: 0; }

    /* ---------------------------------------------------------
       LOGIN / DAFTAR PANEL — card oranye dreamy, mengambang
    --------------------------------------------------------- */
    /* PERBAIKAN: .auth-wrap & .auth-panel dulu div manual yang nggak
       benar-benar membungkus widget Streamlit di dalamnya (lihat komentar
       di render_auth_panel()). Sekarang keduanya adalah st.container(key=...)
       asli, yang otomatis dapat class ".st-key-<key>" pada wrapper div-nya,
       dan wrapper itu BENERAN jadi parent dari semua widget di dalam blok
       `with`-nya. Kita reset padding/gap bawaan container Streamlit dulu
       biar nggak dobel sama padding custom kita. */
    .st-key-auth_wrap {
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
    .st-key-auth_panel {
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
    /* elemen internal st.container Streamlit punya gap default antar-block;
       kita kecilkan supaya rapat kayak card HTML aslinya */
    .st-key-auth_panel div[data-testid="stVerticalBlock"] {
        gap: 0.4rem;
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

    /* PERBAIKAN: di Streamlit versi baru, <input> dibungkus 2 lapis div
       react-aria (.react-aria-TextField > [data-testid="stTextInputRootElement"]),
       dan wrapper PALING LUAR itu ("stTextInputRootElement") punya
       background/border ABU-ABU BAWAAN sendiri (#f0f2f6, radius 8px).
       Kalau cuma elemen <input> yang di-warnain, wrapper abu-abu itu
       tetap keliatan sebagai "pinggiran" di sekitar kotak — makanya
       kotak input keliatan cuma separuh ke-style. Sekarang kita warnai
       WRAPPER-nya juga (bukan cuma <input>-nya), dan bikin <input>
       transparan supaya menyatu sempurna jadi satu kotak utuh. */
    .st-key-auth_panel .stTextInput [data-testid="stTextInputRootElement"] {
        border-radius: 14px;
        background-color: rgba(255,255,255,0.85);
        border: 1px solid rgba(255,255,255,0.6);
        transition: 0.15s ease;
    }
    .st-key-auth_panel .stTextInput [data-testid="stTextInputRootElement"]:focus-within {
        border-color: #3a1c00;
        box-shadow: 0 0 0 3px rgba(58,28,0,0.18);
    }
    .st-key-auth_panel .stTextInput input {
        background-color: transparent;
        color: #3a1c00;
        padding: 0.6em 0.85em;
    }
    .st-key-auth_panel .stTextInput input::placeholder { color: #b07a45; }
    .st-key-auth_panel label { color: #4a2400 !important; font-weight: 600 !important; font-size: 13px !important; }

    /* di HP/tablet portrait: kolom form full lebar, stack ke bawah (bawaan Streamlit) */
    /* di desktop/tablet landscape (>640px): field disejajarkan kiri-kanan */
    @media (max-width: 640px) {
        .st-key-auth_panel div[data-testid="stHorizontalBlock"] {
            flex-direction: column !important;
            gap: 0 !important;
        }
        .st-key-auth_panel div[data-testid="stHorizontalBlock"] > div {
            width: 100% !important;
        }
        .st-key-auth_panel { max-width: 100%; }
    }
    @media (min-width: 641px) {
        .st-key-auth_wrap { max-width: 560px; }
        .st-key-auth_panel { max-width: 460px; }
    }

    /* tab styling — pil lembut di atas card oranye.
       PERBAIKAN: Streamlit 1.59.1 sudah tidak lagi memakai BaseWeb untuk
       komponen Tabs (jadi atribut [data-baseweb="tab-list"] / "tab" yang
       dipakai kode lama sudah TIDAK ADA lagi di DOM versi ini — itu
       kenapa tab-nya render polos/default, bukan pil oranye). Komponen
       Tabs sekarang dibangun pakai react-aria, dengan struktur:
         .stTabs > div[role="tablist"] > div[data-testid="stTab"] (role="tab")
       dan tab yang aktif ditandai atribut aria-selected="true" (ini masih
       sama seperti sebelumnya). React-aria juga menyisipkan elemen
       <div class="react-aria-SelectionIndicator"> sebagai garis bawah
       aktif bawaan — kita sembunyikan itu karena style pil kita pakai
       background solid, bukan garis bawah. */
    .st-key-auth_panel .stTabs [role="tablist"] {
        display: flex;
        gap: 6px;
        background: rgba(255,255,255,0.35);
        padding: 6px;
        border-radius: 50px;
    }
    .st-key-auth_panel .stTabs [data-testid="stTab"] {
        flex: 1;
        justify-content: center;
        border-radius: 50px;
        color: #6b3a10;
        font-weight: 600;
        transition: 0.15s ease;
    }
    .st-key-auth_panel .stTabs [data-testid="stTab"] .react-aria-SelectionIndicator {
        display: none;
    }
    .st-key-auth_panel .stTabs [data-testid="stTab"][aria-selected="true"] {
        background: #2c1500;
        color: #ffd28a !important;
        box-shadow: 0 8px 18px -6px rgba(0,0,0,0.35);
    }
    .st-key-auth_panel .stTabs [data-testid="stTab"][aria-selected="true"] p {
        color: #ffd28a !important;
    }

    .st-key-auth_panel div.stFormSubmitButton > button {
        border-radius: 50px;
        border: none;
        background: #2c1500;
        color: #ffd28a;
        font-weight: 800;
        padding: 0.75em 0.6em;
        box-shadow: 0 16px 30px -10px rgba(0,0,0,0.45);
    }
    .st-key-auth_panel div.stFormSubmitButton > button:hover {
        box-shadow: 0 22px 38px -10px rgba(0,0,0,0.55);
        transform: translateY(-3px);
        color: #ffd28a;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="orange-topbar">
    <div class="orange-topbar-badge">
        <span class="orange-topbar-badge-dot"></span> AI IDX Terminal
    </div>
    <div class="orange-topbar-title">
        🚀 AI IDX Trading Terminal <span class="orange-topbar-pro-chip">PRO</span>
    </div>
    <div class="orange-topbar-sub">Khusus saham syariah — semoga berkah &amp; cuan 🌙</div>
    <div class="orange-topbar-divider"></div>
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
    # PERBAIKAN: dulu pakai <div class="auth-wrap">...</div> manual via
    # beberapa panggilan st.markdown() terpisah. Itu TIDAK benar-benar
    # membungkus tabs/form/input di bawahnya, karena tiap pemanggilan
    # st.markdown/st.tabs/st.form di Streamlit jadi elemen DOM sendiri-
    # sendiri (sibling), bukan nested sesuai urutan tag HTML yang ditulis.
    # Sekarang pakai st.container(key=...) asli, yang beneran membungkus
    # semua widget di dalam blok `with`-nya sebagai satu parent di DOM,
    # dan otomatis dapat CSS class ".st-key-<key>" yang stabil buat di-style.
    with st.container(key="auth_wrap"):
        st.markdown('<div class="auth-logo-badge">🔐</div>', unsafe_allow_html=True)

        with st.container(key="auth_panel"):
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
#  AUTO-REFRESH CONTROL (non-blocking, TIDAK nge-freeze seluruh halaman)
# ============================================================
# PERBAIKAN: versi lama (PRO.py / PRO_v2.py) pakai time.sleep(60) + st.rerun(),
# yang MEMBLOKIR seluruh server thread selama 60 detik (app freeze total buat
# semua user).
#
# PERBAIKAN (biar gak "burem"/nunggu): sebelumnya dipakai streamlit_autorefresh,
# yang me-rerun SELURUH halaman tiap tick — jadi walaupun cuma panel Top
# Gainer/Loser yang butuh update, semua elemen lain (termasuk hasil di bagian
# bawah) ikut nge-freeze/pudar sebentar tiap kali rerun terjadi. Sekarang
# dipakai st.fragment: panel Top Gainer/Loser dibungkus jadi "fragment" yang
# refresh SENDIRIAN di background sesuai jadwal (run_every), tanpa menyentuh
# atau nge-freeze bagian lain dari halaman sama sekali. Butuh streamlit >= 1.33.
#
# PERBAIKAN (kecepatan): data pusat (yfinance untuk .JK) baru berubah tiap
# ~15 menit, jadi auto-refresh disamakan jadi 15 menit (bukan 30-60 detik lagi)
# — dikombinasikan dengan batch-download di get_all_data() di bawah, refresh
# jadi jauh lebih cepat dan tidak lagi kerasa lama.
REFRESH_OPTIONS = {"Otomatis (15 menit)": 900, "Manual (off)": 0}

with st.sidebar:
    st.markdown("### ⚙️ Pengaturan")
    refresh_label = st.radio("Interval auto-refresh", list(REFRESH_OPTIONS.keys()), index=0)
    refresh_seconds = REFRESH_OPTIONS[refresh_label]
    st.caption(
        "Auto-refresh 15 menit cuma berlaku buat panel Top Gainer / Top Loser "
        "(mengikuti jadwal update data pusat), jalan diam-diam di background "
        "tanpa mengganggu bagian lain. Bagian bawah (Scan Market, Bandar "
        "Detector, dst) tetap manual — klik ulang tombolnya atau "
        "'Refresh Now' kalau mau data terbaru."
    )
    # PERBAIKAN: waktu mode "Manual (off)" dipilih, tidak ada cara lain untuk
    # memicu re-scan (sebelumnya bug: data jadi stuck selamanya di mode manual
    # karena ensure_scanned() hanya jalan sekali). Tombol ini forces re-scan.
    manual_refresh_clicked = st.button("🔄 Refresh Now", use_container_width=True)

# ============================================================
#  DATA + INDIKATOR TEKNIKAL
# ============================================================
# PERBAIKAN (kecepatan): sebelumnya tiap saham di-download SATU-SATU secara
# berurutan (get_data dipanggil ~600x dalam loop for), jadi build_full_scan

# bisa makan waktu bermenit-menit. Sekarang semua ticker diambil dalam SATU
# panggilan yf.download (threads=True bikin yfinance download banyak ticker
# sekaligus secara paralel di background), jauh lebih cepat.
@st.cache_data(ttl=60, show_spinner=False)
def get_all_data(stocks_tuple):
    stocks = list(stocks_tuple)
    raw = yf.download(
        stocks,
        period="3mo",
        interval="1d",
        progress=False,
        group_by="ticker",
        threads=True,
        auto_adjust=True,
    )
    data = {}
    for stock in stocks:
        try:
            if len(stocks) == 1:
                df = raw
            else:
                df = raw[stock]
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.dropna(subset=["Close", "Open", "High", "Low", "Volume"])
            if len(df) >= 30:
                data[stock] = df
        except Exception:
            continue
    return data


def _display_ticker(t):
    """Buang suffix .JK biar tampilan UI cukup nama sahamnya aja, mis. DEWA.JK -> DEWA."""
    return t[:-3] if t.endswith(".JK") else t


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


def render_html_table(df):
    """Render dataframe sebagai tabel HTML custom supaya header bisa dibuat
    KAPITAL + rata tengah + lebih besar (st.dataframe bawaan Streamlit
    menggambar header di canvas jadi tidak bisa di-styling pakai CSS)."""
    if df is None or df.empty:
        st.info("Tidak ada data.")
        return

    df_fmt = df.copy()
    for col in df_fmt.columns:
        if df_fmt[col].dtype == bool:
            df_fmt[col] = df_fmt[col].map(lambda v: "✔️" if v else "–")

    header_html = "".join(
        f"<th>{str(col).replace('_', ' ').upper()}</th>" for col in df_fmt.columns
    )
    body_rows = []
    for idx, row in df_fmt.iterrows():
        cells = ""
        for col in df_fmt.columns:
            if col == "stock":
                # Nama saham diklik -> pindah ke halaman detail saham itu
                # (lihat render_stock_detail_page), lewat query param URL.
                cells += f'<td><a class="stock-link" href="?stock={row[col]}">{row[col]}</a></td>'
            else:
                cells += f"<td>{row[col]}</td>"
        body_rows.append(f"<tr><td>{idx}</td>{cells}</tr>")

    table_html = f"""
    <div class="scan-table-wrap">
      <table class="scan-table">
        <thead><tr><th>NO</th>{header_html}</tr></thead>
        <tbody>{''.join(body_rows)}</tbody>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)


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


# ============================================================
#  BROKER SUMMARY (via GOAPI.IO) — kode broker + volume beli/jual
#  per saham, khusus data EOD (End of Day / pas bursa tutup).
# ============================================================
def last_trading_date():
    """Tentukan 'tanggal bursa terakhir' yang datanya sudah pasti closing:
    - Weekend (Sabtu/Minggu) -> mundur ke hari Jumat terakhir.
    - Hari kerja tapi masih di bawah jam 16:00 WIB -> data hari ini
      belum tentu final, jadi mundur ke hari kerja sebelumnya.
    - Hari kerja setelah jam 16:00 WIB -> pakai tanggal hari ini.
    CATATAN: ini TIDAK memperhitungkan tanggal merah/libur bursa
    (butuh kalender libur BEI kalau mau 100% akurat), jadi anggap
    sebagai perkiraan yang cukup baik untuk kebanyakan kasus.
    """
    now = datetime.now()
    d = now.date()
    if now.weekday() < 5 and now.hour >= 16:
        pass  # hari kerja, sudah lewat jam tutup -> pakai hari ini
    else:
        # mundur ke hari kerja sebelumnya (Senin=0 ... Minggu=6)
        d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d = d - timedelta(days=1)
    return d


@st.cache_data(ttl=20 * 3600, show_spinner=False)
def fetch_broker_summary(symbol, date_str):
    """Ambil ringkasan broker (kode broker, sisi beli/jual, lot, value,
    avg price, tipe investor) untuk 1 saham pada 1 tanggal tertentu.
    date_str format: YYYY-MM-DD. Return (DataFrame, error_message).
    DataFrame kosong + error_message terisi kalau gagal.
    """
    clean_symbol = symbol[:-3] if symbol.endswith(".JK") else symbol
    if not GOAPI_API_KEY or GOAPI_API_KEY == "ISI_API_KEY_GOAPI_LO":
        return pd.DataFrame(), "GOAPI_API_KEY belum diisi di Secrets."
    try:
        resp = requests.get(
            GOAPI_BROKER_ENDPOINT_TEMPLATE.format(symbol=clean_symbol),
            params={"date": date_str},
            headers={"X-API-KEY": GOAPI_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not rows:
            return pd.DataFrame(), None
        records = []
        for r in rows:
            broker = r.get("broker", {}) or {}
            records.append({
                "broker_code": broker.get("code", r.get("code", "-")),
                "broker_name": broker.get("name", "-"),
                "side": (r.get("side") or "-").upper(),
                "lot": r.get("lot", 0),
                "value": r.get("value", 0),
                "avg": r.get("avg", 0),
                "investor": r.get("investor", "-"),
            })
        return pd.DataFrame(records), None
    except requests.exceptions.HTTPError as e:
        return pd.DataFrame(), f"GOAPI error: {e}"
    except Exception as e:
        return pd.DataFrame(), f"Gagal ambil data broker: {e}"


def _render_broker_summary_panel():
    """Isi panel Broker Summary yang fungsional (dipanggil hanya kalau
    GOAPI_API_KEY sudah dikonfigurasi — lihat gate 'locked' di panel UI)."""
    default_date = last_trading_date()
    bcol1, bcol2, bcol3 = st.columns([2, 1, 1])
    with bcol1:
        broker_symbol = st.selectbox(
            "Pilih saham",
            options=ISSI_STOCKS,
            format_func=_display_ticker,
            key="broker_symbol_select",
        )
    with bcol2:
        broker_date = st.date_input(
            "Tanggal (hari bursa)",
            value=default_date,
            max_value=datetime.now().date(),
            key="broker_date_input",
        )
    with bcol3:
        st.write("")
        st.write("")
        fetch_broker_clicked = st.button("🔄 Ambil Data", key="fetch_broker_btn")

    # Banner "data ini terakhir tanggal & jam berapa" — supaya kalau
    # dibuka besok pagi langsung kelihatan ini data closing kemarin.
    st.markdown(
        f'<div class="update-strip">'
        f'<span>🕒 Data Broker untuk: <b>{broker_date.strftime("%d %B %Y")}</b> '
        f'pukul <b>16:00 WIB</b> (closing/EOD, sumber: GOAPI.IO)</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    broker_cache_key = f"{broker_symbol}_{broker_date.isoformat()}"
    if (
        fetch_broker_clicked
        or st.session_state.get("broker_last_key") != broker_cache_key
    ):
        with st.spinner(f"Mengambil broker summary {_display_ticker(broker_symbol)}..."):
            df_broker, broker_err = fetch_broker_summary(broker_symbol, broker_date.isoformat())
        st.session_state.broker_df = df_broker
        st.session_state.broker_err = broker_err
        st.session_state.broker_last_key = broker_cache_key

    df_broker = st.session_state.get("broker_df", pd.DataFrame())
    broker_err = st.session_state.get("broker_err")

    if broker_err:
        st.error(broker_err)
        st.caption(
            "Cek: (1) GOAPI_API_KEY sudah diisi di Secrets, (2) endpoint/"
            "header di fetch_broker_summary() sudah cocok dengan dokumentasi "
            "resmi di dashboard GOAPI kamu (goapi.io/docs), (3) kuota API "
            "gratis belum habis."
        )
    elif df_broker.empty:
        st.info(
            f"Belum ada data broker untuk {_display_ticker(broker_symbol)} "
            f"pada {broker_date.strftime('%d %B %Y')}. Coba tanggal hari "
            "bursa lain (bukan Sabtu/Minggu/libur bursa)."
        )
    else:
        buyers = (
            df_broker[df_broker["side"].str.contains("B", na=False)]
            .sort_values("value", ascending=False)
        )
        sellers = (
            df_broker[df_broker["side"].str.contains("S", na=False)]
            .sort_values("value", ascending=False)
        )

        bc1, bc2 = st.columns(2)
        with bc1:
            st.markdown("**🟢 Top Broker Pembeli**")
            if buyers.empty:
                st.caption("Tidak ada data broker pembeli.")
            else:
                st.dataframe(
                    buyers[["broker_code", "broker_name", "lot", "value", "avg", "investor"]],
                    use_container_width=True,
                    hide_index=True,
                )
        with bc2:
            st.markdown("**🔴 Top Broker Penjual**")
            if sellers.empty:
                st.caption("Tidak ada data broker penjual.")
            else:
                st.dataframe(
                    sellers[["broker_code", "broker_name", "lot", "value", "avg", "investor"]],
                    use_container_width=True,
                    hide_index=True,
                )

        total_buy_val = buyers["value"].sum() if not buyers.empty else 0
        total_sell_val = sellers["value"].sum() if not sellers.empty else 0
        net_val = total_buy_val - total_sell_val
        st.markdown(
            f'<div class="card">Total Beli: <b>{total_buy_val:,.0f}</b> &nbsp;|&nbsp; '
            f'Total Jual: <b>{total_sell_val:,.0f}</b> &nbsp;|&nbsp; '
            f'Net: <b>{net_val:+,.0f}</b> '
            f'({"AKUMULASI 🟢" if net_val > 0 else "DISTRIBUSI 🔴" if net_val < 0 else "NETRAL"})'
            f'</div>',
            unsafe_allow_html=True,
        )


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
    all_data = get_all_data(tuple(ISSI_STOCKS))
    for stock, df in all_data.items():
        try:
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

# ---- State awal ----
if "scan_df" not in st.session_state:
    st.session_state.scan_df = None
if "last_updated" not in st.session_state:
    st.session_state.last_updated = None
if "last_updated_epoch" not in st.session_state:
    st.session_state.last_updated_epoch = None


def ensure_scanned(force=False):
    """Dipakai oleh tombol aksi di bagian bawah — HANYA baca cache yang
    sudah ada, tidak pernah memicu fetch baru sendiri (biar bagian bawah
    tetap manual)."""
    if force or st.session_state.scan_df is None:
        st.session_state.scan_df = build_full_scan()
        st.session_state.last_updated = datetime.now()
        st.session_state.last_updated_epoch = time.time()
    return st.session_state.scan_df


# ============================================================
#  HALAMAN DETAIL SAHAM — muncul saat nama saham diklik di tabel
# ============================================================
# WADAH DATA PROVIDER: dua fungsi _fetch_..._goapi() di bawah ini
# sengaja dikosongkan (placeholder). Begitu kamu langganan GOAPI:
#   1. GOAPI_API_KEY di Secrets sudah kepakai (sama dengan Broker Summary)
#   2. Isi logic fetch di _fetch_history_goapi() / _fetch_fundamentals_goapi(),
#      sesuaikan path-nya dengan GOAPI_HISTORY_ENDPOINT_TEMPLATE /
#      GOAPI_FUNDAMENTAL_ENDPOINT_TEMPLATE di bagian CONFIG (cek dokumentasi
#      GOAPI dulu, path di sana baru perkiraan)
# Selama itu belum diisi, semua otomatis fallback ke yfinance, dan field
# yang tidak tersedia ditulis "Data tidak tersedia" (bukan error/crash).

def _fetch_history_goapi(ticker_jk):
    """PLACEHOLDER — belum diimplementasikan. Isi nanti kalau sudah
    langganan GOAPI (pakai GOAPI_HISTORY_ENDPOINT_TEMPLATE). Harus
    return DataFrame dengan kolom: Date, Open, High, Low, Close, Volume."""
    raise NotImplementedError("Fetch histori GOAPI belum diimplementasikan")


def _fetch_fundamentals_goapi(ticker_jk):
    """PLACEHOLDER — belum diimplementasikan. Isi nanti kalau sudah
    langganan GOAPI (pakai GOAPI_FUNDAMENTAL_ENDPOINT_TEMPLATE). Harus
    return dict dengan key: nama, sektor, industri, market_cap, per, eps,
    mata_uang."""
    raise NotImplementedError("Fetch fundamental GOAPI belum diimplementasikan")


_goapi_configured_for_detail = bool(GOAPI_API_KEY) and GOAPI_API_KEY != "ISI_API_KEY_GOAPI_LO"


@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_history_1y(ticker_jk):
    """Histori harga 1 tahun untuk 1 saham. Prioritas GOAPI kalau sudah
    dikonfigurasi, fallback ke yfinance."""
    if _goapi_configured_for_detail:
        try:
            df_api = _fetch_history_goapi(ticker_jk)
            if df_api is not None and not df_api.empty:
                return df_api
        except NotImplementedError:
            pass
        except Exception:
            pass
    try:
        df = yf.Ticker(ticker_jk).history(period="1y", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        return df
    except Exception:
        return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_stock_fundamentals(ticker_jk):
    """Info fundamental dasar 1 saham. Prioritas GOAPI kalau sudah
    dikonfigurasi, fallback ke yfinance .info (untuk saham IDX sering
    tidak lengkap — field kosong ditandai None, ditampilkan sebagai
    'Data tidak tersedia' di halaman detail)."""
    fields = {
        "nama": None, "sektor": None, "industri": None,
        "market_cap": None, "per": None, "eps": None, "mata_uang": None,
    }
    if _goapi_configured_for_detail:
        try:
            data = _fetch_fundamentals_goapi(ticker_jk)
            if data:
                fields.update(data)
                return fields
        except NotImplementedError:
            pass
        except Exception:
            pass
    try:
        info = yf.Ticker(ticker_jk).info or {}
        fields["nama"] = info.get("longName") or info.get("shortName")
        fields["sektor"] = info.get("sector")
        fields["industri"] = info.get("industry")
        fields["market_cap"] = info.get("marketCap")
        fields["per"] = info.get("trailingPE")
        fields["eps"] = info.get("trailingEps")
        fields["mata_uang"] = info.get("currency")
    except Exception:
        pass
    return fields


def _fmt_metric(value, decimals=None):
    """Format angka buat ditampilkan, atau 'Data tidak tersedia' kalau kosong."""
    if value is None or value == "":
        return "Data tidak tersedia"
    if isinstance(value, (int, float)):
        if decimals is not None:
            return f"{value:,.{decimals}f}"
        return f"{value:,}"
    return str(value)


def render_stock_detail_page(ticker_raw):
    """Halaman khusus 1 saham: histori 1 tahun, fundamental, ringkasan
    sinyal dari hasil scan terakhir, dan berita — dibuka lewat query
    param ?stock=KODE saat nama saham diklik di tabel."""
    ticker_no_jk = str(ticker_raw).upper().strip().replace(".JK", "")
    ticker_jk = f"{ticker_no_jk}.JK"

    if st.button("⬅ Kembali ke Dashboard"):
        st.query_params.clear()
        st.rerun()

    df_scan = st.session_state.get("scan_df")
    scan_row = None
    if df_scan is not None and not df_scan.empty:
        match = df_scan[df_scan["stock"] == ticker_jk]
        if not match.empty:
            scan_row = match.iloc[0]

    sub_bits = []
    if scan_row is not None:
        sub_bits.append(f"Harga terakhir: {scan_row['price']}")
    sub_line = " • ".join(sub_bits) if sub_bits else "Belum ada di hasil scan terakhir"
    badge_html = signal_badge(scan_row["signal"]) if scan_row is not None else ""
    st.markdown(
        f"""
        <div class="terminal-header">
            <div>
                <div class="terminal-title">📈 {ticker_no_jk}</div>
                <div class="terminal-sub">{sub_line}</div>
            </div>
            <div>{badge_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---- Histori harga 1 tahun ----
    st.subheader("📊 Histori Harga (1 Tahun)")
    chart_type = st.radio(
        "Tampilan grafik", ["Candlestick", "Line"], horizontal=True, key="detail_chart_type"
    )
    with st.spinner("Mengambil data histori..."):
        hist = get_stock_history_1y(ticker_jk)

    if hist is None or hist.empty:
        st.info("Data tidak tersedia untuk saham ini.")
    else:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.75, 0.25], vertical_spacing=0.03,
        )
        if chart_type == "Candlestick":
            fig.add_trace(
                go.Candlestick(
                    x=hist["Date"], open=hist["Open"], high=hist["High"],
                    low=hist["Low"], close=hist["Close"], name="Harga",
                    increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
                ),
                row=1, col=1,
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=hist["Date"], y=hist["Close"], mode="lines", name="Close",
                    line=dict(color="#ffb35a", width=2),
                ),
                row=1, col=1,
            )
        fig.add_trace(
            go.Bar(x=hist["Date"], y=hist["Volume"], name="Volume", marker_color="#5865f2"),
            row=2, col=1,
        )
        fig.update_layout(
            height=520, showlegend=False,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#f3f2ef"),
            xaxis_rangeslider_visible=False,
        )
        fig.update_xaxes(gridcolor="rgba(255,255,255,0.06)")
        fig.update_yaxes(gridcolor="rgba(255,255,255,0.06)")
        st.plotly_chart(fig, use_container_width=True)

    # ---- Fundamental ----
    st.subheader("🏢 Info Fundamental")
    with st.spinner("Mengambil info fundamental..."):
        fundamentals = get_stock_fundamentals(ticker_jk)

    fcols = st.columns(3)
    fcols[0].metric("Sektor", fundamentals["sektor"] or "Data tidak tersedia")
    fcols[1].metric("Industri", fundamentals["industri"] or "Data tidak tersedia")
    fcols[2].metric("Market Cap", _fmt_metric(fundamentals["market_cap"]))
    fcols2 = st.columns(3)
    fcols2[0].metric("PER", _fmt_metric(fundamentals["per"], decimals=2))
    fcols2[1].metric("EPS", _fmt_metric(fundamentals["eps"], decimals=2))
    fcols2[2].metric("Mata Uang", fundamentals["mata_uang"] or "Data tidak tersedia")

    if not _goapi_configured_for_detail:
        st.caption(
            "📡 Sumber data saat ini: Yahoo Finance (gratis). Data fundamental "
            "saham IDX di Yahoo Finance sering tidak lengkap — begitu kamu "
            "langganan GOAPI dan isi GOAPI_API_KEY di Secrets (lalu isi logic "
            "fetch-nya), semua field di atas otomatis terbuka lengkap."
        )

    # ---- Ringkasan sinyal dari hasil scan terakhir ----
    st.subheader("🧭 Ringkasan Sinyal (Hasil Scan Terakhir)")
    if scan_row is None:
        st.info(
            "Saham ini belum ada di hasil scan terakhir. Klik 🔍 SCAN MARKET "
            "di dashboard dulu untuk data sinyal terbaru."
        )
    else:
        scols = st.columns(4)
        scols[0].metric("Entry", scan_row["entry"])
        scols[1].metric("TP", scan_row["tp"])
        scols[2].metric("SL", scan_row["sl"])
        scols[3].metric("Score", scan_row["score"])
        st.markdown(
            f'<div class="card">Trend: <b>{scan_row["trend"]}</b> &nbsp;•&nbsp; '
            f'Bandar: <b>{scan_row["bandar"]}</b> &nbsp;•&nbsp; '
            f'Swing Drop: <b>{"Ya" if scan_row["swing"] else "Tidak"} '
            f'({scan_row["drop"]}%)</b> &nbsp;•&nbsp; '
            f'Fake Breakout: <b>{"Ya" if scan_row["fake_breakout"] else "Tidak"}</b></div>',
            unsafe_allow_html=True,
        )

    # ---- Berita khusus saham ini — ditaruh paling bawah ----
    st.subheader("📰 Berita Terbaru")
    with st.spinner("Mengambil berita terbaru..."):
        news_items_raw = fetch_stock_news(ticker_jk, f"{ticker_no_jk} saham", max_items=8)

    if not news_items_raw:
        st.info(
            "Belum ada berita terbaru untuk saham ini, atau Google News "
            "sedang tidak bisa diakses."
        )
    else:
        for article in news_items_raw:
            combined_text = f"{article['title']} {article['description']}"
            sentiment, emoji = analyze_sentiment(combined_text)
            with st.container(border=True):
                st.markdown(f"**{article['title']}**")
                cols = st.columns([1, 2])
                cols[0].markdown(f"{emoji} {sentiment}")
                meta = " · ".join(x for x in [article["source"], article["pub_date"]] if x)
                if meta:
                    cols[1].caption(meta)
                if article["description"]:
                    st.caption(
                        article["description"][:220]
                        + ("..." if len(article["description"]) > 220 else "")
                    )
                if article["link"]:
                    st.markdown(f"[Baca selengkapnya]({article['link']})")


# Kalau URL punya query param ?stock=KODE (dari klik nama saham di tabel),
# tampilkan HALAMAN DETAIL SAHAM saja, dan hentikan render dashboard biasa
# (Top Gainers/Losers, tombol aksi, panel scan) di bawahnya.
_stock_param = st.query_params.get("stock")
if _stock_param:
    render_stock_detail_page(_stock_param)
    st.stop()


# ============================================================
#  TOP GAINERS / TOP LOSERS — FRAGMENT TERPISAH
# ============================================================
# PERBAIKAN UTAMA (biar gak "burem"/nunggu lama): panel ini dibungkus
# st.fragment(run_every=...). Streamlit akan me-refresh KHUSUS fragment ini
# sendirian di background sesuai jadwal (15 menit), tanpa rerun/freeze
# seluruh halaman. Hasil di bagian bawah (Scan Market, dst) sama sekali
# tidak kesentuh saat fragment ini refresh — tetap kelihatan & bisa
# dipakai seperti biasa. Ditambah get_all_data() yang sekarang batch +
# paralel, refresh jadi berlangsung dalam hitungan detik, bukan menit.
@st.fragment(run_every=refresh_seconds if refresh_seconds > 0 else None)
def render_top_panel():
    need_refresh = (
        manual_refresh_clicked
        or st.session_state.scan_df is None
        or (
            refresh_seconds > 0
            and st.session_state.last_updated_epoch is not None
            and (time.time() - st.session_state.last_updated_epoch) >= refresh_seconds
        )
    )
    if need_refresh:
        with st.spinner("Memperbarui Top Gainer/Loser..."):
            st.session_state.scan_df = build_full_scan()
            st.session_state.last_updated = datetime.now()
            st.session_state.last_updated_epoch = time.time()

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

    # ---- Countdown mundur ke auto-refresh berikutnya. Dijalankan murni di
    # sisi browser (JS setInterval) supaya tidak perlu rerun Streamlit tiap
    # detik hanya buat update angka countdown. ----
    if refresh_seconds > 0 and st.session_state.last_updated_epoch:
        _target_ms = int((st.session_state.last_updated_epoch + refresh_seconds) * 1000)
        components.html(
            f"""
            <div style="font-family: inherit; font-size:13px; color:#a9a7c4;
                         margin: -10px 0 16px 4px;">
                ⏳ Refresh Top Gainer/Loser berikutnya dalam
                <b id="cd-timer" style="color:#f3f2ef;">--:--</b>
            </div>
            <script>
            (function() {{
                const target = {_target_ms};
                const el = document.getElementById('cd-timer');
                function tick() {{
                    const now = Date.now();
                    let diff = Math.max(0, Math.floor((target - now) / 1000));
                    const m = String(Math.floor(diff / 60)).padStart(2, '0');
                    const s = String(diff % 60).padStart(2, '0');
                    if (el) {{ el.textContent = m + ':' + s; }}
                }}
                tick();
                setInterval(tick, 1000);
            }})();
            </script>
            """,
            height=30,
        )

    df_scan_preview = st.session_state.scan_df
    if df_scan_preview is not None and not df_scan_preview.empty:
        st.markdown("#### 📈 Top Gainer / 📉 Top Loser (dalam watchlist)")
        ranked = df_scan_preview.sort_values("change_pct", ascending=False)
        # PERBAIKAN: tampung sampai 50 saham per panel (bukan cuma 3), tapi
        # dibungkus container scroll biar tinggi UI tetap cuma ~5 baris kelihatan.
        gainers = ranked.head(50)
        losers = ranked.tail(50).sort_values("change_pct")

        def _render_scroll_list(rows):
            items_html = "".join(
                f'<div class="scroll-item">{_display_ticker(r["stock"])} — {r["price"]} '
                f'<span class="{"gain-up" if r["change_pct"] >= 0 else "gain-down"}">'
                f'({r["change_pct"]:+.2f}%)</span></div>'
                for _, r in rows.iterrows()
            )
            return f'<div class="scroll-list">{items_html}</div>'

        gcol, lcol = st.columns(2)
        with gcol:
            st.markdown("**Top Gainers**")
            st.markdown(_render_scroll_list(gainers), unsafe_allow_html=True)
        with lcol:
            st.markdown("**Top Losers**")
            st.markdown(_render_scroll_list(losers), unsafe_allow_html=True)


render_top_panel()

st.divider()


# ============================================================
#  ACTION BUTTONS ROW — tiap tombol = satu command
# ============================================================
# PERBAIKAN: panel yang lagi ditampilkan (Scan/Bandar/Breakout/dst) disimpan
# di session_state supaya TIDAK hilang saat halaman rerun karena tick
# auto-refresh 15 menit (yang cuma seharusnya memperbarui panel Top
# Gainer/Loser di atas). Data yang ditampilkan tetap dari cache manual —
# baru berubah kalau tombolnya diklik ulang, "Refresh Now" ditekan, atau
# auto-refresh baru saja jalan (karena berbagi cache yang sama).
if "active_panel" not in st.session_state:
    st.session_state.active_panel = None

c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
with c1:
    if st.button("🔍 SCAN MARKET"):
        st.session_state.active_panel = "scan"
with c2:
    if st.button("🐋 BANDAR DETECTOR"):
        st.session_state.active_panel = "bandar"
with c3:
    if st.button("🚀 BREAKOUT SCANNER"):
        st.session_state.active_panel = "breakout"
with c4:
    if st.button("📉 SWING ALERT"):
        st.session_state.active_panel = "swing"
with c5:
    if st.button("⚠️ FAKE BREAKOUT"):
        st.session_state.active_panel = "fake"
with c6:
    btn_telegram = st.button("📲 SEND BEST TO TG")
with c7:
    if st.button("📰 NEWS"):
        st.session_state.active_panel = "news"
with c8:
    if st.button("🏦 BROKER SUMMARY"):
        st.session_state.active_panel = "broker"

# ---- SCAN MARKET : tabel penuh, semua saham, semua metrik ----
if st.session_state.active_panel == "scan":
    df_scan = ensure_scanned()
    st.subheader("📊 Hasil Scan Penuh")
    df_display = df_scan.copy()
    df_display["stock"] = df_display["stock"].apply(_display_ticker)
    render_html_table(df_display)
    if not df_scan.empty:
        best = df_scan.iloc[0]
        st.markdown(
            f'<div class="card"><b>BEST PICK:</b> {_display_ticker(best["stock"])} — '
            f'{signal_badge(best["signal"])} @ {best["price"]}</div>',
            unsafe_allow_html=True,
        )

# ---- BANDAR DETECTOR : hanya saham dengan aktivitas bandar non-netral ----
if st.session_state.active_panel == "bandar":
    df_scan = ensure_scanned()
    st.subheader("🐋 Deteksi Aktivitas Bandar")
    bandar_hits = df_scan[df_scan["bandar"] != "NETRAL"]
    if bandar_hits.empty:
        st.info("Tidak ada aktivitas bandar signifikan hari ini.")
    else:
        for _, r in bandar_hits.iterrows():
            st.markdown(
                f'<div class="card"><b>{_display_ticker(r["stock"])}</b> — {r["bandar"]} '
                f'(price: {r["price"]}) {signal_badge(r["signal"])}</div>',
                unsafe_allow_html=True,
            )

# ---- BREAKOUT SCANNER : saham dengan breakout valid ----
if st.session_state.active_panel == "breakout":
    df_scan = ensure_scanned()
    st.subheader("🚀 Saham Breakout Valid")
    breakouts = df_scan[df_scan["score"] >= 50]
    if breakouts.empty:
        st.info("Belum ada breakout kuat terdeteksi.")
    else:
        df_display = breakouts[["stock", "price", "score", "signal", "entry", "tp", "sl"]].copy()
        df_display["stock"] = df_display["stock"].apply(_display_ticker)
        render_html_table(df_display)

# ---- SWING ALERT : saham yang drop >25% dari swing high ----
if st.session_state.active_panel == "swing":
    df_scan = ensure_scanned()
    st.subheader("📉 Swing Drop Alert (>25% dari high)")
    swing_hits = df_scan[df_scan["swing"] == True]
    if swing_hits.empty:
        st.info("Tidak ada saham dengan swing drop signifikan.")
    else:
        df_display = swing_hits[["stock", "price", "drop", "trend", "signal"]].copy()
        df_display["stock"] = df_display["stock"].apply(_display_ticker)
        render_html_table(df_display)

# ---- FAKE BREAKOUT : breakout yang baru saja gagal ----
if st.session_state.active_panel == "fake":
    df_scan = ensure_scanned()
    st.subheader("⚠️ Fake Breakout Warning")
    fake_hits = df_scan[df_scan["fake_breakout"] == True]
    if fake_hits.empty:
        st.info("Tidak ada fake breakout terdeteksi saat ini.")
    else:
        df_display = fake_hits[["stock", "price", "trend", "signal"]].copy()
        df_display["stock"] = df_display["stock"].apply(_display_ticker)
        render_html_table(df_display)

# ---- SEND BEST TO TELEGRAM ----
if btn_telegram:
    df_scan = ensure_scanned()
    if df_scan.empty:
        st.warning("Belum ada data untuk dikirim.")
    else:
        best = df_scan.iloc[0]
        msg = (
            f"BEST PICK: {_display_ticker(best['stock'])}\n"
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
# selalu relevan dengan watchlist ~600 saham ISSI, bukan cuma 3 saham tetap.
if st.session_state.active_panel == "news":
    df_scan = ensure_scanned()
    st.subheader("📰 Berita Saham Terkini")
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

# ---- BROKER SUMMARY : kode broker + volume beli/jual per saham,
# khusus data EOD (update setelah bursa tutup, via GOAPI.IO) ----
if st.session_state.active_panel == "broker":
    st.subheader("🏦 Broker Summary (Data EOD)")

    _goapi_configured = bool(GOAPI_API_KEY) and GOAPI_API_KEY != "ISI_API_KEY_GOAPI_LO"

    if not _goapi_configured:
        # ---- LOCKED / COMING SOON ----
        # Fitur ini butuh langganan berbayar ke GOAPI.IO (Rp 550rb/bulan,
        # bukan sekali bayar). Ditahan dulu sampai ada user berlangganan
        # Pro di app ini, biar biayanya ke-cover — bukan nombok duluan.
        # Begitu GOAPI_API_KEY diisi di Secrets, panel ini otomatis
        # berubah jadi fungsional tanpa perlu ubah kode apa pun lagi.
        st.markdown(
            """
            <div class="card" style="text-align:center; padding: 40px 24px;">
                <div style="font-size:42px; margin-bottom:8px;">🔒</div>
                <div style="font-size:20px; font-weight:800; color:#ffffff; margin-bottom:8px;">
                    Broker Summary — Segera Hadir
                </div>
                <div style="font-size:14px; color:#a9a7c4; max-width:520px; margin:0 auto 18px auto; line-height:1.6;">
                    Fitur ini akan menampilkan kode broker, top broker pembeli & penjual,
                    serta net akumulasi/distribusi untuk tiap saham (data resmi EOD, update
                    setelah bursa tutup). Sedang dalam proses aktivasi langganan data —
                    akan dibuka begitu tersedia.
                </div>
                <span class="badge badge-wait">🔒 PREMIUM · SEGERA HADIR</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.info(
            "Sementara menunggu, coba fitur **🐋 BANDAR DETECTOR** — deteksi indikasi "
            "aktivitas bandar (akumulasi/markup/distribusi) berbasis pola volume & harga, "
            "sudah aktif dan gratis."
        )
    else:
        _render_broker_summary_panel()

if st.session_state.active_panel is None and not btn_telegram:
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
