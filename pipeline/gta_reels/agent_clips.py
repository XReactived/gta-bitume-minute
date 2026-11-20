"""Agent 1 : trouve les meilleurs clips Twitch récents et les télécharge."""
import datetime as dt
import subprocess

import requests

from .commun import DATA, config, db, env, log

L = log("clips")
HELIX = "https://api.twitch.tv/helix"


class Twitch:
    def __init__(self):
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": env("TWITCH_CLIENT_ID"),
                "client_secret": env("TWITCH_CLIENT_SECRET"),
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        r.raise_for_status()
        self.h = {"Client-Id": env("TWITCH_CLIENT_ID"), "Authorization": f"Bearer {r.json()['access_token']}"}

    def get(self, chemin: str, **params) -> list[dict]:
        r = requests.get(f"{HELIX}/{chemin}", headers=self.h, params=params, timeout=30)
        r.raise_for_status()
        return r.json().get("data", [])


def _score(clip: dict) -> float:
    """Vues pondérées par la fraîcheur : un clip récent et viral passe devant."""
    cree = dt.datetime.fromisoformat(clip["created_at"].replace("Z", "+00:00"))
    heures = (dt.datetime.now(dt.timezone.utc) - cree).total_seconds() / 3600
    return clip["view_count"] / (heures + 2) ** 1.5


def chercher() -> list[dict]:
    c = config()["clips"]
    tw = Twitch()
    depuis = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=c["fenetre_heures"])).strftime("%Y-%m-%dT%H:%M:%SZ")

    jeux = tw.get("games", name="Grand Theft Auto VI")
    gta_id = jeux[0]["id"] if jeux else None
    if not gta_id:
        L.warning("Catégorie 'Grand Theft Auto VI' introuvable sur Twitch")

    clips: dict[str, dict] = {}
    logins = c.get("streamers") or []
    users = tw.get("users", login=logins) if logins else []
    trouves = {u["login"] for u in users}
    for manquant in set(logins) - trouves:
        L.warning("Streamer introuvable sur Twitch : %s", manquant)

    for u in users:
        for clip in tw.get("clips", broadcaster_id=u["id"], started_at=depuis, first=50):
            if c["seulement_gta6"] and clip.get("game_id") != gta_id:
                continue
            clip["_login"] = u["login"]
            clips[clip["id"]] = clip

    if c.get("categorie_gta6_fr") and gta_id:
        for clip in tw.get("clips", game_id=gta_id, started_at=depuis, first=100):
            if clip.get("language") == c["langue"]:
                clip["_login"] = clip["broadcaster_name"].lower()
                clips.setdefault(clip["id"], clip)

    exclus = {e.lower() for e in c.get("exclus") or []}
    retenus = [x for x in clips.values()
               if x["view_count"] >= c["vues_minimum"]
               and x["_login"] not in exclus and x["broadcaster_name"].lower() not in exclus]
    retenus.sort(key=_score, reverse=True)
    L.info("%d clips trouvés, %d au-dessus du seuil de vues", len(clips), len(retenus))
    return retenus


def telecharger(url: str, clip_id: str) -> str:
    sortie = DATA / "brut" / f"{clip_id}.mp4"
    subprocess.run(
        ["yt-dlp", "-q", "--no-warnings", "-f", "best[ext=mp4]/best", "-o", str(sortie), url],
        check=True,
        timeout=300,
    )
    return str(sortie)


def lancer() -> int:
    c = config()["clips"]
    conn = db()
    nouveaux = 0
    for clip in chercher():
        if nouveaux >= c["max_par_passage"]:
            break
        if conn.execute("SELECT 1 FROM clips WHERE id=?", (clip["id"],)).fetchone():
            continue
        conn.execute(
            "INSERT INTO clips (id, url, streamer, streamer_login, titre, vues, cree_le, duree, score) VALUES (?,?,?,?,?,?,?,?,?)",
            (clip["id"], clip["url"], clip["broadcaster_name"], clip["_login"], clip["title"],
             clip["view_count"], clip["created_at"], clip["duration"], _score(clip)),
        )
        try:
            chemin = telecharger(clip["url"], clip["id"])
            conn.execute("UPDATE clips SET statut='telecharge', fichier_brut=? WHERE id=?", (chemin, clip["id"]))
            L.info("Téléchargé : %s (%s, %d vues)", clip["title"], clip["broadcaster_name"], clip["view_count"])
            nouveaux += 1
        except Exception as e:  # un clip cassé ne bloque pas les autres
            conn.execute("UPDATE clips SET statut='erreur', erreur=? WHERE id=?", (str(e)[:500], clip["id"]))
            L.error("Échec du téléchargement %s : %s", clip["url"], e)
        conn.commit()
    return nouveaux


if __name__ == "__main__":
    lancer()
