import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";
import { CredentialAuthStore } from "../src/credential-auth-store.js";
import { StudentStoreRegistry } from "../src/student-store-registry.js";

describe("CredentialAuthStore", () => {
  it("hashes credentials and session tokens while preserving verification state", async () => {
    const directory = await mkdtemp(join(tmpdir(), "vv-credential-auth-"));
    const filePath = join(directory, "auth.json");
    const store = new CredentialAuthStore(filePath);
    await store.initialize();

    const created = await store.signUp({
      email: "  Student@Example.com ",
      phone: "+1 (555) 123-4567",
      password: "correct-horse-123",
    });

    assert.equal(created.account.email, "student@example.com");
    assert.equal(created.account.phone, "+15551234567");
    assert.equal(created.account.emailVerified, false);
    assert.equal(created.account.phoneVerified, false);
    assert.equal(store.getSession(created.sessionToken)?.account.id, created.account.id);

    const persisted = await readFile(filePath, "utf8");
    assert.doesNotMatch(persisted, /correct-horse-123/);
    assert.doesNotMatch(persisted, new RegExp(created.sessionToken));
    assert.match(persisted, /scrypt-v1/);

    await store.signOut(created.sessionToken);
    assert.equal(store.getSession(created.sessionToken), null);

    const signedIn = await store.signIn({
      email: "student@example.com",
      password: "correct-horse-123",
    });
    assert.equal(signedIn.account.studentId, created.account.studentId);
    assert.ok(store.getSession(signedIn.sessionToken));
  });

  it("rejects duplicate contacts and invalid passwords without leaking account lookup", async () => {
    const directory = await mkdtemp(join(tmpdir(), "vv-credential-auth-"));
    const store = new CredentialAuthStore(join(directory, "auth.json"));
    await store.initialize();
    await store.signUp({
      email: "student@example.com",
      phone: "+15551234567",
      password: "correct-horse-123",
    });

    await assert.rejects(
      () =>
        store.signUp({
          email: "STUDENT@example.com",
          phone: "+15557654321",
          password: "another-secure-123",
        }),
      (error) => error?.status === 409 && error?.code === "AUTH_EMAIL_EXISTS",
    );
    await assert.rejects(
      () =>
        store.signIn({
          email: "missing@example.com",
          password: "incorrect-password",
        }),
      (error) =>
        error?.status === 401 &&
        error?.message === "Email or password is incorrect",
    );
  });

  it("scopes duplicate credentials and sessions to a university tenant", async () => {
    const directory = await mkdtemp(join(tmpdir(), "vv-credential-auth-"));
    const store = new CredentialAuthStore(join(directory, "auth.json"));
    await store.initialize();
    const input = {
      email: "student@example.com",
      phone: "+15551234567",
      password: "correct-horse-123",
    };

    const aster = await store.signUp(input, "aster");
    const harvard = await store.signUp(input, "harvard");

    assert.equal(aster.account.tenantSlug, "aster");
    assert.equal(harvard.account.tenantSlug, "harvard");
    assert.notEqual(aster.account.id, harvard.account.id);
    assert.equal(store.getSession(aster.sessionToken, "harvard"), null);
    assert.equal(store.getSession(harvard.sessionToken, "aster"), null);
    assert.equal(
      store.getSession(aster.sessionToken, "aster")?.account.id,
      aster.account.id,
    );
  });
});

describe("StudentStoreRegistry", () => {
  it("creates an isolated durable student record for each credential account", async () => {
    const directory = await mkdtemp(join(tmpdir(), "vv-student-registry-"));
    const registry = new StudentStoreRegistry(join(directory, "students"));
    await registry.initialize();
    const first = {
      id: "account-a",
      actorId: "1c57c780-a6e2-43a9-9d64-d060cb27ead1",
      studentId: "08eff694-ed2f-4db0-ae23-7ddb91bfb3df",
      email: "first@example.com",
      phone: "+15551230001",
    };
    const second = {
      id: "account-b",
      actorId: "8eca61fc-a459-4a3b-aa56-83fdeba5fbbb",
      studentId: "f8d73ddf-eb7b-4c16-9c16-53e6e98bad09",
      email: "second@example.com",
      phone: "+15551230002",
    };

    const firstStore = await registry.get(first);
    const secondStore = await registry.get(second);
    await firstStore.transact((draft) => {
      draft.profile.firstName = "Ada";
      return null;
    });

    assert.equal(firstStore.snapshot().profile.studentId, first.studentId);
    assert.equal(firstStore.snapshot().profile.firstName, "Ada");
    assert.equal(secondStore.snapshot().profile.studentId, second.studentId);
    assert.equal(secondStore.snapshot().profile.firstName, "");
    assert.notEqual(firstStore.filePath, secondStore.filePath);
    assert.notEqual(firstStore.uploadDirectory, secondStore.uploadDirectory);
  });
});
