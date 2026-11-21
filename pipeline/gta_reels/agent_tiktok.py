"""Agent 3 : publie la meilleure vidéo montée sur TikTok (Content Posting API)."""
import csv
import datetime as dt
import json
import os
import time
from zoneinfo import ZoneInfo

import requests

from .commun import DATA, config, db, env, log
from .notification import notifier
from .youtube import publier_video as publier_youtube

L = log("tiktok")
API = "https://open.tiktokapis.com/v2"
JETON = DATA / "tiktok_token.json"
DERNIER_STATUT = None


def _sauver(j: dict) -> None:
    j["expire_le"] = time.time() + int(j.get("expires_in", 0)) - 120
    JETON.write_text(json.dumps(j), encoding="utf-8")
    os.chmod(JETON, 0o600)


def echanger_code(code: str) -> None:
    r = requests.post(f"{API}/oauth/token/", data={
        "client_key": env("TIKTOK_CLIENT_KEY"), "client_secret": env("TIKTOK_CLIENT_SECRET"),
        "code": code, "grant_type": "authorization_code", "redirect_uri": env("TIKTOK_REDIRECT_URI"),
    }, timeout=30)
    r.raise_for_status()
    j = r.json()
    if "access_token" not in j:
        raise RuntimeError(f"TikTok a refusé le code : {j}")
    _sauver(j)


def jeton() -> str:
    if not JETON.exists():
        raise RuntimeError("Compte TikTok non relié : lance d'abord  python -m gta_reels.tiktok_auth")
    j = json.loads(JETON.read_text(encoding="utf-8"))
    if time.time() < j.get("expire_le", 0):
        return j["access_token"]
    r = requests.post(f"{API}/oauth/token/", data={
        "client_key": env("TIKTOK_CLIENT_KEY"), "client_secret": env("TIKTOK_CLIENT_SECRET"),
        "grant_type": "refresh_token", "refresh_token": j["refresh_token"],
    }, timeout=30)
    r.raise_for_status()
    nouveau = r.json()
    if "access_token" not in nouveau:
        raise RuntimeError(f"Renouvellement TikTok refusé, relie à nouveau le compte : {nouveau}")
    _sauver(nouveau)
    return nouveau["access_token"]


def _post(chemin: str, corps: dict) -> dict:
    r = requests.post(
        f"{API}{chemin}", json=corps, timeout=60,
        headers={"Authorization": f"Bearer {jeton()}", "Content-Type": "application/json; charset=UTF-8"},
    )
    j = r.json()
    if r.status_code != 200 or j.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok {chemin} : {j.get('error')}")
    return j["data"]


def publier(video: str, legende: str) -> str:
    t = config()["tiktok"]
    taille = os.path.getsize(video)
    source = {"source": "FILE_UPLOAD", "video_size": taille, "chunk_size": taille, "total_chunk_count": 1}

    if t["mode"] == "direct":
        info = _post("/post/publish/creator_info/query/", {})
        options = info.get("privacy_level_options", [])
        vis = t["visibilite"] if t["visibilite"] in options else "SELF_ONLY"
        if vis != t["visibilite"]:
            L.warning("Visibilité %s non autorisée (app pas encore auditée ?) -> %s", t["visibilite"], vis)
        data = _post("/post/publish/video/init/", {
            "post_info": {"title": legende, "privacy_level": vis, "disable_duet": False,
                          "disable_comment": False, "disable_stitch": False, "video_cover_timestamp_ms": 1000},
            "source_info": source,
        })
    else:
        data = _post("/post/publish/inbox/video/init/", {"source_info": source})

    with open(video, "rb") as f:
        r = requests.put(data["upload_url"], data=f, timeout=600, headers={
            "Content-Type": "video/mp4", "Content-Length": str(taille),
            "Content-Range": f"bytes 0-{taille - 1}/{taille}",
        })
    r.raise_for_status()

    global DERNIER_STATUT
    DERNIER_STATUT = None
    for _ in range(60):  # TikTok traite la vidéo de son côté (jusqu'à 10 min)
        time.sleep(10)
        etat = _post("/post/publish/status/fetch/", {"publish_id": data["publish_id"]})
        s = etat.get("status")
        DERNIER_STATUT = s
        if s in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            break
        if s == "FAILED":
            raise RuntimeError(f"TikTok a rejeté la vidéo : {etat.get('fail_reason')}")
    return data["publish_id"]


