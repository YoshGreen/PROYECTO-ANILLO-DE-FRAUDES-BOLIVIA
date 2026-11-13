import time
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import WriteConcern

class ServicioEvaluadorRiesgo:
    LIMITE_VELOCIDAD = 5      # transferencias por minuto
    UMBRAL_RECHAZO = 80       # riesgo >= 80 -> RECHAZADA_FRAUDE

    def __init__(self, r, bd):
        self.r = r
        self.bd = bd
        self.tx = bd.transacciones.with_options(
            write_concern=WriteConcern(w="majority")
        )

    def evaluar(self, id_emisor, id_receptor, tel_destino, device_id):
        """
        Evalúa en tiempo real usando un único pipeline en Redis (latencia submilisegundo).
        """
        p = self.r.pipeline()
        p.sismember("blacklist:users", str(id_emisor))
        p.sismember("blacklist:users", str(id_receptor))
        p.sismember("blacklist:devices", str(device_id))
        p.sismember("blacklist:phones", str(tel_destino))
        p.incr(f"velocity:tx:{id_emisor}")
        p.expire(f"velocity:tx:{id_emisor}", 60)
        p.get(f"risk:score:{id_emisor}")
        res = p.execute()

        # 1. Regla de listas negras O(1)
        if any(res[0:4]):
            motivo_detalle = []
            if res[0]: motivo_detalle.append("Emisor en lista negra")
            if res[1]: motivo_detalle.append("Receptor en lista negra")
            if res[2]: motivo_detalle.append("Dispositivo bloqueado")
            if res[3]: motivo_detalle.append("Teléfono de destino en lista negra")
            return {
                "decision": "RECHAZADA_FRAUDE",
                "riesgo": 100,
                "motivo": "LISTA_NEGRA",
                "detalle": ", ".join(motivo_detalle),
                "velocidad_actual": res[4]
            }

        # 2. Regla de velocidad transaccional (ráfaga)
        if res[4] > self.LIMITE_VELOCIDAD:
            return {
                "decision": "RECHAZADA_FRAUDE",
                "riesgo": 90,
                "motivo": "VELOCIDAD",
                "detalle": f"Límite de velocidad excedido: {res[4]} tx/min (máx. permitido: {self.LIMITE_VELOCIDAD})",
                "velocidad_actual": res[4]
            }

        # 3. Regla de puntuación de riesgo (caché en Redis con respaldo en MongoDB)
        score = res[6]
        if score is None:
            # Fallo de caché: consultar documento de usuario en MongoDB
            u = self.bd.usuarios.find_one({"_id": ObjectId(id_emisor)}, {"score_riesgo": 1})
            score = u.get("score_riesgo", 20) if u else 50
            self.r.setex(f"risk:score:{id_emisor}", 300, score)
        
        riesgo = int(score)
        if riesgo >= self.UMBRAL_RECHAZO:
            return {
                "decision": "RECHAZADA_FRAUDE",
                "riesgo": riesgo,
                "motivo": "SCORE",
                "detalle": f"Puntaje de riesgo de la cuenta muy elevado ({riesgo}/100)",
                "velocidad_actual": res[4]
            }

        return {
            "decision": "COMPLETADA",
            "riesgo": riesgo,
            "motivo": "AUTORIZADA",
            "detalle": "Evaluación de riesgo satisfactoria en tiempo real",
            "velocidad_actual": res[4]
        }

    def autorizar_pago(self, e, rc, monto, canal="APP_ANDROID", tipo_op="P2P_QR"):
        """
        Flujo completo: evaluación en Redis + registro inmutable en MongoDB + actualización de aristas.
        e y rc son diccionarios con la información de usuario (snapshot del Core).
        """
        inicio = time.perf_counter()
        
        device_id = e["dispositivos"][0]["device_id"] if e.get("dispositivos") else "DEV-UNKNOWN"
        tel_dest = rc.get("telefono_principal") or (rc.get("telefonos")[0] if rc.get("telefonos") else "")
        
        d = self.evaluar(str(e["_id"]), str(rc["_id"]), tel_dest, device_id)
        
        ahora = datetime.now(timezone.utc)
        cod_tx = f"TRX-{ObjectId()}"
        
        doc_tx = {
            "codigo_transaccion": cod_tx,
            "monto": float(monto),
            "moneda": "BOB",
            "tipo_operacion": tipo_op,
            "canal": canal,
            "fecha_hora": ahora,
            "estado": d["decision"],
            "riesgo_calculado": d["riesgo"],
            "motivo_decision": d["motivo"],
            "geolocalizacion": {"lat": -16.49, "lon": -68.12},
            "emisor": {
                "id_usuario": e["_id"],
                "nombre": e.get("nombre_completo", "Desconocido"),
                "telefono": e.get("telefono_principal", ""),
                "device_id_origen": device_id,
                "score_riesgo_momento": e.get("score_riesgo", 0)
            },
            "receptor": {
                "id_usuario": rc["_id"],
                "nombre": rc.get("nombre_completo", "Desconocido"),
                "telefono": tel_dest
            }
        }
        
        self.tx.insert_one(doc_tx)
        
        # Si la transacción fue completada, se actualiza la proyección de grafos aristas_flujo
        if d["decision"] == "COMPLETADA":
            self.bd.aristas_flujo.update_one(
                {"id_emisor": e["_id"], "id_receptor": rc["_id"]},
                {
                    "$inc": {"monto_acumulado": float(monto), "cantidad_tx": 1},
                    "$set": {"ultima_fecha": ahora}
                },
                upsert=True
            )
            
        latencia_ms = round((time.perf_counter() - inicio) * 1000, 2)
        d["latencia_ms"] = latencia_ms
        d["codigo_transaccion"] = cod_tx
        d["doc_id"] = str(doc_tx.get("_id"))
        return d


