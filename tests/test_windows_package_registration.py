"""Exercise current-user package queries without loading native Windows APIs."""
import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.platforms.windows import discovery_ext as windows


class PackageRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.packages = {'Claude_fixture': ['Claude_2_x64_fixture', 'Claude_1_x64_fixture']}
        self.snapshots = []
        self.errors = []
        self.capacities = []
        self.query = Mock(side_effect=self.native_query)
        self.dll = self.patch(ctypes, 'WinDLL', return_value=SimpleNamespace(GetPackagesByPackageFamily=self.query),
                              create=True)
        self.patch(ctypes, 'WinError', side_effect=lambda code: OSError(code, 'native query failed'), create=True)
        self.patch(windows, 'os', SimpleNamespace(name='nt'))
        self.powershell = self.patch(windows.subprocess, 'run', side_effect=AssertionError('No PowerShell'))

    def patch(self, target, name, *args, **kwargs):
        patched = patch.object(target, name, *args, **kwargs)
        self.addCleanup(patched.stop)
        return patched.start()

    def native_query(self, family, count, names, length, buffer):
        count = ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32)).contents
        length = ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32)).contents
        self.capacities.append((family, count.value, length.value, names is None))
        if self.errors:
            error = self.errors.pop(0)
            if error:
                return error
        values = self.snapshots.pop(0) if self.snapshots else self.packages.get(family, [])
        needed = sum(len(value) + 1 for value in values)
        sufficient = names is not None and count.value >= len(values) and length.value >= needed
        count.value, length.value = len(values), needed
        if not values:
            return 0
        if not sufficient:
            return 122
        offset = 0
        for index, value in enumerate(values):
            for character in value+'\0':
                buffer[offset] = character
                offset += 1
            names[index] = ctypes.cast(ctypes.byref(buffer, (offset-len(value)-1)*ctypes.sizeof(ctypes.c_wchar)),
                                       ctypes.c_wchar_p)
        return 0

    def test_two_phase_query_is_current_user_scoped_sorted_and_deduplicated_by_family(self):
        result = windows.package_registration_fingerprint(['Claude_fixture', 'Absent_fixture', 'Claude_fixture'])
        self.assertEqual(result, {'Absent_fixture': [], 'Claude_fixture': sorted(self.packages['Claude_fixture'])})
        self.assertEqual([call.args[0] for call in self.query.call_args_list],
                         ['Absent_fixture', 'Claude_fixture', 'Claude_fixture'])
        self.assertEqual(self.capacities[1], ('Claude_fixture', 0, 0, True))
        self.assertEqual(self.capacities[2], ('Claude_fixture', 2,
                         sum(len(value)+1 for value in self.packages['Claude_fixture']), False))
        self.dll.assert_called_once_with('kernel32', use_last_error=True)
        self.assertEqual(self.query.argtypes, [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32),
                         ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p])
        self.assertIs(self.query.restype, ctypes.c_long)
        self.powershell.assert_not_called()

    def test_capacity_growth_retries_once_with_the_new_sizes(self):
        current = ['Claude_new_long_version_x64_fixture', 'Claude_second_version_x64_fixture']
        self.snapshots = [['old'], current, current]
        self.assertEqual(windows.package_registration_fingerprint(['Claude_fixture']), {'Claude_fixture': sorted(current)})
        self.assertEqual(self.query.call_count, 3)
        self.assertEqual(self.capacities[2][1:3], (2, sum(len(value)+1 for value in current)))

    def test_continuous_capacity_growth_propagates_instead_of_returning_an_empty_registration(self):
        self.snapshots = [['old'], ['new', 'another'], ['new', 'another', 'third']]
        with self.assertRaises(OSError) as raised:
            windows.package_registration_fingerprint(['Claude_fixture'])
        self.assertEqual(raised.exception.errno, 122)
        self.assertEqual(self.query.call_count, 3)

    def test_removal_between_probe_and_read_is_a_successful_empty_registration(self):
        self.snapshots = [['old'], []]
        self.assertEqual(windows.package_registration_fingerprint(['Claude_fixture']), {'Claude_fixture': []})
        self.assertEqual(self.query.call_count, 2)

    def test_native_error_in_either_phase_propagates(self):
        for errors in ([5], [None, 5]):
            with self.subTest(errors=errors):
                self.errors = list(errors)
                with self.assertRaises(OSError) as raised:
                    windows.package_registration_fingerprint(['Claude_fixture'])
                self.assertEqual(raised.exception.errno, 5)

    def test_native_exception_and_missing_api_propagate(self):
        self.query.side_effect = OSError('native call failed')
        with self.assertRaisesRegex(OSError, 'native call failed'):
            windows.package_registration_fingerprint(['Claude_fixture'])
        self.dll.return_value = SimpleNamespace()
        with self.assertRaises(AttributeError):
            windows.package_registration_fingerprint(['Claude_fixture'])

    def test_empty_and_invalid_inputs_never_load_native_apis(self):
        self.assertEqual(windows.package_registration_fingerprint([]), {})
        for families in (['Bad!Family'], [''], [None]):
            with self.subTest(families=families), self.assertRaises(ValueError):
                windows.package_registration_fingerprint(families)
        self.dll.assert_not_called()

    def test_non_windows_never_loads_native_apis(self):
        with patch.object(windows, 'os', SimpleNamespace(name='posix')):
            self.assertEqual(windows.package_registration_fingerprint([]), {})
            with self.assertRaisesRegex(OSError, 'requires Windows'):
                windows.package_registration_fingerprint(['Claude_fixture'])
        self.dll.assert_not_called()


if __name__ == '__main__':
    unittest.main()
