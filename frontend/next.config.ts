import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // Preview links (/p/<token>/) need their trailing slash for relative assets to resolve.
  skipTrailingSlashRedirect: true,
};

export default config;
