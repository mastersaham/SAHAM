"""
scan_engine.py
==============
"Mesin" penghitung sinyal saham (support/resistance, scoring, deteksi
bandar, dll) dipisah dari file app utama supaya bisa dipakai bareng oleh
DUA pihak:

1. App utama (ai_idx_trading_terminal.py) -- kalau suatu saat mau scan
   manual sebagai cadangan.
2. Petugas scan terpusat (scan_worker.py) -- dijalankan berkala lewat
   GitHub Actions, HASILNYA disimpan ke Supabase, lalu app cuma baca
   hasil itu (tidak scan sendiri tiap user buka app).

File ini SENGAJA tidak bergantung ke Streamlit (tidak ada `import
streamlit`), supaya bisa dijalankan sebagai script biasa oleh GitHub
Actions tanpa perlu server Streamlit menyala.
"""

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf
import pandas as pd

# Kata kunci untuk menilai sentimen berita (pendekatan berbasis kata
# kunci, bukan AI/NLP model -- masih bisa kurang akurat untuk kalimat
# ambigu/sarkasme, tapi cukup untuk indikasi kasar).
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


# Kata kunci untuk berita umum ekonomi/kebijakan Indonesia (tidak terikat
# 1 saham) -- termasuk pidato/kebijakan Presiden Prabowo karena sering
# jadi pemicu sentimen pasar luas.
GENERAL_NEWS_QUERIES = [
    "IHSG",
    "Prabowo ekonomi",
    "Prabowo pidato",
    "kebijakan ekonomi Indonesia",
    "Bank Indonesia suku bunga",
    "rupiah",
]

# ------------------------------------------------------------
# Daftar saham ISSI fallback (dipakai kalau situs IDX tidak bisa
# diakses / rate-limit -- umum terjadi dari shared cloud IP).
# ------------------------------------------------------------
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


def get_all_idx_stocks():
    """Coba ambil daftar SEMUA saham yang listing di IDX (bukan cuma
    ISSI/syariah) -- endpoint & pola request PERSIS sama dengan
    get_issi_stocks() di atas (sudah terbukti jalan), cuma indexCode-nya
    diganti ke 'IHSG' (Indeks Harga Saham Gabungan = indeks komposit
    yang isinya representasi SEMUA saham yang listing di bursa).
    Kalau gagal / IDX block, fallback ke ISSI_FALLBACK_STOCKS saja
    (artinya utk run itu saham non-syariah sementara tidak ke-scan,
    tapi tidak error/crash)."""
    try:
        resp = requests.get(
            "https://www.idx.co.id/umbraco/Surface/StockData/GetConstituent",
            params={"indexCode": "IHSG"},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://www.idx.co.id/id/data-pasar/ringkasan-perdagangan/ringkasan-saham/",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        codes = [row["Code"].strip() for row in data if row.get("Code")]
        tickers = sorted(set(f"{c}.JK" for c in codes if c))
        if len(tickers) >= 300:  # sanity check -- bursa IDX ada ~900 saham
            return tickers, True
    except Exception:
        pass
    return ISSI_FALLBACK_STOCKS, False


# ------------------------------------------------------------
# Indikator & scoring (identik dengan app utama)
# ------------------------------------------------------------

def support_resistance(df):
    support = df['Low'].rolling(20).min().iloc[-1]
    resistance = df['High'].rolling(20).max().iloc[-1]
    return support, resistance


def calc_ema(df, span):
    """Exponential Moving Average -- lebih responsif ke harga terbaru
    dibanding SMA biasa, karena bobot data baru lebih besar."""
    return df['Close'].ewm(span=span, adjust=False).mean()


def calc_rsi(df, period=14):
    """RSI (Relative Strength Index) 0-100, Wilder's smoothing. Kalau
    data belum cukup / avg_loss = 0, netral (50)."""
    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val.fillna(50)


def calc_macd(df, fast=12, slow=26, signal=9):
    """MACD standar. Return 3 Series: macd_line, signal_line, hist."""
    ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def calc_adx(df, period=14):
    """ADX (Average Directional Index) + DI/-DI (Wilder). Return
    (adx, plus_di, minus_di)."""
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
        / atr_val.replace(0, pd.NA)
    )
    minus_di = 100 * (
        minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        / atr_val.replace(0, pd.NA)
    )
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    adx_val = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return adx_val.fillna(0), plus_di.fillna(0), minus_di.fillna(0)


def calc_bollinger(df, period=20, num_std=2):
    """Bollinger Bands standar. Return (upper, mid, lower)."""
    mid = df['Close'].rolling(period).mean()
    std = df['Close'].rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def calc_vwap_rolling(df, period=20):
    """VWAP rolling N hari (pendekatan dari data harian, bukan VWAP
    intraday asli)."""
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    pv = typical_price * df['Volume']
    return pv.rolling(period).sum() / df['Volume'].rolling(period).sum()


def calc_fibonacci_levels(df, lookback=60):
    """Level Fibonacci retracement otomatis dari swing high & swing low
    N hari terakhir. Return dict {level: harga}, atau {} kalau data
    kurang / swing high=low."""
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


