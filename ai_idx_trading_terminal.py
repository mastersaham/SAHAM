import streamlit as st
import json
import os
import re
import html
import hashlib
import secrets as secrets_lib
import requests
import time
import gc
import numpy as np
import xml.etree.ElementTree as ET
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from datetime import time as dtime_cls
from streamlit_cookies_manager import EncryptedCookieManager
from supabase import create_client

# Fitur Community Feed (post, reaction, laporan spam) + notifikasi bell.
# File-file ini harus ada satu folder sama file utama ini:
#   community_feed.py, notifications.py
from community_feed import render_community_feed
from notifications import render_notification_bell

st.set_page_config(
    page_title="RITEL SYARIAH",
    page_icon="🚀",
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
    "dreamwarriorstudio",
]

# File tempat menyimpan status langganan agar TIDAK hilang setiap kali
# Streamlit menjalankan ulang script (yang terjadi di HAMPIR setiap interaksi).
# CATATAN: ini sekarang FALLBACK doang (dipakai kalau secrets Supabase belum
# ke-set, misal pas develop lokal). Sumber data utama sekarang Supabase —
# lihat load_user_db()/save_user_db() di bawah.
USER_DB_FILE = "user_db.json"

# ============================================================
#  SUPABASE — penyimpanan data user (ganti dari user_db.json lokal).
#  Isi SUPABASE_URL & SUPABASE_SERVICE_KEY di .streamlit/secrets.toml
#  (lokal) atau Settings -> Secrets (Streamlit Cloud):
#
#     SUPABASE_URL = "https://xxxxxxxx.supabase.co"
#     SUPABASE_SERVICE_KEY = "eyJhbGciOi..."   # service_role key, BUKAN anon key
#
#  PENTING: pakai service_role key (bukan anon/public key), karena app ini
#  jalan di server Streamlit (bukan di browser user), dan perlu bypass RLS
#  buat baca/tulis semua baris user. service_role key JANGAN PERNAH dipasang
#  di kode frontend/browser — tapi di sini aman karena cuma dibaca server-side
#  lewat st.secrets.
# ============================================================
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = st.secrets.get("SUPABASE_SERVICE_KEY", "")


@st.cache_resource
def get_supabase_client():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception:
        return None

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
    client = get_supabase_client()
    if client is None:
        # Fallback: secrets Supabase belum ke-set (misal develop lokal tanpa
        # secrets.toml). Tetap jalan pakai file JSON lama biar gak ngeblok kerja.
        if os.path.exists(USER_DB_FILE):
            try:
                with open(USER_DB_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    try:
        resp = client.table("users").select("*").execute()
    except Exception as e:
        st.error(f"Gagal memuat data user dari Supabase: {e}")
        return {}

    db = {}
    for row in resp.data:
        uname = row.pop("username", None)
        if not uname:
            continue
        db[f"user:{uname}"] = row
    return db


def save_user_db(db):
    client = get_supabase_client()
    if client is None:
        # Fallback: sama seperti di load_user_db(), simpan ke JSON lokal.
        with open(USER_DB_FILE, "w") as f:
            json.dump(db, f)
        return

    rows = []
    for key, data in db.items():
        if not key.startswith("user:"):
            continue
        row = dict(data)
        row["username"] = key[len("user:"):]
        rows.append(row)

    if not rows:
        return

    try:
        client.table("users").upsert(rows, on_conflict="username").execute()
    except Exception as e:
        st.error(f"Gagal menyimpan data user ke Supabase: {e}")


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


# ============================================================
#  RESET PASSWORD — token acak + kirim email via Brevo
#  (Hanya untuk pelanggan aktif; verifikasi username + email cocok
#  dengan data terdaftar sebelum token dibuat & email dikirim.)
# ============================================================
RESET_TOKEN_VALID_HOURS = 1


def generate_reset_token(user_db, key):
    """Bikin token reset acak (256-bit, aman dipakai di URL), simpan ke
    user_db dengan batas waktu berlaku. Dipanggil HANYA setelah username,
    email, dan status langganan aktif sudah diverifikasi di pemanggilnya."""
    token = secrets_lib.token_urlsafe(32)
    expiry = (datetime.now() + timedelta(hours=RESET_TOKEN_VALID_HOURS)).isoformat()
    user_db[key]["reset_token"] = token
    user_db[key]["reset_token_expiry"] = expiry
    save_user_db(user_db)
    return token


def clear_reset_token(user_db, key):
    """Hapus token reset setelah dipakai (atau kalau mau dibatalkan),
    supaya link lama tidak bisa dipakai ulang."""
    if key in user_db:
        user_db[key].pop("reset_token", None)
        user_db[key].pop("reset_token_expiry", None)
        save_user_db(user_db)


def is_reset_token_valid(record, token):
    """Cek token yang datang dari URL cocok dengan yang tersimpan DAN
    belum kedaluwarsa. Pakai compare_digest supaya tahan timing attack."""
    if not record or not token:
        return False
    stored_token = record.get("reset_token")
    expiry_str = record.get("reset_token_expiry")
    if not stored_token or not expiry_str:
        return False
    if not secrets_lib.compare_digest(stored_token, token):
        return False
    try:
        expiry = datetime.fromisoformat(expiry_str)
    except ValueError:
        return False
    return datetime.now() <= expiry


def send_reset_password_email(to_email, to_username, reset_link):
    """Kirim email reset password lewat Brevo transactional email API.
    Butuh secrets: BREVO_API_KEY, BREVO_SENDER_EMAIL (email pengirim yang
    sudah diverifikasi di dashboard Brevo). BREVO_SENDER_NAME opsional.
    Return (True, None) kalau sukses, (False, pesan_error) kalau gagal —
    TIDAK PERNAH melempar exception ke pemanggil.
    """
    api_key = st.secrets.get("BREVO_API_KEY", "")
    sender_email = st.secrets.get("BREVO_SENDER_EMAIL", "")
    sender_name = st.secrets.get("BREVO_SENDER_NAME", "RITEL SYARIAH")

    if not api_key or not sender_email:
        return False, "BREVO_API_KEY / BREVO_SENDER_EMAIL belum diisi di Streamlit Secrets."

    safe_username = html.escape(to_username)
    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": to_email, "name": to_username}],
        "subject": "Reset Password — RITEL SYARIAH",
        "htmlContent": (
            '<div style="font-family:sans-serif;font-size:15px;color:#222;">'
            f"<p>Halo <b>{safe_username}</b>,</p>"
            "<p>Kami menerima permintaan reset password untuk akun kamu.</p>"
            "<p>"
            f'<a href="{html.escape(reset_link)}" '
            'style="background:#ffb35a;color:#1a1a1a;padding:10px 18px;'
            "border-radius:6px;text-decoration:none;font-weight:700;"
            'display:inline-block;">Buat Password Baru</a>'
            "</p>"
            f"<p>Link ini berlaku selama {RESET_TOKEN_VALID_HOURS} jam. Kalau kamu "
            "tidak meminta reset password ini, abaikan saja email ini — "
            "akun kamu tetap aman.</p>"
            "</div>"
        ),
    }
    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return True, None
        return False, f"Brevo API error {resp.status_code}: {resp.text[:200]}"
    except requests.RequestException as e:
        return False, f"Gagal menghubungi layanan email: {e}"


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


@st.cache_data(ttl=900, show_spinner=False)  # PERBAIKAN: 55d -> 15m, samain cadence & shared ke semua user
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

    Dipanggil dengan daftar dinamis: gabungan saham portofolio user (yang
    syariah) + top saham hasil scan. Lihat render_news_section() di bawah,
    tab "📈 Saham".
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


# Kata kunci untuk tab "🌐 Umum" -- berita ekonomi/kebijakan Indonesia yang
# berpotensi menggerakkan IHSG secara luas (bukan spesifik satu saham).
# Pidato/kebijakan Presiden Prabowo dipantau khusus karena sering jadi
# pemicu sentimen pasar.
GENERAL_NEWS_QUERIES = [
    "IHSG",
    "Prabowo ekonomi",
    "Prabowo pidato",
    "kebijakan ekonomi Indonesia",
    "Bank Indonesia suku bunga",
    "rupiah",
]


