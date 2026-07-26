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
  conflict,
  unauthorized,
} from "./errors.js";
import { exactKeys, objectBody, requiredString } from "./validation.js";

const scrypt = promisify(scryptCallback);
const authSchemaVersion = 1;
const sessionLifetimeMs = 7 * 24 * 60 * 60 * 1_000;
const maximumSessionsPerAccount = 5;

export class CredentialAuthStore {
  #state = null;
  #queue = Promise.resolve();

  constructor(filePath, clock = () => new Date()) {
    this.filePath = resolve(filePath);
    this.clock = clock;
  }

  async initialize() {
    await mkdir(dirname(this.filePath), { recursive: true });
    try {
      const state = JSON.parse(await readFile(this.filePath, "utf8"));
      validateState(state);
      this.#state = state;
    } catch (error) {
      if (error?.code !== "ENOENT" && !(error instanceof SyntaxError)) {
        throw error;
      }
      this.#state = {
        schemaVersion: authSchemaVersion,
        accounts: [],
        sessions: [],
      };
      await this.#write(this.#state);
    }
  }

  listAccounts() {
    return structuredClone(this.#requireState().accounts);
  }

  async signUp(input) {
    const body = validateSignUp(input);
    const now = this.clock();
    const account = {
      id: randomUUID(),
      actorId: randomUUID(),
      studentId: randomUUID(),
      email: body.email,
      phone: body.phone,
      passwordHash: await hashPassword(body.password),
      emailVerifiedAt: null,
      phoneVerifiedAt: null,
      status: "active",
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
    };
    return this.#mutate((draft) => {
      if (draft.accounts.some((candidate) => candidate.email === account.email)) {
        throw conflict(
          "AUTH_EMAIL_EXISTS",
          "An account already exists for this email address",
        );
      }
      if (draft.accounts.some((candidate) => candidate.phone === account.phone)) {
        throw conflict(
          "AUTH_PHONE_EXISTS",
          "An account already exists for this phone number",
        );
      }
      draft.accounts.push(account);
      const session = createSession(account.id, now);
      draft.sessions.push(session.record);
      return {
        account: publicAccount(account),
        sessionToken: session.token,
        expiresAt: session.record.expiresAt,
      };
    });
  }

  async signIn(input) {
    const body = validateSignIn(input);
    const state = this.#requireState();
    const account = state.accounts.find(
      (candidate) => candidate.email === body.email,
    );
    if (
      !account ||
      account.status !== "active" ||
      !(await verifyPassword(body.password, account.passwordHash))
    ) {
      throw unauthorized("Email or password is incorrect");
    }
    const now = this.clock();
    return this.#mutate((draft) => {
      const activeSessions = draft.sessions
        .filter(
          (session) =>
            session.accountId === account.id &&
            session.revokedAt === null &&
            Date.parse(session.expiresAt) > now.getTime(),
        )
        .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
      for (
        let index = 0;
        index <= activeSessions.length - maximumSessionsPerAccount;
        index += 1
      ) {
        const oldest = draft.sessions.find(
          (session) => session.id === activeSessions[index]?.id,
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

  getSession(token) {
    if (typeof token !== "string" || token.length < 32) return null;
    const now = this.clock();
    const tokenHash = hashSessionToken(token);
    const state = this.#requireState();
    const session = state.sessions.find(
      (candidate) =>
        candidate.tokenHash === tokenHash &&
        candidate.revokedAt === null &&
        Date.parse(candidate.expiresAt) > now.getTime(),
    );
    if (!session) return null;
    const account = state.accounts.find(
      (candidate) =>
        candidate.id === session.accountId && candidate.status === "active",
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
      throw new Error("Credential auth store has not been initialized");
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

function validateSignUp(input) {
  const body = objectBody(input);
  exactKeys(body, ["email", "phone", "password"]);
  return {
    email: normalizeEmail(body.email),
    phone: normalizePhone(body.phone),
    password: validatePassword(body.password),
  };
}

function validateSignIn(input) {
  const body = objectBody(input);
  exactKeys(body, ["email", "password"]);
  return {
    email: normalizeEmail(body.email),
    password: requiredString(body.password, "password", {
      min: 1,
      max: 128,
    }),
  };
}

function normalizeEmail(value) {
  const email = requiredString(value, "email", { min: 3, max: 254 })
    .normalize("NFKC")
    .toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
    throw badRequest("INVALID_EMAIL", "Enter a valid email address");
  }
  return email;
}

function normalizePhone(value) {
  const compact = requiredString(value, "phone", { min: 8, max: 32 }).replace(
    /[ ()-]/g,
    "",
  );
  const phone = compact.startsWith("+") ? compact : `+${compact}`;
  if (!/^\+[1-9][0-9]{7,14}$/.test(phone)) {
    throw badRequest(
      "INVALID_PHONE",
      "Use an international phone number, for example +15551234567",
    );
  }
  return phone;
}

function validatePassword(value) {
  const password = requiredString(value, "password", { min: 12, max: 128 });
  if (!/[A-Za-z]/.test(password) || !/[0-9]/.test(password)) {
    throw badRequest(
      "WEAK_PASSWORD",
      "Use at least 12 characters including a letter and a number",
    );
  }
  return password;
}

async function hashPassword(password) {
  const salt = randomBytes(16);
  const hash = await scrypt(password, salt, 64);
  return `scrypt-v1$${salt.toString("base64url")}$${Buffer.from(hash).toString("base64url")}`;
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
    actorId: account.actorId,
    studentId: account.studentId,
    email: account.email,
    phone: account.phone,
    emailVerified: account.emailVerifiedAt !== null,
    phoneVerified: account.phoneVerifiedAt !== null,
    status: account.status,
    createdAt: account.createdAt,
  };
}

function validateState(state) {
  if (
    !state ||
    state.schemaVersion !== authSchemaVersion ||
    !Array.isArray(state.accounts) ||
    !Array.isArray(state.sessions)
  ) {
    throw new Error("Credential auth state is incompatible");
  }
}
