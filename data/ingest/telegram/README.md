# Telegram user ingest

Модуль использует Telegram MTProto API через пользовательскую сессию, а не через bot token.

Что умеет:

- запросить код авторизации для аккаунта пользователя;
- завершить вход по коду и 2FA-паролю;
- проверить текущую сессию;
- читать список диалогов;
- выгружать сообщения из диалога;
- отдавать сообщения как payload, совместимый по смыслу с таблицей `sources`.

## Зависимость

Нужен пакет `telethon`.

## Конфигурация

Берется из `.env` или из встроенных значений:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_APP_TITLE`
- `TELEGRAM_APP_SHORT_NAME`
- `TELEGRAM_PHONE`
- `TELEGRAM_SESSION_NAME`
- `TELEGRAM_SESSION_DIR`
- `TELEGRAM_EXPORT_DIR`

Сессии сохраняются в `data/ingest/telegram/sessions/`.

## Примеры

Запросить код:

```powershell
python -m data.ingest.telegram send-code --phone +79990000000
```

Войти:

```powershell
python -m data.ingest.telegram sign-in --phone +79990000000 --code 12345
```

Если у аккаунта включен cloud password:

```powershell
python -m data.ingest.telegram sign-in --phone +79990000000 --code 12345 --password "your-2fa-password"
```

Проверить статус:

```powershell
python -m data.ingest.telegram status
```

Список диалогов:

```powershell
python -m data.ingest.telegram dialogs --limit 20
```

Сообщения из диалога:

```powershell
python -m data.ingest.telegram messages --entity some_username --limit 100
```

Сообщения в виде payload для Stoic:

```powershell
python -m data.ingest.telegram messages --entity some_username --limit 100 --as-sources
```

Экспорт в файл:

```powershell
python -m data.ingest.telegram export --entity some_username --limit 200
```
