#!/usr/bin/env python3
"""
senpai_dl.py – Extraction / téléchargement du flux vidéo depuis senpai-stream.bond
================================================================================

Objectif : à partir de l'URL publique d'une œuvre (film / série / anime / épisode)
sur https://senpai-stream.bond, ce script identifie automatiquement l'URL du
fichier MP4 hébergé (Cloudflare R2, S3-compatible, URL pré-signée AWS SigV4)
et la télécharge.

Contexte technique découvert :
  * Front-end Laravel + Livewire + Alpine.js
  * Composant Livewire "watch-component" : impose 5 étapes d'adwall
    (méthode `incrementSteps`) puis un Cloudflare Turnstile.
  * Bypass : le backend n'exige pas de vraie validation du token Turnstile,
    l'appel `watching` (submit du form) fonctionne avec un champ nul.
  * Après `watching`, `getVideoLink(0)` renvoie une URL /embed/<token_Laravel>
    (payload Laravel Crypt), qui rend une page contenant un `<media-player>`
    Vidstack avec l'URL MP4 pré-signée directement dans l'attribut `src`.
  * URL finale = *.r2.cloudflarestorage.com/... avec paramètres
    X-Amz-Signature valables ~12 h ; téléchargeable via GET Range.

Usage :
    python3 senpai_dl.py <URL_œuvre> [-o fichier.mp4] [--extract-only] [--index N]

Exemples :
    python3 senpai_dl.py https://senpai-stream.bond/movie/backrooms
    python3 senpai_dl.py https://senpai-stream.bond/episode/9-1-1/9-17 -o s9e17.mp4
    python3 senpai_dl.py https://senpai-stream.bond/anime/one-piece --extract-only

Dépendances :
    pip install requests
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import sys
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

BASE_URL = "https://senpai-stream.bond"
LIVEWIRE_ENDPOINT = f"{BASE_URL}/livewire/update"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DEFAULT_CHUNK = 1024 * 1024  # 1 Mo


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[senpai_dl] {msg}", file=sys.stderr, flush=True)


def slug_from_url(url: str) -> str:
    """Extrait un slug utilisable comme nom de fichier depuis l'URL de l'œuvre."""
    path = urlparse(url).path.strip("/").replace("/", "_")
    return re.sub(r"[^A-Za-z0-9._-]+", "-", path) or "video"


# ---------------------------------------------------------------------------
# Étapes de l'extraction
# ---------------------------------------------------------------------------

def fetch_page(session: requests.Session, work_url: str) -> str:
    """Étape 1 : GET de la page œuvre + init des cookies Laravel."""
    log(f"GET {work_url}")
    r = session.get(work_url, timeout=30)
    r.raise_for_status()
    return r.text


def parse_csrf_and_snapshot(html: str) -> Tuple[str, str]:
    """Étape 2 : extraction du token CSRF et du snapshot Livewire du composant
    watch-component depuis le HTML rendu."""
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    if not m:
        raise RuntimeError("Token CSRF introuvable dans la page.")
    csrf = m.group(1)

    marker = "&quot;name&quot;:&quot;watch-component&quot;"
    idx = html.find(marker)
    if idx < 0:
        raise RuntimeError(
            "Composant watch-component absent : "
            "contenu payant, indisponible, ou URL incorrecte ?"
        )

    snap_start = html.rfind('wire:snapshot="', 0, idx)
    if snap_start < 0:
        raise RuntimeError("wire:snapshot introuvable pour watch-component.")

    snap_start += len('wire:snapshot="')
    snap_end = html.find('"', snap_start)
    snapshot_json = html_mod.unescape(html[snap_start:snap_end])
    # Validation minimale
    json.loads(snapshot_json)
    return csrf, snapshot_json


def livewire_call(
    session: requests.Session,
    work_url: str,
    csrf: str,
    snapshot: str,
    method: Optional[str] = None,
    params: Optional[list] = None,
    updates: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Étape 3 : appel générique à l'API /livewire/update.
    Retourne la réponse JSON complète. Le champ snapshot du 1er composant est
    à réutiliser pour l'appel suivant."""
    calls = (
        [{"path": "", "method": method, "params": params or []}] if method else []
    )
    payload = {
        "_token": csrf,
        "components": [
            {
                "snapshot": snapshot,
                "updates": updates or {},
                "calls": calls,
            }
        ],
    }
    r = session.post(
        LIVEWIRE_ENDPOINT,
        headers={
            "Content-Type": "application/json",
            "X-CSRF-TOKEN": csrf,
            "X-Livewire": "",
            "Referer": work_url,
            "Origin": BASE_URL,
        },
        data=json.dumps(payload),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def resolve_video_url(session: requests.Session, work_url: str, stream_index: int = 0) -> str:
    """Enchaîne les étapes Livewire pour obtenir l'URL /embed/<token>
    contenant la source vidéo."""
    html = fetch_page(session, work_url)
    csrf, snapshot = parse_csrf_and_snapshot(html)

    # Adwall : incrementSteps × total_steps
    total_steps = int(json.loads(snapshot)["data"].get("total_steps", 5))
    log(f"Adwall à contourner : {total_steps} step(s) incrementSteps.")
    for i in range(total_steps):
        resp = livewire_call(session, work_url, csrf, snapshot, "incrementSteps")
        snapshot = resp["components"][0]["snapshot"]
    current_step = json.loads(snapshot)["data"]["currentStep"]
    log(f"currentStep atteint = {current_step}")

    # Bypass Turnstile : le backend accepte watching même sans jeton valide
    resp = livewire_call(session, work_url, csrf, snapshot, "watching")
    snapshot = resp["components"][0]["snapshot"]
    if json.loads(snapshot)["data"]["isPreloader"]:
        raise RuntimeError("Pré-loader toujours actif après watching.")
    log("watching() OK -> isPreloader=false.")

    # Récupération de l'URL du player
    resp = livewire_call(
        session, work_url, csrf, snapshot, "getVideoLink", [stream_index]
    )
    embed_url = resp["components"][0]["effects"]["returns"][0]
    if not embed_url:
        raise RuntimeError(f"getVideoLink({stream_index}) a retourné null.")
    log(f"URL embed : {embed_url}")

    # La page embed contient l'URL MP4 dans <media-player src="...">
    r = session.get(embed_url, headers={"Referer": work_url}, timeout=30)
    r.raise_for_status()
    m = re.search(r'src="(https://[^"]+\.mp4[^"]*)"', r.text)
    if not m:
        # Fallback : n'importe quelle URL MP4 dans la page
        m = re.search(r'https://[^\s"\'<>]+\.mp4[^\s"\'<>]*', r.text)
        if not m:
            raise RuntimeError("URL MP4 introuvable dans /embed/.")
    video_url = html_mod.unescape(m.group(1) if m.lastindex else m.group(0))
    return video_url


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def http_get_size(url: str) -> int:
    """R2 refuse HEAD sur URL SigV4 : on lit Content-Range d'un GET Range 0-0."""
    r = requests.get(url, headers={"Range": "bytes=0-0"}, timeout=30, stream=True)
    r.raise_for_status()
    cr = r.headers.get("Content-Range", "")
    r.close()
    m = re.match(r"bytes 0-0/(\d+)", cr)
    return int(m.group(1)) if m else 0


def download(url: str, out_path: str, resume: bool = True) -> None:
    """Télécharge le MP4 en streaming avec reprise (Range) et barre de progression."""
    total = http_get_size(url)
    log(f"Taille distante : {total:,} octets (~{total/1024/1024:.1f} Mo)")

    start = 0
    mode = "wb"
    if resume and os.path.exists(out_path):
        start = os.path.getsize(out_path)
        if start >= total > 0:
            log("Fichier déjà complet.")
            return
        mode = "ab"
        log(f"Reprise depuis l'octet {start:,}.")

    headers = {"Range": f"bytes={start}-"} if start else {}
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        done = start
        t0 = time.time()
        with open(out_path, mode) as f:
            for chunk in r.iter_content(DEFAULT_CHUNK):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 / total
                    speed = (done - start) / max(time.time() - t0, 0.1) / 1024 / 1024
                    print(
                        f"\r  {pct:6.2f}%  {done/1024/1024:9.1f} / "
                        f"{total/1024/1024:.1f} Mo  ({speed:5.2f} Mo/s)",
                        end="", file=sys.stderr, flush=True,
                    )
        print("", file=sys.stderr)
    log(f"Terminé : {out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Extraction MP4 depuis senpai-stream.bond")
    ap.add_argument("url", help="URL de l'œuvre (movie/tv-show/anime/episode).")
    ap.add_argument("-o", "--output", help="Fichier de sortie (défaut : dérivé du slug).")
    ap.add_argument("-i", "--index", type=int, default=0, help="Index de source vidéo.")
    ap.add_argument("--extract-only", action="store_true",
                    help="N'affiche que l'URL MP4 sans télécharger.")
    ap.add_argument("--no-resume", action="store_true", help="Ne pas reprendre.")
    args = ap.parse_args()

    if not args.url.startswith(BASE_URL):
        log(f"ERREUR : l'URL doit commencer par {BASE_URL}")
        return 2

    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    })

    try:
        video_url = resolve_video_url(session, args.url, args.index)
    except Exception as e:
        log(f"ERREUR d'extraction : {e}")
        return 1

    print(video_url)  # stdout = URL brute (pipe-friendly)

    if args.extract_only:
        return 0

    out = args.output or f"{slug_from_url(args.url)}.mp4"
    try:
        download(video_url, out, resume=not args.no_resume)
    except Exception as e:
        log(f"ERREUR téléchargement : {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
