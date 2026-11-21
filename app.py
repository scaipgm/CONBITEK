import os
import time
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, render_template
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "4f32125044e3489b88e5aa8082da4cb0")
BASE_URL = "https://api.football-data.org/v4"

CACHE = {
    "timestamp": 0,
    "data": None
}
CACHE_TTL_SECONDS = 900  # 15 minutos


def formatear_horario_argentina(utc_str):
    if not utc_str:
        return "Horario por confirmar"
    try:
        dt_utc = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        dt_arg = dt_utc.astimezone(timezone(timedelta(hours=-3)))
        return dt_arg.strftime("%d/%m/%Y - %H:%M hs (ARG)")
    except Exception:
        return utc_str


def formatear_fecha_corta(utc_str):
    if not utc_str:
        return "Sin fecha"
    try:
        dt_utc = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        dt_arg = dt_utc.astimezone(timezone(timedelta(hours=-3)))
        return dt_arg.strftime("%d/%m/%Y")
    except Exception:
        return utc_str


def obtener_tabla_posiciones(competition_code, headers):
    posiciones = {}
    if not competition_code:
        return posiciones
    try:
        url = f"{BASE_URL}/competitions/{competition_code}/standings"
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            standings = res.json().get("standings", [])
            for st in standings:
                if st.get("type") == "TOTAL":
                    for row in st.get("table", []):
                        team_id = row.get("team", {}).get("id")
                        if team_id:
                            pj = max(1, row.get("playedGames") or 1)
                            gf = row.get("goalsFor") or 0
                            gc = row.get("goalsAgainst") or 0
                            w = row.get("won") or 0
                            d = row.get("draw") or 0
                            l = row.get("lost") or 0
                            form_raw = row.get("form") or ""
                            forma_lista = [c.strip() for c in form_raw.split(",") if c.strip() in ("W", "D", "L")]
                            forma_ved = ["V" if x == "W" else ("E" if x == "D" else "D") for x in forma_lista][-5:]

                            posiciones[team_id] = {
                                "posicion": row.get("position"),
                                "puntos": row.get("points"),
                                "pj": row.get("playedGames"),
                                "gf": gf,
                                "gc": gc,
                                "prom_gf": round(gf / pj, 2),
                                "prom_gc": round(gc / pj, 2),
                                "pct_anota": min(95, max(35, int(round((gf / pj) * 45 + 25)))),
                                "forma": forma_ved,
                                "resumen": f"{w}V - {d}E - {l}D (temporada)"
                            }
    except Exception as e:
        print(f"Error tabla {competition_code}: {e}")
    return posiciones


def obtener_ultimos_5_equipo(team_id, headers):
    forma = []
    gf_total = 0
    gc_total = 0
    anoto_en = 0
    cont = 0
    if not team_id:
        return None
    try:
        url = f"{BASE_URL}/teams/{team_id}/matches"
        res = requests.get(url, headers=headers, params={"status": "FINISHED", "limit": 5}, timeout=8)
        if res.status_code == 200:
            matches = res.json().get("matches", [])[-5:]
            for m in matches:
                ft = m.get("score", {}).get("fullTime", {})
                hg = ft.get("home")
                ag = ft.get("away")
                if hg is None or ag is None:
                    continue
                cont += 1
                es_local = m.get("homeTeam", {}).get("id") == team_id
                gf = hg if es_local else ag
                gc = ag if es_local else hg
                gf_total += gf
                gc_total += gc
                if gf > 0:
                    anoto_en += 1
                if gf > gc:
                    forma.append("V")
                elif gf < gc:
                    forma.append("D")
                else:
                    forma.append("E")
    except Exception as e:
        print(f"Error últimos 5 {team_id}: {e}")

    if cont == 0:
        return None

    return {
        "forma": forma,
        "resumen": f"{forma.count('V')}V - {forma.count('E')}E - {forma.count('D')}D (últ. {cont})",
        "prom_gf": round(gf_total / cont, 2),
        "prom_gc": round(gc_total / cont, 2),
        "pct_anota": int(round((anoto_en / cont) * 100))
    }


