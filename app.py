# =============================================================================
# Googleフォーム × Flask × LINE Messaging API 連携アプリ（完成版）
#
# 概要：
# - GAS(Apps Script) → Webhook で Googleフォーム回答を Flask に送信してDB保存
# - ダッシュボード（全体 / 個人）を表示
# - LINE Webhook で友だち追加の瞬間に、個別フォームURL＆個別ダッシュボードURLを返信
#
# 重要な .env 例：
#   DATABASE_URL=sqlite:///instance/local.db
#   WEBHOOK_TOKEN=SHARED_SECRET_123
#   LINE_CHANNEL_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#   LINE_CHANNEL_ACCESS_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#   FORM_BASE_URL="https://docs.google.com/forms/d/e/XXXX/viewform?usp=pp_url"
#   FORM_ENTRY_ID="entry.1391493516"        # 「ユーザーID」設問の entry.<数字>
#   APP_BASE_URL=http://localhost:8000      # 外部公開時は ngrok の https を設定！
#
# 注意：
# - localhost のURLは「自分のPCからのみ閲覧可」。他の人にLINEで共有する場合は
#   APP_BASE_URL を ngrok等の https URL にすること。
# =============================================================================

from __future__ import annotations

import os
import json
import hmac
import base64
import hashlib
import secrets
import requests
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from flask import Flask, request, abort, jsonify, render_template, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func  # 将来的な集計で使用可能（今は未必須）
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import pytz
from sqlalchemy.orm import relationship, joinedload

# -----------------------------------------------------------------------------
# 環境変数ロード
# -----------------------------------------------------------------------------
load_dotenv()

DATABASE_URL              = os.getenv("DATABASE_URL", "sqlite:///local.db")
WEBHOOK_TOKEN             = os.getenv("WEBHOOK_TOKEN", "SHARED_SECRET_123")
LINE_CHANNEL_SECRET       = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
FORM_BASE_URL             = (os.getenv("FORM_BASE_URL", "") or "").strip()
FORM_ENTRY_ID             = (os.getenv("FORM_ENTRY_ID", "") or "").strip()
APP_BASE_URL              = (os.getenv("APP_BASE_URL", "http://localhost:8000") or "").strip()

JST = ZoneInfo("Asia/Tokyo")

# Googleフォームの設問タイトル（namedValues のキーに一致）
USER_TOKEN_LABEL = "ユーザーID"
QUESTIONS: List[str] = [
    "Q1. 心配事のために睡眠時間が減ったことはありますか？",
    "Q2. いつも緊張していますか？",
    "Q3. ものごとに集中できますか？",
    "Q4. 何か有益な役割を果たしていると思いますか？",
    "Q5. 自分の問題について立ち向かうことができますか？",
    "Q6. 物事について決断できると思いますか？",
    "Q7. いろんな問題を解決できなくて困りますか？",
    "Q8. 全般的にまあ満足していますか？",
    "Q9. 日常生活を楽しむことができますか？",
    "Q10. 不幸せで憂うつと感じますか？",
    "Q11. 自信をなくしますか？",
    "Q12. 自分は役にたたない人間だと感じることがありますか？",
]
QUESTION_TO_INDEX: Dict[str, int] = {q: i + 1 for i, q in enumerate(QUESTIONS)}

# -----------------------------------------------------------------------------
# Flask / DB
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = "your_secret_key_here"
db = SQLAlchemy(app)

# -----------------------------------------------------------------------------
# モデル
# -----------------------------------------------------------------------------
class User(db.Model):
    """メンバー。LINEの userId と、フォーム識別用 external_token を保持"""
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(255))
    external_token = db.Column(db.String(64), unique=True, index=True, nullable=False)
    line_user_id = db.Column(db.String(64), unique=True)
    posts = relationship(
        "Post",
        back_populates="user",
        foreign_keys="Post.user_id",
        lazy="dynamic",
    )

class FormResponse(db.Model):
    """フォーム回答（1送信=1レコード）"""
    __tablename__ = "form_responses"
    id = db.Column(db.Integer, primary_key=True)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    user = db.relationship("User", backref="responses")

    # 12問、全て NOT NULL
    Q1  = db.Column(db.String, nullable=False)
    Q2  = db.Column(db.String, nullable=False)
    Q3  = db.Column(db.String, nullable=False)
    Q4  = db.Column(db.String, nullable=False)
    Q5  = db.Column(db.String, nullable=False)
    Q6  = db.Column(db.String, nullable=False)
    Q7  = db.Column(db.String, nullable=False)
    Q8  = db.Column(db.String, nullable=False)
    Q9  = db.Column(db.String, nullable=False)
    Q10 = db.Column(db.String, nullable=False)
    Q11 = db.Column(db.String, nullable=False)
    Q12 = db.Column(db.String, nullable=False)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(50), nullable=False)
    body  = db.Column(db.String(300), nullable=False)
    # callableにして毎回“今”が入るように（import時固定を防ぐ）
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(pytz.timezone("Asia/Tokyo")))
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = relationship(
        "User",
        back_populates="posts",
        foreign_keys=[user_id],
    )

