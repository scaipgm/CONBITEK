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

# Caché en memoria (15 minutos) para respetar el límite de peticiones de la API gratuita
CACHE = {
    "timestamp": 0,
    "data": None
}
CACHE_TTL_SECONDS = 900


def formatear_horario_argentina(utc_str):
    if not utc_str:
        return "Horario por confirmar"
    try:
        dt_utc = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        dt_arg = dt_utc.astimezone(timezone(timedelta(hours=-3)))
        return dt_arg.strftime("%d/%m/%Y - %H:%M hs (ARG)")
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
                            posiciones[team_id] = {
                                "posicion": row.get("position"),
                                "puntos": row.get("points"),
                                "pj": row.get("playedGames"),
                                "gf": row.get("goalsFor"),
                                "gc": row.get("goalsAgainst")
                            }
    except Exception as e:
        print(f"Error tabla {competition_code}: {e}")
    return posiciones


def obtener_ultimos_5(team_id, headers):
    forma = []
    gf_total = 0
    gc_total = 0
    anoto_en = 0
    partidos_contados = 0

    if not team_id:
        return {
            "forma": [],
            "resumen": "Sin historial reciente",
            "prom_gf": 0.0,
            "prom_gc": 0.0,
            "pct_anota": 0
        }

    try:
        url = f"{BASE_URL}/teams/{team_id}/matches"
        params = {"status": "FINISHED", "limit": 5}
        res = requests.get(url, headers=headers, params=params, timeout=8)
        if res.status_code == 200:
            matches = res.json().get("matches", [])[-5:]
            for m in matches:
                score = m.get("score", {}).get("fullTime", {})
                home_g = score.get("home")
                away_g = score.get("away")
                if home_g is None or away_g is None:
                    continue

                partidos_contados += 1
                es_local = m.get("homeTeam", {}).get("id") == team_id
                g_favor = home_g if es_local else away_g
                g_contra = away_g if es_local else home_g

                gf_total += g_favor
                gc_total += g_contra
                if g_favor > 0:
                    anoto_en += 1

                if g_favor > g_contra:
                    forma.append("V")
                elif g_favor < g_contra:
                    forma.append("D")
                else:
                    forma.append("E")
    except Exception as e:
        print(f"Error últimos 5 equipo {team_id}: {e}")

    if partidos_contados == 0:
        return {
            "forma": [],
            "resumen": "Sin datos recientes",
            "prom_gf": 1.2,
            "prom_gc": 1.1,
            "pct_anota": 60
        }

    v_count = forma.count("V")
    e_count = forma.count("E")
    d_count = forma.count("D")

    return {
        "forma": forma,
        "resumen": f"{v_count}V - {e_count}E - {d_count}D (últ. {partidos_contados})",
        "prom_gf": round(gf_total / partidos_contados, 2),
        "prom_gc": round(gc_total / partidos_contados, 2),
        "pct_anota": int(round((anoto_en / partidos_contados) * 100))
    }


