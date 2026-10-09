"""Observe the installed Desktop's public channel and hand off to its native updater.

Never reuse Bridge update packages, disable vendor policies, or infer Store eligibility
from a public manifest. Installation prompts remain owned by the original application.
"""
import copy
import json
import os
import platform
import re
import struct
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, build_opener

from bridge.clients.desktop_app import DesktopApp
from bridge.features.notifications.channels import NoRedirect, read_json, write_json
from bridge.features.auth.tls import client_context
from urllib.request import HTTPSHandler


SPARKLE = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'
CHANNELS = {'prod':'codex-app-prod', 'public-beta':'codex-app-beta'}
STORE_PRODUCTS = {'prod':'9PLM9XGG6VKS', 'public-beta':'9N8CJ4W95TBZ'}

# Use the target PID, not its display name. The first menu is often the Apple
# menu; inspect the target application's top-level menus for its update action.
MAC_UPDATE_SCRIPT = '''on run argv
with timeout of 180 seconds
tell application "System Events"
  set targetPid to item 1 of argv as integer
  if not (exists (first application process whose unix id is targetPid)) then error "BRIDGE_UPDATE_PROCESS_MISSING"
  tell (first application process whose unix id is targetPid)
    set frontmost to true
    repeat 30 times
      if exists menu bar 1 then
      repeat with heading in menu bar items of menu bar 1
        set choices to {}
        try
          set choices to every menu item of menu 1 of heading whose name contains "Check for Updates"
          if (count of choices) is 0 then set choices to every menu item of menu 1 of heading whose name contains "检查更新"
          if (count of choices) is 0 then set choices to every menu item of menu 1 of heading whose name contains "檢查更新"
        on error errorText number errorNumber
          if errorNumber is not -1719 and errorNumber is not -1728 then error errorText number errorNumber
        end try
        if (count of choices) > 0 then
          if not (enabled of item 1 of choices) then error "BRIDGE_UPDATE_MENU_DISABLED"
          click item 1 of choices
          return "opened"
        end if
      end repeat
      end if
      delay 0.2
    end repeat
    error "BRIDGE_UPDATE_MENU_MISSING"
  end tell
end tell
end timeout
end run'''

UPDATE_FAILURE_MESSAGES = {
    'automationPermission':'请在系统设置的“隐私与安全性 → 自动化”中允许网关控制 System Events，然后重试。',
    'accessibilityPermission':'请在系统设置的“隐私与安全性 → 辅助功能”中允许网关，然后重试；若已开启，请退出并重新打开网关。',
    'timeout':'等待系统授权或更新菜单超时。请完成电脑端授权、解锁电脑后重试。',
    'menuUnavailable':'未找到 Codex 的“检查更新”菜单。请在 Codex 桌面应用中手动检查更新。',
    'menuDisabled':'Codex 的“检查更新”菜单暂不可用。请等待桌面应用就绪后重试。',
    'desktopUnavailable':'未找到唯一的 Codex 桌面进程。请打开所选桌面应用后重试。',
    'handoffFailed':'未能操作 Codex 更新菜单。请解锁电脑后重试，或在 Codex 桌面应用中手动检查更新。',
}


def update_failure(error):
    # AppleScript stderr may contain local paths: classify it, never expose it.
    if isinstance(error, subprocess.TimeoutExpired):
        return 'timeout'
    raw = str(getattr(error, 'stderr', '') or '')
    if '(-1743)' in raw:
        return 'automationPermission'
    if '(-25211)' in raw or 'not allowed assistive access' in raw.lower() or '不允许辅助访问' in raw or '不允许进行辅助访问' in raw:
        return 'accessibilityPermission'
    if '(-1712)' in raw:
        return 'timeout'
    for marker, reason in [('MENU_MISSING','menuUnavailable'), ('MENU_DISABLED','menuDisabled'), ('PROCESS_MISSING','desktopUnavailable')]:
        if 'BRIDGE_UPDATE_'+marker in raw:
            return reason
    return 'desktopUnavailable' if str(error) == 'desktopUnavailable' else 'handoffFailed'


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d+)*', value):
        raise ValueError('更新版本格式无法识别')
    parts = [int(n) for n in value.split('.')]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def package_metadata(archive):
    # Read one public package file, without unpacking application code or credentials.
    with Path(archive).open('rb') as stream:
        header = stream.read(16)
        if len(header) != 16:
            raise ValueError('桌面程序元数据不完整')
        _, header_size, _, json_size = struct.unpack('<4I', header)
        if not 0 < json_size <= 16*1024*1024 or header_size < json_size:
            raise ValueError('桌面程序元数据不完整')
        index = json.loads(stream.read(json_size))['files']['package.json']
        if index.get('unpacked') or not 0 < index['size'] < 1024*1024:
            raise ValueError('桌面程序元数据不完整')
        stream.seek(8+header_size+int(index['offset']))
        raw = json.loads(stream.read(index['size']))
        return {k:raw.get(k) for k in ('version', 'codexBuildFlavor', 'codexBuildNumber', 'codexSparkleFeedUrl')}


