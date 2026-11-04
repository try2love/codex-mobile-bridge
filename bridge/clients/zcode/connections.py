"""The selected transport and its non-secret setup information."""

MODES = ('private-pipe', 'official-remote')


def selected_mode(config):
    mode = config.get('zcodeConnectionMode')
    if mode in MODES:
        return mode
    return 'official-remote' if config.get('zcodeOfficialRemote') is True else 'private-pipe'


def connection_modes(config, *, installed, pipe_supported, official, official_supported):
    selected = selected_mode(config)
    return {
        'connectionMode': selected,
        'connectionModes': [
            {'id': 'private-pipe', 'label': '本机连接',
             'description': '由网关启动 ZCode，无需额外登录。若已自行启动，请结束任务后通过初始化连接正常重启。',
             'configured': bool(installed and pipe_supported), 'available': bool(installed and pipe_supported),
             'connected': False,
             'reason': '' if pipe_supported else '当前安装暂不能使用本机连接'},
            {'id': 'official-remote', 'label': '官方远控',
             'description': '在电脑网关保存原 App 远控链接并完成同账号登录；会话数据经过 ZCode 官方中继。',
             'configured': official.get('configured') is True,
             'available': bool(official_supported and official.get('configured')),
             'connected': selected == 'official-remote' and official.get('connected') is True,
             'reason': ('此 Test 的官方远控暂只支持 macOS' if not official_supported else
                        '' if official.get('configured') else '请先在电脑网关配置并登录官方远控')},
        ],
    }
