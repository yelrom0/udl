import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";

/**
 * 29.8's proof, in a real browser: what is ready to merge is listed with its checks and the
 * bot's word, a Merge button asks which pull it is and merges it through udl, and the task
 * stacked on it is told to rebase.
 *
 * The GitHub here is the fake `tests/support/e2e.py` serves (`support.github.FakeGitHub`), which
 * the account's `api_url` points at: a merge is udl's own, and nothing goes near github.com.
 */

/** A write from outside the page: the session's cookie, the CSRF header and the page's origin. */
async function call(page: Page, name: string, body: object): Promise<unknown> {
	const answer = await page.request.post(`/plugins/orch/${name}`, {
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
		data: body,
	});
	expect(answer.ok(), `${name} answered ${answer.status()}: ${await answer.text()}`).toBe(true);
	return await answer.json();
}

/** The fake GitHub the harness serves: its address, its token, its repository (the harness
 * writes them, since a token never goes in a command line) and its control API.
 *
 * It is read inside the test, not at load: Playwright loads the spec to list its tests before
 * `webServer` has started, so at load the file does not exist yet (and a previous run's would be
 * stale). */
function harness(): { url: string; token: string; repo: string; controls: string } {
	return JSON.parse(
		readFileSync(
			process.env.UDL_E2E_GITHUB ?? new URL("../playwright/.e2e-github.json", import.meta.url),
			"utf8",
		),
	) as { url: string; token: string; repo: string; controls: string };
}
const REPO = "acme/widgets";
const PULL = 7;

/** The account the repo reaches the fake through: its api_url is the fake's, its token the
 * fake's own, and the repository is the one the fake starts with. (Both themes run against one
 * server, so the second finds it connected.) */
async function connected(page: Page): Promise<void> {
	const github = harness();
	const answer = await page.request.post("/accounts", {
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
		data: {
			integration: "github",
			name: "e2e",
			method: "tokens",
			options: { repos: [REPO], api_url: github.url },
			tokens: { token: github.token },
			reconnect: true,
		},
	});
	expect(answer.ok(), `connect answered ${answer.status()}: ${await answer.text()}`).toBe(true);
}

/** A project, a feature, two tasks (the second stacked on the first), and a repo on the fake. */
async function seeded(page: Page, name: string): Promise<void> {
	await connected(page);
	await call(page, "set_project", { name: "udl", title: "udl" });
	await call(page, "set_feature", { key: "29", title: "The web UI" });
	await call(page, "set_task", { key: "29.8", feature: "29", title: "Ready to merge" });
	await call(page, "set_task", {
		key: "29.9",
		feature: "29",
		title: "Built on 29.8",
		needs: ["29.8"],
	});
	await call(page, "set_repo", { name: "udl", github: REPO, account: `github:${name}` });
	// (A task with a pull to show is one that has started, and one that is ready to merge is
	// `ready`: the merge list reads its link. Both themes run against one server, so a task the
	// first theme moved is left where it is.)
	for (const [key, to] of [
		["29.8", "ready"],
		["29.9", "in_progress"],
	] as const) {
		const found = (await call(page, "get_task", { key })) as { task?: { state?: string } };
		if (found.task?.state === "open") {
			await call(page, "move_task", { key, to });
		}
	}
	await call(page, "add_link", {
		owner_kind: "task",
		owner: "29.8",
		link_kind: "pull",
		url: `https://github.com/${REPO}/pull/${PULL}`,
	});
	// (A person pushing to the pull is what makes udl look at it again, and the link's head is
	// what the merge is at.)
	// (A subscription on the pulls' events: it is what has the github watcher look at the
	// repository at all, and what keeps the pull link's head current.)
	const act = await page.request.put("/actions/watch-pulls", {
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
		data: {
			description: "A pull changed",
			plugin: "notify",
			options: { title: "A pull changed" },
		},
	});
	expect(act.ok(), `the action answered ${act.status()}: ${await act.text()}`).toBe(true);
	const sub = await page.request.put("/subscriptions/watch-pulls", {
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
		data: { events: ["github.pr.*"], actions: ["watch-pulls"] },
	});
	expect(sub.ok(), `the subscription answered ${sub.status()}: ${await sub.text()}`).toBe(true);
}

