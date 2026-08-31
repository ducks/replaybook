use std::{
    collections::HashMap,
    net::SocketAddr,
    path::{Path as FsPath, PathBuf},
    sync::Arc,
    time::{Duration, Instant},
};

use axum::{
    Json, Router,
    extract::{Path, State},
    http::{HeaderMap, StatusCode, header},
    response::IntoResponse,
    routing::{get, post},
};
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use tokio::sync::RwLock;
use uuid::Uuid;

type SharedState = Arc<RwLock<AppState>>;

#[derive(Clone)]
struct AppState {
    runs: HashMap<Uuid, Run>,
    workers: HashMap<Uuid, Worker>,
    scenarios: Vec<Scenario>,
    state_file: PathBuf,
    run_limiter: RateLimiter,
    enrollment_limiter: RateLimiter,
}

#[derive(Clone)]
struct RateLimiter {
    windows: HashMap<String, RateWindow>,
    limit: u32,
    window: Duration,
}

#[derive(Clone)]
struct RateWindow {
    started: Instant,
    requests: u32,
}

impl RateLimiter {
    fn from_env(name: &str, default_limit: u32) -> Self {
        let limit = std::env::var(name)
            .ok()
            .and_then(|value| value.parse().ok())
            .filter(|limit| *limit > 0)
            .unwrap_or(default_limit);
        Self {
            windows: HashMap::new(),
            limit,
            window: Duration::from_secs(60),
        }
    }

