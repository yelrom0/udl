import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startFakingMatchMedia, stopFakingMatchMedia, systemIs } from "../test-setup";
import {
	applyTheme,
	followSystem,
	isTheme,
	setTheme,
	shownTheme,
	systemTheme,
	theme,
	useTheme,
} from "./theme";

beforeEach(() => {
	startFakingMatchMedia();
	theme.setState({ current: "system" });
	localStorage.clear();
	delete document.documentElement.dataset.theme;
});

afterEach(() => {
	vi.restoreAllMocks();
	vi.resetModules();
	stopFakingMatchMedia();
	theme.setState({ current: "system" });
	delete document.documentElement.dataset.theme;
});

describe("the theme store", () => {
	it("starts on system", () => {
		const { result } = renderHook(() => useTheme());

		expect(result.current).toBe("system");
	});

	it("follows the system until a choice is made, and shows what it resolved to", () => {
		systemIs(false);
		applyTheme("system");
		expect(document.documentElement.dataset.theme).toBe("light");

		systemIs(true);
		expect(systemTheme()).toBe("dark");
		applyTheme("system");
		expect(document.documentElement.dataset.theme).toBe("dark");
	});

	it("keeps a choice and persists it", () => {
		systemIs(true);
		const { result } = renderHook(() => useTheme());

		act(() => setTheme("light"));

		expect(result.current).toBe("light");
		expect(document.documentElement.dataset.theme).toBe("light");
		expect(JSON.parse(localStorage.getItem("udl.theme") ?? "null")).toBe("light");
	});

	it("goes back to the system when it is chosen again", () => {
		const { result } = renderHook(() => useTheme());
		act(() => setTheme("dark"));
		act(() => setTheme("system"));

		expect(result.current).toBe("system");
		expect(document.documentElement.dataset.theme).toBe(systemTheme());
	});

	it("puts the theme on the page before it tells anyone", () => {
		// (A listener reads the page's tokens — a plugin's wrapper is given them — so it must see
		// the new theme's, not the one being left.)
		const seen: (string | undefined)[] = [];
		const stop = theme.subscribe(() => seen.push(document.documentElement.dataset.theme));

		setTheme("dark");
		stop();

		expect(seen).toEqual(["dark"]);
	});

	it("ignores a choice that is not a theme", () => {
		expect(isTheme("dark")).toBe(true);
		expect(isTheme("chartreuse")).toBe(false);
		expect(isTheme(3)).toBe(false);
		expect(isTheme(null)).toBe(false);
	});

	it("carries on when the choice cannot be saved", () => {
		const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
			throw new Error("no storage");
		});
		const { result } = renderHook(() => useTheme());

		act(() => setTheme("dark"));

		expect(result.current).toBe("dark");
		expect(set).toHaveBeenCalled();
	});

	it("reads a choice back on the next visit", async () => {
		localStorage.setItem("udl.theme", JSON.stringify("light"));
		vi.resetModules();

		const fresh = await import("./theme");

		expect(fresh.theme.getState().current).toBe("light");
	});

	it("starts on the system when the saved choice is not a theme", async () => {
		localStorage.setItem("udl.theme", JSON.stringify("chartreuse"));
		vi.resetModules();

		const fresh = await import("./theme");

		expect(fresh.theme.getState().current).toBe("system");
	});

	it("starts on the system when the saved choice is not JSON", async () => {
		localStorage.setItem("udl.theme", "{not json");
		vi.resetModules();

		const fresh = await import("./theme");

		expect(fresh.theme.getState().current).toBe("system");
	});

	it("starts on the system when the saved choice cannot be read", async () => {
		vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
			throw new Error("no storage");
		});
		vi.resetModules();

		const fresh = await import("./theme");

		expect(fresh.theme.getState().current).toBe("system");
	});

	it("follows the system while that is the choice, and stops when the shell goes", () => {
		const { result } = renderHook(() => useTheme());
		const stop = followSystem();

		systemIs(true);
		expect(result.current).toBe("system");
		expect(document.documentElement.dataset.theme).toBe("dark");

		// (A choice is kept: the system changing must not move it.)
		act(() => setTheme("light"));
		systemIs(false);
		expect(document.documentElement.dataset.theme).toBe("light");

		// (Stopped: back on the system, and the system changing does not reach the page.)
		stop();
		act(() => setTheme("system"));
		systemIs(true);
		expect(document.documentElement.dataset.theme).toBe("light");
	});

	it("says what the page is showing, which is the system's while that is the choice", () => {
		setTheme("dark");
		expect(shownTheme()).toBe("dark");

		setTheme("system");
		expect(shownTheme()).toBe(systemTheme());
	});
});
