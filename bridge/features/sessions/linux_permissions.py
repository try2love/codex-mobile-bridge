"""Read-only permission discovery shared by native and bridge-owned chats.

Runtime support is not evidence of a native desktop feature rollout. Never
probe availability by changing a thread or write the desktop's preferences.
"""
import json
import sys
from pathlib import Path


def enabled(host='local'):
    # This compatibility path is local Linux only, never inferred for SSH hosts.
    return sys.platform == 'linux' and host == 'local'


UNKNOWN = '无法确认当前权限能力，请在桌面检查设置后重新打开此面板。'
RESTRICTED = '当前工作区或管理员策略不允许此权限模式。'
FULL_HIDDEN = '请先在 Codex 桌面设置中开启“完全访问权限”选项，再重新打开此面板。'
AUTO_UNKNOWN = '尚未确认桌面已开放“帮我批准”；请先在桌面选择此模式，再重新打开此面板。'
AUTO_DISABLED = '当前 Codex 运行时或配置未启用“帮我批准”。'


def read_permission_facts(request, cwd):
    """Return only allowlisted capability facts, never credentials/config dumps."""
    facts = {}
    try:
        config = request('config/read', {'cwd': cwd, 'includeLayers': False})['config']
        features = config.get('features') or {}
        facts['config'] = {'guardian': features.get('guardian_approval'),
                           'reviewer': config.get('approvals_reviewer')}
    except Exception:
        pass
    try:
        requirements = request('configRequirements/read', {})['requirements']
        if requirements is None:
            requirements = {}
        if not isinstance(requirements, dict):
            raise ValueError('Invalid requirements')
        keys = ('allowedApprovalPolicies', 'allowedApprovalsReviewers', 'allowedSandboxModes')
        for key in keys:
            value = requirements.get(key)
            if value is not None and not isinstance(value, list):
                raise ValueError('Invalid requirement list')
        for key in ('allowedPermissionProfiles', 'featureRequirements'):
            value = requirements.get(key)
            if value is not None and not isinstance(value, dict):
                raise ValueError('Invalid requirement map')
        facts['requirements'] = {key: requirements.get(key) for key in
                                 (*keys, 'allowedPermissionProfiles', 'featureRequirements')}
    except Exception:
        pass
    for method, key, params in [('permissionProfile/list', 'profiles', {'cwd': cwd}),
                                ('experimentalFeature/list', 'features', {})]:
        try:
            rows, cursor = [], None
            for _ in range(10):
                result = request(method, {**params, 'limit': 200, 'cursor': cursor})
                page = result['data']
                if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
                    raise ValueError('Invalid capability page')
                rows.extend(page)
                cursor = result.get('nextCursor')
                if not cursor:
                    break
            else:
                raise ValueError('Incomplete capability list')
            if key == 'profiles':
                if any(not isinstance(row.get('id'), str) or not isinstance(row.get('allowed'), bool) for row in rows):
                    raise ValueError('Invalid permission profile')
                facts[key] = {row['id']: row['allowed'] for row in rows}
            else:
                facts['guardianEnabled'] = any(row.get('name') == 'guardian_approval' and
                                               row.get('enabled') is True for row in rows)
        except Exception:
            pass
    return facts


def desktop_permission_preferences(home, host='local'):
    try:
        state = json.loads((Path(home) / '.codex-global-state.json').read_text(encoding='utf-8'))
        atoms = state.get('electron-persisted-atom-state', {})
        visibility = atoms.get('composer-permission-mode-visibility', {})
        full = visibility if isinstance(visibility, bool) else visibility.get('full-access', True)
        selection = atoms.get('permission-selection-by-host-id:' + host) or {}
        return {'fullAccess': full if isinstance(full, bool) else None,
                'autoReview': selection.get('kind') == 'agent-mode' and
                              selection.get('agentMode') == 'guardian-approvals'}
    except (OSError, ValueError, TypeError, AttributeError):
        return {'fullAccess': None, 'autoReview': False}


def permission_settings(preset, facts):
    reviewers = (facts.get('requirements') or {}).get('allowedApprovalsReviewers')
    reviewer = 'auto_review'
    if reviewers is not None and reviewer not in reviewers and 'guardian_subagent' in reviewers:
        reviewer = 'guardian_subagent'
    profile = ':danger-full-access' if preset == 'full-access' else ':workspace'
    return {'permissions': profile, 'approvalPolicy': 'never' if preset == 'full-access' else 'on-request',
            'approvalsReviewer': reviewer if preset == 'auto-review' else 'user'}


def permission_options(facts, preferences, current, *, native=True):
    options = []
    for preset in ('ask', 'auto-review', 'full-access'):
        settings = permission_settings(preset, facts)
        reason = None
        requirements = facts.get('requirements')
        if requirements is None or 'profiles' not in facts or 'config' not in facts:
            reason = UNKNOWN
        else:
            mode = 'danger-full-access' if preset == 'full-access' else 'workspace-write'
            for key, value in [('allowedApprovalPolicies', settings['approvalPolicy']),
                               ('allowedApprovalsReviewers', settings['approvalsReviewer']),
                               ('allowedSandboxModes', mode)]:
                allowed = requirements.get(key)
                if allowed is not None and value not in allowed:
                    reason = RESTRICTED
            allowed_profiles = requirements.get('allowedPermissionProfiles')
            if allowed_profiles is not None and allowed_profiles.get(settings['permissions']) is not True:
                reason = RESTRICTED
            if facts['profiles'].get(settings['permissions']) is False:
                reason = RESTRICTED
            elif facts['profiles'].get(settings['permissions']) is not True:
                reason = reason or '当前运行时未提供此权限配置，请在桌面检查或更新 Codex。'
        if not reason and preset == 'full-access':
            if preferences.get('fullAccess') is False:
                reason = FULL_HIDDEN
            elif preferences.get('fullAccess') is not True:
                reason = UNKNOWN
        if not reason and preset == 'auto-review':
            configured = facts['config'].get('guardian')
            required = (requirements.get('featureRequirements') or {}).get('guardian_approval')
            if configured is False or required is False or facts.get('guardianEnabled') is False:
                reason = AUTO_DISABLED
            elif native:
                evidence = (current == 'auto-review' or preferences.get('autoReview') is True or
                            facts['config'].get('reviewer') in ('auto_review', 'guardian_subagent'))
                if not evidence:
                    reason = AUTO_UNKNOWN
            elif facts.get('guardianEnabled') is not True:
                reason = UNKNOWN
        options.append({'preset': preset, 'available': reason is None, 'reason': reason})
    return {'current': current, 'options': options}


def require_permission(options, preset):
    option = next(row for row in options['options'] if row['preset'] == preset)
    if not option['available']:
        raise ValueError(option['reason'])


def current_mode(state):
    from bridge.features.sessions.model import permission_mode
    return permission_mode(state or {}, legacy_reviewer=True)
