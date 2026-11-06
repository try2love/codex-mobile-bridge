fn main() {
    println!("cargo:rerun-if-changed=../../../web");
    println!("cargo:rerun-if-changed=../../../bridge/api/assets.py");
    println!("cargo:rerun-if-changed=assets.py");
    let result = std::process::Command::new("python3")
        .args(["-B", "assets.py", &std::env::var("OUT_DIR").unwrap()])
        .status()
        .unwrap();
    assert!(result.success(), "could not assemble the workbench");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("android") {
        // Cargokit sets CARGO_ENCODED_RUSTFLAGS, which overrides .cargo/config.toml.
        println!("cargo:rustc-link-arg=-Wl,-z,max-page-size=16384");
    }
}
