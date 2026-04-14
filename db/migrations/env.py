from logging.config import fileConfig
import os

from sqlalchemy import engine_from_config, pool, text
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def get_db_url() -> str:
    """Resolve database URL.

    Priority order:
      1. ALEMBIC_DB_URL env var (useful for CI and local dev with a direct URL)
      2. Secret Manager secret named by DB_SECRET_NAME env var (GCP environments)
      3. alembic.ini sqlalchemy.url (fallback for local testing only)
    """
    if url := os.environ.get("ALEMBIC_DB_URL"):
        return url

    secret_name = os.environ.get("DB_SECRET_NAME")
    if secret_name:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(name=f"{secret_name}/versions/latest")
        return response.payload.data.decode("utf-8")

    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    url = get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_db_url()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
