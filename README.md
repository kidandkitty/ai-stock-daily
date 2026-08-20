# 📈 AI 美股日報

每個交易日盤前自動執行，由 Claude AI 分析市場數據，Email 發送報告 + 網頁存檔。

---

## 架構

```
GitHub Actions（免費定時執行）
    ↓ 每天 08:30 美東時間
src/analyze.py
    ├── 抓取 Yahoo Finance 數據
    ├── Claude AI 分析
    ├── 生成 HTML 報告
    ├── Email 發送
    └── 推送至 web/ → GitHub Pages
```

---

## 設定步驟

### 1. Fork 這個 Repo

在 GitHub 點 **Fork** 按鈕，建立你自己的副本。

### 2. 設定 Gmail App Password

1. Google 帳號 → 安全性 → 兩步驟驗證（需先開啟）
2. 搜尋「應用程式密碼」→ 建立一個新的
3. 複製那 16 位密碼備用

### 3. 設定 GitHub Secrets

在你的 Repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret 名稱 | 填入內容 |
|------------|---------|
| `ANTHROPIC_API_KEY` | 你的 Claude API Key（[取得](https://console.anthropic.com)）|
| `EMAIL_FROM` | 你的 Gmail 地址 |
| `EMAIL_PASSWORD` | 剛才的 App Password（16位，不是登入密碼）|
| `EMAIL_TO` | 收件地址（可以和 FROM 一樣）|

### 4. 開啟 GitHub Pages

Repo → **Settings → Pages**
- Source：`Deploy from a branch`
- Branch：`main`，資料夾：`/web`
- 儲存後等約 1 分鐘

你的網址會是：`https://你的帳號.github.io/ai-stock-daily/`

### 5. 測試執行

Repo → **Actions → AI 美股日報 → Run workflow**

第一次手動觸發，確認 Email 有收到、網頁有更新。

---

## 自訂觀察清單

編輯 `src/analyze.py` 第 18 行的 `WATCHLIST`：

```python
WATCHLIST = [
    "SPY", "QQQ", "NVDA", "AAPL", "MSFT",
    "TSLA", "AMZN", "META", "GOOGL", "AMD"
]
```

換成你想追蹤的股票代碼即可。

## 調整執行時間

編輯 `.github/workflows/daily.yml` 的 cron：

```yaml
- cron: '30 12 * * 1-5'   # UTC 12:30 = 美東 08:30 = 台灣 20:30
- cron: '0 21 * * 1-5'    # UTC 21:00 = 美東 17:00 = 台灣 05:00（隔天）
```

---

## 費用估算

| 項目 | 費用 |
|------|------|
| GitHub Actions | 免費（公開 Repo 無限制）|
| GitHub Pages | 免費 |
| Yahoo Finance | 免費 |
| Claude API | 約 $0.01–0.03 / 天（每月 < $1）|
| Gmail | 免費 |
| **合計** | **< $1 / 月** |

---

## 下一步升級

- **第二階段**：加 PWA manifest + Web Push 通知
- **第三階段**：React Native App（iOS + Android 同時發布）
