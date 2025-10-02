# =============================================================================
# Googleフォーム × Flask × LINE Messaging API 連携アプリ
#
# 追加した機能
# - LINE の follow（友だち追加）イベント受信で、個別のプレフィルURLを即時 push 送信
# - APScheduler（JST）で毎日 9:00 に登録メンバー全員へフォームURLを一斉送信
#
# できること（全体）
# - Apps Script → Webhook で Googleフォーム回答を受信しDB保存（ユーザー別）
# - 全体ダッシュボード（/）と人別ダッシュボード（/user/<external_token>）
# - LINEのWebhook (/callback) で userId を受け取り、初回時に
#   1) external_token を発行
#   2) LINEプロフィール(displayName) を取得して表示名に反映
#   3) 個人プレフィルURLをpushで返信（FORM_BASE_URL / FORM_ENTRY_ID 必要）
# - 毎日 9:00(JST) の自動一斉送信（APScheduler）
#
# 必要な環境変数（.env など）
# - DATABASE_URL=sqlite:///instance/local.db       # 絶対パス推奨
# - WEBHOOK_TOKEN=SHARED_SECRET_123                # GAS→Flask の簡易認証
# - LINE_CHANNEL_SECRET=...                        # Messaging APIのチャネルシークレット
# - LINE_CHANNEL_ACCESS_TOKEN=...                  # 同 アクセストークン
# - FORM_BASE_URL="https://docs.google.com/forms/d/e/XXXX/viewform?usp=pp_url"
# - FORM_ENTRY_ID="entry.1391493516"               # 「ユーザーID」設問の entry.<数字>
# - ENABLE_SCHEDULER=1                              # (任意) 自動送信ONにする（デフォルト1）
#
# 開発の初期化
# - 初期スキーマ変更時は SQLite の DB を削除して再生成（開発時のみ）
# =============================================================================

from __future__ import annotations

import os
import json
import hmac
import base64
import hashlib
import secrets
import requests
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List

from flask import Flask, request, abort, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# APScheduler（毎日9時の自動送信用）
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# -----------------------------------------------------------------------------
# 環境変数
# -----------------------------------------------------------------------------
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "SHARED_SECRET_123")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
FORM_BASE_URL = os.getenv("FORM_BASE_URL", "").strip()
FORM_ENTRY_ID = os.getenv("FORM_ENTRY_ID", "").strip()
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "1")  # "1" のときだけ有効化

JST = ZoneInfo("Asia/Tokyo")

# Googleフォーム側の設問文（namedValues のキーと一致させる）
USER_TOKEN_LABEL = "ユーザーID"  # フォームに追加した短答式の設問ラベル

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
# Flask / DB 初期化
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# -----------------------------------------------------------------------------
# モデル
# -----------------------------------------------------------------------------
class User(db.Model):
    """研究室メンバー等のユーザー。
    - external_token: 各人固有のトークン（GoogleフォームのプレフィルURLに埋め込む）
    - line_user_id  : LINEの userId（任意、取得できた人のみ）
    - display_name  : 表示名（LINEプロフィールのdisplayNameを初回時に反映）
    """
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(255))
    external_token = db.Column(db.String(64), unique=True, index=True, nullable=False)
    line_user_id = db.Column(db.String(64), unique=True)

class FormResponse(db.Model):
    """Googleフォームからの1回答（ユーザーと紐づく）。"""
    __tablename__ = "form_responses"
    id = db.Column(db.Integer, primary_key=True)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    user = db.relationship("User", backref="responses")

    # 12問すべてNOT NULL
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

