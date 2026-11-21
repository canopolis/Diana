"""Contabilidad: catalogo de cuentas, diario general, saldos y reportes (año continuo, estilo QuickBooks).

Convencion de montos (igual que el programa de contabilidad anterior):
  monto positivo = DEBITO, monto negativo = CREDITO.
Tipos de cuenta: 1 Activo, 2 Pasivo, 3 Ingreso, 4 Gasto, 5 Capital.
No hay cierre de año: el resultado de los años anteriores se calcula al vuelo y se presenta
dentro de la cuenta marcada como Ganancias retenidas. La proteccion es la fecha de bloqueo.
"""
import datetime, os, zipfile, tempfile, shutil, struct

TIPOS_CTA = {1: "Activo", 2: "Pasivo", 3: "Ingreso", 4: "Gasto", 5: "Capital"}

SCHEMA_GL = """
CREATE TABLE IF NOT EXISTS gl_accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT, type INTEGER,
  retained INTEGER DEFAULT 0, suspended INTEGER DEFAULT 0, notes TEXT DEFAULT '', old_id INTEGER);
CREATE TABLE IF NOT EXISTS gl_subs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, level INTEGER, code TEXT, name TEXT, suspended INTEGER DEFAULT 0,
  old_id INTEGER);
CREATE TABLE IF NOT EXISTS gl_jv(
  id INTEGER PRIMARY KEY AUTOINCREMENT, number INTEGER, year INTEGER, period INTEGER, date TEXT,
  descr TEXT, source TEXT DEFAULT 'GL JV', ref TEXT, amount REAL DEFAULT 0, auto_reverse INTEGER DEFAULT 0,
  reverse_of INTEGER DEFAULT 0, origin TEXT DEFAULT 'app', created TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_jv_ref ON gl_jv(ref) WHERE ref IS NOT NULL AND ref<>'';
CREATE INDEX IF NOT EXISTS ix_jv_num ON gl_jv(number);
CREATE INDEX IF NOT EXISTS ix_jv_per ON gl_jv(year, period);
CREATE TABLE IF NOT EXISTS gl_lines(
  jv_id INTEGER, seq INTEGER, account_id INTEGER, sub1 INTEGER, sub2 INTEGER, sub3 INTEGER,
  amount REAL, name TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_gll_jv ON gl_lines(jv_id);
CREATE INDEX IF NOT EXISTS ix_gll_acc ON gl_lines(account_id);
"""

ORIGEN = {"F": "Venta", "NC": "Nota de crédito", "ND": "Nota de débito", "G": "Conduce",
          "O": "Compra recibida", "PAGO": "Cobro", "APPAGO": "Pago a suplidor", "AJ": "Ajuste inventario",
          "REV": "Reverso"}


class Bloqueado(Exception):
    pass


def ymd(d):
    if isinstance(d, datetime.date):
        return d.isoformat()
    return d or ""


