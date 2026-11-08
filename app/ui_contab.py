"""Pantallas de contabilidad: ventana de asiento, Diario General, Catálogo de cuentas y reportes contables."""
import os, datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from contab import TIPOS_CTA, Bloqueado, ORIGEN
import rep_contab

H = {}   # utilidades de main: fecha_entry, read_dmy, dmy, Picker, open_file, form_dialog


def m2(v):
    return f"{v:,.2f}"


def num(s):
    s = str(s).replace(",", "").strip()
    return float(s) if s else 0.0


def popup(menu, ev):
    try:
        menu.tk_popup(ev.x_root, ev.y_root)
    finally:
        menu.grab_release()
    return "break"


def elegir_cuenta(parent, gl, inicial=""):
    rows = [(c["code"], c["name"], TIPOS_CTA.get(c["type"], "")) for c in gl.cuentas(False)]
    p = H["Picker"](parent, "Buscar cuenta", ("Cuenta", "Nombre", "Tipo"), rows, [100, 420, 100], inicial)
    return p.result[0] if p.result else None


def menu_tabla(tv, parent, opciones, sin_fila=()):
    """Clic derecho en una lista. 'sin_fila' son las opciones que salen aunque no haya fila seleccionada."""
    def abrir(ev):
        fila = tv.identify_row(ev.y)
        if fila:
            tv.selection_set(fila); tv.focus(fila)
        m = tk.Menu(parent, tearoff=0)
        for t, cmd in opciones:
            if t == "-":
                m.add_separator(); continue
            m.add_command(label=t, command=cmd, state="normal" if (fila or t in sin_fila) else "disabled")
        m.add_separator(); m.add_command(label="Cerrar", command=m.unpost)
        return popup(m, ev)
    tv.bind("<Button-3>", abrir)


