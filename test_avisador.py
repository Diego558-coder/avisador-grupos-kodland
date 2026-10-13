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

    def test_busqueda_consulta_la_app_y_no_el_estado_anterior(self):
        tutor = {"kt_id": "tutor-1"}
        with patch("avisador.leer_todos_los_cursos", return_value=({"Roblox": LINEAS_GRUPO}, [])) as lectura, \
             patch("avisador.estado_actual", side_effect=AssertionError("No debe leer estado.json")):
            resultado = avisador.buscar_grupos({}, tutor, "/grupos sábado 8-12")

        lectura.assert_called_once()
        self.assertEqual(resultado["total"], 1)
        self.assertEqual([grupo[2] for grupo in resultado["grupos"]], ["COL123_SA-10"])

    def test_busqueda_informa_si_la_lectura_fue_parcial(self):
        tutor = {"kt_id": "tutor-1"}
        with patch("avisador.leer_todos_los_cursos", return_value=({"Roblox": LINEAS_GRUPO}, ["Unity: TimeoutError"])):
            resultado = avisador.buscar_grupos({}, tutor, "/grupos sábado 8-12")

        self.assertTrue(resultado["lectura_parcial"])

    def test_busqueda_reutiliza_playwright_del_bot(self):
        tutor = {"kt_id": "tutor-1"}
        caliente = types.SimpleNamespace(p="playwright-existente")
        with patch("avisador.leer_todos_los_cursos", return_value=({"Roblox": LINEAS_GRUPO}, [])) as lectura:
            avisador.buscar_grupos({}, tutor, "/grupos sábado 8-12", calientes=caliente)

        self.assertEqual(lectura.call_args.kwargs["p"], "playwright-existente")


if __name__ == "__main__":
    unittest.main()
