@echo off
rem Starts Moodify with Docker Desktop and opens it in the browser. Stop it with stop.bat.
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker is not running. Install and start Docker Desktop first: https://www.docker.com/products/docker-desktop/
  pause
  exit /b 1
)
if not exist .env copy .env.example .env >nul
echo Building and starting Moodify. The first start downloads about 10 GB and can take 15-30 minutes...
docker compose up -d --build --wait
if errorlevel 1 (
  echo Moodify did not start. Check the logs with: docker compose logs
  pause
  exit /b 1
)
start http://localhost:8501
echo Moodify is running at http://localhost:8501
