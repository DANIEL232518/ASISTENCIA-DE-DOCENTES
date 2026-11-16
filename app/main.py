"""API y páginas de la PWA de control de asistencia docente (FastAPI)."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import zipfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from starlette.concurrency import run_in_threadpool

from . import schemas, utils_excel, utils_qr
from . import pagos as pagos_calc
from .auth import (
    COOKIE_NAME,
    JWT_MINUTOS,
    crear_token,
    get_usuario_actual,
    hash_password,
    requiere_directora,
    verify_password,
)
from .database import Base, engine, esperar_base_de_datos, get_db
from .models import (
    ESTADO_PUNTUAL,
    ESTADO_TARDANZA,
    MAX_DOCENTES_ACTIVOS,
    ROL_ADMIN,
    ROL_DIRECTORA,
    Asistencia,
    Docente,
    Evento,
    Usuario,
)
from .seed import seed

_NIVEL_LOG = os.getenv("LOG_LEVEL", "INFO").strip().upper()
if _NIVEL_LOG not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}:
    _NIVEL_LOG = "INFO"
logging.basicConfig(
    level=_NIVEL_LOG,
    format="%(asctime)s · %(levelname)s · %(name)s · %(message)s",
)
logger = logging.getLogger("asistencia")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

TZ = ZoneInfo(os.getenv("TZ_NAME", "America/Lima"))

app = FastAPI(
    title="Control de Asistencia Docente",
    description="PWA de escaneo QR de ingreso docente con reportes y credenciales.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _version_assets() -> str:
    """Huella corta de los estáticos clave para invalidar la caché al cambiarlos.

    Se añade como `?v=` a los <link>/<script> del HTML (que va con network-first),
    de modo que cada despliegue fuerza la descarga del JS/CSS nuevos aunque el
    Service Worker tenga copias antiguas.
    """
    h = hashlib.md5()
    for rel in ("js/app.js", "css/tailwind.css", "vendor/html5-qrcode.js"):
        ruta = STATIC_DIR / rel
        try:
            h.update(ruta.read_bytes())
        except OSError:
            h.update(rel.encode("utf-8"))
    return h.hexdigest()[:10]


ASSET_V = _version_assets()
templates.env.globals["asset_v"] = ASSET_V


# ===========================================================================
# Utilidades de tiempo / eventos
# ===========================================================================
def ahora() -> datetime:
    """Hora local de Lima sin zona horaria (se almacena naive en PostgreSQL)."""
    return datetime.now(TZ).replace(tzinfo=None)


def hoy() -> date:
    return ahora().date()


async def hay_solapamiento(
    db: AsyncSession, fecha: date, inicio: time, fin: time, ignorar_id: int | None = None
) -> bool:
    """No existen eventos paralelos: se verifica solapamiento en la misma fecha."""
    stmt = select(func.count()).select_from(Evento).where(
        Evento.fecha == fecha,
        Evento.activo.is_(True),
        Evento.hora_inicio < fin,
        Evento.hora_fin > inicio,
    )
    if ignorar_id is not None:
        stmt = stmt.where(Evento.id != ignorar_id)
    total = (await db.scalar(stmt)) or 0
    return total > 0


async def evento_en_bloque(
    db: AsyncSession, momento: datetime, incluir_terminados: bool = False
) -> Optional[Evento]:
    """Evento activo del bloque horario actual (o el próximo de la jornada)."""
    fecha = momento.date()
    hora = momento.time()
    stmt = (
        select(Evento)
        .where(Evento.fecha == fecha, Evento.activo.is_(True))
        .order_by(Evento.hora_inicio.asc())
    )
    eventos = (await db.scalars(stmt)).all()
    if not eventos:
        return None
    for ev in eventos:
        if ev.hora_inicio <= hora < ev.hora_fin:
            return ev
    if not incluir_terminados:
        futuros = [e for e in eventos if e.hora_fin > hora]
        if futuros:
            return futuros[0]
    return None


# ===========================================================================
# Ciclo de vida
# ===========================================================================
@app.on_event("startup")
async def arrancar() -> None:
    await esperar_base_de_datos()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migración ligera: create_all no altera tablas existentes. La tarifa por
        # hora se añadió después de la primera versión de la app.
        await conn.execute(
            text(
                "ALTER TABLE docentes "
                "ADD COLUMN IF NOT EXISTS tarifa_por_hora FLOAT NOT NULL DEFAULT 0"
            )
        )
    await seed()
    logger.info("Aplicación lista en %s", os.getenv("BASE_URL", "http://localhost:8000"))


@app.on_event("shutdown")
async def apagar() -> None:
    await engine.dispose()


# ===========================================================================
# Salud
# ===========================================================================
@app.get("/api/health", tags=["sistema"])
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "hora": ahora().isoformat(timespec="seconds")}


# ===========================================================================
# Auth
# ===========================================================================
@app.post("/api/auth/login", response_model=schemas.TokenOut, tags=["auth"])
async def login(datos: schemas.LoginIn, db: AsyncSession = Depends(get_db)) -> schemas.TokenOut:
    email = datos.email.lower().strip()
    usuario = await db.scalar(select(Usuario).where(func.lower(Usuario.email) == email))
    if usuario is None or not verify_password(datos.password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos.")
    if not usuario.activo:
        raise HTTPException(status_code=403, detail="Tu cuenta está desactivada. Contacta a la Directora.")
    token = crear_token(usuario)
    respuesta = JSONResponse(
        {
            "access_token": token,
            "token_type": "bearer",
            "usuario": schemas.UsuarioOut.model_validate(usuario).model_dump(),
        }
    )
    respuesta.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=JWT_MINUTOS * 60,
    )
    return respuesta


@app.get("/api/auth/me", response_model=schemas.UsuarioOut, tags=["auth"])
async def me(usuario: Usuario = Depends(get_usuario_actual)) -> schemas.UsuarioOut:
    return schemas.UsuarioOut.model_validate(usuario)


# ===========================================================================
# Dashboard
# ===========================================================================
@app.get("/api/dashboard", response_model=schemas.DashboardOut, tags=["dashboard"])
async def dashboard(
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
) -> schemas.DashboardOut:
    ahora_dt = ahora()
    eventos_hoy = (
        await db.scalars(
            select(Evento)
            .where(Evento.fecha == hoy(), Evento.activo.is_(True))
            .order_by(Evento.hora_inicio.asc())
        )
    ).all()

    activo = None
    proximo = None
    hora = ahora_dt.time()
    for ev in eventos_hoy:
        if ev.hora_inicio <= hora < ev.hora_fin:
            activo = ev
        elif ev.hora_inicio > hora and proximo is None:
            proximo = ev

    total_docentes = (await db.scalar(
        select(func.count()).select_from(Docente).where(Docente.activo.is_(True))
    )) or 0

    asistencias_hoy = (await db.scalar(
        select(func.count())
        .select_from(Asistencia)
        .join(Evento, Asistencia.evento_id == Evento.id)
        .where(Evento.fecha == hoy())
    )) or 0
    puntuales = (await db.scalar(
        select(func.count())
        .select_from(Asistencia)
        .join(Evento, Asistencia.evento_id == Evento.id)
        .where(Evento.fecha == hoy(), Asistencia.estado == ESTADO_PUNTUAL)
    )) or 0
    tardanzas = (await db.scalar(
        select(func.count())
        .select_from(Asistencia)
        .join(Evento, Asistencia.evento_id == Evento.id)
        .where(Evento.fecha == hoy(), Asistencia.estado == ESTADO_TARDANZA)
    )) or 0

    return schemas.DashboardOut(
        usuario=schemas.UsuarioOut.model_validate(usuario),
        fecha=hoy(),
        hora_actual=ahora_dt.time(),
        evento_activo=schemas.EventoOut.model_validate(activo) if activo else None,
        proximo_evento=schemas.EventoOut.model_validate(proximo) if proximo else None,
        eventos_hoy=[schemas.EventoOut.model_validate(e) for e in eventos_hoy],
        total_docentes_activos=total_docentes,
        asistencias_hoy=asistencias_hoy,
        puntuales_hoy=puntuales,
        tardanzas_hoy=tardanzas,
    )


# ===========================================================================
# Docentes
# ===========================================================================
def _docente_out(d: Docente) -> dict:
    return schemas.DocenteOut.model_validate(d).model_dump(mode="json")


@app.get("/api/docentes", response_model=list[schemas.DocenteOut], tags=["docentes"])
async def listar_docentes(
    incluir_inactivos: bool = Query(False),
    q: str = Query("", max_length=80),
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(get_usuario_actual),
) -> list[schemas.DocenteOut]:
    stmt = select(Docente)
    if not incluir_inactivos:
        stmt = stmt.where(Docente.activo.is_(True))
    if q:
        patron = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Docente.nombres).like(patron),
                func.lower(Docente.apellidos).like(patron),
                func.lower(Docente.especialidad).like(patron),
                func.lower(Docente.qr_token).like(patron),
            )
        )
    stmt = stmt.order_by(Docente.apellidos.asc(), Docente.nombres.asc())
    docentes = (await db.scalars(stmt)).all()
    return [schemas.DocenteOut.model_validate(d) for d in docentes]


@app.get(
    "/api/docentes/activos",
    response_model=list[schemas.DocenteScanner],
    tags=["docentes"],
)
async def docentes_para_escaneo(
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(get_usuario_actual),
) -> list[schemas.DocenteScanner]:
    """Precarga en memoria de la pantalla de escaneo (hasta 40 registros)."""
    docentes = (
        await db.scalars(
            select(Docente)
            .where(Docente.activo.is_(True))
            .order_by(Docente.apellidos.asc())
        )
    ).all()
    return [schemas.DocenteScanner.model_validate(d) for d in docentes]


async def _contar_activos(db: AsyncSession, excluir_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(Docente).where(Docente.activo.is_(True))
    if excluir_id is not None:
        stmt = stmt.where(Docente.id != excluir_id)
    return (await db.scalar(stmt)) or 0


async def _generar_credencial(db: AsyncSession, docente: Docente) -> None:
    ruta = await run_in_threadpool(utils_qr.generar_credencial, docente)
    docente.credencial_path = f"/static/credenciales/credencial_{docente.id}.png"
    docente.actualizado_en = ahora()
    await db.commit()


@app.post("/api/docentes", response_model=schemas.DocenteOut, status_code=201, tags=["docentes"])
async def crear_docente(
    datos: schemas.DocenteCreate,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> schemas.DocenteOut:
    activos = await _contar_activos(db)
    if activos >= MAX_DOCENTES_ACTIVOS and datos.activo:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Se alcanzó el límite de {MAX_DOCENTES_ACTIVOS} docentes activos. "
                "Desactiva a alguno antes de registrar uno nuevo."
            ),
        )

    # Token único (reintenta ante la remota posibilidad de colisión)
    token = utils_qr.nuevo_qr_token()
    for _ in range(12):
        existe = await db.scalar(select(Docente.id).where(Docente.qr_token == token))
        if not existe:
            break
        token = utils_qr.nuevo_qr_token()

    docente = Docente(
        nombres=datos.nombres,
        apellidos=datos.apellidos,
        especialidad=datos.especialidad,
        qr_token=token,
        qr_vigencia=utils_qr.vigencia_qr(),
        activo=datos.activo,
        tarifa_por_hora=datos.tarifa_por_hora,
        foto_path="",
        credencial_path="",
        creado_en=ahora(),
    )
    db.add(docente)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="No se pudo crear el docente (token duplicado).")
    await db.refresh(docente)

    # Avatar de reemplazo (en el volumen persistente) + fotocheck PNG
    try:
        docente.foto_path = await run_in_threadpool(utils_qr.asegurar_avatar, docente)
        await db.commit()
        await _generar_credencial(db, docente)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo generar la credencial del docente %s", docente.id)

    await db.refresh(docente)
    return schemas.DocenteOut.model_validate(docente)


@app.get("/api/docentes/{docente_id}", response_model=schemas.DocenteOut, tags=["docentes"])
async def obtener_docente(
    docente_id: int,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(get_usuario_actual),
) -> schemas.DocenteOut:
    docente = await db.get(Docente, docente_id)
    if docente is None:
        raise HTTPException(status_code=404, detail="Docente no encontrado.")
    return schemas.DocenteOut.model_validate(docente)


@app.put("/api/docentes/{docente_id}", response_model=schemas.DocenteOut, tags=["docentes"])
async def editar_docente(
    docente_id: int,
    datos: schemas.DocenteUpdate,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> schemas.DocenteOut:
    docente = await db.get(Docente, docente_id)
    if docente is None:
        raise HTTPException(status_code=404, detail="Docente no encontrado.")

    cambios = datos.model_dump(exclude_unset=True)
    if cambios.get("activo") is True and not docente.activo:
        activos = await _contar_activos(db, excluir_id=docente.id)
        if activos >= MAX_DOCENTES_ACTIVOS:
            raise HTTPException(
                status_code=409,
                detail=f"Ya hay {MAX_DOCENTES_ACTIVOS} docentes activos.",
            )
    # Un PUT que no toca la tarifa no debe ponerla en NULL.
    if "tarifa_por_hora" in cambios and cambios["tarifa_por_hora"] is None:
        cambios.pop("tarifa_por_hora")

    for campo, valor in cambios.items():
        setattr(docente, campo, valor)

    await db.commit()
    await db.refresh(docente)

    # Toda edición regenera la fotocheck
    try:
        await _generar_credencial(db, docente)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo regenerar la credencial %s", docente.id)
    await db.refresh(docente)
    return schemas.DocenteOut.model_validate(docente)


@app.delete("/api/docentes/{docente_id}", tags=["docentes"])
async def desactivar_docente(
    docente_id: int,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> dict:
    docente = await db.get(Docente, docente_id)
    if docente is None:
        raise HTTPException(status_code=404, detail="Docente no encontrado.")
    docente.activo = False
    docente.actualizado_en = ahora()
    await db.commit()
    return {"ok": True, "mensaje": f"{docente.nombre_completo} quedó desactivado."}


@app.delete("/api/docentes/{docente_id}/eliminar", tags=["docentes"])
async def eliminar_docente(
    docente_id: int,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> dict:
    """Eliminación permanente del docente (y de sus asistencias)."""
    docente = await db.get(Docente, docente_id)
    if docente is None:
        raise HTTPException(status_code=404, detail="Docente no encontrado.")

    nombre = docente.nombre_completo
    # Limpieza de archivos en disco (best-effort)
    for rel in (
        docente.foto_path,
        f"/static/uploads/avatares/avatar_{docente.id}.png",
        f"/static/credenciales/credencial_{docente.id}.png",
        f"/static/credenciales/qr_{docente.id}.png",
    ):
        if not rel:
            continue
        try:
            ruta = BASE_DIR / rel.lstrip("/").replace("/", os.sep)
            if ruta.exists():
                ruta.unlink()
        except OSError:
            logger.warning("No se pudo borrar el archivo %s", rel)

    await db.delete(docente)
    await db.commit()
    return {"ok": True, "mensaje": f"{nombre} fue eliminado permanentemente."}


@app.put("/api/docentes/{docente_id}/foto", tags=["docentes"])
async def subir_foto(
    docente_id: int,
    archivo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> dict:
    docente = await db.get(Docente, docente_id)
    if docente is None:
        raise HTTPException(status_code=404, detail="Docente no encontrado.")

    contenido = await archivo.read()
    if len(contenido) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="La imagen no puede superar 5 MB.")
    if not (archivo.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Debes enviar una imagen (JPG/PNG).")

    def _procesar() -> str:
        from io import BytesIO

        from PIL import Image, ImageOps, UnidentifiedImageError

        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except Exception:  # noqa: BLE001 - opcional, no rompe si no está disponible
            pass

        try:
            img = Image.open(BytesIO(contenido))
        except UnidentifiedImageError:
            raise HTTPException(
                status_code=400,
                detail="El archivo no es una imagen válida. Usa JPG o PNG (las fotos HEIC de iPhone también se aceptan).",
            )
        try:
            # Las fotos del celular traen la orientación en EXIF: sin esto quedan
            # giradas al guardarlas y en la credencial.
            img = ImageOps.exif_transpose(img).convert("RGB")
            # Recorte cuadrado centrado + reescalado (máx 900px)
            lado = min(img.size)
            izq = (img.width - lado) // 2
            top = (img.height - lado) // 2
            img = img.crop((izq, top, izq + lado, top + lado))
            if lado > 900:
                img = img.resize((900, 900), Image.LANCZOS)
            destino = utils_qr.UPLOAD_DIR / f"{docente.id}.jpg"
            destino.parent.mkdir(parents=True, exist_ok=True)
            img.save(destino, format="JPEG", quality=88, optimize=True)
            return f"/static/uploads/docentes/{docente.id}.jpg"
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de imagen -> 400 amigable
            logger.warning("No se pudo procesar la imagen del docente %s: %s", docente.id, exc)
            raise HTTPException(
                status_code=400,
                detail="No se pudo procesar la imagen. Asegúrate de enviar una foto válida en JPG o PNG.",
            )

    docente.foto_path = await run_in_threadpool(_procesar)
    await db.commit()
    try:
        await _generar_credencial(db, docente)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo regenerar la credencial tras subir foto")
    return {"ok": True, "foto_path": docente.foto_path}


@app.get("/api/docentes/{docente_id}/credencial", tags=["docentes"])
async def credencial_png(
    docente_id: int,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Fotocheck PNG. Es pública porque también la usa /mi-credencial."""
    docente = await db.get(Docente, docente_id)
    if docente is None:
        raise HTTPException(status_code=404, detail="Docente no encontrado.")

    ruta = utils_qr.CREDENCIALES_DIR / f"credencial_{docente.id}.png"
    if not ruta.exists():
        ruta = await run_in_threadpool(utils_qr.generar_credencial, docente)
    return FileResponse(
        ruta,
        media_type="image/png",
        filename=f"credencial_{docente.qr_token}.png",
        headers={"Cache-Control": "public, max-age=60"},
    )


