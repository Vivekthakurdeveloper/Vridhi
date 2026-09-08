from app.security import MemberRole, hash_password, role_at_least, slugify, verify_password


def test_password_hash_roundtrip():
    hashed = hash_password("Secret123!")
    assert hashed != "Secret123!"
    assert verify_password("Secret123!", hashed)
    assert not verify_password("wrong", hashed)


def test_slugify():
    assert slugify("Aranya Foods") == "aranya-foods"
    assert slugify("  Hello!! World  ") == "hello-world"


def test_role_rank():
    assert role_at_least(MemberRole.owner, MemberRole.admin)
    assert role_at_least(MemberRole.admin, MemberRole.member)
    assert not role_at_least(MemberRole.member, MemberRole.admin)
