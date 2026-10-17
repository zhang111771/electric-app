<#
.SYNOPSIS
  离线安装 pgvector 扩展到本机 PostgreSQL（幂等，可重复执行）。
.DESCRIPTION
  从同级 bundle/pgvector/ 读取 pgvector 二进制，拷贝到 PostgreSQL 的 lib 与
  share\extension 目录，重启服务并（可选）建扩展。
  部署包结构（bundle/pgvector/）：
    vector.dll          ← 拷贝到 <PG>\lib\
    vector.control      ← 拷贝到 <PG>\share\extension\
    vector--*.sql       ← 拷贝到 <PG>\share\extension\
.PARAMETER PgRoot
  PostgreSQL 安装根目录。默认自动从服务 postgresql-x64-16 探测，探测不到则用 D:\postgres。
.PARAMETER DbName
  建扩展的目标数据库（默认 electric_power）。
.PARAMETER PgUser
  psql 用户名（默认 postgres）。
.PARAMETER PgPassword
  psql 密码。为空且未设 PGPASSWORD 时跳过建扩展（应用首次 seed 会自动建）。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install_pgvector.ps1 -PgPassword postgres
#>
param(
  [string]$PgRoot = "",
  [string]$DbName = "electric_power",
  [string]$PgUser = "postgres",
  [string]$PgPassword = ""
)

$ErrorActionPreference = "Stop"

# ---------- 1) 定位 PostgreSQL 安装目录 ----------
$svc = Get-Service -Name "postgresql-x64-16" -ErrorAction SilentlyContinue
if (-not $PgRoot) {
  if ($svc) {
    $bin = (Get-CimInstance Win32_Service -Filter "Name='postgresql-x64-16'").PathName
    $pgctl = ($bin -split '"')[1]
    $PgRoot = Split-Path (Split-Path $pgctl -Parent) -Parent
  }
}
if (-not $PgRoot) { $PgRoot = "D:\postgres" }
if (-not (Test-Path "$PgRoot\lib")) { throw "未找到 PostgreSQL 安装目录：$PgRoot" }
Write-Host "[1/4] PostgreSQL 安装目录：$PgRoot"

# ---------- 2) 拷贝 pgvector 二进制 ----------
$bundle = Join-Path $PSScriptRoot "..\bundle\pgvector"
if (-not (Test-Path "$bundle\vector.dll")) { throw "未找到离线包：$bundle\vector.dll" }
$lib = "$PgRoot\lib"
$ext = "$PgRoot\share\extension"
if (-not (Test-Path $ext)) { New-Item -ItemType Directory -Path $ext -Force | Out-Null }

Copy-Item "$bundle\vector.dll" $lib -Force
Get-ChildItem "$bundle\vector.control", "$bundle\vector--*.sql" -ErrorAction SilentlyContinue |
  Copy-Item -Destination $ext -Force
Write-Host "[2/4] 已拷贝 vector.dll → $lib"
Write-Host "       已拷贝 vector.control / vector--*.sql → $ext"

# ---------- 3) 重启服务 ----------
if ($svc) {
  Restart-Service -Name "postgresql-x64-16" -Force
  Write-Host "[3/4] 已重启服务 postgresql-x64-16"
} else {
  Write-Host "[3/4] 未找到服务 postgresql-x64-16，请手动重启 PostgreSQL"
}

# ---------- 4) 建扩展（可选） ----------
if (-not $PgPassword) { $PgPassword = $env:PGPASSWORD }
if ($PgPassword) {
  $env:PGPASSWORD = $PgPassword
  & "$PgRoot\bin\psql.exe" -h localhost -U $PgUser -d $DbName -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector"
  if ($LASTEXITCODE -eq 0) { Write-Host "[4/4] 已在 $DbName 库创建 vector 扩展" }
} else {
  Write-Host "[4/4] 跳过建扩展（未提供密码；应用首次 seed 时会自动创建）"
}

Write-Host "完成。可验证：psql -U $PgUser -d $DbName -c ""SELECT vector_dims('[1,2,3]');"""
