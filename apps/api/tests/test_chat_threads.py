from app.services.chat_threads import (
    filter_messages_since,
    format_thread_transcript,
    group_messages_by_thread,
    newest_create_time,
    thread_title,
)


def _msg(name, thread, create_time, text, sender="Priya Shah"):
    return {
        "name": name,
        "createTime": create_time,
        "thread": {"name": thread},
        "text": text,
        "sender": {"name": "users/1", "displayName": sender},
    }


def test_group_messages_by_thread_groups_and_preserves_order():
    messages = [
        _msg("m1", "spaces/S/threads/T1", "2026-09-20T10:00:00Z", "first"),
        _msg("m2", "spaces/S/threads/T2", "2026-09-20T10:01:00Z", "other thread"),
        _msg("m3", "spaces/S/threads/T1", "2026-09-20T10:02:00Z", "reply"),
    ]
    grouped = group_messages_by_thread(messages)
    assert set(grouped.keys()) == {"spaces/S/threads/T1", "spaces/S/threads/T2"}
    assert [m["name"] for m in grouped["spaces/S/threads/T1"]] == ["m1", "m3"]


def test_group_messages_by_thread_sorts_by_create_time_even_if_input_unsorted():
    messages = [
        _msg("m2", "spaces/S/threads/T1", "2026-09-20T10:05:00Z", "later"),
        _msg("m1", "spaces/S/threads/T1", "2026-09-20T10:00:00Z", "earlier"),
    ]
    grouped = group_messages_by_thread(messages)
    assert [m["name"] for m in grouped["spaces/S/threads/T1"]] == ["m1", "m2"]


def test_group_messages_by_thread_empty_input():
    assert group_messages_by_thread([]) == {}


def test_filter_messages_since_keeps_strictly_newer():
    messages = [
        _msg("m1", "spaces/S/threads/T1", "2026-09-20T10:00:00Z", "old"),
        _msg("m2", "spaces/S/threads/T1", "2026-09-20T10:05:00Z", "new"),
    ]
    kept = filter_messages_since(messages, "2026-09-20T10:00:00Z")
    assert [m["name"] for m in kept] == ["m2"]


def test_filter_messages_since_empty_cursor_keeps_everything():
    messages = [_msg("m1", "spaces/S/threads/T1", "2026-09-20T10:00:00Z", "x")]
    assert filter_messages_since(messages, "") == messages


def test_filter_messages_since_handles_mixed_fractional_second_precision():
    # A whole-second cursor and a later-same-second fractional timestamp: as
    # raw strings, "...:00.500000Z" < "...:00Z" (since '.' < 'Z'), so a naive
    # string comparison would drop this message even though it is
    # chronologically newer than the cursor.
    messages = [_msg("m1", "spaces/S/threads/T1", "2026-09-20T10:00:00.500000Z", "later fractional")]
    kept = filter_messages_since(messages, "2026-09-20T10:00:00Z")
    assert [m["name"] for m in kept] == ["m1"]


def test_filter_messages_since_excludes_earlier_fractional_second():
    messages = [_msg("m1", "spaces/S/threads/T1", "2026-09-20T09:59:59.999999Z", "earlier fractional")]
    kept = filter_messages_since(messages, "2026-09-20T10:00:00Z")
    assert kept == []


def test_newest_create_time_of_empty_list_is_empty_string():
    assert newest_create_time([]) == ""


def test_newest_create_time_picks_max():
    messages = [
        _msg("m1", "spaces/S/threads/T1", "2026-09-20T10:00:00Z", "a"),
        _msg("m2", "spaces/S/threads/T1", "2026-09-20T10:05:00Z", "b"),
    ]
    assert newest_create_time(messages) == "2026-09-20T10:05:00Z"


def test_format_thread_transcript_labels_sender_and_time_in_order():
    messages = [
        _msg("m1", "spaces/S/threads/T1", "2026-09-20T10:00:00Z", "hello", sender="Priya"),
        _msg("m2", "spaces/S/threads/T1", "2026-09-20T10:01:00Z", "hi back", sender="Ravi"),
    ]
    transcript = format_thread_transcript(messages)
    lines = transcript.splitlines()
    assert lines[0] == "Priya — 2026-09-20T10:00:00Z: hello"
    assert lines[1] == "Ravi — 2026-09-20T10:01:00Z: hi back"


def test_thread_title_uses_space_name_and_first_message_snippet():
    messages = [_msg("m1", "spaces/S/threads/T1", "2026-09-20T10:00:00Z", "Q4 budget review kickoff")]
    title = thread_title("Finance", messages)
    assert title == "Finance — Q4 budget review kickoff"


def test_thread_title_truncates_long_snippet():
    long_text = "x" * 200
    messages = [_msg("m1", "spaces/S/threads/T1", "2026-09-20T10:00:00Z", long_text)]
    title = thread_title("Finance", messages)
    assert len(title) <= len("Finance — ") + 80
    assert title.startswith("Finance — ")
