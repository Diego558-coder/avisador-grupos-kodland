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
        self.assertEqual(resultado["vacios"], {})

    def test_revisar_descarta_curso_que_desaparece_varias_revisiones(self):
        estado = {"cursos": {"Roblox": LINEAS_GRUPO, "Unity": LINEAS_GRUPO}, "vacios": {}}
        tutor = {"pos": 1, "telegram_chat": "123"}
        cfg = {"heartbeat_horas": 12}

        # Primera revisión sin "Unity": se conserva por el margen anti-fallo.
        with patch("avisador.leer_todos_los_cursos", return_value=({"Roblox": LINEAS_GRUPO}, [])), \
             patch("avisador.guardar_json"), patch("avisador.avisar"), patch("avisador.ruta_estado"):
            resultado = avisador.revisar(cfg, estado, tutor)
        self.assertIn("Unity", resultado["cursos"])
        self.assertEqual(resultado["faltas"].get("Unity"), 1)

        # Segunda revisión seguida sin "Unity": se descarta del estado.
        with patch("avisador.leer_todos_los_cursos", return_value=({"Roblox": LINEAS_GRUPO}, [])), \
             patch("avisador.guardar_json"), patch("avisador.avisar"), patch("avisador.ruta_estado"):
            resultado2 = avisador.revisar(cfg, resultado, tutor)
        self.assertNotIn("Unity", resultado2["cursos"])
        self.assertNotIn("Unity", resultado2["faltas"])

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


if __name__ == "__main__":
    unittest.main()
