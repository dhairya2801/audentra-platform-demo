import {
  Body,
  Controller,
  Get,
  Headers,
  HttpCode,
  Inject,
  Param,
  ParseUUIDPipe,
  Patch,
  Post,
  Put,
  Req,
} from "@nestjs/common";
import type {
  StudentAppointment,
  StudentAppointmentList,
  StudentBootstrap,
  StudentDocument,
  StudentDocumentList,
  StudentHelp,
  StudentMessage,
  StudentMessageList,
  StudentOnboarding,
  StudentPayment,
  StudentPaymentList,
  StudentProfile,
  StudentRequirementDetail,
  StudentRequirementList,
} from "@vv/contracts";
import type { FastifyRequest } from "fastify";
import { CurrentAuth, type AuthContext } from "../auth/auth-context";
import { requireIdempotencyKey } from "../common/idempotency-key";
import {
  PLATFORM_STORE,
  type PlatformStore,
} from "../platform/platform-store";
import {
  CompleteStudentOnboardingDto,
  CreateDepositPaymentDto,
  CreateStudentAppointmentDto,
  CreateStudentDocumentDto,
  UpdateStudentOnboardingDto,
  UpdateStudentProfileDto,
} from "./portal.dto";

@Controller("v1/student")
export class PortalController {
  constructor(
    @Inject(PLATFORM_STORE)
    private readonly store: PlatformStore,
  ) {}

  @Get("bootstrap")
  bootstrap(@CurrentAuth() auth: AuthContext): Promise<StudentBootstrap> {
    return this.store.getStudentBootstrap(auth);
  }

  @Get("onboarding")
  onboarding(@CurrentAuth() auth: AuthContext): Promise<StudentOnboarding> {
    return this.store.getStudentOnboarding(auth);
  }

  @Put("onboarding")
  updateOnboarding(
    @CurrentAuth() auth: AuthContext,
    @Body() update: UpdateStudentOnboardingDto,
    @Req() request: FastifyRequest,
  ): Promise<StudentOnboarding> {
    return this.store.updateStudentOnboarding({
      auth,
      update,
      requestId: request.id,
    });
  }

  @Post("onboarding/complete")
  @HttpCode(200)
  completeOnboarding(
    @CurrentAuth() auth: AuthContext,
    @Body() update: CompleteStudentOnboardingDto,
    @Headers("idempotency-key") idempotencyKey: string | undefined,
    @Req() request: FastifyRequest,
  ): Promise<StudentOnboarding> {
    return this.store.completeStudentOnboarding({
      auth,
      update,
      idempotencyKey: requireIdempotencyKey(idempotencyKey),
      requestId: request.id,
    });
  }

  @Get("requirements")
  requirements(
    @CurrentAuth() auth: AuthContext,
  ): Promise<StudentRequirementList> {
    return this.store.getStudentRequirements(auth);
  }

  @Get("requirements/:id")
  requirement(
    @CurrentAuth() auth: AuthContext,
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<StudentRequirementDetail> {
    return this.store.getStudentRequirement(auth, id);
  }

  @Get("messages")
  messages(@CurrentAuth() auth: AuthContext): Promise<StudentMessageList> {
    return this.store.getStudentMessages(auth);
  }

  @Post("messages/:id/read")
  @HttpCode(200)
  markMessageRead(
    @CurrentAuth() auth: AuthContext,
    @Param("id", new ParseUUIDPipe()) id: string,
    @Req() request: FastifyRequest,
  ): Promise<StudentMessage> {
    return this.store.markStudentMessageRead({
      auth,
      messageId: id,
      requestId: request.id,
    });
  }

  @Get("documents")
  documents(@CurrentAuth() auth: AuthContext): Promise<StudentDocumentList> {
    return this.store.getStudentDocuments(auth);
  }

  @Post("documents")
  @HttpCode(201)
  createDocument(
    @CurrentAuth() auth: AuthContext,
    @Body() document: CreateStudentDocumentDto,
    @Headers("idempotency-key") idempotencyKey: string | undefined,
    @Req() request: FastifyRequest,
  ): Promise<StudentDocument> {
    return this.store.createStudentDocument({
      auth,
      document,
      idempotencyKey: requireIdempotencyKey(idempotencyKey),
      requestId: request.id,
    });
  }

  @Get("appointments")
  appointments(
    @CurrentAuth() auth: AuthContext,
  ): Promise<StudentAppointmentList> {
    return this.store.getStudentAppointments(auth);
  }

  @Post("appointments")
  @HttpCode(201)
  createAppointment(
    @CurrentAuth() auth: AuthContext,
    @Body() appointment: CreateStudentAppointmentDto,
    @Headers("idempotency-key") idempotencyKey: string | undefined,
    @Req() request: FastifyRequest,
  ): Promise<StudentAppointment> {
    return this.store.createStudentAppointment({
      auth,
      appointment,
      idempotencyKey: requireIdempotencyKey(idempotencyKey),
      requestId: request.id,
    });
  }

  @Get("payments")
  payments(@CurrentAuth() auth: AuthContext): Promise<StudentPaymentList> {
    return this.store.getStudentPayments(auth);
  }

  @Post("payments/deposit")
  @HttpCode(200)
  createDeposit(
    @CurrentAuth() auth: AuthContext,
    @Body() payment: CreateDepositPaymentDto,
    @Headers("idempotency-key") idempotencyKey: string | undefined,
    @Req() request: FastifyRequest,
  ): Promise<StudentPayment> {
    return this.store.createDepositPayment({
      auth,
      payment,
      idempotencyKey: requireIdempotencyKey(idempotencyKey),
      requestId: request.id,
    });
  }

  @Get("profile")
  profile(@CurrentAuth() auth: AuthContext): Promise<StudentProfile> {
    return this.store.getStudentProfile(auth);
  }

  @Patch("profile")
  updateProfile(
    @CurrentAuth() auth: AuthContext,
    @Body() update: UpdateStudentProfileDto,
    @Req() request: FastifyRequest,
  ): Promise<StudentProfile> {
    return this.store.updateStudentProfile({
      auth,
      update,
      requestId: request.id,
    });
  }

  @Get("help")
  help(@CurrentAuth() auth: AuthContext): Promise<StudentHelp> {
    return this.store.getStudentHelp(auth);
  }
}
