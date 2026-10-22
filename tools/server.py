"""Server del sito per la VPS: pagine statiche + salvataggio del menu dal pannello "Modifica menu".

- /modifica-menu.html e /api/* sono protetti da utente e password (finestra di accesso del browser).
- PUT /api/menu controlla il menu, fa una copia di sicurezza in data/backup/, salva data/menu.json
  e rigenera menu.html e la home con tools/build.py. Il sito si aggiorna subito.

Uso (dalla cartella del sito):
    GIARA_PASSWORD=una-password-lunga python3 tools/server.py [--porta 8080] [--host 127.0.0.1]
Utente predefinito "giara" (si cambia con GIARA_UTENTE). Davanti va messo Caddy o nginx per HTTPS:
vedi tools/VPS.md.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import threading
from datetime import date, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build  # noqa: E402

MENU = ROOT / "data" / "menu.json"
BACKUP = ROOT / "data" / "backup"
KEEP = 60  # copie di sicurezza da tenere
USER = os.environ.get("GIARA_UTENTE", "giara")
PASSWORD = os.environ.get("GIARA_PASSWORD", "")
PRIVATE = ("/modifica-menu.html", "/api/")
HIDDEN = ("/tools/", "/data/backup/")
LOCK = threading.Lock()


def version(raw):
    return hashlib.sha256(raw).hexdigest()[:20]


def text(v, campo, errs, obbligatorio=False):
    if v is None and not obbligatorio:
        return
    if not isinstance(v, str) or len(v) > 400 or (obbligatorio and not v.strip()):
        errs.append(campo)


def validate(data):
    """Restituisce l'elenco dei problemi (vuoto se il menu va bene)."""
    errs = []
    if not isinstance(data, dict) or not isinstance(data.get("categorie"), list) or not data["categorie"]:
        return ["il menu non ha categorie"]
    ids = set()
    for c in data["categorie"]:
        if not isinstance(c, dict):
            return ["categoria non valida"]
        nome = str(c.get("titolo") or "?")
        if not re.fullmatch(r"[a-z0-9-]{1,60}", str(c.get("id", ""))) or c["id"] in ids:
            errs.append(f'id della categoria "{nome}"')
        ids.add(c.get("id"))
        text(c.get("titolo"), "una categoria senza nome", errs, True)
        text(c.get("breve"), f'nome breve di "{nome}"', errs)
        text(c.get("nota"), f'nota di "{nome}"', errs)
        if not isinstance(c.get("vetrina", True), bool) or not isinstance(c.get("voci"), list):
            errs.append(f'categoria "{nome}"')
            continue
        for v in c["voci"]:
            if not isinstance(v, dict):
                errs.append(f'una voce di "{nome}"')
                continue
            text(v.get("nome"), f'una voce senza nome in "{nome}"', errs, True)
            text(v.get("ingredienti"), f'ingredienti di "{v.get("nome")}"', errs)
            p = v.get("prezzo")
            if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p < 1000:
                errs.append(f'prezzo di "{v.get("nome")}"')
            if not isinstance(v.get("visibile", True), bool):
                errs.append(f'visibilità di "{v.get("nome")}"')
    return errs


def backup(raw):
    BACKUP.mkdir(parents=True, exist_ok=True)
    (BACKUP / f"menu-{datetime.now():%Y%m%d-%H%M%S}.json").write_bytes(raw)
    for old in sorted(BACKUP.glob("menu-*.json"))[:-KEEP]:
        old.unlink()


def write(raw):
    tmp = MENU.with_suffix(".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, MENU)


class Handler(SimpleHTTPRequestHandler):
    def route(self):
        return unquote(self.path.split("?", 1)[0].split("#", 1)[0])

    def end_headers(self):
        p = self.route()
        if p.startswith(PRIVATE) or p.endswith(".json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, code, obj, extra=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        h = self.headers.get("Authorization", "")
        if not PASSWORD or not h.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(h[6:]).decode("utf-8").partition(":")
        except ValueError:
            return False
        return hmac.compare_digest(user.encode(), USER.encode()) & hmac.compare_digest(pw.encode(), PASSWORD.encode())

    def gate(self):
        """False se la richiesta è stata respinta (file nascosto o accesso mancante)."""
        p = self.route()
        if any(s.startswith(".") for s in p.split("/")) or p.startswith(HIDDEN):
            self.send_error(404)
            return False
        if p.startswith(PRIVATE):
            if not PASSWORD:
                self.send_error(503, "Pannello disattivato: avvia il server con GIARA_PASSWORD")
                return False
            if not self.authorized():
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Modifica menu La Giara Art", charset="UTF-8"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
        return True

    def do_GET(self):
        if not self.gate():
            return
        p = self.route()
        if p == "/api/menu":
            raw = MENU.read_bytes()
            return self.send_json(200, json.loads(raw), {"X-Menu-Versione": version(raw)})
        if p.startswith("/api/"):
            return self.send_error(404)
        super().do_GET()

    def do_HEAD(self):
        if self.gate():
            super().do_HEAD()

    def do_PUT(self):
        if not self.gate():
            return
        if self.route() != "/api/menu":
            return self.send_error(404)
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= 1_000_000:
            return self.send_json(413, {"errore": "Menu vuoto o troppo grande."})
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self.send_json(400, {"errore": "Il menu inviato non è leggibile."})
        errs = validate(data)
        if errs:
            return self.send_json(422, {"errore": "Da sistemare: " + "; ".join(errs[:5]) + "."})
        data["aggiornato"] = date.today().isoformat()
        new = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with LOCK:
            old = MENU.read_bytes()
            if self.headers.get("X-Menu-Versione") != version(old):
                return self.send_json(412, {"errore": "Il menu è stato cambiato da un altro dispositivo. "
                                                      "Ricarica la pagina e rifai la modifica."})
            backup(old)
            write(new)
            try:
                build.main()
            except (SystemExit, Exception) as e:  # le pagine tornano come prima
                write(old)
                build.main()
                self.log_error("rigenerazione non riuscita: %s", e)
                return self.send_json(500, {"errore": "Non sono riuscito ad aggiornare le pagine. Il menu è rimasto quello di prima."})
        voci = sum(1 for c in data["categorie"] for v in c["voci"] if v.get("visibile", True))
        self.send_json(200, {"ok": True, "aggiornato": data["aggiornato"], "voci": voci}, {"X-Menu-Versione": version(new)})


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--porta", type=int, default=int(os.environ.get("GIARA_PORTA", 8080)))
    ap.add_argument("--host", default=os.environ.get("GIARA_HOST", "127.0.0.1"))
    a = ap.parse_args()
    if not PASSWORD:
        print("attenzione: GIARA_PASSWORD non impostata, il pannello Modifica menu resta chiuso")
    print(f"sito su http://{a.host}:{a.porta}/  ·  pannello: /modifica-menu.html (utente {USER})")
    ThreadingHTTPServer((a.host, a.porta), partial(Handler, directory=str(ROOT))).serve_forever()


if __name__ == "__main__":
    main()
