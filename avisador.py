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
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

import filtro
import tg

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
    # .strip(): al pegar un secreto es fácil que se cuele un espacio o salto de
    # línea, y con eso ntfy publica en otro tema (o rechaza el mensaje).
    def env(nombre):
        return (os.environ.get(nombre) or "").strip()

    if env("URL_APP"):
        cfg["url_app"] = env("URL_APP")
    if env("NTFY_TEMA"):
        cfg["ntfy_tema"] = env("NTFY_TEMA")
    if env("NTFY_SERVIDOR"):
        cfg["ntfy_servidor"] = env("NTFY_SERVIDOR")
    if env("NTFY_TOKEN"):
        cfg["ntfy_token"] = env("NTFY_TOKEN")
    if env("TELEGRAM_TOKEN"):
        cfg["telegram_token"] = env("TELEGRAM_TOKEN")
    if env("RELAY_URL"):
        cfg["relay_url"] = env("RELAY_URL")
    if env("RELAY_SECRETO"):
        cfg["relay_secreto"] = env("RELAY_SECRETO")
    if env("SESION_JSON") and not SESION_FILE.exists():
        SESION_FILE.write_text(env("SESION_JSON"), encoding="utf-8")

    if "PEGA_AQUI" in cfg.get("url_app", "") or not cfg.get("url_app"):
        sys.exit("Falta la URL de la app: ponla en config.json (url_app) o en el secreto URL_APP.")

    # Varios tutores: secreto TUTORES (o "tutores" en config.json) con una lista
    # [{"nombre": "...", "kt_id": "123", "ntfy_tema": "..."}, ...].
    # Sin eso, funciona como antes con un solo tutor (NTFY_TEMA + la sesión).
    if env("TUTORES"):
        try:
            tutores = json.loads(env("TUTORES"))
        except json.JSONDecodeError:
            sys.exit("El secreto TUTORES no es un JSON válido.")
    elif cfg.get("tutores"):
        tutores = cfg["tutores"]
    elif cfg.get("ntfy_tema"):
        tutores = [{"nombre": "principal", "ntfy_tema": cfg["ntfy_tema"]}]
    else:
        tutores = []

    cfg["tutores"] = []
    for i, t in enumerate(tutores, 1):
        tema = str(t.get("ntfy_tema") or "").strip()
        chat = str(t.get("telegram_chat") or "").strip()
        kt = str(t.get("kt_id") or "").strip() or None
        if "CAMBIA" in tema:
            tema = ""
        if not tema and not (chat and cfg.get("telegram_token")):
            sys.exit(f"Al tutor n.º {i} le falta un canal: 'ntfy_tema' o 'telegram_chat' (con TELEGRAM_TOKEN).")
        cfg["tutores"].append(
            {"nombre": str(t.get("nombre") or f"tutor {i}"), "kt_id": kt, "ntfy_tema": tema,
             "telegram_chat": chat, "pos": i}
        )
    if not cfg["tutores"]:
        sys.exit("Falta al menos un tutor: secreto TUTORES, o NTFY_TEMA / ntfy_tema.")
    cfg.setdefault("intervalo_minutos", 5)
    cfg.setdefault("heartbeat_horas", 24)
    cfg.setdefault("cursos_a_vigilar", [])
    cfg.setdefault("ignorar_lineas", [])
    return cfg


# ---------------------------------------------------------------- notificación

NOTIFICACIONES_FALLIDAS = 0
ULTIMO_FALLO_CUOTA = False


def cuota_ntfy(cfg):
    """Envíos que le quedan hoy a esta conexión en ntfy (el límite es por dirección
    de internet, 250 al día). None si no se pudo consultar."""
    try:
        url = cfg.get("ntfy_servidor", "https://ntfy.sh").rstrip("/") + "/v1/account"
        datos = json.loads(urllib.request.urlopen(url, timeout=10).read().decode("utf-8"))
        return int(datos["stats"]["messages_remaining"])
    except Exception:
        return None


