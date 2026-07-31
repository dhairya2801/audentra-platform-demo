import {
  createHash,
  randomBytes,
  randomUUID,
  scrypt as scryptCallback,
  timingSafeEqual,
} from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { promisify } from "node:util";
import {
  badRequest,
  unauthorized,
} from "./errors.js";

const scrypt = promisify(scryptCallback);
const schemaVersion = 1;
const sessionLifetimeMs = 8 * 60 * 60 * 1_000;
const maximumSessionsPerAccount = 5;
const bootstrapPasswordHashes = new Map();

export const localStaffPassword = "AsterStaff2027!";

export class StaffCredentialAuthStore {
  #state = null;
  #queue = Promise.resolve();

  constructor(filePath, clock = () => new Date(), password) {
    this.filePath = resolve(filePath);
    this.clock = clock;
    this.bootstrapPassword =
      password ??
      process.env.VV_STAFF_BOOTSTRAP_PASSWORD ??
      localStaffPassword;
  }

  async initialize(membersByTenant) {
    await mkdir(dirname(this.filePath), { recursive: true });
    try {
      const parsed = JSON.parse(await readFile(this.filePath, "utf8"));
      validateState(parsed);
      this.#state = parsed;
    } catch (error) {
      if (error?.code !== "ENOENT" && !(error instanceof SyntaxError)) {
        throw error;
      }
      this.#state = {
        schemaVersion,
        accounts: [],
        sessions: [],
      };
    }

    if (
      process.env.NODE_ENV === "production" &&
      !process.env.VV_STAFF_BOOTSTRAP_PASSWORD
    ) {
      throw new Error(
        "VV_STAFF_BOOTSTRAP_PASSWORD is required when the local staff credential adapter runs in production",
      );
    }

