use crate::backend::{
    CreateSessionRequest, HostedPhase, HostedSession, HostedStatus, RemoteVmBackend,
    SessionProvisioner,
};
use crate::scenario;
use anyhow::{Context, Result, bail};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{Mutex as AsyncMutex, Semaphore};
use uuid::Uuid;

const MIN_TOKEN_LEN: usize = 24;
const MAX_TTL_MINUTES: u64 = 24 * 60;
const MAX_REQUEST_BYTES: usize = 16 * 1024;
const MAX_BODY_BYTES: usize = 8 * 1024;

pub struct ControlConfig {
    pub bind: SocketAddr,
    pub token: String,
    pub scenarios_dir: PathBuf,
    pub backend: RemoteVmBackend,
    pub default_ttl_minutes: u64,
}

#[derive(Clone)]
struct AppState {
    token: Arc<str>,
    scenarios_dir: Arc<PathBuf>,
    backend: Arc<RemoteVmBackend>,
    store: Arc<ControlStore>,
    create_lock: Arc<AsyncMutex<()>>,
    connections: Arc<Semaphore>,
    default_ttl_minutes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct StoredSession {
    session: HostedSession,
    status: HostedStatus,
}

struct ControlStore {
    path: PathBuf,
    sessions: Mutex<HashMap<Uuid, StoredSession>>,
}

#[derive(Debug, Deserialize)]
struct CreateBody {
    scenario: String,
    #[serde(default = "default_sla")]
    sla_minutes: u64,
    #[serde(default)]
    ttl_minutes: Option<u64>,
    #[serde(default)]
    fault: Option<String>,
}

fn default_sla() -> u64 {
    15
}

#[derive(Debug, Serialize)]
struct CreateResponse {
    id: Uuid,
    scenario: String,
    status: HostedPhase,
    expires_at: chrono::DateTime<Utc>,
    ssh_destination: String,
    ssh_port: u16,
    private_key: String,
    connect: String,
}

#[derive(Debug, Serialize)]
struct SessionResponse {
    id: Uuid,
    scenario: String,
    status: HostedPhase,
    created_at: chrono::DateTime<Utc>,
    expires_at: chrono::DateTime<Utc>,
    #[serde(skip_serializing_if = "Option::is_none")]
    elapsed_secs: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    hints_used: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
}

struct HttpRequest {
    method: String,
    path: String,
    authorization: Option<String>,
    body: Vec<u8>,
}

struct HttpResponse {
    status: u16,
    reason: &'static str,
    body: Vec<u8>,
}

impl HttpResponse {
    fn empty(status: u16, reason: &'static str) -> Self {
        Self {
            status,
            reason,
            body: Vec::new(),
        }
    }

    fn json(status: u16, reason: &'static str, value: &impl Serialize) -> Self {
        Self {
            status,
            reason,
            body: serde_json::to_vec(value)
                .unwrap_or_else(|_| br#"{"error":"response serialization failed"}"#.to_vec()),
        }
    }

    fn error(status: u16, reason: &'static str, message: impl Into<String>) -> Self {
        #[derive(Serialize)]
        struct ErrorBody {
            error: String,
        }
        Self::json(
            status,
            reason,
            &ErrorBody {
                error: message.into(),
            },
        )
    }
}

impl ControlStore {
    fn open(path: PathBuf) -> Result<Self> {
        let sessions = if path.exists() {
            let bytes = fs::read(&path)?;
            serde_json::from_slice::<Vec<StoredSession>>(&bytes)?
                .into_iter()
                .map(|stored| (stored.session.id, stored))
                .collect()
        } else {
            HashMap::new()
        };
        Ok(Self {
            path,
            sessions: Mutex::new(sessions),
        })
    }

    fn all(&self) -> Vec<StoredSession> {
        self.sessions.lock().unwrap().values().cloned().collect()
    }