def noter_publication(conn, v, pid: str, mode: str) -> None:
    """Journal des publications (data/publications.csv), lisible dans Excel."""
    fichier = DATA / "publications.csv"
    neuf = not fichier.exists()
    ids = v["clips"].split(",")
    streamers = [r["streamer"] for r in conn.execute(
        f"SELECT streamer FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids)]
    with open(fichier, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if neuf:
            w.writerow(["date", "heure", "video", "mode", "streamers", "hook", "legende", "publish_id"])
        now = dt.datetime.now()
        w.writerow([now.date(), now.strftime("%H:%M"), v["id"], mode, ", ".join(streamers),
                    v["hook"], v["legende"].replace("\n", " "), pid])


def choisir(conn):
    """Une série en cours passe en priorité (partie suivante), sinon la meilleure vidéo prête."""
    suite = conn.execute(
        "SELECT v.* FROM videos v WHERE v.statut='prete' AND v.serie IS NOT NULL AND v.partie > 1 "
        "AND EXISTS (SELECT 1 FROM videos p WHERE p.serie=v.serie AND p.partie=v.partie-1 AND p.statut='publiee') "
        "ORDER BY v.partie LIMIT 1").fetchone()
    if suite:
        return suite
    # une série ne démarre que si toutes ses parties sont déjà montées : la suite arrive au créneau suivant
    # et on évite de publier deux fois de suite le même format
    dernier = conn.execute("SELECT format FROM videos WHERE statut='publiee' ORDER BY publiee_le DESC LIMIT 1").fetchone()
    return conn.execute(
        "SELECT v.* FROM videos v WHERE v.statut='prete' AND (v.serie IS NULL OR (v.partie=1 AND NOT EXISTS ("
        "SELECT 1 FROM videos p WHERE p.serie=v.serie AND p.statut!='prete') AND (SELECT COUNT(*) FROM videos p "
        "WHERE p.serie=v.serie)=v.parties)) ORDER BY (COALESCE(v.format,'top')=?) ASC, v.score DESC LIMIT 1",
        ((dernier["format"] or "top") if dernier else "",)).fetchone()


def lancer() -> int | None:
    conn = db()
    v = choisir(conn)
    if not v:
        L.warning("Aucune vidéo prête pour ce créneau")
        return None
    try:
        pid = publier(v["fichier"], v["legende"])
        conn.execute("UPDATE videos SET statut='publiee', publish_id=?, publiee_le=? WHERE id=?",
                     (pid, dt.datetime.now().isoformat(timespec="seconds"), v["id"]))
        mode = config()["tiktok"]["mode"]
        noter_publication(conn, v, pid, mode)
        L.info("%s : vidéo %d (%s)", "Publiée" if mode == "direct" else "Envoyée dans tes brouillons TikTok",
               v["id"], v["hook"])
        if mode == "direct":
            notifier("Vidéo publiée sur TikTok", v["hook"], 3, ["tada"])
        else:
            notifier("Nouvelle vidéo dans tes brouillons TikTok" if DERNIER_STATUT == "SEND_TO_USER_INBOX"
                     else "Vidéo envoyée, TikTok la traite encore (quelques minutes)",
                     (f"EDIT : ajoute un son tendance ({(v['conseil'] or '').split('] ', 1)[-1] or 'phonk ou son du moment'}) et mets le son "
                      "d'origine à 0. Les coupes sont calées sur ce tempo : fais glisser le son pour que son premier "
                      "gros temps (le drop) tombe pile au début de la vidéo.\n\n" if v["format"] == "edit" else "")
                     + f"Copie la légende ci-dessous, colle-la dans TikTok, puis publie :\n\n{v['legende']}",
                     4, ["clapper"])
        conn.commit()  # avant YouTube, qui écrit dans la base avec sa propre connexion
        publier_youtube(v)
    except Exception as e:
        if "spam_risk" in str(e) or "too_many_pending" in str(e):
            # trop de brouillons pas encore publiés : la vidéo reste en stock et repartira au prochain créneau
            # TikTok accepte 5 envois en attente par 24 h : supprimer un brouillon ne libère pas la place,
            # elle se libère 24 h après l'envoi. On calcule l'heure où ça se débloque.
            envois = [r[0] for r in conn.execute(
                "SELECT publiee_le FROM videos WHERE publiee_le >= ? ORDER BY publiee_le",
                ((dt.datetime.now() - dt.timedelta(hours=24)).isoformat(timespec="seconds"),))]
            libre = (f" La place se libère vers {(dt.datetime.fromisoformat(envois[0]) + dt.timedelta(hours=24)).astimezone(ZoneInfo('Europe/Paris')):%H:%M} "
                     "(24 h après le plus ancien envoi)." if envois else "")
            L.error("TikTok refuse : trop d'envois en attente (5 par 24 h)%s", libre)
            notifier("Brouillons TikTok pleins",
                     "TikTok n'accepte que 5 vidéos en attente par 24 h, et supprimer un brouillon ne libère pas la "
                     f"place.{libre} La vidéo reste en stock et repartira à un prochain créneau.", 5, ["warning"])
            conn.commit()
            publier_youtube(v)  # YouTube n'a pas cette limite : la vidéo y part quand même
            return v["id"]
        conn.execute("UPDATE videos SET statut='erreur', erreur=? WHERE id=?", (str(e)[:500], v["id"]))
        L.error("Échec de publication vidéo %d : %s", v["id"], e)
        notifier("Échec d'envoi vers TikTok", f"La vidéo « {v['hook']} » n'a pas pu partir : {str(e)[:300]}", 4,
                 ["warning"])
        conn.commit()
        publier_youtube(v)
    conn.commit()
    return v["id"]


if __name__ == "__main__":
    lancer()
