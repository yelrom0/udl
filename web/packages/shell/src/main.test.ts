import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const drawn: { root: Element; app: unknown }[] = [];

vi.mock("react-dom/client", () => ({
	createRoot: (root: Element) => ({
		render: (app: unknown) => {
			drawn.push({ root, app });
		},
	}),
}));

vi.mock("./app", () => ({ App: () => null }));

beforeEach(() => {
	drawn.length = 0;
	document.body.innerHTML = "";
	vi.resetModules();
	// (Each import is a fresh module: the page's own <div id="root"> decides what happens.)
});

afterEach(() => {
	delete document.documentElement.dataset.theme;
});

async function start(): Promise<void> {
	await import("./main");
}

describe("starting the shell", () => {
	it("draws the App into #root, with the theme on <html>", async () => {
		const root = document.createElement("div");
		root.id = "root";
		document.body.append(root);

		await start();

		expect(drawn).toHaveLength(1);
		expect(drawn[0]?.root).toBe(root);
		expect(drawn[0]?.app).toBeTruthy();
		const theme = document.documentElement.dataset.theme;
		expect(theme).toBe(
			vi.mocked(window.matchMedia).mock.results[0]?.value.matches ? "dark" : "light",
		);
	});

	it("says so when the page has no #root", async () => {
		await expect(start()).rejects.toThrow("the page has no #root");
	});
});
