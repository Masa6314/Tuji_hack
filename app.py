# =============================================================================
# Googleフォーム × Flask × LINE Messaging API 連携アプリ
#
# できること
# - Apps Script → Webhook で Googleフォーム回答を受信しDB保存（ユーザー別）
# - 全体ダッシュボード（/）と人別ダッシュボード（/user/<external_token>）
# - LINEのWebhook (/callback) で userId を受け取り、初回時に
#   1) external_token を発行
#   2) LINEプロフィール(displayName) を取得して表示名に反映
#   3) 個人プレフィルURLとダッシュボードURLを自動返信（reply/push）
#
# 重要な設定（.env 推奨）
# - DATABASE_URL=sqlite:///instance/local.db など（絶対パス推奨）
# - WEBHOOK_TOKEN=SHARED_SECRET_123               # GAS→Flask の簡易認証
# - LINE_CHANNEL_SECRET=...                       # Messaging API のチャネルシークレット
# - LINE_CHANNEL_ACCESS_TOKEN=...                 # 同 アクセストークン
# - FORM_BASE_URL="https://docs.google.com/forms/d/e/XXXX/viewform?usp=pp_url"
# - FORM_ENTRY_ID="entry.1391493516"              # ユーザーID設問の entry.<数字>
# - APP_BASE_URL=http://localhost:8000            # ★今回のご要望どおり localhost を既定値に
#
# 注意：
# - localhost のURLは **自分のPCでしか開けません**。他人にLINEで送る場合は
#   ngrok 等の外部公開URL（https）を APP_BASE_URL に設定してください。
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

# -----------------------------------------------------------------------------
# 環境変数読み込み
# -----------------------------------------------------------------------------
load_dotenv()

DATABASE_URL           = os.getenv("DATABASE_URL", "sqlite:///local.db")
WEBHOOK_TOKEN          = os.getenv("WEBHOOK_TOKEN", "SHARED_SECRET_123")
LINE_CHANNEL_SECRET    = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
FORM_BASE_URL          = (os.getenv("FORM_BASE_URL", "") or "").strip()
FORM_ENTRY_ID          = (os.getenv("FORM_ENTRY_ID", "") or "").strip()

# ★ご要望に合わせ、デフォルトは localhost にしています（本番は必ず外部URLに）
APP_BASE_URL           = (os.getenv("APP_BASE_URL", "http://localhost:8000") or "").strip()

