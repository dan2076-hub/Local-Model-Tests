"""Independent prepared CRUD implementation; extend list filtering."""
import sqlite3


def _open(database):
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, done INTEGER NOT NULL CHECK(done IN (0,1)), tag TEXT NOT NULL)")
    return connection


def _task(row):
    return dict(id=row[0], title=row[1], done=bool(row[2]), tag=row[3])


def add_task(database, task_id, title, tag=""):
    if not task_id or not title:
        raise ValueError("empty ID/title")
    connection = _open(database)
    try:
        with connection:
            connection.execute("INSERT INTO tasks VALUES (?,?,0,?)", (task_id, title, tag))
        return dict(id=task_id, title=title, done=False, tag=tag)
    finally:
        connection.close()


def list_tasks(database):
    connection = _open(database)
    try:
        return [_task(row) for row in connection.execute("SELECT id,title,done,tag FROM tasks ORDER BY id")]
    finally:
        connection.close()


def mark_done(database, task_id):
    if not task_id:
        raise ValueError("empty ID")
    connection = _open(database)
    try:
        with connection:
            row = connection.execute("SELECT id,title,done,tag FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise ValueError("unknown ID")
            connection.execute("UPDATE tasks SET done=1 WHERE id=?", (task_id,))
        task = _task(row)
        task["done"] = True
        return task
    finally:
        connection.close()
