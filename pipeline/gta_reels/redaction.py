"""Claude, rédacteur en chef : choisit le format et la durée de chaque vidéo, puis écrit les textes."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from .commun import config, db, log, secours

L = log("redaction")

CHARTE = """Tu es le rédacteur en chef de « Bitume Minute », une page fan TikTok française (non affiliée à Rockstar Games) qui publie les meilleurs moments de streamers sur GTA 6, présentés par une voix-off.
Règles permanentes :
- Ne promets rien que les clips ne montrent pas. Jamais « officiel », « confirmé », « exclu » : on n'est pas Rockstar.
- Jamais de leak, de rumeur, d'astuce pour jouer avant la sortie ou de triche. Aucun spoiler de l'histoire dans un titre.
- Voix-off : phrases courtes, naturelles à l'oral, ton complice et drôle, sans emoji, hashtag ni abréviation.
- Voix-off : une voix de synthèse lit le texte tel quel. Écris les pseudos et les mots anglais comme ils se prononcent en français (« Léo » et pas « Leo », « Squizi », « strimeur »), sans chiffres collés aux pseudos."""


def _appeler(consigne: str, prompt: str, schema: dict, max_tokens: int = 4000) -> dict:
    t = config()["tiktok"]
    rep = anthropic.Anthropic().beta.messages.create(
        model=t.get("modele_ia", "claude-opus-5-5"),
        max_tokens=max_tokens, **secours(t.get("modele_ia", "claude-opus-5-5")),
        system=CHARTE + "\n\n" + consigne,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    if rep.stop_reason == "refusal":
        raise RuntimeError("refus du modèle")
    return json.loads(next(b.text for b in rep.content if b.type == "text"))


# ---------------------------------------------------------------- plan éditorial

FORMATS = ["solo", "top", "serie", "duel", "devine", "rafale", "astuce", "recap"]

SCHEMA_PLAN = {
    "type": "object",
    "properties": {
        "videos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": FORMATS},
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
- « duel » : exactement 2 clips de 2 streamers différents qui vivent une situation comparable (deux crashs, deux courses-poursuites, deux ratés…). La vidéo finit sur une question au public (« qui a fait le pire ? ») : idéal pour faire réagir en commentaires.
- « devine » : un seul clip avec un retournement clair au milieu ou à la fin. On met la vidéo en pause juste avant le moment clé et on demande au public de deviner la suite, puis on montre la réponse. N'utilise ce format que si la transcription horodatée montre clairement où se trouve le moment clé.
- « rafale » : 3 à 6 clips courts et drôles enchaînés sans annonce entre eux (format « hors contexte », rythme rapide). Pour les fous rires, les phrases cultes, les bugs absurdes.
- « astuce » : 1 à 3 clips où un streamer montre ou découvre quelque chose d'utile ou de surprenant dans le jeu (mécanique, lieu, détail caché). Jamais d'astuce de triche ni de leak.
- « recap » : « le meilleur de la journée » en compte à rebours, 3 à 5 clips de streamers variés. Seulement le soir (après 18 h), et au plus une fois par jour.
Durée : ce qui fait percer sur TikTok, c'est d'être regardé jusqu'au bout. Deux types de vidéos :
  • les vidéos COURTES (environ 3 vidéos sur 5) : 20 à 45 secondes, un seul moment fort ou 2-3 clips très courts (solo, devine, duel, rafale, petit top). Ce sont elles qui ramènent des abonnés.
  • les vidéos LONGUES (environ 2 vidéos sur 5) : 65 à 90 secondes, obligatoirement plus d'1 minute pour être rémunérées par TikTok (recap, top, série, astuce), seulement si les clips sont assez bons pour tenir jusqu'au bout.
Regarde la durée des dernières vidéos pour garder cet équilibre. Jamais plus que la borne maximale donnée. Si un clip est long, mets-en moins dans la vidéo plutôt que d'allonger.
Variété (très important) : ne fais pas toujours le même type de vidéo. Sur une journée, utilise le plus de formats différents possible : chaque format touche un public différent. Regarde la liste des dernières publications fournie et change de format, de thème et de streamer par rapport à elles. Dans un top, mélange plusieurs streamers dès que possible. Évite de mettre le même streamer en vedette deux fois de suite.
Suspense : construis de l'attente. Ordonne les clips pour faire monter la tension, garde le moment le plus fort pour la fin, et coupe les séries sur un suspense (un « cliffhanger ») qui donne envie de voir la partie suivante.
Tu n'es pas obligé d'utiliser tous les clips : garde pour plus tard ceux qui iront mieux avec de futurs clips, ou ignore les clips faibles. N'utilise jamais un clip deux fois. Utilise uniquement les ids fournis."""


