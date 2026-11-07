use crate::error::{BridgeError, Result};
use futures_util::StreamExt;
use reqwest::Response;
pub const MAX_FILE: usize = 20 * 1024 * 1024;
// Forward only the resumable-download contract, never the view grant or web cookies.
pub(crate) fn download_headers(headers: &http::HeaderMap) -> Result<serde_json::Map<String, serde_json::Value>> {
    let mut result = serde_json::Map::new();
    for name in ["Range", "If-Range"] {
        let mut values = headers.get_all(name).iter();
        if let Some(value) = values.next() {
            let value = value.to_str().ok().filter(|text| text.len() <= 256
                && text.bytes().all(|byte| (32..=126).contains(&byte)))
                .ok_or_else(|| BridgeError::Protocol("invalid download range".into()))?;
            if values.next().is_some() {
                return Err(BridgeError::Protocol("duplicate download range".into()));
            }
            result.insert(name.into(), value.into());
        }
    }
    Ok(result)
}
pub struct Transport;
impl Transport {
    pub async fn bounded_bytes(response: Response, limit: usize) -> Result<Vec<u8>> {
        if response
            .content_length()
            .is_some_and(|length| length > limit as u64)
        {
            return Err(BridgeError::TooLarge);
        }
        let mut stream = response.bytes_stream();
        let mut data = Vec::new();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk?;
            if data.len() + chunk.len() > limit {
                return Err(BridgeError::TooLarge);
            }
            data.extend_from_slice(&chunk);
        }
        Ok(data)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn download_requests_preserve_ranges_without_forwarding_credentials() {
        let request = http::Request::builder().header("Range", "bytes=1048576-2097151")
            .header("If-Range", "\"original\"").header("Cookie", "private")
            .header("Authorization", "Bearer private").header("X-CMB-Cloud-View", "private")
            .body(()).unwrap();
        assert_eq!(serde_json::Value::Object(download_headers(request.headers()).unwrap()),
            serde_json::json!({"Range":"bytes=1048576-2097151","If-Range":"\"original\""}));
        let mut headers = request.headers().clone();
        headers.append("Range", "bytes=0-1".parse().unwrap());
        assert!(download_headers(&headers).is_err());
        headers.remove("Range");
        headers.insert("If-Range", "x".repeat(257).parse().unwrap());
        assert!(download_headers(&headers).is_err());
    }
}