def notificar(cfg, titulo, mensaje, prioridad=4, tags=("bell",), acciones=None, contar_fallo=True):
    """Envía a ntfy con reintentos. Devuelve True si llegó."""
    global NOTIFICACIONES_FALLIDAS, ULTIMO_FALLO_CUOTA
    ULTIMO_FALLO_CUOTA = False
    payload = {
        "topic": cfg["ntfy_tema"],
        "title": titulo,
        "message": mensaje[:3900],
        "priority": prioridad,
        "tags": list(tags),
        "click": cfg["url_app"],
    }
    if acciones:
        payload["actions"] = acciones
    # Con token de cuenta, ntfy limita por usuario y no por dirección de internet
    cabeceras = {"Content-Type": "application/json"}
    if cfg.get("ntfy_token"):
        cabeceras["Authorization"] = f"Bearer {cfg['ntfy_token']}"
    for intento in range(1, 4):
        req = urllib.request.Request(
            cfg.get("ntfy_servidor", "https://ntfy.sh"),
            data=json.dumps(payload).encode("utf-8"),
            headers=cabeceras,
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=15).read()
            log(f"Notificación enviada: {titulo}")
            return True
        except Exception as e:
            detalle = ""
            if hasattr(e, "read"):
                try:
                    detalle = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
            if "42908" in detalle:
                # Cuota diaria agotada: reintentar ahora no sirve. Lo que no se avisó
                # queda pendiente y se reenvía solo cuando la cuota se reinicia.
                log("Cuota diaria de ntfy agotada: los avisos pendientes se reenviarán después.")
                ULTIMO_FALLO_CUOTA = True
                return False
            log(f"ERROR enviando notificación (intento {intento}/3): {e} {detalle}")
            time.sleep(5 * intento)
    # El tema es secreto: solo mostramos su largo para poder diagnosticar
    log(f"No se pudo notificar. Largo del tema ntfy: {len(cfg['ntfy_tema'])} caracteres.")
    if contar_fallo:
        NOTIFICACIONES_FALLIDAS += 1
    return False


def avisar(cfg, tutor, titulo, mensaje, prioridad=4, tags=("bell",), grupo=None, info=""):
    """Manda un aviso al tutor por TODOS sus canales (Telegram y/o ntfy).
    grupo=(curso, codigo) añade el botón para postularse. True si llegó por alguno."""
    global NOTIFICACIONES_FALLIDAS
    llego = False
    if tutor.get("telegram_chat") and cfg.get("telegram_token"):
        curso, codigo = grupo if grupo else ("", None)
        botones = tg.botones_grupo(tutor["pos"], curso, codigo, cfg["url_app"])
        if tg.enviar(cfg["telegram_token"], tutor["telegram_chat"], titulo, mensaje, botones,
                     silencioso=prioridad <= 2):
            log(f"Telegram: {titulo}")
            llego = True
        else:
            log(f"ERROR: no se pudo enviar por Telegram: {titulo}")
    if tutor.get("ntfy_tema"):
        cfg_n = {**cfg, "ntfy_tema": tutor["ntfy_tema"]}
        acciones = acciones_para(cfg_n, tutor, grupo[0], grupo[1], info) if grupo else acciones_para(cfg_n, tutor, "", None)
        if notificar(cfg_n, titulo, mensaje, prioridad, tags, acciones, contar_fallo=False):
            llego = True
    if not llego and not ULTIMO_FALLO_CUOTA:
        NOTIFICACIONES_FALLIDAS += 1
    return llego


# ------------------------------------------------------------------ navegador

def sesion_para(kt_id):
    """Sesión de la app con el ID de tutor indicado. La app solo guarda ese ID
    en el navegador (localStorage 'kt_id'), así que basta cambiar ese valor."""
    sesion = json.loads(SESION_FILE.read_text(encoding="utf-8"))
    if not kt_id:
        return sesion
    for origen in sesion.get("origins", []):
        for item in origen.get("localStorage", []):
            if item.get("name") == "kt_id":
                item["value"] = kt_id
                return sesion
    if sesion.get("origins"):
        sesion["origins"][0].setdefault("localStorage", []).append({"name": "kt_id", "value": kt_id})
    return sesion


def kt_id_de_sesion():
    try:
        for origen in json.loads(SESION_FILE.read_text(encoding="utf-8")).get("origins", []):
            for item in origen.get("localStorage", []):
                if item.get("name") == "kt_id":
                    return str(item["value"])
    except (OSError, ValueError):
        pass
    return None


def ruta_estado(tutor):
    """El tutor de la sesión original conserva estado.json; los demás, uno propio."""
    kt = tutor.get("kt_id")
    if not kt or kt == kt_id_de_sesion():
        return ESTADO_FILE
    return BASE / f"estado_{hashlib.sha1(kt.encode()).hexdigest()[:8]}.json"


