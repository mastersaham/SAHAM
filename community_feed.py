"""
community_feed.py (FINAL, PATCHED v2)
==========================
Community Feed dengan:
- Posting text (max 200 karakter) + reaction emoji (bukan like biasa)
- Balas komentar per post (bukan upload foto lagi -- lihat CATATAN v2)
- Trending section (3 post teratas, reaction terbanyak dalam 4 jam terakhir)
- Countdown "hilang dalam X jam" (post auto-hapus tiap 24 jam via pg_cron)
- Report spam
- Badge "X postingan belum dibaca" buat bottom bar Komunitas di app utama

Cara pakai di app utama:

    from community_feed import (
        render_community_feed, get_unread_post_count, mark_community_seen,
    )
    render_community_feed(supabase, user_id, username)

    # Badge notif di bottom bar (dipanggil di luar panel Community):
    unread = get_unread_post_count(supabase, user_id)

Autorefresh (opsional, taruh di halaman feed ini saja):

    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=20000, key="feed_refresh")   # 20 detik

===========================================================================
CATATAN PERBAIKAN v1 (dokter kode, lihat masing-masing komentar "PERBAIKAN"):

1. BUG PALING FATAL (bikin app CRASH): key widget dulu pakai
   `rank_badge or 'feed'`. Di Python, `0 or 'feed'` == 'feed' karena 0
   dianggap False. Jadi post Peringkat 1 (rank_badge=0) di section
   trending dapat key PERSIS SAMA dengan post yang sama saat dia juga
   muncul lagi di feed biasa (rank_badge=None -> juga jadi 'feed').
   Streamlit langsung crash dengan error "duplicate widget key" begitu
   ada post yang lagi trending DAN masih ada di 30 post terbaru (hampir
   selalu kejadian). Sekarang diganti helper _key_suffix() yang cek
   `is not None`, bukan truthy/falsy.

2. BUG PERFORMA (bikin app "nyangkut"/berat): dulu tiap 1 post render
   2 query terpisah ke Supabase (reactions summary + reaksi user),
   dikali sampai ~30 post feed + 3 post trending + semua post yang
   dicek buat scoring trending = puluhan-ratusan roundtrip network
   BERURUTAN cuma buat render 1 halaman. Sekarang di-bulk jadi 1-2
   query pakai `.in_(post_id, [...])`, hasilnya di-mapping di Python.

3. Semua operasi tulis ke Supabase (create post, reaction, dst) di
   bungkus try/except supaya kalau Supabase lagi bermasalah, yang
   muncul pesan error yang jelas -- bukan halaman crash total.
===========================================================================
CATATAN PERUBAHAN v2 (request UI/UX terbaru):

1. Header "🌟 Community Feed" + kategori (pilih kategori pas posting,
   dan filter kategori di bawah tombol Posting) DIHAPUS -- form posting
   sekarang cuma isi tulisan doang, feed selalu tampilkan semua post
   (tanpa filter). `category` tetap disimpan ke DB (diisi "umum" secara
   diam-diam) cuma buat jaga-jaga kalau kolomnya NOT NULL di Supabase --
   tidak ditampilkan lagi di mana pun di UI.
2. Upload foto (screenshot) DIHAPUS TOTAL -- fungsi _upload_image() dan
   kolom image_url tidak dipakai lagi. Sebagai gantinya, post sekarang
   bisa dibalas lewat KOMENTAR (lihat _create_comment / _bulk_comments).
   Butuh tabel baru di Supabase, `post_comments`:
       id (uuid/serial, PK), post_id (FK -> posts.id),
       user_id (text), username (text), content (text),
       created_at (timestamptz, default now())
3. Badge notif "X postingan belum dibaca" di bottom bar Komunitas app
   utama butuh tabel baru `community_last_seen`:
       user_id (text, PRIMARY KEY), seen_at (timestamptz)
   Kalau salah satu tabel baru ini belum dibuat di Supabase, fitur
   terkait otomatis "mati aman" (try/except) -- TIDAK bikin app crash,
   cuma fitur itu aja yang tidak jalan (komentar gagal disimpan / badge
   unread selalu 0).
===========================================================================
"""

import streamlit as st
from datetime import datetime, timezone, timedelta

