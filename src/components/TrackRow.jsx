import { useState } from 'react';
import { fmtDur } from '../utils';
import ContextMenu from './ContextMenu';

export default function TrackRow({
  track, index, currentTrack,
  onPlay, onAddToQueue, onPlayNext, onStartMix,
  onAddToPlaylist, onRemoveFromPlaylist, onRemoveFromQueue,
  inPlaylist, inQueue, playlists,
}) {
  const [ctx, setCtx] = useState(null);
  const isPlaying = currentTrack?.id === track.id;

  const openCtx = (e) => {
    e.stopPropagation();
    e.preventDefault();
    setCtx({ x: e.clientX, y: e.clientY });
  };

  const buildMenuItems = () => {
    const items = [
      { icon: '▶', label: 'Play now', fn: () => onPlay(track) },
      { icon: '⏭', label: 'Play next', fn: () => onPlayNext && onPlayNext(track) },
      { icon: '+', label: 'Add to queue', fn: () => onAddToQueue && onAddToQueue(track) },
      { icon: '🔀', label: 'Start mix', fn: () => onStartMix && onStartMix(track) },
    ];

    if (playlists?.length > 0) {
      items.push({ divider: true });
      items.push({ icon: '♫', label: 'Add to playlist', fn: () => onAddToPlaylist && onAddToPlaylist(track) });
    }

    if (inPlaylist && onRemoveFromPlaylist) {
      items.push({ icon: '✕', label: 'Remove from playlist', danger: true, fn: () => onRemoveFromPlaylist(track) });
    }

    if (inQueue && onRemoveFromQueue) {
      items.push({ icon: '✕', label: 'Remove from queue', danger: true, fn: () => onRemoveFromQueue(track) });
    }

    items.push({ divider: true });
    items.push({
      icon: '◎',
      label: 'View artist',
      fn: () => {
        if (track.artist) window.dispatchEvent(new CustomEvent('search', { detail: track.artist }));
      },
    });

    return items;
  };

  return (
    <>
      <tr
        className={`track-row${isPlaying ? ' is-playing' : ''}`}
        data-track-id={track.id}
        onClick={() => onPlay(track)}
      >
        <td className="t-num">
          <span className="num-glyph">{index}</span>
          <span className="play-glyph">▶</span>
        </td>
        <td>
          <div className="t-info">
            {track.thumbnail ? (
              <img
                className="t-thumb"
                src={track.thumbnail}
                alt=""
                onError={e => { e.target.style.display = 'none'; }}
              />
            ) : (
              <div className="t-thumb" style={{ background: 'var(--surface3)' }} />
            )}
            <div>
              <div className="t-title" title={track.title}>{track.title || '—'}</div>
              <div className="t-sub">{track.artist || '—'}</div>
            </div>
          </div>
        </td>
        <td className="t-artist" title={track.artist}>{track.artist || '—'}</td>
        <td className="t-dur">{fmtDur(track.duration)}</td>
        <td className="t-actions">
          <button className="t-more-btn" onClick={openCtx} title="More options">⋯</button>
        </td>
      </tr>
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