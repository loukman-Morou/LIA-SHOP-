#!/usr/bin/env python3
"""LIA SHOP. En local : SQLite + dossier uploads. En ligne : DATABASE_URL (Postgres) + SUPABASE_* (photos et vidéos)."""
import json, os, re, sys, secrets, sqlite3, hashlib, time, random, mimetypes, threading, traceback, urllib.request
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http import cookies
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
_e = os.path.join(ROOT, ".env")  # fichier .env facultatif (tests en local) ; sur Render on utilise les variables d'environnement
if os.path.isfile(_e):
    for _l in open(_e, encoding="utf-8"):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l: _k, _v = _l.split("=", 1); os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
DATA = os.environ.get("DATA_DIR", ROOT)
DBF, UP = os.path.join(DATA, "boutique.db"), os.path.join(DATA, "uploads")
PORT = int(os.environ.get("PORT", 8000))
PG = os.environ.get("DATABASE_URL", "").strip()
SBU = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SBK = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
SBB = os.environ.get("SUPABASE_BUCKET", "boutique").strip()
REMOTE = bool(SBU and SBK)
PUB = f"{SBU}/storage/v1/object/public/{SBB}/" if REMOTE else "/uploads/"
MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "mp4": "video/mp4", "webm": "video/webm"}
IK = ("logo", "logo_intro")
FAILS, _pool, _lock = {}, [], threading.Lock()
DEF = {"title": "LIA SHOP", "footer": "Chaque pièce est choisie avec soin pour sublimer votre style.", "logo": "/static/img/logo.webp",
       "logo_intro": "/static/img/logo_intro.webp"}
