"""Costeo de importación: reparte fletes, gestión y tributos entre los productos de una compra y crea las
órdenes de compra con el costo en origen y el costo local. Hace lo mismo que la hoja 'costos y gastos menores':
cada flete se carga a los productos de su suplidor; la gestión se reparte entre todos según su valor."""
import os, json, datetime
import tkinter as tk
from tkinter import ttk, messagebox

SCHEMA = """CREATE TABLE IF NOT EXISTS costeos(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, nombre TEXT, datos TEXT, ordenes TEXT DEFAULT '', creado TEXT)"""


def _n(s, d=0.0):
    s = str(s if s is not None else "").replace(",", "").replace("%", "").strip()
    if not s:
        return d
    return float(s)


def m2(v):
    return f"{v:,.2f}"


def nuevo():
    return dict(nombre="", fecha=datetime.date.today().isoformat(), tasa=0.0, gestion=0.0, flete_general=0.0,
                margen=30.0, suplidores=[], lineas=[])


def calcular(d):
    """Devuelve (lineas calculadas, totales). Montos por unidad de inventario en RD$.
    suplidor: nombre, flete (moneda de la factura), flete_rd (RD$), pagado_rd (RD$ pagados por la mercancía; 0 = usa
    precio x tasa). linea: suplidor, code, descr, qty, precio, desc (%), upi (unidades de la factura por cada unidad de
    inventario), arancel, selectivo, itbis (%)."""
    tasa = d.get("tasa") or 0
    if tasa <= 0:
        raise ValueError("Ponga la tasa de cambio.")
    sup = {s["nombre"]: s for s in d.get("suplidores", [])}
    L = []
    for l in d.get("lineas", []):
        qty = l.get("qty") or 0
        if qty <= 0:
            raise ValueError(f"La cantidad de '{l.get('descr') or l.get('code')}' debe ser mayor que cero.")
        if l.get("suplidor") not in sup:
            raise ValueError(f"El producto '{l.get('descr') or l.get('code')}' no tiene suplidor.")
        L.append(dict(l, g=qty * (l.get("precio") or 0) * (1 - (l.get("desc") or 0) / 100.0)))
    if not L:
        raise ValueError("Agregue al menos un producto.")
    por_sup = {}
    for l in L:
        por_sup[l["suplidor"]] = por_sup.get(l["suplidor"], 0) + l["g"]
    for l in L:   # mercancía pagada en RD$ (a otra tasa): el valor del suplidor es lo pagado
        pag = sup[l["suplidor"]].get("pagado_rd") or 0
        if pag > 0 and por_sup[l["suplidor"]] > 0:
            l["g"] = l["g"] / por_sup[l["suplidor"]] * (pag / tasa)
    por_sup = {}
    for l in L:
        por_sup[l["suplidor"]] = por_sup.get(l["suplidor"], 0) + l["g"]
    total_g = sum(l["g"] for l in L)
    if total_g <= 0:
        raise ValueError("Los productos no tienen precio.")
    ges = (d.get("gestion") or 0) / tasa
    fg = d.get("flete_general") or 0
    margen = (d.get("margen") or 0) / 100.0
    out = []
    T = dict(mercancia=0.0, fletes=0.0, gestion=0.0, arancel=0.0, selectivo=0.0, itbis=0.0, costo=0.0)
    for l in L:
        s = sup[l["suplidor"]]
        e = l["g"] / total_g
        fs = (s.get("flete") or 0) + (s.get("flete_rd") or 0) / tasa
        flete = e * fg + (l["g"] / por_sup[l["suplidor"]] * fs if por_sup[l["suplidor"]] > 0 else 0)
        cif = l["g"] + flete
        ar = cif * (l.get("arancel") or 0) / 100.0
        se = (cif + ar) * (l.get("selectivo") or 0) / 100.0
        it = (cif + ar + se) * (l.get("itbis") or 0) / 100.0
        upi = l.get("upi") or 1
        cant = l["qty"] / upi                      # unidades de inventario
        origen = cif * tasa / cant
        local = (e * ges + ar + se) * tasa / cant
        costo = origen + local
        out.append(dict(l, cant=cant, origen=origen, local=local, costo=costo, itbis_u=it * tasa / cant,
                        venta=costo / (1 - margen) if margen < 1 else 0, porc=e))
        T["mercancia"] += l["g"] * tasa; T["fletes"] += flete * tasa; T["gestion"] += e * ges * tasa
        T["arancel"] += ar * tasa; T["selectivo"] += se * tasa; T["itbis"] += it * tasa; T["costo"] += costo * cant
    T["por_suplidor"] = {}
    for o in out:
        p = T["por_suplidor"].setdefault(o["suplidor"], dict(origen=0.0, local=0.0))
        p["origen"] += o["origen"] * o["cant"]; p["local"] += o["local"] * o["cant"]
    return out, T


