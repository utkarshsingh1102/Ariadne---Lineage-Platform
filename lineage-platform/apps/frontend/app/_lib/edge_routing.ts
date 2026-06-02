/*
 * Obstacle-avoiding edges for the lineage graph.
 *
 * Two routers:
 *   A) elkEdgeToPath(edge)        — render ELK's own routed bend points (preferred)
 *   B) routeAroundObstacles(...)  — manual fallback when we lay out ourselves
 *
 * Both return an SVG path `d` string. Use ``fill="none"`` on the <path>.
 *
 * Problem solved: a straight A→C edge passes BEHIND any node B sitting
 * between them. These routers detour around B with right-angle (or rounded /
 * curved) turns so the arrow is always visible and never crosses an unrelated
 * node.
 *
 * Ported from spark-improvement/edge-routing.js with TypeScript types added;
 * the geometry is unchanged.
 */

export interface Point {
  x: number;
  y: number;
}

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** One routed edge in the ELK layout result. */
export interface ElkEdgeSection {
  startPoint: Point;
  endPoint: Point;
  bendPoints?: Point[];
}

export interface ElkRoutedEdge {
  sections?: ElkEdgeSection[];
}

export type Direction = "RIGHT" | "DOWN";

export interface RouteOptions {
  direction?: Direction;
  radius?: number;
  pad?: number;
}

/* ----------------------------------------------------------------------------
 * Layout options handed to ELK. Keep keys aligned with the upstream plan.
 * -------------------------------------------------------------------------- */
export const ELK_LAYOUT_OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.edgeRouting": "ORTHOGONAL",
  // Inter-layer gap kept generous so orthogonal edges have room to bend.
  "elk.layered.spacing.nodeNodeBetweenLayers": "140",
  // Within-layer gap is the gutter between sibling boxes — with variable
  // node heights ELK uses this as a *minimum* clear gap, so even a tall
  // box never butts against a short neighbour.
  "elk.spacing.nodeNode": "32",
  "elk.layered.spacing.edgeNodeBetweenLayers": "30",
  "elk.layered.spacing.edgeEdgeBetweenLayers": "15",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  // Trim aspect-ratio bias so the layout doesn't artificially compress one
  // axis when boxes vary in size.
  "elk.aspectRatio": "1.6",
};

/* ----------------------------------------------------------------------------
 * A) PREFERRED — use ELK's router output.
 * -------------------------------------------------------------------------- */

/**
 * Build an SVG path from one routed ELK edge. ``radius`` controls corner
 * fillet (0 = sharp right angles).
 */
export function elkEdgeToPath(edge: ElkRoutedEdge, radius = 8): string {
  const section = edge.sections && edge.sections[0];
  if (!section) return "";
  const pts: Point[] = [
    section.startPoint,
    ...(section.bendPoints || []),
    section.endPoint,
  ];
  return roundedPolyline(pts, radius);
}

/* ----------------------------------------------------------------------------
 * B) FALLBACK — compute the detour ourselves.
 *
 * For when we're NOT using ELK's router. Given source/target rects and the
 * list of all other node rects (obstacles), produce a path that turns around
 * any obstacle blocking the straight line.
 * -------------------------------------------------------------------------- */
export function routeAroundObstacles(
  src: Rect,
  dst: Rect,
  obstacles: Rect[],
  opts: RouteOptions = {},
): string {
  const dir: Direction = opts.direction ?? "RIGHT";
  const radius = opts.radius ?? 8;
  const pad = opts.pad ?? 12;

  const start = exitPoint(src, dir);
  const end = entryPoint(dst, dir);

  const straight: Point[] = [start, end];
  if (!segmentHitsAny(start, end, obstacles, src, dst)) {
    return roundedPolyline(straight, radius);
  }

  const blocker = firstBlocker(start, end, obstacles, src, dst);
  if (!blocker) return roundedPolyline(straight, radius);

  const mid =
    dir === "RIGHT"
      ? (start.x + end.x) / 2
      : (start.y + end.y) / 2;

  const pts =
    dir === "RIGHT"
      ? detourHorizontal(start, end, blocker, mid, pad)
      : detourVertical(start, end, blocker, mid, pad);

  return roundedPolyline(pts, radius);
}

/* ---- geometry helpers ---------------------------------------------------- */

function exitPoint(rect: Rect, dir: Direction): Point {
  return dir === "RIGHT"
    ? { x: rect.x + rect.width, y: rect.y + rect.height / 2 }
    : { x: rect.x + rect.width / 2, y: rect.y + rect.height };
}

