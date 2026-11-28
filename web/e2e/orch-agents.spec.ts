import { expect, type Page, test } from "@playwright/test";
import { SIGNED_IN } from "./helpers";

/**
 * 29.6's proof, in a real browser: the agents' page draws who reports to whom from the humans
 * down, an agent dragged onto a manager reports to it — udl's own answer, read back — and an
 * agent's page draws its queue in udl's order, with each task's size, its points, and what a
 * person may do to it.
 *
 * The e2e server runs the real orchestrator plugin on a SQLite database with automations off (no
 * dispatcher), so nothing is running an agent here: its session is what the page says it has,
 * and its queue is what `orch_assign` left on it.
 */

test.use({ storageState: SIGNED_IN });

/** The plugin's own element, by its accessible name, in its shadow root. */
function named(page: Page, name: string) {
	return page.locator(`.udl-plugin-slot [aria-label="${name}"]`).first();
}

/** One of the plugin's operations, through the API, the way the page calls it. */
async function call(page: Page, name: string, body: unknown): Promise<unknown> {
	const answer = await page.request.post(`/plugins/orch/${name}`, {
		data: body,
		// (A write with a session's cookie says where it comes from, as a browser's own does.)
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
	});
	expect(answer.ok(), `${name}: ${await answer.text()}`).toBe(true);
	return answer.json();
}

/** What this run seeded: its own manager, its own implementer, and the task it is on. */
interface Seeded {
	readonly manager: string;
	readonly agent: string;
	/** A second implementer, under the humans until a drag gives it to the manager. */
	readonly spare: string;
	readonly task: string;
}

/** A project, a feature, a manager, an implementer and one task on it. */
async function seed(page: Page, run: string): Promise<Seeded> {
	// (A page first: a write says where it comes from, and that is the server's own address.)
	await page.goto("/ui/");
	// (Names of this spec's own: the board's spec seeds `fake-${run}` too, and both run at once.)
	const feature = `agents-${run}`;
	const manager = `boss-agents-${run}`;
	const agent = `impl-agents-${run}`;
	const spare = `spare-agents-${run}`;
	const task = `${feature}.1`;
	// (The one project the e2e server's other specs seed too: a second one would make "the only
	// project" ambiguous for every operation that leaves it out.)
	await call(page, "set_project", { name: "udl", title: "udl" });
	await call(page, "set_feature", { project: "udl", key: feature, title: "Screens" });
	await call(page, "set_agent", {
		name: manager,
		title: "The boss",
		runner: "claude-code",
		role: "manager",
		manages: { assign: true, answer: false, review: false, plan: false },
	});
	await call(page, "set_agent", {
		name: agent,
		title: "Fake",
		runner: "claude-code",
		role: "implementer",
		manager,
		projects: ["udl"],
	});
	await call(page, "set_agent", {
		name: spare,
		title: "Spare",
		runner: "claude-code",
		role: "implementer",
		projects: ["udl"],
		// (A free model, so its usage is an allowance udl counts rather than a key's money: the
		// one an e2e server with no AI provider can answer for.)
		model: "openrouter/ernie/free",
	});
	await call(page, "set_task", {
		project: "udl",
		key: task,
		feature,
		title: "A thing worth doing",
		size: "L",
	});
	await call(page, "assign", { project: "udl", key: task, agent });
	return { manager, agent, spare, task };
}

test("the tree draws who reports to whom, and a drag gives an agent to a manager (29.6)", async ({
	page,
}, testInfo) => {
	const { manager, agent } = await seed(page, testInfo.project.name);

	await page.goto("/ui/p/orch/agents");

	// (The tree: the humans at the root, the manager under them, the implementer under it.)
	await expect(named(page, "Humans")).toBeVisible();
	await expect(
		named(page, "The humans' reports").locator(`[aria-label="${manager}"]`),
	).toBeVisible();
	await expect(
		named(page, `${manager}'s reports`).locator(`[aria-label="${agent}"]`),
	).toBeVisible();

	// (Taken out of the manager's reports and given to the humans, then back to the manager.)
	await named(page, agent).dispatchEvent("dragstart");
	await named(page, "Humans").dispatchEvent("drop");
	await expect(named(page, "The humans' reports").locator(`[aria-label="${agent}"]`)).toBeVisible({
		timeout: 15_000,
	});

	await named(page, agent).dispatchEvent("dragstart");
	await named(page, manager).dispatchEvent("drop");
	await expect(named(page, `${manager}'s reports`).locator(`[aria-label="${agent}"]`)).toBeVisible({
		timeout: 15_000,
	});

	// (It is udl's own answer, not the page's: read until its record says so. A write's own
	// answer can be read back a moment before a later read of the same record sees it.)
	await expect
		.poll(
			async () => {
				const found = (await call(page, "get_agent", { name: agent })) as {
					agent: { manager: string | null };
				};
				return found.agent.manager;
			},
			{ timeout: 15_000 },
		)
		.toBe(manager);
});

test("an agent's page draws its queue, its sizes and its points (29.6)", async ({
	page,
}, testInfo) => {
	const { agent, task } = await seed(page, testInfo.project.name);

	await page.goto(`/ui/p/orch/agents/${agent}`);

	// (Its title, its role, and what it runs on.)
	await expect(page.locator(".udl-plugin-slot h1").first()).toHaveText("Fake");
	await expect(named(page, "Its queue")).toBeVisible();
	// (The task it is on: udl's order, its size, and the points it comes to — an L is five.)
	await expect(named(page, task)).toContainText("now");
	await expect(named(page, task)).toContainText("L");
	await expect(named(page, "Its queue")).toContainText("5 points");
	// (Nothing is running it in this harness.)
	await expect(named(page, "Session")).toContainText("It is not in a session.");
});

