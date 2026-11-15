/* =========================================================================
 *  Control de Asistencia Docente · PWA (Vanilla ES6+)
 *  - Dashboard, escaneo QR (Optimistic UI), historial, programación,
 *    docentes, administrativos y exportación a Excel.
 * ========================================================================= */
'use strict';

/* ------------------------------------------------------------------ */
/* Utilidades                                                          */
/* ------------------------------------------------------------------ */
const $ = (sel, raiz = document) => raiz.querySelector(sel);
const $$ = (sel, raiz = document) => Array.from(raiz.querySelectorAll(sel));

function esc(texto) {
  return String(texto ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function hhmmss(valor) {
  // "2026-11-13T15:30:12.345" -> "15:30:12"  |  "16:00:00" -> "16:00:00"
  if (!valor) return '';
  const s = String(valor);
  if (s.includes('T')) return s.slice(11, 19);
  return s.length >= 8 ? s.slice(0, 8) : s.slice(0, 5);
}

function hhmm(iso) {
  if (!iso) return '';
  return String(iso).slice(0, 5);
}

function fechaLarga(iso) {
  const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  return dt.toLocaleDateString('es-PE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}

function fechaCorta(iso) {
  const [y, m, d] = String(iso).slice(0, 10).split('-');
  return `${d}/${m}/${y}`;
}

function hoyISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function sumarDias(iso, dias) {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + dias);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

function finDeMes(iso) {
  const [y, m] = iso.split('-').map(Number);
  return `${y}-${String(m).padStart(2, '0')}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`;
}

function minutosDe(hhmmStr) {
  const [h, m] = String(hhmmStr).split(':').map(Number);
  return (h || 0) * 60 + (m || 0);
}

/** Valor de un <input type="time"> normalizado a "HH:MM" (algunos navegadores
 *  devuelven "HH:MM:SS" y al concatenar ":00" se generaría un valor inválido). */
function valorHora(sel) {
  const v = String($(sel).value || '').trim();
  return v.length >= 5 ? v.slice(0, 5) : v;
}

function toast(mensaje, tipo = 'info', ms = 3800) {
  const cont = $('#toasts');
  if (!cont) return;
  const estilos = {
    info: 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900',
    ok: 'bg-emerald-600 text-white',
    error: 'bg-rose-600 text-white',
    warn: 'bg-amber-500 text-slate-900',
  }[tipo] || 'bg-slate-900 text-white';
  const el = document.createElement('div');
  el.className = `pointer-events-auto max-w-md animate-pop rounded-xl px-4 py-3 text-sm font-semibold shadow-lg ${estilos}`;
  el.textContent = mensaje;
  cont.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .3s, transform .3s';
    el.style.opacity = '0';
    el.style.transform = 'translateY(-8px)';
    setTimeout(() => el.remove(), 320);
  }, ms);
}

/* ------------------------------------------------------------------ */
/* Cliente HTTP                                                        */
/* ------------------------------------------------------------------ */
const SESION_KEY = 'asistencia_sesion';
let TOKEN = null;
let USER = null;

function leerSesion() {
  try {
    const raw = localStorage.getItem(SESION_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (!data || !data.token) return null;
    return data;
  } catch (e) {
    return null;
  }
}

function guardarSesion(token, usuario) {
  TOKEN = token;
  USER = usuario;
  try {
    localStorage.setItem(SESION_KEY, JSON.stringify({ token, usuario }));
  } catch (e) {
    /* Navegación privada / almacenamiento bloqueado: la sesión vive en memoria. */
  }
}

function cerrarSesion(redirigir = true) {
  TOKEN = null;
  USER = null;
  try {
    localStorage.removeItem(SESION_KEY);
  } catch (e) {}
  if (redirigir) window.location.href = '/logout';
}

async function api(ruta, opciones = {}) {
  const headers = {};
  if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
  let body;
  if (opciones.form) {
    body = opciones.form;
  } else if (opciones.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opciones.body);
  }
  const resp = await fetch(ruta, { method: opciones.method || 'GET', headers, body });
  const tipo = resp.headers.get('content-type') || '';
  let data = null;
  if (tipo.includes('application/json')) {
    data = await resp.json().catch(() => null);
  } else if (opciones.blob) {
    data = await resp.blob();
  } else {
    data = await resp.text();
  }
  if (!resp.ok) {
    const detalle = (data && (data.detail || data.message)) || `Error ${resp.status}`;
    // Un 401 en el propio login NO es una sesión expirada: es una contraseña
    // incorrecta y la pantalla de login debe mostrar el mensaje, no redirigir.
    const esLogin = ruta === '/api/auth/login';
    if (resp.status === 401 && !esLogin && location.pathname !== '/login') {
      cerrarSesion();
    }
    const err = new Error(typeof detalle === 'string' ? detalle : 'Solicitud rechazada');
    err.status = resp.status;
    throw err;
  }
  return data;
}

/* ------------------------------------------------------------------ */
/* Tema claro / oscuro                                                 */
/* ------------------------------------------------------------------ */
function pintarTema(oscuro) {
  document.documentElement.classList.toggle('dark', oscuro);
  // La barra del navegador/PWA también sigue al tema
  const meta = $('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', oscuro ? '#0f172a' : '#ffffff');
  const btn = $('#temaToggle');
  if (btn) {
    btn.setAttribute('aria-pressed', oscuro ? 'true' : 'false');
    btn.title = oscuro ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro';
  }
}

function temaGuardado() {
  try {
    const t = localStorage.getItem('tema');
    if (t === 'dark') return true;
    if (t === 'light') return false;
  } catch (e) {}
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

let _temaListo = false;

function iniciarTema() {
  // Idempotente: solo engancha el botón una vez por carga.
  if (_temaListo) return;
  const btn = $('#temaToggle');
  if (!btn) return;
  _temaListo = true;

  pintarTema(document.documentElement.classList.contains('dark'));
  btn.addEventListener('click', () => {
    const oscuro = !document.documentElement.classList.contains('dark');
    pintarTema(oscuro);
    try { localStorage.setItem('tema', oscuro ? 'dark' : 'light'); } catch (e) {}
  });
}

/* ------------------------------------------------------------------ */
/* Sonido de confirmación                                              */
/* ------------------------------------------------------------------ */
let _audioCtx = null;
function audioCtx() {
  if (!_audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    _audioCtx = new Ctx();
  }
  if (_audioCtx.state === 'suspended') _audioCtx.resume().catch(() => {});
  return _audioCtx;
}

function beep(ok = true) {
  const ctx = audioCtx();
  if (!ctx) return;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = 'sine';
  osc.frequency.value = ok ? 988 : 240;
  const t = ctx.currentTime;
  gain.gain.setValueAtTime(0.0001, t);
  gain.gain.exponentialRampToValueAtTime(ok ? 0.18 : 0.22, t + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + (ok ? 0.20 : 0.42));
  osc.connect(gain).connect(ctx.destination);
  osc.start(t);
  osc.stop(t + (ok ? 0.24 : 0.46));
}

/* ------------------------------------------------------------------ */
/* Página de LOGIN                                                     */
/* ------------------------------------------------------------------ */
function iniciarLogin() {
  const form = $('#formLogin');
  if (!form) return;
  const existente = leerSesion();
  if (existente) {
    window.location.href = '/';
    return;
  }

  $('#verPassword').addEventListener('click', () => {
    const inp = $('#password');
    inp.type = inp.type === 'password' ? 'text' : 'password';
    $('#verPassword').textContent = inp.type === 'password' ? 'Ver' : 'Ocultar';
  });

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = $('#btnLogin');
    const errBox = $('#loginError');
    errBox.classList.add('hidden');
    btn.disabled = true;
    btn.textContent = 'Ingresando…';
    try {
      const data = await api('/api/auth/login', {
        method: 'POST',
        body: { email: $('#email').value.trim(), password: $('#password').value },
      });
      guardarSesion(data.access_token, data.usuario);
      window.location.href = '/';
    } catch (e) {
      errBox.textContent = e.message;
      errBox.classList.remove('hidden');
      beep(false);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Ingresar';
    }
  });
}

/* ==================================================================== */
/* APP                                                                  */
/* ==================================================================== */
const state = {
  docentes: [],
  docentesTodos: [],
  credenciales: [],
  eventoActivo: null,
  eventosHoy: [],
  escaneosSesion: [],
  escaneados: new Set(),
  vista: 'dashboard',
  escaneando: false,
};

const VISTAS = ['dashboard', 'escanear', 'historial', 'pagos', 'programar', 'docentes', 'credenciales', 'usuarios'];

const esDirectora = () => !!USER && USER.rol === 'DIRECTORA';

function iniciarApp() {
  const shell = $('#view-dashboard');
  if (!shell) return;

  const sesion = leerSesion();
  if (!sesion) {
    window.location.href = '/login';
    return;
  }
  TOKEN = sesion.token;
  USER = sesion.usuario || { nombre: 'Usuario', rol: 'ADMIN' };

  pintarUsuario();
  aplicarPermisos();
  enlazarNavegacion();
  enlazarAcciones();

  api('/api/auth/me')
    .then((u) => {
      USER = u;
      guardarSesion(TOKEN, u);
      pintarUsuario();
      aplicarPermisos();
    })
    .catch(() => {});

  enrutar();
}

const ETIQUETAS_ROL = { DIRECTORA: 'Dirección', ADMIN: 'Secretaría' };

function etiquetaRol(rol) {
  return ETIQUETAS_ROL[rol] || rol || '—';
}

function pintarUsuario() {
  $('#userNombre').textContent = USER.nombre || '—';
  $('#userRol').textContent = etiquetaRol(USER.rol);
  $('#userAvatar').textContent = (USER.nombre || 'U').trim().slice(0, 1).toUpperCase();
}

function aplicarPermisos() {
  const directora = esDirectora();
  $$('[data-solo-directora]').forEach((el) => {
    if (directora) el.removeAttribute('hidden');
    else el.setAttribute('hidden', 'hidden');
  });
}

function enlazarNavegacion() {
  $$('[data-nav]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const destino = btn.dataset.nav;
      if (location.hash === `#/${destino}`) enrutar();
      else location.hash = `#/${destino}`;
    });
  });
  window.addEventListener('hashchange', enrutar);
  $('#btnSalir').addEventListener('click', () => cerrarSesion());
}

