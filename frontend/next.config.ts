import type { NextConfig } from "next";

const backend = process.env.API_PROXY_TARGET || "http://127.0.0.1:8010";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
