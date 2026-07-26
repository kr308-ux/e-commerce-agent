#!/usr/bin/env node

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { loadConfig } from "./config.js";
import { buildMcpServer } from "./mcp-server.js";


async function main(): Promise<void> {
  const server = buildMcpServer(loadConfig());
  const transport = new StdioServerTransport();
  await server.connect(transport);
  process.stderr.write("Chrome Data MCP running on stdio\n");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.stack ?? error.message : String(error);
  process.stderr.write(`Chrome Data MCP failed: ${message}\n`);
  process.exitCode = 1;
});
