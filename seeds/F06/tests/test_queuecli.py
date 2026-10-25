"""Prepared baseline regression tests: preserve these while adding filters."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from queuecli import store


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "queue.sqlite"

    def test_persistence(self):
        store.add_task(self.db, "a", "title", "work")
        self.assertEqual(store.list_tasks(self.db), [dict(id="a", title="title", done=False, tag="work")])

    def test_duplicate_preserves(self):
        store.add_task(self.db, "a", "title")
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            store.add_task(self.db, "a", "replacement")
        self.assertEqual(store.list_tasks(self.db)[0]["title"], "title")

    def test_done_unknown(self):
        store.add_task(self.db, "a", "title")
        self.assertTrue(store.mark_done(self.db, "a")["done"])
        self.assertTrue(store.mark_done(self.db, "a")["done"])
        with self.assertRaises(ValueError):
            store.mark_done(self.db, "unknown")
        self.assertEqual(len(store.list_tasks(self.db)), 1)
