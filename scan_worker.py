"""
scan_worker.py
==============
"Petugas scan terpusat". Script ini TIDAK dijalankan oleh user, dan
TIDAK jalan di dalam app Streamlit -- ini dijalankan terpisah secara
berkala oleh GitHub Actions (lihat .github/workflows/scan.yml).

SEMUA data yang dulunya di-fetch LANGSUNG oleh app tiap user buka
halaman (histori harga, fundamental, berita, broker summary, quote
saham) sekarang di-fetch DI SINI dan disimpan ke Supabase. App
(ai_idx_trading_terminal.py) tidak pernah lagi manggil yfinance/Google
News/GOAPI langsung sama sekali -- cuma baca dari Supabase. Ini yang
nutup akar masalah segmentation fault di Streamlit Cloud: dulu tiap
user yang buka halaman detail saham/nambah portofolio memicu panggilan
yfinance (curl_cffi) sendiri-sendiri secara bersamaan di proses yang
sama.

Universe yang di-scan = SEMUA saham yang listing di IDX (bukan cuma
ISSI/syariah) -- lihat get_all_idx_stocks() di scan_engine.py. Skor &
sinyal (scan_results) TETAP cuma dihitung untuk saham ISSI (keputusan
produk: app sengaja tidak kasih rekomendasi untuk saham non-syariah),
tapi data dasar (harga/histori/fundamental/berita/broker) buat SEMUA
saham tersimpan di Supabase -- app cuma menampilkan yang ISSI, saham
non-ISSI datanya "ada tapi disembunyikan". Ini juga yang bikin validasi
"Tambah ke Portofolio" di app tidak perlu live call sama sekali lagi --
tinggal cek ke data yang sudah ada di Supabase.

Ada 2 kelompok tugas:
1. TIAP JALAN (tiap ~15 menit, jam bursa): scan_results (skor/sinyal
   ISSI), stock_intraday (candle 5 menit, SEMUA saham), stock_quotes
   (harga + %perubahan, SEMUA saham).
2. SEKALI SEHARI (dicek lewat tabel daily_job_state, supaya tidak
   diulang tiap 15 menit): stock_history (histori harian panjang),
   stock_fundamentals, stock_news, broker_summary -- SEMUA saham. Data-
   data ini emang jarang berubah / tidak butuh update tiap 15 menit.

Environment variables yang dibutuhkan (GitHub Actions Secrets):
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
    GOAPI_API_KEY   (opsional -- kalau kosong, broker_summary di-skip)
"""

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta

from supabase import create_client
import scan_engine as se

WIB = timezone(timedelta(hours=7))
GOAPI_BASE_URL = "https://api.goapi.io"
GOAPI_BROKER_ENDPOINT_TEMPLATE = GOAPI_BASE_URL + "/v1/stock/idx/{symbol}/broker_summary"


def _today_wib():
    return datetime.now(WIB).date()


def upsert_chunks(client, table, rows, on_conflict, chunk_size=200):
    """Upsert list of dict ke Supabase per-chunk, biar payload gak
    kegedean sekali kirim. Return jumlah baris yang berhasil dikirim."""
    sent = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        try:
            client.table(table).upsert(chunk, on_conflict=on_conflict).execute()
            sent += len(chunk)
        except Exception as e:
            print(f"  [WARNING] gagal upsert {len(chunk)} baris ke {table}: {e}")
    return sent


def should_run_daily_job(client, job_name):
    try:
        res = client.table("daily_job_state").select("last_run_date").eq("job_name", job_name).execute()
        if res.data:
            last_run = res.data[0]["last_run_date"]
            if str(last_run) == str(_today_wib()):
                return False
        return True
    except Exception as e:
        print(f"  [WARNING] gagal cek daily_job_state ({job_name}), asumsikan perlu jalan: {e}")
        return True


def mark_daily_job_done(client, job_name):
    try:
        client.table("daily_job_state").upsert(
            {"job_name": job_name, "last_run_date": str(_today_wib())},
            on_conflict="job_name",
        ).execute()
    except Exception as e:
        print(f"  [WARNING] gagal update daily_job_state ({job_name}): {e}")