class ServicioDetectorAnillos:
    def __init__(self, bd):
        self.bd = bd

    def dispositivos_compartidos(self, minimo=2):
        """
        Escenario 3: Identifica dispositivos compartidos por N o más cuentas (cuentas fantasma).
        """
        pipeline = [
            {"$unwind": "$dispositivos"},
            {"$group": {
                "_id": "$dispositivos.device_id",
                "total_cuentas": {"$sum": 1},
                "usuarios_ids": {"$push": "$_id"},
                "modelos": {"$addToSet": "$dispositivos.modelo"}
            }},
            {"$match": {"total_cuentas": {"$gte": minimo}}},
            {"$sort": {"total_cuentas": -1}}
        ]
        res = list(self.bd.usuarios.aggregate(pipeline))
        # Enriquecer con nombres de usuarios
        for r in res:
            users = list(self.bd.usuarios.find(
                {"_id": {"$in": r["usuarios_ids"]}},
                {"nombre_completo": 1, "ci_documento": 1, "telefono_principal": 1, "estado_cuenta": 1}
            ))
            r["usuarios_detalle"] = users
        return res

    def telefonos_compartidos(self, minimo=2):
        """
        Escenario 2: Identifica líneas telefónicas vinculadas a múltiples cuentas.
        """
        pipeline = [
            {"$unwind": "$telefonos"},
            {"$group": {
                "_id": "$telefonos",
                "total_cuentas": {"$sum": 1},
                "usuarios_ids": {"$push": "$_id"}
            }},
            {"$match": {"total_cuentas": {"$gte": minimo}}},
            {"$sort": {"total_cuentas": -1}}
        ]
        res = list(self.bd.usuarios.aggregate(pipeline))
        for r in res:
            users = list(self.bd.usuarios.find(
                {"_id": {"$in": r["usuarios_ids"]}},
                {"nombre_completo": 1, "ci_documento": 1, "estado_cuenta": 1}
            ))
            r["usuarios_detalle"] = users
        return res

    def anillos_desde(self, id_usuario, max_depth=4):
        """
        Escenario 5: Detecta ciclos o anillos de transferencias desde un usuario específico con $graphLookup.
        """
        if isinstance(id_usuario, str):
            id_usuario = ObjectId(id_usuario)

        pipeline = [
            {"$match": {"id_emisor": id_usuario}},
            {"$graphLookup": {
                "from": "aristas_flujo",
                "startWith": "$id_receptor",
                "connectFromField": "id_receptor",
                "connectToField": "id_emisor",
                "as": "cadena_transferencias",
                "maxDepth": max_depth,
                "depthField": "nivel_salto"
            }},
            {"$match": {"cadena_transferencias.id_receptor": id_usuario}}
        ]
        return list(self.bd.aristas_flujo.aggregate(pipeline))

    def detectar_todos_anillos(self, max_depth=4):
        """
        Detecta todos los ciclos de fraude en la colección de aristas_flujo.
        Reconstruye el orden de los saltos y nombres de las cuentas del anillo.
        """
        # Obtenemos aristas con ciclos
        pipeline = [
            {"$graphLookup": {
                "from": "aristas_flujo",
                "startWith": "$id_receptor",
                "connectFromField": "id_receptor",
                "connectToField": "id_emisor",
                "as": "cadena",
                "maxDepth": max_depth,
                "depthField": "nivel_salto"
            }},
            {"$match": {
                "$expr": {
                    "$in": ["$id_emisor", "$cadena.id_receptor"]
                }
            }}
        ]
        
        aristas_con_ciclo = list(self.bd.aristas_flujo.aggregate(pipeline))
        
        # Mapeamos usuarios para mostrar nombres
        user_ids = set()
        for a in aristas_con_ciclo:
            user_ids.add(a["id_emisor"])
            user_ids.add(a["id_receptor"])
            for c in a.get("cadena", []):
                user_ids.add(c["id_emisor"])
                user_ids.add(c["id_receptor"])
                
        users_map = {u["_id"]: u for u in self.bd.usuarios.find({"_id": {"$in": list(user_ids)}})}
        
        anillos_detectados = []
        vistos = set()

        for a in aristas_con_ciclo:
            emisor = a["id_emisor"]
            receptor = a["id_receptor"]
            
            # Buscar en la cadena cómo se cierra el ciclo hacia emisor
            cadena_ordenada = sorted(a.get("cadena", []), key=lambda x: x.get("nivel_salto", 0))
            
            # Identificar los nodos del ciclo
            miembros_ciclo = [emisor, receptor]
            curr = receptor
            for step in cadena_ordenada:
                if step["id_emisor"] == curr:
                    miembros_ciclo.append(step["id_receptor"])
                    curr = step["id_receptor"]
                    if curr == emisor:
                        break
                        
            # Si se cerró en el emisor
            if len(miembros_ciclo) >= 3 and miembros_ciclo[-1] == emisor:
                ciclo_core = tuple(sorted(str(m) for m in set(miembros_ciclo[:-1])))
                if ciclo_core not in vistos:
                    vistos.add(ciclo_core)
                    
                    # Detalle legible
                    detalle_nodos = []
                    for uid in miembros_ciclo[:-1]:
                        u = users_map.get(uid, {})
                        detalle_nodos.append({
                            "id": str(uid),
                            "nombre": u.get("nombre_completo", f"ID {str(uid)[:6]}"),
                            "ci": u.get("ci_documento", "-"),
                            "telefono": u.get("telefono_principal", "-"),
                            "estado": u.get("estado_cuenta", "ACTIVA"),
                            "dispositivo": u.get("dispositivos", [{}])[0].get("device_id", "-") if u.get("dispositivos") else "-"
                        })
                    
                    anillos_detectados.append({
                        "tamano": len(detalle_nodos),
                        "nodos": detalle_nodos,
                        "primer_emisor": users_map.get(emisor, {}).get("nombre_completo", str(emisor)),
                        "monto_primer_salto": a.get("monto_acumulado", 0),
                        "cantidad_tx": a.get("cantidad_tx", 1)
                    })
                    
        return sorted(anillos_detectados, key=lambda x: x["tamano"])

    def ruta_entre(self, id_a, id_b, max_depth=4):
        """
        Escenario 7: Rastrea la ruta del dinero entre dos usuarios A y B.
        """
        if isinstance(id_a, str): id_a = ObjectId(id_a)
        if isinstance(id_b, str): id_b = ObjectId(id_b)

        pipeline = [
            {"$match": {"id_emisor": id_a}},
            {"$graphLookup": {
                "from": "aristas_flujo",
                "startWith": "$id_receptor",
                "connectFromField": "id_receptor",
                "connectToField": "id_emisor",
                "as": "cadena",
                "maxDepth": max_depth,
                "depthField": "nivel_salto"
            }},
            {"$match": {"$or": [
                {"id_receptor": id_b},
                {"cadena.id_receptor": id_b}
            ]}},
            {"$project": {
                "id_emisor": 1,
                "id_receptor": 1,
                "monto_acumulado": 1,
                "cantidad_tx": 1,
                "cadena.id_emisor": 1,
                "cadena.id_receptor": 1,
                "cadena.monto_acumulado": 1,
                "cadena.nivel_salto": 1
            }}
        ]
        return list(self.bd.aristas_flujo.aggregate(pipeline))


