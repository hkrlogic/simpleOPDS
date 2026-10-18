#!/bin/sh
# Make sure the books folder is writable by the unprivileged "opds" user, then
# drop privileges. This lets the image work with named volumes that already
# exist (root-owned) and with bind-mounted host folders.
set -e

BOOKS="${BOOKS_DIR:-/books}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$BOOKS"
  # Only the top-level folder: existing files stay readable and deletable.
  chown opds:opds "$BOOKS" 2>/dev/null || \
    echo "warning: could not chown $BOOKS; uploads may fail unless it is writable by UID 1000" >&2
  exec su-exec opds "$@"
fi

# Started as a non-root user (e.g. compose `user:`); just run.
exec "$@"
