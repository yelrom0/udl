import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

// The repo root, wherever this file is: `web/` sits in it.
const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// Playwright's version is pinned to the Python Playwright's (uv.lock: 1.63.0), so both use the
// one Chromium in ~/.cache/ms-playwright.
export default defineConfig({
	testDir: "./e2e",
	fullyParallel: false,
	workers: 1,
	use: {
		baseURL: "http://127.0.0.1:8765",
		browserName: "chromium",
	},
	projects: [
		{ name: "light", use: { colorScheme: "light" } },
		{ name: "dark", use: { colorScheme: "dark" } },
	],
	webServer: {
		// (The venv's Python, not `uv run`: Playwright's webServer stops it with SIGTERM, and
		// it must reach the server itself, not the `uv` in front of it. An absolute path, since
		// the venv compares its `sys.prefix` against it; `cwd` is the repo root, where the
		// server's udl runs.)
		command: `PYTHONPATH=tests ${resolve(repo, ".venv/bin/python")} -u -m support.e2e`,
		url: "http://127.0.0.1:8765/docs",
		cwd: repo,
		reuseExistingServer: !process.env.CI,
		timeout: 120_000,
	},
});
