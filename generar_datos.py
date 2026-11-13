import random
import time
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from pymongo import MongoClient, WriteConcern
from config import get_mongo_db, get_redis_client

def crear_esquema_e_indices(bd):
    """Crea colecciones e índices según la especificación del informe."""
    colecciones_existentes = bd.list_collection_names()
    
    # Crear colecciones si no existen
    if "usuarios" not in colecciones_existentes:
        try:
            bd.create_collection("usuarios", validator={
                "$jsonSchema": {
                    "bsonType": "object",
                    "required": ["nombre_completo", "ci_documento", "telefono_principal",
                                 "telefonos", "estado_cuenta", "dispositivos", "version_esquema"],
                    "properties": {
                        "nombre_completo": {"bsonType": "string"},
                        "ci_documento": {"bsonType": "string"},
                        "telefono_principal": {"bsonType": "string", "pattern": "^\\+591[67][0-9]{7}$"},
                        "telefonos": {"bsonType": "array", "minItems": 1, "maxItems": 4, "items": {"bsonType": "string"}},
                        "estado_cuenta": {"enum": ["ACTIVA", "SUSPENDIDA", "BLOQUEADA"]},
                        "score_riesgo": {"bsonType": ["int", "long", "double"], "minimum": 0, "maximum": 100},
                        "dispositivos": {
                            "bsonType": "array", "minItems": 1, "maxItems": 4,
                            "items": {
                                "bsonType": "object", "required": ["device_id"],
                                "properties": {
                                    "device_id": {"bsonType": "string"},
                                    "modelo": {"bsonType": "string"},
                                    "sistema_operativo": {"bsonType": "string"},
                                    "es_bloqueado": {"bsonType": "bool"}
                                }
                            }
                        }
                    }
                }
            })
        except Exception as e:
            print("Aviso al crear colección usuarios con validador:", e)
            
    for c in ["transacciones", "aristas_flujo", "alertas_fraude", "reportes_estafa"]:
        if c not in colecciones_existentes:
            try:
                bd.create_collection(c)
            except Exception:
                pass

    # Crear índices obligatorios del diseño NoSQL (Tabla de índices)
    bd.usuarios.create_index([("dispositivos.device_id", 1)])
    bd.usuarios.create_index([("telefonos", 1)])
    bd.usuarios.create_index([("ci_documento", 1)], unique=True)
    bd.transacciones.create_index([("emisor.id_usuario", 1), ("fecha_hora", -1)])
    bd.transacciones.create_index([("receptor.id_usuario", 1), ("fecha_hora", -1)])
    bd.transacciones.create_index([("codigo_transaccion", 1)], unique=True)
    bd.aristas_flujo.create_index([("id_emisor", 1), ("id_receptor", 1)], unique=True)
    bd.alertas_fraude.create_index([("nivel_riesgo", 1), ("estado", 1), ("fecha_deteccion", -1)])
    bd.reportes_estafa.create_index([("id_usuario_denunciado", 1)])

