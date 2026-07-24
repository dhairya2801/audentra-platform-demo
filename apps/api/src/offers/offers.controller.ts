import {
  Controller,
  Headers,
  HttpCode,
  Inject,
  Param,
  ParseUUIDPipe,
  Post,
  Req,
} from "@nestjs/common";
import type { AcceptOfferResponse } from "@vv/contracts";
import type { FastifyRequest } from "fastify";
import { CurrentAuth, type AuthContext } from "../auth/auth-context";
import { requireIdempotencyKey } from "../common/idempotency-key";
import {
  PLATFORM_STORE,
  type PlatformStore,
} from "../platform/platform-store";

@Controller("v1/admission-offers")
export class OffersController {
  constructor(
    @Inject(PLATFORM_STORE)
    private readonly platformStore: PlatformStore,
  ) {}

  @Post(":offerId/accept")
  @HttpCode(200)
  accept(
    @CurrentAuth() auth: AuthContext,
    @Param("offerId", new ParseUUIDPipe()) offerId: string,
    @Headers("idempotency-key") idempotencyKey: string | undefined,
    @Req() request: FastifyRequest,
  ): Promise<AcceptOfferResponse> {
    return this.platformStore.acceptAdmissionOffer({
      auth,
      offerId,
      idempotencyKey: requireIdempotencyKey(idempotencyKey),
      requestId: request.id,
    });
  }
}
