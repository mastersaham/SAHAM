"""
community_feed.py (FINAL)
==========================
Community Feed untuk SahamPro dengan:
- Posting text (max 200 karakter) + kategori + gambar opsional
- Reaction emoji (bukan like biasa)
- Trending section (3 post teratas, reaction terbanyak dalam 4 jam terakhir)
- Countdown "hilang dalam X jam" (post auto-hapus tiap 24 jam via pg_cron)
- Report spam

Cara pakai di app utama:

    from community_feed import render_community_feed
    render_community_feed(supabase, user_id, username)

Autorefresh (opsional, taruh di halaman feed ini saja):

    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=20000, key="feed_refresh")   # 20 detik
"""

import streamlit as st
from datetime import datetime, timezone, timedelta
import uuid

MAX_CHARS = 200
TRENDING_WINDOW_HOURS = 4
TRENDING_MIN_REACTIONS = 3
POST_LIFETIME_HOURS = 24

CATEGORY_LABELS = {
    "umum": "💬 Umum",
    "hasil_scan": "🎯 Hasil Scan",
    "profit": "💰 Profit",
    "analisis": "📊 Analisis",
}

REACTIONS = {
    "like": "👍",
    "love": "❤️",
    "fire": "🔥",
    "laugh": "😂",
    "wow": "😮",
}

REPORT_REASONS = {
    "spam": "Spam / Iklan",
    "sara": "SARA / Ujaran Kebencian",
    "judi": "Promosi Judi",
    "penipuan": "Penipuan",
    "lainnya": "Lainnya",
}

RANK_BADGES = {
    0: ("🥇 Peringkat 1", "#FAEEDA", "#412402"),
    1: ("🥈 Peringkat 2", "#F1EFE8", "#2C2C2A"),
    2: ("🥉 Peringkat 3", "#FAECE7", "#4A1B0C"),
}


# ---------------------------------------------------------------
# Helpers waktu
# ---------------------------------------------------------------

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


def _time_left(created_at_str: str) -> str:
    """Hitung sisa waktu sebelum post kehapus (post hidup 24 jam)."""
    try:
        created = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        expire_at = created + timedelta(hours=POST_LIFETIME_HOURS)
        remaining = (expire_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return "segera hilang"
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        if hours > 0:
            return f"hilang dalam {hours} jam"
        return f"hilang dalam {minutes} menit"
    except Exception:
        return ""


# ---------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------

def _upload_image(supabase, file):
    if file is None:
        return None
    try:
        ext = file.name.split(".")[-1]
        filename = f"{uuid.uuid4().hex}.{ext}"
        supabase.storage.from_("post-images").upload(
            filename, file.getvalue(), {"content-type": file.type}
        )
        return supabase.storage.from_("post-images").get_public_url(filename)
    except Exception as e:
        st.warning(f"Gagal upload gambar: {e}")
        return None


def _create_post(supabase, user_id, username, category, content, image_file=None):
    image_url = _upload_image(supabase, image_file) if image_file else None
    supabase.table("posts").insert({
        "user_id": user_id,
        "username": username,
        "category": category,
        "content": content,
        "image_url": image_url,
    }).execute()


def _get_reactions_summary(supabase, post_id: str) -> dict:
    res = supabase.table("post_reactions").select("emoji").eq("post_id", post_id).execute()
    summary = {}
    for row in (res.data or []):
        summary[row["emoji"]] = summary.get(row["emoji"], 0) + 1
    return summary


def _get_user_reaction(supabase, post_id: str, user_id: str):
    res = supabase.table("post_reactions").select("emoji").eq(
        "post_id", post_id).eq("user_id", user_id).execute()
    return res.data[0]["emoji"] if res.data else None


def _notify(supabase, recipient_id, actor_id, actor_username, post_id, ntype, emoji=None):
    if recipient_id == actor_id:
        return
    supabase.table("notifications").insert({
        "recipient_id": recipient_id,
        "actor_id": actor_id,
        "actor_username": actor_username,
        "post_id": post_id,
        "type": ntype,
        "emoji": emoji,
    }).execute()


def _toggle_reaction(supabase, post, user_id, username, emoji_key):
    post_id = post["id"]
    post_owner = post["user_id"]
    current = _get_user_reaction(supabase, post_id, user_id)

    if current == emoji_key:
        supabase.table("post_reactions").delete().eq("post_id", post_id).eq(
            "user_id", user_id).execute()
        _notify(supabase, post_owner, user_id, username, post_id, "reaction_remove", emoji_key)
    elif current is not None:
        supabase.table("post_reactions").update({"emoji": emoji_key}).eq(
            "post_id", post_id).eq("user_id", user_id).execute()
        _notify(supabase, post_owner, user_id, username, post_id, "reaction_add", emoji_key)
    else:
        supabase.table("post_reactions").insert({
            "post_id": post_id, "user_id": user_id, "emoji": emoji_key
        }).execute()
        _notify(supabase, post_owner, user_id, username, post_id, "reaction_add", emoji_key)


def _submit_report(supabase, post_id, reporter_id, reason):
    try:
        supabase.table("post_reports").insert({
            "post_id": post_id, "reporter_id": reporter_id, "reason": reason,
        }).execute()
        return True
    except Exception:
        return False


def _get_trending_posts(supabase, limit: int = 3):
    """Post dengan reaction terbanyak dalam TRENDING_WINDOW_HOURS terakhir,
    minimal TRENDING_MIN_REACTIONS reaction."""
    since = (datetime.now(timezone.utc) - timedelta(hours=TRENDING_WINDOW_HOURS)).isoformat()

    recent_posts = supabase.table("posts").select("*").gte(
        "created_at", since
    ).execute().data or []

    if not recent_posts:
        return []

    scored = []
    for post in recent_posts:
        summary = _get_reactions_summary(supabase, post["id"])
        total = sum(summary.values())
        if total >= TRENDING_MIN_REACTIONS:
            scored.append((total, post))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]


