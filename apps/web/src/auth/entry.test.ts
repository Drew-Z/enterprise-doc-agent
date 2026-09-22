import { expect, it } from "vitest";
import { consumeAuthEntry } from "./entry";

it("accepts only fixed sign-in error reasons and consumes them once", () => {
  window.history.replaceState(null, "", "/#/signin?error=github_email_required");
  expect(consumeAuthEntry().signInError).toBe("github_email_required");
  expect(window.location.hash).toBe("");
  expect(consumeAuthEntry().signInError).toBeNull();
  window.history.replaceState(null, "", "/#/signin?error=upstream-secret");
  expect(consumeAuthEntry().signInError).toBeNull();
  window.history.replaceState(null, "", "/");
});
