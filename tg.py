"""
Canal de Telegram para el avisador de grupos Kodland.

- enviar(): manda un aviso a un chat, con botones dentro del mensaje.
- bucle(): bot que atiende los toques de los botones (long polling), pensado para
  correr en GitHub Actions igual que el vigilante.

Postular lleva dos pasos, para que un toque accidental no te postule. Todo ocurre
en el MISMO mensaje, que va cambiando:

    aviso del grupo      [✅ Postularme] [🔗 Abrir app]
      -> ⚠️ ¿Confirmas?  [✅ Confirmar postulación] [❌ Cancelar postulación]
      -> ⏳ Postulando…
      -> ✅ Postulación enviada   /   ❌ No se pudo postular

El estado se lee del propio texto del mensaje, así un botón viejo o repetido
nunca hace nada por error.
"""

import html
import json
import time
import urllib.error
import urllib.request

API = "https://api.telegram.org"

PREGUNTA = "⚠️ ¿Confirmas la postulación?"
EJECUTANDO = "⏳ Postulando… (unos 30 segundos)"
EXITO = "✅ Postulación enviada"
FALLO = "❌ No se pudo postular"
_MARCAS = ("⚠️ ¿Confirmas", "⏳ Postulando", "✅ Postulación enviada", "❌ No se pudo postular")


class ErrorTelegram(Exception):
    def __init__(self, codigo, descripcion, reintentar=0):
        super().__init__(f"{codigo} {descripcion}")
        self.codigo, self.descripcion, self.reintentar = codigo, descripcion, reintentar


def llamar(token, metodo, tiempo=30, **params):
    """Llama a la API de Telegram. Nunca deja escapar el token en los errores."""
    req = urllib.request.Request(
        f"{API}/bot{token}/{metodo}",
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=tiempo) as r:
            datos = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            datos = json.loads(e.read().decode("utf-8"))
        except Exception:
            datos = {"ok": False, "error_code": e.code, "description": "error HTTP"}
    except Exception as e:  # sin conexión, tiempo agotado...
        raise ErrorTelegram(0, "sin conexión (" + type(e).__name__ + ")")
    if not datos.get("ok"):
        raise ErrorTelegram(
            datos.get("error_code", 0),
            str(datos.get("description", "")),
            (datos.get("parameters") or {}).get("retry_after", 0),
        )
    return datos["result"]


def botones_grupo(pos, curso, grupo_id, url_app):
    """Teclado del aviso de un grupo. callback_data admite 64 bytes como máximo."""
    fila = []
    dato = f"p|{pos}|{grupo_id}|{curso}"
    if grupo_id and len(dato.encode("utf-8")) <= 64:
        fila.append({"text": "✅ Postularme", "callback_data": dato})
    fila.append({"text": "🔗 Abrir app", "url": url_app})
    return [fila]


def enviar(token, chat, titulo, texto="", botones=None, silencioso=False):
    """Envía un aviso. Devuelve True si llegó."""
    cuerpo = f"<b>{html.escape(titulo)}</b>" + (f"\n{html.escape(texto)}" if texto else "")
    params = {"chat_id": chat, "text": cuerpo[:4000], "parse_mode": "HTML",
              "disable_web_page_preview": True, "disable_notification": silencioso}
    if botones:
        params["reply_markup"] = {"inline_keyboard": botones}
    for intento in range(1, 4):
        try:
            llamar(token, "sendMessage", **params)
            return True
        except ErrorTelegram as e:
            if e.codigo == 429:
                time.sleep(min(e.reintentar or 3, 30) + 1)
            elif e.codigo in (400, 401, 403, 404):
                return False  # chat no válido, bot bloqueado, token incorrecto: reintentar no sirve
            else:
                time.sleep(2 * intento)
    return False


# ------------------------------------------------------------------ el bot

def _estado(texto):
    if PREGUNTA[:12] in texto:
        return "pregunta"
    if "⏳ Postulando" in texto or "✅ Postulación enviada" in texto or "❌ No se pudo postular" in texto:
        return "terminada"
    return "fresca"


def _base(texto):
    """El aviso original, sin lo que el bot le fue añadiendo al final."""
    corte = len(texto)
    for marca in _MARCAS:
        i = texto.find("\n\n" + marca)
        if i != -1:
            corte = min(corte, i)
    return texto[:corte]