function mostrarVista(vista) {
  VISTAS.forEach((v) => {
    const sec = $(`#view-${v}`);
    if (!sec) return;
    const activa = v === vista;
    sec.classList.toggle('view-active', activa);
    sec.classList.toggle('view', !activa);
  });
  $$('[data-nav]').forEach((btn) => {
    btn.classList.toggle('nav-pill-active', btn.dataset.nav === vista);
  });
  state.vista = vista;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function enrutar() {
  const vista = (location.hash.replace('#/', '') || 'dashboard').split('?')[0];
  const destino = VISTAS.includes(vista) ? vista : 'dashboard';
  if (destino !== 'escanear') Scanner.detener();

  mostrarVista(destino);
  try {
    if (destino === 'dashboard') await cargarDashboard();
    else if (destino === 'escanear') await cargarEscaner();
    else if (destino === 'historial') await cargarHistorial();
    else if (destino === 'pagos') await cargarPagos();
    else if (destino === 'programar') await cargarProgramacion();
    else if (destino === 'docentes') await cargarDocentes();
    else if (destino === 'credenciales') await cargarCredenciales();
    else if (destino === 'usuarios') await cargarUsuarios();
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* ------------------------------------------------------------------ */
/* Dashboard                                                           */
/* ------------------------------------------------------------------ */
async function cargarDashboard() {
  const d = await api('/api/dashboard');
  state.eventoActivo = d.evento_activo;
  state.eventosHoy = d.eventos_hoy || [];

  $('#dashSaludo').textContent = `Hola, ${(d.usuario.nombre || '').split(' ')[0] || 'bienvenido'} 👋`;
  $('#dashFecha').textContent = fechaLarga(d.fecha);
  $('#statDocentes').textContent = d.total_docentes_activos;
  $('#statAsistencias').textContent = d.asistencias_hoy;
  $('#statPuntuales').textContent = d.puntuales_hoy;
  $('#statTardanzas').textContent = d.tardanzas_hoy;

  const caja = $('#dashEventoActivo');
  const badge = $('#dashEventoBadge');

  if (d.evento_activo) {
    const ev = d.evento_activo;
    badge.textContent = 'En curso';
    badge.className = 'badge-puntual';
    caja.innerHTML = `
      <p class="text-lg font-bold text-slate-900 dark:text-white">${esc(ev.titulo)}</p>
      <p class="text-sm font-medium text-slate-500 dark:text-slate-400">
        ${hhmm(ev.hora_inicio)} – ${hhmm(ev.hora_fin)} · tolerancia ${ev.tolerancia_minutos} min
      </p>
      <div class="flex flex-wrap gap-2 pt-1">
        <span class="badge-neutral">${esc(ev.tipo_etiqueta || (ev.es_espontaneo ? 'Espontáneo' : 'Programado'))}</span>
        <span class="badge-neutral">${d.asistencias_hoy} registros hoy</span>
      </div>`;
  } else if (d.proximo_evento) {
    const ev = d.proximo_evento;
    badge.textContent = 'Próximo';
    badge.className = 'badge-tardanza';
    caja.innerHTML = `
      <p class="text-lg font-bold text-slate-900 dark:text-white">${esc(ev.titulo)}</p>
      <p class="text-sm font-medium text-slate-500 dark:text-slate-400">
        Inicia a las ${hhmm(ev.hora_inicio)} · tolerancia ${ev.tolerancia_minutos} min
      </p>
      <p class="text-xs font-medium text-amber-600 dark:text-amber-400">
        Los docentes que lleguen antes se computan como PUNTUALES a la hora de inicio.
      </p>`;
  } else {
    badge.textContent = 'Sin evento';
    badge.className = 'badge-neutral';
    caja.innerHTML = `
      <p class="text-sm font-medium text-slate-500 dark:text-slate-400">
        No hay bloques horarios activos ni programados para hoy.
      </p>
      <button data-nav="programar" class="btn-soft mt-2 w-full">Programar un horario</button>`;
    $$('[data-nav]', caja).forEach((b) => b.addEventListener('click', () => (location.hash = `#/${b.dataset.nav}`)));
  }

  const lista = $('#dashEventosHoy');
  if (!state.eventosHoy.length) {
    lista.innerHTML = `<p class="text-sm text-slate-400">Sin programación para hoy.</p>`;
  } else {
    lista.innerHTML = state.eventosHoy
      .map(
        (ev) => `
      <div class="flex items-center justify-between gap-3 rounded-xl border border-slate-200 px-3 py-2 dark:border-slate-800">
        <div class="min-w-0">
          <p class="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">${esc(ev.titulo)}</p>
          <p class="text-xs text-slate-500 dark:text-slate-400">${hhmm(ev.hora_inicio)} – ${hhmm(ev.hora_fin)} · tol. ${ev.tolerancia_minutos} min</p>
        </div>
        <span class="${ev.es_espontaneo ? 'badge-tardanza' : 'badge-neutral'}">${ev.es_espontaneo ? 'Espontáneo' : 'Programado'}</span>
      </div>`
      )
      .join('');
  }
}

/* ------------------------------------------------------------------ */
/* Escaneo QR                                                          */
/* ------------------------------------------------------------------ */
async function cargarEscaner() {
  const [docentes, evento] = await Promise.all([
    api('/api/docentes/activos'),
    api('/api/eventos/activo').catch(() => null),
  ]);
  state.docentes = docentes;
  state.eventoActivo = evento;
  pintarBarraEvento();
  pintarRecientes();
}

function pintarBarraEvento() {
  const caja = $('#scanEventoInfo');
  const ev = state.eventoActivo;
  if (!ev) {
    caja.innerHTML = `
      <span class="badge-tardanza">Sin bloque activo</span>
      <span class="ml-2 text-xs font-medium">Programa un horario antes de escanear.</span>`;
    return;
  }
  caja.innerHTML = `
    <span class="font-bold text-slate-800 dark:text-slate-100">${esc(ev.titulo)}</span>
    <span class="ml-1">· ${hhmm(ev.hora_inicio)}–${hhmm(ev.hora_fin)} · tol. ${ev.tolerancia_minutos} min</span>
    <span class="ml-2 badge-neutral">${state.docentes.length} docentes en memoria</span>`;
}

function pintarRecientes() {
  const caja = $('#scanRecientes');
  const vacio = $('#scanRecientesVacio');
  const total = state.escaneosSesion.length;
  $('#scanContador').textContent = `${total} registro${total === 1 ? '' : 's'}`;
  if (!total) {
    caja.innerHTML = '';
    vacio.hidden = false;
    return;
  }
  vacio.hidden = true;
  caja.innerHTML = state.escaneosSesion
    .slice(0, 8)
    .map(
      (r) => `
    <div class="flex items-center gap-3 rounded-xl border border-slate-200 p-2 dark:border-slate-800">
      <img src="${esc(r.foto || '/static/img/icon-192.png')}" alt="" class="h-10 w-10 rounded-lg object-cover" onerror="this.style.visibility='hidden'" />
      <div class="min-w-0 flex-1">
        <p class="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">${esc(r.nombre)}</p>
        <p class="text-xs text-slate-500 dark:text-slate-400">${esc(r.hora)} · computable ${esc(r.computable)}</p>
      </div>
      <span class="${r.estado === 'TARDANZA' ? 'badge-tardanza' : 'badge-puntual'}">${esc(r.estado)}</span>
    </div>`
    )
    .join('');
}

function extraerToken(texto) {
  const txt = String(texto || '').trim();
  const m = txt.match(/token=([A-Za-z0-9-]+)/i);
  if (m) return m[1].toUpperCase();
  const solo = txt.match(/(DOC-[A-Z0-9]+)/i);
  if (solo) return solo[1].toUpperCase();
  return txt.toUpperCase();
}

/** Cálculo local (Optimistic UI): idéntico a la regla del backend. */
function calcularLocal(evento, ahora = new Date()) {
  const ahoraSeg = ahora.getHours() * 3600 + ahora.getMinutes() * 60 + ahora.getSeconds();
  const iniSeg = minutosDe(hhmm(evento.hora_inicio)) * 60;
  const tol = Number(evento.tolerancia_minutos || 0) * 60;
  const reloj = `${String(ahora.getHours()).padStart(2, '0')}:${String(ahora.getMinutes()).padStart(2, '0')}:${String(
    ahora.getSeconds()
  ).padStart(2, '0')}`;
  if (ahoraSeg < iniSeg) {
    return { computable: `${hhmm(evento.hora_inicio)}:00`, estado: 'PUNTUAL', anticipado: true };
  }
  if (ahoraSeg <= iniSeg + tol) {
    return { computable: reloj, estado: 'PUNTUAL', anticipado: false };
  }
  return { computable: reloj, estado: 'TARDANZA', anticipado: false };
}

function mostrarTarjetaEscaneo({ docente, calculo, extra = null, estadoTarjeta = 'optimista' }) {
  const caja = $('#scanResultado');
  const foto = extra?.docente_foto || docente.foto_path || '/static/img/icon-192.png';
  const badge = calculo.estado === 'TARDANZA' ? 'badge-tardanza' : 'badge-puntual';
  const borde =
    estadoTarjeta === 'error'
      ? 'border-rose-400 bg-rose-50 dark:bg-rose-950/80'
      : estadoTarjeta === 'duplicado'
      ? 'border-amber-400 bg-amber-50 dark:bg-amber-950/80'
      : 'border-emerald-400 bg-white/95 dark:bg-slate-900/95';

  caja.className = `absolute inset-x-3 bottom-3 z-20 animate-pop rounded-2xl border-2 ${borde} p-3 shadow-2xl`;
  caja.classList.remove('hidden');
  caja.innerHTML = `
    <div class="flex items-center gap-3">
      <img src="${esc(foto)}" alt="" class="h-20 w-20 shrink-0 rounded-xl object-cover ring-2 ring-emerald-500/40"
           onerror="this.src='/static/img/icon-192.png'" />
      <div class="min-w-0 flex-1">
        <p class="truncate text-base font-extrabold text-slate-900 dark:text-white">${esc(docente.nombre_completo)}</p>
        <p class="truncate text-xs font-medium text-slate-500 dark:text-slate-400">${esc(docente.especialidad || 'Docente')}</p>
        <div class="mt-1 flex flex-wrap items-center gap-2">
          <span class="${badge}">${esc(calculo.estado)}</span>
          <span class="text-xs font-semibold text-slate-600 dark:text-slate-300">Computable ${esc(calculo.computable)}</span>
        </div>
        <p class="mt-1 text-[11px] font-medium ${
          estadoTarjeta === 'error' ? 'text-rose-600 dark:text-rose-300' : 'text-slate-500 dark:text-slate-400'
        }">${esc(
          estadoTarjeta === 'optimista'
            ? calculo.anticipado
              ? 'Llegada anticipada: se computa a la hora de inicio. Registrando…'
              : 'Registrando en el servidor…'
            : estadoTarjeta === 'ok'
            ? '✔ Asistencia registrada correctamente.'
            : ''
        )}</p>
      </div>
    </div>`;

  clearTimeout(mostrarTarjetaEscaneo._t);
  mostrarTarjetaEscaneo._t = setTimeout(() => {
    caja.classList.add('hidden');
  }, estadoTarjeta === 'optimista' ? 6000 : 4200);
}

async function procesarEscaneo(texto) {
  const token = extraerToken(texto);
  if (!token) return;

  const ahora = Date.now();
  if (procesarEscaneo._ultimo === token && ahora - (procesarEscaneo._t || 0) < 2500) return;
  procesarEscaneo._ultimo = token;
  procesarEscaneo._t = ahora;

  const docente = state.docentes.find((d) => d.qr_token === token);
  if (!docente) {
    beep(false);
    mostrarTarjetaEscaneo({
      docente: { nombre_completo: 'Código no reconocido', especialidad: token, foto_path: '' },
      calculo: { estado: 'TARDANZA', computable: hhmmss(new Date().toISOString()) },
      estadoTarjeta: 'error',
    });
    $('#scanResultado').innerHTML = `
      <div class="flex items-center gap-3">
        <div class="flex h-20 w-20 shrink-0 items-center justify-center rounded-xl bg-rose-600 text-2xl font-black text-white">!</div>
        <div class="min-w-0">
          <p class="text-base font-extrabold text-rose-700 dark:text-rose-200">Credencial no válida</p>
          <p class="text-xs font-medium text-rose-600 dark:text-rose-300">Código leído: ${esc(token)}</p>
          <p class="mt-1 text-[11px] text-rose-500">Verifica con Dirección o usa el ingreso manual.</p>
        </div>
      </div>`;
    return;
  }

  if (state.escaneados.has(docente.qr_token)) {
    beep(false);
    mostrarTarjetaEscaneo({
      docente,
      calculo: { estado: 'PUNTUAL', computable: '—' },
      estadoTarjeta: 'duplicado',
    });
    $('#scanResultado').innerHTML = `
      <div class="flex items-center gap-3">
        <img src="${esc(docente.foto_path)}" alt="" class="h-20 w-20 shrink-0 rounded-xl object-cover opacity-60" />
        <div class="min-w-0">
          <p class="truncate text-base font-extrabold text-amber-700 dark:text-amber-200">${esc(docente.nombre_completo)}</p>
          <p class="text-xs font-bold text-amber-700 dark:text-amber-300">Ya registró su ingreso en este bloque horario.</p>
          <p class="mt-1 text-[11px] text-amber-600 dark:text-amber-300">El servidor validará el duplicado de todas formas.</p>
        </div>
      </div>`;
    return;
  }

  // ---- Optimistic UI: validación en memoria + tarjeta inmediata ----
  const evento = state.eventoActivo;
  const calculo = evento
    ? calcularLocal(evento)
    : { computable: hhmmss(new Date().toISOString()), estado: 'PUNTUAL', anticipado: false };

  mostrarTarjetaEscaneo({ docente, calculo, estadoTarjeta: 'optimista' });
  beep(true);
  state.escaneados.add(docente.qr_token);

  // ---- POST en segundo plano ----
  try {
    const res = await api('/api/asistencias/escaneo', {
      method: 'POST',
      body: { qr_token: token, evento_id: evento ? evento.id : null },
    });
    const a = res.asistencia;
    mostrarTarjetaEscaneo({
      docente: res.docente,
      calculo: { estado: a.estado, computable: hhmmss(a.hora_computable), anticipado: false },
      extra: a,
      estadoTarjeta: 'ok',
    });
    state.escaneosSesion.unshift({
      nombre: a.docente_nombre,
      foto: a.docente_foto || res.docente.foto_path,
      hora: hhmmss(a.hora_escaneo_real),
      computable: hhmmss(a.hora_computable),
      estado: a.estado,
    });
    pintarRecientes();
  } catch (e) {
    state.escaneados.delete(docente.qr_token);
    beep(false);
    if (e.status === 409) {
      mostrarTarjetaEscaneo({
        docente,
        calculo: { estado: 'PUNTUAL', computable: '—' },
        estadoTarjeta: 'duplicado',
      });
      $('#scanResultado').innerHTML = `
        <div class="flex items-center gap-3">
          <img src="${esc(docente.foto_path)}" alt="" class="h-20 w-20 shrink-0 rounded-xl object-cover opacity-60" />
          <div class="min-w-0">
            <p class="truncate text-base font-extrabold text-amber-700 dark:text-amber-200">${esc(docente.nombre_completo)}</p>
            <p class="text-xs font-bold text-amber-700 dark:text-amber-300">${esc(e.message)}</p>
          </div>
        </div>`;
      state.escaneados.add(docente.qr_token);
      toast(e.message, 'warn');
    } else {
      $('#scanResultado').innerHTML = `
        <div class="flex items-center gap-3">
          <div class="flex h-20 w-20 shrink-0 items-center justify-center rounded-xl bg-rose-600 text-2xl font-black text-white">!</div>
          <div class="min-w-0">
            <p class="text-base font-extrabold text-rose-700 dark:text-rose-200">No se pudo registrar</p>
            <p class="text-xs font-medium text-rose-600 dark:text-rose-300">${esc(e.message)}</p>
          </div>
        </div>`;
      toast(e.message, 'error');
    }
  }
}

/* --------------------------- Scanner ------------------------------ */
const Scanner = {
  modo: null,
  stream: null,
  video: null,
  detector: null,
  html5: null,
  raf: null,
  ultimoFrame: 0,
  intervalo: 66, // ~15 fps

  async iniciar() {
    if (state.escaneando) return;
    const viewport = $('#scanViewport');
    $('#scanPlaceholder').hidden = true;
    audioCtx(); // desbloquea el audio en móviles

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast('Este navegador no permite acceder a la cámara. Usa HTTPS o localhost.', 'error');
      $('#scanPlaceholder').hidden = false;
      return;
    }

    // 1) Vía preferente: BarcodeDetector nativo (máxima velocidad)
    if ('BarcodeDetector' in window) {
      try {
        const soportados = await window.BarcodeDetector.getSupportedFormats();
        if (soportados.includes('qr_code')) {
          this.detector = new window.BarcodeDetector({ formats: ['qr_code'] });
          this.stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
            audio: false,
          });
          viewport.innerHTML = '';
          const video = document.createElement('video');
          video.setAttribute('playsinline', 'true');
          video.setAttribute('muted', 'true');
          video.autoplay = true;
          video.className = 'h-full w-full object-cover';
          viewport.appendChild(video);
          video.srcObject = this.stream;
          await video.play();
          this.video = video;
          this.modo = 'nativo';
          this.loopNativo();
          this.activarUI(true, 'Escaneando (nativo)');
          return;
        }
      } catch (e) {
        this.limpiarStream();
      }
    }

    // 2) Respaldo: html5-qrcode con fps 15 y qrbox 250x250
    try {
      if (typeof Html5Qrcode === 'undefined') {
        throw new Error('La librería del escáner no está disponible.');
      }
      viewport.innerHTML = '';
      const cont = document.createElement('div');
      cont.id = 'qr-reader';
      cont.className = 'h-full w-full';
      viewport.appendChild(cont);
      this.html5 = new Html5Qrcode('qr-reader', {
        verbose: false,
        experimentalFeatures: { useBarCodeDetectorIfSupported: true },
      });
      await this.html5.start(
        { facingMode: 'environment' },
        { fps: 15, qrbox: { width: 250, height: 250 }, aspectRatio: 1.0 },
        (texto) => procesarEscaneo(texto),
        () => {}
      );
      this.modo = 'html5';
      this.activarUI(true, 'Escaneando');
    } catch (e) {
      this.modo = null;
      $('#scanPlaceholder').hidden = false;
      viewport.innerHTML = '';
      this.activarUI(false, 'Detenido');
      toast('No se pudo iniciar la cámara: ' + (e.message || e), 'error');
    }
  },

  loopNativo() {
    const paso = async (ts) => {
      if (this.modo !== 'nativo') return;
      this.raf = requestAnimationFrame(paso);
      if (ts - this.ultimoFrame < this.intervalo) return;
      this.ultimoFrame = ts;
      try {
        const codigos = await this.detector.detect(this.video);
        if (codigos && codigos.length) {
          const valor = codigos[0].rawValue || '';
          if (valor) procesarEscaneo(valor);
        }
      } catch (e) {
        /* frame no válido: se ignora */
      }
    };
    this.raf = requestAnimationFrame(paso);
  },

  async detener() {
    if (!state.escaneando) return;
    if (this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = null;
    }
    if (this.html5) {
      try {
        await this.html5.stop();
        await this.html5.clear();
      } catch (e) {}
      this.html5 = null;
    }
    this.limpiarStream();
    this.modo = null;
    this.activarUI(false, 'Detenido');
  },

  limpiarStream() {
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
    }
    if (this.video) {
      this.video.srcObject = null;
      this.video = null;
    }
    const viewport = $('#scanViewport');
    if (viewport) viewport.innerHTML = '';
  },

  activarUI(activo, etiqueta) {
    state.escaneando = activo;
    $('#scanEstado').textContent = etiqueta;
    $('#scanEstado').className = activo ? 'badge-puntual' : 'badge-neutral';
    $('#scanStart').disabled = activo;
    $('#scanStop').disabled = !activo;
    $('#scanPlaceholder').hidden = activo;
  },
};

