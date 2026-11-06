//! One ordered Noise transport, with bounded independent HTTP streams.
use std::collections::HashMap;
use std::pin::Pin;
use std::sync::{
    Arc,
    atomic::{AtomicU64, AtomicUsize, Ordering},
};

use futures_util::{Stream, stream::SelectAll};
use tokio::sync::{OwnedSemaphorePermit, Semaphore, mpsc};
use tokio::time::Instant;

use super::*;

type Outgoing = Pin<Box<dyn Stream<Item = (u64, Option<Vec<u8>>)> + Send>>;
struct Registration {
    id: u64,
    incoming: mpsc::Sender<Vec<u8>>,
    outgoing: mpsc::Receiver<Vec<u8>>,
    write: bool,
}

pub(super) struct Session {
    register: mpsc::Sender<Registration>,
    slots: Arc<Semaphore>,
    next: AtomicU64,
    pub closed: CancellationToken,
    cancel: mpsc::Sender<u64>,
    large: Arc<Semaphore>,
    pub quiesce: CancellationToken,
    writes: Arc<AtomicUsize>,
}

pub(super) struct Substream {
    id: u64,
    tx: mpsc::Sender<Vec<u8>>,
    rx: mpsc::Receiver<Vec<u8>>,
    closed: CancellationToken,
    complete: bool,
    cancel: mpsc::Sender<u64>,
    large: Arc<Semaphore>,
    large_slot: Option<OwnedSemaphorePermit>,
    writes: Arc<AtomicUsize>,
    write: bool,
    _slot: OwnedSemaphorePermit,
}

impl Drop for Substream {
    fn drop(&mut self) {
        if self.write {
            self.writes.fetch_sub(1, Ordering::AcqRel);
        }
        if !self.complete && !self.closed.is_cancelled() {
            let _ = self.cancel.try_send(self.id);
        }
    }
}

impl Substream {
    pub fn large(&mut self) -> Result<()> {
        if self.large_slot.is_none() {
            self.large_slot = Some(
                self.large
                    .clone()
                    .try_acquire_owned()
                    .map_err(|_| BridgeError::Relay("capacity".into()))?,
            );
        }
        Ok(())
    }
    async fn raw(&self, value: Vec<u8>) -> Result<()> {
        tokio::select! {
            _ = self.closed.cancelled() => Err(BridgeError::Network),
            result = tokio::time::timeout(Duration::from_secs(10), self.tx.send(value)) =>
                result.map_err(|_| BridgeError::Network)?.map_err(|_| BridgeError::Network),
        }
    }
    pub async fn send(&self, mut value: Value) -> Result<()> {
        value["stream_id"] = json!(self.id);
        let mut raw = vec![0];
        raw.extend(serde_json::to_vec(&value)?);
        if raw.len() > MAX_CHUNK {
            return Err(BridgeError::TooLarge);
        }
        self.raw(raw).await
    }
    pub async fn send_body(&self, body: &[u8]) -> Result<()> {
        let mut raw = vec![1];
        raw.extend(self.id.to_be_bytes());
        raw.extend(body);
        self.raw(raw).await
    }
    pub async fn receive(&mut self, timeout: u64) -> Result<Vec<u8>> {
        // Drain authenticated buffered records even after the socket has closed.
        tokio::time::timeout(Duration::from_secs(timeout), self.rx.recv())
            .await
            .map_err(|_| BridgeError::Network)?
            .ok_or(BridgeError::Network)
    }
    pub async fn control(&mut self, timeout: u64) -> Result<Value> {
        let raw = self.receive(timeout).await?;
        if raw.first() != Some(&0) {
            return Err(invalid());
        }
        let value: Value = serde_json::from_slice(&raw[1..])?;
        if value["kind"] == "stream_error" {
            return Err(BridgeError::Relay(
                value["code"].as_str().unwrap_or("request_failed").into(),
            ));
        }
        Ok(value)
    }
    pub async fn finish(&mut self) {
        // Receipt of authenticated response_end is success even if its ACK is lost.
        self.complete = true;
        let _ = self.send(json!({"kind":"response_ack"})).await;
    }
}

