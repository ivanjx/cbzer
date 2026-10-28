from __future__ import annotations

import argparse
import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import chain
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import ZIP_STORED, ZipFile

from .creation_time import folder_creation_time


IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".jxl",
})
INDEX_PATH = Path(__file__).with_name("index.html")


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def natural_key(name: str) -> tuple:
    return tuple(int(part) if index % 2 else part.casefold()
                 for index, part in enumerate(re.split(r"(\d+)", name)))


def resolve_folder(root: Path, relative: str) -> Path:
    if not isinstance(relative, str):
        raise RequestError(400, "The folder path must be text.")
    if "\x00" in relative:
        raise RequestError(400, "The folder path contains an invalid character.")

    folder = (root / relative).resolve()
    if not folder.is_relative_to(root):
        raise RequestError(403, "Choose a folder inside the library.")
    if not folder.is_dir():
        raise RequestError(404, "That folder no longer exists or is not a directory.")

    return folder


def browse_folder(root: Path, relative: str) -> dict:
    folder = resolve_folder(root, relative)
    folders = []
    archives = []
    image_count = 0

    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                folders.append({
                    "name": entry.name,
                    "path": (folder / entry.name).relative_to(root).as_posix(),
                    "created": folder_creation_time(entry),
                })
            elif entry.is_file(follow_symlinks=False):
                extension = Path(entry.name).suffix.casefold()
                if extension in IMAGE_EXTENSIONS:
                    image_count += 1
                elif extension == ".cbz":
                    archives.append(entry.name)

    return {
        "path": folder.relative_to(root).as_posix() if folder != root else "",
        "name": folder.name,
        "folders": sorted(folders, key=lambda item: natural_key(item["name"])),
        "archives": sorted(archives, key=natural_key),
        "image_count": image_count,
    }


def archive_name(name: str | None, default: str) -> str:
    if name is None:
        name = default
    if not isinstance(name, str):
        raise RequestError(400, "The output name must be text.")

    name = name.strip()
    if name.casefold().endswith(".cbz"):
        name = name[:-4]

    if (not name or name in {".", ".."} or name.endswith((".", " "))
            or any(character in '/\\<>:"|?*' or ord(character) < 32 for character in name)):
        raise RequestError(400, "Enter a filename, not a path. Avoid / \\ < > : \" | ? * and trailing dots.")

    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", name.split(".")[0].rstrip(), re.IGNORECASE):
        raise RequestError(400, "This filename is reserved. Choose a different output name.")

    return name + ".cbz"


def image_files(folder: Path):
    def fail(error: OSError):
        raise error

    for current, directories, files in os.walk(folder, followlinks=False, onerror=fail):
        current = Path(current)
        directories[:] = sorted(
            (name for name in directories if not (current / name).is_symlink()),
            key=natural_key,
        )

        for name in sorted(files, key=natural_key):
            image = current / name
            if image.suffix.casefold() in IMAGE_EXTENSIONS and not image.is_symlink() and image.is_file():
                yield image


def create_archive(root: Path, relative: str, name: str | None = None) -> dict:
    folder = resolve_folder(root, relative)
    if folder == root:
        raise RequestError(400, "Choose a folder below the library root.")

    output = folder.parent / archive_name(name, folder.name)
    images = image_files(folder)
    first = next(images, None)
    if first is None:
        raise RequestError(400, "No supported images were found in this folder or its subfolders.")

    try:
        stream = output.open("xb")
    except OSError:
        if output.exists() or output.is_symlink():
            raise RequestError(409, f"{output.name} already exists. Choose a different output name.") from None
        raise

    count = 0
    try:
        with stream, ZipFile(stream, "w", compression=ZIP_STORED, strict_timestamps=False) as archive:
            for image in chain((first,), images):
                archive.write(image, image.relative_to(folder).as_posix())
                count += 1
    except BaseException:
        output.unlink(missing_ok=True)
        raise

    return {
        "path": output.relative_to(root).as_posix(),
        "name": output.name,
        "images": count,
        "bytes": output.stat().st_size,
    }


class Server(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], library: Path):
        self.library = library.resolve()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: Server

    def reply(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, status: int, data: dict):
        self.reply(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def dispatch(self):
        try:
            url = urlsplit(self.path)
            if self.command == "GET" and url.path == "/":
                self.reply(200, INDEX_PATH.read_bytes(), "text/html; charset=utf-8")
            elif self.command == "GET" and url.path == "/favicon.ico":
                self.reply(204, b"", "image/x-icon")
            elif self.command == "GET" and url.path == "/api/browse":
                relative = parse_qs(url.query, keep_blank_values=True).get("path", [""])[0]
                self.reply_json(200, browse_folder(self.server.library, relative))
            elif self.command == "POST" and url.path == "/api/create":
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise RequestError(415, "Send the request as application/json.")

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise RequestError(400, "Invalid request length.") from None
                if not 0 < length <= 16384:
                    raise RequestError(400, "The request must contain a small JSON object.")

                try:
                    data = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeDecodeError):
                    raise RequestError(400, "Invalid JSON request.") from None
                if not isinstance(data, dict):
                    raise RequestError(400, "The request must be a JSON object.")

                result = create_archive(self.server.library, data.get("path", ""), data.get("name"))
                self.reply_json(201, result)
            else:
                raise RequestError(404, "Not found.")
        except RequestError as error:
            self.reply_json(error.status, {"error": str(error)})
        except PermissionError:
            self.reply_json(403, {"error": "Permission denied. Check read/write permissions on the library folder."})
        except OSError as error:
            logging.exception("Library operation failed")
            self.reply_json(500, {"error": f"Filesystem error: {error.strerror}. Check permissions and free space."})

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()


def main():
    parser = argparse.ArgumentParser(description="Browse a library and create CBZ archives without changing the originals.")
    parser.add_argument("library", nargs="?", default=os.environ.get("LIBRARY_PATH"), help="Library root, or set LIBRARY_PATH.")
    parser.add_argument("--host", default="127.0.0.1", help="Listening address (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8080, help="Listening port (default: 8080).")
    args = parser.parse_args()

    if not args.library:
        parser.error("Provide the library folder as an argument or set LIBRARY_PATH.")

    library = Path(args.library).expanduser().resolve()
    if not library.is_dir():
        parser.error("The library folder does not exist or is not a directory.")

    with Server((args.host, args.port), library) as server:
        print(f"Library: {library}", flush=True)
        print(f"CBZer listening on http://{args.host}:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
