"""Read Windows installation metadata without launching apps or reading profiles."""
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath


PRODUCT_NAMES = {'codex': ('Codex', 'ChatGPT'), 'claude': ('Claude', 'Claude Desktop'),
                 'deepseek': ('DSH Desktop', 'DeepSeek Harness', 'DeepSeekHarness', 'deepseek-harness')}
PACKAGE_NAMES = {'openai.codex': 'codex', 'claude': 'claude'}

# Appx registration is readable even when enumerating WindowsApps is forbidden.
# Each source can fail independently; a portable running app needs no registration.
# Do not use CIM command lines here: installation discovery needs only GUI paths.
INVENTORY_SCRIPT = r'''
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ErrorActionPreference = 'SilentlyContinue'
$result = @{packages=@(); uninstall=@(); appPaths=@(); processes=@()}
try {
    $result.packages = @(Get-AppxPackage -ErrorAction Stop |
        Where-Object {$_.Name -in @('OpenAI.Codex', 'Claude')} |
        Select-Object Name,InstallLocation,PackageFamilyName)
} catch {}
$product = '^(Codex|ChatGPT|Claude( Desktop)?|DSH Desktop|DeepSeek Harness|DeepSeekHarness|deepseek-harness)(\s+\d[\w. -]*)?$'
$binary = '^(Codex|ChatGPT|Claude( Desktop)?|DSH Desktop|DeepSeek Harness|DeepSeekHarness|deepseek-harness)\.exe$'
foreach ($base in @('HKCU:\Software', 'HKLM:\Software', 'HKCU:\Software\WOW6432Node', 'HKLM:\Software\WOW6432Node')) {
    try {
        $result.uninstall += @(Get-ItemProperty -Path ($base + '\Microsoft\Windows\CurrentVersion\Uninstall\*') |
            Where-Object {$_.DisplayName -match $product} |
            Select-Object DisplayName,InstallLocation,DisplayIcon)
    } catch {}
    try {
        $result.appPaths += @(Get-ChildItem -Path ($base + '\Microsoft\Windows\CurrentVersion\App Paths') |
            Where-Object {$_.PSChildName -match $binary} | ForEach-Object {
                @{Name=$_.PSChildName; Path=$_.GetValue('')}
            })
    } catch {}
}
try {
    $session = (Get-Process -Id $PID).SessionId
    $result.processes = @(Get-Process | Where-Object {
        $_.SessionId -eq $session -and $_.ProcessName -match $product -and $_.Path
    } | Select-Object ProcessName,Path)
} catch {}
$result | ConvertTo-Json -Compress -Depth 4
'''


def windows_installations():
    """Take one bounded, uncached snapshot for all supported desktop products."""
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        response = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', INVENTORY_SCRIPT],
                                  capture_output=True, text=True, encoding='utf-8', timeout=12, **options)
        value = json.loads(response.stdout or '{}')
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _rows(inventory, key):
    value = inventory.get(key, [])
    if isinstance(value, dict):
        value = [value]
    return (row for row in value if isinstance(row, dict)) if isinstance(value, list) else ()


def _product(name, provider):
    if not isinstance(name, str):
        return False
    return any(re.fullmatch(re.escape(alias) + r'(?:\s+\d[\w. -]*)?', name.strip(), re.IGNORECASE)
               for alias in PRODUCT_NAMES[provider])


def _path(value, *, icon=False):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if icon:
        value = re.sub(r',\s*-?\d+\s*$', '', value)
    value = os.path.expandvars(value.strip().strip('"'))
    path = Path(value)
    return path if path.is_absolute() else None


def _electron(path):
    # codex.exe/claude.exe also name CLIs. Only accept GUI installation evidence.
    return ((path.parent/'resources/app.asar').is_file() or
            (path.parent/'resources/app/package.json').is_file())


def _package_executables(folder, binaries):
    """Prefer a package's visible GUI entry over adjacent launch shims/helpers."""
    try:
        with (folder/'AppxManifest.xml').open('rb') as stream:
            content = stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            return
        manifest = ET.fromstring(content)
        names = {name.casefold() for name in binaries}
        for app in manifest.findall('{*}Applications/{*}Application'):
            visual = app.find('{*}VisualElements')
            if visual is None or visual.get('AppListEntry', '').casefold() == 'none':
                continue
            relative = PureWindowsPath(app.get('Executable', ''))
            if (relative.drive or relative.root or '..' in relative.parts or
                    relative.name.casefold() not in names):
                continue
            candidate = folder.joinpath(*relative.parts)
            if _electron(candidate):
                yield candidate
    except (OSError, ValueError, ET.ParseError):
        return


def registered_candidates(provider, inventory):
    """Yield GUI candidates with their safe package metadata, in source order."""
    binaries = tuple(name + '.exe' for name in PRODUCT_NAMES[provider])
    for row in _rows(inventory, 'packages'):
        if PACKAGE_NAMES.get(str(row.get('Name', '')).casefold()) != provider:
            continue
        folder = _path(row.get('InstallLocation'))
        if folder:
            metadata = {'packageFamilyName': row['PackageFamilyName']} if isinstance(row.get('PackageFamilyName'), str) else {}
            for path in _package_executables(folder, binaries):
                yield {'executable': str(path), **metadata}
            # Current Codex Store packages also contain a small Codex.exe shim;
            # ChatGPT.exe is the Electron GUI identified by the package manifest.
            package_binaries = ('ChatGPT.exe', 'Codex.exe') if provider == 'codex' else binaries
            for root in (folder/'app', folder):
                for binary in package_binaries:
                    yield {'executable': str(root/binary), **metadata}
    for row in _rows(inventory, 'uninstall'):
        if not _product(row.get('DisplayName'), provider):
            continue
        location, icon = _path(row.get('InstallLocation')), _path(row.get('DisplayIcon'), icon=True)
        folders = ([location] if location else []) + ([icon.parent] if icon else [])
        for folder in dict.fromkeys(folders):
            for binary in binaries:
                path = folder/binary
                if _electron(path):
                    yield {'executable': str(path)}
    for key, name in (('appPaths', 'Name'), ('processes', 'ProcessName')):
        for row in _rows(inventory, key):
            product = row.get(name, '')
            if key == 'appPaths' and isinstance(product, str):
                product = re.sub(r'\.exe$', '', product, flags=re.IGNORECASE)
            if not _product(product, provider):
                continue
            path = _path(row.get('Path'))
            if path and path.name.casefold() in {binary.casefold() for binary in binaries} and _electron(path):
                yield {'executable': str(path)}
