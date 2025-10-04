# Tuji-Hack — Googleフォーム × Flask × LINE Messaging API 連携アプリ

Googleフォーム → Googleスプレッドシート → **Flask**（DB保存/可視化）へ連携し、  
**LINE** からユーザーごとに「プレフィル済みフォームURL」＆「個別ダッシュボードURL」を配布するアプリ。  
ローカル開発中は **ngrok** で外部公開します。

---

## できること（概要）

- Googleフォームの回答を Apps Script(Webhook) 経由で Flask に送信し **DB保存（ユーザー別）**  
- **全体ダッシュボード**（`/`） と **人別ダッシュボード**（`/user/<external_token>`）を表示  
- LINEの **友だち追加（follow）直後に即時返信**：  
  - ユーザー専用の **フォームURL**（ユーザーIDを事前入力）  
  - ユーザー専用の **ダッシュボードURL**  
- （任意）**毎日 9:00 にフォームURLを自動送信**（APScheduler）

> 注: `external_token` は推測困難なランダム文字列。ユーザー識別に使用します。

---

## 動作環境

- **Python 3.11.x**（動作保証）
- SQLite（開発用）
- ngrok（ローカル公開用）

---

## ディレクトリ構成（最小）

```
Tuji_hack/
├─ app.py
├─ requirements.txt
├─ .env                   # 環境変数
├─ instance/
│  └─ local.db           # SQLite DB（初回は空でもOK）
├─ templates/
│  └─ index.html         # ダッシュボード（全体/人別共通）
└─ images/               # スクリーンショット
   ├─ image-1.png        # 回答タブ
   ├─ image.png          # 新規スプレッドシート作成
   ├─ image-2.png        # Apps Script メニュー
   ├─ image-3.png        # CODE.gs へリネーム
   ├─ image-5.png        # トリガー設定
   ├─ image-8.png        # 権限付与（Allow）
   ├─ image-9.png        # ngrok Forwarding ログ
   └─ image-10.png       # Flask 起動画面
```

> `instance/local.db` は **絶対パス** を `.env` の `DATABASE_URL` に設定すると安全です。

---

## セットアップ

### 1) 仮想環境 & 依存インストール

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (cmd)
.venv\Scripts\activate.bat

pip install -r requirements.txt
```

### 2) DB ファイル

```bash
mkdir -p instance
# macOS/Linux
touch instance/local.db
# Windows(PowerShell)
# ni instance/local.db
```

### 3) `.env` を作成（**重要**）

```ini
# DB
DATABASE_URL=sqlite:///instance/local.db
WEBHOOK_TOKEN=SHARED_SECRET_123

# LINE Messaging API
LINE_CHANNEL_SECRET=＜LINE Developers で発行したチャネルシークレット＞
LINE_CHANNEL_ACCESS_TOKEN=＜同 アクセストークン＞

# Google フォーム（ユーザーID設問を用意しておくこと）
FORM_BASE_URL="https://docs.google.com/forms/d/e/XXXXXXXXXXXXXXXX/viewform?usp=pp_url"
FORM_ENTRY_ID="entry.1391493516"   # 例: 「ユーザーID」設問の entry.<数字>

# Web アプリの外部URL（開発中は ngrok の https を使う）
APP_BASE_URL=https://nonaccenting-lichenologic-dion.ngrok-free.dev

# スケジューラ（毎朝9:00に送る）
ENABLE_SCHEDULER=1
SCHEDULE_CRON_MIN=0
SCHEDULE_CRON_HOUR=9
SCHEDULE_TZ=Asia/Tokyo
```

> - `APP_BASE_URL` が `http://localhost:8000` のままだと **他者はアクセス不可**。  
> - メンバーに共有する場合は **ngrok の https URL** を設定します。

### 4) Flask を起動

```bash
python app.py
# ブラウザ: http://localhost:8000
```

![アプリ起動](images/image-10.png)

### 5) ngrok で公開

```bash
ngrok http 8000
# 例: https://nonaenting-lichologic-dion.ngrok-free.dev → これを APP_BASE_URL に設定
```

