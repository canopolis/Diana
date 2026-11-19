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


MONEDAS = ("RD$", "US$", "EURO")


def tasa_de(d, moneda):
    return {"RD$": 1.0, "EURO": d.get("tasa_euro") or 0}.get(moneda, d.get("tasa") or 0)


def calcular(d):
    """Cada suplidor tiene su moneda (pesos, dólares o euros). Todo se pasa a pesos con su tasa y se costea."""
    sup = {s["nombre"]: s for s in d.get("suplidores", [])}
    for s_ in d.get("suplidores", []):
        for mon in (s_.get("moneda") or "US$", s_.get("flete_moneda") or s_.get("moneda") or "US$"):
            if tasa_de(d, mon) <= 0:
                raise ValueError("Ponga la tasa del dólar." if mon == "US$" else "Ponga la tasa del euro.")
    r = lambda n: tasa_de(d, (sup.get(n) or {}).get("moneda") or "US$")
    d2 = dict(d, tasa=1.0, flete_general=0.0,
              suplidores=[dict(x, flete=0.0, flete_rd=(x.get("flete") or 0) * tasa_de(d, x.get("flete_moneda") or x.get("moneda") or "US$")
                               + (x.get("flete_rd") or 0)) for x in d.get("suplidores", [])],
              lineas=[dict(l, precio=(l.get("precio") or 0) * (r(l.get("suplidor")) if l.get("suplidor") in sup else 1)) for l in d.get("lineas", [])])
    lin, T = _calcular(d2)
    for l, orig in zip(lin, d.get("lineas", [])):
        l["tasa_s"] = r(l["suplidor"]); l["precio_m"] = orig.get("precio") or 0; l["moneda"] = (sup[l["suplidor"]].get("moneda") or "US$")
    return lin, T


def _calcular(d):
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
            raise ValueError(f"Falta la cantidad de '{l.get('descr') or l.get('code')}'.")
        if l.get("suplidor") not in sup:
            raise ValueError(f"Elija el suplidor de '{l.get('descr') or l.get('code')}'.")
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
        raise ValueError("Falta el precio de los productos.")
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
    ws["A2"] = f"Fecha {d['fecha'][8:10]}/{d['fecha'][5:7]}/{d['fecha'][:4]}   Tasa dólar {d.get('tasa') or 0:,.4f}   Tasa euro {d.get('tasa_euro') or 0:,.4f}   Gestión RD$ {m2(d.get('gestion') or 0)}"
    ws["A2"].font = F()
    cols = ["Suplidor", "Código", "Producto", "Cantidad", "Precio (moneda del suplidor)", "% desc.", "Unid. inventario", "Costo origen + flete o shipping RD$",
            "Costo local RD$", "Costo total RD$", f"Venta {d.get('margen') or 0:g}% margen", "ITBIS RD$", "Total RD$",
            "30% beneficio", "35% beneficio", "40% beneficio", "50% beneficio"]
    for c, t in enumerate(cols, 1):
        x = ws.cell(4, c, t); x.font = F(bold=True, color="FFFFFF"); x.fill = verde; x.border = caja
        x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    r = 5
    for l in lin:
        vals = [l["suplidor"], l.get("code", ""), l.get("descr", ""), l["qty"], l.get("precio_m") or 0, l.get("desc") or 0,
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
    ws.cell(r, 1, "Suplidor").font = F(bold=True); ws.cell(r, 2, "Flete o shipping pagado en origen").font = F(bold=True)
    ws.cell(r, 3, "Moneda del flete o shipping").font = F(bold=True); ws.cell(r, 4, "Origen RD$").font = F(bold=True)
    ws.cell(r, 5, "Local RD$").font = F(bold=True)
    for s in d["suplidores"]:
        r += 1
        p = T["por_suplidor"].get(s["nombre"], dict(origen=0, local=0))
        fl, fm = s.get("flete") or 0, s.get("flete_moneda") or s.get("moneda") or "US$"
        if not fl and s.get("flete_rd"): fl, fm = s["flete_rd"], "RD$"
        for c, v in enumerate([s["nombre"], fl, fm, p["origen"], p["local"]], 1):
            x = ws.cell(r, c, v); x.font = F()
            if c in (2, 4, 5): x.number_format = "#,##0.00"
    for c, w in zip("ABCDEFGHIJKLMNOPQ", (16, 12, 34, 11, 11, 9, 12, 16, 15, 15, 16, 12, 16, 14, 14, 14, 14)):
        ws.column_dimensions[c].width = w
    ws.row_dimensions[4].height = 32
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f"Costeo_{cid}_{d['fecha'].replace('-', '')}.xlsx")
    wb.save(ruta)
    return ruta


