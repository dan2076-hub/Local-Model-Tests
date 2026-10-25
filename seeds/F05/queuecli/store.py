"""Implement SQLite storage for the CLI; see BRIEF.md."""


def add_task(database, task_id, title, tag=""):
    raise NotImplementedError


def list_tasks(database):
    raise NotImplementedError


def mark_done(database, task_id):
    raise NotImplementedError
