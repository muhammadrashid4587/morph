from morph_desktop.actions import MockActionExecutor
from morph_desktop.context import DEFAULT_CONTEXT, DIAL_ROLES, Context, ContextState
from morph_desktop.server import MorphAgent


def test_all_contexts_have_dial_roles() -> None:
    assert set(DIAL_ROLES) == set(Context)
    assert {c.value for c in Context} == {"music", "presentation", "robot_targeting", "teach"}


def test_context_state_defaults_and_updates() -> None:
    state = ContextState()
    assert state.current == DEFAULT_CONTEXT
    assert state.set(Context.PRESENTATION) is True
    assert state.current == Context.PRESENTATION
    assert state.set(Context.PRESENTATION) is False  # no-op
    assert state.set(Context.TEACH) is True
    assert state.current == Context.TEACH


def test_set_context_message_updates_state_and_broadcasts() -> None:
    agent = MorphAgent(MockActionExecutor())
    reply = agent.handle_line('{"action":"set_context","context":"presentation"}')
    assert reply.response == {"status": "context", "context": "presentation"}
    assert reply.broadcast == {"status": "context", "context": "presentation"}
    assert agent.context.current == Context.PRESENTATION


def test_unchanged_context_replies_without_broadcast() -> None:
    agent = MorphAgent(MockActionExecutor(), ContextState(Context.TEACH))
    reply = agent.handle_line('{"action":"set_context","context":"teach"}')
    assert reply.response == {"status": "context", "context": "teach"}
    assert reply.broadcast is None


def test_ping_reports_current_context() -> None:
    agent = MorphAgent(MockActionExecutor())
    agent.handle_line('{"action":"set_context","context":"robot_targeting"}')
    assert agent.handle_line('{"action":"ping"}').response == {
        "status": "pong",
        "context": "robot_targeting",
    }


def test_invalid_context_leaves_state_unchanged() -> None:
    agent = MorphAgent(MockActionExecutor(), ContextState(Context.MUSIC))
    reply = agent.handle_line('{"action":"set_context","context":"gaming"}')
    assert reply.response["status"] == "error"
    assert reply.broadcast is None
    assert agent.context.current == Context.MUSIC
