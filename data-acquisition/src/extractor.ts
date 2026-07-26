import type { Page } from "puppeteer-core";

import { AgentOperationError } from "./errors.js";
import type { ProductExtractionResult } from "./types.js";


export async function extractVisibleProducts(
  page: Page,
  maxRows: number,
): Promise<ProductExtractionResult> {
  const extractPage = async (requestedRows: number) => page.evaluate((pageRows) => {
    const headers = Array.from(document.querySelectorAll("table thead th")).map(
      (cell) => (cell.textContent ?? "").trim().replace(/\s+/g, " "),
    );
    const rows = Array.from(document.querySelectorAll("table tbody tr"));
    const products = rows.slice(0, pageRows).map((row) => {
      const cells = Array.from(row.querySelectorAll("td"));
      const values = cells.map((cell) =>
        (cell.textContent ?? "").trim().replace(/\s+/g, " ")
      );
      const productCell = cells[0];
      const shopCell = cells[1];
      const productLink = productCell?.querySelector("a");
      const shopLink = shopCell?.querySelector("a");
      const productImage = productCell?.querySelector("img");
      const detailLine = productCell === undefined
        ? []
        : Array.from(productCell.querySelectorAll("div"))
            .filter((element) => element.className.includes("gap-1 mt-1"))
            .flatMap((element) =>
              Array.from(element.querySelectorAll(":scope > span")).map((span) =>
                (span.textContent ?? "").trim().replace(/\s+/g, " ")
              )
            );
      const price = productCell === undefined
        ? null
        : Array.from(productCell.querySelectorAll("span"))
            .map((element) =>
              (element.textContent ?? "").trim().replace(/\s+/g, " ")
            )
            .find((text) => /^\$[\d.]/.test(text)) ?? null;
      const videoImages = Array.from(row.querySelectorAll('img[alt^="Video"]'))
        .map((image) => image.getAttribute("src"))
        .filter((source): source is string => source !== null);
      const columns = Object.fromEntries(
        headers.map((header, index) => [header || `column_${index + 1}`, values[index] ?? ""]),
      );
      const shopText = (shopLink?.textContent ?? "").trim().replace(/\s+/g, " ");
      const salesMarker = "销量：";
      const salesIndex = shopText.lastIndexOf(salesMarker);
      const productHref = productLink?.getAttribute("href") ?? null;
      const shopHref = shopLink?.getAttribute("href") ?? null;

      return {
        title: (productLink?.textContent ?? "").trim().replace(/\s+/g, " "),
        productUrl:
          productHref === null
            ? null
            : new URL(productHref, window.location.origin).toString(),
        imageUrl: productImage?.getAttribute("src") ?? null,
        price,
        stockStatus: values[0]?.includes("库存") === true ? "库存" : null,
        category: detailLine[0] ?? null,
        commissionRate: detailLine[1] ?? null,
        rating: detailLine[2]?.replace(/^★\s*/, "") ?? null,
        shopName:
          salesIndex >= 0 ? shopText.slice(0, salesIndex).trim() : shopText || null,
        shopUrl:
          shopHref === null
            ? null
            : new URL(shopHref, window.location.origin).toString(),
        shopTotalSales:
          salesIndex >= 0 ? shopText.slice(salesIndex + salesMarker.length).trim() : null,
        topVideoImages: videoImages,
        recent7DaySales: values[4] === undefined || values[4] === "" ? null : values[4],
        totalSales: values[5] === undefined || values[5] === "" ? null : values[5],
        recent7DayRevenue:
          values[6] === undefined || values[6] === "" ? null : values[6],
        totalRevenue: values[7] === undefined || values[7] === "" ? null : values[7],
        relatedCreators: values[8] === undefined || values[8] === "" ? null : values[8],
        creatorOrderRate:
          values[9] === undefined || values[9] === "" ? null : values[9],
        columns,
      };
    });

    const currentUrl = new URL(window.location.href);
    const selectedCategory =
      Array.from(document.querySelectorAll("button"))
        .map((button) => (button.textContent ?? "").trim().replace(/\s+/g, " "))
        .find((text) => text === "运动服饰") ?? null;

    return {
      source: "chuhaijiang" as const,
      sourceUrl: currentUrl.toString(),
      country: currentUrl.searchParams.get("country"),
      selectedCategory,
      visibleRowCount: rows.length,
      returnedRowCount: products.length,
      products,
    };
  }, requestedRows);

  const ensureFirstPage = async () => {
    const buttons = await page.$$('button[data-ph-capture-attribute-button-name="discover:page_changed"]');
    for (const button of buttons) {
      const text = await button.evaluate((element) => (element.textContent ?? "").trim());
      const isCurrent = await button.evaluate((element) =>
        element.className.includes("bg-primary")
      );
      if (text === "1") {
        if (!isCurrent) {
          const oldFirstProduct = await page.$eval(
            'table tbody tr a[href*="/products/"]',
            (element) => element.getAttribute("href"),
          );
          await button.click();
          await page.waitForFunction(
            (previousHref) =>
              document.querySelector('table tbody tr a[href*="/products/"]')
                ?.getAttribute("href") !== previousHref,
            {},
            oldFirstProduct,
          );
        }
        return;
      }
    }
    throw new AgentOperationError(
      "PRODUCT_PAGINATION_NOT_FOUND",
      "The first product page button was not found.",
      "未找到商品分页控件，请检查页面是否加载完成。",
      true,
    );
  };

  await ensureFirstPage();
  const products: ProductExtractionResult["products"] = [];
  let pageCount = 0;
  let lastPageResult: Awaited<ReturnType<typeof extractPage>> | null = null;

  while (products.length < maxRows) {
    lastPageResult = await extractPage(Math.min(10, maxRows - products.length));
    pageCount += 1;
    products.push(...lastPageResult.products);
    if (products.length >= maxRows || lastPageResult.products.length === 0) {
      break;
    }

    const nextButton = await page.$(
      'button[data-ph-capture-attribute-button-name="discover:page_changed"][aria-label="下一页"]',
    );
    if (nextButton === null || await nextButton.evaluate((element) => element.hasAttribute("disabled"))) {
      break;
    }
    const previousFirstProduct = lastPageResult.products[0]?.productUrl ?? null;
    await nextButton.click();
    await page.waitForFunction(
      (previousUrl) => {
        const anchor = document.querySelector<HTMLAnchorElement>(
          'table tbody tr a[href*="/products/"]',
        );
        return anchor !== null && anchor.href !== previousUrl;
      },
      {},
      previousFirstProduct,
    );
  }

  if (products.length === 0 || lastPageResult === null) {
    throw new AgentOperationError(
      "NO_PRODUCT_ROWS",
      "No product rows were visible in the table.",
      "当前页面没有可提取的商品数据，请检查筛选条件或等待页面加载。",
      true,
    );
  }
  return {
    source: "chuhaijiang",
    sourceUrl: lastPageResult.sourceUrl,
    country: lastPageResult.country,
    selectedCategory: lastPageResult.selectedCategory,
    visibleRowCount: lastPageResult.visibleRowCount,
    returnedRowCount: products.length,
    scannedPageCount: pageCount,
    extractedAt: new Date().toISOString(),
    products,
  };
}
