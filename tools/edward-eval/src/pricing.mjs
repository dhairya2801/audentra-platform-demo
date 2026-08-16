/**
 * Evaluation-only model pricing (USD per token).
 *
 * This is eval configuration, deliberately outside application code: the
 * platform never needs provider prices, only the harness does, and provider
 * prices drift. Update here when the provider updates; runs record which
 * pricing table produced their cost figures.
 */

export const PRICING_VERSION = "2026-08-12";

export const MODEL_PRICING = Object.freeze({
  "gpt-4o-mini": { input: 0.15 / 1_000_000, output: 0.6 / 1_000_000 },
  // The API echoes dated snapshot ids for the same model and price.
  "gpt-4o-mini-2024-07-18": { input: 0.15 / 1_000_000, output: 0.6 / 1_000_000 },
});

export function priceUsd(model, promptTokens, completionTokens) {
  const pricing = MODEL_PRICING[model] ?? worstCasePricing();
  return (
    (promptTokens ?? 0) * pricing.input + (completionTokens ?? 0) * pricing.output
  );
}

/** Unknown models cost the highest known rate, never zero. */
export function worstCasePricing() {
  return Object.values(MODEL_PRICING).reduce(
    (worst, pricing) =>
      pricing.input + pricing.output > worst.input + worst.output
        ? pricing
        : worst,
    { input: 0, output: 0 },
  );
}
