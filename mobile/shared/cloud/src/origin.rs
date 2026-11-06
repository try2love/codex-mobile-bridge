use anyhow::{Result, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
pub fn exact_origin(value: &str) -> Result<url::Url> {
    let url = url::Url::parse(value)?;
    ensure!(
        value.is_ascii()
            && value.len() <= 2048
            && matches!(url.scheme(), "http" | "https")
            && url.host_str().is_some()
            && url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none()
            && url.path() == "/"
            && url.port() != Some(0)
            && url.as_str().strip_suffix('/') == Some(value),
        "exact canonical HTTP or HTTPS origin required"
    );
    Ok(url)
}

pub fn key(encoded: &str) -> Result<[u8; 32]> {
    let key: [u8; 32] = STANDARD
        .decode(encoded)?
        .try_into()
        .map_err(|_| anyhow::anyhow!("invalid key size"))?;
    ensure!(key != [0; 32], "invalid X25519 key");
    Ok(key)
}