# =====================================================================================  ventana de asiento
class AsientoDialog(tk.Toplevel):
    """Ventana de asiento igual a la del programa anterior.
    Monto positivo = débito, negativo = crédito (o use las columnas Débito / Crédito).
    modo 'guardar': graba al dar OK. modo 'documento': solo devuelve (cabecera, lineas) para que
    Facturación guarde el documento y el asiento juntos."""

    def __init__(self, parent, gl, jv_id=None, modo="guardar", titulo="Asiento de diario",
                 info="", descr="", fecha=None, ref=None, source="GL JV", copiar_de=None, nuevo_cb=None, prellenar=None):
        super().__init__(parent)
        self.gl = gl; self.jv_id = jv_id; self.modo = modo; self.ref = ref; self.source = source
        self.result = None; self.nuevo_cb = nuevo_cb
        self.title(titulo); self.transient(parent); self.grab_set(); self.geometry("900x560")
        self.configure(bg="#f4f4f4")
        h, ls = (gl.asiento(jv_id) if jv_id else (None, []))
        if copiar_de and not jv_id:
            _, ls = gl.asiento(copiar_de)
        if prellenar is not None:
            ls = prellenar
        hoy = fecha or (datetime.date.fromisoformat(h["date"]) if h else datetime.date.today())
        if isinstance(hoy, str):
            hoy = datetime.date.fromisoformat(hoy)
        top = ttk.Frame(self, padding=10); top.pack(fill="x")
        self.vper = tk.StringVar(value=str(h["period"] if h else hoy.month))
        self.vyear = tk.StringVar(value=str(h["year"] if h else hoy.year))
        self.vfecha = tk.StringVar(value=H["dmy"](hoy))
        self.vnum = tk.StringVar(value=str(h["number"] if h and h["number"] else (gl.siguiente_numero() if modo == "guardar" else "(automático)")))
        self.vauto = tk.IntVar(value=h["auto_reverse"] if h else 0)
        self.vdesc = tk.StringVar(value=h["descr"] if h else descr)
        ttk.Label(top, text="Período:").grid(row=0, column=0, sticky="w")
        pf = ttk.Frame(top); pf.grid(row=0, column=1, sticky="w")
        ttk.Entry(pf, textvariable=self.vper, width=4, justify="center").pack(side="left")
        ttk.Entry(pf, textvariable=self.vyear, width=6, justify="center").pack(side="left", padx=3)
        ttk.Label(top, text="Fecha:").grid(row=0, column=2, sticky="e", padx=(20, 4))
        fe = H["fecha_entry"](top, self.vfecha); fe.grid(row=0, column=3, sticky="w")
        self.vfecha.trace_add("write", lambda *a: self._fecha_cambio())
        ttk.Label(top, text="No. asiento:").grid(row=1, column=0, sticky="w", pady=4)
        en = ttk.Entry(top, textvariable=self.vnum, width=12, justify="right"); en.grid(row=1, column=1, sticky="w")
        if modo != "guardar":
            en.state(["readonly"])
        ttk.Checkbutton(top, text="Auto-reverso", variable=self.vauto).grid(row=1, column=3, sticky="w")
        ttk.Label(top, text="Descripción:").grid(row=2, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.vdesc, width=70).grid(row=2, column=1, columnspan=4, sticky="w")
        if info:
            tk.Label(self, text=info, bg="#e8f0fe", fg="#1a3d7c", anchor="w", justify="left",
                     font=("Segoe UI", 9), padx=8, pady=6).pack(fill="x", padx=10)
        # cuadricula de lineas
        cab = ttk.Frame(self, padding=(10, 6, 10, 0)); cab.pack(fill="x")
        for txt, w in (("Débito", 14), ("Crédito", 14), ("Cuenta", 12), ("Nombre de la cuenta", 34),
                       ("Sub1", 6), ("Sub2", 6), ("Sub3", 6), ("Nombre", 20)):
            ttk.Label(cab, text=txt, width=w, anchor="w").pack(side="left", padx=1)
        cuerpo = ttk.Frame(self); cuerpo.pack(fill="both", expand=True, padx=10)
        self.canvas = tk.Canvas(cuerpo, bg="white", highlightthickness=1, highlightbackground="#bbb")
        sb = ttk.Scrollbar(cuerpo, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_fr = tk.Frame(self.canvas, bg="white")
        self.canvas.create_window((0, 0), window=self.grid_fr, anchor="nw")
        self.grid_fr.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Button-3>", self._menu_vacio)
        self.filas = []
        for l in ls:
            self.agregar(dict(amount=l["amount"], code=l["code"] or "", sub1=l["sub1"], sub2=l["sub2"],
                              sub3=l["sub3"], name=l["name"] or ""))
        if not self.filas:
            self.agregar(); self.agregar()
        lados = ttk.Frame(self, padding=(10, 2)); lados.pack(fill="x")
        ttk.Button(lados, text="+ Línea", command=lambda: self.agregar(foco=True)).pack(side="left")
        ttk.Button(lados, text="− Línea", command=self.quitar_actual).pack(side="left", padx=4)
        ttk.Button(lados, text="Buscar cuenta (F2)", command=self.buscar_actual).pack(side="left", padx=4)
        pie = ttk.Frame(self, padding=10); pie.pack(fill="x")
        self.lbl = ttk.Label(pie, text="", font=("Segoe UI", 10, "bold")); self.lbl.pack(side="left")
        ttk.Button(pie, text="Cancelar", command=self.destroy).pack(side="right", padx=3)
        if modo == "guardar" and nuevo_cb:
            ttk.Button(pie, text="OK y Nuevo", command=lambda: self.ok(nuevo=True)).pack(side="right", padx=3)
        ttk.Button(pie, text="OK", command=self.ok).pack(side="right", padx=3)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<F2>", lambda e: self.buscar_actual())
        self.actual = self.filas[0] if self.filas else None
        self.totales()
        self.after(50, lambda: self.filas and self.filas[0]["e_deb"].focus_set())

    def _fecha_cambio(self):
        try:
            d = H["read_dmy"](self.vfecha.get())
        except ValueError:
            return
        self.vper.set(str(d.month)); self.vyear.set(str(d.year))

    def agregar(self, d=None, foco=False, despues=None):
        d = d or {}
        f = {"vars": {k: tk.StringVar() for k in ("deb", "cre", "code", "sub1", "sub2", "sub3", "name")}}
        v = f["vars"]
        amt = d.get("amount") or 0
        if amt > 0: v["deb"].set(f"{amt:.2f}")
        if amt < 0: v["cre"].set(f"{-amt:.2f}")
        v["code"].set(d.get("code", ""))
        for k in ("sub1", "sub2", "sub3"):
            v[k].set(self._sub_code(int(k[-1]), d.get(k)))
        v["name"].set(d.get("name", ""))
        fr = tk.Frame(self.grid_fr, bg="white"); f["fr"] = fr
        f["e_deb"] = ttk.Entry(fr, textvariable=v["deb"], width=14, justify="right")
        f["e_cre"] = ttk.Entry(fr, textvariable=v["cre"], width=14, justify="right")
        f["e_code"] = ttk.Entry(fr, textvariable=v["code"], width=12)
        f["lbl"] = tk.Label(fr, text="", width=34, anchor="w", bg="white", fg="#333")
        ws = [f["e_deb"], f["e_cre"], f["e_code"], f["lbl"]]
        for k in ("sub1", "sub2", "sub3"):
            f["e_" + k] = ttk.Entry(fr, textvariable=v[k], width=6); ws.append(f["e_" + k])
        f["e_name"] = ttk.Entry(fr, textvariable=v["name"], width=20); ws.append(f["e_name"])
        for w in ws:
            w.pack(side="left", padx=1, pady=1)
            w.bind("<FocusIn>", lambda e, f=f: self._foco(f), add="+")
            w.bind("<Button-3>", lambda e, f=f: self._menu_fila(e, f))
        f["e_code"].bind("<Double-1>", lambda e, f=f: self.buscar(f))
        f["e_code"].bind("<FocusOut>", lambda e, f=f: self._ver_cuenta(f))
        f["e_code"].bind("<Return>", lambda e, f=f: (self._ver_cuenta(f, buscar=True), f["e_name"].focus_set()))
        f["e_name"].bind("<Tab>", lambda e, f=f: self._tab_ultimo(f))
        for k in ("deb", "cre"):
            v[k].trace_add("write", lambda *a: self.totales())
        f["e_deb"].bind("<KeyRelease>", lambda e, f=f: f["vars"]["deb"].get().strip() and f["vars"]["cre"].set(""))
        f["e_cre"].bind("<KeyRelease>", lambda e, f=f: f["vars"]["cre"].get().strip() and f["vars"]["deb"].set(""))
        if despues is not None and despues in self.filas:
            i = self.filas.index(despues) + 1
            self.filas.insert(i, f)
            for x in self.filas: x["fr"].pack_forget()
            for x in self.filas: x["fr"].pack(fill="x")
        else:
            self.filas.append(f); fr.pack(fill="x")
        self._ver_cuenta(f)
        if foco:
            f["e_deb"].focus_set()
            if not d:   # sugiere el monto que falta para cuadrar
                dif = self._diferencia()
                if abs(dif) > 0.004:
                    (v["cre"] if dif > 0 else v["deb"]).set(f"{abs(dif):.2f}")
        return f

    def _tab_ultimo(self, f):
        if f is self.filas[-1]:
            self.agregar(foco=True); return "break"

    def _sub_code(self, level, sid):
        if not sid:
            return ""
        r = self.gl.con.execute("SELECT code FROM gl_subs WHERE id=?", (sid,)).fetchone()
        return r["code"] if r else ""

    def _sub_id(self, level, code):
        code = (code or "").strip()
        if not code:
            return None
        r = self.gl.con.execute("SELECT id FROM gl_subs WHERE level=? AND code=?", (level, code)).fetchone()
        if not r:
            raise ValueError(f"No existe la subcuenta {level} '{code}'.")
        return r["id"]

    def _foco(self, f):
        self.actual = f

    def _ver_cuenta(self, f, buscar=False):
        code = f["vars"]["code"].get().strip()
        c = self.gl.cuenta_por_codigo(code) if code else None
        if code and not c and buscar:
            self.buscar(f); return
        f["lbl"].config(text=(c["name"] if c else ("¿cuenta no existe?" if code else "")),
                        fg="#333" if c or not code else "#b00020")

    def buscar(self, f):
        code = elegir_cuenta(self, self.gl, f["vars"]["code"].get().strip() if not self.gl.cuenta_por_codigo(
            f["vars"]["code"].get().strip()) else "")
        if code:
            f["vars"]["code"].set(code); self._ver_cuenta(f); f["e_name"].focus_set()

    def buscar_actual(self):
        if self.actual: self.buscar(self.actual)

    def quitar(self, f):
        if f in self.filas:
            f["fr"].destroy(); self.filas.remove(f)
        if not self.filas:
            self.agregar()
        self.actual = self.filas[0]; self.totales()

    def quitar_actual(self):
        if self.actual: self.quitar(self.actual)

    def copiar_linea(self, f):
        v = f["vars"]
        self.agregar(dict(amount=num(v["deb"].get() or 0) - num(v["cre"].get() or 0), code=v["code"].get(),
                          name=v["name"].get()), despues=f)
        self.totales()

    def invertir_linea(self, f):
        v = f["vars"]; d, c = v["deb"].get(), v["cre"].get(); v["deb"].set(c); v["cre"].set(d)

    def _menu_fila(self, ev, f):
        self.actual = f
        w = ev.widget
        m = tk.Menu(self, tearoff=0)
        if isinstance(w, ttk.Entry):
            try: w.focus_set()
            except Exception: pass
            m.add_command(label="Cortar", command=lambda: w.event_generate("<<Cut>>"))
            m.add_command(label="Copiar", command=lambda: w.event_generate("<<Copy>>"))
            m.add_command(label="Pegar", command=lambda: w.event_generate("<<Paste>>"))
            m.add_command(label="Seleccionar todo", command=lambda: (w.selection_range(0, "end"), w.icursor("end")))
            m.add_separator()
        m.add_command(label="Agregar línea", command=lambda: self.agregar(foco=True, despues=f))
        m.add_command(label="Eliminar línea", command=lambda: self.quitar(f))
        m.add_command(label="Buscar cuenta", command=lambda: self.buscar(f))
        m.add_command(label="Copiar línea", command=lambda: self.copiar_linea(f))
        m.add_command(label="Cambiar débito ↔ crédito", command=lambda: self.invertir_linea(f))
        m.add_separator(); m.add_command(label="Cerrar", command=m.unpost)
        return popup(m, ev)

    def _menu_vacio(self, ev):
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Agregar línea", command=lambda: self.agregar(foco=True))
        m.add_separator(); m.add_command(label="Cerrar", command=m.unpost)
        return popup(m, ev)

    def _diferencia(self):
        d = c = 0
        for f in self.filas:
            try:
                d += num(f["vars"]["deb"].get()); c += num(f["vars"]["cre"].get())
            except ValueError:
                pass
        return round(d - c, 2)

    def totales(self):
        d = c = 0
        for f in self.filas:
            try:
                d += num(f["vars"]["deb"].get()); c += num(f["vars"]["cre"].get())
            except ValueError:
                pass
        dif = round(d - c, 2)
        self.lbl.config(text=f"Débitos: {m2(d)}    Créditos: {m2(c)}" + ("" if abs(dif) < 0.005 else f"    Diferencia: {m2(dif)}"),
                        foreground="#1e6b34" if abs(dif) < 0.005 and d > 0 else "#b00020")

    def _leer(self):
        try:
            fecha = H["read_dmy"](self.vfecha.get())
        except ValueError as e:
            raise ValueError(str(e))
        try:
            per, year = int(self.vper.get()), int(self.vyear.get())
        except ValueError:
            raise ValueError("Período no válido.")
        if not 1 <= per <= 12:
            raise ValueError("El período debe ser de 1 a 12.")
        lineas = []
        for i, f in enumerate(self.filas, 1):
            v = f["vars"]
            try:
                d, c = num(v["deb"].get()), num(v["cre"].get())
            except ValueError:
                raise ValueError(f"Monto no válido en la línea {i}.")
            code = v["code"].get().strip()
            if not code and not d and not c:
                continue
            cta = self.gl.cuenta_por_codigo(code)
            if not cta:
                raise ValueError(f"Línea {i}: la cuenta '{code}' no existe." if code else f"Línea {i}: falta la cuenta.")
            if cta["suspended"]:
                raise ValueError(f"Línea {i}: la cuenta {code} está suspendida.")
            if not d and not c:
                raise ValueError(f"Línea {i}: falta el monto.")
            lineas.append(dict(account_id=cta["id"], amount=round(d - c, 2), name=v["name"].get().strip(),
                               sub1=self._sub_id(1, v["sub1"].get()), sub2=self._sub_id(2, v["sub2"].get()),
                               sub3=self._sub_id(3, v["sub3"].get())))
        h = dict(date=fecha.isoformat(), year=year, period=per, descr=self.vdesc.get().strip(),
                 auto_reverse=self.vauto.get(), source=self.source, ref=self.ref)
        if self.modo == "guardar":
            try:
                h["number"] = int(self.vnum.get())
            except ValueError:
                raise ValueError("Número de asiento no válido.")
        return h, lineas

    def ok(self, nuevo=False):
        if self.modo == "ver":
            self.destroy(); return
        try:
            h, lineas = self._leer()
            if self.modo == "documento":
                verificar(self, self.gl, h, lineas, self.jv_id)
                self.result = (h, lineas); self.destroy(); return
            jid = guardar_con_bloqueo(self, self.gl, h, lineas, self.jv_id)
        except ValueError as e:
            messagebox.showwarning("Asiento", str(e), parent=self); return
        if jid is None:
            return
        self.result = jid; self.destroy()
        if nuevo and self.nuevo_cb:
            self.nuevo_cb()


def verificar(parent, gl, h, lineas, jv_id=None):
    """Revisa cuadre y bloqueo antes de guardar el documento (sin grabar nada)."""
    viejo = gl.con.execute("SELECT date FROM gl_jv WHERE id=?", (jv_id,)).fetchone() if jv_id else None
    try:
        gl._validar(h["date"], [l for l in lineas if abs(l["amount"]) > 0.0001], viejo["date"] if viejo else None)
    except Bloqueado as e:
        if not messagebox.askyesno("Período bloqueado", f"{e}\n\n¿Desea desbloquear para grabar este asiento?", parent=parent):
            raise ValueError("No se grabó: el período está bloqueado.")
        h["_desbloqueo"] = gl.bloqueo()
        gl.set_bloqueo(None)


def guardar_con_bloqueo(parent, gl, h, lineas, jv_id=None):
    """Graba; si la fecha cae en el periodo bloqueado pregunta si desbloquear y luego si volver a bloquear."""
    antes = h.pop("_desbloqueo", None)
    try:
        jid = gl.guardar_asiento(h, lineas, jv_id)
    except Bloqueado as e:
        if not messagebox.askyesno("Período bloqueado", f"{e}\n\n¿Desea desbloquear para grabar este asiento?", parent=parent):
            return None
        antes = gl.bloqueo(); gl.set_bloqueo(None)
        jid = gl.guardar_asiento(h, lineas, jv_id)
    if antes and messagebox.askyesno("Período bloqueado", f"Asiento grabado.\n¿Volver a bloquear hasta "
                                     f"{H['dmy'](antes)}?", parent=parent):
        gl.set_bloqueo(antes)
    return jid


# =====================================================================================  integracion con Facturacion
def pedir_asiento_documento(parent, gl, ref, titulo, info, descr, fecha, op=None, montos=None):
    """Con op: asiento automatico sin ventana.
    Abre la ventana de asiento para un documento de Facturación.
    Devuelve (h, lineas, jv_id) o None si el usuario cancela. Si el documento no lleva asiento
    (fecha anterior a la de arranque y sin asiento previo) devuelve False."""
    if op:
        return asiento_automatico(parent, gl, ref, descr, fecha, op, montos or {}, titulo, info)
    jv_id = gl.asiento_de(ref)
    if not jv_id and not gl.contabiliza(fecha):
        return False
    if not gl.arranque():
        return False
    d = AsientoDialog(parent, gl, jv_id=jv_id, modo="documento", titulo=titulo, info=info, descr=descr,
                      fecha=fecha, ref=ref, source=ORIGEN.get(ref.split(":")[0], "Facturación"))
    if jv_id:
        # al editar se respeta lo ya puesto, pero la fecha sigue a la del documento
        d.vfecha.set(H["dmy"](fecha))
    parent.wait_window(d)
    if not d.result:
        return None
    h, lineas = d.result
    return h, lineas, jv_id


def grabar_asiento_documento(parent, gl, r):
    if not r:
        return
    h, lineas, jv_id = r
    try:
        guardar_con_bloqueo(parent, gl, h, lineas, jv_id)
    except Exception as e:
        messagebox.showerror("Asiento", f"El documento se guardó, pero el asiento no:\n{e}\n\n"
                             "Ábralo de nuevo para registrar su asiento.", parent=parent)


def anular_documento(parent, gl, ref, descr):
    """Al eliminar un documento con asiento se registra el reverso con fecha de hoy."""
    if not gl.asiento_de(ref):
        return True
    try:
        gl.reversar_ref(ref, datetime.date.today(), descr)
    except Bloqueado as e:
        messagebox.showwarning("Asiento", f"No se pudo reversar el asiento: {e}", parent=parent); return False
    except ValueError as e:
        messagebox.showwarning("Asiento", f"No se pudo reversar el asiento: {e}", parent=parent); return False
    return True


# =====================================================================================  pestaña Diario General
def pedir_rango(app, titulo, d1="", d2=""):
    """Pide siempre Desde / Hasta. Devuelve (iso1, iso2) o None."""
    hoy = datetime.date.today()
    r = H["form_dialog"](app.root, titulo, [("d1", "Desde", "date"), ("d2", "Hasta", "date")],
                         {"d1": d1 or H["dmy"](datetime.date(hoy.year, 1, 1)), "d2": d2 or H["dmy"](hoy)})
    if not r: return None
    try:
        a, b = H["read_dmy"](r["d1"]), H["read_dmy"](r["d2"])
    except ValueError as e:
        messagebox.showwarning("Fechas", str(e)); return None
    if a > b:
        messagebox.showwarning("Fechas", "La fecha Desde es mayor que Hasta."); return None
    return a.isoformat(), b.isoformat()


class DiarioTab:
    def __init__(self, app):
        self.app = app; self.gl = app.gl
        fr = ttk.Frame(app.nb); app.nb.add(fr, text="Diario General")
        top = ttk.Frame(fr); top.pack(fill="x", pady=4)
        hoy = datetime.date.today()
        self.vd = tk.StringVar(value=H["dmy"](datetime.date(hoy.year, 1, 1))); self.vh = tk.StringVar(value=H["dmy"](hoy))
        self.vs = tk.StringVar()
        ttk.Label(top, text="Desde").pack(side="left", padx=3); H["fecha_entry"](top, self.vd).pack(side="left")
        ttk.Label(top, text="Hasta").pack(side="left", padx=3); H["fecha_entry"](top, self.vh).pack(side="left")
        ttk.Label(top, text="Buscar").pack(side="left", padx=(10, 3))
        e = ttk.Entry(top, textvariable=self.vs, width=25); e.pack(side="left"); e.bind("<Return>", lambda ev: self.load())
        ttk.Button(top, text="Filtrar por esas fechas", command=self.load).pack(side="left", padx=4)
        ttk.Button(top, text="Ver todos los asientos", command=self.todo).pack(side="left")
        ttk.Button(top, text="Este año", command=self.este_anio).pack(side="left", padx=4)
        cols = ("Período", "No.", "Origen", "Fecha", "Descripción", "Monto")
        self.tv = ttk.Treeview(fr, columns=cols, show="headings")
        for c, w in zip(cols, (80, 80, 90, 100, 520, 130)):
            self.tv.heading(c, text=c, command=lambda c=c: self.ordenar(c))
            self.tv.column(c, width=w, minwidth=w if c != "Descripción" else 200, anchor="center",
                           stretch=(c == "Descripción"))
        sb = ttk.Scrollbar(fr, orient="vertical", command=self.tv.yview); self.tv.configure(yscrollcommand=sb.set)
        b = ttk.Frame(fr); b.pack(side="bottom", fill="x", pady=4)
        sb.pack(side="right", fill="y"); self.tv.pack(fill="both", expand=True)
        self.tv.bind("<Double-1>", lambda e: self.editar())
        acts = [("Nuevo", self.nuevo), ("Editar", self.editar), ("Eliminar", self.eliminar), ("Copiar", self.copiar),
                ("Diario en PDF", self.pdf)]
        for t, cmd in acts:
            ttk.Button(b, text=t, command=cmd).pack(side="left", padx=3)
        self.lbl = ttk.Label(b, text=""); self.lbl.pack(side="right", padx=8)
        menu_tabla(self.tv, app.root, [("Nuevo", self.nuevo), ("Editar", self.editar), ("Ver asiento", self.editar),
                                       ("Copiar", self.copiar), ("Eliminar", self.eliminar), ("-", None),
                                       ("Buscar", lambda: e.focus_set()), ("Filtrar por fechas", lambda: self.load()),
                                       ("Diario en PDF", self.pdf)], sin_fila=("Nuevo", "Buscar", "Filtrar por fechas", "Diario en PDF"))
        self.orden = ("date", True)
        self.load()

    def todo(self):
        r = self.gl.con.execute("SELECT MIN(date), MAX(date) FROM gl_jv").fetchone()
        hoy = datetime.date.today()
        if r[0]: self.vd.set(H["dmy"](r[0]))
        fin = max(datetime.date.fromisoformat(str(r[1])[:10]), hoy) if r[1] else hoy
        self.vh.set(H["dmy"](fin)); self.vs.set(""); self.load()

    def este_anio(self):
        hoy = datetime.date.today()
        self.vd.set(H["dmy"](datetime.date(hoy.year, 1, 1))); self.vh.set(H["dmy"](hoy)); self.load()

    def ordenar(self, c):
        k = {"Período": "year*100+period", "No.": "number", "Origen": "source", "Fecha": "date",
             "Descripción": "descr", "Monto": "amount"}[c]
        self.orden = (k, not self.orden[1] if self.orden[0] == k else True); self.load()

    def load(self):
        try:
            d1 = H["read_dmy"](self.vd.get()).isoformat(); d2 = H["read_dmy"](self.vh.get()).isoformat()
        except ValueError as e:
            messagebox.showwarning("Fechas", str(e)); return
        self.tv.delete(*self.tv.get_children())
        s = self.vs.get().strip().lower(); n = 0; tot = 0
        k, desc = self.orden
        for r in self.gl.con.execute(f"SELECT * FROM gl_jv WHERE date BETWEEN ? AND ? ORDER BY {k} {'DESC' if desc else 'ASC'}, "
                                     "number DESC", (d1, d2)):
            if s and s not in f"{r['number']} {r['descr']} {r['source']} {r['amount']:.2f}".lower():
                continue
            self.tv.insert("", "end", iid=str(r["id"]), values=(f"{r['period']:02d}/{r['year']}", r["number"] or "AJ",
                                                                r["source"], H["dmy"](r["date"]), r["descr"], m2(r["amount"] or 0)))
            n += 1; tot += r["amount"] or 0
        self.lbl.config(text=f"{n} asientos   Total {m2(tot)}")

    def sel(self):
        s = self.tv.selection()
        if not s:
            messagebox.showinfo("Diario General", "Seleccione un asiento."); return None
        return int(s[0])

    def nuevo(self, copiar_de=None):
        d = AsientoDialog(self.app.root, self.gl, nuevo_cb=self.nuevo, copiar_de=copiar_de)
        self.app.root.wait_window(d)
        if d.result: self.app.refresh_all()

    def copiar(self):
        j = self.sel()
        if j: self.nuevo(copiar_de=j)

    def _es_documento(self, j):
        h = self.gl.con.execute("SELECT ref FROM gl_jv WHERE id=?", (j,)).fetchone()
        return h and h["ref"] and not h["ref"].startswith("REV:") and "#anulado" not in h["ref"]

    def editar(self):
        j = self.sel()
        if not j: return
        h = self.gl.con.execute("SELECT * FROM gl_jv WHERE id=?", (j,)).fetchone()
        if self._es_documento(j):
            tipo, nro = h["ref"].split(":", 1)
            messagebox.showinfo("Asiento de Facturación",
                                f"Este asiento viene de {ORIGEN.get(tipo, tipo)} {nro}.\n"
                                "Para corregirlo abra el documento en Facturación: al guardarlo se le vuelve "
                                "a preguntar el asiento y se corrige este mismo (no se crea otro).")
            d = AsientoDialog(self.app.root, self.gl, jv_id=j, modo="ver", titulo=f"Asiento {h['number']} (solo ver)")
            for f in d.filas:
                for k in ("e_deb", "e_cre", "e_code", "e_sub1", "e_sub2", "e_sub3", "e_name"):
                    f[k].state(["readonly"])
            return
        d = AsientoDialog(self.app.root, self.gl, jv_id=j, titulo=f"Asiento {h['number'] or 'de ajuste'}")
        self.app.root.wait_window(d)
        if d.result: self.app.refresh_all()

    def eliminar(self):
        j = self.sel()
        if not j: return
        if self._es_documento(j):
            messagebox.showinfo("Eliminar", "Este asiento pertenece a un documento de Facturación.\n"
                                "Si elimina ese documento, el asiento se reversa solo."); return
        h = self.gl.con.execute("SELECT number, descr FROM gl_jv WHERE id=?", (j,)).fetchone()
        if messagebox.askyesno("Eliminar", f"¿Eliminar el asiento {h['number'] or ''} {h['descr']}?"):
            try:
                self.gl.borrar_asiento(j)
            except Bloqueado as e:
                messagebox.showwarning("Eliminar", str(e)); return
            self.app.refresh_all()

    def pdf(self):
        rg = pedir_rango(self.app, "Diario General en PDF", self.vd.get(), self.vh.get())
        if not rg: return
        d1, d2 = rg
        ruta = self.app.rep_path(f"diario_{d1}_{d2}.pdf")
        try:
            rep_contab.diario(self.gl, d1, d2, ruta)
        except PermissionError:
            messagebox.showwarning("PDF", "Ese PDF está abierto. Ciérrelo y vuelva a intentar."); return
        H["open_file"](ruta)


class CuentasTab:
    def __init__(self, app, nb=None):
        self.app = app; self.gl = app.gl
        nb = nb or app.nb
        fr = ttk.Frame(nb); nb.add(fr, text="Catálogo de cuentas")
        top = ttk.Frame(fr); top.pack(fill="x", pady=4)
        self.vs = tk.StringVar(); self.vtipo = tk.StringVar(value="Todas")
        ttk.Label(top, text="Buscar").pack(side="left", padx=3)
        e = ttk.Entry(top, textvariable=self.vs, width=25); e.pack(side="left"); e.bind("<Return>", lambda ev: self.load())
        ttk.Label(top, text="Tipo").pack(side="left", padx=(10, 3))
        cb = ttk.Combobox(top, textvariable=self.vtipo, values=["Todas"] + list(TIPOS_CTA.values()), width=10, state="readonly")
        cb.pack(side="left"); cb.bind("<<ComboboxSelected>>", lambda ev: self.load())
        ttk.Button(top, text="Mostrar", command=self.load).pack(side="left", padx=4)
        cols = ("Cuenta", "Nombre", "Tipo", "Saldo actual", "Estado")
        self.tv = ttk.Treeview(fr, columns=cols, show="headings")
        for c, w in zip(cols, (100, 420, 90, 150, 100)):
            self.tv.heading(c, text=c); self.tv.column(c, width=w, anchor="e" if c == "Saldo actual" else "w")
        sb = ttk.Scrollbar(fr, orient="vertical", command=self.tv.yview); self.tv.configure(yscrollcommand=sb.set)
        b = ttk.Frame(fr); b.pack(side="bottom", fill="x", pady=4)
        sb.pack(side="right", fill="y"); self.tv.pack(fill="both", expand=True)
        self.tv.bind("<Double-1>", lambda e: self.editar())
        for t, cmd in (("Nuevo", self.nuevo), ("Editar", self.editar), ("Eliminar", self.eliminar),
                       ("Mayor de la cuenta (PDF)", self.mayor), ("Historial", self.historial)):
            ttk.Button(b, text=t, command=cmd).pack(side="left", padx=3)
        menu_tabla(self.tv, app.root, [("Nuevo", self.nuevo), ("Editar", self.editar), ("Ver mayor (PDF)", self.mayor),
                                       ("Historial", self.historial), ("Eliminar", self.eliminar)], sin_fila=("Nuevo",))
        self.load()

    def load(self):
        self.tv.delete(*self.tv.get_children())
        hoy = datetime.date.today()
        sal = dict((c["id"], v) for c, v in self.gl.balanza(hoy.year, 12))
        s = self.vs.get().strip().lower(); t = self.vtipo.get()
        for c in self.gl.cuentas():
            tipo = TIPOS_CTA.get(c["type"], "")
            if t != "Todas" and tipo != t: continue
            if s and s not in f"{c['code']} {c['name']}".lower(): continue
            est = "Suspendida" if c["suspended"] else ("Gan. retenidas" if c["retained"] else "")
            self.tv.insert("", "end", iid=str(c["id"]), values=(c["code"], c["name"], tipo, m2(sal.get(c["id"], 0)), est))

    def sel(self):
        s = self.tv.selection()
        if not s:
            messagebox.showinfo("Cuentas", "Seleccione una cuenta."); return None
        return int(s[0])

    def nuevo(self):
        d = CuentaDialog(self.app.root, self.gl); self.app.root.wait_window(d)
        if d.result: self.load()

    def editar(self):
        c = self.sel()
        if c is None: return
        d = CuentaDialog(self.app.root, self.gl, c); self.app.root.wait_window(d)
        if d.result: self.load()

    def eliminar(self):
        c = self.sel()
        if c is None: return
        a = self.gl.cuenta(c)
        if not messagebox.askyesno("Eliminar", f"¿Eliminar la cuenta {a['code']} {a['name']}?"):
            return
        try:
            self.gl.borrar_cuenta(c)
        except ValueError as e:
            messagebox.showwarning("Eliminar", str(e)); return
        self.load()

    def _rango(self, titulo):
        hoy = datetime.date.today()
        r = H["form_dialog"](self.app.root, titulo, [("d1", "Desde", "date"), ("d2", "Hasta", "date")],
                             {"d1": H["dmy"](datetime.date(hoy.year, 1, 1)), "d2": H["dmy"](hoy)})
        if not r: return None
        try:
            return H["read_dmy"](r["d1"]).isoformat(), H["read_dmy"](r["d2"]).isoformat()
        except ValueError as e:
            messagebox.showwarning("Fechas", str(e)); return None

    def mayor(self):
        c = self.sel()
        if c is None: return
        rg = self._rango("Mayor de la cuenta")
        if not rg: return
        a = self.gl.cuenta(c)
        ruta = self.app.rep_path(f"mayor_{a['code']}_{rg[0]}_{rg[1]}.pdf")
        try:
            rep_contab.mayor(self.gl, c, rg[0], rg[1], ruta)
        except PermissionError:
            messagebox.showwarning("PDF", "Ese PDF está abierto. Ciérrelo y vuelva a intentar."); return
        H["open_file"](ruta)

    def historial(self):
        c = self.sel()
        if c is None: return
        rg = self._rango("Historial de la cuenta")
        if not rg: return
        a = self.gl.cuenta(c)
        ini, movs = self.gl.mayor(c, *rg)
        top = tk.Toplevel(self.app.root); top.title(f"Historial {a['code']} {a['name']}"); top.geometry("900x500")
        cols = ("Fecha", "No.", "Descripción", "Débito", "Crédito", "Saldo")
        tv = ttk.Treeview(top, columns=cols, show="headings")
        for col, w in zip(cols, (90, 70, 400, 110, 110, 120)):
            tv.heading(col, text=col); tv.column(col, width=w, anchor="w" if col in ("Fecha", "Descripción") else "e")
        sb = ttk.Scrollbar(top, orient="vertical", command=tv.yview); tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); tv.pack(fill="both", expand=True)
        s = ini
        tv.insert("", "end", values=("", "", "Saldo inicial", "", "", m2(s)))
        for m in movs:
            s += m["amount"]
            tv.insert("", "end", iid=f"j{m['id']}_{len(tv.get_children())}",
                      values=(H["dmy"](m["date"]), m["number"] or "AJ", m["descr"],
                              m2(m["amount"]) if m["amount"] > 0 else "", m2(-m["amount"]) if m["amount"] < 0 else "", m2(s)))
        def ver():
            sl = tv.selection()
            if sl and sl[0].startswith("j"):
                jid = int(sl[0][1:].split("_")[0])
                AsientoDialog(top, self.gl, jv_id=jid, modo="ver", titulo="Asiento (solo ver)")
        tv.bind("<Double-1>", lambda e: ver())
        menu_tabla(tv, top, [("Ver asiento", ver)])


