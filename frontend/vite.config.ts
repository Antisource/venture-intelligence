import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig(({ command }) => ({
  root: import.meta.dirname,
  base: command === "build" ? "/frontend/dist/" : "/",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "three-controls",
              test: /node_modules[\\/]three[\\/]examples[\\/]jsm[\\/]/,
              priority: 40,
              includeDependenciesRecursively: false,
            },
            {
              name: "three-core",
              test: /node_modules[\\/]three[\\/]/,
              priority: 30,
              includeDependenciesRecursively: false,
            },
            {
              name: "react-three-fiber",
              test: /node_modules[\\/](@react-three[\\/]fiber|react-reconciler)[\\/]/,
              priority: 20,
              includeDependenciesRecursively: false,
            },
          ],
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/frontend": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
    restoreMocks: true,
    // Route tests replace process-global fetch. Serial files prevent one
    // worker's cleanup from racing another file's lazy API request.
    fileParallelism: false,
  },
}));
