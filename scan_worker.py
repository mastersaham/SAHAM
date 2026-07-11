"""
scan_worker.py
==============
"Petugas scan terpusat". Script ini TIDAK dijalankan oleh user, dan
TIDAK jalan di dalam app Streamlit -- ini dijalankan terpisah secara
berkala oleh GitHub Actions (lihat .github/workflows/scan.yml), setiap
15 menit pada jam bursa.

Alurnya:
1. Scan semua saham ISSI (lewat scan_engine.build_full_scan)
2. Simpan hasilnya ke satu baris di tabel `scan_results` (Supabase)
3. App utama (ai_idx_trading_terminal.py) SELALU baca dari baris itu,
   tidak pernah scan sendiri -- jadi user buka app = instant, tidak
   perlu nunggu download+hitung ratusan saham.

Environment variables yang dibutuhkan (diisi lewat GitHub Actions
Secrets, BUKAN ditulis langsung di file ini):
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
"""

import os
import sys
import json
from datetime import datetime, timezone

from supabase import create_client
from scan_engine import build_full_scan, get_issi_stocks


def main():
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY belum diisi di environment.")
        sys.exit(1)

    client = create_client(supabase_url, supabase_key)

    stocks, is_live = get_issi_stocks()
    print(f"Scan dimulai untuk {len(stocks)} saham (sumber daftar: "
          f"{'live IDX' if is_live else 'fallback statis'})...")

    df = build_full_scan(stocks)

    if df.empty:
        print("PERINGATAN: hasil scan kosong (kemungkinan Yahoo Finance "
              "lagi bermasalah). Tidak menimpa data lama di Supabase.")
        sys.exit(0)

    records = json.loads(df.to_json(orient="records"))

    payload = {
        "id": 1,
        "data": records,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    client.table("scan_results").upsert(payload).execute()
    print(f"Selesai. {len(records)} saham berhasil di-scan dan disimpan ke Supabase.")


if __name__ == "__main__":
    main()
