import { describe, expect, it, vi } from "vitest";

import { uploadPartWithXhr, type XhrLike } from "./xhrUploadPart";

class FakeXhr implements XhrLike {
  readonly upload = { onprogress: null as ((event: ProgressEvent<EventTarget>) => void) | null };
  readonly headers: Array<[string, string]> = [];
  readonly abort = vi.fn(() => this.onabort?.());
  status = 0;
  timeout = 0;
  etag: string | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  open = vi.fn();
  send = vi.fn();

  setRequestHeader(name: string, value: string): void {
    this.headers.push([name, value]);
  }

  getResponseHeader(name: string): string | null {
    return name.toLowerCase() === "etag" ? this.etag : null;
  }
}

describe("uploadPartWithXhr", () => {
  it("copies only presign headers, reports progress, and preserves the opaque ETag", async () => {
    const xhr = new FakeXhr();
    const onProgress = vi.fn();
    const body = new Blob(["abc"]);
    const handle = uploadPartWithXhr({
      url: "http://object.test/signed",
      headers: { "x-amz-checksum-sha256": "checksum", "x-extra-signed": "value" },
      body,
      timeoutMs: 5_000,
      onProgress,
      xhrFactory: () => xhr,
    });

    xhr.upload.onprogress?.({ loaded: 99 } as ProgressEvent<EventTarget>);
    xhr.status = 200;
    xhr.etag = '"opaque-etag"';
    xhr.onload?.();

    await expect(handle.result).resolves.toEqual({ etag: '"opaque-etag"' });
    expect(xhr.open).toHaveBeenCalledWith("PUT", "http://object.test/signed", true);
    expect(xhr.headers).toEqual([
      ["x-amz-checksum-sha256", "checksum"],
      ["x-extra-signed", "value"],
    ]);
    expect(xhr.headers.some(([name]) => name.toLowerCase() === "authorization")).toBe(false);
    expect(xhr.timeout).toBe(5_000);
    expect(onProgress).toHaveBeenCalledWith(3, 3);
  });

  it.each([
    ["missing ETag", (xhr: FakeXhr) => { xhr.status = 200; xhr.onload?.(); }, "missing_etag"],
    ["HTTP rejection", (xhr: FakeXhr) => { xhr.status = 403; xhr.onload?.(); }, "http_error"],
    ["network failure", (xhr: FakeXhr) => xhr.onerror?.(), "network_error"],
    ["timeout", (xhr: FakeXhr) => xhr.ontimeout?.(), "timeout"],
  ])("maps %s to a typed failure", async (_name, trigger, code) => {
    const xhr = new FakeXhr();
    const handle = uploadPartWithXhr({ url: "http://object.test", headers: {}, body: new Blob(["a"]), xhrFactory: () => xhr });
    trigger(xhr);
    await expect(handle.result).rejects.toEqual(expect.objectContaining({ code }));
  });

  it("aborts immediately and settles once", async () => {
    const xhr = new FakeXhr();
    const handle = uploadPartWithXhr({ url: "http://object.test", headers: {}, body: new Blob(["a"]), xhrFactory: () => xhr });
    handle.abort();
    handle.abort();
    await expect(handle.result).rejects.toMatchObject({ code: "aborted" });
    expect(xhr.abort).toHaveBeenCalledOnce();
  });

  it.each([
    ["invalid URL", () => ({ url: "javascript:alert(1)" })],
    ["invalid timeout", () => ({ timeoutMs: -1 })],
  ])("maps %s to a setup failure before sending", async (_name, overrides) => {
    const xhr = new FakeXhr();
    const handle = uploadPartWithXhr({
      url: "http://object.test",
      headers: {},
      body: new Blob(["a"]),
      xhrFactory: () => xhr,
      ...overrides(),
    });
    await expect(handle.result).rejects.toMatchObject({ code: "setup_error" });
    expect(xhr.send).not.toHaveBeenCalled();
  });

  it.each(["open", "header", "send"] as const)("maps synchronous %s exceptions to typed failures", async (stage) => {
    const xhr = new FakeXhr();
    if (stage === "open") {
      xhr.open.mockImplementation(() => { throw new Error("open failed"); });
    } else if (stage === "header") {
      vi.spyOn(xhr, "setRequestHeader").mockImplementation(() => { throw new Error("header failed"); });
    } else {
      xhr.send.mockImplementation(() => { throw new Error("send failed"); });
    }
    const handle = uploadPartWithXhr({
      url: "http://object.test",
      headers: { "x-signed": "value" },
      body: new Blob(["a"]),
      xhrFactory: () => xhr,
    });
    await expect(handle.result).rejects.toMatchObject({
      code: stage === "send" ? "network_error" : "setup_error",
    });
    expect(xhr.abort).toHaveBeenCalledOnce();
  });

  it("honors pre-aborted and later-aborted signals", async () => {
    const preAborted = new AbortController();
    preAborted.abort();
    const firstXhr = new FakeXhr();
    const first = uploadPartWithXhr({
      url: "http://object.test",
      headers: {},
      body: new Blob(["a"]),
      signal: preAborted.signal,
      xhrFactory: () => firstXhr,
    });
    await expect(first.result).rejects.toMatchObject({ code: "aborted" });
    expect(firstXhr.send).not.toHaveBeenCalled();

    const controller = new AbortController();
    const secondXhr = new FakeXhr();
    const second = uploadPartWithXhr({
      url: "http://object.test",
      headers: {},
      body: new Blob(["a"]),
      signal: controller.signal,
      xhrFactory: () => secondXhr,
    });
    controller.abort();
    await expect(second.result).rejects.toMatchObject({ code: "aborted" });
    expect(secondXhr.abort).toHaveBeenCalledOnce();
  });

  it("turns a throwing progress callback into a typed abort", async () => {
    const xhr = new FakeXhr();
    const handle = uploadPartWithXhr({
      url: "http://object.test",
      headers: {},
      body: new Blob(["a"]),
      onProgress: () => { throw new Error("render failed"); },
      xhrFactory: () => xhr,
    });
    xhr.upload.onprogress?.({ loaded: 1 } as ProgressEvent<EventTarget>);
    await expect(handle.result).rejects.toMatchObject({ code: "progress_callback_error" });
    expect(xhr.abort).toHaveBeenCalledOnce();
  });
});
