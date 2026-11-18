Add-Type -Namespace W -Name P -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[void][W.P]::SetThreadExecutionState(0x80000001)
$env:PYTHONIOENCODING='utf-8'
Set-Location (Split-Path $PSScriptRoot)
New-Item -ItemType Directory -Force run_firme\\full | Out-Null
# 1) screen the new firms (resumable), 2) active list, 3) bridge in background, 4) jobs passes while the bridge runs
if (-not (Test-Path run_firme\full\screen_ok.txt)) {
  py tools\firms_screen_new.py > run_firme\full\screen_log.txt 2> run_firme\full\screen_err.txt
  'ok' | Out-File run_firme\full\screen_ok.txt
}
py tools\firms_prep_active.py > run_firme\full\prep_log.txt
$b = Start-Process py -ArgumentList '-m','scraper.bridge','--firms','run_firme\firme_active_ge3.jsonl','--out','run_firme\full\docs.jsonl','--min-employees','3','--workers','40','--interval','2' -RedirectStandardOutput run_firme\full\bridge_log.txt -RedirectStandardError run_firme\full\bridge_err.txt -PassThru -WindowStyle Hidden
while ($true) {
  $alive = -not $b.HasExited
  if (Test-Path run_firme\full\docs.jsonl) {
    py tools\firms_extract_site.py > run_firme\full\extract_log.txt
    py -m scraper.derkaiser --jobs run_firme\full\docs_site.jsonl --out run_firme\full\out --workers 6 --interval 3 --learn --no-render --recipes run_firme\full\recipes --cache run_firme\full\cache >> run_firme\full\jobs_log.txt 2>> run_firme\full\jobs_err.txt
  }
  if (-not $alive) { break }
  Start-Sleep -Seconds 600
}
'done ' + (Get-Date) | Out-File run_firme\full\full_done.txt
