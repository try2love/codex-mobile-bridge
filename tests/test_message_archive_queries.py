"""The native first-screen SQL must stay indexed as saved history grows."""
import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MessageArchiveQueries(unittest.TestCase):
    def test_native_schema_and_bounded_recent_lookup(self):
        swift = (ROOT / 'mobile/ios/BridgePreview/OfflineArchive.swift').read_text()
        java = (ROOT / 'mobile/android/src/io/github/try2love/codexbridge/OfflineArchive.java').read_text()
        schema = [line.strip() for line in swift.splitlines()
                  if line.strip().startswith('CREATE ') and 'IF NOT EXISTS' in line]
        names = ('archive_records', 'archive_parts', 'archive_deleted', 'turns_recent', 'turns_legacy')
        records = {line for line in schema if any(name in line for name in names)}
        android = set(re.findall(r'db.execSQL\("(CREATE (?:TABLE|INDEX) IF NOT EXISTS [^"\n]+)"\);', java))
        self.assertEqual(records, android)
        # Execute the production query with its recent-page filter and ordering.
        query = swift.split('let candidates=try rows("""', 1)[1].split('"""+filter', 1)[0]
        query += ' AND r.visible=1 ORDER BY r.asked DESC,r.turn DESC,r.position DESC,r.key DESC LIMIT 41'
        with sqlite3.connect(':memory:') as db:
            db.executescript('\n'.join(schema))
            db.execute("INSERT INTO accounts VALUES(1,'g','i','n','valid',0,0,0)")
            db.execute("INSERT INTO sessions VALUES(1,'s','s',1,1,0)")
            costs = []
            start = 0
            for count in (100, 10000):
                for i in range(start, count):
                    turn = f'{i:06}'
                    db.execute('INSERT INTO turns VALUES(1,?,?,?,NULL,0)', ('s', turn, i))
                    db.execute('INSERT INTO archive_records VALUES(1,?,?,?,?,?,1,?,1,?,1)',
                               ('s', turn, turn, i, 0, 'v', '{}'))
                steps = [0]
                def tick():
                    steps[0] += 1
                    return 0
                db.set_progress_handler(tick, 1)
                self.assertEqual(len(db.execute(query, (1, 's', 0)).fetchall()), 41)
                db.set_progress_handler(None, 0)
                costs.append(steps[0])
                start = count
            self.assertLessEqual(costs[1], costs[0] * 1.1, costs)
            plan = db.execute('EXPLAIN QUERY PLAN ' + query, (1, 's', 0)).fetchall()
            self.assertTrue(any('archive_recent' in row[3] for row in plan), plan)
            self.assertFalse(any('TEMP B-TREE' in row[3] for row in plan), plan)


if __name__ == '__main__':
    unittest.main()