/* ------------------------------------------------------------------ */
/* Historial                                                           */
/* ------------------------------------------------------------------ */
function rangoActivo() {
  return { desde: $('#fDesde').value, hasta: $('#fHasta').value };
}

async function cargarHistorial() {
  if (!$('#fDesde').value) {
    $('#fDesde').value = hoyISO();
    $('#fHasta').value = hoyISO();
  }
  await llenarSelectDocentes();
  await consultarHistorial();
}

async function llenarSelectDocentes() {
  const select = $('#fDocente');
  if (!select) return;
  const seleccionado = select.value;
  try {
    const docentes = await api('/api/docentes?incluir_inactivos=true');
    state.docentesTodos = docentes;
    select.innerHTML =
      '<option value="">Todos los docentes</option>' +
      docentes
        .map((d) => `<option value="${d.id}">${esc(d.nombre_completo || `${d.apellidos}, ${d.nombres}`)}</option>`)
        .join('');
    // Si el filtro aplicado apuntaba a un docente que sigue existiendo, se conserva.
    if (seleccionado && docentes.some((d) => String(d.id) === String(seleccionado))) {
      select.value = seleccionado;
    }
    select.dataset.listado = '1';
  } catch (e) {
    /* silencioso: se reintenta en la siguiente consulta */
  }
}

