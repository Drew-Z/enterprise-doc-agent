export interface ScheduledPartTask {
  partNumber: number;
  attempt: number;
  generation: number;
  run: () => Promise<void>;
}

export interface ScheduledPartOutcome {
  partNumber: number;
  attempt: number;
  generation: number;
  status: "fulfilled" | "rejected";
  error?: unknown;
}

export interface PartSchedulerOptions {
  maxConcurrency?: number;
  onSettled?: (outcome: ScheduledPartOutcome) => void;
}

export class DuplicatePartScheduleError extends Error {
  constructor(partNumber: number) {
    super(`Part ${partNumber} is already queued or active.`);
    this.name = "DuplicatePartScheduleError";
  }
}

export class PartUploadScheduler {
  private readonly maxConcurrency: number;
  private readonly queued: ScheduledPartTask[] = [];
  private readonly active = new Map<number, ScheduledPartTask>();
  private paused = false;

  constructor(private readonly options: PartSchedulerOptions = {}) {
    const maxConcurrency = options.maxConcurrency ?? 4;
    if (!Number.isSafeInteger(maxConcurrency) || maxConcurrency <= 0) {
      throw new RangeError("Upload scheduler concurrency must be a positive safe integer.");
    }
    this.maxConcurrency = maxConcurrency;
  }

  get activeCount(): number {
    return this.active.size;
  }

  get queuedCount(): number {
    return this.queued.length;
  }

  enqueue(task: ScheduledPartTask): void {
    const activeTask = this.active.get(task.partNumber);
    if (activeTask !== undefined && task.generation <= activeTask.generation) {
      throw new DuplicatePartScheduleError(task.partNumber);
    }
    const queuedIndex = this.queued.findIndex((queuedTask) => queuedTask.partNumber === task.partNumber);
    if (queuedIndex >= 0) {
      const queuedTask = this.queued[queuedIndex];
      if (queuedTask === undefined || task.generation <= queuedTask.generation) {
        throw new DuplicatePartScheduleError(task.partNumber);
      }
      this.queued[queuedIndex] = task;
    } else {
      this.queued.push(task);
    }
    this.pump();
  }

  pause(clearQueued = false): void {
    this.paused = true;
    if (clearQueued) {
      this.queued.length = 0;
    }
  }

  resume(): void {
    this.paused = false;
    this.pump();
  }

  clearQueued(): void {
    this.queued.length = 0;
  }

  hasPart(partNumber: number): boolean {
    return this.active.has(partNumber) || this.queued.some((task) => task.partNumber === partNumber);
  }

  private pump(): void {
    while (!this.paused && this.active.size < this.maxConcurrency) {
      const runnableIndex = this.queued.findIndex((task) => !this.active.has(task.partNumber));
      if (runnableIndex < 0) {
        return;
      }
      const [task] = this.queued.splice(runnableIndex, 1);
      if (task === undefined) {
        return;
      }
      this.active.set(task.partNumber, task);
      void Promise.resolve()
        .then(task.run)
        .then(
          () => {
            this.active.delete(task.partNumber);
            this.options.onSettled?.({
              partNumber: task.partNumber,
              attempt: task.attempt,
              generation: task.generation,
              status: "fulfilled",
            });
            this.pump();
          },
          (error: unknown) => {
            this.active.delete(task.partNumber);
            this.options.onSettled?.({
              partNumber: task.partNumber,
              attempt: task.attempt,
              generation: task.generation,
              status: "rejected",
              error,
            });
            this.pump();
          },
        )
        .catch(() => {
          // The success and failure handlers above consume task outcomes.
          this.active.delete(task.partNumber);
          this.pump();
        });
    }
  }
}
