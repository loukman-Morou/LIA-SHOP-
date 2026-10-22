#!/usr/bin/env python3
"""Boutique Ornelia - serveur sans dépendance (Python 3.8+). Lancer : python3 server.py"""
import json, os, re, secrets, sqlite3, hashlib, time, random, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http import cookies
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "boutique.db")
UP = os.path.join(ROOT, "uploads")
PORT = int(os.environ.get("PORT", 8000))
SESSIONS, FAILS = {}, {}

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def hpw(p, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 150000).hex()

def init():
    os.makedirs(UP, exist_ok=True)
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, name TEXT, cat TEXT, price INTEGER,
      descr TEXT, images TEXT, stock INTEGER DEFAULT 1, visible INTEGER DEFAULT 1, views INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, code TEXT, items TEXT, name TEXT, phone TEXT,
      address TEXT, total INTEGER, status TEXT, created TEXT, delivered TEXT);
    CREATE TABLE IF NOT EXISTS visits(day TEXT PRIMARY KEY, n INTEGER DEFAULT 0);""")
    cols = [r[1] for r in c.execute("PRAGMA table_info(products)")]
    for col in ("videos", "details"):
        if col not in cols: c.execute(f"ALTER TABLE products ADD COLUMN {col} TEXT DEFAULT ''")
    if c.execute("SELECT 1 FROM settings WHERE k='title' AND v='Ornelia'").fetchone():
        c.execute("UPDATE settings SET v='LIA SHOP' WHERE k='title'"); c.execute("REPLACE INTO settings VALUES('hero',?)", (json.dumps(["/static/img/h2.jpg", "/static/img/h3.jpg"]),))
    c.commit()
    if not c.execute("SELECT 1 FROM settings").fetchone():
        pw = "LiaShop2026!"
        S = {"admin": hpw(pw), "title": "LIA SHOP", "tagline": "Bijoux, parfums & tenues cousues main",
             "whatsapp": "22965685659", "instagram": "01_ornelia", "tiktok": "01_ornelia",
             "hero": json.dumps(["/static/img/h2.jpg", "/static/img/h3.jpg"]),
             "cats": json.dumps(["Bijoux", "Parfums", "Sacs", "Tenues", "Accessoires"]), "currency": "FCFA"}
        c.executemany("INSERT INTO settings VALUES(?,?)", S.items())
        demo = [("Parure bracelets dorés", "Bijoux", 8000, "Bracelets finition or, à porter ensemble ou séparément."),
                ("Coffret Romantic Lady", "Parfums", 12000, "Parfum, brume, crème et nettoyant visage."),
                ("Eau de parfum Aphrodite's Secret", "Parfums", 15000, "Flacon 55 ml, notes douces et florales."),
                ("Sac cabas avec pochette", "Sacs", 10000, "Grand sac épaule et sa pochette assortie."),
                ("Gobelet Mood", "Accessoires", 6000, "Gobelet isotherme avec poignée, plusieurs couleurs."),
                ("Lunettes de style", "Accessoires", 5000, "Montures roses, noires et dorées."),
                ("Tenue cousue main", "Tenues", 18000, "Pièce unique, taille sur mesure possible.")]
        imgs = ["s6", "s11", "s10", "s5", "s7", "s9", "s8"]
        for (n, ca, p, d), im in zip(demo, imgs):
            c.execute("INSERT INTO products(name,cat,price,descr,images) VALUES(?,?,?,?,?)",
                      (n, ca, p, d, json.dumps([f"/static/img/{im}.jpg"])))
        c.commit()
        print("\n=== PREMIER LANCEMENT ===\nMot de passe admin : %s\n(changez-le dans Admin > Sécurité)\n" % pw)
    c.close()

def setting(c, k): 
    r = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone(); return r[0] if r else ""

DEF = {"title": "LIA SHOP", "footer": "Chaque pièce est choisie avec soin pour sublimer votre style.", "logo": "/static/img/logo.png",
       "logo_intro": "/static/img/logo_intro.png", "intro_photo": "/static/img/h1.jpg", "bg_logo": "/static/img/logo_intro.png"}

def pub_settings(c):
    d = {k: (setting(c, k) or DEF.get(k, "")) for k in ("title", "tagline", "footer", "whatsapp", "instagram", "tiktok", "currency", "logo", "logo_intro", "intro_photo", "bg_logo")}
    d["hero"] = json.loads(setting(c, "hero") or "[]"); d["cats"] = json.loads(setting(c, "cats") or "[]"); return d

def prod(r):
    d = dict(r); d["images"] = json.loads(d["images"]); d["videos"] = json.loads(d.get("videos") or "[]"); d["details"] = d.get("details") or ""; d["desc"] = d.pop("descr"); return d

def clean(s, n=200): return re.sub(r"[<>]", "", str(s or "")).strip()[:n]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, obj, code=200, ctype="application/json", extra=None):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", len(b))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        try: self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError): pass
    def body(self): return self.rfile.read(int(self.headers.get("Content-Length") or 0))
    def js(self):
        try: return json.loads(self.body() or b"{}")
        except Exception: return {}
    def admin(self):
        ck = cookies.SimpleCookie(self.headers.get("Cookie", "")); t = ck.get("sid")
        return bool(t and SESSIONS.get(t.value, 0) > time.time())
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

    def do_GET(self):
        p = self.path.split("?")[0]; c = db()
        if p == "/": return self.file(f"{ROOT}/static/index.html")
        if p == "/admin": return self.file(f"{ROOT}/static/admin.html")
        if p.startswith(("/static/", "/uploads/")):
            base = os.path.join(ROOT, p.split("/")[1]); f = os.path.normpath(os.path.join(ROOT, *p.strip("/").split("/")))
            return self.file(f, True) if os.path.commonpath([base, f]) == base else self.send({}, 403)
        if p == "/api/public":
            c.execute("INSERT INTO visits(day,n) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET n=n+1", (str(date.today()),)); c.commit()
            ps = [prod(r) for r in c.execute("SELECT * FROM products WHERE visible=1 ORDER BY id DESC")]
            return self.send({"settings": pub_settings(c), "products": ps})
        if p == "/api/receipt":
            code = self.path.split("code=")[-1][:12]
            r = c.execute("SELECT * FROM orders WHERE code=? AND status='livrée'", (code,)).fetchone()
            return self.send(dict(r) if r else {"error": "Reçu disponible après confirmation de réception."}, 200 if r else 403)
        if p.startswith("/api/admin"):
            if not self.admin(): return self.send({"error": "non autorisé"}, 401)
            ps = [prod(r) for r in c.execute("SELECT * FROM products ORDER BY id DESC")]
            os_ = [dict(r) for r in c.execute("SELECT * FROM orders ORDER BY id DESC")]
            days = [dict(r) for r in c.execute("SELECT * FROM visits ORDER BY day DESC LIMIT 30")]
            s = pub_settings(c); s["hero_all"] = s["hero"]
            return self.send({"settings": s, "products": ps, "orders": os_, "visits": days})
        self.send({"error": "introuvable"}, 404)

    def do_POST(self):
        p = self.path.split("?")[0]; c = db()
        if p == "/api/login":
            ip = self.client_address[0]; f = FAILS.get(ip, (0, 0))
            if f[0] >= 5 and time.time() < f[1]: return self.send({"error": "Trop d'essais, réessayez dans 5 min."}, 429)
            pw = clean(self.js().get("password"), 100); salt = setting(c, "admin").split("$")[0]
            if hpw(pw, salt) == setting(c, "admin"):
                t = secrets.token_hex(24); SESSIONS[t] = time.time() + 8 * 3600; FAILS.pop(ip, None)
                return self.send({"ok": 1}, 200, extra={"Set-Cookie": f"sid={t}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800"})
            FAILS[ip] = (f[0] + 1, time.time() + 300); return self.send({"error": "Mot de passe incorrect"}, 401)
        if p == "/api/order":
            d = self.js(); items, total = [], 0
            for it in d.get("items", [])[:30]:
                r = c.execute("SELECT * FROM products WHERE id=? AND visible=1", (int(it.get("id", 0)),)).fetchone()
                q = max(1, min(int(it.get("qty", 1)), 20))
                if r and r["stock"] >= q: items.append({"id": r["id"], "name": r["name"], "price": r["price"], "qty": q}); total += r["price"] * q
            name, phone, addr = clean(d.get("name"), 80), re.sub(r"[^\d+ ]", "", str(d.get("phone", "")))[:20], clean(d.get("address"), 200)
            if not (items and name and len(phone) >= 8 and addr): return self.send({"error": "Complétez nom, téléphone, adresse et panier."}, 400)
            code = "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
            c.execute("INSERT INTO orders(code,items,name,phone,address,total,status,created) VALUES(?,?,?,?,?,?,?,?)",
                      (code, json.dumps(items), name, phone, addr, total, "en attente", time.strftime("%Y-%m-%d %H:%M")))
            c.commit(); return self.send({"code": code, "total": total})
        if p == "/api/confirm":
            d = self.js(); code = clean(d.get("code"), 12).upper()
            r = c.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
            if not r or r["status"] != "en livraison": return self.send({"error": "Code invalide ou colis pas encore expédié."}, 400)
            for it in json.loads(r["items"]): c.execute("UPDATE products SET stock=MAX(stock-?,0) WHERE id=?", (it["qty"], it["id"]))
            c.execute("UPDATE orders SET status='livrée', delivered=? WHERE id=?", (time.strftime("%Y-%m-%d %H:%M"), r["id"]))
            c.commit(); return self.send({"ok": 1, "receipt": dict(c.execute("SELECT * FROM orders WHERE id=?", (r["id"],)).fetchone())})
        if not p.startswith("/api/admin/"): return self.send({"error": "introuvable"}, 404)
        if not self.admin(): return self.send({"error": "non autorisé"}, 401)
        if p == "/api/admin/upload":
            if int(self.headers.get("Content-Length") or 0) > 90_000_000: return self.send({"error": "Fichier trop lourd (90 Mo max)."}, 413)
            b = self.body(); ext = None
            if b[:3] == b"\xff\xd8\xff": ext = "jpg"
            elif b[:4] == b"\x89PNG": ext = "png"
            elif b[:4] == b"RIFF" and b[8:12] == b"WEBP": ext = "webp"
            elif b[4:8] == b"ftyp": ext = "mp4"
            elif b[:4] == b"\x1a\x45\xdf\xa3": ext = "webm"
            if not ext or (ext in ("jpg", "png", "webp") and len(b) > 10_000_000): return self.send({"error": "Formats : JPG, PNG, WEBP, MP4, WEBM."}, 400)
            n = secrets.token_hex(8) + "." + ext; open(os.path.join(UP, n), "wb").write(b); return self.send({"url": "/uploads/" + n})
        d = self.js()
        if p == "/api/admin/product":
            imgs = json.dumps([u for u in d.get("images", []) if str(u).startswith(("/uploads/", "/static/img/"))][:36])
            vids = json.dumps([u for u in d.get("videos", []) if str(u).startswith("/uploads/")][:6])
            v = (clean(d.get("name"), 80), clean(d.get("cat"), 40), max(0, int(d.get("price") or 0)), clean(d.get("desc"), 800),
                 imgs, max(0, int(d.get("stock") or 0)), 1 if d.get("visible", 1) else 0, vids, clean(d.get("details"), 3000))
            if d.get("id"): c.execute("UPDATE products SET name=?,cat=?,price=?,descr=?,images=?,stock=?,visible=?,videos=?,details=? WHERE id=?", v + (int(d["id"]),))
            else: c.execute("INSERT INTO products(name,cat,price,descr,images,stock,visible,videos,details) VALUES(?,?,?,?,?,?,?,?,?)", v)
        elif p == "/api/admin/delete": c.execute("DELETE FROM products WHERE id=?", (int(d.get("id", 0)),))
        elif p == "/api/admin/order":
            st = d.get("status")
            if st in ("en livraison", "annulée"): c.execute("UPDATE orders SET status=? WHERE id=? AND status!='livrée'", (st, int(d.get("id", 0))))
        elif p == "/api/admin/settings":
            for k in ("title", "tagline", "footer", "whatsapp", "instagram", "tiktok"):
                if k in d: c.execute("REPLACE INTO settings VALUES(?,?)", (k, clean(d[k], 160)))
            for k in ("logo", "logo_intro", "intro_photo", "bg_logo"):
                if str(d.get(k, "")).startswith(("/uploads/", "/static/img/")): c.execute("REPLACE INTO settings VALUES(?,?)", (k, d[k]))
            if "hero" in d: c.execute("REPLACE INTO settings VALUES('hero',?)", (json.dumps([u for u in d["hero"] if str(u).startswith(("/uploads/", "/static/img/"))][:3]),))
            if "cats" in d: c.execute("REPLACE INTO settings VALUES('cats',?)", (json.dumps([clean(x, 30) for x in d["cats"] if clean(x)][:12]),))
        elif p == "/api/admin/password":
            if len(str(d.get("password", ""))) < 8: return self.send({"error": "8 caractères minimum."}, 400)
            c.execute("REPLACE INTO settings VALUES('admin',?)", (hpw(str(d["password"])),))
        c.commit(); self.send({"ok": 1})

if __name__ == "__main__":
    init(); print(f"Boutique : http://localhost:{PORT}   Admin : http://localhost:{PORT}/admin")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()