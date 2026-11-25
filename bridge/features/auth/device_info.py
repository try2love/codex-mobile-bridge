"""Readable client-reported device details; never an authentication signal."""
import re
from urllib.parse import unquote


def describe(user_agent):
    agent = user_agent[:512]
    mobile = re.search(r'(?:^|\s)BridgeMobile/([\w.-]+)-(iOS|Android|HarmonyOS)(?:\s|$)', agent)
    if mobile:
        result = {'kind': 'app', 'platform': mobile[2]}
        for token, field in (('BridgeModel', 'model'), ('BridgeOS', 'osVersion'), ('BridgeBuild', 'build')):
            value = re.search(r'(?:^|\s)' + token + r'/([^\s]{1,160})(?:\s|$)', agent)
            if value:
                result[field] = ''.join(c for c in unquote(value[1]) if c.isprintable())[:120]
        # 0.1 in legacy clients was a protocol marker, not their installed version.
        if result.get('build'):
            result['appVersion'] = mobile[1]
        return result
    result = {'kind': 'browser'}
    for name, pattern in (('Edge', r'Edg(?:A|iOS)?/([\d.]+)'), ('Opera', r'OPR/([\d.]+)'),
                          ('Firefox', r'(?:Firefox|FxiOS)/([\d.]+)'), ('Chrome', r'(?:Chrome|CriOS)/([\d.]+)'),
                          ('Safari', r'Version/([\d.]+).*Safari/')):
        found = re.search(pattern, agent)
        if found:
            result.update(browser=name, browserVersion=found[1])
            break
    if 'HarmonyOS' in agent or 'OpenHarmony' in agent:
        result['platform'] = 'HarmonyOS'
    elif 'Android' in agent:
        result['platform'] = 'Android'
    elif 'iPhone' in agent or 'iPad' in agent:
        result['platform'] = 'iPadOS' if 'iPad' in agent else 'iOS'
    elif 'Windows' in agent:
        result['platform'] = 'Windows'
    elif 'Macintosh' in agent or 'Mac OS X' in agent:
        result['platform'] = 'macOS'
    elif 'Linux' in agent:
        result['platform'] = 'Linux'
    return result


def refresh(previous, user_agent):
    incoming = describe(user_agent)
    if previous and previous.get('kind') == 'app':
        if incoming['kind'] != 'app':
            return previous
        if incoming.get('platform') == previous.get('platform'):
            return {**previous, **incoming}
    return incoming
