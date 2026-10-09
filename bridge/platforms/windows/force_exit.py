"""Explicit, bounded Windows force-stop of one freshly verified GUI installation.

Never expands its initial target set. Process handles are opened and verified
before the first termination, so a PID cannot redirect a later termination.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time

from .desktop import process_inventory


BUDGET = 25
CHILD_TYPES = {'renderer', 'gpu-process', 'utility', 'zygote', 'broker',
               'crashpad-handler', 'ppapi', 'ppapi-broker'}
UNSUPPORTED = '此系统不支持 Windows 客户端强制结束'
INVALID = '桌面应用配置不可用，请重新扫描'
CHANGED = '客户端进程已变化或无法核对，已停止强制结束；请重新扫描后重试'
PROTECTED = '所选应用与网关或其启动进程重叠，不能强制结束'
FAILED = '未能结束全部客户端进程，请检查权限或在电脑端处理后重试'
TIMEOUT = '等待客户端强制结束超时，请重新扫描并检查电脑端状态'


def _path(value):
    return os.path.normcase(str(Path(value).resolve()))


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError(TIMEOUT)
    return remaining


def _windows_argv(command):
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    result = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not result:
        raise ValueError(CHANGED)
    try:
        return tuple(result[index] for index in range(count.value))
    finally:
        kernel.LocalFree(ctypes.cast(result, ctypes.c_void_p))


class _Native:
    """Only this adapter can obtain and terminate a process handle."""

    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'GetProcessId': ([wintypes.HANDLE], wintypes.DWORD),
            'QueryFullProcessImageNameW': ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                           ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            'GetProcessTimes': ([wintypes.HANDLE, *([ctypes.POINTER(ctypes.c_uint64)] * 4)], wintypes.BOOL),
            'ProcessIdToSessionId': ([wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            'TerminateProcess': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'CreateToolhelp32Snapshot': ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.session = self._session(os.getpid())
        self.protected_pids = self._ancestors()
        handle = self.open(os.getpid(), terminate=False)
        try:
            self.protected_paths = {_path(sys.executable), self.identity(handle)['image']}
        finally:
            self.close(handle)
        # Packaged gateways may run this Python worker below an Electron UI.
        # Protect those ancestor images too, including their sibling processes.
        for pid in self.protected_pids - {os.getpid()}:
            try:
                handle = self.open(pid, terminate=False)
            except OSError:
                continue  # Its PID remains protected even if it already exited.
            try:
                self.protected_paths.add(self.identity(handle)['image'])
            except OSError:
                pass
            finally:
                self.close(handle)

    def _session(self, pid):
        session = wintypes.DWORD()
        if not self.kernel.ProcessIdToSessionId(pid, ctypes.byref(session)):
            raise ctypes.WinError(ctypes.get_last_error())
        return session.value

    def _ancestors(self):
        class Entry(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                        ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                        ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                        ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                        ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
        for name in ('Process32FirstW', 'Process32NextW'):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = [wintypes.HANDLE, ctypes.POINTER(Entry)], wintypes.BOOL
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        parents, entry = {}, Entry()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            if not self.kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
                raise ctypes.WinError(ctypes.get_last_error())
            while True:
                parents[entry.th32ProcessID] = entry.th32ParentProcessID
                if not self.kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                    if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                        raise ctypes.WinError(ctypes.get_last_error())
                    break
        finally:
            self.close(snapshot)
        protected, pid = set(), os.getpid()
        while pid and pid not in protected:
            protected.add(pid)
            pid = parents.get(pid, 0)
        return protected

    def open(self, pid, *, terminate):
        rights = 0x1000 | 0x100000 | (1 if terminate else 0)
        handle = self.kernel.OpenProcess(rights, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def close(self, handle):
        self.kernel.CloseHandle(handle)

    def identity(self, handle):
        pid = self.kernel.GetProcessId(handle)
        if not pid:
            raise ctypes.WinError(ctypes.get_last_error())
        size, buffer = wintypes.DWORD(32768), ctypes.create_unicode_buffer(32768)
        times = [ctypes.c_uint64() for _ in range(4)]
        if (not self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
                or not self.kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times))):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'pid': pid, 'image': _path(buffer.value), 'created': times[0].value,
                'session': self._session(pid)}

    def alive(self, handle):
        result = self.kernel.WaitForSingleObject(handle, 0)
        if result not in (0, 258):  # WAIT_OBJECT_0 / WAIT_TIMEOUT; never wait indefinitely.
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 258

    def terminate(self, handle):
        if not self.kernel.TerminateProcess(handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())


def _pids(value):
    if (not isinstance(value, list) or any(type(pid) is not int or pid <= 0 for pid in value)
            or len(value) != len(set(value))):
        raise ValueError(CHANGED)
    return set(value)


def _snapshot(executable, deadline):
    row = process_inventory([executable], timeout=min(15, _remaining(deadline))).get(executable)
    if not isinstance(row, dict):
        raise ValueError(CHANGED)
    pids, commands = _pids(row.get('pids')), row.get('commands')
    if not isinstance(commands, dict):
        raise ValueError(CHANGED)
    main, arguments = [], {}
    for pid in pids:
        command = commands.get(pid)
        if not isinstance(command, str) or not command.strip():
            raise ValueError(CHANGED)
        argv = _windows_argv(command)
        if not argv or not Path(argv[0]).is_absolute() or _path(argv[0]) != _path(executable):
            raise ValueError(CHANGED)
        kinds = []
        for index, argument in enumerate(argv[1:], 1):
            if argument in ('-e', '-p', '--eval', '--print', '--run', '--expose-internals') or any(
                    argument.startswith(flag + '=') for flag in ('--eval', '--print', '--run', '--expose-internals')):
                raise ValueError(CHANGED)
            if argument == '--type':
                kinds.append(argv[index + 1] if index + 1 < len(argv) else '')
            elif argument.startswith('--type='):
                kinds.append(argument.partition('=')[2])
        if len(kinds) > 1 or kinds and kinds[0] not in CHILD_TYPES:
            raise ValueError(CHANGED)
        if not kinds:
            main.append(pid)
        arguments[pid] = argv
    if len(main) > 1:
        raise ValueError(CHANGED)
    return {'pids': pids, 'main': main[0] if main else None, 'arguments': arguments}


def _verify_identity(native, handle, pid, executable):
    identity = native.identity(handle)
    if identity['pid'] != pid or identity['image'] != _path(executable) or identity['session'] != native.session:
        raise ValueError(CHANGED)
    if pid in native.protected_pids or identity['image'] in native.protected_paths:
        raise ValueError(PROTECTED)
    return identity


def _compatible(snapshot, original):
    if snapshot['pids'] - original['pids']:
        raise ValueError(CHANGED)
    if snapshot['main'] is not None and snapshot['main'] != original['main']:
        raise ValueError(CHANGED)
    for pid in snapshot['pids']:
        if snapshot['arguments'][pid] != original['arguments'][pid]:
            raise ValueError(CHANGED)


def _verify_missing(native, pids, executable, deadline):
    """A CIM omission cannot make a previously observed live PID disappear."""
    for pid in pids:
        _remaining(deadline)
        try:
            handle = native.open(pid, terminate=False)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
                continue
            raise
        try:
            if native.alive(handle):
                try:
                    identity = native.identity(handle)
                except OSError:
                    if native.alive(handle):
                        raise
                    continue
                if identity['image'] == _path(executable) and identity['session'] == native.session:
                    raise ValueError(CHANGED)
        finally:
            native.close(handle)


def _current(executable, original, held, native, deadline):
    snapshot = _snapshot(executable, deadline)
    while True:
        _compatible(snapshot, original)
        # An empty or incomplete CIM result cannot conceal a known live handle.
        if any(native.alive(handle) for pid, (handle, _) in held.items() if pid not in snapshot['pids']):
            raise ValueError(CHANGED)
        retry = False
        for pid in snapshot['pids']:
            _remaining(deadline)
            if pid not in held:
                raise ValueError(CHANGED)
            handle, expected = held[pid]
            # Reopen query-only to detect replacements independently. All
            # termination calls still use the original continuously held handle.
            try:
                current = native.open(pid, terminate=False)
                try:
                    if _verify_identity(native, current, pid, executable) != expected:
                        raise ValueError(CHANGED)
                finally:
                    native.close(current)
                if native.identity(handle) != expected:
                    raise ValueError(CHANGED)
            except OSError:
                if native.alive(handle):
                    raise
                # Natural exit between the CIM read and OpenProcess is harmless
                # only if both the original handle and a fresh inventory agree.
                snapshot = _snapshot(executable, deadline)
                if pid in snapshot['pids']:
                    raise ValueError(CHANGED) from None
                retry = True
                break
        if not retry:
            return snapshot


def force_stop_client(descriptor, *, state):
    """Caller must separately require explicit per-action force confirmation.

    Only the selected GUI image in this user session is eligible; agent runtimes
    with other executable paths are never added, even when they are children.
    """
    if sys.platform != 'win32':
        raise ValueError(UNSUPPORTED)
    deadline, held = time.monotonic() + BUDGET, {}
    try:
        executable = Path(descriptor.get('executable', ''))
        if (descriptor.get('id') not in ('codex', 'claude', 'deepseek') or descriptor.get('installed') is not True
                or not executable.is_absolute() or executable.suffix.lower() != '.exe' or not executable.is_file()):
            raise ValueError(INVALID)
        executable = executable.resolve()
        if not isinstance(state, dict) or state.get('unknown') is not False:
            raise ValueError(CHANGED)
        expected_pids, expected_main = _pids(state.get('pids')), _pids(state.get('mainPids'))
        if len(expected_main) > 1 or not expected_main <= expected_pids:
            raise ValueError(CHANGED)
        native = _Native()
        if _path(executable) in native.protected_paths:
            raise ValueError(PROTECTED)
        original = _snapshot(executable, deadline)
        if original['pids'] - expected_pids or original['main'] is not None and {original['main']} != expected_main:
            raise ValueError(CHANGED)
        _verify_missing(native, expected_pids - original['pids'], executable, deadline)
        if not original['pids']:
            return
        # Open EVERY intended target before any destructive action. Holding these
        # kernel objects prevents a later PID lookup from retargeting termination.
        for pid in sorted(original['pids']):
            _remaining(deadline)
            if pid in native.protected_pids:
                raise ValueError(PROTECTED)
            try:
                handle = native.open(pid, terminate=True)
            except OSError as exc:
                if getattr(exc, 'winerror', None) != 87:
                    raise
                fresh = _snapshot(executable, deadline)
                _compatible(fresh, original)
                if pid not in fresh['pids']:
                    continue
                raise
            held[pid] = (handle, None)
            try:
                held[pid] = (handle, _verify_identity(native, handle, pid, executable))
            except OSError:
                if native.alive(handle):
                    raise
                fresh = _snapshot(executable, deadline)
                _compatible(fresh, original)
                if pid in fresh['pids']:
                    raise
        current = _current(executable, original, held, native, deadline)
        order = ([original['main']] if original['main'] is not None else []) + sorted(
            original['pids'] - {original['main']})
        for index, pid in enumerate(order):
            _remaining(deadline)
            if index:
                current = _current(executable, original, held, native, deadline)
            if not current['pids']:
                return
            if pid in current['pids']:
                handle, _ = held[pid]
                if native.alive(handle):
                    try:
                        native.terminate(handle)
                    except OSError:
                        if native.alive(handle):
                            raise
        while True:
            current = _current(executable, original, held, native, deadline)
            if not current['pids']:
                return
            time.sleep(min(.1, _remaining(deadline)))
    except (OSError, subprocess.SubprocessError):
        raise ValueError(FAILED) from None
    finally:
        for handle, _ in held.values():
            native.close(handle)
