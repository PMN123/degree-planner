import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build into the stdlib server's static dir so production stays "python server.py".
// Dev proxies the JSON API to the running Python server.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../static/dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
