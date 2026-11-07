use crate::relay::Relay;
use anyhow::Result;
use bytes::Bytes;
use futures_util::{StreamExt, TryStreamExt};
use http_body_util::{BodyExt, Full, Limited, StreamBody, combinators::UnsyncBoxBody};
use hyper::{
    Method, Request, Response, StatusCode,
    body::{Frame, Incoming},
    server::conn::http1,
    service::service_fn,
};
use hyper_util::rt::TokioIo;
use std::{
    convert::Infallible,
    sync::{Arc, OnceLock},
};
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;

include!(concat!(env!("OUT_DIR"), "/assets.rs"));
type Body = UnsyncBoxBody<Bytes, std::io::Error>;
struct View {
    profile: String,
    relay: Arc<Relay>,
    shutdown: CancellationToken,
}
static VIEW: OnceLock<Mutex<Option<View>>> = OnceLock::new();
fn current() -> &'static Mutex<Option<View>> {
    VIEW.get_or_init(|| Mutex::new(None))
}
pub(crate) async fn close() {
    if let Some(view) = current().lock().await.take() {
        view.shutdown.cancel();
        view.relay.pause().await;
    }
}
pub(crate) async fn close_profile(profile: &str) {
    let view = {
        let mut current = current().lock().await;
        if current.as_ref().is_some_and(|view| view.profile == profile) {
            current.take()
        } else {
            None
        }
    };
    if let Some(view) = view {
        view.shutdown.cancel();
        view.relay.pause().await;
    }
}
pub(crate) async fn open(profile: String, relay: Arc<Relay>, drafts: crate::local_state::Store) -> Result<String> {
    close().await;
    // Allocated only for a user-selected, proven binding; never when displaying the directory.
    let socket = tokio::net::TcpListener::bind(("127.0.0.1", 0)).await?;
    let addr = socket.local_addr()?.to_string();
    let capability = uuid::Uuid::new_v4().simple().to_string();
    let entry = format!("http://{addr}/#cmb-cloud={capability}");
    let grant = Arc::new((addr, capability, relay.clone(), drafts));
    let stop = CancellationToken::new();
    *current().lock().await = Some(View {
        profile,
        relay,
        shutdown: stop.clone(),
    });
    tokio::spawn(async move {
        let slots = Arc::new(tokio::sync::Semaphore::new(32));
        loop {
            let Ok((stream, _)) =
                (tokio::select! {_ = stop.cancelled()=>break, result=socket.accept()=>result})
            else {
                break;
            };
            let Ok(slot) = slots.clone().try_acquire_owned() else {
                continue;
            };
            let grant = grant.clone();
            let stop = stop.clone();
            tokio::spawn(async move {
                let _slot = slot;
                let server = http1::Builder::new().serve_connection(
                    TokioIo::new(stream),
                    service_fn(move |req| serve(grant.clone(), req)),
                );
                tokio::select! {_ = stop.cancelled()=>{},_ = server=>{}}
            });
        }
    });
    Ok(entry)
}
fn bytes(status: u16, mime: &str, data: Bytes) -> Response<Body> {
    Response::builder().status(status).header("Content-Type",mime).header("Cache-Control","no-store")
        .header("X-Content-Type-Options","nosniff").header("Referrer-Policy","no-referrer")
        .header("Content-Security-Policy","default-src 'self'; script-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")
        .body(Full::new(data).map_err(|never|match never {}).boxed_unsync()).unwrap()
}
fn failure(code: u16, reason: &str) -> Response<Body> {
    bytes(
        code,
        "application/json",
        Bytes::from(serde_json::json!({"error":reason}).to_string()),
    )
}
fn relay_failure(error: crate::error::BridgeError) -> Response<Body> {
    let status = match &error {
        crate::error::BridgeError::Relay(code) if code == "access_denied" => 403,
        _ => 503,
    };
    failure(status, &error.to_string())
}
async fn serve(
    grant: Arc<(String, String, Arc<Relay>, crate::local_state::Store)>,
    req: Request<Incoming>,
) -> Result<Response<Body>, Infallible> {
    let (host, cap, relay, drafts) = grant.as_ref();
    let origin = format!("http://{host}");
    if req.headers().get("host").and_then(|v| v.to_str().ok()) != Some(host.as_str())
        || req
            .headers()
            .get("origin")
            .is_some_and(|v| v.to_str().ok() != Some(origin.as_str()))
    {
        return Ok(failure(403, "Invalid origin"));
    }
    let path = req.uri().path();
    if !path.starts_with("/api/") && path != "/cmb-cloud-state" {
        if req.method() != Method::GET {
            return Ok(failure(405, "Unsupported method"));
        }
        let data = if path == "/cmb-cloud-host.js" {
            Some((
                "application/javascript",
                include_bytes!("host.js").as_slice(),
            ))
        } else {
            asset(path)
        };
        return Ok(match data {
            Some((mime, data)) => bytes(200, mime, Bytes::from_static(data)),
            None => failure(404, "Not found"),
        });
    }
    if req
        .headers()
        .get("X-CMB-Cloud-View")
        .and_then(|v| v.to_str().ok())
        != Some(cap.as_str())
    {
        return Ok(failure(403, "Cloud view required"));
    }
    let method = req.method().clone();
    if !matches!(method, Method::GET | Method::POST) {
        return Ok(failure(405, "Unsupported method"));
    }
    if path == "/cmb-cloud-state" {
        let result = if method == Method::GET {
            drafts.snapshot()
        } else {
            match Limited::new(req.into_body(), crate::local_state::MAX_VALUE + 16384).collect().await {
                Ok(body) => drafts.apply(&body.to_bytes()),
                Err(_) => return Ok(failure(413, "Draft too large")),
            }
        };
        return Ok(match result {
            Ok(value) => bytes(200, "application/json", Bytes::from(value.to_string())),
            Err(_) => failure(503, "Draft storage unavailable"),
        });
    }
    let target = req.uri().path_and_query().unwrap().as_str().to_owned();
    let headers = match crate::transport::download_headers(req.headers()) {
        Ok(headers) => headers,
        Err(_) => return Ok(failure(400, "Invalid download range")),
    };
    let kind = req
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let body = match Limited::new(req.into_body(), crate::transport::MAX_FILE)
        .collect()
        .await
    {
        Ok(body) => body.to_bytes().to_vec(),
        Err(_) => return Ok(failure(413, "Request too large")),
    };
    let response = match relay.request(method, &target, Some(body), &kind, headers).await {
        Ok(res) => res,
        Err(err) => return Ok(relay_failure(err)),
    };
    let mut reply =
        Response::builder().status(StatusCode::from_u16(response.status().as_u16()).unwrap());
    for (key, val) in response.headers() {
        if matches!(
            key.as_str(),
            "content-type" | "content-length" | "etag" | "accept-ranges" | "content-range" | "content-disposition"
        ) {
            reply = reply.header(key, val);
        }
    }
    let stream = response
        .bytes_stream()
        .map_ok(Frame::data)
        .map(|res| res.map_err(std::io::Error::other));
    Ok(reply
        .header("Cache-Control", "no-store")
        .body(StreamBody::new(stream).boxed_unsync())
        .unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn denied_operations_do_not_trigger_the_workbench_network_handoff() {
        use crate::error::BridgeError;
        assert_eq!(relay_failure(BridgeError::Relay("access_denied".into())).status(), StatusCode::FORBIDDEN);
        assert_eq!(relay_failure(BridgeError::Network).status(), StatusCode::SERVICE_UNAVAILABLE);
    }
    #[test]
    fn workbench_bootstraps_before_the_gateway_scripts() {
        let (_, bytes) = asset("/").unwrap();
        let html = std::str::from_utf8(bytes).unwrap();
        assert!(html.find("/cmb-cloud-host.js").unwrap() < html.find("/bootstrap.js").unwrap());
        assert!(asset("/api/auth").is_none());
        assert!(asset("/../gateway-control.json").is_none());
    }
    #[tokio::test]
    async fn closing_an_unused_view_allocates_no_listener() {
        close().await;
        assert!(current().lock().await.is_none());
    }
}
