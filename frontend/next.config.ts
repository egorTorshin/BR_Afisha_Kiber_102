import type { NextConfig } from "next";
const config: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: `${(process.env.API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "")}/:path*`,
      },
    ];
  },
};
export default config;
