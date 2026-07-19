FROM python:3.11-slim

ARG LLAMA_CPP_REF=a4107133a634250c8c9d888bc0bc8520dcfd6105

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    CMAKE_BUILD_PARALLEL_LEVEL=2

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git \
    && rm -rf /var/lib/apt/lists/*

RUN git init /opt/llama.cpp \
    && git -C /opt/llama.cpp remote add origin https://github.com/ggml-org/llama.cpp \
    && git -C /opt/llama.cpp fetch --depth 1 origin "${LLAMA_CPP_REF}" \
    && git -C /opt/llama.cpp checkout --detach FETCH_HEAD \
    && cmake -B /opt/llama.cpp/build -S /opt/llama.cpp \
        -DCMAKE_BUILD_TYPE=MinSizeRel \
        -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF \
    && cmake --build /opt/llama.cpp/build --target llama-server llama-quantize -j2

WORKDIR /app
COPY services/businessgpt-api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/businessgpt-api/server.py ./server.py
COPY services/businessgpt-api/context_window.py ./context_window.py
COPY services/businessgpt-api/media.py ./media.py

CMD ["sh", "-c", "uvicorn server:app --host ${HOST} --port ${PORT} --workers 1"]
