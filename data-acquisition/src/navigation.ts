import type { Page } from "puppeteer-core";

import {
  clickHandle,
  findVisibleByCss,
  findVisibleByExactText,
  hasVisibleCss,
  hasVisibleExactText,
  pauseForUi,
} from "./dom.js";
import { AgentOperationError } from "./errors.js";
import type { NavigationCheck, NavigationStepName } from "./types.js";


const productSearchPath = "/app/discover/tiktok/products";

function success(
  step: NavigationStepName,
  page: Page,
  evidence: NavigationCheck["evidence"],
): NavigationCheck {
  return {
    step,
    status: "SUCCESS",
    currentUrl: page.url(),
    evidence,
  };
}

function requireCheck(condition: boolean, code: string, userMessage: string): void {
  if (!condition) {
    throw new AgentOperationError(
      code,
      `Navigation verification failed: ${code}`,
      userMessage,
      true,
    );
  }
}

export async function openSelection(page: Page): Promise<NavigationCheck> {
  const link = await findVisibleByCss(page, 'nav a[href="/app/discover"]', {
    preferLeftmost: true,
  });
  await clickHandle(link);
  await pauseForUi();

  const routeVisible = page.url().includes("/app/discover");
  requireCheck(routeVisible, "SELECTION_NAVIGATION_FAILED", "点击“选品”后页面未进入选品模块。");
  return success("open_selection", page, { routeVisible });
}

export async function openProductModule(page: Page): Promise<NavigationCheck> {
  const productSearchAlreadyVisible = await hasVisibleCss(
    page,
    `a[href="${productSearchPath}"]`,
  );
  if (productSearchAlreadyVisible) {
    return success("open_product_module", page, {
      alreadyOpen: true,
      productSearchVisible: true,
    });
  }

  const button = await findVisibleByExactText(page, "button", "商品", { maxX: 500 });
  const beforeState = await button.evaluate((element) =>
    element.getAttribute("data-state")
  );

  if (beforeState !== "open") {
    await clickHandle(button);
    await pauseForUi();
  }

  const afterState = await button.evaluate((element) =>
    element.getAttribute("data-state")
  );
  const productSearchVisible = await hasVisibleCss(
    page,
    `a[href="${productSearchPath}"]`,
  );
  requireCheck(
    afterState === "open" && productSearchVisible,
    "PRODUCT_MODULE_NOT_OPEN",
    "商品模块未能展开，请刷新页面后重试。",
  );

  return success("open_product_module", page, {
    alreadyOpen: beforeState === "open",
    productSearchVisible,
  });
}

export async function openProductSearch(page: Page): Promise<NavigationCheck> {
  const previousCountry = new URL(page.url()).searchParams.get("country") ?? "US";
  const link = await findVisibleByCss(page, `a[href="${productSearchPath}"]`);
  await clickHandle(link);
  await pauseForUi(700);

  const currentUrl = new URL(page.url());
  if (!currentUrl.searchParams.has("country")) {
    currentUrl.searchParams.set("country", previousCountry);
    await page.goto(currentUrl.toString(), {
      waitUntil: "domcontentloaded",
    });
    await pauseForUi();
  }

  const verifiedUrl = new URL(page.url());
  const correctRoute = verifiedUrl.pathname === productSearchPath;
  const countryPreserved = verifiedUrl.searchParams.get("country") === previousCountry;
  const categoryControlVisible = await hasVisibleExactText(page, "button", "类目")
    || await hasVisibleExactText(page, "button", "运动服饰");
  requireCheck(
    correctRoute && countryPreserved && categoryControlVisible,
    "PRODUCT_SEARCH_NAVIGATION_FAILED",
    "点击“商品搜索”后未进入商品搜索页面。",
  );

  return success("open_product_search", page, {
    correctRoute,
    countryPreserved,
    categoryControlVisible,
  });
}

export async function openCategoryMenu(page: Page): Promise<NavigationCheck> {
  let button;
  try {
    button = await findVisibleByExactText(page, "button", "类目");
  } catch (error) {
    if (!(error instanceof AgentOperationError) || error.code !== "ELEMENT_NOT_FOUND") {
      throw error;
    }
    button = await findVisibleByExactText(page, "button", "运动服饰");
  }

  await clickHandle(button);
  await pauseForUi();

  const sportsOutdoorVisible = await hasVisibleExactText(
    page,
    "button",
    "运动与户外",
  );
  requireCheck(
    sportsOutdoorVisible,
    "CATEGORY_MENU_NOT_OPEN",
    "类目下拉框未打开，请检查页面是否加载完成。",
  );

  return success("open_category_menu", page, { sportsOutdoorVisible });
}

export async function selectSportsOutdoor(page: Page): Promise<NavigationCheck> {
  const button = await findVisibleByExactText(page, "button", "运动与户外");
  await clickHandle(button);
  await pauseForUi();

  const sportsApparelVisible = await hasVisibleExactText(
    page,
    "button",
    "运动服饰",
  );
  requireCheck(
    sportsApparelVisible,
    "SUBCATEGORY_PANEL_NOT_OPEN",
    "选择“运动与户外”后未出现右侧子类目。",
  );

  return success("select_sports_outdoor", page, { sportsApparelVisible });
}

export async function selectSportsApparel(page: Page): Promise<NavigationCheck> {
  const button = await findVisibleByExactText(page, "button", "运动服饰");
  await clickHandle(button);
  await pauseForUi(900);

  const currentUrl = new URL(page.url());
  const categoryApplied = currentUrl.searchParams.has("cat");
  const categoryControlSelected = await hasVisibleExactText(
    page,
    "button",
    "运动服饰",
  );
  requireCheck(
    categoryApplied && categoryControlSelected,
    "CATEGORY_FILTER_NOT_APPLIED",
    "“运动服饰”筛选条件未生效，请检查网络和页面状态。",
  );

  return success("select_sports_apparel", page, {
    categoryApplied,
    categoryControlSelected,
    categoryQuery: currentUrl.searchParams.get("cat"),
  });
}
