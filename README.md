# Access Role Check

Cog для Red-DiscordBot 3.5+, который держит общую роль доступа в соответствии с набором ролей-ключей.

Логика: есть хотя бы одна настроенная роль-ключ — общая роль выдаётся. Не осталось ни одной — снимается.

Права на каналы, `Administrator`, владелец сервера и любые другие роли в расчёт не берутся. Источник истины — только роли, добавленные через `accessrole addkey`.

После аудита cog также:
- очищает старую роль доступа при её замене;
- пересчитывает участников, если роль-ключ удалена прямо в Discord;
- при запуске удаляет устаревшие ID ролей и снимает доступ, если валидных ключей больше нет.

## Установка

```text
[p]repo add access-role-check https://github.com/neuropolimer/access-role-check
[p]cog install access-role-check accessrolecheck
[p]load accessrolecheck
```

## Настройка

```text
[p]accessrole set @ДОСТУП
[p]accessrole addkey @Роль
[p]accessrole addkey @ДругаяРоль
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

## Обновление

Настройки находятся в `redbot.core.Config` и при обновлении не сбрасываются:

```text
[p]repo update
[p]cog update accessrolecheck
[p]reload accessrolecheck
```

Боту нужны `Manage Roles`, Server Members Intent и роль выше общей роли доступа.