async function consultarHistorial() {
  const { desde, hasta } = rangoActivo();
  const params = new URLSearchParams();
  if (desde) params.set('desde', desde);
  if (hasta) params.set('hasta', hasta);
  if ($('#fDocente').value) params.set('docente_id', $('#fDocente').value);

  const registros = await api(`/api/asistencias?${params.toString()}`);
  const tbody = $('#histTabla');
  $('#histVacio').hidden = registros.length > 0;
  $('#hTotal').textContent = registros.length;
  $('#hPuntuales').textContent = registros.filter((r) => r.estado === 'PUNTUAL').length;
  $('#hTardanzas').textContent = registros.filter((r) => r.estado === 'TARDANZA').length;

  tbody.innerHTML = registros
    .map(
      (r) => `
    <tr>
      <td class="td font-bold tabular-nums">${r.n_llegada}</td>
      <td class="td whitespace-nowrap">${fechaCorta(r.fecha)}</td>
      <td class="td">${esc(r.evento_titulo)}</td>
      <td class="td">
        <div class="flex items-center gap-2">
          <img src="${esc(r.docente_foto)}" alt="" class="h-8 w-8 rounded-lg object-cover"
               onerror="this.style.visibility='hidden'" />
          <div class="min-w-0">
            <p class="truncate font-semibold text-slate-800 dark:text-slate-100">${esc(r.docente_nombre)}</p>
            <p class="truncate text-xs text-slate-400">${esc(r.docente_especialidad || '')}</p>
          </div>
        </div>
      </td>
      <td class="td tabular-nums">${hhmmss(r.hora_escaneo_real)}</td>
      <td class="td tabular-nums font-semibold">${hhmmss(r.hora_computable)}</td>
      <td class="td"><span class="${r.estado === 'TARDANZA' ? 'badge-tardanza' : 'badge-puntual'}">${esc(r.estado)}</span></td>
      <td class="td text-xs">${esc(r.registrado_por)}</td>
      ${
        esDirectora()
          ? `<td class="td text-right">
               <button class="btn-soft !px-2 !py-1 text-xs" data-eliminar-asistencia="${r.id}">Eliminar</button>
             </td>`
          : ''
      }
    </tr>`
    )
    .join('');

  $$('[data-eliminar-asistencia]', tbody).forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('¿Eliminar este registro de asistencia? Quedará en el historial del sistema.')) return;
      try {
        await api(`/api/asistencias/${btn.dataset.eliminarAsistencia}`, { method: 'DELETE' });
        toast('Registro eliminado.', 'ok');
        consultarHistorial();
      } catch (e) {
        toast(e.message, 'error');
      }
    });
  });
}

