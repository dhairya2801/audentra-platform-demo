import {
  Body,
  Controller,
  Get,
  Headers,
  HttpCode,
  Inject,
  Post,
  Query,
  Req,
} from "@nestjs/common";
import type {
  CampusLifeFeed,
  CatalogCourse,
  StudentAcademics,
  StudentFinancials,
} from "@vv/contracts";
import type { FastifyRequest } from "fastify";
import { CurrentAuth, type AuthContext } from "../auth/auth-context";
import { requireIdempotencyKey } from "../common/idempotency-key";
import {
  PLATFORM_STORE,
  type PlatformStore,
} from "../platform/platform-store";
import { SelectPaymentPlanDto } from "../portal/portal.dto";

@Controller("v1")
export class StudentDomainController {
  constructor(
    @Inject(PLATFORM_STORE)
    private readonly store: PlatformStore,
  ) {}

  @Get("student/academics")
  academics(@CurrentAuth() auth: AuthContext): Promise<StudentAcademics> {
    return this.store.getStudentAcademics(auth);
  }

  @Get("catalog/courses")
  courses(
    @CurrentAuth() auth: AuthContext,
    @Query("query") query = "",
  ): Promise<{
    items: CatalogCourse[];
    total: number;
    catalogVersion: string;
  }> {
    return this.store.searchCatalogCourses(auth, query);
  }

  @Get("student/financials")
  financials(@CurrentAuth() auth: AuthContext): Promise<StudentFinancials> {
    return this.store.getStudentFinancials(auth);
  }

  @Post("student/financials/payment-plan")
  @HttpCode(200)
  selectPaymentPlan(
    @CurrentAuth() auth: AuthContext,
    @Body() body: SelectPaymentPlanDto,
    @Headers("idempotency-key") idempotencyKey: string | undefined,
    @Req() request: FastifyRequest,
  ): Promise<{ planId: string; status: "enrolled" }> {
    return this.store.selectFinancialPaymentPlan({
      auth,
      planId: body.planId,
      idempotencyKey: requireIdempotencyKey(idempotencyKey),
      requestId: request.id,
    });
  }

  @Get("student/campus-life")
  campusLife(@CurrentAuth() auth: AuthContext): Promise<CampusLifeFeed> {
    return this.store.getCampusLife(auth);
  }
}
