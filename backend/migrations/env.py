"""Use application metadata and configuration, or an explicit test connection."""

from alembic import context

from news_backend.db.config import database_url
from news_backend.db.models import Base
from news_backend.db.session import make_engine


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    run(context.config.attributes["connection"])
else:
    engine = make_engine()
    try:
        with engine.connect() as connection:
            run(connection)
    finally:
        engine.dispose()