async function descargarExcel() {
  const { desde, hasta } = rangoActivo();
  const params = new URLSearchParams();
  if (desde) params.set('desde', desde);
  if (hasta) params.set('hasta', hasta);
  if ($('#fDocente').value) params.set('docente_id', $('#fDocente').value);
  try {
    const blob = await api(`/api/asistencias/export?${params.toString()}`, { blob: true });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `asistencias_${desde || hoyISO()}${hasta && hasta !== desde ? '_a_' + hasta : ''}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast('Excel generado correctamente.', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* ------------------------------------------------------------------ */
/* Pagos                                                               */
/* ------------------------------------------------------------------ */
function soles(valor) {
  return `S/ ${Number(valor || 0).toFixed(2)}`;
}

async function cargarPagos() {
  if (!$('#pagoDesde').value) $('#pagoDesde').value = `${hoyISO().slice(0, 7)}-01`;
  if (!$('#pagoHasta').value) $('#pagoHasta').value = hoyISO();

  const params = new URLSearchParams({
    desde: $('#pagoDesde').value,
    hasta: $('#pagoHasta').value,
  });
  if ($('#pagoDocente').value) params.set('docente_id', $('#pagoDocente').value);

  const [datos, docentes] = await Promise.all([
    api(`/api/pagos?${params.toString()}`),
    llenarSelectPagos(),
  ]);
  pintarPagos(datos, docentes);
}

async function llenarSelectPagos() {
  const select = $('#pagoDocente');
  if (!select) return [];
  const actual = select.value;
  const docentes = await api('/api/docentes?incluir_inactivos=true');
  select.innerHTML =
    '<option value="">Todos los docentes</option>' +
    docentes
      .map(
        (d) =>
          `<option value="${d.id}" ${String(d.id) === actual ? 'selected' : ''}>${esc(
            d.nombre_completo
          )}</option>`
      )
      .join('');
  return docentes;
}

function pintarPagos(datos, docentes = []) {
  const t = datos.totales || {};
  $('#pagoNeto').textContent = soles(t.neto);
  $('#pagoBruto').textContent = soles(t.bruto);
  $('#pagoDescuento').textContent = soles(t.descuento);
  $('#pagoAsistencias').textContent = `${t.asistencias || 0} / ${t.docentes || 0}`;
  $('#pagoPeriodo').textContent = `${fechaCorta(datos.desde)} – ${fechaCorta(datos.hasta)}`;

  const aviso = $('#pagoAvisoTarifa');
  const sinTarifa = docentes.filter((d) => Number(d.tarifa_por_hora || 0) <= 0);
  if (sinTarifa.length) {
    aviso.classList.remove('hidden');
    aviso.innerHTML = `⚠️ <strong>${sinTarifa.length}</strong> docente(s) no tienen tarifa por hora configurada. Escribe su tarifa en <strong>Docentes → Editar</strong> o usa el botón <strong>Tarifa</strong> de la tabla para que el cálculo sea correcto.`;
  } else {
    aviso.classList.add('hidden');
    aviso.innerHTML = '';
  }

  const tbody = $('#pagoResumen');
  const resumen = datos.resumen || [];
  $('#pagoResumenVacio').hidden = resumen.length > 0;
  tbody.innerHTML = resumen
    .map(
      (r) => `
    <tr>
      <td class="td">
        <div class="flex items-center gap-2">
          <img src="${esc(r.foto_path || '/static/img/icon-192.png')}" alt="" class="h-9 w-9 shrink-0 rounded-lg object-cover ring-1 ring-slate-200 dark:ring-slate-800"
               onerror="this.style.visibility='hidden'" />
          <div class="min-w-0">
            <p class="truncate font-semibold text-slate-900 dark:text-white">${esc(r.nombre)}</p>
            <p class="truncate text-xs text-slate-500 dark:text-slate-400">${esc(r.especialidad || 'Sin especialidad')}</p>
          </div>
        </div>
      </td>
      <td class="td whitespace-nowrap text-xs font-semibold ${r.tarifa_por_hora > 0 ? '' : 'text-rose-500'}">${
        r.tarifa_por_hora > 0 ? soles(r.tarifa_por_hora) : 'Sin tarifa'
      }</td>
      <td class="td text-xs">${r.asistencias} <span class="text-slate-400">(${r.puntuales}P / ${r.tardanzas}T)</span></td>
      <td class="td text-xs ${r.tardanzas ? 'text-amber-600 dark:text-amber-400' : ''}">${r.tardanzas}</td>
      <td class="td text-xs">${r.minutos_tarde}</td>
      <td class="td whitespace-nowrap text-xs">${soles(r.bruto)}</td>
      <td class="td whitespace-nowrap text-xs font-semibold ${
        r.descuento > 0 ? 'text-amber-600 dark:text-amber-400' : 'text-slate-400'
      }">
        ${soles(r.descuento)}
        ${r.bruto > 0 ? `<span class="text-[10px] text-slate-400">(${r.descuento_porcentaje.toFixed(1)}%)</span>` : ''}
      </td>
      <td class="td whitespace-nowrap text-xs font-bold text-emerald-600 dark:text-emerald-400">${soles(r.neto)}</td>
      <td class="td text-right whitespace-nowrap">
        <button class="btn-soft !px-2 !py-1 text-xs" data-editar-tarifa="${r.docente_id}"
                data-nombre="${esc(r.nombre)}" data-tarifa="${r.tarifa_por_hora}">Tarifa</button>
      </td>
    </tr>`
    )
    .join('');

  $$('[data-editar-tarifa]', tbody).forEach((btn) => {
    btn.addEventListener('click', () =>
      abrirModalTarifa(
        Number(btn.dataset.editarTarifa),
        btn.dataset.nombre,
        Number(btn.dataset.tarifa || 0)
      )
    );
  });

  const tbodyDetalle = $('#pagoDetalle');
  const detalle = datos.detalle || [];
  $('#pagoDetalleVacio').hidden = detalle.length > 0;
  tbodyDetalle.innerHTML = detalle
    .map(
      (f) => `
    <tr>
      <td class="td whitespace-nowrap text-xs">${fechaCorta(f.fecha)}</td>
      <td class="td max-w-[180px] truncate text-xs">${esc(f.evento_titulo)}</td>
      <td class="td max-w-[200px] truncate text-xs font-medium">${esc(f.docente_nombre)}</td>
      <td class="td whitespace-nowrap text-xs">${hhmm(f.hora_inicio)}</td>
      <td class="td whitespace-nowrap text-xs">${hhmmss(f.hora_escaneo_real)}</td>
      <td class="td"><span class="${f.estado === 'TARDANZA' ? 'badge-tardanza' : 'badge-puntual'}">${esc(
        f.estado
      )}</span></td>
      <td class="td text-xs">${f.minutos_tarde}</td>
      <td class="td whitespace-nowrap text-xs">${soles(f.tarifa_por_hora)}</td>
      <td class="td whitespace-nowrap text-xs">${soles(f.bruto)}</td>
      <td class="td whitespace-nowrap text-xs font-semibold ${
        f.descuento > 0 ? 'text-amber-600 dark:text-amber-400' : 'text-slate-400'
      }">
        ${soles(f.descuento)}
        ${f.bruto > 0 ? `<span class="text-[10px] text-slate-400">(${f.descuento_porcentaje.toFixed(1)}%)</span>` : ''}
      </td>
      <td class="td whitespace-nowrap text-xs font-bold text-emerald-600 dark:text-emerald-400">${soles(f.neto)}</td>
    </tr>`
    )
    .join('');
}

function abrirModalTarifa(id, nombre, tarifa) {
  $('#tarifaDocenteId').value = id;
  $('#tarifaDocenteNombre').textContent = nombre;
  $('#tarifaValor').value = Number(tarifa || 0).toFixed(2);
  abrirModal('modalTarifa');
  setTimeout(() => $('#tarifaValor').focus(), 60);
}

async function descargarExcelPagos() {
  const params = new URLSearchParams({
    desde: $('#pagoDesde').value,
    hasta: $('#pagoHasta').value,
  });
  if ($('#pagoDocente').value) params.set('docente_id', $('#pagoDocente').value);
  try {
    const blob = await api(`/api/pagos/export?${params.toString()}`, { blob: true });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const desde = $('#pagoDesde').value || hoyISO();
    const hasta = $('#pagoHasta').value || desde;
    a.download = `pagos_${desde}${hasta !== desde ? '_a_' + hasta : ''}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast('Excel de pagos generado correctamente.', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

function enlazarPagos() {
  if (!$('#btnExportarPagos')) return;
  $('#btnExportarPagos').addEventListener('click', descargarExcelPagos);
  ['#pagoDesde', '#pagoHasta', '#pagoDocente'].forEach((sel) => {
    $(sel).addEventListener('change', () => cargarPagos());
  });
  $('#pagoHoy').addEventListener('click', () => {
    $('#pagoDesde').value = hoyISO();
    $('#pagoHasta').value = hoyISO();
    cargarPagos();
  });
  $('#pagoSemana').addEventListener('click', () => {
    const hoy = hoyISO();
    const dow = (new Date().getDay() + 6) % 7;
    $('#pagoDesde').value = sumarDias(hoy, -dow);
    $('#pagoHasta').value = sumarDias(hoy, 6 - dow);
    cargarPagos();
  });
  $('#pagoMes').addEventListener('click', () => {
    const hoy = hoyISO();
    $('#pagoDesde').value = `${hoy.slice(0, 7)}-01`;
    $('#pagoHasta').value = finDeMes(hoy);
    cargarPagos();
  });

  $('#formTarifa').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const id = $('#tarifaDocenteId').value;
    const tarifa = Number($('#tarifaValor').value || 0);
    if (!id) return;
    try {
      await api(`/api/docentes/${id}`, {
        method: 'PUT',
        body: { tarifa_por_hora: tarifa },
      });
      toast('Tarifa actualizada.', 'ok');
      cerrarModal('modalTarifa');
      cargarPagos();
    } catch (e) {
      toast(e.message, 'error');
    }
  });
}

