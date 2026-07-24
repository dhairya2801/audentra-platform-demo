import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, it } from "node:test";
import { createDemoApi } from "../src/http-api.js";
import { ids, ONBOARDING_STEPS } from "../src/seed.js";
import { JsonStateStore } from "../src/store.js";

const fixedClock = () => new Date("2026-07-24T12:00:00.000Z");
const servers = [];

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) =>
        new Promise((resolve) => server.close(() => resolve())),
    ),
  );
});

async function startPreview() {
  const directory = await mkdtemp(join(tmpdir(), "vv-demo-api-"));
  const dataFile = join(directory, "state.json");
  const store = new JsonStateStore(dataFile, fixedClock);
  const { server } = await createDemoApi({
    store,
    clock: fixedClock,
    logger: null,
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  servers.push(server);
  const address = server.address();
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    dataFile,
    store,
  };
}

async function api(baseUrl, path, options = {}) {
  const headers = {
    ...(options.authenticated === false
      ? {}
      : { cookie: "vv_demo_session=demo-session" }),
    ...(options.body === undefined
      ? {}
      : { "content-type": "application/json" }),
    ...(options.idempotencyKey
      ? { "idempotency-key": options.idempotencyKey }
      : {}),
    ...(options.headers ?? {}),
  };
  const response = await fetch(`${baseUrl}${path}`, {
    method: options.method ?? "GET",
    headers,
    body:
      options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const payload = await response.json();
  return { response, payload };
}

async function putOnboarding(baseUrl, onboarding, data = {}) {
  return api(baseUrl, "/v1/student/onboarding", {
    method: "PUT",
    body: {
      expectedVersion: onboarding.version,
      currentStep: onboarding.currentStep,
      data,
    },
  });
}

describe("contract-compatible development preview API", () => {
  it("runs the frontend enrollment sequence with canonical contract shapes", async () => {
    const { baseUrl, store } = await startPreview();

    const anonymousBootstrap = await api(baseUrl, "/v1/student/bootstrap", {
      authenticated: false,
    });
    assert.equal(anonymousBootstrap.response.status, 401);
    assert.equal(anonymousBootstrap.payload.error.code, "UNAUTHORIZED");

    const signIn = await api(baseUrl, "/v1/auth/demo/sign-in", {
      authenticated: false,
      method: "POST",
      body: {},
    });
    assert.equal(signIn.response.status, 200);
    assert.equal(signIn.payload.authenticated, true);
    assert.match(
      signIn.response.headers.get("set-cookie"),
      /^vv_demo_session=demo-session;/,
    );

    const bootstrap = await api(baseUrl, "/v1/student/bootstrap");
    assert.deepEqual(Object.keys(bootstrap.payload).sort(), [
      "authenticated",
      "generatedAt",
      "initialRoute",
      "onboarding",
      "student",
    ]);
    assert.equal(bootstrap.payload.authenticated, true);
    assert.equal(bootstrap.payload.initialRoute, "/onboarding");
    assert.deepEqual(bootstrap.payload.onboarding, {
      required: true,
      status: "in_progress",
      currentStep: "offer",
      version: 1,
    });

    let onboarding = (await api(
      baseUrl,
      "/v1/student/onboarding",
    )).payload;
    assert.deepEqual(Object.keys(onboarding).sort(), [
      "completedAt",
      "completedSteps",
      "currentStep",
      "data",
      "status",
      "studentId",
      "updatedAt",
      "version",
    ]);
    assert.equal(onboarding.currentStep, "offer");
    assert.equal(onboarding.version, 1);

    const blockedOfferStep = await putOnboarding(baseUrl, onboarding);
    assert.equal(blockedOfferStep.response.status, 409);
    assert.equal(
      blockedOfferStep.payload.error.code,
      "ACCEPTED_OFFER_REQUIRED",
    );
    assert.equal(store.snapshot().onboarding.version, 1);

    const accepted = await api(
      baseUrl,
      `/v1/admission-offers/${ids.offer}/accept`,
      {
        method: "POST",
        body: {},
        idempotencyKey: "accept-offer-0001",
      },
    );
    assert.equal(accepted.response.status, 200);
    assert.equal(accepted.payload.offerStatus, "accepted");
    assert.equal(store.snapshot().onboarding.currentStep, "offer");
    assert.equal(store.snapshot().onboarding.version, 1);

    const offerStep = await putOnboarding(baseUrl, onboarding);
    assert.equal(offerStep.response.status, 200);
    onboarding = offerStep.payload;
    assert.equal(onboarding.currentStep, "about_you");
    assert.deepEqual(onboarding.completedSteps, ["offer"]);
    assert.equal(onboarding.version, 2);

    const stale = await api(baseUrl, "/v1/student/onboarding", {
      method: "PUT",
      body: {
        expectedVersion: 1,
        currentStep: "about_you",
        data: {},
      },
    });
    assert.equal(stale.response.status, 409);
    assert.equal(stale.payload.error.code, "VERSION_CONFLICT");

    const middleStepData = {
      about_you: {
        legalNameConfirmed: true,
        contactInformationConfirmed: true,
        homeAddressConfirmed: true,
        communicationPreference: "email",
        residencyStatus: "domestic",
      },
      housing: { housingPreference: "undecided" },
      campus_life: {
        campusInterests: ["Robotics", "Student radio"],
        supportNeeds: [],
      },
      emergency_contacts: { emergencyContactConfirmed: true },
      other_records: { recordsConfirmed: true },
      family_permissions: { familyPermissionsReviewed: true },
      review_and_sign: { signatureConfirmed: true },
    };
    for (const step of ONBOARDING_STEPS.slice(1, -1)) {
      assert.equal(onboarding.currentStep, step);
      const updated = await putOnboarding(
        baseUrl,
        onboarding,
        middleStepData[step],
      );
      assert.equal(updated.response.status, 200);
      onboarding = updated.payload;
    }
    assert.equal(onboarding.currentStep, "deposit");
    assert.equal(onboarding.version, 9);

    const blockedDepositStep = await putOnboarding(baseUrl, onboarding, {
      depositAcknowledged: true,
    });
    assert.equal(blockedDepositStep.response.status, 409);
    assert.equal(blockedDepositStep.payload.error.code, "DEPOSIT_REQUIRED");
    assert.equal(store.snapshot().onboarding.version, 9);

    const paymentsBefore = await api(baseUrl, "/v1/student/payments");
    assert.deepEqual(paymentsBefore.payload, { items: [], total: 0 });
    const payment = await api(baseUrl, "/v1/student/payments/deposit", {
      method: "POST",
      body: { offerId: ids.offer },
      idempotencyKey: "deposit-pay-0001",
    });
    assert.equal(payment.payload.offerId, ids.offer);
    assert.equal(payment.payload.type, "enrollment_deposit");
    assert.equal(payment.payload.amountCents, 50000);
    assert.equal(payment.payload.status, "succeeded");
    assert.equal(payment.payload.processor, "dummy");
    assert.match(payment.payload.processorReference, /^dummy_/);
    assert.equal(store.snapshot().onboarding.version, 9);

    const depositStep = await putOnboarding(baseUrl, onboarding, {
      depositAcknowledged: true,
    });
    onboarding = depositStep.payload;
    assert.equal(onboarding.version, 10);
    assert.equal(onboarding.status, "in_progress");
    assert.deepEqual(onboarding.completedSteps, ONBOARDING_STEPS);

    const complete = await api(
      baseUrl,
      "/v1/student/onboarding/complete",
      {
        method: "POST",
        body: { expectedVersion: onboarding.version },
        idempotencyKey: "complete-onboarding-01",
      },
    );
    assert.equal(complete.payload.status, "completed");
    assert.equal(complete.payload.version, 11);
    assert.ok(complete.payload.completedAt);

    const completedBootstrap = await api(
      baseUrl,
      "/v1/student/bootstrap",
    );
    assert.equal(completedBootstrap.payload.initialRoute, "/dashboard");
    assert.equal(completedBootstrap.payload.onboarding.required, false);

    const requirements = await api(baseUrl, "/v1/student/requirements");
    assert.equal(requirements.payload.total, 3);
    const identity = requirements.payload.items.find(
      (requirement) => requirement.code === "identity_document",
    );
    assert.equal(identity.journeyId, ids.journey);
    assert.equal(identity.submissionType, "document");
    assert.equal(identity.responsibleOffice, "Enrollment Documentation");
    assert.deepEqual(identity.dependencyCodes, ["profile_verification"]);
    const detail = await api(
      baseUrl,
      `/v1/student/requirements/${identity.id}`,
    );
    assert.deepEqual(detail.payload, identity);

    const document = await api(baseUrl, "/v1/student/documents", {
      method: "POST",
      body: {
        fileName: "fictional-demo-id.pdf",
        mimeType: "application/pdf",
        sizeBytes: 1_200_000,
        category: "identity",
      },
      idempotencyKey: "document-meta-01",
    });
    assert.equal(document.response.status, 201);
    assert.deepEqual(
      Object.keys(document.payload).sort(),
      [
        "category",
        "createdAt",
        "fileName",
        "id",
        "mimeType",
        "sizeBytes",
        "status",
      ],
    );
    assert.equal(document.payload.status, "placeholder");
    const documents = await api(baseUrl, "/v1/student/documents");
    assert.equal(documents.payload.total, 1);

    const appointment = await api(
      baseUrl,
      "/v1/student/appointments",
      {
        method: "POST",
        body: {
          type: "enrollment_support",
          startsAt: "2027-01-10T15:00:00.000Z",
          notes: "Question about the fictional preview.",
        },
        idempotencyKey: "appointment-0001",
      },
    );
    assert.equal(appointment.response.status, 201);
    assert.equal(appointment.payload.type, "enrollment_support");
    assert.equal(appointment.payload.status, "scheduled");
    const appointments = await api(baseUrl, "/v1/student/appointments");
    assert.equal(appointments.payload.total, 1);

    const messages = await api(baseUrl, "/v1/student/messages");
    const unread = messages.payload.items.find((message) => !message.readAt);
    assert.deepEqual(Object.keys(unread).sort(), [
      "body",
      "id",
      "readAt",
      "senderName",
      "sentAt",
      "subject",
    ]);
    const read = await api(
      baseUrl,
      `/v1/student/messages/${unread.id}/read`,
      { method: "POST" },
    );
    assert.equal(read.response.status, 200);
    assert.ok(read.payload.readAt);
    const readRevision = store.snapshot().fixture.revision;
    const readReplay = await api(
      baseUrl,
      `/v1/student/messages/${unread.id}/read`,
      { method: "POST" },
    );
    assert.deepEqual(readReplay.payload, read.payload);
    assert.equal(store.snapshot().fixture.revision, readRevision);

    const profile = await api(baseUrl, "/v1/student/profile");
    assert.deepEqual(Object.keys(profile.payload).sort(), [
      "communicationPreference",
      "mobilePhone",
      "preferredName",
      "pronouns",
      "studentId",
      "updatedAt",
      "version",
    ]);
    const updatedProfile = await api(baseUrl, "/v1/student/profile", {
      method: "PATCH",
      body: {
        expectedVersion: profile.payload.version,
        preferredName: "Ari",
        pronouns: null,
        mobilePhone: "+1 555 010 3030",
        communicationPreference: "sms",
      },
    });
    assert.equal(updatedProfile.payload.version, 2);
    assert.equal(updatedProfile.payload.communicationPreference, "sms");

    const help = await api(baseUrl, "/v1/student/help");
    assert.ok(Array.isArray(help.payload.articles));
    assert.deepEqual(Object.keys(help.payload.articles[0]).sort(), [
      "answer",
      "category",
      "id",
      "question",
    ]);
    assert.deepEqual(Object.keys(help.payload.support).sort(), [
      "email",
      "hours",
      "phone",
    ]);
  });

  it("enforces canonical concurrency, idempotency, CORS, and validation", async () => {
    const { baseUrl } = await startPreview();

    const preflight = await fetch(`${baseUrl}/v1/student/onboarding`, {
      method: "OPTIONS",
      headers: { origin: "http://localhost:3000" },
    });
    assert.equal(preflight.status, 204);
    assert.match(
      preflight.headers.get("access-control-allow-methods"),
      /PUT/,
    );
    assert.equal(
      preflight.headers.get("access-control-allow-credentials"),
      "true",
    );

    const missingKey = await api(
      baseUrl,
      `/v1/admission-offers/${ids.offer}/accept`,
      { method: "POST", body: {} },
    );
    assert.equal(missingKey.response.status, 400);
    assert.equal(
      missingKey.payload.error.requestId,
      missingKey.response.headers.get("x-request-id"),
    );

    const initialProfile = await api(baseUrl, "/v1/student/profile");
    const profileUpdate = await api(baseUrl, "/v1/student/profile", {
      method: "PATCH",
      body: {
        expectedVersion: initialProfile.payload.version,
        preferredName: "Alex",
      },
    });
    assert.equal(profileUpdate.response.status, 200);
    const profileConflict = await api(baseUrl, "/v1/student/profile", {
      method: "PATCH",
      body: {
        expectedVersion: initialProfile.payload.version,
        preferredName: "Ari",
      },
    });
    assert.equal(profileConflict.response.status, 409);
    assert.equal(profileConflict.payload.error.code, "VERSION_CONFLICT");

    const firstDocument = await api(baseUrl, "/v1/student/documents", {
      method: "POST",
      body: {
        fileName: "first.pdf",
        mimeType: "application/pdf",
        sizeBytes: 100,
        category: "other",
      },
      idempotencyKey: "document-same-key",
    });
    assert.equal(firstDocument.response.status, 201);
    const documentConflict = await api(baseUrl, "/v1/student/documents", {
      method: "POST",
      body: {
        fileName: "second.pdf",
        mimeType: "application/pdf",
        sizeBytes: 100,
        category: "other",
      },
      idempotencyKey: "document-same-key",
    });
    assert.equal(documentConflict.response.status, 409);
    assert.equal(
      documentConflict.payload.error.code,
      "IDEMPOTENCY_KEY_REUSED",
    );

    const invalidDocument = await api(baseUrl, "/v1/student/documents", {
      method: "POST",
      body: {
        fileName: "../unsafe.pdf",
        mimeType: "application/pdf",
        sizeBytes: 100,
        category: "identity",
      },
      idempotencyKey: "invalid-document1",
    });
    assert.equal(invalidDocument.response.status, 400);

    const signOut = await api(baseUrl, "/v1/auth/demo/sign-out", {
      method: "POST",
    });
    const signedOutCookie = signOut.response.headers
      .get("set-cookie")
      .split(";", 1)[0];
    const denied = await api(baseUrl, "/v1/student/dashboard", {
      headers: { cookie: signedOutCookie },
    });
    assert.equal(denied.response.status, 401);
  });

  it("deduplicates activity and restores canonical state after restart", async () => {
    const { baseUrl, dataFile } = await startPreview();
    const event = {
      eventId: "00000000-0000-7000-8000-000000000901",
      eventName: "ui.dashboard_viewed.v1",
      occurredAt: fixedClock().toISOString(),
      sessionId: "session-0001",
      pageInstanceId: "page-instance-0001",
      properties: { projection_version: 1 },
    };
    const activity = await api(baseUrl, "/v1/activity-events/batch", {
      method: "POST",
      body: { events: [event, event] },
    });
    assert.deepEqual(activity.payload, { accepted: 1, duplicates: 1 });

    await api(baseUrl, `/v1/admission-offers/${ids.offer}/accept`, {
      method: "POST",
      body: {},
      idempotencyKey: "persist-offer-01",
    });
    const restored = new JsonStateStore(dataFile, fixedClock);
    await restored.initialize();
    assert.equal(restored.snapshot().offer.status, "accepted");
    assert.equal(restored.snapshot().onboarding.currentStep, "offer");
    assert.equal(restored.snapshot().onboarding.version, 1);
    assert.equal(restored.snapshot().activities.length, 1);
  });
});
