import { loadConfig } from "../src/config.js";
import { ChromeDataService } from "../src/service.js";
import type { NavigationStepName } from "../src/types.js";


const config = loadConfig({
  ...process.env,
  CHROME_CDP_URL: process.env.CHROME_CDP_URL ?? "http://127.0.0.1:9333",
});
const service = new ChromeDataService(config);
const taskId = `live-check-${Date.now()}`;
const steps: NavigationStepName[] = [
  "open_selection",
  "open_product_module",
  "open_product_search",
  "open_category_menu",
  "select_sports_outdoor",
  "select_sports_apparel",
];

async function main(): Promise<void> {
  const checks = [];
  await service.connect({ taskId, stepId: "connect" });
  await service.openUrl({ taskId, stepId: "open-url" });

  await service.checkLogin({ taskId, stepId: "check-login" });

  for (const [index, step] of steps.entries()) {
    checks.push(
      await service.navigate({
        taskId,
        stepId: `navigation-${index + 1}`,
      }, step),
    );
  }

  const extraction = await service.extractProducts(
    { taskId, stepId: "extract" },
    10,
  );
  await service.disconnect({ taskId, stepId: "disconnect" });

  process.stdout.write(
    `${JSON.stringify({
      success: true,
      status: "SUCCESS",
      taskId,
      checks,
      extraction: {
        selectedCategory: extraction.selectedCategory,
        visibleRowCount: extraction.visibleRowCount,
        returnedRowCount: extraction.returnedRowCount,
        sample: extraction.products[0] ?? null,
      },
    }, null, 2)}\n`,
  );
}

main().catch(async (error: unknown) => {
  const message = error instanceof Error ? error.stack ?? error.message : String(error);
  process.stderr.write(`${message}\n`);
  try {
    await service.disconnect({ taskId, stepId: "disconnect-after-error" });
  } catch (disconnectError) {
    const disconnectMessage = disconnectError instanceof Error
      ? disconnectError.message
      : String(disconnectError);
    process.stderr.write(`Disconnect failed: ${disconnectMessage}\n`);
  }
  process.exitCode = 1;
});
