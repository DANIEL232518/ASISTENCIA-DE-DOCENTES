"""Exportación de asistencias a Excel (.xlsx) con openpyxl, lista para enviar."""

from __future__ import annotations

import io
from collections import OrderedDict
from datetime import date, datetime, time
from typing import Iterable

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

COLUMNAS = [
    "N° Llegada",
    "Fecha",
    "Evento",
    "Docente",
    "Hora Real",
    "Hora Computable",
    "Estado",
    "Registrado Por",
]

_ANCHO = [12, 13, 34, 40, 13, 18, 13, 26]

_CABECERA_FILL = PatternFill("solid", fgColor="0F172A")
_CABECERA_FONT = Font(color="FFFFFF", bold=True, size=11, name="Calibri")
_PUNTUAL_FILL = PatternFill("solid", fgColor="D1FAE5")
_PUNTUAL_FONT = Font(color="065F46", bold=True, size=11)
_TARDE_FILL = PatternFill("solid", fgColor="FEF3C7")
_TARDE_FONT = Font(color="92400E", bold=True, size=11)
_ZEBRA = PatternFill("solid", fgColor="F1F5F9")
_THIN = Side(style="thin", color="CBD5E1")
_BORDE = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _fmt(valor, tipo: str) -> str:
    if valor is None:
        return ""
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y") if tipo == "fecha" else valor.strftime("%H:%M:%S")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, time):
        return valor.strftime("%H:%M:%S")
    return str(valor)


def _agrupar(filas: list[dict]) -> tuple[OrderedDict, OrderedDict]:
    """Agrupa los registros por docente y por día contando puntuales/tardanzas."""
    por_docente: OrderedDict = OrderedDict()
    por_dia: OrderedDict = OrderedDict()

    for fila in filas:
        tardanza = str(fila.get("estado", "")).upper() == "TARDANZA"
        clave = "TARDANZA" if tardanza else "PUNTUAL"

        docente = str(fila.get("docente") or "—")
        por_docente.setdefault(docente, {"PUNTUAL": 0, "TARDANZA": 0})[clave] += 1

        fecha = fila.get("fecha")
        if isinstance(fecha, datetime):
            fecha = fecha.date()
        dia = fecha.strftime("%d/%m/%Y") if isinstance(fecha, date) else str(fecha or "—")
        por_dia.setdefault(dia, {"PUNTUAL": 0, "TARDANZA": 0})[clave] += 1

    return por_docente, por_dia


def _tabla_resumen(ws, fila_inicio: int, etiqueta: str, datos: OrderedDict) -> tuple[int, int, int]:
    """Escribe una tabla (Nombre | Puntuales | Tardanzas | Total | % Puntualidad) y
    devuelve (primera_fila_datos, ultima_fila_datos, fila_total)."""
    # Título de sección
    ws.cell(row=fila_inicio, column=1, value=etiqueta).font = Font(bold=True, size=13, color="FFFFFF")
    for c in range(1, 6):
        ws.cell(row=fila_inicio, column=c).fill = PatternFill("solid", fgColor="1E3A8A")
    ws.merge_cells(start_row=fila_inicio, start_column=1, end_row=fila_inicio, end_column=5)
    ws.row_dimensions[fila_inicio].height = 22

    cab = fila_inicio + 1
    headers = ("Nombre", "Puntuales", "Tardanzas", "Total", "% Puntualidad")
    for col, nombre in enumerate(headers, start=1):
        celda = ws.cell(row=cab, column=col, value=nombre)
        celda.fill = _CABECERA_FILL
        celda.font = _CABECERA_FONT
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        celda.border = _BORDE
    ws.row_dimensions[cab].height = 20

    ordenado = sorted(
        datos.items(), key=lambda kv: (-(kv[1]["PUNTUAL"] + kv[1]["TARDANZA"]), kv[0].lower())
    )
    fila = cab
    total_p = total_t = 0
    for nombre, d in ordenado:
        fila += 1
        p, t = d["PUNTUAL"], d["TARDANZA"]
        tot = p + t
        total_p += p
        total_t += t
        valores = [nombre, p, t, tot, (p / tot if tot else 0)]
        for col, valor in enumerate(valores, start=1):
            celda = ws.cell(row=fila, column=col, value=valor)
            celda.border = _BORDE
            celda.alignment = Alignment(
                horizontal="center" if col > 1 else "left", vertical="center"
            )
            if col == 5:
                celda.number_format = "0.0%"
        if (fila - cab) % 2 == 0:
            for col in range(1, 6):
                ws.cell(row=fila, column=col).fill = _ZEBRA

    # Fila TOTAL
    fila += 1
    tot = total_p + total_t
    valores = ["TOTAL", total_p, total_t, tot, (total_p / tot if tot else 0)]
    for col, valor in enumerate(valores, start=1):
        celda = ws.cell(row=fila, column=col, value=valor)
        celda.font = Font(bold=True, size=11, color="0F172A")
        celda.fill = PatternFill("solid", fgColor="E2E8F0")
        celda.border = _BORDE
        celda.alignment = Alignment(horizontal="center" if col > 1 else "left", vertical="center")
        if col == 5:
            celda.number_format = "0.0%"

    return cab + 1, fila - 1, fila


