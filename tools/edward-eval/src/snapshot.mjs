/**
 * Canonical persona state, fetched from the live eval host.
 *
 * The suite's ground truth is never hand-copied into test files: it is read
 * from the same student REST endpoints the portal renders, against the same
 * in-memory store the assistant tools read. A case that needs "the missing
 * documents for this persona" derives them from this snapshot at run time, so
 * the fixture and the expectation cannot drift apart.
 *
 * Snapshots are fetched once per persona from a fault-free host. Fault-injected
 * hosts intentionally break some of these endpoints, so a faulted group reuses
 * the snapshot captured from the clean host of the same persona.
 */

const ENDPOINTS = {
  profile: "/v1/student/profile",
  dashboard: "/v1/student/dashboard",
  requirements: "/v1/student/requirements",
  onboarding: "/v1/student/onboarding",
  documents: "/v1/student/documents",
  payments: "/v1/student/payments",
  financials: "/v1/student/financials",
  housingPlan: "/v1/student/housing-plan",
  appointments: "/v1/student/appointments",
  campusLife: "/v1/student/campus-life",
  academics: "/v1/student/academics",
  messages: "/v1/student/messages",
  help: "/v1/student/help",
};

/** @returns {Promise<object>} one snapshot with a key per ENDPOINTS entry */
export async function fetchSnapshot(baseUrl) {
  const snapshot = { fetchedAt: new Date().toISOString() };
  await Promise.all(
    Object.entries(ENDPOINTS).map(async ([key, path]) => {
      const response = await fetch(`${baseUrl}${path}`);
      if (!response.ok) {
        throw new Error(`snapshot read ${path} returned ${response.status}`);
      }
      snapshot[key] = await response.json();
    }),
  );
  return snapshot;
}
