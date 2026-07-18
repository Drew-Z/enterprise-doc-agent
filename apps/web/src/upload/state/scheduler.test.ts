import { describe, expect, it, vi } from "vitest";

import { DuplicatePartScheduleError, PartUploadScheduler } from "./scheduler";

function deferred(): { promise: Promise<void>; resolve: () => void; reject: (error: Error) => void } {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("PartUploadScheduler", () => {
  it("starts at most four parts and releases slots for every terminal outcome", async () => {
    const jobs = Array.from({ length: 10 }, deferred);
    const starts: number[] = [];
    const settled = vi.fn();
    const scheduler = new PartUploadScheduler({ onSettled: settled });
    jobs.forEach((job, index) => {
      scheduler.enqueue({ partNumber: index + 1, attempt: 1, generation: 1, run: () => { starts.push(index + 1); return job.promise; } });
    });
    await flush();
    expect(starts).toEqual([1, 2, 3, 4]);
    expect(scheduler.activeCount).toBe(4);
    expect(scheduler.queuedCount).toBe(6);

    jobs[0]?.resolve();
    await flush();
    expect(starts).toEqual([1, 2, 3, 4, 5]);

    jobs[1]?.reject(new Error("failed"));
    await flush();
    expect(starts).toEqual([1, 2, 3, 4, 5, 6]);
    expect(settled).toHaveBeenCalledWith(expect.objectContaining({ partNumber: 2, status: "rejected" }));
  });

  it("does not start queued work while paused", async () => {
    const scheduler = new PartUploadScheduler({ maxConcurrency: 1 });
    const run = vi.fn().mockResolvedValue(undefined);
    scheduler.pause();
    scheduler.enqueue({ partNumber: 1, attempt: 1, generation: 1, run });
    await flush();
    expect(run).not.toHaveBeenCalled();
    scheduler.resume();
    await flush();
    expect(run).toHaveBeenCalledOnce();
  });

  it("prevents the same part from being queued or active twice", () => {
    const scheduler = new PartUploadScheduler();
    scheduler.pause();
    scheduler.enqueue({ partNumber: 1, attempt: 1, generation: 1, run: () => Promise.resolve() });
    expect(() => scheduler.enqueue({ partNumber: 1, attempt: 2, generation: 1, run: () => Promise.resolve() })).toThrow(
      DuplicatePartScheduleError,
    );
  });

  it("queues a newer generation behind an aborting old task without concurrent duplicate work", async () => {
    const oldJob = deferred();
    const newRun = vi.fn().mockResolvedValue(undefined);
    const scheduler = new PartUploadScheduler({ maxConcurrency: 1 });
    scheduler.enqueue({ partNumber: 1, attempt: 1, generation: 1, run: () => oldJob.promise });
    await flush();
    scheduler.enqueue({ partNumber: 1, attempt: 1, generation: 2, run: newRun });
    await flush();
    expect(newRun).not.toHaveBeenCalled();
    oldJob.resolve();
    await flush();
    expect(newRun).toHaveBeenCalledOnce();
  });
});
