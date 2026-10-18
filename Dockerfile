FROM python:3.12-alpine

LABEL org.opencontainers.image.source="https://github.com/hkrlogic/simpleOPDS" \
      org.opencontainers.image.description="Tiny OPDS ebook server with a drag-and-drop upload page" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Unprivileged user. Nothing in this image ever runs as root at runtime.
RUN adduser -D -u 1000 opds && mkdir /books && chown opds:opds /books

WORKDIR /app
COPY server.py .
USER opds

VOLUME /books
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python3 -c "import socket; socket.create_connection(('127.0.0.1', 8080), 2).close()"

CMD ["python3", "server.py", "--books", "/books", "--port", "8080"]
