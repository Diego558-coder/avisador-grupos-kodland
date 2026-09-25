/**
 * Mini-servicio que recibe el toque de "✅ Sí, postularme" (ntfy).
 *
 * Postular lleva DOS pasos, para que un toque accidental no te postule:
 *   1) "✅ Postularme" en el aviso del grupo: NO postula. Tu celular publica en
 *      ntfy un segundo aviso "¿Confirmas?" (esto no pasa por este servicio).
 *   2) "✅ Sí, postularme" en ese segundo aviso: llega aquí. Se comprueba la
 *      firma y recién entonces se le pide a GitHub que ejecute la postulación.
 *
 * Los enlaces van firmados (HMAC): solo el avisador puede crearlos. El token de
 * GitHub y el secreto viven en "Propiedades de la secuencia de comandos", nunca
 * en el aviso ni en el repositorio.
 *
 * (No uses parámetros llamados 'c' en la URL: Apps Script los rechaza.)
 *
 * Propiedades necesarias (Configuración del proyecto > Propiedades de la secuencia):
 *   RELAY_SECRETO  texto largo y al azar (el mismo que el secreto RELAY_SECRETO de GitHub)
 *   GH_TOKEN       token de GitHub (solo permiso "Actions: lectura y escritura" en el repo)
 *   GH_REPO        usuario/repositorio, p. ej. Diego558-coder/avisador-grupos-kodland
 */

var VIGENCIA_SEGUNDOS = 14 * 24 * 3600; // un botón sirve 14 días

function doGet(e) {
  var p = (e && e.parameter) || {};
  var props = PropertiesService.getScriptProperties();
  var mensaje;
  try {
    var secreto = props.getProperty('RELAY_SECRETO');
    var token = props.getProperty('GH_TOKEN');
    var repo = props.getProperty('GH_REPO');
    if (!secreto || !token || !repo) throw new Error('falta configurar el servicio');

    if (String(p.paso || '') !== 'confirmar' || !p.curso64) throw new Error('enlace antiguo: pide la lista de nuevo');

    var edad = Date.now() / 1000 - Number(p.ts);
    if (!(edad > -300 && edad < VIGENCIA_SEGUNDOS)) throw new Error('el botón venció');

    // Todo lo firmado es ASCII: Apps Script cambia por '?' los caracteres raros de la URL
    var datos = ['confirmar', p.tutor, p.curso64, p.grupo, p.tema, p.ts].join('|');
    var firma = aHex(Utilities.computeHmacSha256Signature(datos, secreto));
    if (!iguales(firma, String(p.firma || ''))) throw new Error('enlace no válido');
    if (!/^[A-Za-z0-9_-]{3,40}$/.test(String(p.grupo))) throw new Error('grupo no válido');
    var curso = decodificar(String(p.curso64));

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
          inputs: { tutor: String(p.tutor), curso: curso, grupo: String(p.grupo) }
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

// base64 sin relleno (lo que manda el avisador) -> texto UTF-8
function decodificar(b64) {
  while (b64.length % 4) b64 += '=';
  return Utilities.newBlob(Utilities.base64DecodeWebSafe(b64)).getDataAsString('UTF-8');
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