def abrir_contexto(p, headless, kt_id=None):
    """Devuelve (context, browser). 'browser' es None cuando se usa un perfil
    persistente (no hace falta cerrarlo aparte)."""
    args_anti_deteccion = ["--disable-blink-features=AutomationControlled"]

    if SESION_FILE.exists():
        # Modo portátil: sirve igual en Windows (PC) que en Linux (servidor en
        # la nube), porque la sesión viaja en un archivo de datos (cookies),
        # no en un perfil de Chrome atado al sistema operativo.
        # PW_CANAL=chrome usa el Chrome que ya trae el servidor de GitHub: no hay
        # que descargar ni instalar el navegador de Playwright (ahorra ~20 s).
        browser = p.chromium.launch(headless=headless, args=args_anti_deteccion,
                                    channel=os.environ.get("PW_CANAL") or None)
        ctx = browser.new_context(
            storage_state=sesion_para(kt_id), viewport={"width": 1200, "height": 900}
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


def texto_visible(page):
    """Lo que la app muestra en pantalla (sin el saludo con el nombre), para
    explicar por qué no apareció el selector: pausa de postulaciones, error, etc."""
    lineas = []
    for fr in page.frames:
        try:
            for l in fr.inner_text("body").splitlines():
                l = " ".join(l.split())
                if l and not l.lower().startswith("hola,") and l not in lineas:
                    lineas.append(l)
        except Exception:
            pass
    return (" / ".join(lineas) or "nada (página en blanco)")[:200]


def leer_todos_los_cursos(cfg, headless=True, kt_id=None):
    """Devuelve {curso: [lineas de texto]} o lanza excepción."""
    resultado = {}
    with sync_playwright() as p:
        ctx, browser = abrir_contexto(p, headless, kt_id)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            frame = None
            for intento in (1, 2):  # la app a veces tarda: se reintenta antes de dar la alarma
                page.goto(cfg["url_app"], wait_until="domcontentloaded", timeout=60000)
                frame = buscar_frame_con_select(page)
                if frame is not None:
                    break
                log(f"La app no mostró el selector de cursos (intento {intento}/2).")
            if frame is None:
                if "accounts.google.com" in page.url:
                    raise RuntimeError("SESION")
                raise RuntimeError(
                    "No se encontró el selector de cursos. La app muestra: «" + texto_visible(page) + "»"
                )

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
                "← volver a cursos",
                "postularme",
                "✓ ya te postulaste",  # aparece al postularte; no es un grupo nuevo
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


def es_codigo_grupo(linea):
    """Código de grupo, p. ej. COL13688_MI-11 o PRM_COL14171_JU-13."""
    return "_" in linea and " " not in linea and any(c.isdigit() for c in linea) and len(linea) < 30


def parsear_grupos(lineas):
    """Agrupa las líneas de la app en grupos: nombre, horario, inicio, código y
    las líneas originales de cada uno (para poder marcarlas como avisadas)."""
    grupos, g = [], None

    def vacio():
        return {"nombre": "", "horario": "", "inicio": "", "id": "", "duracion": None, "lineas": []}

    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        if linea.startswith("[") and "]" in linea:
            if g and (g["id"] or g["nombre"]):
                grupos.append(g)
            g = vacio()
            g["nombre"] = linea.split("]")[1].split("[")[0].strip() or linea
            dur = re.search(r"\[(\d+)\s*min\]", linea)
            g["duracion"] = int(dur.group(1)) if dur else None
        else:
            if "🕐" in linea:
                campo, valor = "horario", linea.replace("🕐", "").strip()
            elif "📅" in linea:
                campo, valor = "inicio", linea.replace("📅", "").strip()
            elif es_codigo_grupo(linea):
                campo, valor = "id", linea
            else:
                continue
            # Un dato que llega cuando ya estaba lleno (o tras el código) es de otro grupo
            if g is None or g[campo] or (campo != "id" and g["id"]):
                if g and (g["id"] or g["nombre"] or g["horario"]):
                    grupos.append(g)
                g = vacio()
            g[campo] = valor
        g["lineas"].append(linea)
    if g and (g["id"] or g["nombre"] or g["horario"]):
        grupos.append(g)
    return grupos


def formatear_grupo(g):
    """Texto de un solo grupo para el aviso."""
    filas = [g["nombre"] or "Grupo nuevo"]
    if g["horario"]:
        filas.append(f"🕐 {g['horario']}")
    if g.get("duracion"):
        h = filtro._horario(g)  # (día, minuto de inicio, duración) si el horario trae hora
        fin = f" (termina {filtro._hm(h[1] + g['duracion'])} COL)" if h and h[1] + g["duracion"] < 24 * 60 else ""
        filas.append(f"⏱️ {g['duracion']} min{fin}")
    if g["inicio"]:
        filas.append(f"📅 {g['inicio']}")
    if g["id"]:
        filas.append(f"🏷️ {g['id']}")
    return "\n".join(filas)


def formatear_grupos(grupos):
    """Resumen de varios grupos en un solo aviso (máximo 10)."""
    resultado = []
    for i, g in enumerate(grupos[:10], 1):
        resultado.append(f"{i}️⃣ " + formatear_grupo(g).replace("\n", "\n   "))
        resultado.append("")
    if len(grupos) > 10:
        resultado.append(f"… y {len(grupos) - 10} grupos más")
    return "\n".join(resultado)


MAX_AVISOS_POR_CURSO = 6


def enlace_confirmar(cfg, tutor, curso, grupo_id, info=""):
    """Enlace firmado que postula de verdad (lo recibe el mini-servicio de Apps
    Script). Solo va dentro del aviso '¿Confirmas?', nunca en el primero. No lleva
    ninguna llave: la firma (HMAC) se comprueba en el mini-servicio, que es quien
    guarda el token de GitHub."""
    base, secreto = cfg.get("relay_url"), cfg.get("relay_secreto")
    if not base or not secreto or not grupo_id:
        return None
    # Apps Script cambia por '?' todo carácter no ASCII de la URL (tildes, "·"...),
    # y la firma dejaba de coincidir. Por eso el enlace solo lleva ASCII: el curso
    # va en base64 y no lleva texto libre (el nombre y el horario solo se muestran
    # en el aviso, no hacen falta para postular).
    curso64 = base64.urlsafe_b64encode(curso.encode("utf-8")).decode("ascii").rstrip("=")
    ts = str(int(time.time()))
    campos = ["confirmar", str(tutor["pos"]), curso64, grupo_id, tutor["ntfy_tema"], ts]
    firma = hmac.new(secreto.encode("utf-8"), "|".join(campos).encode("utf-8"), hashlib.sha256).hexdigest()
    consulta = urllib.parse.urlencode(
        {"paso": "confirmar", "tutor": tutor["pos"], "curso64": curso64, "grupo": grupo_id,
         "tema": tutor["ntfy_tema"], "ts": ts, "firma": firma},
        quote_via=urllib.parse.quote,
    )
    return f"{base}?{consulta}"


def resumen_grupo(g):
    """Una línea para el aviso de confirmación: qué grupo vas a confirmar."""
    return " · ".join(x for x in (g["nombre"], g["horario"]) if x)


def acciones_para(cfg, tutor, curso, grupo_id, info=""):
    """Botones del aviso. '✅ Postularme' NO postula: al tocarlo, el propio celular
    publica en ntfy un segundo aviso '¿Confirmas?', y solo el botón de ese aviso
    postula. (Lo publica el teléfono y no un servidor porque ntfy limita los envíos
    por dirección de internet y los servidores de Google comparten la suya.)"""
    acciones = []
    confirmar = enlace_confirmar(cfg, tutor, curso, grupo_id, info)
    if confirmar:
        pregunta = {
            "topic": tutor["ntfy_tema"],
            "title": "⚠️ ¿Confirmas la postulación?",
            "message": "\n".join(x for x in (curso, f"🏷️ {grupo_id}", info, "",
                                              "Si es el grupo correcto, toca Confirmar. Si en 1 minuto no llega el aviso Postulando, el toque falló: usa Abrir app.") if x is not None),
            "priority": 4,
            "tags": ["warning"],
            "actions": [
                {"action": "http", "label": "✅ Confirmar postulación", "url": confirmar,
                 "method": "GET", "clear": True},
                # Cancelar: el celular publica un aviso que lo confirma (así se ve que
                # funcionó aunque el aviso de "¿Confirmas?" no se cierre solo)
                {"action": "http", "label": "❌ Cancelar postulación",
                 "url": cfg.get("ntfy_servidor") or "https://ntfy.sh",
                 "method": "POST",
                 "headers": {"Content-Type": "application/json"},
                 "body": json.dumps({
                     "topic": tutor["ntfy_tema"],
                     "title": "✖️ Postulación cancelada",
                     "message": f"{curso} · {grupo_id}\nNo se postuló a nada.",
                     "priority": 2,
                     "tags": ["x"],
                 }, ensure_ascii=False),
                 "clear": True},
            ],
        }
        acciones.append({
            "action": "http", "label": "✅ Postularme",
            "url": cfg.get("ntfy_servidor") or "https://ntfy.sh",
            "method": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(pregunta, ensure_ascii=False),
            "clear": False,  # el aviso del grupo se queda por si cancelas
        })
    acciones.append({"action": "view", "label": "🔗 Abrir app", "url": cfg["url_app"]})
    return acciones


def avisar_cambios(cfg, tutor, curso, lineas):
    """Un aviso por grupo nuevo, cada uno con su botón. Devuelve las líneas que
    NO se pudieron avisar, para que se reintenten en la próxima revisión."""
    grupos = parsear_grupos(lineas)
    # Con mucha cuota se avisa grupo por grupo (cada uno con su botón); con poca,
    # un solo aviso por curso, para no quedarse sin envíos a mitad del día.
    limite = MAX_AVISOS_POR_CURSO
    if tutor.get("telegram_chat") and cfg.get("telegram_token"):
        limite = 30  # Telegram no tiene el tope de 250 avisos al día de ntfy
    else:
        restante = cuota_ntfy(cfg)
        if restante is not None:
            limite = 15 if restante >= 150 else (MAX_AVISOS_POR_CURSO if restante >= 40 else 0)
    if not grupos or len(grupos) > limite or any(not g["id"] for g in grupos):
        cuerpo = formatear_grupos(grupos) or "\n".join(lineas[:15])
        ok = avisar(cfg, tutor, f"🆕 Grupos nuevos: {curso}", cuerpo)
        return [] if ok else list(lineas)
    fallidas = []
    for g in grupos:
        ok = avisar(cfg, tutor, f"🆕 {curso}", formatear_grupo(g),
                    grupo=(curso, g["id"]), info=resumen_grupo(g))
        if not ok:
            fallidas.extend(g["lineas"])
    return fallidas


def quitar_lineas(lineas, quitar):
    pendiente, resultado = Counter(quitar), []
    for l in lineas:
        if pendiente[l] > 0:
            pendiente[l] -= 1
        else:
            resultado.append(l)
    return resultado


def revisar(cfg, estado, tutor):
    """Revisa la app con el ID de un tutor y le avisa a él, por sus propios canales."""
    ruta = ruta_estado(tutor)
    log("Revisando...")
    try:
        actual = leer_todos_los_cursos(cfg, kt_id=tutor.get("kt_id"))
    except RuntimeError as e:
        if str(e) == "SESION":
            msg = "No se pudo entrar a la app de Kodland. Revisa que el ID de tutor siga siendo válido."
        else:
            msg = str(e)
        log(f"ERROR: {msg}")
        if not estado.get("error_avisado"):
            avisar(cfg, tutor, "Avisador Kodland: problema", msg, prioridad=3, tags=("warning",))
            estado["error_avisado"] = True
            guardar_json(ruta, estado)
        return estado
    except Exception as e:
        log(f"ERROR inesperado: {e}")
        return estado

    anterior = estado.get("cursos", {})
    ahora = datetime.now(timezone.utc)
    pendientes = {}  # curso -> líneas que no se pudieron avisar (se reintentan)
    if not anterior:
        log(f"Primera revisión: guardada línea base de {len(actual)} cursos (sin notificar).")
    else:
        cambios = comparar(anterior, actual)
        for curso, lineas in cambios.items():
            # Lo que no se pudo avisar no se da por visto: se reintenta después
            pendientes[curso] = avisar_cambios(cfg, tutor, curso, lineas)
        if not cambios:
            log("Sin novedades.")

    # Aviso de "sigo activo" cada N horas: si un día no llega, algo falló.
    heartbeat = leer_fecha(estado.get("ultimo_heartbeat"))
    if heartbeat is None:
        heartbeat = ahora  # empieza a contar desde ahora, sin avisar
    elif (ahora - heartbeat).total_seconds() >= float(cfg["heartbeat_horas"]) * 3600:
        total = sum(len(v) for v in actual.values())
        if avisar(
            cfg, tutor,
            "Avisador Kodland: sigo activo",
            f"Revisando {len(actual)} cursos ({total} líneas visibles ahora)." + _texto_cuota(cfg, tutor),
            prioridad=2,
            tags=("signal_strength",),
        ):
            heartbeat = ahora

    # Si un curso desaparece temporalmente, conservamos su último estado
    for curso, lineas in actual.items():
        anterior[curso] = quitar_lineas(lineas, pendientes.get(curso, []))
    # Sin "ultima_revision": así estado.json solo cambia cuando hay algo nuevo
    # y el repositorio no se llena de commits cada 5 minutos.
    estado = {"cursos": anterior, "ultimo_heartbeat": heartbeat.isoformat(timespec="seconds")}
    guardar_json(ruta, estado)
    return estado


def _texto_cuota(cfg, tutor):
    if not tutor.get("ntfy_tema"):
        return ""
    restante = cuota_ntfy(cfg)
    return "" if restante is None else f" Avisos de ntfy que quedan hoy: {restante} de 250."


def leer_fecha(texto):
    try:
        fecha = datetime.fromisoformat(texto)
    except (TypeError, ValueError):
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)


