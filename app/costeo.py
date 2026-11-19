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
class CosteoWin(tk.Toplevel):
    """Pantalla como la hoja de Excel: una tabla donde se escribe directo en las casillas amarillas, el desglose
    de cargos debajo y, al lado, los fletes por suplidor. Todo se recalcula solo al salir de cada casilla."""
    VERDE, CLARO, ENTRA, TXT = "#1F4E3D", "#EAF2EC", "#FFF9DB", "#26332C"
    # (clave, título, ancho, se escribe)
    COLS = [("prod", "Item #", 30, 1), ("precio", "price", 9, 1), ("qty", "cantidad", 8, 1), ("bruto", "Precio bruto", 11, 0),
            ("desc", "% descuento", 8, 1), ("g", "Costo Total", 11, 0), ("unit", "Costo unitario", 10, 0),
            ("costo", "COSTO RD$", 12, 0), ("b30", "30% beneficio", 11, 0), ("b35", "35% beneficio", 11, 0),
            ("b40", "40% beneficio", 11, 0), ("b50", "50% beneficio", 11, 0), ("venta", "Margen personalizado", 12, 0),
            ("origen", "Costo origen + shipping RD$", 13, 0), ("local", "Costo local RD$", 12, 0),
            ("suplidor", "Suplidor", 12, 1), ("upi", "Unid. por 1 de inventario", 9, 1),
            ("arancel", "% arancel", 7, 1), ("selectivo", "% selectivo", 7, 1), ("itbis", "% ITBIS", 7, 1)]
    FILAS, SUPS = 18, 6

    def __init__(self, app, cid=None):
        super().__init__(app.root)
        self.app, self.db = app, app.db
        preparar(self.db)
        self.cid, self.ordenes, self.d = cid, "", nuevo()
        if cid:
            r = self.db.one("SELECT datos, ordenes FROM costeos WHERE id=?", (cid,))
            self.d = json.loads(r["datos"]); self.ordenes = r["ordenes"] or ""
        self.title("Costeo de importación"); self.geometry("1320x700"); self.configure(bg="white")
        self.F = ("Verdana", 9); self.FB = ("Verdana", 9, "bold")
        self.prods = [f"{p['code']} | {p['descr']}" for p in self.db.q("SELECT code, descr FROM products WHERE active=1 ORDER BY descr")]
        # ----- fila de arriba
        top = tk.Frame(self, bg="white"); top.pack(fill="x", padx=10, pady=(8, 4))
        self.v = {}
        for k, lab, w in (("fecha", "FECHA", 11), ("nombre", "NOMBRE", 30), ("margen", "MARGEN %", 6)):
            tk.Label(top, text=lab, font=self.FB, bg=self.VERDE, fg="white", padx=6).pack(side="left", padx=(0, 0))
            self.v[k] = tk.StringVar()
            self._entrada(top, self.v[k], w).pack(side="left", padx=(0, 12))
        for t, c in (("Cerrar", self.destroy), ("Exportar a Excel", self.excel), ("Crear órdenes de compra", self.ordenar),
                     ("Guardar", self.guardar), ("Agregar filas", self.mas_filas)):
            ttk.Button(top, text=t, command=c).pack(side="right", padx=3)
        # ----- tabla principal (con barras de desplazamiento)
        marco = tk.Frame(self, bg="white"); marco.pack(fill="both", expand=True, padx=10)
        self.cv = tk.Canvas(marco, bg="white", highlightthickness=0)
        sy = ttk.Scrollbar(marco, orient="vertical", command=self.cv.yview); sx = ttk.Scrollbar(marco, orient="horizontal", command=self.cv.xview)
        self.cv.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side="right", fill="y"); sx.pack(side="bottom", fill="x"); self.cv.pack(fill="both", expand=True)
        self.tab = tk.Frame(self.cv, bg="#C9D1CC"); self.cv.create_window((0, 0), window=self.tab, anchor="nw")
        self.tab.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        for c, (k, t, w, _) in enumerate(self.COLS):
            tk.Label(self.tab, text=t, font=self.FB, bg=self.VERDE, fg="white", width=w, wraplength=w * 8, height=3,
                     justify="center").grid(row=0, column=c, padx=(0, 1), pady=(0, 1), sticky="nsew")
        self.filas = []
        for _ in range(max(self.FILAS, len(self.d["lineas"]))): self._fila()
        # ----- abajo: desglose de cargos y fletes por suplidor
        bajo = tk.Frame(self, bg="white"); bajo.pack(fill="x", padx=10, pady=8)
        dg = tk.Frame(bajo, bg="#C9D1CC"); dg.pack(side="left", anchor="n")
        tk.Label(dg, text="Desglose de cargos", font=self.FB, bg=self.VERDE, fg="white").grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 1))
        self.tot = {}
        for r, (k, lab, entra) in enumerate((("tasa", "Tasa (RD$ por US$)", 1), ("flete_general", "shipping compartido", 1),
                                             ("gestion", "Gestion RD$", 1), ("t_merc", "subtotal mercancía RD$", 0),
                                             ("t_flete", "fletes RD$", 0), ("t_trib", "arancel + selectivo RD$", 0),
                                             ("t_itbis", "ITBIS RD$ (no va al costo)", 0), ("t_total", "total general RD$", 0)), 1):
            tk.Label(dg, text=lab, font=self.FB, bg=self.CLARO, fg=self.TXT, anchor="w", width=26, padx=4).grid(row=r, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))
            if entra:
                self.v[k] = tk.StringVar(); self._entrada(dg, self.v[k], 14).grid(row=r, column=1, sticky="nsew", pady=(0, 1))
            else:
                self.tot[k] = tk.StringVar()
                tk.Label(dg, textvariable=self.tot[k], font=self.FB if k == "t_total" else self.F, bg=self.CLARO if k == "t_total" else "white",
                         fg=self.TXT, anchor="e", width=14, padx=4).grid(row=r, column=1, sticky="nsew", pady=(0, 1))
        fl = tk.Frame(bajo, bg="#C9D1CC"); fl.pack(side="left", anchor="n", padx=24)
        tk.Label(fl, text="Fletes por suplidor", font=self.FB, bg=self.VERDE, fg="white").grid(row=0, column=0, columnspan=4, sticky="nsew", pady=(0, 1))
        for c, (t, w) in enumerate((("Suplidor", 16), ("Flete", 11), ("Flete en RD$", 12), ("Mercancía pagada en RD$", 20))):
            tk.Label(fl, text=t, font=self.FB, bg=self.CLARO, fg=self.TXT, width=w).grid(row=1, column=c, sticky="nsew", padx=(0, 1), pady=(0, 1))
        self.sups = []
        for r in range(max(self.SUPS, len(self.d["suplidores"]))):
            fila = [tk.StringVar() for _ in range(4)]
            for c, (var, w) in enumerate(zip(fila, (16, 11, 12, 20))):
                self._entrada(fl, var, w, "w" if c == 0 else "e").grid(row=r + 2, column=c, sticky="nsew", padx=(0, 1), pady=(0, 1))
            self.sups.append(fila)
        self.lbl = tk.Label(bajo, text="", font=self.F, bg="white", fg=self.TXT, justify="left", anchor="nw"); self.lbl.pack(side="left", anchor="n")
        self._a_pantalla(); self.pintar(avisar=False)

    # ----- casillas
    def _entrada(self, padre, var, ancho, lado="w"):
        e = tk.Entry(padre, textvariable=var, width=ancho, font=self.F, bg=self.ENTRA, fg=self.TXT, relief="flat",
                     justify="left" if lado == "w" else "right", highlightthickness=0)
        e.bind("<FocusOut>", lambda ev: self.pintar(avisar=False)); e.bind("<Return>", lambda ev: self.pintar(avisar=False))
        return e

    def _fila(self):
        r = len(self.filas) + 1; f = {}
        for c, (k, t, w, entra) in enumerate(self.COLS):
            f[k] = tk.StringVar()
            if k in ("prod", "suplidor"):
                x = ttk.Combobox(self.tab, textvariable=f[k], width=w, font=self.F, values=self.prods if k == "prod" else [])
                x.bind("<<ComboboxSelected>>", lambda ev: self.pintar(avisar=False)); x.bind("<FocusOut>", lambda ev: self.pintar(avisar=False))
                if k == "suplidor": x.configure(postcommand=lambda x=x: x.configure(values=self._nombres()))
            elif entra:
                x = self._entrada(self.tab, f[k], w, "e")
            else:
                x = tk.Label(self.tab, textvariable=f[k], font=self.FB if k == "costo" else self.F, anchor="e", width=w, padx=3,
                             bg=self.CLARO if k == "costo" else "white", fg="#1F6F43" if k in ("b30", "b35", "b40", "b50", "venta") else self.TXT)
            x.grid(row=r, column=c, sticky="nsew", padx=(0, 1), pady=(0, 1))
        self.filas.append(f)

    def mas_filas(self):
        for _ in range(5): self._fila()

    def _nombres(self):
        return [s[0].get().strip() for s in self.sups if s[0].get().strip()]

    # ----- datos <-> pantalla
    def _a_pantalla(self):
        d = self.d; g = lambda v: f"{v:.12g}" if v else ""
        f = d.get("fecha") or datetime.date.today().isoformat()
        self.v["fecha"].set(f"{f[8:10]}/{f[5:7]}/{f[:4]}"); self.v["nombre"].set(d.get("nombre", ""))
        for k in ("tasa", "gestion", "flete_general", "margen"): self.v[k].set(g(d.get(k)))
        for fila, s in zip(self.sups, d["suplidores"]):
            fila[0].set(s["nombre"]); fila[1].set(g(s.get("flete"))); fila[2].set(g(s.get("flete_rd"))); fila[3].set(g(s.get("pagado_rd")))
        for fila, l in zip(self.filas, d["lineas"]):
            fila["prod"].set(next((x for x in self.prods if x.split(" | ")[0] == l.get("code")), l.get("descr", "")))
            for k in ("precio", "qty", "desc", "arancel", "selectivo", "itbis"): fila[k].set(g(l.get(k)))
            fila["upi"].set(g(l.get("upi")) if (l.get("upi") or 1) != 1 else ""); fila["suplidor"].set(l.get("suplidor", ""))

    def _de_pantalla(self):
        d = self.d
        d["nombre"] = self.v["nombre"].get().strip()
        t = self.v["fecha"].get().strip().replace("-", "/").split("/")
        if len(t) != 3: raise ValueError("La fecha va como dd/mm/aaaa.")
        d["fecha"] = datetime.date(int(t[2]) if len(t[2]) == 4 else 2000 + int(t[2]), int(t[1]), int(t[0])).isoformat()
        for k in ("tasa", "gestion", "flete_general", "margen"): d[k] = _n(self.v[k].get())
        d["suplidores"] = [dict(nombre=s[0].get().strip(), flete=_n(s[1].get()), flete_rd=_n(s[2].get()), pagado_rd=_n(s[3].get()))
                           for s in self.sups if s[0].get().strip()]
        d["lineas"] = []; self.orden_filas = []
        for i, f in enumerate(self.filas):
            p = f["prod"].get().strip()
            if not p and not f["qty"].get().strip() and not f["precio"].get().strip(): continue
            code = p.split(" | ")[0].strip()
            pr = self.db.one("SELECT descr FROM products WHERE code=?", (code,))
            d["lineas"].append(dict(suplidor=f["suplidor"].get().strip(), code=code if pr else "", descr=pr["descr"] if pr else p,
                                    qty=_n(f["qty"].get()), precio=_n(f["precio"].get()), desc=_n(f["desc"].get()), upi=_n(f["upi"].get(), 1) or 1,
                                    arancel=_n(f["arancel"].get()), selectivo=_n(f["selectivo"].get()), itbis=_n(f["itbis"].get())))
            self.orden_filas.append(i)

    CALC = ("bruto", "g", "unit", "costo", "b30", "b35", "b40", "b50", "venta", "origen", "local")

    def pintar(self, avisar=True):
        for f in self.filas:
            for k in self.CALC: f[k].set("")
        for v in self.tot.values(): v.set("")
        try:
            self._de_pantalla()
            res = calcular(self.d) if self.d["lineas"] else None
            aviso = "" if res or self.d["lineas"] else "Escriba en las casillas amarillas: productos, tasa, gestión y fletes."
        except ValueError as e:
            res, aviso = None, str(e)
            if avisar: messagebox.showwarning("Costeo", aviso, parent=self)
        if res:
            lin, T = res; tasa = self.d["tasa"]
            for i, l in zip(self.orden_filas, lin):
                f = self.filas[i]; c = l["costo"]
                for k, v in (("bruto", l["qty"] * (l.get("precio") or 0)), ("g", l["g"]), ("unit", l["g"] / l["cant"]), ("costo", c),
                             ("b30", c * 1.42), ("b35", c * 1.53), ("b40", c * 1.66), ("b50", c / 0.5), ("venta", l["venta"]),
                             ("origen", l["origen"]), ("local", l["local"])):
                    f[k].set(m2(v))
            self.tot["t_merc"].set(m2(T["mercancia"])); self.tot["t_flete"].set(m2(T["fletes"]))
            self.tot["t_trib"].set(m2(T["arancel"] + T["selectivo"])); self.tot["t_itbis"].set(m2(T["itbis"])); self.tot["t_total"].set(m2(T["costo"]))
        txt = aviso
        if self.ordenes: txt += ("\n" if txt else "") + f"Órdenes de compra creadas: {self.ordenes}"
        elif self.cid: txt += ("\n" if txt else "") + "Pendiente: todavía no se han creado las órdenes de compra."
        self.lbl.config(text=txt)
        return res

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
