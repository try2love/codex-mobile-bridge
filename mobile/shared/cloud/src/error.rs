use thiserror::Error;

#[derive(Debug, Error)]
pub enum BridgeError {
    #[error("Invalid gateway address: {0}")]
    Address(String),
    #[error("Please sign in again")]
    Unauthenticated,
    #[error("The gateway redirected the request. Credentials were not forwarded")]
    Redirect,
    #[error("The gateway connection was interrupted. Check the network and gateway.")]
    Network,
    #[error("Relay connection: {0}")]
    Relay(String),
    #[error("The gateway certificate could not be verified.")]
    Tls,
    #[error("Gateway response exceeded the safe size limit")]
    TooLarge,
    #[error("Gateway error ({status}): {message}")]
    Http {
        status: u16,
        code: String,
        message: String,
    },
    #[error("Incompatible gateway response: {0}")]
    Protocol(String),
    #[error("{0}")]
    Invalid(String),
    #[error("Local state could not be saved. Check the computer before retrying")]
    Storage(#[from] std::io::Error),
    #[error("Operation cancelled")]
    Cancelled,
}

pub type Result<T> = std::result::Result<T, BridgeError>;

impl From<reqwest::Error> for BridgeError {
    fn from(_: reqwest::Error) -> Self {
        Self::Network
    }
}

impl From<serde_json::Error> for BridgeError {
    fn from(_: serde_json::Error) -> Self {
        Self::Protocol("invalid JSON".into())
    }
}
