"""
Music Backend Server — FastAPI v3.0
- Spotify Web API for search (better results, real artist names)
- Last.fm for related tracks + personalised suggestions
- yt-dlp for audio streaming (YouTube as transport only)
- All v2 bugs fixed
"""

import os, sqlite3, uuid, re, hashlib, threading, time as _time
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import yt_dlp, requests as req

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
# Songs ≤ 10 min are cached as opus/webm files in ~/.phonon_cache so repeat
# playback is instant (served as a local file:// URL that the signed YT URL
# redirects to, but more practically we serve it via /cache/<id> endpoint).
CACHE_DIR = Path(os.getenv("phonon_CACHE_DIR", Path.home() / ".phonon_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_MAX_DURATION_S = 600  # 10 minutes
CACHE_MAX_SIZE_GB = float(os.getenv("phonon_CACHE_MAX_GB", "2"))
_cache_lock = threading.Lock()
_download_tasks: dict[str, threading.Event] = {}  # video_id -> Event (done when set)


def _cache_path(yt_id: str) -> Path:
    return CACHE_DIR / f"{yt_id}.opus"


def _cache_exists(yt_id: str) -> bool:
    p = _cache_path(yt_id)
    return p.exists() and p.stat().st_size > 0


def _prune_cache():
    """Remove oldest files if cache exceeds CACHE_MAX_SIZE_GB."""
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
    """Download audio in the background and save to cache. Thread-safe."""
    if _cache_exists(yt_id):
        return
    if duration and duration > CACHE_MAX_DURATION_S:
        return  # too long — don't cache
    with _cache_lock:
        if yt_id in _download_tasks:
            return  # already downloading
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
            # yt-dlp may append .opus extension
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
# Set these as environment variables before starting the server:
#   Spotify (free):  https://developer.spotify.com/dashboard
#   Last.fm  (free): https://www.last.fm/api/account/create
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
    import time

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    if (
        _spotify_token["access_token"]
        and time.time() < _spotify_token["expires_at"] - 30
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
        _spotify_token["expires_at"] = time.time() + d["expires_in"]
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
        "id": item["id"],  # Spotify ID used as track ID throughout
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
    """Strip YouTube channel suffixes to produce a usable artist name."""
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
    """Return the YouTube video ID for the best match to query. Prefers YouTube Music."""
    for search_prefix in (f"ytmsearch1:", "ytsearch1:"):
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
    """
    Get audio stream URL. We request formats that support byte-range requests
    (the &range= param style) so the browser can seek instantly without buffering
    the gap between old and new position.
    Prefer opus/webm at 128kbps then any bestaudio.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    # Try formats that YouTube serves with itag support for range seeking
    for fmt in (
        "bestaudio[ext=webm][acodec=opus]",
        "bestaudio[ext=m4a]",
        "bestaudio[ext=webm]",
        "bestaudio/best",
    ):
        try:
            opts = {
                **YDL_OPTS,
                "format": fmt,
                # Request highest quality within reason
                "postprocessors": [],
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                stream_url = info.get("url", "")
                # YouTube signed URLs that include &range= support instant seek
                # All yt-dlp audio URLs should work; we just confirm we have one
                if stream_url:
                    return stream_url, info
        except Exception:
            continue
    raise Exception("No stream URL found")


def yt_fallback_search(query: str, limit: int = 15) -> list[dict]:
    """YouTube Music search — better results than plain YouTube for music queries."""
    # Try YouTube Music first (ytmsearch), fall back to plain YouTube
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
    # Plain YouTube fallback
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
    {
        "id": "fJ9rUzIMcZQ",
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "duration": 354,
        "thumbnail": "https://i.ytimg.com/vi/fJ9rUzIMcZQ/mqdefault.jpg",
        "source": "mock",
    },
    {
        "id": "dQw4w9WgXcQ",
        "title": "Never Gonna Give You Up",
        "artist": "Rick Astley",
        "duration": 213,
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
        "source": "mock",
    },
    {
        "id": "JGwWNGJdvx8",
        "title": "Shape of You",
        "artist": "Ed Sheeran",
        "duration": 234,
        "thumbnail": "https://i.ytimg.com/vi/JGwWNGJdvx8/mqdefault.jpg",
        "source": "mock",
    },
    {
        "id": "kXYiU_JCYtU",
        "title": "Numb",
        "artist": "Linkin Park",
        "duration": 187,
        "thumbnail": "https://i.ytimg.com/vi/kXYiU_JCYtU/mqdefault.jpg",
        "source": "mock",
    },
    {
        "id": "hTWKbfoikeg",
        "title": "Smells Like Teen Spirit",
        "artist": "Nirvana",
        "duration": 301,
        "thumbnail": "https://i.ytimg.com/vi/hTWKbfoikeg/mqdefault.jpg",
        "source": "mock",
    },
    {
        "id": "YR5ApYxkU-U",
        "title": "Blinding Lights",
        "artist": "The Weeknd",
        "duration": 200,
        "thumbnail": "https://i.ytimg.com/vi/YR5ApYxkU-U/mqdefault.jpg",
        "source": "mock",
    },
]


def mock_search(q: str, limit: int = 10) -> list[dict]:
    q = q.lower()
    res = [
        t for t in MOCK_TRACKS if q in t["title"].lower() or q in t["artist"].lower()
    ]
    return (res or MOCK_TRACKS)[:limit]


# ── Search ─────────────────────────────────────────────────────────────────────


@app.get("/search")
def search(q: str, limit: int = 15):
    """
    Search priority:
      1. Spotify — best quality metadata, real artist names, popularity-ranked
      2. YouTube fallback with channel-name cleaning (no Spotify credentials)
      3. Mock (fully offline)

    YouTube IDs are NOT resolved at search time. The /stream endpoint
    handles resolution lazily so search stays fast.
    """
    if not q.strip():
        raise HTTPException(400, "Query cannot be empty")

    results = spotify_search(q, limit)
    if results:
        return {
            "query": q,
            "results": results,
            "count": len(results),
            "source": "spotify",
        }

    yt = yt_fallback_search(q, limit)
    if yt:
        return {"query": q, "results": yt, "count": len(yt), "source": "youtube"}

    fallback = mock_search(q, limit)
    return {"query": q, "results": fallback, "count": len(fallback), "source": "mock"}


# ── Stream ─────────────────────────────────────────────────────────────────────


@app.get("/cache/{yt_id}")
def serve_cached(yt_id: str):
    """Serve a locally cached opus file for instant repeat playback."""
    p = _cache_path(yt_id)
    if not p.exists():
        raise HTTPException(404, "Not cached")
    return FileResponse(
        str(p),
        media_type="audio/ogg",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
        },
    )


@app.get("/stream/{track_id}")
def stream(track_id: str, title: Optional[str] = None, artist: Optional[str] = None):
    """
    Resolve track_id to an audio stream.

    If the track is already cached locally (from a previous play), we serve
    the cached file directly — this makes repeat plays instant.
    For Spotify IDs we search YouTube Music for "artist title audio".
    After resolving, we kick off a background download to cache the file
    for next time (only for tracks ≤ 10 minutes).
    """
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

        # ── Cache hit: serve immediately, no yt-dlp needed ────────────────────
        if _cache_exists(yt_id):
            print(f"Cache HIT: {yt_id} ({resolved_title})")
            # We still need metadata; get it from DB history if available
            con = get_db()
            row = con.execute(
                "SELECT title, artist, thumbnail FROM history WHERE track_id=? ORDER BY played_at DESC LIMIT 1",
                (track_id,),
            ).fetchone()
            con.close()
            cached_title = resolved_title or (row["title"] if row else None) or yt_id
            cached_artist = resolved_artist or (row["artist"] if row else None) or ""
            cached_thumb = row["thumbnail"] if row else None
            # Build a local cache URL that the browser can stream with range support
            cache_url = f"/cache/{yt_id}"
            return {
                "id": track_id,
                "yt_id": yt_id,
                "title": cached_title,
                "artist": cached_artist,
                "duration": None,
                "stream_url": cache_url,
                "thumbnail": cached_thumb,
                "format": "opus",
                "bitrate": 128,
                "cached": True,
            }

        # ── Cache miss: fetch live stream URL ─────────────────────────────────
        stream_url, info = yt_get_stream(yt_id)

        resolved_title = resolved_title or info.get("title")
        resolved_artist = resolved_artist or _clean_channel(
            info.get("uploader") or info.get("channel") or ""
        )
        duration = info.get("duration")

        # Log to history
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

        # Kick off background cache download for short tracks
        if not duration or duration <= CACHE_MAX_DURATION_S:
            _download_to_cache(yt_id, stream_url, duration)

        return {
            "id": track_id,
            "yt_id": yt_id,
            "title": resolved_title,
            "artist": resolved_artist,
            "duration": duration,
            "stream_url": stream_url,
            "thumbnail": info.get("thumbnail"),
            "format": info.get("ext"),
            "bitrate": info.get("abr"),
            "cached": False,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Related tracks ─────────────────────────────────────────────────────────────


@app.get("/related/{track_id}")
def related(track_id: str, title: Optional[str] = None, artist: Optional[str] = None):
    """
    Related track priority:
      1. Spotify recommendations (if track_id is a Spotify ID)
      2. Last.fm similar tracks (if artist + title provided)
      3. Spotify search for "artist similar" as a last resort
    """
    results: list[dict] = []
    source = "none"

    # 2. Last.fm similar
    results = lastfm_similar(artist, title, limit=12)
    if results:
        source = "lastfm"

    # 3. Spotify search fallback
    if not results and artist:
        results = spotify_search(f"{artist}", limit=10)
        # Filter out the current track itself
        results = [r for r in results if r.get("id") != track_id][:10]
        if results:
            source = "spotify_artist"

    print(
        f"Related tracks for '{artist} - {title}' (ID: {track_id}): {len(results)} found via {source}"
    )
    return {"related": results, "source": source}


# ── Personalised suggestions ───────────────────────────────────────────────────


@app.get("/suggestions")
def suggestions(limit: int = 12):
    """
    Build personalised suggestions from listening history.
    Uses Spotify recommendations seeded with most-played tracks,
    with Last.fm as fallback.
    """
    con = get_db()
    rows = con.execute(
        """
        SELECT track_id, title, artist, thumbnail, COUNT(*) as plays
        FROM history
        GROUP BY track_id
        ORDER BY plays DESC, MAX(played_at) DESC
        LIMIT 10
    """
    ).fetchall()
    con.close()

    if not rows:
        top = lastfm_top_tracks("pop", limit)
        return {
            "suggestions": top or mock_search("top hits", limit),
            "source": "charts",
        }

    # Look up Spotify IDs for top played tracks
    spotify_ids: list[str] = []
    for row in rows[:5]:
        # If track_id is already a Spotify ID (22 chars), use it directly
        if len(row["track_id"]) == 22 and re.match(r"^[A-Za-z0-9]+$", row["track_id"]):
            spotify_ids.append(row["track_id"])
        else:
            # YouTube ID — look up on Spotify
            sp = spotify_search(f"{row['artist']} {row['title']}", limit=1)
            if sp and sp[0].get("spotify_id"):
                spotify_ids.append(sp[0]["spotify_id"])

    if spotify_ids:
        recs = spotify_recommendations(spotify_ids, limit=limit)
        if recs:
            return {"suggestions": recs, "source": "spotify_personalised"}

    # Last.fm fallback
    if rows:
        print(
            f"Spotify recommendations unavailable; falling back to Last.fm for personalised suggestions based on top track '{rows[0]['artist']} - {rows[0]['title']}'"
        )
        sim = lastfm_similar(
            rows[0]["artist"] or "", rows[0]["title"] or "", limit=limit
        )
        if sim:
            return {"suggestions": sim, "source": "lastfm_personalised"}

    return {"suggestions": [], "source": "none"}


# ── History ────────────────────────────────────────────────────────────────────


@app.get("/history")
def history(limit: int = 100):
    con = get_db()
    rows = con.execute(
        "SELECT * FROM history ORDER BY played_at DESC LIMIT ?", (limit,)
    ).fetchall()
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
    is_auto: bool = False  # BUG FIX: was _auto which Pydantic v2 ignores


def _resequence_queue(con):
    """Re-number queue positions 1,2,3… with no gaps."""
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
        (
            track.id,
            track.title,
            track.artist,
            track.thumbnail,
            track.duration,
            int(track.is_auto),
        ),
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
    _resequence_queue(con)  # BUG FIX: was missing — caused position gaps
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
    for _ in range(3):  # BUG FIX: retry on UUID collision instead of silently failing
        pid = str(uuid.uuid4())[:8]
        try:
            con.execute(
                "INSERT INTO playlists (id,name) VALUES (?,?)", (pid, body.name)
            )
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
        "SELECT COALESCE(MAX(position),0)+1 FROM playlist_tracks WHERE playlist_id=?",
        (pid,),
    ).fetchone()[0]
    con.execute(
        "INSERT INTO playlist_tracks (playlist_id,track_id,title,artist,thumbnail,duration,position) VALUES (?,?,?,?,?,?,?)",
        (
            pid,
            track.id,
            track.title,
            track.artist,
            track.thumbnail,
            track.duration,
            pos,
        ),
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
        "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
        (pid, track_id),
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


# ── Health / SPA ───────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {
        "status": "ok",
        "spotify": bool(SPOTIFY_CLIENT_ID),
        "lastfm": bool(LASTFM_API_KEY),
    }


@app.get("/")
def index():
    idx = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(idx):
        return FileResponse(idx)
    return {"status": "ok", "service": "phonon Music Backend v3.0"}


# BUG FIX: catch-all so React Router works on direct URL loads
@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    idx = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(idx):
        return FileResponse(idx)
    raise HTTPException(404)