# ---------------------------------------------------------------- datos
def preparar(db):
    db.con.execute(SCHEMA); db.con.commit()


def guardar(db, cid, d):
    preparar(db)
    txt = json.dumps(d, ensure_ascii=False)
    if cid:
        db.con.execute("UPDATE costeos SET fecha=?, nombre=?, datos=? WHERE id=?", (d["fecha"], d["nombre"], txt, cid))
    else:
        cur = db.con.execute("INSERT INTO costeos(fecha,nombre,datos,creado) VALUES(?,?,?,?)",
                             (d["fecha"], d["nombre"], txt, datetime.datetime.now().isoformat(timespec="seconds")))
        cid = cur.lastrowid
    db.con.commit()
    return cid


def crear_ordenes(db, cid, d):
    """Una orden de compra (pendiente) por suplidor, con costo en origen y costo local. Devuelve los números."""
    lin, _ = calcular(d)
    for l in lin:
        if not (l.get("code") or "").strip() or not db.one("SELECT 1 FROM products WHERE code=?", (l["code"],)):
            raise ValueError(f"'{l.get('descr') or l.get('code')}' no tiene un código de producto de Diana.")
    gen = db.one("SELECT * FROM odocs WHERE kind='O' AND UPPER(name)='GENERICO' AND deleted=0 ORDER BY nro DESC LIMIT 1")
    nros = []
    for s in [x["nombre"] for x in d["suplidores"]]:
        ls = [l for l in lin if l["suplidor"] == s]
        if not ls:
            continue
        nro = db.next_odoc_no("O")
        h = dict(nro=nro, date=d["fecha"], due=d["fecha"], client=gen["client"] if gen else "",
                 name=gen["name"] if gen else s, dir1="", dir2="", dir3="", ship_to="", sdir1="", sdir2="", sdir3="",
                 rnc=(gen["rnc"] if gen else "") or "", terms=(gen["terms"] if gen else "") or "", ship_via="",
                 seller=s if gen else "", ref=f"Costeo {cid}", comment1="",
                 comment2=(gen["comment2"] if gen else "") or "", comment3=(gen["comment3"] if gen else "") or "",
                 discount=0, status="Pendiente", dos_costos=1)
        lines = []
        for l in ls:
            p = db.one("SELECT descr, cost FROM products WHERE code=?", (l["code"],))
            lines.append(dict(code=l["code"], descr=p["descr"], qty=round(l["cant"], 4), price=round(l["costo"], 2),
                              cost=p["cost"] or 0, itbis=0, cost_local=round(l["local"], 2)))
        db.save_odoc("O", h, lines, [])
        nros.append(nro)
    db.con.execute("UPDATE costeos SET ordenes=? WHERE id=?", (",".join(str(n) for n in nros), cid)); db.con.commit()
    return nros


