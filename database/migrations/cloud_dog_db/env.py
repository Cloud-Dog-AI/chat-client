from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from cloud_dog_chat_client.database.models import PlatformBase

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = PlatformBase.metadata


def _version_kwargs() -> dict[str, str]:
    kwargs: dict[str, str] = {}
    version_table = str(config.get_main_option("version_table") or "").strip()
    if version_table:
        kwargs["version_table"] = version_table
    version_table_schema = str(
        config.get_main_option("version_table_schema") or ""
    ).strip()
    if version_table_schema:
        kwargs["version_table_schema"] = version_table_schema
    return kwargs


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=str(url).startswith("sqlite"),
        **_version_kwargs(),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
            **_version_kwargs(),
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