# -----------------------------------------------------------------------------
# ユーティリティ
# -----------------------------------------------------------------------------
def to_jst(dt: datetime | None) -> datetime | None:
    """tz情報無しはUTCとみなしてJSTに変換"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST)

def parse_iso8601_z(s: str | None) -> datetime:
    """ISO8601文字列（末尾Z可）→ tz付きdatetime(UTC)"""
    if not s:
        return datetime.now(timezone.utc)
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def answer_point(s: str | None) -> int:
    """回答の先頭 '1.' '2.' は0点、'3.' '4.' は1点"""
    if not s:
        return 0
    s = s.strip()
    return 0 if s.startswith("1.") or s.startswith("2.") else 1

def total_score_row(rec: FormResponse) -> int:
    """1回答の合計点（0〜12）"""
    return sum(answer_point(getattr(rec, f"Q{i}")) for i in range(1, 13))

def status_label(score: int) -> str:
    """簡易ラベル"""
    if score <= 1:
        return "とても健康です！"
    elif 2 <= score <= 3:
        return "少し休みましょう！"
    else:
        return "休息が必要です！"

def issue_external_token() -> str:
    """フォーム識別用のランダムトークン発行"""
    return secrets.token_urlsafe(12)

def risk_level(score: int) -> str:
    """色分け段階（low/mid/high）"""
    if score <= 1:
        return "low"
    elif 2 <= score <= 3:
        return "mid"
    else:
        return "high"

def risk_color_hex(score: int) -> str:
    """スコア→色（Chart.js用HEX）"""
    if score >= 4:
        return "#ef4444"  # red-500
    elif 2 <= score <= 3:
        return "#f59e0b"  # amber-500
    else:
        return "#10b981"  # emerald-500

def status_icon(score: int) -> str:
    """状態を表す軽いアイコン（必要なら画像に置き換え可）"""
    if score <= 1:
        return "😊"
    elif 2 <= score <= 3:
        return "😐"
    else:
        return "😰"

def compute_login_ranking(top_n: int = 3, lookback_days: int = 14):
    """
    直近 lookback_days 日の『利用日数』（同日複数回答は1）ランキング。
    返却: [{display_name, user_id, days}, ...] を days 降順・同率は名前昇順。
    """
    since_utc = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    days_by_user: dict[int, set[str]] = defaultdict(set)

    rows = (FormResponse.query
            .filter(FormResponse.submitted_at >= since_utc)
            .order_by(FormResponse.user_id.asc(),
                      FormResponse.submitted_at.desc(),
                      FormResponse.id.desc())
            .all())
    for r in rows:
        jst_day = to_jst(r.submitted_at).date().isoformat()
        days_by_user[r.user_id].add(jst_day)

    results = []
    users = {u.id: u for u in User.query.all()}
    for uid, days in days_by_user.items():
        u = users.get(uid)
        results.append({
            "user_id": uid,
            "display_name": (u.display_name if u and u.display_name else "未設定"),
            "days": len(days),
        })
    results.sort(key=lambda x: (-x["days"], x["display_name"]))
    return results[:top_n]

def build_users_overview() -> List[Dict[str, Any]]:
    """全ユーザーの直近1回答をカード用に整形（リスク順）"""
    overview: List[Dict[str, Any]] = []
    for u in User.query.order_by(User.id.asc()).all():
        r = (FormResponse.query
             .filter_by(user_id=u.id)
             .order_by(FormResponse.submitted_at.desc(), FormResponse.id.desc())
             .first())
        if not r:
            overview.append({
                "display_name": u.display_name or "未設定",
                "external_token": u.external_token,
                "latest_score": None,
                "latest_status": "未回答",
                "latest_at": "-",
                "risk": "none",
            })
            continue
        score = total_score_row(r)
        overview.append({
            "display_name": u.display_name or "未設定",
            "external_token": u.external_token,
            "latest_score": score,
            "latest_status": status_label(score),
            "latest_at": to_jst(r.submitted_at).strftime("%Y-%m-%d %H:%M:%S"),
            "risk": risk_level(score),
        })
    order_key = {"high": 0, "mid": 1, "low": 2, "none": 3}
    overview.sort(key=lambda x: order_key.get(x["risk"], 9))
    return overview

def build_own_users_overview(user_id: int) -> List[Dict[str, Any]]:
    """特定ユーザーの直近1回答だけをカード化（owner/user個別ページ上部用）"""
    u = User.query.get(user_id)
    if not u:
        return []
    r = (FormResponse.query
         .filter_by(user_id=u.id)
         .order_by(FormResponse.submitted_at.desc(), FormResponse.id.desc())
         .first())
    if not r:
        return [{
            "display_name": u.display_name or "未設定",
            "external_token": u.external_token,
            "latest_score": None,
            "latest_status": "未回答",
            "latest_at": "-",
            "risk": "none",
        }]
    score = total_score_row(r)
    return [{
        "display_name": u.display_name or "未設定",
        "external_token": u.external_token,
        "latest_score": score,
        "latest_status": status_label(score),
        "latest_at": to_jst(r.submitted_at).strftime("%Y-%m-%d %H:%M:%S"),
        "risk": risk_level(score),
    }]

# -----------------------------------------------------------------------------
# LINE（プロフィール取得 / push / reply）
# -----------------------------------------------------------------------------
def get_line_profile(user_id: str) -> dict | None:
    """LINEのプロフィール（displayName 等）を取得"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("WARN: LINE_CHANNEL_ACCESS_TOKEN 未設定のためプロフィール取得不可")
        return None
    url = f"https://api.line.me/v2/bot/profile/{user_id}"
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            return res.json()
        print("LINE profile error:", res.status_code, res.text)
    except Exception as e:
        print("LINE profile request failed:", e)
    return None