    fn get(&self, id: Uuid) -> Option<StoredSession> {
        self.sessions.lock().unwrap().get(&id).cloned()
    }

    fn put(&self, stored: StoredSession) -> Result<()> {
        let id = stored.session.id;
        let previous = self.sessions.lock().unwrap().insert(id, stored);
        if let Err(error) = self.persist() {
            let mut sessions = self.sessions.lock().unwrap();
            match previous {
                Some(previous) => {
                    sessions.insert(id, previous);
                }
                None => {
                    sessions.remove(&id);
                }
            }
            return Err(error);
        }
        Ok(())
    }

    fn persist(&self) -> Result<()> {
        let sessions = self.sessions.lock().unwrap();
        let mut values: Vec<_> = sessions.values().cloned().collect();
        values.sort_by_key(|stored| stored.session.created_at);
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }
        let tmp = self.path.with_extension("json.tmp");
        fs::write(&tmp, serde_json::to_vec_pretty(&values)?)?;
        fs::rename(tmp, &self.path)?;
        Ok(())
    }
}

pub async fn serve(config: ControlConfig) -> Result<()> {
    validate_config(&config)?;
    let state = AppState {
        token: Arc::from(config.token),
        scenarios_dir: Arc::new(config.scenarios_dir),
        backend: Arc::new(config.backend),
        store: Arc::new(ControlStore::open(
            control_data_dir().join("sessions.json"),
        )?),
        create_lock: Arc::new(AsyncMutex::new(())),
        connections: Arc::new(Semaphore::new(64)),
        default_ttl_minutes: config.default_ttl_minutes,
    };
    let expiry_state = state.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(30));
        loop {
            interval.tick().await;
            expire_sessions(expiry_state.clone()).await;
        }
    });

    let listener = TcpListener::bind(config.bind).await?;
    println!("[replaybook] control plane listening on {}", config.bind);
    println!(
        "[replaybook] dedicated VM: {}:{}",
        state.backend.destination(),
        state.backend.port()
    );
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (stream, _) = accepted?;
                let connection_state = state.clone();
                let permit = state.connections.clone().acquire_owned().await?;
                tokio::spawn(async move {
                    let _permit = permit;
                    if let Err(error) = handle_connection(stream, connection_state).await {
                        eprintln!("[replaybook] control connection failed: {error:#}");
                    }
                });
            }
            _ = tokio::signal::ctrl_c() => break,
        }
    }
    Ok(())
}

async fn handle_connection(mut stream: TcpStream, state: AppState) -> Result<()> {
    let response = match tokio::time::timeout(
        std::time::Duration::from_secs(10),
        read_request(&mut stream),
    )
    .await
    {
        Ok(Ok(request)) => route(request, state).await,
        Ok(Err(error)) => HttpResponse::error(400, "Bad Request", error.to_string()),
        Err(_) => HttpResponse::error(408, "Request Timeout", "request timed out"),
    };
    tokio::time::timeout(
        std::time::Duration::from_secs(10),
        write_response(&mut stream, response),
    )
    .await
    .context("response write timed out")??;
    Ok(())
}

