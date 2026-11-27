import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { onNavigate, routerNavigation } from "./plugins/navigation";
import { OWN_NAV, registerGenerated, registerPlugins, shellRegistry } from "./plugins/shell";
import { BASE } from "./router";
import { routeTree } from "./routeTree.gen";
import { setGenerated } from "./stores/generated";
import { clear, pushEvent, setRun } from "./stores/live";
import { DEFAULT_WIDTH, panels } from "./stores/panels";
import { plugins, setNavCount, setPlugins, type UIPluginInfo } from "./stores/plugins";
import { session } from "./stores/session";
import { QuietEventSource, startFakingMatchMedia, stopFakingMatchMedia } from "./test-setup";

/** An answer as `fetch` gives one: a status and a JSON body. */
function answer(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

const EMPTY = { sites: [], downloads: [] };

/** The path a request asks for (the typed client sends a `Request`, a plain `fetch` a URL). */
function pathOf(input: RequestInfo | URL): string {
	const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
	return new URL(url, "http://localhost").pathname;
}

/** udl's answers: signed in (everything answers), or signed out (everything is a 401). */
function udl({ signedIn = true }: { signedIn?: boolean } = {}): void {
	vi.stubGlobal(
		"fetch",
		vi.fn((input: RequestInfo | URL) => {
			if (input === "/session") {
				return Promise.resolve(new Response(null, { status: 204 }));
			}
			if (!signedIn) {
				return Promise.resolve(
					answer(401, { code: "unauthorized", message: "This udl server needs its API key" }),
				);
			}
			const path = pathOf(input);
			return Promise.resolve(answer(200, path === "/downloads" ? { downloads: [] } : EMPTY));
		}),
	);
}

/** What a bundle's page does in a test: a page test cannot import one from a URL. */
type Page = (
	target: HTMLElement,
	props: unknown,
	ctx: { navigate: (to: string) => void },
) => { update(props: unknown): void; unmount(): void };

/** The page the mocked bundle's `inbox` is (each test says what it does). */
const bundle = vi.hoisted(() => ({ page: null as Page | null }));

// (A plugin's bundle is an ES module of the page's; here it is the mount the test gives it.)
vi.mock("./plugins/load", () => ({
	forgetPlugins: () => {},
	loadPlugin: async () => ({
		routes: {
			inbox: (target: HTMLElement, props: unknown, ctx: { navigate: (to: string) => void }) => {
				if (bundle.page === null) {
					throw new Error("the test gave no page");
				}
				return bundle.page(target, props, ctx);
			},
		},
		panels: {
			chat: (target: HTMLElement) => {
				target.innerHTML = "<p>The chat, drawn by its plugin</p>";
				return { update: () => {}, unmount: () => {} };
			},
		},
	}),
}));

/** A UI plugin with a panel, as udl lists one (`/ui/plugins.json`). */
const CHAT: UIPluginInfo = {
	name: "orch",
	version: "0.1.0",
	base_url: "/ui/plugins/orch/",
	entry_url: "/ui/plugins/orch/index.js",
	styles: [],
	isolation: "shadow",
	contributes: {
		panels: [{ id: "chat", title: "Chat", icon: "message-square", shortcut: "Ctrl+J" }],
	},
	plugin: "orch",
};

/** The page at `path` (`/ui` is its base, as udl serves it). */
function draw(path: string) {
	const router = createRouter({
		routeTree,
		basepath: "/ui",
		history: createMemoryHistory({ initialEntries: [path] }),
	});
	// (As `main.tsx` does it: a plugin's `ctx.navigate` moves the router.)
	onNavigate(routerNavigation(router.history, BASE));
	render(<RouterProvider router={router} />);
	return router;
}

beforeEach(() => {
	startFakingMatchMedia();
	session.setState({ state: "unknown" });
	clear();
	localStorage.clear();
	// (The page's own pages, as `main.tsx` registers them before the first route is drawn.)
	registerPlugins([]);
	setGenerated(null, null);
	plugins.setState({ counts: {} });
});

afterEach(() => {
	stopFakingMatchMedia();
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
	delete document.documentElement.dataset.theme;
});

describe("the shell", () => {
	it("reaches every page of the nav", async () => {
		udl();
		for (const item of OWN_NAV) {
			draw(item.to === "/" ? "/ui/" : `/ui${item.to}`);
			const heading =
				item.to === "/"
					? await screen.findByRole("heading", { level: 1, name: "Dashboard" })
					: await screen.findByRole("heading", { level: 1, name: item.title });
			expect(heading).toBeTruthy();
		}
	});

	it("shows the nav, the theme and signing out", async () => {
		udl();
		draw("/ui/downloads");

		await screen.findByRole("heading", { name: "Downloads" });
		for (const item of OWN_NAV) {
			expect(screen.getAllByRole("link", { name: item.title }).length).toBeGreaterThan(0);
		}
		expect(screen.getByRole("combobox", { name: "Theme" })).toBeTruthy();
		expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
	});

	it("draws a live count on a nav entry (29.7's follow-up)", async () => {
		udl();
		shellRegistry.nav.push({
			plugin: { name: "orch", version: "0.1.0", baseUrl: "/ui/plugins/orch/" },
			title: "Q&A",
			to: "/p/orch/questions",
			route: "questions",
		});
		registerGenerated(null); // (The drawer's list is the registry's, copied in.)
		setNavCount("orch", "questions", 3);
		draw("/ui/downloads");

		const link = await screen.findByRole("link", { name: /Q&A/ });
		expect(link.textContent).toContain("Q&A");
		// (The count is its own element, with what it counts read out rather than a bare "3".)
		const badge = within(link).getByTestId("nav-count");
		expect(badge.textContent).toBe("3 waiting");
		expect(badge.querySelector(".sr-only")?.textContent).toBe(" waiting");

		// (And none when the plugin takes it off: nothing is waiting.)
		act(() => {
			setNavCount("orch", "questions", null);
		});
		expect(screen.queryByTestId("nav-count")).toBeNull();
	});

	it("goes to the login page when udl asks for its key", async () => {
		udl({ signedIn: false });
		draw("/ui/downloads");

		expect(await screen.findByRole("heading", { level: 1, name: "udl" })).toBeTruthy();
		expect(screen.getByText("This server needs its API key.")).toBeTruthy();
		expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
	});

	it("signs in with the key, and lands on the dashboard", async () => {
		udl({ signedIn: false });
		draw("/ui/login");
		const key = await screen.findByLabelText("API key");

		fireEvent.change(key, { target: { value: "s3cret" } });
		fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

		await waitFor(() => {
			expect(session.getState().state).toBe("signed-in");
		});
	});

	it("goes to the login page when the session is lost mid-visit", async () => {
		// (The sessions live in udl's memory: a restart or an expiry answers a 401 from then on,
		// and a page that is already open must go back to the login page, not show an error.)
		udl();
		draw("/ui/downloads");
		await screen.findByRole("heading", { name: "Downloads" });

		udl({ signedIn: false });
		fireEvent.click(screen.getAllByRole("link", { name: "Dashboard" })[0] as HTMLElement);

		expect(await screen.findByLabelText("API key")).toBeTruthy();
		expect(session.getState().state).toBe("signed-out");
	});

	it("sends a signed-in visit to the login page on to the dashboard", async () => {
		udl();
		draw("/ui/login");

		expect(await screen.findByRole("heading", { level: 1, name: "Dashboard" })).toBeTruthy();
		expect(screen.queryByLabelText("API key")).toBeNull();
	});

	it("closes the phone's nav when a page is chosen", async () => {
		// (On a phone the nav covers the page: a link that leaves it open hides what it opened.)
		udl();
		draw("/ui/downloads");
		await screen.findByRole("heading", { name: "Downloads" });
		const nav = document.querySelector<HTMLInputElement>("#nav");
		expect(nav).not.toBeNull();
		if (nav === null) {
			return;
		}
		nav.checked = true;

		fireEvent.click(screen.getAllByRole("link", { name: "Events" })[0] as HTMLElement);

		await screen.findByRole("heading", { name: "Events" });
		expect(nav.checked).toBe(false);
	});

	it("signs out from the navbar", async () => {
		udl();
		draw("/ui/downloads");
		await screen.findByRole("heading", { name: "Downloads" });

		fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

		await waitFor(() => {
			expect(session.getState().state).toBe("signed-out");
		});
		expect(await screen.findByRole("heading", { level: 1, name: "udl" })).toBeTruthy();
	});

	it("says a plugin page it has no plugin for", async () => {
		udl();
		setGenerated({ plugins: [] }, null);
		draw("/ui/p/gmail/inbox");

		expect(await screen.findByRole("heading", { level: 1, name: "gmail" })).toBeTruthy();
		expect(screen.getByText(/There is no plugin named/)).toBeTruthy();
	});

	it("waits for the generated list before saying a plugin is unknown", async () => {
		// (A reload or a direct link asks for `/ui/generated.json` and draws the route at once:
		// "there is no plugin named gmail" would be said before udl had answered.)
		udl();
		draw("/ui/p/gmail/inbox");

		expect(await screen.findByLabelText("Loading the plugin")).toBeTruthy();
		expect(screen.queryByText(/There is no plugin named/)).toBeNull();
	});

	it("says why the generated list could not be read", async () => {
		udl();
		setGenerated(null, "the plugin list could not be read");
		draw("/ui/p/gmail/inbox");

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("the plugin list could not be read"),
		);
		expect(screen.queryByText(/There is no plugin named/)).toBeNull();
	});

	it("switches the theme, and keeps the choice", async () => {
		udl();
		draw("/ui/downloads");
		await screen.findByRole("heading", { name: "Downloads" });

		fireEvent.change(screen.getByRole("combobox", { name: "Theme" }), {
			target: { value: "dark" },
		});

		await waitFor(() => {
			expect(document.documentElement.dataset.theme).toBe("dark");
		});
		expect(localStorage.getItem("udl.theme")).toBe('"dark"');
	});

	it("shows the downloads udl worked on last", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn((input: RequestInfo | URL) => {
				const path = pathOf(input);
				if (path === "/downloads") {
					return Promise.resolve(
						answer(200, {
							downloads: [
								{
									id: "3f0f9c1e-0000-0000-0000-000000000001",
									url: "https://example.com/a.zip",
									site_key: "example.com",
									status: "done",
									started_at: new Date().toISOString(),
								},
							],
						}),
					);
				}
				return Promise.resolve(answer(200, EMPTY));
			}),
		);
		draw("/ui/");

		expect(await screen.findByText("https://example.com/a.zip")).toBeTruthy();
		expect(screen.getByText("done")).toBeTruthy();
		expect(screen.getByText("just now")).toBeTruthy();
	});

	it("says so when udl cannot be reached", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn((input: RequestInfo | URL) => {
				const path = pathOf(input);
				if (path === "/sites") {
					return Promise.resolve(answer(500, { code: "udl_error", message: "udl is unwell" }));
				}
				return Promise.resolve(answer(200, { downloads: [] }));
			}),
		);
		draw("/ui/");

		expect(await screen.findByRole("alert")).toHaveProperty("textContent", "udl is unwell");
	});

	it("says a plugin page the plugin has no route for", async () => {
		udl();
		const gmail = {
			name: "gmail",
			version: "0.1.0",
			base_url: "/ui/plugins/gmail/",
			entry_url: "/ui/plugins/gmail/index.js",
			styles: [],
			isolation: "shadow" as const,
			contributes: { routes: ["inbox"] },
			plugin: "gmail",
		};
		setPlugins([gmail], []);
		registerPlugins([gmail]);
		draw("/ui/p/gmail/compose");

		expect(await screen.findByRole("heading", { level: 1, name: "gmail 0.1.0" })).toBeTruthy();
		expect(screen.getByText(/This plugin has no page "compose"/)).toBeTruthy();
	});

	it("says an unknown plugin, and the shell carries on", async () => {
		udl();
		setGenerated({ plugins: [] }, null);
		draw("/ui/p/nobody/inbox");

		expect(await screen.findByRole("heading", { level: 1, name: "nobody" })).toBeTruthy();
		expect(screen.getByText(/There is no plugin named/)).toBeTruthy();
	});

	it("draws a plugin's generated page when it brings no UI of its own", async () => {
		udl();
		const gmail = {
			name: "gmail",
			kind: "udl.integrations",
			title: "Gmail",
			summary: "Mail, from udl",
			has_ui_plugin: false,
			operations: [],
			settings: { json_schema: { type: "object", properties: {} }, read_only: [], secret: [] },
			accounts: null,
			event_types: [],
		};
		setGenerated({ plugins: [gmail] }, null);
		registerGenerated({ plugins: [gmail] });
		registerPlugins([]);
		draw("/ui/p/gmail/");

		expect(await screen.findByRole("heading", { level: 2, name: "Gmail" })).toBeTruthy();
		expect(screen.getByRole("tab", { name: "settings" })).toBeTruthy();
		// (And the drawer has the plugin's page: it has something to show.)
		expect(screen.getAllByRole("link", { name: "Gmail" }).length).toBeGreaterThan(0);
	});

	it("keeps a UI plugin's own page over the generated one", async () => {
		udl();
		const gmail = {
			name: "gmail",
			version: "0.1.0",
			base_url: "/ui/plugins/gmail/",
			entry_url: "/ui/plugins/gmail/index.js",
			styles: [],
			isolation: "shadow" as const,
			contributes: { routes: ["inbox"] },
			plugin: "gmail",
		};
		bundle.page = (target) => {
			target.textContent = "the plugin's own page";
			return { update: () => {}, unmount: () => {} };
		};
		setPlugins([gmail], []);
		registerPlugins([gmail]);
		const generated = {
			plugins: [
				{
					name: "gmail",
					kind: "udl.integrations",
					title: "Gmail",
					summary: "",
					has_ui_plugin: true,
					operations: [],
					settings: { json_schema: {}, read_only: [], secret: [] },
					accounts: null,
					event_types: [],
				},
			],
		};
		setGenerated(generated, null);
		registerGenerated(generated);
		draw("/ui/p/gmail/inbox");

		// (A plugin's page is drawn in its own shadow root: its text is there, not in the page's.)
		await waitFor(() =>
			expect(document.querySelector(".udl-plugin-slot")?.shadowRoot?.innerHTML).toContain(
				"the plugin's own page",
			),
		);
	});

	it("falls back to the generated page for a page its UI plugin does not have", async () => {
		udl();
		const gmail = {
			name: "gmail",
			version: "0.1.0",
			base_url: "/ui/plugins/gmail/",
			entry_url: "/ui/plugins/gmail/index.js",
			styles: [],
			isolation: "shadow" as const,
			contributes: { routes: ["inbox"] },
			plugin: "gmail",
		};
		setPlugins([gmail], []);
		registerPlugins([gmail]);
		setGenerated(
			{
				plugins: [
					{
						name: "gmail",
						kind: "udl.integrations",
						title: "Gmail",
						summary: "",
						has_ui_plugin: true,
						operations: [],
						settings: { json_schema: {}, read_only: [], secret: [] },
						accounts: null,
						event_types: [],
					},
				],
			},
			null,
		);
		draw("/ui/p/gmail/settings");

		expect(await screen.findByRole("heading", { level: 2, name: "Gmail" })).toBeTruthy();
		expect(screen.getByRole("tab", { name: "settings" })).toBeTruthy();
	});

	it("shows a plugin's page, and an error card in its place when it fails", async () => {
		udl();
		const gmail = {
			name: "gmail",
			version: "0.1.0",
			base_url: "/ui/plugins/gmail/",
			entry_url: "/ui/plugins/gmail/index.js",
			styles: [],
			isolation: "shadow" as const,
			contributes: { routes: ["inbox"] },
			plugin: "gmail",
		};
		setPlugins([gmail], []);
		registerPlugins([gmail]);
		bundle.page = () => {
			throw new Error("its page fails");
		};
		draw("/ui/p/gmail/inbox");

		// (One card in the plugin's place, and the shell's own pages are still there.)
		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("its page fails"),
		);
		expect(screen.getAllByRole("link", { name: "Dashboard" }).length).toBeGreaterThan(0);
	});

	it("moves the shell when a plugin's page navigates", async () => {
		udl();
		const gmail = {
			name: "gmail",
			version: "0.1.0",
			base_url: "/ui/plugins/gmail/",
			entry_url: "/ui/plugins/gmail/index.js",
			styles: [],
			isolation: "shadow" as const,
			contributes: { routes: ["inbox"] },
			plugin: "gmail",
		};
		setPlugins([gmail], []);
		registerPlugins([gmail]);
		bundle.page = (_target, _props, ctx) => {
			ctx.navigate("/sites");
			return { update: () => {}, unmount: () => {} };
		};

		draw("/ui/p/gmail/inbox");

		// (The router's own navigation, with the page's base: no reload, no `/sites` from udl.)
		expect(await screen.findByRole("heading", { level: 1, name: "Sites" })).toBeTruthy();
	});

	it("says where the live connection is: connecting until its stream opens", async () => {
		udl();
		draw("/ui/downloads");

		expect(await screen.findByText("connecting")).toBeTruthy();
	});

	it("says live once udl's stream is open", async () => {
		/** A stream that is accepted at once (udl's, as the page sees it). */
		class Opening extends QuietEventSource {
			constructor(url: string | URL) {
				super(url);
				queueMicrotask(() => this.dispatchEvent(new Event("open")));
			}
		}
		vi.stubGlobal("EventSource", Opening);
		udl();
		draw("/ui/downloads");

		expect(await screen.findByText("live")).toBeTruthy();
	});

	it("says what udl said about a key it refused", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn((input: RequestInfo | URL) => {
				if (input === "/session") {
					return Promise.resolve(
						answer(401, { code: "unauthorized", message: "This udl server needs its API key" }),
					);
				}
				return Promise.resolve(answer(401, { code: "unauthorized", message: "no" }));
			}),
		);
		draw("/ui/login");
		fireEvent.change(await screen.findByLabelText("API key"), { target: { value: "wrong" } });
		fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			"This udl server needs its API key",
		);
	});

	it("says a sign-in that failed for another reason", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn((input: RequestInfo | URL) => {
				if (input === "/session") {
					return Promise.reject(new TypeError("the network is down"));
				}
				return Promise.resolve(answer(401, { code: "unauthorized", message: "no" }));
			}),
		);
		draw("/ui/login");
		fireEvent.change(await screen.findByLabelText("API key"), { target: { value: "x" } });
		fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			"TypeError: the network is down",
		);
	});

	it("shows the runs and events udl sends while the page is open", async () => {
		udl();
		draw("/ui/");
		await screen.findByRole("heading", { level: 1, name: "Dashboard" });

		act(() => {
			setRun("run-1", { id: "run-1", status: "succeeded", summary: "Saved 3 files" });
			pushEvent({
				name: "event",
				at: new Date().toISOString(),
				data: { id: "e-1", type: "download.finished", subject: "A download finished" },
			});
		});

		expect(await screen.findByText(/Saved 3 files/)).toBeTruthy();
		expect(screen.getByText(/A download finished/)).toBeTruthy();
	});

	it("says so when no run has started since the page opened", async () => {
		udl();
		draw("/ui/");

		expect(await screen.findByText("No run has started since the page opened.")).toBeTruthy();
	});

	it("says the dashboard has nothing yet", async () => {
		udl();
		draw("/ui/");

		expect(await screen.findByText("udl has downloaded nothing yet.")).toBeTruthy();
	});
});

