import { randomUUID } from "node:crypto";
import { conflict, notFound, badRequest } from "./errors.js";
import { normalizeEdwardPageContext } from "./edward-safety.js";
import { exactKeys, objectBody, uuidValue } from "./validation.js";
import {
  emitVoiceEvent,
  incrementVoiceCounter,
} from "./voice-observability.js";

const assistantHistoryLimit = 6;
const assistantRetrievalLimit = 100;
const turnLeaseMilliseconds = 120_000;

export function validateCreateAssistantConversationInput(input) {
  const body = objectBody(input);
  exactKeys(body, ["pageContext"]);
  return {
    pageContext: normalizeAssistantPageContext(body.pageContext, true),
  };
}

export function validateAssistantTurnInput(input) {
  const body = objectBody(input);
  exactKeys(body, [
    "conversationId",
    "clientMessageId",
    "message",
    "inputMode",
    "pageContext",
    "history",
  ]);
  const hasConversationId = body.conversationId !== undefined;
  const hasClientMessageId = body.clientMessageId !== undefined;
  if (hasConversationId !== hasClientMessageId) {
    throw badRequest(
      "ASSISTANT_TURN_IDENTIFIERS_REQUIRED",
      "conversationId and clientMessageId must be supplied together",
    );
  }
  if (
    typeof body.message !== "string" ||
    body.message.trim().length < 1 ||
    body.message.length > 2_000
  ) {
    throw badRequest(
      "INVALID_MESSAGE",
      "message must contain 1-2000 characters",
    );
  }
  const inputMode = body.inputMode ?? "text";
  if (inputMode !== "text" && inputMode !== "voice") {
    throw badRequest(
      "INVALID_FIELD",
      "inputMode must be one of: text, voice",
    );
  }
  return {
    ...(hasConversationId
      ? {
          conversationId: uuidValue(body.conversationId, "conversationId"),
          clientMessageId: uuidValue(
            body.clientMessageId,
            "clientMessageId",
          ),
        }
      : {}),
    message: body.message.trim(),
    inputMode,
    pageContext: normalizeAssistantPageContext(body.pageContext),
    history: validateAssistantHistory(body.history),
  };
}

export function normalizeAssistantPageContext(value, requireObject = false) {
  if (typeof value === "string" && !requireObject) {
    if (value.length > 120) {
      throw badRequest(
        "INVALID_PAGE_CONTEXT",
        "pageContext must be a route string or an object with path and label",
      );
    }
    const path = normalizeEdwardPageContext(value);
    return { path, label: path };
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw badRequest(
      "INVALID_PAGE_CONTEXT",
      "pageContext must be a route string or an object with path and label",
    );
  }
  exactKeys(value, ["path", "label"]);
  if (
    typeof value.path !== "string" ||
    !/^\/[^\u0000-\u001f\u007f]{0,119}$/.test(value.path) ||
    typeof value.label !== "string" ||
    value.label.trim().length < 1 ||
    value.label.length > 120
  ) {
    throw badRequest(
      "INVALID_PAGE_CONTEXT",
      "pageContext must be a route string or an object with path and label",
    );
  }
  return {
    path: normalizeEdwardPageContext(value.path),
    label: value.label.trim().slice(0, 120),
  };
}

export async function createAssistantConversation({ store, clock }) {
  const conversation = {
    id: randomUUID(),
    status: "active",
    messages: [],
    createdAt: clock().toISOString(),
  };
  return store.transact((draft) => {
    draft.assistantConversations ??= [];
    draft.assistantConversations.push(conversation);
    return conversation;
  });
}

export function getAssistantConversation(state, conversationId) {
  const conversation = (state.assistantConversations ?? []).find(
    (candidate) => candidate.id === conversationId,
  );
  if (!conversation) throw assistantConversationNotFound();
  return conversation;
}

export function getAssistantConversationMessages(state, conversationId) {
  const conversation = getAssistantConversation(state, conversationId);
  return {
    conversationId,
    messages: structuredClone(
      (conversation.messages ?? []).slice(-assistantRetrievalLimit),
    ),
  };
}