MAX_CHARS = 200
COMMENT_MAX_CHARS = 200
TRENDING_WINDOW_HOURS = 4
TRENDING_MIN_REACTIONS = 3
POST_LIFETIME_HOURS = 24

# CATATAN v2: kategori tidak lagi dipilih user / dipakai buat filter di
# UI (lihat "CATATAN PERUBAHAN v2" di atas). Dict ini dibiarkan ada
# cuma buat jaga-jaga (referensi kalau suatu saat mau diaktifkan lagi),
# TIDAK dipakai di render_community_feed / _render_post_card lagi.
CATEGORY_LABELS = {
    "umum": "💬 Umum",
    "hasil_scan": "🎯 Hasil Scan",
    "profit": "💰 Profit",
    "analisis": "📊 Analisis",
}
DEFAULT_CATEGORY = "umum"

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


def _key_suffix(rank_badge):
    """PERBAIKAN: dulu `rank_badge or 'feed'` -- salah kalau rank_badge==0
    (Peringkat 1), karena 0 falsy di Python jadi ikut jadi 'feed' juga,
    nabrak sama post yang sama pas dirender ulang di section feed biasa
    -> Streamlit crash (duplicate widget key). Sekarang cek `is not None`
    biar rank 0 tetap dapat suffix unik '0', bukan disamakan ke 'feed'."""
    return str(rank_badge) if rank_badge is not None else "feed"


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
# Data layer -- posts
# ---------------------------------------------------------------

def _create_post(supabase, user_id, username, content):
    # PERBAIKAN: dibungkus try/except -- kalau insert ke Supabase gagal
    # (RLS, koneksi putus, dst), user dapat pesan error yang jelas,
    # bukan halaman Community Feed crash total.
    # CATATAN v2: tidak ada lagi image_file / kategori pilihan user --
    # category diisi DEFAULT_CATEGORY diam-diam (jaga-jaga kolom NOT NULL).
    try:
        supabase.table("posts").insert({
            "user_id": user_id,
            "username": username,
            "category": DEFAULT_CATEGORY,
            "content": content,
        }).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def _bulk_reactions_summary(supabase, post_ids):
    """PERBAIKAN (performa): ambil reaction dari SEMUA post_ids dalam 1
    query saja (bukan 1 query per post), hasilnya di-mapping ke
    {post_id: {emoji: jumlah}} di Python. Ini yang paling berperan
    bikin Community Feed berat/'nyangkut' sebelumnya."""
    if not post_ids:
        return {}
    try:
        res = supabase.table("post_reactions").select("post_id, emoji").in_(
            "post_id", post_ids
        ).execute()
    except Exception:
        return {}
    summaries = {}
    for row in (res.data or []):
        pid = row["post_id"]
        bucket = summaries.setdefault(pid, {})
        bucket[row["emoji"]] = bucket.get(row["emoji"], 0) + 1
    return summaries


def _bulk_user_reactions(supabase, post_ids, user_id):
    """PERBAIKAN (performa): sama seperti di atas, tapi khusus reaksi milik
    user yang sedang login -- 1 query buat semua post_ids sekaligus."""
    if not post_ids:
        return {}
    try:
        res = supabase.table("post_reactions").select("post_id, emoji").in_(
            "post_id", post_ids
        ).eq("user_id", user_id).execute()
    except Exception:
        return {}
    return {row["post_id"]: row["emoji"] for row in (res.data or [])}


def _notify(supabase, recipient_id, actor_id, actor_username, post_id, ntype, emoji=None):
    if recipient_id == actor_id:
        return
    try:
        supabase.table("notifications").insert({
            "recipient_id": recipient_id,
            "actor_id": actor_id,
            "actor_username": actor_username,
            "post_id": post_id,
            "type": ntype,
            "emoji": emoji,
        }).execute()
    except Exception:
        # Gagal kirim notifikasi TIDAK boleh bikin reaction/post/komentar
        # gagal juga -- ini cuma fitur pelengkap, bukan fitur inti.
        pass


