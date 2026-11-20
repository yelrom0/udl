/**
 * What jsdom does not have: `matchMedia`, as a real (if small) implementation of the DOM's
 * interface. It keeps the listeners it is given and fires real `change` events when the
 * system's theme is said to change, so the theme store is tested against the same contract a
 * browser gives it — no casts, and no mocks.
 */

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// (A test's page starts empty: each test renders its own, and the one before it is taken away.)
afterEach(cleanup);

const QUERY = "(prefers-color-scheme: dark)";

export class FakeMediaQueryList extends EventTarget implements MediaQueryList {
	#dark: boolean;

	constructor(dark: boolean) {
		super();
		this.#dark = dark;
	}

	get matches(): boolean {
		return this.#dark;
	}

	get media(): string {
		return QUERY;
	}

	get onchange(): ((this: MediaQueryList, event: MediaQueryListEvent) => unknown) | null {
		return null;
	}

	set onchange(_handler: ((this: MediaQueryList, event: MediaQueryListEvent) => unknown) | null) {
		// (No `onchange` is set: listeners are added with addEventListener, as in a browser.)
	}

	set dark(dark: boolean) {
		if (this.#dark === dark) {
			return;
		}
		this.#dark = dark;
		this.dispatchEvent(new Event("change"));
	}

	/** The DOM's deprecated listener methods, which a browser still has. */
	addListener(_listener: (event: MediaQueryListEvent) => void): void {}
	removeListener(_listener: (event: MediaQueryListEvent) => void): void {}
}

let system: FakeMediaQueryList | undefined;

/** The one media query list `matchMedia` hands out, with what the system is now. */
function systemQuery(): FakeMediaQueryList {
	system ??= new FakeMediaQueryList(false);
	return system;
}

export function startFakingMatchMedia(): void {
	const query = systemQuery();
	vi.stubGlobal("matchMedia", (given: string) => {
		if (given !== QUERY) {
			throw new Error(`the test only knows ${QUERY}, not ${given}`);
		}
		return query;
	});
}

/** Say the system changed (dark or light), as a browser does. */
export function systemIs(dark: boolean): void {
	systemQuery().dark = dark;
}

export function stopFakingMatchMedia(): void {
	vi.unstubAllGlobals();
	system = undefined;
}

/**
 * What jsdom does not have either: `EventSource`. The page's own connection tests hand their
 * own in; a page test that does not care gets this one, which opens nothing and sends nothing.
 */
export class QuietEventSource extends EventTarget {
	static readonly CONNECTING = 0 as const;
	static readonly OPEN = 1 as const;
	static readonly CLOSED = 2 as const;

	readonly url: string;
	readonly withCredentials = false;
	readonly CONNECTING = 0 as const;
	readonly OPEN = 1 as const;
	readonly CLOSED = 2 as const;
	readyState: number = QuietEventSource.CONNECTING;
	onerror: ((this: EventSource, event: Event) => unknown) | null = null;
	onmessage: ((this: EventSource, event: MessageEvent) => unknown) | null = null;
	onopen: ((this: EventSource, event: Event) => unknown) | null = null;

	constructor(url: string | URL) {
		super();
		this.url = String(url);
	}

	close(): void {
		this.readyState = QuietEventSource.CLOSED;
	}
}

// (`defineProperty`, not `vi.stubGlobal`: a test that unsets the globals it stubbed keeps this.
// And jsdom's `EventSource` type is not one a small class can satisfy — its listeners are
// `MessageEvent` ones — so this is the page's own, given to the global the page reads.)
Object.defineProperty(globalThis, "EventSource", {
	value: globalThis.EventSource ?? QuietEventSource,
	configurable: true,
	writable: true,
});

/**
 * What jsdom has in name only: a constructed stylesheet (`new CSSStyleSheet()` is not its to
 * build) and the roots that adopt them (its `adoptedStyleSheets` takes only its own brand, which
 * a test cannot make). A browser has both; the page uses them for UI plugins' isolation, so the
 * tests get a stub of the same contract: text in, the sheets a root was given out.
 */
export class FakeStyleSheet {
	css = "";

	replaceSync(text: string): void {
		this.css = text;
	}

	static is(sheet: unknown): sheet is FakeStyleSheet {
		return sheet instanceof FakeStyleSheet;
	}
}

/** What a stylesheet says, for a test that adopted it. */
export function cssOf(sheet: CSSStyleSheet): string {
	return FakeStyleSheet.is(sheet) ? sheet.css : "";
}

Object.defineProperty(globalThis, "CSSStyleSheet", {
	value: FakeStyleSheet,
	configurable: true,
	writable: true,
});

// (One list per root, in a `WeakMap`, as a browser keeps it: assigning a root's sheets replaces
// them, and reading them back is what the page does.)
const adopted = new WeakMap<object, CSSStyleSheet[]>();

function adoptable(proto: object): void {
	Object.defineProperty(proto, "adoptedStyleSheets", {
		configurable: true,
		get(this: object): CSSStyleSheet[] {
			return adopted.get(this) ?? [];
		},
		set(this: object, sheets: CSSStyleSheet[]): void {
			adopted.set(this, sheets);
		},
	});
}

adoptable(ShadowRoot.prototype);
adoptable(Document.prototype);
