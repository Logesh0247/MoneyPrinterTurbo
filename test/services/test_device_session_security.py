from unittest.mock import patch

from webui import auth


def test_forged_device_account_is_not_a_login():
    with (patch.object(auth.st, "session_state", {}),
          patch.object(auth, "read_session_cookie", return_value=""),
          patch.object(auth.user_accounts, "restore_device_account") as restore):
        assert not auth.restore_session_from_device({"account": {"id": "victim", "email": "v@example.com"}})
        restore.assert_not_called()


def test_verified_session_still_restores():
    state = {}
    user = {"id": "user-1"}
    with (patch.object(auth.st, "session_state", state),
          patch.object(auth, "read_session_cookie", return_value="valid"),
          patch.object(auth.user_accounts, "user_from_session", return_value=user),
          patch.object(auth.user_accounts, "get_device_account", return_value=None)):
        assert auth.restore_session_from_device({})
    assert state["current_user"] == user