def _toggle_reaction(supabase, post, user_id, username, emoji_key):
    # PERBAIKAN: dibungkus try/except supaya klik reaction yang gagal
    # (misal koneksi Supabase putus di tengah) tidak mengcrash halaman.
    try:
        post_id = post["id"]
        post_owner = post["user_id"]
        res = supabase.table("post_reactions").select("emoji").eq(
            "post_id", post_id).eq("user_id", user_id).execute()
        current = res.data[0]["emoji"] if res.data else None

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
        return True
    except Exception as e:
        st.error(f"Gagal menyimpan reaksi: {e}")
        return False


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
    minimal TRENDING_MIN_REACTIONS reaction.

    PERBAIKAN (performa): dulu 1 query reaction PER POST recent (bisa
    puluhan query cuma buat scoring). Sekarang 1 query bulk untuk semua
    post_ids recent sekaligus."""
    since = (datetime.now(timezone.utc) - timedelta(hours=TRENDING_WINDOW_HOURS)).isoformat()

    try:
        recent_posts = supabase.table("posts").select("*").gte(
            "created_at", since
        ).execute().data or []
    except Exception:
        return []

    if not recent_posts:
        return []

    recent_ids = [p["id"] for p in recent_posts]
    summaries = _bulk_reactions_summary(supabase, recent_ids)

    scored = []
    for post in recent_posts:
        total = sum(summaries.get(post["id"], {}).values())
        if total >= TRENDING_MIN_REACTIONS:
            scored.append((total, post))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]


# ---------------------------------------------------------------
# Data layer -- komentar/balasan (v2, gantiin upload foto)
# ---------------------------------------------------------------

def _bulk_comments(supabase, post_ids):
    """Ambil semua komentar utk sekumpulan post_ids dalam 1 query (bukan
    1 query per post -- sama semangatnya kayak _bulk_reactions_summary),
    di-mapping ke {post_id: [komentar, ...]} urut LAMA -> BARU. Butuh
    tabel `post_comments` (lihat CATATAN PERUBAHAN v2 di docstring atas)
    -- kalau tabelnya belum ada, aman: return {} (post tampil tanpa
    komentar, bukan error)."""
    if not post_ids:
        return {}
    try:
        res = supabase.table("post_comments").select("*").in_(
            "post_id", post_ids
        ).order("created_at").execute()
    except Exception:
        return {}
    grouped = {}
    for row in (res.data or []):
        grouped.setdefault(row["post_id"], []).append(row)
    return grouped


def _create_comment(supabase, post, user_id, username, content):
    try:
        supabase.table("post_comments").insert({
            "post_id": post["id"],
            "user_id": user_id,
            "username": username,
            "content": content,
        }).execute()
        _notify(supabase, post["user_id"], user_id, username, post["id"], "comment")
        return True, None
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------
# Data layer -- badge "X postingan belum dibaca" (v2)
# ---------------------------------------------------------------

@st.cache_data(ttl=20, show_spinner=False)
def get_unread_post_count(_supabase, user_id):
    """Jumlah post dari USER LAIN yang dibuat setelah terakhir kali user
    ini buka panel Community (community_last_seen.seen_at) -- dipakai
    buat badge "X postingan belum dibaca" di bottom bar Komunitas app
    utama. Argumen client Supabase dikasih nama `_supabase` (bukan
    `supabase`) supaya TIDAK ikut di-hash Streamlit (client Supabase
    tidak hashable) -- konvensi @st.cache_data standar.

    Butuh tabel `community_last_seen` (lihat docstring atas). Kalau
    tabel belum ada / query gagal, aman -- return 0 (bar kuning polos
    tanpa badge, bukan error)."""
    try:
        seen_res = _supabase.table("community_last_seen").select("seen_at").eq(
            "user_id", user_id
        ).execute()
        seen_at = seen_res.data[0]["seen_at"] if seen_res.data else None

        query = _supabase.table("posts").select("id", count="exact").neq("user_id", user_id)
        if seen_at:
            query = query.gt("created_at", seen_at)
        res = query.execute()
        return res.count or 0
    except Exception:
        return 0


def mark_community_seen(supabase, user_id):
    """Tandai panel Community baru saja dibuka user ini -- dipanggil di
    awal render_community_feed() supaya badge notif unread ke-reset.
    Aman kalau tabel `community_last_seen` belum ada (try/except)."""
    try:
        supabase.table("community_last_seen").upsert({
            "user_id": user_id,
            "seen_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        pass
    # Cache count di atas jadi basi begitu seen_at berubah -- bersihkan
    # supaya rerun berikutnya baca angka baru, bukan cache 20 detik lama.
    get_unread_post_count.clear()


# ---------------------------------------------------------------
# UI: render satu kartu post (dipakai di trending & feed biasa)
# ---------------------------------------------------------------

# Sentinel biar bisa bedain "tidak dikasih nilai" vs "memang None"
# (None itu valid, artinya user belum kasih reaction apa pun).
_NOT_GIVEN = object()


def _render_post_card(
    supabase, post, current_user_id, current_username, rank_badge=None,
    reactions_summary=None, user_reaction=_NOT_GIVEN, comments=None,
):
    """
    reactions_summary / user_reaction / comments: kalau dikasih (dari
    bulk fetch di render_community_feed), dipakai langsung -- TIDAK
    query lagi ke Supabase. Kalau tidak dikasih (dipanggil dari tempat
    lain), fallback fetch sendiri per-post biar tetap kompatibel.
    """
    suffix = _key_suffix(rank_badge)
    with st.container(key=f"post_{post['id']}_{suffix}", border=True):
        col1, col2, col3 = st.columns([5, 1, 1])
        with col1:
            # CATATAN v2: tag kategori (dulu "💬 Umum" dst) sudah tidak
            # ditampilkan lagi -- pilihan kategori & filter-nya dihapus.
            st.markdown(f"**{post['username']}**")
            st.caption(f"{_time_ago(post['created_at'])} · {_time_left(post['created_at'])}")
        with col2:
            if post["user_id"] == current_user_id:
                if st.button("🗑️", key=f"del_{post['id']}_{suffix}", help="Hapus post"):
                    try:
                        supabase.table("posts").delete().eq("id", post["id"]).execute()
                    except Exception as e:
                        st.error(f"Gagal menghapus post: {e}")
                    st.rerun()
        with col3:
            with st.popover("🚩", help="Laporkan post"):
                st.caption("Kenapa post ini dilaporkan?")
                reason = st.selectbox(
                    "Alasan", options=list(REPORT_REASONS.keys()),
                    format_func=lambda x: REPORT_REASONS[x],
                    key=f"reason_{post['id']}_{suffix}",
                    label_visibility="collapsed",
                )
                if st.button("Kirim laporan", key=f"report_btn_{post['id']}_{suffix}"):
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
        # CATATAN v2: upload/tampilan foto (image_url) sudah dihapus total
        # -- post sekarang teks + reaction + komentar saja.

        if reactions_summary is None:
            summary = _bulk_reactions_summary(supabase, [post["id"]]).get(post["id"], {})
        else:
            summary = reactions_summary

        if user_reaction is _NOT_GIVEN:
            user_reaction_val = _bulk_user_reactions(
                supabase, [post["id"]], current_user_id
            ).get(post["id"])
        else:
            user_reaction_val = user_reaction

        reaction_cols = st.columns(len(REACTIONS))
        for i, (key, emoji) in enumerate(REACTIONS.items()):
            count = summary.get(key, 0)
            label = f"{emoji} {count}" if count else emoji
            is_active = user_reaction_val == key
            with reaction_cols[i]:
                btn_type = "primary" if is_active else "secondary"
                if st.button(label, key=f"react_{key}_{post['id']}_{suffix}", type=btn_type):
                    _toggle_reaction(supabase, post, current_user_id, current_username, key)
                    st.rerun()

        # ---- Komentar/balasan (v2, gantiin upload foto) ----
        comment_list = comments if comments is not None else _bulk_comments(
            supabase, [post["id"]]
        ).get(post["id"], [])

        if comment_list:
            with st.expander(f"💬 {len(comment_list)} balasan", expanded=False):
                for c in comment_list:
                    st.markdown(
                        f"**{c['username']}** "
                        f"<span style='color:#9ca3af; font-size:11px;'>· {_time_ago(c['created_at'])}</span>",
                        unsafe_allow_html=True,
                    )
                    st.caption(c["content"])
        else:
            st.caption("💬 Belum ada balasan")

        with st.form(f"reply_form_{post['id']}_{suffix}", clear_on_submit=True, border=False):
            rcol1, rcol2 = st.columns([5, 1])
            with rcol1:
                reply_text = st.text_input(
                    "Balas",
                    key=f"reply_input_{post['id']}_{suffix}",
                    label_visibility="collapsed",
                    placeholder="Tulis balasan...",
                    max_chars=COMMENT_MAX_CHARS,
                )
            with rcol2:
                reply_submitted = st.form_submit_button("Balas", use_container_width=True)
            if reply_submitted:
                if not reply_text.strip():
                    st.warning("Tulis dulu balasannya.")
                else:
                    ok, err = _create_comment(
                        supabase, post, current_user_id, current_username, reply_text.strip()
                    )
                    if ok:
                        st.rerun()
                    else:
                        st.error(f"Gagal membalas: {err}")


# ---------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------

def render_community_feed(supabase, current_user_id: str, current_username: str, limit: int = 30):
    # Tandai panel Community baru saja dibuka -- reset badge "X postingan
    # belum dibaca" di bottom bar (lihat get_unread_post_count).
    mark_community_seen(supabase, current_user_id)

    # ---------- TRENDING SECTION (paling atas) ----------
    trending = _get_trending_posts(supabase)
    if trending:
        st.markdown("### 🔥 Sedang Rame")
        st.caption(f"Reaction terbanyak {TRENDING_WINDOW_HOURS} jam terakhir")

        trending_ids = [p["id"] for p in trending]
        t_summaries = _bulk_reactions_summary(supabase, trending_ids)
        t_user_reactions = _bulk_user_reactions(supabase, trending_ids, current_user_id)
        t_comments = _bulk_comments(supabase, trending_ids)

        for i, post in enumerate(trending):
            _render_post_card(
                supabase, post, current_user_id, current_username, rank_badge=i,
                reactions_summary=t_summaries.get(post["id"], {}),
                user_reaction=t_user_reactions.get(post["id"]),
                comments=t_comments.get(post["id"], []),
            )
        st.divider()

    # ---------- FORM POSTING ----------
    # CATATAN v2: header "🌟 Community Feed" + pilihan kategori dihapus --
    # form sekarang langsung ke kotak tulisan, tanpa judul/kategori.
    with st.container(key="feed_post_form"):
        with st.form("new_post_form", clear_on_submit=True):
            content = st.text_area(
                "Apa yang mau kamu share?",
                placeholder="Contoh: Profit +8.4% dari BBCA & TLKM hari ini 🚀",
                max_chars=MAX_CHARS,
            )
            submitted = st.form_submit_button("Posting", use_container_width=True)

            if submitted:
                if not content.strip():
                    st.warning("Isi dulu tulisannya bro.")
                else:
                    ok, err = _create_post(
                        supabase, current_user_id, current_username, content.strip(),
                    )
                    if ok:
                        st.success("Berhasil posting! Post ini akan hilang otomatis dalam 24 jam.")
                        st.rerun()
                    else:
                        st.error(f"Gagal posting: {err}")

    st.divider()

    # ---------- FEED ----------
    # CATATAN v2: filter kategori di bawah tombol Posting dihapus --
    # feed selalu tampilkan semua post terbaru, tanpa filter.
    try:
        posts = supabase.table("posts").select("*").order(
            "created_at", desc=True
        ).limit(limit).execute().data or []
    except Exception as e:
        st.error(f"Gagal memuat feed: {e}")
        return

    if not posts:
        st.info("Belum ada post. Jadilah yang pertama sharing! 🎉")
        return

    post_ids = [p["id"] for p in posts]
    summaries = _bulk_reactions_summary(supabase, post_ids)
    user_reactions = _bulk_user_reactions(supabase, post_ids, current_user_id)
    comments_map = _bulk_comments(supabase, post_ids)

    for post in posts:
        _render_post_card(
            supabase, post, current_user_id, current_username, rank_badge=None,
            reactions_summary=summaries.get(post["id"], {}),
            user_reaction=user_reactions.get(post["id"]),
            comments=comments_map.get(post["id"], []),
        )