# =====================================================================================  pestaña Reportes contables
class ReportesContTab:
    """Reportes contables (van dentro de la pestaña Reportes) y ajustes de contabilidad (van en Configuración)."""
    def __init__(self, app, rep_parent, cfg_nb, resp_parent):
        self.app = app; self.gl = app.gl
        hoy = datetime.date.today()
        # ---- reportes contables, dentro de la pestaña Reportes
        box = ttk.LabelFrame(rep_parent, text="Reportes contables", padding=12); box.pack(anchor="nw", fill="x")
        self.vper = tk.StringVar(value=str(hoy.month)); self.vyear = tk.StringVar(value=str(hoy.year))
        pf = ttk.Frame(box); pf.pack(anchor="w", pady=(0, 10))
        ttk.Label(pf, text="Período").pack(side="left")
        ttk.Spinbox(pf, from_=1, to=12, textvariable=self.vper, width=4).pack(side="left", padx=4)
        ttk.Label(pf, text="Año").pack(side="left")
        ttk.Entry(pf, textvariable=self.vyear, width=6).pack(side="left", padx=4)
        for t, fn, nom in (("Estado de resultados", rep_contab.estado_resultados, "estado_resultados"),
                           ("Balance General", rep_contab.balance_general, "balance_general"),
                           ("Balanza de comprobación", rep_contab.balanza, "balanza")):
            ttk.Button(box, text=t, width=40, command=lambda fn=fn, nom=nom: self.run(fn, nom)).pack(anchor="w", pady=3)
        ttk.Button(box, text="Diario General (PDF)", width=40, command=self.diario_pdf).pack(anchor="w", pady=3)
        ttk.Button(box, text="Mayor de una cuenta (PDF)", width=40, command=self.mayor_pdf).pack(anchor="w", pady=3)
        # ---- ajustes de contabilidad, dentro de Configuración
        fr = ttk.Frame(cfg_nb); cfg_nb.add(fr, text="Contabilidad")
        imp = ttk.Frame(fr, padding=20); imp.pack(anchor="nw", fill="x")
        a = ttk.LabelFrame(imp, text="Fecha de arranque", padding=10); a.pack(anchor="w", fill="x", pady=6)
        ttk.Label(a, text="Desde esta fecha cada documento de Facturación pide su asiento:").grid(row=0, column=0, sticky="w")
        self.varr = tk.StringVar(value=H["dmy"](self.gl.arranque()) if self.gl.arranque() else "")
        H["fecha_entry"](a, self.varr).grid(row=0, column=1, padx=6)
        ttk.Button(a, text="Guardar", command=self.guardar_arranque).grid(row=0, column=2)
        bl = ttk.LabelFrame(imp, text="Bloquear meses ya cerrados", padding=10); bl.pack(anchor="w", fill="x", pady=6)
        ttk.Label(bl, text="Sirve para que nadie cambie por error lo que ya está cuadrado y declarado.\n"
                           "Ningún asiento con fecha igual o anterior a la fecha de bloqueo se puede grabar, cambiar ni\n"
                           "borrar (tampoco desde Facturación) hasta que se quite el bloqueo. Vacío = sin bloqueo.",
                  justify="left").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        ttk.Label(bl, text="Bloqueado hasta").grid(row=1, column=0, sticky="w")
        self.vbloq = tk.StringVar(value=H["dmy"](self.gl.bloqueo()) if self.gl.bloqueo() else "")
        H["fecha_entry"](bl, self.vbloq).grid(row=1, column=1, padx=6)
        ttk.Button(bl, text="Bloquear", command=self.bloquear).grid(row=1, column=2)
        ttk.Button(bl, text="Quitar bloqueo", command=self.desbloquear).grid(row=1, column=3, padx=4)
        o = ttk.LabelFrame(imp, text="Asientos de Facturación", padding=10); o.pack(anchor="w", fill="x", pady=6)
        ttk.Button(o, text="Cuentas automáticas (asientos de Facturación)", width=44,
                   command=lambda: CuentasAutoDialog(self.app.root, self.gl)).pack(anchor="w", pady=2)
        # ---- importar la contabilidad anterior, en la pestaña Respaldo
        ttk.Button(resp_parent, text="Importar contabilidad anterior (.zip)", width=45,
                   command=lambda: self.importar(True)).pack(anchor="w", pady=4)
        ttk.Button(resp_parent, text="Importar contabilidad anterior (carpeta)", width=45,
                   command=lambda: self.importar(False)).pack(anchor="w", pady=4)

    def diario_pdf(self):
        rg = pedir_rango(self.app, "Diario General en PDF")
        if not rg: return
        ruta = self.app.rep_path(f"diario_{rg[0]}_{rg[1]}.pdf")
        try:
            rep_contab.diario(self.gl, rg[0], rg[1], ruta)
        except PermissionError:
            messagebox.showwarning("PDF", "Ese PDF está abierto. Ciérrelo y vuelva a intentar."); return
        H["open_file"](ruta)

    def mayor_pdf(self):
        rows = [(c["code"], c["name"]) for c in self.gl.con.execute(
            "SELECT code, name FROM gl_accounts ORDER BY code").fetchall()]
        p = H["Picker"](self.app.root, "Mayor de una cuenta: elija la cuenta", ("Código", "Cuenta"), rows, [120, 560])
        if not p.result: return
        c = self.gl.cuenta_por_codigo(p.result[0])
        if not c: return
        rg = pedir_rango(self.app, "Mayor de la cuenta " + c["code"])
        if not rg: return
        ruta = self.app.rep_path(f"mayor_{c['code']}_{rg[0]}_{rg[1]}.pdf")
        try:
            rep_contab.mayor(self.gl, c["id"], rg[0], rg[1], ruta)
        except PermissionError:
            messagebox.showwarning("PDF", "Ese PDF está abierto. Ciérrelo y vuelva a intentar."); return
        H["open_file"](ruta)

    def bloquear(self):
        try:
            d = H["read_dmy"](self.vbloq.get())
        except ValueError as e:
            messagebox.showwarning("Bloqueo", str(e)); return
        if messagebox.askyesno("Bloqueo", f"Nadie podrá grabar, cambiar ni borrar asientos con fecha hasta el "
                                          f"{H['dmy'](d)} (inclusive), ni aquí ni desde Facturación, sin quitar el bloqueo antes.\n\n¿Bloquear?"):
            self.gl.set_bloqueo(d); messagebox.showinfo("Bloqueo", f"Bloqueado hasta {H['dmy'](d)}.")

    def desbloquear(self):
        if not self.gl.bloqueo():
            messagebox.showinfo("Bloqueo", "No hay bloqueo."); return
        if messagebox.askyesno("Bloqueo", f"¿Quitar el bloqueo (estaba hasta {H['dmy'](self.gl.bloqueo())})?"):
            self.gl.set_bloqueo(None); self.vbloq.set(""); messagebox.showinfo("Bloqueo", "Sin bloqueo.")

    def run(self, fn, nom):
        try:
            per, year = int(self.vper.get()), int(self.vyear.get())
            assert 1 <= per <= 12
        except Exception:
            messagebox.showwarning("Reporte", "Período o año no válido."); return
        ruta = self.app.rep_path(f"{nom}_{year}_{per:02d}.pdf")
        try:
            fn(self.gl, year, per, ruta)
        except PermissionError:
            messagebox.showwarning("PDF", "Ese PDF está abierto. Ciérrelo y vuelva a intentar."); return
        H["open_file"](ruta)

    def guardar_arranque(self):
        try:
            d = H["read_dmy"](self.varr.get())
        except ValueError as e:
            messagebox.showwarning("Fecha de arranque", str(e)); return
        if messagebox.askyesno("Fecha de arranque", f"Desde el {H['dmy'](d)}, cada venta, nota, conduce, compra recibida, "
                               "cobro, pago y ajuste de inventario le pedirá su asiento.\n"
                               "Lo anterior a esa fecha no genera asientos (ya está en su diario).\n\n¿Guardar?"):
            self.gl.set_arranque(d)

    def importar(self, es_zip):
        p = filedialog.askopenfilename(title="Respaldo de la contabilidad", filetypes=[("Zip", "*.zip")]) if es_zip else \
            filedialog.askdirectory(title="Carpeta de la contabilidad (ej. backup general)")
        if not p: return
        if not messagebox.askyesno("Importar", "Se importan el catálogo de cuentas y TODOS los asientos de la contabilidad anterior.\n"
                                   "Si ya se importó antes, se reemplaza lo importado (no se duplica).\n"
                                   "Los asientos hechos en este programa no se tocan.\n\n¿Seguir?"):
            return
        self.app.db.backup("antes_de_importar")
        self.app.root.config(cursor="watch"); self.app.root.update()
        try:
            r = self.gl.importar_anterior(p, lambda m: (self.app.banner.config(text=m), self.app.root.update()))
        except Exception as e:
            self.app.root.config(cursor="")
            messagebox.showerror("Importar", f"No se pudo importar:\n{e}"); return
        self.app.root.config(cursor="")
        if not self.gl.arranque():
            self.gl.set_arranque(datetime.date.today()); self.varr.set(H["dmy"](datetime.date.today()))
        self.app.refresh_all()
        messagebox.showinfo("Importar", f"Importado:\n  Cuentas: {r['cuentas']}\n  Asientos: {r['asientos']}\n"
                            f"  Líneas: {r['lineas']}\n  Asientos sin líneas (vacíos en el programa anterior): {r['sin_lineas']}\n"
                            f"  Asientos descuadrados en el programa anterior: {r['descuadrados']}\n"
                            f"  Asientos de ajuste para cuadrar con los saldos anteriores: {r['ajustes']}\n\n"
                            f"Fecha de arranque: {H['dmy'](self.gl.arranque())}")


