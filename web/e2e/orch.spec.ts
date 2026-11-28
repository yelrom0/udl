import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { API_KEY, SIGNED_IN } from "./helpers";

/**
 * 29.2's proof, in a real browser: `web/plugins/orch` is the orchestrator's own UI plugin,
 * built by Vite into `src/udl/plugins/apps/orch/ui` (the folder *is* the bundle, so the orch app
 * plugin serves it with itself). Every route its manifest contributes draws a page, in the
 * plugin's own shadow root; its nav is in the shell's drawer; and its widget is on the dashboard.
 * (29.5's task page and 29.7's Q&A pages draw their own headings; a question's thread is 29.7's,
 * and reads the question — with nothing seeded here it says so, under the Q&A heading.)
 *
 * 29.10 is here too: 28.6's story through the pages alone. The e2e server runs 27.7's dispatcher
 * on 28.6's scripted runner (its turns are a file this test writes through the harness's control
 * API), a `FakeGitHub` and a temp git repo, so a task dragged on the board really goes to work, an
 * agent's page really streams its session, and a question really waits for its answer.
 */

/** The routes the manifest contributes, and the heading each draws. */
const PAGES: [string, string][] = [
	["inbox", "Inbox"],
	["board", "Board"],
	["progress", "Progress"],
	["agents", "Agents"],
	["questions", "Q&A"],
	["merge", "Ready to merge"],
	["actions", "To action"],
	["features/29", "Feature 29"],
	["tasks/29.2", "Task 29.2"],
	["agents/claude", "claude"],
	["questions/Q-8", "Q&A"],
];

/** The nav the manifest contributes, in the order it contributes it (Q-8: Progress after Board). */
const NAV = ["Inbox", "Board", "Progress", "Agents", "Q&A", "Merge", "To action"];

/** What the plugin's page says it is: the first heading of its shadow root. */
async function headingOf(page: Page): Promise<string> {
	const slot = page.locator(".udl-plugin-slot");
	return slot.evaluate((host) => host.shadowRoot?.querySelector("h1")?.textContent ?? "");
}

/** The headings the shell's own DOM has: what a shadow root keeps out of `document`. */
async function shellHeadings(page: Page): Promise<string[]> {
	return page.evaluate(() =>
		[...document.querySelectorAll("h1")].map((one) => (one.textContent ?? "").trim()),
	);
}

test("every page the manifest contributes draws its heading, in the plugin's shadow root", async ({
	page,
}) => {
	for (const [route, heading] of PAGES) {
		await page.goto(`/ui/p/orch/${route}`);

		await expect(page.locator(".udl-plugin-slot")).toHaveCount(1);
		await expect.poll(async () => headingOf(page), { timeout: 15_000 }).toBe(heading);
		// (Isolated: the heading is in the plugin's shadow root, and not in the shell's own DOM.
		// `querySelectorAll` does not pierce a shadow root, so a page drawn outside one fails
		// here — which is the card's "inside the shell, isolated in its shadow root".)
		expect(await shellHeadings(page)).not.toContain(heading);
	}
});

test("its nav is in the drawer, Progress after Board (Q-8)", async ({ page }) => {
	await page.goto("/ui/");

	const drawer = page.locator(".drawer-side");
	// (The plugin's nav arrives with its manifest, which the shell reads as the page loads: on a
	// udl that is busy with a turn that is a few seconds, so the first item is waited for on its
	// own rather than each on the five seconds a failing one gets.)
	await expect(drawer.getByRole("link", { name: "Board", exact: true })).toBeVisible({
		timeout: 30_000,
	});
	for (const title of NAV) {
		await expect(drawer.getByRole("link", { name: title, exact: true })).toBeVisible();
	}

	const titles = await drawer.locator("li > a").allTextContents();
	const named = titles.map((one) => one.trim());
	const board = named.indexOf("Board");
	expect(board).toBeGreaterThanOrEqual(0);
	expect(named[board + 1]).toBe("Progress");
});

test("its widget is on the dashboard, counting what waits on a person", async ({ page }) => {
	await page.goto("/ui/");

	const slots = page.locator(".udl-plugin-slot");
	await expect
		.poll(
			async () => {
				const found = await slots.evaluateAll((hosts) =>
					hosts.map((host) => host.shadowRoot?.textContent ?? ""),
				);
				return found.some((text) => text.includes("Orchestrator"));
			},
			{ timeout: 15_000 },
		)
		.toBe(true);
});

