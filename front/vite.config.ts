import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `dist/` serwuje FastAPI (`web/api.py`), więc build idzie tam, gdzie API go
// szuka. W trybie `dev` proxy przekazuje `/api` do uvicorna, żeby ciasteczko
// sesji było tego samego pochodzenia — inaczej `SameSite=Lax` je odrzuci.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
});