    fn check(&mut self, key: String) -> Result<(), u64> {
        let now = Instant::now();
        let stale_after = self.window.saturating_add(self.window);
        self.windows
            .retain(|_, window| now.duration_since(window.started) < stale_after);
        let window = self.windows.entry(key).or_insert(RateWindow {
            started: now,
            requests: 0,
        });
        if now.duration_since(window.started) >= self.window {
            window.started = now;
            window.requests = 0;
        }
        if window.requests >= self.limit {
            let retry_after = self
                .window
                .saturating_sub(now.duration_since(window.started))
                .as_secs()
                .max(1);
            return Err(retry_after);
        }
        window.requests += 1;
        Ok(())
    }
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            runs: HashMap::new(),
            workers: HashMap::new(),
            scenarios: default_scenarios(),
            state_file: PathBuf::new(),
            run_limiter: RateLimiter::from_env("AGENT_ZEN_GARDEN_RUNS_PER_MINUTE", 30),
            enrollment_limiter: RateLimiter::from_env("AGENT_ZEN_GARDEN_ENROLLMENTS_PER_MINUTE", 5),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Scenario {
    id: String,
    version: String,
    title: String,
    description: String,
    modality: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum RunState {
    Queued,
    Running,
    Verifying,
    Passed,
    Failed,
    Expired,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum WorkerState {
    Ready,
    Busy,
    Draining,
    Offline,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Event {
    sequence: u64,
    kind: String,
    message: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Run {
    id: Uuid,
    scenario_id: String,
    scenario_version: String,
    provider: Option<String>,
    model: Option<String>,
    harness: Option<String>,
    reasoning: Option<String>,
    state: RunState,
    token: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    worker_id: Option<Uuid>,
    events: Vec<Event>,
}

#[derive(Clone, Debug, Serialize)]
struct PublicRun {
    id: Uuid,
    scenario_id: String,
    scenario_version: String,
    provider: Option<String>,
    model: Option<String>,
    harness: Option<String>,
    reasoning: Option<String>,
    state: RunState,
    #[serde(skip_serializing_if = "Option::is_none")]
    worker_id: Option<Uuid>,
    events: Vec<Event>,
}

impl Run {
    fn public(&self) -> PublicRun {
        PublicRun {
            id: self.id,
            scenario_id: self.scenario_id.clone(),
            scenario_version: self.scenario_version.clone(),
            provider: self.provider.clone(),
            model: self.model.clone(),
            harness: self.harness.clone(),
            reasoning: self.reasoning.clone(),
            state: self.state.clone(),
            worker_id: self.worker_id,
            events: self.events.clone(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Worker {
    id: Uuid,
    name: String,
    #[allow(dead_code)]
    endpoint: String,
    token: String,
    state: WorkerState,
    current_run: Option<Uuid>,
}

#[derive(Debug, Deserialize)]
struct StartRunRequest {
    scenario_id: String,
    #[serde(default = "default_scenario_version")]
    scenario_version: String,
    provider: Option<String>,
    model: Option<String>,
    harness: Option<String>,
    reasoning: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RegisterWorkerRequest {
    name: String,
    endpoint: String,
    join_token: String,
}

#[derive(Debug, Serialize)]
struct StartRunResponse {
    run: PublicRun,
    token: String,
    endpoint: String,
}

#[derive(Debug, Serialize)]
struct RegisterWorkerResponse {
    worker_id: Uuid,
    token: String,
    state: WorkerState,
}

#[derive(Debug, Serialize)]
struct AssignmentResponse {
    worker_id: Uuid,
    run: Option<RunAssignment>,
}

#[derive(Debug, Serialize)]
struct RunAssignment {
    run_id: Uuid,
    scenario_id: String,
    scenario_version: String,
    provider: Option<String>,
    model: Option<String>,
    harness: Option<String>,
    reasoning: Option<String>,
}

#[derive(Debug, Deserialize)]
struct WorkerEventRequest {
    kind: String,
    message: String,
}

#[derive(Debug, Deserialize)]
struct WorkerResultRequest {
    passed: bool,
    failure_category: Option<String>,
}

#[derive(Debug, Serialize)]
struct EventsResponse {
    run_id: Uuid,
    events: Vec<Event>,
}

#[derive(Debug, Serialize)]
struct ErrorResponse {
    error: String,
}

fn default_scenario_version() -> String {
    "v1".to_string()
}

fn default_scenarios() -> Vec<Scenario> {
    [
        (
            "001-nginx-502-host",
            "Nginx 502 host",
            "Diagnose an upstream host failure behind an Nginx 502.",
            "text",
        ),
        (
            "013-sidekiq-wrong-redis",
            "Sidekiq wrong Redis",
            "Trace a worker fleet connected to the wrong Redis instance.",
            "text",
        ),
        (
            "014-missing-rails-migration",
            "Missing Rails migration",
            "Recover a Rails deployment blocked by an unapplied migration.",
            "text",
        ),
        (
            "015-sidekiq-poison-pill",
            "Sidekiq poison pill",
            "Isolate a job that repeatedly crashes a Sidekiq worker.",
            "text",
        ),
        (
            "016-rails-pool-exhaustion",
            "Rails pool exhaustion",
            "Find and relieve database connection pool exhaustion.",
            "text",
        ),
        (
            "017-partial-rails-rollout",
            "Partial Rails rollout",
            "Repair a rollout where only part of the Rails fleet updated.",
            "text",
        ),
        (
            "018-node-event-loop-blocking",
            "Node event-loop blocking",
            "Diagnose a blocking operation starving a Node.js event loop.",
            "text",
        ),
        (
            "019-rust-fd-leak",
            "Rust file-descriptor leak",
            "Find and stop a Rust service leaking file descriptors.",
            "text",
        ),
        (
            "020-python-gunicorn-saturation",
            "Python Gunicorn saturation",
            "Recover a saturated Gunicorn application fleet.",
            "text",
        ),
        (
            "021-discourse-shared-uploads",
            "Discourse shared uploads",
            "Repair shared upload storage for a Discourse deployment.",
            "text",
        ),
        (
            "022-discourse-multisite-migration",
            "Discourse multisite migration",
            "Complete a migration across a multisite Discourse install.",
            "text",
        ),
        (
            "023-auth-secret-rollout",
            "Auth secret rollout",
            "Safely roll out a changed authentication secret.",
            "text",
        ),
        (
            "024-discourse-interrupted-deploy",
            "Discourse interrupted deploy",
            "Recover a Discourse deployment interrupted mid-release.",
            "text",
        ),
        (
            "026-discourse-plugin-boot-loop",
            "Discourse plugin boot loop",
            "Stop a plugin from repeatedly preventing Discourse startup.",
            "text",
        ),
        (
            "027-partial-service-rollout",
            "Partial service rollout",
            "Repair a service rollout that converged on only part of the fleet.",
            "text",
        ),
        (
            "028-nix-store-disk-pressure",
            "Nix store disk pressure",
            "Recover writable capacity under Nix store disk pressure.",
            "text",
        ),
        (
            "029-visual-topology-drift",
            "Visual topology drift",
            "Use a service diagram to repair a topology mismatch.",
            "visual",
        ),
        (
            "030-visual-metrics-regression",
            "Visual metrics regression",
            "Correlate noisy dashboard signals and repair the underlying issue.",
            "visual",
        ),
        (
            "031-visual-deployment-timeline",
            "Visual deployment timeline",
            "Use a deployment timeline to identify and repair a release regression.",
            "visual",
        ),
    ]
    .into_iter()
    .map(|(id, title, description, modality)| Scenario {
        id: id.to_string(),
        version: "v1".to_string(),
        title: title.to_string(),
        description: description.to_string(),
        modality: modality.to_string(),
    })
    .collect()
}

fn load_scenarios() -> Vec<Scenario> {
    let Some(path) = std::env::var_os("AGENT_ZEN_GARDEN_SCENARIOS_FILE").map(PathBuf::from) else {
        return default_scenarios();
    };
    match std::fs::read_to_string(&path)
        .map_err(|error| error.to_string())
        .and_then(|contents| {
            serde_json::from_str::<Vec<Scenario>>(&contents).map_err(|error| error.to_string())
        }) {
        Ok(scenarios) if !scenarios.is_empty() => scenarios,
        Ok(_) => {
            eprintln!(
                "scenario catalog {} is empty; using built-in catalog",
                path.display()
            );
            default_scenarios()
        }
        Err(error) => {
            eprintln!(
                "could not load scenario catalog {}: {error}; using built-in catalog",
                path.display()
            );
            default_scenarios()
        }
    }
}

#[tokio::main]
async fn main() {
    let state_file = state_file_path();
    let mut initial_state = load_state(&state_file).expect("load Agent Zen Garden state");
    initial_state.scenarios = load_scenarios();
    initial_state.state_file = state_file;
    recover_state(&mut initial_state);
    persist_state(&initial_state.state_file, &initial_state).expect("persist recovered state");
    let state: SharedState = Arc::new(RwLock::new(initial_state));
    let app = Router::new()
        .route("/healthz", get(healthz))
        .route("/v1/scenarios", get(list_scenarios))
        .route("/v1/runs", post(start_run))
        .route("/v1/runs/{run_id}", get(get_run))
        .route("/v1/runs/{run_id}/events", get(get_events))
        .route("/v1/runs/{run_id}/submit", post(submit_run))
        .route("/v1/workers/register", post(register_worker))
        .route("/v1/workers/{worker_id}/assignment", get(worker_assignment))
        .route("/v1/workers/{worker_id}/events", post(worker_event))
        .route("/v1/workers/{worker_id}/heartbeat", post(worker_heartbeat))
        .route("/v1/workers/{worker_id}/result", post(worker_result))
        .with_state(state);

    let address: SocketAddr = std::env::var("AGENT_ZEN_GARDEN_BIND")
        .unwrap_or_else(|_| "127.0.0.1:8080".to_string())
        .parse()
        .expect("AGENT_ZEN_GARDEN_BIND must be a socket address");
    let listener = tokio::net::TcpListener::bind(address)
        .await
        .expect("bind Agent Zen Garden listener");
    println!("agent-zen-garden listening on http://{address}");
    axum::serve(listener, app)
        .await
        .expect("serve Agent Zen Garden");
}

fn state_file_path() -> PathBuf {
    std::env::var_os("AGENT_ZEN_GARDEN_STATE_FILE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("agent-zen-garden-state.json"))
}

fn load_state(path: &FsPath) -> Result<AppState, String> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("create state directory {}: {error}", parent.display()))?;
    }
    let connection =
        Connection::open(path).map_err(|error| format!("open {}: {error}", path.display()))?;
    initialize_schema(&connection)?;
    let mut state = AppState::default();

    let mut runs = connection
        .prepare(
            "SELECT id, scenario_id, scenario_version, provider, model, harness, reasoning, state, token, worker_id, events_json FROM runs",
        )
        .map_err(|error| format!("prepare runs query: {error}"))?;
    let run_rows = runs
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, Option<String>>(3)?,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, Option<String>>(5)?,
                row.get::<_, Option<String>>(6)?,
                row.get::<_, String>(7)?,
                row.get::<_, String>(8)?,
                row.get::<_, Option<String>>(9)?,
                row.get::<_, String>(10)?,
            ))
        })
        .map_err(|error| format!("query runs: {error}"))?;
    for row in run_rows {
        let (
            id,
            scenario_id,
            scenario_version,
            provider,
            model,
            harness,
            reasoning,
            run_state,
            token,
            worker_id,
            events,
        ) = row.map_err(|error| format!("read run: {error}"))?;
        let id = id
            .parse()
            .map_err(|error| format!("parse run id: {error}"))?;
        let worker_id = worker_id
            .map(|id| {
                id.parse()
                    .map_err(|error| format!("parse worker id: {error}"))
            })
            .transpose()?;
        state.runs.insert(
            id,
            Run {
                id,
                scenario_id,
                scenario_version,
                provider,
                model,
                harness,
                reasoning,
                state: parse_json(&run_state, "run state")?,
                token,
                worker_id,
                events: parse_json(&events, "run events")?,
            },
        );
    }

    let mut workers = connection
        .prepare("SELECT id, name, endpoint, token, state, current_run FROM workers")
        .map_err(|error| format!("prepare workers query: {error}"))?;
    let worker_rows = workers
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, Option<String>>(5)?,
            ))
        })
        .map_err(|error| format!("query workers: {error}"))?;
    for row in worker_rows {
        let (id, name, endpoint, token, worker_state, current_run) =
            row.map_err(|error| format!("read worker: {error}"))?;
        let id = id
            .parse()
            .map_err(|error| format!("parse worker id: {error}"))?;
        let current_run = current_run
            .map(|id| id.parse().map_err(|error| format!("parse run id: {error}")))
            .transpose()?;
        state.workers.insert(
            id,
            Worker {
                id,
                name,
                endpoint,
                token,
                state: parse_json(&worker_state, "worker state")?,
                current_run,
            },
        );
    }
    Ok(state)
}

