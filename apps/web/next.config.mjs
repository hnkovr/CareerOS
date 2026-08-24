/** @type {import('next').NextConfig} */
const apiInternal = process.env.API_INTERNAL_URL || "http://localhost:8000";

const nextConfig = {
  output: "standalone",
  transpilePackages: ["@careeros/api-client", "@careeros/schemas"],
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiInternal}/api/:path*` },
      { source: "/backend/health", destination: `${apiInternal}/health` },
    ];
  },
};

export default nextConfig;
