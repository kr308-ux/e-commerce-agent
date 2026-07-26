import path from "node:path";

import { z } from "zod";


const configSchema = z.object({
  CHROME_CDP_URL: z.url().default("http://127.0.0.1:9333"),
  CHROME_TARGET_URL: z
    .url()
    .default("https://www.chuhaijiang.com/app/discover/tiktok/products?country=US"),
  CHROME_OPERATION_TIMEOUT_MS: z.coerce.number().int().positive().default(15_000),
  CHROME_DOWNLOAD_TIMEOUT_MS: z.coerce.number().int().positive().default(60_000),
  LOG_LEVEL: z.string().default("INFO"),
  LOGS_DIR: z.string().optional(),
  EXPORTS_DIR: z.string().optional(),
});

export interface AppConfig {
  chromeCdpUrl: string;
  chromeTargetUrl: string;
  operationTimeoutMs: number;
  downloadTimeoutMs: number;
  logLevel: string;
  logsDir: string;
  exportsDir: string;
}

export function loadConfig(environment: NodeJS.ProcessEnv = process.env): AppConfig {
  const parsed = configSchema.parse(environment);
  const initialDirectory = environment.INIT_CWD ?? process.cwd();

  return {
    chromeCdpUrl: parsed.CHROME_CDP_URL,
    chromeTargetUrl: parsed.CHROME_TARGET_URL,
    operationTimeoutMs: parsed.CHROME_OPERATION_TIMEOUT_MS,
    downloadTimeoutMs: parsed.CHROME_DOWNLOAD_TIMEOUT_MS,
    logLevel: parsed.LOG_LEVEL,
    logsDir:
      parsed.LOGS_DIR === undefined
        ? path.resolve(initialDirectory, "logs", "data-acquisition")
        : path.resolve(parsed.LOGS_DIR),
    exportsDir:
      parsed.EXPORTS_DIR === undefined
        ? path.resolve(initialDirectory, "storage", "exports")
        : path.resolve(parsed.EXPORTS_DIR),
  };
}