export async function runAssistantTurn({
  store,
  clock,
  requestId,
  turn,
  generateResponse,
  metadata,
  logger,
}) {
  const legacy = turn.conversationId === undefined;
  const conversationId =
    turn.conversationId ??
    (
      await createAssistantConversation({
        store,
        clock,
      })
    ).id;
  const clientMessageId = turn.clientMessageId ?? randomUUID();
  const leaseExpiresAt = new Date(
    clock().getTime() + turnLeaseMilliseconds,
  ).toISOString();
  const claim = await store.transact((draft, transaction) => {
    const conversation = getAssistantConversation(draft, conversationId);
    if (conversation.status !== "active") {
      throw conflict(
        "ASSISTANT_CONVERSATION_CLOSED",
        "This assistant conversation is closed",
      );
    }
    conversation.messages ??= [];
    const existing = conversation.messages.find(
      (message) => message.clientMessageId === clientMessageId,
    );
    if (existing) {
      if (
        existing.content !== turn.message ||
        existing.inputMode !== turn.inputMode
      ) {
        throw conflict(
          "ASSISTANT_CLIENT_MESSAGE_CONFLICT",
          "This client message ID was already used for a different message",
        );
      }
      const assistantMessage = conversation.messages.find(
        (message) =>
          message.role === "assistant" &&
          message.metadata?.replyToMessageId === existing.id,
      );
      if (assistantMessage) {
        transaction.skipWrite();
        return {
          state: "completed",
          userMessage: existing,
          assistantMessage,
        };
      }
      if (
        existing.metadata?.turnState === "processing" &&
        typeof existing.metadata.turnLeaseExpiresAt === "string" &&
        Date.parse(existing.metadata.turnLeaseExpiresAt) > clock().getTime()
      ) {
        transaction.skipWrite();
        return { state: "in_progress", userMessage: existing };
      }
      existing.metadata = {
        ...existing.metadata,
        turnState: "processing",
        turnLeaseExpiresAt: leaseExpiresAt,
      };
      return { state: "claimed", userMessage: existing };
    }

    const userMessage = {
      id: randomUUID(),
      conversationId,
      role: "user",
      inputMode: turn.inputMode,
      content: turn.message,
      clientMessageId,
      requestId,
      provider: null,
      model: null,
      usage: null,
      contextReceipts: [],
      suggestedActions: [],
      widgets: [],
      metadata: {
        pageContext: turn.pageContext,
        ...(metadata ? structuredClone(metadata) : {}),
        turnState: "processing",
        turnLeaseExpiresAt: leaseExpiresAt,
      },
      createdAt: clock().toISOString(),
    };
    conversation.messages.push(userMessage);
    return { state: "claimed", userMessage };
  });

  const voiceSessionId = stringMetadata(metadata, "voiceSessionId");
  const streamId = stringMetadata(metadata, "livekitStreamId");
  emitVoiceEvent(logger, clock, "conversation_turn_claimed", {
    requestId,
    conversationId,
    voiceSessionId,
    clientMessageId,
    userMessageId: claim.userMessage.id,
    streamId,
    inputMode: turn.inputMode,
    idempotencyResult:
      claim.state === "completed"
        ? "replay"
        : claim.state === "in_progress"
          ? "in_progress"
          : "new_or_reclaimed",
  });
  if (claim.state === "completed") {
    incrementVoiceCounter(logger, clock, "idempotent_turn_replays", {
      requestId,
      conversationId,
      voiceSessionId,
      clientMessageId,
      userMessageId: claim.userMessage.id,
      inputMode: turn.inputMode,
    });
  }

  if (claim.state === "completed") {
    return responseFromMessages(claim.userMessage, claim.assistantMessage);
  }
  if (claim.state === "in_progress") {
    throw conflict(
      "ASSISTANT_TURN_IN_PROGRESS",
      "This assistant turn is already being processed",
    );
  }

  const conversationContext = legacy
    ? { history: turn.history.slice(-assistantHistoryLimit), priorPriority: null }
    : authoritativeContext(
        store.snapshot(),
        conversationId,
        claim.userMessage.id,
      );
  let response;
  try {
    response = await generateResponse({
      conversationId,
      message: turn.message,
      pageContext: turn.pageContext.path,
      assistantPageContext: turn.pageContext,
      history: conversationContext.history,
      priorPriority: conversationContext.priorPriority,
      inputMode: turn.inputMode,
      observability: {
        voiceSessionId,
        clientMessageId,
        userMessageId: claim.userMessage.id,
        streamId,
      },
    });
  } catch (error) {
    await store
      .transact((draft, transaction) => {
        const conversation = getAssistantConversation(draft, conversationId);
        const userMessage = conversation.messages.find(
          (message) => message.id === claim.userMessage.id,
        );
        if (!userMessage) {
          transaction.skipWrite();
          return;
        }
        delete userMessage.metadata.turnLeaseExpiresAt;
        userMessage.metadata.turnState = "retryable";
      })
      .catch(() => undefined);
    emitVoiceEvent(logger, clock, "backend_turn_failed", {
      requestId,
      conversationId,
      voiceSessionId,
      clientMessageId,
      userMessageId: claim.userMessage.id,
      streamId,
      inputMode: turn.inputMode,
      errorCategory: "backend_turn_failure",
    });
    throw error;
  }

  const assistantMessage = await store.transact((draft, transaction) => {
    const conversation = getAssistantConversation(draft, conversationId);
    const userMessage = conversation.messages.find(
      (message) => message.id === claim.userMessage.id,
    );
    if (!userMessage) throw assistantConversationNotFound();
    const existing = conversation.messages.find(
      (message) =>
        message.role === "assistant" &&
        message.metadata?.replyToMessageId === userMessage.id,
    );
    if (existing) {
      transaction.skipWrite();
      return existing;
    }
    delete userMessage.metadata.turnLeaseExpiresAt;
    userMessage.metadata.turnState = "completed";
    const assistant = {
      id: randomUUID(),
      conversationId,
      role: "assistant",
      inputMode: turn.inputMode,
      content: response.message,
      clientMessageId: null,
      requestId: userMessage.requestId,
      provider: response.provider,
      model: response.model,
      usage: response.usage,
      contextReceipts: response.contextReceipts,
      suggestedActions: response.suggestedActions,
      ...(response.blocks?.length ? { blocks: response.blocks } : {}),
      widgets: response.widgets,
      metadata: {
        replyToMessageId: userMessage.id,
        ...(response.studentAssistant
          ? { studentAssistant: structuredClone(response.studentAssistant) }
          : {}),
      },
      createdAt: clock().toISOString(),
    };
    conversation.messages.push(assistant);
    return assistant;
  });
  emitVoiceEvent(logger, clock, "canonical_messages_persisted", {
    requestId: claim.userMessage.requestId,
    conversationId,
    voiceSessionId,
    clientMessageId,
    userMessageId: claim.userMessage.id,
    assistantMessageId: assistantMessage.id,
    streamId,
    inputMode: turn.inputMode,
    graphVersion: response.studentAssistant?.graphExecution?.graphVersion,
  });
  return responseFromMessages(claim.userMessage, assistantMessage);
}

