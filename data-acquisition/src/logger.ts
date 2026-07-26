import { appendFile, mkdir } from "node:fs/promises";
import path from "node:path";

import { normalizeError } from "./errors.js";
import type {
  OperationContext,
  OperationLogRecord,
  OperationStatus,
} from "./types.js";


const sensitiveKeyPattern = /(api[-_]?key|authorization|cookie|password|secret|token)/i;
const sensitiveValuePattern = /\b(sk-[a-z0-9]{12,}|bearer\s+[a-z0-9._-]{12,})\b/gi;

export function redactSensitive(value: unknown): unknown {
  if (typeof value === "string") {
    return value.replace(sensitiveValuePattern, "[REDACTED]");
  }

  if (Array.isArray(value)) {
    return value.map((item) => redactSensitive(item));
  }

  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        sensitiveKeyPattern.test(key) ? "[REDACTED]" : redactSensitive(item),
      ]),
    );
  }

  return value;
}

interface StartedOperation {
  startedAt: string;
  startedTimestamp: number;
}

export class OperationLogger {
  private readonly sequences = new Map<string, number>();

  constructor(private readonly logsDirectory: string) {}

  async run<T>(
    context: OperationContext,
    operation: string,
    operationType: string,
    inputSummary: unknown,
    action: () => Promise<T>,
    summarizeOutput: (value: T) => unknown = (value) => value,
  ): Promise<T> {
    const started = await this.writeStarted(
      context,
      operation,
      operationType,
      inputSummary,
    );

    try {
      const result = await action();
      await this.writeFinished(
        context,
        operation,
        operationType,
        "SUCCESS",
        started,
        inputSummary,
        summarizeOutput(result),
        null,
        null,
      );
      return result;
    } catch (error) {
      const normalized = normalizeError(error);
      await this.writeFinished(
        context,
        operation,
        operationType,
        "FAILED",
        started,
        inputSummary,
        null,
        normalized.code,
        normalized.message,
      );
      throw normalized;
    }
  }

  private async writeStarted(
    context: OperationContext,
    operation: string,
    operationType: string,
    inputSummary: unknown,
  ): Promise<StartedOperation> {
    const now = new Date();
    const started: StartedOperation = {
      startedAt: now.toISOString(),
      startedTimestamp: now.getTime(),
    };

    await this.write({
      ...this.baseRecord(context, operation, operationType, "STARTED", now),
      message: `${operation} started`,
      inputSummary: redactSensitive(inputSummary),
      outputSummary: null,
      errorCode: null,
      errorMessage: null,
      startedAt: started.startedAt,
      finishedAt: null,
      durationMs: null,
    });

    return started;
  }

  private async writeFinished(
    context: OperationContext,
    operation: string,
    operationType: string,
    status: OperationStatus,
    started: StartedOperation,
    inputSummary: unknown,
    outputSummary: unknown,
    errorCode: string | null,
    errorMessage: string | null,
  ): Promise<void> {
    const now = new Date();
    await this.write({
      ...this.baseRecord(context, operation, operationType, status, now),
      message: `${operation} ${status.toLowerCase()}`,
      inputSummary: redactSensitive(inputSummary),
      outputSummary: redactSensitive(outputSummary),
      errorCode,
      errorMessage: errorMessage === null ? null : String(redactSensitive(errorMessage)),
      startedAt: started.startedAt,
      finishedAt: now.toISOString(),
      durationMs: now.getTime() - started.startedTimestamp,
    });
  }

  private baseRecord(
    context: OperationContext,
    operation: string,
    operationType: string,
    status: OperationStatus,
    now: Date,
  ): Pick<
    OperationLogRecord,
    | "taskId"
    | "stepId"
    | "sequenceNumber"
    | "module"
    | "operation"
    | "operationType"
    | "status"
    | "createdAt"
  > {
    return {
      taskId: context.taskId,
      stepId: context.stepId,
      sequenceNumber: this.nextSequence(context.taskId),
      module: operation === "chrome_connect" ? "CHROME_CDP" : "CHROME_MCP",
      operation,
      operationType,
      status,
      createdAt: now.toISOString(),
    };
  }

  private nextSequence(taskId: string): number {
    const next = (this.sequences.get(taskId) ?? 0) + 1;
    this.sequences.set(taskId, next);
    return next;
  }

  private async write(record: OperationLogRecord): Promise<void> {
    const taskDirectory = path.join(this.logsDirectory, record.taskId);
    const filePath = path.join(taskDirectory, "operations.jsonl");
    const serialized = `${JSON.stringify(record)}\n`;

    await mkdir(taskDirectory, { recursive: true });
    await appendFile(filePath, serialized, { encoding: "utf8", mode: 0o600 });
    process.stderr.write(serialized);
  }
}
