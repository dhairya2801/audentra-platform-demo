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

async function startPreview(options = {}) {
  const directory = await mkdtemp(join(tmpdir(), "vv-demo-api-"));
  const dataFile = join(directory, "state.json");
  const store = new JsonStateStore(dataFile, fixedClock);
  const { server } = await createDemoApi({
    store,
    clock: fixedClock,
    logger: null,
    ...options,
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
      : { cookie: "vv_demo_session=demo-session-v2" }),
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

async function waitForDocument(baseUrl, documentId, predicate) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const listed = await api(baseUrl, "/v1/student/documents");
    const document = listed.payload.items.find(
      (candidate) => candidate.id === documentId,
    );
    if (document && predicate(document)) return document;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail(`Document ${documentId} did not reach the expected state`);
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

async function completeProfilePrerequisite(baseUrl) {
  const profile = await api(baseUrl, "/v1/student/profile");
  const updated = await api(baseUrl, "/v1/student/profile", {
    method: "PATCH",
    body: {
      expectedVersion: profile.payload.version,
      preferredName: profile.payload.preferredName,
      pronouns: profile.payload.pronouns,
      mobilePhone: profile.payload.mobilePhone,
      communicationPreference: profile.payload.communicationPreference,
    },
  });
  assert.equal(updated.response.status, 200);
}

describe("contract-compatible development preview API", () => {
  it("resolves two university tenants and isolates their demo records", async () => {
    const { baseUrl } = await startPreview();
    const asterHeaders = { "x-tenant-slug": "aster" };
    const harvardHeaders = { "x-tenant-slug": "harvard" };

    const asterContext = await api(baseUrl, "/v1/tenant/context", {
      authenticated: false,
      headers: asterHeaders,
    });
    const harvardContext = await api(baseUrl, "/v1/tenant/context", {
      authenticated: false,
      headers: harvardHeaders,
    });
    assert.equal(asterContext.payload.name, "Aster University");
    assert.equal(harvardContext.payload.name, "Harvard University");

    const asterBootstrap = await api(baseUrl, "/v1/student/bootstrap", {
      headers: asterHeaders,
    });
    const harvardBootstrap = await api(baseUrl, "/v1/student/bootstrap", {
      headers: harvardHeaders,
    });
    assert.equal(asterBootstrap.payload.tenant.slug, "aster");
    assert.equal(harvardBootstrap.payload.tenant.slug, "harvard");
    assert.equal(asterBootstrap.payload.rewards.pointName, "Aster Points");
    assert.equal(harvardBootstrap.payload.rewards.pointName, "Harvard Points");
    assert.notEqual(
      asterBootstrap.payload.tenant.id,
      harvardBootstrap.payload.tenant.id,
    );

    const harvardProfile = await api(baseUrl, "/v1/student/profile", {
      headers: harvardHeaders,
    });
    const updatedHarvardProfile = await api(baseUrl, "/v1/student/profile", {
      method: "PATCH",
      headers: harvardHeaders,
      body: {
        expectedVersion: harvardProfile.payload.version,
        preferredName: "Harvard Student",
        pronouns: harvardProfile.payload.pronouns,
        mobilePhone: harvardProfile.payload.mobilePhone,
        communicationPreference:
          harvardProfile.payload.communicationPreference,
      },
    });
    assert.equal(updatedHarvardProfile.payload.preferredName, "Harvard Student");

    const unchangedAsterProfile = await api(baseUrl, "/v1/student/profile", {
      headers: asterHeaders,
    });
    assert.equal(unchangedAsterProfile.payload.preferredName, "Alex");

    const [asterAcademics, harvardAcademics, asterCampus, harvardCampus] =
      await Promise.all([
        api(baseUrl, "/v1/student/academics", { headers: asterHeaders }),
        api(baseUrl, "/v1/student/academics", { headers: harvardHeaders }),
        api(baseUrl, "/v1/student/campus-life", { headers: asterHeaders }),
        api(baseUrl, "/v1/student/campus-life", { headers: harvardHeaders }),
      ]);
    assert.equal(asterAcademics.payload.selectedProgram.code, "BS-CS");
    assert.equal(harvardAcademics.payload.selectedProgram.code, "AB-CS");
    assert.equal(
      harvardAcademics.payload.selectedProgram.source.dataStatus,
      "official_source",
    );
    assert.ok(
      harvardAcademics.payload.plan.some(
        (item) => item.course.code === "COMPSCI 50",
      ),
    );
    assert.ok(
      harvardCampus.payload.clubs.some(
        (club) => club.name === "Women in Computer Science",
      ),
    );
    assert.ok(
      harvardCampus.payload.events.every(
        (event) => event.source.dataStatus === "synthetic_preview",
      ),
    );
    assert.deepEqual(
      asterCampus.payload.events.map((event) => event.visualTheme),
      ["festival", "discovery", "career"],
    );
    assert.equal(
      new Set(
        asterCampus.payload.events.map((event) => event.imageUrl),
      ).size,
      asterCampus.payload.events.length,
    );
    assert.ok(
      asterCampus.payload.events.every(
        (event) =>
          event.imageUrl?.startsWith("/media/events/") &&
          event.imageAlt?.length > 20 &&
          event.imageAttribution?.length > 0,
      ),
    );
    assert.deepEqual(
      harvardCampus.payload.events.map((event) => event.visualTheme),
      ["festival", "discovery", "community"],
    );

    const academicEdward = await api(
      baseUrl,
      "/v1/student/assistant/messages",
      {
        method: "POST",
        headers: harvardHeaders,
        body: {
          message: "Which computer science classes are in my plan?",
          pageContext: "/classrooms",
          history: [],
        },
      },
    );
    assert.ok(
      academicEdward.payload.contextReceipts.some(
        (receipt) => receipt.source === "academics",
      ),
    );
    assert.deepEqual(academicEdward.payload.suggestedActions, [
      { label: "Open My Classrooms", href: "/classrooms" },
    ]);

    const campusEdward = await api(
      baseUrl,
      "/v1/student/assistant/messages",
      {
        method: "POST",
        headers: harvardHeaders,
        body: {
          message: "What clubs and campus events can I join?",
          pageContext: "/campus-life",
          history: [],
        },
      },
    );
    assert.ok(
      campusEdward.payload.contextReceipts.some(
        (receipt) => receipt.source === "campus_life",
      ),
    );
    assert.deepEqual(campusEdward.payload.suggestedActions, [
      { label: "Open My Campus Life", href: "/campus-life" },
    ]);

    const unknownTenant = await api(baseUrl, "/v1/tenant/context", {
      authenticated: false,
      headers: { "x-tenant-slug": "unknown" },
    });
    assert.equal(unknownTenant.response.status, 400);
    assert.equal(unknownTenant.payload.error.code, "UNKNOWN_TENANT");
  });

  it("signs up credential students and isolates their portal records", async () => {
    const { baseUrl } = await startPreview();
    const firstSignup = await api(baseUrl, "/v1/auth/sign-up", {
      authenticated: false,
      method: "POST",
      body: {
        email: "first.student@example.com",
        phone: "+15551230001",
        password: "first-student-123",
      },
    });
    assert.equal(firstSignup.response.status, 201);
    assert.equal(firstSignup.payload.mode, "credentials");
    assert.equal(firstSignup.payload.student.preferredName, null);
    assert.equal(firstSignup.payload.student.emailVerified, false);
    const firstCookie = firstSignup.response.headers
      .get("set-cookie")
      ?.split(";", 1)[0];
    assert.match(firstCookie ?? "", /^vv_session=[A-Za-z0-9_-]+$/);

    const firstBootstrap = await api(baseUrl, "/v1/student/bootstrap", {
      authenticated: false,
      headers: { cookie: firstCookie },
    });
    assert.equal(firstBootstrap.response.status, 200);
    assert.equal(firstBootstrap.payload.initialRoute, "/onboarding");
    assert.equal(firstBootstrap.payload.student.id, firstSignup.payload.student.id);

    const secondSignup = await api(baseUrl, "/v1/auth/sign-up", {
      authenticated: false,
      method: "POST",
      body: {
        email: "second.student@example.com",
        phone: "+15551230002",
        password: "second-student-123",
      },
    });
    assert.equal(secondSignup.response.status, 201);
    assert.notEqual(
      secondSignup.payload.student.id,
      firstSignup.payload.student.id,
    );
    const secondCookie = secondSignup.response.headers
      .get("set-cookie")
      ?.split(";", 1)[0];

    const firstProfile = await api(baseUrl, "/v1/student/profile", {
      authenticated: false,
      headers: { cookie: firstCookie },
    });
    const secondProfile = await api(baseUrl, "/v1/student/profile", {
      authenticated: false,
      headers: { cookie: secondCookie },
    });
    assert.equal(firstProfile.payload.email, "first.student@example.com");
    assert.equal(secondProfile.payload.email, "second.student@example.com");
    assert.notEqual(firstProfile.payload.studentId, secondProfile.payload.studentId);

    const harvardSignup = await api(baseUrl, "/v1/auth/sign-up", {
      authenticated: false,
      method: "POST",
      headers: { "x-tenant-slug": "harvard" },
      body: {
        email: "harvard.student@example.com",
        phone: "+15551230003",
        password: "harvard-student-123",
      },
    });
    const harvardCookie = harvardSignup.response.headers
      .get("set-cookie")
      ?.split(";", 1)[0];
    const missingHarvardDocument = await api(
      baseUrl,
      "/v1/student/documents/00000000-0000-7000-8000-000000009999/content?tenant=harvard",
      {
        authenticated: false,
        headers: { cookie: harvardCookie },
      },
    );
    const crossTenantDocument = await api(
      baseUrl,
      "/v1/student/documents/00000000-0000-7000-8000-000000009999/content?tenant=aster",
      {
        authenticated: false,
        headers: { cookie: harvardCookie },
      },
    );
    assert.equal(missingHarvardDocument.response.status, 404);
    assert.equal(
      missingHarvardDocument.payload.error.code,
      "STUDENT_DOCUMENT_CONTENT_NOT_FOUND",
    );
    assert.equal(crossTenantDocument.response.status, 401);

    const signedOut = await api(baseUrl, "/v1/auth/sign-out", {
      authenticated: false,
      method: "POST",
      headers: { cookie: firstCookie },
    });
    assert.equal(signedOut.response.status, 200);
    const afterSignOut = await api(baseUrl, "/v1/student/bootstrap", {
      authenticated: false,
      headers: { cookie: firstCookie },
    });
    assert.equal(afterSignOut.response.status, 401);
  });

  it("starts a clean guided onboarding journey only in the development preview", async () => {
    const { baseUrl, store } = await startPreview();
    const seededState = store.snapshot();

    const accepted = await api(
      baseUrl,
      `/v1/admission-offers/${ids.offer}/accept`,
      {
        method: "POST",
        body: {},
        idempotencyKey: "guided-onboarding-offer-0001",
      },
    );
    assert.equal(accepted.response.status, 200);
    assert.equal(store.snapshot().offer.status, "accepted");

    const guided = await api(
      baseUrl,
      "/v1/auth/demo/start-guided-onboarding",
      {
        authenticated: false,
        method: "POST",
        body: {},
      },
    );
    assert.equal(guided.response.status, 200);
    assert.equal(guided.payload.authenticated, true);
    assert.equal(guided.payload.mode, "demo");
    assert.match(
      guided.response.headers.get("set-cookie"),
      /^vv_demo_session=demo-session-v2;/,
    );
    assert.deepEqual(store.snapshot(), seededState);

    const bootstrap = await api(baseUrl, "/v1/student/bootstrap");
    assert.equal(bootstrap.response.status, 200);
    assert.equal(bootstrap.payload.initialRoute, "/onboarding");
    assert.equal(bootstrap.payload.onboarding.required, true);
    assert.equal(bootstrap.payload.onboarding.currentStep, "offer");

    const disabledPreview = await startPreview({
      enableGuidedOnboardingReset: false,
    });
    const disabled = await api(
      disabledPreview.baseUrl,
      "/v1/auth/demo/start-guided-onboarding",
      {
        authenticated: false,
        method: "POST",
        body: {},
      },
    );
    assert.equal(disabled.response.status, 404);
    assert.equal(disabled.payload.error.code, "DEMO_GUIDED_ONBOARDING_DISABLED");
  });

  it("keeps onboarding and portal access locked until the offer is accepted", async () => {
    const { baseUrl } = await startPreview();
    const onboarding = (
      await api(baseUrl, "/v1/student/onboarding")
    ).payload;

    const deferred = await api(baseUrl, "/v1/student/onboarding", {
      method: "PUT",
      body: {
        expectedVersion: onboarding.version,
        currentStep: "offer",
        data: {},
        skip: true,
      },
    });
    assert.equal(deferred.response.status, 400);
    assert.equal(
      deferred.payload.error.code,
      "ONBOARDING_STEP_REQUIRED",
    );

    const bootstrap = await api(baseUrl, "/v1/student/bootstrap");
    assert.equal(bootstrap.payload.onboarding.required, true);
    assert.equal(bootstrap.payload.onboarding.status, "in_progress");
    assert.equal(bootstrap.payload.initialRoute, "/onboarding");

    await api(baseUrl, `/v1/admission-offers/${ids.offer}/accept`, {
      method: "POST",
      body: {},
      idempotencyKey: "required-offer-accept-0001",
    });
    const accepted = await api(baseUrl, "/v1/student/onboarding", {
      method: "PUT",
      body: {
        expectedVersion: onboarding.version,
        currentStep: "offer",
        data: {},
      },
    });
    assert.equal(accepted.response.status, 200);
    assert.equal(accepted.payload.currentStep, "about_you");
    assert.deepEqual(accepted.payload.completedSteps, ["offer"]);
    assert.deepEqual(accepted.payload.data.skippedSteps, []);
  });

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
      /^vv_demo_session=demo-session-v2;/,
    );

    const bootstrap = await api(baseUrl, "/v1/student/bootstrap");
    assert.deepEqual(Object.keys(bootstrap.payload).sort(), [
      "authenticated",
      "generatedAt",
      "initialRoute",
      "onboarding",
      "rewards",
      "student",
      "tenant",
    ]);
    assert.equal(bootstrap.payload.authenticated, true);
    assert.equal(bootstrap.payload.initialRoute, "/onboarding");
    assert.deepEqual(bootstrap.payload.onboarding, {
      required: true,
      status: "in_progress",
      currentStep: "offer",
      version: 1,
    });
    assert.deepEqual(bootstrap.payload.rewards, {
      pointName: "Aster Points",
      pointsPerUsd: 100,
      lifetimePoints: 0,
      bookstoreCreditCents: 0,
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
        firstName: "Alex",
        lastName: "Morgan",
        preferredName: "Alex",
        personalEmail: "alex.morgan@example.com",
        mobilePhone: "+15550102027",
        citizenshipStatus: "us_citizen",
        communicationPreference: "email",
        residencyStatus: "domestic",
        residencyVerificationPath: "home_address_review",
        streetAddress: "18 Willow Street",
        city: "Cambridge",
        stateOrProvince: "MA",
        postalCode: "02139",
        country: "United States",
      },
      housing: { housingPreference: "undecided" },
      campus_life: {
        campusInterests: ["Robotics", "Student radio"],
        supportNeeds: [],
      },
      emergency_contacts: {
        emergencyContacts: [
          {
            fullName: "Jordan Morgan",
            relationship: "parent",
            mobilePhone: "+15550100300",
          },
        ],
      },
      family_permissions: { familyPermissions: [] },
      review_and_sign: {
        signatureFullName: "Alex Morgan",
        signatureMethod: "typed",
        signatureConsent: true,
        signedDocumentIds: [
          "ferpa_release",
          "enrollment_acknowledgment",
        ],
      },
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
    assert.equal(onboarding.version, 8);

    const blockedDepositStep = await putOnboarding(baseUrl, onboarding, {
      depositChoice: "pay_now",
    });
    assert.equal(blockedDepositStep.response.status, 409);
    assert.equal(blockedDepositStep.payload.error.code, "DEPOSIT_REQUIRED");
    assert.equal(store.snapshot().onboarding.version, 8);

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
    assert.equal(store.snapshot().onboarding.version, 8);

    const depositStep = await putOnboarding(baseUrl, onboarding, {
      depositChoice: "pay_now",
    });
    onboarding = depositStep.payload;
    assert.equal(onboarding.version, 9);
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
    assert.equal(complete.payload.version, 10);
    assert.ok(complete.payload.completedAt);

    const completedBootstrap = await api(
      baseUrl,
      "/v1/student/bootstrap",
    );
    assert.equal(completedBootstrap.payload.initialRoute, "/dashboard");
    assert.equal(completedBootstrap.payload.onboarding.required, false);
    assert.equal(completedBootstrap.payload.rewards.lifetimePoints, 180);
    assert.equal(completedBootstrap.payload.rewards.bookstoreCreditCents, 180);

    const signedDocumentList = await api(
      baseUrl,
      "/v1/student/documents",
    );
    const signedDocuments = signedDocumentList.payload.items.filter(
      (document) => document.signature != null,
    );
    assert.equal(signedDocuments.length, 2);
    assert.deepEqual(
      signedDocuments.map((document) => document.signature.templateCode).sort(),
      ["enrollment_acknowledgment", "ferpa_release"],
    );
    assert.deepEqual(signedDocuments[0].signature, {
      templateCode: signedDocuments[0].signature.templateCode,
      title: signedDocuments[0].signature.title,
      signerName: "Alex Morgan",
      method: "typed",
      signedAt: "2026-07-24T12:00:00.000Z",
      onboardingVersion: 10,
    });
    const signedPdf = await fetch(
      `${baseUrl}${signedDocuments[0].contentUrl}?tenant=aster`,
      {
        headers: { cookie: "vv_demo_session=demo-session-v2" },
      },
    );
    assert.equal(signedPdf.status, 200);
    assert.match(signedPdf.headers.get("content-type"), /application\/pdf/);
    assert.equal(
      Buffer.from(await signedPdf.arrayBuffer())
        .subarray(0, 5)
        .toString("ascii"),
      "%PDF-",
    );

    const requirements = await api(baseUrl, "/v1/student/requirements");
    assert.equal(requirements.payload.total, 8);
    const identity = requirements.payload.items.find(
      (requirement) => requirement.code === "identity_document",
    );
    assert.equal(identity.journeyId, ids.journey);
    assert.equal(identity.slug, "identity-document-upload");
    assert.equal(identity.submissionType, "document");
    assert.equal(identity.documentCategory, "identity");
    assert.equal(identity.responsibleOffice, "Enrollment Documentation");
    assert.deepEqual(identity.dependencyCodes, ["profile_verification"]);
    assert.deepEqual(identity.reward, { points: 40, earned: false });
    const detail = await api(
      baseUrl,
      `/v1/student/requirements/${identity.id}`,
    );
    assert.deepEqual(detail.payload, identity);
    const detailBySlug = await api(
      baseUrl,
      `/v1/student/requirements/${identity.slug}`,
    );
    assert.deepEqual(detailBySlug.payload, identity);

    const financials = await api(baseUrl, "/v1/student/financials");
    assert.equal(financials.payload.acceptedAidCents, 1_539_500);
    assert.equal(financials.payload.pendingAidCents, 350_000);
    assert.equal(financials.payload.remainingBalanceCents, 1_650_500);
    assert.equal(
      financials.payload.awards.find(
        (award) => award.type === "work_study",
      ).offeredAmountCents,
      250_000,
    );

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
    assert.equal(documents.payload.total, 3);

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
      "firstName",
      "lastName",
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
    assert.equal(
      updatedProfile.payload.version,
      profile.payload.version + 1,
    );
    assert.equal(updatedProfile.payload.communicationPreference, "sms");
    const requirementsAfterProfile = await api(
      baseUrl,
      "/v1/student/requirements",
    );
    assert.equal(
      requirementsAfterProfile.payload.items.find(
        (requirement) => requirement.code === "profile_verification",
      ).status,
      "completed",
    );
    assert.equal(
      requirementsAfterProfile.payload.items.find(
        (requirement) => requirement.code === "identity_document",
      ).status,
      "ready",
    );

    const housingBefore = await api(baseUrl, "/v1/student/housing-plan");
    assert.equal(housingBefore.response.status, 200);
    assert.equal(housingBefore.payload.preference, "undecided");
    const updatedHousing = await api(baseUrl, "/v1/student/housing-plan", {
      method: "PATCH",
      body: {
        expectedVersion: housingBefore.payload.version,
        preference: "on_campus",
      },
    });
    assert.equal(updatedHousing.response.status, 200);
    assert.equal(updatedHousing.payload.preference, "on_campus");
    assert.equal(updatedHousing.payload.residenceOption, null);
    assert.equal(updatedHousing.payload.version, housingBefore.payload.version + 1);
    const requirementsAfterHousing = await api(
      baseUrl,
      "/v1/student/requirements",
    );
    const housingRequirement = requirementsAfterHousing.payload.items.find(
      (requirement) => requirement.code === "housing_preference",
    );
    assert.equal(housingRequirement.status, "completed");
    assert.equal(housingRequirement.progressPercent, 100);
    assert.deepEqual(housingRequirement.reward, {
      points: 25,
      earned: true,
    });
    const rewardsAfterHousing = await api(
      baseUrl,
      "/v1/student/bootstrap",
    );
    assert.equal(rewardsAfterHousing.payload.rewards.lifetimePoints, 205);
    assert.equal(
      rewardsAfterHousing.payload.rewards.bookstoreCreditCents,
      205,
    );

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

  it("awards a tenant page reward once and prevents refresh farming", async () => {
    const { baseUrl, store } = await startPreview();
    const event = (eventId) => ({
      eventId,
      eventName: "ui.portal_section_viewed.v1",
      occurredAt: fixedClock().toISOString(),
      sessionId: "session-reward-0001",
      pageInstanceId: `page-${eventId.slice(-8)}`,
      properties: {
        section: "classrooms",
        entry_point: "portal_navigation",
      },
    });
    const activity = await api(baseUrl, "/v1/activity-events/batch", {
      method: "POST",
      body: {
        events: [
          event("00000000-0000-7000-8000-000000000911"),
          event("00000000-0000-7000-8000-000000000912"),
        ],
      },
    });
    assert.deepEqual(activity.payload, { accepted: 2, duplicates: 0 });

    const bootstrap = await api(baseUrl, "/v1/student/bootstrap");
    assert.equal(bootstrap.payload.rewards.lifetimePoints, 15);
    assert.equal(bootstrap.payload.rewards.bookstoreCreditCents, 15);
    assert.equal(store.snapshot().rewards.ledger.length, 1);
    assert.equal(
      store.snapshot().rewards.ledger[0].sourceKey,
      "ui.portal_section_viewed.v1:classrooms",
    );
  });

  it("stores uploaded content, exposes reviewed extraction, and keeps raw storage private", async () => {
    const extractionInputs = [];
    const ai = {
      async extractStudentDocument(input) {
        extractionInputs.push(input);
        return {
          status: "completed",
          documentType: "transcript",
          summary: "An official transcript with a student name and term.",
          studentName: "Maya Chen",
          institutionName: "Aster University",
          issueDate: "2026-07-20",
          academicTerm: "Fall 2026",
          fields: [
            {
              key: "student_name",
              label: "Student name",
              value: "Maya Chen",
              confidence: 0.99,
            },
            {
              key: "academic_term",
              label: "Academic term",
              value: "Fall 2026",
              confidence: 0.94,
            },
            {
              key: "preferred_name",
              label: "Preferred name",
              value: "Maya",
              confidence: 0.92,
            },
          ],
          courses: [
            {
              sourceCode: "AP Calculus AB",
              title: "AP Calculus AB",
              grade: null,
              score: "5",
              credits: null,
              term: "Spring 2026",
              confidence: 0.99,
            },
          ],
          warnings: [],
          model: "test/document-parser",
          provider: "openrouter",
          processedAt: fixedClock().toISOString(),
          verifiedAt: null,
        };
      },
      async askEdward() {
        return {
          message: "Open Documents to review your extracted fields.",
          provider: "openrouter",
          model: "test/edward",
          usage: {
            promptTokens: 80,
            completionTokens: 12,
            totalTokens: 92,
          },
          suggestedActions: [
            { label: "Open documents", href: "/documents" },
          ],
        };
      },
    };
    const { baseUrl } = await startPreview({ ai });
    await api(baseUrl, `/v1/admission-offers/${ids.offer}/accept`, {
      method: "POST",
      body: {},
      idempotencyKey: "upload-flow-offer-0001",
    });
    const blockedForm = new FormData();
    blockedForm.set(
      "file",
      new Blob(
        [Buffer.from("%PDF-1.7\nBlocked transcript fixture\n%%EOF\n")],
        { type: "application/pdf" },
      ),
      "blocked-transcript.pdf",
    );
    blockedForm.set("requirementId", ids.transcriptRequirement);
    const blockedUpload = await fetch(
      `${baseUrl}/v1/student/documents/upload`,
      {
        method: "POST",
        headers: {
          cookie: "vv_demo_session=demo-session-v2",
          "idempotency-key": "document-upload-blocked-0001",
        },
        body: blockedForm,
      },
    );
    assert.equal(blockedUpload.status, 409);
    assert.equal(
      (await blockedUpload.json()).error.code,
      "DOCUMENT_REQUIREMENT_BLOCKED",
    );
    await completeProfilePrerequisite(baseUrl);
    const fileBytes = Buffer.from("%PDF-1.7\nAster transcript fixture\n%%EOF\n");
    const form = new FormData();
    form.set(
      "file",
      new Blob([fileBytes], { type: "application/pdf" }),
      "aster-transcript.pdf",
    );
    form.set("requirementId", ids.transcriptRequirement);

    const uploadedResponse = await fetch(
      `${baseUrl}/v1/student/documents/upload`,
      {
        method: "POST",
        headers: {
          cookie: "vv_demo_session=demo-session-v2",
          "idempotency-key": "document-upload-0001",
        },
        body: form,
      },
    );
    const uploaded = await uploadedResponse.json();
    assert.equal(uploadedResponse.status, 201);
    assert.equal(uploaded.status, "processing");
    assert.equal(uploaded.fileName, "aster-transcript.pdf");
    assert.equal(uploaded.category, "transcript");
    assert.equal(uploaded.requirementId, ids.transcriptRequirement);
    assert.equal(uploaded.extraction.status, "processing");
    assert.match(uploaded.sha256, /^[0-9a-f]{64}$/);
    assert.equal(
      uploaded.contentUrl,
      `/v1/student/documents/${uploaded.id}/content`,
    );
    assert.equal("storageKey" in uploaded, false);
    const parsed = await waitForDocument(
      baseUrl,
      uploaded.id,
      (document) => document.extraction?.status === "completed",
    );
    assert.equal(parsed.status, "under_review");
    assert.equal(parsed.extraction.status, "completed");
    assert.equal(parsed.extraction.fields.length, 3);
    assert.equal(extractionInputs[0].expectedDocumentType, "transcript");

    const transcriptRequirement = await api(
      baseUrl,
      "/v1/student/requirements/transcript-upload",
    );
    assert.equal(transcriptRequirement.response.status, 200);
    assert.equal(transcriptRequirement.payload.status, "under_review");
    assert.equal(transcriptRequirement.payload.progressPercent, 80);

    const contentResponse = await fetch(
      `${baseUrl}${parsed.contentUrl}`,
      { headers: { cookie: "vv_demo_session=demo-session-v2" } },
    );
    assert.equal(contentResponse.status, 200);
    assert.equal(
      contentResponse.headers.get("content-type"),
      "application/pdf",
    );
    assert.deepEqual(
      Buffer.from(await contentResponse.arrayBuffer()),
      fileBytes,
    );

    const profileAfterExtraction = await api(baseUrl, "/v1/student/profile");
    assert.equal(profileAfterExtraction.payload.preferredName, "Alex");
    assert.equal(profileAfterExtraction.payload.version, 2);

    const academicsAfterExtraction = await api(
      baseUrl,
      "/v1/student/academics",
    );
    const importedCredit = academicsAfterExtraction.payload.transcriptCredits.find(
      (credit) => credit.sourceDocumentId === uploaded.id,
    );
    assert.equal(importedCredit.sourceCode, "AP Calculus AB");
    assert.equal(
      academicsAfterExtraction.payload.exemptionRecommendations.find(
        (recommendation) =>
          recommendation.transcriptCreditId === importedCredit.id,
      ).targetCourseCode,
      "MATH 151",
    );

    const listed = await api(baseUrl, "/v1/student/documents");
    assert.equal(listed.payload.total, 1);
    assert.equal("storageKey" in listed.payload.items[0], false);

    const assistant = await api(
      baseUrl,
      "/v1/student/assistant/messages",
      {
        method: "POST",
        body: {
          message: "Where is my transcript?",
          pageContext: "/documents",
          history: [],
        },
      },
    );
    assert.equal(assistant.response.status, 200);
    assert.equal(assistant.payload.provider, "openrouter");
    assert.equal(assistant.payload.usage.totalTokens, 92);
    assert.deepEqual(assistant.payload.contextReceipts, [
      { source: "dashboard" },
      { source: "profile" },
      { source: "documents" },
    ]);
  });

  it("does not advance a transcript requirement when content classification disagrees", async () => {
    const ai = {
      async extractStudentDocument() {
        return {
          status: "completed",
          documentType: "identity",
          summary: "A government-issued identity document.",
          studentName: "Maya Chen",
          institutionName: null,
          issueDate: "2026-07-20",
          academicTerm: null,
          fields: [],
          courses: [],
          warnings: [],
          model: "test/document-parser",
          provider: "openrouter",
          processedAt: fixedClock().toISOString(),
          verifiedAt: null,
        };
      },
      async askEdward() {
        throw new Error("not used");
      },
    };
    const { baseUrl } = await startPreview({ ai });
    await api(baseUrl, `/v1/admission-offers/${ids.offer}/accept`, {
      method: "POST",
      body: {},
      idempotencyKey: "mismatch-flow-offer-0001",
    });
    await completeProfilePrerequisite(baseUrl);
    const form = new FormData();
    form.set(
      "file",
      new Blob(
        [Buffer.from("%PDF-1.7\nIdentity document fixture\n%%EOF\n")],
        { type: "application/pdf" },
      ),
      "mystery.pdf",
    );
    form.set("requirementId", ids.transcriptRequirement);

    const response = await fetch(
      `${baseUrl}/v1/student/documents/upload`,
      {
        method: "POST",
        headers: {
          cookie: "vv_demo_session=demo-session-v2",
          "idempotency-key": "document-mismatch-0001",
        },
        body: form,
      },
    );
    const uploaded = await response.json();
    assert.equal(response.status, 201);
    assert.equal(uploaded.extraction.status, "processing");
    const parsed = await waitForDocument(
      baseUrl,
      uploaded.id,
      (document) => document.extraction?.status === "completed",
    );
    assert.equal(parsed.extraction.documentType, "identity");
    assert.match(
      parsed.extraction.warnings[0],
      /requirement was not advanced automatically/i,
    );

    const requirement = await api(
      baseUrl,
      "/v1/student/requirements/transcript-upload",
    );
    assert.equal(requirement.payload.status, "ready");
    assert.equal(requirement.payload.progressPercent, 0);
  });

  it("does not advance a requirement when document extraction fails", async () => {
    const ai = {
      async extractStudentDocument() {
        throw new Error("simulated parser outage");
      },
      async askEdward() {
        throw new Error("not used");
      },
    };
    const { baseUrl } = await startPreview({ ai });
    await api(baseUrl, `/v1/admission-offers/${ids.offer}/accept`, {
      method: "POST",
      body: {},
      idempotencyKey: "failed-parse-offer-0001",
    });
    await completeProfilePrerequisite(baseUrl);
    const form = new FormData();
    form.set(
      "file",
      new Blob(
        [Buffer.from("%PDF-1.7\nTranscript fixture\n%%EOF\n")],
        { type: "application/pdf" },
      ),
      "official-transcript.pdf",
    );
    form.set("requirementId", ids.transcriptRequirement);

    const response = await fetch(
      `${baseUrl}/v1/student/documents/upload`,
      {
        method: "POST",
        headers: {
          cookie: "vv_demo_session=demo-session-v2",
          "idempotency-key": "failed-parse-upload-0001",
        },
        body: form,
      },
    );
    const uploaded = await response.json();
    assert.equal(response.status, 201);
    assert.equal(uploaded.extraction.status, "processing");
    const parsed = await waitForDocument(
      baseUrl,
      uploaded.id,
      (document) => document.extraction?.status === "failed",
    );
    assert.equal(parsed.extraction.status, "failed");

    const requirement = await api(
      baseUrl,
      "/v1/student/requirements/transcript-upload",
    );
    assert.equal(requirement.payload.status, "ready");
    assert.equal(requirement.payload.progressPercent, 0);
  });

  it("persists the document record before storage failure and never parses an unstored file", async () => {
    let parserCalls = 0;
    const ai = {
      async extractStudentDocument() {
        parserCalls += 1;
        return {
          status: "completed",
          documentType: "identity",
          summary: "An identity document.",
          studentName: "Maya Chen",
          institutionName: null,
          issueDate: null,
          academicTerm: null,
          fields: [],
          courses: [],
          warnings: [],
          model: "test/document-parser",
          provider: "openrouter",
          processedAt: fixedClock().toISOString(),
          verifiedAt: null,
        };
      },
      async askEdward() {
        throw new Error("not used");
      },
    };
    const { baseUrl, store } = await startPreview({ ai });
    await api(baseUrl, `/v1/admission-offers/${ids.offer}/accept`, {
      method: "POST",
      body: {},
      idempotencyKey: "durable-storage-offer-0001",
    });
    await completeProfilePrerequisite(baseUrl);
    const originalWrite = store.writeUpload.bind(store);
    store.writeUpload = async () => {
      throw new Error("simulated object storage outage");
    };

    const upload = () => {
      const form = new FormData();
      form.set(
        "file",
        new Blob([Buffer.from("%PDF-1.7\nstored-last\n%%EOF\n")], {
          type: "application/pdf",
        }),
        "identity.pdf",
      );
      form.set("requirementId", ids.identityRequirement);
      return fetch(`${baseUrl}/v1/student/documents/upload`, {
        method: "POST",
        headers: {
          cookie: "vv_demo_session=demo-session-v2",
          "idempotency-key": "durable-storage-retry-0001",
        },
        body: form,
      });
    };

    const failedResponse = await upload();
    const failedPayload = await failedResponse.json();
    assert.equal(failedResponse.status, 503);
    assert.equal(failedPayload.error.code, "DOCUMENT_STORAGE_UNAVAILABLE");
    assert.equal(parserCalls, 0);
    assert.equal(store.snapshot().documents.length, 1);
    assert.equal(store.snapshot().documents[0].status, "placeholder");
    assert.equal(store.snapshot().documents[0].contentStored, false);

    store.writeUpload = originalWrite;
    const resumedResponse = await upload();
    const resumed = await resumedResponse.json();
    assert.equal(resumedResponse.status, 201);
    assert.equal(resumed.status, "processing");
    await waitForDocument(
      baseUrl,
      resumed.id,
      (document) => document.extraction?.status === "completed",
    );
    assert.equal(parserCalls, 1);
  });

  it("accepts a requirement document bundle as separate strict uploads", async () => {
    const extractionInputs = [];
    const ai = {
      async extractStudentDocument(input) {
        extractionInputs.push(input);
        return {
          status: "completed",
          documentType: "identity",
          summary: "A government-issued identity document.",
          studentName: "Maya Chen",
          institutionName: null,
          issueDate: "2026-07-20",
          academicTerm: null,
          fields: [],
          courses: [],
          warnings: [],
          model: "test/document-parser",
          provider: "openrouter",
          processedAt: fixedClock().toISOString(),
          verifiedAt: null,
        };
      },
      async askEdward() {
        throw new Error("not used");
      },
    };
    const { baseUrl } = await startPreview({ ai });
    await api(baseUrl, `/v1/admission-offers/${ids.offer}/accept`, {
      method: "POST",
      body: {},
      idempotencyKey: "bundle-offer-0001",
    });
    await completeProfilePrerequisite(baseUrl);
    const uploadBundleId = "00000000-0000-7000-8000-000000000777";

    const uploadSide = async (name, idempotencyKey) => {
      const form = new FormData();
      form.set(
        "file",
        new Blob(
          [Buffer.from(`%PDF-1.7\n${name} identity fixture\n%%EOF\n`)],
          { type: "application/pdf" },
        ),
        `${name}.pdf`,
      );
      form.set("requirementId", ids.identityRequirement);
      form.set("uploadBundleId", uploadBundleId);
      const response = await fetch(`${baseUrl}/v1/student/documents/upload`, {
        method: "POST",
        headers: {
          cookie: "vv_demo_session=demo-session-v2",
          "idempotency-key": idempotencyKey,
        },
        body: form,
      });
      return { response, payload: await response.json() };
    };

    const front = await uploadSide("identity-front", "bundle-front-0001");
    const back = await uploadSide("identity-back", "bundle-back-0001");

    assert.equal(front.response.status, 201);
    assert.equal(back.response.status, 201);
    assert.equal(front.payload.requirementId, ids.identityRequirement);
    assert.equal(back.payload.requirementId, ids.identityRequirement);
    assert.equal(front.payload.category, "identity");
    assert.equal(back.payload.category, "identity");
    await Promise.all([
      waitForDocument(
        baseUrl,
        front.payload.id,
        (document) => document.extraction?.status === "completed",
      ),
      waitForDocument(
        baseUrl,
        back.payload.id,
        (document) => document.extraction?.status === "completed",
      ),
    ]);
    assert.equal(extractionInputs.length, 2);
    assert.ok(
      extractionInputs.every(
        (input) => input.expectedDocumentType === "identity",
      ),
    );

    const documents = await api(baseUrl, "/v1/student/documents");
    assert.equal(documents.payload.total, 2);
  });

  it("classifies financial-aid documents without retaining extracted fields", async () => {
    let extractionCalls = 0;
    const ai = {
      async extractStudentDocument(input) {
        extractionCalls += 1;
        assert.equal(input.expectedDocumentType, "financial_aid");
        return {
          status: "completed",
          documentType: "financial_aid",
          summary: "A financial-aid verification worksheet.",
          studentName: "Synthetic Student",
          institutionName: "Synthetic Academy",
          issueDate: "2026-07-24",
          academicTerm: "Fall 2026",
          fields: [
            {
              key: "synthetic_sensitive_field",
              label: "Synthetic sensitive field",
              value: "must-not-be-retained",
              confidence: 0.99,
            },
          ],
          courses: [],
          visualRegions: [],
          warnings: [],
          model: "test/document-classifier",
          provider: "local",
          processedAt: fixedClock().toISOString(),
          verifiedAt: null,
        };
      },
      async askEdward() {
        throw new Error("not used");
      },
    };
    const { baseUrl } = await startPreview({ ai });
    await api(baseUrl, `/v1/admission-offers/${ids.offer}/accept`, {
      method: "POST",
      body: {},
      idempotencyKey: "financial-classification-offer-0001",
    });
    const form = new FormData();
    form.set(
      "file",
      new Blob(
        [Buffer.from("%PDF-1.7\nFinancial verification fixture\n%%EOF\n")],
        { type: "application/pdf" },
      ),
      "verification-worksheet.pdf",
    );
    // A modified browser cannot opt a requirement back into parsing.
    form.set("category", "transcript");
    form.set("requirementId", ids.financialRequirement);
    const response = await fetch(`${baseUrl}/v1/student/documents/upload`, {
      method: "POST",
      headers: {
        cookie: "vv_demo_session=demo-session-v2",
        "idempotency-key": "financial-classification-upload-0001",
      },
      body: form,
    });
    const uploaded = await response.json();

    assert.equal(response.status, 201);
    assert.equal(uploaded.category, "financial_aid");
    assert.equal(uploaded.processingMode, "classification_only");
    assert.equal(uploaded.extraction.status, "processing");
    const document = await waitForDocument(
      baseUrl,
      uploaded.id,
      (candidate) => candidate.extraction?.status === "completed",
    );
    assert.equal(document.category, "financial_aid");
    assert.equal(document.processingMode, "classification_only");
    assert.equal(document.status, "needs_review");
    assert.equal(document.extraction.documentType, "financial_aid");
    assert.equal(document.extraction.studentName, null);
    assert.equal(document.extraction.institutionName, null);
    assert.equal(document.extraction.issueDate, null);
    assert.equal(document.extraction.academicTerm, null);
    assert.deepEqual(document.extraction.fields, []);
    assert.deepEqual(document.extraction.courses, []);
    assert.deepEqual(document.extraction.visualRegions, []);
    assert.equal(extractionCalls, 1);

    const requirement = await api(
      baseUrl,
      "/v1/student/requirements/financial-aid-verification",
    );
    assert.equal(requirement.payload.status, "under_review");
    assert.equal(requirement.payload.progressPercent, 80);
  });

  it("rejects a restaurant menu from the financial-aid review workflow", async () => {
    const ai = {
      async extractStudentDocument(input) {
        assert.equal(input.expectedDocumentType, "financial_aid");
        return {
          status: "completed",
          documentType: "other",
          summary: "A restaurant menu with appetizers and desserts.",
          studentName: null,
          institutionName: null,
          issueDate: null,
          academicTerm: null,
          fields: [],
          courses: [],
          visualRegions: [],
          warnings: [],
          model: "test/document-classifier",
          provider: "local",
          processedAt: fixedClock().toISOString(),
          verifiedAt: null,
        };
      },
      async askEdward() {
        throw new Error("not used");
      },
    };
    const { baseUrl } = await startPreview({ ai });
    await api(baseUrl, `/v1/admission-offers/${ids.offer}/accept`, {
      method: "POST",
      body: {},
      idempotencyKey: "financial-mismatch-offer-0001",
    });
    const form = new FormData();
    form.set(
      "file",
      new Blob(
        [Buffer.from("%PDF-1.7\nRestaurant menu\nAppetizers\nDesserts\n%%EOF\n")],
        { type: "application/pdf" },
      ),
      "restaurant-menu.pdf",
    );
    form.set("requirementId", ids.financialRequirement);
    const response = await fetch(`${baseUrl}/v1/student/documents/upload`, {
      method: "POST",
      headers: {
        cookie: "vv_demo_session=demo-session-v2",
        "idempotency-key": "financial-mismatch-upload-0001",
      },
      body: form,
    });
    const uploaded = await response.json();

    assert.equal(response.status, 201);
    assert.equal(uploaded.processingMode, "classification_only");
    const document = await waitForDocument(
      baseUrl,
      uploaded.id,
      (candidate) => candidate.extraction?.status === "completed",
    );
    assert.equal(document.extraction.documentType, "other");
    assert.match(
      document.extraction.warnings[0],
      /requirement was not advanced automatically/i,
    );

    const requirement = await api(
      baseUrl,
      "/v1/student/requirements/financial-aid-verification",
    );
    assert.equal(requirement.payload.status, "ready");
    assert.equal(requirement.payload.progressPercent, 35);
  });

  it("keeps the document endpoint strict when a multipart request contains multiple files", async () => {
    const { baseUrl } = await startPreview();
    const form = new FormData();
    form.append(
      "file",
      new Blob([Buffer.from("%PDF-1.7\nfirst\n%%EOF\n")], {
        type: "application/pdf",
      }),
      "first.pdf",
    );
    form.append(
      "file",
      new Blob([Buffer.from("%PDF-1.7\nsecond\n%%EOF\n")], {
        type: "application/pdf",
      }),
      "second.pdf",
    );

    const response = await fetch(`${baseUrl}/v1/student/documents/upload`, {
      method: "POST",
      headers: {
        cookie: "vv_demo_session=demo-session-v2",
        "idempotency-key": "strict-bundle-0001",
      },
      body: form,
    });
    const payload = await response.json();

    assert.equal(response.status, 400);
    assert.equal(payload.error.code, "ONE_FILE_PER_UPLOAD");
  });

  it("rejects a file whose declared type does not match its signature", async () => {
    const { baseUrl } = await startPreview();
    const form = new FormData();
    form.set(
      "file",
      new Blob(["not really a PDF"], { type: "application/pdf" }),
      "misleading.pdf",
    );
    form.set("category", "other");

    const response = await fetch(`${baseUrl}/v1/student/documents/upload`, {
      method: "POST",
      headers: {
        cookie: "vv_demo_session=demo-session-v2",
        "idempotency-key": "document-invalid-signature-0001",
      },
      body: form,
    });
    const payload = await response.json();

    assert.equal(response.status, 415);
    assert.equal(payload.error.code, "FILE_SIGNATURE_MISMATCH");
  });
});
