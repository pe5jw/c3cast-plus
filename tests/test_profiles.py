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
from tuners import HackRfBackend, QocastBackend, RtlSdrBackend


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


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / 'config.json'
        patcher = patch.object(web, 'CONFIG', self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.client = web.app.test_client()


class Api(ApiBase):
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
        self.assertIn('class="btn rx-tune"', page)
        self.assertIn('id="rxTargetLabel"', page)       # receivers.js writes to it
        self.assertEqual(page.count('class="freqrow rx-row"'), 2)   # one receive row per receiver

    def test_index_qo100_keeps_spectrum(self):
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('spectrum.js', page)
        self.assertNotIn('class="btn rx-tune"', page)

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


class HackRf(unittest.TestCase):
    def test_plan_keeps_the_signal_beside_the_dc_spike(self):
        for sr in (125, 250, 333, 500, 1000, 1500):
            with self.subTest(sr=sr):
                rate, offset = HackRfBackend.plan(sr)
                half_width = sr * 1000 * 1.35 / 2                  # occupied half bandwidth, roll-off 0.35 at most
                self.assertGreater(offset - half_width, 0)          # the spike (at 0) is outside the signal
                self.assertLess(offset + half_width, rate / 2 * 0.9)  # the signal stays inside the passband
                self.assertGreaterEqual(rate, 2000000)

    def test_commands(self):
        backend = HackRfBackend(Path('bin'), 0.0, 0.0, lna=33, vga=31, amp=False, antenna=True)
        self.assertEqual((backend.lna, backend.vga), (32, 30))        # snapped to the real steps
        reader, demodulator = backend.command(backend.check(2405.0, 500), 500)
        self.assertEqual(reader[1:], ['2405375000', '2500000', '32', '30', '0', '1', '--watch-stdin'])
        arg = lambda name: demodulator[demodulator.index(name) + 1]
        self.assertEqual((arg('--format'), arg('--rate'), arg('--carrier'), arg('--rs')), ('cs8', '2.5', '-375', '0.5'))

    def test_ranges(self):
        self.assertAlmostEqual(HackRfBackend(Path('.'), 9750.0, 0.0).check(10491.5, 1500), 741.5)
        with self.assertRaises(ValueError):
            HackRfBackend(Path('.'), 9750.0, 0.0).check(2405.0, 500)  # an LNB LO on the 2.4 GHz band
        with self.assertRaises(ValueError):
            HackRfBackend(Path('.'), 0.0, 0.0).check(2405.0, 2000)    # beyond datvrx's symbol rate here

    def test_profile_defaults_and_assignment(self):
        self.assertEqual(profiles.kind_of('hackrf'), 'hackrf')
        self.assertEqual(profiles.get_offsets({})['2400']['hackrf']['lo'], 0.0)
        self.assertEqual(profiles.check_offsets(offsets())[1], '')
        import tuner_assignment
        detected = [{'id': 'rtlsdr', 'count': 1, 'label': 'RTL-SDR'}, {'id': 'hackrf', 'count': 1, 'label': 'HackRF One'}]
        with patch.object(tuner_assignment, '_slots', []), patch('usb_devices.detect', return_value=detected), \
             patch.object(tuner_assignment, 'free_tuners', return_value=[]), \
             patch.object(tuner_assignment, '_driver_readable', return_value=True):
            slots = tuner_assignment.slots()
        self.assertEqual([slot['tuner'] for slot in slots], ['rtlsdr', 'hackrf'])

    def test_receiver_gets_its_own_lo(self):
        cfg = {'profile': '2400', 'rx_offsets': offsets(**{'2400.hackrf.corr': -0.004})}
        command = ReceiverArguments.command_for(ReceiverArguments(), cfg, 'hackrf')
        self.assertEqual(float(command[command.index('--lnb-lo') + 1]), 0.0)
        self.assertEqual(float(command[command.index('--lnb-correction') + 1]), -0.004)

    def test_setup_page_lists_hackrf(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web, 'CONFIG', Path(tmp) / 'config.json'):
            page = web.app.test_client().get('/setup').get_data(as_text=True)
        self.assertIn('data-off="2400.hackrf.lo"', page)


class Assignment(unittest.TestCase):
    MINI = dict(tuner='minitiouner', channel=1, serial='DA3LGWB6', label='MiniTiouner DA3LGWB6')
    RTL = dict(tuner='rtlsdr', channel=1, serial=None, label='RTL-SDR')
    HACK = dict(tuner='hackrf', channel=1, serial=None, label='HackRF One')
    PICO = [dict(tuner='picotuner', channel=n, serial=None, label=f'PicoTuner channel {n}') for n in (1, 2)]

    def assigned(self, found, assign):
        import tuner_assignment
        with patch.object(tuner_assignment, 'slots', return_value=found):
            return {n: slot and slot['tuner'] for n, slot in tuner_assignment.assigned({'rx_assign': assign}).items()}

    def test_automatic_is_the_old_behaviour(self):
        self.assertEqual(self.assigned([self.MINI, self.RTL, self.HACK], {}), {1: 'minitiouner', 2: 'rtlsdr', 3: None})

    def test_pinned_tuners_are_not_taken_automatically(self):
        got = self.assigned([self.MINI, self.RTL, self.HACK], {'1': 'hackrf', '2': 'auto', '3': 'rtlsdr'})
        self.assertEqual(got, {1: 'hackrf', 2: 'minitiouner', 3: 'rtlsdr'})

    def test_pinned_but_absent_stays_empty(self):
        self.assertEqual(self.assigned([self.RTL], {'1': 'minitiouner:DA3LGWB6', '2': 'auto'}), {1: None, 2: 'rtlsdr', 3: None})

    def test_rx3_panel_only_when_used(self):
        import tuner_assignment
        self.assertEqual(tuner_assignment.enabled({}), (1, 2))
        self.assertEqual(tuner_assignment.enabled({'rx_assign': {'3': 'auto'}}), (1, 2, 3))

    def test_pico_channel_two_shares_with_whichever_receiver_has_channel_one(self):
        import tuner_assignment
        cfg = {'rx_assign': {'1': 'rtlsdr', '2': 'picotuner:2', '3': 'picotuner:1'}}
        with patch.object(receivers, '_workers', {}), patch.object(receivers, '_closing', False), \
             patch.object(receivers, '_config', return_value=cfg), \
             patch.object(tuner_assignment, 'slots', return_value=[self.RTL, *self.PICO]), \
             patch.object(receivers.threading, 'Thread'):
            receivers.start(2)
            self.assertEqual(receivers._workers[2]['primary'], 3)
            self.assertEqual(receivers._workers[2]['ts_port'], receivers._workers[3]['ts_port2'])
            self.assertNotIn(1, receivers._workers)

    def test_status_labels(self):
        import tuner_assignment
        cfg = {'rx_assign': {'1': 'hackrf', '2': 'none', '3': 'auto'}}
        with patch.object(receivers, '_config', return_value=cfg), patch.object(receivers, '_workers', {}), \
             patch.object(tuner_assignment, 'slots', return_value=[self.RTL]):
            rows = receivers.status()
        self.assertEqual([r['number'] for r in rows], [1, 2, 3])
        self.assertIn('not found', rows[0]['label'])
        self.assertIn('Not used', rows[1]['label'])
        self.assertEqual(rows[2]['label'], 'RTL-SDR')


class AssignmentApi(ApiBase):
    def test_save_and_refuse(self):
        import tuner_assignment
        with patch.object(receivers, 'restart') as restart, patch.object(tuner_assignment, 'slots', return_value=[]):
            ok = self.client.post('/api/receivers/assign', json={'assign': {'1': 'rtlsdr', '2': 'auto', '3': 'hackrf'}})
            self.assertEqual(ok.status_code, 200, ok.json)
            restart.assert_called_once()
            self.assertEqual(json.loads(self.config.read_text())['rx_assign']['3'], 'hackrf')
            twice = self.client.post('/api/receivers/assign', json={'assign': {'1': 'rtlsdr', '2': 'rtlsdr', '3': 'none'}})
            self.assertEqual(twice.status_code, 400)
            odd = self.client.post('/api/receivers/assign', json={'assign': {'1': '../etc', '2': 'auto', '3': 'none'}})
            self.assertEqual(odd.status_code, 400)
            page = self.client.get('/').get_data(as_text=True)
            self.assertEqual(page.count('class="panel rx-panel"'), 3)
            self.assertIn('rx-three', page)

    def test_rescan_releases_then_lists_again(self):
        import tuner_assignment
        with patch.object(receivers, 'restart') as restart, patch.object(tuner_assignment, 'reset') as reset, \
             patch.object(tuner_assignment, 'slots', return_value=[Assignment.HACK]):
            reply = self.client.post('/api/receivers/rescan', json={})
        restart.assert_called_once()
        reset.assert_called_once()
        self.assertEqual(reply.json['detected'], [{'key': 'hackrf', 'label': 'HackRF One'}])


if __name__ == '__main__':
    unittest.main()
