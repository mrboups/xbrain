import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Excalidraw reads process.env.IS_PREACT at runtime; Vite strips `process`
  // entirely, so without this define the canvas renders blank
  // (Phase 26 research, Pitfall 3 — CITED: docs.excalidraw.com/.../integration).
  define: { "process.env.IS_PREACT": JSON.stringify("true") },
  build: { outDir: "dist", sourcemap: false },
});
