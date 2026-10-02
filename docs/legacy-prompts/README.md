# Отработанные промпты старого бота (ecom-chat)

Источник: MySQL-база Directus из `ecom-chat/database/mysql` — локальный снимок, по датам файлов последнее изменение в мае 2026. На проде могут быть версии новее.
Выгружено 2026-10-02. Основной набор — клиент **Zama Salon** (самый свежий, 2026-05-02).

## Что где

| Файл | Что делает в старом боте | Куда идёт в новом |
|---|---|---|
| `context_manager.txt` | Одиночный режим: переписать вопрос для RAG + определить type/role/language одним вызовом | Контекст-менеджер (шаг 1). Тексты правил берём как есть, `type_list`/`role_list` заполняем из сценариев и персон |
| `context_manager_question.txt` | Раздельный режим: переписать одну строку диалога в самостоятельный вопрос | Контекст-менеджер (шаг 1) — **основа**, это и есть «переписывание» |
| `context_manager_types.txt` | Раздельный режим: выбрать type из списка | Выбор маршрута (шаг 2, Jev; пока Jev нет — LLM по этому промпту) |
| `context_manager_select_service.txt` | Подмешивается в переписывание на шаге «выбор услуги»: точное совпадение с последними показанными вариантами | Поле сценария «выбор из каталога» |
| `check_script_answers.txt` | Проверка ответов на шаги сценария | Заполнение полей сценария (Jev / код / LLM) |
| `_cm_types_table.md` | Описания типов: ethical_violation, greeting_only, goodbye_only, not_a_bot, order_issue, product_filter | Вопросы маршрута (safety, приветствие, «ты бот?») и описания сценариев |
| `scripts/*.json` | Сценарии: шаги, `prompt` (как спросить), `check_prompt` (как проверить ответ), запуск/отмена | Поля сценариев: формулировки вопросов и правила проверки |
| `variants/*.txt` | Все различающиеся версии промптов контекст-менеджера у разных клиентов | Для сравнения. Версия BU (`context_manager_question__874a85`) дополнительно возвращает `tags` — теги услуг |
| остальные `*.txt` | system/user prompt ответа, quality_manager, product_filter, learning | Шаг 5 (генерация ответа) и обучение — разберём позже |

## Как старый бот собирал контекст-менеджер

`ecom-chat/app/llm/chat.py:456-750`:
- промпт — JSON; в `rules.type.type_list` подставляются `curr_prompt` активных сценариев + записи `context_manager_types` клиента; в `rules.role.role_list` — триггеры агентов;
- раздельный режим (есть `context_manager_question`/`context_manager_types`): два отдельных вызова LLM; в `question` передаются `dialogue` (все сообщения без system) и `selected_line` (текущее сообщение), плюс `answer_language`;
- если активен шаг `select_service`, в `rules.question.rule` добавляется `context_manager_select_service` с `%LAST_PRESENTED_OPTIONS%` = показанные клиенту услуги.
