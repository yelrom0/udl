import { expect, type Page, test } from "@playwright/test";

/**
 * 29.7's proof, in a real browser: a question asked while the page is open arrives on the Q&A
 * page, answering it there leaves, and the decision is on the task — read through
 * `orch_get_task`'s notes, and then seen on 29.5's task page, which draws them.
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

/** The project, a feature and a task: what the question is about. */
async function seeded(page: Page): Promise<void> {
	await call(page, "set_project", { name: "udl", title: "udl" });
	await call(page, "set_feature", { key: "29", title: "The web UI" });
	await call(page, "set_task", { key: "29.7", feature: "29", title: "Q&A" });
}

/** The question the test answers, asked with the API (as an agent or a person would). */
async function asked(page: Page): Promise<string> {
	const response = await page.request.post("/plugins/orch/ask", {
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
		data: {
			task: "29.7",
			title: "Should a question's answer be a note on the task?",
			text: "The decision belongs where the work is, so an agent that comes back to the task reads it.",
			options: ["A decision note", "Only on the question"],
			blocking: true,
		},
	});
	// (A retry finds the question its first try left open, and says so: either way there is one.)
	expect([200, 201, 409]).toContain(response.status());
	const found = response.ok() ? ((await response.json()) as { key?: string }) : null;
	return found?.key ?? "";
}

test("the Q&A page shows a question asked while it is open, answers it, and the decision is on the task (29.7)", async ({
	page,
}) => {
	await page.goto("/ui/");
	await seeded(page);

	// (Asked while the page is open: 28.5's `orch.question.asked` is what makes it appear.)
	await page.goto("/ui/p/orch/questions");
	await expect(page.getByRole("heading", { level: 1, name: "Q&A" })).toBeVisible();
	const key = await asked(page);

	const pinned = page.getByRole("region", { name: "Blocking" });
	await expect(pinned).toBeVisible();
	await expect(
		pinned.getByRole("link", { name: "Should a question's answer be a note on the task?" }),
	).toBeVisible();

	// (Answering it is one click, as on the inbox: the page reads again, so it goes at once.)
	await pinned.getByRole("button", { name: "A decision note" }).click();
	await expect(page.getByRole("region", { name: "Blocking" })).toHaveCount(0);

	// (And it is in the answered card, with what was decided: the row for this question, since a
	// retry's earlier question is answered too and has the same shape.)
	const answered = page.getByRole("region", { name: "Answered" });
	const row = answered.locator("li").filter({ has: page.getByRole("link", { name: key }) });
	await expect(row).toBeVisible();
	await expect(row.getByText("A decision note")).toBeVisible();

	// (udl has the answer, not just the page.)
	const found = (await call(page, "get_question", { key })) as {
		question?: { state?: string; answer?: string | null };
	};
	expect(found.question?.state).toBe("answered");
	expect(found.question?.answer).toBe("A decision note");

	// (A question that offers no options is answered in the box: the same `orch_answer`, with
	// what was typed.)
	const openResponse = await page.request.post("/plugins/orch/ask", {
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
		data: {
			task: "29.7",
			title: "Which colour should a blocking card be?",
			text: "Say what it should be.",
			options: [],
			blocking: false,
		},
	});
	const open = (await openResponse.json()) as { key?: string };
	expect([200, 201]).toContain(openResponse.status());
	await page.goto("/ui/p/orch/questions");
	const free = page.getByRole("region", { name: "Being asked" });
	const freeRow = free
		.locator("li")
		.filter({ has: page.getByRole("link", { name: open.key ?? "" }) });
	await freeRow.getByRole("link", { name: "Which colour should a blocking card be?" }).click();
	await page.getByLabel("Answer").fill("the blue one");
	await page.getByRole("button", { name: "Answer" }).click();
	await expect(page.getByText("the blue one")).toBeVisible();

	// (And the decision is on the task, where an agent that comes back to it will read it: both
	// as udl answers it and on 29.5's task page, which draws the task's notes.)
	const task = (await call(page, "get_task", { key: "29.7" })) as {
		notes?: { note_kind: string; text: string; author: string }[];
	};
	const decision = (task.notes ?? []).find((one) => one.note_kind === "decision");
	expect(decision, "a decision note on the task").toBeTruthy();
	expect(decision?.text).toContain("A decision note");

	await page.goto("/ui/p/orch/tasks/29.7");
	const notes = page.getByRole("list", { name: "Notes" });
	// (The note names the question it answers, so a retry's earlier question is not this one.)
	await expect(notes.getByText(`${key}, answered by`, { exact: false }).first()).toBeVisible();
	await expect(notes.getByText(/A decision note/).first()).toBeVisible();
});