def line_push_text(to_user_id: str, text: str) -> None:
    """pushメッセージ送信"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN が未設定です")
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {"to": to_user_id, "messages": [{"type": "text", "text": text}]}
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"push error {r.status_code}: {r.text}")

def line_reply_text(reply_token: str, text: str) -> None:
    """replyメッセージ送信（友だち追加の瞬間はreplyが最も確実）"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN が未設定です")
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"reply error {r.status_code}: {r.text}")

# -----------------------------------------------------------------------------
# Webhook（GAS → Flask）
# -----------------------------------------------------------------------------
@app.route("/api/forms/google", methods=["POST"])
def receive_google_form():
    """Apps Script からの Webhook を受け取り回答保存"""
    if request.headers.get("X-Webhook-Token") != WEBHOOK_TOKEN:
        abort(401, "invalid token")

    data: Dict[str, Any] = request.get_json(silent=True) or {}
    named: Dict[str, List[str]] = data.get("responses") or {}
    submitted_at = parse_iso8601_z(data.get("submitted_at"))

    # external_token でユーザー特定
    token = (named.get(USER_TOKEN_LABEL) or [""])[0].strip()
    if not token:
        abort(400, "user token missing")
    user = User.query.filter_by(external_token=token).one_or_none()
    if not user:
        abort(400, "unknown user token")

    # Q1..Q12 へ詰め替え
    values: Dict[str, str] = {}
    for question_text, answers in named.items():
        idx = QUESTION_TO_INDEX.get(question_text)
        if not idx:
            continue
        if isinstance(answers, list):
            if len(answers) != 1:
                abort(400, f"単一選択のみ許可: {question_text}")
            ans_text = answers[0]
        else:
            ans_text = str(answers)
        values[f"Q{idx}"] = ans_text.strip()

    missing = [f"Q{i}" for i in range(1, 13) if not values.get(f"Q{i}")]
    if missing:
        abort(400, f"必須回答が不足: {', '.join(missing)}")

    rec = FormResponse(user_id=user.id, submitted_at=submitted_at, **values)
    db.session.add(rec)
    db.session.commit()
    return jsonify({"ok": True, "id": rec.id})

