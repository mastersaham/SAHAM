"""
notifications.py
=================
Komponen notifikasi untuk SahamPro Community Feed.
Nampilin bell icon + jumlah notif belum dibaca, dan list notifikasi
(reaction like/unlike ke post user).

Cara pakai di app utama, biasanya taruh di sidebar atau header:

    from notifications import render_notification_bell
    render_notification_bell(supabase, user_id=st.session_state["user_id"])
"""

import streamlit as st
from datetime import datetime, timezone

NTYPE_LABELS = {
    "reaction_add": "bereaksi ke post kamu",
    "reaction_remove": "membatalkan reaksi ke post kamu",
}


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


def get_unread_count(supabase, user_id: str) -> int:
    """Query ringan cuma buat badge angka - aman dipanggil tiap beberapa detik."""
    res = supabase.table("notifications").select(
        "id", count="exact"
    ).eq("recipient_id", user_id).eq("is_read", False).execute()
    return res.count or 0


def mark_all_read(supabase, user_id: str):
    supabase.table("notifications").update({"is_read": True}).eq(
        "recipient_id", user_id).eq("is_read", False).execute()


def render_notification_bell(supabase, user_id: str, limit: int = 20):
    unread = get_unread_count(supabase, user_id)
    badge = f" ({unread})" if unread else ""

    with st.popover(f"🔔{badge}", help="Notifikasi"):
        st.markdown("**Notifikasi**")

        if unread:
            if st.button("Tandai semua sudah dibaca", key="mark_all_read"):
                mark_all_read(supabase, user_id)
                st.rerun()

        res = supabase.table("notifications").select("*").eq(
            "recipient_id", user_id
        ).order("created_at", desc=True).limit(limit).execute()
        notifs = res.data or []

        if not notifs:
            st.caption("Belum ada notifikasi.")
            return

        for n in notifs:
            emoji = n.get("emoji") or ""
            label = NTYPE_LABELS.get(n["type"], n["type"])
            prefix = "🔵 " if not n["is_read"] else ""
            st.markdown(f"{prefix}**{n['actor_username']}** {emoji} {label}")
            st.caption(_time_ago(n["created_at"]))
            st.divider()


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
