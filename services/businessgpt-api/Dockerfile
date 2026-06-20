FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    CMAKE_BUILD_PARALLEL_LEVEL=2

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp /opt/llama.cpp \
    && cmake -B /opt/llama.cpp/build -S /opt/llama.cpp -DCMAKE_BUILD_TYPE=MinSizeRel \
    && cmake --build /opt/llama.cpp/build --target llama-server -j2

WORKDIR /app
COPY services/businessgpt-api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/businessgpt-api/server.py ./server.py

CMD ["sh", "-c", "uvicorn server:app --host ${HOST} --port ${PORT} --workers 1"]

