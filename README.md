# Access Role Check

Cog для Red-DiscordBot 3.5+, который синхронизирует несколько независимых выдаваемых ролей со своими наборами ролей-ключей.

Каждая группа работает отдельно. Например:

```text
🔑 Wardogs → ДОСТУП
Петух      → КАСТА
```

Наличие `Петух` никак не влияет на `ДОСТУП`, а `Wardogs` — на `КАСТА`.

Для каждой выдаваемой роли действует одна и та же логика: есть хотя бы один её настроенный ключ — роль выдаётся; не осталось ни одного ключа — роль снимается.

Права на каналы, `Administrator`, владелец сервера и любые другие роли в расчёт не берутся. Источник истины — только явно настроенные пары ролей.

## Обновление с версии 1.x

Старый конфиг вида «одна роль доступа + список ключей» мигрирует автоматически при первом запуске версии 2.x.

То есть существующая настройка вроде:

```text
Wardogs → ДОСТУП
```

останется рабочей и станет первой независимой группой. Настройки вручную переносить не нужно.

## Установка

```text
[p]repo add access-role-check https://github.com/neuropolimer/access-role-check
[p]cog install access-role-check accessrolecheck
[p]load accessrolecheck
```

## Настройка

Создать новую независимую группу сразу с ключом:

```text
[p]accessrole addcategory @КАСТА @Петух
```

Или сначала создать выдаваемую роль, а потом добавить ключи:

```text
[p]accessrole addcategory @ДОСТУП
[p]accessrole addkey @ДОСТУП @Wardogs
[p]accessrole addkey @ДОСТУП @ДругаяРольКлюч
```

Удалить ключ:

```text
[p]accessrole removekey @ДОСТУП @Wardogs
```

Удалить целую группу и снять её выдаваемую роль с участников:

```text
[p]accessrole removecategory @КАСТА
```

Удалить все ключи одной группы:

```text
[p]accessrole clear @КАСТА
```

Показать весь конфиг и синхронизировать участников:

```text
[p]accessrole list
[p]accessrole sync
[p]accessrole sync @Member
```

Вызов `[p]accessrole` без аргументов показывает встроенную памятку по командам.

`[p]ar` — короткий алиас для `[p]accessrole`.

## Обновление

Настройки находятся в `redbot.core.Config` и при обновлении не сбрасываются:

```text
[p]repo update
[p]cog update accessrolecheck
[p]reload accessrolecheck
```

Боту нужны `Manage Roles`, Server Members Intent и роль бота выше всех ролей, которыми AccessRoleCheck должен управлять.