def planifier(clips: list[dict], dmin: float, dmax: float) -> list[dict]:
    """clips : {id, streamer, titre, transcription, duree, score}. Renvoie [{format, parties: [[ids]]}]."""
    liste = "\n\n".join(
        f"id={c['id']} — {c['streamer']} — {c['duree']:.0f} s — note de Claude {c['score']:.0f}/10\n"
        f"Titre : {c['titre']}\nCe qui est dit : {(c['transcription'] or '(pas de parole)')[:600]}"
        for c in clips
    )
    dernieres = "\n".join(
        f"- {r['format'] or 'top'} — {r['duree'] or 0:.0f} s — {r['hook']} — {r['streamers']}"
        for r in db().execute(
            "SELECT v.format, v.hook, v.duree, (SELECT group_concat(DISTINCT c.streamer) FROM clips c "
            "WHERE instr(',' || v.clips || ',', ',' || c.id || ',') > 0) AS streamers "
            "FROM videos v ORDER BY v.id DESC LIMIT 10")
    )
    maintenant = datetime.now(ZoneInfo(config().get("fuseau_horaire", "Europe/Paris")))
    prompt = (f"Nous sommes le {maintenant:%d/%m/%Y}, il est {maintenant:%H:%M} (heure de Paris).\n\n"
              f"Dernières vidéos fabriquées (de la plus récente à la plus ancienne) :\n{dernieres or '(aucune)'}\n\n"
              f"Bornes de durée par vidéo (ou par partie) : {dmin:.0f} s à {dmax:.0f} s, écrans de voix-off compris "
              f"(les textes sont posés sur les clips : compte seulement la durée des clips, plus 3 s si une question finale).\n\nClips disponibles :\n\n{liste}\n\n"
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
        fmt = v["format"] if v["format"] in FORMATS else "top"
        if fmt == "serie" and len(parties) < 2:
            fmt = "top" if len(parties[0]) > 1 else "solo"
        if fmt != "serie" and len(parties) > 1:
            parties = [[i for p in parties for i in p]]
        nb = len(parties[0])
        if fmt in ("solo", "devine") and nb > 1:
            fmt = "top"
        if fmt == "duel" and nb != 2:
            fmt = "top" if nb > 1 else "solo"
        if fmt in ("rafale", "recap", "top") and nb == 1:
            fmt = "solo"
        # chaque vidéo (ou partie) doit tenir dans la durée max : on retire les clips les plus faibles
        for p in parties:
            while len(p) > 1 and 3 + sum(connus[i]["duree"] for i in p) > dmax:
                p.remove(min(p, key=lambda i: connus[i]["score"]))
        nb = len(parties[0])
        if fmt in ("top", "rafale", "recap") and nb == 1 and len(parties) == 1:
            fmt = "solo"
        if fmt == "duel" and nb != 2:
            fmt = "top" if nb > 1 else "solo"
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
        "hook": {"type": "string", "description": "Accroche posée en gros sur les 3 premières secondes du clip, 2 à 6 mots, MAJUSCULES, sans hashtag"},
        "intro": {"type": "string", "description": "Phrase de voix-off lue par-dessus le début du premier clip, 4 à 10 mots, percutante"},
        "annonces": {"type": "array", "items": {"type": "string"},
                     "description": "Une phrase de voix-off par clip, dans l'ordre, lue par-dessus le début du clip, 3 à 8 mots, qui nomme le streamer"},
        "suite": {"type": "string", "description": "Phrase de fin de partie qui donne envie de voir la suivante (vide si pas une série ou dernière partie)"},
        "question": {"type": "string", "description": "Question posée au public à la fin (duel) ou à la pause (devine), 4 à 10 mots. Peut servir pour d'autres formats si elle fait réagir ; sinon vide."},
        "pause_s": {"type": "number", "description": "Format devine uniquement : seconde du clip où mettre en pause, juste avant le moment clé (d'après les repères [Ns]). 0 pour les autres formats."},
        "legende": {"type": "string", "description": "Légende TikTok courte (max 150 caractères), 1 emoji maximum"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "description": "3 à 5 hashtags pertinents, commençant par #"},
    },
    "required": ["hook", "intro", "annonces", "suite", "question", "pause_s", "legende", "hashtags"],
    "additionalProperties": False,
}

