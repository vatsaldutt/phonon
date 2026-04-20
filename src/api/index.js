const BASE = '';

export async function searchTracks(q, limit = 15) {
    const r = await fetch(`${BASE}/search?q=${encodeURIComponent(q)}&limit=${limit}`);
    return r.json();
}

// track can be { id, title, artist } — passing title/artist lets the server
// do a better YouTube Music search query (e.g. "Artist Title audio")
export async function getStream(track) {
    const id = typeof track === 'string' ? track : track.id;
    const params = new URLSearchParams({});
    if (track?.title) params.set('title', track.title);
    if (track?.artist) params.set('artist', track.artist);
    const qs = params.toString();
    const r = await fetch(`${BASE}/stream/${id}${qs ? '?' + qs : ''}`);
    if (!r.ok) throw new Error('Stream unavailable');
    return r.json();
}

// Pass title/artist for better Last.fm/Spotify related lookup
export async function getRelated(track) {
    const id = typeof track === 'string' ? track : track.id;
    const params = new URLSearchParams({});
    if (track?.title) params.set('title', track.title);
    if (track?.artist) params.set('artist', track.artist);
    const qs = params.toString();
    const r = await fetch(`${BASE}/related/${id}${qs ? '?' + qs : ''}`);
    return r.json();
}

export async function getSuggestions(limit = 12) {
    const r = await fetch(`${BASE}/suggestions?limit=${limit}`);
    return r.json();
}

export async function getHistory(limit = 100) {
    const r = await fetch(`${BASE}/history?limit=${limit}`);
    return r.json();
}

export async function deleteHistory(id) {
    await fetch(`${BASE}/history/${id}`, { method: 'DELETE' });
}

// Queue
export async function getQueue() {
    const r = await fetch(`${BASE}/queue`);
    return r.json();
}

export async function addToQueue(track) {
    await fetch(`${BASE}/queue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            id: track.id,
            title: track.title || track.id,
            artist: track.artist || null,
            thumbnail: track.thumbnail || null,
            duration: track.duration || null,
            is_auto: track.is_auto || track._auto || false,
        }),
    });
}

export async function removeFromQueue(position) {
    await fetch(`${BASE}/queue/${position}`, { method: 'DELETE' });
}

export async function clearQueue() {
    await fetch(`${BASE}/queue`, { method: 'DELETE' });
}

// Playlists
export async function getPlaylists() {
    const r = await fetch(`${BASE}/playlists`);
    return r.json();
}

export async function createPlaylist(name) {
    const r = await fetch(`${BASE}/playlists`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
    });
    return r.json();
}

export async function renamePlaylist(pid, name) {
    const r = await fetch(`${BASE}/playlists/${pid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
    });
    return r.json();
}

export async function deletePlaylist(pid) {
    await fetch(`${BASE}/playlists/${pid}`, { method: 'DELETE' });
}

export async function getPlaylist(pid) {
    const r = await fetch(`${BASE}/playlists/${pid}`);
    if (!r.ok) throw new Error('Playlist not found');
    return r.json();
}

export async function addToPlaylist(pid, track) {
    await fetch(`${BASE}/playlists/${pid}/tracks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            id: track.id,
            title: track.title || track.id,
            artist: track.artist || null,
            thumbnail: track.thumbnail || null,
            duration: track.duration || null,
        }),
    });
}

export async function removeFromPlaylist(pid, trackId) {
    await fetch(`${BASE}/playlists/${pid}/tracks/${trackId}`, { method: 'DELETE' });
}