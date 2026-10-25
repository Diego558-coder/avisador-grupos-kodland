import sys
import types
import unittest
from unittest.mock import patch

# Las pruebas cubren lógica pura y no abren un navegador. Permiten ejecutarse
# también en una instalación local que aún no tenga Playwright.
if "playwright.sync_api" not in sys.modules:
    playwright = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = None
    sys.modules["playwright"] = playwright
    sys.modules["playwright.sync_api"] = sync_api

import avisador


LINEAS_GRUPO = [
    "[COL123_SA-10][1] Roblox · 90 min",
    "🕐 Sábado 10:00 COL",
    "🏷️ COL123_SA-10",
]


class EstadoYBusquedaTests(unittest.TestCase):
    def test_una_lectura_vacia_elimina_grupos_que_ya_no_muestra_la_app(self):
        estado = {"cursos": {"Roblox": LINEAS_GRUPO}, "vacios": {}}
        tutor = {"pos": 1, "telegram_chat": "123"}

        with patch("avisador.leer_todos_los_cursos", return_value=({"Roblox": []}, [])), \
             patch("avisador.guardar_json"), \
             patch("avisador.avisar"), \
             patch("avisador.ruta_estado"):
            resultado = avisador.revisar({}, estado, tutor)

        self.assertEqual(resultado["cursos"]["Roblox"], [])
        # ...pero se recuerda que ya se avisó, para no repetirlo si vuelve
        self.assertIn("COL123_SA-10", resultado["avisados"])

    def test_revisar_descarta_curso_que_desaparece_varias_revisiones(self):
        estado = {"cursos": {"Roblox": LINEAS_GRUPO, "Unity": LINEAS_GRUPO}, "vacios": {}}
        tutor = {"pos": 1, "telegram_chat": "123"}
        cfg = {"heartbeat_horas": 12}
        contextos = (patch("avisador.leer_todos_los_cursos",
                           return_value=({"Roblox": LINEAS_GRUPO}, [])),
                     patch("avisador.guardar_json"), patch("avisador.avisar"),
                     patch("avisador.ruta_estado"))

        # 1ª y 2ª revisión sin "Unity": se conserva por el margen anti-fallo.
        actual = estado
        for esperado in (1, 2):
            with contextos[0], contextos[1], contextos[2], contextos[3]:
                actual = avisador.revisar(cfg, actual, tutor)
            self.assertIn("Unity", actual["cursos"])
            self.assertEqual(actual["faltas"].get("Unity"), esperado)

        # 3ª revisión seguida sin "Unity": se descarta del estado.
        with contextos[0], contextos[1], contextos[2], contextos[3]:
            actual = avisador.revisar(cfg, actual, tutor)
        self.assertNotIn("Unity", actual["cursos"])
        self.assertNotIn("Unity", actual["faltas"])

    def test_busqueda_usa_la_ultima_revision_guardada(self):
        tutor = {"kt_id": "tutor-1"}
        with patch("avisador.estado_actual", return_value={"cursos": {"Roblox": LINEAS_GRUPO}}) as estado:
            resultado = avisador.buscar_grupos({}, tutor, "/grupos sábado 8-12")

        estado.assert_called_once()
        self.assertEqual(resultado["total"], 1)
        self.assertEqual([grupo[2] for grupo in resultado["grupos"]], ["COL123_SA-10"])

    def test_busqueda_no_reescanea_la_app_en_vivo(self):
        tutor = {"kt_id": "tutor-1"}
        with patch("avisador.estado_actual", return_value={"cursos": {"Roblox": LINEAS_GRUPO}}), \
             patch("avisador.leer_todos_los_cursos", side_effect=AssertionError("No debe escanear en vivo")):
            resultado = avisador.buscar_grupos({}, tutor, "/grupos sábado 8-12")

        self.assertEqual(resultado["total"], 1)
        self.assertFalse(resultado["lectura_parcial"])

    def test_bot_empieza_a_escuchar_sin_abrir_kodland(self):
        args = types.SimpleNamespace(minutos=330)
        cfg = {
            "telegram_token": "token-de-prueba",
            "tutores": [{"pos": 1, "telegram_chat": "123"}],
        }
        with patch("avisador.NavegadorCaliente") as navegador, \
             patch("avisador.avisar", return_value=True), \
             patch("avisador.tg.bucle") as bucle:
            avisador.modo_bot(cfg, args)

        self.assertIsNone(bucle.call_args.kwargs.get("al_iniciar"))
        self.assertIsNone(bucle.call_args.kwargs.get("en_reposo"))
        navegador.return_value.cerrar.assert_called_once()

    def test_bot_falla_visiblemente_si_los_secrets_de_telegram_no_corresponden(self):
        args = types.SimpleNamespace(minutos=330)
        cfg = {
            "telegram_token": "token-de-prueba",
            "tutores": [{"pos": 1, "telegram_chat": "123"}],
        }
        with patch("avisador.NavegadorCaliente") as navegador, \
             patch("avisador.avisar", return_value=False), \
             patch("avisador.tg.bucle") as bucle:
            with self.assertRaisesRegex(RuntimeError, "TELEGRAM_TOKEN"):
                avisador.modo_bot(cfg, args)

        bucle.assert_not_called()
        navegador.return_value.cerrar.assert_called_once()


