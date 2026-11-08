"""Native per-instance directories and launch parameters, without launching Apps.

The registry owns directory identity, overlap checks and persistence. Platforms
own process creation and normal exit. These paths describe the original Apps'
configuration contracts, not copies of their accounts or installations.
"""
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Mapping


# Native credential/routing variables: DSH installed provider catalog and Claude
# Desktop credential cleanup/provider flags. Only inherited App-launch values
# are removed; PATH, proxies, TLS settings and global environment remain intact.
# DSH on POSIX can independently re-read login-shell credentials after launch.
_ACCOUNT_ENVIRONMENT = {
    'deepseek': frozenset((
        'AI_GATEWAY_API_KEY',
        'ANTHROPIC_API_KEY',
        'ANTHROPIC_AUTH_TOKEN',
        'ANTHROPIC_FEDERATION_RULE_ID',
        'ANTHROPIC_IDENTITY_TOKEN_FILE',
        'ANTHROPIC_OAUTH_TOKEN',
        'ANTHROPIC_ORGANIZATION_ID',
        'ANTHROPIC_SERVICE_ACCOUNT_ID',
        'ANTHROPIC_WORKSPACE_ID',
        'ANT_LING_API_KEY',
        'AWS_ACCESS_KEY_ID',
        'AWS_BEARER_TOKEN_BEDROCK',
        'AWS_CONTAINER_CREDENTIALS_FULL_URI',
        'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI',
        'AWS_DEFAULT_REGION',
        'AWS_PROFILE',
        'AWS_REGION',
        'AWS_SECRET_ACCESS_KEY',
        'AWS_WEB_IDENTITY_TOKEN_FILE',
        'AZURE_OPENAI_API_KEY',
        'AZURE_OPENAI_BASE_URL',
        'AZURE_OPENAI_RESOURCE_NAME',
        'BASETEN_API_KEY',
        'CEREBRAS_API_KEY',
        'CLOUDFLARE_ACCOUNT_ID',
        'CLOUDFLARE_API_KEY',
        'CLOUDFLARE_GATEWAY_ID',
        'COPILOT_GITHUB_TOKEN',
        'DEEPSEEK_API_KEY',
        'DEEPSEEK_BASE_URL',
        'FIREWORKS_API_KEY',
        'GCLOUD_PROJECT',
        'GEMINI_API_KEY',
        'GOOGLE_APPLICATION_CREDENTIALS',
        'GOOGLE_CLOUD_API_KEY',
        'GOOGLE_CLOUD_LOCATION',
        'GOOGLE_CLOUD_PROJECT',
        'GROQ_API_KEY',
        'HF_TOKEN',
        'KIMI_API_KEY',
        'META_API_KEY',
        'MINIMAX_API_KEY',
        'MINIMAX_CN_API_KEY',
        'MISTRAL_API_KEY',
        'MOONSHOT_API_KEY',
        'NVIDIA_API_KEY',
        'OPENAI_API_KEY',
        'OPENCODE_API_KEY',
        'OPENROUTER_API_KEY',
        'QWEN_TOKEN_PLAN_API_KEY',
        'QWEN_TOKEN_PLAN_CN_API_KEY',
        'RADIUS_API_KEY',
        'TOGETHER_API_KEY',
        'TYPESAFE_API_KEY',
        'XAI_API_KEY',
        'XIAOMI_API_KEY',
        'XIAOMI_TOKEN_PLAN_AMS_API_KEY',
        'XIAOMI_TOKEN_PLAN_CN_API_KEY',
        'XIAOMI_TOKEN_PLAN_SGP_API_KEY',
        'ZAI_API_KEY',
        'ZAI_CODING_CN_API_KEY',
    )),
    'claude': frozenset((
        'ANTHROPIC_API_KEY',
        'ANTHROPIC_AUTH_TOKEN',
        'ANTHROPIC_AWS_API_KEY',
        'ANTHROPIC_BASE_URL',
        'ANTHROPIC_CUSTOM_HEADERS',
        'ANTHROPIC_FOUNDRY_API_KEY',
        'ANTHROPIC_FOUNDRY_AUTH_TOKEN',
        'AWS_ACCESS_KEY_ID',
        'AWS_BEARER_TOKEN_BEDROCK',
        'AWS_CONFIG_FILE',
        'AWS_SECRET_ACCESS_KEY',
        'AWS_SESSION_TOKEN',
        'AWS_SHARED_CREDENTIALS_FILE',
        'CLAUDE_CODE_HOST_AUTH_ENV_VAR',
        'CLAUDE_CODE_HOST_CREDS_FILE',
        'CLAUDE_CODE_OAUTH_REFRESH_TOKEN',
        'CLAUDE_CODE_OAUTH_SCOPES',
        'CLAUDE_CODE_OAUTH_TOKEN',
        'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR',
        'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST',
        'CLAUDE_CODE_RATE_LIMIT_TIER',
        'CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH',
        'CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH',
        'CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH',
        'CLAUDE_CODE_SKIP_ANTHROPIC_GOOGLE_CLOUD_AUTH',
        'CLAUDE_CODE_SKIP_BEDROCK_AUTH',
        'CLAUDE_CODE_SKIP_FOUNDRY_AUTH',
        'CLAUDE_CODE_SKIP_MANTLE_AUTH',
        'CLAUDE_CODE_SKIP_VERTEX_AUTH',
        'CLAUDE_CODE_SUBSCRIPTION_TYPE',
        'CLAUDE_CODE_USE_ANTHROPIC_AWS',
        'CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD',
        'CLAUDE_CODE_USE_BEDROCK',
        'CLAUDE_CODE_USE_FOUNDRY',
        'CLAUDE_CODE_USE_GATEWAY',
        'CLAUDE_CODE_USE_MANTLE',
        'CLAUDE_CODE_USE_VERTEX',
        'GOOGLE_APPLICATION_CREDENTIALS',
    )),
}


