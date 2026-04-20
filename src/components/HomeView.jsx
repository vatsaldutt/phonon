import { useState, useEffect } from 'react';
import { getGreeting } from '../utils';
import * as api from '../api';

export default function HomeView({ onPlay, onSearch, playlists, onPlaylistOpen, currentTrack }) {
    const [recentTracks, setRecentTracks] = useState([]);
    const [suggestions, setSuggestions] = useState([]);
    const [suggestionsSource, setSuggestionsSource] = useState('');
    const [loading, setLoading] = useState(true);
    const [sugLoading, setSugLoading] = useState(true);

    useEffect(() => {
        async function loadRecent() {
            setLoading(true);
            try {
                const h = await api.getHistory(20);
                const rows = h.history || [];
                const seen = new Set();
                const unique = [];
                for (const r of rows) {
                    if (!seen.has(r.track_id)) {
                        seen.add(r.track_id);
                        unique.push(r);
                    }
                }
                setRecentTracks(unique.slice(0, 8));
            } catch { /* silent */ }
            finally { setLoading(false); }
        }
        loadRecent();
    }, [currentTrack]);

    useEffect(() => {
        async function loadSuggestions() {
            setSugLoading(true);
            try {
                const data = await api.getSuggestions(12);
                setSuggestions(data.suggestions || []);
                setSuggestionsSource(data.source || '');
            } catch { /* silent */ }
            finally { setSugLoading(false); }
        }
        loadSuggestions();
    }, []); // load once on mount — stale is fine for home page

    const greeting = getGreeting();

    const sourceLabel = {
        spotify_personalised: 'Based on your taste',
        lastfm_personalised: 'Based on your taste',
        charts: 'Top charts',
        none: '',
    }[suggestionsSource] || 'Suggested for you';

    return (
        <div className="home-wrap">
            <div className="home-greeting">Good <strong>{greeting}</strong></div>
            <div className="home-sub">What do you want to hear?</div>

            {/* Quick resume */}
            <div className="home-section">
                <div className="home-section-title">Quick resume</div>
                {loading ? (
                    <div style={{ padding: '20px 0' }}><span className="spinner" /></div>
                ) : recentTracks.length === 0 ? (
                    <div className="empty-state" style={{ padding: '24px 0' }}>
                        <div className="empty-glyph">♫</div>
                        <div className="empty-title">Nothing yet</div>
                        <div className="empty-sub">Search to start listening</div>
                    </div>
                ) : (
                    <div className="speed-dial-grid">
                        {recentTracks.map(t => (
                            <div
                                key={t.track_id}
                                className="speed-dial-card"
                                onClick={() => onPlay({ id: t.track_id, title: t.title, artist: t.artist, thumbnail: t.thumbnail })}
                            >
                                <img
                                    className="speed-dial-thumb"
                                    src={t.thumbnail || ''}
                                    alt=""
                                    onError={e => { e.target.style.visibility = 'hidden'; }}
                                />
                                <div className="speed-dial-info">
                                    <div className="speed-dial-title" title={t.title}>{t.title || '—'}</div>
                                    <div className="speed-dial-artist">{t.artist || '—'}</div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Personalised suggestions */}
            <div className="home-section">
                <div className="home-section-title">
                    {sourceLabel}
                    {suggestionsSource && <span className="source-badge" style={{ marginLeft: 8 }}>{suggestionsSource.replace(/_/g, ' ')}</span>}
                </div>
                {sugLoading ? (
                    <div style={{ padding: '20px 0' }}><span className="spinner" /></div>
                ) : suggestions.length === 0 ? (
                    <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: '8px 0' }}>
                        Play some music to get personalised suggestions
                    </div>
                ) : (
                    <div className="suggestions-grid">
                        {suggestions.map((t, i) => (
                            <SuggestionCard
                                key={t.id || t.spotify_id || i}
                                track={t}
                                onPlay={onPlay}
                                onSearch={onSearch}
                            />
                        ))}
                    </div>
                )}
            </div>

            {/* Playlists */}
            {playlists.length > 0 && (
                <div className="home-section">
                    <div className="home-section-title">Your playlists</div>
                    <div className="playlist-speed-grid">
                        {playlists.map(pl => (
                            <div key={pl.id} className="playlist-speed-card" onClick={() => onPlaylistOpen(pl.id, pl.name)}>
                                <div className="playlist-speed-icon">♫</div>
                                <div className="playlist-speed-name">{pl.name}</div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Genre browse */}
            <div className="home-section">
                <div className="home-section-title">Browse genres</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {['Top hits', 'Chill', 'Hip hop', 'Rock', 'Jazz', 'Electronic', 'Indie', 'Pop', 'Classical', 'R&B'].map(g => (
                        <button
                            key={g}
                            onClick={() => onSearch(g)}
                            style={{
                                background: 'var(--surface)',
                                border: 'none',
                                borderRadius: 'var(--radius-pill)',
                                color: 'var(--text-dim)',
                                fontFamily: 'var(--font-mono)',
                                fontSize: 10,
                                letterSpacing: '0.1em',
                                textTransform: 'uppercase',
                                padding: '7px 14px',
                                cursor: 'pointer',
                                transition: 'all 0.15s',
                            }}
                            onMouseEnter={e => { e.target.style.background = 'var(--surface2)'; e.target.style.color = 'var(--text-mid)'; }}
                            onMouseLeave={e => { e.target.style.background = 'var(--surface)'; e.target.style.color = 'var(--text-dim)'; }}
                        >
                            {g}
                        </button>
                    ))}
                </div>
            </div>
        </div>
    );
}

function SuggestionCard({ track, onPlay, onSearch }) {
    // If no YouTube ID yet (Spotify-sourced), play triggers stream resolution in usePlayer
    const handlePlay = () => onPlay({ id: track.id || track.spotify_id, title: track.title, artist: track.artist, thumbnail: track.thumbnail });
    const handleArtist = (e) => { e.stopPropagation(); onSearch(track.artist); };

    return (
        <div className="suggestion-card" onClick={handlePlay}>
            {track.thumbnail ? (
                <img
                    className="suggestion-thumb"
                    src={track.thumbnail}
                    alt=""
                    onError={e => { e.target.style.visibility = 'hidden'; }}
                />
            ) : (
                <div className="suggestion-thumb suggestion-thumb-empty" />
            )}
            <div className="suggestion-info">
                <div className="suggestion-title" title={track.title}>{track.title || '—'}</div>
                <div className="suggestion-artist" onClick={handleArtist} title={`Search ${track.artist}`}>
                    {track.artist || '—'}
                </div>
            </div>
            <button className="suggestion-play-btn">▶</button>
        </div>
    );
}