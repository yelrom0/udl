import { describe, expect, it } from "vitest";
import { router } from "./router";

describe("the router", () => {
	it("serves the page at /ui, where udl serves it", () => {
		expect(router.basepath).toBe("/ui");
	});

	it("knows the login page and every nav item's", () => {
		expect(router.routesById["/login"]).toBeTruthy();
		expect(router.routesById["/downloads/"]).toBeTruthy();
		expect(router.routesById["/p/$plugin/$"]).toBeTruthy();
	});
});
