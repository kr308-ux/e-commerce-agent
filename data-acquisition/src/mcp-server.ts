import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod/v4";

import type { AppConfig } from "./config.js";
import { normalizeError } from "./errors.js";
import { ChromeDataService } from "./service.js";
import type {
  NavigationStepName,
  OperationContext,
  ToolResponse,
} from "./types.js";


const operationInput = {
  taskId: z.string().min(1).describe("父任务 ID"),
  stepId: z.string().min(1).describe("当前步骤 ID"),
};

function successResponse<T>(data: T): ToolResponse<T> {
  return {
    success: true,
    status: "SUCCESS",
    data,
    error: null,
  };
}

function asMcpResult<T>(response: ToolResponse<T>): CallToolResult {
  const structuredContent = JSON.parse(JSON.stringify(response)) as Record<string, unknown>;
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(response, null, 2),
      },
    ],
    structuredContent,
    ...(response.success ? {} : { isError: true }),
  };
}

async function executeTool<T>(action: () => Promise<T>): Promise<CallToolResult> {
  try {
    return asMcpResult(successResponse(await action()));
  } catch (error) {
    const normalized = normalizeError(error);
    return asMcpResult({
      success: false,
      status: normalized.status,
      data: null,
      error: normalized.toPayload(),
    });
  }
}

function contextFrom(input: OperationContext): OperationContext {
  return { taskId: input.taskId, stepId: input.stepId };
}

interface NavigationRegistration {
  toolName: string;
  title: string;
  description: string;
  step: NavigationStepName;
}

const navigationRegistrations: NavigationRegistration[] = [
  {
    toolName: "chrome_open_selection",
    title: "打开选品",
    description: "点击出海匠左侧主导航中的“选品”，并验证选品模块可见。",
    step: "open_selection",
  },
  {
    toolName: "chrome_open_product_module",
    title: "展开商品模块",
    description: "确保出海匠选品侧栏中的“商品”模块处于展开状态。",
    step: "open_product_module",
  },
  {
    toolName: "chrome_open_product_search",
    title: "打开商品搜索",
    description: "点击“商品搜索”并验证进入 TikTok 商品搜索页面。",
    step: "open_product_search",
  },
  {
    toolName: "chrome_open_category",
    title: "打开类目",
    description: "点击商品搜索页的类目筛选按钮并验证下拉菜单出现。",
    step: "open_category_menu",
  },
  {
    toolName: "chrome_select_sports_outdoor",
    title: "选择运动与户外",
    description: "在一级类目中选择“运动与户外”，并验证右侧子类目出现。",
    step: "select_sports_outdoor",
  },
  {
    toolName: "chrome_select_sports_apparel",
    title: "选择运动服饰",
    description: "在右侧子类目中选择“运动服饰”，并验证筛选参数生效。",
    step: "select_sports_apparel",
  },
];

