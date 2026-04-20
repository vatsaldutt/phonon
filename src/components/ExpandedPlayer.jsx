import { useState } from 'react';
import { fmtDur } from '../utils';
import ContextMenu from './ContextMenu';

export default function ExpandedPlayer({
    open, currentTrack, isPlaying, progress, currentTime, duration, volume,
    queue, queueIndex, orderedQueue,
    onTogglePlay, onSkipPrev, onSkipNext, onSeek, onSetVolume,
    onRemoveFromQueue, onClearQueue, onPlayQueueItem,
    onAddToPlaylist, onPlayNext, onStartMix, playlists,
}) {
    const handleProgressClick = (e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        onSeek((e.clientX - rect.left) / rect.width);
    };

    const ordered = orderedQueue ? orderedQueue(queue) : [
        ...queue.filter(i => !(i._auto || i.is_auto)),
        ...queue.filter(i => i._auto || i.is_auto),
    ];

    const userQ = ordered.filter(i => !(i._auto || i.is_auto));
    const autoQ = ordered.filter(i => i._auto || i.is_auto);

    // Global index maps: track position in ordered array
    const userStartIdx = 0;
    const autoStartIdx = userQ.length;

    return (
        <div className={`player-expanded${open ? ' open' : ''}`}>
            <div
                className="exp-bg"
                style={{ backgroundImage: currentTrack?.thumbnail ? `url('${currentTrack.thumbnail}')` : 'none' }}
            />
            <div className="exp-overlay" />

            <div className="exp-body">
                {/* Art + controls */}
                <div className="exp-art-side">
                    {currentTrack?.thumbnail ? (
                        <img className="exp-art" src={currentTrack.thumbnail} alt="" />
                    ) : (
                        <div className="exp-art" style={{ background: 'var(--surface3)' }} />
                    )}
                    <div className="exp-track-info">
                        <div className="exp-track-title">{currentTrack?.title || '—'}</div>
                        <div className="exp-track-artist">{currentTrack?.artist || currentTrack?.uploader || 'Select a track'}</div>
                    </div>
                    <div className="exp-controls">
                        <div className="exp-buttons">
                            <button className="exp-ctrl" onClick={onSkipPrev}>⏮</button>
                            <button className="exp-ctrl play" onClick={onTogglePlay}>{isPlaying ? '⏸' : '▶'}</button>
                            <button className="exp-ctrl" onClick={onSkipNext}>⏭</button>
                        </div>
                        <div className="exp-progress-wrap">
                            <span className="exp-time">{fmtDur(Math.floor(currentTime))}</span>
                            <div className="exp-ptrack" onClick={handleProgressClick}>
                                <div className="exp-pfill" style={{ width: `${progress * 100}%` }} />
                            </div>
                            <span className="exp-time">{fmtDur(Math.floor(duration)) || '0:00'}</span>
                        </div>
                    </div>
                </div>

                {/* Queue panel */}
                <div className="exp-queue-side">
                    <div className="exp-queue-header">
                        <div>
                            <div className="exp-queue-title">Queue</div>
                            <div className="exp-queue-count">{queue.length} track{queue.length !== 1 ? 's' : ''}</div>
                        </div>
                        <button className="exp-queue-clear" onClick={onClearQueue}>Clear all</button>
                    </div>
                    <div className="exp-queue-list">
                        {queue.length === 0 ? (
                            <div className="q-empty">
                                <div className="q-empty-glyph">≡</div>
                                <div className="q-empty-text">Queue is empty</div>
                                <div className="q-empty-sub">Add tracks from search results</div>
                            </div>
                        ) : (
                            <>
                                {userQ.map((t, i) => (
                                    <QueueItem
                                        key={t.position}
                                        item={t}
                                        orderedIdx={userStartIdx + i}
                                        globalIndex={queueIndex}
                                        currentTrack={currentTrack}
                                        onPlay={onPlayQueueItem}
                                        onRemove={onRemoveFromQueue}
                                        onAddToPlaylist={onAddToPlaylist}
                                        onPlayNext={onPlayNext}
                                        onStartMix={onStartMix}
                                        playlists={playlists}
                                    />
                                ))}
                                {autoQ.length > 0 && (
                                    <>
                                        <div className="q-divider-label">Auto-added</div>
                                        {autoQ.map((t, i) => (
                                            <QueueItem
                                                key={t.position}
                                                item={t}
                                                orderedIdx={autoStartIdx + i}
                                                globalIndex={queueIndex}
                                                currentTrack={currentTrack}
                                                onPlay={onPlayQueueItem}
                                                onRemove={onRemoveFromQueue}
                                                onAddToPlaylist={onAddToPlaylist}
                                                onPlayNext={onPlayNext}
                                                onStartMix={onStartMix}
                                                playlists={playlists}
                                            />
                                        ))}
                                    </>
                                )}
                            </>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}

function QueueItem({ item, orderedIdx, globalIndex, currentTrack, onPlay, onRemove, onAddToPlaylist, onPlayNext, onStartMix, playlists }) {
    const [ctx, setCtx] = useState(null);
    const isCurrent = currentTrack?.id === item.track_id;

    const openCtx = (e) => {
        e.stopPropagation();
        setCtx({ x: e.clientX, y: e.clientY });
    };

    const track = { id: item.track_id, title: item.title, artist: item.artist, thumbnail: item.thumbnail, duration: item.duration };

    const menuItems = [
        { icon: '▶', label: 'Play now', fn: () => onPlay(item, orderedIdx) },
        { icon: '⏭', label: 'Play next', fn: () => onPlayNext && onPlayNext(track) },
        { icon: '🔀', label: 'Start mix', fn: () => onStartMix && onStartMix(track) },
        ...(playlists?.length > 0 ? [
            { divider: true },
            { icon: '♫', label: 'Add to playlist', fn: () => onAddToPlaylist && onAddToPlaylist(track) },
        ] : []),
        { divider: true },
        { icon: '✕', label: 'Remove from queue', danger: true, fn: () => onRemove(item.position) },
        {
            icon: '◎', label: 'View artist',
            fn: () => { if (item.artist) window.dispatchEvent(new CustomEvent('search', { detail: item.artist })); },
        },
    ];

    return (
        <>
            <div
                className={`q-item${isCurrent ? ' q-item-current' : ''}`}
                onClick={() => onPlay(item, orderedIdx)}
            >
                <div className="q-num">
                    {isCurrent ? <span style={{ color: 'rgba(255,255,255,0.75)' }}>▶</span> : orderedIdx + 1}
                </div>
                {item.thumbnail ? (
                    <img className="q-thumb" src={item.thumbnail} alt="" />
                ) : (
                    <div className="q-thumb" style={{ background: 'var(--surface3)' }} />
                )}
                <div className="q-info">
                    <div className={`q-title${isCurrent ? ' q-title-current' : ''}`}>{item.title || '—'}</div>
                    <div className="q-artist">{item.artist || '—'}</div>
                </div>
                <div className="q-dur">{fmtDur(item.duration)}</div>
                <button className="q-more-btn" onClick={openCtx} title="Options">⋯</button>
            </div>
            {ctx && (
                <ContextMenu items={menuItems} position={ctx} onClose={() => setCtx(null)} />
            )}
        </>
    );
}