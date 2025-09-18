# Donna - WhatsApp Calendar Assistant

A WhatsApp bot that integrates with Google Calendar to help manage your schedule and send meeting reminders.

## Features

- 📅 **Calendar Integration**: Link your Google Calendar
- 📋 **Daily Agenda**: Get today's or tomorrow's schedule  
- ⏰ **Smart Reminders**: Automatic notifications 10 minutes before meetings
- 🌍 **Timezone Support**: Set your preferred timezone
- 💬 **WhatsApp Interface**: Simple commands via WhatsApp

## Commands

- `link calendar` - Connect your Google Calendar
- `today` - Get today's agenda
- `tomorrow` - Get tomorrow's agenda  
- `remind` - Set up 10-minute meeting reminders
- `timezone America/New_York` - Set your timezone

## Setup

### Requirements

```bash
pip install -r requirements.txt
```

### Environment Configuration

Create a `tokens.env` file with:

```env
# Meta WhatsApp
META_ACCESS_TOKEN=your_meta_token
PHONE_NUMBER_ID=your_phone_number_id
VERIFY_TOKEN=donna_verify

# Google OAuth
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
BASE_URL=https://your-ngrok-url.ngrok-free.app
```

### Running the Server

```bash
# Start the FastAPI server
uvicorn main:app --host 127.0.0.1 --port 8001 --reload

# In another terminal, start ngrok
ngrok http 8001
```

### Meta Webhook Configuration

1. Go to Meta Developer Console
2. Navigate to WhatsApp → Configuration  
3. Set webhook URL: `https://your-ngrok-url.ngrok-free.app/webhook`
4. Set verify token: `donna_verify`

## Architecture

- **FastAPI**: Web server and webhook handler
- **SQLite**: User data and reminder storage
- **APScheduler**: Background reminder processing
- **Google Calendar API**: Calendar integration
- **Meta WhatsApp Cloud API**: WhatsApp messaging

## Database Schema

### Users Table
- `wa_number`: WhatsApp phone number (primary key)
- `google_refresh_token`: Google OAuth refresh token
- `timezone`: User's timezone preference

### Reminders Table  
- `id`: Unique reminder ID
- `wa_number`: Associated WhatsApp number
- `event_id`: Google Calendar event ID
- `summary`: Event title
- `start_utc`: Event start time (UTC)
- `remind_at_utc`: When to send reminder (UTC)

## Development

The server includes debug logging to help with development:

- 📥 Webhook request logging
- 🔍 Message processing logs  
- 🔗 OAuth link generation logs
- 📱 WhatsApp API response logs

## Production Deployment

For production, consider:

- Migrate from SQLite to PostgreSQL/MySQL
- Use a proper job queue (Redis + Celery) instead of APScheduler
- Secure environment variable management
- Proper logging and monitoring
- HTTPS endpoints (not ngrok)

## License

MIT License
