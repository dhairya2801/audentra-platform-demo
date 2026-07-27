# Onboarding and Content Media Design

## Purpose

Make onboarding navigable, resumable, visually rich, and consistent with
normal form behavior. Students may revisit any stage they have already
visited, defer offer acceptance, enter housing and roommate preferences without
defaults, and access the portal after completing or skipping permitted
onboarding stages.

Housing and campus visuals become tenant-managed content backed by PostgreSQL
metadata and object storage rather than hardcoded frontend arrays.

## Scope

This delivery covers:

- visited-stage navigation;
- offer deferral;
- authenticated-email prefill;
- housing selection, residence media, and known-roommate fields;
- campus-interest media;
- responsive action layout;
- visible required-field markers across onboarding and enrollment;
- licensed content-asset ingestion and fallbacks.

Document packet signing and AI document intelligence are specified separately.

## Onboarding state and navigation

`currentStep` remains the first step that has not been completed or skipped.
It is the canonical resume point. The page also has a `viewingStep`, selected
from the URL query (`/onboarding?step=housing`) and constrained to the server's
`availableSteps`.

`availableSteps` contains:

- every completed step;
- every skipped step;
- the current step.

Future unvisited steps remain locked. Completed and skipped steps are
clickable in both desktop and mobile progress navigation.

### Step update API

Add:

`PUT /v1/student/onboarding/steps/:step`

Request:

```json
{
  "expectedVersion": 7,
  "action": "save|save_and_continue|skip",
  "data": {}
}
```

Rules:

- `step` must be the current step or a member of `availableSteps`.
- `save` validates and updates a visited step without moving `currentStep`.
- `save_and_continue` on a visited prior step saves and returns the student to
  the unchanged current step.
- `save_and_continue` on the current step advances to the next step.
- `skip` is allowed only on the current step and only when the step is
  server-configured as skippable.
- Optimistic concurrency remains mandatory.
- Existing `PUT /v1/student/onboarding` remains as a compatibility adapter
  during migration.

The response adds `availableSteps` and preserves `completedSteps`,
`skippedSteps`, `currentStep`, `version`, and stored data.

## Offer deferral

`offer` becomes skippable.

- “I'm still deciding—show me my options” saves the Offer step as skipped and
  advances onboarding.
- It does not accept or decline the offer.
- The offer remains `offered`, retains its deadline, and is available from the
  dashboard and enrollment workspace.
- Completing onboarding with a deferred offer unlocks the general portal so
  the student can explore academics, campus life, housing, financial
  information, and support.
- Actions that legally or operationally require acceptance remain visibly
  blocked.
- Deposit payment cannot run before offer acceptance; the Deposit onboarding
  stage can be skipped.
- Accepting the offer later removes the pending-offer banner and refreshes
  dependent enrollment requirements.

## About You prefill

The onboarding read model includes a non-editable prefill projection:

```ts
interface StudentOnboardingPrefill {
  authenticatedEmail: string;
  firstName?: string;
  lastName?: string;
  preferredName?: string;
  mobilePhone?: string;
}
```

The About You form uses stored onboarding data first, then the authenticated
profile/credential projection. Prefill is not persisted until the student
saves the stage. Email comparisons use normalized lowercase values.

## Housing behavior

No housing or residence choice is selected for a new student.

- Initial `housingPreference` and `housingResidenceOption` are `null`/absent.
- Existing saved choices remain selected when revisited.
- Selecting on-campus housing reveals tenant-configured residence cards with
  images, room imagery, descriptions, and amenities.
- Residence options do not silently default to the first card.
- Changing away from on-campus housing clears residence-only fields on save.

### Known roommate

When `roommateMatching === "known_roommate"`, show:

- roommate full name, required;
- roommate email, required and normalized;
- a short note that this is a request, not a guaranteed placement.

Add `knownRoommateFullName` and `knownRoommateEmail` to onboarding contracts,
DTO validation, production storage, preview storage, migration, and tests.
Changing the matching mode clears these values on save.

## Campus life visuals

Campus-interest options use tenant-configured cards rather than a string list.
Each card includes:

