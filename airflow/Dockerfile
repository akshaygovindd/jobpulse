# LOCAL DEMO ONLY. Airflow runs the same `jobpulse` CLI tasks as the daily workflow, against local
# directories. This image holds no cloud credentials and the compose file passes none.
FROM apache/airflow:2.10.5-python3.12

USER root
RUN mkdir -p /opt/jobpulse /opt/jobpulse-venv /opt/demo-data && chown -R airflow:root /opt/jobpulse /opt/jobpulse-venv /opt/demo-data
USER airflow

RUN pip install --no-cache-dir uv==0.9.5

# The project lives in its own virtualenv so its pinned dependencies (duckdb 1.5.6, dbt-duckdb)
# never fight Airflow's constraints. Only the files listed here enter the image (see
# Dockerfile.dockerignore: .env and everything else is excluded from the build context).
WORKDIR /opt/jobpulse
COPY --chown=airflow:root pyproject.toml uv.lock README.md ./
COPY --chown=airflow:root src ./src
COPY --chown=airflow:root config ./config
COPY --chown=airflow:root dbt ./dbt
COPY --chown=airflow:root scripts ./scripts

ENV UV_PROJECT_ENVIRONMENT=/opt/jobpulse-venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python \
    JOBPULSE_ENV=dev \
    AWS_EC2_METADATA_DISABLED=true
RUN uv sync --frozen --no-default-groups --group dbt

COPY --chown=airflow:root airflow/entrypoint.sh /entrypoint-demo.sh
ENTRYPOINT ["/usr/bin/dumb-init", "--", "/entrypoint-demo.sh"]
