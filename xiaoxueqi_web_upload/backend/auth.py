"""
认证与权限管理模块
提供用户注册、登录、Token 刷新、退出、权限中间件、邮箱验证码登录等功能。
"""
import os
import random
import smtplib
from email.mime.text import MIMEText

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from dotenv import load_dotenv

# ---------- 配置 ----------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

# 只加载 backend/.env。真实密钥不要写入代码或提交到 Git。
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-only-change-this-jwt-secret")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7
REFRESH_TOKEN_EXPIRE_DAYS_REMEMBER = 30

auth_router = APIRouter(prefix="/api/auth", tags=["认证"])
auth_scheme = HTTPBearer()

# 限流器（在 main.py 中初始化后通过 app.state 获取）
limiter = Limiter(key_func=get_remote_address)

DATA_DIR.mkdir(exist_ok=True)
# ---------- 邮箱配置 ----------
# SMTP_AUTH_CODE 是邮箱服务商生成的“授权码”，不是邮箱登录密码。
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.qq.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_AUTH_CODE", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER).strip()
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "true").strip().lower() in {"1", "true", "yes", "on"}
AUTH_DEBUG = os.getenv("AUTH_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}

# 验证码配置
VERIFY_CODE_EXPIRE_MINUTES = int(os.getenv("VERIFY_CODE_EXPIRE_MINUTES", "5"))
VERIFY_CODE_LENGTH = int(os.getenv("VERIFY_CODE_LENGTH", "6"))


def is_smtp_configured() -> bool:
    """只判断 SMTP 必要配置是否齐全，不暴露授权码。"""
    return bool(SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASS and SMTP_FROM)


def mask_email(email: str) -> str:
    """用于诊断接口，仅显示脱敏后的发件邮箱。"""
    if not email or "@" not in email:
        return ""
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked_name = name[:1] + "*"
    else:
        masked_name = name[:2] + "*" * max(2, len(name) - 2)
    return f"{masked_name}@{domain}"




# ---------- 数据库连接 ----------

def get_db_connection():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 数据模型 ----------

class LoginRequest(BaseModel):
    account: str       # 用户名或邮箱
    password: str
    remember: bool = False


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class SendCodeRequest(BaseModel):
    email: str


class LoginByCodeRequest(BaseModel):
    email: str
    code: str
    remember: bool = False


# ---------- 邮箱工具 ----------

def send_html_email(
    to_emails: str | list[str] | tuple[str, ...] | set[str],
    subject: str,
    html_body: str,
) -> tuple[bool, str]:
    """
    使用当前 SMTP 配置发送 HTML 邮件。

    返回：
    - (True, "")：发送成功
    - (False, "错误信息")：发送失败

    该函数不会输出或返回 SMTP 授权码。
    """
    if not is_smtp_configured():
        message = "SMTP 未配置，请检查 backend/.env"
        print(f"[EMAIL ERROR] {message}")
        return False, message

    if isinstance(to_emails, str):
        raw_recipients = [to_emails]
    else:
        raw_recipients = list(to_emails or [])

    recipients: list[str] = []
    seen: set[str] = set()

    for item in raw_recipients:
        email = str(item or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        recipients.append(email)

    if not recipients:
        return False, "收件人邮箱为空"

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(recipients)

    try:
        if SMTP_USE_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_FROM, recipients, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_FROM, recipients, msg.as_string())

        return True, ""
    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
        print(
            "[EMAIL ERROR] 发送邮件失败，收件人="
            f"{', '.join(mask_email(item) for item in recipients)}，"
            f"原因={error_message}"
        )
        return False, error_message


def send_verify_code_email(to_email: str, code: str) -> bool:
    """向指定邮箱发送验证码邮件，成功返回 True。"""
    subject = "智能车载视觉感知与告警系统 - 验证码"
    body = f"""<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e0e0e0; border-radius: 12px;">
    <h2 style="color: #2563eb; margin-top: 0;">智能车载视觉感知与告警系统</h2>
    <p>您好！</p>
    <p>您的登录验证码为：</p>
    <div style="font-size: 36px; font-weight: bold; color: #2563eb; text-align: center; padding: 24px; margin: 16px 0; background: #f0f7ff; border-radius: 8px; letter-spacing: 8px;">
        {code}
    </div>
    <p style="color: #64748b;">验证码有效期为 <strong>{VERIFY_CODE_EXPIRE_MINUTES} 分钟</strong>，请勿泄露给他人。</p>
    <p style="color: #94a3b8; font-size: 12px; margin-top: 24px;">此邮件由系统自动发送，请勿回复。</p>
</div>"""

    success, _ = send_html_email(
        to_emails=to_email,
        subject=subject,
        html_body=body,
    )
    return success

def generate_verify_code(length: int = VERIFY_CODE_LENGTH) -> str:
    """生成指定长度的数字验证码"""
    return "".join(str(random.randint(0, 9)) for _ in range(length))


def save_verify_code(email: str, code: str) -> None:
    """保存验证码到数据库（先删除该邮箱旧验证码再插入）"""
    conn = get_db_connection()
    cursor = conn.cursor()
    # 删除该邮箱旧的未过期验证码
    cursor.execute(
        "DELETE FROM verification_codes WHERE email = ?",
        (email,),
    )
    expires_at = (datetime.now() + timedelta(minutes=VERIFY_CODE_EXPIRE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO verification_codes (email, code, expires_at, used, created_at) VALUES (?, ?, ?, 0, ?)",
        (email, code, expires_at, now_text()),
    )
    conn.commit()
    conn.close()


def verify_code(email: str, code: str) -> tuple[bool, int | None]:
    """验证邮箱验证码是否有效（匹配且未过期、未使用），返回 (是否有效, 验证码记录ID)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM verification_codes WHERE email = ? AND code = ? AND used = 0 AND expires_at > ?",
        (email, code, now_text()),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return False, None
    return True, row["id"]


def consume_verify_code(code_id: int) -> None:
    """登录成功后标记验证码为已使用"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE verification_codes SET used = 1 WHERE id = ? AND used = 0",
        (code_id,),
    )
    conn.commit()
    conn.close()


def get_user_by_email(email: str) -> dict | None:
    """通过邮箱查找用户"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    return _user_row_to_dict(row)




# ---------- 密码工具 ----------

def hash_password(password: str) -> str:
    """对密码进行 bcrypt 哈希"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """验证密码是否匹配 bcrypt 哈希"""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def validate_password_strength(password: str) -> str | None:
    """
    校验密码强度：最少8位，包含大小写字母和数字。
    返回 None 表示通过，否则返回错误信息。
    """
    if len(password) < 8:
        return "密码长度不能少于8位"
    if not re.search(r"[A-Z]", password):
        return "密码必须包含大写字母"
    if not re.search(r"[a-z]", password):
        return "密码必须包含小写字母"
    if not re.search(r"\d", password):
        return "密码必须包含数字"
    return None


# ---------- JWT 工具 ----------

def create_access_token(user_id: int, username: str, role: str) -> str:
    """生成 access token（有效期 15 分钟）"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: int, remember: bool = False) -> tuple[str, str]:
    """
    生成 refresh token（有效期 7 天，remember=True 则 30 天）。
    返回 (token_string, expires_at_text)
    """
    days = REFRESH_TOKEN_EXPIRE_DAYS_REMEMBER if remember else REFRESH_TOKEN_EXPIRE_DAYS
    expires_at = datetime.now() + timedelta(days=days)
    token = uuid.uuid4().hex + secrets.token_hex(16)
    return token, expires_at.strftime("%Y-%m-%d %H:%M:%S")


def decode_access_token(token: str) -> dict:
    """解码并验证 access token，返回 payload 字典"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="无效的 token 类型")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="access token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的 access token")


# ---------- 数据库操作 ----------

def _user_row_to_dict(row) -> dict:
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


def get_user_by_account(account: str) -> dict | None:
    """通过用户名或邮箱查找用户"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM users WHERE username = ? OR email = ?",
        (account, account),
    )
    row = cursor.fetchone()
    conn.close()
    return _user_row_to_dict(row)


