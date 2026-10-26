import assert from 'node:assert/strict';
import {contain, nearestMonitor, route, advance, SIZE} from '../extension/movement.js';
const layouts = [
    [{x: 0, y: 32, width: 1280, height: 688}, {x: 1280, y: 0, width: 1440, height: 900}],
    [{x: -1920, y: 0, width: 1920, height: 1080}, {x: 0, y: 300, width: 1280, height: 720}],
    [{x: 0, y: -900, width: 1440, height: 900}, {x: 0, y: 0, width: 1920, height: 1080}],
];
for (const areas of layouts) {
    const target = {x: areas[1].x + 200, y: areas[1].y + 200};
    const path = route(areas, 0, 1, target);
    assert.equal(path.length, 2, 'shared edge needs a transition waypoint');
    assert.deepEqual(path.at(-1), target);
    assert.equal(nearestMonitor(target, areas), 1);
    let point = contain({x: -99999, y: 99999}, areas[0]);
    for (const waypoint of path) {
        let steps = 0;
        while (true) {
            const next = advance(point, waypoint, 2);
            assert.ok(next.distance <= 2, 'no teleportation');
            // Every corner of the avatar must stay inside the monitor union.
            for (const dx of [0, SIZE]) for (const dy of [0, SIZE])
                assert.ok(areas.some(a => next.x + dx >= a.x - .01 && next.x + dx <= a.x + a.width + .01 &&
                    next.y + dy >= a.y - .01 && next.y + dy <= a.y + a.height + .01), 'avatar crosses a gap');
            point = next;
            if (next.arrived) break;
            assert.ok(++steps < 10000);
        }
    }
}
assert.equal(route([{x: 0,y: 0,width: 800,height: 600}, {x: 900,y: 0,width: 800,height: 600}],0,1,{x: 1000,y: 200}), null);
assert.equal(route([{x: 0,y: 0,width: 800,height: 600}, {x: 800,y: 560,width: 800,height: 600}],0,1,{x: 1000,y: 700}), null);
console.log('PASS: horizontal, negative, vertical layouts; safe paths, gaps and constant-speed movement');
