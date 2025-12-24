import os
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, AudioMessageContent
from dotenv import load_dotenv
from openai import OpenAI
import tempfile
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import pytz
import json
from typing import Optional, Dict, Any
from notion_client import Client

load_dotenv()

app = Flask(__name__)

configuration = Configuration(access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Initialize Notion client
notion_api_key = os.getenv('NOTION_API_KEY')
notion_client = Client(auth=notion_api_key) if notion_api_key else None


# Initialize Google Calendar service
def get_calendar_service():
    credentials_path = os.getenv('GOOGLE_CALENDAR_CREDENTIALS')
    if not credentials_path:
        return None

    # Check if file exists
    if not os.path.exists(credentials_path):
        app.logger.warning(f"Google Calendar credentials file not found: {credentials_path}")
        return None

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=['https://www.googleapis.com/auth/calendar']
    )
    return build('calendar', 'v3', credentials=credentials)


try:
    calendar_service = get_calendar_service()
    if calendar_service:
        app.logger.info("Google Calendar service initialized successfully")
    else:
        app.logger.info("Google Calendar service not configured (skipped)")
except Exception as e:
    calendar_service = None
    app.logger.error(f"Failed to initialize Google Calendar: {str(e)}")


def parse_calendar_event(text: str) -> Optional[Dict[str, Any]]:
    """使用 OpenAI 解析訊息中的行事曆事件"""
    tz = pytz.timezone(os.getenv('TIMEZONE', 'Asia/Taipei'))
    now = datetime.now(tz)

    system_message = f"""你是智能行事曆助手。今天：{now.strftime('%Y-%m-%d %A %H:%M')}

相對時間：
- 明天 = 今天 + 1天
- 下週一 = 下個星期一
- 下午3點 = 15:00

如果訊息不包含事件，回應 null。
如果包含事件，提取標題、時間（ISO 8601格式）。
未指定結束時間則預設1小時。"""

    tools = [{
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create calendar event",
            "parameters": {
                "type": "object",
                "properties": {
                    "has_event": {"type": "boolean"},
                    "title": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                    "location": {"type": "string"}
                },
                "required": ["has_event"]
            }
        }
    }]

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": text}
            ],
            tools=tools,
            tool_choice="auto"
        )

        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls:
            return None

        args = json.loads(tool_calls[0].function.arguments)
        if not args.get('has_event'):
            return None

        return {
            'title': args['title'],
            'start_time': args['start_time'],
            'end_time': args['end_time'],
            'location': args.get('location')
        }
    except Exception as e:
        app.logger.error(f"Parse event error: {str(e)}")
        return None


def add_calendar_event(event_data: Dict[str, Any]) -> Dict[str, str]:
    """新增事件到 Google Calendar"""
    try:
        tz = pytz.timezone(os.getenv('TIMEZONE', 'Asia/Taipei'))

        # 解析時間並加上時區
        start_dt = datetime.fromisoformat(event_data['start_time'])
        end_dt = datetime.fromisoformat(event_data['end_time'])

        if start_dt.tzinfo is None:
            start_dt = tz.localize(start_dt)
        if end_dt.tzinfo is None:
            end_dt = tz.localize(end_dt)

        event = {
            'summary': event_data['title'],
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': os.getenv('TIMEZONE', 'Asia/Taipei'),
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': os.getenv('TIMEZONE', 'Asia/Taipei'),
            },
            'reminders': {'useDefault': True}
        }

        if event_data.get('location'):
            event['location'] = event_data['location']

        calendar_id = os.getenv('GOOGLE_CALENDAR_ID', 'primary')
        created = calendar_service.events().insert(
            calendarId=calendar_id,
            body=event
        ).execute()

        return {
            'success': True,
            'event_id': created['id'],
            'event_link': created.get('htmlLink', ''),
            'summary': created['summary'],
            'start': created['start']['dateTime']
        }
    except Exception as e:
        app.logger.error(f"Add event error: {str(e)}")
        return {'success': False, 'error': str(e)}


def process_message_for_calendar(text: str, reply_token: str) -> bool:
    """處理訊息並建立行事曆事件"""
    event_data = parse_calendar_event(text)
    if not event_data:
        return False

    result = add_calendar_event(event_data)

    if result['success']:
        message = f"✅ 已新增行事曆事件！\n\n"
        message += f"標題：{result['summary']}\n"
        message += f"時間：{result['start']}\n"
        message += f"連結：{result['event_link']}"
    else:
        message = f"❌ 新增行事曆失敗\n錯誤：{result['error']}"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=message)]
            )
        )
    return True