def poblar_datos(n_usuarios=300, n_tx=1500):
    """
    Genera un conjunto de datos sintéticos sembrados, ligero y optimizado para laptop.
    Incluye exactamente los 5 anillos de fraude, cuentas bloqueadas y tráfico normal.
    """
    inicio = time.perf_counter()
    random.seed(42)
    bd = get_mongo_db()
    r = get_redis_client()

    crear_esquema_e_indices(bd)

    # 1. Limpieza inicial para garantizar consistencia reproducible
    for c in ["usuarios", "transacciones", "aristas_flujo", "alertas_fraude", "reportes_estafa"]:
        bd[c].delete_many({})
    
    # Limpiar claves de Redis
    r.delete("blacklist:users", "blacklist:devices", "blacklist:phones")
    # Limpiar contadores de velocidad y caché
    keys_vel = r.keys("velocity:tx:*")
    if keys_vel: r.delete(*keys_vel)
    keys_risk = r.keys("risk:score:*")
    if keys_risk: r.delete(*keys_risk)

    TAM_ANILLOS = [3, 3, 4, 4, 5]   # Exactamente 5 anillos sembrados = 19 cuentas
    N_BLOQUEADOS = 10
    CONTACTOS = 3
    AHORA = datetime.now(timezone.utc)
    
    NOMBRES = ["Juan", "Carlos", "Maria", "Ana", "Luis", "Erick", "Yoshua", "Pedro", "Sofia", "Diego", 
               "Valeria", "Alejandro", "Camila", "Mateo", "Lucia", "Rodrigo", "Fernanda", "Gabriel", "Elena"]
    APELLIDOS = ["Mamani", "Quispe", "Flores", "Condori", "Vargas", "Rojas", "Gomez", "Lopez", "Torrez", "Castillo"]
    MODELOS = ["Samsung Galaxy A14", "Xiaomi Redmi 12", "Motorola Moto G23", "iPhone 12", "Honor X8"]
    SISTEMAS = ["Android 13", "Android 14", "iOS 17"]

    # 2. Generación de usuarios normales
    usuarios = []
    for i in range(n_usuarios):
        tel = f"+5917{i:07d}"
        nom = f"{random.choice(NOMBRES)} {random.choice(APELLIDOS)}"
        usuarios.append({
            "_id": ObjectId(),
            "nombre_completo": f"{nom} ({i})",
            "ci_documento": f"{5000000 + i} LP",
            "telefono_principal": tel,
            "telefonos": [tel],
            "email": f"user{i}@yape.bo",
            "estado_cuenta": "ACTIVA",
            "score_riesgo": random.randint(5, 30),
            "fecha_registro": AHORA - timedelta(days=random.randint(10, 180)),
            "version_esquema": 1,
            "dispositivos": [{
                "device_id": f"DEV-{i:06d}",
                "modelo": random.choice(MODELOS),
                "sistema_operativo": random.choice(SISTEMAS),
                "fecha_vinculacion": AHORA - timedelta(days=random.randint(5, 100)),
                "es_bloqueado": False
            }]
        })

    txs = []
    pos = 0

    # 3. Sembrado de los 5 Anillos de Fraude (dispositivos y teléfonos compartidos + transferencias en ciclo)
    for k, tam in enumerate(TAM_ANILLOS):
        miembros = usuarios[pos:pos + tam]
        pos += tam
        base = AHORA - timedelta(hours=2 + k)
        dev_shared = f"DEV-SHARED-{k:02d}"
        phone_shared = f"+591690000{k:02d}"

        for u in miembros:
            u["dispositivos"][0]["device_id"] = dev_shared
            u["telefonos"].append(phone_shared)
            u["score_riesgo"] = random.randint(40, 75)

        # Crear ciclo de transferencias: A -> B -> C -> ... -> A
        for j, emisor in enumerate(miembros):
            receptor = miembros[(j + 1) % tam]
            monto = round(4900.0 - (50 * j), 2)
            fecha_tx = base + timedelta(minutes=4 * j)
            txs.append({
                "codigo_transaccion": f"TRX-RING-{k}-{j}",
                "monto": monto,
                "moneda": "BOB",
                "tipo_operacion": "P2P_QR",
                "canal": "APP_ANDROID",
                "fecha_hora": fecha_tx,
                "estado": "COMPLETADA",
                "riesgo_calculado": emisor["score_riesgo"],
                "motivo_decision": "AUTORIZADA",
                "geolocalizacion": {"lat": -16.49, "lon": -68.12},
                "emisor": {
                    "id_usuario": emisor["_id"],
                    "nombre": emisor["nombre_completo"],
                    "telefono": emisor["telefono_principal"],
                    "device_id_origen": dev_shared,
                    "score_riesgo_momento": emisor["score_riesgo"]
                },
                "receptor": {
                    "id_usuario": receptor["_id"],
                    "nombre": receptor["nombre_completo"],
                    "telefono": receptor["telefono_principal"]
                }
            })

    # 4. Sembrado de cuentas ya bloqueadas (publicadas en Redis)
    for u in usuarios[pos:pos + N_BLOQUEADOS]:
        u["estado_cuenta"] = "BLOQUEADA"
        u["score_riesgo"] = 95
        u["dispositivos"][0]["es_bloqueado"] = True
        r.sadd("blacklist:users", str(u["_id"]))
        r.sadd("blacklist:devices", u["dispositivos"][0]["device_id"])
        r.sadd("blacklist:phones", u["telefono_principal"])

    # 5. Sembrado de tráfico normal disperso (grafo ralo de contactos frecuentes)
    contactos = {}
    for i in range(n_usuarios):
        posibles = [j for j in range(n_usuarios) if j != i]
        contactos[i] = random.sample(posibles, min(CONTACTOS, len(posibles)))

    for n in range(n_tx - len(txs)):
        i = random.randrange(n_usuarios)
        j = random.choice(contactos[i])
        e = usuarios[i]
        rc = usuarios[j]
        monto = round(random.uniform(15, 650), 2)
        fecha_tx = AHORA - timedelta(minutes=random.randint(5, 60 * 24 * 14))
        
        txs.append({
            "codigo_transaccion": f"TRX-NORM-{n:06d}",
            "monto": monto,
            "moneda": "BOB",
            "tipo_operacion": random.choice(["P2P_QR", "P2P_CELULAR"]),
            "canal": random.choice(["APP_ANDROID", "APP_IOS"]),
            "fecha_hora": fecha_tx,
            "estado": "COMPLETADA",
            "riesgo_calculado": e["score_riesgo"],
            "motivo_decision": "AUTORIZADA",
            "geolocalizacion": {"lat": -16.49 + random.uniform(-.03, .03), "lon": -68.12 + random.uniform(-.03, .03)},
            "emisor": {
                "id_usuario": e["_id"],
                "nombre": e["nombre_completo"],
                "telefono": e["telefono_principal"],
                "device_id_origen": e["dispositivos"][0]["device_id"],
                "score_riesgo_momento": e["score_riesgo"]
            },
            "receptor": {
                "id_usuario": rc["_id"],
                "nombre": rc["nombre_completo"],
                "telefono": rc["telefono_principal"]
            }
        })

    # Inserción en MongoDB
    bd.usuarios.insert_many(usuarios)
    tx_col = bd.transacciones.with_options(write_concern=WriteConcern(w="majority"))
    for chunk_start in range(0, len(txs), 2000):
        tx_col.insert_many(txs[chunk_start:chunk_start + 2000], ordered=False)

    # 6. Proyección computada aristas_flujo mediante agregación $merge (Patrón Computed del informe)
    bd.transacciones.aggregate([
        {"$match": {"estado": "COMPLETADA"}},
        {"$group": {
            "_id": {"e": "$emisor.id_usuario", "r": "$receptor.id_usuario"},
            "monto_acumulado": {"$sum": "$monto"},
            "cantidad_tx": {"$sum": 1},
            "ultima_fecha": {"$max": "$fecha_hora"}
        }},
        {"$project": {
            "_id": 0,
            "id_emisor": "$_id.e",
            "id_receptor": "$_id.r",
            "monto_acumulado": 1,
            "cantidad_tx": 1,
            "ultima_fecha": 1
        }},
        {"$merge": {
            "into": "aristas_flujo",
            "on": ["id_emisor", "id_receptor"],
            "whenMatched": "replace",
            "whenNotMatched": "insert"
        }}
    ])

    # 7. Sembrar un par de alertas pendientes en alertas_fraude para demostración inmediata
    primer_sospechoso = usuarios[0]
    bd.alertas_fraude.insert_one({
        "id_transaccion": None,
        "id_usuario_sospechoso": primer_sospechoso["_id"],
        "nombre_sospechoso": primer_sospechoso["nombre_completo"],
        "dispositivo_sospechoso": primer_sospechoso["dispositivos"][0]["device_id"],
        "tipo_regla": "ANILLO_CIRCULAR",
        "nivel_riesgo": "CRITICO",
        "detalle": "Ciclo de transferencias circulares cerrado y hardware compartido (DEV-SHARED-00)",
        "estado": "PENDIENTE",
        "fecha_deteccion": AHORA
    })

    duracion = round(time.perf_counter() - inicio, 2)
    return {
        "usuarios": bd.usuarios.count_documents({}),
        "transacciones": bd.transacciones.count_documents({}),
        "aristas": bd.aristas_flujo.count_documents({}),
        "alertas": bd.alertas_fraude.count_documents({}),
        "redis_users": r.scard("blacklist:users"),
        "redis_devices": r.scard("blacklist:devices"),
        "redis_phones": r.scard("blacklist:phones"),
        "duracion_segundos": duracion
    }

if __name__ == "__main__":
    print("Iniciando generación de datos liviana para laptop...")
    res = poblar_datos(300, 1500)
    print("Resultado:", res)
