use ruvector_server::{Config, RuvectorServer};

#[tokio::main]
async fn main() {
    let config = Config {
        host: "0.0.0.0".to_string(),
        port: 6333,
        enable_cors: true,
        enable_compression: true,
    };

    let server = RuvectorServer::with_config(config);

    println!("Starting RuVector REST server on 0.0.0.0:6333");

    if let Err(e) = server.start().await {
        eprintln!("Server error: {:?}", e);
    }
}