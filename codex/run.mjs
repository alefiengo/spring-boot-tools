#!/usr/bin/env node
// Per-process setup only. The compatibility MCP format in Codex CLI 0.166.0
// neither expands PLUGIN_ROOT nor supplies a plugin-root environment variable.
// Resolve our own installation, then explicitly forward that path to MCP.
import { spawn } from 'node:child_process';
import { constants } from 'node:os';
import { fileURLToPath } from 'node:url';

const child = spawn('codex', process.argv.slice(2), {
  cwd: process.cwd(),
  env: { ...process.env, SBT_PLUGIN_ROOT: fileURLToPath(new URL('../', import.meta.url)) },
  stdio: 'inherit',
});
// Ctrl-C reaches the whole foreground process group, Codex included. Forwarding
// SIGINT would deliver it twice, so the wrapper only stays alive for the exit code.
process.on('SIGINT', () => {});
// Termination aimed at the wrapper alone must still reach Codex.
for (const signal of ['SIGTERM', 'SIGHUP']) process.on(signal, () => child.kill(signal));
child.on('error', error => { console.error(`Cannot launch Codex: ${error.message}`); process.exitCode = 1; });
child.on('exit', (code, signal) => { process.exitCode = code ?? (signal ? 128 + (constants.signals[signal] || 0) : 1); });
