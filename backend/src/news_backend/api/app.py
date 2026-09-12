"""FastAPI application construction with explicit database ownership."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from news_backend.db.session import make_engine, make_session_factory

from .routes import router


def create_app(*, session_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if session_factory is not None:
            app.state.session_factory = session_factory
            yield
            return

        engine = make_engine()
        app.state.session_factory = make_session_factory(engine)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Kun News API", lifespan=lifespan)
    app.include_router(router)
    return app
