"""Band profiles: QO-100 via the satellite, or 2.4 GHz received directly, each with its own receiver LO / correction."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webapp'))
import app as web
import profiles
import receivers
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webapp' / 'receiver'))
from tuners import QocastBackend, RtlSdrBackend


def offsets(**changes):
    """The default offsets with some values changed: offsets(**{'2400.rtlsdr.lo': 1500})."""
    data = json.loads(json.dumps(profiles.DEFAULT_OFFSETS))
    for path, value in changes.items():
        profile, kind, key = path.split('.')
        data[profile][kind][key] = value
    return data


class Profiles(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(profiles.get_profile({}), 'qo100')
        found = profiles.get_offsets({})
        self.assertEqual(found['qo100']['rtlsdr']['lo'], 9750.0)
        self.assertEqual(found['2400']['rtlsdr']['lo'], 1500.0)      # Nooelec Ham It Down
        self.assertEqual(found['2400']['nim']['lo'], 0.0)            # MiniTiouner direct

    def test_broken_config_falls_back(self):
        cfg = {'profile': 'mars', 'rx_offsets': {'qo100': {'rtlsdr': {'lo': 'x', 'corr': 0}}}}
        self.assertEqual(profiles.get_profile(cfg), 'qo100')
        self.assertEqual(profiles.get_offsets(cfg)['qo100']['rtlsdr']['lo'], 9750.0)

    def test_other_lnb_is_accepted(self):
        clean, error = profiles.check_offsets(offsets(**{'qo100.nim.lo': '9739', 'qo100.nim.corr': '0,05'}))
        self.assertEqual(error, '')
        self.assertEqual(clean['qo100']['nim'], {'lo': 9739.0, 'corr': 0.05})

    def test_rtlsdr_cannot_receive_2400_without_converter(self):
        clean, error = profiles.check_offsets(offsets(**{'2400.rtlsdr.lo': 0}))
        self.assertIsNone(clean)
        self.assertIn('RTL-SDR', error)

    def test_nonsense_values_refused(self):
        for change in ({'qo100.rtlsdr.lo': -1}, {'qo100.rtlsdr.corr': 99}, {'qo100.nim.lo': 'abc'}):
            with self.subTest(change=change):
                self.assertIsNone(profiles.check_offsets(offsets(**change))[0])


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / 'config.json'
        patcher = patch.object(web, 'CONFIG', self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.client = web.app.test_client()

    def test_bandplan_qo100(self):
        band = self.client.get('/api/bandplan').json
        self.assertEqual(band['profile'], 'qo100')
        self.assertEqual(band['offset_mhz'], 8089.5)
        self.assertTrue(band['allowed']['500000'])

    def test_bandplan_2400_has_no_slots_and_no_offset(self):
        self.config.write_text(json.dumps({'profile': '2400'}))
        band = self.client.get('/api/bandplan').json
        self.assertEqual((band['profile'], band['offset_mhz'], band['allowed']), ('2400', 0.0, {}))
        self.assertEqual((band['rx_min'], band['rx_max']), (2300.0, 2450.0))

    def test_index_2400_has_no_batc_spectrum(self):
        self.config.write_text(json.dumps({'profile': '2400'}))
        page = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('spectrum.js', page)
        self.assertNotIn('id="spectrum"', page)
        self.assertIn('id="rxTune"', page)
        self.assertIn('id="rxTargetLabel"', page)       # receivers.js writes to it

    def test_index_qo100_keeps_spectrum(self):
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('spectrum.js', page)
        self.assertNotIn('id="rxTune"', page)

    def test_save_profile_restarts_receivers(self):
        with patch.object(receivers, 'restart') as restart:
            reply = self.client.post('/api/profile', json={'profile': '2400', 'rx_offsets': offsets(**{'qo100.nim.lo': 9739})})
            self.assertEqual(reply.status_code, 200, reply.json)
            restart.assert_called_once()
            saved = json.loads(self.config.read_text())
            self.assertEqual(saved['profile'], '2400')
            self.assertEqual(saved['rx_offsets']['qo100']['nim']['lo'], 9739.0)
            self.assertEqual(self.client.get('/api/setup').json['profile'], '2400')
            restart.reset_mock()                        # the same values again: nothing to restart
            self.client.post('/api/profile', json={'profile': '2400', 'rx_offsets': offsets(**{'qo100.nim.lo': 9739})})
            restart.assert_not_called()

    def test_save_profile_refused(self):
        with patch.object(receivers, 'restart') as restart:
            self.assertEqual(self.client.post('/api/profile', json={'profile': 'x', 'rx_offsets': offsets()}).status_code, 400)
            bad = offsets(**{'2400.rtlsdr.lo': 0})
            self.assertEqual(self.client.post('/api/profile', json={'profile': '2400', 'rx_offsets': bad}).status_code, 400)
            with patch.dict(web.tx, {'proc': object()}):
                self.assertEqual(self.client.post('/api/profile', json={'profile': '2400', 'rx_offsets': offsets()}).status_code, 409)
            restart.assert_not_called()


class ReceiverArguments(unittest.TestCase):
    def command_for(self, cfg, tuner):
        slot = dict(tuner=tuner, channel=1, serial=None, label=tuner)
        entry = dict(port=19000, service_port=19001, ts_port=19002, ts_port2=19003, url='http://127.0.0.1:19000')
        proc = MagicMock()
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp, patch.object(receivers, '_closing', False), \
             patch.object(receivers.tuner_access, 'check', return_value=True), \
             patch.object(receivers.paths, 'DATA', Path(tmp)), \
             patch.object(receivers.paths, 'RECEIVER_DATA', Path(tmp)), \
             patch.object(receivers.paths, 'RECEIVER', Path(tmp)), \
             patch.object(receivers.paths, 'TOOLS', Path(tmp)), \
             patch.object(receivers.subprocess, 'Popen', return_value=proc) as popen, \
             patch.object(receivers.urllib.request, 'urlopen') as open_url:
            (Path(tmp) / 'ffmpeg' / 'bin').mkdir(parents=True)
            (Path(tmp) / 'ffmpeg' / 'bin' / 'ffmpeg.exe').touch()
            (Path(tmp) / 'config.json').write_text(json.dumps(cfg))
            open_url.return_value.__enter__.return_value.read.return_value = b'{}'
            receivers._launch(1, slot, entry, False)
            self.assertEqual(entry['state'], 'running', entry.get('error'))
            return popen.call_args.args[0]

    def arg(self, command, name):
        return command[command.index(name) + 1]

    def test_qo100_default(self):
        command = self.command_for({}, 'rtlsdr')
        self.assertEqual(self.arg(command, '--band'), 'qo100')
        self.assertEqual(float(self.arg(command, '--lnb-lo')), 9750.0)
        self.assertNotIn('--no-autotune', command)

    def test_2400_rtlsdr_and_minitiouner_get_their_own_lo(self):
        cfg = {'profile': '2400', 'rx_offsets': offsets(**{'2400.rtlsdr.corr': 0.012})}
        rtl = self.command_for(cfg, 'rtlsdr')
        self.assertEqual(self.arg(rtl, '--band'), '2400')
        self.assertEqual((float(self.arg(rtl, '--lnb-lo')), float(self.arg(rtl, '--lnb-correction'))), (1500.0, 0.012))
        self.assertIn('--no-autotune', rtl)              # there is no beacon on 2.4 GHz
        nim = self.command_for(cfg, 'minitiouner')
        self.assertEqual(float(self.arg(nim, '--lnb-lo')), 0.0)

    def test_restart_cancels_a_launch_in_progress(self):
        entry = {'state': 'starting'}
        with patch.object(receivers, '_workers', {1: entry}), patch.object(receivers, '_closing', False):
            receivers.restart()
            self.assertTrue(entry['cancelled'])
            self.assertEqual(receivers._workers, {})


class Tuners(unittest.TestCase):
    def test_ham_it_down_on_rtlsdr(self):
        backend = RtlSdrBackend(Path('.'), 1500.0, 0.0, 40)
        self.assertAlmostEqual(backend.check(2405.0, 500), 905.0)

    def test_minitiouner_direct_tunes_the_frequency_itself(self):
        backend = QocastBackend('minitiouner', None, 19001, 19002, 0.0, 0.01, 'horizontal', 'A', Path('.'))
        self.assertTrue(backend.direct)
        self.assertEqual(backend.lnb_power, 'off')        # never 18 V on an antenna
        calls = []
        def api(method, path, body=None, timeout=2.0):
            calls.append((path, body))
            return {'ok': True, 'source_connected': True}
        ctx = MagicMock()
        ctx.on_close, ctx.pumps = [], []
        with patch.object(backend, 'api', side_effect=api), patch('tuners.UdpSource'), patch('tuners.threading.Thread'):
            backend.open(ctx, 2405.0, 500)
        tune = next(body for path, body in calls if path == 'tune')
        self.assertTrue(tune['direct'])
        self.assertEqual(tune['frequency_khz'], 2405010)  # correction applied here in direct mode

    def test_minitiouner_with_lnb_is_not_direct(self):
        backend = QocastBackend('minitiouner', None, 19001, 19002, 9739.0, 0.0, 'horizontal', 'A', Path('.'))
        self.assertFalse(backend.direct)
        self.assertEqual(backend.lnb_power, 'horizontal')
        backend.check(10491.5, 1500)
        with self.assertRaises(ValueError):               # 2405 MHz with a 9739 LO is no IF at all
            backend.check(2405.0, 500)


class BiasTee(unittest.TestCase):
    def test_never_touched_when_off(self):
        backend = RtlSdrBackend(Path('.'), 1500.0, 0.0, 40)
        with patch('tuners.set_rtl_bias_tee') as switch:
            backend.apply_bias_tee()
            backend.shutdown()
        switch.assert_not_called()

    def test_on_then_off_then_shutdown(self):
        backend = RtlSdrBackend(Path('.'), 1500.0, 0.0, 40, bias_tee=True)
        with patch('tuners.set_rtl_bias_tee') as switch:
            backend.apply_bias_tee()
            switch.assert_called_with(Path('.'), True)
            backend.bias_tee = False
            backend.apply_bias_tee()
            switch.assert_called_with(Path('.'), False)
            self.assertFalse(backend.bias_applied)
            backend.bias_tee = True
            backend.apply_bias_tee()
            switch.reset_mock()
            backend.shutdown()                            # closing leaves no voltage on the input
            switch.assert_called_once_with(Path('.'), False)

    def test_failure_to_switch_on_stops_the_tuning(self):
        backend = RtlSdrBackend(Path('.'), 1500.0, 0.0, 40, bias_tee=True)
        with patch('tuners.set_rtl_bias_tee', side_effect=OSError('busy')):
            with self.assertRaises(ValueError):
                backend.apply_bias_tee()
        self.assertFalse(backend.bias_applied)

    def test_library_calls(self):
        import tuners
        library = MagicMock()
        library.rtlsdr_open.side_effect = lambda ref, index: setattr(ref._obj, 'value', 1234) or 0
        library.rtlsdr_set_bias_tee.return_value = 0
        with patch('ctypes.CDLL', return_value=library):
            tuners.set_rtl_bias_tee(Path('.'), True)
        self.assertEqual(library.rtlsdr_set_bias_tee.call_args.args[1], 1)
        library.rtlsdr_close.assert_called_once()
        library.rtlsdr_open.side_effect = lambda ref, index: -1
        with patch('ctypes.CDLL', return_value=library), self.assertRaises(OSError):
            tuners.set_rtl_bias_tee(Path('.'), True)


if __name__ == '__main__':
    unittest.main()
