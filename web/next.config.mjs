// The browser talks only to Next (127.0.0.1:3100); /api/* is proxied to the read-only
// FastAPI on 127.0.0.1:8700. PAPER ONLY: no endpoint places anything.
const nextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8700/api/:path*" }];
  },
};
export default nextConfig;
