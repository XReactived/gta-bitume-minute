"""Publie aussi chaque vidéo sur YouTube Shorts (100 % automatique, publique tout de suite).

Une vidéo verticale de moins de 3 minutes devient automatiquement un Short.

Relier la chaîne YouTube une seule fois :
    python -m gta_reels.youtube relier
(il faut YOUTUBE_CLIENT_ID et YOUTUBE_CLIENT_SECRET dans .env)
"""
import json
import os
import re
import secrets
import sys
import time
import urllib.parse

import requests

from .commun import DATA, config, db, env, log
from .notification import notifier

L = log("youtube")
JETON = DATA / "youtube_token.json"
RETOUR = "http://localhost:8765"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def actif() -> bool:
    return bool((config().get("youtube") or {}).get("actif")) and JETON.exists()


def _sauver(j: dict) -> None:
    ancien = json.loads(JETON.read_text(encoding="utf-8")) if JETON.exists() else {}
    j.setdefault("refresh_token", ancien.get("refresh_token"))
    j["expire_le"] = time.time() + int(j.get("expires_in", 0)) - 120
    JETON.write_text(json.dumps(j), encoding="utf-8")
    os.chmod(JETON, 0o600)


def jeton() -> str:
    j = json.loads(JETON.read_text(encoding="utf-8"))
    if time.time() < j.get("expire_le", 0):
        return j["access_token"]
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("YOUTUBE_CLIENT_ID"), "client_secret": env("YOUTUBE_CLIENT_SECRET"),
        "refresh_token": j["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Renouvellement YouTube refusé ({r.status_code}) : relie à nouveau la chaîne")
    _sauver(r.json())
    return json.loads(JETON.read_text(encoding="utf-8"))["access_token"]


def relier() -> None:
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": env("YOUTUBE_CLIENT_ID"), "redirect_uri": RETOUR, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": secrets.token_urlsafe(12)})
    print("\n1) Ouvre ce lien, choisis le compte Google de la chaîne YouTube et accepte "
          "(si Google affiche « application non validée » : Paramètres avancés > Continuer) :\n")
    print(url)
    print("\n2) Tu arrives sur une page qui ne s'affiche pas (localhost) : c'est normal. "
          "Copie l'adresse complète de cette page et colle-la ici.\n")
    retour = input("Adresse : ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(retour).query).get("code", [retour])[0]
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("YOUTUBE_CLIENT_ID"), "client_secret": env("YOUTUBE_CLIENT_SECRET"),
        "code": code, "redirect_uri": RETOUR, "grant_type": "authorization_code"}, timeout=30)
    if r.status_code != 200 or "refresh_token" not in r.json():
        raise SystemExit(f"Google a refusé le code ({r.status_code}). Recommence depuis l'étape 1.")
    _sauver(r.json())
    print("\nChaîne YouTube reliée. Les prochaines vidéos partiront aussi sur YouTube Shorts.")


def _titre(hook: str) -> str:
    t = re.sub(r"\s+", " ", hook or "").strip()
    t = t[:1].upper() + t[1:].lower() if t.isupper() else t  # pas de titre tout en majuscules
    return (t[:85] + " #Shorts").strip()


def publier(video: str, hook: str, legende: str) -> str:
    y = config().get("youtube") or {}
    tags = [m.lstrip("#") for m in re.findall(r"#\w+", legende or "")][:10]
    meta = {
        "snippet": {"title": _titre(hook), "description": (legende or "") + "\n\n" + y.get("description_fin", ""),
                    "tags": tags, "categoryId": "20", "defaultLanguage": "fr", "defaultAudioLanguage": "fr"},
        "status": {"privacyStatus": y.get("visibilite", "public"), "selfDeclaredMadeForKids": False},
    }
    taille = os.path.getsize(video)
    r = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        json=meta, timeout=60, headers={"Authorization": f"Bearer {jeton()}", "X-Upload-Content-Type": "video/mp4",
                                         "X-Upload-Content-Length": str(taille)})
    if r.status_code != 200:
        raise RuntimeError(f"YouTube refuse l'envoi ({r.status_code}) : {r.text[:300]}")
    with open(video, "rb") as f:
        r2 = requests.put(r.headers["Location"], data=f, timeout=900,
                          headers={"Content-Type": "video/mp4", "Content-Length": str(taille)})
    if r2.status_code not in (200, 201):
        raise RuntimeError(f"Envoi YouTube échoué ({r2.status_code}) : {r2.text[:300]}")
    return r2.json()["id"]


def publier_video(v) -> None:
    """Envoie une vidéo du stock sur YouTube Shorts. N'empêche jamais la publication TikTok."""
    if not actif() or ("youtube_id" in v.keys() and v["youtube_id"]):
        return  # YouTube pas relié, ou vidéo déjà envoyée sur YouTube
    conn = db()
    try:
        vid = publier(v["fichier"], v["hook"], v["legende"])
        conn.execute("UPDATE videos SET youtube_id=? WHERE id=?", (vid, v["id"]))
        conn.commit()
        L.info("Publiée sur YouTube Shorts : vidéo %d -> https://youtube.com/shorts/%s", v["id"], vid)
        notifier("Vidéo publiée sur YouTube Shorts", f"{v['hook']}\nhttps://youtube.com/shorts/{vid}", 3, ["tv"])
    except Exception as e:
        L.error("Échec YouTube vidéo %d : %s", v["id"], e)
        notifier("Échec d'envoi vers YouTube", f"« {v['hook']} » : {str(e)[:250]}", 3, ["warning"])


if __name__ == "__main__":
    if sys.argv[1:] == ["relier"]:
        relier()
    else:
        print(__doc__)
