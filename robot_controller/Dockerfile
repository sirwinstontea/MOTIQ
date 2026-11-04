# robot_control API + live dashboard (simulation only, Mock backend).
# RoboDK is a desktop GUI app and does not run in this container, so BACKEND defaults to mock.
#
#   docker build -t robot-controller .
#   docker run -p 8765:8765 -e ROBOT_API_PASSWORD=change-me-long-secret robot-controller
#   open http://localhost:8765/   (user: operator, or $ROBOT_API_USER)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY robot_control ./robot_control
RUN pip install '.[api]'

COPY config ./config
COPY examples ./examples

RUN useradd --create-home --uid 10001 robot && mkdir -p runs && chown robot:robot runs
USER robot

# ROBOT_API_PASSWORD (12+ chars) is required: without it the server refuses to listen on 0.0.0.0.
ENV BACKEND=mock \
    DETECTOR=mock \
    PORT=8765 \
    ROBOT_API_USER=operator

EXPOSE 8765

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["sh", "-c", "exec python -m robot_control.cli serve --host 0.0.0.0 --port \"$PORT\" --backend \"$BACKEND\" --detector \"$DETECTOR\""]