CONSIGNE_TEXTES = """Ton travail : écrire les textes d'une vidéo dont le format est déjà décidé.
- top : compte à rebours ; chaque annonce commence par « Numéro N : » (le dernier clip est le numéro 1).
- solo : une seule annonce courte, ou une phrase qui lance le clip.
- serie : le hook et la légende indiquent la partie (ex. « PARTIE 1/3 »). Les annonces enchaînent l'histoire. « suite » annonce la partie suivante, sauf pour la dernière partie (alors vide).
- duel : une annonce par clip (« Choix 1 : … », « Choix 2 : … »). « question » demande au public de trancher (« Lequel a fait le pire ? »).
- devine : une seule annonce (elle n'est pas lue, mets une phrase courte). « pause_s » = la seconde juste avant le moment clé, d'après les repères [Ns] de la transcription (au moins 2 s après le début et 2 s avant la fin). « question » = « À ton avis, il va se passer quoi ? » ou une variante adaptée. L'intro ne doit rien révéler.
- rafale : pas d'annonce lue entre les clips (mets quand même une phrase courte par clip). Hook du type « GTA 6 HORS CONTEXTE ». Intro très courte et énergique.
- astuce : chaque annonce explique en une phrase ce que le streamer montre ou découvre.
- recap : comme un top, mais présenté comme « le meilleur de la journée ».
Pour les autres formats que duel et devine, « question » peut être une question qui fait réagir en commentaire (ou vide), et « pause_s » vaut 0.
Les textes sont posés directement sur les clips qui jouent déjà (pas d'écran d'attente) : la voix-off parle par-dessus le jeu, donc phrases très courtes. Le hook doit accrocher dans la première seconde.
Suspense : le hook et l'intro créent de l'attente sans rien révéler (« attends la fin », « personne ne s'attendait à ça », une question…). Varie les formulations : jamais deux fois la même accroche.
Ne réutilise pas un hook ou une légende déjà publiés récemment (liste fournie)."""


def _textes_par_defaut(clips, fmt, partie, parties) -> dict:
    n = len(clips)
    hook = {"top": "LE TOP GTA 6 DU JOUR", "recap": "LE MEILLEUR DE LA JOURNÉE", "duel": "QUI A FAIT LE PIRE ?",
            "devine": "DEVINE LA SUITE", "rafale": "GTA 6 HORS CONTEXTE", "astuce": "TU SAVAIS ÇA ?",
            "serie": f"PARTIE {partie}/{parties}"}.get(fmt, "REGARDE ÇA JUSQU'AU BOUT")
    if fmt in ("top", "recap"):
        annonces = [f"Numéro {n - i} : {c['streamer']}." for i, c in enumerate(clips)]
    elif fmt == "duel":
        annonces = [f"Choix {i + 1} : {c['streamer']}." for i, c in enumerate(clips)]
    else:
        annonces = [f"{c['streamer']} sur GTA 6." for c in clips]
    return {"hook": hook, "intro": "Voici un moment fou sur GTA 6.", "annonces": annonces,
            "suite": f"La suite dans la partie {partie + 1}." if fmt == "serie" and partie < parties else "",
            "question": {"duel": "Lequel a fait le pire ?", "devine": "À ton avis, il va se passer quoi ?"}.get(fmt, ""),
            "pause_s": 0, "legende": "GTA 6", "hashtags": []}


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
        f"Clip {i + 1}/{n} — streamer : {c['streamer']} — durée {c.get('duree', 0):.0f} s\nTitre Twitch : {c['titre']}\n"
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
    out["question"] = (out.get("question") or defaut["question"]).strip()
    try:
        out["pause_s"] = float(out.get("pause_s") or 0) if fmt == "devine" else 0.0
    except (TypeError, ValueError):
        out["pause_s"] = 0.0
    out["annonces"] = (out["annonces"] + defaut["annonces"][len(out["annonces"]):])[:n]
    if fmt == "serie" and partie < parties:
        out["suite"] = out.get("suite") or defaut["suite"]
    else:
        out["suite"] = ""

    tags, vus = [], set()  # sans doublons (#gta6 = #GTA6), 5 au maximum
    for h in t.get("hashtags_fixes", []) + out["hashtags"]:
        h = "#" + h.strip().lstrip("#").replace(" ", "")
        if len(h) > 1 and h.lower() not in vus:
            vus.add(h.lower())
            tags.append(h)
    tags = tags[:5]
    credits = " ".join(f"twitch.tv/{c['login']}" for c in {c["login"]: c for c in clips}.values())
    titre = out["legende"]
    if fmt == "serie" and f"{partie}/{parties}" not in titre:
        titre = f"Partie {partie}/{parties} : {titre}"
    out["legende_complete"] = f"{titre}\n\n🎥 Clips : {credits}\n{' '.join(tags)}"[:2200]
    return out