def exportar_excel(db, cid, d, carpeta):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    lin, T = calcular(d)
    wb = Workbook(); ws = wb.active; ws.title = "Costeo"
    F = lambda **k: Font(name="Verdana", size=k.pop("size", 9), **k)
    verde = PatternFill("solid", fgColor="1F4E3D"); claro = PatternFill("solid", fgColor="EAF2EC")
    fino = Side(style="thin", color="C9D1CC"); caja = Border(left=fino, right=fino, top=fino, bottom=fino)
    ws["A1"] = f"Costeo de importación {cid}: {d.get('nombre', '')}"; ws["A1"].font = F(bold=True, size=11)
    ws["A2"] = f"Fecha {d['fecha'][8:10]}/{d['fecha'][5:7]}/{d['fecha'][:4]}   Tasa {d['tasa']:,.4f}   Gestión RD$ {m2(d.get('gestion') or 0)}"
    ws["A2"].font = F()
    cols = ["Suplidor", "Código", "Producto", "Cantidad", "Precio", "% desc.", "Unid. inventario", "Costo origen RD$",
            "Costo local RD$", "Costo total RD$", f"Venta {d.get('margen') or 0:g}% margen", "ITBIS RD$", "Total RD$",
            "30% beneficio", "35% beneficio", "40% beneficio", "50% beneficio"]
    for c, t in enumerate(cols, 1):
        x = ws.cell(4, c, t); x.font = F(bold=True, color="FFFFFF"); x.fill = verde; x.border = caja
        x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    r = 5
    for l in lin:
        vals = [l["suplidor"], l.get("code", ""), l.get("descr", ""), l["qty"], l.get("precio") or 0, l.get("desc") or 0,
                l["cant"], l["origen"], l["local"], l["costo"], l["venta"], l["itbis_u"], l["costo"] * l["cant"],
                l["costo"] * 1.42, l["costo"] * 1.53, l["costo"] * 1.66, l["costo"] / 0.5]   # mismos factores de la hoja
        for c, v in enumerate(vals, 1):
            x = ws.cell(r, c, v); x.font = F(bold=(c == 10)); x.border = caja
            if c >= 4: x.number_format = "#,##0.00"
            if c == 10: x.fill = claro
        r += 1
    x = ws.cell(r, 12, "Total"); x.font = F(bold=True)
    x = ws.cell(r, 13, T["costo"]); x.font = F(bold=True); x.number_format = "#,##0.00"; x.fill = claro
    r += 2
    ws.cell(r, 1, "Suplidor").font = F(bold=True); ws.cell(r, 2, "Flete").font = F(bold=True)
    ws.cell(r, 3, "Flete RD$").font = F(bold=True); ws.cell(r, 4, "Origen RD$").font = F(bold=True)
    ws.cell(r, 5, "Local RD$").font = F(bold=True)
    for s in d["suplidores"]:
        r += 1
        p = T["por_suplidor"].get(s["nombre"], dict(origen=0, local=0))
        for c, v in enumerate([s["nombre"], s.get("flete") or 0, s.get("flete_rd") or 0, p["origen"], p["local"]], 1):
            x = ws.cell(r, c, v); x.font = F()
            if c > 1: x.number_format = "#,##0.00"
    for c, w in zip("ABCDEFGHIJKLMNOPQ", (16, 12, 34, 11, 11, 9, 12, 16, 15, 15, 16, 12, 16, 14, 14, 14, 14)):
        ws.column_dimensions[c].width = w
    ws.row_dimensions[4].height = 32
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f"Costeo_{cid}_{d['fecha'].replace('-', '')}.xlsx")
    wb.save(ruta)
    return ruta


# ---------------------------------------------------------------- ventanas
def _form(parent, titulo, campos, valores):
    """campos: [(clave, etiqueta, opciones|None)]. Devuelve dict de textos o None."""
    top = tk.Toplevel(parent); top.title(titulo); top.transient(parent); top.grab_set()
    vs = {}; res = {}
    for i, (k, lab, ops) in enumerate(campos):
        ttk.Label(top, text=lab).grid(row=i, column=0, sticky="e", padx=6, pady=3)
        v = tk.StringVar(value=str(valores.get(k, "") if valores.get(k) is not None else ""))
        w = ttk.Combobox(top, textvariable=v, values=ops, width=46) if ops is not None else ttk.Entry(top, textvariable=v, width=48)
        w.grid(row=i, column=1, sticky="w", padx=6, pady=3); vs[k] = v
    def ok():
        for k, v in vs.items(): res[k] = v.get().strip()
        top.destroy()
    bf = ttk.Frame(top); bf.grid(row=len(campos), column=0, columnspan=2, pady=8)
    ttk.Button(bf, text="Guardar", command=ok).pack(side="left", padx=6)
    ttk.Button(bf, text="Cancelar", command=top.destroy).pack(side="left", padx=6)
    top.wait_window()
    return res or None


