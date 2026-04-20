import { useState } from 'react';
import { fmtDur } from '../utils';
import ContextMenu from './ContextMenu';

export default function PlayerBar({
    currentTrack, isPlaying, isLoading, progress, currentTime, duration, volume,
    onTogglePlay, onSkipPrev, onSkipNext, onSeek, onSetVolume,
    onToggleExpanded, expandedOpen,
    onAddToQueue, onPlayNext, onStartMix, onAddToPlaylist, playlists,
}) {
    const [ctx, setCtx] = useState(null);

    const handleProgressClick = (e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        onSeek((e.clientX - rect.left) / rect.width);
    };

    const handleBarClick = (e) => {
        if (e.target.closest('button') || e.target.closest('input') || e.target.closest('.progress-track')) return;
        onToggleExpanded();
    };

    const openCtx = (e) => {
        e.stopPropagation();
        setCtx({ x: e.clientX, y: e.clientY });
    };

    const buildMenuItems = () => {
        if (!currentTrack) return [{ icon: '♫', label: 'No track playing', fn: () => { } }];
        const items = [
            { icon: '⏭', label: 'Play next', fn: () => onPlayNext && onPlayNext(currentTrack) },
            { icon: '+', label: 'Add to queue', fn: () => onAddToQueue && onAddToQueue(currentTrack) },
            { icon: '🔀', label: 'Start mix', fn: () => onStartMix && onStartMix(currentTrack) },
        ];
        if (playlists?.length > 0) {
            items.push({ divider: true });
            items.push({ icon: '♫', label: 'Add to playlist', fn: () => onAddToPlaylist && onAddToPlaylist(currentTrack) });
        }
        items.push({ divider: true });
        items.push({
            icon: '◎', label: 'View artist',
            fn: () => { if (currentTrack?.artist) window.dispatchEvent(new CustomEvent('search', { detail: currentTrack.artist })); },
        });
        return items;
    };

    return (
        <>
            <div className="player-bar" onClick={handleBarClick} style={{ cursor: 'pointer' }}>
                {/* Loading shimmer bar across the very top of the player */}
                {isLoading && <div className="player-loading-bar" />}

                <div className="bar-track">
                    <div className="bar-thumb-wrap">
                        {currentTrack?.thumbnail ? (
                            <img
                                className={`bar-thumb${isLoading ? ' bar-thumb-loading' : ''}`}
                                src={currentTrack.thumbnail}
                                alt=""
                            />
                        ) : (
                            <div className={`bar-thumb${isLoading ? ' bar-thumb-loading' : ''}`} style={{ background: 'var(--surface3)' }} />
                        )}
                        {isLoading && <div className="bar-thumb-spinner" />}
                    </div>
                    <div style={{ minWidth: 0 }}>
                        <div className={`bar-title${isLoading ? ' bar-title-loading' : ''}`}>
                            {currentTrack?.title || 'Pulse'}
                        </div>
                        <div className="bar-artist">
                            {isLoading
                                ? <span className="bar-loading-label">Loading…</span>
                                : (currentTrack?.artist || currentTrack?.uploader || 'Select a track to play')
                            }
                        </div>
                    </div>
                </div>

                <div className="bar-controls" onClick={e => e.stopPropagation()}>
                    <div className="bar-buttons">
                        <button className="ctrl" onClick={onSkipPrev} title="Restart / Previous">⏮</button>
                        <button className="ctrl play" onClick={onTogglePlay} disabled={isLoading}>
                            {isLoading
                                ? <span className="ctrl-spinner" />
                                : (isPlaying ? '⏸' : '▶')
                            }
                        </button>
                        <button className="ctrl" onClick={onSkipNext} title="Next">⏭</button>
                    </div>
                    <div className="bar-progress">
                        <span className="bar-time">{fmtDur(Math.floor(currentTime))}</span>
                        <div className="progress-track" onClick={handleProgressClick}>
                            <div className="progress-fill" style={{ width: `${progress * 100}%` }} />
                            {/* Indeterminate shimmer overlay while loading */}
                            {isLoading && <div className="progress-loading-shimmer" />}
                        </div>
                        <span className="bar-time">{fmtDur(Math.floor(duration)) || '0:00'}</span>
                    </div>
                </div>

                <div className="bar-right" onClick={e => e.stopPropagation()}>
                    <div className="vol-wrap">
                        <span className="vol-icon">♪</span>
                        <input
                            type="range" min="0" max="1" step="0.01" value={volume}
                            onChange={e => onSetVolume(parseFloat(e.target.value))}
                        />
                    </div>
                    <button
                        className="t-more-btn"
                        style={{ opacity: 1, marginRight: 4 }}
                        onClick={openCtx}
                        title="More options"
                    >⋯</button>
                </div>

                <button
                    className="bar-expand-btn"
                    onClick={e => { e.stopPropagation(); onToggleExpanded(); }}
                    title={expandedOpen ? 'Collapse' : 'Expand'}
                >
                    {expandedOpen ? '▽' : '△'}
                </button>
            </div>

            {ctx && (
                <ContextMenu
                    items={buildMenuItems()}
                    position={ctx}
                    onClose={() => setCtx(null)}
                />
            )}
        </>
    );
}