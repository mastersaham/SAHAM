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

import requests
import yfinance as yf
import pandas as pd
from datetime import timedelta

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


# ------------------------------------------------------------
# Indikator & scoring (identik dengan app utama)
# ------------------------------------------------------------

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
