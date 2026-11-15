"""Autenticación: hash bcrypt + JWT (PyJWT) y dependencias de rol."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import ROL_DIRECTORA, Usuario

JWT_SECRET = os.getenv("JWT_SECRET", "clave-secreta-cambiar-en-produccion")
JWT_ALGO = "HS256"
JWT_MINUTOS = int(os.getenv("JWT_MINUTOS", "720"))
COOKIE_NAME = "asistencia_token"

# bcrypt trunca a 72 bytes: recortamos la contraseña codificada en UTF-8.
_MAX_PW = 72


def _clave_bcrypt(password: str) -> bytes:
    return password.encode("utf-8")[:_MAX_PW]


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(_clave_bcrypt(password), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_clave_bcrypt(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def crear_token(usuario: Usuario) -> str:
    payload = {
        "sub": str(usuario.id),
        "email": usuario.email,
        "rol": usuario.rol,
        "nombre": usuario.nombre,
        "exp": datetime.utcnow() + timedelta(minutes=JWT_MINUTOS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decodificar_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        return None


def _token_de_peticion(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if request.cookies.get(COOKIE_NAME):
        return request.cookies.get(COOKIE_NAME)
    # Permite enlaces con ?access_token= (imágenes, descargas directas)
    qp = request.query_params.get("access_token")
    if qp:
        return qp
    return None


async def get_usuario_actual(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Usuario:
    token = _token_de_peticion(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión no encontrada. Inicia sesión nuevamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    datos = decodificar_token(token)
    if not datos:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tu sesión expiró. Inicia sesión nuevamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    usuario = await db.get(Usuario, int(datos["sub"]))
    if usuario is None or not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La cuenta está desactivada.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return usuario


async def get_usuario_opcional(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Optional[Usuario]:
    """Para páginas públicas que solo decoran con el usuario si existe sesión."""
    try:
        return await get_usuario_actual(request, db)
    except HTTPException:
        return None


async def requiere_directora(usuario: Usuario = Depends(get_usuario_actual)) -> Usuario:
    if usuario.rol != ROL_DIRECTORA:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta acción está reservada a la Directora.",
        )
    return usuario
