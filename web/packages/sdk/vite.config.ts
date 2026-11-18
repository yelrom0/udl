import { resolve } from "node:path";
import { defineConfig } from "vite";

// The SDK, built as ES modules in library mode: `index` (no React) and `react` (React is
// external, a peer dependency of that entry only). A plugin that bundles it gets two files.
export default defineConfig({
	build: {
		lib: {
			entry: {
				index: resolve(import.meta.dirname, "src/index.ts"),
				react: resolve(import.meta.dirname, "src/react.ts"),
			},
			formats: ["es"],
		},
		rollupOptions: {
			external: ["react", "react-dom", "react-dom/client", "react/jsx-runtime"],
		},
	},
});
