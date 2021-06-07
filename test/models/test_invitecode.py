import pytest

from app.models import User, InviteCode


@pytest.fixture
def null_user() -> User:
    return User.create(
        uid="dummy-user",
        crypto=0,
    )


@pytest.fixture
def real_invite_code(null_user: User) -> InviteCode:
    return InviteCode.create(
        user=null_user.uid,
        code="arealcode",
        max_uses=1,
    )


# NOTE: The tests take the `app` fixture so that the DB is accessible.
def test_an_invite_code_missing_from_the_db_is_invalid(app) -> None:
    with pytest.raises(InviteCode.DoesNotExist):
        InviteCode.get_valid("afakecode")


def test_an_invite_code_present_in_the_db_is_valid(
    app, real_invite_code: InviteCode
) -> None:
    assert InviteCode.get_valid(real_invite_code.code)
