import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { loadAppConfig } from "../config/app-config";
import { S3DocumentStorage } from "../documents/document-storage";
import { portalMediaAssets } from "./portal-media-manifest";

async function main(): Promise<void> {
  const storage = new S3DocumentStorage(loadAppConfig());
  for (const asset of portalMediaAssets) {
    const body = await readFile(asset.localPath);
    const sha256 = createHash("sha256").update(body).digest("hex");
    if (sha256 !== asset.sha256) {
      throw new Error(`Portal media checksum mismatch: ${asset.localPath}`);
    }
    await storage.put({
      key: asset.storageKey,
      body,
      contentType: "image/jpeg",
      sha256,
    });
  }
  process.stdout.write(
    `Uploaded ${portalMediaAssets.length} tenant portal media assets\n`,
  );
}

void main();
