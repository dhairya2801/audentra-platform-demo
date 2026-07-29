"use strict";

const { execFile } = require("node:child_process");
const { mkdtemp, readFile, rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const { promisify } = require("node:util");
const sharp = require("sharp");

const execFileAsync = promisify(execFile);
const maximumInputBytes = 10 * 1024 * 1024;
const pythonScript = join(__dirname, "preprocess_pdf.py");
const signingScript = join(__dirname, "sign_pdf.py");

class DocumentPreprocessingError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "DocumentPreprocessingError";
    this.code = code;
  }
}

async function preprocessStudentDocument(input, options = {}) {
  validateInput(input);
  if (input.mimeType !== "application/pdf") {
    return {
      extractedText: "",
      pageCount: null,
      renderedPageNumbers: [],
      textTruncated: false,
      images: [
        {
          pageNumber: null,
          mimeType: input.mimeType,
          dataBase64: input.bytes.toString("base64"),
          width: null,
          height: null,
        },
      ],
    };
  }

  const temporaryDirectory = await mkdtemp(join(tmpdir(), "vv-document-"));
  const inputPath = join(temporaryDirectory, "source.pdf");
  try {
    await writeFile(inputPath, input.bytes, { flag: "wx", mode: 0o600 });
    const { stdout } = await execFileAsync(
      resolvePythonExecutable(options.pythonExecutable),
      [
        pythonScript,
        "--input",
        inputPath,
        "--max-text-characters",
        String(boundedInteger(options.maxTextCharacters, 40_000, 2_000, 100_000)),
        "--max-text-pages",
        String(boundedInteger(options.maxTextPages, 20, 1, 100)),
        "--max-image-pages",
        String(boundedInteger(options.maxImagePages, 6, 0, 8)),
        "--max-image-dimension",
        String(boundedInteger(options.maxImageDimension, 1_400, 640, 2_048)),
        "--jpeg-quality",
        String(boundedInteger(options.jpegQuality, 72, 40, 90)),
      ],
      {
        encoding: "utf8",
        maxBuffer: 24 * 1024 * 1024,
        timeout: boundedInteger(options.timeoutMs, 30_000, 1_000, 60_000),
        windowsHide: true,
      },
    );
    return validatePreparedDocument(JSON.parse(stdout));
  } catch (error) {
    if (error instanceof DocumentPreprocessingError) throw error;
    throw mapPreprocessingError(error);
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}

async function extractStudentDocumentImageRegion(input) {
  validateInput(input);
  const region = validateNormalizedRegion(input.region);
  let imageBytes = input.bytes;

  if (input.mimeType === "application/pdf") {
    const targetPage = region.pageNumber ?? 1;
    const prepared = await preprocessStudentDocument(input, {
      maxImagePages: targetPage,
      maxTextCharacters: 2_000,
      maxTextPages: 1,
      maxImageDimension: 2_048,
      jpegQuality: 88,
    });
    const page =
      prepared.images.find((candidate) => candidate.pageNumber === targetPage) ??
      prepared.images[0];
    if (!page) {
      throw new DocumentPreprocessingError(
        "PDF_PREPROCESSING_FAILED",
        "The identity-photo page could not be rendered",
      );
    }
    imageBytes = Buffer.from(page.dataBase64, "base64");
  }

  const oriented = await sharp(imageBytes)
    .rotate()
    .toBuffer({ resolveWithObject: true });
  const imageWidth = oriented.info.width;
  const imageHeight = oriented.info.height;
  if (!imageWidth || !imageHeight) {
    throw new DocumentPreprocessingError(
      "PDF_PREPROCESSING_FAILED",
      "The identity-photo image has no readable dimensions",
    );
  }

  const left = Math.min(
    imageWidth - 1,
    Math.max(0, Math.floor(region.x * imageWidth)),
  );
  const top = Math.min(
    imageHeight - 1,
    Math.max(0, Math.floor(region.y * imageHeight)),
  );
  const width = Math.max(
    1,
    Math.min(imageWidth - left, Math.ceil(region.width * imageWidth)),
  );
  const height = Math.max(
    1,
    Math.min(imageHeight - top, Math.ceil(region.height * imageHeight)),
  );

  return sharp(oriented.data)
    .extract({ left, top, width, height })
    .resize(640, 800, { fit: "cover", position: "attention" })
    .jpeg({ quality: 88, mozjpeg: true })
    .toBuffer();
}

function validateNormalizedRegion(region) {
  if (!region || region.kind !== "profile_photo") {
    throw new TypeError("A profile_photo visual region is required");
  }
  for (const key of ["x", "y", "width", "height"]) {
    if (
      typeof region[key] !== "number" ||
      !Number.isFinite(region[key]) ||
      region[key] < 0 ||
      region[key] > 1
    ) {
      throw new TypeError(`Identity-photo ${key} must be between 0 and 1`);
    }
  }
  if (
    region.width < 0.02 ||
    region.height < 0.02 ||
    region.x + region.width > 1.001 ||
    region.y + region.height > 1.001
  ) {
    throw new TypeError("Identity-photo bounds are invalid");
  }
  if (
    region.pageNumber !== null &&
    region.pageNumber !== undefined &&
    (!Number.isInteger(region.pageNumber) ||
      region.pageNumber < 1 ||
      region.pageNumber > 8)
  ) {
    throw new TypeError("Identity-photo page number is invalid");
  }
  return region;
}

function validateInput(input) {
  if (!input || !Buffer.isBuffer(input.bytes)) {
    throw new TypeError("Document preprocessing requires Buffer bytes");
  }
  if (
    !["application/pdf", "image/jpeg", "image/png"].includes(input.mimeType)
  ) {
    throw new TypeError("Unsupported document MIME type");
  }
  if (input.bytes.length < 1 || input.bytes.length > maximumInputBytes) {
    throw new TypeError("Document preprocessing input must be between 1 byte and 10 MB");
  }
}

function validatePreparedDocument(value) {
  if (!value || typeof value !== "object" || !Array.isArray(value.images)) {
    throw new DocumentPreprocessingError(
      "PDF_PREPROCESSING_FAILED",
      "The PDF preprocessor returned an invalid result",
    );
  }
  const images = value.images.slice(0, 8).map((image) => {
    if (
      !image ||
      typeof image !== "object" ||
      typeof image.dataBase64 !== "string" ||
      image.mimeType !== "image/jpeg"
    ) {
      throw new DocumentPreprocessingError(
        "PDF_PREPROCESSING_FAILED",
        "The PDF preprocessor returned an invalid page image",
      );
    }
    return {
      pageNumber:
        Number.isInteger(image.pageNumber) && image.pageNumber > 0
          ? image.pageNumber
          : null,
      mimeType: "image/jpeg",
      dataBase64: image.dataBase64,
      width: Number.isInteger(image.width) ? image.width : null,
      height: Number.isInteger(image.height) ? image.height : null,
    };
  });
  return {
    extractedText:
      typeof value.extractedText === "string"
        ? value.extractedText.slice(0, 100_000)
        : "",
    pageCount:
      Number.isInteger(value.pageCount) && value.pageCount > 0
        ? value.pageCount
        : null,
    renderedPageNumbers: Array.isArray(value.renderedPageNumbers)
      ? value.renderedPageNumbers
          .filter((page) => Number.isInteger(page) && page > 0)
          .slice(0, 8)
      : [],
    textTruncated: value.textTruncated === true,
    images,
  };
}

function mapPreprocessingError(error) {
  const stderr =
    error && typeof error === "object" && typeof error.stderr === "string"
      ? error.stderr
      : "";
  const diagnostic = parseSafeDiagnostic(stderr);
  if (diagnostic) {
    return new DocumentPreprocessingError(
      diagnostic.code,
      safeMessageForCode(diagnostic.code),
    );
  }
  if (
    error &&
    typeof error === "object" &&
    (error.code === "ENOENT" ||
      error.code === "EACCES" ||
      (process.platform === "win32" && error.code === "UNKNOWN"))
  ) {
    return new DocumentPreprocessingError(
      "PDF_PREPROCESSOR_UNAVAILABLE",
      "The local PDF preprocessor is not available",
    );
  }
  return new DocumentPreprocessingError(
    "PDF_PREPROCESSING_FAILED",
    "The PDF could not be preprocessed safely",
  );
}

async function createSignedOnboardingPdf(input, options = {}) {
  validateSigningInput(input);
  const temporaryDirectory = await mkdtemp(join(tmpdir(), "vv-signature-"));
  const inputPath = join(temporaryDirectory, "template.pdf");
  const outputPath = join(temporaryDirectory, "signed.pdf");
  const signaturePath = join(temporaryDirectory, "signature.png");
  try {
    await writeFile(inputPath, input.templateBytes, {
      flag: "wx",
      mode: 0o600,
    });
    const args = [
      signingScript,
      "--input",
      inputPath,
      "--output",
      outputPath,
      "--signer-name",
      input.signerName,
      "--signature-method",
      input.signatureMethod,
      "--signed-at",
      input.signedAt,
      "--audit-receipt",
      input.auditReceipt,
      "--x",
      String(input.signatureBox.x),
      "--y",
      String(input.signatureBox.y),
      "--width",
      String(input.signatureBox.width),
      "--height",
      String(input.signatureBox.height),
    ];
    if (input.signatureMethod === "drawn") {
      const imageBytes = decodeSignatureImage(input.signatureImageData);
      await writeFile(signaturePath, imageBytes, {
        flag: "wx",
        mode: 0o600,
      });
      args.push("--signature-image", signaturePath);
    }
    await execFileAsync(resolvePythonExecutable(options.pythonExecutable), args, {
      encoding: "utf8",
      maxBuffer: 256 * 1024,
      timeout: boundedInteger(options.timeoutMs, 15_000, 1_000, 30_000),
      windowsHide: true,
    });
    const signed = await readFile(outputPath);
    if (
      signed.length < 100 ||
      signed.length > maximumInputBytes ||
      signed.subarray(0, 5).toString("ascii") !== "%PDF-"
    ) {
      throw new DocumentPreprocessingError(
        "PDF_PREPROCESSING_FAILED",
        "The signed PDF could not be created safely",
      );
    }
    return signed;
  } catch (error) {
    if (error instanceof DocumentPreprocessingError) throw error;
    throw mapPreprocessingError(error);
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}

function validateSigningInput(input) {
  if (
    !input ||
    !Buffer.isBuffer(input.templateBytes) ||
    input.templateBytes.length < 100 ||
    input.templateBytes.length > maximumInputBytes
  ) {
    throw new TypeError("A bounded PDF template is required");
  }
  if (
    typeof input.signerName !== "string" ||
    input.signerName.trim().length < 2 ||
    input.signerName.trim().length > 240
  ) {
    throw new TypeError("A bounded signer name is required");
  }
  if (!["typed", "drawn"].includes(input.signatureMethod)) {
    throw new TypeError("A supported signature method is required");
  }
  if (
    typeof input.signedAt !== "string" ||
    Number.isNaN(Date.parse(input.signedAt))
  ) {
    throw new TypeError("A valid signing timestamp is required");
  }
  if (
    typeof input.auditReceipt !== "string" ||
    !/^[A-Za-z0-9._:-]{8,160}$/.test(input.auditReceipt)
  ) {
    throw new TypeError("A bounded audit receipt is required");
  }
  const box = input.signatureBox;
  if (!box || !["x", "y", "width", "height"].every((key) =>
    typeof box[key] === "number" && Number.isFinite(box[key])
  )) {
    throw new TypeError("Normalized signature bounds are required");
  }
}

function decodeSignatureImage(value) {
  if (
    typeof value !== "string" ||
    !value.startsWith("data:image/png;base64,")
  ) {
    throw new TypeError("A PNG signature image is required");
  }
  const bytes = Buffer.from(value.slice("data:image/png;base64,".length), "base64");
  if (bytes.length < 20 || bytes.length > 100_000) {
    throw new TypeError("The signature image is outside the allowed size");
  }
  return bytes;
}

function resolvePythonExecutable(optionValue) {
  const configured =
    optionValue?.trim() || process.env.DOCUMENT_PYTHON_BIN?.trim();
  if (configured) {
    return process.platform === "win32" && configured === "python3"
      ? "python"
      : configured;
  }
  return process.platform === "win32" ? "python" : "python3";
}

function parseSafeDiagnostic(stderr) {
  const line = stderr.trim().split(/\r?\n/).at(-1);
  if (!line) return null;
  try {
    const value = JSON.parse(line);
    if (
      value &&
      [
        "PDF_PREPROCESSOR_UNAVAILABLE",
        "PDF_INVALID",
        "PDF_ENCRYPTED",
        "PDF_PREPROCESSING_FAILED",
      ].includes(value.code)
    ) {
      return { code: value.code };
    }
  } catch {
    return null;
  }
  return null;
}

function safeMessageForCode(code) {
  return {
    PDF_PREPROCESSOR_UNAVAILABLE:
      "The local PDF preprocessor is not available",
    PDF_INVALID: "The uploaded PDF is invalid or unreadable",
    PDF_ENCRYPTED: "Password-protected PDFs cannot be parsed",
    PDF_PREPROCESSING_FAILED: "The PDF could not be preprocessed safely",
  }[code];
}

function boundedInteger(value, fallback, minimum, maximum) {
  const number = Number(value ?? fallback);
  if (!Number.isInteger(number)) return fallback;
  return Math.max(minimum, Math.min(maximum, number));
}

exports.DocumentPreprocessingError = DocumentPreprocessingError;
exports.createSignedOnboardingPdf = createSignedOnboardingPdf;
exports.extractStudentDocumentImageRegion = extractStudentDocumentImageRegion;
exports.preprocessStudentDocument = preprocessStudentDocument;
