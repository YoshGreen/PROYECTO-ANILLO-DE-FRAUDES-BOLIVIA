// Utilidades globales y llamadas asíncronas para la aplicación web

async function ejecutarPoblar() {
    const escala = document.getElementById("selectEscala").value;
    const [u, tx] = escala.split("-").map(Number);
    const loading = document.getElementById("poblarLoading");
    const btn = document.getElementById("btnEjecutarPoblar");

    loading.classList.remove("d-none");
    btn.disabled = true;

    try {
        const response = await fetch("/api/poblar", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ n_usuarios: u, n_tx: tx })
        });
        const data = await response.json();
        if (data.ok) {
            alert(`¡Datos generados con éxito!\n\n` +
                  `• Usuarios creados: ${data.res.usuarios}\n` +
                  `• Transacciones registradas: ${data.res.transacciones}\n` +
                  `• Aristas computadas: ${data.res.aristas}\n` +
                  `• Cuentas en Redis Blacklist: ${data.res.redis_users}\n` +
                  `• Tiempo total: ${data.res.duracion_segundos} s`);
            window.location.reload();
        } else {
            alert("Error al generar datos: " + data.error);
        }
    } catch (err) {
        alert("Error de conexión al servidor: " + err);
    } finally {
        loading.classList.add("d-none");
        btn.disabled = false;
    }
}

async function rehidratarRedis() {
    if (!confirm("¿Deseas reconstruir las listas negras de Redis a partir de los usuarios bloqueados en MongoDB?")) return;
    try {
        const resp = await fetch("/api/rehidratar-redis", { method: "POST" });
        const data = await resp.json();
        if (data.ok) {
            alert(`Rehidratación exitosa. Se sincronizaron ${data.rehidratados} cuentas bloqueadas a Redis.`);
            window.location.reload();
        } else {
            alert("Error: " + data.error);
        }
    } catch (err) {
        alert("Error de red: " + err);
    }
}

async function confirmarAlerta(idAlerta) {
    if (!confirm("¿Confirmar esta alerta de fraude? Esto cambiará el estado de la cuenta a BLOQUEADA en MongoDB y la publicará en los Sets de Redis.")) return;
    try {
        const resp = await fetch(`/api/confirmar-alerta/${idAlerta}`, { method: "POST" });
        const data = await resp.json();
        if (data.ok) {
            alert("Alerta confirmada y bloqueo en cascada propagado a MongoDB y Redis.");
            window.location.reload();
        } else {
            alert("No se pudo confirmar la alerta: " + (data.error || "Alerta ya procesada"));
        }
    } catch (e) {
        alert("Error: " + e);
    }
}

async function bloquearUsuarioDirecto(idUsuario, motivo) {
    motivo = motivo || prompt("Motivo del bloqueo:", "Auditoría manual de seguridad");
    if (!motivo) return;

    try {
        const resp = await fetch("/api/bloquear-usuario", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id_usuario: idUsuario, motivo: motivo })
        });
        const data = await resp.json();
        if (data.ok) {
            alert("Usuario bloqueado y publicado en Redis blacklist:users, devices y phones.");
            window.location.reload();
        } else {
            alert("Error: " + data.error);
        }
    } catch (e) {
        alert("Error: " + e);
    }
}

async function desbloquearUsuario(idUsuario) {
    if (!confirm("¿Desbloquear esta cuenta y remover de las listas negras de Redis?")) return;
    try {
        const resp = await fetch("/api/desbloquear-usuario", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id_usuario: idUsuario })
        });
        const data = await resp.json();
        if (data.ok) {
            alert("Cuenta desbloqueada con éxito.");
            window.location.reload();
        } else {
            alert("Error: " + data.error);
        }
    } catch (e) {
        alert("Error: " + e);
    }
}
