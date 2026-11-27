/**
 * ----------------
 * - machines.test.tsx -
 * ----------------
 *
 * 29.9's Machines view (`operationViews` for `orch_list_runners`): a card per host, each harness's
 * version and whether it is a version behind, who it is signed in as and on what plan, what is
 * wrong with it, and the buttons that change it — install or update, set up, sign in through
 * 25.11's relay (the link, a box for the code, and how it went).
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { JsonValue } from "@udl/ui-sdk";
import { describe, expect, it, vi } from "vitest";
import { fakeCtx } from "../testing/fake";
import { Machines } from "./machines";

/** The list `orch_list_runners` answers, and what the tests change it to. */
const answer: JsonValue = {
	items: [
		{
			host: "local",
			runner: "claude-code",
			health: {
				installed: true,
				version: "3.2.1",
				latest: "3.2.1",
				signed_in: true,
				plan: "Max",
				guards: true,
			},
		},
		{
			host: "local",
			runner: "opencode",
			health: { installed: true, version: "2.1.2", latest: "2.1.2", signed_in: false },
		},
		{
			host: "thevault",
			runner: "claude-code",
			health: {
				installed: true,
				version: "3.1.9",
				latest: "3.2.1",
				signed_in: true,
				plan: "Pro",
				account: "someone@example.com",
				limited_until: "2026-11-28T12:00:00Z",
				problems: ["the plan is at its limit"],
			},
		},
	],
	problems: ["thevault/opencode: it did not answer"],
};

/** The operation the view stands for, as the shell hands it over. */
const operation = {
	name: "orch_list_runners",
	title: "List the harnesses",
	summary: "Every harness on every host.",
	view: "list" as const,
	hints: { read_only: true },
};

