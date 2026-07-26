import type { Page } from "puppeteer-core";

import { inspectLoginState } from "./auth.js";
import { ChromeCdpClient } from "./browser.js";
import type { AppConfig } from "./config.js";
import { AgentOperationError } from "./errors.js";
import { extractVisibleProducts } from "./extractor.js";
import { OperationLogger } from "./logger.js";
import {
  exportRelatedCreators,
  openRelatedCreators,
  productIdFromUrl,
} from "./product-detail.js";
import {
  openCategoryMenu,
  openProductModule,
  openProductSearch,
  openSelection,
  selectSportsApparel,
  selectSportsOutdoor,
} from "./navigation.js";
import type {
  LoginState,
  CreatorExportResult,
  CreatorTaskProductCollection,
  NavigationCheck,
  NavigationStepName,
  OperationContext,
  ProductDetailCheck,
  ProductExtractionResult,
  RelatedCreatorsCheck,
} from "./types.js";


type NavigationAction = (page: Page) => Promise<NavigationCheck>;

const navigationActions: Record<NavigationStepName, NavigationAction> = {
  open_selection: openSelection,
  open_product_module: openProductModule,
  open_product_search: openProductSearch,
  open_category_menu: openCategoryMenu,
  select_sports_outdoor: selectSportsOutdoor,
  select_sports_apparel: selectSportsApparel,
};

export class ChromeDataService {
  private readonly browser: ChromeCdpClient;
  private readonly logger: OperationLogger;

  constructor(private readonly config: AppConfig) {
    this.browser = new ChromeCdpClient(config);
    this.logger = new OperationLogger(config.logsDir);
  }

  async connect(context: OperationContext): Promise<{
    browserVersion: string;
    pageCount: number;
    cdpUrl: string;
  }> {
    return this.logger.run(
      context,
      "chrome_connect",
      "CONNECT",
      { cdpUrl: this.config.chromeCdpUrl },
      async () => ({
        ...(await this.browser.connect()),
        cdpUrl: this.config.chromeCdpUrl,
      }),
      (value) => value,
    );
  }

  async openUrl(
    context: OperationContext,
    targetUrl = this.config.chromeTargetUrl,
  ): Promise<{ url: string; title: string }> {
    return this.logger.run(
      context,
      "chrome_open_url",
      "NAVIGATION",
      { targetUrl },
      async () => {
        const page = await this.browser.openTargetUrl(targetUrl);
        return { url: page.url(), title: await page.title() };
      },
    );
  }

  async checkLogin(context: OperationContext): Promise<LoginState> {
    return this.logger.run(
      context,
      "chrome_check_login",
      "READ",
      {},
      async () => {
        const loginState = await inspectLoginState(await this.browser.findTargetPage());
        if (!loginState.loggedIn) {
          throw new AgentOperationError(
            "AUTH_REQUIRED",
            `Authentication required: ${loginState.reason}`,
            loginState.userMessage ?? "请先在 Chrome 中登录出海匠。",
            false,
            "WAITING_CONFIRMATION",
          );
        }
        return loginState;
      },
    );
  }

  async navigate(
    context: OperationContext,
    step: NavigationStepName,
  ): Promise<NavigationCheck> {
    return this.logger.run(
      context,
      `chrome_${step}`,
      "CLICK",
      { step },
      async () => {
        const page = await this.requireAuthenticatedPage();
        return navigationActions[step](page);
      },
      (value) => ({
        step: value.step,
        status: value.status,
        currentUrl: value.currentUrl,
        evidence: value.evidence,
      }),
    );
  }

  async extractProducts(
    context: OperationContext,
    maxRows: number,
  ): Promise<ProductExtractionResult> {
    return this.logger.run(
      context,
      "chrome_extract_products",
      "READ",
      { maxRows },
      async () => extractVisibleProducts(await this.requireProductListPage(), maxRows),
      (value) => ({
        sourceUrl: value.sourceUrl,
        visibleRowCount: value.visibleRowCount,
        returnedRowCount: value.returnedRowCount,
        scannedPageCount: value.scannedPageCount,
        selectedCategory: value.selectedCategory,
      }),
    );
  }

  async openProductDetail(
    context: OperationContext,
    productUrl: string,
  ): Promise<ProductDetailCheck> {
    return this.logger.run(
      context,
      "chrome_open_product_detail",
      "CLICK",
      { productId: productIdFromUrl(productUrl) },
      async () => {
        await this.requireAuthenticatedPage();
        return this.browser.openProductDetail(productUrl);
      },
      (value) => ({
        productId: value.productId,
        openedInNewTab: value.openedInNewTab,
        currentUrl: value.currentUrl,
      }),
    );
  }

