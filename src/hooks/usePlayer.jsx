import { useState, useRef, useCallback, useEffect } from 'react';
import * as api from '../api';

// ── Media Session ──────────────────────────────────────────────────────────────
function updateMediaSession(track, isPlaying, handlers = {}) {
    if (!('mediaSession' in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
        title: track?.title || 'Pulse',
        artist: track?.artist || '',
        album: track?.album || '',
        artwork: track?.thumbnail
            ? [
                { src: track.thumbnail, sizes: '320x180', type: 'image/jpeg' },
                { src: track.thumbnail, sizes: '640x360', type: 'image/jpeg' },
            ]
            : [],
    });
    navigator.mediaSession.playbackState = isPlaying ? 'playing' : 'paused';
    const set = (action, fn) => {
        try { navigator.mediaSession.setActionHandler(action, fn); } catch { /* unsupported */ }
    };
    set('play', handlers.play || null);
    set('pause', handlers.pause || null);
    set('previoustrack', handlers.prev || null);
    set('nexttrack', handlers.next || null);
    set('seekto', handlers.seekto || null);
    set('seekforward', handlers.seekforward || null);
    set('seekbackward', handlers.seekbackward || null);
}

// ── Robust seek ────────────────────────────────────────────────────────────────
async function robustSeek(audio, targetTime) {
    if (!audio.duration || targetTime < 0) return;
    const t = Math.min(targetTime, audio.duration - 0.5);
    for (let i = 0; i < audio.buffered.length; i++) {
        if (audio.buffered.start(i) <= t && t <= audio.buffered.end(i)) {
            audio.currentTime = t;
            return;
        }
    }
    const wasPlaying = !audio.paused;
    audio.pause();
    audio.currentTime = t;
    await new Promise((resolve) => {
        let done = false;
        const finish = () => {
            if (done) return;
            done = true;
            audio.removeEventListener('seeked', finish);
            audio.removeEventListener('canplay', finish);
            clearTimeout(fallback);
            resolve();
        };
        const fallback = setTimeout(finish, 3000);
        audio.addEventListener('seeked', finish, { once: true });
        audio.addEventListener('canplay', finish, { once: true });
    });
    if (wasPlaying) {
        try { await audio.play(); } catch { /* autoplay blocked */ }
    }
}

