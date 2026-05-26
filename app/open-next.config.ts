// @opennextjs/cloudflare config. Mostly defaults; the adapter picks up
// `wrangler.jsonc` and produces .open-next/worker.js.

import { defineCloudflareConfig } from "@opennextjs/cloudflare";

export default defineCloudflareConfig({
  // No incremental cache for v1; sessions are ephemeral and the snapshot
  // is read-only. Switch to R2 if we add cached tool results.
});
