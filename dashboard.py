"""Loopback-only Tuya strip console. No device writes until an explicit POST."""
import argparse
import base64
import colorsys
import json
import math
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class DeviceError(Exception):
    """A sanitized network/device failure."""


class EffectCancelled(Exception):
    """An effect expired or was stopped before its next write."""


def number(value, low, high):
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        raise ValueError(f'Se requiere un número entre {low} y {high}.')
    return value


def encode_color(color, brightness):
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('El color debe tener seis dígitos hexadecimales.')
    number(brightness, 0, 100)
    rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    h, s, v = colorsys.rgb_to_hsv(*rgb)
    return f'{int(h * 360):04x}{int(s * 1000):04x}{int(v * brightness * 10):04x}'


def validate_effect(data):
    if not isinstance(data, dict) or set(data) - {'kind', 'duration', 'speed', 'brightness', 'color', 'steps', 'indexes', 'acknowledge_experimental'}:
        raise ValueError('Campos del efecto inválidos.')
    result = {'kind': data.get('kind'), 'duration': data.get('duration', 60), 'speed': data.get('speed', 8), 'brightness': data.get('brightness', 50), 'color': data.get('color', '#ff8a42'), 'steps': data.get('steps', [])}
    if result['kind'] not in ('rainbow', 'breathe', 'custom'):
        raise ValueError('Efecto desconocido.')
    number(result['duration'], 1, 300)
    number(result['speed'], 2, 60)
    encode_color(result['color'], result['brightness'])
    if 'indexes' in data:
        if data.get('acknowledge_experimental') is not True:
            raise ValueError('Debe aceptar el protocolo experimental.')
        result['indexes'] = validate_selection(data['indexes'])
    if result['kind'] == 'custom':
        if not isinstance(result['steps'], list) or not 1 <= len(result['steps']) <= 16:
            raise ValueError('La secuencia necesita entre 1 y 16 pasos.')
        for step in result['steps']:
            if not isinstance(step, dict) or set(step) != {'color', 'seconds'}:
                raise ValueError('Cada paso necesita un color y segundos.')
            encode_color(step['color'], result['brightness'])
            number(step['seconds'], 2, 60)
    return result


def encode_paint(data):
    """Official Tuya SmearFormatter layout; LAN DP61 acceptance untested."""
    first, last = data.get('first', 1), data.get('last', 1)
    if type(first) is not int or type(last) is not int or not 1 <= first <= last <= 150 or last - first + 1 > 116:
        raise ValueError('Seleccione entre 1 y 116 segmentos del 1 al 150.')
    hsv = bytes.fromhex(encode_color(data.get('color'), data.get('brightness', 50)))
    indexes = bytes(range(first, last + 1))
    payload = bytes([0, 1, 0, 150, 1]) + hsv + bytes([0x80 | len(indexes)]) + indexes
    return base64.b64encode(payload).decode('ascii')


def validate_selection(indexes):
    if not isinstance(indexes, list) or not 1 <= len(indexes) <= 150 or any(type(i) is not int or not 1 <= i <= 150 for i in indexes) or len(set(indexes)) != len(indexes):
        raise ValueError('Seleccione índices únicos del 1 al 150.')
    return sorted(indexes)


def plan_pixels(pixels):
    if not isinstance(pixels, list) or not pixels:
        raise ValueError('El diseño necesita al menos un LED.')
    indexes = []
    groups = {}
    for pixel in pixels:
        if not isinstance(pixel, dict) or set(pixel) != {'index', 'color', 'brightness'}:
            raise ValueError('Datos de LED inválidos.')
        indexes.append(pixel['index'])
        hsv = encode_color(pixel['color'], pixel['brightness'])
        groups.setdefault(hsv, []).append(pixel['index'])
    validate_selection(indexes)
    plan = []
    for hsv, group in groups.items():
        group.sort()
        for start in range(0, len(group), 116):
            chunk = group[start:start + 116]
            payload = bytes([0, 1, 0, 150, 1]) + bytes.fromhex(hsv) + bytes([0x80 | len(chunk)]) + bytes(chunk)
            plan.append(base64.b64encode(payload).decode('ascii'))
    return plan


def encode_scene(data):
    """Official Tuya custom scene layout; numeric mode interpretation untested."""
    mode, speed = data.get('mode', 2), data.get('speed', 50)
    if type(mode) is not int or mode not in (0, 1, 2, 3) or type(speed) is not int or not 1 <= speed <= 100:
        raise ValueError('Modo o velocidad nativa inválidos.')
    brightness = number(data.get('brightness', 50), 1, 100)
    colors = data.get('colors')
    if not isinstance(colors, list) or not 1 <= len(colors) <= 8 or type(data.get('segmented', False)) is not bool:
        raise ValueError('La escena nativa necesita entre 1 y 8 colores.')
    payload = bytes([1, 200, mode, speed, speed, 0x60 | (0x80 if data.get('segmented') else 0), 0, 0, round(brightness)])
    for color in colors:
        hsv = bytes.fromhex(encode_color(color, 100))
        payload += hsv[:2] + bytes([round(int.from_bytes(hsv[2:4], 'big') / 10)])
    return base64.b64encode(payload).decode('ascii')


