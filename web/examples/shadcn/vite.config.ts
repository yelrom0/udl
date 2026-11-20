import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react-swc";
import { defineConfig, type Plugin } from "vite";

/** The manifest, into the bundle: udl reads it to know what the plugin adds and where. */
function manifest(): Plugin {
	return {
		name: "udl-manifest",
		generateBundle() {
			this.emitFile({
				type: "asset",
				fileName: "manifest.json",
				source: readFileSync(resolve(import.meta.dirname, "manifest.json"), "utf8"),
			});
		},
	};
}

/**
 * The plugin's bundle, as udl serves one (`/ui/plugins/<name>/`): one ES module with React in it
 * (the page has no import map, so a plugin brings its own), and one stylesheet the shell adopts
 * into the plugin's shadow root. The names are fixed — a manifest names them.
 *
 * `dist/` is a folder of UI plugins as `[web] ui_plugin_dirs` reads one, so `make e2e` can serve
 * it beside the hand-written bundles in `tests/support/e2e_ui_plugins/`.
 */
export default defineConfig({
	plugins: [react(), tailwindcss(), manifest()],
	// (React reads `process.env.NODE_ENV` where it is bundled for a Node toolchain; a plugin's
	// bundle runs in a browser, which has no `process`.)
	define: { "process.env.NODE_ENV": JSON.stringify("production") },
	resolve: { alias: { "@": resolve(import.meta.dirname, "src") } },
	build: {
		outDir: resolve(import.meta.dirname, "dist", "shadcn"),
		emptyOutDir: true,
		cssCodeSplit: false,
		lib: {
			entry: resolve(import.meta.dirname, "src/index.tsx"),
			formats: ["es"],
			fileName: () => "index.js",
			cssFileName: "index",
		},
		rollupOptions: { output: { assetFileNames: "index.[ext]" } },
	},
});
