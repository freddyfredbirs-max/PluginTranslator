# Plugin Translator

Приложение с графическим интерфейсом для автоматического перевода файлов локализации Minecraft-плагинов (YAML, JSON, Java `.properties`) с защитой плейсхолдеров, тегов и цветовых кодов от перевода.
GUI app for automatically translating Minecraft plugin locale files (YAML, JSON, Java `.properties`), with built-in protection for placeholders, tags, and color codes.

---

## Зачем это нужно

Файлы локализации плагинов (`messages.properties`, `Locale_EN.yml`, `lang.json` и т.п.) - это не обычный текст. В них вперемешку с человеческими фразами лежит служебный синтаксис, от которого зависит работа самого плагина:

- **Числовые плейсхолдеры** `{0}`, `{1}` - Java `MessageFormat` подставляет туда имя игрока, сумму денег, время и т.д. Если сдвинуть или перевести цифру внутри скобок - сообщение сломается или подставит не то значение.
- **MiniMessage/HTML-теги** `<primary>`, `<dark_red>`, `<click:open_url:"...">` - управляют цветом и кликабельностью текста в чате. Переведённый или изменённый тег просто не сработает.
- **Legacy-коды цвета** `&a`, `§c` - старый формат тех же цветов, тоже нельзя трогать.
- **Экранирование** `\:`, `\!`, `''` - специфика формата `.properties`: двоеточие и апостроф внутри значения нужно экранировать, иначе файл вообще не распарсится Java.
- **Плейсхолдеры-статусы** вроде `[offon]`, `%server_time_hh:mm:ss%` - тоже не текст, а переменные, которые плагин заменяет на лету.

Если просто скопировать весь файл в Google Translate целиком - он перемешает местами `{0}` и `{1}`, потеряет обратные слэши, переведёт названия тегов, и результат перестанет работать в игре.

**Plugin Translator** решает это так: перед отправкой текста на перевод программа находит все подобные технические токены по regex-паттернам, временно заменяет их на нейтральные метки, переводит только оставшийся человеческий текст, а затем возвращает токены на исходные места - побайтово идентичными оригиналу.

## Возможности

- **Графический интерфейс** - не нужно трогать код или консоль
- **Три формата в одном приложении**: YAML (`.yml`), JSON (`.json`), Java `.properties`
- **Защита плейсхолдеров**: `{0}`, `%player%`, `[bracket]`, `<tag>`, `&a`/`§a` цветовые коды, MiniMessage-теги, экранированные символы (`\:`, `\!`, `''`) и т.д.
- **Скан перед переводом** - находит подозрительные токены, не покрытые встроенными паттернами, до того как что-либо переведётся
- **Кэш переводов** - повторный перевод того же файла не тратит время на уже переведённые строки
- **Автоповтор при сетевых сбоях** - ни одна строка не остаётся тихо непереведённой навсегда
- **Готовый `.exe`** - не требует установленного Python у конечного пользователя

## Как это устроено

1. **Парсинг** - под каждый формат свой обработчик (`YamlHandler`, `JsonHandler`, `PropertiesHandler`), учитывающий особенности синтаксиса (кавычки в YAML, экранирование в `.properties` и т.д.)
2. **Маскировка** - единый набор regex-паттернов защищает технические токены перед отправкой на перевод
3. **Скан** - отдельный проход ищет token-подобные конструкции, которые НЕ попали под защиту, и показывает их до перевода
4. **Перевод** - через Google Translate, с кэшированием и автоповтором при сбоях
5. **Сохранение** - переведённый файл сохраняется рядом с оригиналом в том же формате

## Установка и запуск

**Вариант 1 - готовый .exe (Windows, без Python):**
Скачай `PluginTranslator.exe` из раздела [Releases](../../releases), запусти, выбери файл локализации через интерфейс.

**Вариант 2 - из исходников:**
```bash
pip install ruamel.yaml   # или дай приложению установить самому при первом запуске
python PluginTranslator.py
```

### Требования
- Python 3.10+ (если запускаешь из исходников)
- `ruamel.yaml` (для YAML - приложение может поставить само при первом запуске)
- Подключение к интернету (перевод идёт через Google Translate)

