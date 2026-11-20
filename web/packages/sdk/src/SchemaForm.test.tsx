/**
 * ---------------------
 * - SchemaForm.test.tsx -
 * ---------------------
 *
 * `<SchemaForm>`: a form drawn from a JSON Schema — one control per field by its widget, nested
 * objects' own fields, the advanced ones collapsed, a field's error said at it, the config
 * file's fields not editable, a plugin's own widget used, and a box of JSON that keeps what is
 * half-typed.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type FormValue, SchemaForm } from "./SchemaForm";

const request = {
	type: "object",
	title: "Send",
	required: ["to", "subject"],
	properties: {
		to: { type: "string", title: "To", description: "Who it is for." },
		subject: { type: "string", title: "Subject" },
		body: { type: "string", title: "Body", "x-udl-ui": { widget: "textarea" } },
		draft: { type: "boolean", title: "Draft", default: false },
		importance: { type: "string", title: "Importance", enum: ["low", "high"] },
		wait: { type: "integer", title: "Wait" },
		options: {
			type: "object",
			title: "Options",
			properties: { label: { type: "string", title: "Label" } },
		},
		extra: { type: "object", title: "Extra" },
		headers: { type: "object", title: "Headers", "x-udl-ui": { advanced: true } },
	},
};

function draw(value: FormValue = {}, onChange = vi.fn()) {
	render(<SchemaForm schema={request} value={value} onChange={onChange} />);
	return onChange;
}

describe("two forms on one page", () => {
	it("labels each form's own field, not the other's", () => {
		// (A page can hold two forms with a field of the same name — a plugin's operations page
		// draws one per operation — so their ids must not collide.)
		const schema = {
			type: "object",
			properties: { account: { type: "string", title: "account" } },
		};

		render(
			<>
				<SchemaForm schema={schema} value={{}} onChange={() => undefined} />
				<SchemaForm schema={schema} value={{}} onChange={() => undefined} />
			</>,
		);

		const boxes = screen.getAllByLabelText("account");
		expect(boxes).toHaveLength(2);
		expect(new Set(boxes.map((box) => box.id)).size).toBe(2);
	});
});

describe("the fields", () => {
	it("are drawn one per property, with their titles", () => {
		draw();

		expect(screen.getByLabelText(/To/)).toBeDefined();
		expect(screen.getByLabelText(/Subject/)).toBeDefined();
		expect(screen.getByLabelText(/Importance/)).toBeDefined();
	});

	it("show their description, and a required one's mark", () => {
		draw();

		expect(screen.getByText("Who it is for.")).toBeDefined();
		// (The mark is not part of the label a screen reader reads: "Subject", not "Subject *".)
		expect(screen.getAllByText("*")).toHaveLength(2);
	});

	it("are their widget's: a textarea, a toggle, a select, a number, a box of JSON", () => {
		draw();

		expect(screen.getByLabelText(/Body/).tagName).toBe("TEXTAREA");
		expect(screen.getByLabelText(/Draft/).getAttribute("type")).toBe("checkbox");
		expect(screen.getByLabelText(/Importance/).tagName).toBe("SELECT");
		expect(screen.getByLabelText(/Wait/).getAttribute("type")).toBe("number");
		// (An object the schema says nothing about: a box of JSON.)
		expect(screen.getByLabelText(/Extra/).tagName).toBe("TEXTAREA");
	});
});

describe("what the user types", () => {
	it("is the whole value, with the field changed", () => {
		const onChange = draw({ to: "ada" });

		fireEvent.change(screen.getByLabelText(/Subject/), { target: { value: "Hello" } });

		expect(onChange).toHaveBeenCalledWith({ to: "ada", subject: "Hello" });
	});

	it("turns a toggle into true", () => {
		const onChange = draw();

		fireEvent.click(screen.getByLabelText(/Draft/));

		expect(onChange).toHaveBeenCalledWith({ draft: true });
	});

	it("is a number for a number, and taken out when it is emptied", () => {
		const onChange = draw({ wait: 3 });

		fireEvent.change(screen.getByLabelText(/Wait/), { target: { value: "5" } });
		expect(onChange).toHaveBeenCalledWith({ wait: 5 });

		// (Not null: a non-nullable field with a default refuses one.)
		fireEvent.change(screen.getByLabelText(/Wait/), { target: { value: "" } });
		expect(onChange).toHaveBeenCalledWith({});
	});

	it("is the choice's value, not its label", () => {
		const onChange = draw();

		fireEvent.change(screen.getByLabelText(/Importance/), { target: { value: "high" } });

		expect(onChange).toHaveBeenCalledWith({ importance: "high" });
	});
});

describe("a nested object", () => {
	it("is a fieldset of its own fields, which are set in place", () => {
		const onChange = draw({ to: "ada" });

		fireEvent.change(screen.getByLabelText(/Label/), { target: { value: "work" } });

		expect(onChange).toHaveBeenCalledWith({ to: "ada", options: { label: "work" } });
	});
});

describe("the advanced fields", () => {
	it("are in a section that starts closed", () => {
		draw();

		const section = screen.getByText("Advanced").closest("details");
		expect(section?.open).toBe(false);
	});
});

describe("a field's error", () => {
	it("is said at the field, as an alert", () => {
		render(
			<SchemaForm
				schema={request}
				value={{}}
				onChange={() => {}}
				errors={{ to: "A valid email address is required" }}
			/>,
		);

		expect(screen.getByRole("alert").textContent).toBe("A valid email address is required");
	});
});

describe("the config file's fields", () => {
	it("are shown, and not editable", () => {
		render(<SchemaForm schema={request} value={{}} onChange={() => {}} readOnly={["to"]} />);

		expect(screen.getByLabelText(/To/).hasAttribute("disabled")).toBe(true);
		expect(screen.getByText("set in the config file")).toBeDefined();
	});
});

describe("a secret", () => {
	it("is a password box, and never drawn back", () => {
		render(
			<SchemaForm
				schema={request}
				value={{ subject: "x" }}
				onChange={() => {}}
				secret={["subject"]}
			/>,
		);

		expect(screen.getByLabelText(/Subject/).getAttribute("type")).toBe("password");
	});

	it("never has the value in the DOM, even when the form holds one", () => {
		// (A password box bound to the value would put the secret in the page's DOM.)
		render(
			<SchemaForm
				schema={request}
				value={{ subject: "hunter2" }}
				onChange={() => {}}
				secret={["subject"]}
			/>,
		);

		const box = screen.getByLabelText(/Subject/) as HTMLInputElement;
		expect(box.value).toBe("");
		expect(document.body.innerHTML).not.toContain("hunter2");
	});

	it("shows what is typed into it, and gives it to the form", () => {
		const onChange = vi.fn();
		render(<SchemaForm schema={request} value={{}} onChange={onChange} secret={["subject"]} />);

		fireEvent.change(screen.getByLabelText(/Subject/), { target: { value: "s3cret" } });

		expect(onChange).toHaveBeenCalledWith({ subject: "s3cret" });
	});

	it("takes the secret out when what was typed is erased", () => {
		// (An empty string saved over a set secret would replace it with one.)
		const onChange = vi.fn();
		render(
			<SchemaForm
				schema={request}
				value={{ subject: "hunter2" }}
				onChange={onChange}
				secret={["subject"]}
			/>,
		);

		fireEvent.change(screen.getByLabelText(/Subject/), { target: { value: "x" } });
		fireEvent.change(screen.getByLabelText(/Subject/), { target: { value: "" } });

		expect(onChange).toHaveBeenLastCalledWith({});
	});
});

describe("a plugin's own widget", () => {
	it("draws the field in its place", () => {
		render(
			<SchemaForm
				schema={request}
				value={{}}
				onChange={() => {}}
				widgets={{ text: ({ field }) => <p>drawn by the plugin: {field.name}</p> }}
			/>,
		);

		expect(screen.getByText("drawn by the plugin: to")).toBeDefined();
	});

	it("draws the widget a field asks for by name, when it is not udl's own (24.7)", () => {
		render(
			<SchemaForm
				schema={{
					type: "object",
					properties: {
						tint: { type: "string", "x-udl-ui": { widget: "color", label: "Tint" } },
					},
				}}
				value={{}}
				onChange={() => {}}
				widgets={{ color: ({ field }) => <p>the plugin's colour box for {field.label}</p> }}
			/>,
		);

		expect(screen.getByText("the plugin's colour box for Tint")).toBeDefined();
		// (udl's own box is not drawn beside it.)
		expect(screen.queryByLabelText(/^Tint/)).toBeNull();
	});
});

describe("a box of JSON", () => {
	it("changes the value only once what is typed is JSON", () => {
		const onChange = vi.fn();
		render(<SchemaForm schema={request} value={{}} onChange={onChange} />);

		const box = screen.getByLabelText(/Extra/);
		fireEvent.change(box, { target: { value: '{"a": ' } });
		expect(onChange).not.toHaveBeenCalled();

		fireEvent.change(box, { target: { value: '{"a": 1}' } });
		expect(onChange).toHaveBeenCalledWith({ extra: { a: 1 } });
	});

	it("is taken out when it is emptied", () => {
		// (Null is what a non-nullable object field refuses.)
		const onChange = vi.fn();
		render(<SchemaForm schema={request} value={{ extra: { a: 1 } }} onChange={onChange} />);

		fireEvent.change(screen.getByLabelText(/Extra/), { target: { value: "" } });

		expect(onChange).toHaveBeenCalledWith({});
	});
});

describe("a multiselect", () => {
	// (As pydantic writes `list[Literal["a", "b", "c"]]`: the enum is the items', never the array's.)
	const schema = {
		type: "object",
		properties: {
			tags: { type: "array", title: "Tags", items: { enum: ["a", "b", "c"] } },
		},
	};

	it("is a set of choices, and the value is the ones chosen", () => {
		const onChange = vi.fn();
		render(<SchemaForm schema={schema} value={{ tags: ["a"] }} onChange={onChange} />);

		expect(screen.getByLabelText(/a/).getAttribute("type")).toBe("checkbox");
		fireEvent.click(screen.getByLabelText(/b/));

		expect(onChange).toHaveBeenCalledWith({ tags: ["a", "b"] });
	});

	it("takes one out when it is unticked", () => {
		const onChange = vi.fn();
		render(<SchemaForm schema={schema} value={{ tags: ["a", "b"] }} onChange={onChange} />);

		fireEvent.click(screen.getByLabelText(/a/));

		expect(onChange).toHaveBeenCalledWith({ tags: ["b"] });
	});

	it("starts with nothing chosen", () => {
		const onChange = vi.fn();
		render(<SchemaForm schema={schema} value={{}} onChange={onChange} />);

		fireEvent.click(screen.getByLabelText(/c/));

		expect(onChange).toHaveBeenCalledWith({ tags: ["c"] });
	});
});

describe("a markdown field", () => {
	it("is a textarea too", () => {
		render(
			<SchemaForm
				schema={{
					type: "object",
					properties: {
						text: { type: "string", title: "Text", "x-udl-ui": { widget: "markdown" } },
					},
				}}
				value={{}}
				onChange={() => {}}
			/>,
		);

		expect(screen.getByLabelText(/Text/).tagName).toBe("TEXTAREA");
	});
});

describe("a field with an error", () => {
	it("is marked as one", () => {
		render(
			<SchemaForm
				schema={request}
				value={{}}
				onChange={() => {}}
				errors={{ body: "Too long", importance: "Not one of them" }}
			/>,
		);

		expect(screen.getByLabelText(/Body/).className).toContain("textarea-error");
		expect(screen.getByLabelText(/Importance/).className).toContain("select-error");
	});
});

describe("a secret with no value", () => {
	it("says it is not set, and does not show one", () => {
		render(<SchemaForm schema={request} value={{}} onChange={() => {}} secret={["subject"]} />);

		const box = screen.getByLabelText(/Subject/);
		expect(box.getAttribute("placeholder")).toBe("(not set)");
		expect(box.getAttribute("value")).toBe("");
	});
});

describe("the form itself", () => {
	it("is the page's to submit: submitting it does nothing", () => {
		const onChange = vi.fn();
		render(<SchemaForm schema={request} value={{}} onChange={onChange} />);

		fireEvent.submit(screen.getByRole("form", { name: "Send" }));

		expect(onChange).not.toHaveBeenCalled();
	});
});

describe("a box of JSON that the value changes under", () => {
	it("draws the new value", () => {
		const { rerender } = render(
			<SchemaForm schema={request} value={{ extra: { a: 1 } }} onChange={() => {}} />,
		);

		rerender(<SchemaForm schema={request} value={{ extra: { b: 2 } }} onChange={() => {}} />);

		expect((screen.getByLabelText(/Extra/) as HTMLTextAreaElement).value).toBe(
			JSON.stringify({ b: 2 }, null, 2),
		);
	});
});

describe("a schema with references", () => {
	const document = {
		components: {
			schemas: {
				Address: { type: "object", properties: { street: { type: "string", title: "Street" } } },
			},
		},
	};
	const schema = {
		type: "object",
		properties: { address: { $ref: "#/components/schemas/Address", title: "Address" } },
	};

	it("is drawn with what its references point at", () => {
		render(<SchemaForm schema={schema} document={document} value={{}} onChange={() => {}} />);

		expect(screen.getByLabelText(/Street/)).toBeDefined();
	});
});

describe("a form that is disabled", () => {
	it("has nothing to change", () => {
		render(<SchemaForm schema={request} value={{}} onChange={() => {}} disabled />);

		expect(screen.getByLabelText(/Subject/).hasAttribute("disabled")).toBe(true);
		expect(screen.getByLabelText(/Draft/).hasAttribute("disabled")).toBe(true);
	});
});

describe("a field cleared", () => {
	it("is not sent at all, rather than as null", () => {
		// (Choosing "(none)" for a non-nullable field with a default would be a 422 as null.)
		const onChange = vi.fn();
		render(<SchemaForm schema={request} value={{ wait: 5 }} onChange={onChange} />);

		fireEvent.change(screen.getByLabelText(/Wait/), { target: { value: "" } });

		expect(onChange).toHaveBeenCalledWith({});
	});

	it("takes its choice away, leaving no empty string", () => {
		const onChange = vi.fn();
		render(<SchemaForm schema={request} value={{ importance: "low" }} onChange={onChange} />);

		fireEvent.change(screen.getByLabelText(/Importance/), { target: { value: "" } });

		expect(onChange).toHaveBeenCalledWith({});
	});
});