# =====================================================================================  asientos automaticos
# clave -> (nombre, cuenta sugerida)
CUENTAS_AUTO = {
    "cxc": ("Cuentas por cobrar", "1112"),
    "ventas_fiscal": ("Ventas con comprobante", "4000"),
    "ventas_libres": ("Ventas libres (sin comprobante)", "4010"),
    "itbis_pagar": ("ITBIS por pagar", "2040"),
    "costo": ("Costo de la mercancía vendida", "5000"),
    "inventario": ("Inventario", "1200"),
    "banco_cobros": ("Banco donde entran los cobros", "1004"),
    "cxp": ("Cuentas por pagar a suplidores", ""),
    "itbis_compras": ("ITBIS pagado en compras", "5118"),
    "banco_pagos": ("Banco de donde salen los pagos a suplidores", "1004"),
    "ajuste_inv": ("Diferencias de inventario (ajustes)", "5113"),
}
NECESITA = {
    "venta": ["cxc", "ventas_fiscal", "ventas_libres", "itbis_pagar", "costo", "inventario"],
    "nc": ["cxc", "ventas_fiscal", "ventas_libres", "itbis_pagar", "costo", "inventario"],
    "nd": ["cxc", "ventas_fiscal", "ventas_libres", "itbis_pagar"],
    "conduce": ["costo", "inventario"],
    "compra": ["inventario", "itbis_compras", "cxp"],
    "cobro": ["banco_cobros", "cxc"],
    "pago": ["cxp", "banco_pagos"],
    "ajuste": ["inventario", "ajuste_inv"],
}