/* ------------------------------------------------------------------ */
/* Programación                                                        */
/* ------------------------------------------------------------------ */
async function cargarProgramacion() {
  if (!$('#evFecha').value) $('#evFecha').value = hoyISO();
  if (!$('#recDesde').value) $('#recDesde').value = hoyISO();
  if (!$('#recHasta').value) $('#recHasta').value = finDeMes(hoyISO());
  if (!$('#progDesde').value) $('#progDesde').value = hoyISO();
  await listarEventos();
}

async function listarEventos() {
  const desde = $('#progDesde').value || hoyISO();
  const eventos = await api(`/api/eventos?desde=${desde}&solo_activos=false`);
  const caja = $('#listaEventos');
  const vacio = $('#listaEventosVacio');
  vacio.hidden = eventos.length > 0;
  caja.innerHTML = eventos
    .map(
      (ev) => `
    <div class="flex flex-wrap items-center justify-between gap-2 rounded-xl border ${
      ev.activo ? 'border-slate-200 dark:border-slate-800' : 'border-dashed border-rose-300 dark:border-rose-900'
    } px-3 py-2">
      <div class="min-w-0">
        <p class="truncate text-sm font-semibold ${ev.activo ? 'text-slate-800 dark:text-slate-100' : 'text-slate-400 line-through'}">
          ${esc(ev.titulo)}
        </p>
        <p class="text-xs text-slate-500 dark:text-slate-400">
          ${fechaCorta(ev.fecha)} · ${hhmm(ev.hora_inicio)}–${hhmm(ev.hora_fin)} · tol. ${ev.tolerancia_minutos} min
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <span class="${ev.es_espontaneo ? 'badge-tardanza' : 'badge-neutral'}">${ev.es_espontaneo ? 'Espontáneo' : 'Programado'}</span>
        ${
          esDirectora()
            ? `<button class="btn-soft !px-2 !py-1 text-xs" data-toggle-evento="${ev.id}" data-activo="${ev.activo}">
                 ${ev.activo ? 'Día libre' : 'Reactivar'}
               </button>
               <button class="btn-soft !px-2 !py-1 text-xs !text-rose-600" data-borrar-evento="${ev.id}">Eliminar</button>
               ${
                 ev.grupo_id
                   ? `<button class="btn-soft !px-2 !py-1 text-xs" data-borrar-serie="${esc(ev.grupo_id)}">Borrar serie</button>`
                   : ''
               }`
            : ''
        }
      </div>
    </div>`
    )
    .join('');

  $$('[data-toggle-evento]', caja).forEach((btn) => {
    btn.addEventListener('click', async () => {
      const activo = btn.dataset.activo === 'true';
      try {
        await api(`/api/eventos/${btn.dataset.toggleEvento}/estado`, {
          method: 'PUT',
          body: { activo: !activo },
        });
        toast(activo ? 'Fecha marcada como día libre.' : 'Evento reactivado.', 'ok');
        listarEventos();
      } catch (e) {
        toast(e.message, 'error');
      }
    });
  });

  $$('[data-borrar-evento]', caja).forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('¿Eliminar este evento? Se borrarán también sus asistencias.')) return;
      try {
        await api(`/api/eventos/${btn.dataset.borrarEvento}`, { method: 'DELETE' });
        toast('Evento eliminado.', 'ok');
        listarEventos();
      } catch (e) {
        toast(e.message, 'error');
      }
    });
  });

  $$('[data-borrar-serie]', caja).forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('¿Eliminar TODA la serie recurrente (todas las fechas generadas)?')) return;
      try {
        const r = await api(`/api/eventos/serie/${btn.dataset.borrarSerie}`, { method: 'DELETE' });
        toast(r.mensaje || 'Serie eliminada.', 'ok');
        listarEventos();
      } catch (e) {
        toast(e.message, 'error');
      }
    });
  });
}

function enlazarProgramacion() {
  const formEvento = $('#formEvento');
  if (formEvento) {
    formEvento.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      try {
        await api('/api/eventos', {
          method: 'POST',
          body: {
            titulo: $('#evTitulo').value.trim(),
            fecha: $('#evFecha').value,
            hora_inicio: `${valorHora('#evInicio')}:00`,
            hora_fin: `${valorHora('#evFin')}:00`,
            tolerancia_minutos: Number($('#evTolerancia').value || 5),
            es_espontaneo: $('#evEspontaneo').checked,
          },
        });
        toast('Evento creado.', 'ok');
        formEvento.reset();
        $('#evFecha').value = hoyISO();
        $('#evTolerancia').value = 5;
        listarEventos();
      } catch (e) {
        toast(e.message, 'error');
      }
    });
  }

  const chips = $$('#recDias [data-dia]');
  chips.forEach((chip) => {
    chip.addEventListener('click', () => {
      chip.dataset.on = chip.dataset.on === '1' ? '0' : '1';
      chip.className = chip.dataset.on === '1'
        ? 'btn !px-3 !py-2 text-xs bg-emerald-600 text-white'
        : 'btn-soft !px-3 !py-2 text-xs';
    });
  });

  const alcance = $('#recAlcance');
  if (alcance) {
    alcance.addEventListener('change', () => {
      const base = $('#recDesde').value || hoyISO();
      if (alcance.value === 'mes') {
        $('#recDesde').value = base;
        $('#recHasta').value = finDeMes(base);
      } else if (alcance.value === 'anual') {
        $('#recDesde').value = base;
        $('#recHasta').value = sumarDias(base, 365);
      }
      $('#recDesde').disabled = alcance.value !== 'manual';
      $('#recHasta').disabled = alcance.value !== 'manual';
    });
    alcance.dispatchEvent(new Event('change'));
  }

  const formRec = $('#formRecurrente');
  if (formRec) {
    formRec.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const dias = chips.filter((c) => c.dataset.on === '1').map((c) => Number(c.dataset.dia));
      if (!dias.length) {
        toast('Selecciona al menos un día de la semana.', 'warn');
        return;
      }
      try {
        const r = await api('/api/eventos/recurrente', {
          method: 'POST',
          body: {
            titulo: $('#recTitulo').value.trim(),
            dias,
            hora_inicio: `${valorHora('#recInicio')}:00`,
            hora_fin: `${valorHora('#recFin')}:00`,
            tolerancia_minutos: Number($('#recTolerancia').value || 5),
            desde: $('#recDesde').value,
            hasta: $('#recHasta').value,
          },
        });
        toast(r.mensaje || 'Programación generada.', 'ok');
        listarEventos();
      } catch (e) {
        toast(e.message, 'error');
      }
    });
  }

  const btnProg = $('#btnProgCargar');
  if (btnProg) btnProg.addEventListener('click', () => listarEventos());
}