fn parse_json<T: DeserializeOwned>(value: &str, label: &str) -> Result<T, String> {
    serde_json::from_str(value).map_err(|error| format!("parse {label}: {error}"))
}

fn initialize_schema(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "PRAGMA journal_mode = WAL;
             CREATE TABLE IF NOT EXISTS runs (
                 id TEXT PRIMARY KEY,
                 scenario_id TEXT NOT NULL,
                 scenario_version TEXT NOT NULL,
                 provider TEXT,
                 model TEXT,
                 harness TEXT,
                 reasoning TEXT,
                 state TEXT NOT NULL,
                 token TEXT NOT NULL,
                 worker_id TEXT,
                 events_json TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS workers (
                 id TEXT PRIMARY KEY,
                 name TEXT NOT NULL,
                 endpoint TEXT NOT NULL,
                 token TEXT NOT NULL,
                 state TEXT NOT NULL,
                 current_run TEXT
             );",
        )
        .map_err(|error| format!("initialize database schema: {error}"))
}

fn recover_state(state: &mut AppState) {
    for worker in state.workers.values_mut() {
        worker.state = WorkerState::Offline;
        if let Some(run_id) = worker.current_run.take()
            && let Some(run) = state.runs.get_mut(&run_id)
        {
            run.worker_id = None;
            if matches!(run.state, RunState::Running | RunState::Verifying) {
                run.state = RunState::Queued;
                run.events.push(Event {
                    sequence: run.events.len() as u64 + 1,
                    kind: "run_requeued".to_string(),
                    message: "control plane restarted; run returned to the queue".to_string(),
                });
            }
        }
    }
}

