#!/bin/bash
cd "$(dirname "$0")"
set -a
source .env
set +a
exec venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001
