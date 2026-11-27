/**
 * A panel's key, its window and its shortcut (29.12): what the store keeps and the shell's
 * buttons and windows read, without a DOM in sight.
 */

import { describe, expect, it } from "vitest";
import {
	matchesShortcut,
	panelKey,
	panelUrl,
	panelWindowName,
	partsOfPanel,
	shortcutOf,
} from "./panels";

/** A keydown, as the page's own handler sees it. */
function pressed(key: string, modifiers: Partial<KeyboardEvent> = {}): KeyboardEvent {
	return new KeyboardEvent("keydown", { key, ...modifiers });
}

describe("a panel's key", () => {
	it("is its plugin and its id", () => {
		expect(panelKey("orch", "chat")).toBe("orch/chat");
		expect(partsOfPanel("orch/chat")).toEqual({ plugin: "orch", id: "chat" });
	});

	it("is not a key when it names no plugin or no id", () => {
		for (const key of ["", "chat", "/chat", "orch/", "orch"]) {
			expect(partsOfPanel(key)).toBeNull();
		}
	});

	it("keeps a plugin's own dots and dashes", () => {
		expect(partsOfPanel("my.plugin/my-chat")).toEqual({ plugin: "my.plugin", id: "my-chat" });
	});
});

describe("a panel's window", () => {
	it("is the page's own route for it", () => {
		expect(panelUrl("orch/chat")).toBe("/ui/panel/orch/chat");
		expect(panelUrl("my.plugin/my-chat")).toBe("/ui/panel/my.plugin/my-chat");
	});

	it("is named after the panel, so opening it again finds the same window", () => {
		expect(panelWindowName("orch/chat")).toBe("udl-panel-orch-chat");
	});

	it("is nowhere at all when the key names no panel", () => {
		expect(panelUrl("chat")).toBe("");
		expect(panelWindowName("chat")).toBe("udl-panel");
	});
});

describe("a panel's shortcut", () => {
	it("reads Ctrl and a letter", () => {
		expect(shortcutOf("Ctrl+J")).toEqual({ ctrl: true, shift: false, alt: false, key: "j" });
	});

	it("reads Cmd, Shift and Alt beside it", () => {
		expect(shortcutOf("Cmd+Shift+K")).toEqual({ ctrl: true, shift: true, alt: false, key: "k" });
		expect(shortcutOf("Alt+C")).toEqual({ ctrl: false, shift: false, alt: true, key: "c" });
	});

	it("is not a shortcut without a modifier, or with no key", () => {
		for (const text of ["J", "", "Ctrl", "Ctrl+", "Ctrl+J+K+Z", "Hyper+J"]) {
			expect(shortcutOf(text)).toBeNull();
		}
		expect(shortcutOf(null)).toBeNull();
	});

	it("matches the key a person pressed, in either case", () => {
		const shortcut = shortcutOf("Ctrl+J");
		if (shortcut === null) {
			throw new Error("Ctrl+J is a shortcut");
		}

		expect(matchesShortcut(pressed("j", { ctrlKey: true }), shortcut)).toBe(true);
		expect(matchesShortcut(pressed("J", { ctrlKey: true }), shortcut)).toBe(true);
		// (Cmd is the Mac's Ctrl, and opens the same panel.)
		expect(matchesShortcut(pressed("j", { metaKey: true }), shortcut)).toBe(true);
	});

	it("does not match another key, or the same key with other modifiers", () => {
		const shortcut = shortcutOf("Ctrl+J");
		if (shortcut === null) {
			throw new Error("Ctrl+J is a shortcut");
		}

		expect(matchesShortcut(pressed("k", { ctrlKey: true }), shortcut)).toBe(false);
		expect(matchesShortcut(pressed("j"), shortcut)).toBe(false);
		expect(matchesShortcut(pressed("j", { ctrlKey: true, shiftKey: true }), shortcut)).toBe(false);
		expect(matchesShortcut(pressed("j", { ctrlKey: true, altKey: true }), shortcut)).toBe(false);
	});
});
