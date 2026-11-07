from app.services import agent_threads


def test_thread_ids_have_the_expected_shape():
    thread_id = agent_threads.new_thread_id()
    assert agent_threads.valid_id(thread_id)
    assert not agent_threads.valid_id("agents")
    assert not agent_threads.valid_id(thread_id.replace("~", "-"))


def test_titles_come_from_the_first_message():
    assert agent_threads.title_for("  list the  FX35 on the site, 16 photos ") == "list the FX35 on the site, 16 photos"
    assert agent_threads.title_for("") == "New chat"
    assert agent_threads.title_for("x" * 100).endswith("…")
