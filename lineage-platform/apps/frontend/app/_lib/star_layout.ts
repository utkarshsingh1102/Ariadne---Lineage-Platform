/**
 * Polar-BFS positioner for the lineage graph's "star schema" view.
 *
 * Two-tier strategy:
 *   1. Depth-1 children of the center fan out on a global ring whose radius
 *      scales to the count (so 30 siblings get a ring big enough to fit).
 *   2. Depth ≥ 2 children orbit their immediate parent on a *local* sub-orbit
 *      with a constant small radius, aimed outward (away from the center).
 *      This produces compact sub-stars instead of sprawling to a global ring
 *      at depth-N where narrow wedges would blow the radius up.
 */

export interface NodePos {
  x: number;
  y: number;
  angle: number; // direction from center (depth 0) or from parent (deeper)
  depth: number;
}

/** Radius of the depth-1 ring around the center, minimum. */
const MAIN_RING_MIN_RADIUS = 520;
/** Arc-length reservation per depth-1 sibling — used to grow the ring. */
const MAIN_NODE_FOOTPRINT = 240;
/** Local sub-orbit radius — small + constant so sub-stars stay compact. */
const SUB_ORBIT_RADIUS = 230;
/** How wide a fan angle (radians) a sub-star occupies. */
const SUB_FAN_ARC = Math.PI * 0.95; // ~170°

export function computeStarPositions(
  visibleNodeIds: Iterable<string>,
  visibleEdges: Array<{ source: string; target: string }>,
  centerId: string,
): Map<string, NodePos> {
  const visibleSet = new Set(visibleNodeIds);
  const adj = new Map<string, Set<string>>();
  const link = (a: string, b: string) => {
    if (!adj.has(a)) adj.set(a, new Set());
    adj.get(a)!.add(b);
  };
  for (const e of visibleEdges) {
    if (visibleSet.has(e.source) && visibleSet.has(e.target)) {
      link(e.source, e.target);
      link(e.target, e.source);
    }
  }

  const out = new Map<string, NodePos>();
  out.set(centerId, { x: 0, y: 0, angle: 0, depth: 0 });

  let frontier: string[] = [centerId];
  const placed = new Set<string>([centerId]);

  while (frontier.length > 0) {
    const next: string[] = [];

    for (const parentId of frontier) {
      const parent = out.get(parentId)!;
      const childIds = Array.from(adj.get(parentId) ?? []).filter(
        (id) => !placed.has(id),
      );
      if (childIds.length === 0) continue;

      const n = childIds.length;

      if (parent.depth === 0) {
        // Global ring around the center. Radius grows with sibling count.
        const fitR = (n * MAIN_NODE_FOOTPRINT) / (2 * Math.PI);
        const radius = Math.max(MAIN_RING_MIN_RADIUS, fitR);
        const start = -Math.PI / 2; // 12 o'clock
        const step = (2 * Math.PI) / n;
        for (let i = 0; i < n; i++) {
          const angle = start + step * (i + 0.5);
          out.set(childIds[i], {
            x: radius * Math.cos(angle),
            y: radius * Math.sin(angle),
            angle, // direction from center — children orbit OPPOSITE this
            depth: 1,
          });
          placed.add(childIds[i]);
          next.push(childIds[i]);
        }
      } else {
        // Local sub-orbit around the parent. Centred on the direction
        // pointing *outward from center* so sub-stars don't grow back
        // through the main ring.
        // - parent.angle is the polar angle of the parent (depth 1) or
        //   the local "outward direction" (depth >=2).
        const outwardAngle = parent.angle;
        const arc = SUB_FAN_ARC;
        // Allow the sub-orbit to grow modestly when crowded.
        const step = arc / Math.max(n, 1);
        // If too crowded for SUB_ORBIT_RADIUS, bump it just enough so
        // adjacent children are MAIN_NODE_FOOTPRINT/2 apart.
        const fitR = (MAIN_NODE_FOOTPRINT * 0.7) / Math.max(step, 0.05);
        const r = Math.max(SUB_ORBIT_RADIUS, Math.min(fitR, SUB_ORBIT_RADIUS * 2));
        const start = outwardAngle - arc / 2;
        for (let i = 0; i < n; i++) {
          const a = start + step * (i + 0.5);
          out.set(childIds[i], {
            x: parent.x + r * Math.cos(a),
            y: parent.y + r * Math.sin(a),
            angle: a, // grandchildren keep heading outward in `a` direction
            depth: parent.depth + 1,
          });
          placed.add(childIds[i]);
          next.push(childIds[i]);
        }
      }
    }
    frontier = next;
  }

  return out;
}
