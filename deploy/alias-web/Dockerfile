FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[web]' && useradd --uid 10001 --create-home editor
USER editor
EXPOSE 8765
CMD ["arcade-alias-relay", "--host", "0.0.0.0", "--port", "8765"]
