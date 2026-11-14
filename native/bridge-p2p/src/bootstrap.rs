//! Verified WSS bootstrap over the existing HTTPS port. Only EasyTier RPC crosses
//! this pipe; the server-side EasyTier node rejects all payload relaying.
use std::{net::Ipv4Addr, sync::Arc, time::Duration};
use anyhow::Result;
use futures_util::{SinkExt, StreamExt};
use rustls_platform_verifier::BuilderVerifierExt;
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, net::TcpListener, task::JoinSet};
use tokio_tungstenite::{Connector, tungstenite::{Message, protocol::WebSocketConfig}};

pub async fn start(endpoint: String) -> Result<(String, tokio::task::JoinHandle<()>)> {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await?;
    let port = listener.local_addr()?.port();
    let config = rustls::ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()?.with_platform_verifier()?.with_no_client_auth();
    let config = Arc::new(config);
    let task = tokio::spawn(async move {
        let mut tasks = JoinSet::new();
        loop {
            tokio::select! {
                Some(_) = tasks.join_next(), if !tasks.is_empty() => {},
                accepted = listener.accept() => {
                    let Ok((mut stream, _)) = accepted else { break; };
                    if tasks.len() >= 4 { continue; }
                    let endpoint = endpoint.clone(); let config = config.clone();
                    tasks.spawn(async move {
                        let limits = WebSocketConfig::default().max_message_size(Some(65536)).max_frame_size(Some(65536));
                        let Ok(Ok((mut socket, _))) = tokio::time::timeout(Duration::from_secs(15),
                            tokio_tungstenite::connect_async_tls_with_config(endpoint, Some(limits), false, Some(Connector::Rustls(config)))).await else { return; };
                        let mut buffer = [0; 16384];
                        loop {
                            tokio::select! {
                                read = stream.read(&mut buffer) => {
                                    let Ok(count) = read else { break; };
                                    if count == 0 || socket.send(Message::Binary(buffer[..count].to_vec().into())).await.is_err() { break; }
                                }
                                message = socket.next() => match message {
                                    Some(Ok(Message::Binary(data))) => if stream.write_all(&data).await.is_err() { break; },
                                    Some(Ok(Message::Ping(data))) => if socket.send(Message::Pong(data)).await.is_err() { break; },
                                    Some(Ok(Message::Pong(_))) => {},
                                    _ => break,
                                }
                            }
                        }
                        let _ = socket.close(None).await;
                    });
                }
            }
        }
    });
    Ok((format!("tcp://127.0.0.1:{port}"), task))
}
