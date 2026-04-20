import { useEffect, useRef } from 'react';

export default function PlaylistPicker({ playlists, position, onSelect, onClose }) {
    const ref = useRef();

    useEffect(() => {
        const handler = (e) => {
            if (ref.current && !ref.current.contains(e.target)) onClose();
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [onClose]);

    let style = { position: 'fixed', left: position.x, top: position.y };
    if (position.x + 220 > window.innerWidth) style.left = window.innerWidth - 224;
    if (position.y + 300 > window.innerHeight) style.top = position.y - 250;

    return (
        <div className="pl-picker-popup" ref={ref} style={style}>
            <div className="pl-picker-header">Add to playlist</div>
            {playlists.map(pl => (
                <div
                    key={pl.id}
                    className="pl-picker-item"
                    onClick={() => { onSelect(pl); onClose(); }}
                >
                    <span className="pl-picker-icon">♫</span>
                    {pl.name}
                </div>
            ))}
        </div>
    );
}