#!/usr/bin/env bash
# Ephemeral CI only: adapt preinstalled Linux tools to the existing APK builder.
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
test "$(uname -s)" = Linux
tools=.tmp/mobile-tools
test ! -e "$tools"
"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" "platforms;android-35" "build-tools;35.0.0"
mkdir -p "$tools/jdk/ci/Contents" "$tools/sdk/platforms" "$tools/sdk/build-tools"
ln -s "$JAVA_HOME" "$tools/jdk/ci/Contents/Home"
ln -s "$ANDROID_HOME/platforms/android-35" "$tools/sdk/platforms/android-35"
ln -s "$ANDROID_HOME/build-tools/35.0.0" "$tools/sdk/build-tools/35.0.0"
curl --fail --location --silent --show-error --retry 2 \
  https://repo.maven.apache.org/maven2/com/google/zxing/core/3.5.3/core-3.5.3.jar \
  --output "$tools/zxing-core-3.5.3.jar"
printf '%s  %s\n' 8d8064c1636fdaef7189dd9055c7d59950a8940a12f2293956446ec3c109fd82 \
  "$tools/zxing-core-3.5.3.jar" | sha256sum --check
printf '%s\n' ca1349214a356cd7958651b2d5a0e1f3811a9c4b > "$tools/zxing.sha1"
export CMB_ANDROID_STORE_PASSWORD=ci-fixture-only
"$JAVA_HOME/bin/keytool" -genkeypair -keystore "$tools/ci-fixture.p12" \
  -storepass:env CMB_ANDROID_STORE_PASSWORD -keypass:env CMB_ANDROID_STORE_PASSWORD \
  -alias fixture -keyalg RSA -keysize 2048 -validity 2 -dname 'CN=CI Fixture'
python3 scripts/build-mobile-android.py --keystore "$tools/ci-fixture.p12" --isolated-preview --output-dir .tmp/native-ci/android
