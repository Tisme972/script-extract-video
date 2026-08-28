# script-extract-video

Script Python d'extraction et de téléchargement du flux vidéo MP4 depuis [senpai-stream.bond](https://senpai-stream.bond).

## Sommaire

- [Fonctionnement](#fonctionnement)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Utilisation](#utilisation)
- [Exemples](#exemples)
- [Options CLI](#options-cli)
- [Détails techniques](#détails-techniques)
- [Avertissement](#avertissement)
- [Licence](#licence)

## Fonctionnement

À partir de l'URL publique d'une œuvre (film, série, anime ou épisode) sur `senpai-stream.bond`, le script :

1. Récupère la page et initialise la session (cookies Laravel, token CSRF).
2. Enchaîne les appels Livewire nécessaires pour franchir l'adwall (`incrementSteps`) et valider l'étape de lecture (`watching`).
3. Récupère l'URL `/embed/<token>` du lecteur vidéo via `getVideoLink`.
4. Extrait l'URL MP4 pré-signée (Cloudflare R2 / S3, SigV4) présente dans la page embed.
5. Télécharge le fichier en streaming, avec reprise sur interruption (`Range` HTTP).

## Prérequis

- Python 3.8+
- [`requests`](https://pypi.org/project/requests/)

## Installation

```bash
git clone https://github.com/Tisme972/script-extract-video.git
cd script-extract-video
pip install requests
```

## Utilisation

```bash
python3 senpai_dl.py <URL_œuvre> [-o fichier.mp4] [--extract-only] [--index N]
```

## Exemples

```bash
# Téléchargement direct
python3 senpai_dl.py https://senpai-stream.bond/movie/backrooms

# Nom de fichier de sortie personnalisé
python3 senpai_dl.py https://senpai-stream.bond/episode/9-1-1/9-17 -o s9e17.mp4

# Extraction de l'URL MP4 uniquement (sans téléchargement)
python3 senpai_dl.py https://senpai-stream.bond/anime/one-piece --extract-only
```

## Options CLI

| Option              | Description                                              |
|---------------------|-----------------------------------------------------------|
| `url`                | URL de l'œuvre (`movie`, `tv-show`, `anime`, `episode`)  |
| `-o`, `--output`     | Fichier de sortie (défaut : dérivé du slug de l'URL)     |
| `-i`, `--index`      | Index de la source vidéo à extraire (défaut : `0`)       |
| `--extract-only`     | Affiche uniquement l'URL MP4, sans télécharger           |
| `--no-resume`        | Désactive la reprise de téléchargement                   |

## Détails techniques

- **Front-end** : Laravel + Livewire + Alpine.js.
- **Composant** `watch-component` : impose 5 étapes d'adwall (`incrementSteps`) puis un Cloudflare Turnstile.
- Le backend n'exige pas de validation réelle du token Turnstile : l'appel `watching` fonctionne avec un champ nul.
- `getVideoLink(index)` renvoie une URL `/embed/<token_Laravel>` (payload Laravel Crypt) dont la page rend un `<media-player>` Vidstack exposant l'URL MP4 pré-signée dans l'attribut `src`.
- L'URL finale (`*.r2.cloudflarestorage.com/...`) est signée (`X-Amz-Signature`) et valable environ 12 h ; le téléchargement se fait via requêtes `GET` avec `Range`.

## Avertissement

Ce logiciel est fourni "tel quel", sans aucune garantie. L'auteur décline toute responsabilité quant à l'usage qui en est fait — voir [LICENSE](LICENSE).

## Licence

[MIT](LICENSE)