async fn read_request(stream: &mut (impl AsyncRead + Unpin)) -> Result<HttpRequest> {
    let mut bytes = Vec::with_capacity(1024);
    let mut buffer = [0_u8; 1024];
    let header_end;
    loop {
        let read = stream.read(&mut buffer).await?;
        if read == 0 {
            bail!("connection closed before request completed");
        }
        bytes.extend_from_slice(&buffer[..read]);
        if bytes.len() > MAX_REQUEST_BYTES {
            bail!("request exceeds {MAX_REQUEST_BYTES} bytes");
        }
        if let Some(index) = find_header_end(&bytes) {
            header_end = index;
            break;
        }
    }

    let headers = std::str::from_utf8(&bytes[..header_end]).context("headers are not UTF-8")?;
    let mut lines = headers.split("\r\n");
    let request_line = lines.next().context("missing request line")?;
    let mut request_parts = request_line.split_whitespace();
    let method = request_parts.next().context("missing method")?.to_owned();
    let path = request_parts.next().context("missing path")?.to_owned();
    if request_parts.next() != Some("HTTP/1.1") || request_parts.next().is_some() {
        bail!("only HTTP/1.1 requests are supported");
    }
    let mut content_length = 0_usize;
    let mut saw_content_length = false;
    let mut authorization = None;
    let mut saw_host = false;
    for line in lines {
        let (name, value) = line.split_once(':').context("malformed header")?;
        if name.trim() != name
            || name.is_empty()
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        {
            bail!("invalid header name");
        }
        let value = value.trim();
        match name.to_ascii_lowercase().as_str() {
            "content-length" => {
                if saw_content_length {
                    bail!("duplicate Content-Length header");
                }
                saw_content_length = true;
                content_length = value.parse().context("invalid Content-Length")?;
            }
            "authorization" => {
                if authorization.is_some() {
                    bail!("duplicate Authorization header");
                }
                authorization = Some(value.to_owned());
            }
            "host" => saw_host = true,
            "transfer-encoding" => bail!("chunked requests are not supported"),
            _ => {}
        }
    }
    if !saw_host {
        bail!("HTTP/1.1 Host header is required");
    }
    if content_length > MAX_BODY_BYTES {
        bail!("request body exceeds {MAX_BODY_BYTES} bytes");
    }
    let body_start = header_end + 4;
    while bytes.len() < body_start + content_length {
        let read = stream.read(&mut buffer).await?;
        if read == 0 {
            bail!("connection closed before request body completed");
        }
        bytes.extend_from_slice(&buffer[..read]);
        if bytes.len() > MAX_REQUEST_BYTES {
            bail!("request exceeds {MAX_REQUEST_BYTES} bytes");
        }
    }
    Ok(HttpRequest {
        method,
        path,
        authorization,
        body: bytes[body_start..body_start + content_length].to_vec(),
    })
}

fn find_header_end(bytes: &[u8]) -> Option<usize> {
    bytes.windows(4).position(|window| window == b"\r\n\r\n")
}

async fn write_response(stream: &mut TcpStream, response: HttpResponse) -> Result<()> {
    let head = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\n\r\n",
        response.status,
        response.reason,
        response.body.len()
    );
    stream.write_all(head.as_bytes()).await?;
    stream.write_all(&response.body).await?;
    stream.shutdown().await?;
    Ok(())
}

async fn route(request: HttpRequest, state: AppState) -> HttpResponse {
    if request.method == "GET" && request.path == "/healthz" {
        return HttpResponse::empty(204, "No Content");
    }
    if !authorized(request.authorization.as_deref(), &state.token) {
        return HttpResponse::error(401, "Unauthorized", "missing or invalid bearer token");
    }
    if request.method == "POST" && request.path == "/v1/sessions" {
        return create_session(&request.body, state).await;
    }
    if let Some(id) = request.path.strip_prefix("/v1/sessions/") {
        return match request.method.as_str() {
            "GET" => get_session(id, state).await,
            "DELETE" => delete_session(id, state).await,
            _ => HttpResponse::error(405, "Method Not Allowed", "method not allowed"),
        };
    }
    HttpResponse::error(404, "Not Found", "route not found")
}

