import { describe, expect, it } from "vitest";

import { formatApiError } from "./errorDisplay";
import { setLocale } from "../i18n";

describe("formatApiError", () => {
  it("explains unavailable task capacity without losing the support reference", () => {
    setLocale("zh");
    const message = formatApiError({ code: "agent_usage_limit", requestId: "req-capacity" }, "Failed", "请求编号");
    expect(message).toContain("待审批任务占用");
    expect(message).toContain("req-capacity");
    setLocale("en");
    expect(formatApiError({ code: "document_usage_limit" }, "Failed", "Request ID")).toContain("Document processing capacity");
  });
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
