import os
import json
from datetime import datetime, timezone
from bson import ObjectId
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash

from config import get_mongo_db, get_redis_client, check_services
from servicios import ServicioEvaluadorRiesgo, ServicioDetectorAnillos, ServicioGestorBloqueos
from generar_datos import poblar_datos

app = Flask(__name__)
app.secret_key = "yape_antifraude_secreto_local_2026"

def json_encoder(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj

@app.context_processor
def inject_services_status():
    status = check_services()
    return dict(services_status=status)

# =========================================================================
# RUTAS DE INTERFAZ DE USUARIO (HTML)
# =========================================================================

@app.route("/")
def index():
    bd = get_mongo_db()
    r = get_redis_client()
    
    try:
        total_usuarios = bd.usuarios.count_documents({})
        total_tx = bd.transacciones.count_documents({})
        total_aristas = bd.aristas_flujo.count_documents({})
        alertas_pendientes = bd.alertas_fraude.count_documents({"estado": "PENDIENTE"})
        alertas_confirmadas = bd.alertas_fraude.count_documents({"estado": "CONFIRMADO"})
        cuentas_bloqueadas = bd.usuarios.count_documents({"estado_cuenta": "BLOQUEADA"})
        
        redis_users = r.scard("blacklist:users")
        redis_devices = r.scard("blacklist:devices")
        redis_phones = r.scard("blacklist:phones")
        
        # Últimas transacciones
        ultimas_tx = list(bd.transacciones.find().sort("fecha_hora", -1).limit(6))
        # Alertas críticas
        ultimas_alertas = list(bd.alertas_fraude.find({"estado": "PENDIENTE"}).sort("fecha_deteccion", -1).limit(4))
    except Exception as e:
        flash(f"Error al conectar con las bases de datos locales: {e}", "danger")
        total_usuarios = total_tx = total_aristas = alertas_pendientes = alertas_confirmadas = cuentas_bloqueadas = 0
        redis_users = redis_devices = redis_phones = 0
        ultimas_tx = []
        ultimas_alertas = []

    return render_template("index.html",
                           total_usuarios=total_usuarios,
                           total_tx=total_tx,
                           total_aristas=total_aristas,
                           alertas_pendientes=alertas_pendientes,
                           alertas_confirmadas=alertas_confirmadas,
                           cuentas_bloqueadas=cuentas_bloqueadas,
                           redis_users=redis_users,
                           redis_devices=redis_devices,
                           redis_phones=redis_phones,
                           ultimas_tx=ultimas_tx,
                           ultimas_alertas=ultimas_alertas)

@app.route("/transferir")
def transferir():
    bd = get_mongo_db()
    try:
        usuarios_normales = list(bd.usuarios.find({"estado_cuenta": "ACTIVA"}).limit(15))
        usuarios_anillo = list(bd.usuarios.find({"dispositivos.device_id": {"$regex": "^DEV-SHARED"}}).limit(10))
        usuarios_bloqueados = list(bd.usuarios.find({"estado_cuenta": "BLOQUEADA"}).limit(5))
    except Exception as e:
        usuarios_normales = []
        usuarios_anillo = []
        usuarios_bloqueados = []
        flash(f"Aviso de conexión MongoDB: {e}", "warning")
    return render_template("transferir.html",
                           usuarios_normales=usuarios_normales,
                           usuarios_anillo=usuarios_anillo,
                           usuarios_bloqueados=usuarios_bloqueados)

@app.route("/anillos")
def anillos():
    return redirect(url_for("trazabilidad"))

@app.route("/cuentas-fantasma")
def cuentas_fantasma():
    bd = get_mongo_db()
    det = ServicioDetectorAnillos(bd)
    try:
        disp_compartidos = det.dispositivos_compartidos(minimo=2)
        tel_compartidos = det.telefonos_compartidos(minimo=2)
    except Exception as e:
        disp_compartidos = []
        tel_compartidos = []
        flash(f"Error al analizar cuentas fantasma: {e}", "danger")
    return render_template("cuentas_fantasma.html",
                           disp_compartidos=disp_compartidos,
                           tel_compartidos=tel_compartidos)

@app.route("/alertas")
def alertas():
    bd = get_mongo_db()
    try:
        lista_alertas = list(bd.alertas_fraude.find().sort("fecha_deteccion", -1))
    except Exception as e:
        lista_alertas = []
        flash(f"Error al obtener alertas: {e}", "danger")
    return render_template("alertas.html", lista_alertas=lista_alertas)

@app.route("/listas-negras")
def listas_negras():
    r = get_redis_client()
    bd = get_mongo_db()
    try:
        users = list(r.smembers("blacklist:users"))
        devices = list(r.smembers("blacklist:devices"))
        phones = list(r.smembers("blacklist:phones"))
        
        # Enriquecer datos de usuarios bloqueados
        user_ids = []
        for u in users:
            try: user_ids.append(ObjectId(u))
            except: pass
        users_info = {str(doc["_id"]): doc for doc in bd.usuarios.find({"_id": {"$in": user_ids}})}
        
        # Contadores de velocidad activos
        vel_keys = r.keys("velocity:tx:*")
        velocidades = []
        for vk in vel_keys:
            uid = vk.replace("velocity:tx:", "")
            cnt = r.get(vk)
            ttl = r.ttl(vk)
            u_doc = users_info.get(uid) or bd.usuarios.find_one({"_id": ObjectId(uid)}) if ObjectId.is_valid(uid) else None
            velocidades.append({
                "clave": vk,
                "usuario_id": uid,
                "nombre": u_doc.get("nombre_completo", "N/A") if u_doc else "N/A",
                "contador": cnt,
                "ttl_restante": ttl
            })
    except Exception as e:
        users = devices = phones = []
        users_info = {}
        velocidades = []
        flash(f"Error al leer Redis: {e}", "danger")

    return render_template("listas_negras.html",
                           users=users,
                           devices=devices,
                           phones=phones,
                           users_info=users_info,
                           velocidades=velocidades)

@app.route("/trazabilidad")
def trazabilidad():
    bd = get_mongo_db()
    try:
        usuarios = list(bd.usuarios.find({}, {"nombre_completo": 1, "ci_documento": 1, "telefono_principal": 1}).limit(40))
    except Exception as e:
        usuarios = []
        flash(f"Aviso de conexión MongoDB: {e}", "warning")
    return render_template("trazabilidad.html", usuarios=usuarios)

@app.route("/compass-info")
def compass_info():
    return render_template("compass_info.html")


# =========================================================================
# API REST (JSON ENDPOINTS)
# =========================================================================

@app.route("/api/poblar", methods=["POST"])
def api_poblar():
    try:
        data = request.get_json() or {}
        n_usuarios = int(data.get("n_usuarios", 300))
        n_tx = int(data.get("n_tx", 1500))
        res = poblar_datos(n_usuarios=n_usuarios, n_tx=n_tx)
        return jsonify({"ok": True, "res": res})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/autorizar-pago", methods=["POST"])
def api_autorizar_pago():
    bd = get_mongo_db()
    r = get_redis_client()
    ev = ServicioEvaluadorRiesgo(r, bd)

    data = request.get_json() or {}
    id_emisor = data.get("id_emisor")
    id_receptor = data.get("id_receptor")
    monto = float(data.get("monto", 10.0))
    canal = data.get("canal", "APP_ANDROID")
    tipo_op = data.get("tipo_op", "P2P_QR")

    if not id_emisor or not id_receptor:
        return jsonify({"ok": False, "error": "Debe especificar emisor y receptor"}), 400

    try:
        e = bd.usuarios.find_one({"_id": ObjectId(id_emisor)})
        rc = bd.usuarios.find_one({"_id": ObjectId(id_receptor)})
        
        if not e or not rc:
            return jsonify({"ok": False, "error": "Emisor o receptor no existen"}), 404

        resultado = ev.autorizar_pago(e, rc, monto, canal=canal, tipo_op=tipo_op)
        return jsonify({"ok": True, "resultado": resultado})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/confirmar-alerta/<id_alerta>", methods=["POST"])
def api_confirmar_alerta(id_alerta):
    bd = get_mongo_db()
    r = get_redis_client()
    gest = ServicioGestorBloqueos(r, bd)
    try:
        ok = gest.confirmar_alerta(id_alerta)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/bloquear-usuario", methods=["POST"])
def api_bloquear_usuario():
    bd = get_mongo_db()
    r = get_redis_client()
    gest = ServicioGestorBloqueos(r, bd)
    data = request.get_json() or {}
    id_usuario = data.get("id_usuario")
    motivo = data.get("motivo", "Bloqueo preventivo por auditor")
    try:
        ok = gest.bloquear_usuario_directo(id_usuario, motivo)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/desbloquear-usuario", methods=["POST"])
def api_desbloquear_usuario():
    bd = get_mongo_db()
    r = get_redis_client()
    gest = ServicioGestorBloqueos(r, bd)
    data = request.get_json() or {}
    id_usuario = data.get("id_usuario")
    try:
        ok = gest.desbloquear_usuario(id_usuario)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/rehidratar-redis", methods=["POST"])
def api_rehidratar():
    bd = get_mongo_db()
    r = get_redis_client()
    gest = ServicioGestorBloqueos(r, bd)
    try:
        n = gest.rehidratar()
        return jsonify({"ok": True, "rehidratados": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/ruta-dinero", methods=["POST"])
def api_ruta_dinero():
    bd = get_mongo_db()
    det = ServicioDetectorAnillos(bd)
    data = request.get_json() or {}
    id_a = data.get("id_emisor")
    id_b = data.get("id_receptor")
    if not id_a or not id_b:
        return jsonify({"ok": False, "error": "Seleccione emisor y receptor"}), 400

    try:
        rutas = det.ruta_entre(id_a, id_b, max_depth=5)
        # Extraer nombres para visualización
        all_ids = set()
        for r in rutas:
            all_ids.add(r["id_emisor"])
            all_ids.add(r["id_receptor"])
            for c in r.get("cadena", []):
                all_ids.add(c["id_emisor"])
                all_ids.add(c["id_receptor"])

        u_map = {str(u["_id"]): u["nombre_completo"] for u in bd.usuarios.find({"_id": {"$in": list(all_ids)}})}

        resultado = []
        for r in rutas:
            resultado.append({
                "emisor": u_map.get(str(r["id_emisor"]), str(r["id_emisor"])),
                "receptor": u_map.get(str(r["id_receptor"]), str(r["id_receptor"])),
                "monto": r.get("monto_acumulado", 0),
                "saltos": [
                    {
                        "desde": u_map.get(str(c["id_emisor"]), str(c["id_emisor"])),
                        "hacia": u_map.get(str(c["id_receptor"]), str(c["id_receptor"])),
                        "monto": c.get("monto_acumulado", 0),
                        "nivel": c.get("nivel_salto", 1)
                    } for c in sorted(r.get("cadena", []), key=lambda x: x.get("nivel_salto", 0))
                ]
            })

        return jsonify({"ok": True, "rutas": resultado})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    print("=================================================================")
    print("  SISTEMA DE DETECCIÓN DE ANILLOS DE FRAUDE - YAPE BOLIVIA")
    print("  Base de Datos III - MongoDB & Redis (100% Local)")
    print("  Iniciando servidor web en: http://127.0.0.1:5000")
    print("=================================================================")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False, threaded=True)
