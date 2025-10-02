# Googleフォーム × Flask × LINE Messaging API 連携アプリ（最新版）

Googleフォーム → Googleスプレッドシート → Flaskアプリ のデータ連携を実現するアプリです。  
Apps Script を用いてフォーム回答を Webhook 経由で Flask に送信し、LINE Messaging API と連携して**研究室メンバーのストレス管理**を行います。  
この README は「友だち追加直後のURL自動配布」と「毎日9:00の自動配布（スケジューラON/OFF切替）」を反映した最新版です。

---

## 動作環境

- **Python 3.11.x**（動作保証）
- Flask / SQLAlchemy / python-dotenv / requests
- ngrok（ローカルサーバ公開）
- LINE Messaging API（push通知）

---

## セットアップ手順

### 1) 仮想環境の準備
```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
# .\.venv\Scripts\Activate.ps1
# Windows (cmd)
# .\.venv\Scripts\activate.bat
```

### 2) 依存インストール
```bash
pip install -r requirements.txt
```

### 3) データベースの準備
```bash
mkdir -p instance
# 空ファイルでOK（起動時に自動でテーブル生成）
touch instance/local.db
```

### 4) `.env` をプロジェクト直下に作成（**最新版**）
```env
# --- Flask & DB ---
WEBHOOK_TOKEN=SHARED_SECRET_123
# macOS / Linux 例
DATABASE_URL=sqlite:////Users/<USER>/MyHobby/Tuji_hack/instance/local.db
# Windows 例（どちらか片方のみ残す）
# DATABASE_URL=sqlite:///C:/Users/<USER>/MyHobby/Tuji_hack/instance/local.db

FLASK_ENV=development

# --- LINE Messaging API ---
LINE_CHANNEL_SECRET=YOUR_LINE_CHANNEL_SECRET
LINE_CHANNEL_ACCESS_TOKEN=YOUR_LINE_CHANNEL_ACCESS_TOKEN

# --- Google フォーム（プレフィルURL 配布用）---
FORM_BASE_URL="https://docs.google.com/forms/d/e/<FORM_ID>/viewform?usp=pp_url"
FORM_ENTRY_ID=entry.1391493516  # 「ユーザーID」短答設問の entry.<数字>

# --- 日次配信用（保護トークン）---
DAILY_PUSH_TOKEN=TASK_SECRET_123

# --- スケジューラ（APScheduler）---
# 1: スケジューラを起動（毎日09:00 JST に自動配布）
# 0: スケジューラを起動しない（Flaskは起動する／外部cron等を利用）
ENABLE_SCHEDULER=1
```

> **重要:** `DATABASE_URL` は**絶対パス推奨**。  
> macOS/Linux は `sqlite:////絶対パス/.../local.db`（スラッシュ4つ）、Windows は `sqlite:///C:/絶対パス/.../local.db`。

### 5) Googleフォーム連携設定（GAS）

1. Googleフォームに **短答式「ユーザーID」**（必須）を追加。  
2. 回答の保存先をスプレッドシートに設定。  
   ![フォーム回答タブ](images/image-1.png)
3. スプレッドシートの **拡張機能 → Apps Script** を開き、`CODE.gs` に以下を貼付：  
   ![Apps Script 選択](images/image-2.png)

```javascript
const WEBHOOK_URL = "https://<ngrok>/api/forms/google";
const SHARED_SECRET = "SHARED_SECRET_123";

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
4. **トリガー**で「フォーム送信時に `onFormSubmit`」を登録（初回は権限許可）。  
   ![トリガー追加](images/image-5.png)  
   ![認証画面](images/image-8.png)

> `FORM_ENTRY_ID` は「回答の事前入力」で生成したURLの `entry.<数字>` を控えて `.env` に設定。

### 6) ngrok で公開
```bash
ngrok http 8000
```
- 表示された **Forwarding(HTTPS)** を `CODE.gs` の `WEBHOOK_URL` に反映。  
  例）`https://xxxx-xxxx-xxx.ngrok-free.dev/api/forms/google`  
  ![ngrokログ](images/image-9.png)

- LINE Webhook も `https://<ngrok>/callback` を設定して「有効化」。

### 7) Flask 起動
```bash
python app.py
```
- ブラウザで http://localhost:8000 を開く。  
  ![アプリ起動](images/image-10.png)

---

## できること（アプリ機能）

