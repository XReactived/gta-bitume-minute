"""Étape unique : relier ton compte TikTok au pipeline.

Lance :  python -m gta_reels.tiktok_auth
"""
import secrets
import urllib.parse

from .agent_tiktok import echanger_code
from .commun import config, env


def main() -> None:
    scopes = "user.info.basic,video.upload" + (",video.publish" if config()["tiktok"]["mode"] == "direct" else "")
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": env("TIKTOK_CLIENT_KEY"), "scope": scopes, "response_type": "code",
        "redirect_uri": env("TIKTOK_REDIRECT_URI"), "state": secrets.token_urlsafe(12),
    })
    print("\n1) Ouvre ce lien, connecte-toi au compte TikTok du projet et accepte :\n")
    print(url)
    print("\n2) Tu arrives sur une page (elle peut être vide). Copie l'adresse complète de la page et colle-la ici.\n")
    retour = input("Adresse : ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(retour).query).get("code", [retour])[0]
    echanger_code(code)
    print("\nCompte TikTok relié. Le pipeline peut publier.")


if __name__ == "__main__":
    main()
