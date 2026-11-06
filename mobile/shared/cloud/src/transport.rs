use crate::error::{BridgeError, Result};
use futures_util::StreamExt;
use reqwest::Response;
pub const MAX_FILE: usize = 20 * 1024 * 1024;
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