- stable code;
- name and category;
- short description;
- media asset;
- fallback visual;
- active and display-order fields.

The onboarding selection codes map to the main Campus Life club directory.
Campus event and club pages also consume the same asset records.

References to external university clubs are inspiration only. Aster does not
reuse another university's branding or imply endorsement.

## Content asset model

### `content_media_asset`

- `id`
- `tenant_id`
- `code`
- `kind`: `image`
- `storage_provider`
- `storage_key`
- `sha256`
- MIME type, width, height, and byte size
- `alt_text`
- `source_url`
- `creator_name`
- `license_name`
- `license_url`
- `attribution_text`
- `active`
- `version`
- timestamps

The binary lives in S3-compatible object storage or the preview file store.
PostgreSQL stores the authoritative metadata and association. Large image
bytes are not duplicated in PostgreSQL.

### Tenant content tables

- `housing_residence_option`
  - tenant, stable code, title, subtitle, description, display order, active
- `housing_residence_amenity`
  - residence option, label, display order
- `housing_residence_media`
  - residence option, asset, media role, display order
- media association columns/tables for `student_club` and `campus_event`

Shared browser contracts expose a safe `StudentMediaAsset` with an application
content URL, dimensions, and alt text. Storage keys and source administration
metadata are not exposed as writable fields.

## Asset ingestion

A repeatable seed/import command:

1. Reads a reviewed manifest of licensed Unsplash/Pexels candidates.
2. Downloads each exact image.
3. Validates content signature, MIME type, dimensions, and maximum size.
4. Normalizes orientation and creates bounded web variants.
5. Computes SHA-256.
6. Uploads the binary to object storage under an opaque key.
7. Inserts or updates metadata and tenant associations transactionally.

The checked-in manifest records source and license details. Tests use local
fixtures and never depend on the image websites.

If an asset is absent or disabled, the UI renders an Aster-owned fallback
gradient/illustration with meaningful alt behavior.

## Required markers and form semantics

Create a shared label/legend convention:

- visible red `*` next to every required field or required fieldset;
- screen-reader text “required”;
- native `required`/`aria-required` retained;
- optional fields explicitly labeled “Optional” where helpful;
- server validation remains authoritative.

Apply the convention to all onboarding stages and editable enrollment forms,
not only the new fields.

## Responsive actions

The onboarding footer uses a grid/flex layout that:

- wraps before controls overlap;
- gives primary and secondary actions independent minimum sizes;
- becomes a full-width vertical stack on narrow screens;
- preserves keyboard order: Back/Skip, Save, progress message;
- respects safe-area insets and long translated labels;
- prevents text buttons from being positioned over the primary button.

Browser tests cover representative desktop, tablet, and mobile widths.

## Component boundaries

Split the current monolithic onboarding page into focused modules:

- step metadata and navigation;
- flow state/controller;
- one component per onboarding stage;
- shared required label and choice-card components;
- housing and campus media cards;
- responsive action footer;
- form-to-contract serializers.

The split follows current application patterns and does not redesign unrelated
portal code.

## Error behavior

- A stale version reloads the latest server state and preserves a student-safe
  explanation.
- An unavailable image uses the fallback and never blocks form completion.
- A failed media import leaves the prior active asset unchanged.
- An invalid roommate email keeps the stage open with field-level feedback.
- A deferred offer never appears as accepted or declined.
- Revisiting a saved step never resets later progress.

## Acceptance criteria

- A student can open every previously visited onboarding stage and save edits.
- Future stages remain inaccessible until reached.
- A student can skip Offer, complete onboarding, enter the portal, and accept
  the same offer later.
- The authenticated account email prepopulates About You when no onboarding
  email has been saved.
- New housing forms have no selected preference or residence.
- Selecting each residence changes the visible image and amenities.
- Known-roommate mode requires and persists name and email.
- Housing and campus images are served from stored asset records with license
  provenance.
- Required inputs across onboarding and enrollment display accessible red
  asterisks.
- Skip and Save actions do not overlap at supported viewport widths.
- Production API, preview API, render tests, and Playwright flows cover these
  behaviors.