_img = "'self' data: blob:" + (" " + SBU if SBU else "")
CSP = (f"default-src 'self'; img-src {_img}; media-src {_img}; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
       "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
RL = {}
def limited(ip, key, n, sec):
    now = time.time(); k = (ip, key); L = [t for t in RL.get(k, []) if now - t < sec]
    if len(L) >= n: RL[k] = L; return True
    L.append(now); RL[k] = L; return False

# ---------- base de données (SQLite ou Postgres, même code) ----------
def _connect():
    if PG:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(PG, row_factory=dict_row, prepare_threshold=None, connect_timeout=10)
    c = sqlite3.connect(DBF); c.row_factory = lambda cur, row: {d[0]: row[i] for i, d in enumerate(cur.description)}; return c

class Conn:
    def __init__(s, raw=None): s.raw = raw or _connect()
    def execute(s, q, a=()): return s.raw.execute(q.replace("?", "%s") if PG else q, a)
    def commit(s): s.raw.commit()
    def close(s):
        try:
            if PG:
                s.raw.rollback()
                with _lock:
                    if len(_pool) < 6: _pool.append(s.raw); return
            s.raw.close()
        except Exception: pass

def db():
    while PG:
        with _lock: raw = _pool.pop() if _pool else None
        if raw is None: break
        try: raw.execute("SELECT 1"); raw.rollback(); return Conn(raw)
        except Exception:
            try: raw.close()
            except Exception: pass
    return Conn()

def put(c, k, v): c.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
def setting(c, k):
    r = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone(); return r["v"] if r else ""
def hpw(p, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 150000).hex()

def init():
    os.makedirs(UP, exist_ok=True)
    pk = "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY"
    c = db()
    for q in ["CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)",
              f"CREATE TABLE IF NOT EXISTS products(id {pk}, name TEXT, cat TEXT, price INTEGER, descr TEXT, images TEXT, stock INTEGER DEFAULT 1, visible INTEGER DEFAULT 1, views INTEGER DEFAULT 0, videos TEXT DEFAULT '', details TEXT DEFAULT '')",
              f"CREATE TABLE IF NOT EXISTS orders(id {pk}, code TEXT, items TEXT, name TEXT, phone TEXT, address TEXT, total INTEGER, status TEXT, created TEXT, delivered TEXT)",
              "CREATE TABLE IF NOT EXISTS visits(day TEXT PRIMARY KEY, n INTEGER DEFAULT 0)",
              "CREATE TABLE IF NOT EXISTS sessions(h TEXT PRIMARY KEY, exp BIGINT)"]:
        c.execute(q)
    if not PG:  # anciennes bases SQLite
        cols = [r["name"] for r in c.execute("PRAGMA table_info(products)")]
        for col in ("videos", "details"):
            if col not in cols: c.execute(f"ALTER TABLE products ADD COLUMN {col} TEXT DEFAULT ''")
    c.commit()
    if not c.execute("SELECT 1 FROM settings").fetchone():
        S = {"title": "LIA SHOP", "tagline": "Bijoux, parfums & tenues cousues main", "footer": DEF["footer"],
             "whatsapp": "22965685659", "instagram": "01_ornelia", "tiktok": "01_ornelia",
             "cats": json.dumps(["Bijoux", "Parfums", "Sacs", "Tenues", "Accessoires"]), "currency": "FCFA"}
        for k, v in S.items(): put(c, k, v)
        demo = [("Parure bracelets dorés", "Bijoux", 8000, "Bracelets finition or, à porter ensemble ou séparément.", "s6"),
                ("Coffret Romantic Lady", "Parfums", 12000, "Parfum, brume, crème et nettoyant visage.", "s11"),
                ("Eau de parfum Aphrodite's Secret", "Parfums", 15000, "Flacon 55 ml, notes douces et florales.", "s10"),
                ("Sac cabas avec pochette", "Sacs", 10000, "Grand sac épaule et sa pochette assortie.", "s5"),
                ("Gobelet Mood", "Accessoires", 6000, "Gobelet isotherme avec poignée, plusieurs couleurs.", "s7"),
                ("Lunettes de style", "Accessoires", 5000, "Montures roses, noires et dorées.", "s9"),
                ("Tenue cousue main", "Tenues", 18000, "Pièce unique, taille sur mesure possible.", "s8")]
        for n, ca, p, d, im in demo:
            c.execute("INSERT INTO products(name,cat,price,descr,images,videos,details,stock) VALUES(?,?,?,?,?,?,?,?)", (n, ca, p, d, json.dumps([f"/static/img/{im}.jpg"]), "[]", "", 5))
        c.commit()
    pw = os.environ.get("ADMIN_PASSWORD", "")
    if not setting(c, "admin") or (os.environ.get("RESET_ADMIN") == "1" and pw):
        if len(pw) < 10: sys.exit("ERREUR : définissez la variable ADMIN_PASSWORD (10 caractères minimum) avant le premier lancement.")
        put(c, "admin", hpw(pw)); c.execute("DELETE FROM sessions"); c.commit(); print("Mot de passe admin enregistré.")
    c.close()

# ---------- fichiers (Supabase Storage ou dossier local) ----------
def ok_url(u): u = str(u or ""); return u.startswith(("/uploads/", "/static/img/")) or (REMOTE and u.startswith(PUB))
def _sb(method, path, data=None, ctype=None):
    h = {"Authorization": "Bearer " + SBK, "apikey": SBK}
    if method == "POST": h["cache-control"] = "max-age=31536000"
    if ctype: h["Content-Type"] = ctype
    return urllib.request.urlopen(urllib.request.Request(f"{SBU}/storage/v1/{path}", data=data, method=method, headers=h), timeout=120).read()
def store(b, ext, base=None, thumb=False):
    name = (base or secrets.token_hex(8)) + ("_t" if thumb else "") + "." + ext
    if REMOTE: _sb("POST", f"object/{SBB}/{name}", b, MIME[ext]); return PUB + name
    open(os.path.join(UP, name), "wb").write(b); return "/uploads/" + name
def unstore(u):
    for x in [u] + ([re.sub(r"\.(webp|mp4|webm)$", "_t.webp", u)] if re.search(r"/[0-9a-f]{16}\.(webp|mp4|webm)$", u) else []):
        try:
            if REMOTE and x.startswith(PUB): _sb("DELETE", f"object/{SBB}/{x[len(PUB):]}")
            elif x.startswith("/uploads/"): os.remove(os.path.join(UP, os.path.basename(x)))
        except Exception: pass
def used(c, u):
    k = "%" + u + "%"
    return bool(c.execute("SELECT 1 FROM products WHERE images LIKE ? OR videos LIKE ?", (k, k)).fetchone() or
                c.execute("SELECT 1 FROM settings WHERE v LIKE ? AND k<>'admin'", (k,)).fetchone())
def purge(c, urls):  # supprime du stockage les fichiers qui ne servent plus nulle part
    for u in set(urls):
        if u and (u.startswith("/uploads/") or (REMOTE and u.startswith(PUB))) and not used(c, u): unstore(u)

def pub_settings(c):
    def g(k): v = setting(c, k); return "" if v == "-" else (v or DEF.get(k, ""))
    d = {k: g(k) for k in ("title", "tagline", "footer", "whatsapp", "instagram", "tiktok", "currency") + IK}
    d["cats"] = json.loads(setting(c, "cats") or "[]"); return d
def prod(r):
    d = dict(r); d["images"] = json.loads(d["images"] or "[]"); d["videos"] = json.loads(d.get("videos") or "[]")
    d["details"] = d.get("details") or ""; d["desc"] = d.pop("descr"); return d
def stats(c):
    os_ = [dict(r) for r in c.execute("SELECT * FROM orders")]; mois = time.strftime("%Y-%m"); today = time.strftime("%Y-%m-%d")
    done = [o for o in os_ if o["status"] == "livrée"]; cnt = {k: sum(1 for o in os_ if o["status"] == k) for k in ("en attente", "en livraison", "livrée", "annulée")}
    ca = sum(o["total"] for o in done); top = {}
    for o in done:
        for i in json.loads(o["items"]):
            t = top.setdefault(i["name"], [0, 0]); t[0] += i["qty"]; t[1] += i["qty"] * i["price"]
    days = [time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400 * k)) for k in range(13, -1, -1)]
    vis = {r["day"]: r["n"] for r in c.execute("SELECT * FROM visits ORDER BY day DESC LIMIT 60")}
    v30 = sum(list(vis.values())[:30])
    return {"counts": cnt, "ca": ca, "ca_month": sum(o["total"] for o in done if (o["delivered"] or "").startswith(mois)),
            "pending": sum(o["total"] for o in os_ if o["status"] in ("en attente", "en livraison")), "basket": round(ca / len(done)) if done else 0,
            "orders_total": len(os_), "visits30": v30, "visits_today": vis.get(today, 0), "conv": round(100 * len(os_) / v30, 1) if v30 else 0,
            "daily": [{"day": d, "orders": sum(1 for o in os_ if o["created"].startswith(d)), "ca": sum(o["total"] for o in done if (o["delivered"] or "").startswith(d)), "visits": vis.get(d, 0)} for d in days],
            "top": sorted(([k, v[0], v[1]] for k, v in top.items()), key=lambda x: -x[2])[:6],
            "low": [{"name": r["name"], "stock": r["stock"]} for r in c.execute("SELECT name,stock FROM products WHERE visible=1 AND stock<=2 ORDER BY stock")]}