fn persist_state(path: &FsPath, state: &AppState) -> Result<(), String> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("create state directory {}: {error}", parent.display()))?;
    }
    let mut connection =
        Connection::open(path).map_err(|error| format!("open {}: {error}", path.display()))?;
    initialize_schema(&connection)?;
    let transaction = connection
        .transaction()
        .map_err(|error| format!("begin state transaction: {error}"))?;
    transaction
        .execute("DELETE FROM runs", [])
        .map_err(|error| format!("clear runs: {error}"))?;
    transaction
        .execute("DELETE FROM workers", [])
        .map_err(|error| format!("clear workers: {error}"))?;
    for run in state.runs.values() {
        transaction
            .execute(
                "INSERT INTO runs (id, scenario_id, scenario_version, provider, model, harness, reasoning, state, token, worker_id, events_json) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                params![
                    run.id.to_string(),
                    run.scenario_id,
                    run.scenario_version,
                    run.provider,
                    run.model,
                    run.harness,
                    run.reasoning,
                    serde_json::to_string(&run.state).map_err(|error| format!("serialize run state: {error}"))?,
                    run.token,
                    run.worker_id.map(|id| id.to_string()),
                    serde_json::to_string(&run.events).map_err(|error| format!("serialize run events: {error}"))?,
                ],
            )
            .map_err(|error| format!("insert run {}: {error}", run.id))?;
    }
    for worker in state.workers.values() {
        transaction
            .execute(
                "INSERT INTO workers (id, name, endpoint, token, state, current_run) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    worker.id.to_string(),
                    worker.name,
                    worker.endpoint,
                    worker.token,
                    serde_json::to_string(&worker.state).map_err(|error| format!("serialize worker state: {error}"))?,
                    worker.current_run.map(|id| id.to_string()),
                ],
            )
            .map_err(|error| format!("insert worker {}: {error}", worker.id))?;
    }
    transaction
        .commit()
        .map_err(|error| format!("commit state transaction: {error}"))
}

