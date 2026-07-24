import {
  ArgumentsHost,
  Catch,
  ExceptionFilter,
  HttpException,
  HttpStatus,
} from "@nestjs/common";
import type { ApiErrorResponse } from "@vv/contracts";
import type { FastifyReply, FastifyRequest } from "fastify";
import { ApiError } from "./api-error";

@Catch()
export class AllExceptionsFilter implements ExceptionFilter {
  catch(exception: unknown, host: ArgumentsHost): void {
    const context = host.switchToHttp();
    const request = context.getRequest<FastifyRequest>();
    const reply = context.getResponse<FastifyReply>();

    let statusCode = HttpStatus.INTERNAL_SERVER_ERROR;
    let code = "INTERNAL_ERROR";
    let message = "An unexpected error occurred";

    if (exception instanceof ApiError) {
      statusCode = exception.statusCode;
      code = exception.code;
      message = exception.message;
    } else if (exception instanceof HttpException) {
      statusCode = exception.getStatus();
      code = statusCode === 400 ? "VALIDATION_ERROR" : "HTTP_ERROR";
      const response = exception.getResponse();
      if (typeof response === "string") {
        message = response;
      } else if (
        typeof response === "object" &&
        response !== null &&
        "message" in response
      ) {
        const responseMessage = response.message;
        message = Array.isArray(responseMessage)
          ? responseMessage.join("; ")
          : String(responseMessage);
      }
    }

    if (statusCode >= 500) {
      request.log.error(
        { err: exception, requestId: request.id },
        "request failed",
      );
    }

    const body: ApiErrorResponse = {
      error: {
        code,
        message,
        requestId: request.id,
      },
    };
    void reply.status(statusCode).send(body);
  }
}
