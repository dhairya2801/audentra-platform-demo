# Document Packet and Signing Design

## Purpose

Replace the Review & Sign text field with a readable, named document packet and
a native DocuSign-like electronic-signature workflow. Students can open every
document, read it, choose a typed or drawn signature, preview its placement,
and produce an immutable signed derivative.

The native provider is behind an interface so a future DocuSign integration can
use the same packet, field-placement, consent, and audit contracts.

## Scope

- versioned tenant document templates;
- named document carousel and PDF viewer;
- typed and drawn signatures;
- server-authoritative signature placement;
- signed PDF generation and storage;
- consent and audit evidence;
- onboarding completion integration;
- provider-neutral signing interface.

The linked East-West University FERPA release is a structural reference only.
VV Edgent will create an Aster-specific FERPA authorization and will not copy
another institution's branding or identifiers.

## Packet contents

The initial Aster packet contains:

1. **FERPA Authorization**
   - student identity fields;
   - optional authorized recipients from Family Permissions;
   - authorized record scopes;
   - revocation/expiry explanation;
   - signature and date fields.
2. **Enrollment Information Certification**
   - summary of student-entered onboarding information;
   - accuracy acknowledgement;
   - signature and date fields.
3. **Electronic Records and Signature Consent**
   - consent to conduct this onboarding step electronically;
   - hardware/software and withdrawal guidance;
   - signature and date fields.

FERPA authorization remains optional when the student has authorized no one.
The certification and electronic-signature consent are required.

## Template and placement model

### `document_template_version`

- `id`
- `tenant_id`
- stable `code`
- display `name`
- `version`
- source PDF content-asset identifier
- source SHA-256
- page count
- active flag
- effective dates
- timestamps and creator

Templates are immutable. A new PDF or field layout creates a new version.

### `document_template_field`

- `id`
- `template_version_id`
- stable field key
- field type: `text`, `date`, `signature`, `initials`, or `checkbox`
- signer role
- page number
- normalized `x`, `y`, `width`, and `height`
- font family, font size, alignment
- required flag
- data-binding key

Coordinates are normalized to the PDF page box. Client previews and server
rendering use the same field definitions, but the server is authoritative.

### Packet configuration

`document_packet_version` associates ordered template versions with an
onboarding stage and marks each document required or optional.

## Signing domain

### `signature_envelope`

- `id`
- tenant and student identifiers
- packet version
- status: `draft`, `ready`, `signed`, `voided`, or `failed`
- consent text/version and consent timestamp
- signer display/legal name
- selected signature method
- created, signed, and voided timestamps
- optimistic version

### `signature_envelope_document`

- envelope and template version
- ordered display name
- original asset and SHA-256
- rendered signed asset and SHA-256
- status and timestamps

### `signature_artifact`

- envelope identifier
- method: `typed` or `drawn`
- normalized signer name
- signature image asset for drawn/typed rendering
- SHA-256
- dimensions
- created timestamp

The database does not store uncontrolled canvas payloads. The server validates
and converts bounded stroke/image input into a normalized transparent image,
stores it in object storage, and records its hash.

## Provider interface

```ts
interface ElectronicSignatureProvider {
  prepareEnvelope(input: PrepareEnvelopeInput): Promise<SignatureEnvelope>;
  previewEnvelope(input: PreviewEnvelopeInput): Promise<SignaturePreview>;
  signEnvelope(input: SignEnvelopeInput): Promise<SignedEnvelope>;
  voidEnvelope(input: VoidEnvelopeInput): Promise<SignatureEnvelope>;
}
```

`NativeElectronicSignatureProvider` is implemented now. A future
`DocuSignElectronicSignatureProvider` can map the same authoritative fields to
vendor tabs and use webhooks to update envelope state.

## Student experience

### Document carousel

- Cards display document name, page count, required/optional state, and
  reviewed/signed state.
- Arrow controls, dots, and direct card selection are keyboard accessible.
- Selecting a card opens its PDF at page one in the viewer.
- The viewer supports page navigation, zoom, and a download/open-original
  action.
- A student must open every required document before signing; this is recorded
  as reviewed evidence, not inferred from scroll position.

### Signature capture

- “Type” starts with the legal name but remains an explicit confirmation.
- “Draw” uses a bounded canvas with Clear and Redraw controls.
- The student previews the signature at every signature/initial field.
- The signed date is server-generated.
- The student checks the current electronic-signature consent and submits once.

The UI never asks for passwords, government identifiers, or payment
information in the signature component.

## Signing transaction

1. Load the authenticated tenant/student and active packet version.
2. Freeze the packet and template versions in the envelope.
3. Validate all required documents were opened and all required fields have
   values.
4. Validate consent version and signature artifact.
5. Re-read each original PDF from object storage and verify its hash.
6. Render authoritative text/date/signature fields into a new PDF.
7. Compute the signed derivative hash and store it under an opaque immutable
   object key.
8. Persist all document results, mark the envelope signed, and emit audit/outbox
   events transactionally.
9. Mark Review & Sign complete only after all required envelope documents are
   signed.

The submission is idempotent. A retry returns the same signed envelope rather
than creating multiple signed copies.

## Audit evidence

Retain:

- authenticated actor/student and tenant;
- packet and template versions;
- original and signed hashes;
- exact consent text/version;
- typed or drawn method;
- server timestamp;
- field-placement version;
- request and idempotency identifiers;
- application/user-agent class needed for troubleshooting;
- audit/outbox events.

Do not claim DocuSign certification or legal equivalence. Institution counsel
must approve template language and retention policy before production use.

## Security

- PDFs and signature artifacts are private objects served only through
  authenticated, tenant/student-scoped endpoints.
- Content type and file signature are verified independently.
- Drawn-signature payload size, dimensions, and stroke counts are bounded.
- Active markup, embedded files, JavaScript, and external actions in templates
  are rejected during template activation.
- Signed derivatives are immutable; a correction requires a voided envelope
  and a new envelope.
- Logs contain identifiers and hashes, not document contents or signature
  images.

## Error behavior

- Missing original/template: keep the envelope draft and show a retryable
  unavailable message.
- Hash mismatch: fail closed, record a security event, and do not sign.
- Rendering failure: retain original and signature artifact, mark the attempt
  failed/retryable, and do not advance onboarding.
- Version conflict: reload the current envelope.
- Optional FERPA document with no authorized recipients: label “Not needed” and
  exclude it from required completion.

## Acceptance criteria

- Review & Sign displays named documents in an accessible carousel.
- Every document can be opened and read before signing.
- Typed and drawn signatures preview and render at template-defined positions.
- The server-generated signed PDFs visually contain names, signatures, and
  dates in the correct fields.
- Original and signed hashes, consent version, signer, timestamps, and template
  versions are queryable in audit evidence.
- Retrying a completed signing request cannot create another envelope.
- A signed envelope completes the onboarding stage; failed or partial
  envelopes do not.
- PDF render tests visually verify every page and field placement.

