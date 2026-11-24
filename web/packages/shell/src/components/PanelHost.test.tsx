/**
 * The panel host (29.12): the navbar's buttons for the page's panels, and the panel itself —
 * drawn at the right of the page while it is open, with the width this browser keeps. A panel
 * that is popped out is a window of its own (`/ui/panel/<plugin>/<id>`), and the page's copy
 * closes.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mount, PanelProps, PluginIdentity } from "@udl/ui-sdk";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type Contribution, Registry } from "../plugins/registry";
import { DEFAULT_WIDTH, isOpen, openPanel, panels } from "../stores/panels";
import { startFakingMatchMedia, stopFakingMatchMedia } from "../test-setup";
import { PanelButtons, PanelHost } from "./PanelHost";

const ORCH: PluginIdentity = { name: "orch", version: "0.1.0", baseUrl: "/ui/plugins/orch/" };
const HELP: PluginIdentity = { name: "help", version: "1.0.0", baseUrl: "/ui/plugins/help/" };

/** What a test's panel draws: a close button, a pop-out button, and the props it last had. */
function panel(): { mount: Mount<PanelProps>; seen: PanelProps[] } {
	const seen: PanelProps[] = [];
	const mount: Mount<PanelProps> = (target, props) => {
		seen.push(props);
		target.innerHTML = "";
		const close = document.createElement("button");
		close.textContent = "Close the chat";
		close.onclick = () => props.close();
		const popOut = document.createElement("button");
		popOut.textContent = "Pop out the chat";
		popOut.onclick = () => props.popOut();
		target.append(close, popOut);
		return { update: (next) => seen.push(next), unmount: () => {} };
	};
	return { mount, seen };
}

/** A registry with one panel, and the mount it draws. */
function registryWith(
	key: string,
	plugin: PluginIdentity,
	title: string,
	shortcut?: string,
): { registry: Registry; seen: PanelProps[] } {
	const { mount, seen } = panel();
	const registry = new Registry();
	const contribution: Contribution<PanelProps> = {
		plugin,
		key,
		id: key.split("/")[1] ?? key,
		entry: key.split("/")[1] ?? key,
		title,
		...(shortcut === undefined ? {} : { shortcut }),
		mount,
	};
	registry.point("panels").add(key, contribution);
	return { registry, seen };
}

beforeEach(() => {
	startFakingMatchMedia();
	localStorage.clear();
	panels.setState({ open: [], width: DEFAULT_WIDTH });
	vi.restoreAllMocks();
});

afterEach(() => {
	stopFakingMatchMedia();
});

describe("the panel host", () => {
	it("draws nothing while no panel is open", () => {
		const { registry } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");

		render(<PanelHost registry={registry} />);

		expect(screen.queryByRole("complementary")).toBeNull();
	});

	it("draws an open panel, open", async () => {
		const { registry, seen } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");
		openPanel("orch/chat");

		render(<PanelHost registry={registry} />);

		expect(screen.getByRole("complementary", { name: "Chat" })).toBeTruthy();
		expect(await screen.findByText("Close the chat")).toBeTruthy();
		expect(seen[0]?.open).toBe(true);
	});

	it("closes the panel from the panel's own close()", async () => {
		const { registry } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");
		openPanel("orch/chat");
		render(<PanelHost registry={registry} />);

		fireEvent.click(await screen.findByText("Close the chat"));

		expect(isOpen("orch/chat")).toBe(false);
		expect(screen.queryByRole("complementary")).toBeNull();
	});

	it("pops a panel out into a window of its own, and closes the page's copy", async () => {
		const opened = vi.spyOn(window, "open").mockImplementation(() => null);
		const { registry } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");
		openPanel("orch/chat");
		render(<PanelHost registry={registry} />);

		fireEvent.click(await screen.findByText("Pop out the chat"));

		expect(opened).toHaveBeenCalledWith(
			"/ui/panel/orch/chat",
			"udl-panel-orch-chat",
			expect.stringContaining("popup"),
		);
		expect(isOpen("orch/chat")).toBe(false);
	});

	it("draws every open panel, in the order they were opened", () => {
		const { registry } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");
		const help = registryWith("help/tips", HELP, "Tips");
		for (const one of help.registry.point("panels").every()) {
			registry.point("panels").add(one.key, one);
		}
		openPanel("orch/chat");
		openPanel("help/tips");

		render(<PanelHost registry={registry} />);

		expect(
			screen.getAllByRole("complementary").map((one) => one.getAttribute("aria-label")),
		).toEqual(["Chat", "Tips"]);
	});

	it("draws nothing for a panel whose plugin is gone", () => {
		openPanel("orch/chat");

		render(<PanelHost registry={new Registry()} />);

		expect(screen.queryByRole("complementary")).toBeNull();
	});

	it("opens and closes a panel with its own key", async () => {
		const { registry } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");
		render(<PanelHost registry={registry} />);

		fireEvent.keyDown(document, { key: "j", ctrlKey: true });
		await waitFor(() => expect(isOpen("orch/chat")).toBe(true));

		fireEvent.keyDown(document, { key: "J", ctrlKey: true });
		await waitFor(() => expect(isOpen("orch/chat")).toBe(false));
	});

	it("leaves a key no panel asks for alone", () => {
		const { registry } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");
		render(<PanelHost registry={registry} />);

		fireEvent.keyDown(document, { key: "k", ctrlKey: true });

		expect(isOpen("orch/chat")).toBe(false);
	});

	it("keeps the width it is dragged to, between its bounds", () => {
		const { registry } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");
		openPanel("orch/chat");
		render(<PanelHost registry={registry} />);

		const handle = screen.getByRole("separator", { name: "Resize the panel" });
		fireEvent.pointerDown(handle, { clientX: 1_000 });
		fireEvent.pointerMove(document, { clientX: 940 });
		fireEvent.pointerUp(document);

		expect(panels.getState().width).toBe(DEFAULT_WIDTH + 60);
	});
});

describe("the navbar's panel buttons", () => {
	it("draws one button per panel, with what the manifest says", () => {
		const { registry } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");

		render(<PanelButtons registry={registry} />);

		const button = screen.getByRole("button", { name: "Chat" });
		expect(button.getAttribute("title")).toBe("Chat (Ctrl+J)");
		expect(button.getAttribute("aria-pressed")).toBe("false");
	});

	it("opens a panel, and says it is open", async () => {
		const { registry } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");
		render(<PanelButtons registry={registry} />);

		fireEvent.click(screen.getByRole("button", { name: "Chat" }));

		await waitFor(() => expect(isOpen("orch/chat")).toBe(true));
		expect(screen.getByRole("button", { name: "Chat" }).getAttribute("aria-pressed")).toBe("true");
	});

	it("closes it again", async () => {
		const { registry } = registryWith("orch/chat", ORCH, "Chat", "Ctrl+J");
		openPanel("orch/chat");
		render(<PanelButtons registry={registry} />);

		fireEvent.click(screen.getByRole("button", { name: "Chat" }));

		await waitFor(() => expect(isOpen("orch/chat")).toBe(false));
	});

	it("draws no button when the page has no panels", () => {
		render(<PanelButtons registry={new Registry()} />);

		expect(screen.queryByRole("button", { name: "Chat" })).toBeNull();
	});
});
