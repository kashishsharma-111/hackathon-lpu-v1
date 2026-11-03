@echo off
rem Stops Moodify. Your songs and settings are kept; use "docker compose --profile llm down -v" to delete them too.
docker compose --profile llm down