# Formato real de la app nueva (así lo guarda estado.json)
GRUPO_A = [
    "[COL14334_LU-12][1210] Roblox Game Developer (COL14334_LU-12) · 90 min",
    "🕐 Lunes 12h COL (14h ARG)",
    "📅 2026-10-26 12:00",
    "🏷️ COL14334_LU-12",
]
GRUPO_B = [
    "[PRM_COL14999_SA-10][1720] Roblox Game Developer (PRM_COL14999_SA-10) · 60 min",
    "🕐 Sábado 10h COL (12h ARG)",
    "📅 2026-11-07 10:00",
    "🏷️ PRM_COL14999_SA-10",
]
CFG = {"heartbeat_horas": 12, "url_app": "https://app.example"}
TUTOR = {"pos": 1, "telegram_chat": "123"}


def revisiones(estado, lecturas, avisar_ok=True):
    """Ejecuta una revisión por cada lectura simulada. Devuelve (estado final,
    códigos de grupo avisados como nuevos, en orden)."""
    avisos = []

    def avisar(cfg, tutor, titulo, mensaje, prioridad=4, grupo=None):
        ok = avisar_ok(grupo) if callable(avisar_ok) else avisar_ok
        if titulo.startswith("🆕") and ok:
            avisos.append(grupo[1] if grupo else titulo)
        return ok

    for lectura in lecturas:
        leer = lectura if callable(lectura) else (lambda *a, _l=lectura, **kw: (_l, []))
        with patch("avisador.leer_todos_los_cursos", side_effect=leer), \
             patch("avisador.guardar_json"), patch("avisador.ruta_estado"), \
             patch("avisador.avisar", side_effect=avisar):
            estado = avisador.revisar(CFG, estado, TUTOR)
    return estado, avisos


class AvisadosTests(unittest.TestCase):
    def test_grupo_que_desaparece_un_rato_y_vuelve_no_se_repite(self):
        # Estado de la versión anterior (sin "avisados"): lo guardado ya se había avisado
        estado = {"cursos": {"Roblox Game Developer": GRUPO_A}}
        _, avisos = revisiones(estado, [{"Roblox Game Developer": []},
                                        {"Roblox Game Developer": GRUPO_A}])
        self.assertEqual(avisos, [])

    def test_grupo_nuevo_se_avisa_una_sola_vez(self):
        estado = {"cursos": {"Roblox Game Developer": GRUPO_A}, "avisados": {}}
        estado, avisos = revisiones(estado, [
            {"Roblox Game Developer": GRUPO_A + GRUPO_B},
            {"Roblox Game Developer": GRUPO_A},
            {"Roblox Game Developer": GRUPO_A + GRUPO_B},
        ])
        self.assertEqual(avisos, ["PRM_COL14999_SA-10"])
        self.assertTrue(estado["avisados"]["PRM_COL14999_SA-10"]["desde"])

    def test_grupo_guardado_bajo_otro_curso_no_se_repite(self):
        estado = {"cursos": {"Roblox Game Developer": GRUPO_A}, "avisados": {}}
        _, avisos = revisiones(estado, [{"Roblox Game Developer": [], "Unity Game Developer": GRUPO_A}])
        self.assertEqual(avisos, [])

    def test_aviso_que_no_llego_se_reintenta_y_luego_no_se_repite(self):
        intentos = []

        def avisar_ok(grupo):
            intentos.append(grupo[1])
            return len(intentos) > 1  # el primer envío falla

        estado = {"cursos": {"Roblox Game Developer": []}, "avisados": {}}
        _, avisos = revisiones(estado, [{"Roblox Game Developer": GRUPO_B}] * 3, avisar_ok=avisar_ok)
        self.assertEqual(intentos, ["PRM_COL14999_SA-10"] * 2)
        self.assertEqual(avisos, ["PRM_COL14999_SA-10"])

    def test_primera_revision_no_avisa_pero_recuerda_lo_que_hay(self):
        estado, avisos = revisiones({}, [{"Roblox Game Developer": GRUPO_A}])
        self.assertEqual(avisos, [])
        self.assertIsNone(estado["avisados"]["COL14334_LU-12"]["desde"])

    def test_curso_leido_a_medias_conserva_sus_grupos_y_avisa_lo_nuevo(self):
        def leer(*args, incompletos=None, **kwargs):
            incompletos.add("Roblox Game Developer")  # p. ej. falló la modalidad 1-1
            return {"Roblox Game Developer": GRUPO_B}, ["Roblox Game Developer (individual): x"]

        estado = {"cursos": {"Roblox Game Developer": GRUPO_A}, "avisados": {}}
        estado, avisos = revisiones(estado, [leer])
        self.assertEqual(avisos, ["PRM_COL14999_SA-10"])
        ids = {g["id"] for g in avisador.parsear_grupos(estado["cursos"]["Roblox Game Developer"])}
        self.assertEqual(ids, {"COL14334_LU-12", "PRM_COL14999_SA-10"})

    def test_olvida_grupos_que_llevan_mucho_sin_aparecer(self):
        estado = {"cursos": {"Roblox Game Developer": GRUPO_A}, "avisados": {
            "COL1_LU-1": {"curso": "Roblox Game Developer", "desde": None, "visto": "2020-01-01"},
            "COL14334_LU-12": {"curso": "Roblox Game Developer", "desde": None, "visto": "2020-01-01"},
        }}
        estado, _ = revisiones(estado, [{"Roblox Game Developer": GRUPO_A}])
        self.assertNotIn("COL1_LU-1", estado["avisados"])
        self.assertNotEqual(estado["avisados"]["COL14334_LU-12"]["visto"], "2020-01-01")

    def test_estadisticas_salen_de_los_avisados(self):
        avisados = {f"COL{i}_SA-10": {"curso": "Roblox", "desde": "2026-10-24T15:00:00+00:00",
                                      "visto": "2026-10-24"} for i in range(6)}
        avisados["BASE_1"] = {"curso": "Roblox", "desde": None, "visto": "2026-10-24"}
        with patch("avisador.estado_actual", return_value={"avisados": avisados}):
            texto = avisador.estadisticas(TUTOR)
        self.assertIn("6 grupos nuevos", texto)
        self.assertIn("sábado", texto)  # 15:00 UTC = 10:00 en Colombia, sábado