async fn create_session(body: &[u8], state: AppState) -> HttpResponse {
    let body: CreateBody = match serde_json::from_slice(body) {
        Ok(body) => body,
        Err(_) => return HttpResponse::error(400, "Bad Request", "invalid JSON request"),
    };
    let _create_guard = state.create_lock.lock().await;
    let ttl = body.ttl_minutes.unwrap_or(state.default_ttl_minutes);
    if body.sla_minutes == 0 || ttl == 0 || ttl > MAX_TTL_MINUTES {
        return HttpResponse::error(
            400,
            "Bad Request",
            format!("sla_minutes must be positive; ttl_minutes must be 1..={MAX_TTL_MINUTES}"),
        );
    }
    if ttl < body.sla_minutes {
        return HttpResponse::error(
            400,
            "Bad Request",
            "ttl_minutes must be at least sla_minutes",
        );
    }
    if let Err(error) = refresh_sessions(&state).await {
        return internal(error);
    }
    if state
        .store
        .all()
        .iter()
        .any(|stored| !stored.status.phase.is_terminal())
    {
        return HttpResponse::error(
            409,
            "Conflict",
            "the dedicated VM already has an active session",
        );
    }

    let scenarios = match scenario::discover_strict(&state.scenarios_dir) {
        Ok(scenarios) => scenarios,
        Err(error) => return internal(error.context("discovering scenario pack")),
    };
    let matching: Vec<_> = scenarios
        .iter()
        .filter(|scenario| scenario.meta.id == body.scenario)
        .collect();
    let Some(selected) = matching.first().copied() else {
        return HttpResponse::error(404, "Not Found", "scenario not found");
    };
    if matching.len() > 1 {
        return HttpResponse::error(
            409,
            "Conflict",
            "scenario ID is duplicated across installed packs",
        );
    }
    if let Some(fault) = body.fault.as_deref()
        && let Err(error) = selected.select_fault(Some(fault), 0)
    {
        return HttpResponse::error(400, "Bad Request", error.to_string());
    }
    let backend = state.backend.clone();
    let selected = selected.clone();
    let fault = body.fault.clone();
    let sla = body.sla_minutes;
    let created = match tokio::task::spawn_blocking(move || {
        backend.create(CreateSessionRequest {
            scenario: &selected,
            sla_minutes: sla,
            ttl_minutes: ttl,
            fault: fault.as_deref(),
        })
    })
    .await
    {
        Ok(Ok(created)) => created,
        Ok(Err(error)) => return internal(error),
        Err(error) => return internal(anyhow::anyhow!(error)),
    };
    let stored = StoredSession {
        status: HostedStatus {
            session_id: created.session.id,
            phase: HostedPhase::Ready,
            updated_at: Utc::now(),
            elapsed_secs: None,
            hints_used: None,
            message: None,
        },
        session: created.session.clone(),
    };
    if let Err(error) = state.store.put(stored) {
        let backend = state.backend.clone();
        let session = created.session.clone();
        if let Err(cleanup_error) = tokio::task::spawn_blocking(move || backend.destroy(&session))
            .await
            .unwrap_or_else(|join_error| Err(join_error.into()))
        {
            eprintln!("[replaybook] rollback cleanup failed: {cleanup_error:#}");
        }
        return internal(error);
    }
    HttpResponse::json(
        201,
        "Created",
        &CreateResponse {
            id: created.session.id,
            scenario: created.session.scenario_id.clone(),
            status: HostedPhase::Ready,
            expires_at: created.session.expires_at,
            ssh_destination: created.session.destination.clone(),
            ssh_port: created.session.ssh_port,
            private_key: created.private_key,
            connect: format!(
                "ssh -tt -i ./replaybook-{}.key -p {} {}",
                created.session.id, created.session.ssh_port, created.session.destination
            ),
        },
    )
}

async fn get_session(id: &str, state: AppState) -> HttpResponse {
    let id = match Uuid::parse_str(id) {
        Ok(id) => id,
        Err(_) => return HttpResponse::error(400, "Bad Request", "session ID must be a UUID"),
    };
    let Some(stored) = state.store.get(id) else {
        return HttpResponse::error(404, "Not Found", "session not found");
    };
    let backend = state.backend.clone();
    let session = stored.session.clone();
    let status = match tokio::task::spawn_blocking(move || backend.status(&session)).await {
        Ok(Ok(status)) => status,
        Ok(Err(error)) => return internal(error),
        Err(error) => return internal(anyhow::anyhow!(error)),
    };
    let updated = StoredSession {
        session: stored.session,
        status,
    };
    if let Err(error) = state.store.put(updated.clone()) {
        return internal(error);
    }
    HttpResponse::json(200, "OK", &session_response(&updated))
}

