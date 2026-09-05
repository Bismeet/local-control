"""Unit tests for SQLite memory store (preferences, hints, workflows, migrations)."""

from pathlib import Path

from local_control.memory.store import MemoryStore


def test_memory_store_migrations_and_preferences(tmp_path: Path) -> None:
    db_file = tmp_path / "test_memory.db"
    store = MemoryStore(db_path=db_file)

    # Preferences CRUD
    assert store.get_preference("theme") is None
    store.set_preference("theme", "dark")
    assert store.get_preference("theme") == "dark"

    store.set_preference("font_size", "14")
    prefs = store.list_preferences()
    assert prefs == {"font_size": "14", "theme": "dark"}

    # Overwrite preference
    store.set_preference("theme", "light")
    assert store.get_preference("theme") == "light"

    # Delete preference
    deleted = store.delete_preference("theme")
    assert deleted is True
    assert store.get_preference("theme") is None
    assert store.delete_preference("nonexistent") is False

    store.close()


def test_memory_store_hints_and_search(tmp_path: Path) -> None:
    db_file = tmp_path / "test_hints.db"
    store = MemoryStore(db_path=db_file)

    h1_id = store.add_hint(
        app="notepad.exe",
        key="save_shortcut",
        value="Ctrl+S",
        confidence=1.0,
    )
    assert h1_id > 0

    h2_id = store.add_hint(
        app="explorer.exe",
        key="downloads_path",
        value="Downloads folder in user profile",
        confidence=0.9,
    )
    assert h2_id > 0

    h3_id = store.add_hint(
        app="*",
        key="general_tip",
        value="Always confirm destructive moves",
        confidence=0.8,
    )
    assert h3_id > 0

    # Query by app
    notepad_hints = store.get_hints(app="notepad.exe")
    assert len(notepad_hints) == 2  # notepad.exe + global (*)
    assert any(h.key == "save_shortcut" for h in notepad_hints)

    # Search hints by keyword and app
    results = store.search_hints(query="save notepad file", app="notepad.exe")
    assert len(results) >= 1
    assert results[0].key == "save_shortcut"

    # Search hints with downloads keyword
    dl_results = store.search_hints(query="organize downloads", app="explorer.exe")
    assert len(dl_results) >= 1
    assert dl_results[0].key == "downloads_path"

    # Delete hint
    del_ok = store.delete_hint(h1_id)
    assert del_ok is True
    assert not any(h.id == h1_id for h in store.get_hints())

    store.close()


def test_memory_store_workflows_and_runs_index(tmp_path: Path) -> None:
    db_file = tmp_path / "test_workflows.db"
    store = MemoryStore(db_path=db_file)

    wf_id = store.save_workflow(
        name="organize_downloads",
        goal_template="Organize {{downloads_dir}} into categories",
        steps_json='[{"type": "fs_list", "path": "{{downloads_dir}}"}]',
        params_json='{"downloads_dir": "C:\\\\Users\\\\Test\\\\Downloads"}',
        description="Organize files by type",
    )
    assert wf_id > 0

    wf = store.get_workflow("organize_downloads")
    assert wf is not None
    assert wf.name == "organize_downloads"
    assert wf.description == "Organize files by type"
    assert wf.success_count == 1

    steps = wf.get_steps()
    assert len(steps) == 1
    assert steps[0]["type"] == "fs_list"

    params = wf.get_params()
    assert "downloads_dir" in params

    # Workflow run recording and success increment
    run_id = "run-12345"
    rec_id = store.record_workflow_run(wf_id, run_id, "COMPLETED")
    assert rec_id > 0

    store.increment_workflow_success("organize_downloads")
    wf_updated = store.get_workflow("organize_downloads")
    assert wf_updated is not None
    assert wf_updated.success_count == 2

    # Runs indexing
    store.index_run(run_id="run-1", goal="Organize files", status="COMPLETED", step_count=5)
    indexed = store.get_indexed_runs()
    assert len(indexed) == 1
    assert indexed[0]["run_id"] == "run-1"
    assert indexed[0]["status"] == "COMPLETED"

    # Delete workflow
    assert store.delete_workflow("organize_downloads") is True
    assert store.get_workflow("organize_downloads") is None

    store.close()
