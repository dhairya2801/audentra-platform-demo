"use strict";

const assert = require("node:assert/strict");
const { describe, it } = require("node:test");
const {
  createSignedOnboardingPdf,
  DocumentPreprocessingError,
  extractStudentDocumentImageRegion,
  preprocessStudentDocument,
} = require("../src/index.cjs");
const { createHash } = require("node:crypto");
const sharp = require("sharp");
const { execFileSync } = require("node:child_process");
const { mkdtempSync, readFileSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join, resolve } = require("node:path");

const pythonExecutable =
  process.env.DOCUMENT_PYTHON_BIN?.trim() ||
  (process.platform === "win32" ? "python" : "python3");

describe("document preprocessing", () => {
  it("passes a source image through as a bounded multimodal input", async () => {
    const prepared = await preprocessStudentDocument({
      mimeType: "image/png",
      bytes: Buffer.from([0x89, 0x50, 0x4e, 0x47]),
    });

    assert.equal(prepared.extractedText, "");
    assert.equal(prepared.images.length, 1);
    assert.equal(prepared.images[0].mimeType, "image/png");
    assert.equal(prepared.images[0].dataBase64, "iVBORw==");
  });

  it("returns a safe code for an unreadable PDF", async () => {
    await assert.rejects(
      preprocessStudentDocument({
        mimeType: "application/pdf",
        bytes: Buffer.from("%PDF-not-a-real-pdf"),
      }),
      (error) =>
        error instanceof DocumentPreprocessingError &&
        error.code === "PDF_INVALID" &&
        !error.message.includes("%PDF"),
    );
  });

  it("crops a normalized identity-photo region into a bounded portrait", async () => {
    const source = await sharp({
      create: {
        width: 200,
        height: 120,
        channels: 3,
        background: { r: 32, g: 96, b: 160 },
      },
    })
      .png()
      .toBuffer();
    const portrait = await extractStudentDocumentImageRegion({
      mimeType: "image/png",
      bytes: source,
      region: {
        kind: "profile_photo",
        pageNumber: null,
        x: 0.25,
        y: 0.1,
        width: 0.5,
        height: 0.8,
      },
    });
    const metadata = await sharp(portrait).metadata();

    assert.equal(metadata.format, "jpeg");
    assert.equal(metadata.width, 640);
    assert.equal(metadata.height, 800);
  });

  it("can extract PDF text without rendering page images", async () => {
    const directory = mkdtempSync(join(tmpdir(), "vv-preprocess-test-"));
    const pdfPath = join(directory, "text.pdf");
    try {
      execFileSync(pythonExecutable, [
        "-c",
        [
          "import fitz, sys",
          "doc = fitz.open()",
          "page = doc.new_page()",
          "page.insert_text((72, 72), 'Official Transcript')",
          "doc.save(sys.argv[1])",
        ].join(";"),
        pdfPath,
      ]);
      const prepared = await preprocessStudentDocument(
        {
          mimeType: "application/pdf",
          bytes: readFileSync(pdfPath),
        },
        { maxImagePages: 0 },
      );

      assert.match(prepared.extractedText, /Official Transcript/);
      assert.deepEqual(prepared.renderedPageNumbers, []);
      assert.deepEqual(prepared.images, []);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("renders every page of a six-page transcript as its own 2048px image", async () => {
    const directory = mkdtempSync(join(tmpdir(), "vv-preprocess-test-"));
    const pdfPath = join(directory, "six-page-transcript.pdf");
    try {
      execFileSync(pythonExecutable, [
        "-c",
        [
          "import fitz, sys",
          "doc = fitz.open()",
          "[(doc.new_page().insert_text((72, 72), f'Official Transcript page {page + 1}')) for page in range(6)]",
          "doc.save(sys.argv[1])",
        ].join(";"),
        pdfPath,
      ]);
      const prepared = await preprocessStudentDocument(
        {
          mimeType: "application/pdf",
          bytes: readFileSync(pdfPath),
        },
        {
          maxImagePages: 8,
          maxImageDimension: 2_048,
          jpegQuality: 88,
        },
      );

      assert.equal(prepared.pageCount, 6);
      assert.deepEqual(prepared.renderedPageNumbers, [1, 2, 3, 4, 5, 6]);
      assert.equal(prepared.images.length, 6);
      assert.deepEqual(
        prepared.images.map((image) => image.pageNumber),
        [1, 2, 3, 4, 5, 6],
      );
      for (const image of prepared.images) {
        assert.equal(Math.max(image.width, image.height), 2_048);
        assert.equal(image.mimeType, "image/jpeg");
      }
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("creates deterministic typed and valid drawn onboarding signature PDFs", async () => {
    const templateBytes = readFileSync(
      resolve(
        __dirname,
        "./fixtures/aster-ferpa-release.pdf",
      ),
    );
    const commonInput = {
      templateBytes,
      signerName: "Alex Morgan",
      signedAt: "2026-07-24T12:00:00.000Z",
      auditReceipt: "onboarding-signature-test",
      signatureBox: {
        pageNumber: 1,
        x: 0.098,
        y: 0.488,
        width: 0.53,
        height: 0.054,
      },
    };
    const typed = await createSignedOnboardingPdf({
      ...commonInput,
      signatureMethod: "typed",
    });
    const typedReplay = await createSignedOnboardingPdf({
      ...commonInput,
      signatureMethod: "typed",
    });
    const signatureImageBytes = await sharp({
      create: {
        width: 480,
        height: 120,
        channels: 4,
        background: { r: 255, g: 255, b: 255, alpha: 0 },
      },
    })
      .composite([
        {
          input: Buffer.from(
            "<svg width='480' height='120'><path d='M20 85 C110 5 160 110 250 35 S390 100 455 25' fill='none' stroke='#173f31' stroke-width='8' stroke-linecap='round'/></svg>",
          ),
        },
      ])
      .png()
      .toBuffer();
    const drawn = await createSignedOnboardingPdf({
      ...commonInput,
      signatureMethod: "drawn",
      signatureImageData: `data:image/png;base64,${signatureImageBytes.toString("base64")}`,
    });
    const preparedTyped = await preprocessStudentDocument(
      { mimeType: "application/pdf", bytes: typed },
      { maxImagePages: 0 },
    );
    const preparedDrawn = await preprocessStudentDocument(
      { mimeType: "application/pdf", bytes: drawn },
      { maxImagePages: 0 },
    );
    const digest = (value) =>
      createHash("sha256").update(value).digest("hex");

    assert.equal(typed.subarray(0, 5).toString("ascii"), "%PDF-");
    assert.equal(drawn.subarray(0, 5).toString("ascii"), "%PDF-");
    assert.equal(digest(typed), digest(typedReplay));
    assert.match(preparedTyped.extractedText, /Alex Morgan/);
    assert.match(preparedTyped.extractedText, /Electronically signed/);
    assert.equal(preparedTyped.pageCount, 1);
    assert.equal(preparedDrawn.pageCount, 1);
  });
});
