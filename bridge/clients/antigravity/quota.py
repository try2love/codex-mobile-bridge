"""Project native quota pools into the shared account-window contract.

Adapted from windowmmk's PR #257 (6c69a3dcf4a2e4b9109b594ebae49227655b56dd).
Only explicit native model membership is used; family names do not prove a pool.
"""
import copy
import math
import re

from .model import _parse_iso_seconds


def quota_groups(value):
    groups = []
    raw_groups = value.get('groups') if isinstance(value, dict) else None
    for raw in raw_groups if isinstance(raw_groups, list) else []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get('displayName') or '').strip()
        description = str(raw.get('description') or '')
        match = re.search(r'models within this group:\s*(.*)', description, re.I)
        members = [part.strip() for part in match.group(1).split(',') if part.strip()] if match else []
        windows = []
        for bucket in raw.get('buckets', []) if isinstance(raw.get('buckets'), list) else []:
            if not isinstance(bucket, dict) or bucket.get('disabled'):
                continue
            key = str(bucket.get('window') or '').lower()
            minutes = {'5h': 300, 'weekly': 10080, '7d': 10080, 'daily': 1440, '1d': 1440}.get(key)
            # A missing/invalid measurement is unknown, never a fabricated zero.
            fraction = bucket.get('remainingFraction')
            valid = (type(fraction) in (int, float) and math.isfinite(fraction) and 0 <= fraction <= 1)
            windows.append({'id': str(bucket.get('bucketId') or key), 'kind': key or 'quota',
                            'label': str(bucket.get('displayName') or ''), 'windowDurationMins': minutes,
                            'remainingPercent': round(fraction * 100, 1) if valid else None,
                            'resetsAt': _parse_iso_seconds(str(bucket.get('resetTime') or ''))})
        if windows:
            windows.sort(key=lambda window: window['windowDurationMins'] or float('inf'))
            groups.append({'name': name or 'Model Quota', 'models': members, 'windows': windows})
    return groups


def model_windows(groups, model_id, model_name=''):
    names = {str(value).strip().casefold() for value in (model_id, model_name) if value}
    matches = [group for group in groups
               if names.intersection(str(value).strip().casefold() for value in
                                     [group['name'], *group['models']])]
    return copy.deepcopy(matches[0]['windows']) if len(matches) == 1 else []


def overlay(catalog, snapshot):
    value = copy.deepcopy(catalog)
    groups = snapshot.get('groups', [])
    summary = dict(value.get('usageSummary') or {})
    if not groups and not summary:
        return value
    summary.update({key: snapshot[key] for key in ('checkedAt', 'status') if key in snapshot})
    if groups:
        summary['groups'] = copy.deepcopy(groups)
    elif snapshot.get('status') == 'unavailable':
        summary.pop('groups', None)
        summary.pop('checkedAt', None)
    # An unavailable new RPC preserves the legacy single-window display.
    for row in value.get('models', []):
        if not isinstance(row, dict):
            continue
        windows = model_windows(groups, row.get('id'), row.get('name'))
        if windows:
            row['quotaWindows'] = windows
        else:
            row.pop('quotaWindows', None)
    if summary:
        value['usageSummary'] = summary
    return value
