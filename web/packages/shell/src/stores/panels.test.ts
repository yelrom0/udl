/**
 * The panels store (29.12): which of the page's panels are open and how wide they are, kept in
 * this browser so a reload shows what the person had. A value that cannot be read is not a
 * broken page: the panels start closed, at their own width.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
	closePanel,
	DEFAULT_WIDTH,
	isOpen,
	MAX_WIDTH,
	MIN_WIDTH,
	openPanel,
	panels,
	setPanelWidth,
	togglePanel,
} from "./panels";

beforeEach(() => {
	localStorage.clear();
	panels.setState({ open: [], width: DEFAULT_WIDTH });
});

describe("the panels store", () => {
	it("starts closed, at the default width", () => {
		expect(panels.getState().open).toEqual([]);
		expect(panels.getState().width).toBe(DEFAULT_WIDTH);
	});

	it("opens a panel, and remembers it in this browser", () => {
		openPanel("orch/chat");

		expect(isOpen("orch/chat")).toBe(true);
		expect(JSON.parse(localStorage.getItem("udl.panels") ?? "null")).toEqual({
			open: ["orch/chat"],
			width: DEFAULT_WIDTH,
		});
	});

	it("opens two panels, in the order they were opened", () => {
		openPanel("orch/chat");
		openPanel("orch/help");

		expect(panels.getState().open).toEqual(["orch/chat", "orch/help"]);
	});

	it("opens a panel once, however often it is asked for", () => {
		openPanel("orch/chat");
		openPanel("orch/chat");

		expect(panels.getState().open).toEqual(["orch/chat"]);
	});

	it("closes a panel, and remembers that too", () => {
		openPanel("orch/chat");
		closePanel("orch/chat");

		expect(isOpen("orch/chat")).toBe(false);
		expect(JSON.parse(localStorage.getItem("udl.panels") ?? "null")).toEqual({
			open: [],
			width: DEFAULT_WIDTH,
		});
	});

	it("closes a panel that was not open, and says nothing", () => {
		closePanel("orch/chat");

		expect(panels.getState().open).toEqual([]);
	});

	it("toggles a panel", () => {
		togglePanel("orch/chat");
		expect(isOpen("orch/chat")).toBe(true);

		togglePanel("orch/chat");
		expect(isOpen("orch/chat")).toBe(false);
	});

	it("keeps a width it is given, and remembers it", () => {
		setPanelWidth(520);

		expect(panels.getState().width).toBe(520);
		expect(JSON.parse(localStorage.getItem("udl.panels") ?? "null")).toEqual({
			open: [],
			width: 520,
		});
	});

	it("keeps a width between its own bounds", () => {
		setPanelWidth(10);
		expect(panels.getState().width).toBe(MIN_WIDTH);

		setPanelWidth(5_000);
		expect(panels.getState().width).toBe(MAX_WIDTH);
	});

	it("rounds a width to a whole pixel", () => {
		setPanelWidth(411.6);

		expect(panels.getState().width).toBe(412);
	});

	it("reads what this browser kept, when the page starts", async () => {
		localStorage.setItem("udl.panels", JSON.stringify({ open: ["orch/chat"], width: 640 }));
		vi.resetModules();
		const fresh = await import("./panels");

		expect(fresh.panels.getState()).toEqual({ open: ["orch/chat"], width: 640 });
	});

	it("ignores a stored value that is not JSON", async () => {
		localStorage.setItem("udl.panels", "{not json");
		vi.resetModules();
		const fresh = await import("./panels");

		expect(fresh.panels.getState()).toEqual({ open: [], width: fresh.DEFAULT_WIDTH });
	});

	it("keeps the keys it can read, and forgets what is not one", async () => {
		localStorage.setItem(
			"udl.panels",
			JSON.stringify({ open: ["orch/chat", 7, null], width: "wide" }),
		);
		vi.resetModules();
		const fresh = await import("./panels");

		expect(fresh.panels.getState()).toEqual({ open: ["orch/chat"], width: fresh.DEFAULT_WIDTH });
	});

	it("keeps a stored width only when it is a whole number in bounds", async () => {
		localStorage.setItem("udl.panels", JSON.stringify({ open: [], width: 12 }));
		vi.resetModules();
		const fresh = await import("./panels");

		expect(fresh.panels.getState().width).toBe(fresh.MIN_WIDTH);
	});
});
