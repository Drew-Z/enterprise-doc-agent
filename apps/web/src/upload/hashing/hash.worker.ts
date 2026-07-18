/// <reference lib="webworker" />

import { createHashWorkerRuntime } from "./runtime";

const workerScope = self as unknown as DedicatedWorkerGlobalScope;
const runtime = createHashWorkerRuntime(workerScope);

workerScope.addEventListener("message", (event: MessageEvent<unknown>) => {
  runtime.handleMessage(event.data);
});

export {};
