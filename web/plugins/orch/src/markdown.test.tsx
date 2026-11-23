import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./markdown";

describe("<Markdown>", () => {
	it("draws headings, lists and code", () => {
		render(<Markdown text={"# Inbox\n\n- one\n- two\n\n`29.2`"} />);

		expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Inbox");
		expect(screen.getAllByRole("listitem")).toHaveLength(2);
		expect(screen.getByText("29.2").tagName).toBe("CODE");
	});

	it("opens its links in a new tab, safely", () => {
		render(<Markdown text={"[#78](https://github.com/yelrom0/udl/pull/78)"} />);

		const link = screen.getByRole("link", { name: "#78" });
		expect(link.getAttribute("href")).toBe("https://github.com/yelrom0/udl/pull/78");
		expect(link.getAttribute("target")).toBe("_blank");
		expect(link.getAttribute("rel")).toBe("noopener noreferrer");
	});

	it("sanitises what an agent wrote: no script, no handler, no inline style", () => {
		render(
			<Markdown
				text={
					'<script>alert(1)</script><img src="x" onerror="alert(2)"><p style="color:red">hi</p>'
				}
			/>,
		);

		expect(document.querySelector("script")).toBeNull();
		expect(document.querySelector("img")?.getAttribute("onerror")).toBeNull();
		expect(document.querySelector("p")?.getAttribute("style")).toBeNull();
	});
});
