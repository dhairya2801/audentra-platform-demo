"use strict";

const assert = require("node:assert/strict");
const { describe, it } = require("node:test");
const {
  DocumentPreprocessingError,
  extractStudentDocumentImageRegion,
  preprocessStudentDocument,
} = require("../src/index.cjs");
const sharp = require("sharp");
const { execFileSync } = require("node:child_process");
const { mkdtempSync, readFileSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");

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
});