  async collectProductsForCreatorTask(
    context: OperationContext,
    maxRows: number,
  ): Promise<CreatorTaskProductCollection> {
    return this.logger.run(
      context,
      "chrome_collect_products_for_creators",
      "READ",
      { maxRows },
      async () => {
        const extraction = await extractVisibleProducts(
          await this.requireProductListPage(),
          maxRows,
        );
        return {
          sourceUrl: extraction.sourceUrl,
          country: extraction.country,
          selectedCategory: extraction.selectedCategory,
          returnedRowCount: extraction.returnedRowCount,
          scannedPageCount: extraction.scannedPageCount,
          products: extraction.products.flatMap((product) =>
            product.productUrl === null
              ? []
              : [{
                  title: product.title,
                  productUrl: product.productUrl,
                  totalSales: product.totalSales,
                  recent7DayRevenue: product.recent7DayRevenue,
                  totalRevenue: product.totalRevenue,
                  relatedCreators: product.relatedCreators,
                }]
          ),
        };
      },
      (value) => ({
        sourceUrl: value.sourceUrl,
        returnedRowCount: value.returnedRowCount,
        scannedPageCount: value.scannedPageCount,
        selectedCategory: value.selectedCategory,
      }),
    );
  }

  async openRelatedCreators(
    context: OperationContext,
    productId: string,
  ): Promise<RelatedCreatorsCheck> {
    return this.logger.run(
      context,
      "chrome_open_related_creators",
      "CLICK",
      { productId },
      async () =>
        openRelatedCreators(
          await this.requireAuthenticatedProductDetailPage(productId),
        ),
      (value) => ({
        productId: value.productId,
        currentUrl: value.currentUrl,
        creatorCount: value.creatorCount,
        tableVisible: value.tableVisible,
      }),
    );
  }

  async exportRelatedCreators(
    context: OperationContext,
    productId: string,
  ): Promise<CreatorExportResult> {
    return this.logger.run(
      context,
      "chrome_export_related_creators",
      "DOWNLOAD",
      { productId, requestedRowCount: 100 },
      async () =>
        exportRelatedCreators(
          await this.requireAuthenticatedProductDetailPage(productId),
          this.config.exportsDir,
          context.taskId,
          context.stepId,
          this.config.downloadTimeoutMs,
        ),
      (value) => ({
        productId: value.productId,
        requestedRowCount: value.requestedRowCount,
        exportedRowCount: value.exportedRowCount,
        fileName: value.fileName,
        filePath: value.filePath,
        fileSizeBytes: value.fileSizeBytes,
      }),
    );
  }

  async closeProductDetail(
    context: OperationContext,
    productId: string,
  ): Promise<{
    productId: string;
    tabClosed: true;
    browserWindowClosed: false;
  }> {
    return this.logger.run(
      context,
      "chrome_close_product_detail",
      "CLOSE_TAB",
      { productId },
      async () => this.browser.closeProductDetail(productId),
    );
  }

  async disconnect(
    context: OperationContext,
  ): Promise<{ disconnected: true; browserWindowClosed: false }> {
    return this.logger.run(
      context,
      "chrome_disconnect",
      "DISCONNECT",
      {},
      async () => this.browser.disconnect(),
    );
  }

  private async requireAuthenticatedPage(): Promise<Page> {
    const page = await this.browser.findTargetPage();
    const loginState = await inspectLoginState(page);
    if (!loginState.loggedIn) {
      throw new AgentOperationError(
        "AUTH_REQUIRED",
        `Authentication check failed: ${loginState.reason}`,
        loginState.userMessage ?? "请先在 Chrome 中登录出海匠。",
        false,
        "WAITING_CONFIRMATION",
      );
    }
    return page;
  }

  private async requireProductListPage(): Promise<Page> {
    const page = await this.browser.findProductListPage();
    await this.assertAuthenticated(page);
    return page;
  }

  private async requireAuthenticatedProductDetailPage(productId: string): Promise<Page> {
    const page = await this.browser.findProductDetailPage(productId);
    await this.assertAuthenticated(page);
    return page;
  }

  private async assertAuthenticated(page: Page): Promise<void> {
    const loginState = await inspectLoginState(page);
    if (!loginState.loggedIn) {
      throw new AgentOperationError(
        "AUTH_REQUIRED",
        `Authentication check failed: ${loginState.reason}`,
        loginState.userMessage ?? "请先在 Chrome 中登录出海匠。",
        false,
        "WAITING_CONFIRMATION",
      );
    }
  }
}
