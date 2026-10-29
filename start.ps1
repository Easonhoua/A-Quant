param([switch]$Install)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) { py -3 -m venv .venv; if ($LASTEXITCODE) { throw '请安装 Python 3.11+ 并启用 py 启动器' } }
if ($Install -or -not (Test-Path 'frontend/dist/index.html')) {
  & '.venv/Scripts/python.exe' -m pip install -r requirements.txt
  if ($LASTEXITCODE) { throw 'Python 依赖安装失败' }
  Push-Location frontend
  try { npm ci; if ($LASTEXITCODE) { throw '前端依赖安装失败' }; npm run build; if ($LASTEXITCODE) { throw '构建失败' } } finally { Pop-Location }
}
Write-Host '打开 http://127.0.0.1:8765 ；按 Ctrl+C 停止。默认示例模式。'
& '.venv/Scripts/python.exe' -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
