"""
CRYSTAL Music Backend Server — FastAPI v3.1
- yt-dlp (YouTube Music, falling back to YouTube) for search + streaming
- Bird Feeder Camera endpoints (plug-and-play, no env vars needed)
- Local play-history based suggestions

NOTE — Spotify Web API and Last.fm integrations have been removed.
Both were unreliable in this deployment (expired/missing credentials caused
constant fallback-chain failures) and have been deleted rather than left
half-wired. The endpoints they powered (/related, and the "similar songs"
tier of /suggestions) now return an honest empty/placeholder result instead
of silently failing. See the "TODO: recommendation source" markers below
for exactly where a future integration plugs back in.
"""

import asyncio
import os
import re
import sqlite3
import threading
import time as _time
import uuid
from pathlib import Path
from typing import Optional
from fan_router import router as fan_router

import yt_dlp
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="CRYSTAL Music Backend", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fan_router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DB_PATH = os.path.join(os.path.dirname(__file__), "music.db")

# ── Audio cache ────────────────────────────────────────────────────────────────
CACHE_DIR = Path(os.getenv("CRYSTAL_CACHE_DIR", Path.home() / ".crystal_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_MAX_DURATION_S = 600
CACHE_MAX_SIZE_GB = float(os.getenv("CRYSTAL_CACHE_MAX_GB", "2"))
_cache_lock = threading.Lock()
_download_tasks: dict[str, threading.Event] = {}


def _cache_path(yt_id: str) -> Path:
    return CACHE_DIR / f"{yt_id}.opus"


def _cache_exists(yt_id: str) -> bool:
    p = _cache_path(yt_id)
    return p.exists() and p.stat().st_size > 0


def _prune_cache():
    try:
        files = sorted(CACHE_DIR.glob("*.opus"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        limit = int(CACHE_MAX_SIZE_GB * 1024**3)
        while total > limit and files:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
    except Exception:
        pass


def _download_to_cache(yt_id: str, duration: int | None):
    if _cache_exists(yt_id):
        return
    if duration and duration > CACHE_MAX_DURATION_S:
        return
    with _cache_lock:
        if yt_id in _download_tasks:
            return
        done_event = threading.Event()
        _download_tasks[yt_id] = done_event

    def _worker():
        try:
            dest = _cache_path(yt_id)
            tmp = dest.with_suffix(".part")
            opts = {
                **YDL_OPTS,
                "format": "bestaudio[ext=webm][acodec=opus]/bestaudio/best",
                "outtmpl": str(tmp),
                "quiet": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "opus",
                        "preferredquality": "128",
                    }
                ],
            }
            url = f"https://www.youtube.com/watch?v={yt_id}"
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            for candidate in [tmp, tmp.with_suffix(".opus"), Path(str(tmp) + ".opus")]:
                if candidate.exists() and candidate.stat().st_size > 0:
                    candidate.rename(dest)
                    # rename() preserves the source's original mtime, which
                    # for a slow download is when the .part file was first
                    # created — not when the download finished. Re-stamping
                    # here keeps _prune_cache's LRU ordering meaningful: a
                    # track that just finished downloading shouldn't look
                    # older than tracks that genuinely haven't been touched
                    # in a while.
                    os.utime(dest, None)
                    break
            _prune_cache()
        except Exception as e:
            print(f"Cache download failed for {yt_id}: {e}")
        finally:
            done_event.set()
            with _cache_lock:
                _download_tasks.pop(yt_id, None)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ── Database ───────────────────────────────────────────────────────────────────


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    con = get_db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS playlists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS playlist_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id TEXT,
            track_id TEXT,
            title TEXT,
            artist TEXT,
            thumbnail TEXT,
            duration INTEGER,
            position INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (playlist_id) REFERENCES playlists(id),
            UNIQUE (playlist_id, track_id)
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id TEXT,
            title TEXT,
            artist TEXT,
            thumbnail TEXT,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS queue (
            position INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id TEXT,
            title TEXT,
            artist TEXT,
            thumbnail TEXT,
            duration INTEGER,
            is_auto INTEGER DEFAULT 0
        );
    """
    )
    con.commit()

    # ── Migration: dedupe + add UNIQUE(playlist_id, track_id) on upgrade ────────
    # A pre-existing music.db (built before this fix) may already contain
    # duplicate rows for the same (playlist_id, track_id) pair — that's the
    # exact bug this rewrite closes. If the table above already existed
    # without the UNIQUE constraint, SQLite's CREATE TABLE IF NOT EXISTS
    # silently no-ops and the constraint never gets applied. Detect that,
    # remove existing duplicates (keeping the earliest-added row and
    # renumbering positions), then rebuild the table with the constraint.
    has_unique = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND sql LIKE "
        "'%UNIQUE%playlist_id%track_id%' AND tbl_name='playlist_tracks'"
    ).fetchone()
    if not has_unique:
        dupes = con.execute(
            """
            SELECT playlist_id, track_id, COUNT(*) c
            FROM playlist_tracks GROUP BY playlist_id, track_id HAVING c > 1
            """
        ).fetchall()
        if dupes:
            print(f"Migrating playlist_tracks: removing duplicates for {len(dupes)} (playlist, track) pair(s)")
            for d in dupes:
                keep = con.execute(
                    "SELECT MIN(id) FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
                    (d["playlist_id"], d["track_id"]),
                ).fetchone()[0]
                con.execute(
                    "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=? AND id!=?",
                    (d["playlist_id"], d["track_id"], keep),
                )
            con.commit()
        con.executescript(
            """
            CREATE TABLE playlist_tracks_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id TEXT,
                track_id TEXT,
                title TEXT,
                artist TEXT,
                thumbnail TEXT,
                duration INTEGER,
                position INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (playlist_id) REFERENCES playlists(id),
                UNIQUE (playlist_id, track_id)
            );
            INSERT INTO playlist_tracks_new SELECT * FROM playlist_tracks;
            DROP TABLE playlist_tracks;
            ALTER TABLE playlist_tracks_new RENAME TO playlist_tracks;
            """
        )
        con.commit()
        for pid_row in con.execute("SELECT DISTINCT playlist_id FROM playlist_tracks").fetchall():
            rows = con.execute(
                "SELECT id FROM playlist_tracks WHERE playlist_id=? ORDER BY position, id",
                (pid_row["playlist_id"],),
            ).fetchall()
            for i, r in enumerate(rows, 1):
                con.execute("UPDATE playlist_tracks SET position=? WHERE id=?", (i, r["id"]))
        con.commit()

    con.close()


init_db()

# ── yt-dlp helpers ─────────────────────────────────────────────────────────────

YDL_OPTS = {"quiet": True, "no_warnings": True, "nocheckcertificate": True}


def _clean_channel(channel: str) -> str:
    channel = re.sub(r"\s*-\s*Topic$", "", channel, flags=re.IGNORECASE)
    channel = re.sub(r"VEVO$", "", channel, flags=re.IGNORECASE)
    channel = re.sub(
        r"\s+(Official|Music|TV|Channel|HD|Records|Entertainment|Video)$",
        "",
        channel,
        flags=re.IGNORECASE,
    )
    return channel.strip()


def yt_search_one(query: str) -> str | None:
    for search_prefix in ("ytmsearch1:", "ytsearch1:"):
        try:
            with yt_dlp.YoutubeDL({**YDL_OPTS, "extract_flat": True}) as ydl:
                res = ydl.extract_info(f"{search_prefix}{query}", download=False)
                entries = res.get("entries", [])
                if entries and entries[0].get("id"):
                    return entries[0]["id"]
        except Exception:
            continue
    return None


def yt_get_stream(video_id: str) -> tuple[str, dict]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    for fmt in (
        "bestaudio[ext=webm][acodec=opus]",
        "bestaudio[ext=m4a]",
        "bestaudio[ext=webm]",
        "bestaudio/best",
    ):
        try:
            opts = {**YDL_OPTS, "format": fmt, "postprocessors": []}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                stream_url = info.get("url", "")
                if stream_url:
                    return stream_url, info
        except Exception:
            continue
    raise Exception("No stream URL found")


def yt_search(query: str, limit: int = 15) -> list[dict]:
    """YouTube Music search, falling back to plain YouTube search."""
    try:
        with yt_dlp.YoutubeDL({**YDL_OPTS, "extract_flat": True}) as ydl:
            res = ydl.extract_info(f"ytmsearch{limit}:{query}", download=False)
            entries = res.get("entries", [])
            if entries:
                return [
                    {
                        "id": e["id"],
                        "title": e.get("title"),
                        "artist": _clean_channel(
                            e.get("uploader") or e.get("channel") or e.get("artist") or ""
                        ),
                        "duration": e.get("duration"),
                        "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
                        "source": "youtube_music",
                    }
                    for e in entries
                    if e.get("id")
                ]
    except Exception:
        pass
    try:
        with yt_dlp.YoutubeDL({**YDL_OPTS, "extract_flat": True}) as ydl:
            res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            return [
                {
                    "id": e["id"],
                    "title": e.get("title"),
                    "artist": _clean_channel(e.get("uploader") or e.get("channel") or ""),
                    "duration": e.get("duration"),
                    "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
                    "source": "youtube",
                }
                for e in res.get("entries", [])
                if e.get("id")
            ]
    except Exception:
        return []


def yt_get_related(video_id: str, limit: int = 12) -> list[dict]:
    """YouTube's own 'up next' / related list for a video, as a fallback
    recommendation source now that Last.fm/Spotify are gone."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL({**YDL_OPTS, "extract_flat": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            related = []
            for e in (info.get("related_videos") or [])[:limit]:
                if e.get("id") and e["id"] != video_id:
                    related.append(
                        {
                            "id": e["id"],
                            "title": e.get("title"),
                            "artist": _clean_channel(e.get("uploader") or e.get("channel") or ""),
                            "duration": e.get("duration"),
                            "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
                            "source": "youtube_related",
                        }
                    )
            return related
    except Exception:
        return []


# ── Mock fallback ──────────────────────────────────────────────────────────────

MOCK_TRACKS = [
    {"id": "fJ9rUzIMcZQ", "title": "Bohemian Rhapsody", "artist": "Queen", "duration": 354, "thumbnail": "https://i.ytimg.com/vi/fJ9rUzIMcZQ/mqdefault.jpg", "source": "mock"},
    {"id": "dQw4w9WgXcQ", "title": "Never Gonna Give You Up", "artist": "Rick Astley", "duration": 213, "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg", "source": "mock"},
    {"id": "JGwWNGJdvx8", "title": "Shape of You", "artist": "Ed Sheeran", "duration": 234, "thumbnail": "https://i.ytimg.com/vi/JGwWNGJdvx8/mqdefault.jpg", "source": "mock"},
    {"id": "kXYiU_JCYtU", "title": "Numb", "artist": "Linkin Park", "duration": 187, "thumbnail": "https://i.ytimg.com/vi/kXYiU_JCYtU/mqdefault.jpg", "source": "mock"},
    {"id": "hTWKbfoikeg", "title": "Smells Like Teen Spirit", "artist": "Nirvana", "duration": 301, "thumbnail": "https://i.ytimg.com/vi/hTWKbfoikeg/mqdefault.jpg", "source": "mock"},
    {"id": "YR5ApYxkU-U", "title": "Blinding Lights", "artist": "The Weeknd", "duration": 200, "thumbnail": "https://i.ytimg.com/vi/YR5ApYxkU-U/mqdefault.jpg", "source": "mock"},
]


def mock_search(q: str, limit: int = 10) -> list[dict]:
    q = q.lower()
    res = [t for t in MOCK_TRACKS if q in t["title"].lower() or q in t["artist"].lower()]
    return (res or MOCK_TRACKS)[:limit]


# ── Search ─────────────────────────────────────────────────────────────────────


@app.get("/search")
def search(q: str, limit: int = 15):
    if not q.strip():
        raise HTTPException(400, "Query cannot be empty")
    results = yt_search(q, limit)
    if results:
        return {"query": q, "results": results, "count": len(results), "source": results[0]["source"]}
    fallback = mock_search(q, limit)
    return {"query": q, "results": fallback, "count": len(fallback), "source": "mock"}


# ── Stream ─────────────────────────────────────────────────────────────────────


@app.get("/cache/{yt_id}")
def serve_cached(yt_id: str):
    if not _cache_exists(yt_id):
        # Covers both "never downloaded" and "download still in progress" —
        # _cache_exists checks size > 0, not just presence, so a file that's
        # mid-write (renamed but not yet flushed, or still a .part) is
        # correctly treated as not-yet-cached rather than served as an
        # empty/truncated response.
        raise HTTPException(404, "Not cached")
    try:
        p = _cache_path(yt_id)
        os.utime(p, None)  # mark as recently used, for LRU pruning
        return FileResponse(
            str(p),
            media_type="audio/ogg",
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
        )
    except Exception as e:
        # Covers the race where _prune_cache() deletes this exact file
        # (or an I/O error occurs) between the _cache_exists check above
        # and FileResponse actually opening it. Rapid next/prev presses
        # make this race meaningfully more likely by triggering many
        # concurrent /stream + /cache requests in quick succession.
        raise HTTPException(500, f"Cache file unavailable: {e}")


@app.get("/stream/{track_id}")
def stream(track_id: str, title: Optional[str] = None, artist: Optional[str] = None):
    """
    track_id is always a YouTube video ID — every result from /search comes
    from yt-dlp, so there's no separate ID space to detect or resolve here
    (the old Spotify-ID branch is gone along with Spotify search).
    """
    try:
        yt_id = track_id
        if _cache_exists(yt_id):
            print(f"Cache HIT: {yt_id}")
            con = get_db()
            row = con.execute(
                "SELECT title, artist, thumbnail FROM history WHERE track_id=? ORDER BY played_at DESC LIMIT 1",
                (track_id,),
            ).fetchone()
            con.close()
            return {
                "id": track_id, "yt_id": yt_id,
                "title": title or (row["title"] if row else None) or yt_id,
                "artist": artist or (row["artist"] if row else None) or "",
                "duration": None,
                "stream_url": f"/cache/{yt_id}",
                "thumbnail": row["thumbnail"] if row else None,
                "format": "opus", "bitrate": 128, "cached": True,
            }
        stream_url, info = yt_get_stream(yt_id)
        resolved_title = title or info.get("title")
        resolved_artist = artist or _clean_channel(info.get("uploader") or info.get("channel") or "")
        duration = info.get("duration")
        con = get_db()
        recent = con.execute(
            "SELECT id FROM history WHERE track_id=? AND played_at > datetime('now','-5 minutes')",
            (track_id,),
        ).fetchone()
        if not recent:
            con.execute(
                "INSERT INTO history (track_id, title, artist, thumbnail) VALUES (?,?,?,?)",
                (track_id, resolved_title, resolved_artist, info.get("thumbnail")),
            )
            con.commit()
        con.close()
        if not duration or duration <= CACHE_MAX_DURATION_S:
            _download_to_cache(yt_id, duration)
        return {
            "id": track_id, "yt_id": yt_id,
            "title": resolved_title, "artist": resolved_artist,
            "duration": duration, "stream_url": stream_url,
            "thumbnail": info.get("thumbnail"),
            "format": info.get("ext"), "bitrate": info.get("abr"), "cached": False,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Related tracks ─────────────────────────────────────────────────────────────


@app.get("/related/{track_id}")
def related(track_id: str, title: Optional[str] = None, artist: Optional[str] = None):
    # TODO: recommendation source. Last.fm's track.getSimilar (and the
    # Spotify-artist-search fallback) used to power this. Both are removed.
    # YouTube's own "related videos" list is a reasonable stand-in and is
    # tried first below; if yt-dlp can't extract it either, this honestly
    # returns an empty list with source="unavailable" rather than pretending
    # to have recommendations.
    results = yt_get_related(track_id, limit=12)
    source = "youtube_related" if results else "unavailable"
    print(f"Related tracks for '{artist} - {title}' (ID: {track_id}): {len(results)} found via {source}")
    return {"related": results, "source": source}


# ── Personalised suggestions ───────────────────────────────────────────────────


@app.get("/suggestions")
def suggestions(limit: int = 12):
    con = get_db()
    rows = con.execute(
        """
        SELECT track_id, title, artist, thumbnail, COUNT(*) as plays
        FROM history GROUP BY track_id
        ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    con.close()

    if rows:
        # Real signal: the user's own most-played tracks. This doesn't
        # depend on any external API and keeps working with Spotify/Last.fm
        # removed.
        top = [
            {
                "id": r["track_id"],
                "title": r["title"],
                "artist": r["artist"],
                "thumbnail": r["thumbnail"],
                "source": "history",
            }
            for r in rows
        ]
        return {"suggestions": top, "source": "history"}

    # TODO: recommendation source. With no play history yet, this used to
    # fall back to Last.fm's tag.getTopTracks (global pop charts) or a
    # Spotify-recommendations seed. Both are removed and there is currently
    # no working charts/discovery source, so an empty result is returned
    # instead of the old mock-search("top hits") stand-in, which was
    # cosmetic filler rather than a real suggestion.
    return {"suggestions": [], "source": "unavailable"}


# ── History ────────────────────────────────────────────────────────────────────


@app.get("/history")
def history(limit: int = 100):
    con = get_db()
    rows = con.execute("SELECT * FROM history ORDER BY played_at DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return {"history": [dict(r) for r in rows]}


@app.delete("/history/{history_id}")
def delete_history_entry(history_id: int):
    con = get_db()
    con.execute("DELETE FROM history WHERE id=?", (history_id,))
    con.commit()
    con.close()
    return {"status": "deleted"}


@app.delete("/history")
def clear_history():
    con = get_db()
    con.execute("DELETE FROM history")
    con.commit()
    con.close()
    return {"status": "cleared"}


# ── Queue ──────────────────────────────────────────────────────────────────────


class TrackIn(BaseModel):
    id: str
    title: str
    artist: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    is_auto: bool = False


def _resequence_queue(con):
    rows = con.execute("SELECT rowid FROM queue ORDER BY position").fetchall()
    for i, row in enumerate(rows, 1):
        con.execute("UPDATE queue SET position=? WHERE rowid=?", (i, row[0]))


@app.get("/queue")
def get_queue():
    con = get_db()
    rows = con.execute("SELECT * FROM queue ORDER BY position").fetchall()
    con.close()
    return {"queue": [dict(r) for r in rows]}


@app.post("/queue")
def add_to_queue(track: TrackIn):
    con = get_db()
    con.execute(
        "INSERT INTO queue (track_id,title,artist,thumbnail,duration,is_auto) VALUES (?,?,?,?,?,?)",
        (track.id, track.title, track.artist, track.thumbnail, track.duration, int(track.is_auto)),
    )
    con.commit()
    _resequence_queue(con)
    con.commit()
    con.close()
    return {"status": "added"}


@app.delete("/queue")
def clear_queue():
    con = get_db()
    con.execute("DELETE FROM queue")
    con.commit()
    con.close()
    return {"status": "cleared"}


@app.delete("/queue/{position}")
def remove_from_queue(position: int):
    con = get_db()
    con.execute("DELETE FROM queue WHERE position=?", (position,))
    con.commit()
    _resequence_queue(con)
    con.commit()
    con.close()
    return {"status": "removed"}


# ── Playlists ──────────────────────────────────────────────────────────────────


class PlaylistIn(BaseModel):
    name: str


@app.get("/playlists")
def list_playlists():
    con = get_db()
    rows = con.execute("SELECT * FROM playlists ORDER BY created_at DESC").fetchall()
    con.close()
    return {"playlists": [dict(r) for r in rows]}


@app.post("/playlists")
def create_playlist(body: PlaylistIn):
    con = get_db()
    for _ in range(3):
        pid = str(uuid.uuid4())[:8]
        try:
            con.execute("INSERT INTO playlists (id,name) VALUES (?,?)", (pid, body.name))
            con.commit()
            con.close()
            return {"status": "created", "id": pid, "name": body.name}
        except sqlite3.IntegrityError:
            continue
    con.close()
    raise HTTPException(500, "Could not generate unique playlist ID")


@app.get("/playlists/{pid}")
def get_playlist(pid: str):
    con = get_db()
    pl = con.execute("SELECT * FROM playlists WHERE id=?", (pid,)).fetchone()
    if not pl:
        con.close()
        raise HTTPException(404, "Playlist not found")
    tracks = con.execute(
        "SELECT * FROM playlist_tracks WHERE playlist_id=? ORDER BY position", (pid,)
    ).fetchall()
    con.close()
    return {"playlist": dict(pl), "tracks": [dict(t) for t in tracks]}


@app.post("/playlists/{pid}/tracks")
def add_to_playlist(pid: str, track: TrackIn):
    """
    Adds a track to a playlist. If the track is already in this playlist,
    this is a no-op that reports the existing position rather than
    inserting a second row — playlist_tracks now has a UNIQUE(playlist_id,
    track_id) constraint (see init_db) specifically to make this
    unrepresentable at the DB layer, not just avoided by the API.

    This was the source of the frontend "two children with the same key"
    React warning: every id in TrackTable/PlaylistView comes straight from
    track_id, so a duplicate DB row was a duplicate React key by
    construction. Fixing it here (not in the frontend) fixes it for every
    caller, including the CLI and any future client.
    """
    con = get_db()
    if not con.execute("SELECT id FROM playlists WHERE id=?", (pid,)).fetchone():
        con.close()
        raise HTTPException(404, "Playlist not found")

    existing = con.execute(
        "SELECT position FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
        (pid, track.id),
    ).fetchone()
    if existing:
        con.close()
        return {"status": "already_in_playlist", "position": existing["position"]}

    pos = con.execute(
        "SELECT COALESCE(MAX(position),0)+1 FROM playlist_tracks WHERE playlist_id=?", (pid,)
    ).fetchone()[0]
    try:
        con.execute(
            "INSERT INTO playlist_tracks (playlist_id,track_id,title,artist,thumbnail,duration,position) VALUES (?,?,?,?,?,?,?)",
            (pid, track.id, track.title, track.artist, track.thumbnail, track.duration, pos),
        )
        con.commit()
    except sqlite3.IntegrityError:
        # Race: another request inserted the same (pid, track_id) between
        # our SELECT and this INSERT. The UNIQUE constraint caught it —
        # report the now-existing row instead of erroring.
        con.rollback()
        existing = con.execute(
            "SELECT position FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
            (pid, track.id),
        ).fetchone()
        con.close()
        return {"status": "already_in_playlist", "position": existing["position"] if existing else pos}
    con.close()
    return {"status": "added", "position": pos}


@app.delete("/playlists/{pid}/tracks/{track_id}")
def remove_from_playlist(pid: str, track_id: str):
    con = get_db()
    if not con.execute("SELECT id FROM playlists WHERE id=?", (pid,)).fetchone():
        con.close()
        raise HTTPException(404, "Playlist not found")
    con.execute(
        "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?", (pid, track_id)
    )
    con.commit()
    tracks = con.execute(
        "SELECT id FROM playlist_tracks WHERE playlist_id=? ORDER BY position", (pid,)
    ).fetchall()
    for i, t in enumerate(tracks, 1):
        con.execute("UPDATE playlist_tracks SET position=? WHERE id=?", (i, t["id"]))
    con.commit()
    con.close()
    return {"status": "removed"}


@app.delete("/playlists/{pid}/tracks")
def clear_playlist(pid: str):
    con = get_db()
    con.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
    con.commit()
    con.close()
    return {"status": "cleared"}


@app.patch("/playlists/{pid}")
def rename_playlist(pid: str, body: PlaylistIn):
    con = get_db()
    if not con.execute("SELECT id FROM playlists WHERE id=?", (pid,)).fetchone():
        con.close()
        raise HTTPException(404, "Playlist not found")
    con.execute("UPDATE playlists SET name=? WHERE id=?", (body.name, pid))
    con.commit()
    con.close()
    return {"status": "renamed", "id": pid, "name": body.name}


@app.delete("/playlists/{pid}")
def delete_playlist(pid: str):
    con = get_db()
    con.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
    con.execute("DELETE FROM playlists WHERE id=?", (pid,))
    con.commit()
    con.close()
    return {"status": "deleted"}


# ── Health ─────────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
#  BIRD FEEDER — Camera streaming endpoints
#  No environment variables required. Hardcoded defaults work out of the box.
#  Architecture: Pi (outbound POST) → this server (fan-out) → browsers (GET)
# ══════════════════════════════════════════════════════════════════════════════

# Auth: a fixed shared secret between Pi and server.
# Change this string on both files if you want basic security.
# Set to empty string "" to disable auth entirely (fine on a home network).
FEEDER_PUSH_SECRET = ""  # e.g. "mysecret123" — must match pi_camera.py

# How many seconds of silence before the camera is declared offline.
FEEDER_OFFLINE_AFTER_S = 15

# Cosmetic label returned by /feed/status — updated automatically from push headers.
FEEDER_RESOLUTION = "1280x720"

# How often to send a keep-alive MJPEG boundary so Cloudflare/proxies
# don't close idle connections between frames.
_FEEDER_KEEPALIVE_S = 5

# ── In-memory frame store ──────────────────────────────────────────────────────
_frame_lock = threading.Lock()
_latest_frame: bytes | None = None   # raw JPEG of most recent frame
_frame_ts: float = 0.0               # unix time of last received frame
_frame_seq: int = 0                  # bumped every push — SSE uses this to detect new frames
_frame_count_total: int = 0          # lifetime push count for debugging

# Per-browser wake events so MJPEG generators don't busy-poll.
_sse_clients: set = set()
_sse_clients_lock = threading.Lock()

# Connection log — last 20 events, newest first. Shown at /feed/debug.
_feeder_log: list[dict] = []
_feeder_log_lock = threading.Lock()


def _flog(level: str, source: str, msg: str, extra: dict | None = None):
    """
    Append a timestamped entry to the in-memory connection log.
    level:  "INFO" | "WARN" | "ERROR"
    source: "PI" | "SERVER" | "CLIENT"
    """
    entry = {
        "ts": _time.strftime("%H:%M:%S"),
        "unix": round(_time.time(), 2),
        "level": level,
        "source": source,
        "msg": msg,
        **(extra or {}),
    }
    with _feeder_log_lock:
        _feeder_log.insert(0, entry)
        del _feeder_log[20:]  # keep last 20


def _notify_sse_clients():
    with _sse_clients_lock:
        for ev in list(_sse_clients):
            ev.set()


# ── Pi → Server: frame push ───────────────────────────────────────────────────

@app.post("/feed/push")
async def feed_push(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """
    Pi sends raw JPEG bytes here at ~8 fps.
    Returns 204 on success. All errors include a plain-text reason.
    """
    global _latest_frame, _frame_ts, _frame_seq, _frame_count_total

    # ── Optional auth ─────────────────────────────────────────────────────────
    if FEEDER_PUSH_SECRET:
        if not authorization or not authorization.lower().startswith("bearer "):
            _flog("ERROR", "PI", "Push rejected: missing Authorization header")
            raise HTTPException(401, "[PI ERROR] Missing Authorization: Bearer <secret> header")
        if authorization[7:].strip() != FEEDER_PUSH_SECRET:
            _flog("ERROR", "PI", "Push rejected: wrong secret")
            raise HTTPException(403, "[PI ERROR] Wrong push secret — check FEEDER_PUSH_SECRET in pi_camera.py")

    # ── Read body ─────────────────────────────────────────────────────────────
    try:
        body = await request.body()
    except Exception as e:
        _flog("ERROR", "PI", f"Failed to read request body: {e}")
        raise HTTPException(400, f"[SERVER ERROR] Could not read request body: {e}")

    if not body:
        _flog("WARN", "PI", "Push received empty body")
        raise HTTPException(400, "[PI ERROR] Empty body — camera may not be sending frames yet")

    # ── Validate JPEG magic bytes (FF D8) ────────────────────────────────────
    if len(body) < 4 or body[:2] != b"\xff\xd8":
        _flog("ERROR", "PI", f"Push received non-JPEG body ({len(body)} bytes, starts {body[:4].hex()})")
        raise HTTPException(
            415,
            f"[PI ERROR] Body is not a JPEG (expected FF D8, got {body[:2].hex()}). "
            "Check camera capture code in pi_camera.py."
        )

    # ── Store frame ───────────────────────────────────────────────────────────
    with _frame_lock:
        _latest_frame = body
        _frame_ts = _time.time()
        _frame_seq += 1
        _frame_count_total += 1
        seq = _frame_seq

    _notify_sse_clients()

    if seq == 1:
        _flog("INFO", "PI", "First frame received — camera is online", {"bytes": len(body)})
    elif seq % 500 == 0:
        _flog("INFO", "PI", f"Frame #{seq} received", {"bytes": len(body)})

    return Response(status_code=204)


# ── Server → Browser: MJPEG stream ───────────────────────────────────────────

_BOUNDARY = b"BirdFeederBoundary"
_BOUNDARY_SEP = b"--BirdFeederBoundary\r\n"


def _mjpeg_frame(jpeg: bytes) -> bytes:
    return (
        _BOUNDARY_SEP
        + b"Content-Type: image/jpeg\r\n"
        + b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n"
        + b"\r\n"
        + jpeg
        + b"\r\n"
    )


@app.get("/feed/stream")
async def feed_stream(request: Request):
    """
    Browser points an <img> tag here.
    Streams JPEG frames as multipart/x-mixed-replace.
    Sends a keep-alive boundary every 5s so Cloudflare doesn't drop the connection.
    """
    client_ip = request.client.host if request.client else "unknown"
    client_event = threading.Event()
    with _sse_clients_lock:
        _sse_clients.add(client_event)

    _flog("INFO", "CLIENT", f"MJPEG stream opened", {"ip": client_ip})
    loop = asyncio.get_event_loop()

    async def _generate():
        last_seq = -1
        frames_sent = 0
        try:
            with _frame_lock:
                if _latest_frame is not None:
                    yield _mjpeg_frame(_latest_frame)
                    last_seq = _frame_seq
                    frames_sent += 1

            while True:
                got_new = await loop.run_in_executor(
                    None, client_event.wait, _FEEDER_KEEPALIVE_S
                )
                client_event.clear()

                with _frame_lock:
                    frame = _latest_frame
                    current_seq = _frame_seq

                if got_new and frame is not None and current_seq != last_seq:
                    last_seq = current_seq
                    frames_sent += 1
                    yield _mjpeg_frame(frame)
                else:
                    # Keep-alive: prevents Cloudflare 524 on idle connections
                    yield b"--BirdFeederBoundary\r\n\r\n"

        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            with _sse_clients_lock:
                _sse_clients.discard(client_event)
            _flog("INFO", "CLIENT", f"MJPEG stream closed after {frames_sent} frames", {"ip": client_ip})

    return StreamingResponse(
        _generate(),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY.decode()}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── Server → Browser: single snapshot ────────────────────────────────────────

@app.get("/feed/snapshot")
def feed_snapshot():
    """
    Returns the latest frame as a plain JPEG.
    Used as a fallback when MJPEG stalls.
    Returns 503 (not 404) when the camera hasn't sent anything yet.
    """
    with _frame_lock:
        frame = _latest_frame
        ts = _frame_ts

    if frame is None:
        _flog("WARN", "SERVER", "Snapshot requested but no frame available yet")
        raise HTTPException(
            503,
            "[SERVER] No frame received yet. "
            "Check that pi_camera.py is running and can reach this server."
        )

    age_s = round(_time.time() - ts, 1)
    if age_s > FEEDER_OFFLINE_AFTER_S:
        _flog("WARN", "SERVER", f"Snapshot is stale ({age_s}s old)")

    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Age-Seconds": str(int(age_s)),
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/feed/status")
def feed_status():
    """Lightweight poll used by the frontend to show online/offline badge."""
    with _frame_lock:
        frame = _latest_frame
        ts = _frame_ts
        seq = _frame_seq
        total = _frame_count_total

    age_s = round(_time.time() - ts, 1) if ts else None
    online = (frame is not None) and (age_s is not None) and (age_s < FEEDER_OFFLINE_AFTER_S)

    return {
        "online": online,
        "frame_age_s": age_s,
        "frame_count": seq,
        "frame_count_total": total,
        "resolution": FEEDER_RESOLUTION,
        "ts": round(_time.time(), 2),
    }


# ── Debug endpoint ─────────────────────────────────────────────────────────────

@app.get("/feed/debug")
def feed_debug():
    """
    Human-readable diagnostics. Open this in a browser to see exactly
    what's happening and which layer (Pi / Server / Client) has the problem.

    Checklist printed in the response tells you what to fix.
    """
    with _frame_lock:
        frame = _latest_frame
        ts = _frame_ts
        seq = _frame_seq
        total = _frame_count_total

    now = _time.time()
    age_s = round(now - ts, 1) if ts else None
    online = (frame is not None) and (age_s is not None) and (age_s < FEEDER_OFFLINE_AFTER_S)

    with _sse_clients_lock:
        browser_count = len(_sse_clients)

    with _feeder_log_lock:
        log_snapshot = list(_feeder_log)

    # ── Build checklist ───────────────────────────────────────────────────────
    checks = []

    # 1. Server routes reachable
    checks.append({
        "check": "Server routes registered",
        "ok": True,
        "note": "You're reading this response, so the /feed/* routes are correctly registered.",
        "layer": "SERVER",
    })

    # 2. Pi is pushing frames
    if frame is None:
        checks.append({
            "check": "Pi is sending frames",
            "ok": False,
            "note": (
                "[PI PROBLEM] No frames received yet. "
                "Make sure pi_camera.py is running on the Pi and the PUSH_URL "
                "points to this server (https://api.vatsaldutt.com/feed/push). "
                "Check Pi terminal for errors."
            ),
            "layer": "PI",
        })
    elif age_s is not None and age_s > FEEDER_OFFLINE_AFTER_S:
        checks.append({
            "check": "Pi is sending frames",
            "ok": False,
            "note": (
                f"[PI PROBLEM] Last frame was {age_s}s ago — Pi appears to have stopped. "
                "Check for camera timeout errors in the Pi terminal."
            ),
            "layer": "PI",
        })
    else:
        checks.append({
            "check": "Pi is sending frames",
            "ok": True,
            "note": f"Last frame {age_s}s ago. Total frames received this session: {total}.",
            "layer": "PI",
        })

    # 3. Auth config consistency
    if FEEDER_PUSH_SECRET:
        checks.append({
            "check": "Auth config",
            "ok": True,
            "note": f"Push secret is set ({len(FEEDER_PUSH_SECRET)} chars). Make sure PUSH_SECRET in pi_camera.py matches.",
            "layer": "SERVER",
        })
    else:
        checks.append({
            "check": "Auth config",
            "ok": True,
            "note": "No push secret set — all pushes accepted. Fine for home use.",
            "layer": "SERVER",
        })

    # 4. Browsers connected
    checks.append({
        "check": "Browser clients connected",
        "ok": browser_count >= 0,
        "note": f"{browser_count} browser(s) currently streaming MJPEG.",
        "layer": "CLIENT",
    })

    # 5. Frame size sanity
    if frame is not None:
        kb = round(len(frame) / 1024, 1)
        frame_ok = 5 < kb < 500
        checks.append({
            "check": "Frame size looks valid",
            "ok": frame_ok,
            "note": (
                f"Latest frame is {kb} KB. "
                + ("" if frame_ok else "[PI PROBLEM] Unusually small or large — camera may be misconfigured.")
            ),
            "layer": "PI",
        })

    return {
        "summary": {
            "camera_online": online,
            "frame_age_s": age_s,
            "frames_this_session": seq,
            "frames_lifetime": total,
            "browsers_streaming": browser_count,
            "server_time": _time.strftime("%Y-%m-%d %H:%M:%S UTC", _time.gmtime()),
        },
        "checklist": checks,
        "recent_events": log_snapshot,
        "endpoints": {
            "push":     "POST /feed/push       — Pi → Server (JPEG body)",
            "stream":   "GET  /feed/stream     — Server → Browser (MJPEG)",
            "snapshot": "GET  /feed/snapshot   — Server → Browser (single JPEG)",
            "status":   "GET  /feed/status     — lightweight online/offline check",
            "debug":    "GET  /feed/debug      — this page",
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SPA CATCH-ALL — must be LAST so it doesn't swallow /feed/* routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
def index():
    idx = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(idx):
        return FileResponse(idx)
    return {"status": "ok", "service": "CRYSTAL Music Backend v3.1"}


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    """
    React Router catch-all. Registered LAST so all explicit routes
    (including /feed/*) are matched first.
    """
    idx = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(idx):
        return FileResponse(idx)
    raise HTTPException(404)