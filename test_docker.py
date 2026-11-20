"""Prueba local en Docker para el domingo de masas (2026-11-22).
Ejecuta contra la pila aislada en puertos 8001 (PostgreSQL real).
No modifica datos de usuario."""
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

BASE = "http://localhost:8001"
TZ = ZoneInfo("America/Lima")
RES = []


def check(nombre, ok, extra=""):
    RES.append((nombre, bool(ok), extra))
    print(("OK    " if ok else "FALLA ") + f"· {nombre}" + (f" · {extra}" if extra else ""))


async def main():
    hoy = datetime.now(TZ).replace(tzinfo=None)
    domingo = hoy.date() + timedelta(days=(6 - hoy.date().weekday()) % 7)
    if domingo == hoy.date():
        domingo += timedelta(days=7)
    print(f"hoy={hoy.date()} (weekday={hoy.date().weekday()}) domingo={domingo} (weekday={domingo.weekday()})")

    async with httpx.AsyncClient(base_url=BASE, timeout=90) as c:
        # Salud y páginas
        r = await c.get("/api/health")
        check("health", r.status_code == 200, str(r.status_code))
        r = await c.get("/")
        check("GET / panel", r.status_code == 200 and "Control de asistencia" in r.text, str(r.status_code))
        r = await c.get("/login")
        check("GET /login", r.status_code == 200 and "Ingresar" in r.text, str(r.status_code))
        r = await c.get("/manifest.json")
        check("manifest.json", r.status_code == 200 and r.json().get("name"), str(r.status_code))
        r = await c.get("/sw.js")
        check("sw.js Service-Worker-Allowed", r.status_code == 200 and r.headers.get("service-worker-allowed") == "/", str(r.status_code))
        r = await c.get("/static/css/tailwind.css")
        check("tailwind.css compilado", r.status_code == 200 and len(r.content) > 5000 and b".btn-primary" in r.content, f"{len(r.content)} bytes")
        r = await c.get("/static/vendor/html5-qrcode.js")
        check("html5-qrcode vendorizado", r.status_code == 200 and len(r.content) > 100000, f"{len(r.content)} bytes")
        r = await c.get("/static/js/app.js")
        check("app.js servido", r.status_code == 200 and len(r.content) > 50000, f"{len(r.content)} bytes")

        # Auth
        r = await c.post("/api/auth/login", json={"email": "directora@academia.pe", "password": "Directora123"})
        check("login directora (seed)", r.status_code == 200, str(r.status_code))
        H = {"Authorization": f"Bearer {r.json()['access_token']}"}
        check("cookie httponly", "asistencia_token" in r.cookies)
        r = await c.post("/api/auth/login", json={"email": "directora@academia.pe", "password": "mala"})
        check("contraseña mala -> 401", r.status_code == 401, str(r.status_code))
        r = await c.get("/api/auth/me", headers=H)
        check("auth/me DIRECTORA", r.status_code == 200 and r.json()["rol"] == "DIRECTORA")

        # Docente
        r = await c.post("/api/docentes", headers=H, json={
            "nombres": "Ana Maria", "apellidos": "Quispe Rojas",
            "especialidad": "Matemática", "tarifa_por_hora": 12, "activo": True})
        check("crear docente", r.status_code == 201, str(r.text[:120]))
        doc = r.json()
        did, token = doc["id"], doc["qr_token"]
        check("token DOC-XXXXXXXX", token.startswith("DOC-") and len(token) == 12, token)
        check("avatar generado", bool(doc.get("foto_path")), doc.get("foto_path", ""))
        r = await c.get(f"/api/docentes/{did}/credencial")
        check("credencial PNG", r.status_code == 200 and r.content[:4] == b"\x89PNG", f"{len(r.content)}b")
        r = await c.get(f"/api/docentes/{did}/qr")
        check("QR PNG", r.status_code == 200 and r.content[:4] == b"\x89PNG", str(r.status_code))
        r = await c.get("/api/credenciales/zip", headers=H)
        check("ZIP credenciales", r.status_code == 200 and r.content[:2] == b"PK", str(r.status_code))
        r = await c.get(f"/mi-credencial?token={token}")
        check("/mi-credencial pública", r.status_code == 200 and "Quispe" in r.text, str(r.status_code))

        # Evento de hoy
        ini = (hoy - timedelta(minutes=30)).time().replace(microsecond=0)
        fin = (hoy + timedelta(minutes=90)).time().replace(microsecond=0)
        ev_hoy = {"titulo": "Bloque de hoy", "fecha": hoy.date().isoformat(),
                  "hora_inicio": ini.isoformat(), "hora_fin": fin.isoformat(),
                  "tolerancia_minutos": 10, "es_espontaneo": True}
        r = await c.post("/api/eventos", headers=H, json=ev_hoy)
        check("crear evento de hoy", r.status_code == 201, str(r.status_code))
        eid_hoy = r.json().get("id") if r.status_code == 201 else None

        # Serie recurrente solo dominicos (dias=[6])
        hasta_rec = domingo + timedelta(days=28)
        r = await c.post("/api/eventos/recurrente", headers=H, json={
            "titulo": "Domingo · Recurrente", "dias": [6],
            "hora_inicio": "16:00:00", "hora_fin": "18:00:00",
            "tolerancia_minutos": 5, "desde": domingo.isoformat(), "hasta": hasta_rec.isoformat()})
        res = r.json()
        check("recurrente solo domingos -> 201", r.status_code == 201, str(r.text[:160]))
        check("generados > 0", res["generados"] > 0, str(res["generados"]))

        # Verificar que todos los eventos de la serie caen en domingo
        r = await c.get("/api/eventos", headers=H, params={"desde": domingo.isoformat(), "hasta": hasta_rec.isoformat()})
        evs = r.json() if r.status_code == 200 else []
        serie = [e for e in evs if e["titulo"] == "Domingo · Recurrente"]
        esperados = sum(1 for i in range(29) if (domingo + timedelta(days=i)).weekday() == 6)
        check("todos los eventos de la serie caen en domingo",
              len(serie) == esperados and all(date.fromisoformat(e["fecha"]).weekday() == 6 for e in serie),
              f"serie={len(serie)} esperados={esperados}")

        # Escaneo real y duplicados
        r = await c.post("/api/asistencias/escaneo", headers=H, json={"qr_token": token, "evento_id": eid_hoy})
        check("escaneo", r.status_code == 200, str(r.text[:120]))
        aid = r.json()["asistencia"]["id"] if r.status_code == 200 else None
        r = await c.post("/api/asistencias/escaneo", headers=H, json={"qr_token": token, "evento_id": eid_hoy})
        check("duplicado escaneo -> 409", r.status_code == 409, str(r.status_code))
        r = await c.post("/api/asistencias/escaneo", headers=H,
                   json={"qr_token": f"http://localhost:8001/mi-credencial?token={token}", "evento_id": eid_hoy})
        check("QR con URL aceptado (409 duplicado)", r.status_code == 409, str(r.status_code))

        r = await c.get("/api/dashboard", headers=H)
        check("dashboard", r.status_code == 200 and r.json()["asistencias_hoy"] == 1, str(r.status_code))

        # Pagos
        r = await c.get("/api/pagos", headers=H)
        p = r.json()
        res = p["resumen"][0]
        check("bruto 12 x 2h = 24", abs(res["bruto"] - 24) < 0.01, str(res["bruto"]))
        check("descuento 12 x 30/60 = 6", 5.9 <= res["descuento"] <= 6.2, str(res["descuento"]))
        check("sin docentes sin tarifa", p["totales"]["docentes_sin_tarifa"] == 0, str(p["totales"]["docentes_sin_tarifa"]))

        hoy_iso = hoy.date().isoformat()
        r = await c.post("/api/pagos/pagar", headers=H, json={"docente_id": did, "desde": hoy_iso, "hasta": hoy_iso})
        check("registrar pago", r.status_code == 200, str(r.text[:120]))
        pid = r.json().get("id") if r.status_code == 200 else None
        r = await c.get("/api/pagos", headers=H)
        res = r.json()["resumen"][0]
        check("tras pagar: pendiente 0", abs(res["pendiente"]) < 0.01, str(res["pendiente"]))
        r = await c.delete(f"/api/pagos/{pid}", headers=H)
        check("anular pago", r.status_code == 200, str(r.status_code))
        r = await c.get("/api/pagos", headers=H)
        check("tras anular: saldo vuelve", r.json()["resumen"][0]["pendiente"] > 0, str(r.json()["resumen"][0]["pendiente"]))

        # Excel
        r = await c.get("/api/asistencias/export", headers=H)
        check("Excel asistencias", r.status_code == 200 and r.content[:2] == b"PK", str(r.status_code))
        r = await c.get("/api/pagos/export", headers=H)
        check("Excel pagos", r.status_code == 200 and r.content[:2] == b"PK", str(r.status_code))

        # Permisos ADMIN
        r = await c.post("/api/auth/login", json={"email": "admin@academia.pe", "password": "Admin123"})
        HA = {"Authorization": f"Bearer {r.json()['access_token']}"}
        check("login admin (seed)", r.status_code == 200, str(r.status_code))
        r = await c.get("/api/pagos", headers=HA)
        check("ADMIN no ve pagos -> 403", r.status_code == 403, str(r.status_code))
        r = await c.post("/api/docentes", headers=HA, json={"nombres": "X Y", "apellidos": "Z W"})
        check("ADMIN no crea docente -> 403", r.status_code == 403, str(r.status_code))

        # Límite 40 activos
        creados = 1
        fallo_limite = None
        for i in range(2, 42):
            r = await c.post("/api/docentes", headers=H, json={"nombres": f"Docente {i}", "apellidos": "Prueba Limite", "activo": True})
            if r.status_code == 201:
                creados += 1
            elif r.status_code == 409:
                fallo_limite = i
                break
            else:
                fallo_limite = f"{i}:{r.status_code}"
                break
        check("límite de 40 docentes activos", creados == 40 and fallo_limite == 41,
              f"creados={creados} bloqueado_en={fallo_limite}")

        # Borrado
        r = await c.delete(f"/api/asistencias/{aid}", headers=H)
        check("eliminar asistencia", r.status_code == 200, str(r.status_code))

    failed = sum(1 for _, ok, _ in RES if not ok)
    print(f"\nRESUMEN: {len(RES) - failed} OK / {failed} fallas")
    for n, ok, extra in RES:
        if not ok:
            print(f"  - {n}: {extra}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

