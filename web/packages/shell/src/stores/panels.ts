/**
 * The panels store (29.12): which of the page's panels are open — by `<plugin>/<id>`, the key a
 * panel's contribution is registered under — and how wide they are. Both are the person's, and
 * are kept in this browser, so a reload shows what they had. A stored value that cannot be read
 * is not a broken page: the panels start closed, at their own width.
 */

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

/** Where the choice is kept. */
export const KEY = "udl.panels";

/** How wide a docked panel is until it is dragged. */
export const DEFAULT_WIDTH = 400;

/** How narrow and how wide a panel may be dragged (a phone's sheet is the window's width). */
export const MIN_WIDTH = 320;
export const MAX_WIDTH = 800;

/** The panels' state: the open ones, and the width a docked one takes. */
export interface PanelsState {
	/** The open panels, by `<plugin>/<id>`, in the order they were opened. */
	readonly open: readonly string[];
	/** A docked panel's width, in pixels. */
	readonly width: number;
}

/** `value`, when it is a width a panel may have. */
function widthOf(value: unknown): number | null {
	if (typeof value !== "number" || !Number.isFinite(value)) {
		return null;
	}
	return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(value)));
}

/** What this browser kept, as the page's panels start. */
function initial(): PanelsState {
	try {
		const saved: unknown = JSON.parse(localStorage.getItem(KEY) ?? "null");
		if (typeof saved !== "object" || saved === null) {
			return { open: [], width: DEFAULT_WIDTH };
		}
		const one = saved as Record<string, unknown>;
		const open = Array.isArray(one.open)
			? one.open.filter((key: unknown): key is string => typeof key === "string" && key !== "")
			: [];
		return { open, width: widthOf(one.width) ?? DEFAULT_WIDTH };
	} catch {
		// (Storage that cannot be read, or is not JSON: the panels start closed.)
		return { open: [], width: DEFAULT_WIDTH };
	}
}

export const panels = createStore<PanelsState>(() => initial());

/** Keep the state in this browser (private mode, and a full store, only last this visit). */
function remember(state: PanelsState): void {
	try {
		localStorage.setItem(KEY, JSON.stringify(state));
	} catch {
		// (Nothing to do: the choice lasts this visit.)
	}
}

/** Whether `key`'s panel is open. */
export function isOpen(key: string): boolean {
	return panels.getState().open.includes(key);
}

/** Open `key`'s panel (it keeps its place when it is already open). */
export function openPanel(key: string): void {
	const state = panels.getState();
	if (state.open.includes(key)) {
		return;
	}
	const next = { ...state, open: [...state.open, key] };
	panels.setState(next);
	remember(next);
}

/** Close `key`'s panel. */
export function closePanel(key: string): void {
	const state = panels.getState();
	if (!state.open.includes(key)) {
		return;
	}
	const next = { ...state, open: state.open.filter((one) => one !== key) };
	panels.setState(next);
	remember(next);
}

/** Open `key`'s panel when it is closed, and close it when it is open. */
export function togglePanel(key: string): void {
	if (isOpen(key)) {
		closePanel(key);
		return;
	}
	openPanel(key);
}

/** Set a docked panel's width (kept between its bounds, and remembered). */
export function setPanelWidth(width: number): void {
	const state = panels.getState();
	const next = { ...state, width: widthOf(width) ?? state.width };
	panels.setState(next);
	remember(next);
}

/** The open panels, as a hook. */
export const usePanels = () => useStore(panels, (state) => state.open);

/** A docked panel's width, as a hook. */
export const usePanelWidth = () => useStore(panels, (state) => state.width);
