import { describe, expect, it } from "vitest";

import { formatApiError } from "./errorDisplay";

describe("formatApiError", () => {
  it("keeps the user message and adds a stable code and request id", () => {
    const error = Object.assign(new Error("Access denied."), {
      code: "document_access_forbidden",
      requestId: "req-123",
    });

    expect(formatApiError(error, "Request failed.", "请求 ID")).toBe(
      "Access denied. (document_access_forbidden · 请求 ID: req-123)",
    );
  });

  it("uses the fallback without exposing malformed metadata", () => {
    expect(formatApiError({ code: 42, requestId: "" }, "Request failed.", "Request ID")).toBe("Request failed.");
  });
});
