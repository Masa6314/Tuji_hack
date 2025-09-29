# app.py
import os
from collections import OrderedDict
from datetime import datetime, timezone
from flask import Flask, request, abort, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from zoneinfo import ZoneInfo        # JST変換用
from dotenv import load_dotenv       # .env を読み込む

# ===== 設定 =====
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "SHARED_SECRET_123")
JST = ZoneInfo("Asia/Tokyo")

# Googleフォームの namedValues のキーと完全一致させること
QUESTIONS = [
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
QUESTION_TO_INDEX = {q: i + 1 for i, q in enumerate(QUESTIONS)}

# ===== Flask/DB =====
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class FormResponse(db.Model):
    __tablename__ = "form_responses"
    id = db.Column(db.Integer, primary_key=True)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    # 欠損禁止（NOT NULL）
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

def to_jst(dt: datetime) -> datetime:
    """DBから取り出した日時を『UTCとして解釈→JSTへ変換』して返す．"""
    if dt is None:
        return None
    # SQLiteはtz情報を落としやすいので，tzが無ければUTCとみなす
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST)


# ===== ユーティリティ =====
def parse_iso8601_z(s: str) -> datetime:
    """'...Z' 付きISO8601にも対応してUTCで返す．"""
    if not s:
        return datetime.now(timezone.utc)
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def answer_point(s: str) -> int:
    """回答先頭が '1.' または '2.' なら0点，それ以外（3.,4.）は1点．"""
    if not s:
        return 0
    s = s.strip()
    return 0 if s.startswith("1.") or s.startswith("2.") else 1

def total_score_row(rec) -> int:
    """1レコード（12問）の合計点．"""
    return sum(answer_point(getattr(rec, f"Q{i}")) for i in range(1, 13))

def status_label(score: int) -> str:
    """スコア→状態文言．"""
    if score <= 1:
        return "とても健康です！"
    elif 2 <= score <= 3:
        return "少し休みましょう！"
    else:
        return "休息が必要です！"

# ===== Webhook受信（GAS→Flask） =====
@app.route("/api/forms/google", methods=["POST"])
def receive_google_form():
    if request.headers.get("X-Webhook-Token") != WEBHOOK_TOKEN:
        abort(401, "invalid token")

    data = request.get_json(silent=True) or {}
    named = data.get("responses") or {}
    submitted_at = parse_iso8601_z(data.get("submitted_at"))

    # 詰め替え
    values = {}
    for question_text, answers in named.items():
        idx = QUESTION_TO_INDEX.get(question_text)
        if not idx:
            continue
        # 単一選択のみ許容（チェックボックスの複数選択はエラー）
        if isinstance(answers, list):
            if len(answers) != 1:
                abort(400, f"単一選択のみ許可されています: {question_text}")
            ans_text = answers[0]
        else:
            ans_text = str(answers)
        values[f"Q{idx}"] = ans_text.strip()

    # 欠損・空文字チェック（12問すべて必須）
    missing = [f"Q{i}" for i in range(1, 13) if not values.get(f"Q{i}")]
    if missing:
        abort(400, f"必須回答が不足しています: {', '.join(missing)}")

    rec = FormResponse(submitted_at=submitted_at, **values)
    db.session.add(rec)
    db.session.commit()

    return jsonify({"ok": True, "id": rec.id})

# ===== 画面（最新スコア＋状態，日別最新のみの折れ線） =====
@app.route("/")
def index():
    # 厳密な最新順：submitted_at DESC，かつ同時刻は id DESC
    rows = (FormResponse.query
            .order_by(FormResponse.submitted_at.desc(), FormResponse.id.desc())
            .all())

    # 同一日（JST日付）について「その日の最新のみ」を選ぶ
    latest_by_day = OrderedDict()
    for r in rows:
        jst_day = to_jst(r.submitted_at).date().isoformat()
        if jst_day not in latest_by_day:
            latest_by_day[jst_day] = r

    # グラフ用（日付昇順／JST）
    chart_labels = sorted(latest_by_day.keys())
    chart_values = [total_score_row(latest_by_day[d]) for d in chart_labels]

    # 全体の最新（表示はJST）
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

    return render_template(
        "index.html",
        latest_score=latest_score,
        latest_status=latest_status,
        latest_at=latest_at,
        latest_answers=latest_answers,
        chart_labels=chart_labels,
        chart_values=chart_values,
    )

# ===== 起動 =====
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=8000, debug=True)
















