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
 * The orchestrator's UI plugin, as udl serves one: one ES module with React in it, and one
 * stylesheet the shell adopts into the plugin's shadow root.
 *
 * It builds into `src/udl/plugins/apps/orch/ui/`, where the orch app plugin's own `ui` reads it:
 * that folder *is* the bundle (a manifest.json and its files), so udl serves it with the plugin
 * and `make web` puts it in place.
 */
export default defineConfig({
	plugins: [react(), tailwindcss(), manifest()],
	define: { "process.env.NODE_ENV": JSON.stringify("production") },
	build: {
		outDir: resolve(import.meta.dirname, "../../../src/udl/plugins/apps/orch/ui"),
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
