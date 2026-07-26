import { randomUUID } from "node:crypto";
import { mkdir, stat } from "node:fs/promises";
import path from "node:path";

import type { CDPSession, ElementHandle, Page } from "puppeteer-core";

import { clickHandle, findVisibleByExactText, pauseForUi } from "./dom.js";
import { AgentOperationError } from "./errors.js";
import type {
  CreatorExportResult,
  ProductDetailCheck,
  RelatedCreatorsCheck,
} from "./types.js";


const productDetailPath = /^\/app\/discover\/tiktok\/products\/(\d+)$/;
const exportTriggerAttribute =
  'button[data-ph-capture-attribute-button-name="discover:export_opened"]';
const exportCountAttribute =
  'button[data-ph-capture-attribute-export-count="100"]';

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim().replace(/\s+/g, " ");
}

export function productIdFromUrl(productUrl: string): string {
  const match = new URL(productUrl).pathname.match(productDetailPath);
  const productId = match?.[1];
  if (productId === undefined) {
    throw new AgentOperationError(
      "INVALID_PRODUCT_URL",
      `Not a supported Chuhaijiang product URL: ${productUrl}`,
      "商品链接不是有效的出海匠 TikTok 商品详情地址。",
    );
  }
  return productId;
}

function safePathSegment(value: string): string {
  const sanitized = value.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 120);
  return sanitized === "" ? "unknown" : sanitized;
}

export async function verifyProductDetail(
  page: Page,
  productUrl: string,
  openedInNewTab: boolean,
): Promise<ProductDetailCheck> {
  const expectedProductId = productIdFromUrl(productUrl);
  const currentProductId = productIdFromUrl(page.url());
  if (currentProductId !== expectedProductId) {
    throw new AgentOperationError(
      "PRODUCT_DETAIL_MISMATCH",
      `Expected product ${expectedProductId}, opened ${currentProductId}.`,
      "打开的商品详情与所选商品不一致，请返回商品列表后重试。",
      true,
    );
  }

  try {
    await page.waitForFunction(() =>
      Array.from(document.querySelectorAll("button")).some((element) => {
        const rectangle = element.getBoundingClientRect();
        const text = (element.textContent ?? "").trim().replace(/\s+/g, " ");
        return text === "关联达人" && rectangle.width > 0 && rectangle.height > 0;
      })
    );
  } catch {
    // Converted below to the stable, service-facing error contract.
  }
  const title = await page.title();
  const relatedCreatorsTabVisible = await page.evaluate(() =>
    Array.from(document.querySelectorAll("button")).some((element) => {
      const rectangle = element.getBoundingClientRect();
      const text = (element.textContent ?? "").trim().replace(/\s+/g, " ");
      return text === "关联达人" && rectangle.width > 0 && rectangle.height > 0;
    })
  );
  if (!relatedCreatorsTabVisible) {
    throw new AgentOperationError(
      "PRODUCT_DETAIL_NOT_READY",
      "The related-creators tab was not visible on the product detail page.",
      "商品详情页尚未加载完成，未找到“关联达人”模块。",
      true,
    );
  }

  return {
    productId: expectedProductId,
    productUrl,
    title,
    openedInNewTab,
    currentUrl: page.url(),
  };
}

export async function openRelatedCreators(
  page: Page,
): Promise<RelatedCreatorsCheck> {
  const productId = productIdFromUrl(page.url());
  const currentUrl = new URL(page.url());
  if (currentUrl.searchParams.get("tab") !== "related-creators") {
    const tab = await findVisibleByExactText(page, "button", "关联达人");
    await tab.evaluate((element) => (element as HTMLElement).click());
    await page.waitForFunction(
      () => new URL(window.location.href).searchParams.get("tab") === "related-creators",
    );
    await pauseForUi(600);
  }

  const moduleState = await page.evaluate(() => {
    const normalize = (value: string | null | undefined) =>
      (value ?? "").trim().replace(/\s+/g, " ");
    const tables = Array.from(document.querySelectorAll("table"));
    const headerTable = tables.find((table) => {
      const headers = Array.from(table.querySelectorAll("thead th")).map((cell) =>
        normalize(cell.textContent)
      );
      return headers.includes("达人信息") && headers.includes("近 7 天销售额");
    });
    const tableHeaders = headerTable === undefined
      ? []
      : Array.from(headerTable.querySelectorAll("thead th")).map((cell) =>
          normalize(cell.textContent)
        );
    const sectionText = headerTable?.parentElement?.parentElement?.parentElement
      ?.textContent;
    const countMatch = normalize(sectionText).match(/(\d+)\s*位带货达人/);
    return {
      creatorCount: countMatch === null ? null : Number(countMatch[1]),
      tableVisible: headerTable !== undefined,
      tableHeaders,
    };
  });

  if (!moduleState.tableVisible) {
    throw new AgentOperationError(
      "RELATED_CREATORS_NOT_READY",
      "The related-creators table was not visible.",
      "关联达人模块未加载出达人表格，请稍后重试。",
      true,
    );
  }

  return {
    productId,
    currentUrl: page.url(),
    ...moduleState,
  };
}

