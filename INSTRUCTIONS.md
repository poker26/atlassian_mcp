# Инструкции для агента: Confluence через Atlassian MCP

## Рабочий процесс (кратко)

1. Для **точечных** правок `body.storage` на больших страницах — только **`confluence_replace_in_page_storage`**, не полный HTML в **`confluence_update_page`**.
2. Сначала **`dry_run: true`**, проверь `total_occurrences_applied`, `replacements[]`, `warnings` (и `snippets` при необходимости).
3. Затем **`dry_run: false`**. При **`VERSION_CONFLICT`** — снова **`confluence_get_page`**, обнови **`expected_version`**, повтори.
4. В этом репозитории для Cursor дополнительно включено правило **`.cursor/rules/atlassian-mcp-confluence-edits.mdc`** (`alwaysApply: true` при открытии этого workspace).

Если корень Cursor — **другой** проект (MCP только подключён в настройках), скопируй содержимое этого правила в свой `.cursor/rules/` или добавь ссылку на этот файл в свои правила.

## Частичные правки страниц (рекомендуется)

Для больших страниц **не передавайте** полный `body.storage` в `confluence_update_page`. Используйте **`confluence_replace_in_page_storage`**: сервер сам читает актуальную версию, применяет замены, валидирует HTML и выполняет `PUT` с корректным `version.number`.

### Параметры инструмента `confluence_replace_in_page_storage`

| Параметр | Назначение |
|----------|------------|
| `page_id` | ID страницы в Confluence |
| `replacements` | Упорядоченный список правил `{ "find", "replace", "match": "literal" \| "regex", "max_occurrences"? }` |
| `minor_edit` | Передаётся в Confluence как `minorEdit` новой версии |
| `dry_run` | `true` — только подсчёт и (опционально) фрагменты контекста, **без записи** |
| `expected_version` | Если задано, должно совпасть с `version.number` после GET, иначе **`VERSION_CONFLICT`** |
| `fail_if_no_match` | `true` — ошибка **`NO_MATCH`**, если ни одно правило не сработало |
| `version_comment` | Комментарий к версии в Confluence |
| `include_match_snippets` | При `dry_run` — короткие сниппеты вокруг первого совпадения |
| `snippet_radius` | Половина длины сниппета в символах |

### Коды ошибок (префикс сообщения `КОД: …`)

- **`VERSION_CONFLICT`** — версия страницы изменилась; снова вызовите `confluence_get_page`, обновите `expected_version` и повторите.
- **`NO_MATCH`** — при `fail_if_no_match=true` ни одна замена не нашла вхождений.
- **`INVALID_STORAGE_AFTER_PATCH`** — после замен тело не прошло HTML-валидацию на сервере; откат через историю страницы или исправление правил.
- **`REGEX_TIMEOUT`** — превышен лимит времени на regex (см. `replace_regex_timeout_seconds` в конфиге).

### Предупреждения в ответе (`warnings`)

- Усечение числа замен из-за лимитов (`replace_max_literal_occurrences_per_rule`, `replace_max_regex_occurrences_per_rule`).
- **`MULTIPLE_MATCH`** — при `max_occurrences=1` и нескольких вхождениях применена только одна замена.

### Статусы результата (`status`)

- **`ok`** — были применены замены, все правила дали хотя бы одно вхождение (или нечего было заменять при нулевых «опциональных» правилах — см. ниже).
- **`partial`** — часть правил дала `occurrences_applied=0`, при этом другие правила что-то заменили.
- **`no_op`** — итоговый `body.storage` совпал с исходным (в т.ч. повторный вызов с теми же литералами после уже выполненной замены) или совпадений не было; **PUT не выполняется** (идемпотентность).

### Пример: подставить Jira-ключ (literal)

```json
{
  "page_id": "123456789",
  "replacements": [
    {
      "find": "{{JIRA_KEY}}",
      "replace": "PROJ-42",
      "match": "literal"
    }
  ],
  "dry_run": true
}
```

Сначала `dry_run: true`, убедитесь по `total_occurrences_applied` и `replacements[].occurrences_eligible`, затем повторите с `dry_run: false`.

### Пример: ожидаемая версия (оптимистичная блокировка)

1. `confluence_get_page` → запомните `version`.
2. Вызов с `expected_version: <это число>`. При параллельном редактировании получите **`VERSION_CONFLICT`**.

### Полная замена тела: `confluence_update_page`

Используйте, когда меняется вся страница или формат не сводится к точечным заменам.

- **`content_encoding: "base64"`** — полезно для очень больших тел: в MCP передаётся UTF-8 текста, закодированный в Base64 (после декодирования дальше действует `content_format` как раньше).
- **`expected_version`** — то же смысловое ограничение, что и для replace-tool: несовпадение → **`VERSION_CONFLICT`**.

Лимиты длины для replace задаются переменными окружения (префикс как у pydantic-settings, обычно **верхний регистр**), например: `REPLACE_MAX_RULES`, `REPLACE_MAX_FIND_LENGTH`, `REPLACE_MAX_REPLACE_LENGTH`, `REPLACE_MAX_COMBINED_FIND_REPLACE_BYTES`, `REPLACE_MAX_LITERAL_OCCURRENCES_PER_RULE`, `REPLACE_MAX_REGEX_OCCURRENCES_PER_RULE`, `REPLACE_REGEX_TIMEOUT_SECONDS` — см. поля в `atlassian_mcp/config.py`.
