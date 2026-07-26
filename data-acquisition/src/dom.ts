import type { ElementHandle, Page } from "puppeteer-core";

import { AgentOperationError } from "./errors.js";


interface FindOptions {
  maxX?: number;
  preferLeftmost?: boolean;
}

interface RankedHandle {
  handle: ElementHandle<Element>;
  x: number;
}

function normalizeText(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

export async function findVisibleByCss(
  page: Page,
  selector: string,
  options: FindOptions = {},
): Promise<ElementHandle<Element>> {
  const handles = await page.$$(selector);
  const ranked: RankedHandle[] = [];

  for (const handle of handles) {
    const rectangle = await handle.boundingBox();
    if (rectangle === null || !(await handle.isVisible())) {
      continue;
    }
    if (options.maxX !== undefined && rectangle.x > options.maxX) {
      continue;
    }
    ranked.push({ handle, x: rectangle.x });
  }

  if (options.preferLeftmost === true) {
    ranked.sort((left, right) => left.x - right.x);
  }

  const result = ranked[0]?.handle;
  if (result === undefined) {
    throw new AgentOperationError(
      "ELEMENT_NOT_FOUND",
      `No visible element matched selector: ${selector}`,
      "页面结构可能已变化，未找到目标控件。",
      true,
    );
  }
  return result;
}

export async function findVisibleByExactText(
  page: Page,
  selector: string,
  expectedText: string,
  options: FindOptions = {},
): Promise<ElementHandle<Element>> {
  const handles = await page.$$(selector);
  const ranked: RankedHandle[] = [];

  for (const handle of handles) {
    const text = await handle.evaluate((element) =>
      (element.textContent ?? "").trim().replace(/\s+/g, " ")
    );
    const rectangle = await handle.boundingBox();
    if (
      normalizeText(text) !== normalizeText(expectedText)
      || rectangle === null
      || !(await handle.isVisible())
    ) {
      continue;
    }
    if (options.maxX !== undefined && rectangle.x > options.maxX) {
      continue;
    }
    ranked.push({ handle, x: rectangle.x });
  }

  if (options.preferLeftmost === true) {
    ranked.sort((left, right) => left.x - right.x);
  }

  const result = ranked[0]?.handle;
  if (result === undefined) {
    throw new AgentOperationError(
      "ELEMENT_NOT_FOUND",
      `No visible ${selector} matched text: ${expectedText}`,
      `页面中未找到“${expectedText}”，请检查当前页面和操作日志。`,
      true,
    );
  }
  return result;
}

export async function hasVisibleCss(page: Page, selector: string): Promise<boolean> {
  const handles = await page.$$(selector);
  for (const handle of handles) {
    if (await handle.isVisible()) {
      return true;
    }
  }
  return false;
}

export async function hasVisibleExactText(
  page: Page,
  selector: string,
  expectedText: string,
): Promise<boolean> {
  try {
    await findVisibleByExactText(page, selector, expectedText);
    return true;
  } catch (error) {
    if (error instanceof AgentOperationError && error.code === "ELEMENT_NOT_FOUND") {
      return false;
    }
    throw error;
  }
}

export async function clickHandle(handle: ElementHandle<Element>): Promise<void> {
  await handle.scrollIntoView();
  await handle.click();
}

export async function pauseForUi(milliseconds = 400): Promise<void> {
  await new Promise<void>((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}
