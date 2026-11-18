import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetSystem, systemIs, watching } from "../test-setup";
import { applyTheme, followSystem, isTheme, setTheme, systemTheme, theme, useTheme } from "./theme";

beforeEach(() => {
	localStorage.clear();
	delete document.documentElement.dataset.theme;
	theme.setState({ current: "system" });
});

afterEach(() => {
	theme.setState({ current: "system" });
	resetSystem();
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

	it("reads a choice back on the next visit", () => {
		localStorage.setItem("udl.theme", JSON.stringify("light"));
		const saved: unknown = JSON.parse(localStorage.getItem("udl.theme") ?? "null");
		expect(isTheme(saved) && saved).toBe("light");
	});

	it("goes back to the system when it is chosen again", () => {
		const { result } = renderHook(() => useTheme());
		act(() => setTheme("dark"));
		act(() => setTheme("system"));
		expect(result.current).toBe("system");
		expect(document.documentElement.dataset.theme).toBe(systemTheme());
	});

	it("follows the system while that is the choice, and stops when the shell goes", () => {
		const { result } = renderHook(() => useTheme());
		const stop = followSystem();
		expect(watching()).toBe(1);

		systemIs(true);
		expect(result.current).toBe("system");
		expect(document.documentElement.dataset.theme).toBe("dark");

		// (A choice is kept: the system changing must not move it.)
		act(() => setTheme("light"));
		systemIs(false);
		expect(document.documentElement.dataset.theme).toBe("light");

		stop();
		expect(watching()).toBe(0);
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
		set.mockRestore();
	});
});