def _grafico_barras(ws, titulo: str, primera: int, ultima: int, ancla: str) -> None:
    """Inserta un gráfico de barras agrupado (Puntuales vs Tardanzas)."""
    if ultima < primera:
        return
    grafico = BarChart()
    grafico.type = "col"
    grafico.grouping = "clustered"
    grafico.overlap = -10
    grafico.style = 10
    grafico.title = titulo
    grafico.y_axis.title = "Registros"
    grafico.x_axis.title = None
    grafico.gapWidth = 60
    grafico.dLbls = DataLabelList()
    grafico.dLbls.showVal = True

    datos = Reference(ws, min_col=2, max_col=3, min_row=primera - 1, max_row=ultima)
    categorias = Reference(ws, min_col=1, min_row=primera, max_row=ultima)
    grafico.add_data(datos, titles_from_data=True)
    grafico.set_categories(categorias)

    colores = ["10B981", "F59E0B"]
    for serie, color in zip(grafico.series, colores):
        serie.graphicalProperties.solidFill = color
        serie.graphicalProperties.line.solidFill = color

    grafico.height = 9.5
    grafico.width = 19
    ws.add_chart(grafico, ancla)


def _hoja_resumen(wb: Workbook, filas: list[dict], periodo: str) -> None:
    """Crea la hoja 'Resumen' con KPIs, tablas y gráficos de barras."""
    ws = wb.create_sheet("Resumen")
    ws.sheet_view.showGridLines = False

    ws["A1"] = "RESUMEN DE ASISTENCIAS"
    ws["A1"].font = Font(bold=True, size=17, color="0F172A")
    ws["A2"] = periodo or ""
    ws["A2"].font = Font(size=11, color="64748B", italic=True)

    # --- KPIs --------------------------------------------------------------
    total = len(filas)
    puntuales = sum(1 for f in filas if str(f.get("estado", "")).upper() != "TARDANZA")
    tardanzas = total - puntuales
    pct = (puntuales / total) if total else 0

    kpi_fila = 4
    kpis = [
        ("Total registros", total, "0", "0F172A"),
        ("Puntuales", puntuales, "0", "065F46"),
        ("Tardanzas", tardanzas, "0", "92400E"),
        ("% Puntualidad", pct, "0.0%", "1E3A8A"),
    ]
    for i, (nombre, valor, fmt, color) in enumerate(kpis):
        col = 1 + i * 2
        ws.cell(row=kpi_fila, column=col, value=nombre).font = Font(bold=True, size=10, color="64748B")
        c = ws.cell(row=kpi_fila + 1, column=col, value=valor)
        c.font = Font(bold=True, size=16, color=color)
        c.number_format = fmt
        ws.merge_cells(start_row=kpi_fila, start_column=col, end_row=kpi_fila, end_column=col + 1)
        ws.merge_cells(start_row=kpi_fila + 1, start_column=col, end_row=kpi_fila + 1, end_column=col + 1)

    por_docente, por_dia = _agrupar(filas)

    # --- POR DOCENTE --------------------------------------------------------
    fila = 7
    prim_doc, ult_doc, _ = _tabla_resumen(ws, fila, "POR DOCENTE", por_docente)
    # El gráfico va a la derecha de la tabla (columna G) para no solaparse.
    _grafico_barras(ws, "Asistencias por docente", prim_doc, ult_doc, f"G{fila}")

    # Siguiente sección: debajo del bloque más alto (tabla o gráfico ~18 filas)
    fila_dia = max(ult_doc, fila + 18) + 3
    prim_dia, ult_dia, _ = _tabla_resumen(ws, fila_dia, "POR DÍA", por_dia)
    _grafico_barras(ws, "Asistencias por día", prim_dia, ult_dia, f"G{fila_dia}")

    for col, ancho in zip("ABCDEFG", (30, 12, 12, 10, 14, 3, 3)):
        ws.column_dimensions[col].width = ancho


