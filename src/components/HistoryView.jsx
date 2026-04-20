import { useState, useEffect } from 'react';
import { groupHistoryByDate } from '../utils';
import TrackTable from './TrackTable';
import * as api from '../api';

export default function HistoryView({ currentTrack, onPlay, onAddToQueue, onPlayNext, onStartMix, onAddToPlaylist, playlists }) {
    const [groups, setGroups] = useState({});
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function load() {
            setLoading(true);
            try {
                const data = await api.getHistory(100);
                const rows = data.history || [];
                setTotal(rows.length);
                setGroups(groupHistoryByDate(rows));
            } catch (e) { console.error(e); }
            finally { setLoading(false); }
        }
        load();
    }, []);

    if (loading) return (
        <>
            <div className="view-pad"><div className="view-head">
                <div className="view-eyebrow">Playback</div>
                <div className="view-title">History</div>
            </div></div>
            <div className="loading-center"><span className="spinner" /></div>
        </>
    );

    if (total === 0) return (
        <>
            <div className="view-pad"><div className="view-head">
                <div className="view-eyebrow">Playback</div>
                <div className="view-title">History</div>
            </div></div>
            <div className="empty-state">
                <div className="empty-glyph">◷</div>
                <div className="empty-title">Nothing played yet</div>
                <div className="empty-sub">Start listening to build history</div>
            </div>
        </>
    );

    return (
        <>
            <div className="view-pad"><div className="view-head">
                <div className="view-eyebrow">Playback</div>
                <div className="view-title">History</div>
                <div className="view-meta">{total} tracks</div>
            </div></div>
            {Object.entries(groups).map(([label, items]) => (
                <div key={label}>
                    <div className="history-group-label">{label}</div>
                    <TrackTable
                        tracks={items.map(t => ({ id: t.track_id, title: t.title, artist: t.artist, thumbnail: t.thumbnail }))}
                        currentTrack={currentTrack}
                        onPlay={onPlay}
                        onAddToQueue={onAddToQueue}
                        onPlayNext={onPlayNext}
                        onStartMix={onStartMix}
                        onAddToPlaylist={onAddToPlaylist}
                        playlists={playlists}
                        startIndex={1}
                    />
                </div>
            ))}
        </>
    );
}