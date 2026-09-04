# Access Role Check

Cog для Red-DiscordBot 3.5+, который автоматически держит общую роль доступа в соответствии с набором ключевых ролей.

Логика намеренно простая: есть хотя бы одна настроенная ключевая роль — общая роль выдаётся. Не осталось ни одной — снимается.

Права на каналы, `Administrator`, владелец сервера и любые другие роли в расчёт не берутся. Источник истины — только роли, добавленные через `accessrole addkey`.

## Установка

```text
[p]repo add access-role-check https://github.com/neuropolimer/access-role-check
[p]cog install access-role-check accessrolecheck
[p]load accessrolecheck
```

## Настройка

Указать общую роль:

```text
[p]accessrole set @ДОСТУП
```

Добавить роли-ключи:

```text
[p]accessrole addkey @Роль
[p]accessrole addkey @ДругаяРоль
```

Посмотреть настройку и синхронизировать участников:

```text
[p]accessrole list
[p]accessrole sync
```

Остальные команды:

```text
[p]accessrole removekey @Роль
[p]accessrole clear
[p]accessrole unset
[p]accessrole sync @Member
```

`[p]ar` — короткий алиас для `[p]accessrole`.

При изменении конфигурации cog проверяет существующих участников. После перезапуска Red также выполняется синхронизация.

## Обновление

Настройки хранятся в `redbot.core.Config`, поэтому обычное обновление cog'а их не сбрасывает:

```text
[p]repo update access-role-check
[p]cog update accessrolecheck
[p]reload accessrolecheck
```

Боту нужны `Manage Roles`, включённый Server Members Intent и роль выше общей роли доступа.
