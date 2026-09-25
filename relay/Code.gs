/**
 * Mini-servicio que recibe los toques de los botones de ntfy. Postular lleva
 * DOS pasos, para que un toque accidental no te postule:
 *
 *   1) "✅ Postularme" (paso=pedir): no postula. Manda un segundo aviso que
 *      pregunta "¿Confirmas?", con su propio botón.
 *   2) "✅ Sí, postularme" (paso=confirmar): recién ahí le pide a GitHub que
 *      ejecute la postulación. Ese botón vale solo 15 minutos.
 *
 * Los enlaces van firmados (HMAC): solo el avisador y este servicio pueden
 * crearlos. El token de GitHub y el secreto viven en "Propiedades de la
 * secuencia de comandos", nunca en el aviso ni en el repositorio.
 *
 * (No uses parámetros llamados 'c' en la URL: Apps Script los rechaza.)
 *
 * Propiedades necesarias (Configuración del proyecto > Propiedades de la secuencia):
 *   RELAY_SECRETO  texto largo y al azar (el mismo que el secreto RELAY_SECRETO de GitHub)
 *   GH_TOKEN       token de GitHub (solo permiso "Actions: lectura y escritura" en el repo)
 *   GH_REPO        usuario/repositorio, p. ej. Diego558-coder/avisador-grupos-kodland
 *   NTFY_TOKEN     token de tu cuenta de ntfy.sh (sin él, ntfy bloquea al servicio: error 429)
 *   RELAY_URL      (opcional) la URL /exec de este servicio, si no se detecta sola
 */

var VIGENCIA_PEDIR = 14 * 24 * 3600;  // el botón del aviso original sirve 14 días
var VIGENCIA_CONFIRMAR = 15 * 60;     // el botón de confirmar sirve 15 minutos
var NTFY = 'https://ntfy.sh';

function doGet(e) {
  var p = (e && e.parameter) || {};
  var props = PropertiesService.getScriptProperties();
  var mensaje;
  try {
    var secreto = props.getProperty('RELAY_SECRETO');
    var token = props.getProperty('GH_TOKEN');
    var repo = props.getProperty('GH_REPO');
    if (!secreto || !token || !repo) throw new Error('falta configurar el servicio');

    var paso = String(p.paso || '');
    if (paso !== 'pedir' && paso !== 'confirmar') throw new Error('enlace antiguo: pide la lista de nuevo');

    var vigencia = paso === 'pedir' ? VIGENCIA_PEDIR : VIGENCIA_CONFIRMAR;
    var edad = Date.now() / 1000 - Number(p.ts);
    if (!(edad > -300 && edad < vigencia)) throw new Error('el botón venció');

    var firma = firmar([paso, p.tutor, p.curso, p.grupo, p.tema, p.info || '', p.ts], secreto);
    if (!iguales(firma, String(p.firma || ''))) throw new Error('enlace no válido');
    if (!/^[-_A-Za-z0-9]{1,64}$/.test(String(p.tema || ''))) throw new Error('tema no válido');

    if (paso === 'pedir') {
      var base = props.getProperty('RELAY_URL') || ScriptApp.getService().getUrl();
      pedirConfirmacion(p, secreto, base);
      mensaje = 'Te mandé un aviso para confirmar.';
    } else {
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
            inputs: { tutor: String(p.tutor), curso: String(p.curso), grupo: String(p.grupo) }
          }),
          muteHttpExceptions: true
        }
      );
      if (r.getResponseCode() !== 204) throw new Error('GitHub respondió ' + r.getResponseCode());
      mensaje = 'Postulación en camino. Te llegará un aviso con el resultado.';
    }
  } catch (err) {
    mensaje = 'No se pudo: ' + err.message;
  }
  return ContentService.createTextOutput(mensaje);
}

// Segundo aviso: pregunta si de verdad quiere postularse a ese grupo.
function pedirConfirmacion(p, secreto, base) {
  var ts = String(Math.floor(Date.now() / 1000));
  var info = String(p.info || '');
  var firma = firmar(['confirmar', p.tutor, p.curso, p.grupo, p.tema, info, ts], secreto);
  var url = base + '?' + [
    'paso=confirmar',
    'tutor=' + encodeURIComponent(p.tutor),
    'curso=' + encodeURIComponent(p.curso),
    'grupo=' + encodeURIComponent(p.grupo),
    'tema=' + encodeURIComponent(p.tema),
    'info=' + encodeURIComponent(info),
    'ts=' + ts,
    'firma=' + firma
  ].join('&');

  var texto = String(p.curso) + '\n🏷️ ' + String(p.grupo) + (info ? '\n' + info : '') +
    '\n\nSi es el grupo correcto, toca el botón. Vale 15 minutos.';
  var cabeceras = {};
  var tokenNtfy = PropertiesService.getScriptProperties().getProperty('NTFY_TOKEN');
  if (tokenNtfy) cabeceras.Authorization = 'Bearer ' + tokenNtfy;
  var r = UrlFetchApp.fetch(NTFY, {
    method: 'post',
    contentType: 'application/json',
    headers: cabeceras,
    payload: JSON.stringify({
      topic: String(p.tema),
      title: '⚠️ ¿Confirmas la postulación?',
      message: texto,
      priority: 4,
      tags: ['warning'],
      actions: [{ action: 'http', label: '✅ Sí, postularme', url: url, method: 'GET', clear: true }]
    }),
    muteHttpExceptions: true
  });
  if (r.getResponseCode() !== 200) throw new Error('ntfy respondió ' + r.getResponseCode());
}

function firmar(campos, secreto) {
  return aHex(Utilities.computeHmacSha256Signature(campos.join('|'), secreto));
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
