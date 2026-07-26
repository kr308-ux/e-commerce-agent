import type { Page } from "puppeteer-core";

import type { LoginState } from "./types.js";


export function loginStateFromSignals(
  currentUrl: string,
  loginControlVisible: boolean,
): LoginState {
  if (/\/(login|sign-in|signin)(\/|$|\?)/i.test(currentUrl)) {
    return {
      loggedIn: false,
      reason: "LOGIN_URL",
      currentUrl,
      userMessage: "出海匠尚未登录，请在 Chrome 中完成登录后重试。",
    };
  }

  if (loginControlVisible) {
    return {
      loggedIn: false,
      reason: "LOGIN_CONTROL_VISIBLE",
      currentUrl,
      userMessage: "检测到出海匠登录入口，请先在 Chrome 中登录账号。",
    };
  }

  return {
    loggedIn: true,
    reason: "AUTHENTICATED_HEADER",
    currentUrl,
    userMessage: null,
  };
}

export async function inspectLoginState(page: Page): Promise<LoginState> {
  const currentUrl = page.url();
  const loginControlVisible = await page.evaluate(() => {
    return Array.from(document.querySelectorAll("button, a")).some((element) => {
      const text = (element.textContent ?? "").trim().replace(/\s+/g, " ");
      const rectangle = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return (
        text === "登录"
        && rectangle.width > 0
        && rectangle.height > 0
        && style.display !== "none"
        && style.visibility !== "hidden"
      );
    });
  });

  return loginStateFromSignals(currentUrl, loginControlVisible);
}
