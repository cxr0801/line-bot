# LINE Bot - Voice Transcription with Notion & Google Calendar Integration

智能 LINE Bot，支援語音轉文字並根據關鍵字儲存到 Notion 或 Google Calendar。

## 功能

- 🎤 **語音轉文字**：使用 OpenAI Whisper API 將語音訊息轉為文字
- 📝 **Notion 整合**：語音開頭說「notion」，自動儲存到 Notion database
- 📅 **Google Calendar 整合**：語音開頭說「行事曆」，使用 AI 解析並建立行事曆事件
- 🤖 **智能解析**：使用 GPT-4 自動識別自然語言中的時間和事件資訊

## 使用方式

### 儲存到 Notion
🎤 語音說：「**notion** 今天學習了如何整合 LINE Bot」

### 建立行事曆事件
🎤 語音說：「**行事曆** 明天下午三點開會」

### 純語音轉文字
🎤 語音說：「這是測試訊息」（不儲存，只回覆轉錄文字）

## 部署到 Zeabur

### 1. 環境變數設定

在 Zeabur 專案中設定以下環境變數：

#### LINE Bot 設定
```
LINE_CHANNEL_ACCESS_TOKEN=你的_LINE_channel_access_token
LINE_CHANNEL_SECRET=你的_LINE_channel_secret
```

#### OpenAI API
```
OPENAI_API_KEY=你的_OpenAI_API_key
```

#### Google Calendar 設定
```
GOOGLE_CALENDAR_CREDENTIALS=credentials/service-account-key.json
GOOGLE_CALENDAR_ID=你的_Google_Calendar_ID
TIMEZONE=Asia/Taipei
```

#### Notion 設定
```
NOTION_API_KEY=你的_Notion_integration_token
NOTION_DATABASE_ID=你的_Notion_database_ID
```

### 2. Google Calendar Credentials

需要上傳 Google Service Account 金鑰檔案：
1. 在 Zeabur 專案中建立 `credentials` 資料夾
2. 上傳 `service-account-key.json` 到 `credentials/` 資料夾

### 3. Notion Database 設定

在 Notion 中建立 Database，包含以下欄位：
- **標題** (Title) - 必填
- **內容** (Text)
- **日期** (Date)
- **類型** (Select) - 包含「語音記錄」選項
- **用戶ID** (Text) - 可選

### 4. LINE Webhook 設定

部署完成後，將 Zeabur 提供的 URL 設定到 LINE Developers Console：
```
https://你的zeabur網址.zeabur.app/callback
```

## 本地開發

### 安裝依賴
```bash
uv sync
```

### 設定環境變數
複製 `.env.example` 為 `.env` 並填入你的憑證：
```bash
cp .env.example .env
```

### 啟動應用
```bash
uv run python main.py
```

### 使用 ngrok 建立公開 URL（本地測試用）
```bash
ngrok http 5000
```

## 技術棧

- **框架**: Flask
- **語音轉文字**: OpenAI Whisper API
- **AI 解析**: OpenAI GPT-4
- **整合**: LINE Messaging API, Notion API, Google Calendar API
- **語言**: Python 3.10+

## License

MIT
