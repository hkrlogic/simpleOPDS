FROM python:3.12-alpine

LABEL org.opencontainers.image.source="https://github.com/hkrlogic/simpleOPDS" \
      org.opencontainers.image.description="Tiny OPDS ebook server with a drag-and-drop upload page" \
      org.opencontainers.image.licenses="MIT"

RUN apk add --no-cache su-exec \
 && adduser -D -u 1000 opds \
 && mkdir /books && chown opds:opds /books

WORKDIR /app
COPY server.py entrypoint.sh ./
RUN chmod 755 /app/entrypoint.sh

VOLUME /books
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python3 -c "import socket; socket.create_connection(('127.0.0.1', 8080), 2).close()"

# Starts as root only long enough to fix /books ownership, then runs the
# server as the unprivileged "opds" user (see entrypoint.sh).
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python3", "-u", "server.py", "--books", "/books", "--port", "8080"]