def installed(executable):
    app = Path(executable)
    if sys.platform == 'darwin':
        import plistlib
        bundle = next((p for p in app.parents if p.suffix == '.app'), None)
        if bundle is None:
            raise ValueError('未识别 Codex 桌面安装方式')
        info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') != 'com.openai.codex':
            raise ValueError('所选程序不是原版 Codex 桌面应用')
        metadata = package_metadata(bundle/'Contents/Resources/app.asar')
        return {**metadata, 'version':info['CFBundleShortVersionString'], 'build':info['CFBundleVersion'], 'bundle':str(bundle)}
    archive = app.parent/'resources/app.asar'
    metadata = package_metadata(archive)
    result = {**metadata, 'build':str(metadata['codexBuildNumber']), 'bundle':None}
    if sys.platform == 'win32' and metadata['codexBuildFlavor'] in STORE_PRODUCTS:
        manifest = next((p/'AppxManifest.xml' for p in list(app.parents)[:4] if (p/'AppxManifest.xml').is_file()), None)
        if manifest:
            identity = next((n for n in ET.parse(manifest).getroot() if n.tag.rsplit('}', 1)[-1] == 'Identity'), None)
            if identity is not None:
                result.update(build=identity.attrib['Version'], packageIdentity=identity.attrib['Name'],
                              storeProductId=STORE_PRODUCTS[metadata['codexBuildFlavor']])
    return result


def appcast(raw, current, system_version):
    root = ET.fromstring(raw)
    candidates = []
    for item in root.findall('./channel/item'):
        enclosure = item.find('enclosure')
        if enclosure is None or enclosure.get(SPARKLE+'os', 'macos') != 'macos':
            continue
        build = item.findtext(SPARKLE+'version') or enclosure.get(SPARKLE+'version')
        if not build or item.findtext(SPARKLE+'channel') or enclosure.get(SPARKLE+'deltaFrom'):
            continue
        minimum = item.findtext(SPARKLE+'minimumSystemVersion')
        maximum = item.findtext(SPARKLE+'maximumSystemVersion')
        minimum_update = item.findtext(SPARKLE+'minimumUpdateVersion')
        if minimum and version(system_version) < version(minimum) or maximum and version(system_version) > version(maximum):
            continue
        if minimum_update and version(current) < version(minimum_update):
            continue
        if version(build) > version(current):
            candidates.append({'targetBuild':build, 'targetVersion':item.findtext(SPARKLE+'shortVersionString') or enclosure.get(SPARKLE+'shortVersionString') or build})
    return max(candidates, key=lambda row:version(row['targetBuild'])) if candidates else None


