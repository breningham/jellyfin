import os

import pytest

from xtream_sync.writer import (
    MANIFEST_NAME,
    Diff,
    apply_diff,
    content_hash,
    load_manifest,
    plan_diff,
    save_manifest,
)


def test_manifest_round_trip(tmp_path):
    assert load_manifest(tmp_path) == {}
    save_manifest(tmp_path, {"a/b.strm": "h1"})
    assert load_manifest(tmp_path) == {"a/b.strm": "h1"}
    assert not (tmp_path / (MANIFEST_NAME + ".tmp")).exists()


def test_load_manifest_corrupt_json_returns_empty(tmp_path, caplog):
    (tmp_path / MANIFEST_NAME).write_text("{not json")
    with caplog.at_level("WARNING"):
        assert load_manifest(tmp_path) == {}
    assert "manifest" in caplog.text.lower()


@pytest.mark.parametrize(
    "text",
    ["[1, 2, 3]", '"just a string"', "42", '{"a": 1}', '{"a": ["not", "a", "string"]}'],
)
def test_load_manifest_non_dict_of_str_returns_empty(tmp_path, caplog, text):
    (tmp_path / MANIFEST_NAME).write_text(text)
    with caplog.at_level("WARNING"):
        assert load_manifest(tmp_path) == {}
    assert "manifest" in caplog.text.lower()


def test_plan_diff_classifies_paths():
    old = {"keep": content_hash("same"), "change": content_hash("old"), "gone": "x"}
    desired = {"keep": "same", "change": "new", "fresh": "n"}
    diff = plan_diff(old, desired)
    assert diff == Diff(added=["fresh"], updated=["change"], removed=["gone"])


def test_apply_writes_deletes_and_prunes(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "Movies/Old (2000)").mkdir(parents=True)
    (out / "Movies/Old (2000)/Old (2000).strm").write_text("old")
    (out / "Movies/Untracked").mkdir()
    (out / "Movies/Untracked/keep.strm").write_text("mine")

    old = {"Movies/Old (2000)/Old (2000).strm": content_hash("old")}
    desired = {"Movies/New (2001)/New (2001).strm": "url\n"}
    diff = plan_diff(old, desired)
    manifest = apply_diff(out, desired, diff)

    assert (out / "Movies/New (2001)/New (2001).strm").read_text() == "url\n"
    assert not (out / "Movies/Old (2000)").exists()
    assert (out / "Movies/Untracked/keep.strm").read_text() == "mine"
    assert (out / "Movies").is_dir()
    assert manifest == {"Movies/New (2001)/New (2001).strm": content_hash("url\n")}


def test_apply_rewrites_tracked_file_missing_on_disk(tmp_path):
    desired = {"Movies/A/A.strm": "u\n"}
    manifest = {p: content_hash(t) for p, t in desired.items()}
    diff = plan_diff(manifest, desired)
    assert diff == Diff(added=[], updated=[], removed=[])
    apply_diff(tmp_path, desired, diff)
    assert (tmp_path / "Movies/A/A.strm").read_text() == "u\n"
    assert diff.restored == ["Movies/A/A.strm"]


def test_apply_does_not_count_a_normally_added_file_as_restored(tmp_path):
    desired = {"Movies/B/B.strm": "b\n"}
    diff = plan_diff({}, desired)
    assert diff.added == ["Movies/B/B.strm"]
    apply_diff(tmp_path, desired, diff)
    assert diff.restored == []


def test_apply_updates_changed_content(tmp_path):
    (tmp_path / "Movies/A").mkdir(parents=True)
    (tmp_path / "Movies/A/A.strm").write_text("old\n")
    old = {"Movies/A/A.strm": content_hash("old\n")}
    desired = {"Movies/A/A.strm": "new\n"}
    diff = plan_diff(old, desired)
    assert diff.updated == ["Movies/A/A.strm"]
    apply_diff(tmp_path, desired, diff)
    assert (tmp_path / "Movies/A/A.strm").read_text() == "new\n"


def test_removed_file_already_gone_is_not_an_error(tmp_path):
    old = {"Movies/A/A.strm": "h"}
    diff = plan_diff(old, {})
    assert apply_diff(tmp_path, {}, diff) == {}


