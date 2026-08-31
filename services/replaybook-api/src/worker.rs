use std::time::Duration;

use clap::Parser;
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use tokio::{process::Command, time::sleep};
use uuid::Uuid;

#[derive(Debug, Parser)]
#[command(
    name = "replaybook-worker",
    about = "Run assigned Replaybook scenarios on a warm worker"
)]
struct Args {
    /// Base URL of the control plane, for example https://zen.example.com
    #[arg(long, env = "AGENT_ZEN_GARDEN_CONTROL_PLANE_URL")]
    control_plane_url: String,

    #[arg(long, env = "AGENT_ZEN_GARDEN_WORKER_NAME")]
    name: String,

    /// Address advertised for operator visibility; it is not used for callbacks.
    #[arg(long, env = "AGENT_ZEN_GARDEN_WORKER_ENDPOINT")]
    endpoint: String,

    #[arg(long, env = "AGENT_ZEN_GARDEN_JOIN_TOKEN")]
    join_token: String,

    /// Fixed, worker-local executor. The control plane never supplies this path.
    #[arg(long, env = "AGENT_ZEN_GARDEN_EXECUTOR")]
    executor: String,

    #[arg(long, default_value_t = 2, env = "AGENT_ZEN_GARDEN_POLL_INTERVAL_SECS")]
    poll_interval_secs: u64,
}

#[derive(Debug, Serialize)]
struct RegisterRequest<'a> {
    name: &'a str,
    endpoint: &'a str,
    join_token: &'a str,
}

#[derive(Debug, Deserialize)]
struct RegisterResponse {
    worker_id: Uuid,
    token: String,
}

#[derive(Debug, Deserialize)]
struct AssignmentResponse {
    run: Option<RunAssignment>,
}

#[derive(Debug, Deserialize)]
struct RunAssignment {
    run_id: Uuid,
    scenario_id: String,
    scenario_version: String,
    provider: Option<String>,
    model: Option<String>,
    harness: Option<String>,
    reasoning: Option<String>,
}

#[derive(Debug, Serialize)]
struct EventRequest<'a> {
    kind: &'a str,
    message: &'a str,
}

#[derive(Debug, Serialize)]
struct ResultRequest {
    passed: bool,
    failure_category: Option<String>,
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    let client = Client::new();
    let base_url = args.control_plane_url.trim_end_matches('/').to_string();

    loop {
        match register(&client, &base_url, &args).await {
            Ok(registration) => {
                eprintln!(
                    "registered worker {} as {}",
                    registration.worker_id, args.name
                );
                if let Err(error) = run_loop(&client, &base_url, &args, registration).await {
                    eprintln!("worker loop stopped: {error}");
                }
            }
            Err(error) => eprintln!("worker enrollment failed: {error}"),
        }
        sleep(Duration::from_secs(5)).await;
    }
}

async fn register(
    client: &Client,
    base_url: &str,
    args: &Args,
) -> Result<RegisterResponse, String> {
    let response = client
        .post(format!("{base_url}/v1/workers/register"))
        .json(&RegisterRequest {
            name: &args.name,
            endpoint: &args.endpoint,
            join_token: &args.join_token,
        })
        .send()
        .await
        .map_err(|error| error.to_string())?;
    response
        .error_for_status()
        .map_err(|error| error.to_string())?
        .json::<RegisterResponse>()
        .await
        .map_err(|error| error.to_string())
}

async fn run_loop(
    client: &Client,
    base_url: &str,
    args: &Args,
    registration: RegisterResponse,
) -> Result<(), String> {
    let auth = format!("Bearer {}", registration.token);
    let poll_delay = Duration::from_secs(args.poll_interval_secs.max(1));

    loop {
        let heartbeat = client
            .post(format!(
                "{base_url}/v1/workers/{}/heartbeat",
                registration.worker_id
            ))
            .header("authorization", &auth)
            .send()
            .await
            .map_err(|error| error.to_string())?;
        if heartbeat.status() == StatusCode::UNAUTHORIZED {
            return Err("worker token rejected; re-enrolling".to_string());
        }
        heartbeat
            .error_for_status()
            .map_err(|error| error.to_string())?;

        let assignment = client
            .get(format!(
                "{base_url}/v1/workers/{}/assignment",
                registration.worker_id
            ))
            .header("authorization", &auth)
            .send()
            .await
            .map_err(|error| error.to_string())?
            .error_for_status()
            .map_err(|error| error.to_string())?
            .json::<AssignmentResponse>()
            .await
            .map_err(|error| error.to_string())?;

        if let Some(run) = assignment.run {
            execute_run(client, base_url, registration.worker_id, &auth, args, &run).await?;
        } else {
            sleep(poll_delay).await;
        }
    }
}

async fn execute_run(
    client: &Client,
    base_url: &str,
    worker_id: Uuid,
    auth: &str,
    args: &Args,
    run: &RunAssignment,
) -> Result<(), String> {
    post_event(
        client,
        base_url,
        worker_id,
        auth,
        "executor_started",
        &format!("executing {}", run.scenario_id),
    )
    .await?;

    let mut command = Command::new(&args.executor);
    command
        .env("AGENT_ZEN_GARDEN_RUN_ID", run.run_id.to_string())
        .env("AGENT_ZEN_GARDEN_SCENARIO_ID", &run.scenario_id)
        .env("AGENT_ZEN_GARDEN_SCENARIO_VERSION", &run.scenario_version)
        .env(
            "AGENT_ZEN_GARDEN_PROVIDER",
            run.provider.as_deref().unwrap_or(""),
        )
        .env("AGENT_ZEN_GARDEN_MODEL", run.model.as_deref().unwrap_or(""))
        .env(
            "AGENT_ZEN_GARDEN_HARNESS",
            run.harness.as_deref().unwrap_or(""),
        )
        .env(
            "AGENT_ZEN_GARDEN_REASONING",
            run.reasoning.as_deref().unwrap_or(""),
        );

    let result = command.output().await;
    let (passed, failure_category, message) = match result {
        Ok(output) if output.status.success() => {
            (true, None, "executor exited successfully".to_string())
        }
        Ok(output) => {
            let category = format!(
                "executor_exit_{}",
                output
                    .status
                    .code()
                    .map_or("unknown".to_string(), |code| code.to_string())
            );
            (
                false,
                Some(category),
                "executor reported failure".to_string(),
            )
        }
        Err(error) => (
            false,
            Some("executor_spawn_failed".to_string()),
            format!("executor could not start: {error}"),
        ),
    };

    post_event(
        client,
        base_url,
        worker_id,
        auth,
        "executor_finished",
        &message,
    )
    .await?;
    let response = client
        .post(format!("{base_url}/v1/workers/{worker_id}/result"))
        .header("authorization", auth)
        .json(&ResultRequest {
            passed,
            failure_category,
        })
        .send()
        .await
        .map_err(|error| error.to_string())?;
    response
        .error_for_status()
        .map_err(|error| error.to_string())?;

    Ok(())
}

async fn post_event(
    client: &Client,
    base_url: &str,
    worker_id: Uuid,
    auth: &str,
    kind: &str,
    message: &str,
) -> Result<(), String> {
    let response = client
        .post(format!("{base_url}/v1/workers/{worker_id}/events"))
        .header("authorization", auth)
        .json(&EventRequest { kind, message })
        .send()
        .await
        .map_err(|error| error.to_string())?;
    response
        .error_for_status()
        .map_err(|error| error.to_string())?;
    Ok(())
}
