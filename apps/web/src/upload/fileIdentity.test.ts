import { describe, expect, it } from "vitest";

import { compareFileMetadata, compareHashedFileIdentity } from "./fileIdentity";

const expected = {
  filename: "contract.pdf",
  sizeBytes: 5,
  declaredSha256: "a".repeat(64),
};

describe("file identity", () => {
  it.each([
    [new File(["12345"], "other.pdf"), "filename"],
    [new File(["1234"], "contract.pdf"), "size"],
    [new File(["12345"], "contract.pdf"), null],
  ])("checks filename and size before hashing", (file, mismatch) => {
    expect(compareFileMetadata(expected, file)).toBe(mismatch);
  });

  it.each([
    [{ ...expected, filename: "other.pdf" }, "filename"],
    [{ ...expected, sizeBytes: 4 }, "size"],
    [{ ...expected, declaredSha256: "b".repeat(64) }, "sha256"],
    [expected, null],
  ])("checks all identity fields after hashing", (actual, mismatch) => {
    expect(compareHashedFileIdentity(expected, actual)).toBe(mismatch);
  });
});