# タイムゾーン
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
# DB モデル
# -----------------------------------------------------------------------------
class User(db.Model):
    """研究室メンバー等のユーザー。
    - external_token: 各人固有トークン（GoogleフォームのプレフィルURLに埋め込む）
    - line_user_id  : LINEの userId（任意：取得できた人のみ）
    - display_name  : 表示名（LINEプロフィールの displayName を初回時に反映）
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

    # 12問すべて NOT NULL（必須）
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

def build_own_users_overview(user_id: int) -> List[Dict[str, Any]]:
    """指定ユーザーの直近1件を集計してカード用データを返す（1件だけ入ったリスト）。"""
    overview: List[Dict[str, Any]] = []

    u = User.query.get(user_id)
    if not u:
        # 必要に応じて None を返すか、例外にする
        return overview

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
    else:
        score = total_score_row(r)
        overview.append({
            "display_name": u.display_name or "未設定",
            "external_token": u.external_token,
            "latest_score": score,
            "latest_status": status_label(score),
            "latest_at": to_jst(r.submitted_at).strftime("%Y-%m-%d %H:%M:%S"),
            "risk": risk_level(score),
        })

    # 単一要素なので並べ替えは不要だが、残しても問題なし
    # order_key = {"high": 0, "mid": 1, "low": 2, "none": 3}
    # overview.sort(key=lambda x: order_key.get(x["risk"], 9))

    return overview

# -----------------------------------------------------------------------------
# LINE ユーティリティ（プロフィール取得 / push・reply 送信）
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
    """pushメッセージ（任意タイミングで送信）。"""
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
    """replyメッセージ（イベント直後に即時返信）。友だち追加(follow)時はreplyが確実。"""
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
    ctx["users_overview"] = build_users_overview()  # 上段カード（リスク順）
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
    ctx["users_overview"] = build_own_users_overview(user_id=user.id)  # 上段カード
    return render_template("index_for_user.html", **ctx)

@app.route("/healthz")
def healthz():
    return "ok", 200

# -----------------------------------------------------------------------------
# LINE Webhook（userId 取得・登録・URL返信）
# -----------------------------------------------------------------------------
@app.route("/callback", methods=["POST"])
def callback():
    """
    LINEプラットフォームからのWebhookを受け取るエンドポイント。

    処理概要:
      1) 署名検証（X-Line-Signature）
      2) イベントごとに:
         - 個人トーク(user)のみ対象
         - DB上のユーザーを line_user_id で検索、なければ新規作成
           * external_token 自動発行
           * display_name は LINE プロフィールから取得（取得失敗時は「未設定」）
         - フォームURL（プレフィル）と自分専用ダッシュボードURLを返信
           * 友だち追加イベント(type=follow)は reply API 優先（確実に即時）
           * それ以外は push API
    返信メッセージ:
      - フォームURL: FORM_BASE_URL + "?" + FORM_ENTRY_ID + "=" + external_token
      - ダッシュボードURL: APP_BASE_URL + "/user/" + external_token
    """
    # -------------------------------
    # 署名検証（必須）
    # -------------------------------
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    mac = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256
    ).digest()
    expected = base64.b64encode(mac).decode()
    if not hmac.compare_digest(signature, expected):
        # 署名不一致 → LINE からの正当な通知ではない
        abort(400, "invalid signature")

    # -------------------------------
    # イベント配列を取り出す
    # -------------------------------
    try:
        data = json.loads(body)
    except Exception:
        abort(400, "invalid body json")

    events = data.get("events", [])
    if not events:
        # 空配列でも 200 を返す（LINE 側に「受け取った」と伝えるため）
        return "OK"

    # -------------------------------
    # 必要な設定をチェック（足りない場合は案内のみ返信）
    # -------------------------------
    form_base = (os.getenv("FORM_BASE_URL", "") or "").strip()
    entry_id  = (os.getenv("FORM_ENTRY_ID", "") or "").strip()
    app_base  = (os.getenv("APP_BASE_URL", "http://localhost:8000") or "").strip()

    for ev in events:
        etype = ev.get("type")            # "follow" / "message" など
        src   = ev.get("source", {})
        if src.get("type") != "user":
            # 1:1トーク以外（group/room）はスキップ
            continue

        user_id     = src.get("userId")
        reply_token = ev.get("replyToken")

        if not user_id:
            # 想定外だが userId なしの場合はスキップ
            continue

        # ---------------------------
        # DB 上のユーザーを用意
        # （初回は作成、既存は更新）
        # ---------------------------
        user = User.query.filter_by(line_user_id=user_id).one_or_none()
        if user is None:
            # 初回: external_token 発行 + LINE プロフィール名取得
            token = issue_external_token()
            prof  = get_line_profile(user_id)   # {"displayName": "..."} を期待
            name  = (prof or {}).get("displayName") or "未設定"

            user = User(
                display_name=name,
                line_user_id=user_id,
                external_token=token,
            )
            db.session.add(user)
            db.session.commit()
        else:
            # 既存: display_name が未設定なら補完、external_token が無ければ発行
            if not user.display_name or user.display_name == "未設定":
                prof = get_line_profile(user_id)
                if prof and prof.get("displayName"):
                    user.display_name = prof["displayName"]
                    db.session.commit()
            if not user.external_token:
                user.external_token = issue_external_token()
                db.session.commit()

        # ---------------------------
        # URL の組み立て
        # ---------------------------
        # フォームURL（ユーザーID＝external_token をプレフィル）
        if form_base and entry_id:
            sep = "&" if "?" in form_base else "?"
            form_url = f"{form_base}{sep}{entry_id}={user.external_token}"
        else:
            form_url = None

        # ダッシュボードURL（このユーザー専用ビュー）
        # ★ localhost は自分のPCでしか開けない。本番は ngrok 等の https を APP_BASE_URL に。
        dashboard_url = f"{app_base}/user/{user.external_token}"

        # ---------------------------
        # 返信文生成
        # ---------------------------
        if form_url:
            msg = (
                f"{user.display_name or 'こんにちは'} さん、以下のURLをご利用ください👇\n\n"
                f"📋 日次フォーム\n{form_url}\n\n"
                f"📊 あなたのダッシュボード\n{dashboard_url}\n\n"
                "※ フォームの『ユーザーID』欄は自動入力されます。変更せずに送信してください。"
            )
        else:
            # フォーム設定が無い場合はダッシュボードのみ通知
            msg = (
                f"{user.display_name or 'こんにちは'} さん、あなたのダッシュボードはこちらです👇\n"
                f"{dashboard_url}\n\n"
                "（フォームURLは未設定のため送れませんでした。管理者に連絡してください）"
            )

        # ---------------------------
        # 送信（follow=友だち追加時は reply が確実、それ以外は push）
        # ---------------------------
        try:
            if etype == "follow" and reply_token:
                # 友だち追加の瞬間は reply を使う（最も確実）
                line_reply_text(reply_token, msg)
            else:
                # それ以外（テキスト送信など）のイベントは push でもOK
                line_push_text(user_id, msg)
        except Exception as e:
            # 送信失敗はログに残すが、Webhook 200 は返す
            print("LINE send error:", e)

    return "OK"


# -----------------------------------------------------------------------------
# 手動登録API（デバッグ用）
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
    display_name = name
    if not display_name:
        prof = get_line_profile(line_user_id)  # ここでは userId=line_user_id を想定
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
    # 初回作成（既存テーブルが無い場合のみ）。スキーマ変更時は DB 削除→再作成を推奨（開発時）。
    with app.app_context():
        db.create_all()
    # ローカルでUIを確認するなら http://localhost:8000 へアクセス
    app.run(host="0.0.0.0", port=8000, debug=True)
