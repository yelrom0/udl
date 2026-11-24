import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";
import { SIGNED_IN } from "./e2e/helpers";

// The repo root, wherever this file is: `web/` sits in it.
const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// A port nothing is listening on, picked by the OS. Never a fixed one: 8765 is udl's own
// default, where the developer's `udl serve` may be answering, and two worktrees' runs at once
// must not meet.
async function freePort(): Promise<number> {
	const server = createServer();
	await new Promise<void>((listening) => server.listen(0, "127.0.0.1", listening));
	const address = server.address();
	await new Promise<void>((closed, failed) =>
		server.close((error) => (error ? failed(error) : closed())),
	);
	if (address === null || typeof address === "string") throw new Error("the OS gave no port");
	return address.port;
}

// One port for the whole run: picked here, and put in the environment so anything that reads
// this file again (Playwright's workers) gets the same one. `baseURL`, `webServer.url` and the
// harness's `--port` are all it.
const port = process.env.UDL_E2E_PORT ? Number(process.env.UDL_E2E_PORT) : await freePort();
process.env.UDL_E2E_PORT = String(port);
const url = `http://127.0.0.1:${port}`;

// The harness's control API (what a test asks a fake to answer with next), on a port of its own:
// 29.12's chat writes the turns a scripted harness runs, so a spec reaches it directly.
const controls = process.env.UDL_E2E_CONTROLS
	? Number(process.env.UDL_E2E_CONTROLS)
	: await freePort();
process.env.UDL_E2E_CONTROLS = String(controls);

// Playwright's version is pinned to the Python Playwright's (uv.lock: 1.63.0), so both use the
// one Chromium in ~/.cache/ms-playwright.
export default defineConfig({
	testDir: "./e2e",
	fullyParallel: false,
	workers: 1,
	use: {
		baseURL: url,
		browserName: "chromium",
	},
	projects: [
		// (One sign-in for the run, in a browser of its own: `auth.setup.ts`.)
		{ name: "setup", testMatch: /auth\.setup\.ts/ },
		{
			name: "light",
			use: { colorScheme: "light", storageState: SIGNED_IN },
			dependencies: ["setup"],
		},
		{
			name: "dark",
			use: { colorScheme: "dark", storageState: SIGNED_IN },
			dependencies: ["setup"],
		},
	],
	webServer: {
		// (The venv's Python, not `uv run`: Playwright's webServer stops it with SIGTERM, and
		// it must reach the server itself, not the `uv` in front of it. An absolute path, since
		// the venv compares its `sys.prefix` against it; `cwd` is the repo root, where the
		// server's udl runs.)
		command: `PYTHONPATH=tests ${resolve(repo, ".venv/bin/python")} -u -m support.e2e --port ${port} --controls ${controls}`,
		url: `${url}/docs`,
		cwd: repo,
		// (Always: a server Playwright did not start is not the one under test. The harness's
		// own is isolated in a temp dir; a stray one on this port could be the developer's udl,
		// with their own data.)
		reuseExistingServer: false,
		timeout: 120_000,
	},
});
