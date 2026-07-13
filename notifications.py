"""
notifications.py (PATCHED)
=================
Komponen notifikasi untuk SahamPro Community Feed.
Nampilin bell icon + jumlah notif belum dibaca, dan list notifikasi
(reaction like/unlike ke post user).

Cara pakai di app utama, biasanya taruh di sidebar atau header:

    from notifications import render_notification_bell
    render_notification_bell(supabase, user_id=st.session_state["user_id"])

===========================================================================
CATATAN PERBAIKAN:

1. get_unread_count() dipanggil di HEADER, yang dirender di SEMUA
   halaman app utama. Streamlit me-rerun seluruh script tiap ada
   interaksi APA PUN (klik tombol, ganti tab, dst) -- jadi tanpa cache,
   fungsi ini menembak Supabase secara LIVE di setiap klik di
   MANA PUN di seluruh app, bukan cuma pas lagi buka Community Feed.
   Kalau Supabase lagi lambat/timeout, SELURUH app ikut kerasa
   'nyangkut' padahal user cuma klik menu lain yang gak ada
   hubungannya sama notifikasi. Sekarang di-cache 15 detik
   (st.cache_data) -- badge tetap kerasa 'real-time' buat manusia,
   tapi gak lagi query ke DB di SETIAP rerun.

2. Semua query dibungkus try/except. Kalau Supabase gagal/timeout,
   bell tetap tampil (badge dianggap 0 / list kosong dengan pesan),
   bukan melempar exception yang bisa mengcrash header -> seluruh
   halaman ikut gagal render.
===========================================================================
"""

import streamlit as st
from datetime import datetime, timezone

NTYPE_LABELS = {
    "reaction_add": "bereaksi ke post kamu",
    "reaction_remove": "membatalkan reaksi ke post kamu",
}

# PERBAIKAN (performa): cache badge count 15 detik supaya tidak query
# Supabase di SETIAP rerun script (yang di Streamlit terjadi di HAMPIR
# setiap interaksi, di halaman manapun). 15 detik cukup terasa
# "real-time" untuk badge notifikasi, tapi drastis mengurangi beban ke
# Supabase dan menghindari app kerasa "nyangkut" gara-gara bell ini.
@st.cache_data(ttl=15, show_spinner=False)
def get_unread_count(_supabase, user_id: str) -> int:
    """Query ringan cuma buat badge angka - aman dipanggil tiap beberapa detik.

    PERBAIKAN: parameter dinamai `_supabase` (pakai underscore) supaya
    Streamlit TIDAK mencoba hash objek Supabase client ini untuk key
    cache. Tanpa underscore, st.cache_data akan coba hash object client
    yang tidak bisa di-hash dan melempar `UnhashableParamError` --
    artinya bell notifikasi (dan seluruh header, karena dirender di
    semua halaman) langsung crash begitu dibuka."""
    try:
        res = _supabase.table("notifications").select(
            "id", count="exact"
        ).eq("recipient_id", user_id).eq("is_read", False).execute()
        return res.count or 0
    except Exception:
        # Supabase lagi bermasalah -> anggap 0 notif baru, jangan sampai
        # bikin header (dan seluruh halaman) gagal render.
        return 0


def mark_all_read(supabase, user_id: str):
    try:
        supabase.table("notifications").update({"is_read": True}).eq(
            "recipient_id", user_id).eq("is_read", False).execute()
    except Exception as e:
        st.error(f"Gagal menandai notifikasi: {e}")
    # Cache get_unread_count sudah usang begitu status is_read berubah --
    # bersihkan supaya badge langsung update, bukan nunggu TTL 15 detik.
    get_unread_count.clear()


