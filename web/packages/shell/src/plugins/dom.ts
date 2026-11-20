/**
 * Where a contribution is drawn: its own shadow root (or the shell's DOM), the plugin's
 * stylesheets adopted there, its `@property` rules hoisted to the document (a shadow root
 * ignores them), and a wrapper carrying the theme.
 */

import type { UIPluginInfo } from "../stores/plugins";

/** The theme a wrapper carries: daisyUI's light or dark. */
export type Mode = "light" | "dark";

/** Where a plugin draws: the wrapper it is themed by, and the element it is given. */
export interface MountPoint {
	readonly wrapper: HTMLElement;
	readonly target: HTMLElement;
}

/** What `prepare` needs of the page. */
export interface PrepareOptions {
	readonly theme: Mode;
	readonly fetch?: typeof globalThis.fetch;
}

/** The one document-level sheet a plugin's `@property` rules were hoisted to, by plugin. */
const hoisted = new Map<string, CSSStyleSheet>();

/** The `@property` rules in `css` (`@property --x { ... }`, with its braces). */
export function propertyRules(css: string): string[] {
	const rules: string[] = [];
	const pattern = /@property\s+--[\w-]+\s*\{[^}]*\}/g;
	for (const found of css.matchAll(pattern)) {
		rules.push(found[0]);
	}
	return rules;
}

/** Whether this browser can build a stylesheet in memory (every one udl supports can). */
function canConstruct(): boolean {
	return typeof CSSStyleSheet === "function" && "replaceSync" in CSSStyleSheet.prototype;
}

/** `text`, as a stylesheet the document or a shadow root adopts. */
function sheetOf(text: string): CSSStyleSheet {
	const sheet = new CSSStyleSheet();
	sheet.replaceSync(text);
	return sheet;
}

/**
 * Copy `text`'s `@property` rules to one document-level sheet per plugin: a shadow root ignores
 * them, so a Tailwind 4 plugin's custom properties would be unregistered inside its own root.
 */
function hoist(info: UIPluginInfo, text: string): void {
	const rules = propertyRules(text);
	if (rules.length === 0 || !canConstruct()) {
		return;
	}
	const known = hoisted.get(info.name);
	if (known !== undefined) {
		return;
	}
	const sheet = sheetOf(rules.join("\n"));
	hoisted.set(info.name, sheet);
	document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
}

/** What a stylesheet says, as udl reads it (`its text`), or an error naming the file. */
async function textOf(url: string, fetch: typeof globalThis.fetch): Promise<string> {
	const response = await fetch(url, { credentials: "same-origin" });
	if (!response.ok) {
		throw new Error(`udl could not read ${url}: it answered ${response.status}`);
	}
	return response.text();
}

/** A `div` with `className` inside `parent`. */
function div(parent: HTMLElement | ShadowRoot, className: string): HTMLElement {
	const element = document.createElement("div");
	element.className = className;
	parent.append(element);
	return element;
}

/**
 * Get `host` ready for `info`'s contribution: an open shadow root with the plugin's stylesheets
 * adopted (`isolation: "shadow"`, the default), or the shell's own DOM (`"none"`), and a
 * wrapper inside it carrying `theme` so daisyUI's tokens reach the plugin.
 */
export async function prepare(
	host: HTMLElement,
	info: UIPluginInfo | undefined,
	{ theme, fetch = globalThis.fetch }: PrepareOptions,
): Promise<MountPoint> {
	const styles = await Promise.all((info?.styles ?? []).map(async (url) => textOf(url, fetch)));
	// (The shell's own contributions draw in its own DOM: they share its React and its CSS.)
	const shadowed = info !== undefined && info.isolation !== "none";
	const root: HTMLElement | ShadowRoot = shadowed
		? (host.shadowRoot ?? host.attachShadow({ mode: "open" }))
		: host;
	// (A slot drawn again — a retry — starts from an empty root.)
	root.replaceChildren();
	if (root instanceof ShadowRoot && canConstruct()) {
		root.adoptedStyleSheets = styles.map((text) => sheetOf(text));
	}
	if (info !== undefined) {
		for (const text of styles) {
			hoist(info, text);
		}
	}
	const wrapper = div(root, "udl-plugin");
	wrapper.dataset.theme = theme;
	return { wrapper, target: div(wrapper, "udl-plugin-content") };
}

/** Forget the hoisted sheets (a page that signs out, and a test's own page). */
export function clearHoisted(): void {
	hoisted.clear();
	document.adoptedStyleSheets = [];
}
