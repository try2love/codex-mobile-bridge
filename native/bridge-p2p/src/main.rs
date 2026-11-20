//! One private instance per helper. Secrets enter through stdin, never argv or logs.
use anyhow::{Result, ensure};
use bridge_p2p::{Runtime, Settings};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    if run().await.is_err() {
        eprintln!("bridge_p2p_helper_failed");
        std::process::exit(1);
    }
}
async fn run() -> Result<()> {
    let mut input = BufReader::new(tokio::io::stdin()).lines();
    let line = input.next_line().await?.ok_or_else(|| anyhow::anyhow!("missing settings"))?;
    ensure!(line.len() <= 4096, "oversized settings");
    let runtime = Runtime::start(serde_json::from_str::<Settings>(&line)?).await?;
    let mut stdout = tokio::io::stdout();
    stdout.write_all(format!("{{\"ready\":true,\"local_port\":{}}}\n", runtime.local_port).as_bytes()).await?;
    stdout.flush().await?;
    // EOF is the parent's lifetime fence. No daemon, no persistent configuration.
    while input.next_line().await?.is_some() {}
    drop(runtime);
    Ok(())
}