def _postular_en_frame(frame, curso, grupo, simular=False):
    """Hace la postulación sobre una página de la app YA abierta: elige el curso,
    toca 'Postularme' y luego 'Confirmar postulación'. Devuelve (ok, mensaje)."""
    try:
        frame.select_option("select", value="")  # limpia lo que hubiera de una vez anterior
    except Exception:
        pass
    try:
        frame.select_option("select", label=curso)
    except Exception:
        return False, f"No encontré el curso «{curso}» en tu lista."
    try:
        frame.wait_for_function(
            "document.querySelector('#groups .card') || (document.querySelector('#groups .state')"
            " && !/Buscando/i.test(document.querySelector('#groups .state').textContent))",
            timeout=20000,
        )
    except Exception:
        pass  # si no se detecta, seguimos: más abajo se avisa si el grupo no está

    tarjetas = frame.locator("#groups > .card")
    indice = None
    for i in range(tarjetas.count()):
        if tarjetas.nth(i).locator(".grp").first.inner_text().strip() == grupo:
            indice = i
            break
    if indice is None:
        return False, f"El grupo {grupo} ya no está disponible (puede que alguien lo tomara)."

    tarjeta = tarjetas.nth(indice)
    if tarjeta.locator(".done").count():
        return True, f"Ya estabas postulado al grupo {grupo} ✓"
    if frame.locator(f"#h{indice}").count():
        return False, "Este grupo pide que escribas tu horario: postúlate desde la app."

    tarjeta.locator(".apply").click()
    confirmar = tarjeta.locator(".send")
    confirmar.wait_for(state="visible", timeout=10000)
    if simular:
        return True, f"SIMULACIÓN: el grupo {grupo} está listo para confirmar. No se envió nada."

    frame.evaluate("document.getElementById('toast').textContent = ''")
    confirmar.click()
    respuesta, fin = "", time.time() + 30
    while time.time() < fin and not respuesta:
        time.sleep(0.25)
        respuesta = frame.locator("#toast").inner_text().strip()
    # Si la postulación se aceptó, la tarjeta pasa a "✓ Ya te postulaste"
    aceptada = frame.locator("#groups > .card").nth(indice).locator(".done").count() > 0
    return aceptada, respuesta or ("Postulación enviada" if aceptada else "La app no respondió a tiempo.")