# -----------------------------------------------------------------------------
# 画面（全体 / 個人）
# -----------------------------------------------------------------------------
def _build_view_context(rows: list, title: str, user_name: str | None):
    """
    折れ線用データ・最新回答明細をテンプレに渡す形へ整形。
    同一JST日では「その日の最新回答のみ」を採用。
    """
    # 同一日に複数回答があっても最新のみ採用
    latest_by_day: "OrderedDict[str, FormResponse]" = OrderedDict()
    for r in rows:
        jst_day = to_jst(r.submitted_at).date().isoformat()
        if jst_day not in latest_by_day:
            latest_by_day[jst_day] = r

    chart_labels = sorted(latest_by_day.keys())
    chart_values = [total_score_row(latest_by_day[d]) for d in chart_labels]
    chart_point_colors = [risk_color_hex(v) for v in chart_values]

    latest_rec = rows[0] if rows else None
    latest_score = total_score_row(latest_rec) if latest_rec else 0
    latest_status = status_label(latest_score)
    latest_icon = status_icon(latest_score)
    latest_at = (to_jst(latest_rec.submitted_at).strftime("%Y-%m-%d %H:%M:%S")
                 if latest_rec else None)

    latest_answers = [
        {
            "code": f"Q{i}",
            "answer": getattr(latest_rec, f"Q{i}") if latest_rec else "",
            "point": answer_point(getattr(latest_rec, f"Q{i}") if latest_rec else None),
        }
        for i in range(1, 13)
    ]

    return {
        "latest_score": latest_score,
        "latest_status": latest_status,
        "latest_icon": latest_icon,
        "latest_at": latest_at,
        "latest_answers": latest_answers,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "chart_point_colors": chart_point_colors,
        "page_title": title,
        "user_name": user_name,
        # 画像のキャッシュ破棄用（スマホ/LINE WebView対策）
        "asset_ver": str(int(datetime.now().timestamp())),
    }

@app.route("/")
def index():
    """全体ダッシュボード"""
    rows = (FormResponse.query
            .order_by(FormResponse.submitted_at.desc(), FormResponse.id.desc())
            .all())
    ctx = _build_view_context(rows, "全体ダッシュボード", None)
    ctx["users_overview"] = build_users_overview()
    return render_template("index.html", **ctx)

@app.route("/user/<token>")
def user_dashboard(token: str):
    """本人用ダッシュボード（個別）"""
    user = User.query.filter_by(external_token=token).one_or_none()
    if not user:
        abort(404, "user not found")
    rows = (FormResponse.query
            .filter_by(user_id=user.id)
            .order_by(FormResponse.submitted_at.desc(), FormResponse.id.desc())
            .all())
    ctx = _build_view_context(rows, f"{user.display_name or 'ユーザー'} のダッシュボード", user.display_name)
    ctx["login_ranking"] = compute_login_ranking(top_n=3, lookback_days=14)
    ctx["users_overview"] = build_own_users_overview(user_id=user.id)  # 必要なら表示
    return render_template("index_for_user.html", **ctx)

#なりすまし防止　IDの確認をしている
@app.route("/user/board/<external_token>")
def user_entry(external_token):
    user = User.query.filter_by(external_token=external_token).first()
    if not user:
        abort(404)
    # このユーザーをセッションに登録
    session["user_id"] = user.id
    session["user_name"] = user.display_name
    # 掲示板へ飛ばす
    return redirect(url_for("board"))
    
#掲示板
@app.route("/board", methods=["GET", "POST"])
def board():
    uid = session.get("user_id")
    if not uid:
        return "ユーザー情報がありません。入口リンクから入り直してください。", 401
    user = User.query.get(uid)
    if request.method == "POST":
        # 投稿データを受け取る
        title = (request.form.get("title") or "").strip()
        body  = (request.form.get("body")  or "").strip()
        if not title or not body:
            return "タイトルと本文は必須です", 400
        # user_idをセットして保存
        post = Post(title=title, body=body, user_id=uid)
        db.session.add(post)
        db.session.commit()
        return redirect(url_for("board"))
    # GET: 一覧表示
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template("board.html", posts=posts, display_name=user.display_name)


@app.route("/owner/<token>", endpoint="user_dashboard_v2")
def owner_dashboard(token: str):
    """
    管理者が共有する「owner版」個別ページ。
    user版と同機能だが、テンプレ側で“全体へ戻る”導線を表示する想定。
    """
    user = User.query.filter_by(external_token=token).one_or_none()
    if not user:
        abort(404, "user not found")
    rows = (FormResponse.query
            .filter_by(user_id=user.id)
            .order_by(FormResponse.submitted_at.desc(), FormResponse.id.desc())
            .all())
    ctx = _build_view_context(rows, f"{user.display_name or 'ユーザー'} のダッシュボード", user.display_name)
    ctx["login_ranking"] = compute_login_ranking(top_n=3, lookback_days=14)
    ctx["users_overview"] = build_own_users_overview(user_id=user.id)
    return render_template("index_for_owner.html", **ctx)

@app.route("/healthz")
def healthz():
    return "ok", 200

