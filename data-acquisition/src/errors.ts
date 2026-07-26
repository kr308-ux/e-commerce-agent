import type { AgentErrorPayload, TaskStatus } from "./types.js";

export class AgentOperationError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly userMessage: string,
    public readonly retryable = false,
    public readonly status: TaskStatus = "FAILED",
  ) {
    super(message);
    this.name = "AgentOperationError";
  }

  toPayload(): AgentErrorPayload {
    return {
      code: this.code,
      message: this.message,
      userMessage: this.userMessage,
      retryable: this.retryable,
    };
  }
}

export function normalizeError(error: unknown): AgentOperationError {
  if (error instanceof AgentOperationError) {
    return error;
  }

  const message = error instanceof Error ? error.message : String(error);
  return new AgentOperationError(
    "UNEXPECTED_ERROR",
    message,
    "服务执行失败，请查看操作日志后重试。",
  );
}
