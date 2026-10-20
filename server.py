#!/usr/bin/env python3
"""simpleOPDS: a simple OPDS 1.2 ebook server. Standard library only.

Usage:
    python3 server.py [--books ./books] [--port 8080] [--host 0.0.0.0]

Optional HTTP basic auth: set OPDS_USER and OPDS_PASS environment variables.

Point your reader at:  http://<this-machine-ip>:8080/opds
"""
import argparse
import base64
import hmac
import json
import mimetypes
import os
import re
import socket
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

MIME = {
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
    ".mobi": "application/x-mobipocket-ebook",
    ".azw3": "application/vnd.amazon.ebook",
    ".cbz": "application/vnd.comicbook+zip",
    ".txt": "text/plain",
    ".fb2": "application/x-fictionbook+xml",
}
NAV_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"

BOOKS_DIR = "books"
_META = {}  # (path, mtime_ns, size) -> (title, author); avoids re-opening EPUBs
STATE_FILE = ".simpleopds.json"  # lives in the books folder; ignored by the book scan
_STATE_LOCK = threading.Lock()


def load_read():
    """Return {relative book path: ISO time it was marked read}."""
    try:
        with open(os.path.join(BOOKS_DIR, STATE_FILE), encoding="utf-8") as f:
            read = json.load(f).get("read", {})
        return read if isinstance(read, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def set_read(rels, is_read):
    """Mark books read or unread. Raises OSError if the folder isn't writable."""
    with _STATE_LOCK:
        read = load_read()
        for rel in rels:
            if is_read:
                read.setdefault(rel, now())
            else:
                read.pop(rel, None)
        tmp = os.path.join(BOOKS_DIR, f".{uuid.uuid4().hex}.state")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"read": read}, f, indent=1, sort_keys=True)
            os.replace(tmp, os.path.join(BOOKS_DIR, STATE_FILE))
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024
APP_NAME = "simpleOPDS"
LIBRARY_TITLE = "OPDSLibrary"  # shown as the page heading and the catalog name
REPO_URL = "https://github.com/hkrlogic/simpleOPDS"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OPDSLibrary &middot; simpleOPDS</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%232a6f5e'/%3E%3Cpath d='M9 8h11a3 3 0 0 1 3 3v13H12a3 3 0 0 1-3-3z' fill='none' stroke='white' stroke-width='2'/%3E%3C/svg%3E">
<style>
:root{--bg:#fafaf8;--fg:#1c1c1a;--mut:#6b6b66;--line:#dcdcd6;--acc:#2a6f5e;--card:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececE8;--mut:#9a9a94;--line:#2e2e2b;--acc:#5fb8a2;--card:#1f1f1d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.4rem;margin:0 0 4px}
.sub{color:var(--mut);margin:0 0 20px}
code{background:var(--card);border:1px solid var(--line);padding:2px 6px;border-radius:4px;word-break:break-all}
#drop{border:2px dashed var(--line);border-radius:12px;padding:36px 16px;text-align:center;
 background:var(--card);cursor:pointer;color:var(--mut);transition:.15s}