class DemoDevice:
    def __init__(self):
        self.dps = {'20': False, '21': 'colour', '24': '001e03e801f4'}

    def status(self):
        return {'dps': dict(self.dps)}

    def set_multiple_values(self, values):
        self.dps.update(values)
        return self.status()


class Controller:
    def __init__(self, device, interval=0.5):
        self.device = device
        self.interval = interval
        self.io_lock = threading.Lock()
        self.action_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker = None
        self.last_write = 0
        self.effect_error = None

    def _call(self, values=None, cancel=None, deadline=None):
        def check_effect():
            if cancel is not None and (cancel.is_set() or time.monotonic() >= deadline):
                raise EffectCancelled()
        with self.io_lock:
            check_effect()
            try:
                if values is not None:
                    delay = max(0, self.interval - (time.monotonic() - self.last_write))
                    if cancel is None:
                        time.sleep(delay)
                    else:
                        write_at = time.monotonic() + delay
                        while time.monotonic() < write_at:
                            check_effect()
                            cancel.wait(max(0, min(write_at, deadline) - time.monotonic()))
                    check_effect()
                    try:
                        response = self.device.set_multiple_values(values)
                    finally:
                        self.last_write = time.monotonic()
                else:
                    response = self.device.status()
            except EffectCancelled:
                raise
            except Exception:
                raise DeviceError('Controlador no disponible. Revise el Wi-Fi y cierre Smart Life; después reintente.') from None
            if not isinstance(response, dict) or 'Error' in response or 'Err' in response:
                raise DeviceError('El controlador no confirmó la solicitud. Revise el Wi-Fi y reintente.')
            return response

    def status(self):
        dps = self._call().get('dps', {})
        if not isinstance(dps, dict) or type(dps.get('20')) is not bool:
            raise DeviceError('El controlador devolvió un estado inválido. Reintente la conexión.')
        result = {'power': dps.get('20'), 'mode': dps.get('21') if dps.get('21') in ('colour', 'white', 'scene', 'music') else 'unknown', 'color': None, 'brightness': None}
        raw = dps.get('24')
        if isinstance(raw, str) and re.fullmatch('[0-9a-fA-F]{12}', raw):
            h, s, v = [int(raw[i:i + 4], 16) for i in (0, 4, 8)]
            if h <= 360 and s <= 1000 and v <= 1000:
                rgb = colorsys.hsv_to_rgb(h / 360, s / 1000, 1)
                result.update(color='#' + ''.join(f'{round(x * 255):02x}' for x in rgb), brightness=round(v / 10))
        result['effect'] = self.effect_state()
        return result

    def control(self, data):
        if not isinstance(data, dict) or not data or set(data) - {'power', 'color', 'brightness'}:
            raise ValueError('Campos de control inválidos.')
        values = {}
        if 'power' in data:
            if type(data['power']) is not bool:
                raise ValueError('El encendido debe ser verdadero o falso.')
            values['20'] = data['power']
        if 'color' in data:
            values.update({'21': 'colour', '24': encode_color(data['color'], data.get('brightness', 50))})
        elif 'brightness' in data:
            raise ValueError('El brillo necesita un color.')
        with self.action_lock:
            self.stop()
            self._call(values)

    def reconnect(self):
        """Read-only retry using the configured LAN connection."""
        return self.status()

    def native(self, data):
        if not isinstance(data, dict) or data.get('acknowledge_experimental') is not True:
            raise ValueError('Debe aceptar el protocolo experimental.')
        if data.get('kind') == 'paint' and not set(data) - {'kind', 'acknowledge_experimental', 'color', 'brightness', 'first', 'last'}:
            values = {'61': encode_paint(data)}
        elif data.get('kind') == 'scene' and not set(data) - {'kind', 'acknowledge_experimental', 'colors', 'brightness', 'mode', 'speed', 'segmented'}:
            values = {'21': 'scene', '51': encode_scene(data)}
        else:
            raise ValueError('Campos nativos inválidos.')
        with self.action_lock:
            self.stop()
            self._call(values)

    def paint_pixels(self, data):
        if not isinstance(data, dict) or set(data) != {'acknowledge_experimental', 'pixels'} or data['acknowledge_experimental'] is not True:
            raise ValueError('Debe aceptar el protocolo experimental.')
        plan = plan_pixels(data['pixels'])
        with self.action_lock:
            self.stop()
            for count, payload in enumerate(plan):
                try:
                    self._call({'61': payload})
                except DeviceError:
                    raise DeviceError(f'Aplicación incompleta: {count}/{len(plan)} comandos confirmados. Parte de la tira puede haber cambiado; revise el resultado físico.') from None

    def effect_state(self):
        return {'running': bool(self.worker and self.worker.is_alive()), 'error': self.effect_error}

    def stop(self):
        self.stop_event.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join()
        self.worker = None

    def start(self, data):
        effect = validate_effect(data)
        with self.action_lock:
            self.stop()
            self.effect_error = None
            self.stop_event = threading.Event()
            self.worker = threading.Thread(target=self._run, args=(effect,), daemon=True)
            self.worker.start()

    def _run(self, effect):
        start = time.monotonic()
        deadline = start + effect['duration']
        cancel = self.stop_event
        while not self.stop_event.is_set() and time.monotonic() - start < effect['duration']:
            elapsed = time.monotonic() - start
            color, brightness = effect['color'], effect['brightness']
            delay = 0.5
            if effect['kind'] == 'rainbow':
                rgb = colorsys.hsv_to_rgb((elapsed / effect['speed']) % 1, 1, 1)
                color = '#' + ''.join(f'{round(x * 255):02x}' for x in rgb)
            elif effect['kind'] == 'breathe':
                brightness *= 0.15 + 0.85 * (1 - math.cos(elapsed / effect['speed'] * 2 * math.pi)) / 2
            else:
                total = sum(step['seconds'] for step in effect['steps'])
                position = elapsed % total
                for step in effect['steps']:
                    if position < step['seconds']:
                        color, delay = step['color'], step['seconds'] - position
                        break
                    position -= step['seconds']
            try:
                if 'indexes' in effect:
                    plan = plan_pixels([{'index': i, 'color': color, 'brightness': brightness} for i in effect['indexes']])
                    for payload in plan:
                        if self.stop_event.is_set() or time.monotonic() - start >= effect['duration']:
                            break
                        self._call({'61': payload}, cancel, deadline)
                else:
                    self._call({'21': 'colour', '24': encode_color(color, brightness)}, cancel, deadline)
            except EffectCancelled:
                return
            except DeviceError as error:
                self.effect_error = str(error)
                return
            self.stop_event.wait(min(delay, max(0, effect['duration'] - (time.monotonic() - start))))