@app.get("/api/credenciales/zip", tags=["docentes"])
async def descargar_credenciales_zip(
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(get_usuario_actual),
) -> Response:
    """Descarga un .zip con la credencial PNG de todos los docentes activos."""
    docentes = (
        await db.scalars(
            select(Docente).where(Docente.activo.is_(True)).order_by(Docente.id.asc())
        )
    ).all()
    if not docentes:
        raise HTTPException(status_code=404, detail="No hay docentes activos.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archivo:
        for docente in docentes:
            ruta = utils_qr.CREDENCIALES_DIR / f"credencial_{docente.id}.png"
            if not ruta.exists():
                ruta = await run_in_threadpool(utils_qr.generar_credencial, docente)
            archivo.write(ruta, arcname=f"credencial_{docente.qr_token}.png")
    buffer.seek(0)

    nombre = f"credenciales_{ahora().strftime('%Y%m%d')}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename=\"{nombre}\"'},
    )


@app.get("/api/docentes/{docente_id}/qr", tags=["docentes"])
async def qr_png(docente_id: int, db: AsyncSession = Depends(get_db)) -> FileResponse:
    docente = await db.get(Docente, docente_id)
    if docente is None:
        raise HTTPException(status_code=404, detail="Docente no encontrado.")
    ruta = utils_qr.CREDENCIALES_DIR / f"qr_{docente.id}.png"
    if not ruta.exists():
        await run_in_threadpool(
            utils_qr.crear_qr_png,
            utils_qr.url_credencial(docente.qr_token),
            ruta,
        )
    return FileResponse(ruta, media_type="image/png")


