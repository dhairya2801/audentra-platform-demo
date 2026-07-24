import {
  GetObjectCommand,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import type { AppConfig } from "../config/app-config";

export const DOCUMENT_STORAGE = Symbol("DOCUMENT_STORAGE");

export interface DocumentStorage {
  put(input: {
    key: string;
    body: Buffer;
    contentType: string;
    sha256: string;
  }): Promise<void>;
  get(key: string): Promise<Buffer>;
}

export class S3DocumentStorage implements DocumentStorage {
  private readonly client: S3Client;
  private readonly bucket: string;

  constructor(config: AppConfig) {
    const storage = config.objectStorage ?? {
      endpoint: "http://localhost:9000",
      region: "us-east-1",
      bucket: "vv-documents",
      accessKeyId: "vv_minio",
      secretAccessKey: "vv_minio_password",
      forcePathStyle: true,
    };
    this.bucket = storage.bucket;
    this.client = new S3Client({
      endpoint: storage.endpoint,
      region: storage.region,
      forcePathStyle: storage.forcePathStyle,
      credentials: {
        accessKeyId: storage.accessKeyId,
        secretAccessKey: storage.secretAccessKey,
      },
    });
  }

  async put(input: {
    key: string;
    body: Buffer;
    contentType: string;
    sha256: string;
  }): Promise<void> {
    await this.client.send(
      new PutObjectCommand({
        Bucket: this.bucket,
        Key: input.key,
        Body: input.body,
        ContentType: input.contentType,
        Metadata: { sha256: input.sha256 },
      }),
    );
  }

  async get(key: string): Promise<Buffer> {
    const response = await this.client.send(
      new GetObjectCommand({ Bucket: this.bucket, Key: key }),
    );
    if (!response.Body) throw new Error("Document object has no body");
    return Buffer.from(await response.Body.transformToByteArray());
  }
}
