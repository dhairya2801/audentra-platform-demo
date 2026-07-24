import { Controller, Get, Inject } from "@nestjs/common";
import type { StudentDashboard } from "@vv/contracts";
import { CurrentAuth, type AuthContext } from "../auth/auth-context";
import {
  PLATFORM_STORE,
  type PlatformStore,
} from "../platform/platform-store";

@Controller("v1/student")
export class StudentController {
  constructor(
    @Inject(PLATFORM_STORE)
    private readonly platformStore: PlatformStore,
  ) {}

  @Get("dashboard")
  getDashboard(
    @CurrentAuth() auth: AuthContext,
  ): Promise<StudentDashboard> {
    return this.platformStore.getStudentDashboard(auth);
  }
}
