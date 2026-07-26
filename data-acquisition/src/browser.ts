import puppeteer, {
  type Browser,
  type ElementHandle,
  type Page,
} from "puppeteer-core";

import { inspectLoginState } from "./auth.js";
import type { AppConfig } from "./config.js";
import { AgentOperationError } from "./errors.js";
import { productIdFromUrl, verifyProductDetail } from "./product-detail.js";
import type { ProductDetailCheck } from "./types.js";


const allowedHosts = new Set(["chuhaijiang.com", "www.chuhaijiang.com"]);
const productListPath = "/app/discover/tiktok/products";

export class ChromeCdpClient {
  private browser: Browser | null = null;
  private readonly detailPages = new Map<string, Page>();

  constructor(private readonly config: AppConfig) {}

  get isConnected(): boolean {
    return this.browser?.connected ?? false;
  }

  async connect(): Promise<{ browserVersion: string; pageCount: number }> {
    if (!this.isConnected) {
      try {
        this.browser = await puppeteer.connect({
          browserURL: this.config.chromeCdpUrl,
          defaultViewport: null,
          protocolTimeout: this.config.operationTimeoutMs,
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        throw new AgentOperationError(
          "CDP_CONNECTION_FAILED",
          message,
          `无法连接 Chrome CDP：${this.config.chromeCdpUrl}。请确认调试浏览器已启动。`,
          true,
        );
      }
    }

    const browser = this.requireBrowser();
    return {
      browserVersion: await browser.version(),
      pageCount: (await browser.pages()).length,
    };
  }

  async openTargetUrl(targetUrl: string): Promise<Page> {
    this.assertAllowedUrl(targetUrl);
    const browser = this.requireBrowser();
    const pages = await browser.pages();
    const existing = pages.find((page) => this.isTargetPage(page));
    const page = existing ?? await browser.newPage();

    if (page.url() !== targetUrl) {
      await page.goto(targetUrl, {
        waitUntil: "domcontentloaded",
        timeout: this.config.operationTimeoutMs,
      });
    }

    page.setDefaultTimeout(this.config.operationTimeoutMs);
    await page.bringToFront();
    return page;
  }

  async findTargetPage(): Promise<Page> {
    const pages = (await this.requireBrowser().pages()).filter((page) =>
      this.isTargetPage(page)
    );

    if (pages.length === 0) {
      throw new AgentOperationError(
        "TARGET_PAGE_NOT_FOUND",
        "No chuhaijiang.com page is available in the CDP browser.",
        "Chrome 中未找到出海匠页面，请先调用 chrome_open_url。",
        true,
      );
    }

    for (const page of pages) {
      const loginState = await inspectLoginState(page);
      if (loginState.loggedIn) {
        page.setDefaultTimeout(this.config.operationTimeoutMs);
        await page.bringToFront();
        return page;
      }
    }

    const page = pages[0];
    if (page === undefined) {
      throw new AgentOperationError(
        "TARGET_PAGE_NOT_FOUND",
        "Target page disappeared while selecting it.",
        "出海匠页面已关闭，请重新打开后重试。",
        true,
      );
    }

    page.setDefaultTimeout(this.config.operationTimeoutMs);
    await page.bringToFront();
    return page;
  }

  async findProductListPage(): Promise<Page> {
    return this.findAuthenticatedPage((page) => {
      try {
        return new URL(page.url()).pathname === productListPath;
      } catch {
        return false;
      }
    }, "Chrome 中未找到商品列表页，请先打开并筛选商品。");
  }

  async findProductDetailPage(productId?: string): Promise<Page> {
    if (productId !== undefined) {
      const ownedPage = this.detailPages.get(productId);
      if (ownedPage !== undefined && !ownedPage.isClosed()) {
        let ownedProductId: string | null = null;
        try {
          ownedProductId = productIdFromUrl(ownedPage.url());
        } catch {
          // The export download can navigate this owned tab to chrome-error.
        }
        if (ownedProductId !== productId) {
          throw new AgentOperationError(
            "PRODUCT_DETAIL_TAB_INVALID",
            `The owned detail tab for ${productId} is no longer on the product page.`,
            "商品详情标签已离开商品页面，请关闭该标签并重新打开商品。",
            true,
          );
        }
        ownedPage.setDefaultTimeout(this.config.operationTimeoutMs);
        await ownedPage.bringToFront();
        return ownedPage;
      }
    }
    return this.findAuthenticatedPage((page) => {
      try {
        const currentProductId = productIdFromUrl(page.url());
        return productId === undefined || currentProductId === productId;
      } catch {
        return false;
      }
    }, productId === undefined
      ? "Chrome 中未找到商品详情页。"
      : `Chrome 中未找到商品 ${productId} 的详情页。`);
  }

  async openProductDetail(productUrl: string): Promise<ProductDetailCheck> {
    this.assertAllowedUrl(productUrl);
    const productId = productIdFromUrl(productUrl);
    const browser = this.requireBrowser();
    const existingPages = await browser.pages();

    const sourcePage = await this.findProductListPage();
    let targetLink = await this.findVisibleProductLink(sourcePage, productId);
    if (targetLink === undefined) {
      await this.moveProductListToFirstPage(sourcePage);
      for (let pageNumber = 1; pageNumber <= 10; pageNumber += 1) {
        targetLink = await this.findVisibleProductLink(sourcePage, productId);
        if (targetLink !== undefined) {
          break;
        }
        if (!(await this.moveProductListToNextPage(sourcePage))) {
          break;
        }
      }
    }
    if (targetLink === undefined) {
      throw new AgentOperationError(
        "PRODUCT_LINK_NOT_FOUND",
        `Product ${productId} was not visible in the current product table.`,
        "当前商品列表中未找到指定商品链接，请重新提取当前页商品。",
        true,
      );
    }

    const previousTargets = new Set(existingPages.map((page) => page.target()));
    const detailTargetPromise = browser.waitForTarget((candidate) => {
      if (candidate.type() !== "page" || previousTargets.has(candidate)) {
        return false;
      }
      try {
        return productIdFromUrl(candidate.url()) === productId;
      } catch {
        return false;
      }
    }, { timeout: this.config.operationTimeoutMs });
    await targetLink.scrollIntoView();
    await targetLink.evaluate((element) => (element as HTMLElement).click());
    const target = await detailTargetPromise;
    const detailPage = await target.page();
    if (detailPage === null) {
      throw new AgentOperationError(
        "PRODUCT_TAB_NOT_CREATED",
        `Product ${productId} did not open in a browser page.`,
        "点击商品后没有创建详情标签页。",
        true,
      );
    }
    detailPage.setDefaultTimeout(this.config.operationTimeoutMs);
    await detailPage.bringToFront();
    await detailPage.waitForFunction(
      () => document.readyState === "complete" || document.readyState === "interactive",
    );
    this.detailPages.set(productId, detailPage);
    return verifyProductDetail(detailPage, productUrl, true);
  }

  async closeProductDetail(productId: string): Promise<{
    productId: string;
    tabClosed: true;
    browserWindowClosed: false;
  }> {
    let page = this.detailPages.get(productId);
    if (page !== undefined && !page.isClosed()) {
      await page.close();
    }
    this.detailPages.delete(productId);
    const listPage = await this.findProductListPage();
    await listPage.bringToFront();
    return { productId, tabClosed: true, browserWindowClosed: false };
  }

  async disconnect(): Promise<{ disconnected: true; browserWindowClosed: false }> {
    if (this.browser !== null) {
      this.browser.disconnect();
      this.browser = null;
    }

    return { disconnected: true, browserWindowClosed: false };
  }

  private requireBrowser(): Browser {
    if (!this.isConnected || this.browser === null) {
      throw new AgentOperationError(
        "CHROME_NOT_CONNECTED",
        "Chrome CDP has not been connected.",
        "尚未连接 Chrome，请先调用 chrome_connect。",
        true,
      );
    }
    return this.browser;
  }

  private isTargetPage(page: Page): boolean {
    try {
      return allowedHosts.has(new URL(page.url()).hostname);
    } catch {
      return false;
    }
  }

  private async findAuthenticatedPage(
    predicate: (page: Page) => boolean,
    userMessage: string,
  ): Promise<Page> {
    const pages = (await this.requireBrowser().pages()).filter(
      (page) => this.isTargetPage(page) && predicate(page),
    );
    if (pages.length === 0) {
      throw new AgentOperationError(
        "TARGET_PAGE_NOT_FOUND",
        "No matching Chuhaijiang page is available in the CDP browser.",
        userMessage,
        true,
      );
    }
    for (const page of pages) {
      const loginState = await inspectLoginState(page);
      if (loginState.loggedIn) {
        page.setDefaultTimeout(this.config.operationTimeoutMs);
        await page.bringToFront();
        return page;
      }
    }
    const page = pages[0];
    if (page === undefined) {
      throw new AgentOperationError(
        "TARGET_PAGE_NOT_FOUND",
        "The matching page disappeared while selecting it.",
        userMessage,
        true,
      );
    }
    page.setDefaultTimeout(this.config.operationTimeoutMs);
    await page.bringToFront();
    return page;
  }

  private async findVisibleProductLink(
    page: Page,
    productId: string,
  ): Promise<ElementHandle<Element> | undefined> {
    const links = await page.$$('a[href*="/app/discover/tiktok/products/"]');
    for (const link of links) {
      const matches = await link.evaluate(
        (element, expectedProductId) => {
          const href = element.getAttribute("href");
          if (href === null) {
            return false;
          }
          return new URL(href, window.location.origin).pathname.endsWith(
            `/products/${expectedProductId}`,
          );
        },
        productId,
      );
      if (matches && await link.isVisible()) {
        return link;
      }
    }
    return undefined;
  }

  private async moveProductListToFirstPage(page: Page): Promise<void> {
    const buttons = await page.$$(
      'button[data-ph-capture-attribute-button-name="discover:page_changed"]',
    );
    for (const button of buttons) {
      const text = await button.evaluate((element) => (element.textContent ?? "").trim());
      if (text !== "1") {
        continue;
      }
      const isCurrent = await button.evaluate((element) =>
        element.className.includes("bg-primary")
      );
      if (!isCurrent) {
        const previousUrl = await this.firstVisibleProductUrl(page);
        await button.click();
        await this.waitForProductPageChange(page, previousUrl);
      }
      return;
    }
    throw new AgentOperationError(
      "PRODUCT_PAGINATION_NOT_FOUND",
      "The first product page control was not found.",
      "未找到商品分页控件。",
      true,
    );
  }

  private async moveProductListToNextPage(page: Page): Promise<boolean> {
    const button = await page.$(
      'button[data-ph-capture-attribute-button-name="discover:page_changed"][aria-label="下一页"]',
    );
    if (
      button === null
      || await button.evaluate((element) => element.hasAttribute("disabled"))
    ) {
      return false;
    }
    const previousUrl = await this.firstVisibleProductUrl(page);
    await button.click();
    await this.waitForProductPageChange(page, previousUrl);
    return true;
  }

  private async firstVisibleProductUrl(page: Page): Promise<string | null> {
    return page.$eval(
      'table tbody tr a[href*="/products/"]',
      (element) => (element as HTMLAnchorElement).href,
    );
  }

  private async waitForProductPageChange(
    page: Page,
    previousUrl: string | null,
  ): Promise<void> {
    await page.waitForFunction(
      (oldUrl) => {
        const anchor = document.querySelector<HTMLAnchorElement>(
          'table tbody tr a[href*="/products/"]',
        );
        return anchor !== null && anchor.href !== oldUrl;
      },
      {},
      previousUrl,
    );
  }

  private assertAllowedUrl(targetUrl: string): void {
    const hostname = new URL(targetUrl).hostname;
    if (!allowedHosts.has(hostname)) {
      throw new AgentOperationError(
        "URL_NOT_ALLOWED",
        `Host is not allowed: ${hostname}`,
        "当前 Chrome Data MCP 仅允许访问出海匠网页。",
      );
    }
  }
}
