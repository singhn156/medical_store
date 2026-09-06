<#
Simple helper to push this repository to GitHub.
Usage examples:
  # Create a new public repo using GitHub CLI and push
  .\push_to_github.ps1 -RepoName dosedeck -Public

  # Push to an existing remote URL
  .\push_to_github.ps1 -RemoteUrl 'https://github.com/you/dosedeck.git'

Notes:
- If you want the script to create a GitHub repo for you, install the GitHub CLI (`gh`) and authenticate first: `gh auth login`.
- Runs on Windows PowerShell / PowerShell Core.
#>
param(
    [string]$RepoName = $(Split-Path -Leaf (Get-Location)),
    [string]$RemoteUrl = "",
    [switch]$Private,
    [switch]$Public
)

function Write-ErrExit($msg){ Write-Host $msg -ForegroundColor Red; exit 1 }

# Create a .gitignore if missing
if (-not (Test-Path .gitignore)){
    @(
        "venv/",
        "__pycache__/",
        ".env",
        "*.pyc",
        ".DS_Store",
        "pharmacy.db",
        "dosedeck.db",
        "venv/",
        "*.egg-info/",
        "node_modules/",
        ".pytest_cache/",
        ".vscode/"
    ) | Out-File -FilePath .gitignore -Encoding utf8 -Force
    Write-Host "Created .gitignore"
}

# Initialize git if needed
if (-not (Test-Path .git)){
    git init || Write-ErrExit "git init failed. Install git and try again."
    Write-Host "Initialized git repository"
}

# Ensure branch is main
git checkout -B main 2>$null | Out-Null

# Stage and commit
git add --all
$commitMsg = "Initial commit"
try{
    git commit -m $commitMsg -q
    Write-Host "Committed changes"
}catch{
    Write-Host "No changes to commit or commit failed; continuing..."
}

# If gh CLI is available and no RemoteUrl provided, create repo
$gh = Get-Command gh -ErrorAction SilentlyContinue
if ($gh -and -not $RemoteUrl){
    $vis = if ($Private){"--private"} elseif ($Public){"--public"} else {"--public"}
    Write-Host "Creating repository on GitHub using gh CLI: $RepoName ($vis)"
    gh repo create $RepoName $vis --source=. --remote=origin --push || Write-ErrExit "gh repo create failed"
    Write-Host "Repository created and pushed via gh. Remote 'origin' set."
    exit 0
}

# Else, if RemoteUrl provided, add remote and push
if ($RemoteUrl){
    git remote remove origin 2>$null | Out-Null
    git remote add origin $RemoteUrl || Write-ErrExit "Failed to add remote $RemoteUrl"
    git branch -M main
    git push -u origin main || Write-ErrExit "git push failed. Check credentials or remote URL."
    Write-Host "Pushed to remote: $RemoteUrl"
    exit 0
}

Write-ErrExit "No remote provided and GitHub CLI not found. Install GitHub CLI and run 'gh auth login' or re-run script with -RemoteUrl '<repo-url>'"
