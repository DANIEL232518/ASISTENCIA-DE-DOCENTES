"""Modelos ORM: usuarios, docentes, eventos y asistencias."""

from __future__ import annotations

import os
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# ---------------------------------------------------------------------------
# Constantes de dominio
# ---------------------------------------------------------------------------
ROL_DIRECTORA = "DIRECTORA"
ROL_ADMIN = "ADMIN"
ROLES = (ROL_DIRECTORA, ROL_ADMIN)

ESTADO_PUNTUAL = "PUNTUAL"
ESTADO_TARDANZA = "TARDANZA"
ESTADOS = (ESTADO_PUNTUAL, ESTADO_TARDANZA)

MAX_DOCENTES_ACTIVOS = int(os.getenv("MAX_DOCENTES", "40"))


class Usuario(Base):
    """Cuenta de acceso (Directora o Administrativo)."""

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    rol: Mapped[str] = mapped_column(String(20), nullable=False, default=ROL_ADMIN, index=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    asistencias_registradas: Mapped[list["Asistencia"]] = relationship(
        back_populates="registrado_por"
    )

    @property
    def es_directora(self) -> bool:
        return self.rol == ROL_DIRECTORA

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Usuario {self.email} ({self.rol})>"


class Docente(Base):
    """Docente de la academia. Máximo 40 activos."""

    __tablename__ = "docentes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombres: Mapped[str] = mapped_column(String(120), nullable=False)
    apellidos: Mapped[str] = mapped_column(String(120), nullable=False)
    especialidad: Mapped[str] = mapped_column(String(140), nullable=False, default="")
    foto_path: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    qr_token: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, nullable=False
    )
    qr_vigencia: Mapped[date] = mapped_column(Date, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    credencial_path: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Tarifa que la academia paga a este docente por cada hora de clase.
    # Se usa en el apartado "Pagos" para calcular el descuento por tardanza.
    tarifa_por_hora: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    creado_en: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    actualizado_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    asistencias: Mapped[list["Asistencia"]] = relationship(
        back_populates="docente", cascade="all, delete-orphan"
    )

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombres} {self.apellidos}".strip()

    @property
    def iniciales(self) -> str:
        n = (self.nombres[:1] or "").upper()
        a = (self.apellidos[:1] or "").upper()
        return (n + a) or "D"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Docente #{self.id} {self.nombre_completo} [{self.qr_token}]>"


class Evento(Base):
    """Bloque horario general de ingreso (nunca hay eventos paralelos)."""

    __tablename__ = "eventos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    titulo: Mapped[str] = mapped_column(String(160), nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    hora_inicio: Mapped[time] = mapped_column(Time, nullable=False)
    hora_fin: Mapped[time] = mapped_column(Time, nullable=False)
    tolerancia_minutos: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    es_espontaneo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    grupo_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    creado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    creado_en: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    asistencias: Mapped[list["Asistencia"]] = relationship(
        back_populates="evento", cascade="all, delete-orphan"
    )

    @property
    def es_libre(self) -> bool:
        """Un evento desactivado funciona como día libre / feriado."""
        return not self.activo

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Evento #{self.id} {self.titulo} {self.fecha} {self.hora_inicio}-{self.hora_fin}>"


class Asistencia(Base):
    """Registro de ingreso de un docente en un evento (único por docente+evento)."""

    __tablename__ = "asistencias"
    __table_args__ = (
        UniqueConstraint("evento_id", "docente_id", name="uq_asistencia_evento_docente"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evento_id: Mapped[int] = mapped_column(
        ForeignKey("eventos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    docente_id: Mapped[int] = mapped_column(
        ForeignKey("docentes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    registrado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    hora_escaneo_real: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    hora_computable: Mapped[time] = mapped_column(Time, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default=ESTADO_PUNTUAL)
    detalle: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    creado_en: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    evento: Mapped[Evento] = relationship(back_populates="asistencias", lazy="joined")
    docente: Mapped[Docente] = relationship(back_populates="asistencias", lazy="joined")
    registrado_por: Mapped[Usuario | None] = relationship(
        back_populates="asistencias_registradas", lazy="joined"
    )

    @property
    def fecha(self) -> date:
        return self.hora_escaneo_real.date()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asistencia #{self.id} docente={self.docente_id} {self.estado}>"
