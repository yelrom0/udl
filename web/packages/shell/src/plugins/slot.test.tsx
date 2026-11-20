/**
 * `<PluginSlot>`: what draws a contribution point. It looks the contributions up in the
 * registry, imports each plugin's bundle the first time one of its contributions is needed,
 * mounts it in its own shadow root (or the shell's DOM), and turns anything that goes wrong
 * into an error card in that slot — never the end of the page.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { RouteProps } from "@udl/ui-sdk";
import { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { plugins, type UIPluginInfo } from "../stores/plugins";
import { setTheme } from "../stores/theme";
import { clearHoisted } from "./dom";
import { forgetListeners } from "./events";
import { forgetPlugins } from "./load";
import { Registry } from "./registry";
import { PluginSlot } from "./slot";

const GMAIL: UIPluginInfo = {
	name: "gmail",
	version: "0.1.0",
	base_url: "/ui/plugins/gmail/",
	entry_url: "/ui/plugins/gmail/index.js",
	styles: [],
	isolation: "shadow",
	contributes: { routes: ["inbox"] },
	plugin: "gmail",
};

/** A registry with `info`'s contributions in it, as the page's own is filled at startup. */
function registryWith(info: UIPluginInfo): Registry {
	const registry = new Registry();
	register(registry, info);
	return registry;
}

/** The registry's own registration, as `plugins/register.ts` does it. */
function register(registry: Registry, info: UIPluginInfo): void {
	const identity = { name: info.name, version: info.version, baseUrl: info.base_url };
	for (const route of info.contributes.routes ?? []) {
		registry.point("routes").add(route, { plugin: identity, key: route, entry: route });
	}
	for (const widget of info.contributes.widgets ?? []) {
		registry
			.point("widgets")
			.add(widget.slot, { plugin: identity, key: widget.slot, id: widget.id, entry: widget.id });
	}
}

/** A mount that says it was drawn, and records what it was given. */
function recording() {
	const drawn: { target: HTMLElement; props: unknown }[] = [];
	const updates: unknown[] = [];
	let unmounted = 0;
	const mount = vi.fn((target: HTMLElement, props: unknown) => {
		drawn.push({ target, props });
		return {
			update: (next: unknown) => {
				updates.push(next);
			},
			unmount: () => {
				unmounted += 1;
			},
		};
	});
	return {
		mount,
		drawn,
		updates,
		get unmounted() {
			return unmounted;
		},
	};
}

const ROUTE: RouteProps = { path: "inbox", params: {}, search: {} };

beforeEach(() => {
	forgetPlugins();
	forgetListeners();
	clearHoisted();
	plugins.setState({ manifests: [], problems: [], errors: [] });
	setTheme("light");
});