def trend_strength(df):
    """EMA20 vs EMA50 -- lebih responsif ke perubahan harga terbaru
    dibanding SMA biasa."""
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
    """% perubahan harga dari ~5 hari bursa lalu ke harga penutupan
    terakhir. Kalau data kurang dari 6 baris, pakai baris paling awal."""
    if len(df) < 2:
        return 0.0
    idx_back = min(5, len(df) - 1)
    past = df['Close'].iloc[-1 - idx_back]
    now = df['Close'].iloc[-1]
    if past == 0:
        return 0.0
    return (now - past) / past * 100


def scoring(df, support, resistance):
    """Scoring gabungan, total maksimal 100: kriteria klasik (support/
    breakout/volume/trend EMA) + indikator tambahan (RSI, MACD, ADX,
    Bollinger Bands, VWAP, Fibonacci). Tiap kriteria kasih poin KALAU
    kepenuhi -- kalau data belum cukup / kriteria gak kepenuhi, poinnya 0.
    Bobot: support 10, breakout 15, volume 10, trend EMA 15, RSI 10,
    MACD 10, ADX 10, Bollinger 10, VWAP 5, Fibonacci 5."""
    score = 0
    price = df['Close'].iloc[-1]

    if price <= support * 1.05:
        score += 10
    if breakout_valid(df, resistance):
        score += 15
    if volume_spike(df):
        score += 10
    if "Bullish" in trend_strength(df):
        score += 15

    try:
        rsi_val = calc_rsi(df).iloc[-1]
        if 50 < rsi_val <= 70:
            score += 10
        elif rsi_val < 30:
            score += 5
    except Exception:
        pass

    try:
        macd_line, signal_line, hist = calc_macd(df)
        if macd_line.iloc[-1] > signal_line.iloc[-1] and hist.iloc[-1] > 0:
            score += 10
    except Exception:
        pass

    try:
        adx_val, plus_di, minus_di = calc_adx(df)
        if adx_val.iloc[-1] >= 25 and plus_di.iloc[-1] > minus_di.iloc[-1]:
            score += 10
    except Exception:
        pass

    try:
        bb_upper, bb_mid, bb_lower = calc_bollinger(df)
        if price >= bb_upper.iloc[-1]:
            score += 10
        elif price <= bb_lower.iloc[-1] * 1.02:
            score += 5
    except Exception:
        pass

    try:
        vwap_val = calc_vwap_rolling(df).iloc[-1]
        if pd.notna(vwap_val) and price > vwap_val:
            score += 5
    except Exception:
        pass

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


# ------------------------------------------------------------
# Download data (dibagi per-batch supaya RAM tidak melonjak dan
# proses tidak mati / segmentation fault seperti sebelumnya)
# ------------------------------------------------------------

def get_all_data(stocks, batch_size: int = 60):
    stocks = list(stocks)
    data = {}
    for i in range(0, len(stocks), batch_size):
        batch = stocks[i:i + batch_size]
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
                df = raw if len(batch) == 1 else raw[stock]
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna(subset=["Close", "Open", "High", "Low", "Volume"])
                if len(df) >= 30:
                    data[stock] = df
            except Exception:
                continue

        del raw

    return data


def build_full_scan(stocks=None):
    """Jalan sekali, hitung semua metrik untuk semua saham. Dipanggil
    oleh scan_worker.py (petugas scan terpusat) tiap beberapa menit."""
    if stocks is None:
        stocks, _ = get_issi_stocks()

    results = []
    all_data = get_all_data(stocks)
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
                "swing": bool(swing),
                "drop": round(float(drop), 2),
                "bandar": bandar,
                "fake_breakout": bool(fake_break),
            })
        except Exception:
            continue

    df_result = pd.DataFrame(results)
    if not df_result.empty:
        df_result = df_result.sort_values(by="score", ascending=False).reset_index(drop=True)
    return df_result


# ============================================================
#  QUICK QUOTE -- harga + %perubahan harian saja, buat saham NON-ISSI
#  yang dipegang di portofolio siapapun (bukan bagian scan/skor).
# ============================================================

def get_quick_quotes(tickers, batch_size: int = 60):
    """Batch-download harga terakhir + %perubahan harian untuk daftar
    ticker (dipakai untuk saham non-ISSI yang ada di portofolio user).
    Return dict {ticker: {"price":.., "change_pct":..}}."""
    tickers = list(dict.fromkeys(tickers))  # dedupe, jaga urutan
    out = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            raw = yf.download(
                batch, period="10d", interval="1d", progress=False,
                group_by="ticker", threads=True, auto_adjust=True,
            )
        except Exception:
            continue
        for t in batch:
            try:
                df = raw if len(batch) == 1 else raw[t]
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna(subset=["Close"])
                if len(df) < 2:
                    continue
                out[t] = {
                    "price": round(float(df['Close'].iloc[-1]), 2),
                    "change_pct": round(float(pct_change(df)), 2),
                }
            except Exception:
                continue
        del raw
    return out


