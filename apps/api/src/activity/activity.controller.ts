import {
  Body,
  Controller,
  HttpCode,
  Inject,
  Post,
  Req,
} from "@nestjs/common";
import type { FastifyRequest } from "fastify";
import { CurrentAuth, type AuthContext } from "../auth/auth-context";
import {
  PLATFORM_STORE,
  type ActivityIngestionResult,
  type PlatformStore,
} from "../platform/platform-store";
import { ActivityEventBatchDto } from "./activity.dto";

@Controller("v1/activity-events")
export class ActivityController {
  constructor(
    @Inject(PLATFORM_STORE)
    private readonly platformStore: PlatformStore,
  ) {}

  @Post("batch")
  @HttpCode(202)
  ingestBatch(
    @CurrentAuth() auth: AuthContext,
    @Body() batch: ActivityEventBatchDto,
    @Req() request: FastifyRequest,
  ): Promise<ActivityIngestionResult> {
    return this.platformStore.ingestActivityEvents({
      auth,
      events: batch.events,
      requestId: request.id,
    });
  }
}
