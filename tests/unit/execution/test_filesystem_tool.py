"""Unit tests for FilesystemTool."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_control.core.actions import (
    FsCopyAction,
    FsDeleteAction,
    FsListAction,
    FsMkdirAction,
    FsMoveAction,
    FsReadAction,
    FsStatAction,
    FsWriteAction,
    Point,
)
from local_control.core.types import ImageRef, Observation, ScreenGeometry
from local_control.execution.tools.base import ExecutionContext
from local_control.execution.tools.filesystem_tool import FilesystemTool
from local_control.safety.kill_switch import StopToken


@pytest.fixture
def tool() -> FilesystemTool:
    return FilesystemTool()


@pytest.fixture
def ctx(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        run_id="test-fs-run",
        stop=StopToken(),
        workdir=tmp_path,
    )


@pytest.fixture
def dummy_obs() -> Observation:
    return Observation(
        step_index=0,
        captured_at=datetime.now(UTC),
        cursor=Point(x=0, y=0),
        image=ImageRef(
            path_original="",
            path_model="",
            model_width=800,
            model_height=600,
            phash="0000000000000000",
        ),
        screen=ScreenGeometry(width_px=800, height_px=600, scale_factor=1.0),
        windows=[],
    )


@pytest.mark.asyncio
async def test_fs_mkdir(tool: FilesystemTool, ctx: ExecutionContext, tmp_path: Path) -> None:
    target_dir = tmp_path / "sub" / "nested"
    action = FsMkdirAction(
        path=str(target_dir),
        target_description="create nested directory",
        expected_outcome="nested dir exists",
    )
    result = await tool.execute(action, ctx)
    assert result.success is True
    assert target_dir.is_dir()

    # Idempotent call
    result2 = await tool.execute(action, ctx)
    assert result2.success is True


@pytest.mark.asyncio
async def test_fs_write_and_read(
    tool: FilesystemTool, ctx: ExecutionContext, tmp_path: Path
) -> None:
    target_file = tmp_path / "docs" / "test.txt"
    write_act = FsWriteAction(
        path=str(target_file),
        content="Hello, Local Control!",
        overwrite=False,
        target_description="write test file",
        expected_outcome="file exists with text",
    )
    res_write = await tool.execute(write_act, ctx)
    assert res_write.success is True
    assert target_file.is_file()
    assert target_file.read_text(encoding="utf-8") == "Hello, Local Control!"

    # Read action
    read_act = FsReadAction(
        path=str(target_file),
        target_description="read test file",
        expected_outcome="file content returned",
    )
    res_read = await tool.execute(read_act, ctx)
    assert res_read.success is True
    assert res_read.output == "Hello, Local Control!"
    assert res_read.data["bytes_read"] == len("Hello, Local Control!")


@pytest.mark.asyncio
async def test_fs_write_overwrite_protection(
    tool: FilesystemTool, ctx: ExecutionContext, tmp_path: Path
) -> None:
    target_file = tmp_path / "exists.txt"
    target_file.write_text("initial", encoding="utf-8")

    # Overwrite False -> should fail with dest_exists
    write_fail = FsWriteAction(
        path=str(target_file),
        content="new content",
        overwrite=False,
        target_description="try overwrite false",
        expected_outcome="should fail",
    )
    res_fail = await tool.execute(write_fail, ctx)
    assert res_fail.success is False
    assert res_fail.error is not None
    assert res_fail.error.code == "dest_exists"
    assert target_file.read_text(encoding="utf-8") == "initial"

    # Overwrite True -> should succeed
    write_succ = FsWriteAction(
        path=str(target_file),
        content="new content",
        overwrite=True,
        target_description="overwrite true",
        expected_outcome="should overwrite",
    )
    res_succ = await tool.execute(write_succ, ctx)
    assert res_succ.success is True
    assert target_file.read_text(encoding="utf-8") == "new content"


@pytest.mark.asyncio
async def test_fs_read_binary_detection(
    tool: FilesystemTool, ctx: ExecutionContext, tmp_path: Path
) -> None:
    bin_file = tmp_path / "sample.bin"
    bin_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    read_act = FsReadAction(
        path=str(bin_file),
        target_description="read binary file",
        expected_outcome="should fail with binary_file",
    )
    res = await tool.execute(read_act, ctx)
    assert res.success is False
    assert res.error is not None
    assert res.error.code == "binary_file"


@pytest.mark.asyncio
async def test_fs_stat(tool: FilesystemTool, ctx: ExecutionContext, tmp_path: Path) -> None:
    sample_file = tmp_path / "stat_sample.txt"
    sample_file.write_text("Sample data", encoding="utf-8")

    stat_act = FsStatAction(
        path=str(sample_file),
        target_description="stat file",
        expected_outcome="metadata returned",
    )
    res = await tool.execute(stat_act, ctx)
    assert res.success is True
    assert res.data["is_file"] is True
    assert res.data["is_dir"] is False
    assert res.data["size"] == len("Sample data")


@pytest.mark.asyncio
async def test_fs_list(tool: FilesystemTool, ctx: ExecutionContext, tmp_path: Path) -> None:
    # Seed directory with 5 files and 1 subdirectory
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "c.txt").write_text("c", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested", encoding="utf-8")

    # Non-recursive listing
    list_act = FsListAction(
        path=str(tmp_path),
        recursive=False,
        target_description="list dir",
        expected_outcome="items listed",
    )
    res = await tool.execute(list_act, ctx)
    assert res.success is True
    assert res.data["total_count"] == 4  # a, b, c, sub
    assert res.data["truncated"] is False

    # Recursive listing with max_entries cap
    list_capped = FsListAction(
        path=str(tmp_path),
        recursive=True,
        max_entries=2,
        target_description="capped list",
        expected_outcome="truncated result",
    )
    res_capped = await tool.execute(list_capped, ctx)
    assert res_capped.success is True
    assert len(res_capped.data["entries"]) == 2
    assert res_capped.data["truncated"] is True
    assert res_capped.data["total_count"] == 5


@pytest.mark.asyncio
async def test_fs_copy(tool: FilesystemTool, ctx: ExecutionContext, tmp_path: Path) -> None:
    src_file = tmp_path / "orig.txt"
    src_file.write_text("orig", encoding="utf-8")
    dst_file = tmp_path / "copy.txt"

    copy_act = FsCopyAction(
        src=str(src_file),
        dst=str(dst_file),
        overwrite=False,
        target_description="copy file",
        expected_outcome="copy exists",
    )
    res = await tool.execute(copy_act, ctx)
    assert res.success is True
    assert dst_file.is_file()
    assert src_file.is_file()

    # Destination exists collision
    res_fail = await tool.execute(copy_act, ctx)
    assert res_fail.success is False
    assert res_fail.error is not None
    assert res_fail.error.code == "dest_exists"


@pytest.mark.asyncio
async def test_fs_move(tool: FilesystemTool, ctx: ExecutionContext, tmp_path: Path) -> None:
    src_file = tmp_path / "move_me.txt"
    src_file.write_text("content", encoding="utf-8")
    target_dir = tmp_path / "moved_dir"
    target_dir.mkdir()

    move_act = FsMoveAction(
        src=str(src_file),
        dst=str(target_dir),
        overwrite=False,
        target_description="move to dir",
        expected_outcome="moved to target dir",
    )
    res = await tool.execute(move_act, ctx)
    assert res.success is True
    assert not src_file.exists()
    assert (target_dir / "move_me.txt").is_file()


@pytest.mark.asyncio
async def test_fs_delete(tool: FilesystemTool, ctx: ExecutionContext, tmp_path: Path) -> None:
    trash_file = tmp_path / "to_delete.txt"
    trash_file.write_text("trash", encoding="utf-8")

    del_act = FsDeleteAction(
        path=str(trash_file),
        target_description="delete file",
        expected_outcome="file removed to trash",
    )
    res = await tool.execute(del_act, ctx)
    assert res.success is True
    assert not trash_file.exists()


@pytest.mark.asyncio
async def test_filesystem_postconditions(
    tool: FilesystemTool, ctx: ExecutionContext, dummy_obs: Observation, tmp_path: Path
) -> None:
    # mkdir postcondition
    sub_dir = tmp_path / "post_mkdir"
    mkdir_act = FsMkdirAction(
        path=str(sub_dir),
        target_description="mkdir",
        expected_outcome="dir exists",
    )
    res = await tool.execute(mkdir_act, ctx)
    assert await tool.postcondition(mkdir_act, res, dummy_obs) is True

    # write postcondition
    sub_file = tmp_path / "post_write.txt"
    write_act = FsWriteAction(
        path=str(sub_file),
        content="abc",
        target_description="write",
        expected_outcome="file exists",
    )
    res = await tool.execute(write_act, ctx)
    assert await tool.postcondition(write_act, res, dummy_obs) is True

    # delete postcondition
    del_act = FsDeleteAction(
        path=str(sub_file),
        target_description="delete",
        expected_outcome="file deleted",
    )
    res = await tool.execute(del_act, ctx)
    assert await tool.postcondition(del_act, res, dummy_obs) is True
