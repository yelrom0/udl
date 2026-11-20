/**
 * -------------------
 * - generated.test.ts -
 * -------------------
 *
 * The store the generated pages read: what udl answered, the error when it answered nothing
 * usable, and the plugin a page is for. A page whose plugin is not in the list says so; it is
 * never an exception out of a route.
 */

import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { GeneratedPlugin } from "../generated/spec";
import { generated, pluginFor, setGenerated, useGeneratedPlugin } from "./generated";

const PLUGIN: GeneratedPlugin = {
	name: "gmail",
	kind: "udl.integrations",
	title: "Gmail",
	summary: "",
	has_ui_plugin: false,
	operations: [],
	settings: null,
	accounts: null,
	event_types: [],
};

beforeEach(() => {
	generated.setState({ ui: null, error: null });
});

describe("the generated UI's store", () => {
	it("starts with nothing, and no error", () => {
		expect(generated.getState().ui).toBeNull();
		expect(generated.getState().error).toBeNull();
	});

	it("keeps what udl answered", () => {
		setGenerated({ plugins: [PLUGIN] }, null);

		expect(pluginFor({ plugins: [PLUGIN] }, "gmail")).toEqual(PLUGIN);
		expect(renderHook(() => useGeneratedPlugin("gmail")).result.current).toEqual(PLUGIN);
		expect(renderHook(() => useGeneratedPlugin("slack")).result.current).toBeUndefined();
	});

	it("keeps why it has none, when udl answered nothing usable", () => {
		setGenerated(null, "udl answered 500");

		expect(generated.getState().ui).toBeNull();
		expect(generated.getState().error).toBe("udl answered 500");
	});

	it("says a plugin is not there rather than throwing", () => {
		setGenerated({ plugins: [] }, null);

		expect(renderHook(() => useGeneratedPlugin("gmail")).result.current).toBeUndefined();
	});
});
