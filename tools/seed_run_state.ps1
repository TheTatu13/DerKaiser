# Seeds the `run-state` branch on GitHub with the firm list and the progress made on this PC so far.
# Run once from the DerKaiser folder:  powershell -ExecutionPolicy Bypass -File tools\seed_run_state.ps1
# Only pushes to YOUR repo (branch run-state). Dry-run data, no credentials.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$firms = Join-Path $root "run_firme\firme_active_ge3.jsonl"
$full  = Join-Path $root "run_firme\full"
if (-not (Test-Path $firms)) { throw "Missing $firms" }
$tmp = Join-Path $env:TEMP ("dk_seed_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp, "$tmp\out" | Out-Null

# firms list, gzip
$src = [IO.File]::OpenRead($firms); $dst = [IO.File]::Create("$tmp\firms_active.jsonl.gz")
$gz = New-Object IO.Compression.GZipStream($dst, [IO.Compression.CompressionMode]::Compress)
$src.CopyTo($gz); $gz.Dispose(); $dst.Dispose(); $src.Dispose()

# progress so far (copies; the originals stay untouched)
foreach ($f in "docs.jsonl") { if (Test-Path "$full\$f") { Copy-Item "$full\$f" "$tmp\$f" } }
foreach ($f in "jobs.jsonl", "jobs_by_firm.jsonl") { if (Test-Path "$full\out\$f") { Copy-Item "$full\out\$f" "$tmp\out\$f" } }
if (Test-Path "$root\recipes") { Copy-Item "$root\recipes" "$tmp\recipes" -Recurse }

Set-Location $tmp
"firms_active.jsonl`ncache/`n*.tmp" | Set-Content .gitignore -Encoding ascii
git init -q -b run-state
git config user.name "derkaiser-seed"; git config user.email "derkaiser-seed@users.noreply.github.com"
git add -A
git commit -q -m "seed run-state"
$url = (git -C $root remote get-url origin)
git push --force $url run-state:run-state
Set-Location $root
Remove-Item $tmp -Recurse -Force
Write-Host "run-state seeded. Now: GitHub > Actions > 'DerKaiser run' > Run workflow."
