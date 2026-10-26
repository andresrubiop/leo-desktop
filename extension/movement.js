// Geometry in logical desktop coordinates, shared by Shell and the node tests.
export const SIZE = 168;
export const FEET = 158;
export const WALK_FRAMES = 24;
export const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, Math.max(lo, hi)));

export function contain(point, area) {
    return {x: clamp(point.x, area.x, area.x + area.width - SIZE),
        y: clamp(point.y, area.y, area.y + area.height - SIZE)};
}

export function nearestMonitor(point, areas) {
    let best = 0, distance = Infinity;
    areas.forEach((area, index) => {
        const p = contain(point, area);
        const d = Math.hypot(p.x - point.x, p.y - point.y);
        if (d < distance) { best = index; distance = d; }
    });
    return best;
}

// Cross only shared edges with room for the whole dog. Never walk through a
// gap between offset monitors or jump across a disconnected desktop region.
function portal(a, b) {
    const half = SIZE / 2;
    const top = Math.max(a.y, b.y), bottom = Math.min(a.y + a.height, b.y + b.height);
    if (bottom - top >= SIZE) {
        if (Math.abs(a.x + a.width - b.x) <= 1)
            return {x: b.x - half, y: (top + bottom) / 2 - half};
        if (Math.abs(b.x + b.width - a.x) <= 1)
            return {x: a.x - half, y: (top + bottom) / 2 - half};
    }
    const left = Math.max(a.x, b.x), right = Math.min(a.x + a.width, b.x + b.width);
    if (right - left >= SIZE) {
        if (Math.abs(a.y + a.height - b.y) <= 1)
            return {x: (left + right) / 2 - half, y: b.y - half};
        if (Math.abs(b.y + b.height - a.y) <= 1)
            return {x: (left + right) / 2 - half, y: a.y - half};
    }
    return null;
}

export function route(areas, from, to, destination) {
    const queue = [{index: from, points: []}], visited = new Set([from]);
    while (queue.length) {
        const {index, points} = queue.shift();
        if (index === to) return [...points, contain(destination, areas[to])];
        areas.forEach((area, next) => {
            if (visited.has(next)) return;
            const edge = portal(areas[index], area);
            if (edge) {
                visited.add(next);
                queue.push({index: next, points: [...points, edge]});
            }
        });
    }
    return null;
}

export function advance(point, target, distance) {
    const dx = target.x - point.x, dy = target.y - point.y;
    const length = Math.hypot(dx, dy);
    const amount = Math.min(distance, length);
    const fraction = length ? amount / length : 1;
    return {x: point.x + dx * fraction, y: point.y + dy * fraction,
        distance: amount, arrived: amount === length};
}
