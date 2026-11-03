FROM python:3.12-slim
WORKDIR /app
COPY remote_control /app/remote_control
ENV PYTHONPATH=/app
CMD ["python", "-m", "remote_control.server_cli", "--db", "/data/control.sqlite", "--bind", "0.0.0.0", "--port", "8765", "--password-env", "MAA_CONTROL_PASSWORD"]
