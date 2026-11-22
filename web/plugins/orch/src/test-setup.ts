/**
 * What every test of this plugin's pages needs: each test's page is its own. A test that renders
 * takes its page away again, so the next one queries a document it alone drew (the shell's own
 * `test-setup.ts` does the same, and jsdom has no `matchMedia` or `EventSource` of its own —
 * this plugin's pages use neither).
 */

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);