export function buildMcpServer(config: AppConfig): McpServer {
  const service = new ChromeDataService(config);
  const server = new McpServer({
    name: "chrome-data-mcp",
    version: "0.1.0",
  });

  server.registerTool(
    "chrome_connect",
    {
      title: "连接 Chrome CDP",
      description: "连接已启动的普通 Chrome CDP，不关闭用户浏览器窗口。",
      inputSchema: operationInput,
      annotations: { readOnlyHint: true, destructiveHint: false },
    },
    async (input) => executeTool(() => service.connect(contextFrom(input))),
  );

  server.registerTool(
    "chrome_open_url",
    {
      title: "打开出海匠商品页",
      description: "在 CDP Chrome 中打开出海匠 TikTok 商品搜索页面。",
      inputSchema: {
        ...operationInput,
        url: z.url().optional().describe("可选目标 URL，仅允许出海匠域名"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false },
    },
    async (input) =>
      executeTool(() =>
        service.openUrl(contextFrom(input), input.url ?? config.chromeTargetUrl)
      ),
  );

  server.registerTool(
    "chrome_check_login",
    {
      title: "检查出海匠登录状态",
      description: "检查出海匠页面登录态；未登录时返回用户可见提示。",
      inputSchema: operationInput,
      annotations: { readOnlyHint: true, destructiveHint: false },
    },
    async (input) =>
      executeTool(() => service.checkLogin(contextFrom(input))),
  );

  for (const registration of navigationRegistrations) {
    server.registerTool(
      registration.toolName,
      {
        title: registration.title,
        description: registration.description,
        inputSchema: operationInput,
        annotations: { readOnlyHint: false, destructiveHint: false },
      },
      async (input) =>
        executeTool(() =>
          service.navigate(contextFrom(input), registration.step)
        ),
    );
  }

  server.registerTool(
    "chrome_extract_products",
    {
      title: "提取当前商品数据",
      description: "从当前商品表格提取可见行并返回结构化商品数据。",
      inputSchema: {
        ...operationInput,
        maxRows: z.number().int().min(10).max(100).multipleOf(10).default(10)
          .describe("商品数量，只允许 10 到 100 的十倍数"),
      },
      annotations: { readOnlyHint: true, destructiveHint: false },
    },
    async (input) =>
      executeTool(() =>
        service.extractProducts(contextFrom(input), input.maxRows)
      ),
  );

  server.registerTool(
    "chrome_open_product_detail",
    {
      title: "打开商品详情新标签页",
      description: "点击商品列表中的指定商品，并验证详情在新的 Chrome 标签页打开。",
      inputSchema: {
        ...operationInput,
        productUrl: z.url().describe("chrome_extract_products 返回的商品详情 URL"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false },
    },
    async (input) =>
      executeTool(() =>
        service.openProductDetail(contextFrom(input), input.productUrl)
      ),
  );

  server.registerTool(
    "chrome_collect_products_for_creators",
    {
      title: "收集待获取达人的商品",
      description: "按 10 的倍数跨商品分页采集任务所需的精简商品字段和详情链接。",
      inputSchema: {
        ...operationInput,
        maxRows: z.number().int().min(10).max(100).multipleOf(10).default(10)
          .describe("商品数量，只允许 10 到 100 的十倍数"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false },
    },
    async (input) =>
      executeTool(() =>
        service.collectProductsForCreatorTask(contextFrom(input), input.maxRows)
      ),
  );

  server.registerTool(
    "chrome_open_related_creators",
    {
      title: "打开商品关联达人模块",
      description: "在指定商品详情标签页点击“关联达人”，并验证达人表格已加载。",
      inputSchema: {
        ...operationInput,
        productId: z.string().regex(/^\d+$/).describe("出海匠商品 ID"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false },
    },
    async (input) =>
      executeTool(() =>
        service.openRelatedCreators(contextFrom(input), input.productId)
      ),
  );

  server.registerTool(
    "chrome_export_related_creators",
    {
      title: "导出商品关联达人",
      description: "点击关联达人模块的导出按钮并选择 100 条，返回已下载 XLSX 文件信息。",
      inputSchema: {
        ...operationInput,
        productId: z.string().regex(/^\d+$/).describe("出海匠商品 ID"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false },
    },
    async (input) =>
      executeTool(() =>
        service.exportRelatedCreators(contextFrom(input), input.productId)
      ),
  );

  server.registerTool(
    "chrome_close_product_detail",
    {
      title: "关闭商品详情标签页",
      description: "关闭指定商品详情标签页并回到商品列表；不会关闭 Chrome 窗口。",
      inputSchema: {
        ...operationInput,
        productId: z.string().regex(/^\d+$/).describe("出海匠商品 ID"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false },
    },
    async (input) =>
      executeTool(() =>
        service.closeProductDetail(contextFrom(input), input.productId)
      ),
  );

  server.registerTool(
    "chrome_disconnect",
    {
      title: "断开 Chrome CDP",
      description: "释放 MCP 的 CDP 连接，但不关闭用户的 Chrome 窗口。",
      inputSchema: operationInput,
      annotations: { readOnlyHint: true, destructiveHint: false },
    },
    async (input) => executeTool(() => service.disconnect(contextFrom(input))),
  );

  return server;
}
