export {
  stateEffects,
  stateFieldOwnership,
} from "./registry";
export {
  buildStateEffectGraph,
  renderStateEffectMermaid,
} from "./graph";
export {
  validateStateEffectRegistry,
  type RegistryIssue,
} from "./validate";
export type {
  DomainOwner,
  IdempotencyContract,
  StateEffect,
  StateEffectGraph,
  StateEffectImplementationStatus,
  StateEffectKind,
  StateFieldOwnership,
  TransactionContract,
} from "./types";

import { stateEffects } from "./registry";

export function findStateEffectByEvent(
  eventName: string,
): (typeof stateEffects)[number] | undefined {
  return stateEffects.find((effect) =>
    effect.emits.some((candidate) => candidate === eventName),
  );
}
