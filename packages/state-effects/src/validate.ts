import type {
  StateEffect,
  StateFieldOwnership,
} from "./types";

export interface RegistryIssue {
  code:
    | "DUPLICATE_EFFECT_ID"
    | "DUPLICATE_FIELD_OWNER"
    | "EVENT_HANDLER_WITHOUT_DEDUPLICATION"
    | "MISSING_FIELD_OWNER"
    | "SYNCHRONOUS_CYCLE"
    | "UNKNOWN_SYNCHRONOUS_EFFECT";
  message: string;
}

export function validateStateEffectRegistry(input: {
  owners: readonly StateFieldOwnership[];
  effects: readonly StateEffect[];
}): RegistryIssue[] {
  const issues: RegistryIssue[] = [];
  const ownership = new Map<string, DomainOwnerRecord>();
  const effectById = new Map<string, StateEffect>();

  for (const entry of input.owners) {
    const prior = ownership.get(entry.field);
    if (prior) {
      issues.push({
        code: "DUPLICATE_FIELD_OWNER",
        message: `${entry.field} is owned by both ${prior.owner} and ${entry.owner}`,
      });
      continue;
    }
    ownership.set(entry.field, entry);
  }

  for (const effect of input.effects) {
    if (effectById.has(effect.id)) {
      issues.push({
        code: "DUPLICATE_EFFECT_ID",
        message: `Duplicate state effect id: ${effect.id}`,
      });
    }
    effectById.set(effect.id, effect);
    if (
      effect.kind === "event_handler" &&
      effect.consumes.length > 0 &&
      (!effect.idempotency.required ||
        !effect.idempotency.key ||
        effect.idempotency.scope !== "event")
    ) {
      issues.push({
        code: "EVENT_HANDLER_WITHOUT_DEDUPLICATION",
        message: `${effect.id} consumes events without an event-scoped deduplication key`,
      });
    }
    for (const field of effect.writes) {
      const fieldOwner = ownership.get(field);
      if (!fieldOwner) {
        issues.push({
          code: "MISSING_FIELD_OWNER",
          message: `${effect.id} writes ${field}, which has no declared owner`,
        });
      }
    }
  }

  for (const effect of input.effects) {
    for (const target of effect.synchronousCalls) {
      if (!effectById.has(target)) {
        issues.push({
          code: "UNKNOWN_SYNCHRONOUS_EFFECT",
          message: `${effect.id} synchronously calls unknown effect ${target}`,
        });
      }
    }
  }

  const visiting = new Set<string>();
  const visited = new Set<string>();
  const stack: string[] = [];
  const reportedCycles = new Set<string>();

  const visit = (effectId: string): void => {
    if (visiting.has(effectId)) {
      const start = stack.indexOf(effectId);
      const cycle = [...stack.slice(start), effectId];
      const key = cycle.join(" -> ");
      if (!reportedCycles.has(key)) {
        reportedCycles.add(key);
        issues.push({
          code: "SYNCHRONOUS_CYCLE",
          message: `Synchronous state-effect cycle: ${key}`,
        });
      }
      return;
    }
    if (visited.has(effectId)) return;
    visiting.add(effectId);
    stack.push(effectId);
    const effect = effectById.get(effectId);
    for (const target of effect?.synchronousCalls ?? []) {
      if (effectById.has(target)) visit(target);
    }
    stack.pop();
    visiting.delete(effectId);
    visited.add(effectId);
  };

  for (const effect of input.effects) visit(effect.id);
  return issues;
}

type DomainOwnerRecord = StateFieldOwnership;
