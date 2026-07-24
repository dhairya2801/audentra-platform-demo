export const domainOwners = [
  "admissions",
  "onboarding",
  "documents",
  "academics",
  "enrollment",
  "financials",
  "student-profile",
] as const;

export type DomainOwner = (typeof domainOwners)[number];

export type StateEffectKind =
  | "command"
  | "event_handler"
  | "projection";

export interface StateFieldOwnership {
  field: string;
  owner: DomainOwner;
  description: string;
  classification: "public" | "internal" | "sensitive";
}

export interface IdempotencyContract {
  required: boolean;
  key: string | null;
  scope: "request" | "aggregate" | "event" | "not_applicable";
}

export interface TransactionContract {
  boundary: "none" | "single_database" | "database_and_outbox";
  isolation: "database_default" | "serializable";
}

export interface StateEffect {
  id: string;
  version: 1;
  owner: DomainOwner;
  kind: StateEffectKind;
  handler: string;
  description: string;
  reads: readonly string[];
  writes: readonly string[];
  emits: readonly string[];
  consumes: readonly string[];
  synchronousCalls: readonly string[];
  idempotency: IdempotencyContract;
  transaction: TransactionContract;
}

export interface StateEffectGraph {
  schemaVersion: 1;
  generatedAt: string;
  warning: string;
  owners: readonly StateFieldOwnership[];
  effects: readonly StateEffect[];
  nodes: Array<{
    id: string;
    type: "effect" | "field" | "event" | "owner";
    label: string;
    owner?: DomainOwner;
  }>;
  edges: Array<{
    from: string;
    to: string;
    type: "owns" | "reads" | "writes" | "emits" | "consumes" | "calls";
    runtimeEvidence: boolean;
  }>;
}
