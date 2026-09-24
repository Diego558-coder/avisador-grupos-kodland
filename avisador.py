"""
Avisador de grupos Kodland
--------------------------
Revisa periódicamente la app de Google Apps Script de Kodland, recorre todos
los cursos del desplegable y, si aparece algo nuevo, envía una notificación
al teléfono mediante ntfy (https://ntfy.sh).

Uso:
    python avisador.py --login     # 1a vez: abre Chrome para iniciar sesión en Google
    python avisador.py --probar    # envía una notificación de prueba al teléfono
    python avisador.py --una-vez   # hace una sola revisión y termina
    python avisador.py             # revisa cada N minutos para siempre
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "config.json"
ESTADO_FILE = BASE / "estado.json"
PERFIL_DIR = BASE / "perfil_navegador"
SESION_FILE = BASE / "sesion.json"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def cargar_json(path, defecto):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto


def guardar_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cargar_config():
    cfg = cargar_json(CONFIG_FILE, {}) or {}

    # En GitHub Actions los datos sensibles llegan por variables de entorno
    # (Secrets), nunca escritos en el repositorio público.
    if os.environ.get("URL_APP"):
        cfg["url_app"] = os.environ["URL_APP"]
    if os.environ.get("NTFY_TEMA"):
        cfg["ntfy_tema"] = os.environ["NTFY_TEMA"]
    if os.environ.get("NTFY_SERVIDOR"):
        cfg["ntfy_servidor"] = os.environ["NTFY_SERVIDOR"]
    if os.environ.get("SESION_JSON") and not SESION_FILE.exists():
        SESION_FILE.write_text(os.environ["SESION_JSON"], encoding="utf-8")

    if "PEGA_AQUI" in cfg.get("url_app", "") or not cfg.get("url_app"):
        sys.exit("Falta la URL de la app: ponla en config.json (url_app) o en el secreto URL_APP.")
    if "CAMBIA" in cfg.get("ntfy_tema", "") or not cfg.get("ntfy_tema"):
        sys.exit("Falta el tema de ntfy: ponlo en config.json (ntfy_tema) o en el secreto NTFY_TEMA.")
    cfg.setdefault("intervalo_minutos", 5)
    cfg.setdefault("heartbeat_horas", 24)
    cfg.setdefault("cursos_a_vigilar", [])
    cfg.setdefault("ignorar_lineas", [])
    return cfg


# ---------------------------------------------------------------- notificación

def notificar(cfg, titulo, mensaje, prioridad=4, tags=("bell",)):
    payload = {
        "topic": cfg["ntfy_tema"],
        "title": titulo,
        "message": mensaje[:3900],
        "priority": prioridad,
        "tags": list(tags),
        "click": cfg["url_app"],
    }
    req = urllib.request.Request(
        cfg.get("ntfy_servidor", "https://ntfy.sh"),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
        log(f"Notificación enviada: {titulo}")
    except Exception as e:
        log(f"ERROR enviando notificación: {e}")


# ------------------------------------------------------------------ navegador

def abrir_contexto(p, headless):
    """Devuelve (context, browser). 'browser' es None cuando se usa un perfil
    persistente (no hace falta cerrarlo aparte)."""
    args_anti_deteccion = ["--disable-blink-features=AutomationControlled"]

    if SESION_FILE.exists():
        # Modo portátil: sirve igual en Windows (PC) que en Linux (servidor en
        # la nube), porque la sesión viaja en un archivo de datos (cookies),
        # no en un perfil de Chrome atado al sistema operativo.
        browser = p.chromium.launch(headless=headless, args=args_anti_deteccion)
        ctx = browser.new_context(
            storage_state=str(SESION_FILE), viewport={"width": 1200, "height": 900}
        )
        return ctx, browser

    opciones = dict(
        user_data_dir=str(PERFIL_DIR),
        headless=headless,
        ignore_default_args=["--enable-automation"],
        args=args_anti_deteccion,
        viewport={"width": 1200, "height": 900},
    )
    try:
        # Chrome real: Google deja iniciar sesión con menos problemas
        return p.chromium.launch_persistent_context(channel="chrome", **opciones), None
    except Exception:
        return p.chromium.launch_persistent_context(**opciones), None


def cerrar_contexto(ctx, browser):
    try:
        ctx.close()
    except Exception:
        pass
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass


def buscar_frame_con_select(page, espera_seg=40):
    """Las apps de Apps Script van dentro de iframes anidados; buscamos el que tiene el <select>."""
    fin = time.time() + espera_seg
    while time.time() < fin:
        for fr in page.frames:
            try:
                if fr.query_selector("select"):
                    return fr
            except Exception:
                pass
        time.sleep(1)
    return None


PALABRAS_CARGANDO = ("cargando", "loading", "buscando", "espera")


def texto_estable(frame, max_seg=20):
    """Espera a que el contenido deje de cambiar (los grupos se cargan asíncronamente)."""
    anterior, estable_desde = None, time.time()
    fin = time.time() + max_seg
    time.sleep(1.5)
    while time.time() < fin:
        try:
            actual = frame.inner_text("body")
        except Exception:
            actual = ""
        cargando = any(k in actual.lower() for k in PALABRAS_CARGANDO)
        if actual != anterior or cargando:
            anterior, estable_desde = actual, time.time()
        elif time.time() - estable_desde >= 2.5:
            break
        time.sleep(0.5)

    # Si seguía en un estado "cargando/buscando" al agotar el tiempo, damos un
    # último margen extra en vez de guardar ese texto a medias.
    if anterior and any(k in anterior.lower() for k in PALABRAS_CARGANDO):
        for _ in range(6):
            time.sleep(1.0)
            try:
                actual = frame.inner_text("body")
            except Exception:
                actual = ""
            if actual and not any(k in actual.lower() for k in PALABRAS_CARGANDO):
                return actual
        return actual or anterior

    return anterior or ""


def leer_todos_los_cursos(cfg, headless=True):
    """Devuelve {curso: [lineas de texto]} o lanza excepción."""
    resultado = {}
    with sync_playwright() as p:
        ctx, browser = abrir_contexto(p, headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(cfg["url_app"], wait_until="domcontentloaded", timeout=60000)
            frame = buscar_frame_con_select(page)
            if frame is None:
                if "accounts.google.com" in page.url:
                    raise RuntimeError("SESION")
                raise RuntimeError("No se encontró el selector de cursos en la página.")

            opciones = frame.eval_on_selector_all(
                "select option",
                "els => els.map(e => ({value: e.value, text: e.textContent.trim()}))",
            )
            opciones = [o for o in opciones if o["value"] and "elige" not in o["text"].lower()]
            filtro = [c.lower() for c in cfg["cursos_a_vigilar"]]
            if filtro:
                opciones = [o for o in opciones if any(f in o["text"].lower() for f in filtro)]

            nombres_cursos = {o["text"] for o in opciones}
            ignorar = {l.lower() for l in cfg["ignorar_lineas"]}
            ESTATICAS = {
                "postúlate a un grupo",
                "📚 grupos",
                "📋 mis postulaciones",
                "¿qué curso quieres dar?",
                "— elige un curso —",
                "grupos",
                "mis postulaciones",
            }

            for o in opciones:
                frame.select_option("select", value=o["value"])
                texto = texto_estable(frame)
                lineas = []
                for l in texto.splitlines():
                    l = " ".join(l.split())
                    if not l or l in nombres_cursos or l.lower() in ignorar:
                        continue
                    bajo = l.lower()
                    if bajo in ESTATICAS or bajo.startswith("hola,"):
                        continue
                    if bajo.startswith("no hay grupos") and "disponibles" in bajo:
                        continue
                    lineas.append(l)
                resultado[o["text"]] = lineas
                log(f"  {o['text']}: {len(lineas)} líneas")
        finally:
            cerrar_contexto(ctx, browser)
    return resultado


# ------------------------------------------------------------------- lógica

def comparar(anterior, actual):
    """Devuelve {curso: [lineas nuevas]} usando conteo (detecta aunque se repitan textos)."""
    cambios = {}
    for curso, lineas in actual.items():
        if curso not in anterior:
            # Curso que no existía antes en el desplegable
            if anterior:  # solo si ya había una línea base
                cambios[curso] = ["(curso nuevo en la lista)"] + lineas
            continue
        nuevas = Counter(lineas) - Counter(anterior[curso])
        if nuevas:
            # conservar el orden en que aparecen en pantalla
            vistas, orden = Counter(), []
            for l in lineas:
                if nuevas[l] > vistas[l]:
                    vistas[l] += 1
                    orden.append(l)
            cambios[curso] = orden
    return cambios


def revisar(cfg, estado):
    log("Revisando...")
    try:
        actual = leer_todos_los_cursos(cfg)
    except RuntimeError as e:
        if str(e) == "SESION":
            msg = "La sesión de Google expiró. Ejecuta: python avisador.py --login"
        else:
            msg = str(e)
        log(f"ERROR: {msg}")
        if not estado.get("error_avisado"):
            notificar(cfg, "Avisador Kodland: problema", msg, prioridad=3, tags=("warning",))
            estado["error_avisado"] = True
            guardar_json(ESTADO_FILE, estado)
        return estado
    except Exception as e:
        log(f"ERROR inesperado: {e}")
        return estado

    anterior = estado.get("cursos", {})
    ahora = datetime.now()
    heartbeat_horas = float(cfg.get("heartbeat_horas", 24))
    heartbeat_ultimo = estado.get("ultimo_heartbeat")
    if not anterior:
        log(f"Primera revisión: guardada línea base de {len(actual)} cursos (sin notificar).")
    else:
        cambios = comparar(anterior, actual)
        for curso, lineas in cambios.items():
            cuerpo = "\n".join(lineas[:25])
            if len(lineas) > 25:
                cuerpo += f"\n… y {len(lineas) - 25} líneas más"
            notificar(cfg, f"🆕 Grupos nuevos: {curso}", cuerpo)
        if not cambios:
            log("Sin novedades.")
            try:
                ultima = datetime.fromisoformat(estado.get("ultima_revision", "1970-01-01T00:00:00"))
                if heartbeat_ultimo is None and (ahora - ultima).total_seconds() >= heartbeat_horas * 3600:
                    notificar(
                        cfg,
                        "Avisador Kodland: heartbeat",
                        f"El sistema sigue activo. Última revisión: {estado.get('ultima_revision', 'sin dato')}",
                        prioridad=3,
                        tags=("signal_strength",),
                    )
                    estado["ultimo_heartbeat"] = ahora.isoformat(timespec="seconds")
            except ValueError:
                pass

    # Si un curso desaparece temporalmente, conservamos su último estado
    anterior.update(actual)
    estado = {
        "cursos": anterior,
        "ultima_revision": ahora.isoformat(timespec="seconds"),
        "ultimo_heartbeat": estado.get("ultimo_heartbeat"),
    }
    guardar_json(ESTADO_FILE, estado)
    return estado


def modo_login(cfg):
    # Si ya existía una sesión exportada previamente, la quitamos de en medio
    # para asegurarnos de que este login use el perfil persistente de Chrome
    # (donde realmente vas a escribir tu usuario/contraseña).
    if SESION_FILE.exists():
        SESION_FILE.unlink()

    print("Se abrirá Chrome. Inicia sesión con tu cuenta de Google de Kodland,")
    print("espera a que se vea la app con el desplegable de cursos y luego vuelve aquí.")
    with sync_playwright() as p:
        ctx, browser = abrir_contexto(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(cfg["url_app"])
        input("\nCuando veas la app funcionando, presiona ENTER para guardar la sesión... ")
        ctx.storage_state(path=str(SESION_FILE))
        cerrar_contexto(ctx, browser)
    print("Sesión guardada en sesion.json y en el perfil local.")
    print("Ahora puedes probar con: python avisador.py --una-vez")
    print("Ese mismo archivo 'sesion.json' es el que se copia al servidor en la nube.")


def main():
    ap = argparse.ArgumentParser(description="Avisador de grupos Kodland")
    ap.add_argument("--login", action="store_true", help="abrir Chrome para iniciar sesión")
    ap.add_argument("--probar", action="store_true", help="enviar notificación de prueba")
    ap.add_argument("--una-vez", action="store_true", help="revisar una sola vez")
    ap.add_argument("--ver", action="store_true", help="mostrar el navegador mientras revisa")
    ap.add_argument("--reset", action="store_true", help="borrar el estado guardado y empezar desde cero")
    args = ap.parse_args()

    cfg = cargar_config()

    if args.login:
        return modo_login(cfg)
    if args.probar:
        return notificar(cfg, "Avisador Kodland ✅", "¡Las notificaciones funcionan!")
    if args.reset:
        if ESTADO_FILE.exists():
            ESTADO_FILE.unlink()
        print("Estado borrado. La próxima revisión volverá a crear la línea base.")
        return
    if args.ver:
        global leer_todos_los_cursos
        original = leer_todos_los_cursos
        leer_todos_los_cursos = lambda c: original(c, headless=False)

    estado = cargar_json(ESTADO_FILE, {})
    if args.una_vez:
        revisar(cfg, estado)
        return

    log(f"Avisador iniciado. Revisando cada {cfg['intervalo_minutos']} min. Ctrl+C para salir.")
    while True:
        estado = revisar(cfg, estado)
        time.sleep(cfg["intervalo_minutos"] * 60)


if __name__ == "__main__":
    main()