class NavegadorCaliente:
    """Deja un navegador abierto por tutor, con la app ya cargada, para postular en
    pocos segundos (sin esperar los ~17 s de arrancar el navegador y cargar la app).
    Se renueva si pasa mucho tiempo o si algo falla."""

    VIDA = 20 * 60  # segundos antes de recargar una página que lleva mucho abierta

    def __init__(self, cfg):
        self.cfg, self.p, self.sesiones = cfg, None, {}

    def preparar(self, tutor):
        """Abre (o vuelve a abrir) la app de este tutor. Devuelve el frame, o None."""
        self.cerrar_uno(tutor)
        if self.p is None:
            self.p = sync_playwright().start()
        ctx, browser = abrir_contexto(self.p, True, tutor.get("kt_id"))
        frame = None
        try:
            page = ctx.new_page()
            page.goto(self.cfg["url_app"], wait_until="domcontentloaded", timeout=60000)
            frame = buscar_frame_con_select(page)
        except Exception:
            pass
        if frame is None:
            cerrar_contexto(ctx, browser)
            return None
        self.sesiones[tutor["pos"]] = {"ctx": ctx, "browser": browser, "frame": frame, "desde": time.time()}
        return frame

    def frame(self, tutor):
        s = self.sesiones.get(tutor["pos"])
        if s is None or time.time() - s["desde"] > self.VIDA:
            return self.preparar(tutor)
        return s["frame"]

    def preparar_todos(self, tutores):
        for t in tutores:
            log(f"Dejando lista la app de un tutor: {'ok' if self.preparar(t) else 'no se pudo'}")

    def mantener(self, tutores):
        """Se llama en los ratos libres: renueva las páginas que llevan mucho abiertas."""
        for t in tutores:
            s = self.sesiones.get(t["pos"])
            if s is None or time.time() - s["desde"] > self.VIDA:
                self.preparar(t)

    def cerrar_uno(self, tutor):
        s = self.sesiones.pop(tutor["pos"], None)
        if s:
            cerrar_contexto(s["ctx"], s["browser"])

    def cerrar(self):
        for pos in list(self.sesiones):
            s = self.sesiones.pop(pos)
            cerrar_contexto(s["ctx"], s["browser"])
        if self.p is not None:
            try:
                self.p.stop()
            except Exception:
                pass
            self.p = None


