import { defineConfig } from "vitest/config";

// 90 % coverage is the floor (the Definition of Done). This package's tests run from its own
// directory (`pnpm -C packages/api test`), so the files are this package's; the schema and the
// operations map are generated, so they are not counted.
export default defineConfig({
	root: import.meta.dirname,
	test: {
		environment: "node",
		include: ["src/**/*.test.ts"],
		coverage: {
			provider: "v8",
			include: ["src/**/*.ts"],
			exclude: ["src/**/*.test.ts", "src/schema.d.ts", "src/operations.ts"],
			thresholds: { lines: 90, functions: 90, branches: 90, statements: 90 },
		},
	},
});