// ---------------------------------------------------------------------------------------------
// 29.10: 28.6's story, through the pages
// ---------------------------------------------------------------------------------------------

/** A page's own part, by its accessible name, in the plugin's shadow root. */
function named(page: Page, name: string) {
	return page.locator(`.udl-plugin-slot [aria-label="${name}"]`).first();
}

/** One of udl's operations, through the API, the way a page calls it. */
async function call(page: Page, name: string, body: unknown): Promise<unknown> {
	const answer = await page.request.post(`/plugins/orch/${name}`, {
		data: body,
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
	});
	expect(answer.ok(), `${name}: ${await answer.text()}`).toBe(true);
	return answer.json();
}

/** One of udl's own operations (not a plugin's), at the REST path its schema gives it. */
async function core(
	page: Page,
	method: "post" | "put" | "get" | "delete",
	path: string,
	body: unknown,
): Promise<unknown> {
	const answer = await page.request.fetch(path, {
		method,
		data: body,
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
	});
	expect(answer.ok(), `${method} ${path}: ${await answer.text()}`).toBe(true);
	return answer.json();
}

/** Where 29.8's harness put its fakes: the fake GitHub's address, the repo it serves, and the
 * control API (written to a file because the browser's environment is not the harness's). */
interface Fakes {
	readonly url: string;
	readonly token: string;
	readonly repo: string;
	readonly controls: string;
}

function fakes(): Fakes {
	const named = process.env.UDL_E2E_GITHUB;
	expect(named, "the harness's fakes file (UDL_E2E_GITHUB)").toBeTruthy();
	return JSON.parse(readFileSync(named as string, "utf8")) as Fakes;
}

/** The harness's control API. */
function control(path: string): string {
	return `${fakes().controls}${path}`;
}

/** What the scripted harness does with the next turn it is given. */
async function script(turns: unknown[]): Promise<void> {
	const answer = await fetch(control("/turns"), {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(turns),
	});
	expect(answer.status, "the control API took the turns").toBe(204);
}

/** Start udl again, so its dispatcher reads the agents this test made (it reads them at start). */
async function restarted(page: Page): Promise<void> {
	const answer = await fetch(control("/restart"), { method: "POST" });
	expect(answer.status, "the control API started udl again").toBe(204);
	await expect
		.poll(async () => (await page.request.get("/docs")).ok(), { timeout: 120_000 })
		.toBe(true);
	// (The browser signs in again, as a person would: the page it is on is from the server that
	// has just stopped.)
	await page.goto("/ui/login");
	await page.getByLabel("API key").fill(API_KEY);
	await page.getByRole("button", { name: "Sign in" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible();
	// (Sessions live in udl's memory, so the restart signed every browser out: this one signed in
	// again, and the file the other specs start from is refreshed with it — the suite runs one
	// spec at a time (workers: 1), so a later spec is signed in.)
	await page.context().storageState({ path: SIGNED_IN });
}

/** Where the story's checkout and its origin are. */
async function storyRepo(): Promise<{ checkout: string; origin: string }> {
	const answer = await fetch(control("/repo"));
	expect(answer.ok, "the control API says where the story's repo is").toBe(true);
	return (await answer.json()) as { checkout: string; origin: string };
}

/** A repository's pulls, as the fake GitHub has them. */
async function pulls(repo: string): Promise<{ number: number; head: string; state: string }[]> {
	const answer = await fetch(control(`/github-pulls?repo=${encodeURIComponent(repo)}`));
	const found = (await answer.json()) as {
		pulls: { number: number; head: string; state: string }[];
	};
	return found.pulls;
}

/** The commit at a branch's head, as the story's origin has it ("" until it is pushed). */
async function head(branch: string): Promise<string> {
	const answer = await fetch(control(`/repo/head?branch=${encodeURIComponent(branch)}`));
	return ((await answer.json()) as { sha: string }).sha;
}

/** A comment on a pull, as the review bot writes one. */
async function commented(repo: string, number: number, text: string, login: string): Promise<void> {
	const answer = await fetch(control("/github/comment"), {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ repo, number, text, login }),
	});
	expect(answer.status, "the control API took the comment").toBe(204);
}

