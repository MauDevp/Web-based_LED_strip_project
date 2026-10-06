import unittest
import json
import http.client
import base64
import threading
import time
from unittest.mock import Mock

from dashboard import Controller, DemoDevice, DeviceError, encode_color, encode_paint, encode_scene, make_server, validate_effect, plan_pixels, validate_selection


class ControlsTest(unittest.TestCase):
    def setUp(self):
        self.device = Mock()
        self.device.set_multiple_values.return_value = {'dps': {}}
        self.controller = Controller(self.device, interval=0)

    def tearDown(self):
        self.controller.stop()

    def test_hsv_encoding(self):
        self.assertEqual(encode_color('#ff0000', 100), '000003e803e8')
        self.assertEqual(encode_color('#00ff00', 50), '007803e801f4')

    def test_selection_validation(self):
        for values in ([], [0], [151], [1, 1], [True], [1.5], '1'):
            with self.assertRaises(ValueError):
                validate_selection(values)

    def test_150_pixel_selection_chunks(self):
        pixels = [{'index': i, 'color': '#ff0000', 'brightness': 50} for i in range(1, 151)]
        plan = plan_pixels(pixels)
        self.assertEqual(len(plan), 2)
        self.assertTrue(all(len(base64.b64decode(value)) <= 128 for value in plan))
        self.assertEqual(sum(len(base64.b64decode(value)) - 12 for value in plan), 150)

    def test_layout_groups_colors(self):
        plan = plan_pixels([{'index': 1, 'color': '#ff0000', 'brightness': 50}, {'index': 2, 'color': '#00ff00', 'brightness': 50}, {'index': 3, 'color': '#ff0000', 'brightness': 50}])
        self.assertEqual(len(plan), 2)
        self.assertEqual(base64.b64decode(plan[0])[-2:], b'\x01\x03')

    def test_layout_invalid_pixels(self):
        for pixels in ([], [{'index': 1, 'color': '#ffffff', 'brightness': 50}, {'index': 1, 'color': '#ffffff', 'brightness': 50}], [{'index': 2, 'color': 'red', 'brightness': 50}], [{'index': 1, 'color': '#ff0000', 'brightness': 50, 'key': 'private'}]):
            with self.assertRaises(ValueError):
                plan_pixels(pixels)

    def test_section_effect_requires_ack_and_selection(self):
        for data in ({'kind': 'rainbow', 'indexes': [1]}, {'kind': 'rainbow', 'indexes': [], 'acknowledge_experimental': True}):
            with self.assertRaises(ValueError):
                self.controller.start(data)
        self.device.set_multiple_values.assert_not_called()

    def test_partial_failure_is_not_success(self):
        self.device.set_multiple_values.side_effect = [{'dps': {}}, {'Error': 'private'}]
        with self.assertRaises(DeviceError) as error:
            self.controller.paint_pixels({'acknowledge_experimental': True, 'pixels': [{'index': i, 'color': '#ff0000', 'brightness': 50} for i in range(1, 151)]})
        self.assertIn('1/2', str(error.exception))

    def test_section_effect_stop(self):
        sent = threading.Event()
        self.device.set_multiple_values.side_effect = lambda values: (sent.set() or {'dps': {}})
        self.controller.start({'kind': 'rainbow', 'indexes': [1, 2], 'acknowledge_experimental': True})
        self.assertTrue(sent.wait(1))
        self.controller.stop()
        self.assertEqual(set(self.device.set_multiple_values.call_args.args[0]), {'61'})

    def test_section_effect_deadline_while_waiting_for_lock(self):
        self.controller.io_lock.acquire()
        try:
            self.controller.start({'kind': 'rainbow', 'duration': 1, 'indexes': [1], 'acknowledge_experimental': True})
            time.sleep(1.1)
        finally:
            self.controller.io_lock.release()
        self.controller.worker.join(1)
        self.device.set_multiple_values.assert_not_called()

    def test_section_effect_deadline_during_throttle(self):
        self.controller.interval = 1.2
        self.controller.last_write = time.monotonic() + 5
        self.controller.start({'kind': 'rainbow', 'duration': 1, 'indexes': [1], 'acknowledge_experimental': True})
        self.controller.worker.join(2)
        self.device.set_multiple_values.assert_not_called()

    def test_reconnect_is_read_only(self):
        self.device.status.return_value = {'dps': {'20': True}}
        self.controller.reconnect()
        self.device.set_multiple_values.assert_not_called()

    def test_power_is_only_dp20(self):
        self.controller.control({'power': True})
        self.device.set_multiple_values.assert_called_once_with({'20': True})

    def test_color_does_not_turn_on_strip(self):
        self.controller.control({'color': '#ff0000', 'brightness': 50})
        self.device.set_multiple_values.assert_called_once_with({'21': 'colour', '24': '000003e801f4'})

    def test_invalid_controls_never_write(self):
        for value in ({'power': 1}, {'color': 'red'}, {'color': '#ffffff', 'brightness': 101}, {'46': 1}):
            with self.assertRaises(ValueError):
                self.controller.control(value)
        self.device.set_multiple_values.assert_not_called()

    def test_errors_do_not_leak_device_response(self):
        self.device.set_multiple_values.return_value = {'Error': 'secret', 'Payload': 'private'}
        with self.assertRaises(DeviceError) as caught:
            self.controller.control({'power': True})
        self.assertNotIn('secret', str(caught.exception))

    def test_status_allowlist(self):
        self.device.status.return_value = {'dps': {'20': True, '21': 'colour', '24': '000003e803e8', '46': 'secret'}, 'local_key': 'private'}
        state = self.controller.status()
        self.assertEqual(state['color'], '#ff0000')
        self.assertNotIn('secret', str(state))
        self.assertNotIn('private', str(state))

    def test_malformed_dps_is_sanitized(self):
        for response in ({'dps': None}, {}, {'dps': {}}, {'dps': {'61': 'raw'}}):
            self.device.status.return_value = response
            with self.assertRaises(DeviceError):
                self.controller.status()

    def test_effect_bounds(self):
        for data in ({'kind': 'strobe'}, {'kind': 'rainbow', 'duration': 301}, {'kind': 'custom', 'steps': []}, {'kind': 'custom', 'steps': [{'color': '#ffffff', 'seconds': 0.1}]}):
            with self.assertRaises(ValueError):
                validate_effect(data)

    def test_effect_stop(self):
        self.controller.start({'kind': 'rainbow', 'duration': 5, 'speed': 2, 'brightness': 20})
        self.controller.stop()
        self.assertFalse(self.controller.effect_state()['running'])

    def test_custom_validation(self):
        result = validate_effect({'kind': 'custom', 'steps': [{'color': '#ff0000', 'seconds': 2}]})
        self.assertEqual(result['duration'], 60)

    def test_native_paint_fixture(self):
        self.assertEqual(base64.b64decode(encode_paint({'color': '#ff0000', 'brightness': 100, 'first': 1, 'last': 1})).hex(), '0001009601000003e803e88101')

    def test_native_scene_fixture(self):
        self.assertEqual(base64.b64decode(encode_scene({'colors': ['#ff0000', '#00ff00', '#0000ff'], 'brightness': 50, 'mode': 2, 'speed': 50, 'segmented': False})).hex(), '01c80232326000003200006400786400f064')

    def test_native_requires_explicit_ack(self):
        with self.assertRaises(ValueError):
            self.controller.native({'kind': 'paint', 'color': '#ff0000'})
        self.device.set_multiple_values.assert_not_called()

    def test_native_writes_allowlisted_dps(self):
        self.controller.native({'kind': 'paint', 'acknowledge_experimental': True, 'color': '#ff0000'})
        self.assertEqual(set(self.device.set_multiple_values.call_args.args[0]), {'61'})
        self.controller.native({'kind': 'scene', 'acknowledge_experimental': True, 'colors': ['#ff0000'], 'segmented': True})
        self.assertEqual(set(self.device.set_multiple_values.call_args.args[0]), {'21', '51'})

    def test_native_invalid_payloads(self):
        for data in ({'colors': []}, {'colors': ['#ff0000'], 'mode': 20}, {'colors': ['#ff0000'], 'segmented': 1}):
            with self.assertRaises(ValueError):
                encode_scene(data)
        with self.assertRaises(ValueError):
            self.controller.native({'kind': 'unknown', 'acknowledge_experimental': True})

    def test_effect_types_send_color_without_power(self):
        for data in ({'kind': 'breathe'}, {'kind': 'custom', 'steps': [{'color': '#ff0000', 'seconds': 2}]}):
            sent = threading.Event()
            self.device.set_multiple_values.side_effect = lambda values: (sent.set() or {'dps': {}})
            self.controller.start(data)
            self.assertTrue(sent.wait(1))
            self.controller.stop()
            self.assertEqual(set(self.device.set_multiple_values.call_args.args[0]), {'21', '24'})

    def test_effect_error_visible(self):
        self.device.set_multiple_values.return_value = {'Error': 'private'}
        self.controller.start({'kind': 'rainbow'})
        self.controller.worker.join(1)
        self.assertFalse(self.controller.effect_state()['running'])
        self.assertIn('confirmó', self.controller.effect_state()['error'])

    def test_network_exception_sanitized(self):
        self.device.status.side_effect = OSError('private key')
        with self.assertRaises(DeviceError) as caught:
            self.controller.status()
        self.assertNotIn('private', str(caught.exception))

    def test_paint_index_bounds(self):
        for first, last in ((0, 1), (1, 151), (2, 1), (1, 117), (1.5, 2)):
            with self.assertRaises(ValueError):
                encode_paint({'color': '#ff0000', 'first': first, 'last': last})

    def test_concurrent_requests_are_serialized(self):
        active = 0
        overlap = []
        lock = threading.Lock()
        def write(values):
            nonlocal active
            with lock:
                active += 1
                overlap.append(active)
            time.sleep(.01)
            with lock:
                active -= 1
            return {'dps': {}}
        self.device.set_multiple_values.side_effect = write
        workers = [threading.Thread(target=self.controller.control, args=({'power': True},)) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(max(overlap), 1)

    def test_stop_prevents_later_effect_writes(self):
        sent = threading.Event()
        self.device.set_multiple_values.side_effect = lambda values: (sent.set() or {'dps': {}})
        self.controller.start({'kind': 'rainbow', 'duration': 5})
        self.assertTrue(sent.wait(1))
        self.controller.stop()
        count = self.device.set_multiple_values.call_count
        time.sleep(.55)
        self.assertEqual(self.device.set_multiple_values.call_count, count)

    def test_write_rate_limit(self):
        times = []
        self.device.set_multiple_values.side_effect = lambda values: (times.append(time.monotonic()) or {'dps': {}})
        self.controller.interval = .05
        self.controller.control({'power': True})
        self.controller.control({'power': False})
        self.assertGreaterEqual(times[1] - times[0], .049)

    def test_failed_write_attempts_are_rate_limited(self):
        times = []
        def fail(values):
            times.append(time.monotonic())
            raise OSError('private')
        self.device.set_multiple_values.side_effect = fail
        self.controller.interval = .5
        for _ in range(2):
            with self.assertRaises(DeviceError):
                self.controller.control({'power': True})
        self.assertGreaterEqual(times[1] - times[0], .499)


class HTTPTest(unittest.TestCase):
    def setUp(self):
        self.device = DemoDevice()
        self.server = make_server(Controller(self.device, interval=0), 0, True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.host = f'127.0.0.1:{self.port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, path, data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port)
        connection.request('GET' if data is None else 'POST', path, None if data is None else json.dumps(data), headers or {})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_no_write_on_get_and_no_secrets_paths(self):
        self.assertEqual(self.request('/api/state')[0], 200)
        self.assertFalse(self.device.dps['20'])
        for path in ('/.secrets/device-local.json', '/../dashboard.py', '/%2e%2e/.secrets/device-local.json'):
            self.assertEqual(self.request(path)[0], 404)

    def test_host_origin_and_csrf(self):
        self.assertEqual(self.request('/api/session', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/session', headers={'Origin': 'https://evil.example'})[0], 403)
        token = json.loads(self.request('/api/session')[1])['token']
        headers = {'Content-Type': 'application/json', 'Origin': 'http://' + self.host, 'X-CSRF-Token': token}
        self.assertEqual(self.request('/api/control', {'power': True})[0], 403)
        self.assertEqual(self.request('/api/control', {'power': True}, headers)[0], 200)
        self.assertTrue(self.device.dps['20'])
        headers['Origin'] = 'http://evil.example'
        self.assertEqual(self.request('/api/control', {'power': False}, headers)[0], 403)

    def test_invalid_json_shape(self):
        token = json.loads(self.request('/api/session')[1])['token']
        headers = {'Content-Type': 'application/json', 'Origin': 'http://' + self.host, 'X-CSRF-Token': token}
        for body in ([], {'power': 'yes'}, {'color': '#ffffff', 'brightness': float('nan')}, {'color': '#ffffff', 'brightness': 10 ** 400}):
            self.assertEqual(self.request('/api/control', body, headers)[0], 400)

    def test_malformed_device_status_returns_503(self):
        self.device.status = lambda: {'dps': None, 'local_key': 'private'}
        status, body = self.request('/api/state')
        self.assertEqual(status, 503)
        self.assertNotIn(b'private', body)

    def test_reconnect_endpoint_read_only_and_failure(self):
        original = dict(self.device.dps)
        self.assertEqual(self.request('/api/reconnect')[0], 200)
        self.assertEqual(self.device.dps, original)
        self.device.status = lambda: {'Error': 'private'}
        status, body = self.request('/api/reconnect')
        self.assertEqual(status, 503)
        self.assertNotIn(b'private', body)
        self.device.status = lambda: {}
        self.assertEqual(self.request('/api/reconnect')[0], 503)
        self.assertEqual(self.request('/api/state')[0], 503)

    def test_150_layout_body_fits_endpoint(self):
        token = json.loads(self.request('/api/session')[1])['token']
        headers = {'Content-Type': 'application/json', 'Origin': 'http://' + self.host, 'X-CSRF-Token': token}
        data = {'acknowledge_experimental': True, 'pixels': [{'index': i, 'color': '#ff0000', 'brightness': 99.99} for i in range(1, 151)]}
        self.assertGreater(len(json.dumps(data)), 8192)
        self.assertEqual(self.request('/api/pixels', data, headers)[0], 200)

    def test_demo_api_effects_and_native(self):
        token = json.loads(self.request('/api/session')[1])['token']
        headers = {'Content-Type': 'application/json', 'Origin': 'http://' + self.host, 'X-CSRF-Token': token}
        self.assertEqual(self.request('/api/effects', {'kind': 'breathe'}, headers)[0], 200)
        self.assertEqual(self.request('/api/effects/stop', {}, headers)[0], 200)
        self.assertEqual(self.request('/api/effects/stop', {'bad': 1}, headers)[0], 400)
        self.assertEqual(self.request('/api/native', {'kind': 'paint', 'acknowledge_experimental': True, 'color': '#ff0000'}, headers)[0], 200)
        self.assertEqual(self.request('/not-found', {}, headers)[0], 404)
        self.assertEqual(self.request('/')[0], 200)


if __name__ == '__main__':
    unittest.main()
