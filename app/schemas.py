"""Esquemas Pydantic v2 (validación de entrada/salida de la API)."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

Rol = Literal["DIRECTORA", "ADMIN"]
Estado = Literal["PUNTUAL", "TARDANZA"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=4, max_length=128)


class UsuarioOut(ORMModel):
    id: int
    nombre: str
    email: str
    rol: Rol
    activo: bool


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario: UsuarioOut


class UsuarioCreate(BaseModel):
    nombre: str = Field(min_length=3, max_length=120)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    rol: Rol = "ADMIN"
    activo: bool = True


class UsuarioUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, min_length=3, max_length=120)
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)
    rol: Optional[Rol] = None
    activo: Optional[bool] = None


# ---------------------------------------------------------------------------
# Docentes
# ---------------------------------------------------------------------------
class DocenteCreate(BaseModel):
    nombres: str = Field(min_length=2, max_length=120)
    apellidos: str = Field(min_length=2, max_length=120)
    especialidad: str = Field(default="", max_length=140)
    tarifa_por_hora: float = Field(default=0.0, ge=0, le=100000)
    activo: bool = True

    @field_validator("nombres", "apellidos")
    @classmethod
    def _sin_espacios_extremos(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("El campo no puede estar vacío")
        return v


class DocenteUpdate(BaseModel):
    nombres: Optional[str] = Field(default=None, min_length=2, max_length=120)
    apellidos: Optional[str] = Field(default=None, min_length=2, max_length=120)
    especialidad: Optional[str] = Field(default=None, max_length=140)
    tarifa_por_hora: Optional[float] = Field(default=None, ge=0, le=100000)
    activo: Optional[bool] = None

    @field_validator("nombres", "apellidos")
    @classmethod
    def _sin_espacios_extremos(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = " ".join(v.split())
        if not v:
            raise ValueError("El campo no puede estar vacío")
        return v


class DocenteOut(ORMModel):
    id: int
    nombres: str
    apellidos: str
    especialidad: str
    foto_path: str
    qr_token: str
    qr_vigencia: date
    activo: bool
    tarifa_por_hora: float
    nombre_completo: str
    iniciales: str


class DocenteScanner(ORMModel):
    """Versión ligera que se precarga en memoria en la pantalla de escaneo."""

    id: int
    nombre_completo: str
    iniciales: str
    especialidad: str
    foto_path: str
    qr_token: str
    activo: bool


# ---------------------------------------------------------------------------
# Eventos
# ---------------------------------------------------------------------------
class EventoCreate(BaseModel):
    titulo: str = Field(min_length=3, max_length=160)
    fecha: date
    hora_inicio: time
    hora_fin: time
    tolerancia_minutos: int = Field(default=5, ge=0, le=120)
    es_espontaneo: bool = False
    activo: bool = True

    @model_validator(mode="after")
    def _orden_correcto(self):
        if self.hora_fin <= self.hora_inicio:
            raise ValueError("La hora de fin debe ser posterior a la hora de inicio")
        return self


class EventoRecurrente(BaseModel):
    """Generación estilo Google Calendar: días de la semana + rango de fechas."""

    titulo: str = Field(min_length=3, max_length=160)
    dias: list[int] = Field(min_length=1, max_length=7, description="0=Lunes .. 6=Domingo")
    hora_inicio: time
    hora_fin: time
    tolerancia_minutos: int = Field(default=5, ge=0, le=120)
    desde: date
    hasta: date

    @field_validator("dias")
    @classmethod
    def _dias_validos(cls, v: list[int]) -> list[int]:
        limpio = sorted({d for d in v if 0 <= d <= 6})
        if not limpio:
            raise ValueError("Selecciona al menos un día de la semana")
        return limpio

    @model_validator(mode="after")
    def _rangos(self):
        if self.hora_fin <= self.hora_inicio:
            raise ValueError("La hora de fin debe ser posterior a la hora de inicio")
        if self.hasta < self.desde:
            raise ValueError("La fecha final debe ser posterior a la inicial")
        dias_entre = (self.hasta - self.desde).days
        if dias_entre > 800:
            raise ValueError("El rango máximo de generación es de 800 días")
        return self


class EventoUpdate(BaseModel):
    titulo: Optional[str] = Field(default=None, min_length=3, max_length=160)
    fecha: Optional[date] = None
    hora_inicio: Optional[time] = None
    hora_fin: Optional[time] = None
    tolerancia_minutos: Optional[int] = Field(default=None, ge=0, le=120)
    activo: Optional[bool] = None


class EventoOut(ORMModel):
    id: int
    titulo: str
    fecha: date
    hora_inicio: time
    hora_fin: time
    tolerancia_minutos: int
    es_espontaneo: bool
    activo: bool
    grupo_id: Optional[str] = None


class EventoEstadoIn(BaseModel):
    activo: bool


# ---------------------------------------------------------------------------
# Asistencias
# ---------------------------------------------------------------------------
class EscaneoIn(BaseModel):
    # El escáner envía el token crudo ("DOC-XXXXXXXX"), pero también aceptamos la
    # URL pública completa que va codificada en el QR (.../mi-credencial?token=...),
    # por eso el límite es holgado.
    qr_token: str = Field(min_length=4, max_length=512)
    evento_id: Optional[int] = None


class AsistenciaOut(ORMModel):
    id: int
    evento_id: int
    docente_id: int
    hora_escaneo_real: datetime
    hora_computable: time
    estado: Estado
    n_llegada: int = 0
    fecha: date
    evento_titulo: str = ""
    docente_nombre: str = ""
    docente_foto: str = ""
    docente_especialidad: str = ""
    registrado_por: str = ""


class EscaneoOut(BaseModel):
    ok: bool = True
    mensaje: str
    asistencia: AsistenciaOut
    docente: DocenteOut
    evento: EventoOut


# ---------------------------------------------------------------------------
# Dashboard / reportes
# ---------------------------------------------------------------------------
class DashboardOut(BaseModel):
    usuario: UsuarioOut
    fecha: date
    hora_actual: time
    evento_activo: Optional[EventoOut] = None
    proximo_evento: Optional[EventoOut] = None
    eventos_hoy: list[EventoOut] = []
    total_docentes_activos: int = 0
    asistencias_hoy: int = 0
    puntuales_hoy: int = 0
    tardanzas_hoy: int = 0


# ---------------------------------------------------------------------------
# Pagos
# ---------------------------------------------------------------------------
class PagoTotalesOut(BaseModel):
    asistencias: int = 0
    docentes: int = 0
    docentes_sin_tarifa: int = 0
    bruto: float = 0.0
    descuento: float = 0.0
    neto: float = 0.0
    pagado: float = 0.0
    pendiente: float = 0.0


class PagoDocenteOut(BaseModel):
    docente_id: int
    nombre: str
    especialidad: str = ""
    foto_path: str = ""
    tarifa_por_hora: float = 0.0
    asistencias: int = 0
    puntuales: int = 0
    tardanzas: int = 0
    minutos_tarde: int = 0
    bruto: float = 0.0
    descuento: float = 0.0
    descuento_porcentaje: float = 0.0
    neto: float = 0.0
    pagado: float = 0.0
    pendiente: float = 0.0
    todo_pagado: bool = False
    fecha_pago: datetime | None = None


class PagoDetalleOut(BaseModel):
    asistencia_id: int
    fecha: date
    evento_id: int
    evento_titulo: str
    hora_inicio: time
    hora_fin: time
    duracion_minutos: int
    tolerancia_minutos: int
    hora_escaneo_real: datetime
    estado: Estado
    minutos_tarde: int
    tarifa_por_hora: float
    bruto: float
    descuento: float
    descuento_porcentaje: float
    neto: float
    docente_id: int
    docente_nombre: str
    docente_foto: str = ""
    pagado: bool = False
    pago_id: int | None = None
    fecha_pago: datetime | None = None


class PagoRegistroOut(BaseModel):
    id: int
    docente_id: int
    docente_nombre: str = ""
    periodo_desde: date
    periodo_hasta: date
    asistencias: int = 0
    bruto: float = 0.0
    descuento: float = 0.0
    neto: float = 0.0
    fecha_pago: datetime
    nota: str = ""


class PagoIn(BaseModel):
    docente_id: int = Field(gt=0)
    desde: date
    hasta: date

    @model_validator(mode="after")
    def _rango_valido(self) -> "PagoIn":
        if self.desde > self.hasta:
            raise ValueError("La fecha inicial no puede ser posterior a la fecha final.")
        return self


class PagoOut(BaseModel):
    desde: date
    hasta: date
    totales: PagoTotalesOut
    resumen: list[PagoDocenteOut] = []
    detalle: list[PagoDetalleOut] = []
    pagos: list[PagoRegistroOut] = []