# ===========================================================================
# Eventos (programación)
# ===========================================================================
def _evento_out(e: Evento) -> dict:
    return schemas.EventoOut.model_validate(e).model_dump(mode="json")


@app.get("/api/eventos", response_model=list[schemas.EventoOut], tags=["eventos"])
async def listar_eventos(
    fecha: Optional[date] = Query(None),
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    solo_activos: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(get_usuario_actual),
) -> list[schemas.EventoOut]:
    stmt = select(Evento)
    if fecha:
        stmt = stmt.where(Evento.fecha == fecha)
    if desde:
        stmt = stmt.where(Evento.fecha >= desde)
    if hasta:
        stmt = stmt.where(Evento.fecha <= hasta)
    if solo_activos:
        stmt = stmt.where(Evento.activo.is_(True))
    stmt = stmt.order_by(Evento.fecha.asc(), Evento.hora_inicio.asc()).limit(400)
    eventos = (await db.scalars(stmt)).all()
    return [schemas.EventoOut.model_validate(e) for e in eventos]


@app.get("/api/eventos/activo", response_model=Optional[schemas.EventoOut], tags=["eventos"])
async def evento_activo(
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(get_usuario_actual),
) -> Optional[schemas.EventoOut]:
    ev = await evento_en_bloque(db, ahora())
    return schemas.EventoOut.model_validate(ev) if ev else None


