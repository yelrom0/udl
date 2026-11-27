import { expect, type Page, test } from "@playwright/test";
import { SIGNED_IN } from "./helpers";

/**
 * 29.11's proof, in a real browser: the progress page draws the burn-up of a project's history,
 * its four numbers, each feature's share, the tasks merged lately and the hours a point has
 * cost each model — and the forecast on it is the date udl's own answer gives.
 *
 * The e2e server runs the real orchestrator plugin on a SQLite database with automations off, so
 * `orch_progress` here is the one a person reads: two sized tasks merged today, and one still
 * open, which is what the forecast is made from.
 */

test.use({ storageState: SIGNED_IN });

/** The page's own element, by its accessible name (`section[aria-label]`), in its shadow root. */
function named(page: Page, name: string) {
	return page.locator(`.udl-plugin-slot [aria-label="${name}"]`).first();
}

/** One of the plugin's operations, through the API, the way a page calls it. */
async function call(page: Page, name: string, body: unknown): Promise<unknown> {
	const answer = await page.request.post(`/plugins/orch/${name}`, {
		data: body,
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
	});
	expect(answer.ok(), `${name}: ${await answer.text()}`).toBe(true);
	return answer.json();
}

/** What this run seeded: its own feature, its merged tasks and the one still open. */
interface Seeded {
	readonly feature: string;
	readonly merged: readonly string[];
	readonly open: string;
}

/** A project, a feature with three sized tasks, two of them merged. */
async function seed(page: Page, run: string): Promise<Seeded> {
	// (A page first: a write says where it comes from, and that is the server's own address.)
	await page.goto("/ui/");
	const feature = `e2e-${run}`;
	const merged = [`${feature}.1`, `${feature}.2`];
	const open = `${feature}.3`;
	await call(page, "set_project", { name: "udl", title: "udl" });
	await call(page, "set_feature", {
		project: "udl",
		key: feature,
		title: "A feature worth measuring",
	});
	for (const [index, key] of merged.entries()) {
		await call(page, "set_task", {
			project: "udl",
			key,
			feature,
			title: `Merged ${index + 1}`,
			size: index === 0 ? "M" : "L",
		});
		// (A person may move a task anywhere: `merged` is the state the burn-up counts.)
		await call(page, "move_task", { project: "udl", key, to: "merged" });
	}
	await call(page, "set_task", {
		project: "udl",
		key: open,
		feature,
		title: "Still to do",
		size: "M",
	});
	return { feature, merged, open };
}

/** A day as the page writes it (`Sun 29 Nov`), from what udl's answer says. */
function dayLabel(iso: string): string {
	const months = [
		"Jan",
		"Feb",
		"Mar",
		"Apr",
		"May",
		"Jun",
		"Jul",
		"Aug",
		"Sep",
		"Oct",
		"Nov",
		"Dec",
	];
	const weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
	const day = new Date(iso);
	return `${weekdays[day.getUTCDay()]} ${day.getUTCDate()} ${months[day.getUTCMonth()]}`;
}

test("the progress page draws the burn-up, and the forecast udl gives (29.11)", async ({
	page,
}, testInfo) => {
	// (Its own feature: the two tests share one database, and a task already merged cannot merge
	// again.)
	const { feature, merged } = await seed(page, `${testInfo.project.name}-a`);
	const answer = (await call(page, "progress", { project: "udl" })) as {
		forecast: { likely: string };
		series: { merged: number }[];
		features: { key: string }[];
	};
	const likely = dayLabel(answer.forecast.likely);

	await page.goto("/ui/p/orch/progress");

	// (The burn-up: its lines, today, and the forecast's own date on the chart.)
	await expect(named(page, "The burn-up's lines")).toBeVisible();
	await expect(
		page.locator(`.udl-plugin-slot [aria-label="The burn-up's lines"]`).first().getByText(likely),
	).toBeVisible();
	// (The four numbers, and the forecast one is the answer's own day.)
	// (A stat is a fieldset, named by its legend.)
	const forecast = page.getByRole("group", { name: "Forecast" });
	await expect(forecast.getByText(likely)).toBeVisible();
	await expect(forecast.getByText(/points left/)).toBeVisible();
	// (Each feature's share, and the tasks merged lately.)
	// (Its own feature's row: the database holds every run's, so the key is what is asked for.)
	const row = named(page, "By feature").locator("li").filter({ hasText: feature });
	await expect(row.getByText("A feature worth measuring")).toBeVisible();
	const recent = named(page, "Recently merged");
	for (const key of merged) {
		await expect(recent.getByText(key, { exact: true })).toBeVisible();
	}
	// (And what a point costs the models udl knows.)
	await expect(named(page, "Hours per point")).toBeVisible();
});

test("the progress page keeps the project and the day in the URL (29.11)", async ({
	page,
}, testInfo) => {
	await seed(page, `${testInfo.project.name}-b`);

	await page.goto("/ui/p/orch/progress?project=udl");

	await expect(named(page, "Burn-up")).toBeVisible();
	await expect(page.getByLabel("Project")).toHaveValue("udl");
	// (Choosing a day writes it into the URL, which is the page's own state.)
	const since = page.getByLabel("Since");
	const choices = await since
		.locator("option")
		.evaluateAll((ones) =>
			ones.map((one) => (one as HTMLOptionElement).value).filter((value) => value !== ""),
		);
	await since.selectOption(choices[0] ?? "");
	await expect(page).toHaveURL(/since=\d{4}-\d{2}-\d{2}/);
	// (And the page draws again for that day: udl takes the date as the aware datetime it is.)
	await expect(named(page, "Burn-up")).toBeVisible();
	await expect(page.getByRole("group", { name: "Forecast" })).toBeVisible();
	await expect(page.locator(".text-error")).toHaveCount(0);
});