def cta_auto(gl, k):
    code = gl.db.cfg("gl_auto_" + k) or ""
    c = gl.cuenta_por_codigo(code) if code else None
    return c["id"] if c and not c["suspended"] else None


class CuentasAutoDialog(tk.Toplevel):
    """Cuentas que usan los asientos automáticos. Se pregunta una sola vez; se cambian aquí cuando quiera."""
    def __init__(self, parent, gl, claves=None, motivo=""):
        super().__init__(parent)
        self.gl = gl; self.ok_ = False
        self.title("Cuentas automáticas"); self.transient(parent); self.grab_set(); self.resizable(False, False)
        claves = claves or list(CUENTAS_AUTO)
        fr = ttk.Frame(self, padding=14); fr.pack(fill="both")
        if motivo:
            ttk.Label(fr, text=motivo, justify="left", foreground="#1a3d7c").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        self.vs = {}
        for i, k in enumerate(claves, 1):
            nom, sug = CUENTAS_AUTO[k]
            v = tk.StringVar(value=gl.db.cfg("gl_auto_" + k) or (sug if gl.cuenta_por_codigo(sug) else ""))
            lbl = ttk.Label(fr, text="", width=40)
            def ver(*a, v=v, lbl=lbl):
                c = gl.cuenta_por_codigo(v.get().strip())
                lbl.config(text=c["name"] if c else ("(no existe)" if v.get().strip() else "(elija la cuenta)"))
            v.trace_add("write", ver); ver()
            ttk.Label(fr, text=nom).grid(row=i, column=0, sticky="e", padx=4, pady=3)
            e = ttk.Entry(fr, textvariable=v, width=10); e.grid(row=i, column=1, sticky="w")
            ttk.Button(fr, text="...", width=3, command=lambda v=v: self.buscar(v)).grid(row=i, column=2, padx=2)
            lbl.grid(row=i, column=3, sticky="w")
            e.bind("<Double-1>", lambda ev, v=v: self.buscar(v))
            self.vs[k] = v
        bf = ttk.Frame(self, padding=10); bf.pack(fill="x")
        ttk.Button(bf, text="Cancelar", command=self.destroy).pack(side="right", padx=3)
        ttk.Button(bf, text="Guardar", command=self.ok).pack(side="right", padx=3)

    def buscar(self, v):
        c = elegir_cuenta(self, self.gl)
        if c: v.set(c)

    def ok(self):
        for k, v in self.vs.items():
            c = self.gl.cuenta_por_codigo(v.get().strip())
            if not c:
                messagebox.showwarning("Cuentas automáticas", f"Elija la cuenta para: {CUENTAS_AUTO[k][0]}", parent=self); return
        for k, v in self.vs.items():
            self.gl.db.set_cfg("gl_auto_" + k, v.get().strip())
        self.ok_ = True; self.destroy()


