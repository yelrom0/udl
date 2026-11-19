/**
 * Toasts: what the page says it just did, or that something failed. A toast goes away on its
 * own after a while (an error stays longer), or when the user dismisses it.
 */

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

export const KINDS = ["info", "success", "error"] as const;
export type ToastKind = (typeof KINDS)[number];

export interface Toast {
	readonly id: number;
	readonly kind: ToastKind;
	readonly message: string;
}

/** How long a toast stays: an error is worth reading, so it stays longer. */
export const LIFETIME: Record<ToastKind, number> = {
	info: 5000,
	success: 5000,
	error: 10000,
};

export const toasts = createStore<{ items: readonly Toast[] }>(() => ({ items: [] }));

let next = 1;

/** Say something on the page, and let it go away by itself. */
export function toast(kind: ToastKind, message: string): number {
	const id = next++;
	toasts.setState(({ items }) => ({ items: [...items, { id, kind, message }] }));
	setTimeout(() => dismiss(id), LIFETIME[kind]);
	return id;
}

/** Take a toast off the page. */
export function dismiss(id: number): void {
	toasts.setState(({ items }) => ({ items: items.filter((item) => item.id !== id) }));
}

/** The toasts on the page, as a hook. */
export const useToasts = () => useStore(toasts, (state) => state.items);
