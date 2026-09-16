import upstream from './src/worker.ts';
import { createPrivateMailHandler } from './private-handler.mjs';

// Deliberately expose no scheduled handler in the initial receive-only package.
export default createPrivateMailHandler(upstream);
