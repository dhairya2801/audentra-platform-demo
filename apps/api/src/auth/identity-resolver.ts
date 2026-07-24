import type { FastifyRequest } from "fastify";
import type { AuthContext } from "./auth-context";

export const IDENTITY_RESOLVER = Symbol("IDENTITY_RESOLVER");

export interface IdentityResolver {
  resolve(request: FastifyRequest): AuthContext;
}
