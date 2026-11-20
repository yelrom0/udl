/**
 * The contribution registry: everything the shell draws from a contribution point, whether the
 * shell brought it or a UI plugin did. udl's own pages are registered here too, so the
 * navigation and the slots treat them like a plugin's.
 *
 * A contribution is looked up by the key its point uses: a route's path, the plugin whose
 * settings it is, the integration whose accounts it connects, an event type, or a slot.
 */

import type {
	AccountSetupProps,
	ActionEditorProps,
	EventViewProps,
	FilterEditorProps,
	Mount,
	PluginIdentity,
	RouteProps,
	SettingsProps,
	WidgetProps,
} from "@udl/ui-sdk";

/** The points a contribution can be drawn from, with the props its mount is given. */
export interface Points {
	routes: RouteProps;
	settings: SettingsProps;
	accountSetup: AccountSetupProps;
	eventViews: EventViewProps;
	filterEditors: FilterEditorProps;
	actionEditors: ActionEditorProps;
	widgets: WidgetProps;
}

/** One of them. */
export type Point = keyof Points;

/** Every point, in the order the shell reads them. */
export const POINTS: readonly Point[] = [
	"routes",
	"settings",
	"accountSetup",
	"eventViews",
	"filterEditors",
	"actionEditors",
	"widgets",
];

/** Something in the registry: whose it is, what it is called there, and how it mounts. */
export interface Contribution<P> {
	/** The plugin it belongs to (the shell's own pages name the shell). */
	readonly plugin: PluginIdentity;
	/** The key it is registered under. */
	readonly key: string;
	/** A widget's id within its slot (`contributes.widgets[].id`). */
	readonly id?: string;
	/** The key its bundle's record has it under (a widget's id; else the key). */
	readonly entry?: string;
	/** What draws it, when the shell itself does (a plugin's comes from its bundle). */
	readonly mount?: Mount<P>;
}

/** A page in the shell's navigation: the shell's own, or one a plugin brings. */
export interface NavEntry {
	/** Whose it is (the shell's own say `udl`). */
	readonly plugin: PluginIdentity;
	/** What it says. */
	readonly title: string;
	/** Where it goes (a plugin's is `/p/<name>/<route>`). */
	readonly to: string;
	/** The icon's name (the shell maps it to lucide's). */
	readonly icon?: string;
}

/**
 * The key a point looks a contribution up by, and (for a route) the plugin whose it is. A
 * route's props carry its path, a settings panel's the plugin, and a widget's its slot; the
 * rest name their key as `lookup` (the integration, the action kind).
 */
const KEYS: { [Q in Point]: (props: Points[Q], lookup?: string) => { key: string; of?: string } } =
	{
		routes: (props, lookup) => ({
			key: props.path,
			...(lookup === undefined ? {} : { of: lookup }),
		}),
		settings: (props) => ({ key: props.plugin }),
		accountSetup: (props) => ({ key: props.integration }),
		eventViews: (props) => ({ key: eventTypeOf(props.event) }),
		filterEditors: (_props, lookup) => ({ key: lookup ?? "" }),
		actionEditors: (_props, lookup) => ({ key: lookup ?? "" }),
		widgets: (props) => ({ key: props.slot }),
	};

/** The type of the event an event view is drawn for (its `type`). */
export function eventTypeOf(event: { readonly [key: string]: unknown }): string {
	const type = event.type;
	return typeof type === "string" ? type : "";
}

/** What a point looks a contribution up by, from the props it is given. */
export function keyOf<P extends Point>(
	point: P,
	props: Points[P],
	lookup?: string,
): { key: string; of?: string } {
	return KEYS[point](props, lookup);
}

/** The contributions of one point, by key. */
export class Contributions<P> {
	readonly #byKey = new Map<string, Contribution<P>[]>();

	/** Add one, after the ones already under its key. */
	add(key: string, contribution: Contribution<P>): void {
		const found = this.#byKey.get(key);
		if (found === undefined) {
			this.#byKey.set(key, [contribution]);
			return;
		}
		found.push(contribution);
	}

	/** The ones under `key`. */
	at(key: string): readonly Contribution<P>[] {
		return this.#byKey.get(key) ?? [];
	}

	/** Every one, whatever its key, in the order they were added. */
	every(): readonly Contribution<P>[] {
		return [...this.#byKey.values()].flat();
	}

	/**
	 * The ones an event type is drawn with: those registered for it, and those whose key ends
	 * with `.*` and is a prefix of it (a manifest's `"gmail.*"`).
	 */
	matching(eventType: string): readonly Contribution<P>[] {
		const found = [...this.at(eventType)];
		for (const [key, contributions] of this.#byKey) {
			if (!key.endsWith(".*")) {
				continue;
			}
			const prefix = key.slice(0, -1);
			if (eventType.startsWith(prefix)) {
				found.push(...contributions);
			}
		}
		return found;
	}

	/** Forget everything (a test's own registry, and a page that signs out). */
	clear(): void {
		this.#byKey.clear();
	}
}

/** The shell's registry: one list per point, and the navigation's pages. */
export class Registry {
	readonly nav: NavEntry[] = [];

	readonly #points: { [P in Point]: Contributions<Points[P]> } = {
		routes: new Contributions<RouteProps>(),
		settings: new Contributions<SettingsProps>(),
		accountSetup: new Contributions<AccountSetupProps>(),
		eventViews: new Contributions<EventViewProps>(),
		filterEditors: new Contributions<FilterEditorProps>(),
		actionEditors: new Contributions<ActionEditorProps>(),
		widgets: new Contributions<WidgetProps>(),
	};

	/** One point's contributions. */
	point<P extends Point>(point: P): Contributions<Points[P]> {
		return this.#points[point];
	}

	/** Forget every point's, and the navigation's (a page that signs out). */
	clear(): void {
		for (const point of POINTS) {
			this.#points[point].clear();
		}
		this.nav.length = 0;
	}
}