def run_main_scan(client, issi_stocks):
    """Skor & sinyal ISSI -- jalan TIAP kali script ini dipanggil."""
    df = se.build_full_scan(issi_stocks)
    if df.empty:
        print("  PERINGATAN: hasil scan kosong (Yahoo Finance mungkin lagi "
              "bermasalah). Tidak menimpa data lama di Supabase.")
        return
    records = json.loads(df.to_json(orient="records"))
    payload = {
        "id": 1,
        "data": records,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    client.table("scan_results").upsert(payload).execute()
    print(f"  scan_results: {len(records)} saham OK.")


def run_intraday(client, universe, batch_size=60):
    """Candle 5 menit, ~5 hari terakhir -- basis grafik '1 Hari' & '1
    Minggu' di app. Jalan TIAP kali (biar tetap kerasa 'baru').

    Pakai get_intraday_batch() (1 request per 60 saham) bukan lagi
    get_intraday_history() satu-satu per saham -- jauh lebih cepat
    untuk universe ~900 saham. Trade-off: kalau 1 batch gagal total
    (network/Yahoo error), 60 saham di batch itu ikut ke-skip untuk
    run ini (bukan cuma 1 saham) -- tapi run berikutnya (15 menit
    lagi) akan coba lagi seperti biasa."""
    now_iso = datetime.now(timezone.utc).isoformat()
    data_by_ticker = se.get_intraday_batch(universe, batch_size=batch_size)
    rows = [
        {"ticker": ticker, "data": data, "updated_at": now_iso}
        for ticker, data in data_by_ticker.items()
    ]
    sent = upsert_chunks(client, "stock_intraday", rows, on_conflict="ticker")
    print(f"  stock_intraday: {sent}/{len(universe)} saham OK.")


def run_quotes(client, universe, batch_size=60):
    """Harga + %perubahan harian untuk SEMUA saham (ISSI maupun bukan)
    -- jalan TIAP kali. Untuk saham ISSI ini agak redundan dengan harga
    yang sudah ada di scan_results, tapi disimpan juga di sini biar
    app punya 1 sumber seragam buat validasi/tampilan portofolio,
    apapun jenis sahamnya."""
    quotes = se.get_quick_quotes(universe, batch_size=batch_size)
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [
        {"ticker": t, "price": v["price"], "change_pct": v["change_pct"], "updated_at": now_iso}
        for t, v in quotes.items()
    ]
    sent = upsert_chunks(client, "stock_quotes", rows, on_conflict="ticker")
    print(f"  stock_quotes: {sent}/{len(universe)} saham OK.")


def run_daily_history(client, universe, batch_size=60):
    """PERBAIKAN: dulu pakai get_daily_history() 1 request per saham
    (~900 saham berurutan) -- gampang kena rate-limit/block Yahoo dari
    IP shared GitHub Actions, dan gagalnya diam-diam (exception ketelan
    di scan_engine.py) sehingga tabel stock_history bisa kosong tanpa
    ada error yang keliatan di log. Sekarang pakai
    get_daily_history_batch() (1 request per 60 saham), sama pola
    dengan run_intraday()/run_quotes()."""
    now_iso = datetime.now(timezone.utc).isoformat()
    data_by_ticker = se.get_daily_history_batch(universe, batch_size=batch_size)
    rows = [
        {"ticker": ticker, "data": data, "updated_at": now_iso}
        for ticker, data in data_by_ticker.items()
    ]
    sent = upsert_chunks(client, "stock_history", rows, on_conflict="ticker")
    print(f"  stock_history: {sent}/{len(universe)} saham OK.")


def run_fundamentals(client, universe):
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []
    for i, ticker in enumerate(universe):
        f = se.get_fundamentals(ticker)
        rows.append({
            "ticker": ticker, "nama": f["nama"], "sektor": f["sektor"],
            "industri": f["industri"], "market_cap": f["market_cap"],
            "per": f["per"], "eps": f["eps"], "mata_uang": f["mata_uang"],
            "updated_at": now_iso,
        })
        if (i + 1) % 50 == 0:
            print(f"  stock_fundamentals: {i + 1}/{len(universe)} saham diproses...")
        time.sleep(0.1)
    sent = upsert_chunks(client, "stock_fundamentals", rows, on_conflict="ticker")
    print(f"  stock_fundamentals: {sent}/{len(universe)} saham OK.")


def run_news(client, universe):
    rows = []
    rows.extend(se.fetch_general_news(max_items_per_query=5))
    for i, ticker in enumerate(universe):
        kode = ticker.replace(".JK", "")
        items = se.fetch_news(ticker, f"{kode} saham", max_items=8)
        rows.extend(items)
        if (i + 1) % 50 == 0:
            print(f"  stock_news: {i + 1}/{len(universe)} saham diproses...")
        time.sleep(0.1)
    # PERBAIKAN: dulu on_conflict cuma "link" -- kalau ada artikel yang
    # linknya sama persis muncul di hasil pencarian umum (GENERAL) DAN
    # di hasil pencarian saham tertentu (kebetulan artikelnya nyebut
    # saham itu), baris GENERAL bakal ke-TIMPA jadi ticker saham
    # tersebut (karena diproses belakangan). Ini penyebab tabel
    # stock_news isinya per-saham doang, GENERAL selalu kosong. Sekarang
    # kunci konfliknya ticker+link, jadi 1 artikel bisa nempel ke GENERAL
    # dan ke saham tertentu tanpa saling timpa. BUTUH constraint unique
    # (ticker, link) di tabel Supabase -- lihat catatan migrasi SQL.
    sent = upsert_chunks(client, "stock_news", rows, on_conflict="ticker,link")
    print(f"  stock_news: {sent}/{len(rows)} artikel OK.")


def run_broker_summary(client, universe, goapi_api_key):
    if not goapi_api_key:
        print("  broker_summary: GOAPI_API_KEY kosong, skip.")
        return
    date_str = se.last_trading_date().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []
    for i, ticker in enumerate(universe):
        records, err = se.fetch_broker_summary(ticker, date_str, goapi_api_key, GOAPI_BROKER_ENDPOINT_TEMPLATE)
        rows.append({
            "ticker": ticker, "date": date_str, "data": records,
            "error_message": err, "updated_at": now_iso,
        })
        if (i + 1) % 50 == 0:
            print(f"  broker_summary: {i + 1}/{len(universe)} saham diproses...")
        time.sleep(0.15)
    sent = upsert_chunks(client, "broker_summary", rows, on_conflict="ticker,date")
    print(f"  broker_summary ({date_str}): {sent}/{len(universe)} saham OK.")


def main():
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
    goapi_api_key = os.environ.get("GOAPI_API_KEY", "")

    if not supabase_url or not supabase_key:
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY belum diisi di environment.")
        sys.exit(1)

    client = create_client(supabase_url, supabase_key)

    issi_stocks, issi_is_live = se.get_issi_stocks()
    print(f"Daftar ISSI: {len(issi_stocks)} saham (sumber: "
          f"{'live IDX' if issi_is_live else 'fallback statis'}).")

    full_universe, all_is_live = se.get_all_idx_stocks()
    print(f"Daftar SEMUA saham IDX: {len(full_universe)} saham (sumber: "
          f"{'live IDX' if all_is_live else 'fallback statis (cuma ISSI)'}).")

    print("=== [1/3] Scan skor & sinyal (ISSI saja) ===")
    run_main_scan(client, issi_stocks)

    print("=== [2/3] Tugas tiap-jalan (intraday + quote, SEMUA saham) ===")
    run_intraday(client, full_universe)
    run_quotes(client, full_universe)

    print("=== [3/3] Tugas harian (history/fundamental/berita/broker, SEMUA saham) ===")
    if should_run_daily_job(client, "daily_tasks"):
        run_daily_history(client, full_universe)
        run_fundamentals(client, full_universe)
        run_news(client, full_universe)
        run_broker_summary(client, full_universe, goapi_api_key)
        mark_daily_job_done(client, "daily_tasks")
        print("Tugas harian selesai & ditandai selesai untuk hari ini.")
    else:
        print("Tugas harian sudah jalan hari ini, skip.")

    print("Selesai semua.")


if __name__ == "__main__":
    main()