class DesktopUpdates:
    def __init__(self, manager):
        self.manager = manager
        self.lock = threading.RLock()
        self.value = {'state':'idle', 'canRequest':False}
        self.attempts_path = manager.root/'desktop-update-requests.json'
        self.requests = read_json(self.attempts_path, {})
        self.checked_at = 0
        self.worker = None

    def status(self):
        with self.lock:
            return copy.deepcopy(self.value)

    def executable(self):
        return self.manager.index.get('desktopExecutable') or DesktopApp.discover(self.manager.runtime)

    def check(self, background=False):
        with self.lock:
            if self.value['state'] in ('checking', 'requesting') or background and time.time()-self.checked_at < 21600:
                return
            self.checked_at = time.time()
            self.value = {**self.value, 'state':'checking', 'canRequest':False, 'error':None}
            self.worker = threading.Thread(target=self._check, daemon=True)
            self.worker.start()

    def _check(self):
        result = {'state':'unsupported', 'canRequest':False, 'checkedAt':time.time()}
        meta = {}
        try:
            meta = installed(self.executable())
            channel = CHANNELS.get(meta['codexBuildFlavor'])
            result.update(currentVersion=meta['version'], currentBuild=meta['build'], channel=meta['codexBuildFlavor'])
            if sys.platform == 'win32' and meta.get('storeProductId') and channel:
                url = 'https://persistent.oaistatic.com/'+channel+'/windows-store-update.json'
                manifest = json.loads(self.fetch(url))
                if manifest.get('storeProductId') != meta['storeProductId'] or manifest.get('packageIdentity') != meta['packageIdentity']:
                    raise ValueError('更新清单与已安装程序不匹配')
                build = manifest['buildVersion']
                newer = version(build) > version(meta['build'])
                result.update(state='available' if newer else 'checked', canRequest=True,
                              message='Microsoft Store 将确认此设备的更新资格，安装可能需要电脑端操作')
                if newer:
                    result.update(targetBuild=build, targetVersion=build)
            elif sys.platform != 'darwin':
                result['message'] = '此安装方式需要在电脑端或系统商店检查更新'
            elif not channel:
                result['message'] = '此发布渠道需要由 Codex 桌面应用检查更新'
            else:
                url = 'https://persistent.oaistatic.com/'+channel+'/appcast.xml'
                if meta['codexSparkleFeedUrl'] != url:
                    raise ValueError('桌面更新渠道已变化，请使用原生更新器')
                raw = self.fetch(url)
                candidate = appcast(raw, meta['build'], platform.mac_ver()[0])
                result.update(state='available' if candidate else 'checked', canRequest=True,
                              message='安装资格、灰度发布和更新策略由 Codex 原生更新器确认')
                if candidate:
                    result.update(candidate)
        except Exception:
            result.update(state='error', message='无法读取官方更新渠道，请稍后重试或在电脑端检查',
                          canRequest=bool(meta.get('bundle') or meta.get('storeProductId')))
        with self.lock:
            self.value = result

    @staticmethod
    def fetch(url):
        opener = build_opener(NoRedirect(), HTTPSHandler(context=client_context()))
        with opener.open(Request(url, headers={'User-Agent':'Codex-Mobile-Bridge'}), timeout=20) as response:
            raw = response.read(2*1024*1024+1)
        if len(raw) > 2*1024*1024:
            raise ValueError('更新清单过大')
        return raw

    def request(self, value):
        if set(value) != {'requestId', 'confirmed', 'tasksConfirmed', 'currentBuild'} or value['confirmed'] is not True or value['tasksConfirmed'] is not True:
            raise ValueError('请确认所有桌面任务已结束，并授权打开原生更新器')
        request_id = value['requestId']
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError('更新请求标识无效')
        with self.manager.gate, self.manager.lock, self.lock:
            if request_id in self.requests:
                if self.requests[request_id] != value['currentBuild']:
                    raise ValueError('同一请求不能用于不同版本')
                return
            self.manager.check_ready()
            self.manager.idle()
            meta = installed(self.executable())
            if (sys.platform not in ('darwin', 'win32') or not self.value.get('canRequest')
                    or sys.platform == 'win32' and not meta.get('storeProductId') or meta['build'] != value['currentBuild']):
                raise ValueError('当前版本或更新能力已变化，请重新检查')
            self.requests[request_id] = meta['build']
            write_json(self.attempts_path, self.requests)
            self.value.update(state='requesting', canRequest=False)
            self.value.pop('failureReason', None)
            self.worker = threading.Thread(target=self._request, args=(meta,), daemon=True)
            self.worker.start()

    def _request(self, meta):
        # The vendor owns the install prompt and policy; never click an arbitrary dialog.
        failure = None
        try:
            if sys.platform == 'win32':
                if meta.get('storeProductId') not in STORE_PRODUCTS.values():
                    raise ValueError('商店产品标识无效')
                os.startfile('ms-windows-store://pdp/?PRODUCTID='+meta['storeProductId'])
                message = '已请求打开 Microsoft Store 的应用页面，请在电脑端检查和确认更新'
            else:
                processes = DesktopApp(self.executable(), self.manager.home).processes()
                if len(processes) != 1:
                    raise ValueError('desktopUnavailable')
                result = subprocess.run(['osascript', '-e', MAC_UPDATE_SCRIPT, str(processes[0])], check=True, timeout=180,
                                        capture_output=True, text=True)
                if result.stdout.strip() != 'opened':
                    raise ValueError('handoffFailed')
                message = '已打开 Codex 原生更新器，请在电脑端确认安装；此操作不代表更新完成'
        except Exception as error:
            failure = update_failure(error)
            message = ('未能打开系统商店，请在电脑端检查更新' if sys.platform == 'win32' else UPDATE_FAILURE_MESSAGES[failure])
        with self.lock:
            self.value.update(state='needsDesktop', canRequest=bool(failure), message=message, failureReason=failure)
