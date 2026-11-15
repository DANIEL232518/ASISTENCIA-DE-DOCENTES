"""Seed de inicialización: cuenta por defecto de la Directora + iconos PWA.

Se ejecuta de forma idempotente al arrancar la aplicación.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from .auth import hash_password
from .database import SessionLocal
from .models import ROL_ADMIN, ROL_DIRECTORA, Docente, Usuario
from .utils_qr import asegurar_avatar, generar_iconos_pwa

logger = logging.getLogger("asistencia.seed")

# Cuentas base de la academia. La de Dirección es única e intransferible;
# la otra cuenta es de Secretaría / Administrativo.
CUENTA_DIRECTORA = {
    "nombre": "Nayeli Villafan",
    "email": "directora@academia.pe",
    "password": "Directora123",
    "rol": ROL_DIRECTORA,
}
CUENTA_ADMIN = {
    "nombre": "Secretaría",
    "email": "admin@academia.pe",
    "password": "Admin123",
    "rol": ROL_ADMIN,
}

# Nombres que usaban las versiones anteriores. Si una cuenta base todavía tiene
# uno de estos, se renombra con los datos reales; si ya fue personalizada, se
# respeta lo que haya puesto la Directora.
_NOMBRES_ANTIGUOS = {
    "directora general",
    "directora",
    "administrativo",
    "admin",
    "secretaria",
}


async def crear_si_falta(db, datos: dict) -> Usuario:
    email = datos["email"].lower().strip()
    existente = await db.scalar(select(Usuario).where(func.lower(Usuario.email) == email))
    if existente:
        return existente
    usuario = Usuario(
        nombre=datos["nombre"],
        email=email,
        password_hash=hash_password(datos["password"]),
        rol=datos["rol"],
        activo=True,
        creado_en=datetime.utcnow(),
    )
    db.add(usuario)
    await db.commit()
    await db.refresh(usuario)
    logger.info("Cuenta creada: %s (%s)", email, datos["rol"])
    return usuario


async def reparar_avatares() -> None:
    """Regenera los avatares que falten y corrige rutas obsoletas.

    Los avatares se guardan en el volumen `uploads`; si un docente quedó
    apuntando a un archivo que ya no existe (por ejemplo porque antes vivían en
    `/static/img`, que no persiste), se vuelve a generar con sus iniciales.
    """
    try:
        async with SessionLocal() as db:
            docentes = (await db.scalars(select(Docente))).all()
            reparados = 0
            for docente in docentes:
                try:
                    ruta = await run_in_threadpool(asegurar_avatar, docente)
                except Exception:  # noqa: BLE001
                    logger.exception("No se pudo reparar el avatar del docente %s", docente.id)
                    continue
                if ruta != docente.foto_path:
                    docente.foto_path = ruta
                    reparados += 1
            if reparados:
                await db.commit()
                logger.info("Avatares regenerados: %s", reparados)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudieron reparar los avatares")


async def actualizar_cuentas_base(db) -> None:
    """Alinea las cuentas base con los datos reales de la academia.

    - La cuenta de Dirección pasa a llamarse "Nayeli Villafan" (solo si todavía
      conserva un nombre por defecto, para no pisar cambios manuales).
    - Garantiza que la cuenta de Dirección tenga el rol DIRECTORA y esté activa,
      porque esa cuenta es la única que no se puede eliminar ni desactivar.
    """
    directora = await db.scalar(
        select(Usuario).where(func.lower(Usuario.email) == CUENTA_DIRECTORA["email"].lower())
    )
    if directora is not None:
        if directora.nombre.strip().lower() in _NOMBRES_ANTIGUOS:
            directora.nombre = CUENTA_DIRECTORA["nombre"]
        if directora.rol != ROL_DIRECTORA:
            directora.rol = ROL_DIRECTORA
        if not directora.activo:
            directora.activo = True
        await db.commit()
        logger.info("Cuenta de Dirección verificada: %s", directora.nombre)

    admin = await db.scalar(
        select(Usuario).where(func.lower(Usuario.email) == CUENTA_ADMIN["email"].lower())
    )
    if admin is not None and admin.nombre.strip().lower() in _NOMBRES_ANTIGUOS:
        admin.nombre = CUENTA_ADMIN["nombre"]
        await db.commit()
        logger.info("Cuenta de Secretaría verificada: %s", admin.nombre)


async def seed() -> None:
    """Crea las cuentas por defecto y los recursos estáticos iniciales."""
    try:
        async with SessionLocal() as db:
            await crear_si_falta(db, CUENTA_DIRECTORA)
            await crear_si_falta(db, CUENTA_ADMIN)
            await actualizar_cuentas_base(db)
    except Exception:  # noqa: BLE001
        logger.exception("Error al crear las cuentas por defecto")
        raise

    try:
        generar_iconos_pwa()
        logger.info("Iconos PWA generados")
    except Exception:  # noqa: BLE001
        logger.exception("No se pudieron generar los iconos PWA")

    await reparar_avatares()

    logger.info("Seed completado. Directora: %s / Admin: %s",
                CUENTA_DIRECTORA["email"], CUENTA_ADMIN["email"])
