# Plugin Translator

🇷🇺 Автоматический переводчик локализаций для плагинов Minecraft (YAML и Java `.properties`) с защитой плейсхолдеров, тегов и цветовых кодов от перевода.
🇬🇧 Automatic translator for Minecraft plugin locale files (YAML and Java `.properties`), with built-in protection for placeholders, tags, and color codes.

---

## 🇷🇺 Зачем это нужно

Файлы локализации плагинов (`messages.properties`, `Locale_EN.yml` и т.п.) — это не обычный текст. В них вперемешку с человеческими фразами лежит служебный синтаксис, от которого зависит работа самого плагина:

- **Числовые плейсхолдеры** `{0}`, `{1}` — Java `MessageFormat` подставляет туда имя игрока, сумму денег, время и т.д. Если сдвинуть или перевести цифру внутри скобок — сообщение сломается или подставит не то значение.
- **MiniMessage/HTML-теги** `<primary>`, `<dark_red>`, `<click:open_url:"...">` — управляют цветом и кликабельностью текста в чате. Переведённый или изменённый тег просто не сработает, и вместо цвета игрок увидит сырой текст тега.
- **Legacy-коды цвета** `&a`, `§c` — старый формат тех же цветов, тоже нельзя трогать.
- **Экранирование** `\:`, `\!`, `''` — специфика формата `.properties`: двоеточие и апостроф внутри значения нужно экранировать, иначе файл вообще не распарсится Java.
- **Плейсхолдеры-статусы** вроде `[offon]`, `%server_time_hh:mm:ss%` — тоже не текст, а переменные, которые плагин заменяет на лету.

Если просто скопировать весь файл в Google Translate целиком — он перемешает местами `{0}` и `{1}`, потеряет обратные слэши, переведёт названия тегов, и результат перестанет работать в игре. Ручной перевод с сохранением всего этого синтаксиса на тысячах строк — долго и легко ошибиться.

**Plugin Translator** решает это так: перед отправкой текста на перевод скрипт находит все подобные технические токены по regex-паттернам, временно заменяет их на нейтральные метки (`@@0@@`, `@@1@@`...), переводит только оставшийся человеческий текст, а затем возвращает токены на исходные места — побайтово идентичными оригиналу.

## Как это устроено

1. **Парсинг** — YAML читается через `ruamel.yaml` (с сохранением структуры и кавычек), `.properties` — построчным парсером собственной разработки, учитывающим экранирование `key=value`.
2. **Маскировка** — единый `PLACEHOLDER_RE`, собранный из проверенных regex-паттернов под конкретный формат.
3. **Скан перед переводом** (`--scan`) — отдельный проход ищет token-подобные конструкции, которые НЕ попали под защиту, и показывает их до перевода, чтобы можно было доработать паттерны вручную.
4. **Перевод** — через неофициальный публичный endpoint Google Translate, в 12+ потоков с переиспользуемыми keep-alive HTTPS-соединениями (без этого перевод тысяч строк занимает в разы больше времени из-за постоянных TLS-хендшейков).
5. **Кэш** — каждая переведённая строка сохраняется в JSON рядом со скриптом; повторный запуск переводит только новые/изменённые строки.
6. **Отказоустойчивость** — при сетевых сбоях есть автоповтор с экспоненциальной задержкой; если перевод строки так и не удался, она НЕ кэшируется и остаётся помеченной для повторной попытки при следующем запуске, вместо того чтобы тихо остаться непереведённой навсегда.

## Возможности
- Поддержка **YAML** (`Locale_EN.yml` → `Locale_RU.yml`) и **Java `.properties`** (`messages.properties` → `messages_ru.properties`, формат EssentialsX)
- Защита плейсхолдеров: `{0}`, `%player%`, `[bracket]`, `<tag>`, `&a`/`§a` цветовые коды, MiniMessage-теги, экранированные символы (`\:`, `\!`, `''`) и т.д.
- Режим `--scan` — находит подозрительные токены, которые не покрыты встроенными паттернами, до того как что-либо переведётся
- Кэширование переводов (повторный запуск не переводит уже переведённое заново)
- Многопоточность + переиспользуемые HTTPS-соединения для скорости
- Автоматический повтор при сетевых сбоях, без потери непереведённых строк
- Графический интерфейс (`PluginTranslator.py`) — не нужно трогать код или консоль
- Собирается в отдельный `.exe`, не требует установленного Python у конечного пользователя

### Требования
- Python 3.10+
- `pip install ruamel.yaml` (для YAML; GUI-версия ставит сама при необходимости)

### Использование
```bash
python translator.py --scan          # проверить YAML-файл перед переводом
python translator.py                 # перевести YAML

python translator_properties.py --scan
python translator_properties.py      # перевести .properties

python PluginTranslator.py           # запустить GUI-версию
```