async fn delete_session(id: &str, state: AppState) -> HttpResponse {
    let id = match Uuid::parse_str(id) {
        Ok(id) => id,
        Err(_) => return HttpResponse::error(400, "Bad Request", "session ID must be a UUID"),
    };
    let _create_guard = state.create_lock.lock().await;
    let Some(stored) = state.store.get(id) else {
        return HttpResponse::error(404, "Not Found", "session not found");
    };
    if stored.status.phase == HostedPhase::Destroyed {
        return HttpResponse::empty(204, "No Content");
    }
    match destroy_stored(&state, stored).await {
        Ok(()) => HttpResponse::empty(204, "No Content"),
        Err(error) => internal(error),
    }
}

async fn refresh_sessions(state: &AppState) -> Result<()> {
    for stored in state.store.all() {
        if stored.status.phase == HostedPhase::Destroyed {
            continue;
        }
        if stored.session.expires_at <= Utc::now() {
            destroy_stored(state, stored).await?;
            continue;
        }
        if stored.status.phase.is_terminal() {
            continue;
        }
        let backend = state.backend.clone();
        let session = stored.session.clone();
        match tokio::task::spawn_blocking(move || backend.status(&session)).await {
            Ok(Ok(status)) => {
                state.store.put(StoredSession {
                    session: stored.session,
                    status,
                })?;
            }
            Ok(Err(error)) => return Err(error),
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

async fn expire_sessions(state: AppState) {
    let _create_guard = state.create_lock.lock().await;
    let now = Utc::now();
    for stored in state.store.all() {
        if stored.status.phase != HostedPhase::Destroyed
            && stored.session.expires_at <= now
            && let Err(error) = destroy_stored(&state, stored).await
        {
            eprintln!("[replaybook] expiry cleanup failed: {error:#}");
        }
    }
}

async fn destroy_stored(state: &AppState, stored: StoredSession) -> Result<()> {
    let backend = state.backend.clone();
    let session = stored.session.clone();
    tokio::task::spawn_blocking(move || backend.destroy(&session)).await??;
    state.store.put(StoredSession {
        session: stored.session.clone(),
        status: HostedStatus {
            session_id: stored.session.id,
            phase: HostedPhase::Destroyed,
            updated_at: Utc::now(),
            elapsed_secs: stored.status.elapsed_secs,
            hints_used: stored.status.hints_used,
            message: None,
        },
    })?;
    Ok(())
}

fn session_response(stored: &StoredSession) -> SessionResponse {
    SessionResponse {
        id: stored.session.id,
        scenario: stored.session.scenario_id.clone(),
        status: stored.status.phase.clone(),
        created_at: stored.session.created_at,
        expires_at: stored.session.expires_at,
        elapsed_secs: stored.status.elapsed_secs,
        hints_used: stored.status.hints_used,
        message: stored.status.message.clone(),
    }
}

fn authorized(supplied: Option<&str>, expected: &str) -> bool {
    let supplied = supplied
        .and_then(|value| value.strip_prefix("Bearer "))
        .unwrap_or_default();
    constant_time_eq(supplied.as_bytes(), expected.as_bytes())
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    let mut difference = left.len() ^ right.len();
    let length = left.len().max(right.len());
    for index in 0..length {
        difference |= usize::from(
            left.get(index).copied().unwrap_or(0) ^ right.get(index).copied().unwrap_or(0),
        );
    }
    difference == 0
}

fn internal(error: anyhow::Error) -> HttpResponse {
    eprintln!("[replaybook] control-plane error: {error:#}");
    HttpResponse::error(500, "Internal Server Error", "internal error")
}

fn validate_config(config: &ControlConfig) -> Result<()> {
    if config.token.len() < MIN_TOKEN_LEN {
        bail!("control-plane bearer token must be at least {MIN_TOKEN_LEN} characters");
    }
    if !config.scenarios_dir.is_dir() {
        bail!(
            "scenario directory {} does not exist",
            config.scenarios_dir.display()
        );
    }
    if config.default_ttl_minutes == 0 || config.default_ttl_minutes > MAX_TTL_MINUTES {
        bail!("default TTL must be 1..={MAX_TTL_MINUTES} minutes");
    }
    Ok(())
}

fn control_data_dir() -> PathBuf {
    dirs_next::data_local_dir()
        .unwrap_or_else(std::env::temp_dir)
        .join("replaybook/control")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bearer_comparison_handles_lengths_without_short_circuiting() {
        assert!(constant_time_eq(b"same", b"same"));
        assert!(!constant_time_eq(b"same", b"diff"));
        assert!(!constant_time_eq(b"short", b"much-longer"));
        assert!(authorized(Some("Bearer secret"), "secret"));
        assert!(!authorized(Some("secret"), "secret"));
    }

    #[test]
    fn parser_finds_header_boundary() {
        assert_eq!(
            find_header_end(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"),
            Some(23)
        );
        assert_eq!(find_header_end(b"incomplete"), None);
    }

    #[tokio::test]
    async fn parser_reads_a_complete_json_request() {
        let (mut client, mut server) = tokio::io::duplex(1024);
        client
            .write_all(
                b"POST /v1/sessions HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer secret\r\nContent-Length: 2\r\n\r\n{}",
            )
            .await
            .unwrap();
        drop(client);

        let request = read_request(&mut server).await.unwrap();
        assert_eq!(request.method, "POST");
        assert_eq!(request.path, "/v1/sessions");
        assert_eq!(request.authorization.as_deref(), Some("Bearer secret"));
        assert_eq!(request.body, b"{}");
    }

    #[tokio::test]
    async fn parser_rejects_ambiguous_request_framing() {
        let (mut client, mut server) = tokio::io::duplex(1024);
        client
            .write_all(
                b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nContent-Length: 1\r\n\r\n",
            )
            .await
            .unwrap();
        drop(client);

        assert!(read_request(&mut server).await.is_err());
    }

    #[test]
    fn store_round_trips_sessions() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("sessions.json");
        let store = ControlStore::open(path.clone()).unwrap();
        let id = Uuid::new_v4();
        let now = Utc::now();
        store
            .put(StoredSession {
                session: HostedSession {
                    id,
                    scenario_id: "001-test".into(),
                    destination: "replay@vm.example.com".into(),
                    ssh_port: 22,
                    remote_scenario: format!("/tmp/replaybook-hosted/{id}/scenario"),
                    private_key_path: temp.path().join("key"),
                    created_at: now,
                    expires_at: now + chrono::Duration::minutes(30),
                    sla_minutes: 15,
                    fault: None,
                },
                status: HostedStatus {
                    session_id: id,
                    phase: HostedPhase::Ready,
                    updated_at: now,
                    elapsed_secs: None,
                    hints_used: None,
                    message: None,
                },
            })
            .unwrap();
        let reopened = ControlStore::open(path).unwrap();
        assert_eq!(reopened.get(id).unwrap().session.scenario_id, "001-test");
    }

    #[test]
    fn response_does_not_expose_private_key_path_or_remote_path() {
        let json = serde_json::to_string(&SessionResponse {
            id: Uuid::new_v4(),
            scenario: "x".into(),
            status: HostedPhase::Ready,
            created_at: Utc::now(),
            expires_at: Utc::now(),
            elapsed_secs: None,
            hints_used: None,
            message: None,
        })
        .unwrap();
        assert!(!json.contains("private_key"));
        assert!(!json.contains("remote_scenario"));
    }
}
