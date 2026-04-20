import TrackRow from './TrackRow';

export default function TrackTable({ tracks, currentTrack, onPlay, onAddToQueue, onPlayNext, onStartMix, onAddToPlaylist, onRemoveFromPlaylist, onRemoveFromQueue, inPlaylist, inQueue, playlists, startIndex = 1 }) {
    if (!tracks?.length) return null;
    return (
        <table className="track-table">
            <thead>
                <tr>
                    <th style={{ width: 40 }}></th>
                    <th>Track</th>
                    <th style={{ width: 180 }}>Artist</th>
                    <th style={{ width: 56, textAlign: 'right' }}>Time</th>
                    <th style={{ width: 44 }}></th>
                </tr>
            </thead>
            <tbody>
                {tracks.map((t, i) => (
                    <TrackRow
                        key={t.id || t.track_id || i}
                        track={t}
                        index={startIndex + i}
                        currentTrack={currentTrack}
                        onPlay={onPlay}
                        onAddToQueue={onAddToQueue}
                        onPlayNext={onPlayNext}
                        onStartMix={onStartMix}
                        onAddToPlaylist={onAddToPlaylist}
                        onRemoveFromPlaylist={onRemoveFromPlaylist}
                        onRemoveFromQueue={onRemoveFromQueue}
                        inPlaylist={inPlaylist}
                        inQueue={inQueue}
                        playlists={playlists}
                    />
                ))}
            </tbody>
        </table>
    );
}