test("an agent is made through the page's own form, and paused from it (29.6)", async ({
	page,
}, testInfo) => {
	const run = testInfo.project.name;
	const { manager } = await seed(page, run);
	const made = `made-agents-${run}`;

	await page.goto("/ui/p/orch/agents");
	await page.getByRole("button", { name: "New agent" }).click();
	const form = page.getByRole("form", { name: "New agent" });
	await form.getByLabel("Name").fill(made);
	await form.getByLabel("Title").fill("Made by hand");
	// (The editor reads the host's harnesses as it opens and shows the field as loading until
	// they answer, so the spec waits for it: then it is a select when a host has harnesses, and a
	// box to type a name in when none has. The wait is a long one: once udl has a host, every
	// harness's own doctor is asked (each a few seconds when the machine really has them), so the
	// answer takes 20 seconds, not one.)
	const runner = form.getByLabel("Runner", { exact: true });
	await expect(runner).toBeEnabled({ timeout: 90_000 });
	if ((await runner.evaluate((one) => one.tagName)) === "SELECT") {
		const offered = await runner
			.locator("option")
			.evaluateAll((ones) =>
				ones.map((one) => (one as HTMLOptionElement).value).filter((value) => value !== ""),
			);
		await runner.selectOption(offered[0] ?? "fake");
	} else {
		await runner.fill("fake");
	}
	await form.getByRole("button", { name: "Save" }).click();

	// (It is udl's answer: the tree reads it again, and the new agent is under the humans.)
	await expect(named(page, "The humans' reports").locator(`[aria-label="${made}"]`)).toBeVisible({
		timeout: 15_000,
	});

	// (A person may pause it from its own page, and resume it again.)
	await page.goto(`/ui/p/orch/agents/${made}`);
	await page.getByRole("button", { name: "Pause" }).click();
	await expect(page.getByRole("button", { name: "Resume" })).toBeVisible({ timeout: 15_000 });
	await expect(named(page, "paused")).toBeVisible();

	// (Its manager's page still draws what it may do.)
	await page.goto(`/ui/p/orch/agents/${manager}`);
	await expect(named(page, "Its powers")).toContainText("Assign");
});

test("a manager takes two reports by a drag, and both are drawn under it (29.6)", async ({
	page,
}, testInfo) => {
	const { manager, agent, spare } = await seed(page, testInfo.project.name);

	await page.goto("/ui/p/orch/agents");
	// (Both start under the humans; one is given to the manager, and the other follows it.)
	await expect(named(page, "The humans' reports").locator(`[aria-label="${spare}"]`)).toBeVisible();
	await named(page, agent).dispatchEvent("dragstart");
	await named(page, manager).dispatchEvent("drop");
	await expect(named(page, `${manager}'s reports`).locator(`[aria-label="${agent}"]`)).toBeVisible({
		timeout: 15_000,
	});

	await named(page, spare).dispatchEvent("dragstart");
	await named(page, manager).dispatchEvent("drop");
	// (Both are its reports at once: the drag writes one, and the tree is read again.)
	await expect(named(page, `${manager}'s reports`).locator(`[aria-label="${spare}"]`)).toBeVisible({
		timeout: 15_000,
	});
	await expect(
		named(page, `${manager}'s reports`).locator(`[aria-label="${agent}"]`),
	).toBeVisible();

	// (And it is udl's answer for both, not the page's.)
	for (const name of [agent, spare]) {
		await expect
			.poll(
				async () => {
					const found = (await call(page, "get_agent", { name })) as {
						agent: { manager: string | null };
					};
					return found.agent.manager;
				},
				{ timeout: 15_000 },
			)
			.toBe(manager);
	}

	// (Its own page says where it sits: one line up to its manager.)
	await page.goto(`/ui/p/orch/agents/${agent}`);
	await expect(named(page, "Session")).toBeVisible();
	await expect(page.locator(".udl-plugin-slot").first()).toContainText(`reports to ${manager}`);
});

test("says what each agent has left, from udl's own usage (29.6-usage)", async ({
	page,
}, testInfo) => {
	const { agent, spare } = await seed(page, testInfo.project.name);

	await page.goto("/ui/p/orch/agents");
	const row = named(page, spare);
	await expect(row).toBeVisible();

	// (27.14's line, as `orch_usage` answers it: a free model's requests left today, and when
	// they come back. It is read, not guessed: the page draws what udl says.)
	const said = (await call(page, "usage", { agent: spare })) as {
		items: { free: { allowance: number; left: number } | null }[];
	};
	const free = said.items[0]?.free ?? null;
	expect(free).not.toBeNull();
	await expect(
		row.getByText(`${String(free?.left)} of ${String(free?.allowance)} requests left`),
	).toBeVisible();

	// (An agent with nothing to measure — its runner's default model, no provider — says nothing
	// rather than a line about it. Its own row, not the tree under it: another spec drags agents
	// onto a manager, and a row's reports are drawn inside it.)
	await expect(named(page, agent).getByText(/requests left/)).toHaveCount(0);
});