class CosteoWin(tk.Toplevel):
    COLS = [("suplidor", "Suplidor", 110, "w"), ("code", "Código", 80, "w"), ("descr", "Producto", 230, "w"),
            ("qty", "Cantidad", 70, "e"), ("precio", "Precio", 80, "e"), ("desc", "% desc.", 60, "e"),
            ("cant", "Unid. inv.", 70, "e"), ("origen", "Costo origen", 100, "e"), ("local", "Costo local", 95, "e"),
            ("costo", "Costo total", 100, "e"), ("venta", "Venta", 100, "e")]

    def __init__(self, app, cid=None):
        super().__init__(app.root)
        self.app, self.db = app, app.db
        preparar(self.db)
        self.cid = cid
        self.ordenes = ""
        self.d = nuevo()
        if cid:
            r = self.db.one("SELECT datos, ordenes FROM costeos WHERE id=?", (cid,))
            self.d = json.loads(r["datos"]); self.ordenes = r["ordenes"] or ""
        self.title("Costeo de importación"); self.geometry("1180x640")
        top = ttk.Frame(self, padding=8); top.pack(fill="x")
        self.v = {}
        for i, (k, lab, w) in enumerate((("nombre", "Nombre", 28), ("fecha", "Fecha (dd/mm/aaaa)", 12), ("tasa", "Tasa RD$", 9),
                                         ("gestion", "Gestión y cargos locales RD$", 13),
                                         ("flete_general", "Flete compartido", 10), ("margen", "Margen %", 6))):
            ttk.Label(top, text=lab).grid(row=0, column=2 * i, sticky="e", padx=(8, 3))
            self.v[k] = tk.StringVar(); ttk.Entry(top, textvariable=self.v[k], width=w).grid(row=0, column=2 * i + 1, sticky="w")
        self._a_pantalla()
        mid = ttk.Frame(self, padding=(8, 0)); mid.pack(fill="both", expand=True)
        izq = ttk.LabelFrame(mid, text="Suplidores y su flete", padding=4); izq.pack(side="left", fill="y")
        self.ts = ttk.Treeview(izq, columns=("n", "f", "fr", "p"), show="headings", height=12)
        for c, t, w in (("n", "Suplidor", 110), ("f", "Flete", 70), ("fr", "Flete RD$", 80), ("p", "Pagado RD$", 90)):
            self.ts.heading(c, text=t); self.ts.column(c, width=w, anchor="w" if c == "n" else "e")
        self.ts.pack(fill="y", expand=True)
        b = ttk.Frame(izq); b.pack(fill="x", pady=3)
        for t, c in (("Agregar", self.sup_add), ("Editar", self.sup_edit), ("Quitar", self.sup_del)):
            ttk.Button(b, text=t, command=c).pack(side="left", padx=2)
        self.ts.bind("<Double-1>", lambda e: self.sup_edit())
        der = ttk.LabelFrame(mid, text="Productos", padding=4); der.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.tl = ttk.Treeview(der, columns=[c[0] for c in self.COLS], show="headings")
        for k, t, w, a in self.COLS:
            self.tl.heading(k, text=t); self.tl.column(k, width=w, anchor=a)
        sb = ttk.Scrollbar(der, orient="vertical", command=self.tl.yview); self.tl.configure(yscrollcommand=sb.set)
        b = ttk.Frame(der); b.pack(side="bottom", fill="x", pady=3)
        sb.pack(side="right", fill="y"); self.tl.pack(fill="both", expand=True)
        for t, c in (("Agregar producto", self.lin_add), ("Editar", self.lin_edit), ("Quitar", self.lin_del)):
            ttk.Button(b, text=t, command=c).pack(side="left", padx=2)
        self.tl.bind("<Double-1>", lambda e: self.lin_edit())
        pie = ttk.Frame(self, padding=8); pie.pack(fill="x")
        self.lbl = ttk.Label(pie, text="", justify="left"); self.lbl.pack(side="left")
        for t, c in (("Cerrar", self.destroy), ("Exportar a Excel", self.excel), ("Crear órdenes de compra", self.ordenar),
                     ("Guardar", self.guardar), ("Calcular", self.pintar)):
            ttk.Button(pie, text=t, command=c).pack(side="right", padx=3)
        self.pintar(avisar=False)

    # ----- cabecera
    def _a_pantalla(self):
        d = self.d
        self.v["nombre"].set(d.get("nombre", ""))
        f = d.get("fecha") or datetime.date.today().isoformat()
        self.v["fecha"].set(f"{f[8:10]}/{f[5:7]}/{f[:4]}")
        for k in ("tasa", "gestion", "flete_general", "margen"):
            self.v[k].set(f"{d.get(k) or 0:g}")

    def _de_pantalla(self):
        d = self.d
        d["nombre"] = self.v["nombre"].get().strip()
        t = self.v["fecha"].get().strip().replace("-", "/").split("/")
        if len(t) != 3:
            raise ValueError("La fecha va como dd/mm/aaaa.")
        d["fecha"] = datetime.date(int(t[2]) if len(t[2]) == 4 else 2000 + int(t[2]), int(t[1]), int(t[0])).isoformat()
        for k in ("tasa", "gestion", "flete_general", "margen"):
            d[k] = _n(self.v[k].get())

    def pintar(self, avisar=True):
        try:
            self._de_pantalla()
        except ValueError as e:
            if avisar: messagebox.showwarning("Costeo", str(e), parent=self)
            return None
        self.ts.delete(*self.ts.get_children())
        for i, s in enumerate(self.d["suplidores"]):
            self.ts.insert("", "end", iid=str(i), values=(s["nombre"], m2(s.get("flete") or 0), m2(s.get("flete_rd") or 0), m2(s.get("pagado_rd") or 0)))
        self.tl.delete(*self.tl.get_children())
        res = None
        try:
            res = calcular(self.d)
        except ValueError as e:
            if avisar and self.d["lineas"]: messagebox.showwarning("Costeo", str(e), parent=self)
        if res:
            lin, T = res
            for i, l in enumerate(lin):
                self.tl.insert("", "end", iid=str(i), values=(l["suplidor"], l.get("code", ""), l.get("descr", ""), f"{l['qty']:g}",
                               f"{l.get('precio') or 0:,.4f}".rstrip("0").rstrip("."), f"{l.get('desc') or 0:g}", f"{l['cant']:g}",
                               m2(l["origen"]), m2(l["local"]), m2(l["costo"]), m2(l["venta"])))
            txt = (f"Mercancía {m2(T['mercancia'])}   Fletes {m2(T['fletes'])}   Gestión {m2(T['gestion'])}   "
                   f"Tributos al costo {m2(T['arancel'] + T['selectivo'])}   ITBIS {m2(T['itbis'])}\nCosto total RD$ {m2(T['costo'])}")
        else:
            for i, l in enumerate(self.d["lineas"]):
                self.tl.insert("", "end", iid=str(i), values=(l.get("suplidor", ""), l.get("code", ""), l.get("descr", ""), f"{l.get('qty') or 0:g}",
                               f"{l.get('precio') or 0:g}", f"{l.get('desc') or 0:g}", "", "", "", "", ""))
            txt = "Ponga la tasa, los suplidores y los productos, y pulse Calcular."
        if self.ordenes: txt += f"\nÓrdenes de compra creadas: {self.ordenes}"
        elif self.cid: txt += "\nPendiente: todavía no se han creado las órdenes de compra."
        self.lbl.config(text=txt)
        return res

    # ----- suplidores
    def _sup_form(self, s):
        r = _form(self, "Suplidor", [("nombre", "Suplidor", None), ("flete", "Flete en la moneda de la factura", None),
                                     ("flete_rd", "Flete pagado en RD$", None),
                                     ("pagado_rd", "Mercancía pagada en RD$ (si se pagó a otra tasa)", None)],
                  dict(s, flete=f"{s.get('flete') or 0:g}", flete_rd=f"{s.get('flete_rd') or 0:g}", pagado_rd=f"{s.get('pagado_rd') or 0:g}"))
        if not r: return None
        try:
            out = dict(nombre=r["nombre"], flete=_n(r["flete"]), flete_rd=_n(r["flete_rd"]), pagado_rd=_n(r["pagado_rd"]))
        except ValueError:
            messagebox.showwarning("Costeo", "Revise los montos.", parent=self); return None
        if not out["nombre"]:
            messagebox.showwarning("Costeo", "Ponga el nombre del suplidor.", parent=self); return None
        return out

    def sup_add(self):
        s = self._sup_form(dict(nombre=""))
        if not s: return
        if any(x["nombre"].lower() == s["nombre"].lower() for x in self.d["suplidores"]):
            messagebox.showwarning("Costeo", "Ese suplidor ya está en la lista.", parent=self); return
        self.d["suplidores"].append(s); self.pintar(False)

    def sup_edit(self):
        sel = self.ts.selection()
        if not sel: return
        i = int(sel[0]); viejo = self.d["suplidores"][i]["nombre"]
        s = self._sup_form(self.d["suplidores"][i])
        if not s: return
        self.d["suplidores"][i] = s
        for l in self.d["lineas"]:
            if l.get("suplidor") == viejo: l["suplidor"] = s["nombre"]
        self.pintar(False)

    def sup_del(self):
        sel = self.ts.selection()
        if not sel: return
        n = self.d["suplidores"][int(sel[0])]["nombre"]
        if any(l.get("suplidor") == n for l in self.d["lineas"]):
            messagebox.showwarning("Costeo", "Ese suplidor tiene productos. Quite primero sus productos.", parent=self); return
        del self.d["suplidores"][int(sel[0])]; self.pintar(False)

    # ----- productos
    def _lin_form(self, l):
        if not self.d["suplidores"]:
            messagebox.showwarning("Costeo", "Agregue primero el suplidor.", parent=self); return None
        prods = [f"{p['code']} | {p['descr']}" for p in self.db.q("SELECT code, descr FROM products WHERE active=1 ORDER BY descr")]
        actual = ""
        if l.get("code"):
            actual = next((x for x in prods if x.split(" | ")[0] == l["code"]), l["code"])
        r = _form(self, "Producto", [("suplidor", "Suplidor", [s["nombre"] for s in self.d["suplidores"]]),
                                     ("prod", "Producto de Diana", prods), ("qty", "Cantidad de la factura", None),
                                     ("precio", "Precio unitario de la factura", None), ("desc", "% descuento", None),
                                     ("upi", "Unidades de la factura por 1 de inventario", None),
                                     ("arancel", "% arancel", None), ("selectivo", "% selectivo", None), ("itbis", "% ITBIS", None)],
                  dict(suplidor=l.get("suplidor") or self.d["suplidores"][-1]["nombre"], prod=actual,
                       qty=f"{l.get('qty') or 0:g}", precio=f"{l.get('precio') or 0:g}", desc=f"{l.get('desc') or 0:g}",
                       upi=f"{l.get('upi') or 1:g}", arancel=f"{l.get('arancel') or 0:g}", selectivo=f"{l.get('selectivo') or 0:g}",
                       itbis=f"{l.get('itbis') or 0:g}"))
        if not r: return None
        try:
            code = r["prod"].split(" | ")[0].strip()
            p = self.db.one("SELECT descr FROM products WHERE code=?", (code,))
            out = dict(suplidor=r["suplidor"], code=code if p else "", descr=p["descr"] if p else r["prod"], qty=_n(r["qty"]),
                       precio=_n(r["precio"]), desc=_n(r["desc"]), upi=_n(r["upi"], 1) or 1, arancel=_n(r["arancel"]),
                       selectivo=_n(r["selectivo"]), itbis=_n(r["itbis"]))
        except ValueError:
            messagebox.showwarning("Costeo", "Revise las cantidades y los montos.", parent=self); return None
        if out["suplidor"] not in [s["nombre"] for s in self.d["suplidores"]]:
            messagebox.showwarning("Costeo", "Elija un suplidor de la lista.", parent=self); return None
        if out["qty"] <= 0:
            messagebox.showwarning("Costeo", "La cantidad debe ser mayor que cero.", parent=self); return None
        return out

    def lin_add(self):
        l = self._lin_form({})
        if l: self.d["lineas"].append(l); self.pintar(False)

    def lin_edit(self):
        sel = self.tl.selection()
        if not sel: return
        l = self._lin_form(self.d["lineas"][int(sel[0])])
        if l: self.d["lineas"][int(sel[0])] = l; self.pintar(False)

    def lin_del(self):
        sel = self.tl.selection()
        if sel: del self.d["lineas"][int(sel[0])]; self.pintar(False)

    # ----- acciones
    def guardar(self, callar=False):
        if self.pintar() is None: return False
        self.cid = guardar(self.db, self.cid, self.d)
        self.pintar(False)
        return True

    def ordenar(self):
        if self.ordenes:
            messagebox.showinfo("Costeo", f"Este costeo ya tiene sus órdenes de compra: {self.ordenes}.", parent=self); return
        if not self.guardar(): return
        try:
            nros = crear_ordenes(self.db, self.cid, self.d)
        except ValueError as e:
            messagebox.showwarning("Costeo", str(e), parent=self); return
        self.ordenes = ",".join(str(n) for n in nros)
        self.pintar(False); self.app.refresh_all()
        messagebox.showinfo("Costeo", f"Órdenes de compra creadas (pendientes): {self.ordenes}.\n"
                                      "Márquelas como recibidas cuando llegue la mercancía.", parent=self)

    def excel(self):
        if not self.guardar(): return
        try:
            ruta = exportar_excel(self.db, self.cid, self.d, os.path.join(self.db.dir, "Reportes"))
        except Exception as e:
            messagebox.showwarning("Costeo", f"No se pudo exportar: {e}", parent=self); return
        try:
            os.startfile(ruta)
        except Exception:
            messagebox.showinfo("Costeo", f"Guardado en:\n{ruta}", parent=self)


