"""
Asistente web para configurar el avisador de grupos Kodland.
El usuario solo ingresa su ID de tutor y todo se configura automáticamente.
"""

from flask import Flask, render_template, request, jsonify
import os
import secrets
import json
import subprocess
from pathlib import Path
from datetime import datetime

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# Carpeta donde guardamos sesiones temporales
TEMP_DIR = Path(__file__).parent / "temp"
TEMP_DIR.mkdir(exist_ok=True)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/generar-tema", methods=["POST"])
def generar_tema():
    """Genera un tema ntfy único para el usuario."""
    data = request.get_json()
    tutor_id = data.get("tutor_id", "").strip()

    if not tutor_id or len(tutor_id) < 3:
        return jsonify({"error": "ID de tutor inválido"}), 400

    # Tema único: kodland-[tutor_id]-[random]
    random_suffix = secrets.token_hex(4)
    tema = f"kodland-{tutor_id.lower()}-{random_suffix}"

    return jsonify({"tema": tema})


@app.route("/api/iniciar-login", methods=["POST"])
def iniciar_login():
    """Inicia el proceso de login. Devuelve un ID de sesión."""
    data = request.get_json()
    tutor_id = data.get("tutor_id", "").strip()
    tema = data.get("tema", "").strip()

    if not tutor_id or not tema:
        return jsonify({"error": "Datos incompletos"}), 400

    # Crea una sesión temporal
    session_id = secrets.token_hex(8)
    session_file = TEMP_DIR / f"{session_id}.json"

    session_data = {
        "tutor_id": tutor_id,
        "tema": tema,
        "estado": "esperando_login",
        "creado": datetime.now().isoformat(),
    }

    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    return jsonify({"session_id": session_id})


@app.route("/api/estado-login/<session_id>", methods=["GET"])
def estado_login(session_id):
    """Consulta el estado del login."""
    session_file = TEMP_DIR / f"{session_id}.json"

    if not session_file.exists():
        return jsonify({"error": "Sesión no encontrada"}), 404

    session_data = json.loads(session_file.read_text(encoding="utf-8"))
    return jsonify(session_data)


@app.route("/api/guardar-sesion", methods=["POST"])
def guardar_sesion():
    """
    Guarda el sesion.json capturado del navegador.
    (En producción, esto vendría desde Chrome después del login)
    """
    data = request.get_json()
    session_id = data.get("session_id")
    sesion_json = data.get("sesion_json")

    session_file = TEMP_DIR / f"{session_id}.json"
    if not session_file.exists():
        return jsonify({"error": "Sesión no encontrada"}), 404

    session_data = json.loads(session_file.read_text(encoding="utf-8"))
    session_data["sesion_json"] = sesion_json
    session_data["estado"] = "sesion_guardada"

    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    return jsonify({"ok": True})


@app.route("/api/finalizar", methods=["POST"])
def finalizar():
    """
    Finaliza la configuración:
    - Crea/prepara el repositorio de GitHub
    - Configura los secretos
    - Inicia el avisador
    """
    data = request.get_json()
    session_id = data.get("session_id")
    github_token = data.get("github_token", "").strip()

    session_file = TEMP_DIR / f"{session_id}.json"
    if not session_file.exists():
        return jsonify({"error": "Sesión no encontrada"}), 404

    session_data = json.loads(session_file.read_text(encoding="utf-8"))

    # Aquí iría la lógica para:
    # 1. Crear repositorio en GitHub
    # 2. Configurar secretos
    # 3. Hacer push del código
    # 4. Activar GitHub Actions

    # Por ahora, devolvemos que está listo
    session_data["estado"] = "configurado"
    session_data["repo_url"] = f"https://github.com/tu-usuario/avisador-{session_data['tutor_id']}"

    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    return jsonify({"ok": True, "message": "¡Avisador configurado!"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
