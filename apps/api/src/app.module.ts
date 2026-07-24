import { DynamicModule, Module } from "@nestjs/common";
import { APP_GUARD } from "@nestjs/core";
import { ActivityController } from "./activity/activity.controller";
import { AuthContextGuard } from "./auth/auth-context.guard";
import { DemoIdentityResolver } from "./auth/demo-identity.resolver";
import { IDENTITY_RESOLVER } from "./auth/identity-resolver";
import { APP_CONFIG, type AppConfig } from "./config/app-config";
import { DatabaseService } from "./database/database.service";
import { HealthController } from "./health/health.controller";
import { OffersController } from "./offers/offers.controller";
import { PortalController } from "./portal/portal.controller";
import {
  PLATFORM_STORE,
  type PlatformStore,
} from "./platform/platform-store";
import { PostgresPlatformStore } from "./platform/postgres-platform.store";
import { StudentController } from "./student/student.controller";

@Module({})
export class AppModule {
  static register(
    config: AppConfig,
    platformStoreOverride?: PlatformStore,
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

    return {
      module: AppModule,
      controllers: [
        HealthController,
        StudentController,
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
        ...platformProviders,
      ],
    };
  }
}