# -----------------------------------------------------------------------------
# ユーティリティ
# -----------------------------------------------------------------------------
def to_jst(dt: datetime | None) -> datetime | None:
    """DBの日時（tzなしならUTCと仮定）をJSTに変換。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST)

def parse_iso8601_z(s: str | None) -> datetime:
    """ISO8601（末尾Z可）をUTCのdatetimeにする。"""
    if not s:
        return datetime.now(timezone.utc)
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def answer_point(s: str | None) -> int:
    """回答先頭の '1.' '2.' は0点、それ以外（'3.' '4.'）は1点。"""
    if not s:
        return 0
    s = s.strip()
    return 0 if s.startswith("1.") or s.startswith("2.") else 1

def total_score_row(rec: FormResponse) -> int:
    """1回答の合計点（0〜12）。"""
    return sum(answer_point(getattr(rec, f"Q{i}")) for i in range(1, 13))

def status_label(score: int) -> str:
    """簡易ラベル（閾値は暫定）。"""
    if score <= 1:
        return "とても健康です！"
    elif 2 <= score <= 3:
        return "少し休みましょう！"
    else:
        return "休息が必要です！"

def issue_external_token() -> str:
    """URLセーフで十分長いランダムトークンを発行（推測困難）。"""
    return secrets.token_urlsafe(12)

def risk_level(score: int) -> str:
    """色分け用のリスク段階。"""
    if score <= 1:
        return "low"   # 緑
    elif 2 <= score <= 3:
        return "mid"   # 黄
    else:
        return "high"  # 赤

def build_users_overview() -> List[Dict[str, Any]]:
    """全ユーザーの直近1件を集計してカード用データを返す（リスク順ソート）。"""
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

    # “やばい順” に並べる
    order_key = {"high": 0, "mid": 1, "low": 2, "none": 3}
    overview.sort(key=lambda x: order_key.get(x["risk"], 9))
    return overview

# --- 便利: 個別プレフィルURLを作る ---
def build_prefilled_url(token: str) -> str | None:
    if not FORM_BASE_URL or not FORM_ENTRY_ID:
        return None
    sep = "&" if "?" in FORM_BASE_URL else "?"
    return f"{FORM_BASE_URL}{sep}{FORM_ENTRY_ID}={token}"

# -----------------------------------------------------------------------------
# LINE ユーティリティ（プロフィール取得 / push 送信）
# -----------------------------------------------------------------------------
def get_line_profile(user_id: str) -> dict | None:
    """LINEのプロフィール（displayName 等）を取得。"""
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
    """ユーザーにテキストメッセージをpush送信。"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN が未設定です")
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "to": to_user_id,
        "messages": [{"type": "text", "text": text}],
    }
    res = requests.post(url, headers=headers, json=payload, timeout=10)
    if res.status_code != 200:
        raise RuntimeError(f"push error {res.status_code}: {res.text}")

# -----------------------------------------------------------------------------
# Webhook（Googleフォーム → Flask）
# -----------------------------------------------------------------------------
@app.route("/api/forms/google", methods=["POST"])
def receive_google_form():
    """Apps Script からの Webhook を受け取り、回答を保存。"""
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

    # Q1..Q12 に詰め替え
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

    # 必須チェック
    missing = [f"Q{i}" for i in range(1, 13) if not values.get(f"Q{i}")]
    if missing:
        abort(400, f"必須回答が不足: {', '.join(missing)}")

    # 保存
    rec = FormResponse(user_id=user.id, submitted_at=submitted_at, **values)
    db.session.add(rec)
    db.session.commit()
    return jsonify({"ok": True, "id": rec.id})

# -----------------------------------------------------------------------------
# 画面（全体 / 人別）
# -----------------------------------------------------------------------------
def _build_view_context(rows: List[FormResponse], title: str, user_name: str | None):
    """グラフ・最新回答の明細・ヘッダ情報をテンプレ用に整形。"""
    latest_by_day: "OrderedDict[str, FormResponse]" = OrderedDict()
    for r in rows:
        jst_day = to_jst(r.submitted_at).date().isoformat()
        if jst_day not in latest_by_day:
            latest_by_day[jst_day] = r

    chart_labels = sorted(latest_by_day.keys())
    chart_values = [total_score_row(latest_by_day[d]) for d in chart_labels]

    latest_rec = rows[0] if rows else None
    latest_score = total_score_row(latest_rec) if latest_rec else 0
    latest_status = status_label(latest_score)
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

    return dict(
        latest_score=latest_score,
        latest_status=latest_status,
        latest_at=latest_at,
        latest_answers=latest_answers,
        chart_labels=chart_labels,
        chart_values=chart_values,
        page_title=title,
        user_name=user_name,
    )

@app.route("/")
def index():
    rows = (FormResponse.query
            .order_by(FormResponse.submitted_at.desc(), FormResponse.id.desc())
            .all())
    ctx = _build_view_context(rows, "全体ダッシュボード", None)
    ctx["users_overview"] = build_users_overview()  # 上段カード用
    return render_template("index.html", **ctx)

@app.route("/user/<token>")
def user_dashboard(token: str):
    user = User.query.filter_by(external_token=token).one_or_none()
    if not user:
        abort(404, "user not found")
    rows = (FormResponse.query
            .filter_by(user_id=user.id)
            .order_by(FormResponse.submitted_at.desc(), FormResponse.id.desc())
            .all())
    ctx = _build_view_context(rows, f"{user.display_name or 'ユーザー'} のダッシュボード", user.display_name)
    return render_template("index.html", **ctx)

@app.route("/healthz")
def healthz():
    return "ok", 200