/** A pull's head moved to `sha`, as GitHub's does when a branch is pushed to. */
async function pushed(repo: string, number: number, sha: string): Promise<void> {
	const answer = await fetch(control("/github"), {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ repo, number, sha }),
	});
	expect(answer.status, "the control API took the push").toBe(204);
}

/** The fake bot's two comments: one item first, then its clean verification. */
const FIRST_REVIEW = `## Review: the story's task (#1)

**Overall: request changes.** One thing is wrong.

### Issues
1. **The file says the wrong thing** (\`README.md:1\`). It should say the right thing.

**Verdict: changes needed**
`;

const CLEAN_REVIEW = `## Follow-up review: the story's task (#1)

### Earlier items
1. The file says the wrong thing: **FIXED**.

**New**
Nothing new.

**Verdict: all clear**
`;

/** What this run seeded: its own feature, tasks, repo and account, and its two agents. */
interface Seeded {
	readonly repo: string;
	readonly repoName: string;
	readonly account: string;
	readonly home: string;
	readonly feature: string;
	readonly story: string;
	readonly dragged: string;
	readonly merged: string;
	readonly worker: string;
	readonly watcher: string;
}

/**
 * What a person sets up once, through udl's own operations: the GitHub account, the
 * project with its review bot, its repo and its gate, two agents on the scripted runner, the
 * feature and the tasks — one for the story, one to drag, one already merged (so the progress
 * page has a history).
 */
async function setup(page: Page, run: string): Promise<Seeded> {
	const { url: github, repo } = fakes();
	const { checkout } = await storyRepo();
	const feature = `e2e-${run}`;
	const seeded: Seeded = {
		repo,
		// (A repo record of its own: 29.8's spec keeps its `udl` one, and the suite runs on one
		// database.)
		repoName: `story-${run}`,
		account: `github:yelrom0-${run}`,

		// (The harness's temp home: a checkout of the story's repo, whose parent is where its
		// config, its secrets and its database are — a chat's folder belongs inside it.)
		home: checkout.slice(0, checkout.lastIndexOf("/")),
		feature,
		story: `${feature}.1`,
		dragged: `${feature}.2`,
		merged: `${feature}.3`,
		worker: `worker-${run}`,
		watcher: `watcher-${run}`,
	};
	// (The turns run in a folder of the story's own, inside the harness's temp home. It is udl's
	// own `local` host, changed rather than added: `local` is always there, and the only host
	// whose transport is `local`.)
	await core(page, "put", "/hosts", {
		host: { name: "local", transport: "local", work_dir: `${seeded.home}/work` },
		update: { work_dir: `${seeded.home}/work` },
	});
	// (One udl, one story at a time: an agent's push asks which account may push, and two
	// connected accounts make that question unanswerable — so the other colour's is disconnected.
	// Its own story has finished by then: the suite runs one spec at a time.)
	const connected = (await core(page, "get", "/accounts", undefined)) as {
		accounts?: { account?: string; integration?: string }[];
	};
	for (const one of connected.accounts ?? []) {
		const ref = one.account ?? "";
		if (one.integration === "github" && ref !== seeded.account) {
			await core(page, "delete", `/accounts/${encodeURIComponent(ref)}`, undefined);
		}
	}
	await core(page, "post", "/accounts", {
		integration: "github",
		// (Per run: the two projects' stories share one database, and an account's name is its key.)
		name: seeded.account.replace("github:", ""),
		method: "tokens",
		options: { api_url: github, repos: [repo] },
		tokens: { token: "ghp_fake_token" },
	});
	await call(page, "set_project", {
		name: "udl",
		title: "udl",
		review_bot: "claude",
	});
	await call(page, "set_repo", {
		project: "udl",
		name: seeded.repoName,
		github: repo,
		account: seeded.account,
		checkouts: { local: checkout },
		gate: ["make", "check"],
	});
	for (const [name, title] of [
		[seeded.worker, "Worker"],
		[seeded.watcher, "Watcher"],
	] as const) {
		await call(page, "set_agent", {
			name,
			title,
			runner: "scripted",
			projects: ["udl"],
			max_nudges: 50,
		});
	}
	// (The pulls' watcher: it is what has udl look at the repository at all, and what keeps each
	// pull link's head current.)
	await core(page, "put", "/actions/watch-pulls", {
		plugin: "notify",
		options: { title: "A pull changed" },
	});
	await core(page, "put", "/subscriptions/watch-pulls", {
		events: ["github.pr.*"],
		actions: ["watch-pulls"],
	});
	await call(page, "set_feature", { project: "udl", key: feature, title: "The story" });
	await call(page, "set_task", {
		project: "udl",
		key: seeded.story,
		feature,
		title: "The story's task",
		size: "S",
		repo: seeded.repoName,
	});
	await call(page, "set_task", {
		project: "udl",
		key: seeded.dragged,
		feature,
		title: "The dragged task",
		size: "S",
		repo: seeded.repoName,
	});
	await call(page, "set_task", {
		project: "udl",
		key: seeded.merged,
		feature,
		title: "Already done",
		size: "M",
	});
	await call(page, "move_task", { project: "udl", key: seeded.merged, to: "merged" });
	return seeded;
}

