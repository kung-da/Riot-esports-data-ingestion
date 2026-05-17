import asyncio
import json

from crawler.utils.checkpoint import CheckpointManager


def test_checkpoint_skips_existing_raw_match(tmp_path) -> None:
    async def run() -> None:
        match_dir = tmp_path / "output" / "raw" / "matches"
        timeline_dir = tmp_path / "output" / "raw" / "timelines"
        checkpoint_dir = tmp_path / "checkpoints"
        match_dir.mkdir(parents=True)
        timeline_dir.mkdir(parents=True)
        (match_dir / "VN2_123.json").write_text("{}", encoding="utf-8")

        manager = CheckpointManager(checkpoint_dir, match_dir, timeline_dir)

        assert await manager.should_skip_match("VN2_123") is True
        checkpoint = json.loads((checkpoint_dir / "matches.json").read_text(encoding="utf-8"))
        assert "VN2_123" in checkpoint["processed_match_ids"]

    asyncio.run(run())


def test_checkpoint_marks_successful_match(tmp_path) -> None:
    async def run() -> None:
        match_dir = tmp_path / "matches"
        timeline_dir = tmp_path / "timelines"
        checkpoint_dir = tmp_path / "checkpoints"
        match_dir.mkdir(parents=True)
        timeline_dir.mkdir(parents=True)
        manager = CheckpointManager(checkpoint_dir, match_dir, timeline_dir)

        assert await manager.should_skip_match("VN2_456") is False
        await manager.mark_match_processed("VN2_456")
        assert await manager.should_skip_match("VN2_456") is True

    asyncio.run(run())
