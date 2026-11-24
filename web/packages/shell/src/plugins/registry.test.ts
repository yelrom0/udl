/**
 * The contribution registry: what the shell and its plugins put in each of the points, looked
 * up by the key the point uses.
 */

import type {
	AccountSetupProps,
	ActionEditorProps,
	EventViewProps,
	FilterEditorProps,
	Mount,
	PanelProps,
	PluginIdentity,
	RouteProps,
	SettingsProps,
	WidgetProps,
} from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { type Contribution, keyOf, Registry } from "./registry";
import { UDL } from "./shell";

const GMAIL: PluginIdentity = { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" };
const WEATHER: PluginIdentity = {
	name: "weather",
	version: "0.2.0",
	baseUrl: "/ui/plugins/weather/",
};

/** A mount that draws nothing: what a test registers to say "this contribution is here". */
const nothing =
	<P>(): Mount<P> =>
	() => ({ update: () => {}, unmount: () => {} });

const contribution = <P>(plugin: PluginIdentity, key: string, id?: string): Contribution<P> => ({
	plugin,
	key,
	...(id === undefined ? {} : { id }),
	mount: nothing<P>(),
});

describe("the contribution registry", () => {
	it("looks a contribution up by the key its point uses", () => {
		const registry = new Registry();
		const inbox = contribution<RouteProps>(GMAIL, "inbox");
		registry.point("routes").add("inbox", inbox);

		expect(registry.point("routes").at("inbox")).toEqual([inbox]);
		expect(registry.point("routes").at("compose")).toEqual([]);
	});

	it("keeps each point's contributions apart", () => {
		const registry = new Registry();
		const route = contribution<RouteProps>(GMAIL, "gmail");
		const settings = contribution<SettingsProps>(GMAIL, "gmail");
		registry.point("routes").add("gmail", route);
		registry.point("settings").add("gmail", settings);

		expect(registry.point("routes").at("gmail")).toEqual([route]);
		expect(registry.point("settings").at("gmail")).toEqual([settings]);
	});

	it("keeps the order contributions were added in", () => {
		const registry = new Registry();
		const first = contribution<WidgetProps>(GMAIL, "dashboard", "unread");
		const second = contribution<WidgetProps>(WEATHER, "dashboard", "forecast");
		registry.point("widgets").add("dashboard", first);
		registry.point("widgets").add("dashboard", second);

		expect(registry.point("widgets").at("dashboard")).toEqual([first, second]);
	});

	it("lists every contribution of a point, whatever its key", () => {
		const registry = new Registry();
		registry.point("eventViews").add("gmail.*", contribution<EventViewProps>(GMAIL, "gmail.*"));
		registry
			.point("eventViews")
			.add("weather.today", contribution<EventViewProps>(WEATHER, "weather.today"));

		expect(registry.point("eventViews").every()).toHaveLength(2);
	});

	it("matches an event type by a prefix a plugin ended with a star", () => {
		const registry = new Registry();
		const views = registry.point("eventViews");
		const gmail = contribution<EventViewProps>(GMAIL, "gmail.*");
		const weather = contribution<EventViewProps>(WEATHER, "weather.today");
		views.add("gmail.*", gmail);
		views.add("weather.today", weather);

		expect(views.matching("gmail.message.received")).toEqual([gmail]);
		expect(views.matching("weather.today")).toEqual([weather]);
		expect(views.matching("gmail")).toEqual([]);
		expect(views.matching("weather.tomorrow")).toEqual([]);
	});

	it("finds a widget by its slot, with the id it was registered under", () => {
		const registry = new Registry();
		const unread = contribution<WidgetProps>(GMAIL, "dashboard", "unread");
		registry.point("widgets").add("dashboard", unread);

		const [found] = registry.point("widgets").at("dashboard");
		expect(found?.id).toBe("unread");
		expect(found?.plugin.name).toBe("gmail");
	});

	it("looks a route up by its path, and the plugin whose it is", () => {
		expect(keyOf("routes", { path: "inbox", params: {}, search: {} }, "gmail")).toEqual({
			key: "inbox",
			of: "gmail",
		});
	});

	it("looks a settings panel up by the plugin whose settings they are", () => {
		const props: SettingsProps = {
			plugin: "gmail",
			info: { title: "Gmail" },
			save: async () => {},
		};

		expect(keyOf("settings", props)).toEqual({ key: "gmail" });
	});

	it("looks an event view up by the event's type", () => {
		const props: EventViewProps = { event: { type: "gmail.message" } };

		expect(keyOf("eventViews", props)).toEqual({ key: "gmail.message" });
		expect(keyOf("eventViews", { event: {} })).toEqual({ key: "" });
	});

	it("looks an editor up by the key it is asked for", () => {
		const filter: FilterEditorProps = { value: null, onChange: () => {}, eventTypes: [] };
		const action: ActionEditorProps = { options: {}, onChange: () => {}, schema: {} };

		expect(keyOf("filterEditors", filter, "gmail")).toEqual({ key: "gmail" });
		expect(keyOf("actionEditors", action, "gmail.send")).toEqual({ key: "gmail.send" });
		expect(keyOf("filterEditors", filter)).toEqual({ key: "" });
	});

	it("looks an account setup panel up by its integration, and a widget by its slot", () => {
		const setup: AccountSetupProps = { integration: "gmail", done: () => {} };
		const widget: WidgetProps = { slot: "dashboard", plugin: UDL };

		expect(keyOf("accountSetup", setup)).toEqual({ key: "gmail" });
		expect(keyOf("widgets", widget)).toEqual({ key: "dashboard" });
	});

	it("looks a panel up by the key its plugin gave it (29.12)", () => {
		const props: PanelProps = { open: true, close: () => {}, popOut: () => {} };
		const registry = new Registry();
		const chat = contribution<PanelProps>(GMAIL, "gmail/chat");
		registry.point("panels").add("gmail/chat", chat);

		expect(keyOf("panels", props, "gmail/chat")).toEqual({ key: "gmail/chat" });
		expect(registry.point("panels").at("gmail/chat")).toEqual([chat]);
	});

	it("forgets everything when it is cleared", () => {
		const registry = new Registry();
		registry.point("settings").add("gmail", contribution<SettingsProps>(GMAIL, "gmail"));
		registry.point("accountSetup").add("gmail", contribution<AccountSetupProps>(GMAIL, "gmail"));
		registry.point("filterEditors").add("gmail", contribution<FilterEditorProps>(GMAIL, "gmail"));
		registry.point("actionEditors").add("echo", contribution<ActionEditorProps>(GMAIL, "echo"));

		registry.clear();

		for (const point of [
			"routes",
			"settings",
			"accountSetup",
			"eventViews",
			"filterEditors",
			"actionEditors",
			"widgets",
			"panels",
		] as const) {
			expect(registry.point(point).every()).toEqual([]);
		}
	});
});
