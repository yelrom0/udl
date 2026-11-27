/**
 * A panel's own things (29.12): the key it is registered under (`<plugin>/<id>`), where its
 * window is, and the key that opens it (`Ctrl+J`). The store keeps the first and the shell's
 * buttons and windows are the rest.
 */

/** The key a panel's contribution is registered under. */
export function panelKey(plugin: string, id: string): string {
	return `${plugin}/${id}`;
}

/** What a panel's key names: the plugin whose panel it is, and its id within it. */
export interface PanelKey {
	readonly plugin: string;
	readonly id: string;
}

/** `key` (`orch/chat`), as its plugin and id (`null` when it is not one). */
export function partsOfPanel(key: string): PanelKey | null {
	const at = key.indexOf("/");
	if (at <= 0 || at === key.length - 1) {
		return null;
	}
	return { plugin: key.slice(0, at), id: key.slice(at + 1) };
}

/** Where a panel's own window is: the page's route for it (the shell is served at `/ui/`). */
export function panelUrl(key: string): string {
	const parts = partsOfPanel(key);
	if (parts === null) {
		return "";
	}
	return `/ui/panel/${encodeURIComponent(parts.plugin)}/${encodeURIComponent(parts.id)}`;
}

/** What a panel's window is called (opening it twice brings the same one forward). */
export function panelWindowName(key: string): string {
	const parts = partsOfPanel(key);
	return parts === null ? "udl-panel" : `udl-panel-${parts.plugin}-${parts.id}`;
}

/** A key that opens a panel: Ctrl (or Cmd) and a letter, with Shift and Alt as they are given. */
export interface Shortcut {
	readonly ctrl: boolean;
	readonly shift: boolean;
	readonly alt: boolean;
	/** The letter, in lower case (`j`). */
	readonly key: string;
}

/**
 * `text` (`Ctrl+J`, `Ctrl+Shift+K`, `Alt+C`), as the shortcut it names. `null` when it is not
 * one: a shortcut is Ctrl or Cmd, and one key, with Shift and Alt allowed beside them.
 */
export function shortcutOf(text: string | null | undefined): Shortcut | null {
	if (text === null || text === undefined) {
		return null;
	}
	const parts = text
		.split("+")
		.map((part) => part.trim())
		.filter((part) => part !== "");
	if (parts.length < 2) {
		return null;
	}
	const key = parts[parts.length - 1] ?? "";
	if (key === "" || key === "Ctrl" || key === "Cmd" || key === "Shift" || key === "Alt") {
		return null;
	}
	let ctrl = false;
	let shift = false;
	let alt = false;
	for (const part of parts.slice(0, -1)) {
		const one = part.toLowerCase();
		if (one === "ctrl" || one === "cmd" || one === "meta" || one === "control") {
			ctrl = true;
			continue;
		}
		if (one === "shift") {
			shift = true;
			continue;
		}
		if (one === "alt" || one === "option") {
			alt = true;
			continue;
		}
		return null;
	}
	if (!ctrl && !alt) {
		// (A letter on its own is not a shortcut: it would fire while a person types.)
		return null;
	}
	return { ctrl, shift, alt, key: key.toLowerCase() };
}

/** Whether `event` is the shortcut: the same modifiers, and the same key (either case). */
export function matchesShortcut(event: KeyboardEvent, shortcut: Shortcut): boolean {
	// (Cmd on a Mac is Ctrl elsewhere: a manifest says Ctrl, and both open it.)
	const ctrl = event.ctrlKey || event.metaKey;
	return (
		ctrl === shortcut.ctrl &&
		event.shiftKey === shortcut.shift &&
		event.altKey === shortcut.alt &&
		event.key.toLowerCase() === shortcut.key
	);
}
