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
        self.con.commit()

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

    def _validar(self, fecha, lineas, viejo_fecha=None):
        if not lineas:
            raise ValueError("El asiento no tiene líneas.")
        for l in lineas:
            if not l.get("account_id"):
                raise ValueError("Hay una línea sin cuenta.")
            c = self.cuenta(l["account_id"])
            if not c:
                raise ValueError("Hay una línea con una cuenta que no existe.")
            if c["suspended"]:
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

    def guardar_asiento(self, h, lineas, jv_id=None):
        """h: dict(number, year, period, date 'AAAA-MM-DD', descr, source, ref, auto_reverse).
        lineas: [dict(account_id, sub1, sub2, sub3, amount, name)]. Devuelve el id."""
        lineas = [l for l in lineas if l.get("account_id") and abs(l.get("amount") or 0) > 0.0001]
        viejo = self.con.execute("SELECT * FROM gl_jv WHERE id=?", (jv_id,)).fetchone() if jv_id else None
        deb = self._validar(h["date"], lineas, viejo["date"] if viejo else None)
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
        nid = self.guardar_asiento(dict(date=ymd(fecha), descr=descr, source=h["source"], ref="REV:" + ref), lineas)
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