describe("the machines view (29.9)", () => {
	it("draws a card per host, with each harness's version and who it is signed in as", async () => {
		const fake = fakeCtx(() => answer);
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => answer} />);

		expect(await screen.findByRole("heading", { name: "local" })).toBeTruthy();
		expect(screen.getByRole("heading", { name: "thevault" })).toBeTruthy();
		expect(screen.getAllByText("3.2.1").length).toBeGreaterThan(0);
		expect(screen.getByText("signed in (Max plan)")).toBeTruthy();
		expect(screen.getByText("not signed in")).toBeTruthy();
		// (Who it is signed in as, and whether the deny policies are in place.)
		expect(screen.getByText("as someone@example.com")).toBeTruthy();
		expect(screen.getAllByText("no guards").length).toBe(2);
	});

	it("says a harness is a version behind, and updates it", async () => {
		const fake = fakeCtx((name) =>
			name === "orch_runner_install" ? { host: "thevault", runner: "claude-code" } : answer,
		);
		const run = vi.fn(async () => answer);
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={run} />);

		expect(await screen.findByText("update available")).toBeTruthy();
		fireEvent.click(screen.getByRole("button", { name: "Update" }));

		await waitFor(() =>
			expect(fake.calls).toContainEqual({
				name: "orch_runner_install",
				request: { host: "thevault", runner: "claude-code" },
			}),
		);
		// (The list is read again, so the card says what it is now.)
		await waitFor(() => expect(run).toHaveBeenCalledTimes(2));
	});

	it("sets a harness up from its card", async () => {
		const fake = fakeCtx(() => answer);
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => answer} />);

		fireEvent.click((await screen.findAllByRole("button", { name: "Set up" }))[0] as HTMLElement);

		await waitFor(() =>
			expect(fake.calls).toContainEqual({
				name: "orch_runner_setup",
				request: { host: "local", runner: "claude-code" },
			}),
		);
	});

	it("signs a harness in through the relay: the link, the code, and how it went", async () => {
		let replied = false;
		const attempt = {
			id: "login-1",
			host: "local",
			runner: "opencode",
			prompt: "Open https://example.com/device and enter the code",
			expects: "code",
			done: false,
			ok: null,
		};
		const fake = fakeCtx((name) => {
			if (name === "orch_runner_login") {
				return attempt;
			}
			if (name === "orch_runner_login_reply") {
				replied = true;
				return attempt;
			}
			if (name === "orch_runner_login_status") {
				// (It finishes once the code is in, as a device sign-in does.)
				return { attempt: { ...attempt, done: replied, ok: replied ? true : null } };
			}
			return answer;
		});
		const run = vi.fn(async () => answer);
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={run} />);

		fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));

		// (The harness's own words, and its link as a button: nothing to copy out by hand.)
		expect(await screen.findByText(/Open https:\/\/example.com\/device/)).toBeTruthy();
		const link = screen.getByRole("link", { name: "Open the link" });
		expect(link.getAttribute("href")).toBe("https://example.com/device");
		expect(link.getAttribute("rel")).toBe("noopener noreferrer");
		const code = await screen.findByLabelText("Code");
		fireEvent.change(code, { target: { value: "ABCD-1234" } });
		fireEvent.click(screen.getByRole("button", { name: "Send" }));

		await waitFor(() =>
			expect(fake.calls).toContainEqual({
				name: "orch_runner_login_reply",
				request: { id: "login-1", text: "ABCD-1234" },
			}),
		);
		// (Checked after the reply, then the list is read again: it says signed in now.)
		await waitFor(() =>
			expect(fake.calls.some((call) => call.name === "orch_runner_login_status")).toBe(true),
		);
		await waitFor(() => expect(run).toHaveBeenCalledTimes(2));
	});

	it("draws what udl could not ask, rather than hiding it", async () => {
		const fake = fakeCtx(() => answer);
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => answer} />);

		expect(await screen.findByText("thevault/opencode: it did not answer")).toBeTruthy();
	});

	it("reads the list again when asked to", async () => {
		const fake = fakeCtx(() => answer);
		const run = vi.fn(async () => answer);
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={run} />);

		await screen.findByRole("heading", { name: "local" });
		fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

		await waitFor(() => expect(run).toHaveBeenCalledTimes(2));
	});

	it("installs a harness that is not there yet", async () => {
		const missing = {
			items: [
				{
					host: "thevault",
					runner: "opencode",
					health: { installed: false, signed_in: false },
				},
			],
			problems: [],
		};
		const fake = fakeCtx(() => missing);
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => missing} />);

		expect(await screen.findByText("not installed")).toBeTruthy();
		fireEvent.click(screen.getByRole("button", { name: "Install" }));

		await waitFor(() =>
			expect(fake.calls).toContainEqual({
				name: "orch_runner_install",
				request: { host: "thevault", runner: "opencode" },
			}),
		);
	});

	it("says when it is out of quota, and what its doctor said", async () => {
		const fake = fakeCtx(() => answer);
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => answer} />);

		expect(await screen.findByText("out of quota until 2026-11-28T12:00:00Z")).toBeTruthy();
		expect(screen.getByText("the plan is at its limit")).toBeTruthy();
	});

	it("says nothing of a version it does not have", async () => {
		const versionless = {
			items: [
				{
					host: "local",
					runner: "claude-code",
					health: { installed: true, signed_in: true, guards: true },
				},
			],
			problems: [],
		};
		render(
			<Machines
				ctx={fakeCtx(() => versionless).ctx}
				operation={operation}
				schema={{}}
				run={async () => versionless}
			/>,
		);

		expect(await screen.findByText("?")).toBeTruthy();
	});

	it("says what else is worth showing about a harness", async () => {
		const detailed = {
			items: [
				{
					host: "local",
					runner: "claude-code",
					health: {
						installed: true,
						version: "3.2.1",
						signed_in: false,
						detail: "sign in with `claude login`",
					},
				},
			],
			problems: [],
		};
		const fake = fakeCtx(() => detailed);
		render(
			<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => detailed} />,
		);

		expect(await screen.findByText("sign in with `claude login`")).toBeTruthy();
	});

	it("says so when a sign-in ends without signing in", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_runner_login") {
				return {
					id: "login-2",
					host: "local",
					runner: "opencode",
					prompt: "Sign in in your browser",
					expects: "none",
					done: false,
					ok: null,
				};
			}
			if (name === "orch_runner_login_status") {
				return {
					attempt: {
						id: "login-2",
						host: "local",
						runner: "opencode",
						prompt: "Sign in in your browser",
						expects: "none",
						done: true,
						ok: false,
					},
				};
			}
			return answer;
		});
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => answer} />);

		fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));

		// (It wants nothing back: there is no code box, and no command to run.)
		expect(await screen.findByText("Sign in in your browser")).toBeTruthy();
		expect(screen.queryByLabelText("Code")).toBeNull();
		expect(await screen.findByText("It ended without signing in.")).toBeTruthy();
	});

	it("gives the command to run when the harness needs a terminal", async () => {
		const fake = fakeCtx(() => ({
			id: "login-3",
			host: "local",
			runner: "claude-code",
			prompt: "This harness cannot be driven here",
			expects: "none",
			command: "claude login",
			done: false,
			ok: null,
		}));
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => answer} />);

		fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));

		expect(await screen.findByText("claude login")).toBeTruthy();
		// (Asked again by hand, as a person would after doing it.)
		fireEvent.click(screen.getByRole("button", { name: "Check" }));
		await waitFor(() =>
			expect(fake.calls).toContainEqual({
				name: "orch_runner_login_status",
				request: { id: "login-3" },
			}),
		);
	});

	it("says why an action failed, and reads the list again", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_runner_setup") {
				throw new Error("it is not installed there");
			}
			return answer;
		});
		const run = vi.fn(async () => answer);
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={run} />);

		fireEvent.click((await screen.findAllByRole("button", { name: "Set up" }))[0] as HTMLElement);

		await waitFor(() =>
			expect(fake.toasts).toContainEqual({ message: "it is not installed there", kind: "error" }),
		);
		await waitFor(() => expect(run).toHaveBeenCalledTimes(2));
	});

	it("says an install went in, and reads the list again", async () => {
		const fake = fakeCtx(() => answer);
		const run = vi.fn(async () => answer);
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={run} />);

		fireEvent.click(await screen.findByRole("button", { name: "Update" }));

		await waitFor(() =>
			expect(fake.toasts).toContainEqual({
				message: "Installed thevault/claude-code",
				kind: "success",
			}),
		);
	});

	it("takes the link out of the harness's own words, and no punctuation with it", async () => {
		const fake = fakeCtx(() => ({
			id: "login-5",
			host: "local",
			runner: "opencode",
			prompt: "Visit (https://example.com/device).\u001b[0m and enter the code",
			expects: "none",
			done: false,
			ok: null,
		}));
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => answer} />);

		fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));

		const link = await screen.findByRole("link", { name: "Open the link" });
		expect(link.getAttribute("href")).toBe("https://example.com/device");
	});

	it("closes a relay that ended without signing in, and starts the next one clean", async () => {
		let started = 0;
		const fake = fakeCtx((name) => {
			if (name === "orch_runner_login") {
				started += 1;
				return {
					id: `login-${String(started)}`,
					host: "local",
					runner: "opencode",
					prompt: "Sign in in your browser",
					expects: "code",
					done: false,
					ok: null,
				};
			}
			if (name === "orch_runner_login_status") {
				// (The first attempt ended without signing in; the second is still going.)
				return {
					attempt: {
						id: `login-${String(started)}`,
						host: "local",
						runner: "opencode",
						prompt: "Sign in in your browser",
						expects: "code",
						done: started === 1,
						ok: started === 1 ? false : null,
					},
				};
			}
			return answer;
		});
		render(<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => answer} />);

		fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));
		expect(await screen.findByText("It ended without signing in.")).toBeTruthy();

		// (Closed, rather than left until a reload.)
		fireEvent.click(screen.getByRole("button", { name: "Close" }));
		expect(screen.queryByText("It ended without signing in.")).toBeNull();

		// (A new sign-in starts clean: no old failure, and no old code.)
		fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
		expect(await screen.findByText("Sign in in your browser")).toBeTruthy();
		expect(screen.queryByText("It ended without signing in.")).toBeNull();
		expect((screen.getByLabelText("Code") as HTMLInputElement).value).toBe("");
	});

	it("says so when it cannot ask how a sign-in is going, and stops asking", async () => {
		// (Fake timers before the render: the polling's interval is made on this clock, so the
		// advance below really runs the ticks that would have come.)
		vi.useFakeTimers({ shouldAdvanceTime: true });
		try {
			let asked = 0;
			const fake = fakeCtx((name) => {
				if (name === "orch_runner_login") {
					return {
						id: "login-4",
						host: "local",
						runner: "opencode",
						prompt: "Sign in in your browser",
						expects: "none",
						done: false,
						ok: null,
					};
				}
				if (name === "orch_runner_login_status") {
					asked += 1;
					throw new Error("the attempt has expired");
				}
				return answer;
			});
			render(
				<Machines ctx={fake.ctx} operation={operation} schema={{}} run={async () => answer} />,
			);

			fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));

			await waitFor(() =>
				expect(fake.toasts).toContainEqual({
					message: "the attempt has expired",
					kind: "error",
				}),
			);
			// (Six seconds of ticks: the interval was made on this clock, so they would run.)
			await act(async () => {
				await vi.advanceTimersByTimeAsync(6_000);
			});
			expect(asked).toBe(1);
			// (And the box says it ended, rather than waiting forever.)
			expect(screen.getByText("It ended without signing in.")).toBeTruthy();
		} finally {
			vi.useRealTimers();
		}
	});
});