/** What a run left behind, to be cleaned up: its two agents and its GitHub account. */
interface Left {
	readonly worker: string;
	readonly watcher: string;
	readonly account: string;
}

/**
 * Leave nothing behind for the specs that follow: both agents paused (a paused agent is not
 * nudged, so it asks nothing), the account disconnected (an agent's push asks which account may
 * push, and a second one makes that unanswerable). The suite runs on one database and one fake
 * GitHub, one spec at a time, so the next spec sees what this one left.
 */
async function tidy(page: Page, left: Left, origin: string): Promise<void> {
	const headers = { "X-UDL-CSRF": "1", Origin: origin };
	for (const name of [left.worker, left.watcher]) {
		const found = await page.request.post("/plugins/orch/get_agent", {
			data: { name },
			headers,
		});
		if (!found.ok()) {
			continue; // (a test that never made it)
		}
		const answer = (await found.json()) as { agent?: unknown };
		if (answer.agent !== undefined && answer.agent !== null) {
			await call(page, "pause_agent", { name });
		}
	}
	// (A blocking question is pinned on the inbox and counts in the nav, so one left open would
	// be a second one to the specs that follow — and the other colour's run. An agent made
	// after udl started is dispatched to now (27.7), so one this spec assigned a task it has no
	// turn for is nudged until it stalls and asks; answering it closes it, as a person would.)
	// (Up to three passes: a turn already running when the agent was paused can still end and ask
	// after the first.)
	for (let pass = 0; pass < 3; pass += 1) {
		const asked = (await call(page, "list_questions", { project: "udl" })) as {
			questions?: { key: string; state: string; asked_by: string }[];
		};
		const open = (asked.questions ?? []).filter(
			(one) => one.state === "open" && [left.worker, left.watcher].includes(one.asked_by),
		);
		if (open.length === 0) {
			break;
		}
		for (const one of open) {
			await call(page, "answer", { key: one.key, answer: "Answered by the tidy." });
		}
		await page.waitForTimeout(500);
	}
	// (It must be closed now: an open one would be pinned on the next spec's inbox.)
	const left_open = (await call(page, "list_questions", { project: "udl" })) as {
		questions?: { key: string; state: string; asked_by: string }[];
	};
	expect(
		(left_open.questions ?? [])
			.filter((one) => one.state === "open" && [left.worker, left.watcher].includes(one.asked_by))
			.map((one) => `${one.key} ${one.asked_by}`),
	).toEqual([]);
	const gone = await page.request.delete(`/accounts/${encodeURIComponent(left.account)}`, {
		headers,
	});
	// (404: a test that never connected it; 401: one that never signed in, so there is nothing.)
	expect([200, 204, 401, 404]).toContain(gone.status());
}

/** Where a task is now, as udl has it. */
async function stateOf(page: Page, key: string): Promise<string> {
	const found = (await call(page, "get_task", { project: "udl", key })) as {
		task?: { state?: string; branch?: string };
	};
	return found.task?.state ?? "";
}