# ---------------------------------------------------------------- ventanas
class CosteoWin(tk.Toplevel):
    """Pantalla como la hoja de Excel, en tres pasos de arriba hacia abajo: 1) tasas y gestión, 2) suplidores con su
    moneda y su flete, 3) productos. Se escribe en las casillas amarillas y todo se calcula solo."""
    VERDE, CLARO, ENTRA, TXT = "#1F4E3D", "#EAF2EC", "#FFF9DB", "#26332C"
    ROJO = "#F6B5B5"
    # (clave, título, ancho, se escribe, es de las columnas opcionales)
    COLS = [("suplidor", "Suplidor", 13, 1, 0), ("prod", "Producto", 46, 1, 0), ("precio", "Precio", 10, 1, 0),
            ("qty", "Cantidad", 9, 1, 0), ("bruto", "Precio\nbruto", 12, 0, 0), ("desc", "%\ndescuento", 11, 1, 0),
            ("upi", "Unidades por 1\nde inventario", 14, 1, 1), ("arancel", "%\narancel", 9, 1, 1),
            ("selectivo", "%\nselectivo", 10, 1, 1), ("itbis", "%\nITBIS", 8, 1, 1),
            ("g", "Costo\ntotal", 12, 0, 0), ("unit", "Costo\nunitario", 11, 0, 0),
            ("origen", "Costo origen + flete\no shipping RD$", 20, 0, 0), ("local", "Costo local\nRD$", 13, 0, 0),
            ("costo", "COSTO TOTAL\nRD$", 13, 0, 0), ("itbis_u", "ITBIS pagado en\nimportación", 16, 0, 1),
            ("b30", "30%\nbeneficio", 12, 0, 0), ("b35", "35%\nbeneficio", 12, 0, 0), ("b40", "40%\nbeneficio", 12, 0, 0),
            ("b50", "50%\nbeneficio", 12, 0, 0), ("venta", "Margen\npersonalizado", 14, 0, 0)]
    COSTOS = ("origen", "local", "costo")       # columnas de costo, sombreadas
    FILAS, SUPS = 18, 6

    def __init__(self, app, cid=None):
        super().__init__(app.root)
        self.app, self.db = app, app.db
        preparar(self.db)
        self.cid, self.ordenes, self.d = cid, "", nuevo()
        if cid:
            r = self.db.one("SELECT datos, ordenes FROM costeos WHERE id=?", (cid,))
            self.d = json.loads(r["datos"]); self.ordenes = r["ordenes"] or ""
        self.title("Costeo de importación"); self.geometry("1380x730"); self.configure(bg="white")
        try: ttk.Style(self).configure("Ancho.TCombobox", postoffset=(0, 0, 330, 0))   # la lista sale más ancha que la casilla
        except Exception: pass
        self.fila_prod = None
        self.F = ("Verdana", 9); self.FB = ("Verdana", 9, "bold")
        self.prods = [f"{p['code']} | {p['descr']}" for p in self.db.q("SELECT code, descr FROM products WHERE active=1 ORDER BY descr")]
        self.v, self.tot = {}, {}
        tit = lambda padre, t: tk.Label(padre, text=t, font=self.FB, bg=self.VERDE, fg="white", anchor="w", padx=6)
        rot = lambda padre, t, w: tk.Label(padre, text=t, font=self.FB, bg=self.CLARO, fg=self.TXT, anchor="w", width=w, padx=4)
        # ----- arriba: fecha, nombre y botones
        top = tk.Frame(self, bg="white"); top.pack(fill="x", padx=10, pady=(8, 6))
        for k, lab, w in (("fecha", "Fecha", 11), ("nombre", "Nombre de la compra", 30)):
            tk.Label(top, text=lab, font=self.FB, bg=self.VERDE, fg="white", padx=6).pack(side="left")
            self.v[k] = tk.StringVar(); self._entrada(top, self.v[k], w).pack(side="left", padx=(0, 12))
        for t, c in (("Cerrar", self.destroy), ("Exportar a Excel", self.excel), ("Crear órdenes de compra", self.ordenar), ("Guardar", self.guardar_avisa)):
            ttk.Button(top, text=t, command=c).pack(side="right", padx=3)
        # ----- pasos 1 y 2, y el desglose (solo lectura)
        med = tk.Frame(self, bg="white"); med.pack(fill="x", padx=10)
        p1 = tk.Frame(med, bg="#C9D1CC"); p1.pack(side="left", anchor="n")
        tit(p1, "1. Tasas y gestión").grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 1))
        for r, (k, lab) in enumerate((("tasa", "Tasa del dólar (RD$)"), ("tasa_euro", "Tasa del euro (RD$)"),
                                      ("gestion", "Gestión y cargos de aquí (RD$)"), ("margen", "Margen personalizado (%)")), 1):
            rot(p1, lab, 27).grid(row=r, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))
            self.v[k] = tk.StringVar(); self._entrada(p1, self.v[k], 12, "e").grid(row=r, column=1, sticky="nsew", pady=(0, 1))
        p2 = tk.Frame(med, bg="#C9D1CC"); p2.pack(side="left", anchor="n", padx=12)
        tit(p2, "2. Suplidores, su moneda y su flete o shipping pagado en origen").grid(row=0, column=0, columnspan=4, sticky="nsew", pady=(0, 1))
        for c, (t, w) in enumerate((("Suplidor", 14), ("Moneda de\nla factura", 12), ("Flete o shipping\npagado en origen", 17),
                                    ("Moneda del flete\no shipping", 17))):
            tk.Label(p2, text=t, font=self.FB, bg=self.CLARO, fg=self.TXT, width=w, height=2, justify="center").grid(row=1, column=c, sticky="nsew", padx=(0, 1), pady=(0, 1))
        self.sups = []
        for r in range(max(self.SUPS, len(self.d["suplidores"]))):
            fila = [tk.StringVar() for _ in range(4)]
            self._entrada(p2, fila[0], 14).grid(row=r + 2, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))
            for c in (1, 3):   # moneda de la factura y moneda del shipping
                cb = ttk.Combobox(p2, textvariable=fila[c], values=MONEDAS, width=8, font=self.F, state="readonly")
                cb.grid(row=r + 2, column=c, sticky="nsew", padx=(0, 1), pady=(0, 1)); cb.bind("<<ComboboxSelected>>", lambda ev: self.pintar(avisar=False))
            self._entrada(p2, fila[2], 17, "e").grid(row=r + 2, column=2, sticky="nsew", padx=(0, 1), pady=(0, 1))
            self.sups.append(fila)
        dg = tk.Frame(med, bg="#C9D1CC"); dg.pack(side="left", anchor="n")
        tit(dg, "Desglose de cargos (se calcula solo)").grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 1))
        for r, (k, lab) in enumerate((("t_merc", "Mercancía RD$"), ("t_flete", "Flete o shipping pagado en origen RD$"), ("t_ges", "Gestión RD$"),
                                      ("t_trib", "Arancel + selectivo RD$"), ("t_itbis", "ITBIS RD$ (no va al costo)"), ("t_total", "Total general RD$")), 1):
            rot(dg, lab, 37).grid(row=r, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))
            self.tot[k] = tk.StringVar()
            tk.Label(dg, textvariable=self.tot[k], font=self.FB if k == "t_total" else self.F, bg=self.CLARO if k == "t_total" else "white",
                     fg=self.TXT, anchor="e", width=13, padx=4).grid(row=r, column=1, sticky="nsew", pady=(0, 1))
        # ----- paso 3: productos
        bar = tk.Frame(self, bg="white"); bar.pack(fill="x", padx=10, pady=(8, 0))
        tit(bar, "3. Productos (el precio va en la moneda de su suplidor)").pack(side="left")
        ttk.Button(bar, text="Agregar filas", command=self.mas_filas).pack(side="left", padx=(10, 3))
        ttk.Button(bar, text="Crear producto nuevo", command=self.producto_nuevo).pack(side="left", padx=(3, 10))
        self.ver_mas = tk.BooleanVar(value=any((l.get("upi") or 1) != 1 or l.get("arancel") or l.get("selectivo") or l.get("itbis") for l in self.d["lineas"]))
        tk.Checkbutton(bar, text="Mostrar tributos y unidades de inventario", variable=self.ver_mas, command=self._opcionales,
                       font=self.F, bg="white", activebackground="white").pack(side="left")
        marco = tk.Frame(self, bg="white"); marco.pack(fill="both", expand=True, padx=10, pady=(2, 0))
        self.cv = tk.Canvas(marco, bg="white", highlightthickness=0)
        sy = ttk.Scrollbar(marco, orient="vertical", command=self.cv.yview); sx = ttk.Scrollbar(marco, orient="horizontal", command=self.cv.xview)
        self.cv.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side="right", fill="y"); sx.pack(side="bottom", fill="x"); self.cv.pack(fill="both", expand=True)
        self.tab = tk.Frame(self.cv, bg="#C9D1CC"); self.cv.create_window((0, 0), window=self.tab, anchor="nw")
        self.tab.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.extra = []   # casillas de las columnas opcionales
        for c, (k, t, w, _, op) in enumerate(self.COLS):
            x = tk.Label(self.tab, text=t, font=self.FB, bg=self.VERDE, fg="white", width=w, height=2, justify="center")
            x.grid(row=0, column=c + 1, padx=(0, 1), pady=(0, 1), sticky="nsew")
            if op: self.extra.append(x)
        tk.Label(self.tab, text="Borrar\nfila", font=self.FB, bg=self.VERDE, fg="white", width=6, height=2, justify="center").grid(
            row=0, column=0, padx=(0, 1), pady=(0, 1), sticky="nsew")
        self.filas = []; self.marcos = {}
        for _ in range(max(self.FILAS, len(self.d["lineas"]))): self._fila()
        self.lbl = tk.Label(self, text="", font=self.FB, bg="white", fg="#B00020", justify="left", anchor="w", wraplength=1340); self.lbl.pack(fill="x", padx=10, pady=6)
        self._a_pantalla(); self._opcionales(); self.pintar(avisar=False)

    # ----- casillas
    def _entrada(self, padre, var, ancho, lado="w"):
        e = tk.Entry(padre, textvariable=var, width=ancho, font=self.F, bg=self.ENTRA, fg=self.TXT, relief="flat",
                     justify="left" if lado == "w" else "right", highlightthickness=0)
        e.bind("<FocusOut>", lambda ev: self.pintar(avisar=False)); e.bind("<Return>", lambda ev: self.pintar(avisar=False))
        var.trace_add("write", self._vivo)
        if not hasattr(self, "ent"): self.ent = {}
        self.ent[str(var)] = e
        return e

    def _buscar(self, txt):
        """Productos cuyo código o nombre contiene todo lo escrito (sin escribir nada, o con uno ya elegido, salen todos)."""
        t = txt.strip().lower()
        if not t or txt.strip() in self.prods: return self.prods
        pal = t.split()
        return [p for p in self.prods if all(w in p.lower() for w in pal)]

    def _elegir(self, x):
        r = self._buscar(x.get())
        if len(r) == 1:
            x.set(r[0]); x.icursor("end"); self.pintar(avisar=False)
        elif r:
            x.event_generate("<Down>")          # abre la lista ya filtrada
        return "break"

    def _fila(self):
        r = len(self.filas) + 1; f = {}
        for c, (k, t, w, entra, op) in enumerate(self.COLS):
            f[k] = tk.StringVar()
            if k in ("prod", "suplidor"):
                marco = tk.Frame(self.tab, bg="white")
                x = ttk.Combobox(marco, textvariable=f[k], width=w, font=self.F, values=self.prods if k == "prod" else [],
                                 style="Ancho.TCombobox" if k == "prod" else "TCombobox")
                x.pack(fill="both", expand=True, padx=2, pady=2); self.marcos[str(f[k])] = marco
                if k == "prod":
                    x.configure(postcommand=lambda x=x: x.configure(values=self._buscar(x.get())), state="disabled")
                    x.bind("<FocusIn>", lambda ev, f=f: setattr(self, "fila_prod", f))
                    x.bind("<Return>", lambda ev, x=x: self._elegir(x))
                    f["suplidor"].trace_add("write", lambda *a, x=x, v=f["suplidor"]: x.configure(state="normal" if v.get().strip() else "disabled"))
                x.bind("<<ComboboxSelected>>", lambda ev: self.pintar(avisar=False)); x.bind("<FocusOut>", lambda ev: self.pintar(avisar=False))
                f[k].trace_add("write", self._vivo)
                if k == "suplidor": x.configure(state="readonly", postcommand=lambda x=x: x.configure(values=[""] + self._nombres()))
            elif entra:
                x = self._entrada(self.tab, f[k], w, "e")
            else:
                x = tk.Label(self.tab, textvariable=f[k], font=self.FB if k in self.COSTOS else self.F, anchor="e", width=w, padx=3,
                             bg=self.CLARO if k in self.COSTOS else "white", fg="#1F6F43" if k in ("b30", "b35", "b40", "b50", "venta") else self.TXT)
            (x.master if k in ("prod", "suplidor") else x).grid(row=r, column=c + 1, sticky="nsew", padx=(0, 1), pady=(0, 1))
            if op:
                self.extra.append(x)
                if not self.ver_mas.get(): x.grid_remove()
        tk.Button(self.tab, text="X", font=self.FB, fg="#B00020", bg="white", relief="flat", cursor="hand2", takefocus=0,
                  command=lambda f=f: self.borrar_fila(f)).grid(row=r, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))
        self.filas.append(f)

    ESCRITAS = ("suplidor", "prod", "precio", "qty", "desc", "upi", "arancel", "selectivo", "itbis")

    def borrar_fila(self, f):
        """Vacía la fila completa (suplidor, producto y lo escrito)."""
        for k in self.ESCRITAS: f[k].set("")
        self.pintar(avisar=False)

    def _vivo(self, *a):
        """Al escribir en cualquier casilla, todo se recalcula solo un momento después."""
        if getattr(self, "_pint", True): return
        if getattr(self, "_job", None):
            try: self.after_cancel(self._job)
            except Exception: pass
        self._job = self.after(350, self._vivo_ya)

    def _vivo_ya(self):
        self._job = None
        try:
            if self.winfo_exists(): self.pintar(avisar=False)
        except tk.TclError:
            pass

    def producto_nuevo(self, descr="", fila=None):
        """Crea en Diana un producto que no existe y lo pone en la fila donde se estaba escribiendo."""
        top = tk.Toplevel(self); top.title("Producto nuevo"); top.transient(self); top.grab_set()
        vs = {}
        for i, (k, lab, val) in enumerate((("code", "Código", ""), ("descr", "Descripción", descr), ("price", "Precio de venta (puede dejarlo vacío)", ""))):
            ttk.Label(top, text=lab).grid(row=i, column=0, sticky="e", padx=6, pady=4)
            vs[k] = tk.StringVar(value=val); ttk.Entry(top, textvariable=vs[k], width=46).grid(row=i, column=1, padx=6, pady=4)
        hecho = {}
        def ok():
            code, des = vs["code"].get().strip(), vs["descr"].get().strip()
            if not code or not des:
                messagebox.showwarning("Producto nuevo", "Ponga el código y la descripción.", parent=top); return
            if self.db.one("SELECT 1 FROM products WHERE code=?", (code,)):
                messagebox.showwarning("Producto nuevo", "Ese código ya existe.", parent=top); return
            try: precio = _n(vs["price"].get())
            except ValueError:
                messagebox.showwarning("Producto nuevo", "Revise el precio.", parent=top); return
            self.db.save_product(dict(code=code, descr=des, price=precio, price2=0, cost=0, itbis=0, category="", active=1, stock_base=0))
            hecho["t"] = f"{code} | {des}"; top.destroy()
        b = ttk.Frame(top); b.grid(row=3, column=0, columnspan=2, pady=8)
        ttk.Button(b, text="Crear", command=ok).pack(side="left", padx=6); ttk.Button(b, text="Cancelar", command=top.destroy).pack(side="left", padx=6)
        top.wait_window()
        if not hecho: return False
        self.prods = [f"{p['code']} | {p['descr']}" for p in self.db.q("SELECT code, descr FROM products WHERE active=1 ORDER BY descr")]
        f = fila or self.fila_prod
        if f is None or (f is not fila and f["prod"].get().strip() and self.db.one("SELECT 1 FROM products WHERE code=?", (f["prod"].get().split(" | ")[0].strip(),))):
            f = next((x for x in self.filas if not x["prod"].get().strip()), None)   # primera fila vacía
        if f is None: self._fila(); f = self.filas[-1]
        f["prod"].set(hecho["t"]); self.pintar(avisar=False)
        try: self.app.refresh_all()
        except Exception: pass
        return True

    def _opcionales(self):
        for x in self.extra:
            x.grid() if self.ver_mas.get() else x.grid_remove()

    def mas_filas(self):
        for _ in range(5): self._fila()

    def _nombres(self):
        return [s[0].get().strip() for s in self.sups if s[0].get().strip()]

    # ----- datos <-> pantalla
    def _a_pantalla(self):
        d = self.d; g = lambda v: f"{v:.12g}" if v else ""
        f = d.get("fecha") or datetime.date.today().isoformat()
        self.v["fecha"].set(f"{f[8:10]}/{f[5:7]}/{f[:4]}"); self.v["nombre"].set(d.get("nombre", ""))
        for k in ("tasa", "tasa_euro", "gestion", "margen"): self.v[k].set(g(d.get(k)))
        for fila, s in zip(self.sups, d["suplidores"]):
            mon = s.get("moneda") or "US$"; fl, fm = s.get("flete") or 0, s.get("flete_moneda") or mon
            if not fl and s.get("flete_rd"): fl, fm = s["flete_rd"], "RD$"     # costeos guardados con el shipping en pesos
            fila[0].set(s["nombre"]); fila[1].set(mon); fila[2].set(g(fl)); fila[3].set(fm if fl else "")
        for fila, l in zip(self.filas, d["lineas"]):
            fila["prod"].set(next((x for x in self.prods if x.split(" | ")[0] == l.get("code")), l.get("descr", "")))
            for k in ("precio", "qty", "desc", "arancel", "selectivo", "itbis"): fila[k].set(g(l.get(k)))
            fila["upi"].set(g(l.get("upi")) if (l.get("upi") or 1) != 1 else ""); fila["suplidor"].set(l.get("suplidor", ""))

    def _de_pantalla(self):
        d = self.d
        d["nombre"] = self.v["nombre"].get().strip()
        t = self.v["fecha"].get().strip().replace("-", "/").split("/")
        if len(t) != 3: raise ValueError("La fecha va como día/mes/año, por ejemplo 18/11/2026.")
        d["fecha"] = datetime.date(int(t[2]) if len(t[2]) == 4 else 2000 + int(t[2]), int(t[1]), int(t[0])).isoformat()
        for k in ("tasa", "tasa_euro", "gestion", "margen"): d[k] = _n(self.v[k].get())
        d["flete_general"] = 0.0
        viejos = {s["nombre"]: s for s in d.get("suplidores", [])}
        d["suplidores"] = []
        for s in self.sups:
            n = s[0].get().strip()
            if not n: continue
            if not s[1].get(): s[1].set("US$")
            if _n(s[2].get()) and not s[3].get(): s[3].set(s[1].get())     # si no elige, el shipping va en la moneda de la factura
            d["suplidores"].append(dict(nombre=n, moneda=s[1].get(), flete=_n(s[2].get()), flete_moneda=s[3].get() or s[1].get(),
                                        flete_rd=0.0, pagado_rd=(viejos.get(n) or {}).get("pagado_rd") or 0))
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

    CALC = ("bruto", "g", "unit", "costo", "b30", "b35", "b40", "b50", "venta", "origen", "local", "itbis_u")

    def _faltan(self):
        """Lista, en palabras, de las casillas que faltan por llenar. Vacía = todo está lleno."""
        F = []; R = self._rojos = set()
        usados = [(n[0].get().strip(), n[1].get() or "US$", (n[3].get() or n[1].get() or "US$") if n[2].get().strip() else None)
                  for n in self.sups if n[0].get().strip()]
        nombres = [u[0] for u in usados]
        mon = {m for u in usados for m in u[1:] if m}
        try: td, te = _n(self.v["tasa"].get()), _n(self.v["tasa_euro"].get())
        except ValueError: td = te = 0
        if "US$" in mon and td <= 0: F.append("Paso 1: Tasa del dólar"); R.add(str(self.v["tasa"]))
        if "EURO" in mon and te <= 0: F.append("Paso 1: Tasa del euro"); R.add(str(self.v["tasa_euro"]))
        if not nombres: F.append("Paso 2: escriba al menos un suplidor"); R.add(str(self.sups[0][0]))
        completas = set(); alguna = False
        for i, f in enumerate(self.filas):
            if not any(f[k].get().strip() for k in self.ESCRITAS): continue
            alguna = True; fa = []
            sp = f["suplidor"].get().strip()
            if not sp: fa.append("Suplidor"); R.add(str(f["suplidor"]))
            elif sp not in nombres: fa.append("Suplidor (ese nombre ya no está en el paso 2)"); R.add(str(f["suplidor"]))
            if not f["prod"].get().strip(): fa.append("Producto"); R.add(str(f["prod"]))
            if not f["precio"].get().strip(): fa.append("Precio"); R.add(str(f["precio"]))
            try: q = _n(f["qty"].get())
            except ValueError: q = 0
            if q <= 0: fa.append("Cantidad"); R.add(str(f["qty"]))
            if fa: F.append(f"Fila {i + 1}: " + ", ".join(fa))
            else: completas.add(i)
        if not alguna: F.append("Paso 3: llene al menos un producto"); R.add(str(self.filas[0]["suplidor"]))
        return F, completas

    def pintar(self, avisar=True):
        self._pint = True
        try:
            return self._pintar(avisar)
        finally:
            self._pint = False

    def _pintar(self, avisar):
        for f in self.filas:
            for k in self.CALC: f[k].set("")
        for v in self.tot.values(): v.set("")
        res = None; mal = ""
        faltan, completas = self._faltan()
        for n, e in self.ent.items(): e.configure(bg=self.ROJO if n in self._rojos else self.ENTRA)
        for n, m in self.marcos.items(): m.configure(bg="#D32F2F" if n in self._rojos else "white")
        try:
            self._de_pantalla()
            pares = [(i, l) for i, l in zip(self.orden_filas, self.d["lineas"]) if i in completas]
            if pares and not any(x.startswith("Paso") for x in faltan):
                res = calcular(dict(self.d, lineas=[l for _, l in pares]))
        except ValueError as e:
            mal = str(e)
            if "could not convert" in mal: mal = "Hay una casilla con un valor que no es un número."
        if res:
            lin, T = res
            for (i, _), l in zip(pares, lin):
                f = self.filas[i]; c = l["costo"]; ts = l["tasa_s"]
                for k, v in (("bruto", l["qty"] * l["precio_m"]), ("g", l["g"] / ts), ("unit", l["g"] / ts / l["cant"]), ("costo", c),
                             ("b30", c * 1.42), ("b35", c * 1.53), ("b40", c * 1.66), ("b50", c / 0.5), ("venta", l["venta"]),
                             ("origen", l["origen"]), ("local", l["local"]), ("itbis_u", l["itbis_u"])):
                    f[k].set(m2(v))
            self.tot["t_merc"].set(m2(T["mercancia"])); self.tot["t_flete"].set(m2(T["fletes"])); self.tot["t_ges"].set(m2(T["gestion"]))
            self.tot["t_trib"].set(m2(T["arancel"] + T["selectivo"])); self.tot["t_itbis"].set(m2(T["itbis"])); self.tot["t_total"].set(m2(T["costo"]))
        if mal:
            txt, listo = mal, False
        elif faltan:
            txt = "Faltan casillas por llenar:   " + ";   ".join(faltan[:6]) + (f";   y {len(faltan) - 6} más" if len(faltan) > 6 else "")
            listo = False
        else:
            txt, listo = "Todo está lleno. Ya puede guardar, exportar o crear las órdenes de compra.", True
        if avisar and not listo:
            messagebox.showwarning("Costeo", mal or "Faltan casillas por llenar:\n\n" + "\n".join(faltan), parent=self)
        if self.ordenes: txt += f"   Órdenes de compra creadas: {self.ordenes}"
        elif self.cid: txt += "   Pendiente: todavía no se han creado las órdenes de compra."
        self.lbl.config(text=txt, fg="#1F6F43" if listo else "#B00020")
        return res if listo else None

    # ----- acciones
    def guardar_avisa(self):
        if self.guardar():
            messagebox.showinfo("Costeo", "El costeo fue guardado.", parent=self)

    def guardar(self, callar=False):
        if self.pintar() is None: return False
        self.cid = guardar(self.db, self.cid, self.d)
        self.pintar(False)
        return True

    def ordenar(self):
        if self.ordenes:
            messagebox.showinfo("Costeo", f"Este costeo ya tiene sus órdenes de compra: {self.ordenes}.", parent=self); return
        if self.pintar() is None: return
        for i in list(self.orden_filas):          # productos escritos que todavía no existen en Diana: ofrecer crearlos
            f = self.filas[i]; t = f["prod"].get().strip()
            if t and not self.db.one("SELECT 1 FROM products WHERE code=?", (t.split(" | ")[0].strip(),)):
                if not messagebox.askyesno("Costeo", f"El producto '{t}' no existe en Diana.\n¿Crearlo ahora?", parent=self): return
                if not self.producto_nuevo(t, f): return
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
        self.mnu = tk.Menu(self, tearoff=0)
        for t, c in (("Abrir", self.abrir), ("Nuevo", self.nuevo), ("Eliminar", self.borrar)):
            self.mnu.add_command(label=t, command=c)
        self.mnu.add_separator(); self.mnu.add_command(label="Cerrar", command=self.mnu.unpost)
        self.tv.bind("<Button-3>", self.menu)
        self.cargar()

    def menu(self, ev):
        fila = self.tv.identify_row(ev.y)
        if fila:
            self.tv.selection_set(fila); self.tv.focus(fila)
        for t in ("Abrir", "Eliminar"):
            self.mnu.entryconfigure(t, state="normal" if fila else "disabled")
        try:
            self.mnu.tk_popup(ev.x_root, ev.y_root)
        finally:
            self.mnu.grab_release()
        return "break"

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