impl Session {
    pub fn start(channel: Channel, status: tokio::sync::watch::Sender<String>) -> Arc<Self> {
        let (tx, rx) = mpsc::channel(8);
        let (cancel, cancelled) = mpsc::channel(16);
        let quiesce = CancellationToken::new();
        let writes = Arc::new(AtomicUsize::new(0));
        let session = Arc::new(Self {
            register: tx,
            slots: Arc::new(Semaphore::new(8)),
            next: AtomicU64::new(1),
            closed: CancellationToken::new(),
            cancel,
            large: Arc::new(Semaphore::new(1)),
            quiesce: quiesce.clone(),
            writes: writes.clone(),
        });
        let closed = session.closed.clone();
        tokio::spawn(async move {
            let id = channel.channel;
            let result = run(channel, rx, cancelled, closed.clone(), quiesce, writes).await;
            // Only public categories; never error bodies, tokens or plaintext.
            let reason = if result.is_ok() {
                "paused"
            } else {
                "reconnecting"
            };
            if let Err(error) = &result {
                eprintln!(
                    "relay_failure channel={id} category={:?}",
                    std::mem::discriminant(error)
                );
            }
            eprintln!("relay_session_end channel={id} reason={reason}");
            status.send_replace(reason.into());
            closed.cancel();
        });
        session
    }
    pub async fn open(&self, write: bool) -> Result<Substream> {
        if self.quiesce.is_cancelled() {
            return Err(BridgeError::Cancelled);
        }
        let permit = tokio::select! {
            _ = self.closed.cancelled() => return Err(BridgeError::Network),
            result = tokio::time::timeout(Duration::from_secs(15), self.slots.clone().acquire_owned()) =>
                result.map_err(|_| BridgeError::Relay("capacity".into()))?.map_err(|_| BridgeError::Network)?,
        };
        let id = self.next.fetch_add(1, Ordering::Relaxed);
        if id >= (1 << 53) {
            self.closed.cancel();
            return Err(invalid());
        }
        let (incoming, rx) = mpsc::channel(8);
        let (tx, outgoing) = mpsc::channel(8);
        self.register
            .send(Registration {
                id,
                incoming,
                outgoing,
                write,
            })
            .await
            .map_err(|_| BridgeError::Network)?;
        if write {
            self.writes.fetch_add(1, Ordering::AcqRel);
        }
        Ok(Substream {
            id,
            tx,
            rx,
            closed: self.closed.clone(),
            complete: false,
            cancel: self.cancel.clone(),
            large: self.large.clone(),
            large_slot: None,
            write,
            writes: self.writes.clone(),
            _slot: permit,
        })
    }
}