def save_to_notion(transcription: str, user_id: str = None) -> Dict[str, Any]:
    """將語音轉錄內容儲存到 Notion database"""
    if not notion_client:
        return {'success': False, 'error': 'Notion client not initialized'}

    try:
        database_id = os.getenv('NOTION_DATABASE_ID')
        if not database_id:
            return {'success': False, 'error': 'NOTION_DATABASE_ID not set'}

        tz = pytz.timezone(os.getenv('TIMEZONE', 'Asia/Taipei'))
        now = datetime.now(tz)

        # Create page in Notion database
        properties = {
            "標題": {
                "title": [
                    {
                        "text": {
                            "content": transcription[:100]  # 使用前100字作為標題
                        }
                    }
                ]
            },
            "內容": {
                "rich_text": [
                    {
                        "text": {
                            "content": transcription
                        }
                    }
                ]
            },
            "日期": {
                "date": {
                    "start": now.isoformat()
                }
            }
        }

        response = notion_client.pages.create(
            parent={"database_id": database_id},
            properties=properties
        )

        return {
            'success': True,
            'page_id': response['id'],
            'url': response['url']
        }
    except Exception as e:
        app.logger.error(f"Save to Notion error: {str(e)}")
        return {'success': False, 'error': str(e)}


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)

    return 'OK'


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text

    # 先嘗試處理為行事曆事件
    if calendar_service and process_message_for_calendar(text, event.reply_token):
        return

    # 不是事件，echo 回去
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=text)]
            )
        )


@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    try:
        message_id = event.message.id

        # Download audio content from LINE
        with ApiClient(configuration) as api_client:
            line_bot_blob_api = MessagingApiBlob(api_client)
            audio_content = line_bot_blob_api.get_message_content(message_id)

        # Create temporary file for audio
        with tempfile.NamedTemporaryFile(delete=False, suffix='.m4a') as temp_audio:
            temp_audio.write(audio_content)
            temp_audio_path = temp_audio.name

        try:
            # Transcribe audio using OpenAI Whisper
            with open(temp_audio_path, 'rb') as audio_file:
                transcription = openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )

            # 檢查開頭關鍵字
            transcription_lower = transcription.strip().lower()
            content = transcription.strip()

            # 判斷要使用哪個功能
            if transcription_lower.startswith('行事曆'):
                # 移除「行事曆」關鍵字
                content = transcription[3:].strip()

                # 只處理 Google Calendar
                if calendar_service and content:
                    calendar_handled = process_message_for_calendar(content, event.reply_token)
                    if not calendar_handled:
                        # 如果沒有成功處理為行事曆事件，回覆提示
                        with ApiClient(configuration) as api_client:
                            line_bot_api = MessagingApi(api_client)
                            line_bot_api.reply_message_with_http_info(
                                ReplyMessageRequest(
                                    reply_token=event.reply_token,
                                    messages=[TextMessage(text=f"📅 轉錄內容：{content}\n\n⚠️ 無法識別為行事曆事件，請提供時間資訊")]
                                )
                            )
                else:
                    with ApiClient(configuration) as api_client:
                        line_bot_api = MessagingApi(api_client)
                        line_bot_api.reply_message_with_http_info(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text="❌ Google Calendar 未設定或內容為空")]
                            )
                        )
                return

            elif transcription_lower.startswith('notion'):
                # 移除「notion」關鍵字
                content = transcription[6:].strip()

                # 只儲存到 Notion
                if notion_client and content:
                    user_id = event.source.user_id if hasattr(event.source, 'user_id') else None
                    notion_result = save_to_notion(content, user_id)

                    if notion_result['success']:
                        reply_text = f"📝 轉錄內容：{content}\n\n✅ 已儲存到 Notion\n{notion_result['url']}"
                    else:
                        reply_text = f"📝 轉錄內容：{content}\n\n⚠️ Notion 儲存失敗: {notion_result['error']}"
                else:
                    reply_text = "❌ Notion 未設定或內容為空"

                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)]
                        )
                    )
                return

            else:
                # 沒有關鍵字，只回覆轉錄文字，不儲存
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=f"💬 {transcription}\n\n💡 提示：開頭說「行事曆」或「notion」來儲存")]
                        )
                    )
        finally:
            # Clean up temporary file
            import os as os_module
            if os_module.path.exists(temp_audio_path):
                os_module.unlink(temp_audio_path)

    except Exception as e:
        # Log error and send user-friendly message
        app.logger.error(f"Error processing audio message: {str(e)}")
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="抱歉，語音轉文字時發生錯誤。\nSorry, an error occurred during transcription.")]
                )
            )


if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