async fn healthz() -> &'static str {
    "ok\n"
}

async fn list_scenarios(State(state): State<SharedState>) -> impl IntoResponse {
    let state = state.read().await;
    Json(state.scenarios.clone())
}

fn client_key(headers: &HeaderMap) -> String {
    headers
        .get("x-forwarded-for")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(',').next())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .or_else(|| {
            headers
                .get("x-real-ip")
                .and_then(|value| value.to_str().ok())
                .map(str::trim)
                .filter(|value| !value.is_empty())
        })
        .unwrap_or("unknown")
        .to_string()
}

fn rate_limited(retry_after: u64) -> axum::response::Response {
    let mut response = (
        StatusCode::TOO_MANY_REQUESTS,
        Json(ErrorResponse {
            error: "rate limit exceeded".to_string(),
        }),
    )
        .into_response();
    if let Ok(value) = retry_after.to_string().parse() {
        response.headers_mut().insert(header::RETRY_AFTER, value);
    }
    response
}

async fn start_run(
    State(state): State<SharedState>,
    headers: HeaderMap,
    Json(request): Json<StartRunRequest>,
) -> impl IntoResponse {
    let client = client_key(&headers);
    let mut state_guard = state.write().await;
    if let Err(retry_after) = state_guard.run_limiter.check(client) {
        return rate_limited(retry_after);
    }
    drop(state_guard);

    if request.scenario_id.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                error: "scenario_id is required".to_string(),
            }),
        )
            .into_response();
    }

    {
        let state = state.read().await;
        let known_scenario = state.scenarios.iter().any(|scenario| {
            scenario.id == request.scenario_id && scenario.version == request.scenario_version
        });
        if !known_scenario {
            return (
                StatusCode::BAD_REQUEST,
                Json(ErrorResponse {
                    error: format!(
                        "unknown scenario or version: {}@{}",
                        request.scenario_id, request.scenario_version
                    ),
                }),
            )
                .into_response();
        }
    }

    let id = Uuid::new_v4();
    let token = Uuid::new_v4().to_string();
    let mut run = Run {
        id,
        scenario_id: request.scenario_id,
        scenario_version: request.scenario_version,
        provider: request.provider,
        model: request.model,
        harness: request.harness,
        reasoning: request.reasoning,
        state: RunState::Queued,
        token: token.clone(),
        worker_id: None,
        events: vec![Event {
            sequence: 1,
            kind: "run_queued".to_string(),
            message: "run accepted; worker assignment pending".to_string(),
        }],
    };

    let mut state = state.write().await;
    if let Some(worker) = state
        .workers
        .values_mut()
        .find(|worker| matches!(worker.state, WorkerState::Ready))
    {
        worker.state = WorkerState::Busy;
        worker.current_run = Some(id);
        run.worker_id = Some(worker.id);
        run.state = RunState::Running;
        run.events.push(Event {
            sequence: 2,
            kind: "worker_assigned".to_string(),
            message: format!("assigned to warm worker {}", worker.name),
        });
    }

    let response_run = run.clone();
    state.runs.insert(id, run);
    let state_file = state.state_file.clone();
    if let Err(error) = persist_state(&state_file, &state) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse { error }),
        )
            .into_response();
    }

    (
        StatusCode::ACCEPTED,
        Json(StartRunResponse {
            run: response_run.public(),
            token,
            endpoint: format!("/v1/runs/{id}"),
        }),
    )
        .into_response()
}

