/**
 * What jsdom does not have: `matchMedia` (jsdom does not follow `prefers-color-scheme`), with a
 * `matches` a test can set and listeners a test can fire, so the theme store can be tested in
 * both the light and the dark system.
 */

import { vi } from "vitest";

const listeners = new Set<(event: MediaQueryListEvent) => void>();
let dark = false;

const query = (): MediaQueryList =>
	({
		get matches() {
			return dark;
		},
		media: "(prefers-color-scheme: dark)",
		addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
			listeners.add(listener);
		},
		removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
			listeners.delete(listener);
		},
	}) as unknown as MediaQueryList;

vi.stubGlobal("matchMedia", vi.fn(query));

/** Say what the system is, and tell the shell it changed (as a browser does). */
export function systemIs(darkNow: boolean): void {
	dark = darkNow;
	for (const listener of listeners) {
		listener({ matches: dark } as MediaQueryListEvent);
	}
}

/** The media query lists still listening (so a test can say the shell stopped following). */
export const watching = (): number => listeners.size;

export const resetSystem = () => {
	listeners.clear();
	dark = false;
};