def postular(cfg, tutor, curso, grupo, simular=False, calientes=None):
    """Se postula a un grupo como lo haría una persona. Devuelve (ok, mensaje).
    Con simular=True llega hasta el panel de confirmación y se detiene.
    Con `calientes` reutiliza una app ya abierta (mucho más rápido)."""
    if calientes is not None:
        for intento in (1, 2):
            frame = calientes.frame(tutor) if intento == 1 else calientes.preparar(tutor)
            if frame is None:
                return False, "No se pudo entrar a la app de Kodland."
            try:
                return _postular_en_frame(frame, curso, grupo, simular)
            except Exception:
                # La página pudo caducar: se abre otra y se reintenta una vez. Si la
                # postulación ya se había enviado, el reintento verá 'Ya te postulaste'.
                calientes.cerrar_uno(tutor)
        return False, "La app no respondió; inténtalo de nuevo."

    with sync_playwright() as p:
        ctx, browser = abrir_contexto(p, True, tutor.get("kt_id"))
        try:
            page = ctx.new_page()
            page.goto(cfg["url_app"], wait_until="domcontentloaded", timeout=60000)
            frame = buscar_frame_con_select(page)
            if frame is None:
                return False, "No se pudo entrar a la app de Kodland."
            return _postular_en_frame(frame, curso, grupo, simular)
        finally:
            cerrar_contexto(ctx, browser)


def modo_postular(cfg, args):
    tutores = cfg["tutores"]
    try:
        tutor = tutores[int(args.tutor) - 1]
        assert int(args.tutor) >= 1
    except (TypeError, ValueError, IndexError, AssertionError):
        sys.exit("Tutor inválido.")
    grupo, curso = (args.grupo or "").strip(), (args.curso or "").strip()
    # Estos datos pueden venir de fuera (botón del celular): se validan
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,40}", grupo) or not curso or len(curso) > 80:
        sys.exit("Grupo o curso inválido.")

    log("Postulando…")
    if not args.simular:
        avisar(
            cfg, tutor,
            "⏳ Postulando…",
            f"{curso}\n🏷️ {grupo}\nEn unos 30 segundos te digo cómo salió.",
            prioridad=2, tags=("hourglass_flowing_sand",),
        )
    try:
        ok, msg = postular(cfg, tutor, curso, grupo, simular=args.simular)
    except Exception as e:
        ok, msg = False, f"Error inesperado: {type(e).__name__}"
    log(("OK: " if ok else "NO SE PUDO: ") + msg)
    if not args.simular:
        avisar(
            cfg, tutor,
            "✅ Postulación enviada" if ok else "❌ No se pudo postular",
            f"{curso}\n🏷️ {grupo}\n{msg}",
            tags=("white_check_mark",) if ok else ("x",),
        )
    sys.exit(0 if ok else 1)


