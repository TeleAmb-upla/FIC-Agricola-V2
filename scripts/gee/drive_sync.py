"""
Sincronización Google Drive → disco local (mismas credenciales que ``earthengine authenticate``).

Patrón alineado a genius_upla ``download_drive_to_repo``: alcance ``auth/drive``,
carpetas identificadas por nombre exacto, primera pasada espejo completo por clave
luego incremental (estado en ``drive_sync_keys_state.json`` junto a este módulo).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import paths

STATE_FILENAME = "drive_sync_keys_state.json"


@dataclass(frozen=True)
class DriveSyncSpec:
    drive_folder: str
    dest_dir: Path
    extensions: tuple[str, ...]
    stem_prefixes: tuple[str, ...] | None = None
    stem_exclude_substrings: tuple[str, ...] = ()


def _drive_sync_state_path() -> Path:
    return Path(__file__).resolve().parent / STATE_FILENAME


def _load_key_modes() -> dict[str, str]:
    p = _drive_sync_state_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        raw = data.get("keys")
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items()}
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return {}


def _save_key_modes(modes: dict[str, str]) -> None:
    p = _drive_sync_state_path()
    p.write_text(json.dumps({"keys": modes}, indent=2), encoding="utf-8")


def _ee_credential_paths() -> list[Path]:
    extra = os.environ.get("EARTHENGINE_CREDENTIALS", "").strip()
    if extra:
        return [Path(extra)]
    return [Path.home() / ".config" / "earthengine" / "credentials"]


def _load_drive_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    scope = ["https://www.googleapis.com/auth/drive"]
    cred_path = next((p for p in _ee_credential_paths() if p.is_file()), None)
    if cred_path is None:
        raise FileNotFoundError(
            "No se encontró credentials de Earth Engine. Ejecute: earthengine authenticate\n"
            f"Buscado en: {_ee_credential_paths()[0]}"
        )

    data = json.loads(cred_path.read_text(encoding="utf-8"))
    if not data.get("client_id") or not data.get("client_secret"):
        try:
            from ee import oauth as ee_oauth
        except ImportError as e:
            raise RuntimeError(
                "En credentials faltan client_id/client_secret. "
                "Instale earthengine-api o ejecute: earthengine authenticate"
            ) from e
        if not data.get("client_id"):
            data["client_id"] = ee_oauth.CLIENT_ID
        if not data.get("client_secret"):
            data["client_secret"] = ee_oauth.CLIENT_SECRET
        if not data.get("token_uri"):
            data["token_uri"] = ee_oauth.TOKEN_URI

    creds = Credentials.from_authorized_user_info(data, scope)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds.valid:
        raise RuntimeError(
            "Credenciales no válidas. Vuelva a ejecutar: earthengine authenticate"
        )
    return creds


def get_drive_service():
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=_load_drive_credentials(), cache_discovery=False)


def _drive_api_execute(request_callable):
    import errno
    import ssl

    from googleapiclient.errors import HttpError

    retry_http = {429, 500, 502, 503, 504}
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            return request_callable().execute()
        except HttpError as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status in retry_http and attempt < max_attempts - 1:
                time.sleep(min(2.0**attempt, 45.0))
                continue
            raise
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            if attempt < max_attempts - 1:
                time.sleep(min(2.0**attempt, 45.0))
                continue
            raise
        except OSError as e:
            if e.errno in (errno.EPIPE, errno.ECONNRESET, errno.ETIMEDOUT) and attempt < max_attempts - 1:
                time.sleep(min(2.0**attempt, 45.0))
                continue
            raise
        except ssl.SSLError:
            if attempt < max_attempts - 1:
                time.sleep(min(2.0**attempt, 45.0))
                continue
            raise


def _find_folder_id(service, name: str) -> str:
    from googleapiclient.errors import HttpError

    safe = name.replace("'", "\\'")
    q = (
        f"name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    try:
        res = _drive_api_execute(
            lambda: service.files().list(
                q=q, spaces="drive", fields="files(id,name)", pageSize=10
            )
        )
    except HttpError as e:
        raise RuntimeError(f"Drive API error al buscar carpeta '{name}': {e}") from e
    files = res.get("files", [])
    if not files:
        raise FileNotFoundError(
            f"No hay carpeta en Drive llamada exactamente '{name}'. "
            "Cree la carpeta o espere a que una exportación EE la genere."
        )
    if len(files) > 1:
        print(
            f"Aviso: varias carpetas '{name}'; usando la primera (id={files[0]['id']}).",
            file=sys.stderr,
        )
    return files[0]["id"]


def _list_files(service, folder_id: str) -> list[dict]:
    out: list[dict] = []
    page_token = None
    q = f"'{folder_id}' in parents and trashed = false"
    while True:

        def _page_request(page_token_arg=page_token):
            return service.files().list(
                q=q,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
                pageToken=page_token_arg,
                pageSize=100,
            )

        res = _drive_api_execute(_page_request)
        out.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return out


def _normalize_exts(extensions: Iterable[str]) -> tuple[str, ...]:
    return tuple(e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions)


def _filter_drive_files(files: list[dict], exts: tuple[str, ...]) -> list[dict]:
    out: list[dict] = []
    for f in files:
        name = f.get("name") or ""
        mime = f.get("mimeType") or ""
        if mime.startswith("application/vnd.google-apps."):
            continue
        if any(name.lower().endswith(ext) for ext in exts):
            out.append(f)
    return out


def _file_matches_sync_spec(filename: str, spec: DriveSyncSpec) -> bool:
    if spec.stem_prefixes:
        if not any(filename.startswith(p) for p in spec.stem_prefixes):
            return False
    lower = filename.lower()
    for sub in spec.stem_exclude_substrings:
        if sub.lower() in lower:
            return False
    return True


def _download_binary(service, file_id: str, dest: Path) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    dest.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    dest.write_bytes(buf.getvalue())


def _sync_folder(
    service,
    spec: DriveSyncSpec,
    *,
    dry_run: bool,
    full_replace: bool,
    restrict_filenames: frozenset[str] | None = None,
) -> int:
    drive_folder_name = spec.drive_folder
    dest_dir = spec.dest_dir
    exts = _normalize_exts(spec.extensions)
    fid = _find_folder_id(service, drive_folder_name)
    files = _list_files(service, fid)
    raw_candidates = _filter_drive_files(files, exts)
    candidates = [
        f for f in raw_candidates if _file_matches_sync_spec(f.get("name") or "", spec)
    ]
    skipped_filter = len(raw_candidates) - len(candidates)
    allow_only = {n.lower() for n in restrict_filenames} if restrict_filenames else None
    if allow_only is not None:
        before = len(candidates)
        candidates = [
            f for f in candidates if (f.get("name") or "").lower() in allow_only
        ]
        print(
            f"  Filtro --only-files: {len(candidates)} de {before} archivo(s) "
            f"en '{drive_folder_name}'."
        )
    print(
        f"  Disponible en Drive '{drive_folder_name}': "
        f"{len(candidates)} archivo(s) tras filtro ({', '.join(exts)})"
    )
    if skipped_filter:
        print(f"  (Excluidos por prefijo/exclusión: {skipped_filter})")

    dest_dir.mkdir(parents=True, exist_ok=True)

    deleted = 0
    if full_replace and dest_dir.is_dir():
        for p in dest_dir.iterdir():
            if not p.is_file():
                continue
            if p.suffix.lower() not in exts:
                continue
            if not _file_matches_sync_spec(p.name, spec):
                continue
            if allow_only is not None and p.name.lower() not in allow_only:
                continue
            if dry_run:
                print(f"  [dry-run] eliminar local {p.name}")
            else:
                p.unlink()
            deleted += 1
        if deleted and not dry_run:
            print(f"  Eliminados {deleted} archivo(s) locales (espejo completo).")
        elif deleted and dry_run:
            print(f"  [dry-run] se eliminarían {deleted} archivo(s) locales.")

    n_down = 0
    for f in candidates:
        name = f.get("name") or ""
        dest = dest_dir / name
        if not full_replace and dest.is_file():
            continue
        if dry_run:
            print(f"  [dry-run] {drive_folder_name}/{name} -> {dest}")
        else:
            print(f"  descargando {name} -> {dest}")
            _download_binary(service, f["id"], dest)
        n_down += 1

    if not full_replace and len(candidates) > n_down:
        skipped = len(candidates) - n_down
        print(f"  Omitidos {skipped} archivo(s) (ya existen en local; modo incremental).")

    mode = "espejo completo" if full_replace else "incremental"
    print(f"  Descargas en esta pasada ({mode}): {n_down}")
    return n_down


SYNC_REGISTRY: dict[str, DriveSyncSpec] = {
    "s2": DriveSyncSpec(
        paths.DRIVE_S2_EXPORT_FOLDER,
        paths.REPO_S2_DIR,
        (".tif", ".tiff", ".geotiff", ".csv", ".json", ".geojson"),
    ),
    "s1": DriveSyncSpec(
        paths.DRIVE_S1_EXPORT_FOLDER,
        paths.REPO_S1_DIR,
        (".tif", ".tiff", ".geotiff"),
    ),
}


def parse_sync_keys(only_raw: str) -> list[str]:
    s = only_raw.strip().lower()
    if s == "all":
        return list(SYNC_REGISTRY.keys())
    keys = [k.strip().lower() for k in only_raw.split(",") if k.strip()]
    bad = set(keys) - set(SYNC_REGISTRY)
    if bad:
        raise ValueError(f"Claves no válidas: {bad}. Válidas: {sorted(SYNC_REGISTRY)}")
    return keys


def parse_restrict_filenames(raw: str | None) -> frozenset[str] | None:
    if not raw or not str(raw).strip():
        return None
    names = tuple(s.strip() for s in str(raw).split(",") if s.strip())
    return frozenset(names) if names else None


def run_drive_sync(
    keys: list[str],
    *,
    dry_run: bool = False,
    full_replace: bool | None = None,
    restrict_filenames: frozenset[str] | None = None,
) -> int:
    """
    full_replace: None = primera vez por clave es espejo completo, luego incremental;
    True = forzar espejo completo; False = solo incremental.
    """
    print("Conectando a Google Drive…")
    service = get_drive_service()
    modes = _load_key_modes()
    total = 0

    for key in keys:
        spec = SYNC_REGISTRY[key]
        if full_replace is True:
            use_full = True
        elif full_replace is False:
            use_full = False
        else:
            use_full = modes.get(key) != "incremental"

        print(f"  ▸ [{key}] {spec.drive_folder} -> {spec.dest_dir}")
        if use_full:
            print("  Modo: espejo completo (reemplaza gestionados en local)")
        else:
            print("  Modo: incremental (solo faltantes)")
        try:
            total += _sync_folder(
                service,
                spec,
                dry_run=dry_run,
                full_replace=use_full,
                restrict_filenames=restrict_filenames,
            )
            if not dry_run:
                modes[key] = "incremental"
                _save_key_modes(modes)
        except FileNotFoundError as e:
            print(f"  omitido: {e}", file=sys.stderr)

    print(f"Listo. Archivos descargados (esta corrida): {total}" + (" (dry-run)" if dry_run else ""))
    if not dry_run and full_replace is None:
        print(f"Estado de modo por clave guardado en: {_drive_sync_state_path()}")
    return total


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Copiar exportaciones Sentinel-2 (GEE → Drive) al repositorio local fic_agro."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo lista acciones, sin escribir ni borrar.",
    )
    parser.add_argument(
        "--full-sync",
        action="store_true",
        help="Forzar espejo completo en todas las claves (reemplaza locales gestionados).",
    )
    parser.add_argument(
        "--incremental-only",
        action="store_true",
        help="Solo archivos que no existan en local (no borrar locales).",
    )
    parser.add_argument(
        "--only",
        default="all",
        help="Claves: s2, s1 o all.",
    )
    parser.add_argument(
        "--only-files",
        default="",
        metavar="NAMES",
        help="Nombres de archivo exactos (con extensión), separados por comas.",
    )
    args = parser.parse_args(argv)

    if args.full_sync and args.incremental_only:
        print("Use solo uno de --full-sync o --incremental-only.", file=sys.stderr)
        sys.exit(2)

    try:
        keys = parse_sync_keys(args.only)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    fr: bool | None
    if args.full_sync:
        fr = True
    elif args.incremental_only:
        fr = False
    else:
        fr = None

    run_drive_sync(
        keys,
        dry_run=args.dry_run,
        full_replace=fr,
        restrict_filenames=parse_restrict_filenames(args.only_files),
    )


if __name__ == "__main__":
    main()
