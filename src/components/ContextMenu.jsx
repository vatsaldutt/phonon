import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

export default function ContextMenu({ items, position, onClose }) {
  const ref = useRef();

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    const keyHandler = (e) => { if (e.key === 'Escape') onClose(); };
    const tid = setTimeout(() => {
      document.addEventListener('mousedown', handler);
      document.addEventListener('keydown', keyHandler);
    }, 0);
    return () => {
      clearTimeout(tid);
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('keydown', keyHandler);
    };
  }, [onClose]);

  // Keep menu inside viewport
  let x = position.x;
  let y = position.y;
  if (x + 220 > window.innerWidth) x = window.innerWidth - 228;
  if (y + 320 > window.innerHeight) y = Math.max(4, y - 200);

  return createPortal(
    <div className="ctx-menu" ref={ref} style={{ left: x, top: y }}>
      {items.map((item, i) =>
        item.divider ? (
          <hr key={i} className="ctx-divider" />
        ) : (
          <div
            key={i}
            className={`ctx-item${item.danger ? ' danger' : ''}`}
            onMouseDown={e => e.stopPropagation()}
            onClick={() => { item.fn(); onClose(); }}
          >
            <span className="ctx-icon">{item.icon}</span>
            {item.label}
          </div>
        )
      )}
    </div>,
    document.body
  );
}