def modo_listar(cfg, args):
    """Manda al celular TODOS los grupos disponibles ahora, cada uno con su botón.
    No toca el estado del vigilante, así que no afecta a los avisos futuros."""
    tutores = cfg["tutores"]
    try:
        tutor = tutores[int(args.tutor or 1) - 1]
        assert int(args.tutor or 1) >= 1
    except (ValueError, IndexError, AssertionError):
        sys.exit("Tutor inválido.")
    if args.canal == "telegram":
        if not (tutor.get("telegram_chat") and cfg.get("telegram_token")):
            sys.exit("Ese tutor no tiene Telegram configurado.")
        tutor = {**tutor, "ntfy_tema": ""}  # solo Telegram: no gasta la cuota de ntfy

    actual = leer_todos_los_cursos(cfg, kt_id=tutor.get("kt_id"))
    lista, vistos = [], set()
    for curso, lineas in actual.items():
        for g in parsear_grupos(lineas):
            if g["id"] and (curso, g["id"]) not in vistos:
                vistos.add((curso, g["id"]))
                lista.append((curso, g))
    por_curso = Counter(c for c, _ in lista)
    log(f"Grupos disponibles: {len(lista)}")
    for curso, n in por_curso.items():
        log(f"  {curso}: {n}")
    if args.contar:
        return

    enviados = 0
    for curso, g in lista:
        if avisar(cfg, tutor, f"📋 {curso}", formatear_grupo(g), prioridad=3,
                  grupo=(curso, g["id"]), info=resumen_grupo(g)):
            enviados += 1
        # ntfy limita la velocidad de envío; Telegram admite 1 mensaje por segundo por chat
        time.sleep(1.2 if (tutor.get("telegram_chat") and not tutor.get("ntfy_tema")) else 2.5)
    avisar(cfg, tutor, "📋 Lista completa enviada",
              f"{enviados} de {len(lista)} grupos disponibles ahora.\n" +
              "\n".join(f"• {c}: {n}" for c, n in por_curso.items()),
              prioridad=3)


def estado_actual(tutor):
    """Los grupos que el vigilante vio en su última revisión. En GitHub se lee la
    versión más reciente del repositorio (el vigilante la actualiza sola); si no se
    puede, se usa la copia local."""
    ruta = ruta_estado(tutor)
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if repo:
        try:
            url = f"https://raw.githubusercontent.com/{repo}/main/{ruta.name}"
            datos = json.loads(urllib.request.urlopen(url, timeout=10).read().decode("utf-8"))
            if datos.get("cursos"):
                return datos
        except Exception:
            pass
    return cargar_json(ruta, {})


def buscar_grupos(cfg, tutor, texto):
    """Filtra los grupos actuales por día y hora (ver filtro.py) para el bot de Telegram."""
    filtros = filtro.parsear_consulta(texto)
    if not filtros:
        return {"error": "Dime qué días y horas puedes. Ejemplo: /grupos sábado y domingo de 8 a 12\nEscribe /ayuda para ver más."}
    grupos = [(curso, g)
              for curso, lineas in estado_actual(tutor).get("cursos", {}).items()
              for g in parsear_grupos(lineas) if g["id"]]
    si, sin_hora = filtro.buscar(grupos, filtros)
    if not si and all(not f.dias and f.ini is None for f in filtros):
        # Ni días ni horas y ninguna coincidencia con un curso: no era una búsqueda
        return {"error": "No entendí. Dime qué días y horas puedes, por ejemplo: /grupos sábado 8 a 12\nEscribe /ayuda para ver más."}
    return {
        "resumen": filtro.explicar(filtros),
        "total": len(grupos),
        "sin_hora": len(sin_hora),
        "grupos": [(curso, formatear_grupo(g), g["id"]) for curso, g in si],
    }


