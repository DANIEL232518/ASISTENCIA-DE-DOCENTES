"""Cálculo del pago de docentes según su asistencia y tarifa por hora.

Regla de negocio
----------------
* El pago base de un evento es ``tarifa_por_hora × duración del evento``.
* Si el docente llega dentro de la tolerancia (o antes), no hay descuento.
* Si supera la tolerancia, se descuenta ``tarifa_por_hora × minutos_tarde / 60``,
  es decir, la parte proporcional de la hora que no cumplió. El descuento nunca
  puede ser mayor que el pago base del evento.

Ejemplo: tarifa S/ 12/h, evento de 1 hora y 20 minutos de tardanza
→ descuento = 12 × 20/60 = S/ 4.00 (1/3 de la hora).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from math import ceil
from typing import Any

_CENTAVO = Decimal("0.01")
_MINUTO = Decimal(60)


def _dinero(valor: Decimal) -> Decimal:
    """Redondea a 2 decimales (soles) con criterio comercial."""
    return valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def duracion_minutos(evento: Any) -> int:
    """Minutos que dura el bloque horario (el fin siempre es posterior al inicio)."""
    inicio = evento.hora_inicio.hour * 60 + evento.hora_inicio.minute
    fin = evento.hora_fin.hour * 60 + evento.hora_fin.minute
    return max(0, fin - inicio)


def minutos_de_tardanza(hora_real: datetime, evento: Any) -> int:
    """Minutos completos transcurridos desde la hora de inicio del evento.

    Si el docente llegó antes o justo a la hora, devuelve 0. Se redondea hacia
    arriba: cualquier segundo adicional ya cuenta como un minuto completo.
    """
    inicio = datetime.combine(evento.fecha, evento.hora_inicio)
    segundos = (hora_real - inicio).total_seconds()
    if segundos <= 0:
        return 0
    return int(ceil(segundos / 60))


@dataclass(frozen=True)
class PagoCalculado:
    duracion_minutos: int
    minutos_tarde: int
    bruto: Decimal
    descuento: Decimal
    neto: Decimal

    @property
    def descuento_porcentaje(self) -> Decimal:
        if self.bruto <= 0:
            return Decimal("0")
        return _dinero(self.descuento / self.bruto * Decimal(100))


def calcular_pago(tarifa_por_hora: float | None, evento: Any, hora_real: datetime) -> PagoCalculado:
    """Calcula el pago de una asistencia concreta.

    ``tarifa_por_hora`` se interpreta en soles por cada hora de clase.
    """
    tarifa = Decimal(str(tarifa_por_hora or 0))
    duracion = duracion_minutos(evento)
    tardanza = minutos_de_tardanza(hora_real, evento)
    tolerancia = int(getattr(evento, "tolerancia_minutos", 0) or 0)

    bruto = _dinero(tarifa * Decimal(duracion) / _MINUTO)
    if tardanza <= tolerancia:
        descuento = Decimal("0.00")
    else:
        descuento = _dinero(min(bruto, tarifa * Decimal(tardanza) / _MINUTO))

    return PagoCalculado(
        duracion_minutos=duracion,
        minutos_tarde=tardanza,
        bruto=bruto,
        descuento=descuento,
        neto=_dinero(bruto - descuento),
    )