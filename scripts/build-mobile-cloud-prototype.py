#!/usr/bin/env python3
"""Build the optional native cloud module without installing a system toolchain."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT/'mobile/shared/cloud'
AAR_SHA256 = 'aa021794230fbc2f0be355999e2cf67398dd563de066a2e17001ffbd0b69101b'
TARGETS = {'android': 'aarch64-linux-android', 'ios-sim': 'aarch64-apple-ios-sim', 'ios': 'aarch64-apple-ios'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', choices=TARGETS)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'dist/mobile-cloud-test')
    parser.add_argument('--debug', action='store_true', help='Use a debug Rust library for simulator diagnostics')
    parser.add_argument('--ndk', type=Path)
    parser.add_argument('--verifier-aar', type=Path)
    parser.add_argument('--kotlin-jar', type=Path)
    args = parser.parse_args()
    platform, target = args.platform, TARGETS[args.platform]
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = ROOT/'.tmp/mobile-cloud-build'
    env = dict(os.environ)
    target_dir = Path(env.get('CARGO_TARGET_DIR',str(work/'target'))).resolve()
    env['CARGO_TARGET_DIR'] = str(target_dir)
    if platform != 'android':
        env.setdefault('IPHONEOS_DEPLOYMENT_TARGET','15.0')
    if platform == 'android':
        if not args.ndk or not args.verifier_aar or not args.kotlin_jar:
            parser.error('Android requires --ndk, --verifier-aar and --kotlin-jar')
        if hashlib.sha256(args.verifier_aar.read_bytes()).hexdigest()!=AAR_SHA256:
            parser.error('TLS AAR does not match rustls-platform-verifier 0.2.0')
        compilers = list(args.ndk.glob('toolchains/llvm/prebuilt/*/bin/aarch64-linux-android26-clang'))
        if len(compilers)!=1:
            parser.error('Expected one ARM64 compiler in the NDK')
        cc = compilers[0].absolute()
        env.update(CC_aarch64_linux_android=str(cc), AR_aarch64_linux_android=str(cc.parent/'llvm-ar'),
                   CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER=str(cc))
    subprocess.run(['cargo','build','--locked',*([] if args.debug else ['--release']),'--manifest-path',str(CRATE/'Cargo.toml'),'--target',target],
                   cwd=ROOT,env=env,check=True)
    native = target_dir/target/('debug' if args.debug else 'release')
    if platform!='android':
        directory=out/target
        directory.mkdir(exist_ok=True)
        shutil.copy2(native/'libcmb_cloud.a',directory/'libcmb_cloud.a')
        print(directory/'libcmb_cloud.a')
        return
    libs = work/'android-libs'
    arm = libs/'arm64-v8a'
    arm.mkdir(parents=True,exist_ok=True)
    shutil.copy2(native/'libcmb_cloud.so',arm/'libcmb_cloud.so')
    subprocess.run([str(cc),'-shared','-fPIC','-Wl,-z,max-page-size=16384','-Wl,-soname,libcmb_cloud_android.so',
                    str(CRATE/'android/native.c'),'-L'+str(arm),'-lcmb_cloud','-o',str(arm/'libcmb_cloud_android.so')],check=True)
    with zipfile.ZipFile(args.verifier_aar) as archive:
        (libs/'verifier.jar').write_bytes(archive.read('classes.jar'))
    shutil.copy2(args.kotlin_jar,libs/'kotlin-stdlib.jar')
    subprocess.run([sys.executable,'-B',str(ROOT/'scripts/build-mobile-android.py'),
                    '--cloud-libs',str(libs),'--output-dir',str(out)],cwd=ROOT,check=True)

if __name__ == '__main__':
    main()
