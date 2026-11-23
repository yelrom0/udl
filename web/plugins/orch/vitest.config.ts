import { defineConfig } from "vitest/config";

// 90 % coverage is the floor (the Definition of Done). Each package's tests are run from its
// own directory (`pnpm -C plugins/orch test`), so the files are this package's.
export default defineConfig({
	root: import.meta.dirname,
	test: {
		environment: "jsdom",
		setupFiles: ["./src/test-setup.ts"],
		include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
		coverage: {
			provider: "v8",
			include: ["src/**/*.ts", "src/**/*.tsx"],
			exclude: ["src/**/*.test.ts", "src/**/*.test.tsx", "src/test-setup.ts", "src/testing/**"],
			thresholds: { lines: 90, functions: 90, branches: 90, statements: 90 },
		},
	},
});
