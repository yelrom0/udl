import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react-swc";
import { defineConfig } from "vite";

// The shell is built into udl's package (`src/udl/interfaces/web/dist/`), and served there at
// /ui/. The dev server does the same at :5173 and proxies everything else to `udl serve`.
const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

export default defineConfig({
	base: "/ui/",
	plugins: [react(), tailwindcss()],
	build: {
		outDir: resolve(repo, "src/udl/interfaces/web/dist"),
		emptyOutDir: true,
	},
	server: {
		proxy: {
			"^/(?!ui/)": "http://127.0.0.1:8765",
		},
	},
});