def construir_stats_fallback(info_equipo):
    if info_equipo:
        return {
            "forma": info_equipo.get("forma", []),
            "resumen": info_equipo.get("resumen", "Datos de tabla"),
            "prom_gf": info_equipo.get("prom_gf", 1.2),
            "prom_gc": info_equipo.get("prom_gc", 1.1),
            "pct_anota": info_equipo.get("pct_anota", 60)
        }
    return {
        "forma": [],
        "resumen": "Sin historial disponible",
        "prom_gf": 1.2,
        "prom_gc": 1.1,
        "pct_anota": 60
    }


def calcular_analisis_combinada(home_name, away_name, liga_name, pos_local, pos_visit, stats_local, stats_visit):
    prom_goles_partido = round(stats_local["prom_gf"] + stats_visit["prom_gf"], 2)
    btts_score = int(round((stats_local["pct_anota"] + stats_visit["pct_anota"]) / 2))

    if btts_score >= 65 and stats_local["prom_gc"] >= 0.9 and stats_visit["prom_gc"] >= 0.9:
        ambos_anotan = f"SÍ ({btts_score}%)"
    elif btts_score >= 52:
        ambos_anotan = f"Probable ({btts_score}%)"
    else:
        ambos_anotan = f"Bajo ({btts_score}%)"

    factor_ritmo = max(0.0, prom_goles_partido - 2.0)
    corners_est = round(8.5 + min(3.0, factor_ritmo * 1.1), 1)
    diff_pos = abs(pos_local - pos_visit) if (isinstance(pos_local, int) and isinstance(pos_visit, int)) else 6
    amarillas_est = round(3.8 + (0.7 if diff_pos <= 4 else 0.0) + (0.4 if btts_score >= 68 else 0.0), 1)
    offsides_est = round(3.0 + min(2.0, factor_ritmo * 0.7), 1)

    if prom_goles_partido >= 2.7 and btts_score >= 65:
        pick_sugerido = "Ambos Anotan (Sí) o Más de 2.0 Goles"
        cuota_est = 1.92
    elif prom_goles_partido <= 2.1:
        pick_sugerido = "Menos de 3.0 Goles + Doble Oportunidad"
        cuota_est = 1.78
    else:
        pick_sugerido = f"Más de 1.5 Goles + Más de {corners_est} Córners"
        cuota_est = 1.85

    pos_texto = ""
    if isinstance(pos_local, int) and isinstance(pos_visit, int):
        pos_texto = f"Tabla: {home_name} ({pos_local}°) vs {away_name} ({pos_visit}°). "

    diagnostico = (
        f"{pos_texto}{home_name} ({stats_local['prom_gf']} GF / {stats_local['prom_gc']} GC · {stats_local['resumen']}) "
        f"vs {away_name} ({stats_visit['prom_gf']} GF / {stats_visit['prom_gc']} GC · {stats_visit['resumen']}). "
        f"Proyección {liga_name}: {prom_goles_partido} goles estimados."
    )

    confianza = min(92, max(68, int(round(64 + (btts_score * 0.23)))))

    return {
        "pickSugerido": pick_sugerido,
        "cuotaEstimada": cuota_est,
        "confianza": confianza,
        "ambosAnotan": ambos_anotan,
        "golesPromedio": f"{prom_goles_partido} goles/partido",
        "cornersPromedio": f"Más de {corners_est} córners",
        "amarillasPromedio": f"~{amarillas_est} amarillas",
        "offsidesPromedio": f"~{offsides_est} fueras de juego",
        "diagnostico": diagnostico
    }


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/detalle-equipo/<int:team_id>", methods=["GET"])
def detalle_equipo(team_id):
    headers = {"X-Auth-Token": API_KEY}
    data = obtener_ultimos_5_equipo(team_id, headers)
    if not data:
        return jsonify({"status": "empty", "stats": None})
    return jsonify({"status": "success", "stats": data})