function stringMetadata(metadata, key) {
  const value = metadata?.[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function authoritativeContext(state, conversationId, currentUserMessageId) {
  const conversation = getAssistantConversation(state, conversationId);
  const messages = conversation.messages.filter(
    (message) => message.id !== currentUserMessageId,
  );
  const priorResponse = [...messages]
    .reverse()
    .find(
      (message) =>
        message.role === "assistant" &&
        message.metadata?.studentAssistant?.prioritizedAction,
    )?.metadata?.studentAssistant;
  return {
    history: messages
      .slice(-assistantHistoryLimit)
      .map((message) => ({ role: message.role, content: message.content })),
    priorPriority: priorResponse?.prioritizedAction
      ? {
          action: structuredClone(priorResponse.prioritizedAction),
          evidence: priorResponse.priorityEvidence
            ? structuredClone(priorResponse.priorityEvidence)
            : null,
        }
      : null,
  };
}

function responseFromMessages(userMessage, assistantMessage) {
  return {
    message: assistantMessage.content,
    provider: assistantMessage.provider,
    model: assistantMessage.model,
    usage: assistantMessage.usage,
    suggestedActions: assistantMessage.suggestedActions,
    contextReceipts: assistantMessage.contextReceipts,
    // Persisted with the message so rehydrated history renders through the same
    // component as a live turn, rather than silently degrading to plain text.
    ...(assistantMessage.blocks?.length
      ? { blocks: structuredClone(assistantMessage.blocks) }
      : {}),
    widgets: assistantMessage.widgets,
    conversationId: userMessage.conversationId,
    userMessageId: userMessage.id,
    assistantMessageId: assistantMessage.id,
    requestId: userMessage.requestId,
    ...(assistantMessage.metadata?.studentAssistant
      ? {
          studentAssistant: structuredClone(
            assistantMessage.metadata.studentAssistant,
          ),
        }
      : {}),
  };
}

function validateAssistantHistory(value) {
  if (value === undefined) return [];
  if (
    !Array.isArray(value) ||
    value.length > 8 ||
    value.some(
      (entry) =>
        !entry ||
        typeof entry !== "object" ||
        Array.isArray(entry) ||
        Object.keys(entry).some(
          (key) => key !== "role" && key !== "content",
        ) ||
        !["user", "assistant"].includes(entry.role) ||
        typeof entry.content !== "string" ||
        entry.content.length < 1 ||
        entry.content.length > 1_200,
    )
  ) {
    throw badRequest(
      "INVALID_HISTORY",
      "history must contain up to 8 short user or assistant messages",
    );
  }
  return structuredClone(value);
}

function assistantConversationNotFound() {
  return notFound(
    "ASSISTANT_CONVERSATION_NOT_FOUND",
    "No assistant conversation is available for this student",
  );
}
