"""
验证码登录功能测试脚本（无需网络、无需邮箱）
直接测试 auth 模块核心函数，验证登录逻辑正确性。
"""
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth import (
    init_auth, generate_verify_code, save_verify_code,
    verify_code, consume_verify_code, get_user_by_email,
    create_user, create_access_token, create_refresh_token,
    save_refresh_token, cleanup_expired_tokens,
    get_db_connection, decode_access_token,
    VERIFY_CODE_LENGTH,
)

TEST_EMAIL = "testonly@debug.local"

def setup():
    init_auth()
    user = get_user_by_email(TEST_EMAIL)
    if user is None:
        create_user("testonly", TEST_EMAIL, "Test1234", "user")
        print(f"[SETUP] Created test user: {TEST_EMAIL}")
    else:
        print(f"[SETUP] Test user already exists")

def assert_eq(a, b, msg):
    assert a == b, f"{msg}: expected {b}, got {a}"
    print(f"  [OK] {msg}")

def assert_true(v, msg):
    assert v, f"{msg}: should be True"
    print(f"  [OK] {msg}")

def assert_false(v, msg):
    assert not v, f"{msg}: should be False"
    print(f"  [OK] {msg}")

### 测试用例 ###

def test_save_and_verify():
    code = generate_verify_code()
    assert_eq(len(code), VERIFY_CODE_LENGTH, "code length correct")
    assert_true(code.isdigit(), "code is digits")
    save_verify_code(TEST_EMAIL, code)
    is_valid, cid = verify_code(TEST_EMAIL, code)
    assert_true(is_valid, "valid code passes")
    assert_true(cid is not None, "returns code_id")
    return code, cid

def test_reusable_before_consume(code):
    is_valid1, cid1 = verify_code(TEST_EMAIL, code)
    is_valid2, cid2 = verify_code(TEST_EMAIL, code)
    assert_true(is_valid1, "1st verify passes")
    assert_true(is_valid2, "2nd verify also passes before consume")
    assert_eq(cid1, cid2, "same code_id returned")

def test_consume_invalidates(code, cid):
    consume_verify_code(cid)
    is_valid, _ = verify_code(TEST_EMAIL, code)
    assert_false(is_valid, "consumed code invalid")

def test_wrong_code():
    is_valid, _ = verify_code(TEST_EMAIL, "000000")
    assert_false(is_valid, "wrong code rejected")

def test_expired_code():
    code = generate_verify_code()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM verification_codes WHERE email = ?", (TEST_EMAIL,))
    expires_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO verification_codes (email, code, expires_at, used, created_at) VALUES (?, ?, ?, 0, ?)",
        (TEST_EMAIL, code, expires_at, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()
    is_valid, _ = verify_code(TEST_EMAIL, code)
    assert_false(is_valid, "expired code rejected")

def test_full_lifecycle():
    """模拟 login_by_code 的完整流程"""
    code = generate_verify_code()
    save_verify_code(TEST_EMAIL, code)
    is_valid, cid = verify_code(TEST_EMAIL, code)
    assert_true(is_valid, "code valid")

    user = get_user_by_email(TEST_EMAIL)
    assert_true(user is not None, "user exists")

    # 执行登录后续操作
    token = create_access_token(user["id"], user["username"], user["role"])
    rt, exp = create_refresh_token(user["id"])
    save_refresh_token(rt, user["id"], exp)
    cleanup_expired_tokens()

    # 成功后消耗验证码
    consume_verify_code(cid)
    
    # 验证码不可再用
    is_valid2, _ = verify_code(TEST_EMAIL, code)
    assert_false(is_valid2, "consumed code unusable")
    
    # token 有效
    payload = decode_access_token(token)
    assert_eq(payload["sub"], str(user["id"]), "token user_id match")
    print(f"  OK access_token valid: user_id={payload['sub']}")

def test_midway_failure_preserves_code():
    """中间失败不消耗验证码"""
    code = generate_verify_code()
    save_verify_code(TEST_EMAIL, code)
    
    _, cid = verify_code(TEST_EMAIL, code)
    # 模拟失败 — 不 consume
    
    is_valid, _ = verify_code(TEST_EMAIL, code)
    assert_true(is_valid, "code still valid after midway failure")
    
    # 清理
    consume_verify_code(cid)


if __name__ == "__main__":
    print("=" * 56)
    print("  Verify Code Login - Unit Tests (no email needed)")
    print("=" * 56)
    setup()

    tests = [
        ("[1] generate/verify", lambda: test_save_and_verify()),
        ("[2] reusable before consume", lambda: test_reusable_before_consume(test_save_and_verify()[0])),
        ("[3] consumed invalid", lambda: test_consume_invalidates(*test_save_and_verify())),
        ("[4] wrong code rejected", test_wrong_code),
        ("[5] expired code rejected", test_expired_code),
        ("[6] full lifecycle", test_full_lifecycle),
        ("[7] midway failure preserves code", test_midway_failure_preserves_code),
    ]
    for name, fn in tests:
        print(f"\n--- {name} ---")
        fn()

    print("\n" + "=" * 56)
    print("  ALL TESTS PASSED!")
    print("=" * 56)