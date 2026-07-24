import { describe, expect, it } from "vitest";
import {
  createRequestId,
  loadAppConfig,
} from "../src/config/app-config";

describe("application configuration", () => {
  it("fails closed when the demo identity adapter is selected in production", () => {
    expect(() =>
      loadAppConfig({
        NODE_ENV: "production",
        AUTH_MODE: "demo",
      }),
    ).toThrow(/disabled in production/);
  });

  it("accepts safe inbound request IDs and replaces unsafe values", () => {
    expect(createRequestId("request.safe-0001")).toBe("request.safe-0001");
    expect(createRequestId("line\nbreak")).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });
});
