/**
 * Mini-servicio que recibe el toque del botón "Postularme" de ntfy.
 *
 * Comprueba que el enlace esté firmado (solo lo puede generar el avisador) y
 * entonces le pide a GitHub que ejecute la postulación. El token de GitHub y
 * el secreto de la firma viven en "Propiedades de la secuencia de comandos",
 * nunca en el aviso ni en el repositorio.
 *
 * Propiedades necesarias (Configuración del proyecto > Propiedades de la secuencia):
 *   RELAY_SECRETO  texto largo y al azar (el mismo que el secreto RELAY_SECRETO de GitHub)
 *   GH_TOKEN       token de GitHub (solo permiso "Actions: lectura y escritura" en el repo)
 *   GH_REPO        usuario/repositorio, p. ej. Diego558-coder/avisador-grupos-kodland
 */

var VIGENCIA_SEGUNDOS = 14 * 24 * 3600; // un enlace sirve 14 días

function doGet(e) {
  var p = (e && e.parameter) || {};
  var props = PropertiesService.getScriptProperties();
  var mensaje;
  try {
    var secreto = props.getProperty('RELAY_SECRETO');
    var token = props.getProperty('GH_TOKEN');
    var repo = props.getProperty('GH_REPO');
    if (!secreto || !token || !repo) throw new Error('falta configurar el servicio');

    var edad = Date.now() / 1000 - Number(p.ts);
    if (!(edad > -300 && edad < VIGENCIA_SEGUNDOS)) throw new Error('el enlace venció');

    var datos = [p.t, p.c, p.g, p.ts].join('|');
    var firma = aHex(Utilities.computeHmacSha256Signature(datos, secreto));
    if (!iguales(firma, String(p.s || ''))) throw new Error('enlace no válido');

    var r = UrlFetchApp.fetch(
      'https://api.github.com/repos/' + repo + '/actions/workflows/postular.yml/dispatches',
      {
        method: 'post',
        contentType: 'application/json',
        headers: {
          Authorization: 'Bearer ' + token,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28'
        },
        payload: JSON.stringify({
          ref: 'main',
          inputs: { tutor: String(p.t), curso: String(p.c), grupo: String(p.g) }
        }),
        muteHttpExceptions: true
      }
    );
    if (r.getResponseCode() !== 204) throw new Error('GitHub respondió ' + r.getResponseCode());
    mensaje = 'Postulación en camino. Te llegará un aviso con el resultado.';
  } catch (err) {
    mensaje = 'No se pudo: ' + err.message;
  }
  return ContentService.createTextOutput(mensaje);
}

function aHex(bytes) {
  return bytes.map(function (b) {
    var n = (b < 0 ? b + 256 : b).toString(16);
    return n.length === 1 ? '0' + n : n;
  }).join('');
}

// Comparación que no se detiene en la primera diferencia
function iguales(a, b) {
  if (a.length !== b.length) return false;
  var d = 0;
  for (var i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}
