/**
 * What the component tests need: a page that starts empty. Each test renders its own form, and
 * the one before it is taken away — as the shell's own setup does.
 */

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);
