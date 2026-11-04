export async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
}
export const fmt = (x: number | null | undefined, d = 2) =>
  x === null || x === undefined || Number.isNaN(x) ? "–" : x.toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
export const stockHref = (key: string) => `/stock/${encodeURIComponent(key)}`;
/** POST JSON; on an error status throws the API's `detail` text (e.g. a 400 from the journal). */
export async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, { method: "POST", cache: "no-store", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const txt = await r.text();
  if (!r.ok) {
    let msg = txt;
    try { const j = JSON.parse(txt); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* plain text */ }
    throw new Error(`${r.status}: ${msg}`);
  }
  return JSON.parse(txt) as T;
}
