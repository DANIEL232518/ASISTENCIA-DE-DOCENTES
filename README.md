# 📲 Control de Asistencia Docente · Neurociencias (PWA)

Aplicación web progresiva **mobile-first** para el control de ingreso del personal docente de
**Neurociencias** (capacidad máxima **40 docentes activos**), con escaneo de credenciales QR,
programación recurrente de horarios, historial filtrable, exportación a Excel y credenciales
digitales en PNG (formato vertical de celular).

---

## 1. Arranque rápido (Docker Compose)

```bash
# 1) Construir y levantar (app + PostgreSQL 16)
docker compose up --build
```

Cuando el log muestre `Aplicación lista en http://localhost:8000`, abre:

| Recurso | URL |
|---|---|
| Aplicación | http://localhost:8000 |
| Consulta pública de credencial | http://localhost:8000/mi-credencial |
| Documentación de la API | http://localhost:8000/docs |

> Para usarla desde el **celular** (y que la cámara del escáner funcione) revisa
> [Probar desde el celular](#11-probar-desde-el-celular).

### Credenciales iniciales (creadas por el seed automático)

| Rol | Email | Contraseña | Permisos |
|---|---|---|---|
| **DIRECTORA** (Dirección) | `directora@academia.pe` | `Directora123` *(cámbiala en producción)* | Acceso total... |
| **ADMIN** (Secretaría / Administrativo) | `admin@academia.pe` | `Admin123` *(cámbiala en producción)* | Escanear QR... |

> La cuenta de **Dirección** es única e intransferible: **no se puede eliminar ni desactivar** desde
> la aplicación (el backend responde `409`). El nombre inicial de la Directora es **Nayeli Villafan**.
> Las únicas cuentas que se pueden crear son de **Secretaría / Administrativo**.
> Cambia estas contraseñas desde **Secretaría y Administrativos** y ajusta `JWT_SECRET` en
> `docker-compose.yml` antes de usar en producción.

Detener / reiniciar:

```bash
docker compose down          # detener (conserva datos)
docker compose down -v       # detener y BORRAR base de datos y fotos
docker compose up -d --build # reconstruir en segundo plano
```

### Notas importantes de cámara

El navegador solo permite usar la cámara en **contexto seguro**: `http://localhost` funciona en la PC,
pero si accedes desde el **celular** debes usar HTTPS o publicar la app detrás de un proxy con TLS.
Si el dispositivo lo soporta se usa la **`BarcodeDetector` nativa**; si no, se usa `html5-qrcode`
(`fps: 15`, `qrbox: 250×250`).

---

## 1.1 Probar desde el celular

El puerto ya está publicado (`8000:8000` en `docker-compose.yml`), así que **no hay que tocar el router**.

### Opción A — Túnel HTTPS (recomendado, la cámara funciona)

Es la única opción que funciona igual en Android y en iPhone, sin instalar certificados:

```bash
# 1) Instalar cloudflared (una sola vez)
winget install --id Cloudflare.cloudflared -e

# 2) Levantar el túnel contra la app
cloudflared tunnel --url http://localhost:8000
#    -> imprime una URL tipo https://algo-aleatorio.trycloudflare.com
```

Copia esa URL y **vuelve a levantar la app apuntando ahí**, para que los QR de las
credenciales se generen con la dirección correcta (si no, el QR apuntaría a `localhost`
y no abriría nada desde el celular):

```powershell
$env:BASE_URL = "https://algo-aleatorio.trycloudflare.com"
docker compose up -d
```

Abre la URL desde el celular y escanea. Cada vez que reinicies el túnel la URL cambia:
repite el paso con la nueva y vuelve a generar las credenciales.

### Opción B — Red local por HTTP (sin cámara)

Averigua la IP de tu PC (`ipconfig`) y abre `http://<IP-DE-TU-PC>:8000` desde un celular
conectado a la **misma Wi-Fi**. Sirve para revisar dashboard, historial, credenciales y Excel,
pero **el escáner QR no funcionará** porque `http://` no es contexto seguro.

Si no carga, permite la entrada del puerto en el Firewall de Windows (PowerShell **como Administrador**):

```powershell
New-NetFirewallRule -DisplayName "Asistencia QR 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private,Public
```

### Opción C — HTTPS local con tu propio certificado

Pon un proxy inverso con TLS delante de la app (Caddy, nginx o `mkcert`) y **instala el
certificado raíz en el celular**. Todo queda dentro de tu red, pero requiere más pasos que la opción A.

---

## 2. Stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.11 · FastAPI (async) · SQLAlchemy 2.0 async · asyncpg · Pydantic v2 · PyJWT · bcrypt |
| Base de datos | PostgreSQL 16 (volumen persistente `pgdata`) |
| Reportes | openpyxl (`.xlsx` formateado con filtros y celdas de color) |
| Credenciales | `qrcode[pil]` + Pillow (fotocheck PNG con foto, nombre, especialidad y QR) |
| Frontend | HTML5 + JavaScript Vanilla ES6+ · Tailwind CSS 3 (compilado en el build, funciona offline) |
| Escáner | `BarcodeDetector` nativo → respaldo `html5-qrcode` (vendorizado en la imagen) |
| PWA | `manifest.json`, service worker con caché de shell, modo oscuro/claro |

---

## 3. Estructura del proyecto

```
.
├── docker-compose.yml         # servicios app (8000) y db (PostgreSQL 16)
├── Dockerfile                 # multi-etapa: compila Tailwind + vendoriza html5-qrcode
├── requirements.txt
├── frontend/                  # solo utilería de build (Tailwind)
│   ├── package.json
│   └── tailwind.config.js
└── app/
    ├── main.py                # API REST + páginas + reglas de negocio
    ├── database.py            # engine async, sesión, espera de BD
    ├── models.py              # usuarios, docentes, eventos, asistencias
    ├── schemas.py             # Pydantic v2
    ├── auth.py                # bcrypt + JWT + dependencias por rol
    ├── utils_qr.py            # QR estáticos, fotocheck PNG, iconos PWA
    ├── utils_excel.py         # exportación .xlsx
    ├── seed.py                # cuentas por defecto + iconos
    ├── templates/             # base, login, index (SPA), credencial pública
    └── static/
        ├── js/app.js          # toda la lógica del cliente
        ├── css/input.css      # fuentes Tailwind (compiladas en el build)
        ├── manifest.json · sw.js
        └── uploads/ · credenciales/ · img/   (volumen `uploads`)
```

---

## 4. Modelo de datos y reglas de negocio

### `usuarios`
`id`, `nombre`, `email` (único), `password_hash` (bcrypt), `rol` (`DIRECTORA` | `ADMIN`), `activo`.
La **Dirección** (`DIRECTORA`) es la única que puede crear, editar, activar y desactivar docentes,
gestionar cuentas, programar series de horarios y eliminar asistencias; **Secretaría / Administrativo**
(`ADMIN`) queda limitado por el backend (no solo por la UI).
La cuenta de Dirección es única y **no se puede eliminar ni desactivar** (`409`), y el rol nunca se
edita desde la app: toda cuenta nueva se crea como `ADMIN`.

### `docentes`
`id`, `nombres`, `apellidos`, `especialidad`, `foto_path`, `qr_token`, `qr_vigencia`, `activo`,
`tarifa_por_hora` (monto en soles que la academia paga por cada hora de clase).

* **Límite estricto de 40 activos** validado en el backend (`409` si se excede).
* `qr_token` = `DOC-XXXXXXXX` (8 caracteres de un alfabeto sin caracteres ambiguos), único e
  impredecible, **estático por 1 año** (`qr_vigencia`).
* El QR codifica la URL pública `/mi-credencial?token=DOC-XXXXXXXX`, por lo que también se puede
  leer con la cámara normal del celular.
* Al crear o editar se genera/regenera la **credencial PNG** en formato vertical de celular
  (**1080×1920**, con un QR grande y de alto contraste):
  `GET /api/docentes/{id}/credencial` (foto, nombre, especialidad, token, vigencia y QR).
* Si no se sube foto, se genera un avatar con las iniciales (guardado en el volumen `uploads`).

### `eventos`
`id`, `titulo`, `fecha`, `hora_inicio`, `hora_fin`, `tolerancia_minutos` (default 5),
`es_espontaneo`, `activo`, `grupo_id` (serie recurrente).

* **Sin eventos paralelos**: el backend rechaza cualquier solapamiento horario en la misma fecha.
* **Programación recurrente** (`POST /api/eventos/recurrente`): días de la semana + rango de fechas
  (mensual o anual) → genera una fila por instancia, todas con el mismo `grupo_id`.
* **Evento espontáneo** (`POST /api/eventos`): bloque único rápido; es lo único que puede crear un Admin.
* **Días libres / feriados**: `DELETE /api/eventos/fecha/{fecha}` desactiva todos los eventos de una
  fecha; `PUT /api/eventos/{id}/estado` reactiva o desactiva un evento puntual;
  `DELETE /api/eventos/serie/{grupo_id}` elimina toda una serie.

### `asistencias`
`id`, `evento_id`, `docente_id`, `registrado_por_id`, `hora_escaneo_real`, `hora_computable`, `estado`
(`PUNTUAL` | `TARDANZA`) + `UNIQUE(evento_id, docente_id)`.

Reglas aplicadas en `POST /api/asistencias/escaneo`:

| Situación | `hora_escaneo_real` | `hora_computable` | `estado` |
|---|---|---|---|
| Llega antes del inicio (15:30 para evento de 16:00) | 15:30:xx | **16:00:00** | `PUNTUAL` |
| Dentro del inicio + tolerancia | hora real | hora real | `PUNTUAL` |
| Después de la tolerancia | hora real | hora real | `TARDANZA` |

* **Anti-duplicados**: si el docente ya escaneó en ese evento, el backend responde `409` con un
  mensaje amigable que incluye la hora en que ya registró su ingreso.

* **Cálculo de pagos** (`/api/pagos`, solo Directora): por cada asistencia del rango se
  calcula `bruto = tarifa_por_hora × duración del evento` y, si el docente superó la
  tolerancia, se descuenta `tarifa_por_hora × minutos_tarde / 60` (proporcional a la
  hora no cumplida). Dentro de la tolerancia no se descuenta nada. El apartado
  **Pagos** muestra el resumen por docente, el detalle por asistencia y permite
  exportar a Excel.

---

1. **Inicio / Dashboard** — saludo con el rol, tarjeta del **evento activo actual** (o próximo del
   día), contadores del día, botón grande **“Registrar Asistencia (Escanear QR)”** y accesos
   secundarios: Historial y Reportes, Programar Horarios, Docentes (máx. 40), Pagos y
   Secretaría y Administrativos (solo visible para la Dirección).
2. **Escaneo QR (Optimistic UI)** — al entrar selecciona el bloque horario actual y **precarga en
   memoria los hasta 40 docentes**; al detectar un QR valida al instante en el cliente, muestra la
   tarjeta con **foto grande**, nombre, hora computable y badge verde/ámbar, emite un `beep` suave y
   envía el `POST` en segundo plano. El escaneo es continuo (sin cerrar modales) y cada lectura
   permanece visible unos segundos antes de la siguiente.
3. **Historial y Reportes** — filtros por **Día / Semana / Mes** y por **docente**, tabla ordenada
   por orden de llegada (`hora_escaneo_real ASC`) con numeración `N° Llegada` y botón
   **“Exportar a Excel (.xlsx)”**.
4. **Reportes de fallas por WhatsApp** — botón con el **logo oficial de WhatsApp (SVG)** presente en
   el pie de la app, en la ayuda del dashboard, en la vista pública `/mi-credencial` y en el modal de
   credencial, enlazado a `https://wa.me/51979097247` con el mensaje predefinido
   *"hay un problema con el sistema de asistencia"*, pensado para que los docentes avisen de fallas.

---

## 6. API (resumen)

```
POST   /api/auth/login                 → token JWT + usuario
GET    /api/auth/me
GET    /api/dashboard                  → evento activo, contadores del día
GET    /api/docentes?incluir_inactivos=&q=
GET    /api/docentes/activos           → preload del escáner
POST   /api/docentes                   (DIRECTORA · valida límite 40)
PUT    /api/docentes/{id}              (DIRECTORA · regenera credencial)
DELETE /api/docentes/{id}              (DIRECTORA · baja lógica)
PUT    /api/docentes/{id}/foto         (multipart, recorte cuadrado automático)
GET    /api/docentes/{id}/credencial   → PNG (fotocheck)
GET    /api/docentes/{id}/qr           → PNG del QR
GET    /api/eventos?fecha=|desde=&hasta=&solo_activos=
GET    /api/eventos/activo
POST   /api/eventos                    (espontáneo para ADMIN)
POST   /api/eventos/recurrente         (DIRECTORA)
PUT    /api/eventos/{id} · PUT /api/eventos/{id}/estado
DELETE /api/eventos/{id} · /api/eventos/serie/{grupo_id} · /api/eventos/fecha/{fecha}
POST   /api/asistencias/escaneo        → aplica reglas de llegada y anti-duplicados
GET    /api/asistencias?desde=&hasta=&docente_id=
GET    /api/asistencias/export         → .xlsx
DELETE /api/asistencias/{id}           (DIRECTORA)
GET    /api/pagos?desde=&hasta=&docente_id=   → resumen y detalle de pagos
GET    /api/pagos/export              → .xlsx de pagos
GET    /api/usuarios · POST · PUT · DELETE   (DIRECTORA · la cuenta de Dirección no se puede borrar)
GET    /api/health
```

---

## 7. Exportación a Excel

El archivo generado incluye: **`N° Llegada`, `Fecha`, `Evento`, `Docente`, `Hora Real`,
`Hora Computable`, `Estado`, `Registrado Por`**, con encabezado oscuro, filas alternadas,
`PUNTUAL` en verde, `TARDANZA` en ámbar, fila de totales, filtros automáticos y panel congelado.

---

## 8. Desarrollo local sin Docker (opcional)

```bash
python -m venv .venv && .venv\Scripts\activate         # Windows
pip install -r requirements.txt

# CSS (opcional, ya se compila en Docker)
cd frontend && npm install && cd ..
.\frontend\node_modules\.bin\tailwindcss -c frontend/tailwind.config.js -i app/static/css/input.css -o app/static/css/tailwind.css --minify

set DATABASE_URL=postgresql+asyncpg://asistencia:academia2026@localhost:5432/asistencia
uvicorn app.main:app --reload --port 8000
```

---

## 9. Seguridad y producción

* Cambia `JWT_SECRET` y las contraseñas por defecto.
* Sirve la aplicación detrás de HTTPS (obligatorio para la cámara en móviles).
* El endpoint `GET /api/docentes/{id}/credencial` es público por diseño (lo consume la vista
  `/mi-credencial` que usan los docentes); el resto de la API exige JWT.
* Las fotos y credenciales viven en el volumen Docker `uploads`, la base de datos en `pgdata`.
