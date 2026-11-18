/**
 * -------------------
 * - ResultView.test.tsx -
 * -------------------
 *
 * `<ResultView>`: a response as the generated UI shows it — `list_accounts`' and `list_events`'
 * as sortable, paged tables (booleans badges, URLs links, datetimes said as when), a flat object
 * as a description list, and anything else as JSON. What the API sends is text, never HTML.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ResultView } from "./ResultView";

const accountInfo = {
	type: "object",
	required: ["account", "enabled"],
	properties: {
		account: { type: "string", title: "Account" },
		enabled: { type: "boolean", title: "Enabled" },
		homepage: { type: "string", format: "uri", title: "Homepage" },
		created_at: { type: "string", format: "date-time", title: "Created At" },
	},
};

const accountList = {
	type: "object",
	properties: {
		accounts: { type: "array", title: "Accounts", items: accountInfo },
	},
};

const accounts = [
	{
		account: "gmail:work",
		enabled: true,
		homepage: "https://mail.example.com",
		created_at: "2026-11-19T00:00:00Z",
	},
	{
		account: "gmail:home",
		enabled: false,
		homepage: "https://example.com",
		created_at: "2026-11-18T00:00:00Z",
	},
];

const eventList = {
	type: "object",
	properties: {
		events: {
			type: "array",
			title: "Events",
			items: {
				type: "object",
				properties: {
					id: { type: "string", title: "Id" },
					depth: { type: "integer", title: "Depth" },
					account_id: { type: "string", title: "Account" },
				},
			},
		},
	},
};

const events = [
	{ id: "e1", depth: 0, account_id: "a1" },
	{ id: "e2", depth: 2, account_id: "a2" },
	{ id: "e3", depth: 1, account_id: "a1" },
];

function table(): HTMLElement {
	return screen.getByRole("table");
}

function cells(row: HTMLElement): readonly string[] {
	return within(row)
		.getAllByRole("cell")
		.map((cell) => cell.textContent ?? "");
}

describe("list_accounts' response", () => {
	it("is a table, named by its list, with its columns", () => {
		render(<ResultView schema={accountList} value={{ accounts }} />);

		expect(screen.getByText("Accounts")).toBeDefined();
		const headers = within(table()).getAllByRole("columnheader");
		expect(headers.map((header) => header.textContent)).toEqual([
			"Account",
			"Enabled",
			"Homepage",
			"Created At",
		]);
	});

	it("says a boolean as a badge", () => {
		render(<ResultView schema={accountList} value={{ accounts }} />);

		const rows = within(table()).getAllByRole("row").slice(1);
		expect(within(rows[0] as HTMLElement).getByText("yes").className).toContain("badge");
		expect(within(rows[1] as HTMLElement).getByText("no").className).toContain("badge");
	});

	it("says a URL as a link, and a datetime as when it was", () => {
		render(<ResultView schema={accountList} value={{ accounts }} />);

		const link = screen.getByRole("link", { name: "https://mail.example.com" });
		expect(link.getAttribute("href")).toBe("https://mail.example.com");
		expect(screen.getAllByRole("time")).toHaveLength(2);
	});
});

describe("list_events' response", () => {
	it("is a table of the events", () => {
		render(<ResultView schema={eventList} value={{ events }} />);

		expect(screen.getByText("Events")).toBeDefined();
		const rows = within(table()).getAllByRole("row").slice(1);
		expect(rows).toHaveLength(3);
		expect(cells(rows[0] as HTMLElement)).toEqual(["e1", "0", "a1"]);
	});
});

describe("sorting", () => {
	it("is by a column, its header clicked, and clicked again the other way", () => {
		render(<ResultView schema={eventList} value={{ events }} />);

		fireEvent.click(screen.getByRole("button", { name: /Id/ }));
		const first = within(table()).getAllByRole("row").slice(1);
		expect(cells(first[0] as HTMLElement)[0]).toBe("e1");

		fireEvent.click(screen.getByRole("button", { name: /Id/ }));
		const again = within(table()).getAllByRole("row").slice(1);
		expect(cells(again[0] as HTMLElement)[0]).toBe("e3");
	});

	it("is by a number as numbers are", () => {
		render(<ResultView schema={eventList} value={{ events }} />);

		fireEvent.click(screen.getByRole("button", { name: /Depth/ }));
		const rows = within(table()).getAllByRole("row").slice(1);
		expect(rows.map((row) => cells(row as HTMLElement)[1])).toEqual(["0", "1", "2"]);
	});
});

describe("paging", () => {
	const many = Array.from({ length: 30 }, (_, index) => ({
		id: `e${String(index + 1).padStart(2, "0")}`,
		depth: index,
		account_id: "a1",
	}));

	it("shows one page, and the next one when asked", () => {
		render(<ResultView schema={eventList} value={{ events: many }} />);

		expect(within(table()).getAllByRole("row")).toHaveLength(26);
		fireEvent.click(screen.getByRole("button", { name: "Next" }));

		const rows = within(table()).getAllByRole("row").slice(1);
		expect(cells(rows[0] as HTMLElement)[0]).toBe("e26");
	});

	it("is not there for a list that fits", () => {
		render(<ResultView schema={eventList} value={{ events }} />);

		expect(screen.queryByRole("navigation", { name: "Pages" })).toBeNull();
	});
});

describe("an empty list", () => {
	it("says there is nothing", () => {
		render(<ResultView schema={eventList} value={{ events: [] }} />);

		expect(screen.getByText("Nothing yet.")).toBeDefined();
		expect(screen.queryByRole("table")).toBeNull();
	});
});

describe("a flat object", () => {
	it("is a description list of its fields", () => {
		const schema = {
			type: "object",
			properties: {
				version: { type: "string", title: "Version" },
				healthy: { type: "boolean", title: "Healthy" },
			},
		};

		render(<ResultView schema={schema} value={{ version: "1.2.3", healthy: true }} />);

		const [version] = screen.getAllByRole("definition");
		expect(version?.textContent).toBe("1.2.3");
		expect(screen.getByText("Healthy")).toBeDefined();
	});
});

describe("a value that is not what the schema said", () => {
	it("is JSON, and nothing is a dash", () => {
		const schema = {
			type: "array",
			items: {
				type: "object",
				properties: {
					note: { type: "string", title: "Note" },
					when: { type: "string", format: "date-time", title: "When" },
				},
			},
		};

		render(<ResultView schema={schema} value={[{ note: { a: 1 }, when: null }]} />);

		expect(screen.getByText(/"a": 1/)).toBeDefined();
		expect(screen.getByText("—")).toBeDefined();
	});
});

describe("sorting with nothing in a cell", () => {
	it("puts it last, whichever way the column is sorted", () => {
		const schema = {
			type: "array",
			items: {
				type: "object",
				properties: { name: { type: "string", title: "Name" } },
			},
		};

		render(<ResultView schema={schema} value={[{ name: "b" }, { name: null }, { name: "a" }]} />);

		fireEvent.click(screen.getByRole("button", { name: /Name/ }));
		const ascending = within(table())
			.getAllByRole("row")
			.slice(1)
			.map((row) => cells(row as HTMLElement)[0]);
		expect(ascending).toEqual(["a", "b", "—"]);

		fireEvent.click(screen.getByRole("button", { name: /Name/ }));
		const descending = within(table())
			.getAllByRole("row")
			.slice(1)
			.map((row) => cells(row as HTMLElement)[0]);
		expect(descending).toEqual(["b", "a", "—"]);
	});
});

describe("a nested value", () => {
	it("is JSON, as is a value the schema does not name", () => {
		render(
			<ResultView
				schema={{ type: "object", properties: { data: { type: "object" } } }}
				value={{ data: { a: 1 } }}
			/>,
		);

		expect(screen.getByText(/"a": 1/)).toBeDefined();
	});
});

describe("what the API sends", () => {
	it("is text, never HTML", () => {
		const schema = {
			type: "array",
			items: { type: "object", properties: { name: { type: "string", title: "Name" } } },
		};
		const { container } = render(
			<ResultView schema={schema} value={[{ name: "<img src=x onerror=alert(1)>" }]} />,
		);

		expect(container.querySelector("img")).toBeNull();
		expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeDefined();
	});

	it("is a link only for what the schema says is a URL", () => {
		const schema = {
			type: "array",
			items: {
				type: "object",
				properties: { note: { type: "string", title: "Note" } },
			},
		};

		render(<ResultView schema={schema} value={[{ note: "javascript:alert(1)" }]} />);

		expect(screen.queryByRole("link")).toBeNull();
	});
});
