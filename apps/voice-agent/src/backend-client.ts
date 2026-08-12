import type {
  CanonicalAssistantResponse,
  FinalizedVoiceTurn,
  Logger,
  VoiceBackend,
  VoiceSessionBinding,
} from "./types.js";

export type BackendErrorCode =
  | "timeout"
  | "network"
  | "unauthorized"
  | "rejected"
  | "invalid_response";

export class VoiceBackendError extends Error {
  constructor(
    message: string,
    readonly code: BackendErrorCode,
    readonly retryable: boolean,
    readonly status: number | null = null,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "VoiceBackendError";
  }
}

interface BackendClientOptions {
  baseUrl: string;
  token: string;
  timeoutMs: number;
  maxAttempts: number;
  retryBaseMs: number;
  logger: Logger;
  fetch?: typeof fetch;
  sleep?: (milliseconds: number) => Promise<void>;
}

interface RequestOptions {
  method: "GET" | "POST";
  path: string;
  body?: unknown;
  retry: boolean;
}

export class VoiceBackendClient implements VoiceBackend {
  private readonly fetch: typeof fetch;
  private readonly sleep: (milliseconds: number) => Promise<void>;

  constructor(private readonly options: BackendClientOptions) {
    this.fetch = options.fetch ?? globalThis.fetch;
    this.sleep =
      options.sleep ??
      ((milliseconds) =>
        new Promise((resolve) => setTimeout(resolve, milliseconds)));
  }

  async lookupSession(voiceSessionId: string): Promise<VoiceSessionBinding> {
    const value = await this.request({
      method: "GET",
      path: this.sessionPath(voiceSessionId),
      retry: true,
    });
    return parseSessionBinding(value);
  }

  async submitFinalizedTurn(
    voiceSessionId: string,
    turn: FinalizedVoiceTurn,
  ): Promise<CanonicalAssistantResponse> {
    const value = await this.request({
      method: "POST",
      path: `${this.sessionPath(voiceSessionId)}/turns`,
      body: turn,
      retry: true,
    });
    return parseCanonicalResponse(value);
  }

  async endSession(voiceSessionId: string): Promise<VoiceSessionBinding> {
    const value = await this.request({
      method: "POST",
      path: `${this.sessionPath(voiceSessionId)}/end`,
      retry: true,
    });
    return parseSessionBinding(value);
  }

  private sessionPath(voiceSessionId: string): string {
    return `/internal/assistant/voice-sessions/${encodeURIComponent(voiceSessionId)}`;
  }

  private async request(options: RequestOptions): Promise<unknown> {
    const attempts = options.retry ? this.options.maxAttempts : 1;
    let lastError: VoiceBackendError | null = null;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      try {
        return await this.requestOnce(options);
      } catch (error) {
        const normalized = normalizeBackendError(error);
        lastError = normalized;
        if (!normalized.retryable || attempt === attempts) throw normalized;
        this.options.logger.warn("voice_backend_request_retrying", {
          path: options.path,
          attempt,
          maxAttempts: attempts,
          errorCode: normalized.code,
          status: normalized.status,
        });
        await this.sleep(this.options.retryBaseMs * attempt);
      }
    }
    throw lastError ?? new VoiceBackendError(
      "Voice backend request failed",
      "network",
      false,
    );
  }

  private async requestOnce(options: RequestOptions): Promise<unknown> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.options.timeoutMs);
    timeout.unref();
    try {
      const response = await this.fetch(`${this.options.baseUrl}${options.path}`, {
        method: options.method,
        headers: {
          authorization: `Bearer ${this.options.token}`,
          accept: "application/json",
          ...(options.body === undefined
            ? {}
            : { "content-type": "application/json" }),
        },
        ...(options.body === undefined
          ? {}
          : { body: JSON.stringify(options.body) }),
        signal: controller.signal,
      });
      const value = await parseJson(response);
      if (!response.ok) {
        const backendCode = apiErrorCode(value);
        if (response.status === 401 || response.status === 403) {
          throw new VoiceBackendError(
            `Voice backend authorization failed${backendCode ? ` (${backendCode})` : ""}`,
            "unauthorized",
            false,
            response.status,
          );
        }
        const retryable =
          response.status === 408 ||
          response.status === 429 ||
          response.status >= 500;
        throw new VoiceBackendError(
          `Voice backend rejected the request${backendCode ? ` (${backendCode})` : ""}`,
          "rejected",
          retryable,
          response.status,
        );
      }
      return value;
    } catch (error) {
      if (
        controller.signal.aborted &&
        error instanceof VoiceBackendError &&
        error.code === "invalid_response"
      ) {
        throw new VoiceBackendError(
          "Voice backend request timed out",
          "timeout",
          true,
          null,
          { cause: error },
        );
      }
      if (error instanceof VoiceBackendError) throw error;
      if (controller.signal.aborted) {
        throw new VoiceBackendError(
          "Voice backend request timed out",
          "timeout",
          true,
          null,
          { cause: error },
        );
      }
      throw new VoiceBackendError(
        "Voice backend network request failed",
        "network",
        true,
        null,
        { cause: error },
      );
    } finally {
      clearTimeout(timeout);
    }
  }
}

async function parseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch (error) {
    throw new VoiceBackendError(
      "Voice backend returned invalid JSON",
      "invalid_response",
      false,
      response.status,
      { cause: error },
    );
  }
}

function normalizeBackendError(error: unknown): VoiceBackendError {
  return error instanceof VoiceBackendError
    ? error
    : new VoiceBackendError(
        "Voice backend request failed",
        "network",
        true,
        null,
        { cause: error },
      );
}

function apiErrorCode(value: unknown): string | null {
  if (!isRecord(value) || !isRecord(value.error)) return null;
  return typeof value.error.code === "string" ? value.error.code : null;
}

function parseSessionBinding(value: unknown): VoiceSessionBinding {
  if (
    !isRecord(value) ||
    !nonempty(value.voiceSessionId) ||
    !nonempty(value.conversationId) ||
    value.provider !== "livekit" ||
    !nonempty(value.roomName) ||
    !nonempty(value.participantIdentity) ||
    !isPageContext(value.pageContext) ||
    (value.status !== "active" && value.status !== "ended") ||
    !nonempty(value.expiresAt) ||
    !(value.endedAt === null || nonempty(value.endedAt)) ||
    !nonempty(value.createdAt)
  ) {
    throw new VoiceBackendError(
      "Voice backend returned an invalid session binding",
      "invalid_response",
      false,
    );
  }
  return value as unknown as VoiceSessionBinding;
}

function parseCanonicalResponse(value: unknown): CanonicalAssistantResponse {
  if (
    !isRecord(value) ||
    !nonempty(value.conversationId) ||
    !nonempty(value.userMessageId) ||
    !nonempty(value.assistantMessageId) ||
    !nonempty(value.requestId) ||
    !nonempty(value.message) ||
    !Array.isArray(value.suggestedActions) ||
    !Array.isArray(value.contextReceipts) ||
    !Array.isArray(value.widgets)
  ) {
    throw new VoiceBackendError(
      "Voice backend returned an invalid canonical response",
      "invalid_response",
      false,
    );
  }
  return value as unknown as CanonicalAssistantResponse;
}

function isPageContext(value: unknown): boolean {
  return (
    isRecord(value) && nonempty(value.path) && typeof value.label === "string"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonempty(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
