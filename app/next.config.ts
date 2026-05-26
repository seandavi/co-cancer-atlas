import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // @duckdb/node-bindings is a native module with platform-specific
  // `require()` calls Turbopack can't statically resolve. Keep it
  // out of the server bundle.
  serverExternalPackages: ["@duckdb/node-api", "@duckdb/node-bindings"],
};

export default nextConfig;