@app.post("/api/eventos", response_model=schemas.EventoOut, status_code=201, tags=["eventos"])
async def crear_evento(
    datos: schemas.EventoCreate,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
) -> schemas.EventoOut:
    if usuario.rol != ROL_DIRECTORA:
        if not datos.es_espontaneo:
            raise HTTPException(
                status_code=403,
                detail="Los administrativos solo pueden crear eventos espontáneos.",
            )
        if datos.fecha != hoy():
            raise HTTPException(
                status_code=403,
                detail="Un administrativo solo puede crear eventos espontáneos para hoy.",
            )

    if await hay_solapamiento(db, datos.fecha, datos.hora_inicio, datos.hora_fin):
        raise HTTPException(
            status_code=409,
            detail="Ya existe un evento que se cruza con ese horario en la misma fecha. "
                    "No se permiten eventos paralelos.",
        )

    evento = Evento(
        titulo=datos.titulo,
        fecha=datos.fecha,
        hora_inicio=datos.hora_inicio,
        hora_fin=datos.hora_fin,
        tolerancia_minutos=datos.tolerancia_minutos,
        es_espontaneo=datos.es_espontaneo,
        activo=datos.activo,
        creado_por_id=usuario.id,
        creado_en=ahora(),
    )
    db.add(evento)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="No se pudo crear el evento.")
    await db.refresh(evento)
    return schemas.EventoOut.model_validate(evento)


@app.post(
    "/api/eventos/recurrente",
    response_model=dict,
    status_code=201,
    tags=["eventos"],
)
async def crear_eventos_recurrentes(
    datos: schemas.EventoRecurrente,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(requiere_directora),
) -> dict:
    """Genera una instancia por cada día seleccionado dentro del rango."""
    grupo_id = utils_qr.nuevo_qr_token().replace("DOC-", "SER-")
    creados: list[int] = []
    omitidos = 0

    actual = datos.desde
    while actual <= datos.hasta:
        if actual.weekday() in datos.dias:
            if await hay_solapamiento(db, actual, datos.hora_inicio, datos.hora_fin):
                omitidos += 1
            else:
                evento = Evento(
                    titulo=datos.titulo,
                    fecha=actual,
                    hora_inicio=datos.hora_inicio,
                    hora_fin=datos.hora_fin,
                    tolerancia_minutos=datos.tolerancia_minutos,
                    es_espontaneo=False,
                    activo=True,
                    grupo_id=grupo_id,
                    creado_por_id=usuario.id,
                    creado_en=ahora(),
                )
                db.add(evento)
                creados.append(1)
        actual += timedelta(days=1)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Error al generar la programación.")

    return {
        "ok": True,
        "grupo_id": grupo_id,
        "generados": len(creados),
        "omitidos_por_solape": omitidos,
        "mensaje": (
            f"Se generaron {len(creados)} eventos."
            + (f" {omitidos} fechas omitidas por solaparse con otro horario." if omitidos else "")
        ),
    }