def lineas_auto(gl, op, m):
    """Arma el asiento segun la operacion. m: sub, desc, itb, total, costo, monto, fiscal (bool), devolucion (bool)."""
    a = lambda k: cta_auto(gl, k)
    neto = round((m.get("sub") or 0) - (m.get("desc") or 0), 2)
    itb = round(m.get("itb") or 0, 2); tot = round(m.get("total") or 0, 2); costo = round(m.get("costo") or 0, 2)
    ventas = a("ventas_fiscal") if m.get("fiscal") else a("ventas_libres")
    L = []
    def add(k, v, cid=None):
        if abs(v) > 0.004:
            L.append(dict(account_id=cid or a(k), amount=round(v, 2)))
    if op in ("venta", "nd"):
        add("cxc", tot); add(None, -neto, ventas); add("itbis_pagar", -itb)
        if op == "venta":
            add("costo", costo); add("inventario", -costo)
    elif op == "nc":
        add("cxc", -tot); add(None, neto, ventas); add("itbis_pagar", itb)
        if m.get("devolucion"):
            add("costo", -costo); add("inventario", costo)
    elif op == "conduce":
        add("costo", costo); add("inventario", -costo)
    elif op == "compra":
        add("inventario", neto); add("itbis_compras", itb); add("cxp", -tot)
    elif op == "cobro":
        add("banco_cobros", m["monto"]); add("cxc", -m["monto"])
    elif op == "pago":
        add("cxp", m["monto"]); add("banco_pagos", -m["monto"])
    elif op == "ajuste":
        add("inventario", m["monto"]); add("ajuste_inv", -m["monto"])
    return L


