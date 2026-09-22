import importlib.util
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('start_network_under_test', SCRIPT_DIR / 'start_local.py')
launcher = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(SCRIPT_DIR))
try:
    spec.loader.exec_module(launcher)
finally:
    sys.path.pop(0)


class NetworkDetectionTests(TestCase):
    def test_active_address_is_detected_again_after_network_change(self):
        with patch.object(launcher.socket, 'socket') as factory:
            probe = factory.return_value.__enter__.return_value
            probe.getsockname.side_effect = [('192.168.1.12', 1234), ('172.20.10.2', 1235)]
            self.assertEqual(launcher.detect_network_address(), '192.168.1.12')
            self.assertEqual(launcher.detect_network_address(), '172.20.10.2')
            probe.send.assert_not_called()
            probe.sendto.assert_not_called()

    def test_no_network_is_reported_before_any_setup(self):
        with patch.object(sys, 'argv', ['start', '--lan']), patch.object(launcher, 'detect_network_address', side_effect=OSError), patch.object(launcher, 'run') as run:
            self.assertEqual(launcher.main(), 1)
            run.assert_not_called()

    def test_rejects_loopback_or_all_interfaces(self):
        for address in ('127.0.0.1', '0.0.0.0', '224.0.0.1'):
            with self.subTest(address=address), patch.object(launcher.socket, 'socket') as factory:
                factory.return_value.__enter__.return_value.getsockname.return_value = (address, 1234)
                with self.assertRaises(OSError):
                    launcher.detect_network_address()

    def test_normal_start_stays_on_loopback(self):
        import errno
        with patch.object(sys, 'argv', ['start']), patch.object(launcher, 'detect_network_address') as detect, patch.object(launcher.socket, 'socket') as factory:
            probe = factory.return_value.__enter__.return_value
            probe.bind.side_effect = OSError(errno.EADDRINUSE, 'busy')
            self.assertEqual(launcher.main(), 1)
            detect.assert_not_called()
            probe.bind.assert_called_once_with(('127.0.0.1', 8000))
