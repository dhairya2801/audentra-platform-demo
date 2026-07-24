import { ValidationPipe } from "@nestjs/common";
import { NestFactory } from "@nestjs/core";
import type { IncomingMessage } from "node:http";
import {
  FastifyAdapter,
  type NestFastifyApplication,
} from "@nestjs/platform-fastify";
import { LogController } from "fastify";
import { AppModule } from "./app.module";
import { AllExceptionsFilter } from "./common/all-exceptions.filter";
import { RequestContextInterceptor } from "./common/request-context.interceptor";
import {
  createRequestId,
  loadAppConfig,
  type AppConfig,
} from "./config/app-config";
import type { PlatformStore } from "./platform/platform-store";

export interface CreateApiApplicationOptions {
  config?: AppConfig;
  platformStoreOverride?: PlatformStore;
  logger?: boolean;
}

export async function createApiApplication(
  options: CreateApiApplicationOptions = {},
): Promise<NestFastifyApplication> {
  const config = options.config ?? loadAppConfig();
  const adapter = new FastifyAdapter({
    logger: options.logger ?? config.environment !== "test",
    genReqId: (request: IncomingMessage) => {
      const candidate = request.headers["x-request-id"];
      return createRequestId(
        typeof candidate === "string" ? candidate : undefined,
      );
    },
    requestIdHeader: false,
    logController: new LogController({
      disableRequestLogging: config.environment === "test",
    }),
  });
  const app = await NestFactory.create<NestFastifyApplication>(
    AppModule.register(config, options.platformStoreOverride),
    adapter,
    options.logger === false
      ? { bufferLogs: false, logger: false }
      : { bufferLogs: false },
  );

  app.enableCors({
    origin: config.webOrigins,
    credentials: true,
    methods: ["GET", "POST", "PUT", "PATCH", "OPTIONS"],
    allowedHeaders: [
      "Content-Type",
      "Idempotency-Key",
      "X-Request-Id",
      "X-Demo-Tenant-Id",
      "X-Demo-Student-Id",
      "X-Demo-Actor-Id",
    ],
    exposedHeaders: ["X-Request-Id"],
    maxAge: 600,
  });
  app.useGlobalPipes(
    new ValidationPipe({
      transform: true,
      whitelist: true,
      forbidNonWhitelisted: true,
      forbidUnknownValues: true,
      stopAtFirstError: false,
    }),
  );
  app.useGlobalFilters(new AllExceptionsFilter());
  app.useGlobalInterceptors(new RequestContextInterceptor());
  await app.init();
  await app.getHttpAdapter().getInstance().ready();
  return app;
}
