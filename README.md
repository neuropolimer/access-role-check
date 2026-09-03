# Access Role Check

Cog for Red-DiscordBot 3.5+ that keeps one visual **access role** synchronized with a configurable set of **key roles**.

## What it does

- If a member has **at least one** configured key role, the cog grants the configured access role.
- If the member loses their **last** key role, the access role is removed.
- Works when a key role is added or removed later.
- Handles members who join the server already carrying a key role from an invite/onboarding/access system.
- Automatically checks existing members when configuration changes.
- Runs a synchronization pass after bot startup when the cog is already configured.
- Provides a manual full-server sync command.
- Ignores bot accounts.

## Install

```text
[p]repo add access-role-check https://github.com/neuropolimer/access-role-check
[p]cog install access-role-check accessrolecheck
[p]load accessrolecheck
```

The repository is currently private. Red's host must be able to authenticate to GitHub for the `repo add` command to clone a private repository. The simplest deployment option is to make the repository public; otherwise configure Git credentials/PAT on the bot host.

## Configure

Set the visual access role:

```text
[p]accessrole set @ДОСТУП
```

Add every role that should imply access:

```text
[p]accessrole addkey @🔑 Wardogs
[p]accessrole addkey @AnotherKeyRole
```

Adding a key immediately triggers a pass over existing members, so users who already have that key will receive the access role automatically.

Check configuration:

```text
[p]accessrole list
```

Force a full synchronization at any time:

```text
[p]accessrole sync
```

Synchronize one member only:

```text
[p]accessrole sync @Member
```

Remove a key role:

```text
[p]accessrole removekey @🔑 Wardogs
```

Remove all configured key roles and remove the access role from members:

```text
[p]accessrole clear
```

Forget the access-role setting without modifying existing member roles:

```text
[p]accessrole unset
```

`[p]ar` is an alias for `[p]accessrole`.

## Discord permissions / intents

The bot needs:

- **Manage Roles** permission.
- Its highest role must be above the configured access role.
- **Server Members Intent** should be enabled so full-server synchronization can reliably see every existing member and receive member role updates.

The key roles themselves only need to be readable; the bot does not modify them.
