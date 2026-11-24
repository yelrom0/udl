import { expect, type Page, test } from "@playwright/test";

/**
 * The drawer's question count (29.7's follow-up): the Q&A entry carries how many questions are
 * still open, and it moves as one is asked and as it is answered. It asks on a task of its own
 * (29.8), so it never answers another spec's question — the e2e specs share one udl.
 */

/** A write from outside the page: the session's cookie, the CSRF header and the page's origin. */
async function call(page: Page, name: string, body: object): Promise<unknown> {
	const response = await post(page, name, body);
	expect(response.ok(), `${name} answered ${response.status()}: ${await response.text()}`).toBe(
		true,
	);
	return await response.json();
}

/** The POST itself: the CSRF header and the page's origin, which every write here needs. */
function post(page: Page, name: string, body: object) {
	return page.request.post(`/plugins/orch/${name}`, {
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
		data: body,
	});
}

/** A project, a feature and a task of this spec's own: what the question is asked in. (The e2e
 * specs share one udl, so a task of its own keeps this one from answering another spec's
 * question.) */
async function seeded(page: Page): Promise<void> {
	await call(page, "set_project", { name: "udl", title: "udl" });
	await call(page, "set_feature", { key: "29", title: "The web UI" });
	await call(page, "set_task", { key: "29.8", feature: "29", title: "Ready to merge" });
}

/** What the Q&A entry's badge counts: 0 when it carries none (nothing is waiting). */
async function badge(page: Page): Promise<number> {
	const link = page.getByRole("link", { name: "Q&A" });
	await expect(link).toBeVisible();
	const found = link.getByTestId("nav-count");
	if ((await found.count()) === 0) {
		return 0;
	}
	// (Its text is the number and what it counts, for a screen reader: read the number.)
	const said = ((await found.first()?.textContent()) ?? "").trim().split(/\s/)[0] ?? "";
	return Number.parseInt(said, 10);
}

async function ask(page: Page, title: string): Promise<string> {
	// (A fresh question each run: the title is the run's own, so a retry asks its own again.)
	const found = (await call(page, "ask", {
		task: "29.8",
		title,
		text: "",
		options: [],
		blocking: false,
	})) as { key?: string };
	expect(found.key, "the question has a key").toBeTruthy();
	return found.key ?? "";
}

test("the nav counts the open questions, up when one is asked and down when it is answered", async ({
	page,
}) => {
	await page.goto("/ui/");
	await seeded(page);

	// (The count comes from the inbox, which reads on 28.5's question events.)
	await page.goto("/ui/p/orch/inbox");
	const first = await badge(page);

	const key = await ask(page, "Should the nav count blocking questions too?");
	await expect.poll(async () => await badge(page)).toBe(first + 1);

	// (And answering **that** question takes it down again: the inbox re-reads on
	// `orch.question.answered`.)
	await call(page, "answer", { key, answer: "Yes, count them" });

	await expect.poll(async () => await badge(page)).toBe(first);
});