class ServicioGestorBloqueos:
    def __init__(self, r, bd):
        self.r = r
        self.bd = bd

    def _publicar(self, u):
        """Publica identificadores de la cuenta bloqueada en Redis con O(1)."""
        p = self.r.pipeline()
        p.sadd("blacklist:users", str(u["_id"]))
        for d in u.get("dispositivos", []):
            if d.get("device_id"):
                p.sadd("blacklist:devices", str(d["device_id"]))
        for t in u.get("telefonos", []):
            if t:
                p.sadd("blacklist:phones", str(t))
        p.execute()

    def confirmar_alerta(self, id_alerta):
        """
        Escenario 8: Confirma una alerta pendiente, bloquea al usuario en MongoDB
        y propaga el bloqueo a los Sets de Redis.
        """
        if isinstance(id_alerta, str):
            id_alerta = ObjectId(id_alerta)

        al = self.bd.alertas_fraude.find_one_and_update(
            {"_id": id_alerta, "estado": "PENDIENTE"},
            {"$set": {"estado": "CONFIRMADO", "fecha_confirmacion": datetime.now(timezone.utc)}}
        )
        if al is None:
            return False

        u = self.bd.usuarios.find_one_and_update(
            {"_id": al["id_usuario_sospechoso"]},
            {"$set": {
                "estado_cuenta": "BLOQUEADA",
                "dispositivos.$[].es_bloqueado": True
            }},
            return_document=True
        )
        if u:
            self._publicar(u)
        return True

    def bloquear_usuario_directo(self, id_usuario, motivo="BLOQUEO_MANUAL"):
        """Bloquea un usuario directamente y publica en Redis."""
        if isinstance(id_usuario, str):
            id_usuario = ObjectId(id_usuario)

        u = self.bd.usuarios.find_one_and_update(
            {"_id": id_usuario},
            {"$set": {
                "estado_cuenta": "BLOQUEADA",
                "dispositivos.$[].es_bloqueado": True
            }},
            return_document=True
        )
        if u:
            self._publicar(u)
            # Registrar alerta automática
            self.bd.alertas_fraude.insert_one({
                "id_transaccion": None,
                "id_usuario_sospechoso": id_usuario,
                "tipo_regla": "BLOQUEO_DIRECTO",
                "nivel_riesgo": "CRITICO",
                "detalle": f"Bloqueo aplicado: {motivo}",
                "estado": "CONFIRMADO",
                "fecha_deteccion": datetime.now(timezone.utc),
                "fecha_confirmacion": datetime.now(timezone.utc)
            })
            return True
        return False

    def desbloquear_usuario(self, id_usuario):
        """Desbloquea una cuenta y retira de las listas negras de Redis (útil para pruebas)."""
        if isinstance(id_usuario, str):
            id_usuario = ObjectId(id_usuario)

        u = self.bd.usuarios.find_one_and_update(
            {"_id": id_usuario},
            {"$set": {
                "estado_cuenta": "ACTIVA",
                "dispositivos.$[].es_bloqueado": False
            }},
            return_document=True
        )
        if u:
            self.r.srem("blacklist:users", str(u["_id"]))
            for d in u.get("dispositivos", []):
                self.r.srem("blacklist:devices", str(d["device_id"]))
            for t in u.get("telefonos", []):
                self.r.srem("blacklist:phones", str(t))
            return True
        return False

    def rehidratar(self):
        """Reconstruye los sets de Redis a partir de los usuarios bloqueados en MongoDB."""
        n = 0
        for u in self.bd.usuarios.find({"estado_cuenta": "BLOQUEADA"}):
            self._publicar(u)
            n += 1
        return n
