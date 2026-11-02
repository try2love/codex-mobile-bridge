import asyncio
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from bridge.clients.claude.adapter import Claude

ROOT = Path(__file__).resolve().parents[1]


class FirstConnection(unittest.TestCase):
    def test_trusted_folder_connects_without_a_session_or_model_and_bounds_unacknowledged_replies(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            root = Path(temp)
            stage = root/'profile/pending-uploads'
            stage.mkdir(parents=True)
            own_attachment = stage/'user-attachment.txt'
            own_attachment.write_text('untouched')
            directory = root/'bridge'
            adapter = Claude(directory, auto_connect=False)
            adapter.discovery['dataHome'] = str(stage.parent)
            try:
                adapter.prepare()
                desktop = adapter.desktop
                process = subprocess.Popen(['node', str(ROOT/'tests/fixtures/claude-no-turn.cjs'), str(directory), str(stage)],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic()+5
                    while not desktop.connected and process.poll() is None and time.monotonic()<deadline:
                        time.sleep(.05)
                    self.assertTrue(desktop.connected)
                    call = asyncio.run_coroutine_threadsafe(desktop.call('code','getAll',timeout=6), adapter.loop)
                    self.assertEqual(call.result(timeout=7), [])
                    counts = json.loads((directory/'counts.json').read_text())
                    self.assertEqual((counts['starts'],counts['sends'],counts['trust']), (0,0,1))
                    self.assertEqual(own_attachment.read_text(), 'untouched')
                    self.assertTrue((directory/'staged-ack.json').is_file())
                    # Stop the reader but leave the renderer and its last signed request alive.
                    desktop.close()
                    time.sleep(0.65)
                    self.assertLessEqual(len(list(stage.glob('*-cmb-*.bin'))),1)
                finally:
                    process.terminate()
                    process.communicate(timeout=5)
            finally:
                adapter.close()
