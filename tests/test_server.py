import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from src.server import RequestError, Server, browse_folder, create_archive


class LibraryFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = (Path(temporary.name) / "library").resolve()
        self.chapter = self.root / "Series" / "Chapter 2"
        self.chapter.mkdir(parents=True)
        (self.chapter / "extras").mkdir()
        (self.chapter / "page10.JPG").write_bytes(b"page ten")
        (self.chapter / "page2.jpg").write_bytes(b"page two")
        (self.chapter / "extras" / "page1.png").write_bytes(b"bonus page")
        (self.chapter / "notes.txt").write_text("Leave this file alone.\n", encoding="utf-8")
        self.originals = self.snapshot()

    def snapshot(self):
        return {
            path.relative_to(self.chapter).as_posix(): path.read_bytes()
            for path in self.chapter.rglob("*") if path.is_file()
        }

    def assert_request_error(self, status, relative, name=None):
        with self.assertRaises(RequestError) as caught:
            create_archive(self.root, relative, name)
        self.assertEqual(status, caught.exception.status)


class ArchiveTests(LibraryFixture):
    def test_default_name_contents_and_originals(self):
        result = create_archive(self.root, "Series/Chapter 2")
        output = self.root / "Series" / "Chapter 2.cbz"

        self.assertEqual("Series/Chapter 2.cbz", result["path"])
        self.assertEqual(3, result["images"])
        self.assertEqual(output.stat().st_size, result["bytes"])
        with ZipFile(output) as archive:
            self.assertEqual(["page2.jpg", "page10.JPG", "extras/page1.png"], archive.namelist())
            for name in archive.namelist():
                self.assertEqual(self.originals[name], archive.read(name))
            self.assertIsNone(archive.testzip())
        self.assertEqual(self.originals, self.snapshot())

    def test_custom_name_and_optional_extension(self):
        for name in ["My edition", "My other edition.CBZ"]:
            with self.subTest(name=name):
                result = create_archive(self.root, "Series/Chapter 2", name)
                expected = name.removesuffix(".CBZ") + ".cbz"
                self.assertEqual(expected, result["name"])
                self.assertTrue((self.chapter.parent / expected).is_file())
        self.assertEqual(self.originals, self.snapshot())

    def test_existing_archive_is_never_overwritten(self):
        output = self.chapter.parent / "Chapter 2.cbz"
        output.write_bytes(b"existing archive must survive")

        self.assert_request_error(409, "Series/Chapter 2")

        self.assertEqual(b"existing archive must survive", output.read_bytes())
        self.assertEqual(self.originals, self.snapshot())

    def test_existing_directory_is_never_replaced(self):
        output = self.chapter.parent / "Chapter 2.cbz"
        output.mkdir()
        self.assert_request_error(409, "Series/Chapter 2")
        self.assertTrue(output.is_dir())

    def test_empty_folder_does_not_create_archive(self):
        (self.root / "empty").mkdir()
        self.assert_request_error(400, "empty")
        self.assertFalse((self.root / "empty.cbz").exists())

    def test_images_only_in_nested_folders(self):
        nested = self.root / "Series" / "Volume" / "Chapter"
        nested.mkdir(parents=True)
        (nested / "page1.png").write_bytes(b"volume page")

        result = create_archive(self.root, "Series/Volume")
        self.assertEqual("Series/Volume.cbz", result["path"])
        with ZipFile(self.root / result["path"]) as archive:
            self.assertEqual(["Chapter/page1.png"], archive.namelist())

    def test_top_level_folder_cannot_be_archived_into_library_root(self):
        self.assert_request_error(400, "Series")
        self.assertEqual([], list(self.root.glob("*.cbz")))

    def test_library_root_cannot_be_archived(self):
        self.assert_request_error(400, "")
        self.assertFalse(self.root.with_suffix(".cbz").exists())

    def test_invalid_names_cannot_escape_or_create_special_files(self):
        for name in ["", " ", ".", "..", "../escape", "a/b", "a\\b", "bad:name", "bad\x00name", "trailing.", "NUL", "COM1"]:
            with self.subTest(name=name):
                self.assert_request_error(400, "Series/Chapter 2", name)
        self.assertEqual([], list(self.root.rglob("*.cbz")))
        self.assertEqual(self.originals, self.snapshot())

    def test_paths_outside_library_are_rejected(self):
        for relative in ["..", str(self.root.parent)]:
            with self.subTest(relative=relative):
                self.assert_request_error(403, relative)

    def test_invalid_path_is_a_request_error(self):
        for relative in [42, "bad\x00path"]:
            with self.subTest(relative=relative):
                self.assert_request_error(400, relative)

    def test_failed_write_removes_incomplete_archive_only(self):
        with patch("src.server.ZipFile.write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                create_archive(self.root, "Series/Chapter 2")

        self.assertFalse((self.chapter.parent / "Chapter 2.cbz").exists())
        self.assertEqual(self.originals, self.snapshot())

    def test_symlinks_cannot_include_outside_images(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "private.jpg").write_bytes(b"not part of the library")
        try:
            (self.chapter / "linked-folder").symlink_to(outside, target_is_directory=True)
            (self.chapter / "linked.jpg").symlink_to(outside / "private.jpg")
        except OSError as error:
            self.skipTest(f"Symlinks unavailable: {error}")

        result = create_archive(self.root, "Series/Chapter 2")
        with ZipFile(self.root / result["path"]) as archive:
            self.assertEqual(["page2.jpg", "page10.JPG", "extras/page1.png"], archive.namelist())
        self.assert_request_error(403, "Series/Chapter 2/linked-folder")
        self.assertEqual(["extras"], [item["name"] for item in browse_folder(self.root, "Series/Chapter 2")["folders"]])

    def test_browsing_unicode_names_and_natural_order(self):
        for name in ["Volume 10", "Volume 2", "²"]:
            (self.root / name).mkdir()
        (self.root / "Existing.cbz").write_bytes(b"archive")

        result = browse_folder(self.root, "")

        self.assertEqual(["Series", "Volume 2", "Volume 10", "²"], [item["name"] for item in result["folders"]])
        self.assertEqual(["Existing.cbz"], result["archives"])
        chapter = browse_folder(self.root, "Series/Chapter 2")
        self.assertEqual(2, chapter["image_count"])
        self.assertEqual([("extras", "Series/Chapter 2/extras")], [(item["name"], item["path"]) for item in chapter["folders"]])

    def test_browse_reports_creation_date_not_modification_date(self):
        before = time.time()
        directory = self.root / "New chapter"
        directory.mkdir()
        os.utime(directory, (946684800, 946684800))

        listing = browse_folder(self.root, "")
        created = next(item["created"] for item in listing["folders"] if item["name"] == "New chapter")
        if created is None:
            self.skipTest("The test filesystem does not expose creation dates.")

        self.assertGreaterEqual(created, before - 2)
        self.assertLessEqual(created, time.time() + 2)


class HttpTests(LibraryFixture):
    def setUp(self):
        super().setUp()
        self.server = Server(("127.0.0.1", 0), self.root)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            thread.join()
            self.server.server_close()

        self.addCleanup(stop)

    def request(self, method, path, body=None, content_type="application/json"):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.request(method, path, body=body, headers={"Content-Type": content_type})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_browse_create_and_find_archive_without_auth(self):
        status, listing = self.request("GET", "/api/browse?path=Series")
        self.assertEqual(200, status)
        self.assertEqual([("Chapter 2", "Series/Chapter 2")], [(item["name"], item["path"]) for item in listing["folders"]])

        status, result = self.request("POST", "/api/create", json.dumps({"path": "Series/Chapter 2", "name": "Mobile edition"}))
        self.assertEqual(201, status)
        self.assertEqual("Series/Mobile edition.cbz", result["path"])
        with ZipFile(self.root / result["path"]) as archive:
            self.assertEqual(b"page two", archive.read("page2.jpg"))

        status, listing = self.request("GET", "/api/browse?path=Series")
        self.assertEqual(200, status)
        self.assertEqual(["Mobile edition.cbz"], listing["archives"])
        self.assertEqual(self.originals, self.snapshot())

    def test_api_reports_existing_archive_as_conflict(self):
        body = json.dumps({"path": "Series/Chapter 2"})
        self.assertEqual(201, self.request("POST", "/api/create", body)[0])
        status, result = self.request("POST", "/api/create", body)
        self.assertEqual(409, status)
        self.assertIn("already exists", result["error"])

    def test_malformed_requests_do_not_create_files(self):
        for body in ["{", "[]", '{"path": 2}', '{"path": "bad\\u0000path"}']:
            with self.subTest(body=body):
                status, result = self.request("POST", "/api/create", body)
                self.assertEqual(400, status)
                self.assertIn("error", result)
        self.assertEqual([], list(self.root.rglob("*.cbz")))

    def test_cross_origin_form_requests_are_not_accepted(self):
        status, result = self.request("POST", "/api/create", "path=Series/Chapter+2", "application/x-www-form-urlencoded")
        self.assertEqual(415, status)
        self.assertIn("application/json", result["error"])
        self.assertEqual([], list(self.root.rglob("*.cbz")))

    def test_browse_cannot_leave_library(self):
        status, result = self.request("GET", "/api/browse?path=..")
        self.assertEqual(403, status)
        self.assertIn("inside the library", result["error"])


if __name__ == "__main__":
    unittest.main()
