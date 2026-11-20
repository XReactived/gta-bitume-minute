"""Rédaction par Claude : hook, voix-off (intro + annonces), légende et hashtags."""
import json

import anthropic

from .commun import config, db, log

L = log("redaction")


def _schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "hook": {"type": "string", "description": "Titre affiché à l'écran au début, 2 à 6 mots, MAJUSCULES, sans hashtag"},
            "intro": {"type": "string", "description": "Phrase lue par la voix-off au début, 8 à 14 mots, qui annonce le top"},
            "annonces": {
                "type": "array", "items": {"type": "string"},
                "description": f"Exactement {n} phrases lues par la voix-off avant chaque clip, dans l'ordre donné, "
                               "6 à 12 mots, qui commencent par le numéro (« Numéro 3 : … ») et nomment le streamer",
            },
            "legende": {"type": "string", "description": "Légende TikTok courte (max 150 caractères)"},
            "hashtags": {"type": "array", "items": {"type": "string"}, "description": "3 à 5 hashtags pertinents, commençant par #"},
        },
        "required": ["hook", "intro", "annonces", "legende", "hashtags"],
        "additionalProperties": False,
    }


CONSIGNE = """Tu écris pour une page fan TikTok française « Bitume Minute » (non affiliée à Rockstar Games) qui publie des tops d'1 min 10 des meilleurs moments de streamers sur GTA 6, présentés par une voix-off.
Règles :
- Le top est un compte à rebours : le dernier clip est le numéro 1, le meilleur.
- Voix-off : phrases courtes, naturelles à l'oral, ton complice et drôle, pas d'emoji, pas de hashtag, pas d'abréviation.
- Hook : dit en 2 à 6 mots pourquoi rester jusqu'au bout. Aucun spoiler de l'histoire.
- Légende : une phrase courte, 1 emoji maximum. Hashtags : 3 à 5, pertinents.
- Ne promets rien que les clips ne montrent pas. Jamais « officiel », « confirmé », « exclu » : on n'est pas Rockstar.
- Jamais de leak, de rumeur, d'astuce pour jouer avant la sortie ou de triche.
- Ne réutilise pas une légende ou un hook déjà publié récemment (liste fournie)."""


def _par_defaut(clips: list[dict]) -> dict:
    n = len(clips)
    return {
        "hook": "LE TOP GTA 6 DU JOUR",
        "intro": "Voici le top des moments les plus fous sur GTA 6 aujourd'hui.",
        "annonces": [f"Numéro {n - i} : {c['streamer']}." for i, c in enumerate(clips)],
        "legende": "Le top GTA 6 du jour",
        "hashtags": [],
    }


def rediger(clips: list[dict]) -> dict:
    """clips : dans l'ordre d'affichage, chacun {streamer, login, titre, transcription}."""
    t = config()["tiktok"]
    n = len(clips)
    recents = "\n".join(
        f"- {r['hook']} / {r['legende'].splitlines()[0]}"
        for r in db().execute(
            "SELECT hook, legende FROM videos WHERE creee_le > datetime('now', '-7 days') ORDER BY id DESC LIMIT 40"
        )
    )
    details = "\n\n".join(
        f"Clip affiché en position {i + 1} (numéro {n - i} du top) — streamer : {c['streamer']}\n"
        f"Titre Twitch : {c['titre']}\nCe qui est dit : {(c['transcription'] or '(pas de parole)')[:1200]}"
        for i, c in enumerate(clips)
    )
    prompt = (f"{details}\n\nDéjà publié cette semaine (ne pas répéter) :\n{recents or '(rien)'}\n\n"
              "Écris le hook, l'intro, les annonces, la légende et les hashtags.")
    try:
        rep = anthropic.Anthropic().beta.messages.create(
            model=t.get("modele_ia", "claude-opus-5-5"),
            max_tokens=3000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=CONSIGNE,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema(n)}},
            messages=[{"role": "user", "content": prompt}],
        )
        if rep.stop_reason == "refusal":
            raise RuntimeError("refus du modèle")
        out = json.loads(next(b.text for b in rep.content if b.type == "text"))
        if len(out["annonces"]) != n:  # on complète ou on coupe plutôt que tout jeter
            defaut = _par_defaut(clips)["annonces"]
            out["annonces"] = (out["annonces"] + defaut[len(out["annonces"]):])[:n]
    except Exception as e:
        L.warning("Rédaction IA indisponible (%s), texte par défaut utilisé", e)
        out = _par_defaut(clips)

    tags = list(dict.fromkeys(t.get("hashtags_fixes", []) + out["hashtags"]))
    credits = " ".join(f"twitch.tv/{c['login']}" for c in {c["login"]: c for c in clips}.values())
    out["legende_complete"] = f"{out['legende']}\n\n🎥 Clips : {credits}\n{' '.join(tags)}"[:2200]
    return out
