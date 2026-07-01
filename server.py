"""
Music Backend Server — FastAPI v3.0
- Spotify Web API for search (better results, real artist names)
- Last.fm for related tracks + personalised suggestions
- yt-dlp for audio streaming (YouTube as transport only)
- Bird Feeder Camera endpoints (plug-and-play, no env vars needed)
- All v2 bugs fixed
"""

import asyncio
import hashlib
import os
import re
import sqlite3
import threading
import time as _time
import uuid
from pathlib import Path
from typing import Optional

import yt_dlp
import requests as req
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="phonon Music Backend", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DB_PATH = os.path.join(os.path.dirname(__file__), "music.db")

# ── Audio cache ────────────────────────────────────────────────────────────────
CACHE_DIR = Path(os.getenv("phonon_CACHE_DIR", Path.home() / ".phonon_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_MAX_DURATION_S = 600
CACHE_MAX_SIZE_GB = float(os.getenv("phonon_CACHE_MAX_GB", "2"))
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


def _download_to_cache(yt_id: str, stream_url: str, duration: int | None):
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


# ── API credentials ────────────────────────────────────────────────────────────
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "")

# ── Database ───────────────────────────────────────────────────────────────────


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
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
            FOREIGN KEY (playlist_id) REFERENCES playlists(id)
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
    con.close()


init_db()

# ── Spotify ────────────────────────────────────────────────────────────────────

_spotify_token: dict = {"access_token": None, "expires_at": 0}


def _get_spotify_token() -> str | None:
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    if (
        _spotify_token["access_token"]
        and _time.time() < _spotify_token["expires_at"] - 30
    ):
        return _spotify_token["access_token"]
    try:
        r = req.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
            timeout=6,
        )
        r.raise_for_status()
        d = r.json()
        _spotify_token["access_token"] = d["access_token"]
        _spotify_token["expires_at"] = _time.time() + d["expires_in"]
        return d["access_token"]
    except Exception:
        return None


def _spotify_images_to_thumb(images: list) -> str | None:
    thumb = next((i["url"] for i in images if i.get("width", 0) >= 300), None)
    return thumb or (images[0]["url"] if images else None)


def _normalise_spotify_track(item: dict) -> dict:
    artists = ", ".join(a["name"] for a in item.get("artists", []))
    thumb = _spotify_images_to_thumb(item.get("album", {}).get("images", []))
    return {
        "id": item["id"],
        "spotify_id": item["id"],
        "title": item["name"],
        "artist": artists,
        "album": item.get("album", {}).get("name"),
        "duration": item["duration_ms"] // 1000,
        "thumbnail": thumb,
        "popularity": item.get("popularity", 0),
        "source": "spotify",
    }


def spotify_search(query: str, limit: int = 15) -> list[dict]:
    token = _get_spotify_token()
    if not token:
        return []
    try:
        r = req.get(
            "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": query, "type": "track", "limit": limit, "market": "US"},
            timeout=6,
        )
        r.raise_for_status()
        return [
            _normalise_spotify_track(i)
            for i in r.json().get("tracks", {}).get("items", [])
        ]
    except Exception:
        return []


def spotify_recommendations(seed_spotify_ids: list[str], limit: int = 12) -> list[dict]:
    token = _get_spotify_token()
    if not token or not seed_spotify_ids:
        return []
    try:
        r = req.get(
            "https://api.spotify.com/v1/recommendations",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "seed_tracks": ",".join(seed_spotify_ids[:5]),
                "limit": limit,
                "market": "US",
            },
            timeout=6,
        )
        r.raise_for_status()
        return [_normalise_spotify_track(i) for i in r.json().get("tracks", [])]
    except Exception:
        return []