def asiento_automatico(parent, gl, ref, descr, fecha, op, montos, titulo="Asiento", info=""):
    """Asiento sin ventana. Devuelve (h, lineas, jv_id), False si no lleva asiento, None si se cancela."""
    jv_id = gl.asiento_de(ref)
    if not gl.arranque() or (not jv_id and not gl.contabiliza(fecha)):
        return False
    faltan = [k for k in NECESITA[op] if not cta_auto(gl, k)]
    if faltan:
        d = CuentasAutoDialog(parent, gl, faltan, "Esto se pregunta una sola vez. Desde ahora el asiento de esta operación\n"
                                                  "se hace solo con estas cuentas (se cambian en Configuración > Contabilidad > Cuentas automáticas).")
        parent.wait_window(d)
        if not d.ok_:
            return None
    lineas = lineas_auto(gl, op, montos)
    if not lineas:
        if jv_id:   # el documento quedo en cero: se quita su asiento
            gl.borrar_asiento(jv_id)
        return False
    # la ventana sale ya llena: el usuario revisa y da OK (o cambia algo)
    pre = []
    for l in lineas:
        c = gl.cuenta(l["account_id"])
        pre.append(dict(amount=l["amount"], code=c["code"], sub1=None, sub2=None, sub3=None, name=""))
    d = AsientoDialog(parent, gl, jv_id=jv_id, modo="documento", titulo=titulo, info=info, descr=descr,
                      fecha=fecha, ref=ref, source=ORIGEN.get(ref.split(":")[0], "Facturación"), prellenar=pre)
    d.vfecha.set(H["dmy"](fecha if not isinstance(fecha, str) else datetime.date.fromisoformat(fecha)))
    parent.wait_window(d)
    if not d.result:
        return None
    h, lineas = d.result
    return h, lineas, jv_id
