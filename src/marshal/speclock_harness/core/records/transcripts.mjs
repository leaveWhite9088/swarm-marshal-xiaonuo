/**
 * Adapted from SpecLock2 (`packages/agentd/core/records/transcripts.mjs`).
 * Appends worker turn transcripts per worker session in JSONL format.
 */

import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';

const safe = (s) => String(s ?? 'unknown').replace(/[^A-Za-z0-9._~-]/g, '_');

export function appendWorkerTranscript(transcriptsDir, workerId, entry) {
  if (!transcriptsDir || !workerId) return false;
  try {
    const file = join(transcriptsDir, `${safe(workerId)}.jsonl`);
    mkdirSync(dirname(file), { recursive: true });
    appendFileSync(file, JSON.stringify({ ts: new Date().toISOString(), ...entry }) + '\n', 'utf8');
    return true;
  } catch {
    return false;
  }
}
