// The browser talks only to Next; /api/* is proxied to the read-only FastAPI, by default
// 127.0.0.1:8700 (NEXT_PUBLIC_DRISHTI_API overrides it, e.g. http://127.0.0.1:8710 for a track's
// dev server). PAPER ONLY: no endpoint places anything.
const API = process.env.NEXT_PUBLIC_DRISHTI_API || "http://127.0.0.1:8700";
const nextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};
export default nextConfig;