class ListaCosteos(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app, self.db = app, app.db
        preparar(self.db)
        self.title("Costeos de importación"); self.geometry("720x380")
        self.tv = ttk.Treeview(self, columns=("id", "f", "n", "o"), show="headings")
        for c, t, w in (("id", "Nro", 60), ("f", "Fecha", 90), ("n", "Nombre", 330), ("o", "Órdenes de compra", 200)):
            self.tv.heading(c, text=t); self.tv.column(c, width=w, anchor="e" if c == "id" else "w")
        b = ttk.Frame(self); b.pack(side="bottom", fill="x", pady=5)
        self.tv.pack(fill="both", expand=True, padx=6, pady=6)
        for t, c in (("Nuevo", self.nuevo), ("Abrir", self.abrir), ("Eliminar", self.borrar), ("Cerrar", self.destroy)):
            ttk.Button(b, text=t, command=c).pack(side="left", padx=4)
        self.tv.bind("<Double-1>", lambda e: self.abrir())
        self.cargar()

    def cargar(self):
        self.tv.delete(*self.tv.get_children())
        for r in self.db.q("SELECT id, fecha, nombre, ordenes FROM costeos ORDER BY id DESC"):
            f = r["fecha"] or ""
            self.tv.insert("", "end", iid=str(r["id"]), values=(r["id"], f"{f[8:10]}/{f[5:7]}/{f[:4]}" if len(f) == 10 else f,
                                                                r["nombre"], r["ordenes"] or "Pendiente"))

    def _abrir(self, cid):
        w = CosteoWin(self.app, cid); self.wait_window(w); self.cargar()

    def nuevo(self): self._abrir(None)

    def abrir(self):
        sel = self.tv.selection()
        if sel: self._abrir(int(sel[0]))

    def borrar(self):
        sel = self.tv.selection()
        if not sel: return
        r = self.db.one("SELECT ordenes FROM costeos WHERE id=?", (int(sel[0]),))
        extra = "\nLas órdenes de compra que creó NO se borran." if r and r["ordenes"] else ""
        if messagebox.askyesno("Costeo", f"¿Eliminar el costeo {sel[0]}?{extra}", parent=self):
            self.db.con.execute("DELETE FROM costeos WHERE id=?", (int(sel[0]),)); self.db.con.commit(); self.cargar()