async fn run(
    mut channel: Channel,
    mut register: mpsc::Receiver<Registration>,
    mut cancelled: mpsc::Receiver<u64>,
    closed: CancellationToken,
    quiesce: CancellationToken,
    writes: Arc<AtomicUsize>,
) -> Result<()> {
    let mut inputs = HashMap::<u64, mpsc::Sender<Vec<u8>>>::new();
    let mut outputs = SelectAll::<Outgoing>::new();
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut last = Instant::now();
    let mut ping: Option<(String, Instant)> = None;
    let mut writable = true;
    let mut write_ids = std::collections::HashSet::new();
    let mut draining: Option<Instant> = None;
    loop {
        tokio::select! {
            _ = closed.cancelled() => { let _ = channel.socket.close(None).await; return Ok(()); }
            _ = quiesce.cancelled(), if draining.is_none() => {
                draining = Some(Instant::now());
                let read_ids:Vec<_> = inputs.keys().filter(|id| !write_ids.contains(*id)).copied().collect();
                for id in read_ids { inputs.remove(&id); channel.send(json!({"kind":"cancel","stream_id":id})).await?; }
                if writes.load(Ordering::Acquire)==0 { return Ok(()); }
            }
            Some(id) = cancelled.recv() => {
                inputs.remove(&id);
                channel.send(json!({"kind":"cancel","stream_id":id})).await?;
            }
            registration = register.recv() => {
                let Some(item) = registration else { return Ok(()); };
                if item.incoming.is_closed() { continue; }
                if draining.is_some() { continue; }
                if item.write { write_ids.insert(item.id); }
                inputs.insert(item.id, item.incoming);
                let id = item.id;
                outputs.push(Box::pin(futures_util::stream::unfold(Some(item.outgoing), move |state| async move {
                    let mut rx = state?;
                    match rx.recv().await {
                        Some(raw) => Some(((id, Some(raw)), Some(rx))),
                        None => Some(((id, None), None)),
                    }
                })));
            }
            frame = channel.socket.next() => {
                let raw = match frame {
                    Some(Ok(Message::Binary(raw))) => raw,
                    Some(Ok(Message::Ping(raw))) => {
                        channel.socket.send(Message::Pong(raw)).await.map_err(|_| BridgeError::Network)?;
                        continue;
                    }
                    Some(Ok(Message::Pong(_))) => continue,
                    _ => return Err(BridgeError::Network),
                };
                let frame = checked(Frame::decode(&raw))?;
                if frame.binding != channel.binding || frame.channel != channel.channel || frame.kind != 2 { return Err(invalid()); }
                let data = checked(channel.cipher.decrypt(&frame.payload))?;
                last = Instant::now();
                let (id, data) = if data.first() == Some(&0) {
                    let mut value: Value = serde_json::from_slice(&data[1..])?;
                    let id = value.get("stream_id").and_then(Value::as_u64).ok_or_else(invalid)?;
                    value.as_object_mut().ok_or_else(invalid)?.remove("stream_id");
                    if id == 0 {
                        if value["kind"] != "pong" || !ping.as_ref().is_some_and(|(id,_)| value["id"] == *id) { return Err(invalid()); }
                        ping = None;
                        continue;
                    }
                    let mut raw = vec![0]; raw.extend(serde_json::to_vec(&value)?); (id,raw)
                } else if data.first() == Some(&1) && data.len()>9 {
                    let id = u64::from_be_bytes(data[1..9].try_into().unwrap());
                    let mut raw = vec![1]; raw.extend_from_slice(&data[9..]); (id,raw)
                } else { return Err(invalid()); };
                if let Some(tx) = inputs.get(&id)
                    && tx.try_send(data).is_err() {
                        inputs.remove(&id);
                        channel.send(json!({"kind":"cancel","stream_id":id})).await?;
                }
            }
            item = outputs.next(), if !outputs.is_empty() && writable => {
                if let Some((id, data)) = item {
                    if let Some(data) = data {
                        if !inputs.contains_key(&id) { continue; }
                        if channel.send_data(&data).await.is_err() { writable = false; }
                    } else { inputs.remove(&id); write_ids.remove(&id); }
                }
            }
            _ = tick.tick() => {
                if draining.is_some_and(|at| writes.load(Ordering::Acquire)==0 || at.elapsed()>=Duration::from_secs(60)) { return Ok(()); }
                if ping.as_ref().is_some_and(|(_,at)| at.elapsed() >= Duration::from_secs(10)) { return Err(BridgeError::Network); }
                if last.elapsed() >= Duration::from_secs(20) && ping.is_none() {
                    if !writable { return Err(BridgeError::Network); }
                    let id = Uuid::new_v4().to_string();
                    channel.send(json!({"kind":"ping","stream_id":0,"id":id})).await?;
                    ping = Some((id,Instant::now()));
                }
            }
        }
    }
}