describe("a plugin slot", () => {
	it("draws the contribution in a shadow root of its own", async () => {
		const drawn = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });

		const { container } = render(
			<PluginSlot
				point="routes"
				registry={registryWith(GMAIL)}
				lookup="gmail"
				importer={async () => ({ default: { routes: { inbox: drawn.mount } } })}
				props={ROUTE}
			/>,
		);

		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());
		const host = container.querySelector(".udl-plugin-slot");
		expect(host?.shadowRoot?.mode).toBe("open");
		const [called] = drawn.drawn;
		expect(called?.props).toEqual(ROUTE);
		expect(called?.target.closest(".udl-plugin")?.getAttribute("data-theme")).toBe("light");
	});

	it("draws it again with other props, rather than mounting it twice", async () => {
		const drawn = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		const registry = registryWith(GMAIL);
		const importer = async () => ({ default: { routes: { inbox: drawn.mount } } });

		const { rerender } = render(
			<PluginSlot
				point="routes"
				props={ROUTE}
				registry={registry}
				lookup="gmail"
				importer={importer}
			/>,
		);
		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());
		rerender(
			<PluginSlot
				point="routes"
				registry={registry}
				lookup="gmail"
				importer={importer}
				props={{ ...ROUTE, search: { q: "unread" } }}
			/>,
		);

		await waitFor(() => expect(drawn.updates).toHaveLength(1));
		expect(drawn.mount).toHaveBeenCalledTimes(1);
		expect(drawn.updates[0]).toMatchObject({ search: { q: "unread" } });
	});

	it("unmounts the contribution when the slot goes away", async () => {
		const drawn = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });

		const { unmount } = render(
			<PluginSlot
				point="routes"
				registry={registryWith(GMAIL)}
				lookup="gmail"
				importer={async () => ({ default: { routes: { inbox: drawn.mount } } })}
				props={ROUTE}
			/>,
		);
		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());

		unmount();

		expect(drawn.unmounted).toBe(1);
	});

	it("shows an error card when the plugin throws, and the rest of the page carries on", async () => {
		const good = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		const registry = registryWith(GMAIL);
		registry.point("widgets").add("dashboard", {
			plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
			key: "dashboard",
			id: "boom",
			entry: "boom",
		});
		registry.point("widgets").add("dashboard", {
			plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
			key: "dashboard",
			id: "fine",
			entry: "fine",
		});
		const importer = async () => ({
			default: {
				widgets: {
					boom: () => {
						throw new Error("its own bug");
					},
					fine: good.mount,
				},
			},
		});

		render(
			<PluginSlot
				point="widgets"
				registry={registry}
				importer={importer}
				props={{
					slot: "dashboard",
					plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
				}}
			/>,
		);

		await waitFor(() => expect(good.mount).toHaveBeenCalled());
		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("its own bug"),
		);
		expect(screen.getByRole("alert").textContent).toContain("gmail");
	});

	it("shows an error card when the bundle will not load, and says so in the store", async () => {
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });

		render(
			<PluginSlot
				point="routes"
				registry={registryWith(GMAIL)}
				lookup="gmail"
				importer={async () => {
					throw new TypeError("Failed to fetch dynamically imported module");
				}}
				props={ROUTE}
			/>,
		);

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("Failed to fetch dynamically imported module"),
		);
		await waitFor(() => expect(plugins.getState().errors[0]).toMatchObject({ name: "gmail" }));
	});

	it("shows an error card when the bundle has no such contribution", async () => {
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });

		render(
			<PluginSlot
				point="routes"
				props={ROUTE}
				registry={registryWith(GMAIL)}
				lookup="gmail"
				importer={async () => ({ default: {} })}
			/>,
		);

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("inbox"),
		);
	});

	it("draws a plugin that asks for no isolation in the shell's own DOM", async () => {
		const drawn = recording();
		const plain: UIPluginInfo = { ...GMAIL, isolation: "none" };
		plugins.setState({ manifests: [plain], problems: [], errors: [] });

		const { container } = render(
			<PluginSlot
				point="routes"
				registry={registryWith(plain)}
				lookup="gmail"
				importer={async () => ({ default: { routes: { inbox: drawn.mount } } })}
				props={ROUTE}
			/>,
		);

		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());
		const host = container.querySelector(".udl-plugin-slot");
		expect(host?.shadowRoot).toBeNull();
	});

	it("draws nothing when no plugin contributes to the point", () => {
		plugins.setState({ manifests: [], problems: [], errors: [] });

		const { container } = render(
			<PluginSlot point="routes" props={ROUTE} registry={new Registry()} lookup="gmail" />,
		);

		expect(container.querySelector(".udl-plugin-slot")).toBeNull();
	});

	it("follows the theme the page switches to", async () => {
		const drawn = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });

		render(
			<PluginSlot
				point="routes"
				registry={registryWith(GMAIL)}
				lookup="gmail"
				importer={async () => ({ default: { routes: { inbox: drawn.mount } } })}
				props={ROUTE}
			/>,
		);
		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());

		act(() => {
			setTheme("dark");
		});

		await waitFor(() =>
			expect(drawn.drawn[0]?.target.closest(".udl-plugin")?.getAttribute("data-theme")).toBe(
				"dark",
			),
		);
	});

	it("says a contribution whose plugin is no longer listed", async () => {
		plugins.setState({ manifests: [], problems: [], errors: [] });

		render(
			<PluginSlot point="routes" props={ROUTE} registry={registryWith(GMAIL)} lookup="gmail" />,
		);

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("not in the page's list"),
		);
	});

	it("tries again when the error card's retry is clicked", async () => {
		const drawn = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		let attempts = 0;
		const mounting = (target: HTMLElement, props: unknown) => {
			attempts += 1;
			if (attempts === 1) {
				throw new Error("it threw while drawing");
			}
			return drawn.mount(target, props);
		};
		const importer = async () => ({ default: { routes: { inbox: mounting } } });

		render(
			<PluginSlot
				point="routes"
				props={ROUTE}
				registry={registryWith(GMAIL)}
				lookup="gmail"
				importer={importer}
			/>,
		);
		const card = await screen.findByRole("alert");
		fireEvent.click(within(card).getByRole("button", { name: "Try again" }));

		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());
		expect(screen.queryByRole("alert")).toBeNull();
	});

	it("draws a contribution whose bundle has it under another key", async () => {
		const drawn = recording();
		const registry = new Registry();
		registry.point("widgets").add("dashboard", {
			plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
			key: "dashboard",
			id: "unread",
			entry: "unread-widget",
		});
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });

		render(
			<PluginSlot
				point="widgets"
				registry={registry}
				props={{
					slot: "dashboard",
					plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
				}}
				importer={async () => ({ default: { widgets: { "unread-widget": drawn.mount } } })}
			/>,
		);

		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());
	});

	it("draws a settings panel, looked up by the plugin whose settings they are", async () => {
		const drawn = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		const registry = new Registry();
		registry.point("settings").add("gmail", {
			plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
			key: "gmail",
			entry: "gmail",
		});

		render(
			<PluginSlot
				point="settings"
				registry={registry}
				props={{ plugin: "gmail", info: {}, save: async () => {} }}
				importer={async () => ({ default: { settings: { gmail: drawn.mount } } })}
			/>,
		);

		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());
	});

	it("draws an account setup panel, by its integration", async () => {
		const drawn = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		const registry = new Registry();
		registry.point("accountSetup").add("gmail", {
			plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
			key: "gmail",
			entry: "gmail",
		});

		render(
			<PluginSlot
				point="accountSetup"
				registry={registry}
				props={{ integration: "gmail", done: () => {} }}
				importer={async () => ({ default: { accountSetup: { gmail: drawn.mount } } })}
			/>,
		);

		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());
	});

	it("draws an event view, by the event's type", async () => {
		const drawn = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		const registry = new Registry();
		registry.point("eventViews").add("gmail.message", {
			plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
			key: "gmail.message",
			entry: "gmail.message",
		});

		render(
			<PluginSlot
				point="eventViews"
				registry={registry}
				props={{ event: { type: "gmail.message" } }}
				importer={async () => ({ default: { eventViews: { "gmail.message": drawn.mount } } })}
			/>,
		);

		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());
	});

	it("draws a filter editor, by the integration it is asked for", async () => {
		const drawn = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		const registry = new Registry();
		registry.point("filterEditors").add("gmail", {
			plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
			key: "gmail",
			entry: "gmail",
		});

		render(
			<PluginSlot
				point="filterEditors"
				registry={registry}
				lookup="gmail"
				props={{ value: null, onChange: () => {}, eventTypes: [] }}
				importer={async () => ({ default: { filterEditors: { gmail: drawn.mount } } })}
			/>,
		);

		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());
	});

	it("draws an action editor, by the action kind it is asked for", async () => {
		const drawn = recording();
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		const registry = new Registry();
		registry.point("actionEditors").add("gmail.send", {
			plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
			key: "gmail.send",
			entry: "gmail.send",
		});

		render(
			<PluginSlot
				point="actionEditors"
				registry={registry}
				lookup="gmail.send"
				props={{ options: {}, onChange: () => {}, schema: {} }}
				importer={async () => ({ default: { actionEditors: { "gmail.send": drawn.mount } } })}
			/>,
		);

		await waitFor(() => expect(drawn.mount).toHaveBeenCalled());
	});

	it("leaves no wrapper behind when it goes before its bundle has loaded", async () => {
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		let finish: (module: unknown) => void = () => {};
		const importer = () =>
			new Promise<unknown>((resolve) => {
				finish = resolve;
			});
		const view = render(
			<PluginSlot
				point="routes"
				props={ROUTE}
				registry={registryWith(GMAIL)}
				lookup="gmail"
				importer={importer}
			/>,
		);
		const host = view.container.querySelector(".udl-plugin-slot");
		view.unmount();
		finish({ default: { routes: { inbox: recording().mount } } });

		await act(async () => {});
		// (Its wrapper was made, then dropped: nothing of it is left in the slot's own root.)
		expect(host?.shadowRoot?.childElementCount).toBe(0);
		expect(host?.shadowRoot?.querySelector(".udl-plugin")).toBeNull();
	});

	it("shows a card when a contribution throws while it updates", async () => {
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		const mount = () => ({
			update: () => {
				throw new Error("its update fails");
			},
			unmount: () => {},
		});
		// (One registry and one importer for both renders: a new one would be a fresh mount,
		// and it is the update that fails here.)
		const registry = registryWith(GMAIL);
		const importer = async () => ({ default: { routes: { inbox: mount } } });

		const view = render(
			<PluginSlot
				point="routes"
				props={ROUTE}
				registry={registry}
				lookup="gmail"
				importer={importer}
			/>,
		);
		await waitFor(() =>
			expect(view.container.querySelector(".udl-plugin-slot")?.shadowRoot?.innerHTML).toContain(
				"udl-plugin",
			),
		);
		view.rerender(
			<PluginSlot
				point="routes"
				props={{ ...ROUTE, search: { q: "1" } }}
				registry={registry}
				lookup="gmail"
				importer={importer}
			/>,
		);

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("its update fails"),
		);
	});

	it("says an error, and still goes, when a contribution throws while it unmounts", async () => {
		plugins.setState({ manifests: [GMAIL], problems: [], errors: [] });
		const mount = () => ({
			update: () => {},
			unmount: () => {
				throw new Error("its unmount fails");
			},
		});
		const view = render(
			<PluginSlot
				point="routes"
				props={ROUTE}
				registry={registryWith(GMAIL)}
				lookup="gmail"
				importer={async () => ({ default: { routes: { inbox: mount } } })}
			/>,
		);
		await waitFor(() =>
			expect(view.container.querySelector(".udl-plugin-slot")?.shadowRoot?.innerHTML).toContain(
				"udl-plugin",
			),
		);

		expect(() => view.unmount()).not.toThrow();
		expect(plugins.getState().errors).toEqual([{ name: "gmail", message: "its unmount fails" }]);
	});

	it("draws the shell's own contribution without importing anything", async () => {
		const own = recording();
		const registry = new Registry();
		registry.point("widgets").add("dashboard", {
			plugin: { name: "udl", version: "0.0.0", baseUrl: "/ui/" },
			key: "dashboard",
			id: "stats",
			entry: "stats",
			mount: own.mount,
		});
		const importer = vi.fn(async () => ({ default: {} }));

		render(
			<PluginSlot
				point="widgets"
				registry={registry}
				importer={importer}
				props={{
					slot: "dashboard",
					plugin: { name: "udl", version: "0.0.0", baseUrl: "/ui/" },
				}}
			/>,
		);

		await waitFor(() => expect(own.mount).toHaveBeenCalled());
		expect(importer).not.toHaveBeenCalled();
	});
});
