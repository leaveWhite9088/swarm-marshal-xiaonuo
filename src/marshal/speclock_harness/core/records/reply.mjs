/**
 * Adapted from SpecLock2 (`packages/agentd/core/records/reply.mjs`).
 * Trims overly long executor replies so the Marshal's context is not flooded,
 * while preserving full output on disk.
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export const REPLY_LIMIT = Number(process.env.SWARM_REPLY_CHARS) || 6000;

export function fitReply(resultsDir, jobId, text, limit = REPLY_LIMIT) {
  const full = String(text ?? '');
  if (full.length <= limit) return { text: full, file: null, omitted: 0 };

  const head = full.slice(0, Math.round(limit * 0.55));
  const tail = full.slice(full.length - Math.round(limit * 0.25));
  const omitted = full.length - head.length - tail.length;

  let file = resultsDir ? join(resultsDir, `${String(jobId).replace(/[^\w.-]/g, '_')}.md`) : null;
  if (file) {
    try {
      mkdirSync(resultsDir, { recursive: true });
      writeFileSync(file, full, 'utf8');
    } catch {
      file = null;
    }
  }
  const where = file ? `完整输出已保存至 ${file}。` : '';
  return {
    text: `${head}\n\n[... 中间省略 ${omitted} 字符。${where}以上为开头与结尾摘要 ...]\n\n${tail}`,
    file,
    omitted,
  };
}
