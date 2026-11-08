"""Exercise the signed Mac Test broker boundary with disposable native binaries."""
import importlib.util
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('zcode_starter_bundle', ROOT/'scripts/zcode_starter_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)

# Both processes use a relative Unix socket under the temporary test directory.
# The named helper fixture tests only the production peer identity check.
BROKER = r"""
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc != 2) return 1;
    struct sockaddr_un a = {.sun_family=AF_UNIX, .sun_path="test.sock"};
    int s = socket(AF_UNIX, SOCK_STREAM, 0);
    if (s < 0 || bind(s,(void*)&a,SUN_LEN(&a)) || listen(s,1)) return 2;
    pid_t child = fork();
    if (child == 0) { close(s); execl(argv[1], argv[1], NULL); _exit(3); }
    if (child < 0) return 4;
    int connection = accept(s,NULL,NULL), status;
    if (connection < 0 || waitpid(child,&status,0) != child || !WIFEXITED(status)) return 5;
    close(connection); close(s); unlink(a.sun_path); return WEXITSTATUS(status);
}
"""
PEER = r"""
#define main productionMain
#include "zcode-pipe-starter.m"
#undef main
int main(void) {
    @autoreleasepool {
        struct sockaddr_un a = {.sun_family=AF_UNIX, .sun_path="test.sock"};
        int s = socket(AF_UNIX, SOCK_STREAM, 0);
        if (s < 0 || connect(s,(void*)&a,SUN_LEN(&a))) return 6;
        return bundledBrokerIsPeer(s) ? 0 : 7;
    }
}
"""


@unittest.skipUnless(sys.platform == 'darwin', 'macOS signed process identities')
class ZCodeStarterBundle(unittest.TestCase):
    def test_only_the_signed_broker_in_the_expected_package_can_transfer_descriptors(self):
        self.exercise_bundle(bundle.TEST_ID)

    def test_production_bundle_preserves_the_same_peer_boundary(self):
        self.exercise_bundle(bundle.PRODUCT_ID)

    def exercise_bundle(self, product):
        temp = ROOT/'.tmp'
        temp.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp) as work:
            root = Path(work)
            app = root/'Test.app'
            broker = app/'Contents/Resources/gateway/codex-mobile-gateway'
            broker.parent.mkdir(parents=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': product}))
            broker_source = root/'broker.c'
            broker_source.write_text(BROKER)
            subprocess.run(['/usr/bin/clang', broker_source, '-o', broker], check=True)
            self.sign(broker, 'codex-mobile-gateway')
            bundle.prepare(app)
            helper = bundle.helper_path(app)
            binary = helper/'Contents/MacOS'/bundle.NAME
            self.sign(helper, bundle.IDENTIFIER)
            bundle.verify(app)
            env = {key: value for key, value in os.environ.items() if key != 'CMB_ZCODE_LAUNCH_REQUEST'}
            self.assertEqual(subprocess.run([binary], env=env, timeout=5).returncode, 31)

            # No production option grants access to this diagnostic. Replace only
            # this disposable fixture's main and sign the local test copy again.
            peer_source = root/'peer.m'
            peer_source.write_text(PEER)
            subprocess.run(['/usr/bin/clang', '-fobjc-arc', '-framework', 'AppKit', '-framework', 'Security',
                            '-I', bundle.SOURCE.parent, peer_source, '-o', binary], check=True)
            self.sign(helper, bundle.IDENTIFIER)
            self.assertEqual(subprocess.run([broker, binary], cwd=root, timeout=8).returncode, 0)
            impostor = root/'copied-broker'
            shutil.copy2(broker, impostor)
            self.assertEqual(subprocess.run([impostor, binary], cwd=root, timeout=8).returncode, 7)
            broker_source.write_text('int main(void) { return 42; }\n')
            subprocess.run(['/usr/bin/clang', broker_source, '-o', broker], check=True)
            self.sign(broker, 'codex-mobile-gateway')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                bundle.verify(app)

    @staticmethod
    def sign(path, identity):
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', identity, str(path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
