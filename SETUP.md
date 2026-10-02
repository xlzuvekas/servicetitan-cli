# Setting up servicetitan-cli

`servicetitan` is a command-line tool that reads a company's ServiceTitan data
and answers questions about it: revenue, service calls, sales, receivables,
memberships and call handling. You can run it yourself in a terminal, or let an
AI assistant such as Claude run it for you.

Setup takes about half an hour, most of it in ServiceTitan getting the API
credentials. When you finish:

- the `servicetitan` command is installed on this computer;
- your company is connected, with its secrets in the computer's password store;
- your data is pulled once, so `servicetitan brief` answers "how are we doing?"
  in one step.

You need:

- a Windows, macOS or Linux computer you can install software on;
- a ServiceTitan login that can open **Settings > Integrations > API Application
  Access**, or ten minutes with someone who has one;
- this folder, unzipped.

## Getting Claude to guide you

Give Claude this folder, or the zip it came in, and say "help me set this up".
Claude will take you through the steps below one at a time and check each one
with you before moving on.

**Instructions for Claude**, or any other assistant guiding someone through this file:

1. Ask which operating system they use. Give commands for that system only, and
   tell them how to open a terminal there (step 2 explains).
2. Go one step at a time. After each command, ask them to paste what it printed
   and compare it with the "You should see" line before moving on.
3. **Never ask for, accept or repeat the client secret or the app key.** The
   person types them into hidden prompts when they run `servicetitan setup` in
   their own terminal, and they go straight to the computer's password store. If
   someone pastes one into the chat anyway, tell them to generate a new client
   secret in ServiceTitan (Settings > Integrations > API Application Access) and
   use that instead. The tenant ID and client ID are not secret.
4. If you can run commands on their computer yourself, for example in Claude
   Code, you may run every step except step 5. Step 5 needs the person at the
   keyboard: ask them to run `servicetitan setup` in their own terminal window.
5. When a command fails, its error message says what went wrong and what to do
   next. Read it with them before trying anything else; the troubleshooting
   table at the end covers the common cases.
6. Finish with step 6, then point them at the "Daily use" section of README.md.

## Step 1: Check for Python

The tool needs Python 3.9 or newer. The optional search features need Python
3.10, 3.11 or 3.12.

| System | Command |
|---|---|
| Windows (PowerShell) | `py --version` |
| macOS, Linux | `python3 --version` |

**You should see** `Python 3.` followed by 9 or a higher number, for example
`Python 3.12.7`.

If the command is not found, or the number is lower:

- **Windows:** download Python 3.12 from <https://www.python.org/downloads/>
  and tick **Add python.exe to PATH** on the installer's first screen. Or run
  `winget install Python.Python.3.12` in PowerShell. Close and reopen
  PowerShell afterwards.
- **macOS:** download Python 3.12 from <https://www.python.org/downloads/>, or
  run `brew install python@3.12` if you use Homebrew.
- **Linux:** use the package manager, for example
  `sudo apt install python3 python3-pip pipx`.

## Step 2: Install the tool

Open a terminal **in this folder**, the one that contains README.md and SETUP.md.

- **Windows:** open the folder in File Explorer, click the address bar, type
  `powershell` and press Enter.
- **macOS:** open Terminal, type `cd ` with a space after it, drag this folder
  onto the Terminal window, and press Enter.
- **Linux:** open a terminal and `cd` into the folder.

Install with pipx, which keeps the tool in its own space and puts the
`servicetitan` command where the terminal can find it:

```powershell
# Windows (PowerShell)
py -m pip install --user pipx
py -m pipx ensurepath
# close PowerShell, open it again in this folder, then:
pipx install .
```

```bash
# macOS: brew install pipx   (or: python3 -m pip install --user pipx)
# Linux: sudo apt install pipx
pipx ensurepath
# close the terminal, open it again in this folder, then:
pipx install .
```

To include the optional search features, run `pipx install ".[index]"`
instead of `pipx install .`.

**You should see** a line starting `installed package servicetitan-cli`.

If `pipx` is not found after reopening the terminal, use `py -m pipx install .`
on Windows or `python3 -m pipx install .` elsewhere. Plain pip also works
(`py -m pip install .` or `python3 -m pip install .`), but some systems refuse
it with "externally-managed-environment"; use pipx there.

## Step 3: Check the install

```bash
servicetitan --version
```

**You should see** `servicetitan-cli 0.1.0` or a later version.

If the command is not recognized, close and reopen the terminal first. If you
installed with plain pip, every command also works as `py -m servicetitan_cli`
on Windows or `python3 -m servicetitan_cli` elsewhere, for example
`py -m servicetitan_cli --version`.

`servicetitan init` shows a checklist of what is still to do at any point.

## Step 4: Get your ServiceTitan credentials

