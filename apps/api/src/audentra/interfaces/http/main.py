"""Production process entry point for the FastAPI HTTP adapter."""

import uvicorn

from audentra.bootstrap.api import create_production_app
from audentra.bootstrap.settings import RuntimeSettings

settings = RuntimeSettings.from_environment()
app = create_production_app(settings)


def run() -> None:
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
    )
