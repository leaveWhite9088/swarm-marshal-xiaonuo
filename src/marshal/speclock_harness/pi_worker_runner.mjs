#!/usr/bin/env node
/**
 * Swarm Marshal Pi Worker Runner (built on SpecLock2's `PiRpc`, `models.mjs`, `killTree`, and `TOOL_CALL_AS_TEXT`).
 *
 * Reads a JSON task payload from `--input <json_file>` (or stdin), spawns `pi --mode rpc` with:
 *   - `--no-extensions --no-mcp --no-skills --no-prompt-templates --no-context-files` (total isolation from repo AGENTS.md & global plugins)
 *   - `--session-dir <worker_session_dir>` (persistent per-worker session across dispatches & REJECT reworks, just like SpecLock2)
 *   - `--append-system-prompt <system_prompt_file>` (Executor Rules + Marshal's role_prompt + allowed_paths)
 *   - Locked weak model (`provider/model`) and locked thinking level (`off` by default)
 *
 * Writes structured JSON result to `--output <json_file>` (or stdout).
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PiRpc, addPiProvider } from './core/pi/index.mjs';
import { TOOL_CALL_AS_TEXT } from './core/base/index.mjs';
import { fitReply } from './core/records/reply.mjs';
import { appendWorkerTranscript } from './core/records/transcripts.mjs';
import { reasoningFor } from './drivers/pi-catalog.mjs';
import piDriver from './drivers/pi.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));

function findDeviceProvidersFile() {
  const candidates = [
    process.env.SWARM_PROVIDERS_FILE,
    process.env.AGENTD_PROVIDERS_FILE,
    join(HERE, '..', '..', '..', 'configs', 'providers.local.json'),
    'E:/Project/Code-261013-speclock2/speclock2/packages/agentd/data/global/providers.json',
  ].filter(Boolean);
  for (const p of candidates) {
    if (existsSync(p)) return p;
  }
  return null;
}

function loadDeviceProvider(providerId) {
  const file = findDeviceProvidersFile();
  if (!file || !providerId) return null;
  try {
    const data = JSON.parse(readFileSync(file, 'utf8'));
    return data?.providers?.[providerId] ?? null;
  } catch {
    return null;
  }
}

const LEAK_CORRECTION =
  '【系统提示】你上一条回复把工具调用写成了普通文字（如 <｜DSML｜> 或 <function=…>），并未真正触发文件读写工具。' +
  '请立即发起真实的 write / edit / read / bash 工具调用落盘修改文件，不要只在文字里贴代码。';

function resolvePiCli() {
  if (process.env.PI_CLI && existsSync(process.env.PI_CLI)) return process.env.PI_CLI;
  const candidates = [
    join(dirname(process.execPath), 'node_modules', '@earendil-works', 'pi-coding-agent', 'dist', 'bundle', 'cli.js'),
    join(dirname(process.execPath), '..', 'lib', 'node_modules', '@earendil-works', 'pi-coding-agent', 'dist', 'bundle', 'cli.js'),
  ];
  if (process.env.APPDATA) {
    candidates.push(join(process.env.APPDATA, 'npm', 'node_modules', '@earendil-works', 'pi-coding-agent', 'dist', 'bundle', 'cli.js'));
  }
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  throw new Error(
    `Cannot locate @earendil-works/pi-coding-agent cli.js (checked ${candidates.join('; ')}). Set PI_CLI env var if installed elsewhere.`
  );
}

function newestSessionFile(sessionDir) {
  if (!existsSync(sessionDir)) return null;
  const hits = [];
  const walk = (dir) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith('.jsonl')) {
        try {
          hits.push({ p, mtime: statSync(p).mtimeMs });
        } catch {
          /* ignore */
        }
      }
    }
  };
  walk(sessionDir);
  hits.sort((a, b) => b.mtime - a.mtime);
  return hits[0]?.p ?? null;
}

function parseArgs(argv) {
  const out = { input: null, output: null, probe: false };
  for (let i = 2; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--input') out.input = argv[++i];
    else if (a === '--output') out.output = argv[++i];
    else if (a === '--probe') out.probe = true;
  }
  return out;
}