async fn get_run(
    State(state): State<SharedState>,
    Path(run_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let state = state.read().await;
    let Some(run) = state.runs.get(&run_id) else {
        return not_found();
    };
    if !authorized(&headers, &run.token) {
        return unauthorized();
    }
    Json(run.public()).into_response()
}

async fn get_events(
    State(state): State<SharedState>,
    Path(run_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let state = state.read().await;
    let Some(run) = state.runs.get(&run_id) else {
        return not_found();
    };
    if !authorized(&headers, &run.token) {
        return unauthorized();
    }
    Json(EventsResponse {
        run_id,
        events: run.events.clone(),
    })
    .into_response()
}

async fn submit_run(
    State(state): State<SharedState>,
    Path(run_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let mut state = state.write().await;
    let response_run = {
        let Some(run) = state.runs.get_mut(&run_id) else {
            return not_found();
        };
        if !authorized(&headers, &run.token) {
            return unauthorized();
        }
        run.state = RunState::Verifying;
        let sequence = run.events.len() as u64 + 1;
        run.events.push(Event {
            sequence,
            kind: "verification_requested".to_string(),
            message: "submission accepted; evaluator pending".to_string(),
        });
        run.clone()
    };
    let state_file = state.state_file.clone();
    if let Err(error) = persist_state(&state_file, &state) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse { error }),
        )
            .into_response();
    }
    Json(response_run.public()).into_response()
}

async fn register_worker(
    State(state): State<SharedState>,
    headers: HeaderMap,
    Json(request): Json<RegisterWorkerRequest>,
) -> impl IntoResponse {
    let client = client_key(&headers);
    if let Err(retry_after) = state.write().await.enrollment_limiter.check(client) {
        return rate_limited(retry_after);
    }

    if request.name.trim().is_empty() || request.endpoint.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                error: "name and endpoint are required".to_string(),
            }),
        )
            .into_response();
    }

    let Some(expected_join_token) = std::env::var("AGENT_ZEN_GARDEN_JOIN_TOKEN")
        .ok()
        .filter(|token| !token.trim().is_empty())
    else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(ErrorResponse {
                error: "worker enrollment is not configured".to_string(),
            }),
        )
            .into_response();
    };
    if request.join_token != expected_join_token {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ErrorResponse {
                error: "invalid worker join token".to_string(),
            }),
        )
            .into_response();
    }

    let id = Uuid::new_v4();
    let token = Uuid::new_v4().to_string();
    let worker = Worker {
        id,
        name: request.name,
        endpoint: request.endpoint,
        token: token.clone(),
        state: WorkerState::Ready,
        current_run: None,
    };
    let worker_state = worker.state.clone();
    let mut state = state.write().await;
    state.workers.insert(id, worker);
    let state_file = state.state_file.clone();
    if let Err(error) = persist_state(&state_file, &state) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse { error }),
        )
            .into_response();
    }

    (
        StatusCode::CREATED,
        Json(RegisterWorkerResponse {
            worker_id: id,
            token,
            state: worker_state,
        }),
    )
        .into_response()
}

