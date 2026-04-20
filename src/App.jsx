import { useState, useEffect, useCallback, useRef } from 'react';
import './index.css';
import { usePlayer } from './hooks/usePlayer';
import { useToast } from './hooks/useToast';
import * as api from './api';

import PlayerBar from './components/PlayerBar';
import ExpandedPlayer from './components/ExpandedPlayer';
import HomeView from './components/HomeView';
import SearchView from './components/SearchView';
import HistoryView from './components/HistoryView';
import PlaylistView from './components/PlaylistView';
import PlaylistPicker from './components/PlaylistPicker';

export default function App() {
    const player = usePlayer();
    const { message: toastMsg, visible: toastVisible, toast } = useToast();

    const [view, setView] = useState('home');
    const [playlistId, setPlaylistId] = useState(null);
    const [playlistName, setPlaylistName] = useState('');
    const [expandedOpen, setExpandedOpen] = useState(false);

    const [playlists, setPlaylists] = useState([]);
    const [newPlName, setNewPlName] = useState('');

    const [searchQuery, setSearchQuery] = useState('');
    const [searchInput, setSearchInput] = useState('');
    const [searchResults, setSearchResults] = useState([]);
    const [searchSource, setSearchSource] = useState('');
    const [searchLoading, setSearchLoading] = useState(false);

    const lastPlRef = useRef({ id: null, name: '' });
    const [plSaveBanner, setPlSaveBanner] = useState(null);
    const plSaveBannerTimer = useRef(null);
    const [plPicker, setPlPicker] = useState(null);

    useEffect(() => { loadPlaylists(); }, []);

    useEffect(() => {
        const handler = (e) => triggerSearch(e.detail);
        window.addEventListener('search', handler);
        return () => window.removeEventListener('search', handler);
    }, []);

    useEffect(() => { setExpandedOpen(false); }, [view, playlistId]);

    const loadPlaylists = async () => {
        const data = await api.getPlaylists();
        setPlaylists(data.playlists || []);
    };

    const triggerSearch = useCallback(async (q) => {
        if (!q?.trim()) return;
        setSearchInput(q);
        setSearchQuery(q);
        setSearchLoading(true);
        setView('search');
        try {
            const data = await api.searchTracks(q, 15);
            setSearchResults(data.results || []);
            setSearchSource(data.source || '');
        } catch { toast('Search failed'); }
        finally { setSearchLoading(false); }
    }, [toast]);

    const doSearch = () => triggerSearch(searchInput);
    const handleKeyDown = (e) => { if (e.key === 'Enter') doSearch(); };

    const showView = (v, pid = null, pname = '') => {
        setView(v);
        if (v === 'playlist') { setPlaylistId(pid); setPlaylistName(pname); }
    };

    // ── Playlists ──────────────────────────────────────────────────────────────
    const createPlaylist = async () => {
        if (!newPlName.trim()) return;
        const name = newPlName.trim();
        await api.createPlaylist(name);
        setNewPlName('');
        await loadPlaylists();
        toast(`Created "${name}"`);
    };

    const deletePlaylist = async (pid) => {
        await api.deletePlaylist(pid);
        if (playlistId === pid) showView('home');
        if (lastPlRef.current.id === pid) lastPlRef.current = { id: null, name: '' };
        await loadPlaylists();
        toast('Playlist deleted');
    };

    const renamePlaylist = async (pid, newName) => {
        await api.renamePlaylist(pid, newName);
        if (playlistId === pid) setPlaylistName(newName);
        await loadPlaylists();
        toast(`Renamed to "${newName}"`);
    };

    // ── Add to playlist ────────────────────────────────────────────────────────
    const handleAddToPlaylist = useCallback((track, clickPos = null) => {
        if (!playlists.length) { toast('Create a playlist first'); return; }
        const remembered = lastPlRef.current;
        if (remembered.id && playlists.find(p => p.id === remembered.id)) {
            doAddToPlaylist(remembered.id, track);
            return;
        }
        const pos = clickPos || { x: window.innerWidth / 2 - 100, y: window.innerHeight / 2 - 100 };
        setPlPicker({ track, position: pos });
    }, [playlists]);

    const doAddToPlaylist = async (pid, track) => {
        const pl = playlists.find(p => p.id === pid);
        const name = pl?.name || pid;
        await api.addToPlaylist(pid, track);
        lastPlRef.current = { id: pid, name };
        clearTimeout(plSaveBannerTimer.current);
        setPlSaveBanner({ track, playlistName: name });
        plSaveBannerTimer.current = setTimeout(() => setPlSaveBanner(null), 4000);
        toast(`Added to "${name}"`);
    };

    const handlePlPickerSelect = (pl) => {
        if (plPicker?.track) doAddToPlaylist(pl.id, plPicker.track);
        setPlPicker(null);
    };

    const handleChangeBannerPlaylist = () => {
        const track = plSaveBanner?.track;
        setPlSaveBanner(null);
        clearTimeout(plSaveBannerTimer.current);
        lastPlRef.current = { id: null, name: '' };
        if (track) setPlPicker({ track, position: { x: window.innerWidth / 2 - 100, y: window.innerHeight / 2 - 100 } });
    };

    // ── Player wrappers ────────────────────────────────────────────────────────

    // Generic play — passes fromPlaylistId: null so playlist queue gets cleared
    // if one was active (queue-reset logic lives in usePlayer)
    const handlePlay = useCallback(async (track) => {
        await player.playTrack(track, { fromPlaylistId: null });
        toast(`▶  ${track.title || track.id}`);
    }, [player, toast]);

    const handleAddToQueue = useCallback(async (track) => {
        await player.addToQueue(track);
        toast(`+ Queue: ${track.title}`);
    }, [player, toast]);

    const handlePlayNext = useCallback(async (track) => {
        await player.addToQueue(track, true);
        toast(`Playing next: ${track.title}`);
    }, [player, toast]);

    const handleStartMix = useCallback(async (track) => {
        toast('Starting mix…');
        await player.startMix(track);
        toast(`🔀 Mix: ${track.title}`);
    }, [player, toast]);

    // Play from inside a playlist — passes the playlist ID so the reset logic
    // knows this play is legitimately within the active playlist queue
    const handlePlayFromPlaylist = useCallback(async (pid, track) => {
        await player.loadPlaylistIntoQueue(pid, track, false);
        toast(`▶  ${track?.title || 'Playlist'}`);
    }, [player, toast]);

    const handleShufflePlaylist = useCallback(async (pid) => {
        await player.loadPlaylistIntoQueue(pid, null, true);
        toast('🔀 Shuffle play');
    }, [player, toast]);

    const handleQueuePlaylistAfterCurrent = useCallback(async (pid) => {
        await player.queuePlaylistAfterCurrent(pid);
        toast('Playlist added to queue');
    }, [player, toast]);

    const handleQueueItemPlay = useCallback(async (item, orderedIdx) => {
        await player.playQueueItem(item, orderedIdx);
    }, [player]);

    const handleClearQueue = useCallback(async () => {
        await player.clearQueue();
        toast('Queue cleared');
    }, [player, toast]);

    const toggleExpanded = useCallback(() => setExpandedOpen(o => !o), []);

    return (
        <>
            <header className="header">
                <div className="logo">Phonon <em>CRYSTAL</em></div>
                <div className="search-wrap">
                    <input
                        type="text"
                        placeholder="Search artists, tracks…"
                        value={searchInput}
                        onChange={e => setSearchInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        autoComplete="off"
                    />
                    <button onClick={doSearch}>Search</button>
                </div>
                <div style={{ width: 40 }} />
            </header>

            <aside className="sidebar">
                <div className="sidebar-section">
                    <div className="sidebar-label">Library</div>
                    <button className={`nav-btn${view === 'home' ? ' active' : ''}`} onClick={() => showView('home')}>
                        <span className="nav-icon">⌂</span> Home
                    </button>
                    <button className={`nav-btn${view === 'search' ? ' active' : ''}`} onClick={() => showView('search')}>
                        <span className="nav-icon">◎</span> Search
                    </button>
                    <button className={`nav-btn${view === 'history' ? ' active' : ''}`} onClick={() => showView('history')}>
                        <span className="nav-icon">◷</span> History
                    </button>
                </div>
                <hr className="sidebar-divider" />
                <div className="sidebar-playlists">
                    <div className="sidebar-label">Playlists</div>
                    <div className="pl-new">
                        <input
                            type="text"
                            placeholder="New playlist…"
                            value={newPlName}
                            onChange={e => setNewPlName(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') createPlaylist(); }}
                        />
                        <button onClick={createPlaylist}>+</button>
                    </div>
                    {playlists.map(pl => (
                        <div key={pl.id} className={`pl-item${view === 'playlist' && playlistId === pl.id ? ' active' : ''}`}>
                            <span className="pl-item-icon">♫</span>
                            <span className="pl-item-name" onClick={() => showView('playlist', pl.id, pl.name)}>{pl.name}</span>
                            <button
                                className="pl-item-del"
                                title={`Play "${pl.name}"`}
                                style={{ color: 'var(--text-mid)', fontSize: 13 }}
                                onClick={e => { e.stopPropagation(); handlePlayFromPlaylist(pl.id, null); toast(`▶ ${pl.name}`); }}
                            >▶</button>
                        </div>
                    ))}
                </div>
            </aside>

            <main className="main">
                {view === 'home' && (
                    <HomeView
                        onPlay={handlePlay}
                        onSearch={q => triggerSearch(q)}
                        playlists={playlists}
                        onPlaylistOpen={(pid, name) => showView('playlist', pid, name)}
                        currentTrack={player.currentTrack}
                    />
                )}
                {view === 'search' && (
                    <SearchView
                        results={searchResults}
                        query={searchQuery}
                        source={searchSource}
                        loading={searchLoading}
                        currentTrack={player.currentTrack}
                        onPlay={handlePlay}
                        onAddToQueue={handleAddToQueue}
                        onPlayNext={handlePlayNext}
                        onStartMix={handleStartMix}
                        onAddToPlaylist={handleAddToPlaylist}
                        playlists={playlists}
                    />
                )}
                {view === 'history' && (
                    <HistoryView
                        currentTrack={player.currentTrack}
                        onPlay={handlePlay}
                        onAddToQueue={handleAddToQueue}
                        onPlayNext={handlePlayNext}
                        onStartMix={handleStartMix}
                        onAddToPlaylist={handleAddToPlaylist}
                        playlists={playlists}
                    />
                )}
                {view === 'playlist' && (
                    <PlaylistView
                        pid={playlistId}
                        name={playlistName}
                        currentTrack={player.currentTrack}
                        onPlayFromPlaylist={handlePlayFromPlaylist}
                        onShufflePlaylist={handleShufflePlaylist}
                        onQueuePlaylistAfterCurrent={handleQueuePlaylistAfterCurrent}
                        onAddToQueue={handleAddToQueue}
                        onPlayNext={handlePlayNext}
                        onStartMix={handleStartMix}
                        onAddToPlaylist={handleAddToPlaylist}
                        onDeletePlaylist={deletePlaylist}
                        onRenamePlaylist={renamePlaylist}
                        playlists={playlists}
                        toast={toast}
                    />
                )}
            </main>

            <PlayerBar
                currentTrack={player.currentTrack}
                isLoading={player.isLoading}
                isPlaying={player.isPlaying}
                progress={player.progress}
                currentTime={player.currentTime}
                duration={player.duration}
                volume={player.volume}
                onTogglePlay={player.togglePlay}
                onSkipPrev={player.skipPrev}
                onSkipNext={player.skipNext}
                onSeek={player.seek}
                onSetVolume={player.setVolume}
                onToggleExpanded={toggleExpanded}
                expandedOpen={expandedOpen}
                onAddToQueue={handleAddToQueue}
                onPlayNext={handlePlayNext}
                onStartMix={handleStartMix}
                onAddToPlaylist={handleAddToPlaylist}
                playlists={playlists}
            />

            <ExpandedPlayer
                open={expandedOpen}
                currentTrack={player.currentTrack}
                isPlaying={player.isPlaying}
                progress={player.progress}
                currentTime={player.currentTime}
                duration={player.duration}
                volume={player.volume}
                queue={player.queue}
                queueIndex={player.queueIndex}
                orderedQueue={player.orderedQueue}
                onTogglePlay={player.togglePlay}
                onSkipPrev={player.skipPrev}
                onSkipNext={player.skipNext}
                onSeek={player.seek}
                onSetVolume={player.setVolume}
                onRemoveFromQueue={player.removeFromQueue}
                onClearQueue={handleClearQueue}
                onPlayQueueItem={handleQueueItemPlay}
                onAddToPlaylist={handleAddToPlaylist}
                onPlayNext={handlePlayNext}
                onStartMix={handleStartMix}
                playlists={playlists}
            />

            {plPicker && (
                <PlaylistPicker
                    playlists={playlists}
                    position={plPicker.position}
                    onSelect={handlePlPickerSelect}
                    onClose={() => setPlPicker(null)}
                />
            )}

            {plSaveBanner && (
                <div className="pl-save-banner">
                    <span>Saved <strong>"{plSaveBanner.track?.title}"</strong> to <strong>{plSaveBanner.playlistName}</strong></span>
                    <button className="pl-save-banner-change" onClick={handleChangeBannerPlaylist}>Change</button>
                </div>
            )}

            <div className={`toast${toastVisible ? ' show' : ''}`}>{toastMsg}</div>
        </>
    );
}