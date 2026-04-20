import { fmtDur } from '../utils';
import TrackTable from './TrackTable';

export default function SearchView({ results, query, source, loading, currentTrack, onPlay, onAddToQueue, onPlayNext, onStartMix, onAddToPlaylist, playlists }) {
    if (loading) return <div className="loading-center"><span className="spinner" /></div>;

    if (!query) return (
        <div className="empty-state">
            <div className="empty-glyph">♫</div>
            <div className="empty-title">Search for music</div>
            <div className="empty-sub">Type above and press enter</div>
        </div>
    );

    if (!results.length) return (
        <div className="empty-state">
            <div className="empty-glyph">◎</div>
            <div className="empty-title">No results</div>
            <div className="empty-sub">Try a different search</div>
        </div>
    );

    const [featured, ...rest] = results;

    return (
        <div>
            {/* Featured card */}
            <div className="search-featured fade-up">
                <div className="search-featured-bg" style={{ backgroundImage: `url('${featured.thumbnail || ''}')` }} />
                <div className="search-featured-art">
                    <img src={featured.thumbnail || ''} alt="" />
                </div>
                <div className="search-featured-info">
                    <div className="featured-label">Best Result</div>
                    <div className="featured-title" title={featured.title}>{featured.title || '—'}</div>
                    <div className="featured-artist">{featured.artist || '—'}</div>
                    <div className="featured-actions">
                        <button className="feat-play-btn" onClick={e => { e.stopPropagation(); onPlay(featured); }}>▶ Play</button>
                        <button className="feat-ghost-btn" onClick={e => { e.stopPropagation(); onAddToQueue(featured); }}>+ Queue</button>
                        <button className="feat-ghost-btn" onClick={e => { e.stopPropagation(); onStartMix(featured); }}>🔀 Mix</button>
                        {playlists.length > 0 && (
                            <button className="feat-ghost-btn" onClick={e => { e.stopPropagation(); onAddToPlaylist(featured); }}>+ Playlist</button>
                        )}
                    </div>
                </div>
                <div className="featured-dur">{fmtDur(featured.duration)}</div>
            </div>

            <div className="search-list-header">
                <span className="search-list-title">{rest.length} more result{rest.length !== 1 ? 's' : ''}</span>
                <span className="source-badge">{source}</span>
            </div>

            <TrackTable
                tracks={rest}
                currentTrack={currentTrack}
                onPlay={onPlay}
                onAddToQueue={onAddToQueue}
                onPlayNext={onPlayNext}
                onStartMix={onStartMix}
                onAddToPlaylist={onAddToPlaylist}
                playlists={playlists}
                startIndex={2}
            />
        </div>
    );
}