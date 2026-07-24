import "reflect-metadata";
import { createApiApplication } from "./create-app";
import { loadAppConfig } from "./config/app-config";

async function bootstrap(): Promise<void> {
  const config = loadAppConfig();
  const app = await createApiApplication({ config });
  app.enableShutdownHooks();
  await app.listen(config.port, "0.0.0.0");
}

void bootstrap();