function entryPoint(rect: Rect, dir: Direction): Point {
  return dir === "RIGHT"
    ? { x: rect.x, y: rect.y + rect.height / 2 }
    : { x: rect.x + rect.width / 2, y: rect.y };
}

function rectsOverlapSeg(p1: Point, p2: Point, r: Rect): boolean {
  const minX = Math.min(p1.x, p2.x);
  const maxX = Math.max(p1.x, p2.x);
  const minY = Math.min(p1.y, p2.y);
  const maxY = Math.max(p1.y, p2.y);
  return !(
    maxX < r.x ||
    minX > r.x + r.width ||
    maxY < r.y ||
    minY > r.y + r.height
  );
}

function segmentHitsAny(
  p1: Point,
  p2: Point,
  obstacles: Rect[],
  src: Rect,
  dst: Rect,
): boolean {
  return obstacles.some(
    (o) => o !== src && o !== dst && rectsOverlapSeg(p1, p2, o),
  );
}

function firstBlocker(
  p1: Point,
  p2: Point,
  obstacles: Rect[],
  src: Rect,
  dst: Rect,
): Rect | undefined {
  return obstacles.find(
    (o) => o !== src && o !== dst && rectsOverlapSeg(p1, p2, o),
  );
}

function detourHorizontal(
  start: Point,
  end: Point,
  blocker: Rect,
  midX: number,
  pad: number,
): Point[] {
  const aboveGap = start.y - blocker.y;
  const belowGap = blocker.y + blocker.height - start.y;
  const lane =
    aboveGap < belowGap ? blocker.y - pad : blocker.y + blocker.height + pad;
  return [
    start,
    { x: midX, y: start.y },
    { x: midX, y: lane },
    { x: end.x - pad, y: lane },
    { x: end.x - pad, y: end.y },
    end,
  ];
}

function detourVertical(
  start: Point,
  end: Point,
  blocker: Rect,
  midY: number,
  pad: number,
): Point[] {
  const leftGap = start.x - blocker.x;
  const rightGap = blocker.x + blocker.width - start.x;
  const lane =
    leftGap < rightGap ? blocker.x - pad : blocker.x + blocker.width + pad;
  return [
    start,
    { x: start.x, y: midY },
    { x: lane, y: midY },
    { x: lane, y: end.y - pad },
    { x: end.x, y: end.y - pad },
    end,
  ];
}

/* ---- path builder: polyline with rounded corners ------------------------- */

/**
 * Turn a list of points into an SVG path. With radius>0, each interior corner
 * becomes a smooth quadratic fillet instead of a sharp right angle.
 */
export function roundedPolyline(points: Point[], radius = 0): string {
  const pts = dedupe(points);
  if (pts.length < 2) return "";
  if (radius <= 0 || pts.length === 2) {
    return "M " + pts.map((p) => `${r(p.x)} ${r(p.y)}`).join(" L ");
  }
  let d = `M ${r(pts[0].x)} ${r(pts[0].y)}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const prev = pts[i - 1];
    const cur = pts[i];
    const next = pts[i + 1];
    const a = shorten(cur, prev, radius);
    const b = shorten(cur, next, radius);
    d += ` L ${r(a.x)} ${r(a.y)} Q ${r(cur.x)} ${r(cur.y)} ${r(b.x)} ${r(b.y)}`;
  }
  const last = pts[pts.length - 1];
  d += ` L ${r(last.x)} ${r(last.y)}`;
  return d;
}

function shorten(from: Point, to: Point, dist: number): Point {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy) || 1;
  const d = Math.min(dist, len / 2);
  return { x: from.x + (dx / len) * d, y: from.y + (dy / len) * d };
}

function dedupe(pts: Point[]): Point[] {
  return pts.filter(
    (p, i) =>
      i === 0 ||
      Math.abs(p.x - pts[i - 1].x) > 0.5 ||
      Math.abs(p.y - pts[i - 1].y) > 0.5,
  );
}

function r(n: number): number {
  return Math.round(n * 10) / 10;
}

/**
 * Spread parallel edges entering the same target so they don't stack into one
 * line. Offset each edge's entry point by index.
 */
export function fanInOffset(
  entry: Point,
  indexInGroup: number,
  groupSize: number,
  spacing = 10,
): Point {
  const total = (groupSize - 1) * spacing;
  return { x: entry.x, y: entry.y - total / 2 + indexInGroup * spacing };
}