// (After every test, whatever it did: the agents it made are paused and the account it
// connected is gone, so the specs that follow — and the other colour's run — find the database
// as they expect it. A test that failed midway leaves state too, which is why this is here and
// not at the end of each test.)
test.afterEach(async ({ page }, testInfo) => {
	// (The run's own address: a test that failed before its first page has no `page.url()`.)
	const origin = String(testInfo.project.use.baseURL);
	for (const run of [testInfo.project.name, `${testInfo.project.name}-chat`]) {
		await tidy(
			page,
			{
				worker: `worker-${run}`,
				watcher: `watcher-${run}`,
				account: `github:yelrom0-${run}`,
			},
			origin,
		);
	}
});

test("the story: a drag, a transcript, a question, an answer, and a pull, through the pages (29.10)", async ({
	page,
}, testInfo) => {
	// (The story waits on sessions, polls and a bot: far longer than a page's own 30 seconds.)
	test.setTimeout(600_000);
	await page.goto("/ui/");
	const seeded = await setup(page, `${testInfo.project.name}-story`);
	// (The dispatcher reads its agents when udl starts (27.7), and a browser test can only make
	// them once it runs: udl starts again, as a person's would after they set the agents up.)
	await restarted(page);

	// (The agent's turns, as 28.6's story has them: it asks what it cannot decide, does the work
	// and pushes once answered, fixes what the bot found, then says the pull is ready.)
	await script([
		{
			when: `Your task is ${seeded.dragged}`,
			steps: [
				{ mcp: { tool: "orch_get_task", arguments: { key: seeded.dragged } } },
				{ wait: 6 },
				{ mcp: { tool: "orch_get_task", arguments: { key: seeded.dragged } } },
				{ wait: 20 },
				{
					mcp: {
						tool: "orch_ask",
						arguments: {
							task: seeded.dragged,
							title: "Shall I carry on?",
							text: "The dragged task needs a word from a person.",
							options: ["Not now."],
							blocking: true,
						},
					},
				},
			],
		},
		{
			when: `Your task is ${seeded.story}`,
			steps: [
				{
					mcp: {
						tool: "orch_ask",
						arguments: {
							task: seeded.story,
							title: "Which way?",
							text: "Which way should the file say it?",
							options: ["The right way."],
							blocking: true,
						},
					},
				},
			],
		},
		{
			when: "The right way.",
			steps: [
				{ shell: ["sh", "-c", "echo 'the right thing' > README.md"] },
				{ shell: ["git", "add", "-A"] },
				{ shell: ["git", "commit", "-m", "the thing, the right way"] },
				{ mcp: { tool: "orch_push", arguments: { key: seeded.story } } },
			],
		},
		{
			when: "The file says the wrong thing",
			steps: [
				{ shell: ["sh", "-c", "echo 'the right thing, said plainly' > README.md"] },
				{ shell: ["git", "add", "-A"] },
				{ shell: ["git", "commit", "-m", "fix: the file says it plainly"] },
				{ mcp: { tool: "orch_push", arguments: { key: seeded.story } } },
			],
		},
		{
			when: "its pull is ready to merge",
			steps: [
				{
					mcp: {
						tool: "orch_ready",
						arguments: { key: seeded.story, note: "done" },
					},
				},
			],
		},
	]);

	// (The story's task goes to its agent, as a person's own assignment: the dispatcher wakes on
	// the change, so its first turn starts at once.)
	await call(page, "assign", { project: "udl", key: seeded.story, agent: seeded.worker });

	// -- the board: a suggestion dragged onto an agent goes to work, live (29.4's step) ---------
	await page.goto("/ui/p/orch/board");
	await expect(named(page, "Suggested")).toBeVisible();
	await named(page, `Suggested: ${seeded.dragged} The dragged task`).dispatchEvent("dragstart");
	await named(page, seeded.watcher).dispatchEvent("drop");
	// (udl has it: the card is on that agent's queue…)
	await expect(named(page, seeded.watcher).locator(`[data-task="${seeded.dragged}"]`)).toBeVisible({
		timeout: 15_000,
	});
	// (…and the dispatcher starts the session, so the card goes to work without a reload.)
	await expect(named(page, `${seeded.dragged} The dragged task`)).toContainText("in_progress", {
		timeout: 90_000,
	});
	// (Its scripted turn is all the story asks of it: paused now, so the turn that is running
	// finishes and no other starts — as a person pauses an agent they have seen do its part.)
	await call(page, "pause_agent", { name: seeded.watcher });

	// -- the agent's page: its transcript, as the session streams it (29.6's step) --------------
	await page.goto(`/ui/p/orch/agents/${seeded.watcher}`);
	// (It is in one, and the page says so — not "it is not in a session".)
	await expect(named(page, "Session")).toContainText("running", { timeout: 30_000 });
	const transcript = named(page, "Transcript");
	// (Line by line: the page is open while the turn runs, and the second tool call — six seconds
	// after the first — is drawn as the runner says it, without a reload. Two calls, not two
	// rows: the transcript keeps a session's events by their key, so 18.6-dupe's doubling of the
	// live stream cannot make it four.)
	await expect(transcript.getByText(/orch_get_task/)).toHaveCount(2, { timeout: 60_000 });

	// -- the Q&A page: the agent asks, and the person answers (29.7's page) ---------------------
	await page.goto("/ui/p/orch/questions");
	const blocking = page.getByRole("region", { name: "Blocking" });
	await expect(blocking.getByText("Which way?")).toBeVisible({ timeout: 90_000 });
	await expect(blocking.getByText("Shall I carry on?")).toBeVisible({ timeout: 90_000 });
	await blocking.getByRole("button", { name: "The right way." }).click();
	await blocking.getByRole("button", { name: "Not now." }).click();
	// (The answer is sent, the agent takes the task up again, and the page's live stream drops the
	// question: a few seconds, not a page load. The story's two, not "none at all": the suite
	// runs on one database, and another spec's agent may be asking its own.)
	await expect(blocking.getByText("Which way?")).toHaveCount(0, { timeout: 60_000 });
	await expect(blocking.getByText("Shall I carry on?")).toHaveCount(0, { timeout: 60_000 });

	// -- the pull: the agent pushes, the bot finds an item, the fix goes up, the bot is clean ---
	// (Everything from here is read from udl's records, through the API — the task, the branch,
	// the pull — rather than counted in the page's live stream: with automations on, the stream
	// sends each event twice (18.6-dupe), so a doubled row would break a count that is not a
	// count of anything.)
	const { repo } = seeded;
	/** The pull the agent opened, once there is one: the story's branch's own (the fake has
	 * pulls of its own, which a person's merges never touch). */
	async function thePull() {
		const task = (await call(page, "get_task", { project: "udl", key: seeded.story })) as {
			task?: { branch?: string };
		};
		const branch = task.task?.branch ?? "";
		const found = (await pulls(repo)).filter((one) => one.branch === branch);
		return found.length === 0 ? null : found[found.length - 1];
	}
	/** The task's branch head, with the pull moved to it as GitHub's own would be. */
	async function theHead(pull: { number: number; head: string }): Promise<string> {
		const branch = (await call(page, "get_task", { project: "udl", key: seeded.story })) as {
			task?: { branch?: string };
		};
		const sha = await head(branch.task?.branch ?? "");
		if (sha !== "" && sha !== pull.head) {
			await pushed(repo, pull.number, sha);
		}
		return sha;
	}
	// (The pull appears once the agent pushes; its head is the branch's, as GitHub's would be —
	// the fake answers the pull it is asked for, and the harness moves it to the pushed commit.)
	let first = "";
	for (let at = 0; at < 90 && first === ""; at += 1) {
		const found = await thePull();
		if (found !== null) {
			const sha = await theHead(found);
			if (sha !== "" && found.head === sha) {
				first = sha;
			}
		}
		if (first === "") {
			await page.waitForTimeout(3000);
		}
	}
	expect(first, "the pull the agent opened, at its branch's head").not.toBe("");
	const pull = (await thePull()) as { number: number; head: string };
	await commented(repo, pull.number, FIRST_REVIEW, "claude");

	// (The agent fixes it, and the new head is the pull's: the bot's verification follows it.)
	await expect
		.poll(
			async () => {
				const found = await thePull();
				if (found === null) {
					return false;
				}
				const sha = await theHead(found);
				return sha !== "" && sha !== first && found.head === sha;
			},
			{ timeout: 180_000 },
		)
		.toBe(true);
	await commented(repo, pull.number, CLEAN_REVIEW, "claude");

	// (The agent says the pull is ready — answering the dispatcher, as a person would, if it
	// looked at the pull in the moment between the head moving and the bot's answer.)
	await expect
		.poll(
			async () => {
				if ((await stateOf(page, seeded.story)) === "ready") {
					return true;
				}
				const asked = (await call(page, "list_questions", { project: "udl" })) as {
					questions?: { key: string; title: string; state: string }[];
				};
				const waiting = (asked.questions ?? []).find(
					(one) => one.state === "open" && one.title.toLowerCase().includes("how long to wait"),
				);
				if (waiting !== undefined) {
					await call(page, "answer", { key: waiting.key, answer: "Carry on now." });
				}
				return false;
			},
			{ timeout: 240_000 },
		)
		.toBe(true);

	// -- the task's page: ready, with its pull, its readiness and its decision ------------------
	await page.goto(`/ui/p/orch/tasks/${seeded.story}`);
	await expect(named(page, "State").getByText("ready")).toBeVisible({ timeout: 30_000 });
	await expect(named(page, "Pull")).toContainText("Ready to merge");
	const notes = page.getByRole("list", { name: "Notes" });
	await expect(notes).toContainText("The right way.");

	// -- the progress page: the history, drawn (29.11's step) ----------------------------------
	await page.goto("/ui/p/orch/progress?project=udl");
	await expect(named(page, "The burn-up's lines")).toBeVisible({ timeout: 30_000 });
	await expect(
		named(page, "By feature").locator("li").filter({ hasText: seeded.feature }),
	).toBeVisible();
});

