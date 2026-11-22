import preact from "@preact/preset-vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [preact(), tailwindcss()],
  base: "/",
  build: { outDir: "../src/clipgen/webui", emptyOutDir: true, assetsInlineLimit: 0 },
  server: { host: "127.0.0.1", port: 5173, strictPort: true },
});
