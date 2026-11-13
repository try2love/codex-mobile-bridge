"""Assemble the visualization reader for hosts without an installed bridge package."""
import ast
import json

from bridge.resources import source_text


def worker_source():
    source = source_text('features/workspace/visualize.py')
    reader = ast.parse(source)
    body = []
    for node in reader.body:
        if isinstance(node, ast.ImportFrom) and node.module == 'bridge.features.workspace.workspace':
            if node.level or len(node.names) != 1 or node.names[0].name != 'Workspace':
                raise ValueError('可视化远端读取依赖无效')
            if node.names[0].asname:
                body.append(node.names[0].asname + ' = Workspace')
        else:
            body.append(ast.get_source_segment(source, node))
    # Share the same path and symlink checks as local reads, including in bundles.
    return source_text('features/workspace/workspace.py') + '\n' + '\n\n'.join(body) + '\n'


def read_request(path, cwd):
    request = repr(json.dumps({'path': path, 'cwd': cwd}))
    return worker_source() + f'''
try:
    result = {{"value": read_source(**json.loads({request}))}}
except (ValueError, OSError) as error:
    result = {{"error": str(error)}}
print(json.dumps(result, ensure_ascii=False))
'''
