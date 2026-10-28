# CBZer

A small, mobile-friendly web interface for turning an image folder into a CBZ. Python standard library only: no dependencies, database, or app-level authentication.

## Run

Requires Python 3.12 or newer.

```powershell
python -m src.server "D:\Comics"
```

Open <http://127.0.0.1:8080>.

To try the sample library, run `python -m src.server data`. It includes 15 sample PNG pages across two series, with a nested bonus folder.

The folder argument is the library root containing your series. The server needs permission to read the images and write to the selected folder's parent directory.

You can set `LIBRARY_PATH` instead of providing the folder argument. This can be a mounted path; the app only sees paths available to the server, not your browser's filesystem.

```sh
LIBRARY_PATH=/library python -m src.server
```

Change the listening address or port with `--host` and `--port`. The default address is loopback only. For access through another machine or container:

```sh
python -m src.server /library --host 0.0.0.0 --port 8080
```

## Use

1. Open a series, then the image folder you want to archive. Breadcrumbs navigate back up.
2. Keep the default output name (the source folder's name), or enter a custom name. The interface previews the destination; `.cbz` is added automatically. Typing the extension is also accepted.
3. Click **Create CBZ** and wait for the completion message.

## Tests

```sh
python -m unittest discover -s tests -v
```

Tests use temporary libraries, including HTTP requests, archive contents, naming, overwrite protection, path boundaries, and source preservation.

## License

[MIT](LICENSE).
