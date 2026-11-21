"""End-to-end command boundary behaviour, including the SQLite ledger."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from snowbudget.cli import main
from snowbudget.common import decode, encode

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / 'examples' / 'baseline.json'
CHANGED = ROOT / 'examples' / 'changed.json'
INVALID = ROOT / 'examples' / 'invalid.json'


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / 'ledger.sqlite'

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *argv):
        import io
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue().strip(), err.getvalue().strip()

    def test_preview_matches_published_result(self):
        code, preview, _ = self._run('preview', str(BASELINE))
        self.assertEqual(code, 0)
        code, published, _ = self._run('publish', str(BASELINE), str(self.ledger), 'original')
        self.assertEqual(code, 0)
        receipt = decode(published)
        self.assertEqual(set(receipt), {'edition', 'source_sha256', 'result_sha256'})

        code, shown, _ = self._run('show', str(self.ledger), 'original')
        self.assertEqual(code, 0)
        self.assertEqual(shown, preview)

    def test_invalid_input_exits_two_and_publishes_nothing(self):
        code, _, err = self._run('publish', str(INVALID), str(self.ledger), 'bad')
        self.assertEqual(code, 2)
        self.assertIn('overlapping grid cell', err)
        self.assertFalse(self.ledger.exists())

    def test_edition_names_cannot_be_overwritten(self):
        self.assertEqual(self._run('publish', str(BASELINE), str(self.ledger), 'e')[0], 0)
        code, _, err = self._run('publish', str(CHANGED), str(self.ledger), 'e')
        self.assertEqual(code, 2)
        self.assertIn('edition already exists', err)

    def test_history_show_source_and_replay(self):
        self._run('publish', str(BASELINE), str(self.ledger), 'original')
        self._run('publish', str(CHANGED), str(self.ledger), 'revised')

        code, history, _ = self._run('history', str(self.ledger))
        self.assertEqual(code, 0)
        self.assertEqual([row['edition'] for row in decode(history)], ['original', 'revised'])

        code, source, _ = self._run('source', str(self.ledger), 'original')
        self.assertEqual(code, 0)
        self.assertEqual(decode(source), decode(BASELINE.read_text(encoding='utf-8')))

        code, _, err = self._run('replay', str(self.ledger), 'revised')
        self.assertEqual(code, 0)
        self.assertEqual(err, '')

        code, _, err = self._run('show', str(self.ledger), 'missing')
        self.assertEqual(code, 2)
        self.assertIn('unknown edition', err)

    def test_stored_digests_guard_integrity(self):
        self._run('publish', str(BASELINE), str(self.ledger), 'original')
        with sqlite3.connect(self.ledger) as db:
            row = db.execute('SELECT result FROM editions WHERE name=?', ('original',)).fetchone()
            original_result = row[0]
            db.execute('UPDATE editions SET result=? WHERE name=?', (encode({'survey': 'tampered'}), 'original'))
        code, _, err = self._run('show', str(self.ledger), 'original')
        self.assertEqual(code, 2)
        self.assertIn('content digest mismatch', err)
        # Replay also refuses a tampered row rather than silently re-publishing.
        code, _, err = self._run('replay', str(self.ledger), 'original')
        self.assertEqual(code, 2)
        self.assertIn('content digest mismatch', err)
        # Restore the sealed row; replay of the intact edition succeeds.
        with sqlite3.connect(self.ledger) as db:
            db.execute('UPDATE editions SET result=? WHERE name=?', (original_result, 'original'))
        self.assertEqual(self._run('replay', str(self.ledger), 'original')[0], 0)

    def test_read_only_commands_never_create_an_archive(self):
        missing = Path(self.tmp.name) / 'never.sqlite'
        code, _, _ = self._run('history', str(missing))
        self.assertEqual(code, 2)
        self.assertFalse(missing.exists())

    def test_preview_unknown_file_exits_two(self):
        code, _, _ = self._run('preview', str(Path(self.tmp.name) / 'nope.json'))
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
