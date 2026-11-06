import type { NextConfig } from "next";

const apiUrl = process.env.API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  output: "standalone",
  // Compression would buffer the live event stream.
  compress: false,
  devIndicators: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default config;