/* ------------------------------------------------------------------ */
/* Docentes                                                            */
/* ------------------------------------------------------------------ */
async function cargarDocentes() {
  const incluir = $('#docInactivos').checked;
  const q = $('#docBuscar').value.trim();
  const params = new URLSearchParams();
  if (incluir) params.set('incluir_inactivos', 'true');
  if (q) params.set('q', q);

  const docentes = await api(`/api/docentes?${params.toString()}`);
  state.docentesTodos = docentes;
  const activos = docentes.filter((d) => d.activo).length;

  // Contador global de activos (independiente del filtro)
  let totalActivos = activos;
  if (q || incluir) {
    const todos = await api('/api/docentes');
    totalActivos = todos.length;
  }
  $('#docContador').textContent = `${totalActivos} / 40 docentes activos`;
  const pct = Math.min(100, Math.round((totalActivos / 40) * 100));
  $('#docBarra').style.width = `${pct}%`;
  $('#docBarra').className =
    'h-full rounded-full transition-all duration-500 ' +
    (pct >= 100 ? 'bg-rose-500' : pct >= 80 ? 'bg-amber-500' : 'bg-emerald-500');
  $('#docPorcentaje').textContent = `${pct}%`;
  $('#btnNuevoDocente').disabled = totalActivos >= 40;

  const lista = $('#listaDocentes');
  $('#docVacio').hidden = docentes.length > 0;
  lista.innerHTML = docentes
    .map(
      (d) => `
    <div class="card card-hover p-4 ${d.activo ? '' : 'opacity-60'}">
      <div class="flex items-start gap-3">
        <img src="${esc(d.foto_path)}" alt="" class="h-16 w-16 shrink-0 rounded-xl object-cover ring-2 ring-slate-200 dark:ring-slate-800"
             onerror="this.src='/static/img/icon-192.png'" />
        <div class="min-w-0 flex-1">
          <p class="truncate font-bold text-slate-900 dark:text-white">${esc(d.nombre_completo)}</p>
          <p class="truncate text-xs font-medium text-slate-500 dark:text-slate-400">${esc(d.especialidad || 'Sin especialidad')}</p>
          <p class="mt-1 font-mono text-xs font-bold text-emerald-600 dark:text-emerald-400">${esc(d.qr_token)}</p>
          <p class="mt-1 text-xs font-semibold ${d.tarifa_por_hora > 0 ? 'text-slate-600 dark:text-slate-300' : 'text-rose-500'}">
            ${d.tarifa_por_hora > 0 ? `Tarifa: ${soles(d.tarifa_por_hora)} / hora` : 'Sin tarifa por hora'}
          </p>
        </div>
        <span class="${d.activo ? 'badge-puntual' : 'badge-neutral'}">${d.activo ? 'Activo' : 'Inactivo'}</span>
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <button class="btn-soft !px-3 !py-1.5 text-xs" data-credencial="${d.id}" data-nombre="${esc(d.nombre_completo)}">Credencial</button>
        ${
          esDirectora()
            ? `<button class="btn-soft !px-3 !py-1.5 text-xs" data-editar-docente="${d.id}">Editar</button>
               ${
                 d.activo
                   ? `<button class="btn-soft !px-3 !py-1.5 text-xs !text-rose-600" data-baja-docente="${d.id}">Desactivar</button>`
                   : `<button class="btn-soft !px-3 !py-1.5 text-xs !text-emerald-600" data-alta-docente="${d.id}">Reactivar</button>`
               }`
            : ''
        }
      </div>
    </div>`
    )
    .join('');

  $$('[data-credencial]', lista).forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.credencial;
      $('#credNombre').textContent = btn.dataset.nombre;
      $('#credImg').src = `/api/docentes/${id}/credencial?t=${Date.now()}`;
      $('#credDescargar').href = `/api/docentes/${id}/credencial`;
      $('#credDescargar').setAttribute('download', `credencial_${id}.png`);
      abrirModal('modalCredencial');
    });
  });

  $$('[data-editar-docente]', lista).forEach((btn) => {
    btn.addEventListener('click', () => abrirFormDocente(Number(btn.dataset.editarDocente)));
  });

  $$('[data-baja-docente]', lista).forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('¿Desactivar a este docente? Ya no aparecerá en el escáner.')) return;
      try {
        const r = await api(`/api/docentes/${btn.dataset.bajaDocente}`, { method: 'DELETE' });
        toast(r.mensaje, 'ok');
        cargarDocentes();
      } catch (e) {
        toast(e.message, 'error');
      }
    });
  });

  $$('[data-alta-docente]', lista).forEach((btn) => {
    btn.addEventListener('click', async () => {
      try {
        await api(`/api/docentes/${btn.dataset.altaDocente}`, { method: 'PUT', body: { activo: true } });
        toast('Docente reactivado.', 'ok');
        cargarDocentes();
      } catch (e) {
        toast(e.message, 'error');
      }
    });
  });
}

function abrirFormDocente(id = null) {
  const form = $('#formDocente');
  form.reset();
  $('#docId').value = id || '';
  if (id) {
    const d = state.docentesTodos.find((x) => x.id === id);
    if (d) {
      $('#modalDocenteTitulo').textContent = `Editar a ${d.nombre_completo}`;
      $('#docNombres').value = d.nombres;
      $('#docApellidos').value = d.apellidos;
      $('#docEspecialidad').value = d.especialidad || '';
      $('#docTarifa').value = Number(d.tarifa_por_hora || 0).toFixed(2);
      $('#docActivo').checked = !!d.activo;
    }
  } else {
    $('#modalDocenteTitulo').textContent = 'Nuevo docente';
    $('#docActivo').checked = true;
  }
  abrirModal('modalDocente');
}

function enlazarDocentes() {
  const form = $('#formDocente');
  if (!form) return;
  $('#btnNuevoDocente').addEventListener('click', () => abrirFormDocente(null));
  $('#docBuscar').addEventListener('input', debounce(() => cargarDocentes(), 350));
  $('#docInactivos').addEventListener('change', () => cargarDocentes());

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const id = $('#docId').value;
    const payload = {
      nombres: $('#docNombres').value.trim(),
      apellidos: $('#docApellidos').value.trim(),
      especialidad: $('#docEspecialidad').value.trim(),
      tarifa_por_hora: Number($('#docTarifa').value || 0),
      activo: $('#docActivo').checked,
    };
    const archivo = $('#docFoto').files[0];
    try {
      let docenteId = id;
      if (id) {
        await api(`/api/docentes/${id}`, { method: 'PUT', body: payload });
      } else {
        const creado = await api('/api/docentes', { method: 'POST', body: payload });
        docenteId = creado.id;
      }
      if (archivo) {
        const fd = new FormData();
        fd.append('archivo', archivo);
        await api(`/api/docentes/${docenteId}/foto`, { method: 'PUT', form: fd });
      }
      toast(id ? 'Docente actualizado y credencial regenerada.' : 'Docente creado con su credencial.', 'ok');
      cerrarModal('modalDocente');
      cargarDocentes();
    } catch (e) {
      toast(e.message, 'error');
    }
  });
}

/* ------------------------------------------------------------------ */
/* Credenciales de docentes                                            */
/* ------------------------------------------------------------------ */
async function cargarCredenciales() {
  // Se incluyen también los inactivos: la tabla tiene columna "Estado" y el
  // docente puede necesitar descargar su credencial aunque esté desactivado.
  const docentes = await api('/api/docentes?incluir_inactivos=true');
  state.credenciales = docentes
    .slice()
    .sort((a, b) => Number(b.activo) - Number(a.activo));
  pintarCredenciales($('#credBuscar') ? $('#credBuscar').value : '');
}