# import os
# from datetime import datetime
# from flask import Flask, request, abort, jsonify, render_template, request, redirect
# from flask_sqlalchemy import SQLAlchemy
# from dotenv import load_dotenv
# from datetime import datetime

# load_dotenv()  # .env を読み込む

# app = Flask(__name__)
# app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///local.db")
# app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# db = SQLAlchemy(app)


# class FormResponse(db.Model):
#     id = db.Column(db.Integer, primary_key=True)
#     submitted_at = db.Column(db.DateTime, nullable=False)
#     Q1 = db.Column(db.String, nullable=True)
#     Q2 = db.Column(db.String, nullable=True)
#     Q3 = db.Column(db.String, nullable=True)
#     Q4 = db.Column(db.String, nullable=True)
#     Q5 = db.Column(db.String, nullable=True)
#     Q6 = db.Column(db.String, nullable=True)
#     # Q7 = db.Column(db.String, nullable=True)
#     # Q8 = db.Column(db.String, nullable=True)
#     # Q9 = db.Column(db.String, nullable=True)
#     # Q10 = db.Column(db.String, nullable=True)
#     # Q11 = db.Column(db.String, nullable=True)
#     # Q12 = db.Column(db.String, nullable=True)

# class Post(db.Model):
#     id = db.Column(db.Integer, primary_key=True)
#     title = db.Column(db.String(50), nullable=False)
#     body = db.Column(db.String(300), nullable=False)
#     created_at = db.Column(db.DateTime, nullable=False, default=datetime.now())



# @app.route("/api/forms/google", methods=["POST"]) #POSTでアクセスが来たときに呼ばれる関数
# def receive_google_form():
#     # 共有シークレットで簡易認証
#     if request.headers.get("X-Webhook-Token") != os.getenv("WEBHOOK_TOKEN"):
#         abort(401, "invalid token")
#     body = request.get_json(silent=True) or {}
#     responses = body.get("responses")
#     ts = body.get("submitted_at")
#     if not responses:
#         abort(400, "no responses")

#     # 日時パース（Z終端なら除去）
#     if ts:
#       ts = ts.replace("Z", "")
#       submitted_at = datetime.fromisoformat(ts)
#     else:
#       submitted_at = datetime

#     rec = FormResponse(
#         submitted_at=submitted_at,
#         Q1=responses["Test 1"][0],
#         Q2=responses["Test 2"][0],
#         Q3=responses["Test 3"][0],
#         Q4=responses["Test 4"][0],
#         Q5=responses["Test 5"][0],
#         Q6=responses["Test 6"][0],
#         # Q7=responses["Test 7"][0],
#         # Q8=responses["Test 8"][0],
#         # Q9=responses["Test 9"][0],
#         # Q10=responses["Test 10"][0],
#         # Q11=responses["Test 11"][0],
#         # Q12=responses["Test 12"][0],
#     )
#     db.session.add(rec)
#     db.session.commit()
#     return jsonify({"ok": True})


# @app.route("/", methods=["GET", "POST"])
# def hello():
#     if request.method=="GET":
#         posts = Post.query.all()
#         forms = FormResponse.query.all()
#         for f in forms:
#             if f.Q1 == "選択肢 1" or "選択肢2":
#                 f.Q1 = 1
#             else:
#                 f.Q1 = 0
#         return render_template('index.html', posts=posts, forms=forms)


# @app.route("/create", methods=["GET", "POST"])
# def create():
#     if request.method == "POST":
#         title = request.form.get("title")
#         body = request.form.get("body")

#         post = Post(title=title, body=body)
        
#         db.session.add(post)
#         db.session.commit()
#         return redirect("/")

#     else:
#         return render_template('create.html')

# @app.route("/<int:id>/update", methods=["GET", "POST"])
# def update(id):
#     post = Post.query.get(id)
#     if request.method == "GET":
#         return render_template("update.html", post=post)
#     else:
#         post.title = request.form.get("title")
#         post.body = request.form.get("body")

#         db.session.commit()
#         return redirect("/")


# @app.route("/<int:id>/delete", methods=["GET"])
# def delete(id):
#     post = Post.query.get(id)

#     db.session.delete(post)
#     db.session.commit()
#     return redirect("/")


# if __name__ == "__main__":
#     with app.app_context():
#         db.create_all()  # 初回テーブル作成
#     app.run(host="0.0.0.0", port=8000, debug=True)