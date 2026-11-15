"""Generación de credenciales PNG (fotocheck), QR estáticos y iconos PWA.

- `qr_token`  : token alfanumérico único e impredecible (DOC-XXXXXXXX), estático 1 año.
- El QR codifica la URL pública `/mi-credencial?token=DOC-XXXXXXXX`, de modo que
  también funciona al escanearlo con la cámara nativa del celular.
"""

from __future__ import annotations

import os
import secrets
import string
from datetime import date, timedelta
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = STATIC_DIR / "uploads" / "docentes"
CREDENCIALES_DIR = STATIC_DIR / "credenciales"
IMG_DIR = STATIC_DIR / "img"
# Los avatares viven dentro del volumen `uploads` (igual que las fotos) para que
# no se pierdan al recrear el contenedor.
AVATAR_DIR = UPLOAD_DIR.parent / "avatares"

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

# Paleta (Tailwind slate / emerald) para que la credencial coincida con la app
COL_FONDO = "#f8fafc"
COL_TINTA = "#0f172a"
COL_ACENTO = "#059669"
COL_GRIS = "#64748b"
COL_BLANCO = "#ffffff"

# Formato vertical 9:16 (1080×1920), igual que la pantalla de un celular:
# el docente guarda la credencial como captura y la muestra para el escaneo.
_ANCHO, _ALTO = 1080, 1920

_FUENTES_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
_FUENTES_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _asegurar_dirs() -> None:
    for ruta in (UPLOAD_DIR, AVATAR_DIR, CREDENCIALES_DIR, IMG_DIR):
        ruta.mkdir(parents=True, exist_ok=True)


def _fuente(tam: int, negrita: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for ruta in (_FUENTES_BOLD if negrita else _FUENTES_REG):
        try:
            return ImageFont.truetype(ruta, tam)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tam)
    except TypeError:  # Pillow antiguo
        return ImageFont.load_default()


def nuevo_qr_token() -> str:
    """DOC- + 8 caracteres alfanuméricos sin caracteres ambiguos (0/O, 1/I/L)."""
    alfabeto = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "DOC-" + "".join(secrets.choice(alfabeto) for _ in range(8))


def vigencia_qr(anios: int = 1) -> date:
    return date.today() + timedelta(days=365 * anios)


def url_credencial(qr_token: str) -> str:
    return f"{BASE_URL}/mi-credencial?token={qr_token}"