You need four values: the **app key**, **tenant ID**, **client ID** and
**client secret**. [docs/setup-servicetitan-api-access.md](docs/setup-servicetitan-api-access.md)
walks through it in detail. In short:

1. In the ServiceTitan developer portal, <https://developer.servicetitan.io>,
   sign in, register a new app, tick the **read** permissions it lists, add your
   tenant ID, and save. Copy the **app key** from the app's page.
2. In ServiceTitan, open **Settings > Integrations > API Application Access**,
   choose **Connect New App**, pick the app, and allow access.
3. On the same page, generate the **client ID** and **client secret**. The secret
   is shown only once, so keep the page open until step 5 is done. The **tenant
   ID** is on the same page.

Leave every write permission unticked unless you mean to let the tool change
data in ServiceTitan. A read-only app can never change anything.

Keep the secret and the app key to yourself. Do not email them or paste them
into a chat; step 5 asks for them in hidden prompts.

## Step 5: Connect your company

Run this in your own terminal window:

```bash
servicetitan setup
```

It asks, in four short steps:

1. **The company:** a short name such as `acme`, the company name, and its time
   zone, picked from a list.
2. **The ServiceTitan account:** a short code such as `MAIN`, a name for it,
   the tenant ID and client ID, then the client secret and app key. Nothing
   appears on screen while you type or paste those two; that is expected.
3. **The connection test:** it signs in to ServiceTitan and shows how your
   business units are grouped into Service, Install and Sales. Nothing is saved
   until this works, unless you choose to keep the values anyway. If the app
   has write permissions, it offers to keep the company read-only.
4. **The first data pull:** a few minutes for a year of jobs, invoices and calls.

**You should see** `Connected.` with your business units, then `Saved.`, then
`All set.`

A company with a second ServiceTitan account, for example a second location
with its own tenant, runs `servicetitan setup` again and picks the same company
name. A company with one account and several business units needs only one.

## Step 6: Try it

```bash
servicetitan brief
```

**You should see** today's jobs, the next seven days, month to date,
receivables, memberships and any flags worth a look. If it says there is no
snapshot, run `servicetitan fetch` first.

README.md covers daily use: more questions to ask, the dashboard
(`servicetitan render --out dashboard.html`), and keeping the data fresh
(`servicetitan fetch --max-age 6`).

## Optional: let Claude Code use it

Claude Code runs commands on your computer, so it can answer questions with your
live numbers. Copy the `skills/servicetitan` folder from this folder into your
Claude skills folder: `~/.claude/skills/` on macOS and Linux,
`%USERPROFILE%\.claude\skills\` on Windows. Then ask Claude Code something like
"how did service do this month?" and it will use the tool on its own.

## What is stored where

| What | Where |
|---|---|
| Client secret and app key | The computer's password store, under the name `servicetitan-cli`: Windows Credential Manager, the macOS Keychain, or the Linux Secret Service. |
| ...on a machine without one | `credentials.json` in the settings folder, readable only by you. This happens on servers and containers, and the tool says so every time. |
| Tenant ID, client ID, company settings | `profiles.json` in the settings folder. These are not secret. |
| Sign-in tokens, valid 15 minutes | `tokens.json` in the settings folder, readable only by you. |
| Your data | `snapshots/`, `results/` and `index/` in the settings folder. |

The settings folder is `%APPDATA%\servicetitan-cli` on Windows,
`~/Library/Application Support/servicetitan-cli` on macOS and
`~/.config/servicetitan-cli` on Linux. `servicetitan init` prints the exact
folder. The tool talks only to ServiceTitan's own servers.

The data files contain customer names and revenue. Treat them like the
ServiceTitan account itself.

## Troubleshooting

| You see | What it means | What to do |
|---|---|---|
| `servicetitan` is not recognized, or command not found | The terminal has not picked up the install location yet | Close and reopen the terminal. Then try `py -m pipx ensurepath` (Windows) or `pipx ensurepath`, and reopen again. |
| `externally-managed-environment` | The system Python refuses plain pip installs | Install with pipx, as in step 2. |
| `AUTH` error, "client id or client secret is wrong" | A typo, an old secret, or a secret from the sandbox environment | Generate a new client secret in ServiceTitan, then run `servicetitan tenant set-secret <company> --code MAIN`. |
| `Forbidden (403)` | The app lacks a read permission, or is not connected to this tenant | `servicetitan auth check` lists missing permissions. Add them in the developer portal, then approve the new version in ServiceTitan. |
| `note: secrets are kept in ... credentials.json` | This machine has no password store | Fine on a server. On a desktop, make sure the keychain or credential manager is running, then run setup again. |
| `No snapshot`, exit code 7 | No data pulled yet, or it is old | `servicetitan fetch` |

## Removing everything

```bash
servicetitan profile remove <company>   # deletes the company and its secrets from the password store
pipx uninstall servicetitan-cli         # or: pip uninstall servicetitan-cli
```

Then delete the settings folder listed above.