# ============================================================
#  HISTORI HARGA HARIAN (panjang, dari IPO/semaksimal yang ada).
#  Rentang waktu lain (1mo/YTD/1th/3th/5th) di-derive di app dari
#  data ini (resample pandas), jadi cukup 1x fetch/saham/hari.
# ============================================================

def get_daily_history(ticker):
    """Return list of dict [{date, open, high, low, close, volume}, ...]
    urut TERLAMA -> TERBARU, atau None kalau gagal."""
    try:
        df = yf.Ticker(ticker).history(period="max", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                                 "Low": "low", "Close": "close", "Volume": "volume"})
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
        cols = ["date", "open", "high", "low", "close", "volume"]
        return df[cols].round({"open": 2, "high": 2, "low": 2, "close": 2}).to_dict(orient="records")
    except Exception:
        return None


# ============================================================
#  HISTORI INTRADAY (candle 5 menit, ~5 hari terakhir). Basis buat
#  tampilan "1 Hari" (ambil hari terakhir) dan "1 Minggu" (resample
#  ke 30 menit) di app.
# ============================================================

def get_intraday_history(ticker):
    """Return list of dict [{date, open, high, low, close, volume}, ...],
    atau None kalau gagal / tidak tersedia (saham ilikuid kadang tidak
    punya data intraday granular dari Yahoo)."""
    try:
        df = yf.Ticker(ticker).history(period="5d", interval="5m", auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        date_col = "Datetime" if "Datetime" in df.columns else "Date"
        df = df.rename(columns={date_col: "date", "Open": "open", "High": "high",
                                 "Low": "low", "Close": "close", "Volume": "volume"})
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%dT%H:%M:%S%z")
        cols = ["date", "open", "high", "low", "close", "volume"]
        return df[cols].round({"open": 2, "high": 2, "low": 2, "close": 2}).to_dict(orient="records")
    except Exception:
        return None


# ============================================================
#  FUNDAMENTAL -- sektor/industri/market cap/PER/EPS/mata uang.
# ============================================================

def get_fundamentals(ticker):
    fields = {
        "nama": None, "sektor": None, "industri": None,
        "market_cap": None, "per": None, "eps": None, "mata_uang": None,
    }
    try:
        info = yf.Ticker(ticker).info or {}
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


# ============================================================
#  BERITA (Google News RSS) -- per saham + umum IHSG.
# ============================================================

def fetch_news(ticker_label, query, max_items=8):
    """Ambil berita dari Google News RSS. `ticker_label` dipakai sebagai
    label penyimpanan (boleh 'GENERAL' buat berita umum). Sentimen
    dihitung SEKALI di sini (server), disimpan langsung ke Supabase --
    app tidak perlu hitung ulang tiap render. Return list of dict siap
    disimpan ke Supabase."""
    try:
        resp = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "id", "gl": "ID", "ceid": "ID:id"},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=10,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for item in root.findall(".//item")[:max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            desc = (item.findtext("description") or "").strip()
            source_el = item.find("source")
            source = source_el.text.strip() if source_el is not None and source_el.text else ""
            if not title or not link:
                continue
            sentiment, emoji = analyze_sentiment(f"{title} {desc}")
            items.append({
                "ticker": ticker_label,
                "title": title,
                "link": link,
                "description": desc,
                "pub_date": pub_date,
                "source": source,
                "sentiment": sentiment,
                "sentiment_emoji": emoji,
            })
        return items
    except Exception:
        return []


def fetch_general_news(max_items_per_query=5):
    """Berita umum ekonomi/IHSG -- gabungan beberapa kata kunci di
    GENERAL_NEWS_QUERIES, dedupe by link. Semua disimpan dengan
    ticker='GENERAL'."""
    seen_links = set()
    out = []
    for query in GENERAL_NEWS_QUERIES:
        for article in fetch_news("GENERAL", query, max_items_per_query):
            if article["link"] in seen_links:
                continue
            seen_links.add(article["link"])
            out.append(article)
    return out


# ============================================================
#  BROKER SUMMARY (GOAPI.IO) -- kode broker + volume beli/jual per
#  saham per tanggal. HANYA dipanggil oleh scan_worker.py (server),
#  bukan dari app -- jadi API key GOAPI TIDAK PERNAH ada di proses
#  yang diakses banyak user sekaligus.
# ============================================================

def fetch_broker_summary(symbol, date_str, api_key, endpoint_template):
    """Return (list_of_records, error_message)."""
    clean_symbol = symbol[:-3] if symbol.endswith(".JK") else symbol
    if not api_key:
        return [], "GOAPI_API_KEY belum diisi."
    try:
        resp = requests.get(
            endpoint_template.format(symbol=clean_symbol),
            params={"date": date_str},
            headers={"X-API-KEY": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not rows:
            return [], None
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
        return records, None
    except requests.exceptions.HTTPError as e:
        return [], f"GOAPI error: {e}"
    except Exception as e:
        return [], f"Gagal ambil data broker: {e}"


def last_trading_date():
    """'Tanggal bursa terakhir' yang datanya sudah pasti closing."""
    now = datetime.now()
    d = now.date()
    if now.weekday() < 5 and now.hour >= 16:
        pass
    else:
        d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d = d - timedelta(days=1)
    return d
