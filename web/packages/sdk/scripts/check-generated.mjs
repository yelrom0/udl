// Checks that src/manifest.ts is what src/manifest.schema.json makes: the schema comes from udl's
// model (`python -m udl.plugins.ui --schema`) and the type from the schema
// (`pnpm --filter @udl/ui-sdk run manifest`), so a stale one is a plugin writing against types
// the shell does not have. It runs where the API's `check-generated` does: `pnpm -C web check`.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const here = (name) => fileURLToPath(new URL(name, import.meta.url));
const SCHEMA = here("../src/manifest.schema.json");
const TYPES = here("../src/manifest.ts");

const scratch = mkdtempSync(join(tmpdir(), "udl-ui-sdk-"));
const made = join(scratch, "manifest.ts");
let fresh;
try {
	execFileSync("json2ts", [SCHEMA, "--output", made], { stdio: "inherit" });
	fresh = readFileSync(made, "utf8");
} finally {
	rmSync(scratch, { recursive: true, force: true });
}

if (fresh !== readFileSync(TYPES, "utf8")) {
	console.error(
		`${TYPES} is out of date: run \`pnpm -C web --filter @udl/ui-sdk run manifest\` ` +
			"(and `python -m udl.plugins.ui --schema` first if the model changed).",
	);
	process.exit(1);
}
