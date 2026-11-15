"""Conexión asíncrona a PostgreSQL 16 (SQLAlchemy 2.0 + asyncpg)."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://asistencia:academia2026@localhost:5432/asistencia",
)

# Pool pequeño y robusto: la app está pensada para <= 40 docentes concurrentes.
# SQLite (usado en pruebas locales) no admite pool_size/max_overflow.
_ES_SQLITE = DATABASE_URL.startswith("sqlite")
_OPCIONES_POOL = (
    {}
    if _ES_SQLITE
    else {
        "pool_size": int(os.getenv("DB_POOL_SIZE", "15")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "15")),
    }
)
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=1800,
    **_OPCIONES_POOL,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base declarativa común a todos los modelos."""


async def get_db():
    """Dependencia FastAPI: sesión asíncrona por request."""
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def esperar_base_de_datos(intentos: int = 60, espera: float = 2.0) -> None:
    """Reintenta la conexión (el contenedor `db` puede tardar en estar healthy)."""
    ultimo_error: Exception | None = None
    for _ in range(intentos):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - queremos reintentar ante cualquier error
            ultimo_error = exc
            await asyncio.sleep(espera)
    raise RuntimeError(f"No se pudo conectar a la base de datos: {ultimo_error}")
