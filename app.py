# app.py（単一ファイル／単一テーブル：FormResponse）
import os
from datetime import datetime, timezone
from flask import Flask, request, abort, jsonify
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

# ===== 設定 =====
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "SHARED_SECRET_123")

# フォームの質問文（GASの namedValues のキーと**完全一致**させること）
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
# 逆引きマップ（質問文→1..12）
QUESTION_TO_INDEX = {q: i + 1 for i, q in enumerate(QUESTIONS)}

# ===== Flask/DB =====
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class FormResponse(db.Model):
    __tablename__ = "form_responses"
    id = db.Column(db.Integer, primary_key=True)
    submitted_at = db.Column(db.DateTime, nullable=False, index=True)
    # 横持ちで保存（テキスト回答）
    Q1  = db.Column(db.String, nullable=True)
    Q2  = db.Column(db.String, nullable=True)
    Q3  = db.Column(db.String, nullable=True)
    Q4  = db.Column(db.String, nullable=True)
    Q5  = db.Column(db.String, nullable=True)
    Q6  = db.Column(db.String, nullable=True)
    Q7  = db.Column(db.String, nullable=True)
    Q8  = db.Column(db.String, nullable=True)
    Q9  = db.Column(db.String, nullable=True)
    Q10 = db.Column(db.String, nullable=True)
    Q11 = db.Column(db.String, nullable=True)
    Q12 = db.Column(db.String, nullable=True)
    # 元JSONを保全したい場合はコメントアウトを外す
    # raw_json = db.Column(db.JSON, nullable=True)

def parse_iso8601_z(s: str) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

# ===== Webhook受信 =====
@app.route("/api/forms/google", methods=["POST"])
def receive_google_form():
    # 簡易認証
    if request.headers.get("X-Webhook-Token") != WEBHOOK_TOKEN:
        abort(401, "invalid token")

    data = request.get_json(silent=True) or {}
    named = data.get("responses") or {}
    submitted_at = parse_iso8601_z(data.get("submitted_at"))

    # ひな形（Noneで初期化）
    values = {f"Q{i}": None for i in range(1, 13)}

    # namedValues から Q1..Q12 へ詰め替え
    for question_text, answers in named.items():
        idx = QUESTION_TO_INDEX.get(question_text)
        if not idx:
            # 未知の設問は無視（フォーム文言が変わっていないか確認）
            continue
        # Google Formsは配列で届くことが多いので先頭要素を採用
        ans_text = answers[0] if isinstance(answers, list) and answers else str(answers)
        values[f"Q{idx}"] = ans_text

    rec = FormResponse(submitted_at=submitted_at, **values)
    # rec.raw_json = data  # 保存したい場合
    db.session.add(rec)
    db.session.commit()

    return jsonify({"ok": True, "id": rec.id})

# ===== 簡易確認ページ（テンプレ不要） =====
@app.route("/")
def index():
    rows = FormResponse.query.order_by(FormResponse.submitted_at.desc()).limit(100).all()
    def td(x): return "" if x is None else x
    trs = []
    for r in rows:
        tds = "".join([f"<td>{td(getattr(r, f'Q{i}'))}</td>" for i in range(1, 13)])
        trs.append(f"<tr><td>{r.id}</td><td>{r.submitted_at}</td>{tds}</tr>")
    header_q = "".join([f"<th>Q{i}</th>" for i in range(1, 13)])
    html = f"""
    <html><head><meta charset="utf-8"><title>FormResponses</title></head>
    <body>
      <h2>最新100件</h2>
      <table border="1" cellpadding="6">
        <tr><th>ID</th><th>submitted_at</th>{header_q}</tr>
        {''.join(trs) if trs else '<tr><td colspan="14">no data</td></tr>'}
      </table>
    </body></html>
    """
    return html

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