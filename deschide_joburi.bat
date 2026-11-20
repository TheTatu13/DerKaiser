@echo off
rem Deschide cautarea de joburi DerKaiser in browser (pagina merge doar pe acest calculator).
cd /d "%~dp0"
if not exist derkaiser.db (
  echo Construiesc baza de date din joburile gasite...
  py -m scraper.jobdb build derkaiser.db run_all\final_distinct.jsonl run_all\boards_jobs2.jsonl run_firme\full\out\jobs.jsonl --companies run_firme\full\docs.jsonl
)
py -m scraper.jobdb serve derkaiser.db --open
pause