class LecturaTests(unittest.TestCase):
    def test_modalidad_que_falla_en_todos_los_cursos_no_deja_cursos_incompletos(self):
        lecturas = [
            {"curso": "Roblox", "modo": "grupal", "lineas": GRUPO_A},
            {"curso": "Unity", "modo": "grupal", "lineas": []},
            {"curso": "Roblox", "modo": "individual", "error": "Script function not found"},
            {"curso": "Unity", "modo": "individual", "error": "Script function not found"},
        ]
        incompletos = set()
        resultado, errores = avisador._juntar_lecturas(lecturas, incompletos=incompletos)
        self.assertEqual(incompletos, set())
        self.assertEqual(resultado, {"Roblox": GRUPO_A, "Unity": []})
        self.assertEqual(len(errores), 1)

    def test_fallo_puntual_de_un_curso_lo_marca_incompleto(self):
        lecturas = [
            {"curso": "Roblox", "modo": "grupal", "error": "sin respuesta en 40 s"},
            {"curso": "Unity", "modo": "grupal", "lineas": []},
            {"curso": "Roblox", "modo": "individual", "lineas": GRUPO_B},
            {"curso": "Unity", "modo": "individual", "lineas": []},
        ]
        incompletos = set()
        resultado, errores = avisador._juntar_lecturas(lecturas, incompletos=incompletos)
        self.assertEqual(incompletos, {"Roblox"})
        self.assertEqual(resultado["Roblox"], GRUPO_B)
        self.assertEqual(errores, ["Roblox (grupal): sin respuesta en 40 s"])

    def test_lineas_desde_los_datos_del_servidor(self):
        grupos = [
            {"group": "COL14334_LU-12", "course": "[1210] Roblox Game Developer (COL14334_LU-12) · 90 min",
             "schedule": "Lunes 12h COL (14h ARG)", "start": "Inicia: 2026-10-26 12:00", "applied": False},
            {"group": "COL1_YA-1", "course": "x", "schedule": "x", "start": "", "applied": True},
            {"group": "COL2_SIN-1", "course": "", "schedule": "", "start": ""},
        ]
        lineas = avisador._lineas_de_grupos(grupos, "Roblox Game Developer")
        self.assertEqual(lineas[:4], GRUPO_A)
        self.assertNotIn("🏷️ COL1_YA-1", lineas)  # ya postulado
        self.assertEqual(lineas[4:], ["[COL2_SIN-1]Roblox Game Developer", "🕐 A convenir", "🏷️ COL2_SIN-1"])


class FormatoTests(unittest.TestCase):
    def test_nombre_y_duracion_limpios_de_la_cabecera(self):
        g = avisador.parsear_grupos(GRUPO_A)[0]
        self.assertEqual((g["nombre"], g["duracion"], g["id"]),
                         ("Roblox Game Developer", 90, "COL14334_LU-12"))
        self.assertIn("⏱️ 90 min (termina 13:30 COL)", avisador.formatear_grupo(g))

    def test_cabecera_de_la_app_antigua(self):
        self.assertEqual(avisador._nombre_y_duracion("[COL1_LU-12] Python Start [60 min]"),
                         ("Python Start", 60))

    def test_filtro_por_palabra_tambien_mira_el_codigo(self):
        grupos = [("Roblox Game Developer", g) for g in avisador.parsear_grupos(GRUPO_A + GRUPO_B)]
        si, _ = avisador.filtro.buscar(grupos, avisador.filtro.parsear_consulta("/grupos prm sábado"))
        self.assertEqual([g["id"] for _, g in si], ["PRM_COL14999_SA-10"])


if __name__ == "__main__":
    unittest.main()
