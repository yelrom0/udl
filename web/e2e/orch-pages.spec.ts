import { expect, type Page, test } from "@playwright/test";
import { SIGNED_IN } from "./helpers";

/**
 * 29.5's proof, in a real browser: a feature's page and a task's page draw what udl holds of
 * them, every key on them is a link, and a task's page reaches its feature and back.
 *
 * The e2e server runs the real orchestrator plugin on a SQLite database with automations off, so
 * what these pages read is what the operations answer — a feature with its spec, its tasks, a
 * note and a link, and a task with its spec, its state and its own note.
 */

test.use({ storageState: SIGNED_IN });

/** One of the plugin's elements, by its accessible name, in its shadow root. */
function named(page: Page, name: string) {
	return page.locator(`.udl-plugin-slot [aria-label="${name}"]`).first();
}

/** One of the plugin's operations, through the API, the way a page calls it. */
async function call(page: Page, name: string, body: unknown): Promise<void> {
	const answer = await page.request.post(`/plugins/orch/${name}`, {
		data: body,
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
	});
	expect(answer.ok(), `${name}: ${await answer.text()}`).toBe(true);
}

/** What this run seeded: its own feature and tasks, so light and dark do not find each other's. */
interface Seeded {
	readonly feature: string;
	readonly first: string;
	readonly second: string;
}

/** A feature with a spec, two tasks, a note and a pull, and a note on the first task. */
async function seed(page: Page, run: string): Promise<Seeded> {
	await page.goto("/ui/");
	const feature = `e2e-${run}`;
	const first = `${feature}.1`;
	const second = `${feature}.2`;
	await call(page, "set_project", { name: "udl", title: "udl" });
	await call(page, "set_feature", {
		project: "udl",
		key: feature,
		title: "Screens",
		spec: "## What it is\n\nThe screens.",
	});
	await call(page, "set_task", {
		project: "udl",
		key: first,
		feature,
		title: "The first thing",
		spec: "## How it works\n\nBy asking udl.",
		size: "M",
	});
	await call(page, "set_task", {
		project: "udl",
		key: second,
		feature,
		title: "The second thing",
		needs: [first],
	});
	await call(page, "add_note", {
		project: "udl",
		owner_kind: "feature",
		owner: feature,
		note_kind: "decision",
		text: "The feature's own note.",
	});
	await call(page, "add_note", {
		project: "udl",
		owner_kind: "task",
		owner: first,
		note_kind: "progress",
		text: "The task's own note.",
	});
	await call(page, "add_link", {
		project: "udl",
		owner_kind: "task",
		owner: first,
		link_kind: "pull",
		url: "https://github.com/yelrom0/udl/pull/99",
	});
	return { feature, first, second };
}

test("a feature's page draws its tasks, and a task's page reaches it and back (29.5)", async ({
	page,
}, testInfo) => {
	const { feature, first, second } = await seed(page, testInfo.project.name);

	await page.goto(`/ui/p/orch/features/${feature}`);

	// (Its own heading, its title, and its spec as markdown.)
	await expect
		.poll(
			async () =>
				page
					.locator(".udl-plugin-slot")
					.evaluate((host) => host.shadowRoot?.querySelector("h1")?.textContent ?? ""),
			{ timeout: 15_000 },
		)
		.toBe(`Feature ${feature}`);
	await expect(named(page, "Spec").getByRole("heading", { name: "What it is" })).toBeVisible();
	await expect(named(page, "Spec").getByRole("button", { name: "Edit" })).toBeVisible();

	// (Its tasks, each key a link to that task's page.)
	const tasks = named(page, "Tasks");
	await expect(tasks.getByRole("link", { name: first })).toHaveAttribute(
		"href",
		`/ui/p/orch/tasks/${first}`,
	);
	await expect(tasks.getByRole("link", { name: second })).toBeVisible();
	// (Its own note, and what points at it.)
	await expect(named(page, "Notes").getByText("The feature's own note.")).toBeVisible();

	// (A task's page, from the feature's own link.)
	await tasks.getByRole("link", { name: first }).click();
	await expect
		.poll(
			async () =>
				page
					.locator(".udl-plugin-slot")
					.evaluate((host) => host.shadowRoot?.querySelector("h1")?.textContent ?? ""),
			{ timeout: 15_000 },
		)
		.toBe(`Task ${first}`);

	// (Its spec, its own note — the timeline 29.7's proof reads — and its pull.)
	await expect(named(page, "Spec").getByRole("heading", { name: "How it works" })).toBeVisible();
	await expect(named(page, "Notes").getByText("The task's own note.")).toBeVisible();
	await expect(named(page, "Pull").getByRole("link", { name: /#99/ })).toBeVisible();
	// (Its state, with a move a person may make.)
	await expect(named(page, "State").getByRole("combobox", { name: "Move to" })).toBeVisible();

	// (And back: the breadcrumb names its feature, and goes there.)
	const back = page.locator(".udl-plugin-slot nav[aria-label='Breadcrumb'] a").first();
	await expect(back).toHaveAttribute("href", `/ui/p/orch/features/${feature}`);
	await expect(back).toContainText("Screens");
	await back.click();
	await expect
		.poll(
			async () =>
				page
					.locator(".udl-plugin-slot")
					.evaluate((host) => host.shadowRoot?.querySelector("h1")?.textContent ?? ""),
			{ timeout: 15_000 },
		)
		.toBe(`Feature ${feature}`);
});

test("a task's page moves it, and says so when it comes back (29.5)", async ({
	page,
}, testInfo) => {
	const { first } = await seed(page, testInfo.project.name);

	await page.goto(`/ui/p/orch/tasks/${first}`);
	await expect(named(page, "State").getByRole("combobox", { name: "Move to" })).toBeVisible();

	await named(page, "State").getByRole("combobox", { name: "Move to" }).selectOption("blocked");
	await named(page, "State").getByRole("button", { name: "Move" }).click();

	// (udl's own answer, read back: the task is blocked now, and the page says so.)
	await expect(named(page, "State").getByText("blocked")).toBeVisible({ timeout: 15_000 });
});
