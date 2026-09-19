import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";

import { configureAuthentication } from "../auth/transport";

// Existing component fixtures explicitly exercise the supported machine-token mode.
beforeEach(() => configureAuthentication("bearer"));