![ngrokログ](images/image-9.png)

---

## Googleフォーム側の準備

1. フォームの **回答タブ** を開き、「スプレッドシートにリンク」をクリック  
   ![フォーム回答タブ](images/image-1.png)

2. 「新しいスプレッドシートを作成」 → 「作成」  
   ![新しいスプレッドシート](images/image.png)

3. スプレッドシートの **拡張機能 → Apps Script** を選択  
   ![Apps Script 選択](images/image-2.png)

4. Apps Script のファイル名を `CODE.gs` に変更  
   ![ファイル名変更](images/image-3.png)

5. 下記コードを貼り付け（`WEBHOOK_URL` は **ngrok の https** を使用）

```javascript
// ===== 設定（あなたのFlask公開URLと共有シークレット） =====
const WEBHOOK_URL = "https://nonacceing-licnologc-dion.ngrok-free.dev/api/forms/google";
const SHARED_SECRET = "SHARED_SECRET_123";

// フォーム送信時に自動で呼ばれる
function onFormSubmit(e) {
  try {
    const payload = {
      submitted_at: new Date().toISOString(),
      responses: e.namedValues
    };
    const res = UrlFetchApp.fetch(WEBHOOK_URL, {
      method: "post",
      contentType: "application/json",
      headers: { "X-Webhook-Token": SHARED_SECRET },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
    console.log("POST status:", res.getResponseCode(), "body:", res.getContentText());
  } catch (err) {
    console.error("onFormSubmit error:", err);
  }
}
```

6. **トリガー** を追加（「フォーム送信時」）  
   ![トリガー追加](images/image-5.png)

7. 初回保存時に権限エラーが出たら、**自分のアカウント**で認可 → **Allow**  
   ![認証画面](images/image-8.png)

---

## LINE 側の設定（超重要）

- **Webhook URL**: `https://nenting-lichogic-dion.ngrok-free.dev/callback`  
- **Webhook**: 利用する（オン）  
- **チャネルアクセストークン / シークレット**: `.env` に設定  
- **応答設定 → あいさつメッセージ**: **オフ**（公式定型文を無効化して自前返信のみ表示）  
- **応答設定 → 応答メッセージ**: 不要なら **オフ**

> 友だち追加（`follow`）で **reply** により即時に「フォームURL & ダッシュボードURL」を返します。  
> 日次定期送信は APScheduler が **push** で実行します。

---

## 挙動（ユーザーストーリー）

1. ユーザーが LINE で**友だち追加**  
2. `/callback` が **署名検証** → `userId` を取得  
3. DB にユーザーを作成（初回は `external_token` 自動発行、`display_name` を LINE プロフィールから取得）  
4. **個人プレフィルURL**（`FORM_BASE_URL + entry...=<external_token>`）と  
   **ダッシュボードURL**（`APP_BASE_URL/user/<external_token>`）を **reply** で即時送信  
5. 以後はフォーム回答があるたびに **Webhook → DB保存 → ダッシュボード反映**  
6. スケジューラが有効なら **毎朝9:00にフォームURLを push**

---

## エンドポイント（主なもの）

- `POST /api/forms/google` — GAS からの Webhook 受け口（トークンヘッダ必須）  
- `GET /` — 全体ダッシュボード（リスク順カード + 折れ線 + 直近回答）  
- `GET /user/<external_token>` — 個人ダッシュボード（本人専用ビュー）  
- `POST /callback` — LINE Webhook（follow/message 等のイベント）  
- `POST /register_line_user` — 手動登録デバッグ用（`line_user_id` を渡すと `external_token` 付与）

---

## トラブルシュート

**友だち追加したのにURLが来ない**  
- Webhook URL が **ngrok の https** か、**Webhook利用がオン** か確認  
- 公式 **あいさつメッセージ** を **オフ**（二重/競合防止）  
- `LINE_CHANNEL_SECRET/ACCESS_TOKEN` が正しいか  
- サーバログに `invalid signature` が出ていないか  
- Flask と ngrok の両方が稼働しているか