def modo_bot(cfg, args):
    """Atiende los toques de los botones de Telegram durante --minutos."""
    if not cfg.get("telegram_token"):
        sys.exit("Falta el token de Telegram (secreto TELEGRAM_TOKEN o 'telegram_token' en config.json).")
    chats = {t["telegram_chat"]: t for t in cfg["tutores"] if t.get("telegram_chat")}
    if not chats:
        # Sin chats configurados el bot igual responde a quien le escriba con su ID
        # de chat, que es justo lo que hace falta para configurarlo.
        log("Ningún tutor tiene 'telegram_chat' todavía: el bot solo dirá el ID de chat de quien le escriba.")
    cal = NavegadorCaliente(cfg)
    tutores_bot = list(chats.values())
    try:
        tg.bucle(
            cfg, chats,
            lambda c, tutor, curso, grupo: postular(c, tutor, curso, grupo, calientes=cal),
            log, minutos=float(args.minutos or 330),
            al_iniciar=lambda: cal.preparar_todos(tutores_bot),
            en_reposo=lambda: cal.mantener(tutores_bot),
            buscar_fn=buscar_grupos,
        )
    finally:
        cal.cerrar()


def modo_chats(cfg):
    """Muestra los chats que le han escrito al bot (para saber el ID de cada persona)."""
    if not cfg.get("telegram_token"):
        sys.exit("Falta el token de Telegram.")
    vistos = {}
    for u in tg.llamar(cfg["telegram_token"], "getUpdates", timeout=0):
        m = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
        chat = m.get("chat") or {}
        if chat.get("id"):
            vistos[chat["id"]] = chat.get("first_name") or chat.get("title") or ""
    if not vistos:
        print("Nadie le ha escrito al bot todavía: abre el bot en Telegram y toca Iniciar (/start).")
    for cid, nombre in vistos.items():
        print(f"ID de chat {cid}  ({nombre})")


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
    ap.add_argument("--postular", action="store_true", help="postularse a un grupo (con --tutor, --curso y --grupo)")
    ap.add_argument("--tutor", help="posición del tutor en la lista (1, 2, ...)")
    ap.add_argument("--curso", help="nombre del curso, tal como sale en la app")
    ap.add_argument("--grupo", help="código del grupo, p. ej. COL13688_MI-11")
    ap.add_argument("--simular", action="store_true", help="con --postular: llega hasta confirmar, pero no envía nada")
    ap.add_argument("--listar", action="store_true",
                    help="enviar al celular todos los grupos disponibles ahora (con --tutor N; --contar solo cuenta)")
    ap.add_argument("--contar", action="store_true", help="con --listar: solo contar, sin enviar")
    ap.add_argument("--canal", choices=["telegram"], help="con --listar: enviar solo por ese canal")
    ap.add_argument("--probar-boton", action="store_true",
                    help="envía un aviso con botón Postularme sobre un grupo que NO existe (prueba sin efectos)")
    ap.add_argument("--bot", action="store_true", help="atender los botones de Telegram (con --minutos)")
    ap.add_argument("--minutos", help="con --bot: cuánto tiempo escuchar (por defecto 330)")
    ap.add_argument("--telegram-chats", action="store_true", help="ver el ID de chat de quien le escribió al bot")
    args = ap.parse_args()

    cfg = cargar_config()

    if args.login:
        return modo_login(cfg)
    if args.bot:
        return modo_bot(cfg, args)
    if args.telegram_chats:
        return modo_chats(cfg)
    if args.postular:
        return modo_postular(cfg, args)
    if args.listar:
        return modo_listar(cfg, args)
    if args.probar_boton:
        t = cfg["tutores"][0]
        ok = avisar(
            cfg, t,
            "🧪 Prueba del botón",
            "Grupo inventado: no se postula a nada real.\n1) Toca Postularme  2) te pregunta '¿Confirmas?'  3) toca Confirmar (o Cancelar)\n4) te dice que ese grupo no existe.",
            grupo=("Unity", "PRUEBA_0-0"), info="Grupo inventado de prueba",
        )
        sys.exit(0 if ok else 1)
    tutores = cfg["tutores"]
    if args.probar:
        # Solo al primer tutor (el tuyo): así no molestas al resto al probar.
        ok = avisar(cfg, tutores[0], "Avisador Kodland ✅", "¡Las notificaciones funcionan!")
        sys.exit(0 if ok else 1)
    if args.reset:
        for t in tutores:
            ruta_estado(t).unlink(missing_ok=True)
        print("Estado borrado. La próxima revisión volverá a crear la línea base.")
        return
    if args.ver:
        global leer_todos_los_cursos
        original = leer_todos_los_cursos
        leer_todos_los_cursos = lambda c, kt_id=None: original(c, headless=False, kt_id=kt_id)

    def revisar_todos():
        for i, t in enumerate(tutores, 1):
            if len(tutores) > 1:
                log(f"— Tutor {i} de {len(tutores)} —")
            revisar(cfg, cargar_json(ruta_estado(t), {}), t)

    if args.una_vez:
        revisar_todos()
        # Código de salida 1 si algo no se pudo notificar: en GitHub Actions la
        # ejecución queda en rojo en vez de "verde" silencioso.
        sys.exit(1 if NOTIFICACIONES_FALLIDAS else 0)

    log(f"Avisador iniciado. Revisando cada {cfg['intervalo_minutos']} min. Ctrl+C para salir.")
    while True:
        revisar_todos()
        time.sleep(cfg["intervalo_minutos"] * 60)


if __name__ == "__main__":
    main()
