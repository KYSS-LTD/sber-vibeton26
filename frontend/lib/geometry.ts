export type LngLat = [number, number];
export type Polygon = { type: "Polygon"; coordinates: LngLat[][] };
export type Bounds = {
  west: number;
  east: number;
  south: number;
  north: number;
};

export function boundsOf(points: number[][]): Bounds {
  return {
    west: Math.min(...points.map((p) => p[0])),
    east: Math.max(...points.map((p) => p[0])),
    south: Math.min(...points.map((p) => p[1])),
    north: Math.max(...points.map((p) => p[1])),
  };
}

export function polygonFromRing(ring: LngLat[]): Polygon {
  return { type: "Polygon", coordinates: [[...ring, ring[0]]] };
}

// GeoJSON ring without the closing point.
export function openRing(polygon: Polygon): LngLat[] {
  return polygon.coordinates[0].slice(0, -1);
}

export function centroid(ring: number[][]): LngLat {
  const n = ring.length;
  return [
    ring.reduce((a, p) => a + p[0], 0) / n,
    ring.reduce((a, p) => a + p[1], 0) / n,
  ];
}

function cross(o: number[], a: number[], b: number[]) {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

function segmentsCross(a: number[], b: number[], c: number[], d: number[]) {
  const d1 = cross(a, b, c);
  const d2 = cross(a, b, d);
  const d3 = cross(c, d, a);
  const d4 = cross(c, d, b);
  return d1 * d2 < 0 && d3 * d4 < 0;
}

// True when non-adjacent edges of the open ring intersect (PostGIS would reject it as invalid).
export function selfIntersects(ring: number[][]): boolean {
  const n = ring.length;
  for (let i = 0; i < n; i++)
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue;
      if (segmentsCross(ring[i], ring[(i + 1) % n], ring[j], ring[(j + 1) % n]))
        return true;
    }
  return false;
}

export function insideRing(point: number[], ring: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (
      yi > point[1] !== yj > point[1] &&
      point[0] < ((xj - xi) * (point[1] - yi)) / (yj - yi) + xi
    )
      inside = !inside;
  }
  return inside;
}

// Approximate area in m² (local equirectangular projection; fine for site-sized polygons).
export function areaM2(ring: number[][]): number {
  if (ring.length < 3) return 0;
  const lat0 =
    (ring.reduce((a, p) => a + p[1], 0) / ring.length) * (Math.PI / 180);
  const k = 111320;
  const xy = ring.map((p) => [p[0] * k * Math.cos(lat0), p[1] * k]);
  let sum = 0;
  for (let i = 0; i < xy.length; i++) {
    const [x1, y1] = xy[i];
    const [x2, y2] = xy[(i + 1) % xy.length];
    sum += x1 * y2 - x2 * y1;
  }
  return Math.abs(sum) / 2;
}

// Pixel distance between two points on a 256px-tile Web Mercator map at a given zoom.
export function pixelDistance(a: number[], b: number[], zoom: number): number {
  const world = 256 * 2 ** zoom;
  const px = (p: number[]) => [
    ((p[0] + 180) / 360) * world,
    (0.5 -
      Math.log(Math.tan(Math.PI / 4 + (p[1] * Math.PI) / 360)) /
        (2 * Math.PI)) *
      world,
  ];
  const [ax, ay] = px(a);
  const [bx, by] = px(b);
  return Math.hypot(ax - bx, ay - by);
}

// Nearest candidate within `radius` screen pixels, else the point itself.
export function snapPoint(
  point: LngLat,
  candidates: number[][],
  zoom: number,
  radius = 14,
): LngLat {
  let best: number[] | null = null;
  let bestDistance = radius;
  for (const c of candidates) {
    const d = pixelDistance(point, c, zoom);
    if (d <= bestDistance) {
      best = c;
      bestDistance = d;
    }
  }
  return best ? [best[0], best[1]] : point;
}
