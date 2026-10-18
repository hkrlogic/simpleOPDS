# simpleOPDS

A tiny, dependency-free [OPDS](https://opds.io) ebook server with a drag-and-drop
upload page. Drop EPUBs onto a web page, then browse and download them from any
OPDS-capable e-reader (built with the XTEink in mind, but it's standard OPDS 1.2).

- **Web page** at `/` to upload (drag and drop), list and delete books
- **OPDS catalog** at `/opds`: all books (A-Z), recently added, search
- Reads title and author from EPUB metadata (falls back to the filename)
- Supports EPUB, PDF, MOBI, AZW3, CBZ, TXT and FB2
- Python standard library only; small multi-arch image (amd64 and arm64)
- Optional basic auth; runs as a non-root user

## Quick start

```yaml
services:
  opds:
    image: ghcr.io/hkrlogic/simpleopds:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - opds-books:/books
    # environment:
    #   OPDS_USER: reader
    #   OPDS_PASS: change-me

volumes:
  opds-books:
```

```
docker compose up -d
```

In **Portainer**: Stacks → Add stack → Web editor → paste the above → Deploy.

Then:

1. Open `http://<server-ip>:8080/` and drag your books onto the page.
2. On your e-reader, add a catalog: `http://<server-ip>:8080/opds`
   (if you set a username and password, enter them in the reader too).

## Configuration

| Variable        | Default | Description                                             |
|-----------------|---------|---------------------------------------------------------|
| `OPDS_USER`     | unset   | Enables HTTP basic auth (with `OPDS_PASS`) everywhere   |
| `OPDS_PASS`     | unset   | Password for basic auth                                 |
| `MAX_UPLOAD_MB` | `500`   | Largest allowed upload                                  |

Books live in `/books` inside the container. Use a named volume (as above) or a
bind mount such as `/srv/books:/books`; a bind-mounted folder must be writable
by UID 1000 (`chown 1000:1000 /srv/books`).

## Security

Without `OPDS_USER`/`OPDS_PASS`, **anyone who can reach the port can upload and
delete books**. That's fine on a trusted home LAN, but set a password before
exposing it any further. The server speaks plain HTTP: if you put it on the
internet, run it behind a reverse proxy that terminates TLS (it honors
`X-Forwarded-Proto` and `X-Forwarded-Host` so catalog links use `https://`).
Basic auth over plain HTTP sends the password unencrypted.

Only the listed ebook extensions can be uploaded, filenames are sanitized,
downloads can't escape the books folder, and state-changing requests require a
custom header so other websites can't trigger them from your browser.

## Run without Docker

```
python3 server.py --books ./books --port 8080
```

Requires Python 3.8 or newer.

## Build locally

```
git clone https://github.com/hkrlogic/simpleOPDS
cd simpleOPDS
docker build -t simpleopds .
```

## License

[MIT](LICENSE)