def test_apply_adopts_byte_identical_file_at_added_path(tmp_path, caplog):
    (tmp_path / "Movies/A").mkdir(parents=True)
    (tmp_path / "Movies/A/A.strm").write_text("generated\n")
    before_mtime = (tmp_path / "Movies/A/A.strm").stat().st_mtime_ns
    desired = {"Movies/A/A.strm": "generated\n"}
    diff = plan_diff({}, desired)
    assert diff.added == ["Movies/A/A.strm"]
    with caplog.at_level("WARNING"):
        manifest = apply_diff(tmp_path, desired, diff)
    assert (tmp_path / "Movies/A/A.strm").stat().st_mtime_ns == before_mtime
    assert (tmp_path / "Movies/A/A.strm").read_text() == "generated\n"
    assert manifest == {"Movies/A/A.strm": content_hash("generated\n")}
    assert "Movies/A/A.strm" not in caplog.text


def test_apply_never_overwrites_untracked_file_at_added_path(tmp_path, caplog):
    (tmp_path / "Movies/A").mkdir(parents=True)
    (tmp_path / "Movies/A/A.strm").write_text("mine\n")
    desired = {"Movies/A/A.strm": "generated\n", "Movies/B/B.strm": "b\n"}
    diff = plan_diff({}, desired)
    assert diff.added == ["Movies/A/A.strm", "Movies/B/B.strm"]
    with caplog.at_level("WARNING"):
        manifest = apply_diff(tmp_path, desired, diff)
    assert (tmp_path / "Movies/A/A.strm").read_text() == "mine\n"
    assert (tmp_path / "Movies/B/B.strm").read_text() == "b\n"
    assert manifest == {"Movies/B/B.strm": content_hash("b\n")}
    assert "Movies/A/A.strm" in caplog.text


def test_updated_file_keeps_previous_mtime(tmp_path):
    target = tmp_path / "Movies/A/A.strm"
    target.parent.mkdir(parents=True)
    target.write_text("old\n")
    old_ns = 1_600_000_000_000_000_000
    os.utime(target, ns=(old_ns, old_ns))
    old = {"Movies/A/A.strm": content_hash("old\n")}
    desired = {"Movies/A/A.strm": "new\n"}
    diff = plan_diff(old, desired)
    assert diff.updated == ["Movies/A/A.strm"]
    apply_diff(tmp_path, desired, diff)
    assert target.read_text() == "new\n"
    assert target.stat().st_mtime_ns == old_ns


def test_added_file_gets_current_mtime(tmp_path):
    desired = {"Movies/B/B.strm": "b\n"}
    before = 1_600_000_000
    apply_diff(tmp_path, desired, plan_diff({}, desired))
    assert (tmp_path / "Movies/B/B.strm").stat().st_mtime > before


def test_plan_diff_pairs_moves_by_content_hash():
    old = {"Movies/A/A.strm": content_hash("u1\n"), "Movies/B/B.strm": content_hash("u2\n"), "gone": "x"}
    desired = {"Kids/A/A.strm": "u1\n", "Movies/B/B.strm": "u2\n", "Movies/C/C.strm": "u3\n"}
    diff = plan_diff(old, desired)
    assert diff == Diff(
        added=["Movies/C/C.strm"],
        updated=[],
        removed=["gone"],
        moved=[("Movies/A/A.strm", "Kids/A/A.strm")],
    )


def test_plan_diff_pairs_each_removed_path_once():
    old = {"a/x.nfo": content_hash("<n>1</n>"), "b/x.nfo": content_hash("<n>1</n>")}
    desired = {"c/x.nfo": "<n>1</n>"}
    diff = plan_diff(old, desired)
    assert diff.moved == [("a/x.nfo", "c/x.nfo")]
    assert diff.removed == ["b/x.nfo"]
    assert diff.added == []