# ---------------------------------------------------------------
# UI: render satu kartu post (dipakai di trending & feed biasa)
# ---------------------------------------------------------------

def _render_post_card(supabase, post, current_user_id, current_username, rank_badge=None):
    with st.container(key=f"post_{post['id']}_{rank_badge or 'feed'}", border=True):
        col1, col2, col3 = st.columns([5, 1, 1])
        with col1:
            header = f"**{post['username']}** · {CATEGORY_LABELS.get(post['category'], '💬')}"
            st.markdown(header)
            st.caption(f"{_time_ago(post['created_at'])} · {_time_left(post['created_at'])}")
        with col2:
            if post["user_id"] == current_user_id:
                if st.button("🗑️", key=f"del_{post['id']}_{rank_badge or 'feed'}", help="Hapus post"):
                    supabase.table("posts").delete().eq("id", post["id"]).execute()
                    st.rerun()
        with col3:
            with st.popover("🚩", help="Laporkan post"):
                st.caption("Kenapa post ini dilaporkan?")
                reason = st.selectbox(
                    "Alasan", options=list(REPORT_REASONS.keys()),
                    format_func=lambda x: REPORT_REASONS[x],
                    key=f"reason_{post['id']}_{rank_badge or 'feed'}",
                    label_visibility="collapsed",
                )
                if st.button("Kirim laporan", key=f"report_btn_{post['id']}_{rank_badge or 'feed'}"):
                    ok = _submit_report(supabase, post["id"], current_user_id, reason)
                    if ok:
                        st.success("Laporan terkirim, makasih!")
                    else:
                        st.info("Kamu sudah pernah melaporkan post ini.")

        if rank_badge is not None:
            label, bg, fg = RANK_BADGES[rank_badge]
            st.markdown(
                f"<span style='background:{bg}; color:{fg}; font-size:12px; "
                f"font-weight:600; padding:2px 10px; border-radius:999px;'>{label}</span>",
                unsafe_allow_html=True,
            )

        st.write(post["content"])
        if post.get("image_url"):
            st.image(post["image_url"], use_container_width=True)

        summary = _get_reactions_summary(supabase, post["id"])
        user_reaction = _get_user_reaction(supabase, post["id"], current_user_id)

        reaction_cols = st.columns(len(REACTIONS))
        for i, (key, emoji) in enumerate(REACTIONS.items()):
            count = summary.get(key, 0)
            label = f"{emoji} {count}" if count else emoji
            is_active = user_reaction == key
            with reaction_cols[i]:
                btn_type = "primary" if is_active else "secondary"
                if st.button(label, key=f"react_{key}_{post['id']}_{rank_badge or 'feed'}", type=btn_type):
                    _toggle_reaction(supabase, post, current_user_id, current_username, key)
                    st.rerun()


# ---------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------

def render_community_feed(supabase, current_user_id: str, current_username: str, limit: int = 30):

    # ---------- TRENDING SECTION (paling atas) ----------
    trending = _get_trending_posts(supabase)
    if trending:
        st.markdown("### 🔥 Sedang Rame")
        st.caption(f"Reaction terbanyak {TRENDING_WINDOW_HOURS} jam terakhir")
        for i, post in enumerate(trending):
            _render_post_card(supabase, post, current_user_id, current_username, rank_badge=i)
        st.divider()

    # ---------- FORM POSTING ----------
    st.markdown("### 🌟 Community Feed")
    st.caption(f"Sharing hasil scan, profit, atau analisis kamu (maks {MAX_CHARS} karakter)")

    with st.container(key="feed_post_form"):
        with st.form("new_post_form", clear_on_submit=True):
            category = st.selectbox(
                "Kategori", options=list(CATEGORY_LABELS.keys()),
                format_func=lambda x: CATEGORY_LABELS[x],
            )
            content = st.text_area(
                "Apa yang mau kamu share?",
                placeholder="Contoh: Profit +8.4% dari BBCA & TLKM hari ini 🚀",
                max_chars=MAX_CHARS,
            )
            image_file = st.file_uploader("Screenshot (opsional)", type=["png", "jpg", "jpeg"])
            submitted = st.form_submit_button("Posting", use_container_width=True)

            if submitted:
                if not content.strip():
                    st.warning("Isi dulu tulisannya bro.")
                else:
                    _create_post(supabase, current_user_id, current_username,
                                  category, content.strip(), image_file)
                    st.success("Berhasil posting! Post ini akan hilang otomatis dalam 24 jam.")
                    st.rerun()

    st.divider()

    # ---------- FILTER & FEED ----------
    filter_category = st.radio(
        "Filter", options=["semua"] + list(CATEGORY_LABELS.keys()),
        format_func=lambda x: "🔍 Semua" if x == "semua" else CATEGORY_LABELS[x],
        horizontal=True,
    )

    query = supabase.table("posts").select("*").order("created_at", desc=True).limit(limit)
    if filter_category != "semua":
        query = query.eq("category", filter_category)
    posts = query.execute().data or []

    if not posts:
        st.info("Belum ada post. Jadilah yang pertama sharing! 🎉")
        return

    for post in posts:
        _render_post_card(supabase, post, current_user_id, current_username, rank_badge=None)
