import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { afterEach, describe, expect, it } from "vitest";

import type { AppConfig } from "../src/config.js";
import { buildMcpServer } from "../src/mcp-server.js";


const config: AppConfig = {
  chromeCdpUrl: "http://127.0.0.1:65534",
  chromeTargetUrl:
    "https://www.chuhaijiang.com/app/discover/tiktok/products?country=US",
  operationTimeoutMs: 100,
  downloadTimeoutMs: 100,
  logLevel: "INFO",
  logsDir: "temporary/test-logs",
  exportsDir: "temporary/test-exports",
};

const closeCallbacks: Array<() => Promise<void>> = [];

afterEach(async () => {
  while (closeCallbacks.length > 0) {
    await closeCallbacks.pop()?.();
  }
});

describe("Chrome Data MCP", () => {
  it("registers isolated, single-purpose Chrome tools", async () => {
    const server = buildMcpServer(config);
    const client = new Client({ name: "test-client", version: "0.1.0" });
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    closeCallbacks.push(async () => server.close());
    closeCallbacks.push(async () => client.close());

    await server.connect(serverTransport);
    await client.connect(clientTransport);
    const tools = await client.listTools();
    const names = tools.tools.map((tool) => tool.name);

    expect(names).toEqual(expect.arrayContaining([
      "chrome_connect",
      "chrome_open_url",
      "chrome_check_login",
      "chrome_open_selection",
      "chrome_open_product_module",
      "chrome_open_product_search",
      "chrome_open_category",
      "chrome_select_sports_outdoor",
      "chrome_select_sports_apparel",
      "chrome_extract_products",
      "chrome_collect_products_for_creators",
      "chrome_open_product_detail",
      "chrome_open_related_creators",
      "chrome_export_related_creators",
      "chrome_close_product_detail",
      "chrome_disconnect",
    ]));
    expect(names).toHaveLength(16);
  });

  it("returns a structured error when tools are called before connection", async () => {
    const server = buildMcpServer(config);
    const client = new Client({ name: "test-client", version: "0.1.0" });
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    closeCallbacks.push(async () => server.close());
    closeCallbacks.push(async () => client.close());

    await server.connect(serverTransport);
    await client.connect(clientTransport);
    const result = await client.callTool({
      name: "chrome_check_login",
      arguments: { taskId: "task-test", stepId: "step-test" },
    });

    expect(result.isError).toBe(true);
    expect(result.structuredContent).toMatchObject({
      success: false,
      status: "FAILED",
      error: { code: "CHROME_NOT_CONNECTED" },
    });
  });
});