def calcular_analisis_combinada(home_name, away_name, liga_name, pos_local, pos_visit, stats_local, stats_visit):
    prom_goles_partido = round(stats_local["prom_gf"] + stats_visit["prom_gf"], 2)
    if prom_goles_partido == 0:
        prom_goles_partido = round((stats_local["prom_gf"] + stats_visit["prom_gc"] + stats_visit["prom_gf"] + stats_local["prom_gc"]) / 2, 2)

    btts_score = int(round((stats_local["pct_anota"] + stats_visit["pct_anota"]) / 2))
    ambos_anotan = "SÍ (Alta tendencia)" if btts_score >= 65 and stats_local["prom_gc"] >= 0.8 and stats_visit["prom_gc"] >= 0.8 else "Moderado / Revisar línea"

    # Proyección de córners, amarillas y fueras de juego en función del ritmo de goles y tabla
    factor_ritmo = max(0.0, prom_goles_partido - 2.0)
    corners_est = round(8.5 + min(3.0, factor_ritmo * 1.1), 1)
    amarillas_est = round(4.0 + (0.6 if (pos_local and pos_visit and abs(pos_local - pos_visit) <= 3) else 0.0) + (0.4 if btts_score >= 70 else 0.0), 1)
    offsides_est = round(3.2 + min(2.0, factor_ritmo * 0.7), 1)

    if prom_goles_partido >= 2.8 and btts_score >= 65:
        pick_sugerido = "Ambos Anotan (Sí) o Más de 2.0 Goles"
        cuota_est = 1.92
    elif prom_goles_partido <= 2.0:
        pick_sugerido = "Menos de 3.0 Goles + Doble Oportunidad"
        cuota_est = 1.78
    else:
        pick_sugerido = f"Más de 1.5 Goles + Más de {corners_est} Córners"
        cuota_est = 1.85

    pos_texto = ""
    if pos_local and pos_visit:
        pos_texto = f"En tabla: {home_name} ({pos_local}°) vs {away_name} ({pos_visit}°). "

    diagnostico = (
        f"{pos_texto}{home_name} promedia {stats_local['prom_gf']} GF / {stats_local['prom_gc']} GC "
        f"({stats_local['resumen']}) y {away_name} {stats_visit['prom_gf']} GF / {stats_visit['prom_gc']} GC "
        f"({stats_visit['resumen']}). Estimación de ritmo para {liga_name}: {prom_goles_partido} goles esperados."
    )

    confianza = min(92, max(68, int(round(65 + (btts_score * 0.22)))))

    return {
        "pickSugerido": pick_sugerido,
        "cuotaEstimada": cuota_est,
        "confianza": confianza,
        "ambosAnotan": f"{ambos_anotan} ({btts_score}%)",
        "golesPromedio": f"{prom_goles_partido} goles/partido",
        "cornersPromedio": f"Más de {corners_est} córners",
        "amarillasPromedio": f"~{amarillas_est} amarillas",
        "offsidesPromedio": f"~{offsides_est} fueras de juego",
        "diagnostico": diagnostico
    }


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/analizar-partidos", methods=["GET"])
def analizar_partidos():
    ahora = time.time()
    if CACHE["data"] is not None and (ahora - CACHE["timestamp"]) < CACHE_TTL_SECONDS:
        return jsonify(CACHE["data"])

    headers = {"X-Auth-Token": API_KEY}
    partidos_procesados = []
    tablas_cache = {}
    rachas_cache = {}

    try:
        hoy = datetime.now(timezone(timedelta(hours=-3))).strftime("%Y-%m-%d")
        hasta = (datetime.now(timezone(timedelta(hours=-3))) + timedelta(days=3)).strftime("%Y-%m-%d")

        res = requests.get(
            f"{BASE_URL}/matches",
            headers=headers,
            params={"dateFrom": hoy, "dateTo": hasta},
            timeout=10
        )

        matches = []
        if res.status_code == 200:
            matches = res.json().get("matches", [])

        # Si no hay partidos en la ventana inmediata, consultamos los próximos disponibles en la API
        if not matches:
            res_all = requests.get(f"{BASE_URL}/matches", headers=headers, timeout=10)
            if res_all.status_code == 200:
                matches = res_all.json().get("matches", [])

        for idx, m in enumerate(matches[:8]):
            comp = m.get("competition", {})
            comp_name = comp.get("name", "Competencia Oficial")
            comp_code = comp.get("code")

            home = m.get("homeTeam", {})
            away = m.get("awayTeam", {})
            home_id = home.get("id")
            away_id = away.get("id")
            home_name = home.get("name", "Local")
            away_name = away.get("name", "Visitante")

            if comp_code and comp_code not in tablas_cache:
                tablas_cache[comp_code] = obtener_tabla_posiciones(comp_code, headers)

            tabla_liga = tablas_cache.get(comp_code, {})
            info_local = tabla_liga.get(home_id, {})
            info_visit = tabla_liga.get(away_id, {})

            if home_id not in rachas_cache:
                rachas_cache[home_id] = obtener_ultimos_5(home_id, headers)
            if away_id not in rachas_cache:
                rachas_cache[away_id] = obtener_ultimos_5(away_id, headers)

            stats_local = rachas_cache[home_id]
            stats_visit = rachas_cache[away_id]

            analisis = calcular_analisis_combinada(
                home_name,
                away_name,
                comp_name,
                info_local.get("posicion"),
                info_visit.get("posicion"),
                stats_local,
                stats_visit
            )

            partidos_procesados.append({
                "id": idx,
                "liga": comp_name,
                "horario": formatear_horario_argentina(m.get("utcDate")),
                "estado": m.get("status", "SCHEDULED"),
                "equipoLocal": home_name,
                "equipoVisitante": away_name,
                "posicionLocal": info_local.get("posicion", "-"),
                "puntosLocal": info_local.get("puntos", "-"),
                "posicionVisitante": info_visit.get("posicion", "-"),
                "puntosVisitante": info_visit.get("puntos", "-"),
                "statsLocal": stats_local,
                "statsVisitante": stats_visit,
                "analisis": analisis
            })

    except Exception as e:
        print(f"Error general en /api/analizar-partidos: {e}")

    payload = {
        "status": "success",
        "total": len(partidos_procesados),
        "partidos": partidos_procesados
    }

    if partidos_procesados:
        CACHE["timestamp"] = ahora
        CACHE["data"] = payload

    return jsonify(payload)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
