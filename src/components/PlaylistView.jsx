import { useState, useEffect, useRef } from 'react';
import TrackTable from './TrackTable';
import ContextMenu from './ContextMenu';
import * as api from '../api';

export default function PlaylistView({
    pid, name, currentTrack,
    onPlayFromPlaylist, onShufflePlaylist, onQueuePlaylistAfterCurrent,
    onAddToQueue, onPlayNext, onStartMix, onAddToPlaylist,
    onDeletePlaylist, onRenamePlaylist, playlists, toast,
}) {
    const [tracks, setTracks] = useState([]);
    const [loading, setLoading] = useState(true);
    const [related, setRelated] = useState([]);
    const [relLoading, setRelLoading] = useState(false);
    const [ctx, setCtx] = useState(null);
    const [editing, setEditing] = useState(false);
    const [editName, setEditName] = useState(name || '');
    const editInputRef = useRef();

    useEffect(() => { setEditName(name || ''); }, [name]);

    useEffect(() => {
        if (!pid) return;
        setLoading(true);
        setRelated([]); // clear stale related when switching playlist
        api.getPlaylist(pid)
            .then(data => setTracks(data.tracks || []))
            .catch(() => toast('Playlist not found'))
            .finally(() => setLoading(false));
    }, [pid]);

    // Load related songs once tracks are loaded — seed from first track
    useEffect(() => {
        if (!tracks.length) return;
        const seed = tracks[0];
        if (!seed) return;
        setRelLoading(true);
        api.getRelated({
            id: seed.track_id,
            title: seed.title,
            artist: seed.artist,
        })
            .then(data => {
                // Filter out tracks already in the playlist
                const inPlaylist = new Set(tracks.map(t => t.track_id));
                const filtered = (data.related || [])
                    .filter(r => r.id && !inPlaylist.has(r.id))
                    .slice(0, 10);
                setRelated(filtered);
            })
            .catch(() => { /* silent */ })
            .finally(() => setRelLoading(false));
    }, [tracks]);

    useEffect(() => {
        if (editing && editInputRef.current) {
            editInputRef.current.focus();
            editInputRef.current.select();
        }
    }, [editing]);

    const normalizedTracks = tracks.map(t => ({
        id: t.track_id,
        title: t.title,
        artist: t.artist,
        thumbnail: t.thumbnail,
        duration: t.duration,
    }));

    const handleRemove = async (track) => {
        try {
            await api.removeFromPlaylist(pid, track.id);
            setTracks(prev => prev.filter(t => t.track_id !== track.id));
            toast('Removed from playlist');
        } catch {
            toast('Could not remove track');
        }
    };

    const commitRename = async () => {
        const trimmed = editName.trim();
        if (!trimmed || trimmed === name) { setEditing(false); return; }
        try {
            await onRenamePlaylist(pid, trimmed);
            setEditing(false);
        } catch {
            toast('Could not rename playlist');
            setEditing(false);
        }
    };

    const moreMenuItems = [
        { icon: '⏭', label: 'Play next', fn: () => onQueuePlaylistAfterCurrent(pid) },
        {
            icon: '🔀', label: 'Start mix',
            fn: () => { if (normalizedTracks.length) onStartMix(normalizedTracks[Math.floor(Math.random() * normalizedTracks.length)]); }
        },
        { divider: true },
        { icon: '✏', label: 'Rename playlist', fn: () => setEditing(true) },
        { icon: '🗑', label: 'Delete playlist', danger: true, fn: () => onDeletePlaylist(pid) },
    ];

    if (loading) return (
        <>
            <div className="view-pad">
                <div className="view-head">
                    <div className="view-eyebrow">Playlist</div>
                    <div className="view-title">{name || '—'}</div>
                </div>
            </div>
            <div className="loading-center"><span className="spinner" /></div>
        </>
    );

    return (
        <>
            <div className="view-pad">
                <div className="view-head">
                    <div className="view-eyebrow">Playlist</div>

                    {editing ? (
                        <div className="pl-rename-wrap">
                            <input
                                ref={editInputRef}
                                className="pl-rename-input"
                                value={editName}
                                onChange={e => setEditName(e.target.value)}
                                onKeyDown={e => {
                                    if (e.key === 'Enter') commitRename();
                                    if (e.key === 'Escape') { setEditing(false); setEditName(name); }
                                }}
                                onBlur={commitRename}
                            />
                        </div>
                    ) : (
                        <div className="view-title">{name || '—'}</div>
                    )}

                    <div className="view-meta">{tracks.length} track{tracks.length !== 1 ? 's' : ''}</div>
                </div>

                <div className="pl-action-bar">
                    <button className="pl-action-btn" disabled={!tracks.length}
                        onClick={() => onPlayFromPlaylist(pid, normalizedTracks[0])}>▶ Play</button>
                    <button className="pl-action-btn" disabled={!tracks.length}
                        onClick={() => onShufflePlaylist(pid)}>🔀 Shuffle</button>
                    <button className="pl-action-btn" onClick={() => setEditing(true)}>✏ Edit</button>
                    <button className="pl-action-btn"
                        onClick={e => setCtx({ x: e.clientX, y: e.clientY })}
                        style={{ padding: '8px 14px' }}>⋯</button>
                </div>
            </div>

            {tracks.length === 0 ? (
                <div className="empty-state">
                    <div className="empty-glyph">♪</div>
                    <div className="empty-title">Empty playlist</div>
                    <div className="empty-sub">Add tracks from search results</div>
                </div>
            ) : (
                <TrackTable
                    tracks={normalizedTracks}
                    currentTrack={currentTrack}
                    onPlay={t => onPlayFromPlaylist(pid, t)}
                    onAddToQueue={onAddToQueue}
                    onPlayNext={onPlayNext}
                    onStartMix={onStartMix}
                    onAddToPlaylist={onAddToPlaylist}
                    onRemoveFromPlaylist={handleRemove}
                    playlists={playlists}
                    inPlaylist
                    startIndex={1}
                />
            )}

            {/* ── Related songs section ──────────────────────────────────────── */}
            {(relLoading || related.length > 0) && (
                <div className="view-pad" style={{ marginTop: 32 }}>
                    <div className="view-head" style={{ marginBottom: 0 }}>
                        <div className="view-eyebrow">Discover</div>
                        <div className="view-title" style={{ fontSize: 20 }}>Recommended for this playlist</div>
                    </div>
                </div>
            )}

            {relLoading && (
                <div className="loading-center" style={{ padding: '24px 0' }}>
                    <span className="spinner" />
                </div>
            )}

            {!relLoading && related.length > 0 && (
                <TrackTable
                    tracks={related.map(r => ({
                        id: r.id || r.spotify_id,
                        title: r.title,
                        artist: r.artist,
                        thumbnail: r.thumbnail,
                        duration: r.duration,
                    }))}
                    currentTrack={currentTrack}
                    onPlay={onAddToQueue ? t => { onPlayNext(t); toast(`Playing next: ${t.title}`); } : undefined}
                    onAddToQueue={onAddToQueue}
                    onPlayNext={onPlayNext}
                    onStartMix={onStartMix}
                    onAddToPlaylist={onAddToPlaylist}
                    playlists={playlists}
                    startIndex={1}
                />
            )}

            {ctx && <ContextMenu items={moreMenuItems} position={ctx} onClose={() => setCtx(null)} />}
        </>
    );
}