### Ограничения (честно)
- Используется неофициальный публичный endpoint Google Translate - не production-grade API, возможны временные сбои (учтены через retry, но не гарантированы).
- Автоматически различить "тег форматирования, который нельзя трогать" (`<primary>`) от "аргумента команды, который стоило бы перевести" (`<player>`) без ручного whitelist'а невозможно - сейчас защищаются оба варианта целиком.
- Не заменяет вычитку носителем языка - это инструмент для черновика перевода, а не финального релиза без проверки.

## Поддержать проект
Если хочешь поддержать разработку - буду благодарен переводу через СБП (Сбербанк) по номеру телефона: *(укажи свой номер)*

---

## Why this exists

Plugin locale files (`messages.properties`, `Locale_EN.yml`, `lang.json`, etc.) aren't plain text. Mixed in with the human-readable phrases is syntax the plugin depends on to actually function:

- **Numbered placeholders** `{0}`, `{1}` - Java `MessageFormat` substitutes the player name, a money amount, a timestamp, etc. Reorder or translate a digit inside the braces and the message breaks.
- **MiniMessage/HTML tags** `<primary>`, `<dark_red>`, `<click:open_url:"...">` - control chat color and clickability. A translated tag simply won't render.
- **Legacy color codes** `&a`, `§c` - the older format for the same thing, equally untouchable.
- **Escaping** `\:`, `\!`, `''` - `.properties`-specific: colons and apostrophes inside a value must be escaped or Java fails to parse the file at all.
- **Status placeholders** like `[offon]`, `%server_time_hh:mm:ss%` - variables the plugin substitutes at runtime.

Pasting the whole file into Google Translate as-is will scramble placeholder ordering, drop backslashes, translate tag names, and break the plugin in-game.

**Plugin Translator** handles this by scanning the text for these technical tokens via regex before sending anything for translation, swapping them for neutral markers, translating only the remaining human text, then restoring the tokens exactly where they were.

## Features

- **GUI app** - no need to touch code or a console
- **Three formats in one app**: YAML (`.yml`), JSON (`.json`), Java `.properties`
- **Placeholder protection**: `{0}`, `%player%`, `[bracket]`, `<tag>`, `&a`/`§a` color codes, MiniMessage tags, escaped characters (`\:`, `\!`, `''`), and more
- **Pre-translation scan** - surfaces suspicious tokens not covered by the built-in patterns before anything gets translated
- **Translation caching** - re-translating the same file won't waste time on strings already done
- **Automatic retry on network failures** - no string is silently left untranslated
- **Standalone .exe available** - end users don't need Python installed

## How it works

1. **Parsing** - a dedicated handler per format (`YamlHandler`, `JsonHandler`, `PropertiesHandler`) accounts for that format's quirks (YAML quoting, `.properties` escaping, etc.)
2. **Masking** - a shared set of regex patterns protects technical tokens before they're sent for translation
3. **Scan** - a separate pass surfaces token-like constructs NOT caught by the protection patterns, before translation runs
4. **Translation** - via Google Translate, with caching and automatic retry on failures
5. **Saving** - the translated file is saved next to the original, in the same format

## Install & run

**Option 1 - prebuilt .exe (Windows, no Python needed):**
Download `PluginTranslator.exe` from [Releases](../../releases), run it, pick your locale file through the interface.

**Option 2 - from source:**
```bash
pip install ruamel.yaml   # or let the app install it automatically on first run
python PluginTranslator.py
```

### Requirements
- Python 3.10+ (if running from source)
- `ruamel.yaml` (for YAML - the app can install it automatically on first run)
- An internet connection (translation goes through Google Translate)

### Limitations (honest)
- Uses Google Translate's unofficial public endpoint - not a production-grade API; occasional failures are expected (mitigated via retry, but not guaranteed).
- Reliably telling apart "a formatting tag that must not be touched" (`<primary>`) from "a command argument that should be translated" (`<player>`) without a manual whitelist isn't possible - both are currently protected wholesale.
- Not a substitute for native-speaker proofreading - this is a drafting tool, not a final-release-ready translator.

## License
MIT