def render_notification_bell(supabase, user_id: str, limit: int = 20):
    if supabase is None:
        return

    unread = get_unread_count(supabase, user_id)

    # PERBAIKAN (tampilan): dulu badge jumlah notif ditulis nempel di
    # label tombol ("🔔 (3)"), beda gaya sama ikon-ikon lain di bottom
    # nav bar (yang polos, cuma SVG). Sekarang label tombolnya cuma
    # "🔔" polos, dan jumlah notif ditampilkan sebagai badge bulat
    # merah kecil yang di-overlay di pojok kanan-atas ikon lewat CSS
    # -- konsisten dengan gaya badge notifikasi di app pada umumnya.
    with st.container(key="notif_bell_wrap"):
        with st.popover("🔔", help="Notifikasi"):
            st.markdown("**Notifikasi**")

            if unread:
                if st.button("Tandai semua sudah dibaca", key="mark_all_read"):
                    mark_all_read(supabase, user_id)
                    st.rerun()

            try:
                res = supabase.table("notifications").select("*").eq(
                    "recipient_id", user_id
                ).order("created_at", desc=True).limit(limit).execute()
                notifs = res.data or []
            except Exception as e:
                st.caption(f"Gagal memuat notifikasi: {e}")
                notifs = None

            if notifs is not None:
                if not notifs:
                    st.caption("Belum ada notifikasi.")
                else:
                    for n in notifs:
                        emoji = n.get("emoji") or ""
                        label = NTYPE_LABELS.get(n.get("type"), n.get("type", ""))
                        prefix = "🔵 " if not n.get("is_read") else ""
                        actor = n.get("actor_username", "Seseorang")
                        st.markdown(f"{prefix}**{actor}** {emoji} {label}")
                        st.caption(_time_ago(n.get("created_at", "")))
                        st.divider()

        if unread:
            badge_text = "9+" if unread > 9 else str(unread)
            st.markdown(
                f'<div class="notif-badge-dot">{badge_text}</div>',
                unsafe_allow_html=True,
            )

    # CSS ikon bell + badge -- diinjeksi di sini (bukan di file app utama)
    # supaya notifications.py tetap "self-contained" dan portable, tidak
    # bergantung style harus ditaruh di tempat lain. st.markdown dgn
    # <style> yang sama dipanggil ulang tiap render itu aman/idempotent
    # di browser, cuma nambah elemen <style> duplikat yang efeknya nol.
    st.markdown(
        """
        <style>
        .st-key-notif_bell_wrap { position: relative; }
        .st-key-notif_bell_wrap button {
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            color: #ffb35c !important;
            font-size: 21px !important;
            height: 42px !important;
            min-height: 42px !important;
            padding: 0 !important;
        }
        .notif-badge-dot {
            position: absolute;
            top: 2px;
            right: 8px;
            background: #ff3b30;
            color: #fff;
            font-size: 10px;
            font-weight: 700;
            line-height: 1;
            min-width: 16px;
            height: 16px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 0 3px;
            pointer-events: none;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _time_ago(created_at_str: str) -> str:
    try:
        created = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        diff = (datetime.now(timezone.utc) - created).total_seconds()
        if diff < 60:
            return "baru saja"
        elif diff < 3600:
            return f"{int(diff // 60)} menit lalu"
        elif diff < 86400:
            return f"{int(diff // 3600)} jam lalu"
        return f"{int(diff // 86400)} hari lalu"
    except Exception:
        return ""


# ------------------------------------------------------------
# CATATAN INTEGRASI:
#
# 1. Taruh render_notification_bell() di bagian atas app (header/sidebar),
#    biar user selalu liat badge notif dari halaman manapun.
#
# 2. Untuk update badge count tanpa reload penuh, pasang st_autorefresh
#    dengan interval PENDEK (5-8 detik) TAPI HANYA untuk query
#    get_unread_count() -- ini query ringan (cuma count, bukan select *).
#    Jangan pasang autorefresh cepat di render_community_feed() penuh,
#    karena itu query lebih berat (ambil semua post + reactions).
#
# 3. Kalau mau lebih hemat resource lagi (rekomendasi buat awal-awal
#    user masih sedikit): skip autorefresh sama sekali, biarkan badge
#    keupdate saat user pindah halaman / interaksi apapun yang trigger
#    rerun Streamlit natural (submit form, klik tombol, dst).
# ------------------------------------------------------------
