import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import path from "node:path";

export default defineConfig({
  root: path.resolve(process.cwd(), "desktop/renderer"),
  base: "./",
  plugins: [react()],
  build: {
    outDir: path.resolve(process.cwd(), "desktop-dist"),
    emptyOutDir: true,
  },
  resolve: {
    alias: { "@app": path.resolve(process.cwd(), "app") },
  },
});

