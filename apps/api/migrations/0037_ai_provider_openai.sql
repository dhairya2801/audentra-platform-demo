-- Allow direct-OpenAI rows in the AI provider diagnostic journal.
--
-- The assistant planner and composer use api.openai.com whenever OPENAI_API_KEY
-- is configured. The original provider constraint admitted only OpenRouter and
-- Groq, which caused direct-OpenAI telemetry writes to be rejected.

ALTER TABLE ai_provider_response_attempt
  DROP CONSTRAINT IF EXISTS ai_provider_response_attempt_provider_check;

ALTER TABLE ai_provider_response_attempt
  ADD CONSTRAINT ai_provider_response_attempt_provider_check
    CHECK (provider IN ('openrouter', 'groq', 'openai'));
