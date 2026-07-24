import { JsonStateStore, defaultDataFile } from "./store.js";

const dataFile = process.env.DEMO_API_DATA_FILE ?? defaultDataFile;
const store = new JsonStateStore(dataFile);
const state = await store.reset();
process.stdout.write(
  `${JSON.stringify({
    message: "Development fixture reset",
    fixtureVersion: state.fixture.version,
    revision: state.fixture.revision,
    dataFile: store.filePath,
  })}\n`,
);
