# Googleフォーム連携 Flask アプリ

Googleフォーム → Googleスプレッドシート → Flaskアプリ というデータ連携を実現する手順である．  
Google Apps Script を用いてフォーム回答をWebhook経由でFlaskに送信し，ローカル開発環境を ngrok で公開する流れである．

---

## 動作環境

- 動作保証 Python バージョン：**Python 3.11.x** である．  
  - `python --version` でバージョンを確認すること．
  - もし異なるバージョンが表示される場合は，pyenv 等で Python 3.11 を用意することを推奨する．

---

## フォームの実行手順

1. Googleフォームの **回答タブ** をクリックし，「スプレッドシートにリンク」をクリック．  
   ![フォーム回答タブ](images/image-1.png)

2. 「新しいスプレッドシートを作成」を選択し，「作成」をクリック．  
   ![新しいスプレッドシート](images/image.png)

3. スプレッドシートの **拡張機能 → Apps Script** を選択．  
   ![Apps Script 選択](images/image-2.png)

4. Apps Script のファイル名を `CODE.gs` に変更．  
   ![ファイル名変更](images/image-3.png)

5. 以下のコードをペーストする．  
   （Flaskの公開URLとシークレットを適切に設定すること）

```javascript
// ===== 設定（Flask公開URLと共有シークレット） =====
const WEBHOOK_URL = "https://xxxx-xxxx-xxx.ngrok-free.dev/api/forms/google"; 
const SHARED_SECRET = "SHARED_SECRET_123"; // 任意の長い文字列

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
  }
  catch (err) {
    console.error("onFormSubmit error:", err);
  }
}
```

6. **トリガーを追加**．  
   ![トリガー追加](images/image-5.png)

7. 初回保存時はエラーが出る場合があるが，再保存すると認証画面が出る．認証は自分のGoogleアカウントを選択し，「詳細」→「Go to CODE.gs (unsafe)」→「許可」を行う．  
   ![認証画面](images/image-8.png)

---

## プログラムの実行手順（ローカル）

1. **仮想環境の作成（プロジェクト直下で実行する）**  
   ```bash
   python -m venv .venv
   ```

2. **仮想環境の有効化**  
   - macOS / Linux:
     ```bash
     source .venv/bin/activate
     ```
   - Windows（PowerShell）:
     ```powershell
     .venv\Scripts\Activate.ps1
     ```
     > PowerShell 実行ポリシーでエラーが出る場合は，管理者権限で `Set-ExecutionPolicy RemoteSigned` を実行する必要がある場合がある．
   - Windows（コマンドプロンプト）:
     ```cmd
     .venv\Scripts\activate.bat
     ```

3. **依存関係のインストール**  
   ```bash
   pip install -r requirements.txt
   ```

4. **ngrokでローカルサーバーを外部公開**  
   ```bash
   ngrok http 8000
   ```
   - 表示された `Forwarding` の左側の HTTPS URL をコピーし，`CODE.gs` の `WEBHOOK_URL` に貼り付けて保存する．  
   - 例: `https://xxxx-xxxx-xxx.ngrok-free.dev` → `https://xxxx-xxxx-xxx.ngrok-free.dev/api/forms/google`

   ![ngrokログ](images/image-9.png)

5. **環境変数ファイル（.env）の作成**  
   - プロジェクト直下（`Tuji_hack`）に `.env` ファイルを作成し，以下の内容を記述すること．`DATABASE_URL` は自分の環境に合わせて絶対パスを設定すること．

```
WEBHOOK_TOKEN=SHARED_SECRET_123
DATABASE_URL=sqlite:////Users/kai/MyHobby/Tuji_hack/instance/local.db
FLASK_ENV=development
```

   - 例（Windows の場合）:
```
WEBHOOK_TOKEN=SHARED_SECRET_123
DATABASE_URL=sqlite:///C:/Users/kai/MyHobby/Tuji_hack/instance/local.db
FLASK_ENV=development
```

   - 注意点：`DATABASE_URL` の `sqlite:////` のスラッシュ数は環境によって変わる．macOS/Linux では `sqlite:////absolute/path/to/db` の形式，Windows では `sqlite:///C:/.../local.db` の形式を使用すること．

6. **instanceフォルダとデータベースファイルの作成**  
   - プロジェクト直下に `instance` フォルダを作成する．  
   - その中に `local.db` を作成する（空ファイルでよい）．  
   - 例: macOS/Linux  
     ```bash
     mkdir instance
     touch instance/local.db
     ```
   - Windows  
     ```powershell
     mkdir instance
     ni instance/local.db
     ```

7. **Flaskアプリの起動**  
   ```bash
   python app.py
   ```
   - アプリが起動したらブラウザで `http://localhost:8000` を開き確認する．  
   ![アプリ起動](images/image-10.png)

---

## 開発時のチェックリスト

- Python バージョンが 3.11.x であることを確認する．  
- 仮想環境を有効化してから `pip install -r requirements.txt` を実行する．  
- `.env` の `DATABASE_URL` は**必ず**自分の環境の絶対パスに置き換えること．  
- `instance/local.db` を作成していることを確認する．  
- ngrok の Forwarding URL を `CODE.gs` に反映していることを確認する．  
- Apps Script のトリガーが正しく登録されていることを確認する（フォーム送信時のトリガー）．

---

## まとめ

- Googleフォーム送信 → Apps Script（Webhook） → Flask アプリ受信の流れである．  
- ローカル開発中は ngrok で外部に公開し，Apps Script 側はその公開URLにPOSTするよう設定する．  
- Python 3.11.x 上で仮想環境を使用して依存関係を管理することが推奨される．  
- `instance` フォルダを作成し，`local.db` を配置することでDBが利用可能になる．