def test_apply_renames_moved_file_and_keeps_mtime(tmp_path):
    src = tmp_path / "Movies/A/A.strm"
    src.parent.mkdir(parents=True)
    src.write_text("u1\n")
    old_ns = 1_600_000_000_000_000_000
    os.utime(src, ns=(old_ns, old_ns))
    old = {"Movies/A/A.strm": content_hash("u1\n")}
    desired = {"Kids/A/A.strm": "u1\n"}
    diff = plan_diff(old, desired)
    manifest = apply_diff(tmp_path, desired, diff)
    dst = tmp_path / "Kids/A/A.strm"
    assert dst.read_text() == "u1\n"
    assert dst.stat().st_mtime_ns == old_ns
    assert not src.exists()
    assert not (tmp_path / "Movies/A").exists()
    assert manifest == {"Kids/A/A.strm": content_hash("u1\n")}


def test_apply_move_falls_back_to_write_when_source_is_missing(tmp_path, caplog):
    old = {"Movies/A/A.strm": content_hash("u1\n")}
    desired = {"Kids/A/A.strm": "u1\n"}
    diff = plan_diff(old, desired)
    with caplog.at_level("WARNING"):
        manifest = apply_diff(tmp_path, desired, diff)
    assert (tmp_path / "Kids/A/A.strm").read_text() == "u1\n"
    assert manifest == {"Kids/A/A.strm": content_hash("u1\n")}
    assert "Movies/A/A.strm" in caplog.text


def test_apply_move_does_not_overwrite_untracked_destination(tmp_path, caplog):
    src = tmp_path / "Movies/A/A.strm"
    src.parent.mkdir(parents=True)
    src.write_text("u1\n")
    dst = tmp_path / "Kids/A/A.strm"
    dst.parent.mkdir(parents=True)
    dst.write_text("someone else's content\n")
    old = {"Movies/A/A.strm": content_hash("u1\n")}
    desired = {"Kids/A/A.strm": "u1\n"}
    diff = plan_diff(old, desired)
    with caplog.at_level("WARNING"):
        manifest = apply_diff(tmp_path, desired, diff)
    assert dst.read_text() == "someone else's content\n"
    assert not src.exists()
    assert "Kids/A/A.strm" not in manifest
    assert "Kids/A/A.strm" in caplog.text


def test_apply_keeps_listed_library_roots_when_empty(tmp_path):
    (tmp_path / "Movies/A").mkdir(parents=True)
    (tmp_path / "Movies/A/A.strm").write_text("u\n")
    (tmp_path / "Kids/Movies/B").mkdir(parents=True)
    (tmp_path / "Kids/Movies/B/B.strm").write_text("v\n")
    old = {"Movies/A/A.strm": content_hash("u\n"), "Kids/Movies/B/B.strm": content_hash("v\n")}
    diff = plan_diff(old, {})
    apply_diff(tmp_path, {}, diff, keep=["Movies", "Kids/Movies"])
    assert (tmp_path / "Movies").is_dir()
    assert (tmp_path / "Kids/Movies").is_dir()
    assert not (tmp_path / "Movies/A").exists()
    assert not (tmp_path / "Kids/Movies/B").exists()


def test_apply_creates_listed_library_root_even_when_never_pruned(tmp_path):
    # F2: a library root that never existed on disk at all (not just one
    # that would otherwise be pruned empty) must still be created, since
    # Jellyfin is configured against it.
    diff = plan_diff({}, {})
    apply_diff(tmp_path, {}, diff, keep=["Movies"])
    assert (tmp_path / "Movies").is_dir()


def test_apply_never_overwrites_directory_at_added_path(tmp_path, caplog):
    # F6: a directory sitting where an added file wants to go must not
    # raise (IsADirectoryError from read_text), and the other desired files
    # must still be written.
    (tmp_path / "Movies/A/A.strm").mkdir(parents=True)
    desired = {"Movies/A/A.strm": "generated\n", "Movies/B/B.strm": "b\n"}
    diff = plan_diff({}, desired)
    assert diff.added == ["Movies/A/A.strm", "Movies/B/B.strm"]
    with caplog.at_level("WARNING"):
        manifest = apply_diff(tmp_path, desired, diff)
    assert (tmp_path / "Movies/B/B.strm").read_text() == "b\n"
    assert manifest == {"Movies/B/B.strm": content_hash("b\n")}
    assert "Movies/A/A.strm" not in manifest
    assert "Movies/A/A.strm" in caplog.text
