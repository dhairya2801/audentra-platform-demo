import { DynamicModule, Module } from "@nestjs/common";
import { APP_GUARD } from "@nestjs/core";
import { ActivityController } from "./activity/activity.controller";
import { StudentAgentService } from "./agentic/student-agent.service";
import {
  AI_PROMPT_RUNTIME,
  PostgresAiPromptRuntimeRepository,
  VersionedAiPromptRuntime,
} from "./agentic/ai-prompt-runtime";
import {
  OpenRouterStudentAiGateway,
  STUDENT_AI_GATEWAY,
  type StudentAiGateway,
} from "./agentic/student-ai.gateway";
import { AuthContextGuard } from "./auth/auth-context.guard";
import { DemoIdentityResolver } from "./auth/demo-identity.resolver";
import { IDENTITY_RESOLVER } from "./auth/identity-resolver";
import { APP_CONFIG, type AppConfig } from "./config/app-config";
import { DatabaseService } from "./database/database.service";
import {
  DOCUMENT_STORAGE,
  S3DocumentStorage,
  type DocumentStorage,
} from "./documents/document-storage";
import { HealthController } from "./health/health.controller";
import { OffersController } from "./offers/offers.controller";
import { PortalController } from "./portal/portal.controller";
import {
  PLATFORM_STORE,
  type PlatformStore,
} from "./platform/platform-store";
import { PostgresPlatformStore } from "./platform/postgres-platform.store";
import { StudentController } from "./student/student.controller";
import { StudentDomainController } from "./student/student-domain.controller";

@Module({})
export class AppModule {
  static register(
    config: AppConfig,
    platformStoreOverride?: PlatformStore,
    agenticOverrides?: {
      documentStorage?: DocumentStorage;
      studentAiGateway?: StudentAiGateway;
    },
  ): DynamicModule {
    const platformProviders = platformStoreOverride
      ? [
          {
            provide: PLATFORM_STORE,
            useValue: platformStoreOverride,
          },
        ]
      : [
          DatabaseService,
          PostgresPlatformStore,
          {
            provide: PLATFORM_STORE,
            useExisting: PostgresPlatformStore,
          },
        ];
    const promptRuntimeProviders = platformStoreOverride
      ? [{ provide: AI_PROMPT_RUNTIME, useValue: undefined }]
      : [
          PostgresAiPromptRuntimeRepository,
          {
            provide: AI_PROMPT_RUNTIME,
            inject: [PostgresAiPromptRuntimeRepository],
            useFactory: (repository: PostgresAiPromptRuntimeRepository) =>
              new VersionedAiPromptRuntime(repository),
          },
        ];

    return {
      module: AppModule,
      controllers: [
        HealthController,
        StudentController,
        StudentDomainController,
        OffersController,
        PortalController,
        ActivityController,
      ],
      providers: [
        { provide: APP_CONFIG, useValue: config },
        DemoIdentityResolver,
        {
          provide: IDENTITY_RESOLVER,
          useExisting: DemoIdentityResolver,
        },
        {
          provide: APP_GUARD,
          useClass: AuthContextGuard,
        },
        StudentAgentService,
        agenticOverrides?.documentStorage
          ? {
              provide: DOCUMENT_STORAGE,
              useValue: agenticOverrides.documentStorage,
            }
          : {
              provide: DOCUMENT_STORAGE,
              useFactory: () => new S3DocumentStorage(config),
            },
        agenticOverrides?.studentAiGateway
          ? {
              provide: STUDENT_AI_GATEWAY,
              useValue: agenticOverrides.studentAiGateway,
            }
          : {
              provide: STUDENT_AI_GATEWAY,
              inject: [PLATFORM_STORE, AI_PROMPT_RUNTIME],
              useFactory: (
                store: PlatformStore,
                promptRuntime:
                  | InstanceType<typeof VersionedAiPromptRuntime>
                  | undefined,
              ) =>
                new OpenRouterStudentAiGateway(
                  config,
                  globalThis.fetch,
                  undefined,
                  config.openRouter?.storeResponses
                    ? (response) => store.recordAiProviderResponse(response)
                    : undefined,
                  promptRuntime,
                ),
            },
        ...platformProviders,
        ...promptRuntimeProviders,
      ],
    };
  }
}