@app.put("/api/eventos/{evento_id}", response_model=schemas.EventoOut, tags=["eventos"])
async def editar_evento(
    evento_id: int,
    datos: schemas.EventoUpdate,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> schemas.EventoOut:
    evento = await db.get(Evento, evento_id)
    if evento is None:
        raise HTTPException(status_code=404, detail="Evento no encontrado.")

    cambios = datos.model_dump(exclude_unset=True)
    nueva_fecha = cambios.get("fecha", evento.fecha)
    nuevo_inicio = cambios.get("hora_inicio", evento.hora_inicio)
    nuevo_fin = cambios.get("hora_fin", evento.hora_fin)
    if nuevo_fin <= nuevo_inicio:
        raise HTTPException(status_code=400, detail="La hora de fin debe ser posterior al inicio.")
    if await hay_solapamiento(
        db, nueva_fecha, nuevo_inicio, nuevo_fin, ignorar_id=evento.id
    ):
        raise HTTPException(status_code=409, detail="Ese horario se cruza con otro evento.")

    for campo, valor in cambios.items():
        setattr(evento, campo, valor)
    await db.commit()
    await db.refresh(evento)
    return schemas.EventoOut.model_validate(evento)


@app.put("/api/eventos/{evento_id}/estado", response_model=schemas.EventoOut, tags=["eventos"])
async def alternar_evento(
    evento_id: int,
    datos: schemas.EventoEstadoIn,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> schemas.EventoOut:
    """Desactivar una fecha concreta = día libre / feriado (se puede reactivar)."""
    evento = await db.get(Evento, evento_id)
    if evento is None:
        raise HTTPException(status_code=404, detail="Evento no encontrado.")
    evento.activo = datos.activo
    await db.commit()
    await db.refresh(evento)
    return schemas.EventoOut.model_validate(evento)


@app.delete("/api/eventos/{evento_id}", tags=["eventos"])
async def eliminar_evento(
    evento_id: int,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> dict:
    evento = await db.get(Evento, evento_id)
    if evento is None:
        raise HTTPException(status_code=404, detail="Evento no encontrado.")
    await db.delete(evento)
    await db.commit()
    return {"ok": True, "mensaje": "Evento eliminado."}


@app.delete("/api/eventos/serie/{grupo_id}", tags=["eventos"])
async def eliminar_serie(
    grupo_id: str,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> dict:
    eventos = (
        await db.scalars(select(Evento).where(Evento.grupo_id == grupo_id))
    ).all()
    if not eventos:
        raise HTTPException(status_code=404, detail="Programación no encontrada.")
    for ev in eventos:
        await db.delete(ev)
    await db.commit()
    return {"ok": True, "mensaje": f"Se eliminaron {len(eventos)} eventos de la serie."}


@app.delete("/api/eventos/fecha/{fecha}", tags=["eventos"])
async def liberar_fecha(
    fecha: date,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> dict:
    """Marca todos los eventos de una fecha como inactivos (feriado / día libre)."""
    eventos = (
        await db.scalars(select(Evento).where(Evento.fecha == fecha, Evento.activo.is_(True)))
    ).all()
    for ev in eventos:
        ev.activo = False
    await db.commit()
    return {
        "ok": True,
        "mensaje": f"Se liberaron {len(eventos)} eventos del {fecha.strftime('%d/%m/%Y')}.",
    }


# ===========================================================================
# Asistencias
# ===========================================================================
def _calcular_llegada(momento: datetime, evento: Evento) -> tuple[time, str, str]:
    """Reglas de negocio de la llegada.

    1. Antes de hora_inicio  -> hora_computable = hora_inicio  · PUNTUAL
    2. Entre inicio e inicio+tolerancia -> hora_computable = real · PUNTUAL
    3. Después de la tolerancia -> hora_computable = real · TARDANZA
    """
    momento = momento.replace(microsecond=0)
    inicio_dt = datetime.combine(momento.date(), evento.hora_inicio)
    limite_dt = inicio_dt + timedelta(minutes=evento.tolerancia_minutos or 0)

    if momento < inicio_dt:
        return evento.hora_inicio, ESTADO_PUNTUAL, "Ingreso anticipado: se computa como hora de inicio."
    if momento <= limite_dt:
        return momento.time(), ESTADO_PUNTUAL, "Dentro de la tolerancia."
    return momento.time(), ESTADO_TARDANZA, (
        f"Superó la tolerancia de {evento.tolerancia_minutos} minutos."
    )


async def _fila_asistencia(a: Asistencia, n: int) -> schemas.AsistenciaOut:
    out = schemas.AsistenciaOut(
        id=a.id,
        evento_id=a.evento_id,
        docente_id=a.docente_id,
        hora_escaneo_real=a.hora_escaneo_real,
        hora_computable=a.hora_computable,
        estado=a.estado,  # type: ignore[arg-type]
        n_llegada=n,
        fecha=a.hora_escaneo_real.date(),
        evento_titulo=a.evento.titulo if a.evento else "",
        docente_nombre=a.docente.nombre_completo if a.docente else "",
        docente_foto=a.docente.foto_path if a.docente else "",
        docente_especialidad=a.docente.especialidad if a.docente else "",
        registrado_por=a.registrado_por.nombre if a.registrado_por else "—",
    )
    return out


@app.post("/api/asistencias/escaneo", response_model=schemas.EscaneoOut, tags=["asistencias"])
async def escanear_qr(
    datos: schemas.EscaneoIn,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
) -> schemas.EscaneoOut:
    token = datos.qr_token.strip()
    # Acepta el token crudo o la URL pública codificada en el QR
    if "token=" in token:
        token = token.split("token=")[-1].split("&")[0].strip()
    token = token.upper()

    docente = await db.scalar(select(Docente).where(Docente.qr_token == token))
    if docente is None or not docente.activo:
        raise HTTPException(
            status_code=404,
            detail="Código QR no reconocido o docente inactivo. Verifica con Dirección.",
        )

    momento = ahora()
    if datos.evento_id:
        evento = await db.get(Evento, datos.evento_id)
        if evento is None or not evento.activo:
            raise HTTPException(status_code=404, detail="El evento seleccionado ya no está disponible.")
    else:
        evento = await evento_en_bloque(db, momento)
        if evento is None:
            raise HTTPException(
                status_code=400,
                detail="No hay un bloque horario activo en este momento.",
            )

    # Anti-duplicado por (evento, docente)
    existente = await db.scalar(
        select(Asistencia).where(
            Asistencia.evento_id == evento.id,
            Asistencia.docente_id == docente.id,
        )
    )
    if existente is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{docente.nombre_completo} ya registró su ingreso a las "
                f"{existente.hora_escaneo_real.strftime('%H:%M:%S')} "
                f"({existente.estado.lower()})."
            ),
        )

    computable, estado, detalle = _calcular_llegada(momento, evento)
    asistencia = Asistencia(
        evento_id=evento.id,
        docente_id=docente.id,
        registrado_por_id=usuario.id,
        hora_escaneo_real=momento,
        hora_computable=computable,
        estado=estado,
        detalle=detalle,
        creado_en=ahora(),
    )
    db.add(asistencia)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Carrera entre dos escaneos simultáneos: volvemos a consultar
        existente = await db.scalar(
            select(Asistencia).where(
                Asistencia.evento_id == evento.id,
                Asistencia.docente_id == docente.id,
            )
        )
        if existente is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{docente.nombre_completo} ya registró su ingreso a las "
                    f"{existente.hora_escaneo_real.strftime('%H:%M:%S')}."
                ),
            )
        raise HTTPException(status_code=500, detail="No se pudo registrar la asistencia.")

    # Recarga explícita con sus relaciones (evita lazy-load en contexto async)
    asistencia = await db.scalar(
        select(Asistencia)
        .options(
            joinedload(Asistencia.docente),
            joinedload(Asistencia.evento),
            joinedload(Asistencia.registrado_por),
        )
        .where(Asistencia.id == asistencia.id)
    )
    n = await _contar_llegadas(db, evento.id, asistencia.hora_escaneo_real)

    mensaje = (
        "Registro PUNTUAL"
        if estado == ESTADO_PUNTUAL
        else f"Registro con TARDANZA ({detalle})"
    )
    return schemas.EscaneoOut(
        ok=True,
        mensaje=mensaje,
        asistencia=await _fila_asistencia(asistencia, n),
        docente=schemas.DocenteOut.model_validate(docente),
        evento=schemas.EventoOut.model_validate(evento),
    )


async def _contar_llegadas(db: AsyncSession, evento_id: int, hasta: datetime) -> int:
    total = (
        await db.scalar(
            select(func.count())
            .select_from(Asistencia)
            .where(
                Asistencia.evento_id == evento_id,
                Asistencia.hora_escaneo_real <= hasta,
            )
        )
    ) or 0
    return total


async def _consultar_asistencias(
    db: AsyncSession,
    desde: date,
    hasta: date,
    docente_id: int | None,
    evento_id: int | None,
) -> list[Asistencia]:
    stmt = (
        select(Asistencia)
        .join(Evento, Asistencia.evento_id == Evento.id)
        .where(Evento.fecha >= desde, Evento.fecha <= hasta)
        .order_by(Asistencia.hora_escaneo_real.asc(), Asistencia.id.asc())
    )
    if docente_id:
        stmt = stmt.where(Asistencia.docente_id == docente_id)
    if evento_id:
        stmt = stmt.where(Asistencia.evento_id == evento_id)
    return list((await db.scalars(stmt)).all())


@app.get("/api/asistencias", response_model=list[schemas.AsistenciaOut], tags=["asistencias"])
async def listar_asistencias(
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    docente_id: Optional[int] = Query(None),
    evento_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
) -> list[schemas.AsistenciaOut]:
    fecha_desde = desde or hoy()
    fecha_hasta = hasta or hoy()
    # El administrativo solo consulta el día en curso
    if usuario.rol != ROL_DIRECTORA:
        fecha_desde = fecha_hasta = hoy()

    registros = await _consultar_asistencias(
        db, fecha_desde, fecha_hasta, docente_id, evento_id
    )
    return [await _fila_asistencia(a, i + 1) for i, a in enumerate(registros)]


@app.get("/api/asistencias/export", tags=["asistencias"])
async def exportar_excel(
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    docente_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
) -> Response:
    fecha_desde = desde or hoy()
    fecha_hasta = hasta or hoy()
    if usuario.rol != ROL_DIRECTORA:
        fecha_desde = fecha_hasta = hoy()

    registros = await _consultar_asistencias(db, fecha_desde, fecha_hasta, docente_id, None)

    filas = []
    for i, a in enumerate(registros):
        filas.append(
            {
                "n_llegada": i + 1,
                "fecha": a.hora_escaneo_real.date(),
                "evento": a.evento.titulo if a.evento else "",
                "docente": a.docente.nombre_completo if a.docente else "",
                "hora_real": a.hora_escaneo_real,
                "hora_computable": a.hora_computable,
                "estado": a.estado,
                "registrado_por": a.registrado_por.nombre if a.registrado_por else "—",
            }
        )

    periodo = (
        "Todo el histórico"
        if (desde or hasta)
        else f"Día {fecha_desde.strftime('%d/%m/%Y')}"
    )
    contenido = await run_in_threadpool(
        utils_excel.exportar_asistencias, filas, "Asistencias docentes", periodo
    )
    nombre = f"asistencias_{fecha_desde.strftime('%Y%m%d')}"
    if fecha_hasta != fecha_desde:
        nombre += f"_a_{fecha_hasta.strftime('%Y%m%d')}"
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}.xlsx"',
            "Content-Length": str(len(contenido)),
        },
    )