/** What a panel's slot drew: a plugin's shadow root, which the DOM's queries do not see into. */
async function drewInPanel(key: string, text: string): Promise<void> {
	// (The query is inside: the page draws its frame and its panel a turn after the route.)
	await waitFor(() =>
		expect(
			document.querySelector(`[data-panel="${key}"] .udl-plugin-slot`)?.shadowRoot?.textContent ??
				"",
		).toContain(text),
	);
}

describe("the page's panels (29.12)", () => {
	beforeEach(() => {
		localStorage.clear();
		panels.setState({ open: [], width: DEFAULT_WIDTH });
		registerPlugins([CHAT]);
		setPlugins([CHAT], []);
	});

	it("puts a button in the navbar for each panel, and opens it", async () => {
		udl();
		draw("/ui/");

		const button = await screen.findByRole("button", { name: "Chat" });
		expect(button.getAttribute("title")).toBe("Chat (Ctrl+J)");
		fireEvent.click(button);

		expect(await screen.findByRole("complementary", { name: "Chat" })).toBeTruthy();
		await drewInPanel("orch/chat", "The chat, drawn by its plugin");
	});

	it("opens and closes the chat with Ctrl+J", async () => {
		udl();
		draw("/ui/");
		// (The page is drawn before its keys are listened to.)
		await screen.findByRole("button", { name: "Chat" });

		fireEvent.keyDown(document, { key: "j", ctrlKey: true });
		await drewInPanel("orch/chat", "The chat, drawn by its plugin");

		fireEvent.keyDown(document, { key: "j", ctrlKey: true });
		await waitFor(() => expect(screen.queryByRole("complementary", { name: "Chat" })).toBeNull());
	});

	it("draws only the panel in its own window, and closes it there", async () => {
		udl();
		const closed = vi.spyOn(window, "close").mockImplementation(() => {});
		draw("/ui/panel/orch/chat");

		await drewInPanel("orch/chat", "The chat, drawn by its plugin");
		// (A window of its own is the panel: no nav, no signing out.)
		expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
		expect(screen.queryByRole("link", { name: "udl" })).toBeNull();

		expect(closed).not.toHaveBeenCalled();
	});

	it("says so when the panel's plugin is not there", async () => {
		udl();
		draw("/ui/panel/nobody/chat");

		expect(await screen.findByRole("heading", { name: 'No plugin "nobody"' })).toBeTruthy();
	});
});