def clean(s, n=200): return re.sub(r"[<>]", "", str(s or "")).strip()[:n]
def sh(t): return hashlib.sha256(t.encode()).hexdigest()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, obj, code=200, ctype="application/json", extra=None):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", len(b))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin"); self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if ctype in ("text/html", "text/html; charset=utf-8") or ctype == "application/json": self.send_header("Content-Security-Policy", CSP)
        if ctype == "application/json" and not (extra and "Cache-Control" in extra): self.send_header("Cache-Control", "no-store")
        if self.headers.get("X-Forwarded-Proto") == "https": self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        try: self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError): pass
    def body(self): return self.rfile.read(int(self.headers.get("Content-Length") or 0))
    def ip(self): return self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]
    def js(self):
        if int(self.headers.get("Content-Length") or 0) > 1_000_000: return {}
        try: return json.loads(self.body() or b"{}")
        except Exception: return {}
    def token(self):
        t = cookies.SimpleCookie(self.headers.get("Cookie", "")).get("sid"); return t.value if t else ""
    def admin(self, c):
        t = self.token(); r = c.execute("SELECT exp FROM sessions WHERE h=?", (sh(t),)).fetchone() if t else None
        return bool(r and r["exp"] > time.time())
    def file(self, path, cache=False):
        if not os.path.isfile(path): return self.send({"error": "introuvable"}, 404)
        data = open(path, "rb").read(); code = 200
        h = {"Cache-Control": "public,max-age=86400" if cache else "no-cache", "Accept-Ranges": "bytes"}
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and (m[1] or m[2]):
            s0 = int(m[1]) if m[1] else max(len(data) - int(m[2]), 0)
            e0 = min(int(m[2]), len(data) - 1) if (m[1] and m[2]) else len(data) - 1
            h["Content-Range"] = f"bytes {s0}-{e0}/{len(data)}"; data = data[s0:e0 + 1]; code = 206
        self.send(data, code, mimetypes.guess_type(path)[0] or "application/octet-stream", h)
    def static(self, p):
        if p == "/healthz": self.send(b"ok", 200, "text/plain"); return True
        if p == "/": self.file(f"{ROOT}/static/index.html"); return True
        if p == "/admin": self.file(f"{ROOT}/static/admin.html"); return True
        if p.startswith(("/static/", "/uploads/")):
            top = p.split("/")[1]; base = UP if top == "uploads" else os.path.join(ROOT, "static")
            f = os.path.normpath(os.path.join(base, *p.strip("/").split("/")[1:]))
            self.file(f, True) if os.path.commonpath([base, f]) == base else self.send({}, 403); return True
    def do_HEAD(self): self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
    def run(self, fn, p):
        c = None
        try: c = db(); fn(c, p)
        except Exception:
            traceback.print_exc()
            try: self.send({"error": "Erreur serveur, réessayez."}, 500)
            except Exception: pass
        finally:
            if c: c.close()
    def do_GET(self):
        p = self.path.split("?")[0]
        if not self.static(p): self.run(self.get, p)
    def do_POST(self):
        o = self.headers.get("Origin")
        if o and urlparse(o).netloc != self.headers.get("Host"): return self.send({"error": "Origine refusée."}, 403)
        self.run(self.post, self.path.split("?")[0])

    def get(self, c, p):
        if p == "/api/public":
            c.execute("INSERT INTO visits(day,n) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET n=visits.n+1", (str(date.today()),)); c.commit()
            return self.send({"settings": pub_settings(c), "products": [prod(r) for r in c.execute("SELECT * FROM products WHERE visible=1 ORDER BY id DESC")]})
        if p == "/api/admin/data":
            if not self.admin(c): return self.send({"error": "non autorisé"}, 401)
            s = pub_settings(c)
            return self.send({"settings": s, "products": [prod(r) for r in c.execute("SELECT * FROM products ORDER BY id DESC")],
                              "orders": [dict(r) for r in c.execute("SELECT * FROM orders ORDER BY id DESC")],
                              "visits": [dict(r) for r in c.execute("SELECT * FROM visits ORDER BY day DESC LIMIT 30")]})
        if p == "/api/admin/stats":
            if not self.admin(c): return self.send({"error": "non autorisé"}, 401)
            return self.send(stats(c))
        self.send({"error": "introuvable"}, 404)

    def post(self, c, p):
        if p == "/api/login":
            ip = self.ip(); f = FAILS.get(ip, (0, 0))
            if f[0] >= 5 and time.time() < f[1]: return self.send({"error": "Trop d'essais, réessayez dans 5 min."}, 429)
            pw = clean(self.js().get("password"), 100); hashed = setting(c, "admin")
            if secrets.compare_digest(hpw(pw, hashed.split("$")[0]), hashed):
                t = secrets.token_hex(24); FAILS.pop(ip, None)
                c.execute("DELETE FROM sessions WHERE exp<?", (int(time.time()),)); c.execute("INSERT INTO sessions(h,exp) VALUES(?,?)", (sh(t), int(time.time()) + 8 * 3600)); c.commit()
                sec = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
                return self.send({"ok": 1}, 200, extra={"Set-Cookie": f"sid={t}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{sec}"})
            FAILS[ip] = (f[0] + 1, time.time() + 300); return self.send({"error": "Mot de passe incorrect"}, 401)
        if p == "/api/order":
            if limited(self.ip(), "order", 8, 600): return self.send({"error": "Trop de commandes, réessayez dans quelques minutes."}, 429)
            d = self.js(); items, total = [], 0
            for it in d.get("items", [])[:30]:
                try: pid, q = int(it.get("id", 0)), max(1, min(int(it.get("qty", 1)), 20))
                except Exception: continue
                r = c.execute("SELECT * FROM products WHERE id=? AND visible=1", (pid,)).fetchone()
                if r and r["stock"] >= q: items.append({"id": r["id"], "name": r["name"], "price": r["price"], "qty": q}); total += r["price"] * q
            name, phone, addr = clean(d.get("name"), 80), re.sub(r"[^\d+ ]", "", str(d.get("phone", "")))[:20], clean(d.get("address"), 200)
            if not (items and name and len(phone) >= 8 and addr): return self.send({"error": "Complétez nom, téléphone, adresse et panier."}, 400)
            code = "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
            c.execute("INSERT INTO orders(code,items,name,phone,address,total,status,created) VALUES(?,?,?,?,?,?,?,?)",
                      (code, json.dumps(items), name, phone, addr, total, "en attente", time.strftime("%Y-%m-%d %H:%M")))
            c.commit(); return self.send({"code": code, "total": total})
        if p == "/api/track":
            if limited(self.ip(), "track", 30, 600): return self.send({"error": "Trop d'essais, réessayez dans quelques minutes."}, 429)
            r = c.execute("SELECT * FROM orders WHERE code=?", (clean(self.js().get("code"), 12).upper(),)).fetchone()
            if not r: return self.send({"error": "Code introuvable."}, 404)
            return self.send({"status": r["status"], "total": r["total"], "created": r["created"], "items": [{"name": i["name"], "qty": i["qty"]} for i in json.loads(r["items"])]})
        if p == "/api/confirm":
            if limited(self.ip(), "confirm", 15, 600): return self.send({"error": "Trop d'essais, réessayez dans quelques minutes."}, 429)
            code = clean(self.js().get("code"), 12).upper()
            r = c.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
            if not r or r["status"] != "en livraison": return self.send({"error": "Code invalide ou colis pas encore expédié."}, 400)
            for it in json.loads(r["items"]): c.execute("UPDATE products SET stock=CASE WHEN stock>? THEN stock-? ELSE 0 END WHERE id=?", (it["qty"], it["qty"], it["id"]))
            c.execute("UPDATE orders SET status='livrée', delivered=? WHERE id=?", (time.strftime("%Y-%m-%d %H:%M"), r["id"])); c.commit()
            return self.send({"ok": 1, "receipt": dict(c.execute("SELECT * FROM orders WHERE id=?", (r["id"],)).fetchone())})
        if not p.startswith("/api/admin/"): return self.send({"error": "introuvable"}, 404)
        if not self.admin(c): return self.send({"error": "non autorisé"}, 401)
        if p == "/api/admin/upload":
            if int(self.headers.get("Content-Length") or 0) > 52_000_000: return self.send({"error": "Fichier trop lourd (50 Mo max)."}, 413)
            b = self.body(); ext = None
            if b[:3] == b"\xff\xd8\xff": ext = "jpg"
            elif b[:4] == b"\x89PNG": ext = "png"
            elif b[:4] == b"RIFF" and b[8:12] == b"WEBP": ext = "webp"
            elif b[4:8] == b"ftyp": ext = "mp4"
            elif b[:4] == b"\x1a\x45\xdf\xa3": ext = "webm"
            if not ext or (ext in ("jpg", "png", "webp") and len(b) > 10_000_000): return self.send({"error": "Formats : JPG, PNG, WEBP (10 Mo), MP4, WEBM (50 Mo)."}, 400)
            base = (parse_qs(urlparse(self.path).query).get("name") or [""])[0]
            if base and (not re.fullmatch(r"[0-9a-f]{16}", base) or ext != "webp"): return self.send({"error": "Nom de miniature invalide."}, 400)
            try: return self.send({"url": store(b, ext, base or None, bool(base))})
            except Exception: traceback.print_exc(); return self.send({"error": "Envoi vers le stockage impossible. Vérifiez la configuration."}, 502)
        d = self.js(); old = []
        if p == "/api/admin/product":
            imgs = [u for u in d.get("images", []) if ok_url(u)][:36]; vids = [u for u in d.get("videos", []) if ok_url(u) and not str(u).startswith("/static/")][:6]
            pid = int(d.get("id") or 0)
            if pid:
                r = c.execute("SELECT images,videos FROM products WHERE id=?", (pid,)).fetchone()
                if r: old = json.loads(r["images"] or "[]") + json.loads(r["videos"] or "[]")
            v = (clean(d.get("name"), 80), clean(d.get("cat"), 40), max(0, int(d.get("price") or 0)), clean(d.get("desc"), 800), json.dumps(imgs),
                 max(0, int(d.get("stock") or 0)), 1 if d.get("visible", 1) else 0, json.dumps(vids), clean(d.get("details"), 3000))
            if pid: c.execute("UPDATE products SET name=?,cat=?,price=?,descr=?,images=?,stock=?,visible=?,videos=?,details=? WHERE id=?", v + (pid,))
            else: c.execute("INSERT INTO products(name,cat,price,descr,images,stock,visible,videos,details) VALUES(?,?,?,?,?,?,?,?,?)", v)
            old = [u for u in old if u not in imgs + vids]
        elif p == "/api/admin/delete":
            pid = int(d.get("id") or 0); r = c.execute("SELECT images,videos FROM products WHERE id=?", (pid,)).fetchone()
            if r: old = json.loads(r["images"] or "[]") + json.loads(r["videos"] or "[]")
            c.execute("DELETE FROM products WHERE id=?", (pid,))
        elif p == "/api/admin/order":
            if d.get("status") == "supprimer": c.execute("DELETE FROM orders WHERE id=? AND status='annulée'", (int(d.get("id") or 0),))
            elif d.get("status") in ("en livraison", "annulée"): c.execute("UPDATE orders SET status=? WHERE id=? AND status<>'livrée'", (d["status"], int(d.get("id") or 0)))
        elif p == "/api/admin/settings":
            old = [setting(c, k) for k in IK]
            for k in ("title", "tagline", "footer", "whatsapp", "instagram", "tiktok"):
                if k in d: put(c, k, clean(d[k], 160))
            for k in IK:
                if k in d:
                    v = str(d[k] or "")
                    if not v: put(c, k, "-")
                    elif ok_url(v): put(c, k, v)
            if "cats" in d: put(c, "cats", json.dumps([clean(x, 30) for x in d["cats"] if clean(x)][:12]))
        elif p == "/api/admin/logout": c.execute("DELETE FROM sessions WHERE h=?", (sh(self.token()),))
        elif p == "/api/admin/password":
            if len(str(d.get("password", ""))) < 10: return self.send({"error": "10 caractères minimum."}, 400)
            put(c, "admin", hpw(str(d["password"]))); c.execute("DELETE FROM sessions WHERE h<>?", (sh(self.token()),))
        c.commit(); purge(c, old); self.send({"ok": 1})

if __name__ == "__main__":
    init(); print(f"Boutique : http://localhost:{PORT}   Admin : http://localhost:{PORT}/admin   [{'Postgres' if PG else 'SQLite'} / fichiers {'Supabase' if REMOTE else 'locaux'}]")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
