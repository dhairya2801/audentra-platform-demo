import { Inject, Injectable } from "@nestjs/common";
import { UnauthorizedError } from "../common/api-error";
import { APP_CONFIG, type AppConfig } from "../config/app-config";
import type { AuthContext } from "./auth-context";
import type { IdentityResolver } from "./identity-resolver";
import type { FastifyRequest } from "fastify";

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

function getSingleHeader(
  request: FastifyRequest,
  name: string,
): string | undefined {
  const value = request.headers[name];
  return typeof value === "string" ? value : undefined;
}

@Injectable()
export class DemoIdentityResolver implements IdentityResolver {
  constructor(@Inject(APP_CONFIG) private readonly config: AppConfig) {}

  resolve(request: FastifyRequest): AuthContext {
    const isStaffRoute = request.url
      .split("?", 1)[0]
      ?.startsWith("/v1/staff");
    const requestedActorType = getSingleHeader(
      request,
      "x-demo-actor-type",
    );
    if (isStaffRoute && requestedActorType !== "staff") {
      throw new UnauthorizedError(
        "Staff routes require the development staff identity header",
      );
    }
    const tenantId =
      getSingleHeader(request, "x-demo-tenant-id") ??
      this.config.demoIds.tenantId;
    const studentId =
      getSingleHeader(request, "x-demo-student-id") ??
      this.config.demoIds.studentId;
    const actorId =
      getSingleHeader(request, "x-demo-actor-id") ??
      (isStaffRoute
        ? this.config.demoIds.staffActorId ?? this.config.demoIds.actorId
        : this.config.demoIds.actorId);

    if (![tenantId, studentId, actorId].every(isUuid)) {
      throw new UnauthorizedError("Demo identity headers must be valid UUIDs");
    }

    return {
      tenantId,
      studentId,
      actorId,
      actorType: isStaffRoute ? "staff" : "student",
      authenticationMethod: "demo",
    };
  }
}
