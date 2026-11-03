@echo off
rem Starts Moodify with Docker Desktop and opens it in the browser. Stop it with stop.bat.
rem Set LLM_ENABLED=false in .env for light mode on machines that can't run a local LLM.
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker is not running. Install and start Docker Desktop first: https://www.docker.com/products/docker-desktop/
  pause
  exit /b 1
)
if not exist .env copy .env.example .env >nul

findstr /i /r /c:"^LLM_ENABLED=false" .env >nul
if errorlevel 1 (
  echo LLM is on. The first start downloads about 10 GB and can take 15-30 minutes...
  docker compose --profile llm up -d --build --wait
) else (
  echo Light mode: LLM is off. The first start downloads about 5 GB and can take 10-20 minutes...
  docker compose --profile llm rm -sf ollama ollama-pull >nul 2>&1
  docker compose up -d --build --wait
)
if errorlevel 1 (
  echo Moodify did not start. Check the logs with: docker compose logs
  pause
  exit /b 1
)
start http://localhost:8501
echo Moodify is running at http://localhost:8501