async fn worker_heartbeat(
    State(state): State<SharedState>,
    Path(worker_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let mut state = state.write().await;
    let worker_state = {
        let Some(worker) = state.workers.get_mut(&worker_id) else {
            return not_found();
        };
        if !authorized(&headers, &worker.token) {
            return unauthorized();
        }
        if matches!(worker.state, WorkerState::Offline) {
            worker.state = WorkerState::Ready;
        }
        worker.state.clone()
    };
    let state_file = state.state_file.clone();
    if let Err(error) = persist_state(&state_file, &state) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse { error }),
        )
            .into_response();
    }
    Json(serde_json::json!({"worker_id": worker_id, "state": worker_state})).into_response()
}

async fn worker_assignment(
    State(state): State<SharedState>,
    Path(worker_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let state = state.read().await;
    let Some(worker) = state.workers.get(&worker_id) else {
        return not_found();
    };
    if !authorized(&headers, &worker.token) {
        return unauthorized();
    }

    let run = worker.current_run.and_then(|run_id| {
        state.runs.get(&run_id).map(|run| RunAssignment {
            run_id,
            scenario_id: run.scenario_id.clone(),
            scenario_version: run.scenario_version.clone(),
            provider: run.provider.clone(),
            model: run.model.clone(),
            harness: run.harness.clone(),
            reasoning: run.reasoning.clone(),
        })
    });
    Json(AssignmentResponse { worker_id, run }).into_response()
}

async fn worker_event(
    State(state): State<SharedState>,
    Path(worker_id): Path<Uuid>,
    headers: HeaderMap,
    Json(event): Json<WorkerEventRequest>,
) -> impl IntoResponse {
    if event.kind.trim().is_empty() || event.message.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                error: "kind and message are required".to_string(),
            }),
        )
            .into_response();
    }

    let mut state = state.write().await;
    let Some(worker) = state.workers.get(&worker_id) else {
        return not_found();
    };
    if !authorized(&headers, &worker.token) {
        return unauthorized();
    }
    let Some(run_id) = worker.current_run else {
        return (
            StatusCode::CONFLICT,
            Json(ErrorResponse {
                error: "worker has no assigned run".to_string(),
            }),
        )
            .into_response();
    };
    let Some(run) = state.runs.get_mut(&run_id) else {
        return not_found();
    };
    let sequence = run.events.len() as u64 + 1;
    run.events.push(Event {
        sequence,
        kind: event.kind,
        message: event.message,
    });
    let state_file = state.state_file.clone();
    if let Err(error) = persist_state(&state_file, &state) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse { error }),
        )
            .into_response();
    }
    Json(serde_json::json!({"run_id": run_id, "sequence": sequence})).into_response()
}

async fn worker_result(
    State(state): State<SharedState>,
    Path(worker_id): Path<Uuid>,
    headers: HeaderMap,
    Json(result): Json<WorkerResultRequest>,
) -> impl IntoResponse {
    let mut state = state.write().await;
    let Some(worker) = state.workers.get_mut(&worker_id) else {
        return not_found();
    };
    if !authorized(&headers, &worker.token) {
        return unauthorized();
    }
    let Some(run_id) = worker.current_run.take() else {
        return (
            StatusCode::CONFLICT,
            Json(ErrorResponse {
                error: "worker has no assigned run".to_string(),
            }),
        )
            .into_response();
    };
    worker.state = WorkerState::Ready;

    let response_run = {
        let Some(run) = state.runs.get_mut(&run_id) else {
            return not_found();
        };
        run.state = if result.passed {
            RunState::Passed
        } else {
            RunState::Failed
        };
        let sequence = run.events.len() as u64 + 1;
        let message = result
            .failure_category
            .map(|category| format!("worker reported failure: {category}"))
            .unwrap_or_else(|| "worker reported verification result".to_string());
        run.events.push(Event {
            sequence,
            kind: "verification_result".to_string(),
            message,
        });
        run.clone()
    };
    let state_file = state.state_file.clone();
    if let Err(error) = persist_state(&state_file, &state) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse { error }),
        )
            .into_response();
    }
    Json(response_run.public()).into_response()
}

