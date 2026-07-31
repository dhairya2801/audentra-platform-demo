import {
  Body,
  Controller,
  Get,
  Param,
  ParseUUIDPipe,
  Patch,
  Post,
  Req,
} from "@nestjs/common";
import type {
  StaffActionCenter,
  StaffDocumentDecisionResult,
  StaffStudentRecord,
  StaffWorkItem,
} from "@vv/contracts";
import type { FastifyRequest } from "fastify";
import { CurrentAuth, type AuthContext } from "../auth/auth-context";
import { PostgresStaffActionStore } from "./staff-action.store";
import {
  ReviewStaffDocumentDto,
  UpdateStaffStudentPreferencesDto,
  UpdateStaffWorkItemDto,
} from "./staff.dto";

@Controller("v1/staff")
export class StaffController {
  constructor(private readonly staff: PostgresStaffActionStore) {}

  @Get("action-center")
  actionCenter(
    @CurrentAuth() auth: AuthContext,
  ): Promise<StaffActionCenter> {
    return this.staff.getActionCenter(auth);
  }

  @Patch("work-items/:id")
  updateWorkItem(
    @CurrentAuth() auth: AuthContext,
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() update: UpdateStaffWorkItemDto,
    @Req() request: FastifyRequest,
  ): Promise<StaffWorkItem> {
    return this.staff.updateWorkItem({
      auth,
      workItemId: id,
      update,
      requestId: request.id,
    });
  }

  @Get("students/:id")
  student(
    @CurrentAuth() auth: AuthContext,
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<StaffStudentRecord> {
    return this.staff.getStudentRecord(auth, id);
  }

  @Patch("students/:id/preferences")
  updateStudentPreferences(
    @CurrentAuth() auth: AuthContext,
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() update: UpdateStaffStudentPreferencesDto,
    @Req() request: FastifyRequest,
  ): Promise<StaffStudentRecord> {
    return this.staff.updateStudentPreferences({
      auth,
      studentId: id,
      update,
      requestId: request.id,
    });
  }

  @Post("documents/:id/decision")
  reviewDocument(
    @CurrentAuth() auth: AuthContext,
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() review: ReviewStaffDocumentDto,
    @Req() request: FastifyRequest,
  ): Promise<StaffDocumentDecisionResult> {
    return this.staff.reviewDocument({
      auth,
      documentId: id,
      review,
      requestId: request.id,
    });
  }
}