def get_user_by_id(user_id: int) -> dict | None:
    """通过 ID 查找用户"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return _user_row_to_dict(row)


def create_user(username: str, email: str, password: str, role: str = "user") -> int:
    """创建用户，返回新用户 ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    hashed = hash_password(password)
    cursor.execute(
        "INSERT INTO users (username, email, password, role, created_at) VALUES (?, ?, ?, ?, ?)",
        (username, email, hashed, role, now_text()),
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


def save_refresh_token(token: str, user_id: int, expires_at: str) -> None:
    """保存 refresh token 到数据库"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO refresh_tokens (token, user_id, expires_at, revoked, created_at) VALUES (?, ?, ?, 0, ?)",
        (token, user_id, expires_at, now_text()),
    )
    conn.commit()
    conn.close()


def revoke_refresh_token(token: str) -> bool:
    """标记 refresh token 为已撤销，返回是否成功"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE refresh_tokens SET revoked = 1 WHERE token = ? AND revoked = 0",
        (token,),
    )
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0


def is_refresh_token_valid(token: str) -> dict | None:
    """
    检查 refresh token 是否有效（未撤销且未过期）。
    返回包含 user_id 和 expires_at 的字典，无效则返回 None。
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM refresh_tokens WHERE token = ? AND revoked = 0",
        (token,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    expires_at = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
    if expires_at < datetime.now():
        return None
    return {"id": row["id"], "user_id": row["user_id"], "expires_at": row["expires_at"]}


def cleanup_expired_tokens() -> None:
    """清理过期和已撤销的 refresh token"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM refresh_tokens WHERE expires_at < ? OR revoked = 1",
        (now_text(),),
    )
    conn.commit()
    conn.close()


