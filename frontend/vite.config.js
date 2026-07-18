import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const flyApiOrigin = "https://physician-assistant-srck5q.fly.dev";
const flyWsOrigin = "wss://physician-assistant-srck5q.fly.dev";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: flyApiOrigin,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/ws": {
        target: flyWsOrigin,
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
