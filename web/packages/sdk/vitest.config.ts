import { defineConfig } from "vitest/config";

// 90 % coverage is the floor (the Definition of Done). The generated manifest types have no
// statements to cover, and the test setup is not the package's, so they are out of it.
export default defineConfig({
	root: import.meta.dirname,
	test: {
		environment: "jsdom",
		setupFiles: ["./src/test-setup.ts"],
		include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
		coverage: {
			provider: "v8",
			include: ["src/**/*.ts", "src/**/*.tsx"],
			exclude: ["src/**/*.test.ts", "src/**/*.test.tsx", "src/test-setup.ts", "src/manifest.ts"],
			thresholds: { lines: 90, functions: 90, branches: 90, statements: 90 },
		},
	},
});
