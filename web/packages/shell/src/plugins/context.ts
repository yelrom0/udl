/**
 * What a plugin gets from the shell (`UIContext`): the API, the live events, the theme, the
 * stores, navigation, toasts and questions. It holds no state of its own: everything is the
 * shell's, read through here.
 */

import type { JsonObject, JsonValue, Store, Theme, UIContext } from "@udl/ui-sdk";
import { api } from "../api";
import { ask } from "../stores/confirms";
import { live } from "../stores/live";
import type { UIPluginInfo } from "../stores/plugins";
import { plugins } from "../stores/plugins";
import { systemTheme, theme } from "../stores/theme";
import { toast as say, toasts } from "../stores/toasts";
import { callOperation } from "./call";
import { eventsOf } from "./events";

/** The daisyUI tokens a plugin is given (the CSS custom properties of its theme). */
export const TOKENS: readonly string[] = [
	"--color-base-100",
	"--color-base-200",
	"--color-base-300",
	"--color-base-content",
	"--color-primary",
	"--color-primary-content",
	"--color-secondary",
	"--color-secondary-content",
	"--color-accent",
	"--color-accent-content",
	"--color-neutral",
	"--color-neutral-content",
	"--color-info",
	"--color-info-content",
	"--color-success",
	"--color-success-content",
	"--color-warning",
	"--color-warning-content",
	"--color-error",
	"--color-error-content",
	"--radius-selector",
	"--radius-field",
	"--radius-box",
	"--size-selector",
	"--size-field",
	"--border",
	"--depth",
	"--noise",
];

/** What the page is showing: daisyUI's light or dark (`system` is whichever the system is). */
export function mode(): "light" | "dark" {
	const chosen = theme.getState().current;
	return chosen === "system" ? systemTheme() : chosen;
}

/** The theme as a plugin reads it: the mode, its name, its tokens, and when it changes. */
function themeOf(): Theme {
	const tokens: Record<string, string> = {};
	const style = window.getComputedStyle(document.documentElement);
	for (const token of TOKENS) {
		tokens[token] = style.getPropertyValue(token).trim();
	}
	const shown = mode();
	return {
		mode: shown,
		name: shown,
		tokens,
		onChange(listener) {
			return theme.subscribe(() => {
				listener(themeOf());
			});
		},
	};
}

/** One of the shell's stores, as a plugin may read it (its data is the shell's to change). */
function storeOf<T>(source: {
	getState: () => T;
	subscribe: (listener: (state: T) => void) => () => void;
}): Store<T> {
	return {
		getState: () => source.getState(),
		subscribe: (listener) => source.subscribe((state) => listener(state)),
	};
}

/** What `contextOf` needs of the page. */
export interface ContextOptions {
	/** The plugin the contribution belongs to. */
	readonly info: UIPluginInfo;
	/** Where the shell's router goes (the page's own path). */
	readonly navigate: (to: string) => void;
}

/** The context a plugin's contributions are given. */
export function contextOf({ info, navigate }: ContextOptions): UIContext {
	return {
		api,
		call: (name: string, request?: JsonObject): Promise<JsonValue> =>
			callOperation(name, request ?? {}),
		events: eventsOf(),
		get theme() {
			return themeOf();
		},
		stores: {
			live: storeOf(live),
			plugins: storeOf(plugins),
			toasts: storeOf(toasts),
			theme: storeOf(theme),
		},
		navigate,
		toast: (message, kind) => {
			say(kind ?? "info", message);
		},
		confirm: (question, options) => ask(question, options),
		plugin: { name: info.name, version: info.version, baseUrl: info.base_url },
	};
}
