"""Session-local startup settings shared by local and standalone remote CLI creation."""
import re


def creation_launch_options(selection):
    if not selection:
        return [], {}
    model = selection.get('model')
    if not isinstance(model, str) or not re.fullmatch(r'[^\s\x00-\x1f\x7f]{1,256}', model):
        raise ValueError('请输入有效的模型名称')
    args, environment = ['--model', model], {}
    effort = selection.get('effort')
    if effort is not None:
        if not isinstance(effort, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', effort):
            raise ValueError('请输入有效的思考强度')
        environment['CLAUDE_CODE_EFFORT_LEVEL'] = 'auto' if effort == 'auto' else None
        if effort != 'auto':
            args.extend(('--effort', effort))
    return args, environment