def _atender_toque(cfg, cq, chats, postular_fn, log):
    token = cfg["telegram_token"]

    def responder(texto=""):
        try:
            llamar(token, "answerCallbackQuery", callback_query_id=cq["id"], text=texto[:180])
        except ErrorTelegram:
            pass

    mensaje = cq.get("message") or {}
    chat = str((mensaje.get("chat") or {}).get("id", ""))
    tutor = chats.get(chat)
    if tutor is None:
        return responder("No autorizado.")
    partes = (cq.get("data") or "").split("|", 3)
    if len(partes) != 4:
        return responder("Botón no válido.")
    op, pos, grupo, curso = partes
    if pos != str(tutor["pos"]):
        return responder("Este botón no es tuyo.")

    texto, entidades = mensaje.get("text") or "", mensaje.get("entities")
    base, estado = _base(texto), _estado(texto)
    teclado_original = botones_grupo(pos, curso, grupo, cfg["url_app"])

    def editar(nuevo, teclado):
        params = {"chat_id": chat, "message_id": mensaje["message_id"], "text": nuevo,
                  "reply_markup": {"inline_keyboard": teclado}}
        if entidades:
            params["entities"] = entidades  # conserva el título en negrita
        try:
            llamar(token, "editMessageText", **params)
        except ErrorTelegram as e:
            if "not modified" not in e.descripcion:
                raise

    if op == "p":
        if estado != "fresca":
            return responder("Este botón ya no está activo.")
        responder()
        editar(base + "\n\n" + PREGUNTA, [
            [{"text": "✅ Confirmar postulación", "callback_data": f"c|{pos}|{grupo}|{curso}"}],
            [{"text": "❌ Cancelar postulación", "callback_data": f"x|{pos}|{grupo}|{curso}"}],
        ])
    elif op == "x":
        if estado != "pregunta":
            return responder("Este botón ya no está activo.")
        responder("Cancelado: no se postuló a nada.")
        editar(base, teclado_original)
    elif op == "c":
        if estado != "pregunta":
            return responder("Este botón ya no está activo.")
        responder("Postulando…")
        editar(base + "\n\n" + EJECUTANDO, [])
        log("Postulando desde Telegram…")
        inicio = time.time()
        try:
            ok, motivo = postular_fn(cfg, tutor, curso, grupo)
        except Exception as e:
            ok, motivo = False, "Error inesperado: " + type(e).__name__
        seg = round(time.time() - inicio)
        log(("OK: " if ok else "NO SE PUDO: ") + motivo + f" ({seg} s)")
        editar(
            base + "\n\n" + (EXITO if ok else FALLO) + "\n" + motivo + f"\n⏱️ {seg} s",
            [] if ok else [[{"text": "🔗 Abrir app", "url": cfg["url_app"]}]],
        )
    else:
        responder("Botón no válido.")


MAX_RESULTADOS = 20


def _buscar(cfg, chat, tutor, texto, buscar_fn):
    """Filtra los grupos por día y hora y manda cada coincidencia con su botón."""
    token = cfg["telegram_token"]
    r = buscar_fn(cfg, tutor, texto)
    if r.get("error"):
        return enviar(token, chat, "🔎 No entendí", r["error"], silencioso=True)
    grupos, extra = r["grupos"], r.get("sin_hora", 0)
    nota = f"\n({extra} grupos no traen hora y no se pudieron comparar: míralos en la app.)" if extra else ""
    if not grupos:
        return enviar(token, chat, f"🔎 {r['resumen']}",
                      f"No encontré grupos con ese horario, de {r['total']} que hay ahora. "
                      f"Prueba con más días o una ventana más amplia.{nota}", silencioso=True)
    mostrar = grupos[:MAX_RESULTADOS]
    enviar(token, chat, f"🔎 {r['resumen']}",
           f"{len(grupos)} grupos caben en ese horario (de {r['total']} que hay ahora)."
           + (f" Te muestro los primeros {len(mostrar)}: acota con un curso o una franja." if len(grupos) > len(mostrar) else "")
           + nota, silencioso=True)
    for curso, texto_grupo, gid in mostrar:
        enviar(token, chat, f"📋 {curso}", texto_grupo,
               botones_grupo(tutor["pos"], curso, gid, cfg["url_app"]), silencioso=True)
        time.sleep(1.1)  # Telegram admite ~1 mensaje por segundo en un mismo chat


def _atender_mensaje(cfg, m, chats, log, buscar_fn=None):
    """/start, /ayuda, /grupos ... y texto libre. A un chat desconocido le dice su ID,
    para poder añadirlo a la configuración (sin darle acceso a nada)."""
    token, chat = cfg["telegram_token"], str((m.get("chat") or {}).get("id", ""))
    if not chat:
        return
    tutor = chats.get(chat)
    if tutor is None:
        return enviar(token, chat, "Tu ID de chat", f"{chat}\nPásaselo a quien configura el avisador.", silencioso=True)
    texto = (m.get("text") or "").strip()
    comando = texto.split()[0].split("@")[0].lower() if texto.startswith("/") else ""
    if comando == "/start":
        enviar(token, chat, "✅ Conectado",
               "Aquí te llegarán los grupos nuevos de Kodland, con botones para postularte.\n\n" + _ayuda(), silencioso=True)
    elif comando in ("/ayuda", "/help"):
        enviar(token, chat, "🔎 Filtrar grupos", _ayuda(), silencioso=True)
    elif buscar_fn is None:
        enviar(token, chat, "🔎 Filtrar grupos", "Esta función no está disponible ahora.", silencioso=True)
    elif comando in ("", "/grupos", "/buscar", "/filtrar"):
        _buscar(cfg, chat, tutor, texto, buscar_fn)
    else:
        enviar(token, chat, "🤔", "No conozco ese comando. Escribe /ayuda.", silencioso=True)


def _ayuda():
    import filtro
    return filtro.AYUDA


def atender(cfg, update, chats, postular_fn, log, buscar_fn=None):
    if update.get("callback_query"):
        _atender_toque(cfg, update["callback_query"], chats, postular_fn, log)
    elif update.get("message"):
        _atender_mensaje(cfg, update["message"], chats, log, buscar_fn)


def bucle(cfg, chats, postular_fn, log, minutos=330, al_iniciar=None, en_reposo=None, buscar_fn=None):
    """Escucha los toques durante `minutos`. chats: {id_de_chat: tutor}.
    al_iniciar: se llama una vez antes de escuchar (p. ej. dejar la app lista).
    en_reposo: se llama cuando pasa un rato sin toques (mantenimiento)."""
    token = cfg["telegram_token"]
    try:
        llamar(token, "deleteWebhook")  # si hubiera un webhook, getUpdates no funciona
    except ErrorTelegram:
        pass
    try:  # menú de comandos que aparece al escribir "/" en el chat
        llamar(token, "setMyCommands", commands=[
            {"command": "grupos", "description": "Buscar grupos por día y hora"},
            {"command": "ayuda", "description": "Cómo usar el filtro"},
        ])
    except ErrorTelegram:
        pass
    if al_iniciar:
        try:
            al_iniciar()
        except Exception as e:
            log("No se pudo preparar la app al iniciar: " + type(e).__name__)
    fin, offset, errores = time.time() + minutos * 60, None, 0
    log("Bot de Telegram escuchando…")
    while time.time() < fin:
        params = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        try:
            novedades = llamar(token, "getUpdates", tiempo=40, **params)
            errores = 0
        except ErrorTelegram as e:
            errores += 1
            log(f"Telegram no respondió bien: {e.codigo} {e.descripcion[:60]}")
            time.sleep(min(30, 2 * errores))
            continue
        if not novedades and en_reposo:
            try:
                en_reposo()
            except Exception as e:
                log("Mantenimiento de la app: " + type(e).__name__)
        for u in novedades:
            offset = u["update_id"] + 1
            try:
                atender(cfg, u, chats, postular_fn, log, buscar_fn)
            except Exception as e:
                log("Error atendiendo un toque: " + type(e).__name__)
    if offset is not None:  # confirma lo atendido para que la próxima ejecución no lo repita
        try:
            llamar(token, "getUpdates", timeout=0, offset=offset)
        except ErrorTelegram:
            pass
    log("Bot de Telegram: fin del turno.")