export function usePlayer() {
    const audioRef = useRef(null);
    if (!audioRef.current) audioRef.current = new Audio();

    // ── OPTIMISTIC TRACK STATE ─────────────────────────────────────────────────
    // pendingTrack: set immediately on user action so the UI reflects the
    // intended track (title, artist, thumbnail) while the network request runs.
    // currentTrack: set only after the stream URL is confirmed and playback begins.
    const [pendingTrack, setPendingTrack] = useState(null);
    const [currentTrack, setCurrentTrack] = useState(null);
    const [isLoading, setIsLoading] = useState(false);   // true while fetching stream URL
    const [isPlaying, setIsPlaying] = useState(false);
    const [progress, setProgress] = useState(0);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [volume, setVolumeState] = useState(1);
    const [queue, setQueue] = useState([]);
    const [queueIndex, setQueueIndex] = useState(-1);
    const [activePlaylistId, setActivePlaylistId] = useState(null);

    // The "displayed" track in the UI is whichever we know most about:
    // pending while loading, current once playing.
    // Components should use displayTrack for the player bar / expanded view.
    const displayTrack = pendingTrack || currentTrack;

    const queueRef = useRef([]);
    const queueIndexRef = useRef(-1);
    const activePlaylistRef = useRef(null);
    const currentTrackRef = useRef(null);
    const lastPrevPressRef = useRef(0);
    // Guard against race: if a newer play request comes in before previous finishes, discard old result
    const playRequestIdRef = useRef(0);

    useEffect(() => { queueRef.current = queue; }, [queue]);
    useEffect(() => { queueIndexRef.current = queueIndex; }, [queueIndex]);
    useEffect(() => { activePlaylistRef.current = activePlaylistId; }, [activePlaylistId]);
    useEffect(() => { currentTrackRef.current = currentTrack; }, [currentTrack]);

    const audio = audioRef.current;
    useEffect(() => { audio.volume = 1; }, []);

    const refreshQueue = useCallback(async () => {
        const data = await api.getQueue();
        const q = data.queue || [];
        setQueue(q);
        queueRef.current = q;
        return q;
    }, []);

    useEffect(() => { refreshQueue(); }, []);

    const orderedQueue = useCallback((q) => {
        const userQ = q.filter(i => !i.is_auto);
        const autoQ = q.filter(i => i.is_auto);
        return [...userQ, ...autoQ];
    }, []);

    const fillAutoQueue = useCallback(async (track) => {
        const q = queueRef.current;
        if (q.filter(i => i.is_auto).length >= 4) return;
        try {
            const data = await api.getRelated(track);
            for (const rel of (data.related || []).slice(0, 6)) {
                await api.addToQueue({ ...rel, is_auto: true });
            }
            await refreshQueue();
        } catch { /* silent */ }
    }, [refreshQueue]);

    // ── Core: fetch stream URL and start playback ──────────────────────────────
    // OPTIMISTIC: caller should call setPendingTrack(track) BEFORE calling this,
    // so the UI updates instantly. This function resolves the stream and commits.
    const _playStream = useCallback(async (track, requestId) => {
        try {
            setIsLoading(true);
            // Optimistically update media session metadata right away
            updateMediaSession(track, false, {});

            const data = await api.getStream(track);

            // Discard stale responses if a newer play was requested
            if (requestId !== undefined && requestId !== playRequestIdRef.current) return null;

            const vol = audio.volume;
            audio.src = data.stream_url;
            audio.volume = vol;
            await audio.play();

            const merged = {
                ...track,
                ...data,
                title: track.title || data.title,
                artist: track.artist || data.artist,
            };
            setCurrentTrack(merged);
            setPendingTrack(null); // clear pending — we're live
            currentTrackRef.current = merged;
            setIsPlaying(true);
            setIsLoading(false);
            updateMediaSession(merged, true, buildMediaHandlers());
            fillAutoQueue(merged);
            return merged;
        } catch (e) {
            setIsLoading(false);
            setPendingTrack(null);
            throw e;
        }
    }, [audio, fillAutoQueue]);

    // ── Advance queue ──────────────────────────────────────────────────────────
    const advanceQueue = useCallback(async () => {
        const q = queueRef.current;
        const ordered = orderedQueue(q);
        if (!ordered.length) return;
        const currentIdx = queueIndexRef.current;
        const nextIdx = currentIdx < ordered.length - 1 ? currentIdx + 1 : currentIdx;
        if (nextIdx === currentIdx) return;
        setQueueIndex(nextIdx);
        queueIndexRef.current = nextIdx;
        const next = ordered[nextIdx];
        if (!next) return;
        const track = { id: next.track_id, title: next.title, artist: next.artist, thumbnail: next.thumbnail, duration: next.duration };
        // Optimistic: show next track immediately in the player bar
        setPendingTrack(track);
        const rid = ++playRequestIdRef.current;
        await _playStream(track, rid);
    }, [orderedQueue, _playStream]);

    const advanceQueueRef = useRef(advanceQueue);
    useEffect(() => { advanceQueueRef.current = advanceQueue; }, [advanceQueue]);

    // ── Audio events ───────────────────────────────────────────────────────────
    useEffect(() => {
        const onTime = () => {
            const dur = audio.duration || 0;
            setCurrentTime(audio.currentTime);
            setDuration(dur);
            setProgress(dur ? audio.currentTime / dur : 0);
            if ('mediaSession' in navigator && navigator.mediaSession.setPositionState && dur) {
                try {
                    navigator.mediaSession.setPositionState({
                        duration: dur,
                        playbackRate: audio.playbackRate,
                        position: Math.min(audio.currentTime, dur),
                    });
                } catch { /* ignore */ }
            }
        };
        const onPlay = () => {
            setIsPlaying(true);
            if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'playing';
        };
        const onPause = () => {
            setIsPlaying(false);
            if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'paused';
        };
        const onEnded = () => { setIsPlaying(false); advanceQueueRef.current(); };
        audio.addEventListener('timeupdate', onTime);
        audio.addEventListener('play', onPlay);
        audio.addEventListener('pause', onPause);
        audio.addEventListener('ended', onEnded);
        return () => {
            audio.removeEventListener('timeupdate', onTime);
            audio.removeEventListener('play', onPlay);
            audio.removeEventListener('pause', onPause);
            audio.removeEventListener('ended', onEnded);
        };
    }, [audio]);

    // ── Media Session handlers ─────────────────────────────────────────────────
    const buildMediaHandlers = useCallback(() => ({
        play: () => { audio.play(); },
        pause: () => { audio.pause(); },
        prev: () => {
            if (audio.currentTime > 3) {
                audio.currentTime = 0;
            } else {
                const q = queueRef.current;
                const ordered = orderedQueue(q);
                const idx = queueIndexRef.current;
                if (idx > 0) {
                    const prev = ordered[idx - 1];
                    if (prev) {
                        setQueueIndex(idx - 1);
                        queueIndexRef.current = idx - 1;
                        const track = { id: prev.track_id, title: prev.title, artist: prev.artist, thumbnail: prev.thumbnail, duration: prev.duration };
                        setPendingTrack(track);
                        const rid = ++playRequestIdRef.current;
                        _playStream(track, rid);
                    }
                } else {
                    audio.currentTime = 0;
                }
            }
        },
        next: () => { advanceQueueRef.current(); },
        seekto: (details) => { if (details.seekTime != null) robustSeek(audio, details.seekTime); },
        seekforward: (details) => { robustSeek(audio, audio.currentTime + (details.seekOffset || 10)); },
        seekbackward: (details) => { robustSeek(audio, audio.currentTime - (details.seekOffset || 10)); },
    }), [audio, orderedQueue, _playStream]);

    useEffect(() => {
        if (!currentTrack) return;
        updateMediaSession(currentTrack, isPlaying, buildMediaHandlers());
    }, [currentTrack, isPlaying, buildMediaHandlers]);

    // ── playTrack (public) ─────────────────────────────────────────────────────
    const playTrack = useCallback(async (track, options = {}) => {
        if (!track?.id) return;
        try {
            // OPTIMISTIC: immediately show the requested track in the UI
            setPendingTrack(track);
            setProgress(0);
            setCurrentTime(0);
            setDuration(track.duration || 0);

            const { fromPlaylistId = null } = options;
            const activePid = activePlaylistRef.current;
            const isOutsidePlaylist = activePid && fromPlaylistId !== activePid;
            if (isOutsidePlaylist) {
                await api.clearQueue();
                setActivePlaylistId(null);
                activePlaylistRef.current = null;
            }
            const q = await refreshQueue();
            const existing = q.findIndex(i => i.track_id === track.id);
            let targetIdx = 0;
            if (existing === -1) {
                const ordered = orderedQueue(q);
                await api.clearQueue();
                await api.addToQueue({ ...track, is_auto: false });
                for (const item of ordered) {
                    await api.addToQueue({ id: item.track_id, title: item.title, artist: item.artist, thumbnail: item.thumbnail, duration: item.duration, is_auto: item.is_auto });
                }
                targetIdx = 0;
            } else {
                const ordered = orderedQueue(q);
                targetIdx = ordered.findIndex(i => i.track_id === track.id);
                if (targetIdx === -1) targetIdx = 0;
            }
            await refreshQueue();
            setQueueIndex(targetIdx);
            queueIndexRef.current = targetIdx;

            const rid = ++playRequestIdRef.current;
            await _playStream(track, rid);
        } catch (e) {
            console.error('Play error', e);
        }
    }, [refreshQueue, orderedQueue, _playStream]);

    const togglePlay = useCallback(() => {
        if (!audio.src) return;
        if (audio.paused) audio.play(); else audio.pause();
    }, [audio]);

    // ── Smart prev ─────────────────────────────────────────────────────────────
    const skipPrev = useCallback(() => {
        const now = Date.now();
        const timeSinceLastPress = now - lastPrevPressRef.current;
        lastPrevPressRef.current = now;

        if (audio.currentTime > 3 && timeSinceLastPress > 800) {
            audio.currentTime = 0;
        } else {
            const q = queueRef.current;
            const ordered = orderedQueue(q);
            const idx = queueIndexRef.current;
            if (idx > 0) {
                const prev = ordered[idx - 1];
                if (prev) {
                    setQueueIndex(idx - 1);
                    queueIndexRef.current = idx - 1;
                    const track = { id: prev.track_id, title: prev.title, artist: prev.artist, thumbnail: prev.thumbnail, duration: prev.duration };
                    setPendingTrack(track);
                    setProgress(0); setCurrentTime(0);
                    const rid = ++playRequestIdRef.current;
                    _playStream(track, rid);
                }
            } else {
                audio.currentTime = 0;
            }
        }
    }, [audio, orderedQueue, _playStream]);

    const skipNext = useCallback(() => { advanceQueueRef.current(); }, []);

    const seek = useCallback((ratio) => {
        if (!audio.duration) return;
        robustSeek(audio, Math.max(0, Math.min(1, ratio)) * audio.duration);
    }, [audio]);

    const setVolume = useCallback((v) => {
        audio.volume = v;
        setVolumeState(v);
    }, [audio]);

    const addToQueue = useCallback(async (track, asNext = false) => {
        if (asNext) {
            const q = await refreshQueue();
            const ordered = orderedQueue(q);
            const currentIdx = queueIndexRef.current;
            await api.clearQueue();
            for (let i = 0; i <= currentIdx && i < ordered.length; i++) {
                const item = ordered[i];
                await api.addToQueue({ id: item.track_id, title: item.title, artist: item.artist, thumbnail: item.thumbnail, duration: item.duration, is_auto: item.is_auto });
            }
            await api.addToQueue({ ...track, is_auto: false });
            for (let i = currentIdx + 1; i < ordered.length; i++) {
                const item = ordered[i];
                await api.addToQueue({ id: item.track_id, title: item.title, artist: item.artist, thumbnail: item.thumbnail, duration: item.duration, is_auto: item.is_auto });
            }
        } else {
            await api.addToQueue({ ...track, is_auto: false });
        }
        await refreshQueue();
    }, [refreshQueue, orderedQueue]);

    const removeFromQueue = useCallback(async (position) => {
        await api.removeFromQueue(position);
        await refreshQueue();
    }, [refreshQueue]);

    const clearQueueFn = useCallback(async () => {
        await api.clearQueue();
        setQueue([]); queueRef.current = [];
        setQueueIndex(-1); queueIndexRef.current = -1;
        setActivePlaylistId(null); activePlaylistRef.current = null;
    }, []);

    const loadPlaylistIntoQueue = useCallback(async (pid, startTrack, shuffle = false) => {
        const data = await api.getPlaylist(pid);
        let tracks = data.tracks || [];
        if (!tracks.length) return;
        if (shuffle) tracks = [...tracks].sort(() => Math.random() - 0.5);

        // Optimistic: show first/start track immediately
        let startIdx = 0;
        if (startTrack && !shuffle) {
            const idx = tracks.findIndex(t => t.track_id === startTrack.id);
            if (idx > -1) startIdx = idx;
        }
        const firstTrack = shuffle ? tracks[0] : tracks[startIdx];
        const optimisticTrack = { id: firstTrack.track_id, title: firstTrack.title, artist: firstTrack.artist, thumbnail: firstTrack.thumbnail, duration: firstTrack.duration };
        setPendingTrack(optimisticTrack);
        setProgress(0); setCurrentTime(0);

        await api.clearQueue();
        for (const t of tracks) {
            await api.addToQueue({ id: t.track_id, title: t.title, artist: t.artist, thumbnail: t.thumbnail, duration: t.duration, is_auto: false });
        }
        await refreshQueue();
        setActivePlaylistId(pid); activePlaylistRef.current = pid;
        setQueueIndex(startIdx); queueIndexRef.current = startIdx;

        const rid = ++playRequestIdRef.current;
        await _playStream(optimisticTrack, rid);
    }, [refreshQueue, _playStream]);

    const queuePlaylistAfterCurrent = useCallback(async (pid) => {
        const data = await api.getPlaylist(pid);
        for (const t of data.tracks || []) {
            await api.addToQueue({ id: t.track_id, title: t.title, artist: t.artist, thumbnail: t.thumbnail, duration: t.duration, is_auto: false });
        }
        await refreshQueue();
    }, [refreshQueue]);

    const startMix = useCallback(async (track) => {
        setPendingTrack(track);
        setProgress(0); setCurrentTime(0);

        await api.clearQueue();
        await api.addToQueue({ ...track, is_auto: false });
        try {
            const data = await api.getRelated(track);
            for (const rel of (data.related || []).slice(0, 10)) {
                await api.addToQueue({ ...rel, is_auto: false });
            }
        } catch (e) { console.error('Mix error', e); }
        await refreshQueue();
        setQueueIndex(0); queueIndexRef.current = 0;
        setActivePlaylistId(null); activePlaylistRef.current = null;

        const rid = ++playRequestIdRef.current;
        await _playStream(track, rid);
    }, [refreshQueue, _playStream]);

    const playQueueItem = useCallback(async (item, orderedIdx) => {
        const track = { id: item.track_id, title: item.title, artist: item.artist, thumbnail: item.thumbnail, duration: item.duration };
        setPendingTrack(track);
        setProgress(0); setCurrentTime(0);
        setQueueIndex(orderedIdx); queueIndexRef.current = orderedIdx;
        const rid = ++playRequestIdRef.current;
        await _playStream(track, rid);
    }, [_playStream]);

    return {
        // displayTrack is the optimistic view: pending while loading, current once live
        currentTrack: displayTrack,
        _confirmedTrack: currentTrack,
        isLoading,
        isPlaying, progress, currentTime, duration, volume,
        queue, queueIndex, orderedQueue, activePlaylistId,
        playTrack, togglePlay, skipPrev, skipNext, seek, setVolume,
        addToQueue, removeFromQueue, clearQueue: clearQueueFn,
        loadPlaylistIntoQueue, queuePlaylistAfterCurrent, startMix,
        playQueueItem, refreshQueue,
    };
}