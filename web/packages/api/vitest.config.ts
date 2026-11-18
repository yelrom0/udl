import { defineConfig } from "vitest/config";

// 90 % coverage is the floor (the Definition of Done). This package's tests run from its own
// directory (`pnpm -C packages/api test`), so the files are this package's; the generated ones
// (the schema, the operations map and the fixtures) are not counted: `generate.mjs --check` says
// whether they are current.
export default defineConfig({
	root: import.meta.dirname,
	test: {
		environment: "node",
		include: ["src/**/*.test.ts"],
		coverage: {
			provider: "v8",
			include: ["src/**/*.ts"],
			exclude: ["src/**/*.test.ts", "src/schema.d.ts", "src/operations.ts", "src/fixtures.ts"],
			thresholds: { lines: 90, functions: 90, branches: 90, statements: 90 },
		},
	},
});
