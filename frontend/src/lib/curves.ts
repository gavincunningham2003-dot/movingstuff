/** Resampling helpers so several curves with different x grids can share one Recharts dataset. */

export interface XY {
  x: number;
  y: number;
}

/** Linear interpolation on points sorted by x ascending. */
export function interpolateAt(points: XY[], x: number): number | null {
  if (points.length === 0) return null;
  const first = points[0];
  const last = points[points.length - 1];
  if (x <= first.x) return first.y;
  if (x >= last.x) return last.y;
  let lo = 0;
  let hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid].x <= x) lo = mid;
    else hi = mid;
  }
  const a = points[lo];
  const b = points[hi];
  if (b.x === a.x) return b.y;
  const t = (x - a.x) / (b.x - a.x);
  return a.y + t * (b.y - a.y);
}

export interface NamedCurve {
  name: string;
  points: XY[];
}

/** One row per grid step with a column per curve name (plus `x`). */
export function gridResample(curves: NamedCurve[], steps = 100): Record<string, number | null>[] {
  const rows: Record<string, number | null>[] = [];
  for (let i = 0; i <= steps; i++) {
    const x = i / steps;
    const row: Record<string, number | null> = { x };
    for (const c of curves) row[c.name] = interpolateAt(c.points, x);
    rows.push(row);
  }
  return rows;
}