# ---------- 权限中间件 / 依赖注入 ----------

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(auth_scheme),
) -> dict:
    """
    验证 access token，返回当前用户信息。
    任何需要登录的接口添加 Depends(get_current_user) 即可。
    """
    payload = decode_access_token(credentials.credentials)
    user_id = int(payload["sub"])
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在或已被删除")
    return user


def require_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """验证当前用户是否为 admin 角色"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="权限不足，仅管理员可访问")
    return current_user


# ---------- 初始化数据库表 ----------

def init_auth():
    """创建认证相关表并插入种子数据"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user', 'admin')),
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        conn.commit()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS verification_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                code TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)



        # 种子数据：创建默认管理员（仅当不存在时）
        conn.commit()

        
        existing = cursor.execute(
            "SELECT id FROM users WHERE username = ?", ("admin",)
        ).fetchone()

        if existing is None:
            hashed = hash_password("Admin123!")
            cursor.execute(
                "INSERT INTO users (username, email, password, role, created_at) VALUES (?, ?, ?, ?, ?)",
                ("admin", "admin@xiaoxueqi.local", hashed, "admin", now_text()),
            )
            conn.commit()
            print("[auth] 默认管理员已创建: admin / Admin123!")

        conn.close()
        print("[auth] 认证模块初始化完成")
    except Exception as e:
        print(f"[auth] 初始化失败: {e}")
        import traceback
        traceback.print_exc()


# ---------- API 端点 ----------

@auth_router.post("/register")
def register(request: RegisterRequest):
    """注册新用户"""
    # 校验密码强度
    strength_error = validate_password_strength(request.password)
    if strength_error:
        raise HTTPException(status_code=400, detail=strength_error)

    # 校验用户名
    if not request.username or len(request.username.strip()) < 2:
        raise HTTPException(status_code=400, detail="用户名至少需要2个字符")
    if not re.match(r"^[a-zA-Z0-9_一-鿿]+$", request.username):
        raise HTTPException(status_code=400, detail="用户名只能包含字母、数字、下划线和中文")

    # 校验邮箱
    if not request.email or "@" not in request.email:
        raise HTTPException(status_code=400, detail="请输入有效的邮箱地址")

    # 检查用户名是否已存在
    conn = get_db_connection()
    cursor = conn.cursor()
    existing = cursor.execute(
        "SELECT id FROM users WHERE username = ? OR email = ?",
        (request.username, request.email),
    ).fetchone()
    conn.close()

    if existing:
        raise HTTPException(status_code=409, detail="用户名或邮箱已被注册")

    # 创建用户
    user_id = create_user(request.username, request.email, request.password, role="user")
    user = get_user_by_id(user_id)

    return {
        "status": "success",
        "message": "注册成功",
        "user": user,
    }


@auth_router.post("/login")
async def login(request: LoginRequest, req: Request):
    """用户登录（限流：每分钟最多 5 次）"""
    # 查找用户
    user_row = None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM users WHERE username = ? OR email = ?",
        (request.account, request.account),
    )
    user_row = cursor.fetchone()
    conn.close()

    if user_row is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 验证密码
    if not verify_password(request.password, user_row["password"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    user = _user_row_to_dict(user_row)

    # 生成 token
    access_token = create_access_token(user["id"], user["username"], user["role"])
    refresh_token, expires_at = create_refresh_token(user["id"], request.remember)

    # 保存 refresh token
    save_refresh_token(refresh_token, user["id"], expires_at)

    # 清理过期 token
    cleanup_expired_tokens()

    return {
        "status": "success",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": user,
    }


@auth_router.post("/send-code")
def send_code(request: SendCodeRequest):
    """发送邮箱验证码"""
    email = request.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="请输入有效的邮箱地址")

    # 检查邮箱是否已被注册
    user = get_user_by_email(email)
    if user is None:
        raise HTTPException(status_code=400, detail="该邮箱未注册，请先注册账号")

    # 生成验证码
    code = generate_verify_code()

    # 保存到数据库
    save_verify_code(email, code)

    # 发送邮件
    success = send_verify_code_email(email, code)
    if not success:
        raise HTTPException(status_code=500, detail="验证码发送失败，请稍后重试")

    return {
        "status": "success",
        "message": f"验证码已发送到 {email}，请查收",
    }


@auth_router.post("/debug-send-code")
def debug_send_code(request: SendCodeRequest):
    """调试接口。仅当 backend/.env 中 AUTH_DEBUG=true 时开放。"""
    if not AUTH_DEBUG:
        raise HTTPException(status_code=404, detail="调试接口未启用")

    email = request.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="请输入有效的邮箱地址")

    # 如果邮箱未注册，自动创建一个测试用户
    user = get_user_by_email(email)
    if user is None:
        create_user(email.split("@")[0], email, "Test1234", "user")
        print(f"[DEBUG] 自动创建测试用户: {email}")

    # 生成并保存验证码
    code = generate_verify_code()
    save_verify_code(email, code)

    # 直接返回验证码（不发送邮件）
    print(f"[DEBUG] 验证码 for {email}: {code}")

    return {
        "status": "success",
        "message": f"验证码已生成（调试模式）",
        "debug_code": code,
        "expire_minutes": VERIFY_CODE_EXPIRE_MINUTES,
    }



