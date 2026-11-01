#!/usr/bin/env node
// Keep the host's workspace cwd. Never use the plugin installation as the app
// root, or inherit a stale PROJECT_ROOT / Claude session's project selection.
// Discovery, tool validation and execution remain in the shared MCP/runtime.
import { fileURLToPath } from 'node:url';

process.env.PROJECT_ROOT = process.cwd();
const entry = new URL('../mcp-server/index.js', import.meta.url);
// The shared entrypoint starts serveStdio only when argv identifies index.js.
process.argv[1] = fileURLToPath(entry);
await import(entry);