    let changed = false;
    for (const entry of membersByTenant) {
      for (const member of entry.members) {
        const email = normalizeEmail(member.email);
        const existing = this.#state.accounts.find(
          (account) =>
            account.tenantSlug === entry.tenantSlug &&
            account.staffId === member.id,
        );
        if (existing) {
          existing.displayName = member.name;
          existing.component = member.component;
          existing.email = email;
          continue;
        }
        const now = this.clock().toISOString();
        this.#state.accounts.push({
          id: randomUUID(),
          tenantSlug: entry.tenantSlug,
          staffId: member.id,
          email,
          displayName: member.name,
          component: member.component,
          role: "staff",
          passwordHash: await cachedBootstrapHash(this.bootstrapPassword),
          status: "active",
          createdAt: now,
          updatedAt: now,
        });
        changed = true;
      }
    }
    if (changed || !(await fileExists(this.filePath))) {
      await this.#write(this.#state);
    }
  }

  async signIn(input, tenantSlug) {
    if (!input || typeof input !== "object" || Array.isArray(input)) {
      throw badRequest(
        "INVALID_STAFF_SIGN_IN",
        "Enter a staff email and password",
      );
    }
    const email = normalizeEmail(input.email);
    const password =
      typeof input.password === "string" ? input.password : "";
    if (!password || password.length > 128) {
      throw unauthorized("Email or password is incorrect");
    }
    const state = this.#requireState();
    const account = state.accounts.find(
      (candidate) =>
        candidate.tenantSlug === tenantSlug &&
        candidate.email === email &&
        candidate.status === "active",
    );
    if (
      !account ||
      !(await verifyPassword(password, account.passwordHash))
    ) {
      throw unauthorized("Email or password is incorrect");
    }
    const now = this.clock();
    return this.#mutate((draft) => {
      const active = draft.sessions
        .filter(
          (session) =>
            session.accountId === account.id &&
            session.revokedAt === null &&
            Date.parse(session.expiresAt) > now.getTime(),
        )
        .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
      for (
        let index = 0;
        index <= active.length - maximumSessionsPerAccount;
        index += 1
      ) {
        const oldest = draft.sessions.find(
          (session) => session.id === active[index]?.id,
        );
        if (oldest) oldest.revokedAt = now.toISOString();
      }
      const session = createSession(account.id, now);
      draft.sessions.push(session.record);
      return {
        account: publicAccount(account),
        sessionToken: session.token,
        expiresAt: session.record.expiresAt,
      };
    });
  }

  getSession(token, tenantSlug) {
    if (typeof token !== "string" || token.length < 32) return null;
    const state = this.#requireState();
    const tokenHash = hashSessionToken(token);
    const now = this.clock().getTime();
    const session = state.sessions.find(
      (candidate) =>
        candidate.tokenHash === tokenHash &&
        candidate.revokedAt === null &&
        Date.parse(candidate.expiresAt) > now,
    );
    if (!session) return null;
    const account = state.accounts.find(
      (candidate) =>
        candidate.id === session.accountId &&
        candidate.tenantSlug === tenantSlug &&
        candidate.status === "active",
    );
    return account
      ? {
          account: publicAccount(account),
          session: structuredClone(session),
        }
      : null;
  }

  async signOut(token) {
    if (typeof token !== "string" || token.length < 32) return;
    const tokenHash = hashSessionToken(token);
    await this.#mutate((draft, transaction) => {
      const session = draft.sessions.find(
        (candidate) =>
          candidate.tokenHash === tokenHash && candidate.revokedAt === null,
      );
      if (!session) {
        transaction.skipWrite();
        return;
      }
      session.revokedAt = this.clock().toISOString();
    });
  }

  async #mutate(mutator) {
    const operation = this.#queue.then(async () => {
      const draft = structuredClone(this.#requireState());
      let shouldWrite = true;
      const result = await mutator(draft, {
        skipWrite() {
          shouldWrite = false;
        },
      });
      if (shouldWrite) {
        await this.#write(draft);
        this.#state = draft;
      }
      return structuredClone(result);
    });
    this.#queue = operation.catch(() => undefined);
    return operation;
  }

  #requireState() {
    if (!this.#state) {
      throw new Error("Staff credential store has not been initialized");
    }
    return this.#state;
  }

  async #write(state) {
    await mkdir(dirname(this.filePath), { recursive: true });
    const temporaryFile = `${this.filePath}.${process.pid}.tmp`;
    await writeFile(temporaryFile, `${JSON.stringify(state, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    await rename(temporaryFile, this.filePath);
  }
}

function normalizeEmail(value) {
  if (typeof value !== "string") {
    throw badRequest("INVALID_STAFF_EMAIL", "Enter a valid staff email address");
  }
  const email = value.normalize("NFKC").trim().toLowerCase();
  if (
    email.length < 3 ||
    email.length > 254 ||
    !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)
  ) {
    throw badRequest("INVALID_STAFF_EMAIL", "Enter a valid staff email address");
  }
  return email;
}

async function hashPassword(password) {
  const salt = randomBytes(16);
  const hash = await scrypt(password, salt, 64);
  return `scrypt-v1$${salt.toString("base64url")}$${Buffer.from(hash).toString("base64url")}`;
}

async function cachedBootstrapHash(password) {
  let pending = bootstrapPasswordHashes.get(password);
  if (!pending) {
    pending = hashPassword(password);
    bootstrapPasswordHashes.set(password, pending);
  }
  return pending;
}

async function verifyPassword(password, encoded) {
  const [version, saltText, hashText] = String(encoded).split("$");
  if (version !== "scrypt-v1" || !saltText || !hashText) return false;
  const expected = Buffer.from(hashText, "base64url");
  const actual = Buffer.from(
    await scrypt(password, Buffer.from(saltText, "base64url"), expected.length),
  );
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

function createSession(accountId, now) {
  const token = randomBytes(32).toString("base64url");
  return {
    token,
    record: {
      id: randomUUID(),
      accountId,
      tokenHash: hashSessionToken(token),
      createdAt: now.toISOString(),
      expiresAt: new Date(now.getTime() + sessionLifetimeMs).toISOString(),
      revokedAt: null,
    },
  };
}

function hashSessionToken(token) {
  return createHash("sha256").update(token).digest("hex");
}

function publicAccount(account) {
  return {
    id: account.id,
    tenantSlug: account.tenantSlug,
    staffId: account.staffId,
    email: account.email,
    displayName: account.displayName,
    component: account.component,
    role: account.role,
    status: account.status,
  };
}

function validateState(state) {
  if (
    !state ||
    state.schemaVersion !== schemaVersion ||
    !Array.isArray(state.accounts) ||
    !Array.isArray(state.sessions)
  ) {
    throw new Error("Staff credential state is incompatible");
  }
}

async function fileExists(filePath) {
  try {
    await readFile(filePath, "utf8");
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}