function pintarCredenciales(filtro = '') {
  const texto = (filtro || '').trim().toLowerCase();
  const docentes = state.credenciales.filter((d) => {
    if (!texto) return true;
    return (
      (d.nombre_completo || '').toLowerCase().includes(texto) ||
      (d.qr_token || '').toLowerCase().includes(texto) ||
      (d.especialidad || '').toLowerCase().includes(texto)
    );
  });
  const tbody = $('#listaCredenciales');
  $('#credVacio').hidden = state.credenciales.length > 0;
  if (docentes.length === 0 && state.credenciales.length > 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="py-8 text-center text-sm text-slate-400">Sin resultados para "${esc(filtro)}".</td></tr>`;
    return;
  }
  tbody.innerHTML = docentes
    .map(
      (d, i) => `
    <tr class="${d.activo ? '' : 'opacity-60'}">
      <td class="td tabular-nums text-xs font-bold text-slate-400">${i + 1}</td>
      <td class="td">
        <div class="flex items-center gap-2">
          <img src="${esc(d.foto_path)}" alt="" class="h-9 w-9 shrink-0 rounded-lg object-cover ring-1 ring-slate-200 dark:ring-slate-800"
               onerror="this.src='/static/img/icon-192.png'" />
          <div class="min-w-0">
            <p class="truncate font-semibold text-slate-900 dark:text-white">${esc(d.nombre_completo)}</p>
          </div>
        </div>
      </td>
      <td class="td text-xs">${esc(d.especialidad || 'Sin especialidad')}</td>
      <td class="td font-mono text-xs font-bold text-emerald-600 dark:text-emerald-400">${esc(d.qr_token)}</td>
      <td class="td whitespace-nowrap text-xs">${d.qr_vigencia ? fechaCorta(d.qr_vigencia) : '—'}</td>
      <td class="td"><span class="${d.activo ? 'badge-puntual' : 'badge-tardanza'}">${d.activo ? 'Activo' : 'Inactivo'}</span></td>
      <td class="td text-right whitespace-nowrap">
        <button class="btn-soft !px-2 !py-1 text-xs" data-ver-credencial="${d.id}" data-nombre="${esc(d.nombre_completo)}">Ver</button>
        <a href="/api/docentes/${d.id}/credencial" download="credencial_${esc(d.qr_token)}.png"
           class="btn-soft !px-2 !py-1 text-xs">Descargar</a>
      </td>
    </tr>`
    )
    .join('');

  $$('[data-ver-credencial]', tbody).forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.verCredencial;
      $('#credNombre').textContent = btn.dataset.nombre;
      $('#credImg').src = `/api/docentes/${id}/credencial?t=${Date.now()}`;
      $('#credDescargar').href = `/api/docentes/${id}/credencial`;
      $('#credDescargar').setAttribute('download', `credencial_${id}.png`);
      abrirModal('modalCredencial');
    });
  });
}

/* ------------------------------------------------------------------ */
/* Usuarios administrativos                                            */
/* ------------------------------------------------------------------ */
async function cargarUsuarios() {
  const usuarios = await api('/api/usuarios');
  const tbody = $('#listaUsuarios');
  tbody.innerHTML = usuarios
    .map((u) => {
      const esDireccion = u.rol === 'DIRECTORA';
      const acciones = esDireccion
        ? `<span class="text-xs font-semibold text-slate-400" title="La cuenta de Dirección no se puede eliminar ni desactivar">Protegida</span>`
        : u.activo
          ? `<button class="btn-soft !px-2 !py-1 text-xs !text-rose-600" data-baja-usuario="${u.id}">Desactivar</button>`
          : `<button class="btn-soft !px-2 !py-1 text-xs !text-emerald-600" data-alta-usuario="${u.id}">Activar</button>`;
      return `
    <tr>
      <td class="td font-semibold">${esc(u.nombre)}</td>
      <td class="td text-xs">${esc(u.email)}</td>
      <td class="td"><span class="${esDireccion ? 'badge-puntual' : 'badge-neutral'}">${esc(etiquetaRol(u.rol))}</span></td>
      <td class="td"><span class="${u.activo ? 'badge-puntual' : 'badge-tardanza'}">${u.activo ? 'Activo' : 'Inactivo'}</span></td>
      <td class="td text-right whitespace-nowrap">
        <button class="btn-soft !px-2 !py-1 text-xs" data-editar-usuario="${u.id}">Editar</button>
        ${acciones}
      </td>
    </tr>`;
    })
    .join('');

  $$('[data-editar-usuario]', tbody).forEach((btn) =>
    btn.addEventListener('click', () => {
      const u = usuarios.find((x) => x.id === Number(btn.dataset.editarUsuario));
      if (!u) return;
      $('#modalUsuarioTitulo').textContent = `Editar ${u.nombre}`;
      $('#usrId').value = u.id;
      $('#usrNombre').value = u.nombre;
      $('#usrEmail').value = u.email;
      $('#usrPassword').value = '';
      $('#usrActivo').checked = !!u.activo;
      const esDireccion = u.rol === 'DIRECTORA';
      $('#usrActivo').disabled = esDireccion;
      $('#usrRolTexto').textContent = esDireccion
        ? 'Dirección · cuenta protegida'
        : 'Secretaría / Administrativo';
      const aviso = $('#usrAviso');
      aviso.hidden = !esDireccion;
      aviso.textContent = esDireccion
        ? 'La cuenta de Dirección no se puede eliminar, desactivar ni cambiar de rol.'
        : '';
      $('#grupoPassword').hidden = false;
      abrirModal('modalUsuario');
    })
  );

  const cambiarEstado = async (id, activo) => {
    try {
      await api(`/api/usuarios/${id}`, { method: 'PUT', body: { activo } });
      toast(activo ? 'Cuenta activada.' : 'Cuenta desactivada.', 'ok');
      cargarUsuarios();
    } catch (e) {
      toast(e.message, 'error');
    }
  };
  $$('[data-baja-usuario]', tbody).forEach((btn) =>
    btn.addEventListener('click', () => {
      if (confirm('¿Desactivar esta cuenta?')) cambiarEstado(btn.dataset.bajaUsuario, false);
    })
  );
  $$('[data-alta-usuario]', tbody).forEach((btn) =>
    btn.addEventListener('click', () => cambiarEstado(btn.dataset.altaUsuario, true))
  );
}

function enlazarUsuarios() {
  const form = $('#formUsuario');
  if (!form) return;
  $('#btnNuevoUsuario').addEventListener('click', () => {
    form.reset();
    $('#usrId').value = '';
    $('#usrActivo').checked = true;
    $('#usrActivo').disabled = false;
    $('#usrRolTexto').textContent = 'Secretaría / Administrativo';
    $('#usrAviso').hidden = true;
    $('#grupoPassword').hidden = false;
    $('#modalUsuarioTitulo').textContent = 'Nueva cuenta';
    abrirModal('modalUsuario');
  });

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const id = $('#usrId').value;
    const payload = {
      nombre: $('#usrNombre').value.trim(),
      email: $('#usrEmail').value.trim(),
      activo: $('#usrActivo').checked,
    };
    const password = $('#usrPassword').value;
    if (password) payload.password = password;
    try {
      if (id) {
        await api(`/api/usuarios/${id}`, { method: 'PUT', body: payload });
        toast('Cuenta actualizada.', 'ok');
      } else {
        if (!password) {
          toast('Define una contraseña inicial.', 'warn');
          return;
        }
        await api('/api/usuarios', { method: 'POST', body: payload });
        toast('Cuenta creada.', 'ok');
      }
      cerrarModal('modalUsuario');
      cargarUsuarios();
    } catch (e) {
      toast(e.message, 'error');
    }
  });
}

/* ------------------------------------------------------------------ */
/* Modales y utilidades de UI                                          */
/* ------------------------------------------------------------------ */
function abrirModal(id) {
  const m = document.getElementById(id);
  if (!m) return;
  m.classList.remove('hidden');
  m.classList.add('flex');
}
function cerrarModal(id) {
  const m = document.getElementById(id);
  if (!m) return;
  m.classList.add('hidden');
  m.classList.remove('flex');
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

/* ------------------------------------------------------------------ */
/* Enlazado general de la app                                          */
/* ------------------------------------------------------------------ */
function enlazarAcciones() {
  $('#btnEscanearGrande').addEventListener('click', () => {
    location.hash = '#/escanear';
  });
  $('#scanStart').addEventListener('click', () => Scanner.iniciar());
  $('#scanStop').addEventListener('click', () => Scanner.detener());
  $('#btnLimpiarRecientes').addEventListener('click', () => {
    state.escaneosSesion = [];
    state.escaneados.clear();
    pintarRecientes();
  });
  $('#btnManual').addEventListener('click', () => {
    const valor = $('#scanManual').value.trim();
    if (!valor) return;
    procesarEscaneo(valor);
    $('#scanManual').value = '';
  });
  $('#scanManual').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      $('#btnManual').click();
    }
  });

  $('#btnExportar').addEventListener('click', descargarExcel);
  ['#fDesde', '#fHasta', '#fDocente'].forEach((sel) => {
    $(sel).addEventListener('change', () => consultarHistorial());
  });
  $('#btnHoy').addEventListener('click', () => {
    $('#fDesde').value = hoyISO();
    $('#fHasta').value = hoyISO();
    consultarHistorial();
  });
  $('#btnSemana').addEventListener('click', () => {
    const hoy = hoyISO();
    const dow = (new Date().getDay() + 6) % 7; // 0 = lunes
    $('#fDesde').value = sumarDias(hoy, -dow);
    $('#fHasta').value = sumarDias(hoy, 6 - dow);
    consultarHistorial();
  });
  $('#btnMes').addEventListener('click', () => {
    const hoy = hoyISO();
    $('#fDesde').value = `${hoy.slice(0, 7)}-01`;
    $('#fHasta').value = finDeMes(hoy);
    consultarHistorial();
  });

  $$('[data-cerrar]').forEach((btn) => {
    btn.addEventListener('click', () => cerrarModal(btn.dataset.cerrar));
  });
  ['modalDocente', 'modalTarifa', 'modalUsuario', 'modalCredencial'].forEach((id) => {
    const m = document.getElementById(id);
    if (!m) return;
    m.addEventListener('click', (ev) => {
      if (ev.target === m) cerrarModal(id);
    });
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape')
      ['modalDocente', 'modalTarifa', 'modalUsuario', 'modalCredencial'].forEach(cerrarModal);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) Scanner.detener();
  });

  const credBuscar = $('#credBuscar');
  if (credBuscar) {
    credBuscar.addEventListener('input', () => pintarCredenciales(credBuscar.value));
  }

  enlazarProgramacion();
  enlazarDocentes();
  enlazarPagos();
  enlazarUsuarios();
}

/* ------------------------------------------------------------------ */
/* Arranque                                                            */
/* ------------------------------------------------------------------ */
document.addEventListener('DOMContentLoaded', () => {
  iniciarTema();
  if ($('#formLogin')) {
    iniciarLogin();
  } else if ($('#view-dashboard')) {
    iniciarApp();
  }
});
