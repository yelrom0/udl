import { renderHook } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { dismiss, LIFETIME, toast, toasts, useToasts } from "./toasts";

beforeEach(() => {
	vi.useFakeTimers();
	toasts.setState({ items: [] });
});

afterEach(() => {
	vi.useRealTimers();
});

describe("the toasts store", () => {
	it("says something, and takes it away after a while", () => {
		toast("success", "Saved");
		expect(toasts.getState().items).toHaveLength(1);
		expect(toasts.getState().items[0]?.message).toBe("Saved");

		vi.advanceTimersByTime(LIFETIME.success);

		expect(toasts.getState().items).toEqual([]);
	});

	it("keeps an error longer than a note", () => {
		toast("info", "Working");
		toast("error", "It failed");

		vi.advanceTimersByTime(LIFETIME.info);

		expect(toasts.getState().items.map((item) => item.message)).toEqual(["It failed"]);
	});

	it("gives each toast its own id", () => {
		const first = toast("info", "One");
		const second = toast("info", "Two");

		expect(first).not.toBe(second);
	});

	it("takes one away when it is dismissed, and leaves the rest", () => {
		const first = toast("info", "One");
		toast("error", "Two");

		dismiss(first);

		expect(toasts.getState().items.map((item) => item.message)).toEqual(["Two"]);
	});

	it("dismissing one that is gone is nothing", () => {
		dismiss(999);

		expect(toasts.getState().items).toEqual([]);
	});

	it("hands the toasts to a component, as they come and go", () => {
		const { result } = renderHook(() => useToasts());
		expect(result.current).toEqual([]);

		act(() => {
			toast("info", "One");
		});

		expect(result.current.map((item) => item.message)).toEqual(["One"]);
	});
});
