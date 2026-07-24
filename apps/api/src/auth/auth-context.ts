import { createParamDecorator, ExecutionContext } from "@nestjs/common";
import type { FastifyRequest } from "fastify";

export interface AuthContext {
  tenantId: string;
  studentId: string;
  actorId: string;
  actorType: "student";
  authenticationMethod: "demo";
}

export type AuthenticatedRequest = FastifyRequest & {
  authContext?: AuthContext;
};

export const CurrentAuth = createParamDecorator(
  (_data: unknown, context: ExecutionContext): AuthContext => {
    const request = context.switchToHttp().getRequest<AuthenticatedRequest>();
    if (!request.authContext) {
      throw new Error("Auth guard did not establish an authentication context");
    }
    return request.authContext;
  },
);