### 1. 友だち追加直後の **URL自動配布**
- ユーザーが Official アカウントを **友だち追加 → 何か1メッセージ送信** すると、`/callback` が受信。  
- サーバ側で
  - LINE `userId` を登録
  - `external_token`（ランダム）を発行
  - LINE プロフィールの `displayName` を取得して `display_name` に保存
  - `.env` の `FORM_BASE_URL` / `FORM_ENTRY_ID` が設定されていれば、**個人用プレフィルURLを push で自動返信**

### 2. 毎日9:00 JST に **自動でURL配布**
- `.env` の `ENABLE_SCHEDULER=1` なら APScheduler が起動し、**毎朝9時(JST)** に登録済みユーザーへ一斉 push。
- `.env` を `ENABLE_SCHEDULER=0` にすると、**スケジューラだけ停止**（Flask は起動します）。外部 cron 等で代替可能。

### 3. 外部から叩ける **日次配信API**
- エンドポイント：`POST /tasks/daily_push`  
- ヘッダ：`X-Task-Token: <DAILY_PUSH_TOKEN>`  
- 例：
```bash
curl -s -X POST -H "X-Task-Token: TASK_SECRET_123" http://localhost:8000/tasks/daily_push
# => {"ok": true, "sent": N, "skipped": M}
```
- サーバ常時稼働でない場合は、GitHub Actions / Cloud Scheduler / crontab などから上記を毎日実行。

### 4. ダッシュボード
- `/` … **最新の回答**と**メンバーの状態（ヤバい順）**をカードで可視化。  
- `/user/<external_token>` … **人別ダッシュボード**（最新スコア・詳細・日別最新のみの折れ線）。

---

## LINE 側の設定チェック

- Messaging API を有効化。  
- Webhook URL：`https://<ngrok>/callback` を設定して **有効化**。  
- 応答設定の **既定のメッセージ**は **OFF**（自動メッセージが出ないように）。  
- `LINE_CHANNEL_SECRET` / `LINE_CHANNEL_ACCESS_TOKEN` を `.env` に設定。

---

## トラブルシュート

- **`ENABLE_SCHEDULER=0` にしたら起動しない？**  
  → 起動します。スケジューラが起動しないだけで、Flask サーバは通常どおり起動します。  
  ログに `Scheduler disabled (ENABLE_SCHEDULER!=1)` が出ていればOK。

- **`unknown user token`**  
  → プレフィルURLの `external_token` が DB 未登録、または `FORM_ENTRY_ID` が誤り。  
    友だちが一度メッセージを送る（/callback 発火）→ トークン自動発行 → 配布URLで回答、が正攻法。

- **push が来ない**  
  → Webhook 有効化、`LINE_CHANNEL_*` 設定、相手が「友だち追加後に最初のメッセージを送ったか」を確認。  
    企業/グループトークでは `userId` が取れない場合があるため**1:1トーク**でテスト。

- **別DBを参照している**  
  → `.env` の `DATABASE_URL` が相対パスだったり誤っている可能性。**絶対パス**推奨。

- **/user/<token> が 404**  
  → その `external_token` を持つユーザーが DB にいない。トップの「メンバーの状態」カードから遷移が安全。

---

## 画像（スクショ配置パス）

- `images/image-1.png` … フォームの回答タブ（スプレッドシートにリンク）  
- `images/image.png` … 新しいスプレッドシートを作成  
- `images/image-2.png` … スプレッドシート → 拡張機能 → Apps Script  
- `images/image-3.png` … Apps Script のファイル名を `CODE.gs` へ変更  
- `images/image-5.png` … トリガー追加画面  
- `images/image-8.png` … 権限承認（初回のみ）  
- `images/image-9.png` … ngrok の Forwarding 表示  
- `images/image-10.png` … Flask アプリ起動確認

> 画像は `images/` ディレクトリに上記ファイル名で配置（相対パス参照）。

---

## ひとことメモ（所感）

- 友だち追加直後の自動配布で「最初の一歩」を迷わせないのが◎。  
- スケジューラは `ENABLE_SCHEDULER` で**簡単にON/OFF**できるので、開発中はオフ、本番はオン＋外部cronの併用もやりやすいです。  
- プレフィルURLに `external_token` を使う運用は、**個人を特定しつつメール不要**でシンプル。運用コストが低く実用的でした。