def inherited_account_environment(family):
    """Known native account inputs that a new profile must not inherit."""
    if family not in _ACCOUNT_ENVIRONMENT:
        raise ValueError("Unsupported native profile family")
    return tuple(sorted(_ACCOUNT_ENVIRONMENT[family]))


@dataclass(frozen=True)
class NativeProfile:
    family: str
    kind: str
    home: Path
    roots: Mapping[str, Path]


def resolve_profile(family, home, *, platform=None):
    """Describe one explicit native profile; never create or import its data."""
    platform = sys.platform if platform is None else platform
    if family not in ('deepseek', 'claude'):
        raise ValueError('此应用暂不支持新增独立桌面配置')
    if platform not in ('darwin', 'linux', 'win32'):
        raise ValueError('当前系统暂不支持独立桌面配置')
    if not isinstance(home, (str, Path)) or not str(home).strip() or '\x00' in str(home):
        raise ValueError('请选择独立配置目录')
    home = Path(home).expanduser()
    if not home.is_absolute():
        raise ValueError('独立配置目录必须是绝对路径')
    home = home.resolve()
    if family == 'deepseek':
        roots = {'runtimeHome': home, 'userData': home/'desktop-user-data'}
    else:
        # Claude selects the sibling "-3p" itself for API login. Reserving both
        # roots prevents another entry from claiming that native destination.
        if platform == 'win32':
            # The packaged App derives 3p from LOCALAPPDATA and may migrate
            # APPDATA/Claude-3p. Scope both to this process, including the old
            # migration source; --user-data-dir alone does not isolate them.
            roots = {'userData': home, 'code': home/'claude-code-config',
                     'roamingAppData': home/'windows-roaming', 'localAppData': home/'windows-local',
                     'thirdParty': home/'windows-local/Claude-3p'}
        else:
            third_party = home if str(home).endswith('-3p') else Path(str(home)+'-3p')
            roots = {'userData': home, 'code': home/'claude-code-config', 'thirdParty': third_party}
    return NativeProfile(family, 'desktop-profile', home, roots)


def launch_parameters(profile, *, platform=None):
    """Return only the selected App's native arguments and environment changes."""
    expected = resolve_profile(profile.family, profile.home, platform=platform)
    if profile.kind != expected.kind or dict(profile.roots) != dict(expected.roots):
        raise ValueError('独立配置的原生目录不一致，请重新加载接入')
    if profile.family == 'deepseek':
        environment = {'DSH_HOME': str(profile.roots['runtimeHome'])}
    else:
        environment = {'CLAUDE_CONFIG_DIR': str(profile.roots['code'])}
        if 'localAppData' in profile.roots:
            environment.update(APPDATA=str(profile.roots['roamingAppData']),
                               LOCALAPPDATA=str(profile.roots['localAppData']))
    return {'arguments': ('--user-data-dir='+str(profile.roots['userData']),),
            'environment': environment,
            'environmentUnset': inherited_account_environment(profile.family)}
