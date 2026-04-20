export function fmtDur(secs) {
    if (!secs) return '—';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
}

export function groupHistoryByDate(rows) {
    const groups = {};
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);

    rows.forEach(t => {
        const d = new Date(t.played_at);
        let label;
        if (sameDay(d, today)) label = 'Today';
        else if (sameDay(d, yesterday)) label = 'Yesterday';
        else label = d.toLocaleDateString('en-US', { month: 'long', day: 'numeric' });
        if (!groups[label]) groups[label] = [];
        groups[label].push(t);
    });
    return groups;
}

function sameDay(a, b) {
    return (
        a.getFullYear() === b.getFullYear() &&
        a.getMonth() === b.getMonth() &&
        a.getDate() === b.getDate()
    );
}

export function getGreeting() {
    const h = new Date().getHours();
    if (h < 12) return 'morning';
    if (h < 17) return 'afternoon';
    return 'evening';
}