@app.route("/api/analizar-partidos", methods=["GET"])
def analizar_partidos():
    ahora = time.time()
    if CACHE["data"] is not None and (ahora - CACHE["timestamp"]) < CACHE_TTL_SECONDS:
        return jsonify(CACHE["data"])

    headers = {"X-Auth-Token": API_KEY}
    partidos_procesados = []
    tablas_cache = {}
    ligas_set = set()

    try:
        tz_arg = timezone(timedelta(hours=-3))
        hoy = (datetime.now(tz_arg) - timedelta(days=1)).strftime("%Y-%m-%d")
        hasta = (datetime.now(tz_arg) + timedelta(days=7)).strftime("%Y-%m-%d")

        res = requests.get(
            f"{BASE_URL}/matches",
            headers=headers,
            params={"dateFrom": hoy, "dateTo": hasta},
            timeout=10
        )

        matches = []
        if res.status_code == 200:
            matches = res.json().get("matches", [])

        if not matches:
            res_all = requests.get(f"{BASE_URL}/matches", headers=headers, timeout=10)
            if res_all.status_code == 200:
                matches = res_all.json().get("matches", [])

        # Priorizamos partidos por jugarse o en vivo primero, y luego terminados recientes
        estados_pendientes = {"SCHEDULED", "TIMED", "IN_PLAY", "PAUSED"}
        pendientes = [m for m in matches if m.get("status") in estados_pendientes]
        otros = [m for m in matches if m.get("status") not in estados_pendientes]
        ordenados = (pendientes + otros)[:35]

        for idx, m in enumerate(ordenados):
            comp = m.get("competition", {})
            comp_name = comp.get("name", "Competencia Oficial")
            comp_code = comp.get("code")
            ligas_set.add(comp_name)

            home = m.get("homeTeam", {})
            away = m.get("awayTeam", {})
            home_id = home.get("id")
            away_id = away.get("id")
            home_name = home.get("name", "Local")
            away_name = away.get("name", "Visitante")

            if comp_code and comp_code not in tablas_cache:
                tablas_cache[comp_code] = obtener_tabla_posiciones(comp_code, headers)

            tabla_liga = tablas_cache.get(comp_code, {})
            info_local = tabla_liga.get(home_id)
            info_visit = tabla_liga.get(away_id)

            stats_local = construir_stats_fallback(info_local)
            stats_visit = construir_stats_fallback(info_visit)

            analisis = calcular_analisis_combinada(
                home_name,
                away_name,
                comp_name,
                info_local.get("posicion") if info_local else None,
                info_visit.get("posicion") if info_visit else None,
                stats_local,
                stats_visit
            )

            estado_raw = m.get("status", "SCHEDULED")
            ya_jugado = estado_raw in ("FINISHED", "AWARDED")

            partidos_procesados.append({
                "id": idx,
                "liga": comp_name,
                "horario": formatear_horario_argentina(m.get("utcDate")),
                "fechaDia": formatear_fecha_corta(m.get("utcDate")),
                "estado": estado_raw,
                "yaJugado": ya_jugado,
                "homeId": home_id,
                "awayId": away_id,
                "equipoLocal": home_name,
                "equipoVisitante": away_name,
                "posicionLocal": info_local.get("posicion", "-") if info_local else "-",
                "puntosLocal": info_local.get("puntos", "-") if info_local else "-",
                "posicionVisitante": info_visit.get("posicion", "-") if info_visit else "-",
                "puntosVisitante": info_visit.get("puntos", "-") if info_visit else "-",
                "statsLocal": stats_local,
                "statsVisitante": stats_visit,
                "analisis": analisis
            })

    except Exception as e:
        print(f"Error general en /api/analizar-partidos: {e}")

    payload = {
        "status": "success",
        "total": len(partidos_procesados),
        "ligas": sorted(list(ligas_set)),
        "partidos": partidos_procesados
    }

    if partidos_procesados:
        CACHE["timestamp"] = ahora
        CACHE["data"] = payload

    return jsonify(payload)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
