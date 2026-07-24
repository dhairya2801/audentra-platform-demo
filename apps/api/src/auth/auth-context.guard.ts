import {
  CanActivate,
  ExecutionContext,
  Inject,
  Injectable,
} from "@nestjs/common";
import type { FastifyRequest } from "fastify";
import type { AuthenticatedRequest } from "./auth-context";
import {
  IDENTITY_RESOLVER,
  type IdentityResolver,
} from "./identity-resolver";

@Injectable()
export class AuthContextGuard implements CanActivate {
  constructor(
    @Inject(IDENTITY_RESOLVER)
    private readonly identityResolver: IdentityResolver,
  ) {}

  canActivate(context: ExecutionContext): boolean {
    if (context.getType() !== "http") return true;
    const request = context.switchToHttp().getRequest<FastifyRequest>();
    if (request.url.split("?", 1)[0]?.startsWith("/health")) return true;

    (request as AuthenticatedRequest).authContext =
      this.identityResolver.resolve(request);
    return true;
  }
}