### Ограничения (честно)
- Используется неофициальный публичный endpoint Google Translate — не production-grade API, возможны временные сбои и лимиты по частоте запросов (учтены через retry + keep-alive, но не гарантированы).
- Автоматически различить "тег форматирования, который нельзя трогать" (`<primary>`) от "аргумента команды, который стоило бы перевести" (`<player>`) без ручного whitelist'а невозможно — сейчас защищаются оба варианта целиком (безопасный, но не идеально "красивый" перевод усечённых usage-строк).
- Не заменяет вычитку носителем языка — это инструмент для черновика перевода, а не финального релиза без проверки.

---

## 🇬🇧 Why this exists

Plugin locale files (`messages.properties`, `Locale_EN.yml`, etc.) aren't plain text. Mixed in with the human-readable phrases is syntax the plugin depends on to actually function:

- **Numbered placeholders** `{0}`, `{1}` — Java `MessageFormat` substitutes the player name, a money amount, a timestamp, etc. Reorder or translate a digit inside the braces and the message breaks or substitutes the wrong value.
- **MiniMessage/HTML tags** `<primary>`, `<dark_red>`, `<click:open_url:"...">` — control chat color and clickability. A translated or altered tag simply won't render — players see the raw tag text instead of color.
- **Legacy color codes** `&a`, `§c` — the older format for the same thing, equally untouchable.
- **Escaping** `\:`, `\!`, `''` — `.properties`-specific: colons and apostrophes inside a value must be escaped or Java fails to parse the file at all.
- **Status placeholders** like `[offon]`, `%server_time_hh:mm:ss%` — also not text, but variables the plugin substitutes at runtime.

Pasting the whole file into Google Translate as-is will scramble `{0}`/`{1}` ordering, drop backslashes, translate tag names, and the result stops working in-game. Manually translating thousands of lines while preserving all of this by hand is slow and error-prone.

**Plugin Translator** handles this by scanning the text for these technical tokens via regex before sending anything for translation, swapping them out for neutral markers (`@@0@@`, `@@1@@`...), translating only the remaining human text, then restoring the tokens exactly where they were — byte-identical to the original.

## How it works

1. **Parsing** — YAML is read via `ruamel.yaml` (preserving structure and quoting); `.properties` uses a custom line parser that respects `key=value` escaping.
2. **Masking** — a single `PLACEHOLDER_RE` built from a curated set of regex patterns per format.
3. **Pre-translation scan** (`--scan`) — a separate pass surfaces token-like constructs NOT caught by the protection patterns, before translation runs, so you can extend the patterns manually.
4. **Translation** — via Google Translate's unofficial public endpoint, across 12+ threads with reusable keep-alive HTTPS connections (without this, translating thousands of lines takes far longer due to repeated TLS handshakes).
5. **Caching** — every translated string is saved to a JSON file next to the script; re-running only translates new/changed strings.
6. **Fault tolerance** — network failures are retried with exponential backoff; if a string still can't be translated, it is NOT cached and stays flagged for retry on the next run, instead of silently staying untranslated forever.

## Features
- Supports **YAML** (`Locale_EN.yml` → `Locale_RU.yml`) and **Java `.properties`** (`messages.properties` → `messages_ru.properties`, EssentialsX format)
- Protects placeholders: `{0}`, `%player%`, `[bracket]`, `<tag>`, `&a`/`§a` color codes, MiniMessage tags, escaped characters (`\:`, `\!`, `''`), and more
- `--scan` mode — surfaces suspicious tokens not covered by the built-in patterns before anything gets translated
- Translation caching (re-running the script won't re-translate what's already done)
- Multithreaded with persistent HTTPS connections for speed
- Automatic retry on network failures, without silently losing untranslated strings
- GUI version (`PluginTranslator.py`) — no need to touch code or a console
- Can be packaged as a standalone `.exe` — end users don't need Python installed

### Requirements
- Python 3.10+
- `pip install ruamel.yaml` (for YAML support; the GUI version can install it automatically)

### Usage
```bash
python translator.py --scan          # check a YAML file before translating
python translator.py                 # translate the YAML file

python translator_properties.py --scan
python translator_properties.py      # translate the .properties file

python PluginTranslator.py           # run the GUI version
```

### Limitations (honest)
- Uses Google Translate's unofficial public endpoint — not a production-grade API; occasional failures and rate limits are expected (mitigated via retry + keep-alive, but not guaranteed).
- Reliably telling apart "a formatting tag that must not be touched" (`<primary>`) from "a command argument that should be translated" (`<player>`) without a manual whitelist isn't possible — both are currently protected wholesale (safe, but usage-line translations aren't as polished as they could be).
- Not a substitute for native-speaker proofreading — this is a drafting tool, not a final-release-ready translator.

## License
MIT