**GAS のトリガー頻度を変えたら LINE が動かなくなった**  
- LINE 即時返信は **GAS 無関係**。`/callback` が受け取れていない可能性が高い  
  → ngrok URL を張り替えたのに **LINE側の Webhook URL を更新していない** 等が典型

**404 /user/<token> になる**  
- その `external_token` を持つユーザーが DB に存在しているか確認  
  （友だち追加→初回返信時にトークンが払い出されます）

---

## セキュリティ/運用メモ

- ダッシュボードURLは **トークンを知っていれば閲覧可能**（認証なし）。共有先は信頼できるメンバーに限定  
- 本番運用は ngrok ではなく常時稼働サーバへ  
- スキーマ変更時は Alembic 等のマイグレーションを検討（開発中は DB を削除して作り直しも可）

---

## 動作確認（手元 curl）

```bash
# 1) 手動ユーザー登録（デバッグ用）
curl -s -X POST http://localhost:8000/register_line_user \
  -H "Content-Type: application/json" \
  -d '{"line_user_id":"TEST_LINE_USER_001","name":"テスト太郎"}'

# 2) GAS → Flask Webhook の疑似POST
curl -s -X POST http://localhost:8000/api/forms/google \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: SHARED_SECRET_123" \
  -d '{
    "submitted_at":"2025-01-01T00:00:00Z",
    "responses":{
      "ユーザーID":["＜1で受け取った external_token を入れる＞"],
      "Q1. 心配事のために睡眠時間が減ったことはありますか？":["1. そんなことはない"],
      "Q2. いつも緊張していますか？":["1. そんなことはない"],
      "Q3. ものごとに集中できますか？":["3. いつもよりできない"],
      "Q4. 何か有益な役割を果たしていると思いますか？":["3. いつもよりできない"],
      "Q5. 自分の問題について立ち向かうことができますか？":["1. そんなことはない"],
      "Q6. 物事について決断できると思いますか？":["2. いつもと同じ"],
      "Q7. いろんな問題を解決できなくて困りますか？":["2. いつもと同じ"],
      "Q8. 全般的にまあ満足していますか？":["3. いつもより多くはない"],
      "Q9. 日常生活を楽しむことができますか？":["3. いつもほどではない"],
      "Q10. 不幸せで憂うつと感じますか？":["2. いつもと同じ"],
      "Q11. 自信をなくしますか？":["3. いつもよりかなり多い"],
      "Q12. 自分は役にたたない人間だと感じることがありますか？":["2. いつもより多くはない"]
    }
  }'
```

---

## 付録：あいさつメッセージを無効化（公式定型文を消す）

1. **LINE Official Account Manager** → **設定 → 応答設定**  
2. **あいさつメッセージ** を **オフ**（必要なら **応答メッセージ** もオフ）  
3. **保存**  
4. **Messaging API → Webhook** を **有効**、URL を `https://<ngrok>/callback` に設定

> これで友だち追加時は、当アプリの `/callback` が送る **自前の案内のみ** が届きます。

---

## ひとこと

現状は **プレフィルURL＋トークン方式** で個人識別を実現しています。  
厳密な秘匿やアクセス制御が必要なら、将来的に **ログイン認証**（LINEログイン / OIDC / パスワードレス等）で保護する設計に拡張してください。

---

### 備考（画像について）
上記 README は、次の画像ファイルを **`images/`** に配置して参照します。  
存在しない場合は、ファイル名を合わせて配置してください。

- **images/image-1.png**（フォーム回答タブ）  
- **images/image.png**（新規スプレッドシート作成）  
- **images/image-2.png**（Apps Script メニュー）  
- **images/image-3.png**（CODE.gs リネーム）  
- **images/image-5.png**（トリガー設定）  
- **images/image-8.png**（権限付与 Allow）  
- **images/image-9.png**（ngrok Forwarding ログ）  
- **images/image-10.png**（Flask 起動画面）