def spotify_track_by_id(spotify_id: str) -> dict | None:
    token = _get_spotify_token()
    if not token:
        return None
    try:
        r = req.get(
            f"https://api.spotify.com/v1/tracks/{spotify_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        r.raise_for_status()
        return _normalise_spotify_track(r.json())
    except Exception:
        return None


# ── Last.fm ────────────────────────────────────────────────────────────────────


def lastfm(method: str, **params) -> dict:
    r = req.get(
        "https://ws.audioscrobbler.com/2.0/",
        params={
            "method": method,
            "api_key": LASTFM_API_KEY,
            "format": "json",
            **params,
        },
        timeout=5,
    )
    print(r.url)
    r.raise_for_status()
    return r.json()


def lastfm_similar(artist: str, title: str, limit: int = 12) -> list[dict]:
    title = title.split("|")[0]
    if not LASTFM_API_KEY:
        print("Last.fm API key not configured; skipping similar tracks")
        return []
    try:
        print(f"Fetching similar tracks from Last.fm for '{title}'")
        data = lastfm("track.getSimilar", track=title, limit=limit)
        result = [
            {
                "id": None,
                "title": t["name"],
                "artist": t["artist"]["name"],
                "thumbnail": next(
                    (
                        i["#text"]
                        for i in t.get("image", [])
                        if i.get("size") == "large" and i.get("#text")
                    ),
                    None,
                ),
                "source": "lastfm",
            }
            for t in data.get("similartracks", {}).get("track", [])
        ]
        print(result)
        return result
    except Exception:
        return []


def lastfm_top_tracks(tag: str = "pop", limit: int = 12) -> list[dict]:
    if not LASTFM_API_KEY:
        return []
    try:
        data = lastfm("tag.getTopTracks", tag=tag, limit=limit)
        result = [
            {
                "id": None,
                "title": t["name"],
                "artist": t["artist"]["name"],
                "thumbnail": None,
                "source": "lastfm",
            }
            for t in data.get("tracks", {}).get("track", [])
        ]
        print(result)
        return result
    except Exception:
        return []


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


def yt_fallback_search(query: str, limit: int = 15) -> list[dict]:
    for prefix in (f"https://music.youtube.com/search?q=", None):
        try:
            search_query = f"ytmsearch{limit}:{query}"
            with yt_dlp.YoutubeDL({**YDL_OPTS, "extract_flat": True}) as ydl:
                res = ydl.extract_info(search_query, download=False)
                entries = res.get("entries", [])
                if entries:
                    return [
                        {
                            "id": e["id"],
                            "title": e.get("title"),
                            "artist": _clean_channel(
                                e.get("uploader")
                                or e.get("channel")
                                or e.get("artist")
                                or ""
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
                    "artist": _clean_channel(
                        e.get("uploader") or e.get("channel") or ""
                    ),
                    "duration": e.get("duration"),
                    "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
                    "source": "youtube",
                }
                for e in res.get("entries", [])
                if e.get("id")
            ]
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
    results = spotify_search(q, limit)
    if results:
        return {"query": q, "results": results, "count": len(results), "source": "spotify"}
    yt = yt_fallback_search(q, limit)
    if yt:
        return {"query": q, "results": yt, "count": len(yt), "source": "youtube"}
    fallback = mock_search(q, limit)
    return {"query": q, "results": fallback, "count": len(fallback), "source": "mock"}


# ── Stream ─────────────────────────────────────────────────────────────────────


@app.get("/cache/{yt_id}")
def serve_cached(yt_id: str):
    p = _cache_path(yt_id)
    if not p.exists():
        raise HTTPException(404, "Not cached")
    return FileResponse(str(p), media_type="audio/ogg", headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"})


@app.get("/stream/{track_id}")
def stream(track_id: str, title: Optional[str] = None, artist: Optional[str] = None):
    try:
        yt_id: str | None = None
        resolved_title = title
        resolved_artist = artist
        is_spotify_id = len(track_id) == 22 and re.match(r"^[A-Za-z0-9]+$", track_id)
        if is_spotify_id:
            if not resolved_title:
                sp = spotify_track_by_id(track_id)
                if sp:
                    resolved_title = sp["title"]
                    resolved_artist = sp["artist"]
            query = f"{resolved_artist or ''} {resolved_title or ''} audio".strip()
            yt_id = yt_search_one(query)
            if not yt_id:
                raise HTTPException(404, "Could not find audio for this track")
        else:
            yt_id = track_id
        if _cache_exists(yt_id):
            print(f"Cache HIT: {yt_id} ({resolved_title})")
            con = get_db()
            row = con.execute(
                "SELECT title, artist, thumbnail FROM history WHERE track_id=? ORDER BY played_at DESC LIMIT 1",
                (track_id,),
            ).fetchone()
            con.close()
            return {
                "id": track_id, "yt_id": yt_id,
                "title": resolved_title or (row["title"] if row else None) or yt_id,
                "artist": resolved_artist or (row["artist"] if row else None) or "",
                "duration": None,
                "stream_url": f"/cache/{yt_id}",
                "thumbnail": row["thumbnail"] if row else None,
                "format": "opus", "bitrate": 128, "cached": True,
            }
        stream_url, info = yt_get_stream(yt_id)
        resolved_title = resolved_title or info.get("title")
        resolved_artist = resolved_artist or _clean_channel(info.get("uploader") or info.get("channel") or "")
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
            _download_to_cache(yt_id, stream_url, duration)
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
    results: list[dict] = []
    source = "none"
    results = lastfm_similar(artist, title, limit=12)
    if results:
        source = "lastfm"
    if not results and artist:
        results = spotify_search(f"{artist}", limit=10)
        results = [r for r in results if r.get("id") != track_id][:10]
        if results:
            source = "spotify_artist"
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
        ORDER BY plays DESC, MAX(played_at) DESC LIMIT 10
        """
    ).fetchall()
    con.close()
    if not rows:
        top = lastfm_top_tracks("pop", limit)
        return {"suggestions": top or mock_search("top hits", limit), "source": "charts"}
    spotify_ids: list[str] = []
    for row in rows[:5]:
        if len(row["track_id"]) == 22 and re.match(r"^[A-Za-z0-9]+$", row["track_id"]):
            spotify_ids.append(row["track_id"])
        else:
            sp = spotify_search(f"{row['artist']} {row['title']}", limit=1)
            if sp and sp[0].get("spotify_id"):
                spotify_ids.append(sp[0]["spotify_id"])
    if spotify_ids:
        recs = spotify_recommendations(spotify_ids, limit=limit)
        if recs:
            return {"suggestions": recs, "source": "spotify_personalised"}
    if rows:
        print(f"Spotify recommendations unavailable; falling back to Last.fm for personalised suggestions based on top track '{rows[0]['artist']} - {rows[0]['title']}'")
        sim = lastfm_similar(rows[0]["artist"] or "", rows[0]["title"] or "", limit=limit)
        if sim:
            return {"suggestions": sim, "source": "lastfm_personalised"}
    return {"suggestions": [], "source": "none"}


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
        raise HTTPException(404, "Playlist not found")
    tracks = con.execute(
        "SELECT * FROM playlist_tracks WHERE playlist_id=? ORDER BY position", (pid,)
    ).fetchall()
    con.close()
    return {"playlist": dict(pl), "tracks": [dict(t) for t in tracks]}


@app.post("/playlists/{pid}/tracks")
def add_to_playlist(pid: str, track: TrackIn):
    con = get_db()
    if not con.execute("SELECT id FROM playlists WHERE id=?", (pid,)).fetchone():
        raise HTTPException(404, "Playlist not found")
    pos = con.execute(
        "SELECT COALESCE(MAX(position),0)+1 FROM playlist_tracks WHERE playlist_id=?", (pid,)
    ).fetchone()[0]
    con.execute(
        "INSERT INTO playlist_tracks (playlist_id,track_id,title,artist,thumbnail,duration,position) VALUES (?,?,?,?,?,?,?)",
        (pid, track.id, track.title, track.artist, track.thumbnail, track.duration, pos),
    )
    con.commit()
    con.close()
    return {"status": "added", "position": pos}


@app.delete("/playlists/{pid}/tracks/{track_id}")
def remove_from_playlist(pid: str, track_id: str):
    con = get_db()
    if not con.execute("SELECT id FROM playlists WHERE id=?", (pid,)).fetchone():
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
    return {"status": "ok", "spotify": bool(SPOTIFY_CLIENT_ID), "lastfm": bool(LASTFM_API_KEY)}


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
    return {"status": "ok", "service": "phonon Music Backend v3.0"}


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