# -----------------------------------------------------------------------------
# LINE Webhook（userId 取得・登録・URL返信）
# -----------------------------------------------------------------------------
@app.route("/callback", methods=["POST"])
def callback():
    """
    LINEプラットフォームからのWebhook。
    - 署名検証
    - userId をキーにユーザー作成/更新（external_token 発行、displayName 取得）
    - フォームURL（プレフィル）とダッシュボードURLを返信
      * 友だち追加(follow)は reply を最優先、それ以外は push
    """
    # --- 署名検証 ---
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    mac = hmac.new(LINE_CHANNEL_SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    if not hmac.compare_digest(signature, expected):
        abort(400, "invalid signature")

    try:
        data = json.loads(body)
    except Exception:
        abort(400, "invalid body json")

    events = data.get("events", [])
    if not events:
        return "OK"

    form_base = (os.getenv("FORM_BASE_URL", "") or "").strip()
    entry_id  = (os.getenv("FORM_ENTRY_ID", "") or "").strip()
    app_base  = (os.getenv("APP_BASE_URL", APP_BASE_URL) or "").strip()

    for ev in events:
        etype = ev.get("type")
        src   = ev.get("source", {})
        if src.get("type") != "user":
            continue  # group/roomはスキップ

        user_id     = src.get("userId")
        reply_token = ev.get("replyToken")
        if not user_id:
            continue

        # --- DBユーザー確保 ---
        user = User.query.filter_by(line_user_id=user_id).one_or_none()
        if user is None:
            token = issue_external_token()
            prof  = get_line_profile(user_id)
            name  = (prof or {}).get("displayName") or "未設定"
            user = User(display_name=name, line_user_id=user_id, external_token=token)
            db.session.add(user)
            db.session.commit()
        else:
            if not user.display_name or user.display_name == "未設定":
                prof = get_line_profile(user_id)
                if prof and prof.get("displayName"):
                    user.display_name = prof["displayName"]
                    db.session.commit()
            if not user.external_token:
                user.external_token = issue_external_token()
                db.session.commit()

        # --- URL作成 ---
        if form_base and entry_id:
            sep = "&" if "?" in form_base else "?"
            form_url = f"{form_base}{sep}{entry_id}={user.external_token}"
        else:
            form_url = None
        dashboard_url = f"{app_base}/user/{user.external_token}"

        # --- 返信メッセージ ---
        if form_url:
            msg = (
                f"{user.display_name or 'こんにちは'} さん、以下をご利用ください👇\n\n"
                f"📋 日次フォーム\n{form_url}\n\n"
                f"📊 あなたのダッシュボード\n{dashboard_url}\n\n"
                "※ フォームの『ユーザーID』欄は自動入力されます。変更せず送信してください。"
            )
        else:
            msg = (
                f"{user.display_name or 'こんにちは'} さん、あなたのダッシュボードはこちら👇\n"
                f"{dashboard_url}\n\n"
                "（フォームURLは未設定です。管理者に連絡してください）"
            )

        # --- 送信 ---
        try:
            if etype == "follow" and reply_token:
                line_reply_text(reply_token, msg)  # 友だち追加時はreplyが最も確実
            else:
                line_push_text(user_id, msg)
        except Exception as e:
            print("LINE send error:", e)

    return "OK"

# -----------------------------------------------------------------------------
# 手動登録（デバッグ用）
# -----------------------------------------------------------------------------
@app.route("/register_line_user", methods=["POST"])
def register_line_user():
    """line_user_id を直接登録して external_token を払い出す簡易API"""
    data = request.get_json()
    name = data.get("name")
    line_user_id = data.get("line_user_id")
    if not line_user_id:
        abort(400, "line_user_id が必要です")

    existing = User.query.filter_by(line_user_id=line_user_id).first()
    if existing:
        return jsonify({"ok": True, "msg": "既に登録済み",
                        "id": existing.id, "external_token": existing.external_token})

    token = issue_external_token()
    display_name = name
    if not display_name:
        prof = get_line_profile(line_user_id)
        if isinstance(prof, dict) and prof.get("displayName"):
            display_name = prof["displayName"]
    if not display_name:
        display_name = "未設定"

    u = User(display_name=display_name, line_user_id=line_user_id, external_token=token)
    db.session.add(u)
    db.session.commit()
    return jsonify({"ok": True, "id": u.id, "external_token": token})

# -----------------------------------------------------------------------------
# エントリポイント
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # 既存が無いときのみ作成
    # ローカル確認： http://localhost:8000
    app.run(host="0.0.0.0", port=8000, debug=True)