#drop.over{border-color:var(--acc);color:var(--acc)}
#drop b{color:var(--fg)}
#queue div{margin-top:8px;font-size:.9rem}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:3px}
.bar i{display:block;height:100%;width:0;background:var(--acc)}
.err{color:#c0392b}
h2{font-size:1.05rem;margin:28px 0 8px}
ul{list-style:none;margin:0;padding:0;border-top:1px solid var(--line)}
li{display:flex;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}
li .t{flex:1;min-width:0}
li .t strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
li .t span{color:var(--mut);font-size:.85rem}
a{color:var(--acc)}
footer{margin-top:32px;color:var(--mut);font-size:.85rem;text-align:center}
#bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:8px 0}
#bar .all{display:flex;gap:8px;align-items:center;color:var(--mut);font-size:.9rem;cursor:pointer}
#bar .right{margin-left:auto;display:flex;gap:12px;flex-wrap:wrap}
#bar .sort{color:var(--mut);font-size:.9rem}
input[type=checkbox]{width:18px;height:18px;accent-color:var(--acc);flex:none;cursor:pointer}
select{font:inherit;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:2px 6px}
li.sel{background:color-mix(in srgb,var(--acc) 10%,transparent)}
#del{color:#c0392b;border-color:#c0392b}
button{font:inherit;background:none;border:1px solid var(--line);color:var(--mut);
 border-radius:6px;padding:2px 10px;cursor:pointer}
button:hover{color:var(--acc);border-color:var(--acc)}
button.danger:hover{color:#c0392b;border-color:#c0392b}
li .t .badge{color:var(--acc);font-weight:600}
.acts{display:flex;gap:8px;align-items:center;flex:none}
@media(max-width:560px){li{flex-wrap:wrap}li .t{flex:1 1 calc(100% - 40px)}.acts{flex-basis:100%;padding-left:30px}}
</style></head><body><main>
<h1>OPDSLibrary</h1>
<p class="sub">Add this catalog to your reader: <code id="opds"></code><br>
<small>If your reader can't connect, add <code>/opds</code> to the end of the address.</small></p>
<div id="drop"><b>Drop books here</b> or click to choose<br>
<small>EPUB, PDF, MOBI, AZW3, CBZ, TXT, FB2</small></div>
<input id="file" type="file" multiple hidden accept=".epub,.pdf,.mobi,.azw3,.cbz,.txt,.fb2">
<div id="queue"></div>
<h2>Books (<span id="count">0</span>)</h2>
<div id="bar">
<label class="all"><input type="checkbox" id="all"><span id="sel">Select all</span></label>
<button id="rd" hidden>Mark read</button>
<button id="ur" hidden>Mark unread</button>
<button id="del" class="danger" hidden>Delete selected</button>
<div class="right"><label class="sort">Show
<select id="filter">
<option value="all">All</option>
<option value="unread">Unread</option>
<option value="read">Read</option>
</select></label>
<label class="sort">Sort
<select id="sort">
<option value="title">Title A\u2013Z</option>
<option value="title-desc">Title Z\u2013A</option>
<option value="author">Author A\u2013Z</option>
<option value="newest">Newest first</option>
<option value="oldest">Oldest first</option>
<option value="largest">Largest first</option>
<option value="smallest">Smallest first</option>
</select></label></div>
</div>
<ul id="list"></ul>
<footer>Powered by <a href="https://github.com/hkrlogic/simpleOPDS">simpleOPDS</a></footer>
</main>
<script>
const $ = id => document.getElementById(id);
$('opds').textContent = location.origin;
const drop = $('drop'), input = $('file');
drop.onclick = () => input.click();
input.onchange = () => { upload([...input.files]); input.value = ''; };
['dragenter','dragover'].forEach(e => drop.addEventListener(e, ev => {ev.preventDefault(); drop.classList.add('over');}));
['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => {ev.preventDefault(); drop.classList.remove('over');}));
drop.addEventListener('drop', ev => upload([...ev.dataTransfer.files]));
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => e.preventDefault());

function size(n){return n>1048576?(n/1048576).toFixed(1)+' MB':Math.max(1,Math.round(n/1024))+' KB'}

function upload(files){
  files.forEach(f => {
    const row = document.createElement('div');
    const label = document.createElement('span');
    label.textContent = f.name;
    const bar = document.createElement('div'); bar.className = 'bar';
    const fill = document.createElement('i'); bar.appendChild(fill);
    row.append(label, bar); $('queue').appendChild(row);
    const x = new XMLHttpRequest();
    x.open('POST', '/upload?name=' + encodeURIComponent(f.name));
    x.setRequestHeader('X-Requested-With', 'opds');
    x.upload.onprogress = e => { if (e.lengthComputable) fill.style.width = (e.loaded/e.total*100)+'%'; };
    x.onload = () => {
      if (x.status === 200) { fill.style.width = '100%'; label.textContent = f.name + ' \\u2713'; load(); setTimeout(() => row.remove(), 2500); }
      else { let m = x.responseText; try { m = JSON.parse(m).error } catch(e){} label.textContent = f.name + ' \\u2014 ' + m; label.className = 'err'; bar.remove(); }
    };
    x.onerror = () => { label.textContent = f.name + ' \\u2014 upload failed'; label.className = 'err'; bar.remove(); };
    x.send(f);
  });
}

let books = [], selected = new Set(), rows = [], lastIdx = null;
const SORTS = {
  'title': (a, b) => a.title.localeCompare(b.title, undefined, {numeric: true, sensitivity: 'base'}),
  'title-desc': (a, b) => SORTS.title(b, a),
  'author': (a, b) => a.author.localeCompare(b.author, undefined, {sensitivity: 'base'}) || SORTS.title(a, b),
  'newest': (a, b) => b.mtime.localeCompare(a.mtime),
  'oldest': (a, b) => a.mtime.localeCompare(b.mtime),
  'largest': (a, b) => b.size - a.size,
  'smallest': (a, b) => a.size - b.size
};
const sortSel = $('sort'), filterSel = $('filter');
try { const v = localStorage.getItem('opds-sort'); if (v && SORTS[v]) sortSel.value = v; } catch (e) {}
try { const v = localStorage.getItem('opds-filter'); if (v === 'all' || v === 'read' || v === 'unread') filterSel.value = v; } catch (e) {}
sortSel.onchange = () => { try { localStorage.setItem('opds-sort', sortSel.value); } catch (e) {} render(); };
filterSel.onchange = () => { try { localStorage.setItem('opds-filter', filterSel.value); } catch (e) {} render(); };

function refresh() {
  rows.forEach(r => { const on = selected.has(r.rel); r.cb.checked = on; r.li.classList.toggle('sel', on); });
  const n = selected.size, total = rows.length;
  $('all').checked = total > 0 && n === total;
  $('all').indeterminate = n > 0 && n < total;
  $('sel').textContent = n ? n + ' selected' : 'Select all';
  ['rd', 'ur', 'del'].forEach(id => { $(id).hidden = n === 0; });
  $('del').textContent = 'Delete selected (' + n + ')';
}

$('all').onchange = () => {
  if ($('all').checked) rows.forEach(r => selected.add(r.rel)); else selected.clear();
  refresh();
};

async function mark(rels, flag) {
  try {
    const r = await fetch('/mark', {method: 'POST', headers: {'X-Requested-With': 'opds', 'Content-Type': 'application/json'}, body: JSON.stringify({names: rels, read: flag})});
    if (!r.ok) { let m = 'Could not save.'; try { m = (await r.json()).error; } catch (e) {} alert(m); }
  } catch (e) { alert('Could not save.'); }
  await load();
}
$('rd').onclick = () => mark([...selected], true);
$('ur').onclick = () => mark([...selected], false);

$('del').onclick = async () => {
  const names = [...selected];
  if (!names.length || !confirm('Delete ' + names.length + (names.length === 1 ? ' book' : ' books') + '? This cannot be undone.')) return;
  $('del').disabled = true;
  let failed = 0;
  for (const rel of names) {
    try {
      const r = await fetch('/delete?name=' + encodeURIComponent(rel), {method: 'POST', headers: {'X-Requested-With': 'opds'}});
      if (r.ok) selected.delete(rel); else failed++;
    } catch (e) { failed++; }
  }
  $('del').disabled = false;
  await load();
  if (failed) alert(failed + ' could not be deleted.');
};

function render() {
  const f = filterSel.value;
  const list = books.filter(b => f === 'all' || (f === 'read') === !!b.read).sort(SORTS[sortSel.value]);
  // Never keep a hidden book selected: bulk actions only touch what you can see.
  const vis = new Set(list.map(b => b.rel));
  [...selected].forEach(rel => { if (!vis.has(rel)) selected.delete(rel); });
  const ul = $('list'); ul.textContent = '';
  rows = []; lastIdx = null;
  $('count').textContent = list.length === books.length ? books.length : list.length + ' of ' + books.length;
  if (!list.length) {
    const li = document.createElement('li'); li.style.color = 'var(--mut)';
    li.textContent = books.length ? 'No books match this filter.' : 'No books yet. Drop some above.';
    ul.appendChild(li);
  }
  list.forEach((b, i) => {
    const li = document.createElement('li');
    const cb = document.createElement('input'); cb.type = 'checkbox';
    cb.setAttribute('aria-label', 'Select ' + b.title);
    cb.onclick = ev => {
      const on = cb.checked;
      if (ev.shiftKey && lastIdx !== null) {
        for (let k = Math.min(lastIdx, i); k <= Math.max(lastIdx, i); k++) on ? selected.add(list[k].rel) : selected.delete(list[k].rel);
      } else { on ? selected.add(b.rel) : selected.delete(b.rel); }
      lastIdx = i; refresh();
    };
    const t = document.createElement('div'); t.className = 't';
    const s1 = document.createElement('strong'); s1.textContent = b.title;
    const m = document.createElement('span'); m.textContent = b.author + ' \\u00b7 ' + size(b.size);
    if (b.read) { const rb = document.createElement('span'); rb.className = 'badge'; rb.textContent = ' \\u00b7 Read'; m.append(rb); }
    t.append(s1, m);
    const acts = document.createElement('div'); acts.className = 'acts';
    const a = document.createElement('a'); a.href = '/download/' + encodeURIComponent(b.rel).replace(/%2F/g, '/'); a.textContent = 'Download';
    const mk = document.createElement('button'); mk.textContent = b.read ? 'Mark unread' : 'Mark read';
    mk.onclick = () => mark([b.rel], !b.read);
    const d = document.createElement('button'); d.className = 'danger'; d.textContent = 'Delete';
    d.onclick = async () => {
      if (!confirm('Delete "' + b.title + '"?')) return;
      await fetch('/delete?name=' + encodeURIComponent(b.rel), {method: 'POST', headers: {'X-Requested-With': 'opds'}});
      selected.delete(b.rel); load();
    };
    acts.append(a, mk, d);
    li.append(cb, t, acts); ul.appendChild(li);
    rows.push({rel: b.rel, li: li, cb: cb});
  });
  refresh();
}

async function load() {
  const r = await fetch('/api/books'); books = await r.json();
  render();
}
load();
</script></body></html>
"""


def attr(s):
    """Escape a value for use inside a double-quoted XML attribute."""
    return escape(s, {'"': "&quot;"})


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epub_meta(path):
    """Return (title, author) from an EPUB's OPF, or (None, None)."""
    try:
        with zipfile.ZipFile(path) as z:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            opf_path = container.find(".//{*}rootfile").get("full-path")
            opf = ET.fromstring(z.read(opf_path))
            title = opf.find(".//{*}metadata/{*}title")
            creator = opf.find(".//{*}metadata/{*}creator")
            return (
                title.text.strip() if title is not None and title.text else None,
                creator.text.strip() if creator is not None and creator.text else None,
            )
    except Exception:
        return None, None


def scan_books():
    books = []
    read = load_read()
    for root, _, files in os.walk(BOOKS_DIR):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in MIME:
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, BOOKS_DIR).replace(os.sep, "/")
            st = os.stat(full)
            key = (full, st.st_mtime_ns, st.st_size)
            if key in _META:
                title, author = _META[key]
            else:
                title, author = (None, None)
                if ext == ".epub":
                    title, author = epub_meta(full)
                if not title:
                    title = os.path.splitext(name)[0].replace("_", " ")
                _META[key] = (title, author)
            books.append(
                {
                    "rel": rel,
                    "title": title,
                    "author": author or "Unknown",
                    "read": rel in read,
                    "mime": MIME[ext],
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                }
            )
    books.sort(key=lambda b: b["title"].lower())
    return books


def feed(title, self_href, kind, entries_xml, base):
    ftype = NAV_TYPE if kind == "nav" else ACQ_TYPE
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/" xmlns:opds="http://opds-spec.org/2010/catalog" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <id>urn:opds:{escape(self_href)}</id>
  <title>{escape(title)}</title>
  <updated>{now()}</updated>
  <generator uri="{REPO_URL}">{APP_NAME}</generator>
  <link rel="self" href="{attr(base + self_href)}" type="{ftype}"/>
  <link rel="start" href="{base}/opds" type="{NAV_TYPE}"/>
  <link rel="search" href="{base}/opds/opensearch.xml" type="application/opensearchdescription+xml"/>
{entries_xml}</feed>
"""


def nav_entry(title, href, content, base):
    return f"""  <entry>
    <title>{escape(title)}</title>
    <id>urn:opds:{escape(href)}</id>
    <updated>{now()}</updated>
    <content type="text">{escape(content)}</content>
    <link rel="subsection" href="{base}{href}" type="{ACQ_TYPE}"/>
  </entry>
"""


def book_entry(b, base):
    title = ("[Read] " if b.get("read") else "") + b["title"]
    href = f"{base}/download/{quote(b['rel'])}"
    return f"""  <entry>
    <title>{escape(title)}</title>
    <id>urn:opds:book:{escape(b['rel'])}</id>
    <updated>{b['mtime']}</updated>
    <author><name>{escape(b['author'])}</name></author>
    <link rel="http://opds-spec.org/acquisition" href="{href}" type="{b['mime']}" length="{b['size']}"/>
  </entry>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "simpleOPDS/1.0"

    def base(self):
        """Public base URL for links. Honors a reverse proxy's forwarded headers."""
        proto = self.headers.get("X-Forwarded-Proto", "http")
        if proto not in ("http", "https"):
            proto = "http"
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        if not re.fullmatch(r"[A-Za-z0-9.\-]+(:\d+)?|\[[0-9A-Fa-f:]+\](:\d+)?", host):
            host = f"localhost:{self.server.server_port}"
        return f"{proto}://{host}"

    def authorized(self):
        user, pw = os.environ.get("OPDS_USER"), os.environ.get("OPDS_PASS")
        if not user:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                given = base64.b64decode(h[6:]).decode()
                return hmac.compare_digest(given, f"{user}:{pw}")
            except Exception:
                pass
        return False

    def send_xml(self, body, ctype):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Vary", "Accept")
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="OPDS"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def resolve(self, rel):
        """Map a relative book path to a real path inside BOOKS_DIR, or None."""
        root = os.path.realpath(BOOKS_DIR)
        full = os.path.realpath(os.path.join(root, rel))
        if full.startswith(root + os.sep) and os.path.isfile(full):
            return full
        return None

    def do_POST(self):
        if not self.authorized():
            return self.deny()
        # Blocks cross-site form/fetch attacks: browsers can't send a custom
        # header cross-origin without a preflight, which this server never allows.
        if self.headers.get("X-Requested-With") != "opds":
            self.close_connection = True
            return self.send_json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        name = parse_qs(url.query).get("name", [""])[0]

        if url.path == "/mark":
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or int(length) > 1 << 20:
                self.close_connection = True
                return self.send_json({"error": "bad request"}, 400)
            try:
                body = json.loads(self.rfile.read(int(length)) or b"{}")
                names, flag = body["names"], body["read"]
                if not isinstance(flag, bool) or not isinstance(names, list):
                    raise ValueError
                if not all(isinstance(n, str) for n in names):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self.send_json({"error": "bad request"}, 400)
            root = os.path.realpath(BOOKS_DIR)
            rels = []
            for n in names:
                full = self.resolve(n)
                if full:
                    rels.append(os.path.relpath(full, root).replace(os.sep, "/"))
            try:
                set_read(rels, flag)
            except OSError:
                return self.send_json(
                    {"error": "could not save read status (is the books folder writable?)"}, 500
                )
            return self.send_json({"ok": True, "changed": len(rels)})

        if url.path == "/upload":
            name = os.path.basename(name.replace("\\", "/")).strip()
            ext = os.path.splitext(name)[1].lower()
            if not name or name.startswith(".") or ext not in MIME:
                self.close_connection = True
                return self.send_json({"error": "unsupported file type"}, 400)
            length = self.headers.get("Content-Length")
            if length is None or not length.isdigit():
                self.close_connection = True
                return self.send_json({"error": "length required"}, 411)
            length = int(length)
            if length > MAX_UPLOAD:
                self.close_connection = True
                return self.send_json({"error": "file too large"}, 413)

            os.makedirs(BOOKS_DIR, exist_ok=True)
            tmp = os.path.join(BOOKS_DIR, f".{uuid.uuid4().hex}.part")
            try:
                with open(tmp, "wb") as f:
                    left = length
                    while left > 0:
                        chunk = self.rfile.read(min(65536, left))
                        if not chunk:
                            raise IOError("connection closed")
                        f.write(chunk)
                        left -= len(chunk)
                stem, n = os.path.splitext(name)[0], 0
                dest = os.path.join(BOOKS_DIR, name)
                while os.path.exists(dest):
                    n += 1
                    dest = os.path.join(BOOKS_DIR, f"{stem} ({n}){ext}")
                os.rename(tmp, dest)
            except PermissionError:
                self.close_connection = True
                return self.send_json(
                    {
                        "error": "the books folder is not writable by the server "
                        f"(running as UID {os.getuid()}). Make that folder writable "
                        f"by UID {os.getuid()}, e.g. chown -R {os.getuid()}:{os.getgid()} <folder>. "
                        "See the README."
                    },
                    500,
                )
            except Exception as e:
                if os.path.exists(tmp):
                    os.remove(tmp)
                self.close_connection = True
                return self.send_json({"error": f"upload failed: {e}"}, 500)
            return self.send_json({"ok": True, "name": os.path.basename(dest)})

        if url.path == "/delete":
            full = self.resolve(name)
            if not full:
                return self.send_json({"error": "not found"}, 404)
            os.remove(full)
            try:  # forget its read status
                set_read([os.path.relpath(full, os.path.realpath(BOOKS_DIR)).replace(os.sep, "/")], False)
            except OSError:
                pass
            return self.send_json({"ok": True})

        self.send_error(404)

    def do_GET(self):
        if not self.authorized():
            return self.deny()

        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        base = self.base()

        # Browsers (which always ask for HTML) get the web page at "/"; OPDS
        # readers and everything else get the catalog there. /opds always works.
        if path == "/" and "text/html" in self.headers.get("Accept", ""):
            data = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Vary", "Accept")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)

        if path == "/api/books":
            return self.send_json(scan_books())

        if path in ("/opds", "/"):
            entries = nav_entry("All books", "/opds/all", "Every book, A-Z", base)
            entries += nav_entry("Recently added", "/opds/recent", "Newest 50 books", base)
            return self.send_xml(feed(LIBRARY_TITLE, "/opds", "nav", entries, base), NAV_TYPE)

        if path == "/opds/all":
            xml = "".join(book_entry(b, base) for b in scan_books())
            return self.send_xml(feed("All books", path, "acq", xml, base), ACQ_TYPE)

        if path == "/opds/recent":
            books = sorted(scan_books(), key=lambda b: b["mtime"], reverse=True)[:50]
            xml = "".join(book_entry(b, base) for b in books)
            return self.send_xml(feed("Recently added", path, "acq", xml, base), ACQ_TYPE)

        if path == "/opds/search":
            q = parse_qs(url.query).get("q", [""])[0].lower().strip()
            books = [
                b for b in scan_books() if q in b["title"].lower() or q in b["author"].lower()
            ]
            xml = "".join(book_entry(b, base) for b in books)
            return self.send_xml(feed(f"Search: {q}", self.path, "acq", xml, base), ACQ_TYPE)

        if path == "/opds/opensearch.xml":
            body = f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <ShortName>{LIBRARY_TITLE}</ShortName>
  <Description>Search {LIBRARY_TITLE}</Description>
  <Url type="{ACQ_TYPE}" template="{base}/opds/search?q={{searchTerms}}"/>
</OpenSearchDescription>
"""
            return self.send_xml(body, "application/opensearchdescription+xml")

        if path.startswith("/download/"):
            return self.send_book(unquote(path[len("/download/"):]))

        self.send_error(404)

    def send_book(self, rel):
        full = self.resolve(rel)
        if not full:
            return self.send_error(404)
        ext = os.path.splitext(full)[1].lower()
        ctype = MIME.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        size = os.path.getsize(full)
        fname = os.path.basename(full)
        ascii_name = re.sub(r'[^\x20-\x7e]|["\\]', "_", fname)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(fname)}",
        )
        self.end_headers()
        with open(full, "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def main():
    global BOOKS_DIR
    p = argparse.ArgumentParser()
    p.add_argument("--books", default="books", help="folder containing your books")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    a = p.parse_args()
    BOOKS_DIR = a.books
    os.makedirs(BOOKS_DIR, exist_ok=True)
    try:
        probe = os.path.join(BOOKS_DIR, f".probe-{uuid.uuid4().hex}")
        open(probe, "wb").close()
        os.remove(probe)
    except OSError as e:
        print(
            f"WARNING: {os.path.abspath(BOOKS_DIR)} is not writable ({e}); uploads will fail.\n"
            f"         The server runs as UID {os.getuid()}. Fix the folder's owner, e.g.: "
            f"chown -R {os.getuid()}:{os.getgid()} <folder>"
        )
    print(f"{APP_NAME}: serving {os.path.abspath(BOOKS_DIR)}")
    print(f"Web page and OPDS catalog: http://{lan_ip()}:{a.port}  (catalog also at /opds)")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
