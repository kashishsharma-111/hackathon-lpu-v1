FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    HF_HOME=/data/huggingface

WORKDIR /app

# CPU-only torch keeps the image about 2 GB smaller than the default CUDA build
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.py ./

EXPOSE 8235 8501

# docker-compose.yml starts the backend and the frontend from this one image
CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8235"]