@app.delete("/api/asistencias/{asistencia_id}", tags=["asistencias"])
async def eliminar_asistencia(
    asistencia_id: int,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> dict:
    asistencia = await db.get(Asistencia, asistencia_id)
    if asistencia is None:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")
    await db.delete(asistencia)
    await db.commit()
    return {"ok": True, "mensaje": "Registro eliminado."}


# ===========================================================================
# Pagos de docentes
# ===========================================================================
async def _calcular_pagos(
    db: AsyncSession,
    fecha_desde: date,
    fecha_hasta: date,
    docente_id: int | None = None,
) -> dict:
    """Calcula el detalle y el resumen de pagos del rango indicado."""
    stmt = (
        select(Asistencia)
        .join(Evento, Asistencia.evento_id == Evento.id)
        .where(Evento.fecha >= fecha_desde, Evento.fecha <= fecha_hasta)
        .order_by(
            Evento.fecha.asc(),
            Evento.hora_inicio.asc(),
            Asistencia.hora_escaneo_real.asc(),
        )
    )
    if docente_id:
        stmt = stmt.where(Asistencia.docente_id == docente_id)
    asistencias = (await db.scalars(stmt)).all()

    detalle: list[dict] = []
    resumen: dict[int, dict] = {}

    for a in asistencias:
        evento = a.evento
        docente = a.docente
        if evento is None or docente is None:
            continue

        pago = pagos_calc.calcular_pago(docente.tarifa_por_hora, evento, a.hora_escaneo_real)
        porcentaje = float(pago.descuento_porcentaje)

        detalle.append(
            {
                "asistencia_id": a.id,
                "fecha": evento.fecha,
                "evento_id": evento.id,
                "evento_titulo": evento.titulo,
                "hora_inicio": evento.hora_inicio,
                "hora_fin": evento.hora_fin,
                "duracion_minutos": pago.duracion_minutos,
                "tolerancia_minutos": evento.tolerancia_minutos or 0,
                "hora_escaneo_real": a.hora_escaneo_real,
                "estado": a.estado,
                "minutos_tarde": pago.minutos_tarde,
                "tarifa_por_hora": float(docente.tarifa_por_hora or 0),
                "bruto": float(pago.bruto),
                "descuento": float(pago.descuento),
                "descuento_porcentaje": porcentaje,
                "neto": float(pago.neto),
                "docente_id": docente.id,
                "docente_nombre": docente.nombre_completo,
                "docente_foto": docente.foto_path,
            }
        )

        r = resumen.setdefault(
            docente.id,
            {
                "docente_id": docente.id,
                "nombre": docente.nombre_completo,
                "especialidad": docente.especialidad,
                "foto_path": docente.foto_path,
                "tarifa_por_hora": float(docente.tarifa_por_hora or 0),
                "asistencias": 0,
                "puntuales": 0,
                "tardanzas": 0,
                "minutos_tarde": 0,
                "bruto": Decimal("0"),
                "descuento": Decimal("0"),
                "neto": Decimal("0"),
            },
        )
        r["asistencias"] += 1
        if a.estado == ESTADO_PUNTUAL:
            r["puntuales"] += 1
        else:
            r["tardanzas"] += 1
        r["minutos_tarde"] += pago.minutos_tarde
        r["bruto"] += pago.bruto
        r["descuento"] += pago.descuento
        r["neto"] += pago.neto

    filas_resumen: list[dict] = []
    for r in resumen.values():
        bruto = r["bruto"]
        descuento = r["descuento"]
        r["bruto"] = float(bruto)
        r["descuento"] = float(descuento)
        r["neto"] = float(r["neto"])
        r["descuento_porcentaje"] = (
            round(float(descuento / bruto * Decimal(100)), 2) if bruto > 0 else 0.0
        )
        filas_resumen.append(r)

    filas_resumen.sort(key=lambda x: x["nombre"].lower())

    total_bruto = sum((Decimal(str(x["bruto"])) for x in filas_resumen), Decimal("0"))
    total_descuento = sum((Decimal(str(x["descuento"])) for x in filas_resumen), Decimal("0"))
    total_neto = sum((Decimal(str(x["neto"])) for x in filas_resumen), Decimal("0"))

    totales = {
        "asistencias": len(detalle),
        "docentes": len(filas_resumen),
        "docentes_sin_tarifa": sum(1 for x in filas_resumen if x["tarifa_por_hora"] <= 0),
        "bruto": float(total_bruto),
        "descuento": float(total_descuento),
        "neto": float(total_neto),
    }

    return {"detalle": detalle, "resumen": filas_resumen, "totales": totales}


def _rango_de_pagos(desde: Optional[date], hasta: Optional[date]) -> tuple[date, date]:
    fecha_desde = desde or hoy().replace(day=1)
    fecha_hasta = hasta or hoy()
    if fecha_desde > fecha_hasta:
        raise HTTPException(
            status_code=400,
            detail="La fecha inicial no puede ser posterior a la fecha final.",
        )
    return fecha_desde, fecha_hasta


@app.get("/api/pagos", response_model=schemas.PagoOut, tags=["pagos"])
async def obtener_pagos(
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    docente_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> schemas.PagoOut:
    """Pago de docentes según asistencia, tarifa por hora y tardanza.

    Por defecto calcula el mes en curso. Solo la Dirección puede consultarlo.
    """
    fecha_desde, fecha_hasta = _rango_de_pagos(desde, hasta)
    datos = await _calcular_pagos(db, fecha_desde, fecha_hasta, docente_id)
    return schemas.PagoOut(
        desde=fecha_desde,
        hasta=fecha_hasta,
        totales=schemas.PagoTotalesOut(**datos["totales"]),
        resumen=[schemas.PagoDocenteOut(**f) for f in datos["resumen"]],
        detalle=[schemas.PagoDetalleOut(**f) for f in datos["detalle"]],
    )


@app.get("/api/pagos/export", tags=["pagos"])
async def exportar_pagos_excel(
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    docente_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> Response:
    """Exporta el cálculo de pagos a un .xlsx con resumen por docente y detalle."""
    fecha_desde, fecha_hasta = _rango_de_pagos(desde, hasta)
    datos = await _calcular_pagos(db, fecha_desde, fecha_hasta, docente_id)

    periodo = f"{fecha_desde.strftime('%d/%m/%Y')} al {fecha_hasta.strftime('%d/%m/%Y')}"
    contenido = await run_in_threadpool(
        utils_excel.exportar_pagos, datos["resumen"], datos["detalle"], periodo
    )
    nombre = f"pagos_{fecha_desde.strftime('%Y%m%d')}"
    if fecha_hasta != fecha_desde:
        nombre += f"_a_{fecha_hasta.strftime('%Y%m%d')}"
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}.xlsx"',
            "Content-Length": str(len(contenido)),
        },
    )


