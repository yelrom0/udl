/**
 * A panel in a window of its own (29.12): `/ui/panel/<plugin>/<id>`. The window loads the shell
 * from scratch, so it has no plugins on its first draw: it waits for udl's list before it says a
 * panel is missing, and it is the panel and nothing else.
 */

import { render, screen } from "@testing-library/react";
import type { Mount, PanelProps, PluginIdentity } from "@udl/ui-sdk";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type Contribution, Registry } from "../../../plugins/registry";
import { panels } from "../../../stores/panels";
import { plugins, setPlugins, type UIPluginInfo } from "../../../stores/plugins";
import { startFakingMatchMedia, stopFakingMatchMedia } from "../../../test-setup";
import { PanelWindowView } from "./$id";

const ORCH: PluginIdentity = { name: "orch", version: "0.1.0", baseUrl: "/ui/plugins/orch/" };

/** What udl says the `orch` UI plugin is, as `/ui/plugins.json` answers it. */
const INFO: UIPluginInfo = {
	name: "orch",
	version: "0.1.0",
	base_url: "/ui/plugins/orch/",
	entry_url: "/ui/plugins/orch/index.js",
	styles: [],
	// (The shell's own DOM, so the test's queries reach what the panel draws: a shadow root is
	// the browser's, and Testing Library's queries do not go into one.)
	isolation: "none",
	contributes: { panels: [{ id: "chat", title: "Chat" }] },
	plugin: "orch",
};

/** A registry with the chat panel, and the props it last had. */
function withChat(): { registry: Registry; seen: PanelProps[] } {
	const seen: PanelProps[] = [];
	const mount: Mount<PanelProps> = (target, props) => {
		seen.push(props);
		target.innerHTML = "";
		const close = document.createElement("button");
		close.textContent = "Back to the page";
		close.onclick = () => props.close();
		target.append(close);
		return { update: (next) => seen.push(next), unmount: () => {} };
	};
	const registry = new Registry();
	const contribution: Contribution<PanelProps> = {
		plugin: ORCH,
		key: "orch/chat",
		id: "chat",
		entry: "chat",
		title: "Chat",
		mount,
	};
	registry.point("panels").add("orch/chat", contribution);
	return { registry, seen };
}

beforeEach(() => {
	startFakingMatchMedia();
	localStorage.clear();
	plugins.setState({ manifests: [], problems: [], errors: [], loaded: false });
	panels.setState({ open: [], width: 400 });
});

afterEach(() => {
	stopFakingMatchMedia();
	vi.restoreAllMocks();
});

describe("a panel's own window", () => {
	it("waits for the plugins' list rather than naming a panel missing", () => {
		// (A new window has no plugins on its first draw: saying "no such panel" then would be a
		// lie about a panel that is about to arrive.)
		render(<PanelWindowView plugin="orch" id="chat" registry={new Registry()} />);

		expect(screen.getByLabelText("Loading the panel")).toBeTruthy();
		expect(screen.queryByText(/No panel/)).toBeNull();
	});

	it("says a panel the plugin does not have, once the list has been read", () => {
		setPlugins([INFO], []);

		render(<PanelWindowView plugin="orch" id="none" registry={new Registry()} />);

		expect(screen.getByText('No panel "none"')).toBeTruthy();
	});

	it("says a plugin the page does not have, once the list has been read", () => {
		setPlugins([INFO], []);

		render(<PanelWindowView plugin="gmail" id="chat" registry={new Registry()} />);

		expect(screen.getByText('No plugin "gmail"')).toBeTruthy();
	});

	it("draws the panel, and brings the page back when it is closed", async () => {
		setPlugins([INFO], []);
		const focus = vi.fn();
		Object.defineProperty(window, "opener", { value: { focus }, configurable: true });
		const close = vi.spyOn(window, "close").mockImplementation(() => {});
		const { registry } = withChat();

		render(<PanelWindowView plugin="orch" id="chat" registry={registry} />);
		(await screen.findByText("Back to the page")).click();

		expect(focus).toHaveBeenCalled();
		expect(close).toHaveBeenCalled();
	});
});
