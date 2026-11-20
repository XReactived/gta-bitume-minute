"""Claude, rédacteur en chef : choisit le format et la durée de chaque vidéo, puis écrit les textes."""
import json

import anthropic

from .commun import config, db, log

L = log("redaction")

CHARTE = """Tu es le rédacteur en chef de « Bitume Minute », une page fan TikTok française (non affiliée à Rockstar Games) qui publie les meilleurs moments de streamers sur GTA 6, présentés par une voix-off.
Règles permanentes :
- Ne promets rien que les clips ne montrent pas. Jamais « officiel », « confirmé », « exclu » : on n'est pas Rockstar.
- Jamais de leak, de rumeur, d'astuce pour jouer avant la sortie ou de triche. Aucun spoiler de l'histoire dans un titre.
- Voix-off : phrases courtes, naturelles à l'oral, ton complice et drôle, sans emoji, hashtag ni abréviation."""


def _appeler(consigne: str, prompt: str, schema: dict, max_tokens: int = 4000) -> dict:
    t = config()["tiktok"]
    rep = anthropic.Anthropic().beta.messages.create(
        model=t.get("modele_ia", "claude-opus-5-5"),
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=CHARTE + "\n\n" + consigne,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    if rep.stop_reason == "refusal":
        raise RuntimeError("refus du modèle")
    return json.loads(next(b.text for b in rep.content if b.type == "text"))


# ---------------------------------------------------------------- plan éditorial

SCHEMA_PLAN = {
    "type": "object",
    "properties": {
        "videos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["solo", "top", "serie"]},
                    "parties": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                        "description": "Ids des clips de chaque partie, dans l'ordre d'affichage. Une seule partie sauf pour une série.",
                    },
                    "raison": {"type": "string"},
                },
                "required": ["format", "parties", "raison"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["videos"],
    "additionalProperties": False,
}

CONSIGNE_PLAN = """Ton travail : décider quelles vidéos fabriquer avec les clips disponibles. Choisis le format qui sert le mieux chaque moment :
- « solo » : un seul clip, quand il se suffit à lui-même (un moment énorme, une histoire complète). Durée = celle du clip.
- « top » : 2 à 5 clips sur un même thème (accidents, braquages, fous rires…), en compte à rebours, le meilleur en dernier.
- « serie » : 2 ou 3 parties publiées à la suite, quand plusieurs clips racontent une même histoire ou une même session d'un streamer et que tout mettre dans une vidéo serait trop long. Chaque partie doit tenir seule et donner envie de voir la suivante.
Durée : adapte-la au contenu, pas l'inverse. Respecte les bornes de durée données. Pour la monétisation TikTok, les vidéos de plus d'1 minute sont préférables : ne fais du court que si le moment le mérite vraiment.
Variété (très important) : ne fais pas toujours le même type de vidéo. Regarde la liste des dernières publications fournie et change de format, de thème et de streamer par rapport à elles. Dans un top, mélange plusieurs streamers dès que possible. Évite de mettre le même streamer en vedette deux fois de suite.
Suspense : construis de l'attente. Ordonne les clips pour faire monter la tension, garde le moment le plus fort pour la fin, et coupe les séries sur un suspense (un « cliffhanger ») qui donne envie de voir la partie suivante.
Tu n'es pas obligé d'utiliser tous les clips : garde pour plus tard ceux qui iront mieux avec de futurs clips, ou ignore les clips faibles. N'utilise jamais un clip deux fois. Utilise uniquement les ids fournis."""


def planifier(clips: list[dict], dmin: float, dmax: float) -> list[dict]:
    """clips : {id, streamer, titre, transcription, duree, score}. Renvoie [{format, parties: [[ids]]}]."""
    liste = "\n\n".join(
        f"id={c['id']} — {c['streamer']} — {c['duree']:.0f} s — score {c['score']:.1f}\n"
        f"Titre : {c['titre']}\nCe qui est dit : {(c['transcription'] or '(pas de parole)')[:600]}"
        for c in clips
    )
    dernieres = "\n".join(
        f"- {r['format'] or 'top'} — {r['hook']} — {r['streamers']}"
        for r in db().execute(
            "SELECT v.format, v.hook, (SELECT group_concat(DISTINCT c.streamer) FROM clips c "
            "WHERE instr(',' || v.clips || ',', ',' || c.id || ',') > 0) AS streamers "
            "FROM videos v ORDER BY v.id DESC LIMIT 10")
    )
    prompt = (f"Dernières vidéos fabriquées (de la plus récente à la plus ancienne) :\n{dernieres or '(aucune)'}\n\n"
              f"Bornes de durée par vidéo (ou par partie) : {dmin:.0f} s à {dmax:.0f} s, écrans de voix-off compris "
              f"(compte environ 3 s d'intro et 2,5 s d'annonce par clip).\n\nClips disponibles :\n\n{liste}\n\n"
              "Propose le plan des vidéos à fabriquer maintenant.")
    plan = _appeler(CONSIGNE_PLAN, prompt, SCHEMA_PLAN)["videos"]

    connus = {c["id"]: c for c in clips}
    utilises, propre = set(), []
    for v in plan:  # on ne garde que des plans valides
        parties = []
        for p in v["parties"]:
            ids = []
            for i in p:
                if i in connus and i not in utilises and i not in ids:
                    ids.append(i)
            if ids:
                parties.append(ids)
        if not parties:
            continue
        fmt = v["format"] if v["format"] in ("solo", "top", "serie") else "top"
        if fmt == "serie" and len(parties) < 2:
            fmt = "top" if len(parties[0]) > 1 else "solo"
        if fmt != "serie" and len(parties) > 1:
            parties = [[i for p in parties for i in p]]
        if fmt == "solo" and len(parties[0]) > 1:
            fmt = "top"
        for p in parties:
            utilises.update(p)
        propre.append({"format": fmt, "parties": parties, "raison": v.get("raison", "")})
        L.info("Plan : %s en %d partie(s) — %s", fmt, len(parties), v.get("raison", ""))
    return propre


def plan_par_defaut(clips: list[dict], dmin: float, dmax: float) -> list[dict]:
    """Si Claude est indisponible : des tops simples qui tiennent dans les bornes."""
    plan, courant, total = [], [], 3.0
    for c in clips:
        if courant and total + 2.5 + c["duree"] > dmax:
            plan.append({"format": "top" if len(courant) > 1 else "solo", "parties": [[x["id"] for x in courant]]})
            courant, total = [], 3.0
        courant.append(c)
        total += 2.5 + c["duree"]
        if total >= max(dmin, 61):
            plan.append({"format": "top" if len(courant) > 1 else "solo", "parties": [[x["id"] for x in courant]]})
            courant, total = [], 3.0
    return plan


# ---------------------------------------------------------------- textes d'une vidéo

SCHEMA_TEXTES = {
    "type": "object",
    "properties": {
        "hook": {"type": "string", "description": "Titre à l'écran au début, 2 à 6 mots, MAJUSCULES, sans hashtag"},
        "intro": {"type": "string", "description": "Phrase de voix-off au début, 6 à 14 mots"},
        "annonces": {"type": "array", "items": {"type": "string"},
                     "description": "Une phrase de voix-off par clip, dans l'ordre, 5 à 12 mots, qui nomme le streamer"},
        "suite": {"type": "string", "description": "Phrase de fin de partie qui donne envie de voir la suivante (vide si pas une série ou dernière partie)"},
        "legende": {"type": "string", "description": "Légende TikTok courte (max 150 caractères), 1 emoji maximum"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "description": "3 à 5 hashtags pertinents, commençant par #"},
    },
    "required": ["hook", "intro", "annonces", "suite", "legende", "hashtags"],
    "additionalProperties": False,
}

CONSIGNE_TEXTES = """Ton travail : écrire les textes d'une vidéo dont le format est déjà décidé.
- top : compte à rebours ; chaque annonce commence par « Numéro N : » (le dernier clip est le numéro 1).
- solo : une seule annonce courte, ou une phrase qui lance le clip.
- serie : le hook et la légende indiquent la partie (ex. « PARTIE 1/3 »). Les annonces enchaînent l'histoire. « suite » annonce la partie suivante, sauf pour la dernière partie (alors vide).
Suspense : le hook et l'intro créent de l'attente sans rien révéler (« attends la fin », « personne ne s'attendait à ça », une question…). Varie les formulations : jamais deux fois la même accroche.
Ne réutilise pas un hook ou une légende déjà publiés récemment (liste fournie)."""


def _textes_par_defaut(clips, fmt, partie, parties) -> dict:
    n = len(clips)
    hook = {"top": "LE TOP GTA 6 DU JOUR", "solo": "REGARDE ÇA JUSQU'AU BOUT"}.get(fmt, f"PARTIE {partie}/{parties}")
    annonces = ([f"Numéro {n - i} : {c['streamer']}." for i, c in enumerate(clips)] if fmt == "top"
                else [f"{c['streamer']} sur GTA 6." for c in clips])
    return {"hook": hook, "intro": "Voici un moment fou sur GTA 6.", "annonces": annonces,
            "suite": f"La suite dans la partie {partie + 1}." if fmt == "serie" and partie < parties else "",
            "legende": "GTA 6", "hashtags": []}


def rediger(clips: list[dict], fmt: str = "top", partie: int = 1, parties: int = 1) -> dict:
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
        f"Clip {i + 1}/{n} — streamer : {c['streamer']}\nTitre Twitch : {c['titre']}\n"
        f"Ce qui est dit : {(c['transcription'] or '(pas de parole)')[:1200]}"
        for i, c in enumerate(clips)
    )
    entete = f"Format : {fmt}" + (f", partie {partie} sur {parties}" if fmt == "serie" else "")
    prompt = (f"{entete}\n\n{details}\n\nDéjà publié cette semaine (ne pas répéter) :\n{recents or '(rien)'}\n\n"
              f"Écris les textes ({n} annonce(s)).")
    defaut = _textes_par_defaut(clips, fmt, partie, parties)
    try:
        out = _appeler(CONSIGNE_TEXTES, prompt, SCHEMA_TEXTES, 3000)
    except Exception as e:
        L.warning("Rédaction IA indisponible (%s), texte par défaut utilisé", e)
        out = dict(defaut)
    out["annonces"] = (out["annonces"] + defaut["annonces"][len(out["annonces"]):])[:n]
    if fmt == "serie" and partie < parties:
        out["suite"] = out.get("suite") or defaut["suite"]
    else:
        out["suite"] = ""

    tags = list(dict.fromkeys(t.get("hashtags_fixes", []) + out["hashtags"]))
    credits = " ".join(f"twitch.tv/{c['login']}" for c in {c["login"]: c for c in clips}.values())
    titre = out["legende"]
    if fmt == "serie" and f"{partie}/{parties}" not in titre:
        titre = f"Partie {partie}/{parties} : {titre}"
    out["legende_complete"] = f"{titre}\n\n🎥 Clips : {credits}\n{' '.join(tags)}"[:2200]
    return out
