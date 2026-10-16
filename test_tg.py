"""Simulación del flujo del botón "Postularme" de Telegram (sin red ni navegador).

Cubre: la generación del botón, la máquina de estados del mensaje y cada toque
(Postularme -> Confirmar/Cancelar -> resultado), comprobando qué llamadas se
hacen a la API de Telegram y a la función de postulación.
"""
import unittest
from unittest.mock import MagicMock, patch

import tg


TUTOR = {"pos": 1, "telegram_chat": "123", "nombre": "Diego"}
CFG = {"telegram_token": "TOKEN", "url_app": "https://app.example"}


def _cq(data, texto, chat_id="123"):
    return {
        "id": "cbq-1",
        "data": data,
        "message": {
            "chat": {"id": chat_id},
            "message_id": 7,
            "text": texto,
            "entities": [{"type": "bold", "offset": 0, "length": 12}],
        },
    }


class BotonesTests(unittest.TestCase):
    def test_boton_postular_usa_callback_data_corto(self):
        fila = tg.botones_grupo(1, "Unity", "PRUEBA_0-0", "https://app.example")
        self.assertEqual(fila[0][0]["callback_data"], "p|1|PRUEBA_0-0|Unity")

    def test_boton_postular_se_omite_si_el_dato_es_muy_largo(self):
        # Curso + grupo larguísimos: no debe crear el botón (Telegram limita a 64 bytes)
        fila = tg.botones_grupo(1, "Un curso con un nombre realmente larguísimo que excede", "X" * 60, "u")
        self.assertFalse(any("callback_data" in b for b in fila[0]))


class EstadoMensajeTests(unittest.TestCase):
    def test_estados_del_mensaje(self):
        base = "🧪 Prueba del botón\nGrupo inventado"
        self.assertEqual(tg._estado(base), "fresca")
        self.assertEqual(tg._estado(base + "\n\n⚠️ ¿Confirmas la postulación?"), "pregunta")
        self.assertEqual(tg._estado(base + "\n\n⏳ Postulando… (unos 30 segundos)"), "terminada")
        self.assertEqual(tg._estado(base + "\n\n✅ Postulación enviada"), "terminada")

    def test_base_quita_las_marcas_añadidas(self):
        base = "🧪 Prueba del botón\nGrupo inventado"
        texto = base + "\n\n⚠️ ¿Confirmas la postulación?"
        self.assertEqual(tg._base(texto), base)


class ToquePostularTests(unittest.TestCase):
    def _run(self, data, texto, postular_fn):
        with patch("tg.llamar") as llamar:
            tg._atender_toque(CFG, _cq(data, texto), {"123": TUTOR}, postular_fn, lambda *a: None)
        return llamar

    def test_toque_postular_me_muestra_confirmacion(self):
        base = "🧪 Prueba del botón\nGrupo inventado"
        llamar = self._run("p|1|PRUEBA_0-0|Unity", base, None)

        # Responde el callback y edita el mensaje pidiendo confirmación
        metodos = [c.args[1] for c in llamar.call_args_list]
        self.assertIn("answerCallbackQuery", metodos)
        self.assertIn("editMessageText", metodos)
        edit = next(c for c in llamar.call_args_list if c.args[1] == "editMessageText")
        self.assertIn("¿Confirmas", edit.kwargs["text"])

    def test_toque_confirmar_llama_a_postular_y_muestra_resultado(self):
        base = "🧪 Prueba del botón\nGrupo inventado"
        texto = base + "\n\n⚠️ ¿Confirmas la postulación?"
        postular_fn = MagicMock(return_value=(True, "Postulación enviada al grupo PRUEBA_0-0."))

        llamar = self._run("c|1|PRUEBA_0-0|Unity", texto, postular_fn)

        postular_fn.assert_called_once_with(CFG, TUTOR, "Unity", "PRUEBA_0-0")
        edits = [c for c in llamar.call_args_list if c.args[1] == "editMessageText"]
        self.assertTrue(edits)
        self.assertIn("Postulación enviada", edits[-1].kwargs["text"])

    def test_toque_confirmar_con_fallo_muestra_error(self):
        base = "🧪 Prueba del botón\nGrupo inventado"
        texto = base + "\n\n⚠️ ¿Confirmas la postulación?"
        postular_fn = MagicMock(return_value=(False, "No encontré el grupo"))

        llamar = self._run("c|1|PRUEBA_0-0|Unity", texto, postular_fn)

        edits = [c for c in llamar.call_args_list if c.args[1] == "editMessageText"]
        self.assertIn("No se pudo postular", edits[-1].kwargs["text"])

    def test_toque_cancelar_vuelve_al_boton_original(self):
        base = "🧪 Prueba del botón\nGrupo inventado"
        texto = base + "\n\n⚠️ ¿Confirmas la postulación?"
        llamar = self._run("x|1|PRUEBA_0-0|Unity", texto, None)

        edits = [c for c in llamar.call_args_list if c.args[1] == "editMessageText"]
        self.assertTrue(edits)
        self.assertEqual(edits[-1].kwargs["text"], base)

    def test_toque_de_un_chat_no_autorizado_no_hace_nada(self):
        llamar = self._run("p|1|PRUEBA_0-0|Unity", "texto", None)
        # chat id "123" sí está autorizado, así que probamos con un chat distinto
        with patch("tg.llamar") as llamar2:
            tg._atender_toque(CFG, _cq("p|1|PRUEBA_0-0|Unity", "texto", chat_id="999"),
                              {"123": TUTOR}, None, lambda *a: None)
        respuesta = next(c for c in llamar2.call_args_list if c.args[1] == "answerCallbackQuery")
        self.assertEqual(respuesta.kwargs["text"], "No autorizado.")


if __name__ == "__main__":
    unittest.main()
