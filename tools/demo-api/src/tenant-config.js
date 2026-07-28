export const demoTenants = Object.freeze({
  aster: Object.freeze({
    id: "00000000-0000-7000-8000-000000000001",
    slug: "aster",
    name: "Aster University",
    shortName: "Aster",
    mark: "A",
    supportEmail: "enrollment@aster.edu",
    admissionsEmail: "admissions@aster.edu",
    registrarEmail: "registrar@aster.edu",
  }),
  harvard: Object.freeze({
    id: "00000000-0000-7000-8000-000000000002",
    slug: "harvard",
    name: "Harvard University",
    shortName: "Harvard",
    mark: "H",
    supportEmail: "studentservices@harvard.edu",
    admissionsEmail: "admissions@harvard.edu",
    registrarEmail: "registrar@harvard.edu",
  }),
});

export function tenantConfigForSlug(value) {
  return typeof value === "string" ? demoTenants[value.toLowerCase()] ?? null : null;
}

export function publicTenantContext(tenant) {
  return {
    id: tenant.id,
    slug: tenant.slug,
    name: tenant.name,
    shortName: tenant.shortName,
    mark: tenant.mark,
    supportEmail: tenant.supportEmail,
    admissionsEmail: tenant.admissionsEmail,
    registrarEmail: tenant.registrarEmail,
  };
}