# -----------------------------------------------------------------------------
# LINE Webhook（followで即送信、messageで案内）
# -----------------------------------------------------------------------------
@app.route("/callback", methods=["POST"])
def callback():
    """LINEプラットフォームからのWebhook。
    - 署名検証
    - follow: userId 登録＆ external_token 発行 → 個別URLを即 push
    - message: 該当ユーザーが居ればURLを返す（任意の補助対応）
    """
    # --- 署名検証（必須） ---
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    mac = hmac.new(LINE_CHANNEL_SECRET.encode("utf-8"),
                   body.encode("utf-8"),
                   hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    if not hmac.compare_digest(signature, expected):
        abort(400, "invalid signature")

    # --- イベント処理 ---
    data = json.loads(body)
    events = data.get("events", [])
    if not events:
        return "OK"

    for ev in events:
        etype = ev.get("type")
        src = ev.get("source", {})
        if src.get("type") != "user":  # 1:1トークのみ対象（group/roomは除外）
            continue
        user_id = src.get("userId")
        if not user_id:
            continue

        # ユーザーの確保
        user = User.query.filter_by(line_user_id=user_id).one_or_none()
        if user is None:
            token = issue_external_token()
            profile = get_line_profile(user_id)
            display_name = profile.get("displayName") if profile else "未設定"
            user = User(display_name=display_name,
                        line_user_id=user_id,
                        external_token=token)
            db.session.add(user)
            db.session.commit()
        else:
            # 表示名の補正／external_tokenの穴埋め
            if (not user.display_name) or (user.display_name == "未設定"):
                profile = get_line_profile(user_id)
                if profile and profile.get("displayName"):
                    user.display_name = profile["displayName"]
                    db.session.commit()
            if not user.external_token:
                user.external_token = issue_external_token()
                db.session.commit()

        # 個別URL
        url = build_prefilled_url(user.external_token)

        # follow: 友だち追加の瞬間に送る
        if etype == "follow" and url:
            msg = (
                f"{user.display_name or 'こんにちは'} さん、友だち追加ありがとう！\n"
                "毎日のフォームはこちらです👇（ユーザーIDは自動入力済み）\n"
                f"{url}"
            )
            try:
                line_push_text(user_id, msg)
            except Exception as e:
                print("push error:", e)

        # 任意: message受信時にも案内（url があるときのみ）
        if etype == "message" and url:
            try:
                line_push_text(user_id, f"本日のフォームはこちらです👇\n{url}")
            except Exception as e:
                print("push error:", e)

    return "OK"

# -----------------------------------------------------------------------------
# 日次9時の自動配信（APScheduler）
# -----------------------------------------------------------------------------
def send_daily_forms():
    """DBに登録済み（line_user_id がある）ユーザー全員へ、毎日9時にURLを配布。"""
    users = User.query.filter(User.line_user_id.isnot(None)).all()
    sent, skipped = 0, 0
    for u in users:
        if not u.external_token or not u.line_user_id:
            skipped += 1
            continue
        url = build_prefilled_url(u.external_token)
        if not url:
            skipped += 1
            continue
        msg = (
            f"{u.display_name or 'おはようございます'} さん、おはようございます！\n"
            "本日のフォームはこちら👇\n"
            f"{url}"
        )
        try:
            line_push_text(u.line_user_id, msg)
            sent += 1
        except Exception as e:
            print("daily push error:", e)
            skipped += 1
    print(f"[daily_push] sent={sent}, skipped={skipped}, at={datetime.now(JST)}")

def start_scheduler_if_needed():
    """開発サーバのリロード二重起動を避けつつ、APScheduler を開始。"""
    if ENABLE_SCHEDULER != "1":
        print("Scheduler disabled (ENABLE_SCHEDULER!=1)")
        return
    # Werkzeug のリローダ下では 2プロセスになるため、本体プロセスのみスケジューラ起動
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    scheduler = BackgroundScheduler(timezone=str(JST))
    # 毎日 9:00（JST）
    scheduler.add_job(send_daily_forms,
                      trigger=CronTrigger(hour=9, minute=0, second=0, timezone=JST),
                      id="daily_forms_9am_jst",
                      replace_existing=True)
    scheduler.start()
    print("APScheduler started: every day 09:00 JST")

# -----------------------------------------------------------------------------
# ユーザー手動登録API（デバッグ/代替用途）
# -----------------------------------------------------------------------------
@app.route("/register_line_user", methods=["POST"])
def register_line_user():
    """line_user_id を手動登録し、external_token を払い出す簡易API。"""
    data = request.get_json()
    name = data.get("name")
    line_user_id = data.get("line_user_id")
    if not line_user_id:
        abort(400, "line_user_id が必要です")

    existing = User.query.filter_by(line_user_id=line_user_id).first()
    if existing:
        return jsonify({
            "ok": True,
            "msg": "既に登録済み",
            "id": existing.id,
            "external_token": existing.external_token,
        })

    token = issue_external_token()
    # display_name が未指定なら LINEから引ける場合は取得
    display_name = name
    if not display_name:
        profile = get_line_profile(line_user_id)  # ここでは userId=line_user_id を想定
        if isinstance(profile, dict) and profile.get("displayName"):
            display_name = profile["displayName"]
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
        db.create_all()  # 初回はテーブル作成
        # スケジューラ起動（本体プロセスのみ）
        start_scheduler_if_needed()

    # デバッグサーバ起動
    app.run(host="0.0.0.0", port=8000, debug=True)
