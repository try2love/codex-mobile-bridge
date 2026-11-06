"""Pack exactly the gateway's public asset map, using its normal assembler."""
from pathlib import Path
import json
import sys
root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from bridge.api.assets import STATIC, served_asset_bytes

out = Path(sys.argv[1])
lines = []
for index, (url, (_, mime)) in enumerate(STATIC.items()):
    data = served_asset_bytes(root/'web', url)
    if url in ('/', '/index.html'):
        data = data.replace(b'<head>', b'<head><script src="/cmb-cloud-host.js"></script>', 1)
    name = f'cloud-web-{index}'
    (out/name).write_bytes(data)
    lines.append(f'{json.dumps(url)} => Some(({json.dumps(mime)}, include_bytes!("{name}"))),')
(out/'assets.rs').write_text('fn asset(path: &str) -> Option<(&\'static str, &\'static [u8])>{match path {\n'
                           + '\n'.join(lines) + '\n_=>None,}}\n')
