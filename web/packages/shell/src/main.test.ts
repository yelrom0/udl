import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startFakingMatchMedia, stopFakingMatchMedia } from "./test-setup";

const drawn: { root: Element; app: unknown }[] = [];

vi.mock("react-dom/client", () => ({
	createRoot: (root: Element) => ({
		render: (app: unknown) => {
			drawn.push({ root, app });
		},
	}),
}));

vi.mock("./router", () => ({ router: { routeTree: null } }));

beforeEach(() => {
	startFakingMatchMedia();
	drawn.length = 0;
	document.body.innerHTML = "";
	// (Each import is a fresh module: the page's own <div id="root"> decides what happens.)
	vi.resetModules();
});

afterEach(() => {
	stopFakingMatchMedia();
	delete document.documentElement.dataset.theme;
});

async function start(): Promise<void> {
	await import("./main");
}

describe("starting the shell", () => {
	it("draws the router into #root, with the theme on <html>", async () => {
		const root = document.createElement("div");
		root.id = "root";
		document.body.append(root);

		await start();

		expect(drawn).toHaveLength(1);
		expect(drawn[0]?.root).toBe(root);
		expect(drawn[0]?.app).toBeTruthy();
		expect(document.documentElement.dataset.theme).toBe("light");
	});

	it("says so when the page has no #root", async () => {
		await expect(start()).rejects.toThrow("the page has no #root");
	});
});