# ===========================================================================
# Usuarios (solo Directora)
# ===========================================================================
@app.get("/api/usuarios", response_model=list[schemas.UsuarioOut], tags=["usuarios"])
async def listar_usuarios(
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> list[schemas.UsuarioOut]:
    usuarios = (await db.scalars(select(Usuario).order_by(Usuario.id.asc()))).all()
    return [schemas.UsuarioOut.model_validate(u) for u in usuarios]


@app.post("/api/usuarios", response_model=schemas.UsuarioOut, status_code=201, tags=["usuarios"])
async def crear_usuario(
    datos: schemas.UsuarioCreate,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> schemas.UsuarioOut:
    email = datos.email.lower().strip()
    existe = await db.scalar(select(Usuario).where(func.lower(Usuario.email) == email))
    if existe:
        raise HTTPException(status_code=409, detail="Ese email ya está registrado.")
    usuario = Usuario(
        nombre=datos.nombre.strip(),
        email=email,
        password_hash=hash_password(datos.password),
        # La única cuenta con acceso total es la de Dirección: las demás son
        # siempre Secretaría/Administrativo.
        rol=ROL_ADMIN,
        activo=datos.activo,
        creado_en=ahora(),
    )
    db.add(usuario)
    await db.commit()
    await db.refresh(usuario)
    return schemas.UsuarioOut.model_validate(usuario)


@app.put("/api/usuarios/{usuario_id}", response_model=schemas.UsuarioOut, tags=["usuarios"])
async def editar_usuario(
    usuario_id: int,
    datos: schemas.UsuarioUpdate,
    db: AsyncSession = Depends(get_db),
    _u: Usuario = Depends(requiere_directora),
) -> schemas.UsuarioOut:
    usuario = await db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    cambios = datos.model_dump(exclude_unset=True)
    # El rol no se edita: solo la cuenta de Dirección tiene acceso total y las
    # demás son siempre Secretaría/Administrativo.
    cambios.pop("rol", None)
    if usuario.rol == ROL_DIRECTORA and cambios.get("activo") is False:
        raise HTTPException(
            status_code=409,
            detail="La cuenta de Dirección no se puede desactivar.",
        )
    if "email" in cambios and cambios["email"]:
        email = str(cambios["email"]).lower().strip()
        otro = await db.scalar(
            select(Usuario).where(func.lower(Usuario.email) == email, Usuario.id != usuario_id)
        )
        if otro:
            raise HTTPException(status_code=409, detail="Ese email ya está registrado.")
        cambios["email"] = email
    password = cambios.pop("password", None)
    for campo, valor in cambios.items():
        setattr(usuario, campo, valor)
    if password:
        usuario.password_hash = hash_password(password)
    await db.commit()
    await db.refresh(usuario)
    return schemas.UsuarioOut.model_validate(usuario)


@app.delete("/api/usuarios/{usuario_id}", tags=["usuarios"])
async def desactivar_usuario(
    usuario_id: int,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(requiere_directora),
) -> dict:
    objetivo = await db.get(Usuario, usuario_id)
    if objetivo is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if objetivo.rol == ROL_DIRECTORA:
        raise HTTPException(
            status_code=409,
            detail="La cuenta de Dirección no se puede eliminar ni desactivar.",
        )
    if objetivo.id == usuario.id:
        raise HTTPException(status_code=400, detail="No puedes desactivar tu propia cuenta.")
    objetivo.activo = False
    await db.commit()
    return {"ok": True, "mensaje": f"{objetivo.nombre} quedó desactivado."}


@app.delete("/api/usuarios/{usuario_id}/eliminar", tags=["usuarios"])
async def eliminar_usuario(
    usuario_id: int,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(requiere_directora),
) -> dict:
    """Eliminación permanente de una cuenta (solo Secretaría/Administrativo)."""
    objetivo = await db.get(Usuario, usuario_id)
    if objetivo is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if objetivo.rol == ROL_DIRECTORA:
        raise HTTPException(
            status_code=409,
            detail="La cuenta de Dirección no se puede eliminar.",
        )
    if objetivo.id == usuario.id:
        raise HTTPException(status_code=400, detail="No puedes eliminar tu propia cuenta.")
    nombre = objetivo.nombre
    await db.delete(objetivo)
    await db.commit()
    return {"ok": True, "mensaje": f"{nombre} fue eliminado permanentemente."}


# ===========================================================================
# Páginas
# ===========================================================================
@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def pagina_login(request: Request) -> HTMLResponse:
    respuesta = templates.TemplateResponse(
        "login.html", {"request": request, "title": "Iniciar sesión"}
    )
    respuesta.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    respuesta.headers["Pragma"] = "no-cache"
    respuesta.headers["Expires"] = "0"
    return respuesta


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def pagina_principal(request: Request) -> HTMLResponse:
    respuesta = templates.TemplateResponse(
        "index.html", {"request": request, "title": "Panel"}
    )
    respuesta.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    respuesta.headers["Pragma"] = "no-cache"
    respuesta.headers["Expires"] = "0"
    return respuesta


@app.get("/mi-credencial", response_class=HTMLResponse, include_in_schema=False)
async def pagina_credencial(
    request: Request,
    token: str = Query("", max_length=40),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Vista pública de consulta de credencial (la usan los docentes)."""
    docente = None
    if token:
        docente = await db.scalar(
            select(Docente).where(func.upper(Docente.qr_token) == token.strip().upper())
        )
    respuesta = templates.TemplateResponse(
        "credencial.html",
        {
            "request": request,
            "title": "Mi credencial",
            "token": token,
            "docente": docente,
            "encontrado": docente is not None,
        },
    )
    respuesta.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    respuesta.headers["Pragma"] = "no-cache"
    respuesta.headers["Expires"] = "0"
    return respuesta


@app.get("/manifest.json", include_in_schema=False)
async def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/logout", include_in_schema=False)
async def logout() -> RedirectResponse:
    respuesta = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    respuesta.delete_cookie(COOKIE_NAME)
    return respuesta


# ===========================================================================
# Manejadores de error
# ===========================================================================
@app.exception_handler(HTTPException)
async def manejar_http(request: Request, exc: HTTPException) -> JSONResponse:
    acepta_html = "text/html" in request.headers.get("accept", "")
    if exc.status_code == 401 and acepta_html and not request.url.path.startswith("/api"):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )
