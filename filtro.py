"""
Filtro de grupos por día y hora, escrito como lo diría una persona:

    sábado y domingo de 8 a 12
    lunes a viernes tarde
    fin de semana después de las 2
    roblox sábado mañana
    sábado 8-12; domingo 14-18        (el ; junta varios filtros)

La hora que se compara es la hora de Colombia (COL) que trae cada grupo. Un grupo
solo coincide si la clase empieza Y termina dentro de la ventana pedida.
"""

import re
import unicodedata

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_ALIAS = {
    "lun": 0, "lunes": 0, "mar": 1, "martes": 1,
    "mie": 2, "mier": 2, "miercoles": 2, "jue": 3, "jueves": 3,
    "vie": 4, "viernes": 4, "sab": 5, "sabado": 5, "sabados": 5,
    "dom": 6, "domingo": 6, "domingos": 6,
}
_FRANJAS = {"manana": (6 * 60, 12 * 60), "tarde": (12 * 60, 18 * 60), "noche": (18 * 60, 24 * 60)}
_RELLENO = {
    "de", "del", "a", "al", "las", "los", "el", "la", "en", "y", "e", "o", "hasta", "desde", "despues",
    "antes", "entre", "semana", "fin", "finde", "todos", "todo", "dia", "dias", "hora", "horas", "por",
    "grupo", "grupos", "buscar", "filtrar", "quiero", "que", "mis", "para", "am", "pm", "con", "un",
    "una", "partir", "cualquier", "pueden", "puedo", "tengo", "libre", "disponible", "disponibles",
}


class Filtro:
    def __init__(self, dias=None, ini=None, fin=None, palabras=None):
        self.dias = set(dias or [])          # vacío = cualquier día
        self.ini, self.fin = ini, fin        # minutos desde las 0:00; None = cualquier hora
        self.palabras = list(palabras or []) # deben aparecer en el nombre del curso o del grupo

    def vacio(self):
        return not self.dias and self.ini is None and not self.palabras


def _norm(texto):
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


def _hora24(h, m, ampm, es_fin=False, inicio=None):
    """Convierte '3' a 15:00 cuando no dicen am/pm: las clases no empiezan de madrugada."""
    h, m = int(h), int(m or 0)
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    elif ampm is None:
        if not es_fin and h <= 7:
            h += 12
        elif es_fin and inicio is not None and h * 60 + m <= inicio and h < 12:
            h += 12
    return h * 60 + m


def _segmento(texto):
    t = _norm(texto).replace(".", "").replace("–", "-").replace("—", "-")
    dias, ini, fin = set(), None, None

    # ---- horas
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|a|hasta)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if m:
        ini = _hora24(m.group(1), m.group(2), m.group(3))
        fin = _hora24(m.group(4), m.group(5), m.group(6), es_fin=True, inicio=ini)
        t = t[:m.start()] + " " + t[m.end():]
    else:
        m = re.search(r"(?:desde|despues de|a partir de)(?: las?)? (\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
        if m:
            ini, fin = _hora24(m.group(1), m.group(2), m.group(3)), 24 * 60
            t = t[:m.start()] + " " + t[m.end():]
        else:
            m = re.search(r"antes de(?: las?)? (\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
            if m:
                ini, fin = 0, _hora24(m.group(1), m.group(2), m.group(3), es_fin=True, inicio=0)
                t = t[:m.start()] + " " + t[m.end():]
    for nombre, (a, b) in _FRANJAS.items():
        if re.search(rf"\b{nombre}s?\b", t) and ini is None:
            ini, fin = a, b
            t = re.sub(rf"\b{nombre}s?\b", " ", t)

    # ---- días
    if re.search(r"\b(fin de semana|finde)\b", t):
        dias |= {5, 6}
        t = re.sub(r"\b(fin de semana|finde)\b", " ", t)
    if re.search(r"\bentre semana\b", t):
        dias |= {0, 1, 2, 3, 4}
        t = t.replace("entre semana", " ")
    m = re.search(r"\b([a-z]+)\s+(?:a|hasta)\s+(?:el\s+)?([a-z]+)\b", t)
    if m and m.group(1) in _ALIAS and m.group(2) in _ALIAS:
        d, h = _ALIAS[m.group(1)], _ALIAS[m.group(2)]
        while True:
            dias.add(d)
            if d == h:
                break
            d = (d + 1) % 7
        t = t[:m.start()] + " " + t[m.end():]
    palabras = []
    for w in re.findall(r"[a-z0-9]+", t):
        if w in _ALIAS:
            dias.add(_ALIAS[w])
        elif w not in _RELLENO and not w.isdigit() and len(w) >= 3:
            palabras.append(w)
    return Filtro(dias, ini, fin, palabras)


def parsear_consulta(texto):
    """'sábado 8-12; domingo tarde' -> [Filtro, Filtro]. Ignora el /comando del inicio."""
    texto = re.sub(r"^\s*/\w+(@\w+)?", "", texto or "")
    filtros = [_segmento(s) for s in re.split(r"[;\n]", texto) if s.strip()]
    return [f for f in filtros if not f.vacio()]


def _horario(g):
    """(día 0-6, minuto de inicio, duración) de un grupo, o None si no trae hora."""
    m = re.search(r"([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\s+(\d{1,2}):(\d{2})\s*COL", g.get("horario") or "")
    if not m:
        return None
    dia = _ALIAS.get(_norm(m.group(1)))
    if dia is None:
        return None
    dur = g.get("duracion")
    if not dur:
        cabecera = (g.get("lineas") or [""])[0]
        d = re.search(r"(\d+)\s*min", cabecera)
        dur = int(d.group(1)) if d else 90
    return dia, int(m.group(2)) * 60 + int(m.group(3)), dur


def coincide(g, curso, f):
    """True si el grupo cumple el filtro, False si no, None si no se puede saber (sin hora)."""
    palabras_ok = all(p in _norm(curso + " " + (g.get("nombre") or "")) for p in f.palabras)
    if not palabras_ok:
        return False
    if not f.dias and f.ini is None:
        return True
    h = _horario(g)
    if h is None:
        return None
    dia, inicio, dur = h
    if f.dias and dia not in f.dias:
        return False
    if f.ini is not None and not (inicio >= f.ini and inicio + dur <= f.fin):
        return False
    return True


def buscar(grupos, filtros):
    """grupos: [(curso, grupo)]. Devuelve (coinciden, sin_hora), ordenados por día y hora."""
    si, sin_hora = [], []
    for curso, g in grupos:
        resultados = [coincide(g, curso, f) for f in filtros]
        if any(r is True for r in resultados):
            si.append((curso, g))
        elif any(r is None for r in resultados):
            sin_hora.append((curso, g))
    clave = lambda cg: (_horario(cg[1]) or (9, 0, 0))[:2]
    return sorted(si, key=clave), sin_hora


def _hm(minutos):
    return f"{minutos // 60}:{minutos % 60:02d}" if minutos < 24 * 60 else "24:00"


def explicar(filtros):
    partes = []
    for f in filtros:
        p = []
        if f.dias:
            p.append(", ".join(DIAS[d] for d in sorted(f.dias)))
        if f.ini is not None:
            p.append(f"{_hm(f.ini)}–{_hm(f.fin)}")
        if f.palabras:
            p.append(" ".join(f.palabras))
        partes.append(" · ".join(p) or "todo")
    return "  |  ".join(partes)


AYUDA = (
    "Escríbeme qué días y horas puedes y te muestro solo los grupos que caben.\n\n"
    "Ejemplos:\n"
    "• /grupos sábado y domingo de 8 a 12\n"
    "• /grupos lunes a viernes tarde\n"
    "• /grupos fin de semana después de las 2\n"
    "• /grupos roblox sábado mañana\n"
    "• /grupos sábado 8-12; domingo 14-18\n\n"
    "Cuentan la hora de Colombia y la duración de la clase: el grupo solo aparece si "
    "empieza y termina dentro de tu horario. Mañana = 6–12, tarde = 12–18, noche = 18–24."
)
