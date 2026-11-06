use jni::EnvUnowned;
use jni::errors::ThrowRuntimeExAndDefault;
use jni::jni_mangle;
use jni::objects::JObject;

// Android's trust manager needs the application class loader before any FFI networking.
#[allow(non_snake_case)]
#[jni_mangle("io.github.try2love.codexbridge.CloudNative")]
pub fn initializeTls<'caller>(
    mut env: EnvUnowned<'caller>,
    _this: JObject<'caller>,
    context: JObject<'caller>,
) {
    env.with_env(|env| rustls_platform_verifier::android::init_with_env(env, context))
        .resolve::<ThrowRuntimeExAndDefault>();
}
