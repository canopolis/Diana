"""Reportes contables en PDF: Estado de resultados, Balance General, Balanza de comprobación,
Diario General y Mayor de una cuenta."""
import datetime
from reportes import Tabla, dmy

MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]


def n2(v):
    v = 0.0 if abs(v) < 0.005 else v
    return f"({-v:,.2f})" if v < 0 else f"{v:,.2f}"


def estado_resultados(gl, year, period, ruta):
    d = gl.estado_resultados(year, period)
    t = Tabla(gl.db, ruta, ["Estado de resultados", f"Período {period:02d}/{year} ({MESES[period]})"],
              [(20, "l", "", "Cuenta"), (440, "r", "Este", "período"), (585, "r", "Acumulado", "del año")], 9)
    tot = {}
    for tipo, titulo, sg in ((3, "Ingresos", -1), (4, "Gastos", 1)):
        t.text(20, titulo, True)
        sm = sa = 0
        for c, m, a in d[tipo]:
            if abs(m) < 0.005 and abs(a) < 0.005 and c["suspended"]:
                continue
            t.row([f"   {c['code']}  {c['name']}", n2(sg * m), n2(sg * a)])
            sm += sg * m; sa += sg * a
        t.line(); t.row([f"Total {titulo.lower()}", n2(sm), n2(sa)], True); t.space()
        tot[tipo] = (sm, sa)
    t.line()
    t.row(["Resultado (utilidad / pérdida)", n2(tot[3][0] - tot[4][0]), n2(tot[3][1] - tot[4][1])], True, 10)
    return t.save()


def balance_general(gl, year, period, ruta):
    filas, res = gl.balance_general(year, period)
    t = Tabla(gl.db, ruta, ["Balance General", f"Al cierre del período {period:02d}/{year}"],
              [(20, "l", "", "Cuenta"), (585, "r", "", "Saldo")], 9)
    def bloque(titulo, tipo, sg, extra=None):
        t.text(20, titulo, True)
        s = 0
        for c, v in filas[tipo]:
            if c is None:
                t.row(["   Ganancias retenidas (años anteriores)", n2(sg * v)]); s += sg * v; continue
            if abs(v) < 0.005 and c["suspended"]:
                continue
            nombre = f"   {c['code']}  {c['name']}" + ("  (incluye años anteriores)" if c["retained"] else "")
            t.row([nombre, n2(sg * v)]); s += sg * v
        if extra is not None:
            t.row(["   Resultado del ejercicio (año en curso)", n2(sg * extra)]); s += sg * extra
        t.line(); t.row([f"Total {titulo.lower()}", n2(s)], True); t.space()
        return s
    a = bloque("Activos", 1, 1)
    p = bloque("Pasivos", 2, -1)
    k = bloque("Capital", 5, -1, res)
    t.line(); t.row(["Total pasivos y capital", n2(p + k)], True, 10)
    if abs(a - (p + k)) > 0.01:
        t.row([f"DIFERENCIA: {n2(a - (p + k))}", ""], True)
    return t.save()


def balanza(gl, year, period, ruta):
    filas = gl.balanza(year, period)
    t = Tabla(gl.db, ruta, ["Balanza de comprobación", f"Al cierre del período {period:02d}/{year}"],
              [(20, "l", "", "Cuenta"), (330, "l", "", "Tipo"), (480, "r", "", "Débito"), (585, "r", "", "Crédito")], 9)
    from contab import TIPOS_CTA
    td = tc = 0
    for c, v in filas:
        if abs(v) < 0.005:
            continue
        t.row([f"{c['code']}  {c['name']}", TIPOS_CTA.get(c["type"], ""), n2(v) if v > 0 else "", n2(-v) if v < 0 else ""])
        if v > 0: td += v
        else: tc -= v
    t.line(); t.row(["Totales", "", n2(td), n2(tc)], True)
    return t.save()


def diario(gl, d1, d2, ruta):
    t = Tabla(gl.db, ruta, ["Diario General", f"Del {dmy(d1)} al {dmy(d2)}"],
              [(20, "l", "", "No."), (60, "l", "", "Fecha"), (115, "l", "", "Cuenta / Descripción"),
               (500, "r", "", "Débito"), (585, "r", "", "Crédito")], 8)
    td = tc = 0
    for j in gl.con.execute("SELECT * FROM gl_jv WHERE date BETWEEN ? AND ? ORDER BY date, number", (d1, d2)).fetchall():
        t.need(30)
        t.row([j["number"] or "AJ", dmy(j["date"]), f"{j['descr']}   [{j['source']}]"], True)
        for l in gl.con.execute("SELECT l.*, a.code, a.name an FROM gl_lines l LEFT JOIN gl_accounts a ON a.id=l.account_id "
                                "WHERE jv_id=? ORDER BY seq", (j["id"],)).fetchall():
            v = l["amount"]
            t.row(["", "", f"   {l['code']}  {l['an']}" + (f"  - {l['name']}" if l["name"] else ""),
                   n2(v) if v > 0 else "", n2(-v) if v < 0 else ""])
            if v > 0: td += v
            else: tc -= v
        t.space(3)
    t.line(); t.row(["", "", "Totales", n2(td), n2(tc)], True)
    return t.save()


def mayor(gl, cid, d1, d2, ruta):
    c = gl.cuenta(cid)
    ini, movs = gl.mayor(cid, d1, d2)
    t = Tabla(gl.db, ruta, [f"Mayor de la cuenta {c['code']} {c['name']}", f"Del {dmy(d1)} al {dmy(d2)}"],
              [(20, "l", "", "Fecha"), (75, "l", "", "No."), (115, "l", "", "Descripción"),
               (420, "r", "", "Débito"), (500, "r", "", "Crédito"), (585, "r", "", "Saldo")], 8)
    s = ini
    t.row(["", "", "Saldo inicial", "", "", n2(s)], True)
    td = tc = 0
    for m in movs:
        v = m["amount"]; s += v
        t.row([dmy(m["date"]), m["number"] or "AJ", (m["descr"] or "")[:60], n2(v) if v > 0 else "",
               n2(-v) if v < 0 else "", n2(s)])
        if v > 0: td += v
        else: tc -= v
    t.line(); t.row(["", "", "Totales / saldo final", n2(td), n2(tc), n2(s)], True)
    return t.save()