def _dibujar_qr(contenido: str, tam: int = 240) -> Image.Image:
    """QR con módulos de tamaño entero y sin reescalado.

    Se calcula primero cuántos módulos necesita el contenido y luego se elige el
    `box_size` entero más cercano al tamaño pedido. Así los módulos nunca quedan
    de anchos distintos (lo que arruina la lectura desde la pantalla de un celular)
    y el resultado se centra sobre un lienzo blanco del tamaño exacto solicitado.
    """
    sonda = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=2)
    sonda.add_data(contenido)
    sonda.make(fit=True)
    modulos = sonda.modules_count + sonda.border * 2

    caja = max(1, tam // modulos)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=caja, border=2)
    qr.add_data(contenido)
    qr.make(fit=True)
    img = qr.make_image(fill_color=COL_TINTA, back_color=COL_BLANCO).convert("RGB")

    if img.width == tam:
        return img
    lienzo = Image.new("RGB", (tam, tam), COL_BLANCO)
    lienzo.paste(img, ((tam - img.width) // 2, (tam - img.height) // 2))
    return lienzo


def crear_qr_png(contenido: str, destino: Path) -> Path:
    _asegurar_dirs()
    destino.parent.mkdir(parents=True, exist_ok=True)
    _dibujar_qr(contenido, 900).save(destino, format="PNG", optimize=True)
    return destino


def avatar_placeholder(iniciales: str, tam: int = 320) -> Image.Image:
    """Foto de reemplazo con iniciales sobre degradado, por si aún no suben foto."""
    img = Image.new("RGB", (tam, tam), COL_FONDO)
    draw = ImageDraw.Draw(img)
    for y in range(tam):
        t = y / tam
        color = (
            int(15 + (5 - 15) * t),
            int(23 + (150 - 23) * t),
            int(42 + (110 - 42) * t),
        )
        draw.line([(0, y), (tam, y)], fill=color)
    fuente = _fuente(int(tam * 0.42), negrita=True)
    texto = (iniciales or "D")[:3].upper()
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((tam - w) / 2 - bbox[0], (tam - h) / 2 - bbox[1]), texto, font=fuente, fill="#ffffff")
    return img


def guardar_avatar(iniciales: str, destino: Path) -> str:
    _asegurar_dirs()
    destino.parent.mkdir(parents=True, exist_ok=True)
    avatar_placeholder(iniciales).save(destino, format="PNG", optimize=True)
    return str(destino)


def ruta_avatar(docente_id: int) -> Path:
    return AVATAR_DIR / f"avatar_{docente_id}.png"


def url_avatar(docente_id: int) -> str:
    return f"/static/uploads/avatares/avatar_{docente_id}.png"


def asegurar_avatar(docente) -> str:
    """Devuelve la ruta web de la foto del docente, generándola si hace falta.

    - Si el docente ya subió una foto y el archivo sigue ahí, se respeta.
    - Si no hay foto (o el archivo se perdió), se genera el avatar con sus
      iniciales dentro del volumen persistente.

    Es idempotente: sirve tanto al crear/editar un docente como para reparar
    registros antiguos al arrancar la aplicación.
    """
    actual = (getattr(docente, "foto_path", "") or "").strip()
    if actual:
        candidata = BASE_DIR / actual.lstrip("/").replace("/", os.sep)
        if candidata.exists():
            return actual
    guardar_avatar(docente.iniciales, ruta_avatar(docente.id))
    return url_avatar(docente.id)


def _cargar_foto(foto_path: str | None, iniciales: str, caja: tuple[int, int]) -> Image.Image:
    ancho, alto = caja
    ruta = None
    if foto_path:
        candidata = BASE_DIR / foto_path.lstrip("/").replace("/", os.sep)
        if candidata.exists():
            ruta = candidata
        elif Path(foto_path).exists():
            ruta = Path(foto_path)
    if ruta:
        try:
            foto = Image.open(ruta).convert("RGB")
            # recorte centrado tipo "cover"
            w, h = foto.size
            escala = max(ancho / w, alto / h)
            nw, nh = int(w * escala + 0.5), int(h * escala + 0.5)
            foto = foto.resize((nw, nh), Image.LANCZOS)
            izq = (nw - ancho) // 2
            top = (nh - alto) // 2
            return foto.crop((izq, top, izq + ancho, top + alto))
        except Exception:  # noqa: BLE001 - cualquier error cae en el placeholder
            pass
    return avatar_placeholder(iniciales).resize((ancho, alto), Image.LANCZOS)


def _texto_ajustado(draw: ImageDraw.ImageDraw, texto: str, fuente, max_ancho: int) -> str:
    """Recorta con '…' si el texto no entra en el ancho máximo."""
    if draw.textbbox((0, 0), texto, font=fuente)[2] <= max_ancho:
        return texto
    while texto and draw.textbbox((0, 0), texto + "…", font=fuente)[2] > max_ancho:
        texto = texto[:-1]
    return texto + "…"


def _envolver(
    draw: ImageDraw.ImageDraw, texto: str, fuente, max_ancho: int, max_lineas: int = 2
) -> list[str]:
    """Reparte el texto en varias líneas; si sobran, recorta la última con '…'."""
    lineas: list[str] = []
    actual = ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if not actual or draw.textbbox((0, 0), prueba, font=fuente)[2] <= max_ancho:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    if len(lineas) > max_lineas:
        resto = " ".join(lineas[max_lineas - 1 :])
        lineas = lineas[: max_lineas - 1] + [_texto_ajustado(draw, resto, fuente, max_ancho)]
    return lineas


def _centrado(
    draw: ImageDraw.ImageDraw, texto: str, fuente, y: int, color: str, ancho: int = _ANCHO
) -> None:
    """Escribe el texto centrado en horizontal, alineando su borde superior en `y`."""
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    draw.text(
        ((ancho - (bbox[2] - bbox[0])) / 2 - bbox[0], y - bbox[1]),
        texto,
        font=fuente,
        fill=color,
    )


def generar_credencial(docente, guardar_en: Path | None = None) -> Path:
    """Dibuja la credencial vertical (1080×1920) y devuelve la ruta del PNG.

    Está pensada para el celular: el docente la guarda como captura de pantalla y
    la muestra al ingresar. Por eso el QR va grande, sobre fondo blanco y con buen
    margen de silencio, que es lo que permite leerlo desde la pantalla de otro
    teléfono aunque baje el brillo o haya reflejos.
    """
    _asegurar_dirs()

    lienzo = Image.new("RGB", (_ANCHO, _ALTO), COL_FONDO)
    d = ImageDraw.Draw(lienzo)

    # --- Cabecera -----------------------------------------------------------
    d.rectangle([0, 0, _ANCHO, 190], fill=COL_TINTA)
    d.rectangle([0, 190, _ANCHO, 200], fill=COL_ACENTO)
    _centrado(d, "NEUROCIENCIAS", _fuente(44, negrita=True), 52, COL_BLANCO)
    _centrado(d, "Credencial docente · Control de asistencia", _fuente(24), 118, "#94a3b8")

    # --- Foto ---------------------------------------------------------------
    foto_tam = 240
    foto = _cargar_foto(
        getattr(docente, "foto_path", ""), docente.iniciales, (foto_tam, foto_tam)
    )
    x_foto, y_foto = (_ANCHO - foto_tam) // 2, 240
    marco = Image.new("RGB", (foto_tam + 12, foto_tam + 12), COL_TINTA)
    marco.paste(foto, (6, 6))
    lienzo.paste(marco, (x_foto - 6, y_foto - 6))

    # --- Nombre y especialidad ---------------------------------------------
    f_nombre = _fuente(52, negrita=True)
    y = y_foto + foto_tam + 40
    for linea in _envolver(d, docente.nombre_completo.upper(), f_nombre, _ANCHO - 120, 2):
        _centrado(d, linea, f_nombre, y, COL_TINTA)
        y += 62
    y += 8
    _centrado(d, docente.especialidad or "Docente", _fuente(30), y, COL_GRIS)
    y += 46

    # --- Código de ingreso --------------------------------------------------
    ancho_caja, alto_caja = 620, 110
    x_caja = (_ANCHO - ancho_caja) // 2
    d.rectangle(
        [x_caja, y, x_caja + ancho_caja, y + alto_caja],
        fill="#eef2f7",
        outline=COL_TINTA,
        width=3,
    )
    _centrado(d, "CÓDIGO DE INGRESO", _fuente(20, negrita=True), y + 16, COL_GRIS)
    _centrado(d, docente.qr_token, _fuente(40, negrita=True), y + 46, COL_TINTA)

    # --- QR grande (lo que realmente se escanea) ---------------------------
    qr_tam, qr_y = 740, 880
    qr_x = (_ANCHO - qr_tam) // 2
    d.rectangle(
        [qr_x - 30, qr_y - 30, qr_x + qr_tam + 30, qr_y + qr_tam + 30],
        fill=COL_BLANCO,
        outline="#e2e8f0",
        width=3,
    )
    lienzo.paste(_dibujar_qr(url_credencial(docente.qr_token), qr_tam), (qr_x, qr_y))

    f_pie = _fuente(24)
    _centrado(
        d,
        "Muestra este QR al ingresar · Sube el brillo al máximo",
        f_pie,
        qr_y + qr_tam + 50,
        COL_GRIS,
    )

    # --- Pie ----------------------------------------------------------------
    vigencia = getattr(docente, "qr_vigencia", None) or vigencia_qr()
    d.rectangle([0, _ALTO - 110, _ANCHO, _ALTO], fill=COL_TINTA)
    _centrado(d, f"Vigente hasta {vigencia.strftime('%d/%m/%Y')}", f_pie, _ALTO - 84, COL_BLANCO)
    _centrado(d, "Neurociencias · Resolver dudas con Dirección", _fuente(20), _ALTO - 44, "#94a3b8")

    destino = guardar_en or (CREDENCIALES_DIR / f"credencial_{docente.id}.png")
    destino.parent.mkdir(parents=True, exist_ok=True)
    lienzo.save(destino, format="PNG", optimize=True)
    return destino


def generar_iconos_pwa() -> dict[str, str]:
    """Crea icon-192.png / icon-512.png y un favicon (estilo QR) en /static/img."""
    _asegurar_dirs()
    rutas: dict[str, str] = {}
    for tam in (192, 512):
        img = Image.new("RGB", (tam, tam), COL_TINTA)
        d = ImageDraw.Draw(img)
        margen = int(tam * 0.14)
        celda = (tam - margen * 2) // 5
        patron = [
            "11111",
            "10001",
            "10101",
            "10001",
            "11111",
        ]
        # esquinas tipo QR
        for fila in range(5):
            for col in range(5):
                if patron[fila][col] == "1":
                    x = margen + col * celda
                    y = margen + fila * celda
                    d.rectangle([x + 2, y + 2, x + celda - 3, y + celda - 3], fill=COL_BLANCO)
        # acento central
        d.rectangle(
            [
                tam // 2 - int(tam * 0.11),
                tam // 2 - int(tam * 0.11),
                tam // 2 + int(tam * 0.11),
                tam // 2 + int(tam * 0.11),
            ],
            fill=COL_ACENTO,
        )
        nombre = f"icon-{tam}.png"
        img.save(IMG_DIR / nombre, format="PNG", optimize=True)
        rutas[f"icon-{tam}"] = f"/static/img/{nombre}"

    favicon = generar_favicon()
    rutas["favicon"] = favicon
    return rutas


def generar_favicon() -> str:
    img = Image.new("RGB", (64, 64), COL_TINTA)
    d = ImageDraw.Draw(img)
    d.rectangle([6, 6, 58, 58], outline="#ffffff", width=5)
    d.rectangle([22, 22, 42, 42], fill=COL_ACENTO)
    ruta = IMG_DIR / "favicon.png"
    img.save(ruta, format="PNG", optimize=True)
    return "/static/img/favicon.png"