class Contab:
    def __init__(self, db):
        self.db = db
        self.preparar()

    @property
    def con(self):
        return self.db.con

    def preparar(self):
        """Crea las tablas de contabilidad si faltan (tambien despues de restaurar un respaldo)."""
        self.con.executescript(SCHEMA_GL)
        # "Ventas sin comprobantes" pasa a llamarse "Ventas libres" (solo el nombre de la cuenta)
        import re
        pat = re.compile(r"ventas?\s*(libres?\s*)?\(?\s*sin\s+comprobantes?\s*\)?", re.I)
        for tabla in ("gl_accounts", "gl_subs"):
            for r in self.con.execute(f"SELECT id, name FROM {tabla} WHERE name LIKE '%sin comprobante%'").fetchall():
                nuevo = pat.sub("Ventas libres", r[1] or "").strip()
                if nuevo != r[1]:
                    self.con.execute(f"UPDATE {tabla} SET name=? WHERE id=?", (nuevo, r[0]))
        # Corrección única (a pedido): la compra de la orden 367 (asiento 13650) se pagó con TC internacional;
        # el crédito de 286,705.00 pasa de 2034 Suplidores por pagar a 2033. No se crea ningún asiento nuevo.
        if not self.db.cfg("gl_fix_o367"):
            j = self.con.execute("SELECT id FROM gl_jv WHERE number=13650 AND ref='O:367' AND date='2026-11-18'").fetchone()
            de = self.con.execute("SELECT id FROM gl_accounts WHERE code='2034'").fetchone()
            a_ = self.con.execute("SELECT id FROM gl_accounts WHERE code='2033'").fetchone()
            if j and de and a_:
                n = self.con.execute("SELECT COUNT(*) FROM gl_lines WHERE jv_id=? AND account_id=? AND ABS(amount+286705.0)<0.005",
                                     (j[0], de[0])).fetchone()[0]
                if n == 1:
                    self.con.execute("UPDATE gl_lines SET account_id=? WHERE jv_id=? AND account_id=? AND ABS(amount+286705.0)<0.005",
                                     (a_[0], j[0], de[0]))
                    self.db.set_cfg("gl_fix_o367", "1")
        # Segunda corrección única (a pedido): en ese mismo asiento los cargos locales (59,551.00) se pagaron
        # con la cuenta 1006 y no con la 1004. Tampoco crea asiento nuevo.
        if not self.db.cfg("gl_fix_o367_local"):
            j = self.con.execute("SELECT id FROM gl_jv WHERE number=13650 AND ref='O:367' AND date='2026-11-18'").fetchone()
            de = self.con.execute("SELECT id FROM gl_accounts WHERE code='1004'").fetchone()
            a_ = self.con.execute("SELECT id FROM gl_accounts WHERE code='1006'").fetchone()
            if j and de and a_:
                n = self.con.execute("SELECT COUNT(*) FROM gl_lines WHERE jv_id=? AND account_id=? AND ABS(amount+59551.0)<0.005",
                                     (j[0], de[0])).fetchone()[0]
                if n == 1:
                    self.con.execute("UPDATE gl_lines SET account_id=? WHERE jv_id=? AND account_id=? AND ABS(amount+59551.0)<0.005",
                                     (a_[0], j[0], de[0]))
                    self.db.set_cfg("gl_fix_o367_local", "1")
        # Tercera corrección única (a pedido): compras del 18/11/2026 (órdenes 365 Linvet, 366 Reliq y 367 KVP).
        # Costos según la hoja de costos (cada flete a su suplidor), asientos con lo realmente pagado por cada cuenta,
        # fichas con costo promedio y se eliminan los tres asientos de ajuste que dejan de hacer falta.
        # Solo se aplica si TODO está exactamente como se revisó; si algo cambió, no hace nada.
        if not self.db.cfg("gl_fix_compras_1811"):
            try:
                self._fix_compras_1811()
            except Exception:
                self.con.rollback()
        self.con.commit()
        # Orden 365 (Linvet): las 15 piezas del A609-2 se separan en 12 del A609-2 y 3 del A609-3 nuevo, con los costos
        # aprobados el 20/11/2026. El total de la orden no cambia. Solo se aplica si TODO está exactamente como se revisó.
        if not self.db.cfg("gl_fix_linvet_a609"):
            try:
                self._fix_linvet_a609()
            except Exception:
                self.con.rollback()
        self.con.commit()
        # Orden 365 (Linvet): a pedido (21/11/2026) se restauran los costos originales ya puestos aquí, sin pasar costo
        # de un producto a otro: A609-2 a 10,341.65 y A609-3 a 31,024.95. El total de la orden y el asiento no cambian.
        if self.db.cfg("gl_fix_linvet_a609") and not self.db.cfg("gl_fix_linvet_orig"):
            try:
                self._fix_linvet_orig()
            except Exception:
                self.con.rollback()
        self.con.commit()
        self.con.execute("CREATE TABLE IF NOT EXISTS cost_transfers(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, "
                         "origen TEXT, monto REAL, detalle TEXT, jv_id INTEGER, reversada INTEGER DEFAULT 0)")
        self.con.commit()

    def _fix_linvet_orig(self):
        q = self.con.execute
        ls = sorted(tuple(r) for r in q("SELECT code, qty, price, cost_local FROM olines WHERE kind='O' AND nro=365"))
        ok = [("A609-2", 12.0, 13063.14, 2102.04), ("A609-3", 3.0, 20138.99, 3240.65)]
        if len(ls) != 2 or any(a[0] != b[0] or abs(a[1] - b[1]) > 1e-6 or abs(a[2] - b[2]) > 0.005
                               or abs((a[3] or 0) - b[3]) > 0.005 for a, b in zip(ls, ok)): return
        h = q("SELECT total, status, deleted FROM odocs WHERE kind='O' AND nro=365").fetchone()
        if not h or abs(h[0] - 217174.65) > 0.005 or h[1] != "Recibida" or h[2]: return
        for code, c in (("A609-2", 13063.14), ("A609-3", 20138.99)):
            p = q("SELECT cost, stock_base FROM products WHERE code=?", (code,)).fetchone()
            if not p or abs((p[0] or 0) - c) > 0.005 or abs(p[1] or 0) > 1e-6: return
        if q("SELECT COUNT(*) FROM lines WHERE code IN ('A609-2','A609-3')").fetchone()[0]: return
        mv = sorted(tuple(r) for r in q("SELECT code, nro, qty, kind FROM stock_moves WHERE code IN ('A609-2','A609-3')"))
        if mv != [("A609-2", 365, 12.0, "O"), ("A609-3", 365, 3.0, "O")]: return
        if round(12 * 10341.65 + 3 * 31024.95, 2) != 217174.65: return
        for code, pr, loc in (("A609-2", 10341.65, 1664.12), ("A609-3", 31024.95, 4992.37)):
            q("UPDATE olines SET price=?, cost_local=? WHERE kind='O' AND nro=365 AND code=?", (pr, loc, code))
            q("UPDATE stock_moves SET cost=? WHERE kind='O' AND nro=365 AND code=?", (pr, code))
            q("UPDATE products SET cost=? WHERE code=?", (pr, code))
        self.db.set_cfg("gl_fix_linvet_orig", "1")

    def _fix_linvet_a609(self):
        q = self.con.execute
        ls = [tuple(r) for r in q("SELECT pos, code, qty, price, cost_local FROM olines WHERE kind='O' AND nro=365")]
        if len(ls) != 1: return
        pos, code, qty, price, loc = ls[0]
        if code != "A609-2" or abs(qty - 15) > 1e-6 or abs(price - 14478.31) > 0.005 or abs((loc or 0) - 2329.77) > 0.005: return
        h = q("SELECT total, status, deleted FROM odocs WHERE kind='O' AND nro=365").fetchone()
        if not h or abs(h[0] - 217174.65) > 0.005 or h[1] != "Recibida" or h[2]: return
        p = q("SELECT cost, stock_base, itbis, category FROM products WHERE code='A609-2'").fetchone()
        if not p or abs((p[0] or 0) - 14478.31) > 0.005 or abs(p[1] or 0) > 1e-6: return
        if q("SELECT 1 FROM products WHERE code='A609-3'").fetchone(): return
        mv = [tuple(r) for r in q("SELECT nro, qty, kind FROM stock_moves WHERE code='A609-2'")]
        if mv != [(365, 15.0, "O")]: return
        if q("SELECT COUNT(*) FROM lines WHERE code IN ('A609-2','A609-3')").fetchone()[0]: return
        if q("SELECT COUNT(*) FROM olines WHERE code IN ('A609-2','A609-3')").fetchone()[0] != 1: return
        if q("SELECT COUNT(*) FROM stock_moves WHERE code='A609-3'").fetchone()[0]: return
        if round(12 * 13063.14 + 3 * 20138.99, 2) != 217174.65: return
        q("UPDATE olines SET qty=12, price=13063.14, cost_local=2102.04 WHERE kind='O' AND nro=365 AND pos=?", (pos,))
        q("INSERT INTO olines(kind,nro,pos,code,descr,qty,price,cost,itbis,received,cost_local) "
          "SELECT kind,nro,pos+1,'A609-3','Ajuste toracico quirurgico 3',3,20138.99,cost,itbis,received,3240.65 "
          "FROM olines WHERE kind='O' AND nro=365 AND pos=?", (pos,))
        q("UPDATE stock_moves SET qty=12 WHERE nro=365 AND code='A609-2' AND kind='O'")
        q("INSERT INTO stock_moves(nro,code,qty,kind,date,cost) SELECT nro,'A609-3',3,kind,date,cost FROM stock_moves "
          "WHERE nro=365 AND code='A609-2' AND kind='O'")
        q("UPDATE products SET cost=13063.14 WHERE code='A609-2'")
        q("INSERT INTO products(code,descr,price,price2,cost,stock_base,itbis,category,active,origin) "
          "VALUES('A609-3','Ajuste toracico quirurgico 3',0,0,20138.99,0,?,?,1,'app')", (p[2] or 0, p[3] or ""))
        self.db.set_cfg("gl_fix_linvet_a609", "1")

    def _fix_compras_1811(self):
        q = self.con.execute
        def _id(code):
            r = q("SELECT id FROM gl_accounts WHERE code=?", (code,)).fetchone()
            return r[0] if r else None
        def _jv(num, ref):
            r = (q("SELECT id FROM gl_jv WHERE number=? AND date='2026-11-18' AND ref IS NULL", (num,)) if ref is None else
                 q("SELECT id FROM gl_jv WHERE number=? AND date='2026-11-18' AND ref=?", (num, ref))).fetchone()
            return r[0] if r else None
        def _lin(jv):
            return sorted((r[0], round(r[1], 2)) for r in q("SELECT account_id, amount FROM gl_lines WHERE jv_id=?", (jv,)))
        inv, tc, mig, uxp, comp = _id("1200"), _id("2033"), _id("1006"), _id("3112"), _id("1113")
        jk, jr, jl = _jv(13650, "O:367"), _jv(13651, "O:366"), _jv(13652, "O:365")
        b1, b2, b3 = _jv(13653, None), _jv(13657, None), _jv(13661, None)
        if None in (inv, tc, mig, uxp, comp, jk, jr, jl, b1, b2, b3): return
        if not (_lin(jk) == sorted([(inv, 346256.0), (tc, -286705.0), (mig, -59551.0)]) and
                _lin(jr) == sorted([(inv, 90043.56), (tc, -74238.6), (mig, -15804.96)]) and
                _lin(jl) == sorted([(inv, 217101.9), (uxp, -178994.7), (mig, -38107.2)]) and
                _lin(b1) == sorted([(comp, -182229.0), (uxp, 182229.0)]) and
                _lin(b2) == sorted([(inv, -263280.18), (uxp, 263280.18)]) and
                _lin(b3) == sorted([(inv, 5582.0), (tc, -5582.0)])): return
        # código: (orden, cantidad, costo viejo en la orden, costo nuevo, costo local nuevo, costo viejo de la ficha)
        NUEVO = {"A609-2": (365, 15, 14473.46, 14478.31, 2329.77, 0.0),
                 "A603": (367, 100, 111.14, 103.17, 20.32, 96.0), "A604": (367, 400, 119.49, 110.92, 21.84, 103.0),
                 "A605": (367, 400, 128.63, 122.85, 24.19, 114.0), "A606": (367, 400, 149.04, 138.35, 27.24, 128.0),
                 "A607": (367, 400, 181.8, 168.77, 33.23, 156.0), "A608": (367, 200, 219.07, 203.36, 40.04, 188.0),
                 "A609": (367, 200, 298.72, 277.3, 54.6, 257.0),
                 "ML1GG": (366, 12, 2141.43, 2291.91, 391.43, 2230.94), "MLGTV1-1": (366, 30, 2144.88, 2291.91, 391.43, 2145.0)}
        TOT = {365: (217101.9, 217174.65), 366: (90043.56, 96260.22), 367: (346256.0, 322805.0)}
        for n, (viejo, _) in TOT.items():
            o = q("SELECT total, status, deleted FROM odocs WHERE kind='O' AND nro=?", (n,)).fetchone()
            if not o or o[2] or o[1] != "Recibida" or abs((o[0] or 0) - viejo) > 0.005: return
            if q("SELECT COUNT(*) FROM olines WHERE kind='O' AND nro=?", (n,)).fetchone()[0] != sum(1 for v in NUEVO.values() if v[0] == n): return
        for code, (n, cant, pv, pn, loc, cf) in NUEVO.items():
            l = q("SELECT qty, price FROM olines WHERE kind='O' AND nro=? AND code=?", (n, code)).fetchall()
            p = q("SELECT cost FROM products WHERE code=?", (code,)).fetchone()
            if len(l) != 1 or abs(l[0][0] - cant) > 1e-9 or abs(l[0][1] - pv) > 0.005 or not p or abs((p[0] or 0) - cf) > 0.005: return
        pagos = sorted((r[0], r[1], round(r[2], 2)) for r in q("SELECT id, nro, amount FROM ap_payments WHERE nro IN (365,366,367)"))
        if pagos != [(59, 365, 217110.0), (60, 367, 90047.04), (61, 366, 399446.0), (62, 367, 256208.96)]: return
        # --- 1) órdenes de compra: costos nuevos
        for code, (n, cant, pv, pn, loc, cf) in NUEVO.items():
            q("UPDATE olines SET price=?, cost_local=? WHERE kind='O' AND nro=? AND code=?", (pn, loc, n, code))
        for n, (_, nuevo) in TOT.items():
            q("UPDATE odocs SET subtotal=?, total=? WHERE kind='O' AND nro=?", (nuevo, nuevo, n))
        # --- 2) asientos de compra: cada cuenta con lo que de verdad se pagó (no se crean asientos)
        U = "UPDATE gl_lines SET amount=? WHERE jv_id=? AND account_id=?"
        q(U, (322805.0, jk, inv)); q(U, (-259239.40, jk, tc)); q(U, (-63565.60, jk, mig))          # KVP: factura US$4,222.14
        q(U, (96260.22, jr, inv)); q(U, (-79820.0, jr, tc)); q(U, (-16440.22, jr, mig))            # Reliq: factura US$1,300.00
        q(U, (217174.65, jl, inv)); q(U, (-34946.61, jl, mig))                                     # Linvet: ya pagado desde la 1004
        q("UPDATE gl_lines SET account_id=?, amount=? WHERE jv_id=? AND account_id=?", (comp, -182228.04, jl, uxp))
        for jv, (_, nuevo) in ((jk, TOT[367]), (jr, TOT[366]), (jl, TOT[365])):
            q("UPDATE gl_jv SET amount=? WHERE id=?", (nuevo, jv))
        # --- 3) asientos que ya no hacen falta
        for jv in (b1, b2, b3):
            q("DELETE FROM gl_lines WHERE jv_id=?", (jv,)); q("DELETE FROM gl_jv WHERE id=?", (jv,))
        # --- 4) pagos en Cuentas por pagar: cada orden pagada por su total
        q("UPDATE ap_payments SET amount=217174.65 WHERE id=59")
        q("UPDATE ap_payments SET amount=322805.0 WHERE id=60")
        q("UPDATE ap_payments SET amount=96260.22 WHERE id=61")
        q("DELETE FROM ap_payments WHERE id=62")
        q("CREATE TABLE IF NOT EXISTS gl_ya_en_diario(ref TEXT PRIMARY KEY, fecha TEXT)")
        q("DELETE FROM gl_ya_en_diario WHERE ref='APPAGO:62'")
        # --- 5) fichas: costo promedio (lo que había al costo viejo + lo comprado al costo nuevo)
        for code, (n, cant, pv, pn, loc, cf) in NUEVO.items():
            hay = self.db.stock(code)
            antes = hay - cant
            costo = pn if antes <= 0.005 or cf <= 0 else round((antes * cf + cant * pn) / hay, 2)
            q("UPDATE products SET cost=? WHERE code=?", (costo, code))
        self.db.set_cfg("gl_fix_compras_1811", "1")

    # ---------- configuracion ----------
    def arranque(self):
        return self.db.cfg("gl_arranque") or ""

    def set_arranque(self, d):
        self.db.set_cfg("gl_arranque", ymd(d))

    def bloqueo(self):
        return self.db.cfg("gl_bloqueo") or ""

    def set_bloqueo(self, d):
        self.db.set_cfg("gl_bloqueo", ymd(d) if d else "")

    def bloqueada(self, fecha):
        b = self.bloqueo()
        return bool(b) and ymd(fecha) <= b

    def contabiliza(self, fecha):
        """True si un documento de Facturacion con esa fecha debe llevar asiento."""
        a = self.arranque()
        return bool(a) and ymd(fecha) >= a

    # ---------- cuentas ----------
    def cuentas(self, incluir_suspendidas=True):
        sql = "SELECT * FROM gl_accounts" + ("" if incluir_suspendidas else " WHERE suspended=0")
        return self.con.execute(sql + " ORDER BY code").fetchall()

    def cuenta(self, cid):
        return self.con.execute("SELECT * FROM gl_accounts WHERE id=?", (cid,)).fetchone()

    def cuenta_por_codigo(self, code):
        return self.con.execute("SELECT * FROM gl_accounts WHERE code=?", ((code or "").strip(),)).fetchone()

    def guardar_cuenta(self, cid, code, name, tipo, retained=0, suspended=0, notes=""):
        code = (code or "").strip(); name = (name or "").strip()
        if not code or not name:
            raise ValueError("Escriba el número y el nombre de la cuenta.")
        if tipo not in TIPOS_CTA:
            raise ValueError("Elija el tipo de cuenta.")
        otra = self.cuenta_por_codigo(code)
        if otra and otra["id"] != cid:
            raise ValueError(f"Ya existe la cuenta {code}.")
        if retained:
            if tipo != 5:
                raise ValueError("Solo una cuenta de Capital puede ser de Ganancias retenidas.")
            self.con.execute("UPDATE gl_accounts SET retained=0 WHERE id<>?", (cid or 0,))
        if cid:
            self.con.execute("UPDATE gl_accounts SET code=?,name=?,type=?,retained=?,suspended=?,notes=? WHERE id=?",
                             (code, name, tipo, int(retained), int(suspended), notes, cid))
        else:
            cur = self.con.execute("INSERT INTO gl_accounts(code,name,type,retained,suspended,notes) VALUES(?,?,?,?,?,?)",
                                   (code, name, tipo, int(retained), int(suspended), notes))
            cid = cur.lastrowid
        self.con.commit()
        return cid

    def cuenta_tiene_movimientos(self, cid):
        return self.con.execute("SELECT 1 FROM gl_lines WHERE account_id=? LIMIT 1", (cid,)).fetchone() is not None

    def borrar_cuenta(self, cid):
        if self.cuenta_tiene_movimientos(cid):
            raise ValueError("La cuenta tiene movimientos registrados. No se puede eliminar; puede bloquearla.")
        self.con.execute("DELETE FROM gl_accounts WHERE id=?", (cid,)); self.con.commit()

    def set_suspendida(self, cid, si):
        self.con.execute("UPDATE gl_accounts SET suspended=? WHERE id=?", (1 if si else 0, cid)); self.con.commit()

    def cuenta_retenidas(self):
        return self.con.execute("SELECT * FROM gl_accounts WHERE retained=1").fetchone()

    def subcuentas(self, level):
        return self.con.execute("SELECT * FROM gl_subs WHERE level=? ORDER BY code", (level,)).fetchall()

    # ---------- asientos ----------
    def siguiente_numero(self):
        r = self.con.execute("SELECT MAX(number) FROM gl_jv").fetchone()[0]
        return int(r or 0) + 1

    def asiento(self, jv_id):
        h = self.con.execute("SELECT * FROM gl_jv WHERE id=?", (jv_id,)).fetchone()
        ls = self.con.execute("SELECT l.*, a.code, a.name AS acc_name FROM gl_lines l LEFT JOIN gl_accounts a "
                              "ON a.id=l.account_id WHERE jv_id=? ORDER BY seq", (jv_id,)).fetchall()
        return h, ls

    def asiento_de(self, ref):
        r = self.con.execute("SELECT id FROM gl_jv WHERE ref=?", (ref,)).fetchone()
        return r["id"] if r else None

    def _validar(self, fecha, lineas, viejo_fecha=None, permitir_bloqueadas=False):
        if not lineas:
            raise ValueError("El asiento no tiene líneas.")
        for l in lineas:
            if not l.get("account_id"):
                raise ValueError("Hay una línea sin cuenta.")
            c = self.cuenta(l["account_id"])
            if not c:
                raise ValueError("Hay una línea con una cuenta que no existe.")
            if c["suspended"] and not permitir_bloqueadas:
                raise ValueError(f"La cuenta {c['code']} {c['name']} está bloqueada y no acepta movimientos.\n"
                                 "Reactívela en el Catálogo de cuentas para usarla.")
        deb = round(sum(l["amount"] for l in lineas if l["amount"] > 0), 2)
        cre = round(-sum(l["amount"] for l in lineas if l["amount"] < 0), 2)
        if abs(deb - cre) > 0.004:
            raise ValueError(f"El asiento no cuadra.\nDébitos {deb:,.2f}   Créditos {cre:,.2f}   "
                             f"Diferencia {deb - cre:,.2f}")
        if deb <= 0:
            raise ValueError("El asiento está en cero.")
        for f in (fecha, viejo_fecha):
            if f and self.bloqueada(f):
                raise Bloqueado(f"La fecha {f[8:10]}/{f[5:7]}/{f[:4]} está dentro del período bloqueado "
                                f"(hasta {self.bloqueo()[8:10]}/{self.bloqueo()[5:7]}/{self.bloqueo()[:4]}).")
        return deb

    def guardar_asiento(self, h, lineas, jv_id=None, permitir_bloqueadas=False):
        """h: dict(number, year, period, date 'AAAA-MM-DD', descr, source, ref, auto_reverse).
        lineas: [dict(account_id, sub1, sub2, sub3, amount, name)]. Devuelve el id."""
        lineas = [l for l in lineas if l.get("account_id") and abs(l.get("amount") or 0) > 0.0001]
        viejo = self.con.execute("SELECT * FROM gl_jv WHERE id=?", (jv_id,)).fetchone() if jv_id else None
        deb = self._validar(h["date"], lineas, viejo["date"] if viejo else None, permitir_bloqueadas)
        if not h.get("number"):
            h["number"] = viejo["number"] if viejo else self.siguiente_numero()
        dup = self.con.execute("SELECT id FROM gl_jv WHERE number=? AND id<>?", (h["number"], jv_id or 0)).fetchone()
        if dup:
            raise ValueError(f"Ya existe el asiento número {h['number']}.")
        d = datetime.date.fromisoformat(h["date"])
        year = int(h.get("year") or d.year); period = int(h.get("period") or d.month)
        vals = (h["number"], year, period, h["date"], h.get("descr", ""), h.get("source") or "GL JV",
                h.get("ref") or None, deb, int(bool(h.get("auto_reverse"))))
        if viejo:
            self.con.execute("UPDATE gl_jv SET number=?,year=?,period=?,date=?,descr=?,source=?,ref=?,amount=?,"
                             "auto_reverse=? WHERE id=?", vals + (jv_id,))
            self.con.execute("DELETE FROM gl_lines WHERE jv_id=?", (jv_id,))
            self._quitar_autoreverso(jv_id)
        else:
            jv_id = self.con.execute("INSERT INTO gl_jv(number,year,period,date,descr,source,ref,amount,auto_reverse,"
                                     "created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                     vals + (datetime.datetime.now().isoformat(timespec="seconds"),)).lastrowid
        for i, l in enumerate(lineas, 1):
            self.con.execute("INSERT INTO gl_lines(jv_id,seq,account_id,sub1,sub2,sub3,amount,name) VALUES(?,?,?,?,?,?,?,?)",
                             (jv_id, i, l["account_id"], l.get("sub1"), l.get("sub2"), l.get("sub3"),
                              round(l["amount"], 2), l.get("name", "")))
        if h.get("auto_reverse"):
            self._crear_autoreverso(jv_id)
        self.con.commit()
        return jv_id

    def _quitar_autoreverso(self, jv_id):
        for r in self.con.execute("SELECT id FROM gl_jv WHERE reverse_of=? AND source='GL JVR'", (jv_id,)).fetchall():
            self.con.execute("DELETE FROM gl_lines WHERE jv_id=?", (r["id"],))
            self.con.execute("DELETE FROM gl_jv WHERE id=?", (r["id"],))

    def _crear_autoreverso(self, jv_id):
        h, ls = self.asiento(jv_id)
        y, p = (h["year"], h["period"] + 1) if h["period"] < 12 else (h["year"] + 1, 1)
        fecha = datetime.date(y, p, 1).isoformat()
        if self.bloqueada(fecha):
            raise Bloqueado("El reverso automático caería en el período bloqueado.")
        nid = self.con.execute("INSERT INTO gl_jv(number,year,period,date,descr,source,amount,reverse_of,created) "
                               "VALUES(?,?,?,?,?,?,?,?,?)",
                               (self.siguiente_numero(), y, p, fecha, f"Reverso de {h['number']}: {h['descr']}",
                                "GL JVR", h["amount"], jv_id, datetime.datetime.now().isoformat(timespec="seconds"))).lastrowid
        for l in ls:
            self.con.execute("INSERT INTO gl_lines(jv_id,seq,account_id,sub1,sub2,sub3,amount,name) VALUES(?,?,?,?,?,?,?,?)",
                             (nid, l["seq"], l["account_id"], l["sub1"], l["sub2"], l["sub3"], -l["amount"], l["name"]))

    def borrar_asiento(self, jv_id):
        h = self.con.execute("SELECT * FROM gl_jv WHERE id=?", (jv_id,)).fetchone()
        if not h:
            return
        if self.bloqueada(h["date"]):
            raise Bloqueado("Ese asiento está en el período bloqueado.")
        self._quitar_autoreverso(jv_id)
        self.con.execute("DELETE FROM gl_lines WHERE jv_id=?", (jv_id,))
        self.con.execute("DELETE FROM gl_jv WHERE id=?", (jv_id,))
        self.con.commit()

    def reversar_ref(self, ref, fecha, descr):
        """Anulacion de un documento: crea el asiento contrario con fecha de la anulacion (una sola vez)."""
        jid = self.asiento_de(ref)
        if not jid:
            return None
        if self.asiento_de("REV:" + ref):
            return None
        h, ls = self.asiento(jid)
        lineas = [dict(account_id=l["account_id"], sub1=l["sub1"], sub2=l["sub2"], sub3=l["sub3"],
                       amount=-l["amount"], name=l["name"]) for l in ls]
        # el reverso se permite aunque alguna cuenta este bloqueada (solo deshace lo que ya existia)
        nid = self.guardar_asiento(dict(date=ymd(fecha), descr=descr, source=h["source"], ref="REV:" + ref), lineas,
                                   permitir_bloqueadas=True)
        # el documento queda libre por si se vuelve a crear con el mismo numero
        self.con.execute("UPDATE gl_jv SET ref=? WHERE id=?", (f"{ref}#anulado{jid}", jid))
        self.con.execute("UPDATE gl_jv SET ref=? WHERE id=?", (f"REV:{ref}#anulado{jid}", nid))
        self.con.commit()
        return nid

    # ---------- saldos ----------
    def _suma(self, where, args):
        r = self.con.execute("SELECT l.account_id, SUM(l.amount) s FROM gl_lines l JOIN gl_jv j ON j.id=l.jv_id "
                             f"WHERE {where} GROUP BY l.account_id", args).fetchall()
        return {x["account_id"]: x["s"] or 0 for x in r}

    def saldos_hasta(self, year, period):
        return self._suma("(j.year<? OR (j.year=? AND j.period<=?))", (year, year, period))

    def saldos_rango(self, y1, p1, y2, p2):
        return self._suma("(j.year*100+j.period) BETWEEN ? AND ?", (y1 * 100 + p1, y2 * 100 + p2))

    def balance_general(self, year, period):
        """Saldos al cierre del periodo. Los ingresos/gastos de años anteriores se suman a Ganancias retenidas;
        los del año en curso se presentan como Resultado del ejercicio."""
        s = self.saldos_hasta(year, period)
        antes = self.saldos_hasta(year - 1, 12)
        cts = {c["id"]: c for c in self.cuentas()}
        res_ant = sum(v for k, v in antes.items() if cts.get(k) and cts[k]["type"] in (3, 4))
        res_anio = sum(v for k, v in s.items() if cts.get(k) and cts[k]["type"] in (3, 4)) - res_ant
        filas = {1: [], 2: [], 5: []}
        ret = self.cuenta_retenidas()
        for c in self.cuentas():
            if c["type"] not in filas:
                continue
            v = s.get(c["id"], 0)
            if ret and c["id"] == ret["id"]:
                v += res_ant
            filas[c["type"]].append((c, v))
        if not ret:
            filas[5].append((None, res_ant))
        return filas, res_anio

    def estado_resultados(self, year, period):
        mes = self.saldos_rango(year, period, year, period)
        acum = self.saldos_rango(year, 1, year, period)
        out = {3: [], 4: []}
        for c in self.cuentas():
            if c["type"] in out:
                out[c["type"]].append((c, mes.get(c["id"], 0), acum.get(c["id"], 0)))
        return out

    def mayor(self, cid, d1, d2):
        """Movimientos de la cuenta entre dos fechas con saldo inicial y corrido."""
        c = self.cuenta(cid)
        ini = self.con.execute("SELECT COALESCE(SUM(l.amount),0) FROM gl_lines l JOIN gl_jv j ON j.id=l.jv_id "
                               "WHERE l.account_id=? AND j.date<?", (cid, d1)).fetchone()[0]
        if c and c["type"] in (3, 4):   # cuentas de resultado: empiezan en cero cada año
            ini = self.con.execute("SELECT COALESCE(SUM(l.amount),0) FROM gl_lines l JOIN gl_jv j ON j.id=l.jv_id "
                                   "WHERE l.account_id=? AND j.date<? AND j.date>=?", (cid, d1, d1[:4] + "-01-01")).fetchone()[0]
        movs = self.con.execute("SELECT j.id, j.number, j.date, j.descr, j.source, l.amount, l.name FROM gl_lines l "
                                "JOIN gl_jv j ON j.id=l.jv_id WHERE l.account_id=? AND j.date BETWEEN ? AND ? "
                                "ORDER BY j.date, j.number", (cid, d1, d2)).fetchall()
        return ini, movs

    def balanza(self, year, period):
        s = self.saldos_hasta(year, period)
        antes = self.saldos_hasta(year - 1, 12)
        out = []
        for c in self.cuentas():
            v = s.get(c["id"], 0)
            if c["type"] in (3, 4):
                v -= antes.get(c["id"], 0)
            out.append((c, v))
        ret = self.cuenta_retenidas()
        if ret:
            cts = {c["id"]: c for c in self.cuentas()}
            ra = sum(v for k, v in antes.items() if cts.get(k) and cts[k]["type"] in (3, 4))
            out = [(c, v + ra if c["id"] == ret["id"] else v) for c, v in out]
        return out

    # ---------- importacion de la contabilidad anterior ----------
    def importar_anterior(self, origen, progreso=lambda m: None):
        import dbisam
        tmp = None
        try:
            carpeta = origen
            if os.path.isfile(origen) and origen.lower().endswith(".zip"):
                tmp = tempfile.mkdtemp(prefix="conta_")
                with zipfile.ZipFile(origen) as z:
                    nombres = z.namelist()
                    # la carpeta que tiene GLAccnt.dat mas reciente sin entrar en subcarpetas de respaldo
                    cands = [n for n in nombres if n.lower().endswith("/glaccnt.dat") or n.lower() == "glaccnt.dat"]
                    if not cands:
                        raise ValueError("Ese .zip no tiene la contabilidad (no aparece GLAccnt.dat).")
                    cands.sort(key=lambda n: ("/backup/" in n.lower(), n.count("/")))
                    base = cands[0][:-len("GLAccnt.dat")]
                    for n in nombres:
                        if n.startswith(base) and "/" not in n[len(base):] and not n.endswith("/"):
                            z.extract(n, tmp)
                    carpeta = os.path.join(tmp, base)
            return self._importar_carpeta(carpeta, progreso)
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    def _importar_carpeta(self, B, progreso):
        import dbisam
        def tabla(n):
            p = os.path.join(B, n + ".dat")
            if not os.path.exists(p):
                for f in os.listdir(B):
                    if f.lower() == (n + ".dat").lower():
                        p = os.path.join(B, f); break
            return dbisam.rows(p)
        progreso("Leyendo el catálogo de cuentas...")
        accs = tabla("GLAccnt")
        if not accs:
            raise ValueError("No se encontró el catálogo de cuentas (GLAccnt.dat).")
        con = self.con
        con.execute("DELETE FROM gl_lines WHERE jv_id IN (SELECT id FROM gl_jv WHERE origin='importado')")
        con.execute("DELETE FROM gl_jv WHERE origin='importado'")
        mapa = {}
        for a in accs:
            ex = self.cuenta_por_codigo(a["GLAccount"])
            if ex:
                con.execute("UPDATE gl_accounts SET name=?,type=?,retained=?,suspended=?,old_id=? WHERE id=?",
                            (a["AccountName"], a["AccountType"], int(bool(a["RetainedEarnings"])),
                             int(bool(a["Suspended"])), a["GLAccountID"], ex["id"]))
                mapa[a["GLAccountID"]] = ex["id"]
            else:
                mapa[a["GLAccountID"]] = con.execute(
                    "INSERT INTO gl_accounts(code,name,type,retained,suspended,old_id) VALUES(?,?,?,?,?,?)",
                    (a["GLAccount"], a["AccountName"], a["AccountType"], int(bool(a["RetainedEarnings"])),
                     int(bool(a["Suspended"])), a["GLAccountID"])).lastrowid
        submap = {1: {}, 2: {}, 3: {}}
        for lv in (1, 2, 3):
            try:
                subs = tabla(f"GLAcSub{lv}")
            except Exception:
                subs = []
            for s in subs:
                if s.get("SubAccountID") is None:
                    continue
                ex = con.execute("SELECT id FROM gl_subs WHERE level=? AND code=?", (lv, s["SubAccount"])).fetchone()
                submap[lv][s["SubAccountID"]] = ex["id"] if ex else con.execute(
                    "INSERT INTO gl_subs(level,code,name,suspended,old_id) VALUES(?,?,?,?,?)",
                    (lv, s["SubAccount"], s["SubAccountName"], int(bool(s["Suspended"])), s["SubAccountID"])).lastrowid
        progreso("Leyendo los asientos del diario...")
        jvs = tabla("JV"); det = tabla("JVDet")
        por_jv = {}
        for d in det:
            por_jv.setdefault(d["JVID"], []).append(d)
        num_existentes = {r[0] for r in con.execute("SELECT number FROM gl_jv WHERE origin<>'importado'")}
        n_jv = n_lin = sin_lineas = descuadrados = 0
        movs = {}   # (cuenta_vieja, año, periodo) -> suma importada
        for j in sorted(jvs, key=lambda x: (x["JVNumber"] or 0)):
            if j.get("JVID") is None or j.get("GLYear") is None:
                continue
            if j["JVNumber"] in num_existentes:
                continue
            fecha = j["TransDate"] or datetime.date(j["GLYear"], j["GLPeriod"], 1)
            ls = sorted(por_jv.get(j["JVID"], []), key=lambda x: x["Seq"] or 0)
            src = f"{j['Source'] or 'GL'} {j['TransType'] or 'JV'}"
            jid = con.execute("INSERT INTO gl_jv(number,year,period,date,descr,source,amount,auto_reverse,origin) "
                              "VALUES(?,?,?,?,?,?,?,?,'importado')",
                              (j["JVNumber"], j["GLYear"], j["GLPeriod"], fecha.isoformat(), j["TransDescription"] or "",
                               src, j["JVAmount"] or 0, int(bool(j["AutoReverse"])))).lastrowid
            n_jv += 1
            if not ls:
                sin_lineas += 1
            if abs(sum((x["GLAmount"] or 0) for x in ls)) > 0.005:
                descuadrados += 1
            for i, x in enumerate(ls, 1):
                if x["GLAccountID"] not in mapa:
                    continue
                con.execute("INSERT INTO gl_lines(jv_id,seq,account_id,sub1,sub2,sub3,amount) VALUES(?,?,?,?,?,?,?)",
                            (jid, x["Seq"] or i, mapa[x["GLAccountID"]], submap[1].get(x["SubAccount1ID"]),
                             submap[2].get(x["SubAccount2ID"]), submap[3].get(x["SubAccount3ID"]), x["GLAmount"] or 0))
                k = (x["GLAccountID"], j["GLYear"], j["GLPeriod"])
                movs[k] = movs.get(k, 0) + (x["GLAmount"] or 0)
                n_lin += 1
        progreso("Cuadrando con los saldos de la contabilidad anterior...")
        aj = self._ajustes_saldos(tabla("GLActual"), accs, mapa, movs)
        con.commit()
        return dict(cuentas=len(accs), asientos=n_jv, lineas=n_lin, sin_lineas=sin_lineas,
                    descuadrados=descuadrados, ajustes=aj)

    def _ajustes_saldos(self, actual, accs, mapa, movs):
        """La contabilidad anterior guarda los saldos oficiales por cuenta, año y periodo (GLActual).
        Donde los asientos guardados no llegan a esos saldos (historial depurado en aquel programa),
        se crea un asiento 'Ajuste de importacion' por periodo para que todo quede exactamente igual."""
        tipo = {a["GLAccountID"]: a["AccountType"] for a in accs}
        ret_old = next((a["GLAccountID"] for a in accs if a["RetainedEarnings"]), None)
        ap = {}; op = {}
        for x in actual:
            if x.get("ActualBudget") != "A" or x.get("GLAccountID") is None:
                continue
            a, y = x["GLAccountID"], x["GLYear"]
            op[(a, y)] = op.get((a, y), 0) + (x["OpeningBal"] or 0)
            for p in range(1, 13):
                ap[(a, y, p)] = ap.get((a, y, p), 0) + (x[f"Amount{p}"] or 0)
        if not op:
            return 0
        anios = sorted({y for _, y in op})
        ajustes = {}   # (y, p) -> {cuenta_vieja: monto}
        def add(y, p, a, v):
            if abs(v) > 0.004:
                d = ajustes.setdefault((y, p), {}); d[a] = d.get(a, 0) + v
        # 1) movimientos por periodo
        for k in set(ap) | set(movs):
            a, y, p = k
            if y < anios[0] or a not in mapa:
                continue
            add(y, p, a, ap.get(k, 0) - movs.get(k, 0))
        # 2) saldos de apertura de cada año (cuentas de balance). Lo nuestro: saldo acumulado al final
        #    del año anterior; resultado del ejercicio anterior va a ganancias retenidas (virtual).
        acum = {}
        res_acum = 0.0
        for y in anios:
            for a in mapa:
                t = tipo.get(a)
                if t in (3, 4):
                    continue
                esperado = op.get((a, y), 0)
                nuestro = acum.get(a, 0) + (res_acum if a == ret_old else 0)
                add(y, 1, a, esperado - nuestro)
                acum[a] = acum.get(a, 0) + (esperado - nuestro)
            for a in mapa:
                tot = sum(ap.get((a, y, p), 0) for p in range(1, 13))
                if tipo.get(a) in (3, 4):
                    res_acum += tot
                else:
                    acum[a] = acum.get(a, 0) + tot
        n = 0
        for (y, p), d in sorted(ajustes.items()):
            d = {a: round(v, 4) for a, v in d.items() if abs(v) > 0.004}
            if not d:
                continue
            tot = sum(d.values())
            jid = self.con.execute("INSERT INTO gl_jv(number,year,period,date,descr,source,amount,origin) "
                                   "VALUES(NULL,?,?,?,?,?,?,'importado')",
                                   (y, p, datetime.date(y, p, 1).isoformat(),
                                    "Ajuste de importación (cuadre con saldos de la contabilidad anterior)",
                                    "AJ IMP", sum(v for v in d.values() if v > 0))).lastrowid
            for i, (a, v) in enumerate(sorted(d.items()), 1):
                self.con.execute("INSERT INTO gl_lines(jv_id,seq,account_id,amount) VALUES(?,?,?,?)",
                                 (jid, i, mapa[a], v))
            n += 1
        return n
