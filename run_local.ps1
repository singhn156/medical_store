$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Write-Host 'Starting DoseDeck at http://127.0.0.1:8000/?demo=true' -ForegroundColor Green
Write-Host 'Swagger API: http://127.0.0.1:8000/docs' -ForegroundColor Cyan
Write-Host 'Press Ctrl+C to stop. Data is stored in pharmacy.db.' -ForegroundColor DarkGray
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
