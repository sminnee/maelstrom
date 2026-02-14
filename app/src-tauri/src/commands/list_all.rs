use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::process::Command;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Worktree {
    pub name: String,
    pub folder: String,
    pub path: String,
    pub branch: Option<String>,
    pub is_closed: bool,
    pub dirty_files: u32,
    pub local_commits: u32,
    pub pr_number: Option<u32>,
    pub pr_commits: Option<u32>,
    pub pushed_commits: Option<u32>,
    pub ide_active: bool,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Project {
    pub name: String,
    pub path: String,
    pub worktrees: Vec<Worktree>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ListAllResponse {
    pub projects: Vec<Project>,
}

/// Returns the repo root directory (parent of the `app/` directory).
fn repo_root() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    // CARGO_MANIFEST_DIR points to app/src-tauri, go up twice to reach repo root
    dir.pop(); // app/
    dir.pop(); // repo root
    dir
}

#[tauri::command]
pub async fn list_all() -> Result<ListAllResponse, String> {
    let root = repo_root();

    let output = Command::new("uv")
        .args(["run", "mael", "--json", "list-all"])
        .current_dir(&root)
        .output()
        .map_err(|e| format!("Failed to execute uv run mael: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("mael list-all failed: {}", stderr));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let response: ListAllResponse = serde_json::from_str(&stdout)
        .map_err(|e| format!("Failed to parse JSON: {}", e))?;

    Ok(response)
}
