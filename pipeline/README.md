# GTA Reels : 3 agents IA pour un compte TikTok GTA 6

| Agent | Ce qu'il fait | Fichier |
|---|---|---|
| **Clips** | Toutes les heures, cherche les clips Twitch récents de tes streamers (et de la catégorie GTA VI en français), les classe par vues et fraîcheur, télécharge les meilleurs. | `gta_reels/agent_clips.py` |
| **Montage** | Passe chaque clip en vertical 9:16 (fond flouté), ajoute les sous-titres automatiques (Whisper) et le crédit `@streamer sur Twitch`. Puis fabrique un **top de 1 min 10 exactement** : écran d'intro avec le titre, puis « #3 », « #2 », « #1 » annoncés par une **voix-off française** (Piper, gratuite, calculée sur le serveur) avant chaque clip. Claude écrit le titre, les phrases de la voix-off, la légende et les hashtags. | `gta_reels/agent_montage.py`, `gta_reels/redaction.py`, `gta_reels/voix.py` |
| **TikTok** | **Toutes les 2 h de 12h à 22h** (6 vidéos/jour), publie la meilleure vidéo prête et la note dans `data/publications.csv`. | `gta_reels/agent_tiktok.py` |

Tout se règle dans **`config.yaml`** (streamers, horaires, durée, hashtags…), sans toucher au code. Le fichier est relu à chaque minute : pas besoin de redémarrer.

---

## Installation (environ 1 h, une seule fois)

### 1. Louer un serveur (≈ 5 €/mois)
Par exemple Hetzner Cloud, modèle **CX22** (2 processeurs, 4 Go), système **Ubuntu 24.04**. Note l'adresse IP qu'il te donne.

### 2. Créer les 3 accès
1. **Twitch** : https://dev.twitch.tv/console/apps → *Register Your Application*. Nom au choix, OAuth Redirect URL `http://localhost`, catégorie *Application Integration*. Récupère le **Client ID** et génère un **Client Secret**.
2. **Claude** : https://console.anthropic.com → *API Keys* → crée une clé. Ajoute 5 € de crédit, ça suffit pour des centaines de légendes.
3. **TikTok** : https://developers.tiktok.com → *Manage apps* → *Connect an app*.
   - Ajoute les produits **Login Kit** et **Content Posting API**.
   - Dans Login Kit, mets une *Redirect URI* en `https://` (n'importe quelle page qui t'appartient, même vide).
   - Récupère le **Client key** et le **Client secret**.
   - Pour la publication 100 % automatique, active *Direct Post* et **soumets l'app à l'audit** (voir plus bas).

### 3. Installer sur le serveur
Depuis ton ordinateur, envoie le dossier sur le serveur puis connecte-toi :
```bash
scp -r gta-reels root@ADRESSE_IP:/root/
ssh root@ADRESSE_IP
```
Sur le serveur :
```bash
curl -fsSL https://get.docker.com | sh
cd /root/gta-reels
cp .env.example .env
nano .env          # colle tes clés, puis Ctrl+O, Entrée, Ctrl+X
docker compose build
```

### 4. Relier ton compte TikTok (une fois)
```bash
docker compose run --rm gta-reels python -m gta_reels.tiktok_auth
```
Ouvre le lien affiché, accepte, puis colle l'adresse de la page d'arrivée.

### 5. Lancer, 24h/24
```bash
docker compose up -d
docker compose logs -f      # voir ce que font les agents (Ctrl+C pour quitter l'affichage)
```
Le pipeline redémarre tout seul si le serveur redémarre.

### Tester un agent à la main
```bash
docker compose run --rm gta-reels python -m gta_reels.agent_clips
docker compose run --rm gta-reels python -m gta_reels.agent_montage
docker compose run --rm gta-reels python -m gta_reels.agent_tiktok
```
Les vidéos finales sont dans `data/final/`, l'historique des publications dans `data/publications.csv` (s'ouvre dans Excel), le journal technique dans `data/journal.log`.

Si un streamer demande un retrait : supprime la vidéo sur TikTok et ajoute son login dans `exclus:` de `config.yaml`.

---

## Publication : brouillon ou 100 % auto

- **`mode: brouillon`** (par défaut) : la vidéo arrive dans les notifications de ton app TikTok, tu appuies sur *Publier*. Ça marche tout de suite.
- **`mode: direct`** : publication sans aucune action. TikTok l'autorise **seulement après audit** de ton app ; avant l'audit, les vidéos sont forcées en privé (le pipeline bascule tout seul en `SELF_ONLY` et le note dans le journal). Après l'audit, mets `mode: direct` dans `config.yaml` et relance l'étape 4 (il faut le droit `video.publish`).

## À savoir (important)
- **Droits** : chaque clip appartient à son streamer. Demande-leur l'accord (beaucoup ont des programmes de « clippers »), sinon un signalement peut faire tomber des vidéos ou le compte. Le crédit est affiché à l'écran et dans la légende.
- **TikTok** pousse moins le contenu « non original ». Le montage (sous-titres, hook) aide, mais varier les formats (compilations, réactions, ta voix) aidera encore plus.
- **Rockstar** : pas de leaks, pas de trailers officiels repostés tels quels.

## Coûts
Serveur ≈ 5 €/mois, Claude quelques centimes par jour, Twitch, TikTok et Whisper gratuits.
