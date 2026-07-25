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
  Res,
} from "@nestjs/common";
import "@fastify/multipart";
import type { Multipart } from "@fastify/multipart";
import type {
  AskEdwardResponse,
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
import type { FastifyReply, FastifyRequest } from "fastify";
import { StudentAgentService } from "../agentic/student-agent.service";
import { CurrentAuth, type AuthContext } from "../auth/auth-context";
import { ApiError, BadRequestError } from "../common/api-error";
import { requireIdempotencyKey } from "../common/idempotency-key";
import {
  PLATFORM_STORE,
  type PlatformStore,
} from "../platform/platform-store";
import {
  AskEdwardDto,
  CompleteStudentOnboardingDto,
  ConfirmStudentDocumentExtractionDto,
  CreateDepositPaymentDto,
  CreateStudentAppointmentDto,
  CreateStudentDocumentDto,
  UpdateStudentOnboardingDto,
  UpdateStudentProfileDto,
} from "./portal.dto";

type MultipartRequest = FastifyRequest & {
  isMultipart(): boolean;
  parts(): AsyncIterableIterator<Multipart>;
};

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

@Controller("v1/student")
export class PortalController {
  constructor(
    @Inject(PLATFORM_STORE)
    private readonly store: PlatformStore,
    private readonly studentAgent: StudentAgentService,
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
    @Param("id") id: string,
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

  @Post("documents/upload")
  @HttpCode(201)
  async uploadDocument(
    @CurrentAuth() auth: AuthContext,
    @Headers("idempotency-key") idempotencyKey: string | undefined,
    @Req() request: MultipartRequest,
  ): Promise<StudentDocument> {
    if (!request.isMultipart()) {
      throw new ApiError(
        415,
        "MULTIPART_REQUIRED",
        "Content-Type must be multipart/form-data",
      );
    }
    let file:
      | {
          fileName: string;
          mimeType: string;
          bytes: Buffer;
        }
      | undefined;
    let category: string | undefined;
    let requirementId: string | undefined;
    try {
      for await (const part of request.parts()) {
        if (part.type === "file") {
          if (file || part.fieldname !== "file") {
            throw new BadRequestError(
              "ONE_DOCUMENT_REQUIRED",
              "Upload exactly one document file",
            );
          }
          file = {
            fileName: part.filename,
            mimeType: part.mimetype,
            bytes: await part.toBuffer(),
          };
        } else if (part.fieldname === "category") {
          category = String(part.value);
        } else if (part.fieldname === "requirementId") {
          requirementId = String(part.value);
        } else {
          throw new BadRequestError(
            "UNEXPECTED_UPLOAD_FIELD",
            "Only file, category, and requirementId fields are accepted",
          );
        }
      }
    } catch (error) {
      if (
        error &&
        typeof error === "object" &&
        "code" in error &&
        typeof error.code === "string" &&
        [
          "FST_PARTS_LIMIT",
          "FST_FILES_LIMIT",
          "FST_FIELDS_LIMIT",
          "FST_REQ_FILE_TOO_LARGE",
        ].includes(error.code)
      ) {
        throw new ApiError(
          413,
          "DOCUMENT_TOO_LARGE",
          "Upload one document no larger than 10 MB",
        );
      }
      throw error;
    }
    if (!file) {
      throw new BadRequestError(
        "FILE_REQUIRED",
        "Choose a document file to upload",
      );
    }
    if (requirementId && !isUuid(requirementId)) {
      throw new BadRequestError(
        "INVALID_REQUIREMENT_ID",
        "The document requirement is invalid",
      );
    }
    return this.studentAgent.uploadDocument({
      auth,
      ...file,
      ...(category ? { category } : {}),
      ...(requirementId ? { requirementId } : {}),
      idempotencyKey: requireIdempotencyKey(idempotencyKey),
      requestId: request.id,
    });
  }

  @Get("documents/:id/content")
  async documentContent(
    @CurrentAuth() auth: AuthContext,
    @Param("id", new ParseUUIDPipe()) id: string,
    @Res() reply: FastifyReply,
  ): Promise<void> {
    const document = await this.studentAgent.getDocumentContent({
      auth,
      documentId: id,
    });
    reply
      .type(document.mimeType)
      .header(
        "content-disposition",
        `inline; filename="${document.fileName.replaceAll('"', "")}"`,
      )
      .header("cache-control", "private, no-store")
      .send(document.bytes);
  }

  @Post("documents/:id/confirm-extraction")
  @HttpCode(200)
  confirmDocumentExtraction(
    @CurrentAuth() auth: AuthContext,
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() confirmation: ConfirmStudentDocumentExtractionDto,
    @Headers("idempotency-key") idempotencyKey: string | undefined,
    @Req() request: FastifyRequest,
  ): Promise<StudentDocument> {
    return this.studentAgent.confirmDocumentExtraction({
      auth,
      documentId: id,
      confirmation,
      idempotencyKey: requireIdempotencyKey(idempotencyKey),
      requestId: request.id,
    });
  }

  @Post("assistant/messages")
  @HttpCode(200)
  askEdward(
    @CurrentAuth() auth: AuthContext,
    @Body() question: AskEdwardDto,
  ): Promise<AskEdwardResponse> {
    return this.studentAgent.askEdward({ auth, question });
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