@auth_router.post("/login-by-code")
def login_by_code(request: LoginByCodeRequest):
    """邮箱验证码登录"""
    email = request.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="请输入有效的邮箱地址")
    if not request.code or len(request.code) != VERIFY_CODE_LENGTH:
        raise HTTPException(status_code=400, detail=f"请输入{VERIFY_CODE_LENGTH}位验证码")

    # 验证验证码（仅校验，不消耗）
    is_valid, code_id = verify_code(email, request.code)
    if not is_valid:
        raise HTTPException(status_code=400, detail="验证码无效或已过期，请重新获取")

    # 查找用户
    user = get_user_by_email(email)
    if user is None:
        raise HTTPException(status_code=400, detail="该邮箱未注册，请先注册账号")

    # 生成 token
    access_token = create_access_token(user["id"], user["username"], user["role"])
    refresh_token, expires_at = create_refresh_token(user["id"], request.remember)

    # 保存 refresh token
    save_refresh_token(refresh_token, user["id"], expires_at)

    # 清理过期 token
    cleanup_expired_tokens()

    # 所有操作成功后，标记验证码为已使用（防止中间失败导致验证码被吞）
    if code_id is not None:
        consume_verify_code(code_id)

    return {
        "status": "success",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": user,
    }



@auth_router.post("/refresh")
def refresh_token(request: RefreshRequest):
    """用 refresh token 换取新的 access token（token 旋转）"""
    if not request.refresh_token:
        raise HTTPException(status_code=400, detail="缺少 refresh_token 参数")

    # 验证 refresh token 有效性
    token_info = is_refresh_token_valid(request.refresh_token)
    if token_info is None:
        raise HTTPException(status_code=401, detail="refresh token 无效或已过期")

    # 查找用户
    user = get_user_by_id(token_info["user_id"])
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在或已被删除")

    # 撤销旧 token（旋转策略）
    revoke_refresh_token(request.refresh_token)

    # 生成新 token
    access_token = create_access_token(user["id"], user["username"], user["role"])
    new_refresh_token, expires_at = create_refresh_token(user["id"])

    # 保存新 refresh token
    save_refresh_token(new_refresh_token, user["id"], expires_at)

    return {
        "status": "success",
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": user,
    }


@auth_router.post("/logout")
def logout(
    request: LogoutRequest,
    current_user: dict = Depends(get_current_user),
):
    """退出登录，撤销 refresh token"""
    if request.refresh_token:
        revoke_refresh_token(request.refresh_token)

    return {
        "status": "success",
        "message": "已退出登录",
    }


@auth_router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return {
        "status": "success",
        "user": current_user,
    }


@auth_router.get("/email-config-check")
def check_email_config():
    """检查邮箱配置是否完整；不会返回授权码。"""
    return {
        "status": "success",
        "smtp_configured": is_smtp_configured(),
        "smtp_host": SMTP_HOST,
        "smtp_port": SMTP_PORT,
        "smtp_use_ssl": SMTP_USE_SSL,
        "smtp_user_masked": mask_email(SMTP_USER),
        "smtp_from_masked": mask_email(SMTP_FROM),
        "auth_debug": AUTH_DEBUG,
        "authorization_code_loaded": bool(SMTP_PASS),
    }


@auth_router.get("/check")
def check_auth_setup():
    """检查认证模块是否正常初始化（诊断用）"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 检查 users 表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        has_users_table = cursor.fetchone() is not None

        # 检查 admin 用户是否存在
        admin_count = 0
        total_users = 0
        if has_users_table:
            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
            admin_count = cursor.fetchone()[0]

        conn.close()

        return {
            "status": "success",
            "auth_ready": has_users_table and total_users > 0,
            "has_users_table": has_users_table,
            "total_users": total_users,
            "admin_count": admin_count,
        }
    except Exception as e:
        return {
            "status": "error",
            "auth_ready": False,
            "detail": str(e),
        }