async function runWorkerTask(spec) {
  const piCli = resolvePiCli();
  const nodeBin = process.env.PI_NODE_BIN || process.execPath;
  const cwd = resolve(spec.cwd);
  const sessionDir = resolve(spec.sessionDir);
  const transcriptsDir = spec.transcriptsDir ? resolve(spec.transcriptsDir) : null;
  const resultsDir = spec.resultsDir ? resolve(spec.resultsDir) : null;
  const workerId = String(spec.workerId || 'worker');
  const jobId = String(spec.jobId || `${workerId}-${Date.now()}`);
  const timeoutMs = Math.max(15_000, Number(spec.timeoutSec || 240) * 1000);
  const compactAt = Number(spec.compactAtTokens || 24_000);
  const replyLimit = Number(spec.replyCharLimit || 6000);

  mkdirSync(cwd, { recursive: true });
  mkdirSync(sessionDir, { recursive: true });

  const env = {
    ...process.env,
    PI_OFFLINE: '1',
    PI_TELEMETRY: '0',
    AGENTD_MCP_SERVERS: '',
  };

  // 1) Explicit custom OpenAI-compatible endpoint OR 2) SpecLock2 device providers.json via piDriver.provider.apply
  let appliedModelPair = null;
  if (spec.customEndpoint?.enabled && spec.customEndpoint.baseUrl) {
    const agentCfgDir = resolve(spec.agentConfigDir || join(sessionDir, '.pi-agent-config'));
    const providerId = String(spec.provider || 'worker-provider').toLowerCase().replace(/[^a-z0-9._-]/g, '-');
    const modelId = String(spec.model);
    const keyEnv = String(spec.customEndpoint.apiKeyEnv || 'SWARM_WORKER_API_KEY');
    const apiKeyVal = process.env[keyEnv] || spec.customEndpoint.apiKey || '';
    if (!apiKeyVal) {
      throw new Error(`Custom worker endpoint enabled, but env var ${keyEnv} is empty.`);
    }
    const rInfo = reasoningFor(modelId, piCli, providerId);
    addPiProvider(
      {
        provider: providerId,
        baseUrl: spec.customEndpoint.baseUrl,
        api: spec.customEndpoint.api || 'openai-completions',
        apiKey: `$${keyEnv}`,
        models: [
          {
            id: modelId,
            contextWindow: Number(spec.maxContextTokens || 32000),
            reasoning: Boolean(rInfo.reasoning),
          },
        ],
      },
      agentCfgDir
    );
    writeFileSync(
      join(agentCfgDir, 'settings.json'),
      JSON.stringify({ defaultProvider: providerId, defaultModel: modelId }, null, 2),
      'utf8'
    );
    env.PI_CODING_AGENT_DIR = agentCfgDir;
    env[keyEnv] = apiKeyVal;
  } else {
    const devProvider = loadDeviceProvider(spec.provider);
    const openaiEp = devProvider?.endpoints?.openai;
    if (devProvider && openaiEp && devProvider.apiKey) {
      const agentCfgDir = resolve(spec.agentConfigDir || join(sessionDir, '.pi-agent-config'));
      mkdirSync(agentCfgDir, { recursive: true });
      const applied = piDriver.provider.apply({
        provider: devProvider,
        endpoint: openaiEp,
        model: spec.model,
        dir: agentCfgDir,
        env: { ...env, PI_CLI: piCli },
      });
      Object.assign(env, applied.env);
      for (const f of applied.files ?? []) {
        writeFileSync(join(agentCfgDir, f.name), f.content, 'utf8');
      }
      appliedModelPair = applied.model;
    }
  }

  const args = [
    piCli,
    '--mode',
    'rpc',
    '--no-extensions',
    '--no-mcp',
    '--no-skills',
    '--no-prompt-templates',
    '--no-context-files',
    '--no-themes',
    '--session-dir',
    sessionDir,
  ];

  if (appliedModelPair) {
    args.push('--model', appliedModelPair);
  } else if (spec.provider && spec.model) {
    args.push('--provider', String(spec.provider), '--model', String(spec.model));
  } else if (spec.model) {
    args.push('--model', String(spec.model));
  }
  if (spec.thinking) {
    args.push('--thinking', String(spec.thinking));
  }
  if (spec.systemPromptFile && existsSync(spec.systemPromptFile)) {
    args.push('--append-system-prompt', resolve(spec.systemPromptFile));
  }

  const silentLog = () => {};
  const rpc = new PiRpc({ label: `pi-worker:${workerId}`, log: silentLog });

  let assistantText = '';
  let modelError = null;
  let leakedTextCall = false;
  const toolEvents = [];

  rpc.addListener((rec) => {
    if (rec.type === 'extension_ui_request') {
      rpc.write({ type: 'extension_ui_response', id: rec.id, cancelled: true });
      return;
    }
    if (rec.type === 'message_update') {
      const ev = rec.assistantMessageEvent;
      if (ev?.type === 'text_delta' && ev.delta) {
        assistantText += ev.delta;
        const tail = assistantText.slice(-400);
        if (!leakedTextCall && TOOL_CALL_AS_TEXT.test(tail)) {
          leakedTextCall = true;
          rpc.abort().catch(() => {});
        }
      }
    } else if (rec.type === 'message_end' && rec.message?.role === 'assistant') {
      if (rec.message.stopReason === 'error') {
        modelError = String(rec.message.errorMessage || 'pi model call failed');
      }
    } else if (rec.type === 'tool_execution_start') {
      toolEvents.push({
        tool: rec.toolName ?? 'unknown',
        args_preview: JSON.stringify(rec.args ?? {}).slice(0, 200),
      });
    }
  });

  const startedAt = Date.now();
  try {
    const priorSession = spec.freshSession ? null : newestSessionFile(sessionDir);
    rpc.start({ cmd: nodeBin, args, cwd, env });

    if (priorSession && existsSync(priorSession)) {
      try {
        await rpc.switchSession(priorSession);
      } catch {
        await rpc.newSession();
      }
    } else {
      await rpc.newSession();
    }

    // Enforce locked model and thinking level at RPC session level as well
    if (spec.provider && spec.model) {
      await rpc.setModel(String(spec.provider), String(spec.model)).catch(() => {});
    }
    if (spec.thinking) {
      await rpc.setThinking(String(spec.thinking)).catch(() => {});
    }

    // Auto-compact if continuing session has grown beyond weak-model context budget
    if (priorSession && compactAt > 0) {
      const ctxBefore = await rpc.context().catch(() => null);
      if (ctxBefore?.used && ctxBefore.used >= compactAt) {
        await rpc.compact(120_000).catch(() => {});
      }
    }

    appendWorkerTranscript(transcriptsDir, workerId, {
      jobId,
      role: 'user',
      prompt: spec.prompt,
    });

    const isTransientNetErr = (msg) =>
      /ECONNRESET|ETIMEDOUT|UND_ERR_|other side closed|socket disconnected|Connect Timeout|Request timed out|502|503|504|rate limit/i.test(
        String(msg || '')
      );

    const runSinglePrompt = async (message) => {
      leakedTextCall = false;
      modelError = null;
      const settled = rpc.waitSettled(timeoutMs);
      settled.catch(() => {});
      const r = await rpc.command('prompt', { message }, timeoutMs);
      if (r?.disposition !== 'handled') await settled;
    };

    for (let attempt = 0; attempt < 3; attempt += 1) {
      await runSinglePrompt(String(spec.prompt));
      if (!modelError || !isTransientNetErr(modelError) || attempt === 2) break;
      await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
    }

    // SpecLock2 self-healing: if weak model wrote DSML/XML tool markup as plain text, correct once
    if (leakedTextCall) {
      assistantText += '\n[检测到伪工具调用文本，触发一次纠偏重试]\n';
      await runSinglePrompt(LEAK_CORRECTION);
    }

    if (modelError) {
      throw new Error(modelError);
    }

    const [st, stats] = await Promise.all([
      rpc.state().catch(() => ({})),
      rpc.stats(15_000).catch(() => ({})),
    ]);

    const fitted = fitReply(resultsDir, jobId, assistantText.trim(), replyLimit);
    appendWorkerTranscript(transcriptsDir, workerId, {
      jobId,
      role: 'assistant',
      reply: fitted.text,
      toolCount: toolEvents.length,
      tokens: stats?.tokens ?? null,
    });

    return {
      ok: true,
      harness: 'pi',
      worker_id: workerId,
      job_id: jobId,
      model_active: st?.model ? `${st.model.provider}/${st.model.id}` : `${spec.provider || ''}/${spec.model || ''}`,
      thinking_level: st?.thinkingLevel ?? spec.thinking ?? 'off',
      session_file: st?.sessionFile ?? priorSession ?? null,
      elapsed_ms: Date.now() - startedAt,
      summary: fitted.text || '（工人已完成执行，未输出额外文字说明）',
      omitted_chars: fitted.omitted,
      tool_calls: toolEvents,
      tokens: {
        input_tokens: stats?.tokens?.input ?? null,
        output_tokens: stats?.tokens?.output ?? null,
        total_tokens: stats?.tokens?.total ?? stats?.contextUsage?.tokens ?? null,
      },
    };
  } finally {
    rpc.stop();
  }
}

async function main() {
  const cliArgs = parseArgs(process.argv);
  if (cliArgs.probe) {
    const cli = resolvePiCli();
    process.stdout.write(JSON.stringify({ ok: true, pi_cli: cli, node: process.execPath }) + '\n');
    return;
  }

  const raw = cliArgs.input ? readFileSync(resolve(cliArgs.input), 'utf8') : readFileSync(0, 'utf8');
  const spec = JSON.parse(raw);
  let res;
  try {
    res = await runWorkerTask(spec);
  } catch (err) {
    res = {
      ok: false,
      harness: 'pi',
      worker_id: spec?.workerId ?? 'worker',
      error: String(err?.message || err),
      stderr_tail: '',
    };
  }
  const jsonText = JSON.stringify(res, null, 2);
  if (cliArgs.output) {
    mkdirSync(dirname(resolve(cliArgs.output)), { recursive: true });
    writeFileSync(resolve(cliArgs.output), jsonText, 'utf8');
  } else {
    process.stdout.write(jsonText + '\n');
  }
  if (!res.ok) process.exit(1);
}

main().catch((err) => {
  process.stderr.write(`[pi_worker_runner] fatal: ${err?.stack || err}\n`);
  process.exit(1);
});
