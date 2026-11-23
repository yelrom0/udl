import { expect, type Page, test } from "@playwright/test";
import { SIGNED_IN } from "./helpers";

/**
 * 29.4's proof, in a real browser: the board draws a project's features, its agents and what is
 * suggested, and a suggestion dragged onto an agent's column is assigned to it — udl's own
 * answer, read back by the page.
 *
 * The e2e server runs the real orchestrator plugin on a SQLite database with automations off
 * (no dispatcher), so what a drop does here is what `orch_assign` does: the task lands on that
 * agent's queue, `queued`, and the board is read again to show it. The dispatcher is what moves
 * it on to `in_progress`, and it is not running in this harness.
 */

test.use({ storageState: SIGNED_IN });

/** The board's own element, by its accessible name (`section[aria-label]`), in its shadow root. */
function named(page: Page, name: string) {
	return page.locator(`.udl-plugin-slot [aria-label="${name}"]`).first();
}

/** One of the plugin's operations, through the API, the way the page calls it. */
async function call(page: Page, name: string, body: unknown): Promise<void> {
	const answer = await page.request.post(`/plugins/orch/${name}`, {
		data: body,
		// (A write with a session's cookie says where it comes from, as a browser's own does.)
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
	});
	expect(answer.ok(), `${name}: ${await answer.text()}`).toBe(true);
}

/** What this run seeded: its own feature, agent and task, so the light and dark runs — one
 * server, one database — do not find each other's already assigned. */
interface Seeded {
	readonly agent: string;
	readonly task: string;
}

/** A project, a feature, an agent and one task nothing waits on: enough for a suggestion. */
async function seed(page: Page, run: string): Promise<Seeded> {
	// (A page first: a write says where it comes from, and that is the server's own address.)
	await page.goto("/ui/");
	const feature = `e2e-${run}`;
	const agent = `fake-${run}`;
	const task = `${feature}.1`;
	// (The one project the e2e server's other specs seed too: a second one would make "the only
	// project" ambiguous for every operation that leaves it out.)
	await call(page, "set_project", { name: "udl", title: "udl" });
	await call(page, "set_feature", { project: "udl", key: feature, title: "Screens" });
	await call(page, "set_agent", {
		name: agent,
		title: "Fake",
		runner: "claude-code",
		role: "implementer",
		projects: ["udl"],
	});
	await call(page, "set_task", {
		project: "udl",
		key: task,
		feature,
		title: "A thing worth doing",
	});
	return { agent, task };
}

test("a suggestion dragged onto an agent is assigned to it (29.4)", async ({ page }, testInfo) => {
	const { agent, task } = await seed(page, testInfo.project.name);

	await page.goto("/ui/p/orch/board");

	// (Its three panes: the feature with its bar, the agent's column, and what is suggested.)
	await expect(named(page, "Features")).toBeVisible();
	await expect(named(page, agent)).toBeVisible();
	await expect(named(page, "Suggested")).toBeVisible();
	await expect(named(page, "Suggested").getByText("A thing worth doing")).toBeVisible();

	// (A drag carries the suggestion; the column is what takes it.)
	await named(page, `Suggested: ${task} A thing worth doing`).dispatchEvent("dragstart");
	await named(page, agent).dispatchEvent("drop");

	// (The board is read again, and the queue it draws is udl's: the task is the agent's now.)
	await expect(named(page, agent).locator(`[data-task="${task}"]`)).toBeVisible({
		timeout: 15_000,
	});
	await expect(named(page, `${task} A thing worth doing`)).toContainText("queued");
});

test("the board's filters are in the URL, and the page is given them (29.4)", async ({
	page,
}, testInfo) => {
	await seed(page, testInfo.project.name);

	await page.goto("/ui/p/orch/board?text=nothing-like-this");
	// (The text filter is the query's: the page draws nothing that matches it, and says so.)
	await expect(page.locator(".udl-plugin-slot").getByText("Nothing matches that.")).toBeVisible();
	// (And the box shows what the URL said, so the page is the one someone was sent.)
	await expect(page.locator('.udl-plugin-slot input[aria-label="Text"]')).toHaveValue(
		"nothing-like-this",
	);
});