fn authorized(headers: &HeaderMap, expected: &str) -> bool {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        == Some(expected)
}

fn not_found() -> axum::response::Response {
    (
        StatusCode::NOT_FOUND,
        Json(ErrorResponse {
            error: "resource not found".to_string(),
        }),
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn state_round_trips_through_disk() {
        let path = std::env::temp_dir().join(format!("agent-zen-garden-{}.json", Uuid::new_v4()));
        let mut state = AppState::default();
        let run_id = Uuid::new_v4();
        state.runs.insert(
            run_id,
            Run {
                id: run_id,
                scenario_id: "scenario".to_string(),
                scenario_version: "v1".to_string(),
                provider: Some("provider".to_string()),
                model: Some("model".to_string()),
                harness: Some("harness".to_string()),
                reasoning: Some("high".to_string()),
                state: RunState::Queued,
                token: "run-token".to_string(),
                worker_id: None,
                events: vec![],
            },
        );

        persist_state(&path, &state).expect("persist state");
        let loaded = load_state(&path).expect("load state");
        assert_eq!(
            loaded.runs.get(&run_id).map(|run| &run.scenario_id),
            Some(&"scenario".to_string())
        );
        std::fs::remove_file(path).expect("remove temporary state");
    }

    #[test]
    fn restart_requeues_runs_assigned_to_workers() {
        let run_id = Uuid::new_v4();
        let worker_id = Uuid::new_v4();
        let mut state = AppState::default();
        state.runs.insert(
            run_id,
            Run {
                id: run_id,
                scenario_id: "scenario".to_string(),
                scenario_version: "v1".to_string(),
                provider: None,
                model: None,
                harness: None,
                reasoning: None,
                state: RunState::Running,
                token: "run-token".to_string(),
                worker_id: Some(worker_id),
                events: vec![],
            },
        );
        state.workers.insert(
            worker_id,
            Worker {
                id: worker_id,
                name: "worker".to_string(),
                endpoint: "endpoint".to_string(),
                token: "worker-token".to_string(),
                state: WorkerState::Busy,
                current_run: Some(run_id),
            },
        );

        recover_state(&mut state);
        assert!(matches!(state.runs[&run_id].state, RunState::Queued));
        assert_eq!(state.runs[&run_id].worker_id, None);
        assert!(matches!(
            state.workers[&worker_id].state,
            WorkerState::Offline
        ));
        assert_eq!(state.workers[&worker_id].current_run, None);
    }

    #[test]
    fn built_in_catalog_covers_text_and_visual_scenarios() {
        let scenarios = default_scenarios();
        assert!(scenarios.iter().any(|scenario| scenario.modality == "text"));
        assert!(
            scenarios
                .iter()
                .any(|scenario| scenario.modality == "visual")
        );
        assert!(
            scenarios
                .iter()
                .any(|scenario| scenario.id == "001-nginx-502-host")
        );
        assert!(
            scenarios
                .iter()
                .any(|scenario| scenario.id == "031-visual-deployment-timeline")
        );
    }
}

fn unauthorized() -> axum::response::Response {
    (
        StatusCode::UNAUTHORIZED,
        Json(ErrorResponse {
            error: "bearer token required".to_string(),
        }),
    )
        .into_response()
}

#[cfg(test)]
mod rate_limit_tests {
    use super::*;

    #[test]
    fn limiter_rejects_after_configured_window_capacity() {
        let mut limiter = RateLimiter {
            windows: HashMap::new(),
            limit: 2,
            window: Duration::from_secs(60),
        };
        assert!(limiter.check("client".to_string()).is_ok());
        assert!(limiter.check("client".to_string()).is_ok());
        let retry_after = limiter
            .check("client".to_string())
            .expect_err("limit should apply");
        assert!(retry_after >= 1);
        assert!(limiter.check("other-client".to_string()).is_ok());
    }

    #[test]
    fn forwarded_client_key_uses_first_proxy_address() {
        let mut headers = HeaderMap::new();
        headers.insert(
            "x-forwarded-for",
            "203.0.113.10, 127.0.0.1".parse().unwrap(),
        );
        assert_eq!(client_key(&headers), "203.0.113.10");
    }
}