async function findCreatorExportButton(
  page: Page,
): Promise<ElementHandle<Element>> {
  const buttons = await page.$$(exportTriggerAttribute);
  for (const button of buttons) {
    const isCreatorExport = await button.evaluate((element) => {
      let current: Element | null = element.parentElement;
      for (let level = 0; level < 4 && current !== null; level += 1) {
        const text = (current.textContent ?? "").trim().replace(/\s+/g, " ");
        if (text.includes("位带货达人")) {
          return true;
        }
        current = current.parentElement;
      }
      return false;
    });
    if (isCreatorExport && await button.isVisible()) {
      return button;
    }
  }

  throw new AgentOperationError(
    "CREATOR_EXPORT_NOT_FOUND",
    "No export button was found in the related-creators module.",
    "关联达人模块中未找到“导出”按钮。",
    true,
  );
}

interface DownloadEvent {
  guid: string;
  suggestedFilename: string;
}

async function waitForDownload(
  session: CDPSession,
  timeoutMs: number,
  click: () => Promise<void>,
): Promise<DownloadEvent> {
  return new Promise<DownloadEvent>((resolve, reject) => {
    let activeDownload: DownloadEvent | null = null;
    const timeout = setTimeout(() => {
      cleanup();
      reject(new AgentOperationError(
        "CREATOR_EXPORT_TIMEOUT",
        `Creator export did not finish within ${timeoutMs}ms.`,
        "达人文件导出超时，请检查页面提示和网络后重试。",
        true,
      ));
    }, timeoutMs);

    const cleanup = () => {
      clearTimeout(timeout);
      session.off("Browser.downloadWillBegin", onWillBegin);
      session.off("Browser.downloadProgress", onProgress);
    };
    const onWillBegin = (event: DownloadEvent) => {
      activeDownload = {
        guid: event.guid,
        suggestedFilename: event.suggestedFilename,
      };
    };
    const onProgress = (event: { guid: string; state: string }) => {
      if (activeDownload?.guid !== event.guid) {
        return;
      }
      if (event.state === "completed") {
        const completed = activeDownload;
        cleanup();
        resolve(completed);
      } else if (event.state === "canceled") {
        cleanup();
        reject(new AgentOperationError(
          "CREATOR_EXPORT_CANCELLED",
          "The browser cancelled the creator export download.",
          "达人文件下载已取消，请重试。",
          true,
        ));
      }
    };

    session.on("Browser.downloadWillBegin", onWillBegin);
    session.on("Browser.downloadProgress", onProgress);
    click().catch((error: unknown) => {
      cleanup();
      reject(error);
    });
  });
}

export async function exportRelatedCreators(
  page: Page,
  exportsDirectory: string,
  taskId: string,
  stepId: string,
  downloadTimeoutMs: number,
): Promise<CreatorExportResult> {
  const relatedCreators = await openRelatedCreators(page);
  const runDirectory = path.join(
    exportsDirectory,
    safePathSegment(taskId),
    relatedCreators.productId,
    `${Date.now()}-${safePathSegment(stepId)}-${randomUUID().slice(0, 8)}`,
  );
  await mkdir(runDirectory, { recursive: true });

  const session = await page.createCDPSession();
  await session.send("Browser.setDownloadBehavior", {
    behavior: "allow",
    downloadPath: runDirectory,
    eventsEnabled: true,
  });

  const trigger = await findCreatorExportButton(page);
  if (await trigger.evaluate((element) => element.getAttribute("data-state")) !== "open") {
    await clickHandle(trigger);
    await pauseForUi(250);
  }

  const option = await page.$(exportCountAttribute);
  if (option === null || !(await option.isVisible())) {
    throw new AgentOperationError(
      "CREATOR_EXPORT_OPTION_NOT_FOUND",
      "The 100-row creator export option was not visible.",
      "导出菜单中未找到“100 条”选项。",
      true,
    );
  }

  const downloaded = await waitForDownload(
    session,
    downloadTimeoutMs,
    async () => clickHandle(option),
  );
  const filePath = path.join(runDirectory, downloaded.suggestedFilename);
  const fileStats = await stat(filePath);
  const rowCountMatch = downloaded.suggestedFilename.match(/_(\d+)条_/);

  return {
    productId: relatedCreators.productId,
    requestedRowCount: 100,
    exportedRowCount: Number(rowCountMatch?.[1] ?? 0),
    fileName: downloaded.suggestedFilename,
    filePath,
    fileSizeBytes: fileStats.size,
    downloadedAt: new Date().toISOString(),
  };
}

export async function detailPageSummary(page: Page): Promise<{
  productId: string;
  currentUrl: string;
  title: string;
}> {
  return {
    productId: productIdFromUrl(page.url()),
    currentUrl: page.url(),
    title: normalizeText(await page.title()),
  };
}
