export type SupportedStudentDocumentMimeType =
  | "application/pdf"
  | "image/jpeg"
  | "image/png";

export interface PreparedDocumentImage {
  pageNumber: number | null;
  mimeType: "image/jpeg" | "image/png";
  dataBase64: string;
  width: number | null;
  height: number | null;
}

export interface PreparedStudentDocument {
  extractedText: string;
  pageCount: number | null;
  renderedPageNumbers: number[];
  textTruncated: boolean;
  images: PreparedDocumentImage[];
}

export interface DocumentPreprocessingOptions {
  pythonExecutable?: string;
  timeoutMs?: number;
  maxTextCharacters?: number;
  maxTextPages?: number;
  maxImagePages?: number;
  maxImageDimension?: number;
  jpegQuality?: number;
}

export class DocumentPreprocessingError extends Error {
  readonly code:
    | "PDF_PREPROCESSOR_UNAVAILABLE"
    | "PDF_INVALID"
    | "PDF_ENCRYPTED"
    | "PDF_PREPROCESSING_FAILED";
}

export function preprocessStudentDocument(
  input: {
    bytes: Buffer;
    mimeType: SupportedStudentDocumentMimeType;
  },
  options?: DocumentPreprocessingOptions,
): Promise<PreparedStudentDocument>;

export function extractStudentDocumentImageRegion(input: {
  bytes: Buffer;
  mimeType: SupportedStudentDocumentMimeType;
  region: {
    kind: "profile_photo";
    pageNumber: number | null;
    x: number;
    y: number;
    width: number;
    height: number;
  };
}): Promise<Buffer>;

export function createSignedOnboardingPdf(
  input: {
    templateBytes: Buffer;
    signerName: string;
    signatureMethod: "typed" | "drawn";
    signatureImageData?: string;
    signedAt: string;
    auditReceipt: string;
    signatureBox: {
      x: number;
      y: number;
      width: number;
      height: number;
    };
  },
  options?: {
    pythonExecutable?: string;
    timeoutMs?: number;
  },
): Promise<Buffer>;