def make_server(controller, port=8765, demo=False):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type='application/json'):
            if content_type == 'application/json':
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self, mutation=False):
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host:
                return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + host:
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return False
            return not mutation or (origin == 'http://' + host and secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), token))

        def do_GET(self):
            if not self.allowed():
                return self.send(403, {'error': 'Se requiere un origen local.'})
            if self.path == '/api/session':
                return self.send(200, {'token': token, 'demo': demo})
            if self.path in ('/api/state', '/api/reconnect'):
                try:
                    return self.send(200, controller.status())
                except DeviceError as error:
                    return self.send(503, {'error': str(error), 'effect': controller.effect_state()})
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if self.path not in files:
                return self.send(404, {'error': 'No encontrado.'})
            name, mime = files[self.path]
            self.send(200, (ROOT / 'static' / name).read_bytes(), mime)

        def do_POST(self):
            if not self.allowed(True):
                return self.send(403, {'error': 'Se requiere una sesión local. Recargue la página.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('Se requiere una solicitud JSON pequeña.')
                data = json.loads(self.rfile.read(length))
                if self.path == '/api/control':
                    controller.control(data)
                elif self.path == '/api/effects':
                    controller.start(data)
                elif self.path == '/api/native':
                    controller.native(data)
                elif self.path == '/api/pixels':
                    controller.paint_pixels(data)
                elif self.path == '/api/effects/stop':
                    if data != {}:
                        raise ValueError('Detener requiere un objeto vacío.')
                    with controller.action_lock:
                        controller.stop()
                else:
                    return self.send(404, {'error': 'No encontrado.'})
                self.send(200, {'accepted': True, 'effect': controller.effect_state()})
            except (ValueError, UnicodeDecodeError):
                self.send(400, {'error': 'Solicitud inválida. Revise color, tiempos, brillo y campos obligatorios.'})
            except DeviceError as error:
                self.send(503, {'error': str(error)})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.demo:
        device = DemoDevice()
    else:
        import tinytuya
        try:
            config = json.loads((ROOT / '.secrets' / 'device-local.json').read_text())
            device = tinytuya.Device(config['device_id'], config['ip'], config['local_key'], version=config['version'])
            device.set_socketTimeout(3)
            device.set_socketRetryLimit(1)
        except Exception:
            parser.exit(1, 'No se pudo cargar la configuración privada. Revise .secrets/device-local.json.\n')
    controller = Controller(device)
    server = make_server(controller, args.port, args.demo)
    print(f'LED console: http://127.0.0.1:{server.server_port} ({"DEMO" if args.demo else "LOCAL DEVICE"})', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        server.server_close()


if __name__ == '__main__':
    main()
