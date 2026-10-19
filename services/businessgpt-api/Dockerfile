ARG PYTHON_IMAGE=python:3.11-slim@sha256:b27df5841f3355e9473f9a516d38a6783b6c8dfeacaf2d14a240f443b368ddb6
FROM ${PYTHON_IMAGE} AS llama-builder

ARG LLAMA_CPP_REF=a4107133a634250c8c9d888bc0bc8520dcfd6105

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git \
    && rm -rf /var/lib/apt/lists/*

RUN git init /opt/llama.cpp \
    && git -C /opt/llama.cpp remote add origin https://github.com/ggml-org/llama.cpp \
    && git -C /opt/llama.cpp fetch --depth 1 origin "${LLAMA_CPP_REF}" \
    && git -C /opt/llama.cpp checkout --detach FETCH_HEAD \
    && cmake -B /opt/llama.cpp/build -S /opt/llama.cpp \
        -DCMAKE_BUILD_TYPE=MinSizeRel \
        -DBUILD_SHARED_LIBS=OFF \
        -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF \
    && cmake --build /opt/llama.cpp/build --target llama-server llama-quantize -j2

FROM llama-builder AS runtime-libs

RUN libgomp_path="$(gcc -print-file-name=libgomp.so.1)" \
    && test -f "${libgomp_path}" \
    && cp -L "${libgomp_path}" /tmp/libgomp.so.1

FROM ${PYTHON_IMAGE}

ARG LLAMA_CPP_REF=a4107133a634250c8c9d888bc0bc8520dcfd6105

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    HOST=0.0.0.0 \
    PORT=8000 \
    LLAMA_CPP_REF=${LLAMA_CPP_REF} \
    LLAMA_SERVER_BIN=/usr/local/bin/llama-server \
    LD_LIBRARY_PATH=/usr/local/lib

RUN groupadd --gid 10001 businessgpt \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin businessgpt

COPY --from=llama-builder /opt/llama.cpp/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=llama-builder /opt/llama.cpp/build/bin/llama-quantize /usr/local/bin/llama-quantize
COPY --from=runtime-libs /tmp/libgomp.so.1 /usr/local/lib/libgomp.so.1

WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock

COPY server.py context_window.py media.py api_security.py request_models.py model_verification.py backend_provenance.py ./
COPY model-manifest.json ./model-manifest.json

USER 10001:10001

CMD ["sh", "-c", "exec python -m uvicorn server:app --host \"$HOST\" --port \"$PORT\" --workers 1 --no-server-header --timeout-keep-alive 5 --h11-max-incomplete-event-size 16384"]
