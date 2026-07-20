import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built SPA is served by the FastAPI app itself (single Cloud Run service,
// no CDN, nothing extra to keep warm). Output therefore lands in server/static,
// which main.py mounts in preference to the legacy single-file board.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../server/static",
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    // Dev server talks to the local backend from ./run.sh up
    proxy: {
      "/api": { target: "http://localhost:8790", changeOrigin: true },
      "/mcp": { target: "http://localhost:8790", changeOrigin: true },
      "/health": { target: "http://localhost:8790", changeOrigin: true },
    },
  },
});
