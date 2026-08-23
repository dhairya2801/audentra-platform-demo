import { gzipSync } from "node:zlib";

/**
 * Gzip's final header byte is an operating-system marker. zlib can vary it
 * between runtimes even when the compressed JSON payload is otherwise
 * identical, so pin it to the RFC-defined "unknown" value.
 */
export const GZIP_OS_BYTE_OFFSET = 9;
export const GZIP_OS_UNKNOWN = 0xff;

/**
 * Serializes a generated universe as a byte-stable gzip archive.
 *
 * The archive is checked into source control and its compressed digest is
 * verified by the Python seeder, so it must be reproducible on Windows and
 * Linux, not only on the machine that last regenerated it.
 */
export function gzipCanonicalJson(value) {
  const archive = gzipSync(Buffer.from(JSON.stringify(value), "utf8"), { level: 9 });
  archive[GZIP_OS_BYTE_OFFSET] = GZIP_OS_UNKNOWN;
  return archive;
}
