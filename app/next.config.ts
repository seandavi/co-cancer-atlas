import path from "node:path";

import type { NextConfig } from "next";

// public/data is a committed symlink to ../../data (the ETL snapshot).
// Turbopack refuses symlinks that point outside its root, so widen the
// root to the repo. Keep file tracing on the same root (Next warns if
// they differ).
const repoRoot = path.join(__dirname, "..");

const nextConfig: NextConfig = {
  // @duckdb/node-bindings is a native module with platform-specific
  // `require()` calls Turbopack can't statically resolve. Keep it
  // out of the server bundle.
  serverExternalPackages: ["@duckdb/node-api", "@duckdb/node-bindings"],
  turbopack: { root: repoRoot },
  outputFileTracingRoot: repoRoot,
};

export default nextConfig;