test("a ready pull merges, and the task stacked on it is told to rebase (29.8)", async ({
	page,
}) => {
	test.setTimeout(120_000);
	await page.goto("/ui/");
	await seeded(page, "e2e");

	// (The watcher's first look at a repository is its baseline, so what was already there is not
	// news: wait until it has looked (the harness counts the fake's calls), then push a new head.)
	const controls = harness().controls;
	await expect
		.poll(
			async () => {
				const seen = await page.request.get(`${controls}/github-calls`);
				const body = (await seen.json()) as { calls: string[] };
				// (Twice, and the list itself — `/pulls/7` is one pull, not the look: the first
				// look is the baseline, the second proves it looks again.)
				const looks = body.calls.filter((one) => /\/repos\/acme\/widgets\/pulls(\?|$)/.test(one));
				return looks.length >= 2;
			},
			{ timeout: 60_000 },
		)
		.toBe(true);
	const pushed = await page.request.post(`${controls}/github`, { data: { sha: "abc1235" } });
	expect(pushed.ok(), `the push answered ${pushed.status()}`).toBe(true);
	// (The event the watcher published, then the link's head, read through the task.)
	await expect
		.poll(
			async () => {
				// (Only the pulls' events: the whole run's list is long, and a page of it
				// would not reach this one.)
				const ev = await page.request.get("/events", { params: { type: "github.pr.*" } });
				const found = JSON.parse(await ev.text()) as { events: { type: string }[] };
				return found.events.some((one) => one.type.startsWith("github.pr."));
			},
			{ timeout: 60_000 },
		)
		.toBe(true);
	// (What the notes claim: the link's head, read through the task, is the pushed one.)
	await expect
		.poll(
			async () => {
				const found = (await call(page, "get_task", { key: "29.8" })) as {
					links?: { link_kind: string; state?: { head_sha?: string } }[];
				};
				const link = (found.links ?? []).find((one) => one.link_kind === "pull");
				return link?.state?.head_sha ?? "";
			},
			{ timeout: 60_000 },
		)
		.toBe("abc1235");

	// (Both themes run against one server: the first theme merged this pull, and the second
	// finds the task done — the merge itself is proved once, and the note both times.)
	const before = (await call(page, "get_task", { key: "29.8" })) as {
		task?: { state?: string };
	};
	if (before.task?.state === "done" || before.task?.state === "merged") {
		const next = (await call(page, "get_task", { key: "29.9" })) as {
			notes?: { note_kind: string; text: string }[];
		};
		expect(
			(next.notes ?? []).some(
				(one) => one.note_kind === "system" && one.text.includes("Rebase onto the base"),
			),
			"the stacked task is told to rebase",
		).toBe(true);
		return;
	}

	await page.goto("/ui/p/orch/merge");
	const row = page
		.getByRole("listitem")
		.filter({ hasText: `#${PULL}` })
		.first();
	await expect(row).toBeVisible();
	await expect(row.getByText("29.8")).toBeVisible();

	// (The button asks which pull it is — the shell's own confirm dialog, naming it
	// `owner/repo#number` — then merges at the head the row was listed at.)
	await row.getByRole("button", { name: "Merge" }).click();
	const asked = page.locator("dialog");
	await expect(asked).toBeVisible();
	await expect(asked).toContainText(`#${PULL}`);
	await asked.getByRole("button", { name: "Merge" }).click();

	// (The task is merged, and the one stacked on it is told to rebase onto the base.)
	await expect
		.poll(async () => {
			const found = (await call(page, "get_task", { key: "29.8" })) as {
				task?: { state?: string };
			};
			return found.task?.state;
		})
		// (Merged, and done at once when nothing follows the base: a person merged it, 28.3.)
		.toBe("done");

	const next = (await call(page, "get_task", { key: "29.9" })) as {
		notes?: { note_kind: string; text: string }[];
	};
	const told = (next.notes ?? []).find(
		(one) => one.note_kind === "system" && one.text.includes("Rebase onto the base"),
	);
	expect(told, "the stacked task is told to rebase").toBeTruthy();
});
