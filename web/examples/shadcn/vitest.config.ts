import { defineConfig } from "vitest/config";

// 90 % coverage is the floor (the Definition of Done). Each package's tests are run from its
// own directory (`pnpm -C examples/shadcn test`), so the files are this package's.
export default defineConfig({
	root: import.meta.dirname,
	resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
	test: {
		environment: "jsdom",
		include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
		coverage: {
			provider: "v8",
			include: ["src/**/*.ts", "src/**/*.tsx"],
			exclude: ["src/**/*.test.ts", "src/**/*.test.tsx", "src/components/ui/**"],
			thresholds: { lines: 90, functions: 90, branches: 90, statements: 90 },
		},
	},
});