@st.cache_data(ttl=900, show_spinner=False)  # PERBAIKAN: 55d -> 15m, samain cadence & shared ke semua user
def get_general_market_news(max_items_per_query=5):
    """Ambil berita ekonomi/IHSG umum (tidak terikat saham tertentu) dari
    beberapa kata kunci di GENERAL_NEWS_QUERIES, gabungkan, buang duplikat
    (link sama muncul dari >1 kata kunci), urutkan dari yang paling baru."""
    all_news = []
    seen_links = set()
    for query in GENERAL_NEWS_QUERIES:
        articles = fetch_stock_news(query, query, max_items_per_query)
        for article in articles:
            link = article.get("link", "")
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)
            combined_text = f"{article['title']} {article['description']}"
            sentiment, emoji = analyze_sentiment(combined_text)
            all_news.append({
                **article,
                "matched_stocks": [],
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
        overflow-y: auto;
        max-height: 460px;
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
        position: sticky;
        top: 0;
        z-index: 1;
        text-transform: uppercase;
        text-align: center;
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.6px;
        color: #ffb35a;
        background: #0f1020;
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
    .stock-link,
    [data-testid="stMarkdownContainer"] a.stock-link,
    [data-testid="stMarkdownContainer"] a.stock-link:link,
    [data-testid="stMarkdownContainer"] a.stock-link:visited {
        color: #ff8a1f;
        font-weight: 700;
        text-decoration: none !important;
        cursor: pointer;
    }
    [data-testid="stMarkdownContainer"] a.stock-link:hover {
        color: #ffab5c;
        text-decoration: none !important;
    }

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
    .orange-topbar-title {
        font-size: 30px;
        font-weight: 800;
        color: #ffd400;
        margin-top: 10px;
        letter-spacing: 0.3px;
        line-height: 1.25;
        display: inline-flex;
        align-items: center;
        gap: 8px;
    }
    .orange-topbar-rocket {
        fill: #ffd400;
        flex-shrink: 0;
    }
    .orange-topbar-sub {
        font-size: 14px;
        color: #a9a7c4;
        margin-top: 8px;
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
    .stock-fund-inline {
        font-size: 15px;
        font-weight: 500;
        color: #d9d7ec;
        margin-left: 10px;
        vertical-align: middle;
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
    .st-key-header_status_bar {
        position: sticky;
        top: 0;
        z-index: 999;
        background: linear-gradient(135deg, #ffe27a, #ffc700);
        backdrop-filter: blur(6px);
        padding: 10px 4px 6px 4px;
        margin: -10px -4px 10px -4px;
        border-bottom: 1px solid rgba(120,90,0,0.25);
        border-radius: 0 0 18px 18px;
    }
    /* Tombol home di dalam card kuning header -- dibikin transparan +
       beda gaya dari tombol aksi oranye lain, biar nyatu sama warna card. */
    .st-key-header_status_bar div.stButton > button {
        background: rgba(255,255,255,0.35);
        color: #3a2900;
        box-shadow: none;
    }
    .st-key-header_status_bar div.stButton > button:hover {
        background: rgba(255,255,255,0.55);
        transform: translateY(-1px);
    }

    .badge-buy { background: rgba(0,224,140,0.15); color: #00e08c; }
    .badge-hold { background: rgba(255,152,0,0.15); color: #ff9800; }
    .badge-neutral { background: rgba(255,193,7,0.15); color: #ffc107; }
    .badge-wait { background: rgba(255,193,7,0.15); color: #ffc107; }
    .badge-sell { background: rgba(255,82,82,0.15); color: #ff5252; }
    .badge-nonsyariah { background: rgba(255,82,82,0.15); color: #ff5252; }
    .gain-up { color: #00e08c; font-weight: 700; }
    .gain-down { color: #ff5252; font-weight: 700; }
    .portfolio-table-wrap { overflow-x: auto; }
    .portfolio-table { width: 100%; border-collapse: collapse; font-size: 14px; }
    .portfolio-table th {
        text-align: center;
        padding: 10px 12px;
        color: #ffb35a;
        border-bottom: 1px solid rgba(255,179,90,0.28);
        white-space: nowrap;
    }
    .portfolio-table td {
        text-align: center;
        padding: 9px 12px;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        color: #f3f2ef;
    }
    .broker-chip {
        display: inline-block;
        padding: 2px 8px;
        margin: 2px 3px 2px 0;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 700;
    }
    .broker-foreign { background: rgba(34,211,238,0.15); color: #22d3ee; }
    .broker-domestic { background: rgba(167,139,250,0.15); color: #a78bfa; }
    .broker-other { background: rgba(156,163,175,0.15); color: #9ca3af; }

    .hist-table-wrap {
        max-height: 320px;
        overflow-y: auto;
        overflow-x: auto;
        border: 1px solid rgba(255,179,90,0.15);
        border-radius: 8px;
    }
    .hist-table { width: 100%; border-collapse: collapse; font-size: 13px; }
    .hist-table th {
        position: sticky; top: 0;
        background: #14152a;
        text-align: center;
        padding: 8px 10px;
        color: #ffb35a;
        border-bottom: 1px solid rgba(255,179,90,0.28);
        white-space: nowrap;
        z-index: 1;
    }
    .hist-table td {
        text-align: center;
        padding: 7px 10px;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        color: #f3f2ef;
        white-space: nowrap;
    }
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
    <div class="orange-topbar-title">
        <svg class="orange-topbar-rocket" viewBox="0 0 24 24" width="28" height="28" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 2C9 5 7 9 7 13c0 1.5.5 3 1.5 4L7 21l3-1.5L12 21l2-1.5L17 21l-1.5-4c1-1 1.5-2.5 1.5-4 0-4-2-8-5-11z"/>
            <circle cx="12" cy="11" r="1.4" fill="#0b0c16"/>
        </svg>
        RITEL SYARIAH
    </div>
    <div class="orange-topbar-sub">Khusus saham syariah, semoga berkah</div>
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


# ============================================================
#  LANGGANAN — durasi paket, aktivasi/perpanjangan, & info tampilan
# ============================================================
# Durasi per paket (hari). Sesuaikan di sini kalau nanti ada paket baru
# atau durasinya berubah -- semua tempat lain (webhook Stripe, panel
# aktivasi manual Owner, popover status) otomatis ikut.
PLAN_DURATIONS = {
    "bulanan": 30,
    "3_bulanan": 90,
    "tahunan": 365,
}
PLAN_LABELS = {
    "bulanan": "Bulanan",
    "3_bulanan": "3 Bulanan",
    "tahunan": "Tahunan",
}

_BULAN_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def format_tanggal_id(dt):
    """Format datetime jadi '12 Jul 2026' (tanpa perlu setting locale OS)."""
    return f"{dt.day} {_BULAN_ID[dt.month - 1]} {dt.year}"


def activate_subscription(user_db, identifier, plan):
    """Aktifkan/perpanjang langganan user (dipanggil dari webhook Stripe
    ATAU dari panel aktivasi manual Owner). Sesuai keputusan: setiap kali
    ini dipanggil (= setiap kali user bayar), status jadi aktif, tanggal
    MULAI = saat ini juga (waktu pembayaran/aktivasi terjadi), dan tanggal
    BERAKHIR = mulai + durasi paket yang dipilih. Tidak ada logika 'sisa
    hari lama ditambahkan' -- simpel sesuai permintaan awal, gampang
    diubah nanti kalau perlu."""
    if plan not in PLAN_DURATIONS:
        plan = "bulanan"
    now = datetime.now()
    record = user_db.setdefault(identifier, {})
    record["status"] = "active"
    record["plan"] = plan
    record["subscribed_at"] = now.isoformat()
    record["expires_at"] = (now + timedelta(days=PLAN_DURATIONS[plan])).isoformat()
    save_user_db(user_db)
    return record


def get_subscription_info(identifier, user_db):
    """Return dict {plan, subscribed_at, expires_at, days_left} kalau user
    ini punya data langganan (pernah diaktifkan), None kalau belum pernah
    (misal owner, atau akun baru daftar yang belum pernah bayar)."""
    record = user_db.get(identifier, {})
    expires_at = record.get("expires_at")
    if not expires_at:
        return None
    try:
        expires_dt = datetime.fromisoformat(expires_at)
    except (ValueError, TypeError):
        return None
    days_left = (expires_dt.date() - datetime.now().date()).days
    return {
        "plan": record.get("plan"),
        "subscribed_at": record.get("subscribed_at"),
        "expires_at": expires_at,
        "days_left": days_left,
    }


# ============================================================
#  PORTOFOLIO USER — daftar saham personal, tersimpan permanen di user_db
# ============================================================
def get_user_portfolio(user_db, identifier):
    """Daftar kode saham (tanpa '.JK') yang sudah ditambahkan user, urut
    sesuai waktu ditambahkan."""
    return user_db.get(identifier, {}).get("portfolio", [])


def add_to_portfolio(user_db, identifier, code):
    """Tambah 1 kode saham ke portofolio user. Return (True, None) kalau
    berhasil, (False, pesan_error) kalau gagal (kode kosong, sudah ada,
    atau tickernya tidak valid/tidak ada datanya di bursa)."""
    code = code.strip().upper().replace(".JK", "")
    if not code:
        return False, "Kode saham tidak boleh kosong."
    if identifier not in user_db:
        return False, "Akun tidak ditemukan."

    portfolio = user_db[identifier].setdefault("portfolio", [])
    if code in portfolio:
        return False, f"{code} sudah ada di portofolio kamu."

    # Validasi: kodenya beneran ada datanya di bursa (baik ISSI maupun
    # bukan) sebelum diterima, supaya tidak nyimpen kode saham ngaco.
    ticker_jk = f"{code}.JK"
    is_syariah = ticker_jk in ISSI_STOCKS
    if not is_syariah:
        quote = get_quick_quote(ticker_jk)
        if quote is None:
            return False, f"Kode saham '{code}' tidak ditemukan / tidak ada datanya."

    portfolio.append(code)
    save_user_db(user_db)
    return True, None


def remove_from_portfolio(user_db, identifier, code):
    if identifier in user_db and "portfolio" in user_db[identifier]:
        user_db[identifier]["portfolio"] = [
            c for c in user_db[identifier]["portfolio"] if c != code
        ]
        save_user_db(user_db)


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
                'cukup username, email &amp; password.</div>',
                unsafe_allow_html=True,
            )

            tab_masuk, tab_daftar, tab_lupa = st.tabs(["Masuk", "Daftar Baru", "Lupa Password"])

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
                    new_email = st.text_input(
                        "Email", help="Dipakai untuk reset password kalau lupa."
                    )
                    col_np, col_np2 = st.columns(2)
                    with col_np:
                        new_password = st.text_input(
                            "Buat password", type="password", help="Minimal 6 karakter."
                        )
                    with col_np2:
                        new_password2 = st.text_input("Ulangi password", type="password")
                    submit_daftar = st.form_submit_button("Daftar Sekarang", use_container_width=True)

                if submit_daftar:
                    uname = new_username.strip().lower()
                    email_clean = new_email.strip().lower()
                    key = f"user:{uname}"
                    if not uname or not new_password or not email_clean:
                        st.error("Username, email, dan password wajib diisi.")
                    elif "@" not in email_clean or "." not in email_clean.split("@")[-1]:
                        st.error("Format email tidak valid.")
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
                            "email": email_clean,
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

            with tab_lupa:
                st.markdown(
                    '<div class="auth-caption">Masukkan username dan email yang '
                    'terdaftar. Kalau akun kamu pelanggan aktif, link reset '
                    'password akan dikirim ke email kamu.</div>',
                    unsafe_allow_html=True,
                )
                with st.form("form_lupa_password", clear_on_submit=False):
                    lupa_username = st.text_input("Username", key="lupa_username_input")
                    lupa_email = st.text_input("Email terdaftar", key="lupa_email_input")
                    submit_lupa = st.form_submit_button("Kirim Link Reset", use_container_width=True)

                if submit_lupa:
                    uname = lupa_username.strip().lower()
                    email_input = lupa_email.strip().lower()
                    key = f"user:{uname}"
                    record = user_db.get(key)
                    status_ok = get_user_status(key, user_db) in ("owner", "active")
                    email_ok = bool(
                        record and email_input
                        and record.get("email", "").strip().lower() == email_input
                    )

                    if not uname or not email_input:
                        st.error("Username dan email wajib diisi.")
                    else:
                        app_url = st.secrets.get("APP_URL", "")
                        if record and email_ok and status_ok and app_url:
                            token = generate_reset_token(user_db, key)
                            reset_link = f"{app_url}/?reset_token={token}&u={uname}"
                            sent, err = send_reset_password_email(record["email"], uname, reset_link)
                            if not sent and uname in OWNER_USERNAMES:
                                # Detail error cuma tampil ke akun Owner sendiri,
                                # buat gampang debug konfigurasi Brevo/APP_URL —
                                # tidak bocor ke user lain lewat pesan generik di bawah.
                                st.error(f"[Owner debug] Email gagal terkirim: {err}")
                        # Pesan SELALU sama persis apa pun hasilnya (username salah,
                        # email tidak cocok, belum berlangganan aktif, dll) — supaya
                        # orang tidak bisa menebak-nebak username/email pelanggan
                        # aktif dari respons form ini (mencegah user enumeration).
                        st.success(
                            "Jika akun terdaftar, email cocok, dan berlangganan aktif, "
                            "link reset password sudah dikirim. Cek juga folder "
                            "Spam/Promosi kalau belum masuk."
                        )


def render_reset_password_page(user_db, uname, token):
    """Halaman set password baru, diakses lewat link reset dari email.
    Tidak butuh login dulu — validitasnya murni dari token + masa berlaku."""
    key = f"user:{uname}"
    record = user_db.get(key)

    with st.container(key="auth_wrap"):
        st.markdown('<div class="auth-logo-badge">🔑</div>', unsafe_allow_html=True)
        with st.container(key="auth_panel"):
            st.markdown('<div class="auth-title">Buat Password Baru</div>', unsafe_allow_html=True)

            if not is_reset_token_valid(record, token):
                st.error(
                    "Link reset password tidak valid atau sudah kedaluwarsa "
                    f"(berlaku {RESET_TOKEN_VALID_HOURS} jam). Silakan minta link "
                    "baru lewat tab 'Lupa Password'."
                )
                return

            st.markdown(
                f'<div class="auth-caption">Untuk akun <b>{html.escape(uname)}</b>.</div>',
                unsafe_allow_html=True,
            )
            with st.form("form_reset_password", clear_on_submit=False):
                new_pwd = st.text_input("Password baru", type="password", help="Minimal 6 karakter.")
                new_pwd2 = st.text_input("Ulangi password baru", type="password")
                submit_reset = st.form_submit_button("Simpan Password Baru", use_container_width=True)

            if submit_reset:
                if not new_pwd:
                    st.error("Password wajib diisi.")
                elif len(new_pwd) < 6:
                    st.error("Password minimal 6 karakter.")
                elif new_pwd != new_pwd2:
                    st.error("Password dan ulangi password tidak sama.")
                else:
                    pwd_hash, salt = hash_password(new_pwd)
                    user_db[key]["password_hash"] = pwd_hash
                    user_db[key]["salt"] = salt
                    clear_reset_token(user_db, key)
                    save_user_db(user_db)
                    st.success(
                        "Password berhasil diganti! Silakan tutup halaman ini "
                        "lalu masuk lagi lewat tab 'Masuk' dengan password baru."
                    )


# ---- Kalau ini kunjungan dari link reset password di email, tampilkan
#      halaman ganti password dan berhenti di sini (tidak perlu login dulu).
_reset_token_param = st.query_params.get("reset_token")
_reset_uname_param = st.query_params.get("u")
if _reset_token_param and _reset_uname_param:
    render_reset_password_page(load_user_db(), _reset_uname_param.strip().lower(), _reset_token_param)
    st.stop()


# ---- Tentukan identitas user yang sedang login (kalau ada) ----
identifier = st.session_state.get("auth_identifier")
display_name = st.session_state.get("auth_display_name", identifier)

if not identifier and not st.session_state.pop("skip_cookie_restore", False):
    # Belum ada sesi di session_state (misal karena tab baru / reload) —
    # coba pulihkan otomatis dari cookie sebelum minta login ulang.
    #
    # CATATAN: flag "skip_cookie_restore" dicek di sini untuk menghindari
    # race condition saat logout. EncryptedCookieManager menghapus cookie
    # lewat komponen JS yang butuh 1 siklus komunikasi browser<->server;
    # kalau kita langsung st.rerun() setelah clear_login_cookie(), rerun
    # itu bisa kejadian SEBELUM cookie beneran terhapus di browser, jadi
    # tanpa flag ini user akan otomatis ke-login lagi dari cookie lama.
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

# Client Supabase dipakai bersama untuk Community Feed + Notifikasi.
# Kalau secrets SUPABASE_URL/SUPABASE_SERVICE_KEY belum diisi, ini akan
# None -- panel komunitas & bell notif otomatis nyembunyiin diri (lihat
# guard di bawah), jadi app utama tetap jalan normal tanpa error.
supabase_client = get_supabase_client()

def _go_to_dashboard():
    """Reset semua state navigasi & query param, balik lurus ke Dashboard
    utama -- dipakai tombol logo rumah (home) di header."""
    st.query_params.clear()
    st.session_state["show_portfolio"] = False
    st.session_state["show_customer_panel"] = False
    st.session_state["active_panel"] = None
    st.rerun()


with st.container(key="header_status_bar"):
    if status in ("owner", "active") and supabase_client:
        col_home, col_status, col_notif, col_portfolio = st.columns([0.5, 3.3, 0.6, 1.3])
    else:
        col_home, col_status, col_portfolio = st.columns([0.5, 3.7, 1.3])
        col_notif = None

    with col_home:
        if st.button("🏠", key="home_btn", use_container_width=True, help="Kembali ke Dashboard"):
            _go_to_dashboard()

    # ---- Nama profil = trigger dropdown (popover) berisi Privasi Akun
    # & Logout, biar gak makan tempat sebagai tombol terpisah-pisah di
    # header. Label tombolnya sendiri sudah nunjukkin status + nama.
    # Badge centang biru (gaya "verified" media sosial) ditaruh di KANAN
    # nama, buat owner & pelanggan aktif -- mahkota (owner) tetap di kiri. ----
    _verified_badge = ":blue[✔️]"
    if status == "owner":
        _profile_label = f"👑 {display_name} {_verified_badge}"
    elif status == "active":
        _profile_label = f"{display_name} {_verified_badge}"
    else:
        _profile_label = f"❌ {display_name}"

    # PERBAIKAN: st.popover bawaan Streamlit TIDAK otomatis nutup diri
    # sendiri kalau tombol DI DALAMNYA diklik lalu trigger st.rerun() --
    # popover-nya kebuka lagi setelah rerun karena widget key-nya sama
    # persis. Solusinya: kasih popover ini "key" yang berubah tiap kali
    # ada aksi di dalamnya (Kelola Pelanggan/Privasi Akun/Logout) --
    # Streamlit jadi menganggapnya widget baru (state awal = tertutup),
    # jadi begitu opsi diklik, popover otomatis ketutup sendiri.
    if "profile_popover_seed" not in st.session_state:
        st.session_state["profile_popover_seed"] = 0
    _popover_key = f"profile_popover_{st.session_state['profile_popover_seed']}"

    with col_status:
        with st.popover(_profile_label, use_container_width=True, key=_popover_key):
            if status == "owner":
                st.success("👑 Owner access granted")
                st.divider()
                if st.button("🗂️ Kelola Pelanggan", use_container_width=True, key="open_customer_panel_btn"):
                    st.session_state["show_customer_panel"] = True
                    st.session_state["profile_popover_seed"] += 1
                    st.rerun()
            elif status == "active":
                st.success("✅ Subscription aktif")
                _sub_info = get_subscription_info(identifier, user_db)
                if _sub_info and _sub_info.get("subscribed_at") and _sub_info.get("expires_at"):
                    try:
                        _mulai_dt = datetime.fromisoformat(_sub_info["subscribed_at"])
                        _akhir_dt = datetime.fromisoformat(_sub_info["expires_at"])
                        _plan_label = PLAN_LABELS.get(_sub_info["plan"], _sub_info["plan"] or "-")
                        st.caption(
                            f"Paket **{_plan_label}**  \n"
                            f"Mulai: {format_tanggal_id(_mulai_dt)}  \n"
                            f"Berakhir: {format_tanggal_id(_akhir_dt)} "
                            f"(sisa {_sub_info['days_left']} hari)"
                        )
                    except (ValueError, TypeError):
                        pass
            else:
                st.warning("❌ Belum berlangganan")
            st.divider()
            if st.button("🔒 Privasi Akun", use_container_width=True, key="open_privacy_btn"):
                st.session_state.active_panel = "privacy"
                st.session_state["profile_popover_seed"] += 1
                st.rerun()
            if st.button("🚪 Logout", use_container_width=True, key="logout_btn"):
                st.session_state["profile_popover_seed"] += 1
                st.session_state.pop("auth_identifier", None)
                st.session_state.pop("auth_display_name", None)
                clear_login_cookie()
                # Cegah cookie lama (yang mungkin belum sempat kehapus di
                # browser saat rerun ini terjadi) auto-login-in kita lagi.
                st.session_state["skip_cookie_restore"] = True
                # PERBAIKAN (bug "harus klik 2x"): EncryptedCookieManager
                # menghapus cookie lewat komponen JS yang butuh 1 kali
                # pulang-pergi komunikasi browser<->server. Kalau
                # st.rerun() dipanggil PERSIS sesudah clear_login_cookie(),
                # rerun itu sering kejadian SEBELUM instruksi hapus cookie
                # beneran sampai & disinkronkan ke browser -- jadi klik
                # pertama kelihatan "gak ngefek", baru klik kedua yang
                # kerasa. Jeda singkat ini kasih waktu component itu
                # menyelesaikan pulang-pergi tadi sebelum halaman di-render
                # ulang, jadi 1x klik langsung keluar.
                with st.spinner("Logout..."):
                    time.sleep(0.35)
                st.rerun()

    if col_notif is not None:
        with col_notif:
            render_notification_bell(supabase_client, user_id=identifier)
    with col_portfolio:
        if status in ("owner", "active"):
            if st.button("📌 Portofolio Saya", use_container_width=True):
                st.session_state["show_portfolio"] = True
                st.rerun()

# ---- Banner soft: sisa masa aktif <=3 hari ----
# Sengaja diletakkan di sini (segera setelah header, SEBELUM percabangan
# show_portfolio/show_customer_panel/active_panel manapun), supaya tampil
# terus menerus di halaman apapun yang lagi dibuka, bukan cuma di dashboard,
# dan tanpa perlu diklik/dibuka dulu (beda dari popover yang perlu diklik).
if status == "active":
    _sub_info_banner = get_subscription_info(identifier, user_db)
    if _sub_info_banner and 0 <= _sub_info_banner["days_left"] <= 3:
        _sisa = _sub_info_banner["days_left"]
        _sisa_txt = "hari ini" if _sisa == 0 else f"{_sisa} hari lagi"
        st.info(f"⏳ Masa aktif langganan kamu tinggal {_sisa_txt}. Yuk perpanjang biar akses gak terputus.")

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
#
# CATATAN: tidak ada lagi kontrol auto-refresh/refresh manual di sidebar.
# Data sudah diperbarui otomatis di belakang layar oleh sistem pusat
# (scan_worker.py -> Supabase) tiap ~15 menit; user tinggal pakai data itu
# tanpa perlu ngatur atau memicu refresh sendiri. Panel Top Gainer/Loser
# tetap baca ulang data terbaru tiap 15 menit lewat st.fragment (diam-diam,
# tanpa UI/countdown).
REFRESH_SECONDS = 900
manual_refresh_clicked = False

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
    """Download data semua saham, TAPI dibagi jadi kelompok kecil (batch)
    biar RAM tidak numpuk sekaligus -- sebelumnya semua ~600 saham
    di-download dalam satu panggilan yf.download, ini bikin memori
    server melonjak drastis dan proses mati (segmentation fault) di
    Streamlit Cloud (jatah RAM terbatas). Sekarang diproses per-batch,
    dan hasil mentah tiap batch langsung dibuang dari memori (gc.collect)
    sebelum lanjut ke batch berikutnya."""
    stocks = list(stocks_tuple)
    BATCH_SIZE = 60
    data = {}

    for i in range(0, len(stocks), BATCH_SIZE):
        batch = stocks[i:i + BATCH_SIZE]
        try:
            raw = yf.download(
                batch,
                period="3mo",
                interval="1d",
                progress=False,
                group_by="ticker",
                threads=True,
                auto_adjust=True,
            )
        except Exception:
            continue

        for stock in batch:
            try:
                if len(batch) == 1:
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

        del raw
        gc.collect()

    return data


def _display_ticker(t):
    """Buang suffix .JK biar tampilan UI cukup nama sahamnya aja, mis. DEWA.JK -> DEWA."""
    return t[:-3] if t.endswith(".JK") else t


def support_resistance(df):
    support = df['Low'].rolling(20).min().iloc[-1]
    resistance = df['High'].rolling(20).max().iloc[-1]
    return support, resistance


def trend_strength(df):
    """UPGRADE: sebelumnya pakai SMA20 vs SMA50 (rata-rata biasa), sekarang
    EMA20 vs EMA50 (Exponential Moving Average) -- lebih responsif ke
    perubahan harga terbaru, sesuai indikator 'EMA 20/50' yang diminta."""
    ema20 = calc_ema(df, 20)
    ema50 = calc_ema(df, 50)
    if ema20.iloc[-1] > ema50.iloc[-1]:
        return "Bullish Strong"
    elif ema20.iloc[-1] < ema50.iloc[-1]:
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


def pct_change_week(df):
    """% perubahan harga dari ~5 hari bursa lalu (kira-kira 1 minggu bursa)
    ke harga penutupan terakhir. Kalau data historinya kurang dari 6 baris
    (saham baru IPO / data terbatas), pakai baris paling awal yang ada."""
    if len(df) < 2:
        return 0.0
    idx_back = min(5, len(df) - 1)
    past = df['Close'].iloc[-1 - idx_back]
    now = df['Close'].iloc[-1]
    if past == 0:
        return 0.0
    return (now - past) / past * 100


@st.cache_data(ttl=15 * 60, show_spinner=False)
def get_quick_quote(ticker_jk):
    """Ambil harga terakhir + %perubahan harian saja untuk saham DI LUAR
    watchlist ISSI (non-syariah). SENGAJA tidak menghitung skor/sinyal/
    status bandar untuk saham ini — sesuai keputusan: sistem tidak
    menampilkan rekomendasi/analisis untuk saham non-syariah, cuma info
    harga dasar. Return dict {"price", "change_pct"} atau None kalau
    ticker tidak valid / data tidak tersedia.
    (Didefinisikan di sini, SEBELUM titik 'st.stop()' halaman portofolio,
    supaya tidak NameError saat dipanggil dari render_portfolio_page /
    add_to_portfolio — script Streamlit berhenti di st.stop() sebelum
    sempat mengeksekusi definisi fungsi yang ditulis lebih ke bawah.)"""
    try:
        df = yf.Ticker(ticker_jk).history(period="10d", interval="1d", auto_adjust=True)
        if df is None or df.empty or len(df) < 2:
            return None
        price = float(df['Close'].iloc[-1])
        change_pct = pct_change(df)
        return {"price": round(price, 2), "change_pct": round(float(change_pct), 2)}
    except Exception:
        return None


def scoring(df, support, resistance):
    """Scoring gabungan, total maksimal 100. Sekarang menggabungkan
    kriteria klasik (support/breakout/volume/trend EMA) DENGAN indikator
    teknikal tambahan: RSI, MACD, ADX, Bollinger Bands, VWAP, Fibonacci.

    Tiap kriteria kasih poin KALAU kepenuhi. Kalau datanya belum cukup
    buat 1 indikator tertentu (saham baru listing, histori pendek) atau
    kriterianya gak kepenuhi, poinnya 0 -- TIDAK dipaksa kasih poin biar
    skor 'kelihatan bagus'. Bobot per kriteria (total 100):
      - Harga dekat support        : 10
      - Breakout valid              : 15
      - Volume spike                : 10
      - Trend EMA20>EMA50 (bullish) : 15
      - RSI momentum bullish        : 10
      - MACD bullish crossover      : 10
      - ADX tren kuat + arah naik   : 10
      - Bollinger Bands posisi      : 10
      - VWAP (harga > VWAP rolling) : 5
      - Fibonacci dekat support kuat: 5
    """
    score = 0
    price = df['Close'].iloc[-1]

    # ---- Kriteria klasik ----
    if price <= support * 1.05:
        score += 10
    if breakout_valid(df, resistance):
        score += 15
    if volume_spike(df):
        score += 10
    if "Bullish" in trend_strength(df):
        score += 15

    # ---- RSI: momentum bullish sehat (bukan overbought), atau oversold
    # (potensi rebound, poin lebih kecil karena bukan sinyal sekuat momentum) ----
    try:
        rsi_val = calc_rsi(df).iloc[-1]
        if 50 < rsi_val <= 70:
            score += 10
        elif rsi_val < 30:
            score += 5
    except Exception:
        pass

    # ---- MACD: garis MACD di atas garis sinyal DAN histogram positif ----
    try:
        macd_line, signal_line, hist = calc_macd(df)
        if macd_line.iloc[-1] > signal_line.iloc[-1] and hist.iloc[-1] > 0:
            score += 10
    except Exception:
        pass

    # ---- ADX: tren KUAT (>=25) dan arahnya naik (+DI > -DI) ----
    try:
        adx_val, plus_di, minus_di = calc_adx(df)
        if adx_val.iloc[-1] >= 25 and plus_di.iloc[-1] > minus_di.iloc[-1]:
            score += 10
    except Exception:
        pass

    # ---- Bollinger Bands: breakout upper band (momentum kuat) ATAU
    # baru mantul dari lower band (potensi rebound, poin lebih kecil) ----
    try:
        bb_upper, bb_mid, bb_lower = calc_bollinger(df)
        if price >= bb_upper.iloc[-1]:
            score += 10
        elif price <= bb_lower.iloc[-1] * 1.02:
            score += 5
    except Exception:
        pass

    # ---- VWAP rolling: harga di atas VWAP = bias beli lebih dominan ----
    try:
        vwap_val = calc_vwap_rolling(df).iloc[-1]
        if pd.notna(vwap_val) and price > vwap_val:
            score += 5
    except Exception:
        pass

    # ---- Fibonacci: harga lagi persis di level support kuat (0.5/0.618),
    # toleransi 1.5% dari harga sekarang ----
    try:
        fib = calc_fibonacci_levels(df)
        if fib:
            for lvl_key in ("0.5", "0.618"):
                lvl_price = fib[lvl_key]
                if lvl_price > 0 and abs(price - lvl_price) / price <= 0.015:
                    score += 5
                    break
    except Exception:
        pass

    return min(score, 100)


def signal(score):
    if score >= 75:
        return "STRONG BUY 🚀"
    elif score >= 50:
        return "BUY"
    elif score >= 30:
        return "HOLD"
    else:
        return "SELL"


def score_badge(score):
    """Tampilkan angka score dengan warna gradasi: makin rendah makin
    merah, naik ke oranye/kuning di tengah, makin tinggi makin hijau.
    Pakai HSL supaya transisinya halus (bukan cuma 3 warna patah-patah)."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return str(score)
    s_clamped = max(0.0, min(100.0, s))
    hue = s_clamped * 1.2  # 0 = merah, ~60 = kuning/oranye, 120 = hijau
    color = f"hsl({hue:.0f}, 75%, 50%)"
    display_val = int(s) if float(s).is_integer() else s
    return f'<span style="color:{color}; font-weight:700;">{display_val}</span>'


def render_html_table(df):
    """Render dataframe sebagai tabel HTML custom supaya header bisa dibuat
    KAPITAL + rata tengah + lebih besar (st.dataframe bawaan Streamlit
    menggambar header di canvas jadi tidak bisa di-styling pakai CSS)."""
    if df is None or df.empty:
        st.info("Tidak ada data.")
        return

    # Kolom-kolom persentase naik/turun -- diwarnai ijo/merah konsisten
    # dengan .gain-up/.gain-down yang sudah dipakai di Portofolio, Top
    # Gainer/Loser, dan tabel histori harga saham.
    PCT_GAIN_LOSS_COLS = {"change_pct", "week_change_pct"}

    df_fmt = df.copy()
    for col in df_fmt.columns:
        if df_fmt[col].dtype == bool:
            df_fmt[col] = df_fmt[col].map(lambda v: "✔️" if v else "–")

    header_html = "".join(
        f"<th>{str(col).replace('_', ' ').upper()}</th>" for col in df_fmt.columns
    )
    body_rows = []
    for pos, (idx, row) in enumerate(df_fmt.iterrows(), start=1):
        cells = ""
        for col in df_fmt.columns:
            if col == "stock":
                # Nama saham diklik -> pindah ke halaman detail saham itu
                # (lihat render_stock_detail_page), lewat query param URL.
                cells += f'<td><a class="stock-link" href="?stock={row[col]}" target="_self">{row[col]}</a></td>'
            elif col == "score":
                cells += f'<td>{score_badge(row[col])}</td>'
            elif col == "signal":
                cells += f'<td>{signal_badge(row[col])}</td>'
            elif col in PCT_GAIN_LOSS_COLS and pd.notna(row[col]):
                _v = row[col]
                _cls = "gain-up" if _v >= 0 else "gain-down"
                cells += f'<td><span class="{_cls}">{_v:+.2f}%</span></td>'
            else:
                cells += f"<td>{row[col]}</td>"
        # Lencana buat 3 peringkat teratas (tabel sudah terurut dari skor
        # tertinggi, lihat build_full_scan -> sort_values("score")), biar
        # user langsung tahu peringkat itu dinilai dari skor scoring().
        medal = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}.get(pos, "")
        body_rows.append(f"<tr><td>{medal}{pos}</td>{cells}</tr>")

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
    elif "HOLD" in sig:
        cls = "badge-hold"
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
#  INDIKATOR TEKNIKAL TAMBAHAN — RSI, MACD, EMA, ATR, ADX,
#  Bollinger Bands, VWAP, Auto-Fibonacci.
#  Semua dipakai sebagai KOMPONEN SCORING (lihat scoring()), bukan
#  kolom tabel baru -- tujuannya scoring makin akurat/kuat, sesuai
#  indikator yang sudah jadi standar di produk sejenis.
# ============================================================
def calc_ema(df, span):
    """Exponential Moving Average -- lebih responsif ke harga terbaru
    dibanding SMA biasa, karena bobot data baru lebih besar."""
    return df['Close'].ewm(span=span, adjust=False).mean()


def calc_rsi(df, period=14):
    """RSI (Relative Strength Index) 0-100. Wilder's smoothing (standar
    baku RSI, bukan simple moving average biasa). >70 = overbought,
    <30 = oversold. Kalau data belum cukup / avg_loss = 0, netral (50)
    -- BUKAN dipaksa jadi sinyal kuat."""
    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val.fillna(50)


def calc_macd(df, fast=12, slow=26, signal=9):
    """MACD standar (EMA12 - EMA26), garis sinyal EMA9 dari MACD line,
    dan histogram (MACD - sinyal). Return 3 Series: macd_line, signal_line, hist."""
    ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def calc_atr(df, period=14):
    """Average True Range (Wilder) -- ukuran volatilitas harian, dipakai
    scoring lain (bukan berdiri sendiri) buat menimbang seberapa 'wajar'
    breakout/volume spike terjadi relatif ke volatilitas normal saham itu."""
    high, low, close = df['High'], df['Low'], df['Close']
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def calc_adx(df, period=14):
    """ADX (Average Directional Index) + DI/-DI (Wilder). ADX >= 25 =
    tren sedang kuat (naik ATAU turun -- makanya WAJIB dicek bareng
    +DI vs -DI buat tau arahnya). Return (adx, plus_di, minus_di)."""
    high, low, close = df['High'], df['Low'], df['Close']
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_val = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (
        plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        / atr_val.replace(0, np.nan)
    )
    minus_di = 100 * (
        minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        / atr_val.replace(0, np.nan)
    )
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return adx_val.fillna(0), plus_di.fillna(0), minus_di.fillna(0)


def calc_bollinger(df, period=20, num_std=2):
    """Bollinger Bands standar: SMA20 di tengah, ±2 standar deviasi.
    Return (upper, mid, lower)."""
    mid = df['Close'].rolling(period).mean()
    std = df['Close'].rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def calc_vwap_rolling(df, period=20):
    """PENDEKATAN, bukan VWAP intraday asli -- VWAP asli reset tiap hari
    dari data per-menit yang TIDAK tersedia dari yfinance interval
    harian (sama seperti keterbatasan yang dibahas soal BSJP). Ini
    'VWAP rolling N hari' dari data harian: total(harga tipikal x volume)
    / total(volume) selama N hari terakhir -- dipakai sebagai proxy level
    harga rata-rata tertimbang volume, bukan VWAP harian yang presisi."""
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    pv = typical_price * df['Volume']
    return pv.rolling(period).sum() / df['Volume'].rolling(period).sum()


def calc_fibonacci_levels(df, lookback=60):
    """Level Fibonacci retracement otomatis dari swing high & swing low
    N hari terakhir. Return dict {level: harga}, atau {} kalau data
    kurang / swing high=low (gak ada range buat dihitung)."""
    window = df.tail(lookback)
    swing_high = window['High'].max()
    swing_low = window['Low'].min()
    diff = swing_high - swing_low
    if diff <= 0:
        return {}
    return {
        "0.0": swing_high,
        "0.236": swing_high - 0.236 * diff,
        "0.382": swing_high - 0.382 * diff,
        "0.5": swing_high - 0.5 * diff,
        "0.618": swing_high - 0.618 * diff,
        "0.786": swing_high - 0.786 * diff,
        "1.0": swing_low,
    }


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


def broker_category_class(investor_value):
    """Petakan field 'investor' dari GOAPI ke kelas CSS warna kategori
    broker. Sengaja fleksibel (cocokkan substring, bukan exact-match)
    karena kita belum punya dokumentasi pasti nilai persis yang dikirim
    GOAPI — kalau nilainya di luar dugaan, jatuh ke kategori netral
    (bukan nebak/salah kategorikan)."""
    v = str(investor_value or "").strip().lower()
    if "asing" in v or "foreign" in v:
        return "broker-foreign"
    if "domestik" in v or "domestic" in v or "lokal" in v or "local" in v or "retail" in v or "ritel" in v:
        return "broker-domestic"
    return "broker-other"


def get_top5_broker_net(symbol, date_str, top_n=5):
    """Hitung Top-N broker net buy & net sell (net = total value beli -
    total value jual per kode broker) untuk 1 saham + 1 tanggal, dari 1x
    panggilan fetch_broker_summary() (SUDAH di-cache 20 jam) — jadi
    top-5-nya sendiri TIDAK menambah panggilan API sama sekali, murni
    olah data di Python. Return (top_buyers, top_sellers, error) — tiap
    item dict {"broker_code", "net", "category_class"}."""
    df, err = fetch_broker_summary(symbol, date_str)
    if err:
        return [], [], err
    if df is None or df.empty:
        return [], [], None

    def _net_per_broker(g):
        buy_val = g.loc[g["side"].str.startswith("B", na=False), "value"].sum()
        sell_val = g.loc[g["side"].str.startswith("S", na=False), "value"].sum()
        investor = g["investor"].iloc[0] if len(g) else "-"
        return pd.Series({"net": buy_val - sell_val, "investor": investor})

    grouped = df.groupby("broker_code").apply(_net_per_broker).reset_index()
    if grouped.empty:
        return [], [], None

    def _to_items(sub_df):
        return [
            {
                "broker_code": row["broker_code"],
                "net": row["net"],
                "category_class": broker_category_class(row["investor"]),
            }
            for _, row in sub_df.iterrows()
        ]

    top_buyers = _to_items(grouped[grouped["net"] > 0].sort_values("net", ascending=False).head(top_n))
    top_sellers = _to_items(grouped[grouped["net"] < 0].sort_values("net", ascending=True).head(top_n))
    return top_buyers, top_sellers, None


def resample_price_history(hist, freq):
    """Ubah data histori harian (kolom Date, Close, Volume) jadi ringkasan
    per minggu ('W') atau per bulan ('M'): harga penutupan periode itu,
    %perubahan dari periode sebelumnya, dan total volume selama periode
    itu. Return DataFrame terurut TERBARU DI ATAS."""
    if hist is None or hist.empty:
        return pd.DataFrame()
    df = hist.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    agg = df.resample(freq).agg({"Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
    if agg.empty:
        return pd.DataFrame()
    agg["pct_change"] = agg["Close"].pct_change() * 100
    agg = agg.reset_index().sort_values("Date", ascending=False)
    return agg


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
        f'pukul <b>16:00 WIB</b> (closing/EOD)</span>'
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
        st.error("Data broker sedang tidak bisa diambil, coba lagi sebentar lagi.")
        if status == "owner":
            st.caption(
                f"[Owner] Detail error: {broker_err} — cek: (1) GOAPI_API_KEY "
                "sudah diisi di Secrets, (2) endpoint/header di "
                "fetch_broker_summary() sudah cocok dengan dokumentasi resmi "
                "di dashboard GOAPI kamu (goapi.io/docs), (3) kuota API "
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



# CATATAN: build_full_scan() di bawah ini SUDAH TIDAK DIPANGGIL di mana
# pun lagi. App sekarang TIDAK PERNAH scan sendiri -- selalu baca hasil
# scan terpusat dari Supabase (lihat _load_central_scan / ensure_scanned
# di atas). Fungsi ini dibiarkan ada (bukan dihapus) sebagai referensi
# logika scan, kalau-kalau suatu saat dibutuhkan lagi.
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
            week_change_pct = pct_change_week(df)
            results.append({
                "stock": stock,
                "price": round(float(df['Close'].iloc[-1]), 2),
                "change_pct": round(float(change_pct), 2),
                "week_change_pct": round(float(week_change_pct), 2),
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
#  HEADER — search bar cari saham (dipanggil ulang di SETIAP halaman:
#  Dashboard, Portofolio, Kelola Pelanggan, dan semua panel fitur
#  seperti Scan Market/Bandar/Breakout/dll -- bukan cuma di Dashboard)
# ============================================================
def render_stock_search_bar(form_key):
    with st.form(form_key, clear_on_submit=False):
        _search_col1, _search_col2 = st.columns([6, 1])
        with _search_col1:
            _query = st.text_input(
                "Cari saham",
                placeholder="Cari kode saham, mis. BBCA",
                label_visibility="collapsed",
                key=f"{form_key}_input",
            )
        with _search_col2:
            _submitted = st.form_submit_button("🔍", use_container_width=True)
    if _submitted and _query.strip():
        st.query_params["stock"] = _query.strip().upper()
        st.rerun()


# CATATAN: render_stock_search_bar("dashboard_search_form") TIDAK dipanggil
# di sini lagi -- sekarang search bar cuma tampil di halaman Dashboard
# (lihat blok "if st.session_state.active_panel is None" di bawah) dan di
# halaman Detail Saham (render_stock_detail_page). Halaman Portofolio,
# Kelola Pelanggan, dan panel fitur (Scan/Bandar/dll) TIDAK lagi menampilkan
# search bar.

# ---- State awal ----
if "scan_df" not in st.session_state:
    st.session_state.scan_df = None
if "last_updated" not in st.session_state:
    st.session_state.last_updated = None
if "last_updated_epoch" not in st.session_state:
    st.session_state.last_updated_epoch = None


@st.cache_data(ttl=900, show_spinner=False)
def _load_central_scan():
    """Baca hasil scan yang sudah disiapkan 'petugas scan' (scan_worker.py,
    dijalankan berkala lewat GitHub Actions) dari Supabase -- BUKAN scan
    sendiri. Ini yang bikin app cepat dibuka biarpun banyak user
    bersamaan, karena tidak ada yang download+hitung data saham sendiri
    tiap buka app. Return (DataFrame, waktu_update) atau (None, None)
    kalau data pusat belum ada / gagal diambil.

    PERBAIKAN (skala 1000+ user): dibungkus @st.cache_data(ttl=900) --
    cache ini DIBAGI ke SEMUA user di server yang sama (bukan per-session
    seperti st.session_state). Jadi walau ribuan user klik apapun
    bersamaan, cuma ADA MAKSIMAL 1 request nyata ke Supabase tiap 15
    menit (disamakan dengan cadence scan_worker.py) -- user lainnya
    otomatis dapat data dari cache RAM server, nyaris instan, tanpa
    network call sendiri-sendiri."""
    if not supabase_client:
        return None, None
    try:
        res = supabase_client.table("scan_results").select("*").eq("id", 1).execute()
        rows = res.data or []
        if not rows:
            return None, None
        records = rows[0].get("data") or []
        if not records:
            return None, None
        df = pd.DataFrame(records)
        updated_dt = None
        updated_raw = rows[0].get("updated_at")
        if updated_raw:
            try:
                updated_dt = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
                updated_dt = (updated_dt + timedelta(hours=7)).replace(tzinfo=None)
            except Exception:
                updated_dt = None
        return df, updated_dt
    except Exception:
        return None, None


def ensure_scanned(force=False):
    """Dipakai oleh tombol aksi & panel atas. App TIDAK PERNAH scan
    sendiri lagi -- cukup baca hasil scan TERPUSAT dari Supabase (diisi
    scan_worker.py tiap 15 menit lewat GitHub Actions, HANYA jam bursa).
    Kalau data pusat kosong total (baru pertama kali setup, belum pernah
    ada scan sukses sekalipun), df yang dikembalikan kosong -- banner di
    render_top_panel() yang menjelaskan situasinya ke user, BUKAN dengan
    scan langsung (biar app tetap ringan buat semua orang).

    PERBAIKAN (skala 1000+ user): sekarang SELALU panggil
    _load_central_scan() langsung -- tidak lagi menyimpan salinan manual
    di st.session_state per-user. Karena _load_central_scan() sendiri
    sudah di-cache (ttl=900, dibagi ke semua user), pemanggilan ini
    nyaris gratis (baca cache RAM) kecuali cache-nya baru saja expired.
    Efeknya: Scan / Portofolio / Top Gainer-Loser semua otomatis baca
    sumber data yang SAMA & SINKRON, dan otomatis "ter-update" begitu
    cache di-refresh oleh siapapun user yang kebetulan jadi pemicu
    pertama setelah 15 menit -- tanpa perlu tombol refresh manual.
    `force` dipertahankan untuk kompatibilitas tapi sekarang tidak
    berefek berbeda, karena tidak ada lagi cache manual di session_state
    yang perlu "dipaksa" dilewati."""
    df_central, updated_dt = _load_central_scan()
    st.session_state.scan_df = df_central if df_central is not None else pd.DataFrame()
    st.session_state.last_updated = updated_dt
    st.session_state.last_updated_epoch = time.time()
    return st.session_state.scan_df


def render_portfolio_page(user_db, identifier, display_name):
    """Halaman 'Portofolio Saya' — daftar saham pribadi user, dengan data
    lengkap (harga, %harian, %mingguan, sinyal, status bandar) untuk saham
    ISSI, dan data dasar SAJA (harga + %harian) untuk saham non-syariah,
    tanpa sinyal/rekomendasi apa pun untuk yang non-syariah."""
    st.markdown(f"### 📌 Portofolio Saya — {display_name}")
    if st.button("← Kembali"):
        st.session_state["show_portfolio"] = False
        st.rerun()

    st.markdown(
        '<div class="auth-caption">Tambahkan kode saham yang kamu pantau. '
        'Saham ISSI (syariah) dapat data lengkap; saham non-syariah cuma '
        'ditampilkan harga & perubahan harian, tanpa sinyal/rekomendasi.</div>',
        unsafe_allow_html=True,
    )

    with st.form("form_add_portfolio", clear_on_submit=True):
        col_add_input, col_add_btn = st.columns([4, 1])
        with col_add_input:
            new_code = st.text_input(
                "Tambah kode saham", placeholder="Contoh: BBCA", label_visibility="collapsed"
            )
        with col_add_btn:
            submit_add = st.form_submit_button("➕ Tambah", use_container_width=True)

    if submit_add:
        ok, err = add_to_portfolio(user_db, identifier, new_code)
        if ok:
            st.success(f"{new_code.strip().upper()} ditambahkan ke portofolio.")
        else:
            st.error(err)

    portfolio = get_user_portfolio(user_db, identifier)
    if not portfolio:
        st.info("Portofolio kamu masih kosong. Tambahkan kode saham di atas.")
        return

    df_scan = ensure_scanned()  # pakai cache kalau sudah ada, scan sekali kalau belum

    rows_html = []
    for code in portfolio:
        ticker_jk = f"{code}.JK"
        is_syariah = ticker_jk in ISSI_STOCKS
        remove_key = f"remove_portfolio_{code}"

        if is_syariah:
            match = df_scan[df_scan["stock"] == ticker_jk] if df_scan is not None else None
            if match is not None and not match.empty:
                r = match.iloc[0]
                price = f"{r['price']:,.0f}"
                score_txt = score_badge(r["score"])
                daily = r["change_pct"]
                weekly = r.get("week_change_pct", None)
                weekly_txt = f"{weekly:+.2f}%" if weekly is not None else "–"
                daily_cls = "gain-up" if daily >= 0 else "gain-down"
                weekly_cls = "gain-up" if (weekly or 0) >= 0 else "gain-down"
                sig_html = signal_badge(r["signal"])
                bandar_txt = r["bandar"]
                row_cls = ""
            else:
                price, daily_cls, weekly_txt, weekly_cls = "–", "", "–", ""
                daily = None
                score_txt = "–"
                sig_html = '<span class="badge badge-neutral">DATA BELUM ADA</span>'
                bandar_txt = "–"
                row_cls = ""
        else:
            quote = get_quick_quote(ticker_jk)
            row_cls = "portfolio-row-nonsyariah"
            if quote:
                price = f"{quote['price']:,.0f}"
                daily = quote["change_pct"]
                daily_cls = "gain-up" if daily >= 0 else "gain-down"
            else:
                price, daily, daily_cls = "–", None, ""
            score_txt = "–"
            weekly_txt, weekly_cls = "–", ""
            sig_html = '<span class="badge badge-nonsyariah">⚠️ BUKAN SYARIAH</span>'
            bandar_txt = "–"

        daily_txt = f"{daily:+.2f}%" if daily is not None else "–"
        rows_html.append(
            f'<tr class="{row_cls}">'
            f'<td><a class="stock-link" href="?stock={ticker_jk}" target="_self"><b>{code}</b></a></td>'
            f'<td>{price}</td>'
            f'<td class="{daily_cls}">{daily_txt}</td>'
            f'<td class="{weekly_cls}">{weekly_txt}</td>'
            f'<td>{score_txt}</td>'
            f'<td>{sig_html}</td>'
            f'<td>{bandar_txt}</td>'
            f'</tr>'
        )

    table_html = f"""
    <div class="portfolio-table-wrap">
      <table class="portfolio-table">
        <thead><tr>
          <th>KODE</th><th>HARGA</th><th>%HARI INI</th><th>%MINGGU INI</th><th>SCORE</th>
          <th>STATUS</th><th>BANDAR</th>
        </tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)

    st.markdown("##### Hapus saham dari portofolio")
    col_del_select, col_del_btn = st.columns([4, 1])
    with col_del_select:
        code_to_remove = st.selectbox(
            "Pilih saham", portfolio, label_visibility="collapsed", key="portfolio_remove_select"
        )
    with col_del_btn:
        if st.button("🗑️ Hapus", use_container_width=True):
            remove_from_portfolio(user_db, identifier, code_to_remove)
            st.rerun()


if st.session_state.get("show_portfolio"):
    render_portfolio_page(user_db, identifier, display_name)
    st.stop()


def render_customer_panel(user_db):
    """Halaman khusus Owner (dibuka lewat tombol 'Kelola Pelanggan' di
    popover nama akun): daftar SEMUA pelanggan beserta status/paket/tanggal
    langganan masing-masing, plus form aktivasi/perpanjangan manual --
    dipakai selama Stripe belum live / untuk koreksi manual selagi app
    belum dibuka ke umum."""
    st.markdown("### 🗂️ Kelola Pelanggan")
    if st.button("← Kembali", key="back_from_customer_panel"):
        st.session_state["show_customer_panel"] = False
        st.rerun()

    st.markdown("##### Aktivasi / Perpanjang Manual")
    semua_username = sorted(
        k[len("user:"):] for k in user_db.keys()
        if k.startswith("user:") and k[len("user:"):] not in OWNER_USERNAMES
    )
    if not semua_username:
        st.info("Belum ada akun pelanggan yang terdaftar.")
    else:
        with st.form("form_activate_customer", clear_on_submit=False):
            col_u, col_p, col_btn = st.columns([2, 2, 1])
            with col_u:
                target_username = st.selectbox("Pilih user", semua_username)
            with col_p:
                target_plan = st.selectbox(
                    "Pilih paket",
                    list(PLAN_DURATIONS.keys()),
                    format_func=lambda p: PLAN_LABELS.get(p, p),
                )
            with col_btn:
                st.markdown("<br>", unsafe_allow_html=True)
                submit_activate = st.form_submit_button("✅ Aktifkan", use_container_width=True)

        if submit_activate:
            activate_subscription(user_db, f"user:{target_username}", target_plan)
            st.success(
                f"Langganan {target_username} diaktifkan -- paket {PLAN_LABELS.get(target_plan, target_plan)}."
            )
            st.rerun()

    st.divider()
    st.markdown("##### Semua Pelanggan")

    rows_html = []
    total_active = 0
    for key in sorted(user_db.keys()):
        if not key.startswith("user:"):
            continue
        uname = key[len("user:"):]
        if uname in OWNER_USERNAMES:
            continue
        record = user_db.get(key, {})
        rec_status = record.get("status", "inactive")
        if rec_status == "active":
            total_active += 1
        sub_info = get_subscription_info(key, user_db)
        if sub_info and sub_info.get("subscribed_at") and sub_info.get("expires_at"):
            try:
                mulai_dt = datetime.fromisoformat(sub_info["subscribed_at"])
                akhir_dt = datetime.fromisoformat(sub_info["expires_at"])
                plan_label = PLAN_LABELS.get(sub_info["plan"], sub_info["plan"] or "-")
                mulai_txt = format_tanggal_id(mulai_dt)
                akhir_txt = format_tanggal_id(akhir_dt)
                sisa_txt = f"{sub_info['days_left']} hari"
            except (ValueError, TypeError):
                plan_label, mulai_txt, akhir_txt, sisa_txt = "-", "-", "-", "-"
        else:
            plan_label, mulai_txt, akhir_txt, sisa_txt = "-", "-", "-", "-"

        status_html = (
            '<span class="badge badge-buy">Aktif</span>'
            if rec_status == "active"
            else '<span class="badge badge-sell">Belum/Nonaktif</span>'
        )

        rows_html.append(
            f"<tr><td>{uname}</td><td>{record.get('email', '-')}</td>"
            f"<td>{status_html}</td><td>{plan_label}</td>"
            f"<td>{mulai_txt}</td><td>{akhir_txt}</td><td>{sisa_txt}</td></tr>"
        )

    st.caption(f"Total pelanggan aktif: **{total_active}** dari {len(rows_html)} akun terdaftar.")

    table_html = f"""
    <div class="portfolio-table-wrap">
      <table class="portfolio-table">
        <thead><tr>
          <th>USERNAME</th><th>EMAIL</th><th>STATUS</th><th>PAKET</th>
          <th>MULAI</th><th>BERAKHIR</th><th>SISA</th>
        </tr></thead>
        <tbody>{''.join(rows_html) if rows_html else '<tr><td colspan="7">Belum ada pelanggan.</td></tr>'}</tbody>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)


if st.session_state.get("show_customer_panel"):
    if status != "owner":
        st.session_state["show_customer_panel"] = False
    else:
        render_customer_panel(user_db)
        st.stop()


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

def _fetch_history_goapi(ticker_jk, period_label):
    """PLACEHOLDER — belum diimplementasikan. Isi nanti kalau sudah
    langganan GOAPI (pakai GOAPI_HISTORY_ENDPOINT_TEMPLATE). period_label
    adalah salah satu key di PERIOD_OPTIONS (mis. "1 Tahun", "5 Tahun",
    "Sejak IPO (Max)") — dipakai untuk menentukan rentang tanggal & interval
    saat memanggil endpoint GOAPI. Harus return DataFrame dengan kolom:
    Date, Open, High, Low, Close, Volume."""
    raise NotImplementedError("Fetch histori GOAPI belum diimplementasikan")


def _fetch_fundamentals_goapi(ticker_jk):
    """PLACEHOLDER — belum diimplementasikan. Isi nanti kalau sudah
    langganan GOAPI (pakai GOAPI_FUNDAMENTAL_ENDPOINT_TEMPLATE). Harus
    return dict dengan key: nama, sektor, industri, market_cap, per, eps,
    mata_uang."""
    raise NotImplementedError("Fetch fundamental GOAPI belum diimplementasikan")


_goapi_configured_for_detail = bool(GOAPI_API_KEY) and GOAPI_API_KEY != "ISI_API_KEY_GOAPI_LO"

# Pilihan rentang waktu grafik histori harga. "days" dipakai untuk hitung
# tanggal mulai (None = sejak IPO/data paling awal yang ada, "ytd" = sejak
# 1 Januari tahun berjalan). "interval" disesuaikan supaya jumlah titik
# data tetap wajar (intraday untuk rentang pendek, mingguan/bulanan untuk
# rentang yang sangat panjang).
PERIOD_OPTIONS = {
    "1 Hari": {"days": 1, "interval": "5m"},
    "1 Minggu": {"days": 7, "interval": "30m"},
    "1 Bulan": {"days": 30, "interval": "1d"},
    "Tahun Ini (YTD)": {"days": "ytd", "interval": "1d"},
    "1 Tahun": {"days": 365, "interval": "1d"},
    "3 Tahun": {"days": 365 * 3, "interval": "1wk"},
    "5 Tahun": {"days": 365 * 5, "interval": "1wk"},
    "Sejak IPO (Max)": {"days": None, "interval": "1mo"},
}


@st.cache_data(ttl=900, show_spinner=False)
def get_stock_history(ticker_jk, period_label):
    """Histori harga untuk 1 saham sesuai rentang waktu yang dipilih user
    (lihat PERIOD_OPTIONS). Prioritas GOAPI kalau sudah dikonfigurasi,
    fallback ke yfinance."""
    cfg = PERIOD_OPTIONS.get(period_label, PERIOD_OPTIONS["1 Tahun"])
    interval = cfg["interval"]
    days = cfg["days"]

    if _goapi_configured_for_detail:
        try:
            df_api = _fetch_history_goapi(ticker_jk, period_label)
            if df_api is not None and not df_api.empty:
                return df_api
        except NotImplementedError:
            pass
        except Exception:
            pass

    try:
        ticker_obj = yf.Ticker(ticker_jk)
        if days is None:
            # "Sejak IPO" — ambil data paling panjang yang tersedia di yfinance
            df = ticker_obj.history(period="max", interval=interval, auto_adjust=True)
        elif days == "ytd":
            start = datetime(datetime.now().year, 1, 1)
            df = ticker_obj.history(start=start, interval=interval, auto_adjust=True)
        else:
            start = datetime.now() - timedelta(days=days)
            df = ticker_obj.history(start=start, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        # Data intraday (interval < 1d) index-nya bernama "Datetime", bukan
        # "Date" — samakan namanya biar kode chart di bawah tidak perlu tahu
        # bedanya.
        if "Datetime" in df.columns:
            df = df.rename(columns={"Datetime": "Date"})
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


def _compute_trading_rangebreaks(date_series):
    """Deteksi celah waktu SECARA OTOMATIS dari data histori yang benar-benar
    ada (bukan asumsi jam bursa tetap) — jadi ini otomatis menangani jam
    istirahat siang, tutup malam, akhir pekan, MAUPUN hari libur bursa
    (karena hari libur otomatis tidak punya baris data sama sekali, sehingga
    kelihatan sebagai jarak yang lebih besar dari biasanya). Celah yang
    jaraknya jauh lebih besar dari jarak antar-candle biasa akan
    disembunyikan dari chart, supaya candle sebelum & sesudahnya nyambung
    langsung tanpa ruang kosong."""
    ts = pd.to_datetime(date_series).sort_values().reset_index(drop=True)
    if len(ts) < 3:
        return []
    diffs = ts.diff().dropna()
    typical = diffs.median()
    if typical <= pd.Timedelta(0):
        return []
    breaks = []
    for i in range(1, len(ts)):
        gap = ts.iloc[i] - ts.iloc[i - 1]
        # Jarak lebih dari 1.5x jarak normal antar-candle -> dianggap
        # waktu bursa tutup (istirahat siang / malam / akhir pekan / libur).
        if gap > typical * 1.5:
            breaks.append(dict(bounds=[ts.iloc[i - 1].isoformat(), ts.iloc[i].isoformat()]))
    return breaks


def render_stock_detail_page(ticker_raw):
    """Halaman khusus 1 saham: histori 1 tahun, fundamental, ringkasan
    sinyal dari hasil scan terakhir, dan berita — dibuka lewat query
    param ?stock=KODE saat nama saham diklik di tabel."""
    ticker_no_jk = str(ticker_raw).upper().strip().replace(".JK", "")
    ticker_jk = f"{ticker_no_jk}.JK"

    if st.button("⬅ Kembali"):
        # Cuma hapus query param "?stock=..." -- state navigasi lain
        # (show_portfolio/show_customer_panel/active_panel) SENGAJA
        # dibiarkan apa adanya, supaya tombol ini balik ke halaman ASAL
        # user klik saham tadi (Dashboard/Portofolio/Scan Market/dll),
        # bukan selalu dipaksa balik ke Dashboard. Kalau mau langsung ke
        # Dashboard dari halaman manapun, pakai logo 🏠 di header.
        st.query_params.pop("stock", None)
        st.rerun()
    render_stock_search_bar("stock_detail_search_form")

    df_scan = st.session_state.get("scan_df")
    scan_row = None
    if df_scan is not None and not df_scan.empty:
        match = df_scan[df_scan["stock"] == ticker_jk]
        if not match.empty:
            scan_row = match.iloc[0]

    # Fundamental diambil di sini (di-cache 6 jam, lihat get_stock_fundamentals)
    # supaya sektor/industrinya bisa langsung tampil di sebelah kode saham,
    # sekaligus dipakai ulang lagi nanti di section "Info Fundamental" di
    # bawah tanpa perlu fetch dua kali.
    with st.spinner("Mengambil info fundamental..."):
        fundamentals = get_stock_fundamentals(ticker_jk)
    _fund_bits = [b for b in [fundamentals.get("sektor"), fundamentals.get("industri")] if b]
    fund_inline = " • ".join(_fund_bits) if _fund_bits else "Info fundamental tidak tersedia"

    sub_line = "Belum ada di hasil scan terakhir"
    if scan_row is not None:
        _chg = scan_row.get("change_pct")
        if _chg is not None and pd.notna(_chg):
            _chg_cls = "gain-up" if _chg >= 0 else "gain-down"
            _chg_html = f' <span class="{_chg_cls}">({_chg:+.2f}%)</span>'
        else:
            _chg_html = ""
        sub_line = f"Harga terakhir: {scan_row['price']}{_chg_html}"
    badge_html = signal_badge(scan_row["signal"]) if scan_row is not None else ""
    st.markdown(
        f"""
        <div class="terminal-header">
            <div>
                <div class="terminal-title">{ticker_no_jk} <span class="stock-fund-inline">{fund_inline}</span></div>
                <div class="terminal-sub">{sub_line}</div>
            </div>
            <div>{badge_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---- Histori harga (rentang waktu bisa dipilih) ----
    st.subheader("📊 Histori Harga")
    pcol1, pcol2 = st.columns([3, 2])
    with pcol1:
        period_label = st.radio(
            "Rentang waktu",
            list(PERIOD_OPTIONS.keys()),
            index=4,  # default: "1 Tahun"
            horizontal=True,
            key="detail_period",
        )
    with pcol2:
        chart_type = st.radio(
            "Tampilan grafik", ["Candlestick", "Line"], horizontal=True, key="detail_chart_type"
        )
    with st.spinner("Mengambil data histori..."):
        hist = get_stock_history(ticker_jk, period_label)

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
        # Sembunyikan SEMUA celah waktu bursa tutup (istirahat siang, malam,
        # akhir pekan, hari libur) secara otomatis, supaya candle nyambung
        # langsung dari satu sesi/hari buka bursa ke sesi/hari buka berikutnya.
        auto_breaks = _compute_trading_rangebreaks(hist["Date"])
        if auto_breaks:
            fig.update_xaxes(rangebreaks=auto_breaks)
        st.plotly_chart(fig, use_container_width=True)

    # ---- Tabel Histori Harga (harian/mingguan/bulanan) ----
    st.subheader("🗓️ Tabel Histori Harga")
    hist_mode = st.radio(
        "Tampilan tabel",
        ["Harian (1 Bulan)", "Mingguan (1 Tahun)", "Bulanan (1 Tahun)"],
        horizontal=True,
        key="detail_hist_table_mode",
    )

    if hist_mode == "Harian (1 Bulan)":
        with st.spinner("Mengambil data harian..."):
            hist_daily = get_stock_history(ticker_jk, "1 Bulan")
        if hist_daily is None or hist_daily.empty:
            st.info("Data histori harian tidak tersedia.")
        else:
            df_daily = hist_daily.copy()
            df_daily["Date"] = pd.to_datetime(df_daily["Date"])
            df_daily = df_daily.sort_values("Date")
            df_daily["pct_change"] = df_daily["Close"].pct_change() * 100
            df_daily = df_daily.sort_values("Date", ascending=False).reset_index(drop=True)

            # Broker Top 5 Net Buy/Sell CUMA untuk 3 hari bursa TERAKHIR
            # (kesepakatan hemat kuota API — lihat catatan di
            # get_top5_broker_net: 1 tanggal = 1 panggilan API, di-cache
            # 20 jam & dibagi ke SEMUA user, bukan per-user).
            broker_col_html = {}
            if _goapi_configured_for_detail:
                for i in range(min(3, len(df_daily))):
                    d = df_daily.iloc[i]["Date"]
                    date_str = d.strftime("%Y-%m-%d")
                    with st.spinner(f"Mengambil data broker {date_str}..."):
                        buyers, sellers, berr = get_top5_broker_net(ticker_jk, date_str)
                    if berr:
                        broker_col_html[i] = ("–", "–")
                        continue
                    buy_html = " ".join(
                        f'<span class="broker-chip {b["category_class"]}">{b["broker_code"]}</span>'
                        for b in buyers
                    ) or "–"
                    sell_html = " ".join(
                        f'<span class="broker-chip {s["category_class"]}">{s["broker_code"]}</span>'
                        for s in sellers
                    ) or "–"
                    broker_col_html[i] = (buy_html, sell_html)

            rows_html = []
            for i, row in df_daily.iterrows():
                pct = row["pct_change"]
                pct_txt = f"{pct:+.2f}%" if pd.notna(pct) else "–"
                pct_cls = "gain-up" if (pd.notna(pct) and pct >= 0) else ("gain-down" if pd.notna(pct) else "")
                buy_html, sell_html = broker_col_html.get(i, ("–", "–"))
                rows_html.append(
                    f"<tr><td>{row['Date'].strftime('%d %b %Y')}</td>"
                    f"<td>{row['Close']:,.0f}</td>"
                    f'<td class="{pct_cls}">{pct_txt}</td>'
                    f"<td>{int(row['Volume']):,}</td>"
                    f"<td>{buy_html}</td>"
                    f"<td>{sell_html}</td></tr>"
                )
            broker_note = (
                "Top 5 broker (3 hari bursa terakhir saja)"
                if _goapi_configured_for_detail
                else "Data top 5 broker belum tersedia untuk saham ini"
            )
            st.markdown(
                f"""
                <div class="hist-table-wrap">
                  <table class="hist-table">
                    <thead><tr>
                      <th>TANGGAL</th><th>HARGA</th><th>%PERUBAHAN</th><th>VOLUME</th>
                      <th>TOP 5 NET BUY</th><th>TOP 5 NET SELL</th>
                    </tr></thead>
                    <tbody>{''.join(rows_html)}</tbody>
                  </table>
                </div>
                <div style="margin-top:6px;">
                    <span class="broker-chip broker-foreign">ASING</span>
                    <span class="broker-chip broker-domestic">DOMESTIK/RITEL</span>
                    <span class="broker-chip broker-other">LAINNYA</span>
                    &nbsp;•&nbsp;{broker_note}
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        freq = "W" if hist_mode.startswith("Mingguan") else "ME"
        with st.spinner("Mengambil data histori 1 tahun..."):
            hist_long = get_stock_history(ticker_jk, "1 Tahun")
        agg = resample_price_history(hist_long, freq)
        if agg.empty:
            st.info("Data tidak tersedia untuk rentang ini.")
        else:
            rows_html = []
            for _, row in agg.iterrows():
                pct = row["pct_change"]
                pct_txt = f"{pct:+.2f}%" if pd.notna(pct) else "–"
                pct_cls = "gain-up" if (pd.notna(pct) and pct >= 0) else ("gain-down" if pd.notna(pct) else "")
                label = row["Date"].strftime("%d %b %Y") if freq == "W" else row["Date"].strftime("%B %Y")
                rows_html.append(
                    f"<tr><td>{label}</td>"
                    f"<td>{row['Close']:,.0f}</td>"
                    f'<td class="{pct_cls}">{pct_txt}</td>'
                    f"<td>{int(row['Volume']):,}</td></tr>"
                )
            st.markdown(
                f"""
                <div class="hist-table-wrap">
                  <table class="hist-table">
                    <thead><tr><th>PERIODE</th><th>HARGA</th><th>%PERUBAHAN</th><th>VOLUME</th></tr></thead>
                    <tbody>{''.join(rows_html)}</tbody>
                  </table>
                </div>
                <div style="margin-top:6px; color:#9ca3af; font-size:12px;">
                    Kolom broker cuma tersedia di tampilan Harian (hemat kuota API).
                </div>
                """,
                unsafe_allow_html=True,
            )

    # ---- Fundamental (detail lengkap; ringkasan sektor/industri sudah
    # ditampilkan di sebelah kode saham di atas, `fundamentals` di sini
    # pakai hasil yang sama, sudah di-cache jadi tidak fetch dua kali) ----
    st.subheader("🏢 Info Fundamental")
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
            "📡 Sebagian data fundamental saham ini mungkin belum lengkap. "
            "Data akan otomatis lebih lengkap begitu sumber data premium "
            "diaktifkan."
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
# PERBAIKAN (bug lama): sebelumnya @st.fragment(run_every=...) salah
# nempel di _is_market_hours() (cuma fungsi cek jam bursa, tidak
# me-render apapun) -- bukan di render_top_panel() yang benar-benar
# menggambar panel & narik data. Efeknya panel ini TIDAK PERNAH benar-
# benar auto-refresh sendiri di background seperti niatnya.
#
# PERBAIKAN (skala 1000+ user): sekarang dekorator dipindah ke
# render_top_panel() yang sebenarnya, DAN fungsi ini sudah tidak lagi
# fetch berat langsung -- cukup panggil ensure_scanned() yang di baliknya
# baca _load_central_scan() ber-cache (ttl=900, dibagi semua user). Jadi
# walau fragment ini jalan tiap 15 menit di ribuan tab user bersamaan,
# hampir semuanya cuma baca cache RAM (murah) -- cuma 1 dari mereka yang
# kebetulan "kena" saat cache expired yang benar-benar fetch ke Supabase.
def _is_market_hours(now_dt):
    """Senin-Jumat, 09:00-16:15 (asumsi waktu server = WIB, konsisten
    dengan konvensi yang sudah dipakai di last_trading_date())."""
    if now_dt.weekday() >= 5:  # Sabtu(5) / Minggu(6)
        return False
    t = now_dt.time()
    return dtime_cls(9, 0) <= t <= dtime_cls(16, 15)


@st.fragment(run_every=REFRESH_SECONDS)
def render_top_panel():
    # TIDAK PERNAH scan sendiri di sini -- ensure_scanned() cuma baca
    # cache bersama (_load_central_scan, ttl=900). Kalau data pusat
    # kosong, df yang balik kosong -- ditangani di banner status di
    # bawah (bukan dengan scan langsung, biar app tetap ringan).
    ensure_scanned()

    # ---- Banner status kesegaran data (dilihat SEMUA user) ----
    is_owner_view = status == "owner"
    updated_dt = st.session_state.last_updated
    now_dt = datetime.now()

    if updated_dt is None:
        st.info("📊 Sedang menyiapkan data, mohon tunggu beberapa menit.")
        if is_owner_view:
            st.caption(
                "⚠️ [Owner] Belum pernah ada scan yang berhasil sama sekali — "
                "cek tab **Actions** di GitHub (\"Scan Saham Terpusat\" → Run workflow)."
            )
    else:
        last_upd_str = updated_dt.strftime("%H:%M, %d %b %Y")
        market_open = _is_market_hours(now_dt)
        stale = (now_dt - updated_dt) > timedelta(minutes=30)

        if market_open and stale:
            if is_owner_view:
                st.warning(
                    f"⚠️ [Owner] Data belum diperbarui >30 menit (terakhir: "
                    f"{last_upd_str} WIB) — cek GitHub Actions/petugas scan."
                )
            else:
                st.warning(f"⏳ Data sedang diperbarui, mohon tunggu sebentar. (Terakhir: {last_upd_str} WIB)")
        else:
            label = "Update terakhir" if market_open else "Data terakhir saat bursa tutup"
            st.markdown(
                f'<div class="update-strip">'
                f'<span>🕒 {label}: <b>{last_upd_str}</b> WIB</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    df_scan_preview = st.session_state.scan_df
    if df_scan_preview is not None and not df_scan_preview.empty:
        st.markdown("#### 📈 Top Gainer / 📉 Top Loser")
        ranked = df_scan_preview.sort_values("change_pct", ascending=False)
        # PERBAIKAN: tampung sampai 50 saham per panel (bukan cuma 3), tapi
        # dibungkus container scroll biar tinggi UI tetap cuma ~5 baris kelihatan.
        gainers = ranked.head(50)
        losers = ranked.tail(50).sort_values("change_pct")

        def _render_scroll_list(rows):
            items_html = "".join(
                f'<div class="scroll-item">'
                f'<a class="stock-link" href="?stock={_display_ticker(r["stock"])}" target="_self">'
                f'{_display_ticker(r["stock"])}</a> — {r["price"]} '
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


# ============================================================
#  NEWS SECTION — dipanggil dari dalam halaman dashboard saja (lihat
#  blok NAVIGASI di bawah). Kalau ada panel yang
#  lagi dibuka (Scan/Bandar/dst), otomatis nongol DI BAWAH panel itu
#  (karena Python jalan top-to-bottom, pemanggilannya ditaruh setelah
#  semua blok "if active_panel == ..."). Kalau tidak ada panel dibuka,
#  otomatis nongol di bawah baris tombol aksi.
# ============================================================
def _build_stock_news_queries(df_scan, user_db, identifier, top_n=15):
    """Gabungan ticker untuk tab 'Saham': saham portofolio user (KHUSUS
    yang syariah/terdaftar ISSI) + top-N skor tertinggi hasil scan,
    duplikat dibuang (portofolio didahulukan di urutan)."""
    portfolio_codes = get_user_portfolio(user_db, identifier) if identifier else []
    portfolio_tickers = [
        f"{c}.JK" for c in portfolio_codes if f"{c}.JK" in ISSI_STOCKS
    ]
    top_tickers = []
    if df_scan is not None and not df_scan.empty:
        top_tickers = df_scan.head(top_n)["stock"].tolist()
    combined = list(dict.fromkeys(portfolio_tickers + top_tickers))  # dedup, urutan dijaga
    return combined, set(portfolio_tickers)


def _render_news_cards(news_items, show_stock_tag):
    """Render daftar berita sebagai card, dibungkus container tinggi tetap
    (~10 berita kelihatan, sisanya bisa discroll)."""
    with st.container(height=420):
        for news in news_items:
            with st.container(border=True):
                st.markdown(f"**{news['title']}**")
                if show_stock_tag and news["matched_stocks"]:
                    stocks_str = ", ".join(t.replace(".JK", "") for t in news["matched_stocks"])
                    cols = st.columns([1, 1, 2])
                    cols[0].markdown(f"📈 Saham: `{stocks_str}`")
                    cols[1].markdown(f"{news['sentiment_emoji']} {news['sentiment']}")
                    meta_col = cols[2]
                else:
                    cols = st.columns([1, 3])
                    cols[0].markdown(f"{news['sentiment_emoji']} {news['sentiment']}")
                    meta_col = cols[1]
                meta = " · ".join(x for x in [news["source"], news["pub_date"]] if x)
                if meta:
                    meta_col.caption(meta)
                if news["description"]:
                    st.caption(news["description"][:220] + ("..." if len(news["description"]) > 220 else ""))
                if news["link"]:
                    st.markdown(f"[Baca selengkapnya]({news['link']})")


# PERBAIKAN (skala 1000+ user): dibungkus fragment sendiri, terpisah dari
# render_top_panel(), supaya berita bisa auto-refresh tiap 15 menit tanpa
# ikut nge-render ulang panel Top Gainer/Loser (dan sebaliknya). Karena
# fetch_stock_news() & get_general_market_news() di baliknya sudah
# di-cache (ttl=900, dibagi semua user), rerun fragment ini tiap 15 menit
# di ribuan tab sekaligus tetap murah -- hampir selalu baca cache RAM.
@st.fragment(run_every=REFRESH_SECONDS)
def render_news_section():
    st.subheader("📰 Berita")
    tab_saham, tab_umum = st.tabs(["📈 Saham", "🌐 Umum"])

    with tab_saham:
        df_scan = ensure_scanned()
        tickers, portfolio_set = _build_stock_news_queries(df_scan, user_db, identifier)
        if not tickers:
            st.info("Belum ada saham untuk dicarikan beritanya (portofolio kosong & scan belum ada).")
        else:
            stock_news_queries = {t: f"{t.replace('.JK', '')} saham" for t in tickers}
            with st.spinner("Mengambil berita saham terbaru..."):
                news_items = get_all_stock_news(stock_news_queries)

            if not news_items:
                st.info(
                    "Belum ada berita terbaru untuk watchlist kamu saat ini, "
                    "atau Google News sedang tidak bisa diakses."
                )
            else:
                # Prioritas: berita HARI INI yang menyangkut saham portofolio
                # user ditaruh paling atas, sisanya tetap urut dari yang
                # paling baru (sort dua tahap, keduanya stabil).
                today = datetime.now().date()

                def _is_today_portfolio(item):
                    pub = parse_pub_date(item["pub_date"])
                    return pub.date() == today and bool(set(item["matched_stocks"]) & portfolio_set)

                news_items.sort(key=lambda item: parse_pub_date(item["pub_date"]), reverse=True)
                news_items.sort(key=lambda item: 0 if _is_today_portfolio(item) else 1)
                _render_news_cards(news_items, show_stock_tag=True)

    with tab_umum:
        with st.spinner("Mengambil berita ekonomi & IHSG terbaru..."):
            general_news_items = get_general_market_news()

        if not general_news_items:
            st.info(
                "Belum ada berita ekonomi/IHSG terbaru saat ini, "
                "atau Google News sedang tidak bisa diakses."
            )
        else:
            _render_news_cards(general_news_items, show_stock_tag=False)


# ============================================================
# ============================================================
#  NAVIGASI HALAMAN — dashboard vs halaman fitur terpisah
# ============================================================
# PERBAIKAN (mobile-app-ready): dulu semua panel (Scan/Bandar/dst)
# nongol DI BAWAH tombol menu, di halaman yang sama dengan dashboard.
# Sekarang beneran dipecah jadi 2 "layar" terpisah:
#   1) DASHBOARD (active_panel is None): HANYA Top Gainer/Loser + menu
#      tombol + Berita. Tidak ada tabel/panel fitur di sini.
#   2) HALAMAN FITUR (active_panel terisi): HANYA konten fitur yang
#      dipilih + tombol "Kembali ke Dashboard" di atasnya. Dashboard,
#      menu, dan Berita disembunyikan total selama di halaman ini.
# Pola 1-layar-1-fungsi ini sengaja dipakai supaya gampang di-porting
# ke navigasi tab/stack ala app Android/iOS nanti.
if "active_panel" not in st.session_state:
    st.session_state.active_panel = None

MENU_ITEMS = [
    ("scan", "🔍 SCAN MARKET"),
    ("bandar", "🐋 BANDAR DETECTOR"),
    ("breakout", "🚀 BREAKOUT SCANNER"),
    ("swing", "📉 SWING ALERT"),
    ("fake", "⚠️ FAKE BREAKOUT"),
    ("telegram", "📲 SEND BEST TO TG"),
    ("broker", "🏦 BROKER SUMMARY"),
    ("community", "💬 KOMUNITAS"),
]

if st.session_state.active_panel is None:
    # ===================== HALAMAN: DASHBOARD =====================
    render_stock_search_bar("dashboard_search_form")
    render_top_panel()
    st.divider()

    st.markdown("#### Menu")
    menu_cols = st.columns(4)
    for i, (panel_key, panel_label) in enumerate(MENU_ITEMS):
        with menu_cols[i % 4]:
            if st.button(panel_label, use_container_width=True, key=f"menu_{panel_key}"):
                st.session_state.active_panel = panel_key
                st.rerun()

    st.divider()
    render_news_section()

else:
    # ===================== HALAMAN: FITUR =====================
    _panel = st.session_state.active_panel
    if st.button("⬅ Kembali", key="back_to_dashboard_btn"):
        st.session_state.active_panel = None
        st.rerun()
    st.divider()

    # ---- SCAN MARKET : tabel penuh, semua saham, semua metrik ----
    if _panel == "scan":
        df_scan = ensure_scanned()
        st.subheader("📊 Hasil Scan Penuh")
        df_display = df_scan.copy()
        # PERBAIKAN: urutan kolom dipaksa eksplisit di sini -- data hasil
        # scan disimpan sebagai jsonb di Supabase, dan Postgres TIDAK
        # menjaga urutan key asli tiap kali disimpan (bisa teracak beda
        # tiap kali worker nulis ulang), jadi kalau dibiarkan ikut urutan
        # dari database, kolom "stock" bisa nongol di tengah-tengah tabel.
        _scan_col_order = [
            "stock", "price", "change_pct", "week_change_pct", "score",
            "signal", "trend", "entry", "tp", "sl", "swing", "drop",
            "bandar", "fake_breakout",
        ]
        _cols_present = [c for c in _scan_col_order if c in df_display.columns]
        _cols_leftover = [c for c in df_display.columns if c not in _cols_present]
        df_display = df_display[_cols_present + _cols_leftover]
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
    elif _panel == "bandar":
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
    elif _panel == "breakout":
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
    elif _panel == "swing":
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
    elif _panel == "fake":
        df_scan = ensure_scanned()
        st.subheader("⚠️ Fake Breakout Warning")
        fake_hits = df_scan[df_scan["fake_breakout"] == True]
        if fake_hits.empty:
            st.info("Tidak ada fake breakout terdeteksi saat ini.")
        else:
            df_display = fake_hits[["stock", "price", "trend", "signal"]].copy()
            df_display["stock"] = df_display["stock"].apply(_display_ticker)
            render_html_table(df_display)

    # ---- SEND BEST TO TELEGRAM : sekarang halaman sendiri dengan tombol
    # konfirmasi terpisah, biar gak kekirim gak sengaja. ----
    elif _panel == "telegram":
        st.subheader("📲 Kirim Best Pick ke Telegram")
        df_scan = ensure_scanned()
        if df_scan.empty:
            st.warning("Belum ada data untuk dikirim.")
        else:
            best = df_scan.iloc[0]
            st.markdown(
                f'<div class="card"><b>BEST PICK:</b> {_display_ticker(best["stock"])} — '
                f'{signal_badge(best["signal"])} @ {best["price"]}</div>',
                unsafe_allow_html=True,
            )
            if st.button("📲 Kirim Sekarang", key="send_telegram_confirm_btn"):
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
                    st.error("Gagal kirim ke Telegram — coba lagi sebentar lagi.")

    # ---- BROKER SUMMARY : kode broker + volume beli/jual per saham,
    # khusus data EOD (update setelah bursa tutup). ----
    elif _panel == "broker":
        st.subheader("🏦 Broker Summary (Data EOD)")

        _goapi_configured = bool(GOAPI_API_KEY) and GOAPI_API_KEY != "ISI_API_KEY_GOAPI_LO"

        if not _goapi_configured:
            # ---- LOCKED / COMING SOON ----
            # Fitur ini butuh langganan data premium. Ditahan dulu sampai
            # ada user berlangganan Pro di app ini, biar biayanya
            # ke-cover -- bukan nombok duluan. Begitu API key diisi di
            # Secrets, panel ini otomatis berubah jadi fungsional tanpa
            # perlu ubah kode apa pun lagi.
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
                    <span class="badge badge-neutral">🔒 PREMIUM · SEGERA HADIR</span>
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

    # ---- KOMUNITAS : community feed (post, reaction, laporan spam) + bell
    # notif sudah dipasang di header. Panel ini hanya untuk user aktif/owner
    # (sudah dijamin karena st.stop() di atas kalau bukan owner/active). ----
    elif _panel == "community":
        st.subheader("💬 Komunitas SahamPro")
        if not supabase_client:
            st.warning(
                "Community Feed belum aktif — SUPABASE_URL / SUPABASE_SERVICE_KEY "
                "belum diisi di Secrets. Isi dulu supaya fitur ini jalan."
            )
        else:
            render_community_feed(supabase_client, identifier, display_name)

    # ---- PRIVASI AKUN : dibuka dari dropdown nama profil di header,
    # bukan dari menu utama. Baru berisi info dasar akun -- kontrol
    # privasi lebih detail (export data, hapus akun, dst) bisa
    # ditambahkan di sini nanti tanpa ubah struktur navigasi. ----
    elif _panel == "privacy":
        st.subheader("🔒 Privasi Akun")
        st.markdown(
            f'<div class="card">'
            f'<b>Username:</b> {display_name}<br>'
            f'<b>Status akun:</b> {"Owner" if status == "owner" else ("Aktif berlangganan" if status == "active" else "Belum berlangganan")}<br>'
            f'<b>Sesi login:</b> tersimpan aman di cookie terenkripsi di perangkat ini, '
            f'berlaku {SESSION_DURATION_DAYS} hari sejak login terakhir.'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.info(
            "Kontrol privasi lain (mis. unduh/hapus data akun) belum tersedia di "
            "halaman ini — akan ditambahkan menyusul. Untuk sekarang, gunakan "
            "tombol Logout di menu profil kalau ingin mengakhiri sesi login di "
            "perangkat ini."
        )

    # ---- Tombol kembali kedua di bawah, biar gak perlu scroll ke atas
    # lagi di halaman yang tabelnya panjang. ----
    st.divider()
    if st.button("⬅ Kembali", key="back_to_dashboard_btn_bottom"):
        st.session_state.active_panel = None
        st.rerun()


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
#   stripe.checkout.Session.create(
#       ...,
#       client_reference_id=identifier,
#       # WAJIB juga diisi metadata "plan" sesuai tombol paket yang diklik
#       # user, salah satu dari: "bulanan", "3_bulanan", "tahunan" -- ini
#       # yang dipakai activate_subscription() buat hitung tanggal berakhir.
#       metadata={"plan": "bulanan"},
#   )
def stripe_webhook(event):
    user_db = load_user_db()
    if event["type"] == "checkout.session.completed":
        obj = event["data"]["object"]
        user_key = obj.get("client_reference_id")
        plan = (obj.get("metadata") or {}).get("plan", "bulanan")
        if user_key:
            activate_subscription(user_db, user_key, plan)
    elif event["type"] == "invoice.payment_failed":
        user_key = event["data"]["object"].get("client_reference_id")
        if user_key:
            existing = user_db.get(user_key, {})
            existing["status"] = "inactive"
            user_db[user_key] = existing
            save_user_db(user_db)