def exportar_asistencias(
    filas: Iterable[dict],
    titulo: str = "Asistencias",
    periodo: str = "",
) -> bytes:
    """Devuelve el contenido binario de un .xlsx formateado.

    Cada fila debe traer: n_llegada, fecha, evento, docente, hora_real,
    hora_computable, estado, registrado_por.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Asistencias"[:31]

    # --- Encabezado decorativo ---------------------------------------------
    ws.merge_cells("A1:H1")
    t = ws["A1"]
    t.value = "NEUROCIENCIAS · CONTROL DE ASISTENCIA DOCENTE"
    t.font = Font(bold=True, size=16, color="0F172A")
    t.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 26

    ws.merge_cells("A2:H2")
    s = ws["A2"]
    s.value = f"{titulo}" + (f"  ·  {periodo}" if periodo else "")
    s.font = Font(size=11, color="64748B", italic=True)
    s.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 18

    fila_cabecera = 4
    for col, nombre in enumerate(COLUMNAS, start=1):
        celda = ws.cell(row=fila_cabecera, column=col, value=nombre)
        celda.fill = _CABECERA_FILL
        celda.font = _CABECERA_FONT
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        celda.border = _BORDE
    ws.row_dimensions[fila_cabecera].height = 22

    filas = list(filas)
    for i, fila in enumerate(filas):
        r = fila_cabecera + 1 + i
        valores = [
            fila.get("n_llegada", i + 1),
            _fmt(fila.get("fecha"), "fecha"),
            fila.get("evento", ""),
            fila.get("docente", ""),
            _fmt(fila.get("hora_real"), "hora"),
            _fmt(fila.get("hora_computable"), "hora"),
            fila.get("estado", ""),
            fila.get("registrado_por", ""),
        ]
        for col, valor in enumerate(valores, start=1):
            celda = ws.cell(row=r, column=col, value=valor)
            celda.border = _BORDE
            celda.alignment = Alignment(
                horizontal="center" if col in (1, 2, 5, 6, 7) else "left",
                vertical="center",
            )
            if i % 2 == 1 and col != 7:
                celda.fill = _ZEBRA
        estado_cell = ws.cell(row=r, column=7)
        if str(fila.get("estado", "")).upper() == "TARDANZA":
            estado_cell.fill = _TARDE_FILL
            estado_cell.font = _TARDE_FONT
        else:
            estado_cell.fill = _PUNTUAL_FILL
            estado_cell.font = _PUNTUAL_FONT
        ws.row_dimensions[r].height = 20

    # Fila de totales
    if filas:
        r = fila_cabecera + 1 + len(filas)
        tardanzas = sum(1 for f in filas if str(f.get("estado", "")).upper() == "TARDANZA")
        puntuales = len(filas) - tardanzas
        celda = ws.cell(row=r, column=4, value=f"TOTAL: {len(filas)} registros")
        celda.font = Font(bold=True, size=11, color="0F172A")
        ws.cell(row=r, column=7, value=f"P:{puntuales} / T:{tardanzas}").font = Font(
            bold=True, size=11, color="0F172A"
        )

    # Pie: credito del desarrollador (discreto, al final de la hoja)
    r_pie = fila_cabecera + len(filas) + 3
    pie = ws.cell(row=r_pie, column=1, value="© 2026 Neurociencias · Todos los derechos reservados")
    pie.font = Font(size=9, color="94A3B8", italic=True)
    pie.alignment = Alignment(horizontal="left", vertical="center")
    pie2 = ws.cell(row=r_pie + 1, column=1, value="Desarrollado por Jhon Daniel Pari Valencia · Contacto 900717074")
    pie2.font = Font(size=9, color="94A3B8", italic=True)
    pie2.alignment = Alignment(horizontal="left", vertical="center")

    for i, ancho in enumerate(_ANCHO, start=1):
        ws.column_dimensions[get_column_letter(i)].width = ancho

    ws.freeze_panes = ws.cell(row=fila_cabecera + 1, column=1)
    ws.auto_filter.ref = (
        f"A{fila_cabecera}:H{fila_cabecera + max(len(filas), 1)}"
    )

    # Hoja de resumen con gráficos de barras (solo si hay datos)
    if filas:
        _hoja_resumen(wb, filas, periodo)

    buffer = io.BytesIO()
    wb.save(buffer)
    wb.close()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Pagos
# ---------------------------------------------------------------------------
_PAGO_MONEY = '"S/" #,##0.00'
_PAGO_PCT = '0.0%'
_PAGO_ANCHO_RESUMEN = [38, 14, 12, 12, 12, 13, 15, 15, 14, 15]
_PAGO_ANCHO_DETALLE = [12, 34, 38, 13, 13, 13, 13, 14, 12, 14, 15, 15, 14, 15]


def _encabezado_hoja(ws, titulo: str, periodo: str, columnas: int) -> int:
    """Escribe el título y devuelve la fila donde va la cabecera de la tabla."""
    ultima = get_column_letter(columnas)
    ws.merge_cells(f"A1:{ultima}1")
    t = ws["A1"]
    t.value = titulo
    t.font = Font(bold=True, size=16, color="0F172A")
    t.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 26

    ws.merge_cells(f"A2:{ultima}2")
    s = ws["A2"]
    s.value = periodo
    s.font = Font(size=11, color="64748B", italic=True)
    s.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 18
    return 4


def _cabecera(ws, fila: int, columnas: list[str]) -> None:
    for col, nombre in enumerate(columnas, start=1):
        celda = ws.cell(row=fila, column=col, value=nombre)
        celda.fill = _CABECERA_FILL
        celda.font = _CABECERA_FONT
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        celda.border = _BORDE
    ws.row_dimensions[fila].height = 24


def exportar_pagos(
    resumen: Iterable[dict],
    detalle: Iterable[dict],
    periodo: str = "",
) -> bytes:
    """Devuelve un .xlsx con dos hojas: resumen por docente y detalle de asistencias."""
    wb = Workbook()

    # --- Hoja 1: resumen por docente --------------------------------------
    ws = wb.active
    ws.title = "Resumen"
    ws.sheet_view.showGridLines = False
    cabecera_filas = _encabezado_hoja(ws, "PAGO DE DOCENTES · NEUROCIENCIAS", periodo, 10)
    _cabecera(
        ws,
        cabecera_filas,
        [
            "Docente",
            "Tarifa/h",
            "Asistencias",
            "Puntuales",
            "Tardanzas",
            "Min. tarde",
            "Bruto",
            "Descuento",
            "% Descuento",
            "Neto a pagar",
        ],
    )

    filas_resumen = list(resumen)
    r = cabecera_filas
    for i, fila in enumerate(filas_resumen):
        r += 1
        valores = [
            fila.get("nombre", ""),
            fila.get("tarifa_por_hora", 0),
            fila.get("asistencias", 0),
            fila.get("puntuales", 0),
            fila.get("tardanzas", 0),
            fila.get("minutos_tarde", 0),
            fila.get("bruto", 0),
            fila.get("descuento", 0),
            (fila.get("descuento", 0) / fila.get("bruto", 0)) if fila.get("bruto", 0) else 0,
            fila.get("neto", 0),
        ]
        for col, valor in enumerate(valores, start=1):
            celda = ws.cell(row=r, column=col, value=valor)
            celda.border = _BORDE
            celda.alignment = Alignment(
                horizontal="left" if col == 1 else "center", vertical="center"
            )
            if i % 2 == 1:
                celda.fill = _ZEBRA
            if col in (2, 7, 8, 10):
                celda.number_format = _PAGO_MONEY
            if col == 9:
                celda.number_format = _PAGO_PCT
        if fila.get("tardanzas", 0):
            ws.cell(row=r, column=8).fill = _TARDE_FILL
            ws.cell(row=r, column=8).font = _TARDE_FONT
        ws.row_dimensions[r].height = 20

    # Fila de totales
    if filas_resumen:
        r += 1
        total_bruto = sum(float(f.get("bruto", 0) or 0) for f in filas_resumen)
        total_descuento = sum(float(f.get("descuento", 0) or 0) for f in filas_resumen)
        total_neto = sum(float(f.get("neto", 0) or 0) for f in filas_resumen)
        total_asistencias = sum(int(f.get("asistencias", 0) or 0) for f in filas_resumen)
        total_puntuales = sum(int(f.get("puntuales", 0) or 0) for f in filas_resumen)
        total_tardanzas = sum(int(f.get("tardanzas", 0) or 0) for f in filas_resumen)
        total_minutos = sum(int(f.get("minutos_tarde", 0) or 0) for f in filas_resumen)
        valores = [
            "TOTAL",
            "",
            total_asistencias,
            total_puntuales,
            total_tardanzas,
            total_minutos,
            total_bruto,
            total_descuento,
            (total_descuento / total_bruto) if total_bruto else 0,
            total_neto,
        ]
        for col, valor in enumerate(valores, start=1):
            celda = ws.cell(row=r, column=col, value=valor)
            celda.font = Font(bold=True, size=11, color="0F172A")
            celda.fill = PatternFill("solid", fgColor="E2E8F0")
            celda.border = _BORDE
            celda.alignment = Alignment(
                horizontal="left" if col == 1 else "center", vertical="center"
            )
            if col in (2, 7, 8, 10):
                celda.number_format = _PAGO_MONEY
            if col == 9:
                celda.number_format = _PAGO_PCT

    # Pie: credito del desarrollador (discreto, al final de la hoja)
    r_pie = cabecera_filas + len(filas_resumen) + 3
    pie = ws.cell(row=r_pie, column=1, value="© 2026 Neurociencias · Todos los derechos reservados")
    pie.font = Font(size=9, color="94A3B8", italic=True)
    pie.alignment = Alignment(horizontal="left", vertical="center")
    pie2 = ws.cell(row=r_pie + 1, column=1, value="Desarrollado por Jhon Daniel Pari Valencia · Contacto 900717074")
    pie2.font = Font(size=9, color="94A3B8", italic=True)
    pie2.alignment = Alignment(horizontal="left", vertical="center")

    for i, ancho in enumerate(_PAGO_ANCHO_RESUMEN, start=1):
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.freeze_panes = ws.cell(row=cabecera_filas + 1, column=1)
    if filas_resumen:
        ws.auto_filter.ref = (
            f"A{cabecera_filas}:J{cabecera_filas + len(filas_resumen)}"
        )

        # Gráfico: neto a pagar por docente
        grafico = BarChart()
        grafico.type = "col"
        grafico.style = 10
        grafico.title = "Neto a pagar por docente"
        grafico.y_axis.title = "Soles"
        grafico.x_axis.title = None
        grafico.legend = None
        grafico.dLbls = DataLabelList()
        grafico.dLbls.showVal = True
        datos = Reference(ws, min_col=10, min_row=cabecera_filas, max_row=cabecera_filas + len(filas_resumen))
        categorias = Reference(ws, min_col=1, min_row=cabecera_filas + 1, max_row=cabecera_filas + len(filas_resumen))
        grafico.add_data(datos, titles_from_data=True)
        grafico.set_categories(categorias)
        grafico.height = 9
        grafico.width = 20
        ws.add_chart(grafico, f"A{cabecera_filas + len(filas_resumen) + 4}")

    # --- Hoja 2: detalle por asistencia -----------------------------------
    ws2 = wb.create_sheet("Detalle")
    ws2.sheet_view.showGridLines = False
    cab2 = _encabezado_hoja(ws2, "DETALLE DE PAGOS POR ASISTENCIA", periodo, 14)
    _cabecera(
        ws2,
        cab2,
        [
            "Fecha",
            "Evento",
            "Docente",
            "Hora inicio",
            "Hora fin",
            "Duración (min)",
            "Hora real",
            "Estado",
            "Min. tarde",
            "Tarifa/h",
            "Bruto",
            "Descuento",
            "% Descuento",
            "Neto a pagar",
        ],
    )

    filas_detalle = list(detalle)
    for i, fila in enumerate(filas_detalle):
        r = cab2 + 1 + i
        bruto = float(fila.get("bruto", 0) or 0)
        descuento = float(fila.get("descuento", 0) or 0)
        valores = [
            _fmt(fila.get("fecha"), "fecha"),
            fila.get("evento_titulo", ""),
            fila.get("docente_nombre", ""),
            _fmt(fila.get("hora_inicio"), "hora"),
            _fmt(fila.get("hora_fin"), "hora"),
            fila.get("duracion_minutos", 0),
            _fmt(fila.get("hora_escaneo_real"), "hora"),
            fila.get("estado", ""),
            fila.get("minutos_tarde", 0),
            fila.get("tarifa_por_hora", 0),
            bruto,
            descuento,
            (descuento / bruto) if bruto else 0,
            fila.get("neto", 0),
        ]
        for col, valor in enumerate(valores, start=1):
            celda = ws2.cell(row=r, column=col, value=valor)
            celda.border = _BORDE
            celda.alignment = Alignment(
                horizontal="left" if col in (2, 3) else "center", vertical="center"
            )
            if i % 2 == 1:
                celda.fill = _ZEBRA
            if col in (10, 11, 12, 14):
                celda.number_format = _PAGO_MONEY
            if col == 13:
                celda.number_format = _PAGO_PCT
        estado_cell = ws2.cell(row=r, column=8)
        if str(fila.get("estado", "")).upper() == "TARDANZA":
            estado_cell.fill = _TARDE_FILL
            estado_cell.font = _TARDE_FONT
        else:
            estado_cell.fill = _PUNTUAL_FILL
            estado_cell.font = _PUNTUAL_FONT
        ws2.row_dimensions[r].height = 20

    for i, ancho in enumerate(_PAGO_ANCHO_DETALLE, start=1):
        ws2.column_dimensions[get_column_letter(i)].width = ancho
    ws2.freeze_panes = ws2.cell(row=cab2 + 1, column=1)
    if filas_detalle:
        ws2.auto_filter.ref = f"A{cab2}:N{cab2 + len(filas_detalle)}"

    buffer = io.BytesIO()
    wb.save(buffer)
    wb.close()
    return buffer.getvalue()
