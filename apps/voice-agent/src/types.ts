import type {
  AssistantPageContext,
  AssistantVoiceSessionDetails,
  AskEdwardResponse,
} from "@vv/contracts";

export interface Logger {
  debug(message: string, fields?: Record<string, unknown>): void;
  info(message: string, fields?: Record<string, unknown>): void;
  warn(message: string, fields?: Record<string, unknown>): void;
  error(message: string, fields?: Record<string, unknown>): void;
}

export type VoiceSessionBinding = AssistantVoiceSessionDetails;

export type CanonicalAssistantResponse = AskEdwardResponse & {
  studentAssistant?: unknown;
};

export interface FinalizedVoiceTurn {
  clientMessageId: string;
  text: string;
  inputMode: "voice";
  pageContext: AssistantPageContext;
  livekitStreamId: string;
}

export interface VoiceBackend {
  lookupSession(voiceSessionId: string): Promise<VoiceSessionBinding>;
  submitFinalizedTurn(
    voiceSessionId: string,
    turn: FinalizedVoiceTurn,
  ): Promise<CanonicalAssistantResponse>;
  endSession(voiceSessionId: string): Promise<VoiceSessionBinding>;
}
