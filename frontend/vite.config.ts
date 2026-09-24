import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The backend is the same origin in the demo build (nginx proxies /api),
    // so the app never needs to know where it lives.
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      // The billing stub's control surface, for the provoke buttons. The stub
      // has no CORS, so the browser must reach it through this origin.
      "/billing": {
        target: "http://localhost:8081",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/billing/, ""),
      },
    },
  },
});
