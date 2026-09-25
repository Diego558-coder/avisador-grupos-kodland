# Avisador de Grupos Kodland - Asistente Web

Una página web súper simple para que cualquiera (sin conocimientos técnicos) configure el avisador de grupos en 4 pasos.

## Pasos para usar:

1. **Entrar a la página** → El usuario abre el navegador
2. **Ingresar ID de tutor** → Solo su ID de Kodland
3. **Generar tema de notificaciones** → Automático
4. **Iniciar sesión** → Se abre Chrome para autenticarse
5. **¡Listo!** → El sistema ya está vigilando

## Instalación local (para desarrollo):

```bash
pip install -r requirements.txt
python app.py
```

Luego abre: http://localhost:5000

## Instalación en Replit (RECOMENDADO):

1. Crea una cuenta en https://replit.com
2. Haz fork de este proyecto
3. Presiona "Run"
4. Comparte el enlace con tu primo
5. ¡Listo!

## Cómo funciona:

- **Paso 1**: Guarda el ID del tutor
- **Paso 2**: Genera un tema ntfy único (ej: `kodland-diego-abc123`)
- **Paso 3**: Abre Chrome para que haga login en Kodland
- **Paso 4**: Captura el `sesion.json` automáticamente
- **Paso 5**: Crea el repositorio en GitHub y configura los secretos
- **Resultado**: El primo recibe notificaciones en su celular cada 5 minutos

## Variables de entorno necesarias:

```
GITHUB_TOKEN=<token de GitHub para crear repos>
KODLAND_APP_URL=<URL de la app de Kodland>
NTFY_SERVER=https://ntfy.sh
```