test("the chat allows a permission and assigns a task, and the Board has it (29.12, Q-8)", async ({
	page,
}, testInfo) => {
	test.setTimeout(600_000);
	await page.goto("/ui/");
	const seeded = await setup(page, `${testInfo.project.name}-chat`);

	// (The agent the chat assigns to is paused before it: udl dispatches to an agent made while it
	// runs now (27.7), and this spec's scripted harness has a turn for the chat's prompt, not for
	// an agent session's — an agent nudged `max_nudges` times without progress stalls and asks a
	// blocking question, which the specs that follow would see pinned. This spec is about the
	// chat's permission and the Board, and neither needs the agent to work.)
	await call(page, "pause_agent", { name: seeded.watcher });

	// (The harness asks before it acts, and its next step is the assignment: the person's answer
	// is what lets the turn go on.)
	await script([
		{
			when: "put it on the board",
			steps: [
				{
					mcp: {
						tool: "orch_chat_ask_permission",
						arguments: {
							tool_name: "orch_assign",
							input: { project: "udl", key: seeded.dragged, agent: seeded.watcher },
						},
					},
				},
				{
					mcp: {
						tool: "orch_assign",
						arguments: { project: "udl", key: seeded.dragged, agent: seeded.watcher },
					},
				},
			],
		},
	]);

	// (The chat, from the navbar: the scripted harness, in a folder of its own.)
	await page.getByRole("button", { name: "Chat" }).click();
	const panel = page.locator('[data-panel="orch/chat"]');
	await expect(panel).toBeVisible();
	await panel.getByRole("button", { name: "Start a new chat" }).click();
	await panel.getByLabel("Harness").selectOption("scripted");
	await panel.getByLabel("Folder").fill(seeded.home);
	await page.getByRole("button", { name: "Start the chat" }).click();
	await expect(panel.getByLabel("Transcript")).toBeVisible();

	const message = panel.getByLabel("Message");
	await message.fill("put it on the board");
	await message.press("Enter");
	const asked = panel.getByLabel("Waiting on you");
	await expect(asked).toBeVisible({ timeout: 30_000 });
	await expect(asked).toContainText("orch_assign");
	await asked.getByRole("button", { name: "Allow once" }).click();
	await expect(asked).toBeHidden();

	// (The assignment the chat made, on the Board: the task is that agent's.)
	await page.goto("/ui/p/orch/board");
	await expect(named(page, seeded.watcher).locator(`[data-task="${seeded.dragged}"]`)).toBeVisible({
		timeout: 60_000,
	});
});
