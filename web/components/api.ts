export async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
}
export const fmt = (x: number | null | undefined, d = 2) =>
  x === null || x === undefined || Number.isNaN(x) ? "–" : x.toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
export const stockHref = (key: string) => `/stock/${encodeURIComponent(key)}`;
